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
    data_inicio: str | None = None,
    data_fim: str | None = None,
    codigo_municipio: str | None = None,
) -> set[str]:
    if (data_inicio is None) != (data_fim is None):
        raise ValueError("data_inicio e data_fim devem ser informados juntos.")

    limite_validade = (
        datetime.now(timezone.utc)
        - timedelta(
            days=config.opencnpj_dias_validade_cache
        )
    )

    filtro_periodo = ""
    parametros_periodo: tuple = ()
    if data_inicio is not None and data_fim is not None:
        filtro_periodo = """
              AND EXISTS (
                  SELECT 1
                  FROM compra_livre.fato_item_licitacao f
                  WHERE f.cnpj = compra_livre.dim_empresa.cnpj
                    AND f.data_realizacao_licitacao BETWEEN %s AND %s
        """
        parametros_periodo = (data_inicio, data_fim)
        if codigo_municipio is not None:
            filtro_periodo += "                    AND f.codigo_municipio = %s\n"
            parametros_periodo += (codigo_municipio,)
        filtro_periodo += "              )\n"

    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT cnpj
            FROM compra_livre.dim_empresa
            WHERE LENGTH(cnpj) = 14
              AND cnpj ~ '^[0-9]+$'
              AND (
                  enriquecido = FALSE
                  OR consultado_em IS NULL
                  OR consultado_em <= %s
              )
              {filtro_periodo}
              AND NOT (cnpj = ANY(%s))
            ORDER BY cnpj
            LIMIT %s
            """,
            (
                limite_validade,
                *parametros_periodo,
                list(excluir or set()),
                limite,
            ),
        )

        ja_em_cache = {
            linha[0]
            for linha in cursor.fetchall()
        }

    return ja_em_cache