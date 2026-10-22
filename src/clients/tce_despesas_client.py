"""
Cliente das rotas de despesa do TCE-CE usadas na apuração de pagamentos:

  /orgaos            -> cadastro de órgãos (identificar Prefeitura x Câmara)
  /notas_empenhos    -> empenhos (natureza da despesa e credor)
  /notas_pagamentos  -> notas de pagamento (valor efetivamente pago)

Regras do guia aplicadas aqui (passos 3 e 4):
  - consulta mês a mês por data_referencia_doc (AAAAMM), avançando como
    data: depois de 202512 vem 202601, nunca 202513;
  - paginação por mês com $start_index 0, 1000, 2000... até vir uma página
    com menos de $count registros; ao mudar de mês volta para 0;
  - lista vazia encerra só aquele mês;
  - falha de conexão ou resposta de erro NUNCA vira "mês vazio": depois das
    tentativas, a coleta inteira é interrompida com erro.

Este cliente é independente do tce_client.py (licitações) para não alterar
o comportamento da carga que já está funcionando.
"""
import json
import logging
import time
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import requests

from src.config import Config

logger = logging.getLogger(__name__)


class TCEDespesasErro(RuntimeError):
    """Erro de coleta: a API não respondeu de forma válida após as tentativas."""


def referencias_mensais(inicio: str, fim: str) -> list[str]:
    """Gera as referências AAAAMM entre inicio e fim (inclusive)."""
    for valor in (inicio, fim):
        if len(valor) != 6 or not valor.isdigit() or not 1 <= int(valor[4:]) <= 12:
            raise ValueError(f"Referência inválida '{valor}'. Use AAAAMM, ex.: 202501.")
    ano, mes = int(inicio[:4]), int(inicio[4:])
    ano_fim, mes_fim = int(fim[:4]), int(fim[4:])
    if (ano, mes) > (ano_fim, mes_fim):
        raise ValueError(f"Referência inicial {inicio} é posterior à final {fim}.")

    referencias = []
    while (ano, mes) <= (ano_fim, mes_fim):
        referencias.append(f"{ano:04d}{mes:02d}")
        # avança como data: dezembro -> janeiro do ano seguinte
        mes += 1
        if mes == 13:
            ano, mes = ano + 1, 1
    return referencias


class TCEDespesasClient:
    TENTATIVAS = 3
    MAX_PASSADAS = 8            # limite de releituras de um mesmo mês
    PASSADAS_SEM_NOVIDADE = 2   # passadas seguidas sem registro novo = estável

    def __init__(
        self,
        config: Config,
        session: requests.Session | None = None,
        pausa_base_segundos: float = 2.0,
    ):
        # um item por mês/rota: passadas, recebidos, únicos, completo
        self.relatorio_coleta: list[dict[str, Any]] = []
        self.base_url = config.tce_base_url.rstrip("/")
        self.page_size = config.tce_page_size
        self.timeout = config.http_timeout_segundos
        self.session = session or requests.Session()
        self.pausa_base = pausa_base_segundos

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get(self, rota: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Faz o GET e devolve a lista 'elements'. Nunca devolve [] por erro."""
        url = f"{self.base_url}/{rota}"
        ultimo_erro: Exception | None = None

        for tentativa in range(1, self.TENTATIVAS + 1):
            try:
                resposta = self.session.get(url, params=params, timeout=self.timeout)
                if resposta.status_code != 200:
                    raise TCEDespesasErro(
                        f"HTTP {resposta.status_code} em /{rota} params={params}"
                    )
                # parse_float=Decimal evita perder centavos na conversão binária
                corpo = resposta.json(parse_float=Decimal)
                elementos = corpo.get("elements") if isinstance(corpo, dict) else None
                if not isinstance(elementos, list):
                    raise TCEDespesasErro(
                        f"Resposta de /{rota} sem a lista 'elements' params={params}"
                    )
                return elementos
            except (requests.RequestException, ValueError, TCEDespesasErro) as erro:
                ultimo_erro = erro
                logger.warning(
                    "Falha em /%s (tentativa %d/%d): %s",
                    rota, tentativa, self.TENTATIVAS, erro,
                )
                if tentativa < self.TENTATIVAS:
                    time.sleep(self.pausa_base * tentativa)

        raise TCEDespesasErro(
            f"/{rota} não respondeu após {self.TENTATIVAS} tentativas params={params}"
        ) from ultimo_erro

    # ------------------------------------------------------------------
    # Paginação de um mês
    # ------------------------------------------------------------------

    def _paginar_mes(
        self, rota: str, codigo_municipio: str, exercicio_orcamento: str, referencia: str
    ) -> Iterator[list[dict[str, Any]]]:
        start_index = 0
        assinatura_anterior = None

        while True:
            params = {
                "codigo_municipio": codigo_municipio,
                "exercicio_orcamento": exercicio_orcamento,
                "data_referencia_doc": referencia,
                "$format": "json",
                "$count": self.page_size,
                "$start_index": start_index,
            }
            pagina = self._get(rota, params)

            # lista vazia válida: encerra apenas este mês
            if not pagina:
                return

            # proteção: se a API ignorar $start_index, a mesma página voltaria
            # para sempre e os valores seriam somados várias vezes
            assinatura = (
                len(pagina),
                json.dumps(pagina[0], sort_keys=True, default=str),
                json.dumps(pagina[-1], sort_keys=True, default=str),
            )
            if assinatura == assinatura_anterior:
                raise TCEDespesasErro(
                    f"/{rota} devolveu a mesma página em start_index={start_index} "
                    f"(referência {referencia}); paginação não está avançando."
                )
            assinatura_anterior = assinatura

            yield pagina

            if len(pagina) < self.page_size:
                return
            start_index += self.page_size

    def _uma_passada(
        self, rota: str, codigo_municipio: str, exercicio_orcamento: str, referencia: str
    ) -> list[dict[str, Any]]:
        """Percorre todas as páginas de um mês uma vez."""
        registros: list[dict[str, Any]] = []
        for pagina in self._paginar_mes(rota, codigo_municipio, exercicio_orcamento, referencia):
            registros.extend(pagina)
        return registros

    def _coletar_mes_estavel(
        self, rota: str, codigo_municipio: str, exercicio_orcamento: str, referencia: str
    ) -> list[dict[str, Any]]:
        """Coleta um mês até o resultado ficar estável.

        A API do TCE não garante ordem fixa entre páginas: na prática, numa
        mesma passada alguns registros vêm repetidos e outros não vêm. Por
        isso o mês é percorrido várias vezes e as passadas são unidas pelo
        CONTEÚDO completo do registro (repetição idêntica conta uma vez).

        Para quando:
          - a união atinge o total que a API entrega por passada
            (nada mais está faltando); ou
          - PASSADAS_SEM_NOVIDADE passadas seguidas não trazem nada novo; ou
          - chega a MAX_PASSADAS (fica registrado como possível coleta incompleta).
        """
        unicos: dict[str, dict[str, Any]] = {}
        total_por_passada = 0
        passadas_sem_novidade = 0
        passada = 0

        while passada < self.MAX_PASSADAS:
            passada += 1
            registros = self._uma_passada(rota, codigo_municipio, exercicio_orcamento, referencia)
            total_por_passada = max(total_por_passada, len(registros))
            novos = 0
            for registro in registros:
                assinatura = json.dumps(registro, sort_keys=True, default=str)
                if assinatura not in unicos:
                    unicos[assinatura] = registro
                    novos += 1

            if len(unicos) >= total_por_passada:
                break                                   # nada faltando
            if passada > 1 and novos == 0:
                passadas_sem_novidade += 1
                if passadas_sem_novidade >= self.PASSADAS_SEM_NOVIDADE:
                    break                               # estável
            else:
                passadas_sem_novidade = 0

        # Completo quando:
        #  (a) a união tem tantos registros quanto a API entrega por passada; ou
        #  (b) as releituras ESTABILIZARAM (passadas seguidas sem nenhum registro
        #      novo). A diferença que sobra é uma repetição estável: aparece em
        #      toda leitura e, vista de fora, não se distingue de um registro
        #      gravado duas vezes na origem. Fica registrada como informação.
        # Incompleto só quando a última passada ainda trazia registros novos
        # (coleta não estabilizou dentro de MAX_PASSADAS).
        sem_perda = len(unicos) >= total_por_passada
        estabilizou = passadas_sem_novidade >= self.PASSADAS_SEM_NOVIDADE
        completo = sem_perda or estabilizou
        repeticao_estavel = total_por_passada - len(unicos) if (completo and not sem_perda) else 0
        self.relatorio_coleta.append({
            "rota": rota,
            "referencia": referencia,
            "passadas": passada,
            "registros_por_passada": total_por_passada,
            "registros_unicos": len(unicos),
            "repeticao_estavel": repeticao_estavel,
            "completo": completo,
        })
        if repeticao_estavel:
            situacao = (f"(estável: {repeticao_estavel} registro(s) repetido(s) em todas as "
                        "leituras, tratado(s) como duplicado(s) na origem)")
        elif completo:
            situacao = ""
        else:
            situacao = "(POSSÍVEL COLETA INCOMPLETA: releituras ainda traziam registros novos)"
        logger.log(
            logging.INFO if completo else logging.WARNING,
            "/%s referência=%s passadas=%d recebidos/passada=%d únicos=%d %s",
            rota, referencia, passada, total_por_passada, len(unicos), situacao,
        )
        return list(unicos.values())

    def _coletar(
        self,
        rota: str,
        codigo_municipio: str,
        exercicio_orcamento: str,
        referencia_inicio: str,
        referencia_fim: str,
    ) -> list[dict[str, Any]]:
        registros: list[dict[str, Any]] = []
        for referencia in referencias_mensais(referencia_inicio, referencia_fim):
            do_mes = self._coletar_mes_estavel(
                rota, codigo_municipio, exercicio_orcamento, referencia
            )
            fora_da_referencia = 0
            for registro in do_mes:
                # guarda a referência consultada para rastrear a origem
                registro["_referencia_consultada"] = referencia
                if str(registro.get("data_referencia_doc", "")).strip() != referencia:
                    fora_da_referencia += 1
            if fora_da_referencia:
                logger.warning(
                    "/%s referência %s: %d registro(s) vieram com outra "
                    "data_referencia_doc. Confira se o filtro foi aplicado.",
                    rota, referencia, fora_da_referencia,
                )
            registros.extend(do_mes)
        return registros

    # ------------------------------------------------------------------
    # Rotas públicas
    # ------------------------------------------------------------------

    def listar_orgaos(
        self, codigo_municipio: str, exercicio_orcamento: str
    ) -> list[dict[str, Any]]:
        orgaos = self._get(
            "orgaos",
            {
                "codigo_municipio": codigo_municipio,
                "exercicio_orcamento": exercicio_orcamento,
            },
        )
        if not orgaos:
            raise TCEDespesasErro(
                f"Cadastro de órgãos vazio para município {codigo_municipio} "
                f"exercício {exercicio_orcamento}; não é possível separar a Câmara."
            )
        return orgaos

    def listar_empenhos(
        self, codigo_municipio: str, exercicio_orcamento: str,
        referencia_inicio: str, referencia_fim: str,
    ) -> list[dict[str, Any]]:
        return self._coletar(
            "notas_empenhos", codigo_municipio, exercicio_orcamento,
            referencia_inicio, referencia_fim,
        )

    def listar_pagamentos(
        self, codigo_municipio: str, exercicio_orcamento: str,
        referencia_inicio: str, referencia_fim: str,
    ) -> list[dict[str, Any]]:
        # exercicio_orcamento NÃO muda quando a referência entra no ano seguinte
        return self._coletar(
            "notas_pagamentos", codigo_municipio, exercicio_orcamento,
            referencia_inicio, referencia_fim,
        )