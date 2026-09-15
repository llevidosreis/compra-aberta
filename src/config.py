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
    """Falha rápido (fail-fast) se uma variável obrigatória não existir.

    Preferimos quebrar aqui, na inicialização, a deixar o pipeline
    avançar sem DATABASE_URL e falhar de forma confusa lá na frente,
    no meio de uma carga.
    """
    valor = os.getenv(nome)
    if not valor:
        raise RuntimeError(
            f"Variável de ambiente obrigatória '{nome}' não foi definida. "
            f"Confira o arquivo .env (veja .env.example)."
        )
    return valor


@dataclass(frozen=True)
class Config:
    database_url: str
    tce_base_url: str
    opencnpj_base_url: str
    ibge_base_url: str
    uf: str
    tce_page_size: int
    http_timeout_segundos: int
    opencnpj_dias_validade_cache: int
    opencnpj_max_workers: int


def carregar_config() -> Config:
    return Config(
        database_url=_obrigatoria("DATABASE_URL"),
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
        tce_page_size=int(os.getenv("TCE_PAGE_SIZE", "1000")),
        http_timeout_segundos=int(os.getenv("HTTP_TIMEOUT_SEGUNDOS", "30")),
        opencnpj_dias_validade_cache=int(os.getenv("OPENCNPJ_DIAS_VALIDADE_CACHE", "30")),
        opencnpj_max_workers=int(os.getenv("OPENCNPJ_MAX_WORKERS", "10")),
    )
