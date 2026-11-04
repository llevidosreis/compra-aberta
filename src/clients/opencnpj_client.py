# src/clients/opencnpj_client.py

"""
Cliente da API OpenCNPJ.
"""

from dataclasses import dataclass
from datetime import datetime
import time

import requests

from src.config import Config
from src.eligibility import cnpj_numerico_valido


@dataclass
class DadosEmpresa:
    cnpj: str
    razao_social: str | None
    nome_fantasia: str | None
    situacao_cadastral: str | None
    data_inicio_atividade: str | None
    cnae_principal: str | None
    cnaes_secundarios: list[str]
    uf: str | None
    municipio: str | None
    porte_empresa: str | None
    opcao_mei: str | None
    fonte_atualizada_em: datetime | None
    encontrado: bool


class OpenCNPJClient:

    MAX_TENTATIVAS = 3
    BACKOFF_INICIAL = 1.0
    MAX_ESPERA_RATE_LIMIT_SEGUNDOS = 60

    def __init__(self, config: Config):
        self._config = config

    def consultar(self, cnpj: str) -> DadosEmpresa:
        """
        Consulta um CNPJ no OpenCNPJ.

        Possui retry automático para erros temporários:
        - HTTP 429
        - HTTP 500
        - HTTP 502
        - HTTP 503
        - HTTP 504
        """

        cnpj_limpo = "".join(
            caractere
            for caractere in cnpj
            if caractere.isdigit()
        )
        if not cnpj_numerico_valido(cnpj_limpo):
            raise ValueError(
                "OpenCNPJ exige um CNPJ numérico válido com 14 dígitos"
            )

        url = (
            f"{self._config.opencnpj_base_url}"
            f"/{cnpj_limpo}"
        )

        ultimo_erro = None

        for tentativa in range(1, self.MAX_TENTATIVAS + 1):

            try:
                resposta = requests.get(
                    url,
                    params={"datasets": "receita"},
                    timeout=self._config.http_timeout_segundos,
                )

                # CNPJ não encontrado não é erro do pipeline.
                if resposta.status_code == 404:
                    return self._dados_nao_encontrados(cnpj_limpo)

                # Rate limit / erros temporários.
                if resposta.status_code in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }:

                    ultimo_erro = RuntimeError(
                        f"OpenCNPJ HTTP {resposta.status_code}"
                    )

                    if tentativa < self.MAX_TENTATIVAS:
                        espera = self.BACKOFF_INICIAL * (
                            2 ** (tentativa - 1)
                        )
                        retry_after = resposta.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit():
                            espera = int(retry_after)
                            if espera > self.MAX_ESPERA_RATE_LIMIT_SEGUNDOS:
                                raise RuntimeError(
                                    "OpenCNPJ solicitou espera de "
                                    f"{espera}s por rate limit"
                                )
                        time.sleep(espera)
                        continue

                    raise ultimo_erro

                resposta.raise_for_status()

                dados = resposta.json()
                if dados.get("cnpj") != cnpj_limpo:
                    raise RuntimeError(
                        "OpenCNPJ retornou um CNPJ diferente do consultado"
                    )

                return DadosEmpresa(
                    cnpj=cnpj_limpo,
                    razao_social=dados.get("razao_social"),
                    nome_fantasia=dados.get("nome_fantasia"),
                    situacao_cadastral=dados.get(
                        "situacao_cadastral"
                    ),
                    data_inicio_atividade=dados.get(
                        "data_inicio_atividade"
                    ),
                    cnae_principal=dados.get(
                        "cnae_principal"
                    ),
                    cnaes_secundarios=(
                        dados.get("cnaes_secundarios")
                        or []
                    ),
                    uf=dados.get("uf"),
                    municipio=dados.get("municipio"),
                    porte_empresa=dados.get(
                        "porte_empresa"
                    ),
                    opcao_mei=dados.get("opcao_mei"),
                    fonte_atualizada_em=self._timestamp_fonte(
                        dados.get("updated_at")
                    ),
                    encontrado=True,
                )

            except requests.HTTPError as erro:
                if erro.response is not None and erro.response.status_code not in {
                    429, 500, 502, 503, 504
                }:
                    raise
                ultimo_erro = erro
                if tentativa < self.MAX_TENTATIVAS:
                    time.sleep(self.BACKOFF_INICIAL * (2 ** (tentativa - 1)))
                    continue
                raise
            except requests.Timeout as erro:
                ultimo_erro = erro
                if tentativa < self.MAX_TENTATIVAS:
                    time.sleep(self.BACKOFF_INICIAL * (2 ** (tentativa - 1)))
                    continue
                raise
            except requests.RequestException:
                raise

        raise RuntimeError(
            f"Falha ao consultar CNPJ {cnpj_limpo}"
        ) from ultimo_erro

    @staticmethod
    def _dados_nao_encontrados(
        cnpj: str,
    ) -> DadosEmpresa:

        return DadosEmpresa(
            cnpj=cnpj,
            razao_social=None,
            nome_fantasia=None,
            situacao_cadastral=None,
            data_inicio_atividade=None,
            cnae_principal=None,
            cnaes_secundarios=[],
            uf=None,
            municipio=None,
            porte_empresa=None,
            opcao_mei=None,
            fonte_atualizada_em=None,
            encontrado=False,
        )

    @staticmethod
    def _timestamp_fonte(valor: str | None) -> datetime | None:
        if not valor:
            return None
        atualizado_em = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        if atualizado_em.tzinfo is None:
            raise ValueError("OpenCNPJ updated_at deve conter fuso horário")
        return atualizado_em