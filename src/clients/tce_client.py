"""
Cliente das APIs do TCE-CE (Sistema SIM).

As duas APIs documentadas (licitantes_fornecedores_bens_servicos e
itens_compoem_bens_servicos) compartilham o mesmo comportamento de
paginação: no máximo 1000 registros por chamada, e é preciso ir
incrementando start_index em +1000 até a resposta vir vazia. Por isso
essa lógica de paginação fica centralizada aqui em um único gerador,
reaproveitado pelos dois endpoints.

IMPORTANTE sobre codigo_municipio: apesar do texto da documentação
original dizer que esse parâmetro "corresponde ao código IBGE", os
testes reais mostraram que a API espera o código PRÓPRIO do TCE-CE
(3 dígitos, ex: "010" = Amontada), não o código IBGE de 7 dígitos.
Esse código de 3 dígitos é obtido pelo endpoint /sim/municipios
(não documentado no PDF original, encontrado testando a API), que
devolve os dois códigos lado a lado. listar_municipios(), abaixo, usa
esse endpoint em vez da API do IBGE.
"""
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from typing import Any

import requests
import time

from src.config import Config


@dataclass
class MunicipioTCE:
    codigo_municipio: str          # código do TCE-CE, 3 dígitos — usado em todo o resto do pipeline
    nome: str
    uf: str
    codigo_ibge: str | None        # guardado só como referência
    codigo_geonames: str | None


class TCEClienteError(Exception):
    """Erro ao consultar a API do TCE-CE, depois de esgotar as tentativas."""


class TCEClient:
    def __init__(self, config: Config):
        self._config = config

    def _paginar(
        self,
        endpoint: str,
        codigo_municipio: str,
        data_inicio: str,
        data_fim: str,
        start_index_inicial: int = 0,
        max_tentativas: int = 3,
    ) -> Iterator[tuple[list[dict[str, Any]], int]]:
        """Gera páginas de registros até a API retornar uma lista vazia.

        Produz tuplas (registros_da_pagina, start_index_usado_nessa_pagina)
        para que quem chamar consiga persistir o ponto de retomada
        (pipeline_execucoes.ultimo_start_index) mesmo se o processo cair
        no meio da paginação.
        """
        url = f"{self._config.tce_base_url}/{endpoint}"
        start_index = start_index_inicial
        paginas_vistas: set[str] = set()
        if endpoint in {
            "itens_compoem_bens_servicos",
            "dotacoes_utilizadas_contratacoes",
        }:
            data_inicio = date.fromisoformat(data_inicio).strftime("%Y%m%d")
            data_fim = date.fromisoformat(data_fim).strftime("%Y%m%d")

        while True:
            params = {
                "codigo_municipio": codigo_municipio,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "$format": "json",
                "$count": self._config.tce_page_size,
                "$start_index": start_index,
            }

            registros = self._buscar_pagina_com_retry(url, params, max_tentativas)

            if not registros:
                return

            # Uma repetição exata em offsets distintos torna impossível provar
            # a completude da coleta. Falhar é preferível a repetir staging e
            # avançar checkpoints como se a fonte estivesse paginando.
            assinatura = hashlib.sha256(
                json.dumps(
                    registros, sort_keys=True, ensure_ascii=False, default=str,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if assinatura in paginas_vistas:
                raise TCEClienteError(
                    "Pagina repetida ao paginar "
                    f"{url}; start_index={start_index}; params={params}. "
                    "A coleta foi interrompida porque a completude nao pode ser comprovada."
                )
            paginas_vistas.add(assinatura)

            yield registros, start_index

            start_index += self._config.tce_page_size

    def _buscar_pagina_com_retry(
        self, url: str, params: dict[str, Any], max_tentativas: int
    ) -> list[dict[str, Any]]:
        ultimo_erro: Exception | None = None

        for tentativa in range(1, max_tentativas + 1):
            try:
                resposta = requests.get(
                    url, params=params, timeout=self._config.http_timeout_segundos
                )
                resposta.raise_for_status()
                corpo = resposta.json()
                if not isinstance(corpo, dict) or "elements" not in corpo:
                    raise TCEClienteError(
                        f"Invalid JSON response from {url}: missing object/list elements; params={params}"
                    )
                elementos = corpo["elements"]
                if not isinstance(elementos, list) or not all(isinstance(item, dict) for item in elementos):
                    raise TCEClienteError(
                        f"Invalid JSON response from {url}: elements must be a list of objects; params={params}"
                    )
                return elementos
            except TCEClienteError:
                raise
            except (requests.RequestException, ValueError) as erro:
                ultimo_erro = erro
                status = (
                    erro.response.status_code
                    if isinstance(erro, requests.HTTPError) and erro.response
                    else None
                )
                transitorio = status is None or status in {429, 500, 502, 503, 504}
                if tentativa < max_tentativas and transitorio:
                    espera = 2 ** tentativa
                    if status == 429 and isinstance(erro, requests.HTTPError):
                        retry_after = erro.response.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit():
                            espera = int(retry_after)
                    time.sleep(espera)
                elif not transitorio:
                    raise TCEClienteError(
                        f"Falha permanente ao consultar {url} com params={params}: {erro}"
                    ) from erro

        raise TCEClienteError(
            f"Falha ao consultar {url} com params={params} após {max_tentativas} tentativas: {ultimo_erro}"
        )

    def listar_licitantes_fornecedores(
        self,
        codigo_municipio: str,
        data_inicio: str,
        data_fim: str,
        start_index_inicial: int = 0,
    ) -> Iterator[tuple[list[dict[str, Any]], int]]:
        yield from self._paginar(
            "licitantes_fornecedores_bens_servicos",
            codigo_municipio,
            data_inicio,
            data_fim,
            start_index_inicial,
        )

    def listar_itens_bens_servicos(
        self,
        codigo_municipio: str,
        data_inicio: str,
        data_fim: str,
        start_index_inicial: int = 0,
    ) -> Iterator[tuple[list[dict[str, Any]], int]]:
        yield from self._paginar(
            "itens_compoem_bens_servicos",
            codigo_municipio,
            data_inicio,
            data_fim,
            start_index_inicial,
        )

    def listar_dotacoes_utilizadas(
        self,
        codigo_municipio: str,
        data_inicio: str,
        data_fim: str,
        start_index_inicial: int = 0,
    ) -> Iterator[tuple[list[dict[str, Any]], int]]:
        yield from self._paginar(
            "dotacoes_utilizadas_contratacoes",
            codigo_municipio,
            data_inicio,
            data_fim,
            start_index_inicial,
        )

    def listar_municipios(self, max_tentativas: int = 3) -> list[MunicipioTCE]:
        """Busca a lista oficial de municípios do endpoint /sim/municipios,
        que traz o código do TCE-CE e o código IBGE lado a lado. Descarta
        registros sem codigo_ibge (ex.: "001 - T.C.M.", que não é um
        município de verdade, é um código administrativo interno do TCE).
        """
        url = f"{self._config.tce_base_url}/municipios"
        start_index = 0
        registros: list[dict[str, Any]] = []

        while True:
            params = {"$format": "json", "$count": 200, "$start_index": start_index}
            pagina = self._buscar_pagina_com_retry(url, params, max_tentativas)
            if not pagina:
                break
            registros.extend(pagina)
            if len(pagina) < 200:
                break
            start_index += 200

        municipios = []
        for item in registros:
            codigo_ibge = item.get("codigo_municipio_ibge")
            if not codigo_ibge:
                continue  # registro administrativo (ex.: T.C.M.), não é um município real
            municipios.append(
                MunicipioTCE(
                    codigo_municipio=str(item["codigo_municipio"]),
                    nome=item.get("nome_municipio", ""),
                    uf=self._config.uf,
                    codigo_ibge=str(codigo_ibge),
                    codigo_geonames=str(item["codigo_municipio_geonames"]) if item.get("codigo_municipio_geonames") else None,
                )
            )
        return municipios
