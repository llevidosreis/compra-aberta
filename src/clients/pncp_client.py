from collections.abc import Iterator
from typing import Any
import time

import requests

from src.config import Config


class PNCPClienteError(Exception):
    """Erro ao consultar o PNCP depois de esgotar as tentativas."""


class PNCPClient:
    MAX_TENTATIVAS = 3

    def __init__(self, config: Config):
        self._config = config

    def _paginar(
        self,
        endpoint: str,
        filtros: dict[str, Any],
        pagina_inicial: int = 1,
    ) -> Iterator[tuple[list[dict[str, Any]], int]]:
        """Usa totalPaginas como limite para não fazer chamada vazia extra."""
        url = f"{self._config.pncp_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        pagina = max(1, pagina_inicial)
        total_paginas: int | None = None

        while total_paginas is None or pagina <= total_paginas:
            params = {
                **filtros,
                "pagina": pagina,
                "tamanhoPagina": self._config.pncp_page_size,
            }
            corpo = self._buscar_pagina_com_retry(url, params)
            if not corpo:
                return

            registros = corpo.get("data") or []
            total_paginas = int(corpo.get("totalPaginas") or 0)
            if not registros:
                return

            yield registros, pagina
            pagina += 1

    def _buscar_pagina_com_retry(
        self, url: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        ultimo_erro: Exception | None = None
        for tentativa in range(1, self.MAX_TENTATIVAS + 1):
            try:
                resposta = requests.get(
                    url, params=params, timeout=self._config.http_timeout_segundos
                )
                if resposta.status_code == 204:
                    return None
                resposta.raise_for_status()
                if getattr(resposta, "content", None) == b"":
                    return None
                corpo = resposta.json()
                return corpo if isinstance(corpo, dict) else None
            except (requests.RequestException, ValueError) as erro:
                ultimo_erro = erro
                status = (
                    erro.response.status_code
                    if isinstance(erro, requests.HTTPError) and erro.response
                    else None
                )
                transitorio = status is None or status in {429, 500, 502, 503, 504}
                if not transitorio:
                    raise PNCPClienteError(
                        f"Falha permanente ao consultar {url} com params={params}: {erro}"
                    ) from erro
                if tentativa < self.MAX_TENTATIVAS:
                    espera = 2 ** tentativa
                    if status == 429 and isinstance(erro, requests.HTTPError):
                        retry_after = erro.response.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit():
                            espera = int(retry_after)
                    time.sleep(espera)

        raise PNCPClienteError(
            f"Falha ao consultar {url} com params={params} após "
            f"{self.MAX_TENTATIVAS} tentativas: {ultimo_erro}"
        ) from ultimo_erro

    def listar_contratacoes_publicadas(
        self,
        data_inicio: str,
        data_fim: str,
        modalidade: int,
        uf: str,
        pagina_inicial: int = 1,
    ) -> Iterator[tuple[list[dict[str, Any]], int]]:
        return self._paginar(
            "contratacoes/publicacao",
            {
                "dataInicial": self._data_api(data_inicio),
                "dataFinal": self._data_api(data_fim),
                "codigoModalidadeContratacao": modalidade,
                "uf": uf,
            },
            pagina_inicial,
        )

    def listar_contratacoes_com_proposta_aberta(
        self,
        data_fim: str,
        modalidade: int,
        uf: str,
        pagina_inicial: int = 1,
    ) -> Iterator[tuple[list[dict[str, Any]], int]]:
        return self._paginar(
            "contratacoes/proposta",
            {
                "dataFinal": self._data_api(data_fim),
                "codigoModalidadeContratacao": modalidade,
                "uf": uf,
            },
            pagina_inicial,
        )

    def listar_contratacoes_atualizadas(
        self,
        data_inicio: str,
        data_fim: str,
        modalidade: int,
        uf: str,
        pagina_inicial: int = 1,
    ) -> Iterator[tuple[list[dict[str, Any]], int]]:
        return self._paginar(
            "contratacoes/atualizacao",
            {
                "dataInicial": self._data_api(data_inicio),
                "dataFinal": self._data_api(data_fim),
                "codigoModalidadeContratacao": modalidade,
                "uf": uf,
            },
            pagina_inicial,
        )

    def listar_itens(
        self,
        cnpj_orgao: str,
        ano: int,
        sequencial: int,
        pagina_inicial: int = 1,
    ) -> Iterator[tuple[list[dict[str, Any]], int]]:
        """Lê itens embutidos no detalhe, única operação de compra listada no Swagger."""
        endpoint = f"orgaos/{cnpj_orgao}/compras/{ano}/{sequencial}"
        url = f"{self._config.pncp_base_url.rstrip('/')}/{endpoint}"
        corpo = self._buscar_pagina_com_retry(url, {})
        if not corpo:
            return
        itens = corpo.get("itens") or corpo.get("data") or []
        if isinstance(itens, dict):
            itens = [itens]
        if not isinstance(itens, list):
            return
        if itens:
            yield itens, max(1, pagina_inicial)

    @staticmethod
    def _data_api(valor: str) -> str:
        return valor.replace("-", "")