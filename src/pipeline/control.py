"""
Controle de execuções do pipeline (tabela pipeline_execucoes).

Existe para responder duas perguntas em qualquer momento:
  1. Se o processo cair no meio de uma paginação, de que start_index
     retomar, sem duplicar nem pular registros?
  2. Essa fonte/município já foi carregado hoje, ou ainda está em
     andamento?
"""
from dataclasses import dataclass


@dataclass
class Execucao:
    id: int
    ultimo_start_index: int
    status: str


def iniciar_execucao(
    conn, fonte: str, modo: str, codigo_municipio: str | None,
    data_inicio: str | None, data_fim: str | None,
) -> Execucao:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO compra_livre.pipeline_execucoes
                (fonte, modo, codigo_municipio, data_inicio, data_fim, status)
            VALUES (%s, %s, %s, %s, %s, 'em_andamento')
            RETURNING id, ultimo_start_index, status
            """,
            (fonte, modo, codigo_municipio, data_inicio, data_fim),
        )
        id_, start_index, status = cursor.fetchone()
    return Execucao(id=id_, ultimo_start_index=start_index, status=status)


def atualizar_start_index(conn, execucao_id: int, start_index: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE compra_livre.pipeline_execucoes
            SET ultimo_start_index = %s
            WHERE id = %s
            """,
            (start_index, execucao_id),
        )


def concluir_execucao(conn, execucao_id: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE compra_livre.pipeline_execucoes
            SET status = 'concluido', finalizado_em = now()
            WHERE id = %s
            """,
            (execucao_id,),
        )


def registrar_erro(conn, execucao_id: int, mensagem: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE compra_livre.pipeline_execucoes
            SET status = 'erro', mensagem_erro = %s, finalizado_em = now()
            WHERE id = %s
            """,
            (mensagem[:2000], execucao_id),
        )


def buscar_execucao_incompleta(
    conn, fonte: str, modo: str, codigo_municipio: str | None,
    data_inicio: str | None, data_fim: str | None
) -> Execucao | None:
    """Usado pelo modo incremental para saber se dá pra retomar de onde
    parou em vez de começar do zero.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, ultimo_start_index, status
            FROM compra_livre.pipeline_execucoes
            WHERE fonte = %s
              AND codigo_municipio IS NOT DISTINCT FROM %s
              AND modo = %s
              AND data_inicio IS NOT DISTINCT FROM %s
              AND data_fim IS NOT DISTINCT FROM %s
              AND status = 'em_andamento'
            ORDER BY iniciado_em DESC
            LIMIT 1
            """,
            (fonte, codigo_municipio, modo, data_inicio, data_fim),
        )
        linha = cursor.fetchone()
    if not linha:
        return None
    return Execucao(id=linha[0], ultimo_start_index=linha[1], status=linha[2])
