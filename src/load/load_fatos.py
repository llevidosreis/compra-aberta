"""
Transforma o dado de staging nas tabelas fato finais.

Feito em SQL puro (INSERT ... SELECT ... GROUP BY) em vez de trazer
tudo para Python e agregar com pandas/loops: o volume de dados de
licitações públicas é exatamente o tipo de operação em que o banco é
mais eficiente que reprocessar em memória, e evita termos que carregar
todo o staging na aplicação de uma vez.
"""


def substituir_fatos_municipio(conn, codigo_municipio: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM compra_livre.fato_licitacao WHERE codigo_municipio = %s;
            DELETE FROM compra_livre.fato_participacao_empresa WHERE codigo_municipio = %s;
            DELETE FROM compra_livre.fato_item_licitacao WHERE codigo_municipio = %s;
            """,
            (codigo_municipio, codigo_municipio, codigo_municipio),
        )


def substituir_fatos_municipio_periodo(
    conn, codigo_municipio: str, data_inicio: str, data_fim: str
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TEMP TABLE licitacoes_reprocessar
            ON COMMIT DROP
            AS
            SELECT codigo_municipio, numero_licitacao
            FROM compra_livre.fato_licitacao
            WHERE codigo_municipio = %s
              AND data_realizacao_licitacao BETWEEN %s AND %s
            UNION
            SELECT codigo_municipio, numero_licitacao
            FROM compra_livre.fato_item_licitacao
            WHERE codigo_municipio = %s
              AND data_realizacao_licitacao BETWEEN %s AND %s
            UNION
            SELECT codigo_municipio, numero_licitacao
            FROM compra_livre.fato_participacao_empresa
            WHERE codigo_municipio = %s
              AND data_realizacao_licitacao BETWEEN %s AND %s
            UNION
            SELECT codigo_municipio, numero_licitacao
            FROM compra_livre.stg_item_licitacao
            WHERE codigo_municipio = %s
              AND data_realizacao_licitacao BETWEEN %s AND %s
            """,
            (
                codigo_municipio, data_inicio, data_fim,
                codigo_municipio, data_inicio, data_fim,
                codigo_municipio, data_inicio, data_fim,
                codigo_municipio, data_inicio, data_fim,
            ),
        )
        cursor.execute(
            """
            DELETE FROM compra_livre.fato_participacao_empresa p
            USING licitacoes_reprocessar r
            WHERE p.codigo_municipio = r.codigo_municipio
              AND p.numero_licitacao = r.numero_licitacao
            """
        )
        cursor.execute(
            """
            DELETE FROM compra_livre.fato_item_licitacao i
            USING licitacoes_reprocessar r
            WHERE i.codigo_municipio = r.codigo_municipio
              AND i.numero_licitacao = r.numero_licitacao
            """
        )
        cursor.execute(
            """
            DELETE FROM compra_livre.fato_licitacao f
            USING licitacoes_reprocessar r
            WHERE f.codigo_municipio = r.codigo_municipio
              AND f.numero_licitacao = r.numero_licitacao
            """
        )
        cursor.execute(
            """
            INSERT INTO compra_livre.fato_item_licitacao (
                codigo_municipio, numero_licitacao, numero_sequencial_item_licitacao,
                cnpj, descricao_item_licitacao, unidade, quantidade, valor_unitario,
                valor_vencedor, data_realizacao_licitacao
            )
            SELECT
                s.codigo_municipio, s.numero_licitacao,
                s.numero_sequencial_item_licitacao,
                s.numero_documento_negociante, s.descricao_item_licitacao,
                s.descricao_unidade_item_licitacao,
                s.numero_quantidade_item_licitacao,
                s.valor_unitario_item_licitacao,
                s.valor_vencedor_item_licitacao,
                s.data_realizacao_licitacao
            FROM compra_livre.stg_item_licitacao s
            JOIN licitacoes_reprocessar r
              ON r.codigo_municipio = s.codigo_municipio
             AND r.numero_licitacao = s.numero_licitacao
            """
        )
        cursor.execute(
            """
            INSERT INTO compra_livre.fato_participacao_empresa (
                codigo_municipio, numero_licitacao, cnpj, valor_total_vencido,
                quantidade_itens_vencidos, data_realizacao_licitacao
            )
            SELECT
                i.codigo_municipio, i.numero_licitacao, i.cnpj,
                SUM(COALESCE(i.valor_vencedor, 0)), COUNT(*),
                MAX(i.data_realizacao_licitacao)
            FROM compra_livre.fato_item_licitacao i
            JOIN licitacoes_reprocessar r
              ON r.codigo_municipio = i.codigo_municipio
             AND r.numero_licitacao = i.numero_licitacao
            WHERE i.cnpj IS NOT NULL
            GROUP BY i.codigo_municipio, i.numero_licitacao, i.cnpj
            """
        )
        cursor.execute(
            """
            INSERT INTO compra_livre.fato_licitacao (
                codigo_municipio, numero_licitacao, data_realizacao_licitacao,
                valor_total_licitacao, quantidade_empresas, quantidade_itens
            )
            SELECT
                i.codigo_municipio, i.numero_licitacao,
                MAX(i.data_realizacao_licitacao),
                SUM(COALESCE(i.valor_vencedor, 0)),
                COUNT(DISTINCT i.cnpj), COUNT(*)
            FROM compra_livre.fato_item_licitacao i
            JOIN licitacoes_reprocessar r
              ON r.codigo_municipio = i.codigo_municipio
             AND r.numero_licitacao = i.numero_licitacao
            GROUP BY i.codigo_municipio, i.numero_licitacao
            """
        )


def transformar_fato_item_licitacao(conn, codigo_municipio: str) -> None:
    sql = """
        INSERT INTO compra_livre.fato_item_licitacao (
            codigo_municipio, numero_licitacao, numero_sequencial_item_licitacao,
            cnpj, descricao_item_licitacao, unidade, quantidade, valor_unitario,
            valor_vencedor, data_realizacao_licitacao
        )
        SELECT
            s.codigo_municipio,
            s.numero_licitacao,
            s.numero_sequencial_item_licitacao,
            s.numero_documento_negociante,
            s.descricao_item_licitacao,
            s.descricao_unidade_item_licitacao,
            s.numero_quantidade_item_licitacao,
            s.valor_unitario_item_licitacao,
            s.valor_vencedor_item_licitacao,
            s.data_realizacao_licitacao
        FROM compra_livre.stg_item_licitacao s
        WHERE s.codigo_municipio = %s
        ON CONFLICT (
            codigo_municipio, numero_licitacao, numero_sequencial_item_licitacao,
            (COALESCE(cnpj, ''))
        )
        DO UPDATE SET
            descricao_item_licitacao = EXCLUDED.descricao_item_licitacao,
            unidade = EXCLUDED.unidade,
            quantidade = EXCLUDED.quantidade,
            valor_unitario = EXCLUDED.valor_unitario,
            valor_vencedor = EXCLUDED.valor_vencedor,
            atualizado_em = now()
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (codigo_municipio,))


def transformar_fato_participacao_empresa(conn, codigo_municipio: str) -> None:
    """Implementa literalmente a regra da seção 2 do documento: quando
    o mesmo par CNPJ + numero_licitacao aparece em mais de um item,
    soma valor_vencedor_item_licitacao.
    """
    sql = """
        INSERT INTO compra_livre.fato_participacao_empresa (
            codigo_municipio, numero_licitacao, cnpj,
            valor_total_vencido, quantidade_itens_vencidos, data_realizacao_licitacao
        )
        SELECT
            codigo_municipio,
            numero_licitacao,
            cnpj,
            SUM(COALESCE(valor_vencedor, 0)),
            COUNT(*),
            MAX(data_realizacao_licitacao)
        FROM compra_livre.fato_item_licitacao
        WHERE codigo_municipio = %s
          AND cnpj IS NOT NULL
        GROUP BY codigo_municipio, numero_licitacao, cnpj
        ON CONFLICT (codigo_municipio, numero_licitacao, cnpj)
        DO UPDATE SET
            valor_total_vencido = EXCLUDED.valor_total_vencido,
            quantidade_itens_vencidos = EXCLUDED.quantidade_itens_vencidos,
            data_realizacao_licitacao = EXCLUDED.data_realizacao_licitacao,
            atualizado_em = now()
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (codigo_municipio,))


def transformar_fato_licitacao(conn, codigo_municipio: str) -> None:
    """Agrega todos os itens, usando participações como fallback para
    licitações que não tenham itens disponíveis.
    """
    sql = """
        INSERT INTO compra_livre.fato_licitacao (
            codigo_municipio, numero_licitacao, data_realizacao_licitacao,
            valor_total_licitacao, quantidade_empresas, quantidade_itens
        )
        WITH itens AS (
            SELECT
                codigo_municipio,
                numero_licitacao,
                MAX(data_realizacao_licitacao) AS data_realizacao_licitacao,
                SUM(COALESCE(valor_vencedor, 0)) AS valor_total_licitacao,
                COUNT(DISTINCT cnpj) AS quantidade_empresas,
                COUNT(*) AS quantidade_itens
            FROM compra_livre.fato_item_licitacao
            WHERE codigo_municipio = %s
            GROUP BY codigo_municipio, numero_licitacao
        ),
        participacoes AS (
            SELECT
                codigo_municipio,
                numero_licitacao,
                MAX(data_realizacao_licitacao) AS data_realizacao_licitacao,
                SUM(valor_total_vencido) AS valor_total_licitacao,
                COUNT(DISTINCT cnpj) AS quantidade_empresas
            FROM compra_livre.fato_participacao_empresa
            WHERE codigo_municipio = %s
            GROUP BY codigo_municipio, numero_licitacao
        )
        SELECT
            COALESCE(i.codigo_municipio, p.codigo_municipio),
            COALESCE(i.numero_licitacao, p.numero_licitacao),
            COALESCE(i.data_realizacao_licitacao, p.data_realizacao_licitacao),
            COALESCE(i.valor_total_licitacao, p.valor_total_licitacao),
            COALESCE(i.quantidade_empresas, p.quantidade_empresas),
            COALESCE(i.quantidade_itens, 0)
        FROM itens i
        FULL OUTER JOIN participacoes p
          ON p.codigo_municipio = i.codigo_municipio
         AND p.numero_licitacao = i.numero_licitacao
        ON CONFLICT (codigo_municipio, numero_licitacao)
        DO UPDATE SET
            data_realizacao_licitacao = EXCLUDED.data_realizacao_licitacao,
            valor_total_licitacao = EXCLUDED.valor_total_licitacao,
            quantidade_empresas = EXCLUDED.quantidade_empresas,
            quantidade_itens = EXCLUDED.quantidade_itens,
            atualizado_em = now()
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (codigo_municipio, codigo_municipio))


def transformar_fatos_do_municipio(
    conn, codigo_municipio: str, substituir: bool = False
) -> None:
    """Ordem importa: itens -> participação conhecida -> cabeçalho."""
    if substituir:
        substituir_fatos_municipio(conn, codigo_municipio)
    transformar_fato_item_licitacao(conn, codigo_municipio)
    transformar_fato_participacao_empresa(conn, codigo_municipio)
    transformar_fato_licitacao(conn, codigo_municipio)
