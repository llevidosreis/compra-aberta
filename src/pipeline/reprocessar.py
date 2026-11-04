"""Reprocessamento direcionado dos itens de um município e período."""
from datetime import date
import logging

from src.clients.tce_client import TCEClient
from src.config import Config
from src.db import conexao
from src.load.load_fatos import substituir_fatos_municipio_periodo
from src.load.load_staging import limpar_staging_itens_periodo
from src.pipeline.extract_common import carregar_itens

logger = logging.getLogger(__name__)


def executar(
    config: Config,
    codigo_municipio: str,
    data_inicio: str,
    data_fim: str,
) -> None:
    inicio = date.fromisoformat(data_inicio)
    fim = date.fromisoformat(data_fim)
    if inicio > fim:
        raise ValueError("data_inicio não pode ser posterior a data_fim")
    if not codigo_municipio.strip():
        raise ValueError("codigo_municipio não pode ser vazio")
    codigo_municipio = codigo_municipio.strip()

    cliente = TCEClient(config)
    carregar_itens(
        config,
        cliente,
        codigo_municipio,
        data_inicio,
        data_fim,
        "reprocessamento",
        intervalo_staging=(data_inicio, data_fim),
    )

    with conexao(config) as conn:
        substituir_fatos_municipio_periodo(
            conn, codigo_municipio, data_inicio, data_fim
        )
        limpar_staging_itens_periodo(
            conn, codigo_municipio, data_inicio, data_fim
        )

    logger.info(
        "Reprocessamento concluído: município=%s período=%s a %s",
        codigo_municipio,
        data_inicio,
        data_fim,
    )
