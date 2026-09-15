"""
Full load: reconstrói a base inteira, para todos os municípios do
Ceará, no período informado.
"""

import logging

from src.clients.opencnpj_client import OpenCNPJClient
from src.clients.tce_client import TCEClient
from src.config import Config
from src.db import conexao
from src.enrich.enrich_empresas import enriquecer_empresas_pendentes
from src.load.load_dimensoes import upsert_municipios
from src.load.load_fatos import transformar_fatos_do_municipio
from src.pipeline.extract_common import carregar_itens, carregar_licitantes

logger = logging.getLogger(__name__)


def executar(config: Config, data_inicio: str, data_fim: str) -> None:
    tce_client = TCEClient(config)
    opencnpj_client = OpenCNPJClient(config)

    logger.info(
        "Iniciando carga completa: %s até %s",
        data_inicio,
        data_fim,
    )

    # ============================================================
    # 1. CARREGAR MUNICÍPIOS
    # ============================================================

    try:
        municipios = tce_client.listar_municipios()

        with conexao(config) as conn:
            upsert_municipios(conn, municipios)

        logger.info(
            "dim_municipio carregada com %d municípios do %s",
            len(municipios),
            config.uf,
        )

    except Exception:
        logger.exception(
            "Falha crítica ao obter/carregar a lista de municípios. "
            "Pipeline não pode continuar."
        )
        raise

    municipios_com_erro = []
    municipios_processados = []

    # ============================================================
    # 2. EXTRAÇÃO DE TODOS OS MUNICÍPIOS
    # ============================================================

    for municipio in municipios:
        logger.info(
            "=== Município %s (codigo_municipio=%s) ===",
            municipio.nome,
            municipio.codigo_municipio,
        )

        try:
            carregar_licitantes(
                config,
                tce_client,
                municipio.codigo_municipio,
                data_inicio,
                data_fim,
                "full",
            )

            carregar_itens(
                config,
                tce_client,
                municipio.codigo_municipio,
                data_inicio,
                data_fim,
                "full",
            )

            municipios_processados.append(municipio)

        except Exception as erro:
            municipios_com_erro.append(municipio.codigo_municipio)

            logger.exception(
                "ERRO na extração do município %s "
                "(codigo=%s). O município será ignorado "
                "e o pipeline continuará. Erro: %s",
                municipio.nome,
                municipio.codigo_municipio,
                erro,
            )

            continue

    # ============================================================
    # 3. ENRIQUECIMENTO OPEN CNPJ
    #    EXECUTADO UMA ÚNICA VEZ
    # ============================================================

    try:
        logger.info(
            "Iniciando enriquecimento global das empresas "
            "após a extração dos municípios."
        )

        total_enriquecido = enriquecer_empresas_pendentes(
            config,
            opencnpj_client,
        )

        logger.info(
            "Enriquecimento global concluído: %d empresas.",
            total_enriquecido,
        )

    except Exception as erro:
        logger.exception(
            "ERRO no enriquecimento global via OpenCNPJ."
        )

        raise RuntimeError(
            "O enriquecimento global via OpenCNPJ falhou."
        ) from erro

    # ============================================================
    # 4. TRANSFORMAÇÃO DOS FATOS
    # ============================================================

    for municipio in municipios_processados:
        try:
            logger.info(
                "Transformando fatos do município %s "
                "(codigo_municipio=%s).",
                municipio.nome,
                municipio.codigo_municipio,
            )

            with conexao(config) as conn:
                transformar_fatos_do_municipio(
                    conn,
                    municipio.codigo_municipio,
                    substituir=True,
                )
                from src.load.load_staging import limpar_staging_municipio
                limpar_staging_municipio(conn, municipio.codigo_municipio)

        except Exception as erro:
            municipios_com_erro.append(municipio.codigo_municipio)

            logger.exception(
                "ERRO ao transformar fatos do município %s "
                "(codigo=%s). Erro: %s",
                municipio.nome,
                municipio.codigo_municipio,
                erro,
            )

    # ============================================================
    # 5. RESULTADO FINAL
    # ============================================================

    if municipios_com_erro:
        municipios_unicos = list(dict.fromkeys(municipios_com_erro))

        logger.error(
            "Carga completa finalizada COM ERROS. "
            "%d município(s) apresentaram falha: %s",
            len(municipios_unicos),
            ", ".join(municipios_unicos),
        )

        raise RuntimeError(
            "A carga completa terminou com erro em "
            f"{len(municipios_unicos)} município(s): "
            f"{', '.join(municipios_unicos)}"
        )

    logger.info(
        "Carga completa finalizada com sucesso. "
        "%d municípios processados.",
        len(municipios_processados),
    )