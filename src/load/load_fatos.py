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
        ON CONFLICT (codigo_municipio, numero_licitacao, numero_sequencial_item_licitacao, cnpj)
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
    sql = """
        INSERT INTO compra_livre.fato_licitacao (
            codigo_municipio, numero_licitacao, data_realizacao_licitacao,
            valor_total_licitacao, quantidade_empresas, quantidade_itens
        )
        SELECT
            p.codigo_municipio,
            p.numero_licitacao,
            MAX(p.data_realizacao_licitacao),
            SUM(p.valor_total_vencido),
            COUNT(DISTINCT p.cnpj),
            (
                SELECT COUNT(*) FROM compra_livre.fato_item_licitacao i
                WHERE i.codigo_municipio = p.codigo_municipio
                  AND i.numero_licitacao = p.numero_licitacao
            )
        FROM compra_livre.fato_participacao_empresa p
        WHERE p.codigo_municipio = %s
        GROUP BY p.codigo_municipio, p.numero_licitacao
        ON CONFLICT (codigo_municipio, numero_licitacao)
        DO UPDATE SET
            data_realizacao_licitacao = EXCLUDED.data_realizacao_licitacao,
            valor_total_licitacao = EXCLUDED.valor_total_licitacao,
            quantidade_empresas = EXCLUDED.quantidade_empresas,
            quantidade_itens = EXCLUDED.quantidade_itens,
            atualizado_em = now()
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (codigo_municipio,))


def transformar_fatos_do_municipio(
    conn, codigo_municipio: str, substituir: bool = False
) -> None:
    """Ordem importa: item -> participação por empresa (agregado) ->
    cabeçalho da licitação (agregado do agregado)."""
    if substituir:
        substituir_fatos_municipio(conn, codigo_municipio)
    transformar_fato_item_licitacao(conn, codigo_municipio)
    transformar_fato_participacao_empresa(conn, codigo_municipio)
    transformar_fato_licitacao(conn, codigo_municipio)
