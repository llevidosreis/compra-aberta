"""
Conexão com o Postgres do Supabase.

Optamos por psycopg2 puro (sem ORM) em vez do cliente REST do
Supabase: o pipeline faz upserts em lote com ON CONFLICT, agregações
em SQL e transações — tudo isso é direto em SQL e ficaria mais
complicado (e mais lento) via REST/PostgREST, que foi pensado para
acesso do frontend, não para carga em massa.
"""
from contextlib import contextmanager
from collections.abc import Iterator

import psycopg2
import psycopg2.extras

from src.config import Config


@contextmanager
def conexao(config: Config) -> Iterator[psycopg2.extensions.connection]:
    """Abre uma conexão, garante commit no sucesso e rollback no erro."""
    conn = psycopg2.connect(config.database_url, client_encoding="UTF8")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def executar_muitos(conn, sql: str, linhas: list[tuple]) -> None:
    """Wrapper fino sobre execute_values, para upserts em lote."""
    if not linhas:
        return
    with conn.cursor() as cursor:
        psycopg2.extras.execute_values(cursor, sql, linhas)
