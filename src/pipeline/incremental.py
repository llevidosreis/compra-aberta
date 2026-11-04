"""
Carga incremental: processa uma janela recente de datas.
"""

import logging
from datetime import date, timedelta
from time import monotonic

from src.clients.opencnpj_client import OpenCNPJClient
from src.clients.tce_client import TCEClient
from src.config import Config
from src.db import conexao
from src.enrich.enrich_empresas import enriquecer_empresas_pendentes
from src.load.load_dimensoes import upsert_municipios
from src.load.load_fatos import transformar_fatos_do_municipio
from src.pipeline.extract_common import carregar_itens, carregar_licitantes

logger = logging.getLogger(__name__)


def executar(
    config: Config,
    dias_retroativos: int = 7,
    *,
    enriquecer: bool = True,
) -> None:
    if dias_retroativos < 0:
        raise ValueError("dias_retroativos não pode ser negativo")

    hoje = date.today()

    data_inicio = (
        hoje - timedelta(days=dias_retroativos)
    ).isoformat()

    data_fim = hoje.isoformat()

    tce_client = TCEClient(config)
    opencnpj_client = OpenCNPJClient(config) if enriquecer else None
    inicio_execucao = monotonic()

    logger.info(
        "Iniciando carga incremental: %s até %s",
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
            "Carga incremental preparada para %d municípios",
            len(municipios),
        )

    except Exception:
        logger.exception(
            "Falha crítica ao obter/carregar a lista de municípios. "
            "Carga incremental não pode continuar."
        )
        raise

    municipios_com_erro = []
    municipios_processados = []

    # ============================================================
    # 2. EXTRAÇÃO E PUBLICAÇÃO PROGRESSIVA POR MUNICÍPIO
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
                "incremental",
                limpar_staging=False,
            )

            carregar_itens(
                config,
                tce_client,
                municipio.codigo_municipio,
                data_inicio,
                data_fim,
                "incremental",
                limpar_staging=False,
            )

            logger.info(
                "Transformando e publicando fatos do município %s (código=%s).",
                municipio.nome,
                municipio.codigo_municipio,
            )
            with conexao(config) as conn:
                transformar_fatos_do_municipio(
                    conn,
                    municipio.codigo_municipio,
                )

            municipios_processados.append(municipio)
            logger.info(
                "Município publicado: código=%s progresso=%d/%d",
                municipio.codigo_municipio,
                len(municipios_processados),
                len(municipios),
            )

        except Exception as erro:
            municipios_com_erro.append(municipio.codigo_municipio)

            logger.exception(
                "ERRO ao processar o município %s "
                "(codigo=%s). O município será ignorado "
                "e a carga continuará. Erro: %s",
                municipio.nome,
                municipio.codigo_municipio,
                erro,
            )

            continue

    # ============================================================
    # ============================================================
    # 3. ENRIQUECIMENTO OPEN CNPJ (OPCIONAL)
    # ============================================================

    erro_enriquecimento = None
    if enriquecer:
        try:
            logger.info("Iniciando enriquecimento global via OpenCNPJ.")
            total_enriquecido = enriquecer_empresas_pendentes(
                config,
                opencnpj_client,
                data_inicio=data_inicio,
                data_fim=data_fim,
            )
            logger.info("Enriquecimento global concluído: %d empresas.", total_enriquecido)
        except Exception as erro:
            logger.exception("ERRO no enriquecimento global via OpenCNPJ.")
            erro_enriquecimento = erro
    else:
        logger.info(
            "Enriquecimento OpenCNPJ adiado; use o comando enriquecer "
            "com o período carregado."
        )

    # ============================================================
    # 5. RESULTADO FINAL
    # ============================================================

    if municipios_com_erro:
        municipios_unicos = list(dict.fromkeys(municipios_com_erro))

        logger.error(
            "Carga incremental finalizada COM ERROS. "
            "%d município(s) apresentaram falha: %s",
            len(municipios_unicos),
            ", ".join(municipios_unicos),
        )

        raise RuntimeError(
            "A carga incremental terminou com erro em "
            f"{len(municipios_unicos)} município(s): "
            f"{', '.join(municipios_unicos)}"
        )

    if erro_enriquecimento:
        raise RuntimeError(
            "Os fatos TCE foram processados, mas o enriquecimento OpenCNPJ falhou."
        ) from erro_enriquecimento

    logger.info(
        "Carga incremental finalizada com sucesso. %d/%d municípios publicados "
        "em %.1f segundos.",
        len(municipios_processados),
        len(municipios),
        monotonic() - inicio_execucao,
    )