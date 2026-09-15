# src/cache/cnpj_cache.py

"""
Cache persistente das consultas ao OpenCNPJ.

A tabela dim_empresa funciona como cache.
"""

from datetime import datetime, timedelta, timezone

from src.config import Config


def cnpjs_pendentes_de_consulta(
    conn,
    config: Config,
    limite: int = 1000,
    excluir: set[str] | None = None,
) -> set[str]:
    limite_validade = (
        datetime.now(timezone.utc)
        - timedelta(
            days=config.opencnpj_dias_validade_cache
        )
    )

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT cnpj
            FROM compra_livre.dim_empresa
            WHERE LENGTH(cnpj) = 14
              AND cnpj ~ '^[0-9]+$'
              AND (
                  enriquecido = FALSE
                  OR consultado_em IS NULL
                  OR consultado_em <= %s
              )
              AND NOT (cnpj = ANY(%s))
            ORDER BY cnpj
            LIMIT %s
            """,
            (
                limite_validade,
                list(excluir or set()),
                limite,
            ),
        )

        ja_em_cache = {
            linha[0]
            for linha in cursor.fetchall()
        }

    return ja_em_cache