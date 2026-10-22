"""
Classificação cadastral dos fornecedores (passos 8 e 9 do guia — Opção A).

Fonte: OpenCNPJ, consulta pelo CNPJ completo em {OPENCNPJ_BASE_URL}/{cnpj}.
Campos usados (conferidos em uma resposta real em 21/10/2026):
  cnpj, razao_social, porte_empresa ("Microempresa (ME)", "Demais"...),
  opcao_mei ("S"/"N"), opcao_simples ("S"/"N"), updated_at.

Regras:
  - um resultado por CNPJ completo + fonte + referência da base;
  - o CNPJ devolvido precisa ser o mesmo consultado;
  - porte original é preservado; porte_codigo é a forma normalizada;
  - não encontrado, falha ou porte desconhecido NUNCA vira ME ou Demais;
  - opção pelo Simples é guardada só como informação: não substitui o porte;
  - a referência da base é registrada; se a fonte não informa, fica
    'NAO_INFORMADA' (nunca inventamos competência).
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src.config import Config
from src.transform.despesas import (
    PORTE_NAO_INFORMADO,
    cnpj_basico,
    documento_comparacao,
    indicador_sim_nao,
    normalizar_porte,
    texto,
)

logger = logging.getLogger(__name__)

FONTE_OPENCNPJ = "OpenCNPJ"
REFERENCIA_NAO_INFORMADA = "NAO_INFORMADA"

SIT_CLASSIFICADO = "classificado"
SIT_PORTE_NAO_INFORMADO = "porte_nao_informado"
SIT_PORTE_DESCONHECIDO = "porte_desconhecido"
SIT_NAO_ENCONTRADO = "nao_encontrado"
SIT_CNPJ_DIVERGENTE = "cnpj_divergente"
SIT_FALHA = "falha_consulta"

# Situações que valem como cache (falhas são sempre consultadas de novo)
SITUACOES_CACHEAVEIS = {
    SIT_CLASSIFICADO, SIT_PORTE_NAO_INFORMADO, SIT_PORTE_DESCONHECIDO,
    SIT_NAO_ENCONTRADO,
}


@dataclass
class ClassificacaoPorte:
    cnpj_completo: str
    cnpj_basico: str
    porte_original: str | None
    porte_codigo: str | None
    indicador_mei: bool | None
    opcao_simples: bool | None
    razao_social: str | None
    fonte_cadastral: str
    referencia_base_cadastral: str
    atualizado_na_fonte: str | None
    consultado_em: str
    situacao_classificacao: str
    detalhe: str | None = None

    def para_dict(self) -> dict[str, Any]:
        return asdict(self)


class LimiteRequisicoesAtingido(RuntimeError):
    def __init__(self, retry_after: str | None):
        super().__init__(f"OpenCNPJ respondeu 429 (Retry-After={retry_after})")
        self.retry_after = retry_after


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classificacao_de_resposta(
    cnpj: str, corpo: dict[str, Any], referencia_base: str
) -> ClassificacaoPorte:
    """Converte a resposta do OpenCNPJ. Separada do HTTP para ser testável."""
    devolvido = documento_comparacao(corpo.get("cnpj"))
    porte_original = texto(corpo.get("porte_empresa"))
    porte_codigo = normalizar_porte(porte_original)

    if devolvido != cnpj:
        situacao, porte_codigo = SIT_CNPJ_DIVERGENTE, None
        detalhe = f"Fonte devolveu CNPJ '{devolvido}'"
    elif porte_original is None:
        situacao, detalhe = SIT_PORTE_DESCONHECIDO, "Campo porte_empresa ausente"
    elif porte_codigo is None:
        situacao, detalhe = SIT_PORTE_DESCONHECIDO, f"Porte '{porte_original}' sem mapeamento"
    elif porte_codigo == PORTE_NAO_INFORMADO:
        situacao, detalhe = SIT_PORTE_NAO_INFORMADO, None
    else:
        situacao, detalhe = SIT_CLASSIFICADO, None

    return ClassificacaoPorte(
        cnpj_completo=cnpj,
        cnpj_basico=cnpj_basico(cnpj),
        porte_original=porte_original,
        porte_codigo=porte_codigo,
        indicador_mei=indicador_sim_nao(corpo.get("opcao_mei")),
        opcao_simples=indicador_sim_nao(corpo.get("opcao_simples")),
        razao_social=texto(corpo.get("razao_social")),
        fonte_cadastral=FONTE_OPENCNPJ,
        referencia_base_cadastral=referencia_base,
        atualizado_na_fonte=texto(corpo.get("updated_at")),
        consultado_em=_agora_iso(),
        situacao_classificacao=situacao,
        detalhe=detalhe,
    )


def _sem_cadastro(cnpj: str, situacao: str, detalhe: str, referencia: str) -> ClassificacaoPorte:
    return ClassificacaoPorte(
        cnpj_completo=cnpj, cnpj_basico=cnpj_basico(cnpj),
        porte_original=None, porte_codigo=None, indicador_mei=None,
        opcao_simples=None, razao_social=None, fonte_cadastral=FONTE_OPENCNPJ,
        referencia_base_cadastral=referencia, atualizado_na_fonte=None,
        consultado_em=_agora_iso(), situacao_classificacao=situacao, detalhe=detalhe,
    )


class OpenCNPJPorteClient:
    TENTATIVAS = 3

    def __init__(
        self,
        config: Config,
        referencia_base: str = REFERENCIA_NAO_INFORMADA,
        session: requests.Session | None = None,
        intervalo_minimo_segundos: float = 0.2,
    ):
        self.base_url = config.opencnpj_base_url.rstrip("/")
        self.timeout = config.http_timeout_segundos
        self.referencia_base = referencia_base
        self.session = session or requests.Session()
        self._intervalo = intervalo_minimo_segundos
        self._trava = threading.Lock()
        self._ultima_chamada = 0.0

    def _aguardar_vez(self) -> None:
        # espaçamento mínimo entre chamadas, mesmo com várias threads
        with self._trava:
            espera = self._intervalo - (time.monotonic() - self._ultima_chamada)
            if espera > 0:
                time.sleep(espera)
            self._ultima_chamada = time.monotonic()

    def consultar(self, cnpj: str) -> ClassificacaoPorte:
        ultimo_erro = "sem resposta"
        for tentativa in range(1, self.TENTATIVAS + 1):
            self._aguardar_vez()
            try:
                resposta = self.session.get(
                    f"{self.base_url}/{cnpj}", timeout=self.timeout
                )
            except requests.RequestException as erro:
                ultimo_erro = str(erro)
                time.sleep(tentativa)
                continue

            if resposta.status_code == 200:
                try:
                    return classificacao_de_resposta(
                        cnpj, resposta.json(), self.referencia_base
                    )
                except ValueError as erro:
                    ultimo_erro = f"JSON inválido: {erro}"
                    continue
            if resposta.status_code == 404:
                return _sem_cadastro(
                    cnpj, SIT_NAO_ENCONTRADO, "OpenCNPJ respondeu 404",
                    self.referencia_base,
                )
            if resposta.status_code == 429:
                raise LimiteRequisicoesAtingido(resposta.headers.get("Retry-After"))
            ultimo_erro = f"HTTP {resposta.status_code}"
            time.sleep(tentativa)

        return _sem_cadastro(cnpj, SIT_FALHA, ultimo_erro, self.referencia_base)


def cache_valido(item: ClassificacaoPorte, validade_dias: int, referencia: str) -> bool:
    if item.situacao_classificacao not in SITUACOES_CACHEAVEIS:
        return False
    if item.referencia_base_cadastral != referencia:
        return False
    try:
        consultado = datetime.fromisoformat(item.consultado_em)
    except (TypeError, ValueError):
        return False
    if consultado.tzinfo is None:
        consultado = consultado.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - consultado <= timedelta(days=validade_dias)


def classificar_cnpjs(
    cnpjs: list[str],
    client: OpenCNPJPorteClient,
    cache: dict[str, ClassificacaoPorte],
    validade_dias: int,
    max_workers: int = 4,
) -> tuple[dict[str, ClassificacaoPorte], list[str]]:
    """Classifica cada CNPJ único. Devolve (classificações, cnpjs_consultados_agora).

    Em HTTP 429 o lote é interrompido: os CNPJs que faltaram ficam como
    falha_consulta (pendentes) e serão consultados na próxima execução.
    """
    resultado: dict[str, ClassificacaoPorte] = {}
    a_consultar = []
    for cnpj in cnpjs:
        em_cache = cache.get(cnpj)
        if em_cache and cache_valido(em_cache, validade_dias, client.referencia_base):
            resultado[cnpj] = em_cache
        else:
            a_consultar.append(cnpj)

    logger.info(
        "Porte: %d CNPJs únicos, %d do cache, %d a consultar no OpenCNPJ",
        len(cnpjs), len(resultado), len(a_consultar),
    )

    consultados: list[str] = []
    limite = threading.Event()

    def tarefa(cnpj: str) -> ClassificacaoPorte:
        if limite.is_set():
            return _sem_cadastro(cnpj, SIT_FALHA, "Lote interrompido por 429",
                                 client.referencia_base)
        try:
            return client.consultar(cnpj)
        except LimiteRequisicoesAtingido as erro:
            limite.set()
            logger.warning("%s — CNPJs restantes ficam pendentes.", erro)
            return _sem_cadastro(cnpj, SIT_FALHA, str(erro), client.referencia_base)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futuros = {executor.submit(tarefa, c): c for c in a_consultar}
        for indice, futuro in enumerate(as_completed(futuros), start=1):
            item = futuro.result()
            resultado[item.cnpj_completo] = item
            consultados.append(item.cnpj_completo)
            if indice % 50 == 0:
                logger.info("Porte: %d/%d consultas", indice, len(a_consultar))

    return resultado, consultados