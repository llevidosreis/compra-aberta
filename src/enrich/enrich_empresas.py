# src/enrich/enrich_empresas.py

"""
Enriquece empresas identificadas nas licitações via OpenCNPJ.

Somente CNPJs (14 dígitos) são enviados ao OpenCNPJ.
CPFs (11 dígitos) podem continuar existindo nos dados do TCE,
mas não são tratados como empresas para fins de enriquecimento.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.cache.cnpj_cache import cnpjs_pendentes_de_consulta
from src.clients.opencnpj_client import DadosEmpresa, OpenCNPJClient
from src.config import Config
from src.db import conexao
from src.load.load_dimensoes import upsert_empresas_enriquecidas

logger = logging.getLogger(__name__)

TAMANHO_LOTE = 25


def enriquecer_empresas_pendentes(
    config: Config,
    client: OpenCNPJClient,
) -> int:
    """
    Consulta somente CNPJs (14 dígitos) sem cache válido.
    """

    total_enriquecido = 0
    falhos_nesta_execucao: set[str] = set()
    while True:
        with conexao(config) as conn:
            pendentes = cnpjs_pendentes_de_consulta(
                conn,
                config,
                limite=1000,
                excluir=falhos_nesta_execucao,
            )
        if not pendentes:
            break

        logger.info("%d CNPJs pendentes no lote atual.", len(pendentes))
        lote: list[DadosEmpresa] = []
        enriquecidos_neste_lote = 0
        with ThreadPoolExecutor(
            max_workers=config.opencnpj_max_workers
        ) as executor:
            futuros = {
                executor.submit(client.consultar, cnpj): cnpj
                for cnpj in sorted(pendentes)
            }
            for futuro in as_completed(futuros):
                cnpj = futuros[futuro]
                try:
                    lote.append(futuro.result())
                except Exception:
                    falhos_nesta_execucao.add(cnpj)
                    logger.exception("Erro ao consultar CNPJ %s no OpenCNPJ.", cnpj)
                if len(lote) >= TAMANHO_LOTE:
                    _gravar_lote(config, lote)
                    total_enriquecido += len(lote)
                    enriquecidos_neste_lote += len(lote)
                    lote = []
        if lote:
            _gravar_lote(config, lote)
            total_enriquecido += len(lote)
            enriquecidos_neste_lote += len(lote)
        if enriquecidos_neste_lote == 0:
            logger.warning("Nenhum CNPJ do lote pôde ser enriquecido; interrompendo para evitar retry infinito.")
            break

    logger.info(
        "Enriquecimento concluído: %d empresas.",
        total_enriquecido,
    )

    return total_enriquecido


def _gravar_lote(
    config: Config,
    lote: list[DadosEmpresa],
) -> None:
    """Grava um lote de empresas no PostgreSQL."""

    if not lote:
        return

    with conexao(config) as conn:
        upsert_empresas_enriquecidas(conn, lote)