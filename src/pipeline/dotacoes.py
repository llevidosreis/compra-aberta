"""Coleta incremental das dotações sem repetir a carga principal."""

import logging

from src.clients.tce_client import TCEClient
from src.config import Config
from src.db import conexao
from src.load.load_dotacoes import inserir_naturezas_catalogo, upsert_dotacoes
from src.pipeline.control import (
    atualizar_start_index,
    buscar_execucao_incompleta,
    concluir_execucao,
    iniciar_execucao,
    registrar_erro,
)
from src.transform.normalize import normalizar_dotacao

logger = logging.getLogger(__name__)


def executar(config: Config, data_inicio: str, data_fim: str) -> None:
    client = TCEClient(config)
    with conexao(config) as conn:
        inserir_naturezas_catalogo(conn)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT codigo_municipio FROM compra_livre.dim_municipio ORDER BY codigo_municipio"
        )
        municipios = [row[0] for row in cursor.fetchall()]

    for indice, codigo_municipio in enumerate(municipios, start=1):
        total = 0
        execucao = None
        try:
            with conexao(config) as conn:
                execucao = buscar_execucao_incompleta(
                    conn, "tce_dotacoes", "dotacoes",
                    codigo_municipio, data_inicio, data_fim,
                )
                if execucao is None:
                    execucao = iniciar_execucao(
                        conn, "tce_dotacoes", "dotacoes",
                        codigo_municipio, data_inicio, data_fim,
                    )

            for pagina, start_index in client.listar_dotacoes_utilizadas(
                codigo_municipio, data_inicio, data_fim,
                start_index_inicial=execucao.ultimo_start_index,
            ):
                registros = [normalizar_dotacao(item) for item in pagina]
                with conexao(config) as conn:
                    upsert_dotacoes(conn, registros)
                    atualizar_start_index(
                        conn, execucao.id, start_index + len(pagina)
                    )
                total += len(registros)

            with conexao(config) as conn:
                concluir_execucao(conn, execucao.id)
        except Exception as erro:
            if execucao is not None:
                with conexao(config) as conn:
                    registrar_erro(conn, execucao.id, str(erro))
            logger.exception("Falha nas dotações do município=%s", codigo_municipio)
            raise
        logger.info(
            "Dotações município=%s registros=%d progresso=%d/%d",
            codigo_municipio, total, indice, len(municipios),
        )
