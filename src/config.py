"""
Configuração central do pipeline.

Todas as variáveis vêm de ambiente (.env), nunca hardcoded, para não
expor credenciais no código-fonte.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _obrigatoria(nome: str) -> str:
    """Falha rápido (fail-fast) se uma variável obrigatória não existir."""
    valor = os.getenv(nome)
    if not valor:
        raise RuntimeError(
            f"Variável de ambiente obrigatória '{nome}' não foi definida. "
            f"Confira o arquivo .env (veja .env.example)."
        )
    return valor


def _parse_int(nome: str, valor: str | None, *, minimo: int | None = None, maximo: int | None = None) -> int:
    try:
        numero = int(valor) if valor is not None else 0
    except (TypeError, ValueError) as erro:
        raise ValueError(f"Variável '{nome}' deve ser um inteiro válido.") from erro

    if minimo is not None and numero < minimo:
        raise ValueError(f"Variável '{nome}' deve ser maior ou igual a {minimo}.")
    if maximo is not None and numero > maximo:
        raise ValueError(f"Variável '{nome}' deve ser menor ou igual a {maximo}.")
    return numero


@dataclass(frozen=True)
class Config:
    database_url: str
    tce_base_url: str
    opencnpj_base_url: str
    uf: str
    tce_page_size: int
    http_timeout_segundos: int
    opencnpj_dias_validade_cache: int
    opencnpj_max_workers: int
    ibge_base_url: str = "https://servicodados.ibge.gov.br/api/v1/localidades"

    def __post_init__(self) -> None:
        if self.tce_page_size <= 0:
            raise ValueError("TCE_PAGE_SIZE deve ser maior que zero.")
        if self.http_timeout_segundos <= 0:
            raise ValueError("HTTP_TIMEOUT_SEGUNDOS deve ser maior que zero.")
        if self.opencnpj_dias_validade_cache <= 0:
            raise ValueError("OPENCNPJ_DIAS_VALIDADE_CACHE deve ser maior que zero.")
        if self.opencnpj_max_workers <= 0:
            raise ValueError("OPENCNPJ_MAX_WORKERS deve ser maior que zero.")


@dataclass(frozen=True)
class ConfigBanco:
    """Configuração necessária para atender a API de leitura."""

    database_url: str
    opencnpj_dias_validade_cache: int = 30


def carregar_config_banco() -> ConfigBanco:
    """Carrega as dependências da API sem validar as demais opções do pipeline."""
    return ConfigBanco(
        database_url=_obrigatoria("DATABASE_URL"),
        opencnpj_dias_validade_cache=_parse_int(
            "OPENCNPJ_DIAS_VALIDADE_CACHE",
            os.getenv("OPENCNPJ_DIAS_VALIDADE_CACHE", "30"),
            minimo=1,
        ),
    )


def carregar_config(exigir_banco: bool = True) -> Config:
    """exigir_banco=False permite rodar a apuração de pagamentos só com
    relatório em arquivo (opção --sem-banco), sem DATABASE_URL."""
    return Config(
        database_url=(
            _obrigatoria("DATABASE_URL") if exigir_banco
            else os.getenv("DATABASE_URL", "")
        ),
        tce_base_url=os.getenv(
            "TCE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim"
        ),
        opencnpj_base_url=os.getenv(
            "OPENCNPJ_BASE_URL", "https://api.opencnpj.org"
        ),
        ibge_base_url=os.getenv(
            "IBGE_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades"
        ),
        uf=os.getenv("UF_ALVO", "CE"),
        tce_page_size=_parse_int(
            "TCE_PAGE_SIZE", os.getenv("TCE_PAGE_SIZE", "1000"), minimo=1
        ),
        http_timeout_segundos=_parse_int(
            "HTTP_TIMEOUT_SEGUNDOS", os.getenv("HTTP_TIMEOUT_SEGUNDOS", "30"), minimo=1
        ),
        opencnpj_dias_validade_cache=_parse_int(
            "OPENCNPJ_DIAS_VALIDADE_CACHE",
            os.getenv("OPENCNPJ_DIAS_VALIDADE_CACHE", "30"),
            minimo=1,
        ),
        opencnpj_max_workers=_parse_int(
            "OPENCNPJ_MAX_WORKERS", os.getenv("OPENCNPJ_MAX_WORKERS", "10"), minimo=1
        ),
    )
