"""
Extração dos dois endpoints do TCE-CE, compartilhada entre full load e
incremental. Suporta retomar de um start_index existente quando há uma
execução anterior marcada como 'em_andamento' (ou seja, que foi
interrompida antes de concluir).
"""
import logging
from collections import Counter

from src.db import conexao
from src.load.load_dimensoes import upsert_empresas_basicas
from src.load.load_staging import (
    limpar_staging_itens,
    limpar_staging_itens_periodo,
    limpar_staging_licitantes,
    upsert_staging_itens,
    upsert_staging_licitantes,
)
from src.pipeline.control import (
    atualizar_start_index,
    buscar_execucao_incompleta,
    concluir_execucao,
    iniciar_execucao,
    reabrir_execucao,
    registrar_erro,
)
from src.quality.validators import validar_itens, validar_licitantes
from src.transform.deduplicate import deduplicar_por_chave
from src.transform.normalize import normalizar_item, normalizar_licitante

logger = logging.getLogger(__name__)


def _registrar_metricas_pagina(
    entidade: str,
    codigo_municipio: str,
    start_index: int,
    recebidos: int,
    validos: int,
    invalidos: list[tuple[dict, str]],
    persistidos: int,
    sem_documento: int = 0,
    erros_normalizacao: list[dict] | None = None,
) -> None:
    logger.info(
        "%s município=%s start_index=%d recebidos=%d validos=%d "
        "invalidos=%d sem_documento=%d persistidos=%d",
        entidade,
        codigo_municipio,
        start_index,
        recebidos,
        validos,
        len(invalidos),
        sem_documento,
        persistidos,
    )
    if invalidos:
        motivos = Counter(motivo for _, motivo in invalidos)
        logger.warning(
            "%s descartes município=%s start_index=%d motivos=%s",
            entidade,
            codigo_municipio,
            start_index,
            dict(motivos),
        )
    motivos_normalizacao = Counter(
        detalhe["motivo"]
        for erros in (erros_normalizacao or [])
        for detalhe in erros.values()
    )
    if motivos_normalizacao:
        logger.warning(
            "%s campos invalidos na normalizacao municipio=%s start_index=%d motivos=%s",
            entidade,
            codigo_municipio,
            start_index,
            dict(motivos_normalizacao),
        )


def carregar_licitantes(
    config,
    tce_client,
    codigo_municipio,
    data_inicio,
    data_fim,
    modo,
    limpar_staging: bool = True,
):
    with conexao(config) as conn:
        pendente = buscar_execucao_incompleta(
            conn, "tce_licitantes", modo, codigo_municipio, data_inicio, data_fim
        )
        if pendente:
            execucao = pendente
            if execucao.status == "erro":
                reabrir_execucao(conn, execucao.id)
                execucao.status = "em_andamento"
            logger.info("Retomando tce_licitantes do município %s em start_index=%d", codigo_municipio, execucao.ultimo_start_index)
        else:
            execucao = iniciar_execucao(conn, "tce_licitantes", modo, codigo_municipio, data_inicio, data_fim)
            if limpar_staging:
                limpar_staging_licitantes(conn, codigo_municipio)

    try:
        for pagina, start_index in tce_client.listar_licitantes_fornecedores(
            codigo_municipio, data_inicio, data_fim, start_index_inicial=execucao.ultimo_start_index
        ):
            normalizados = [normalizar_licitante(r) for r in pagina]
            resultado = validar_licitantes(normalizados)
            deduplicados = deduplicar_por_chave(
                resultado.validos,
                chave=lambda r: (r["codigo_municipio"], r["numero_licitacao"], r["numero_documento_negociante"]),
            )

            with conexao(config) as conn:
                upsert_staging_licitantes(conn, deduplicados)
                cnpjs_nomes = {
                    r["numero_documento_negociante"]: r["nome_negociante"] for r in deduplicados
                }
                upsert_empresas_basicas(conn, cnpjs_nomes)
                atualizar_start_index(conn, execucao.id, start_index + config.tce_page_size)

            _registrar_metricas_pagina(
                "licitantes",
                codigo_municipio,
                start_index,
                len(pagina),
                len(resultado.validos),
                resultado.invalidos,
                len(deduplicados),
                erros_normalizacao=[r.get("_erros_normalizacao", {}) for r in normalizados],
            )

        with conexao(config) as conn:
            concluir_execucao(conn, execucao.id)

    except Exception as erro:
        with conexao(config) as conn:
            registrar_erro(conn, execucao.id, str(erro))
        raise


def carregar_itens(
    config, tce_client, codigo_municipio, data_inicio, data_fim, modo,
    intervalo_staging: tuple[str, str] | None = None,
    limpar_staging: bool = True,
    max_paginas: int | None = None,
):
    if max_paginas is not None and max_paginas < 1:
        raise ValueError("max_paginas deve ser maior que zero.")

    with conexao(config) as conn:
        pendente = buscar_execucao_incompleta(
            conn, "tce_itens", modo, codigo_municipio, data_inicio, data_fim
        )
        if pendente:
            execucao = pendente
            if execucao.status == "erro":
                reabrir_execucao(conn, execucao.id)
                execucao.status = "em_andamento"
            logger.info("Retomando tce_itens do município %s em start_index=%d", codigo_municipio, execucao.ultimo_start_index)
        else:
            execucao = iniciar_execucao(conn, "tce_itens", modo, codigo_municipio, data_inicio, data_fim)
            if limpar_staging:
                if intervalo_staging:
                    limpar_staging_itens_periodo(
                        conn, codigo_municipio, *intervalo_staging
                    )
                else:
                    limpar_staging_itens(conn, codigo_municipio)

    try:
        paginas_processadas = 0
        limite_atingido = False
        for pagina, start_index in tce_client.listar_itens_bens_servicos(
            codigo_municipio, data_inicio, data_fim, start_index_inicial=execucao.ultimo_start_index
        ):
            normalizados = [normalizar_item(r) for r in pagina]
            resultado = validar_itens(normalizados)
            deduplicados = deduplicar_por_chave(
                resultado.validos,
                chave=lambda r: (
                    r["codigo_municipio"], r["numero_licitacao"],
                    r["numero_sequencial_item_licitacao"], r["numero_documento_negociante"],
                ),
            )

            with conexao(config) as conn:
                upsert_staging_itens(conn, deduplicados)
                upsert_empresas_basicas(
                    conn,
                    {
                        r["numero_documento_negociante"]: r.get("nome_negociante") or ""
                        for r in deduplicados
                        if r["numero_documento_negociante"]
                    },
                )
                atualizar_start_index(conn, execucao.id, start_index + config.tce_page_size)

            _registrar_metricas_pagina(
                "itens",
                codigo_municipio,
                start_index,
                len(pagina),
                len(resultado.validos),
                resultado.invalidos,
                len(deduplicados),
                sum(not r["numero_documento_negociante"] for r in deduplicados),
                [r.get("_erros_normalizacao", {}) for r in normalizados],
            )

            paginas_processadas += 1
            if max_paginas is not None and paginas_processadas >= max_paginas:
                limite_atingido = True
                logger.info(
                    "Amostra parcial: %s município=%s páginas=%d; "
                    "checkpoint mantido aberto para retomada.",
                    "itens",
                    codigo_municipio,
                    paginas_processadas,
                )
                break

        if not limite_atingido:
            with conexao(config) as conn:
                concluir_execucao(conn, execucao.id)

    except Exception as erro:
        with conexao(config) as conn:
            registrar_erro(conn, execucao.id, str(erro))
        raise
