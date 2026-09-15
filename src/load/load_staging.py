"""
Carga das tabelas de staging, a partir dos registros já normalizados
(ver src/transform/normalize.py).
"""
from src.db import executar_muitos
from src.transform.normalize import para_data, para_numero


def upsert_staging_licitantes(conn, registros: list[dict]) -> None:
    sql = """
        INSERT INTO compra_livre.stg_licitante (
            codigo_municipio, data_realizacao_licitacao, numero_licitacao,
            numero_documento_negociante, codigo_tipo_negociante, nome_negociante,
            endereco_negociante, fone_negociante, cep_negociante,
            nome_municipio_negociante, codigo_uf, data_referencia_doc
        )
        VALUES %s
        ON CONFLICT (codigo_municipio, numero_licitacao, numero_documento_negociante)
        DO UPDATE SET
            nome_negociante = EXCLUDED.nome_negociante,
            endereco_negociante = EXCLUDED.endereco_negociante,
            fone_negociante = EXCLUDED.fone_negociante,
            cep_negociante = EXCLUDED.cep_negociante,
            carregado_em = now()
    """
    linhas = [
        (
            r["codigo_municipio"], r["data_realizacao_licitacao"], r["numero_licitacao"],
            r["numero_documento_negociante"], r["codigo_tipo_negociante"], r["nome_negociante"],
            r["endereco_negociante"], r["fone_negociante"], r["cep_negociante"],
            r["nome_municipio_negociante"], r["codigo_uf"], r["data_referencia_doc"],
        )
        for r in registros
    ]
    executar_muitos(conn, sql, linhas)


def upsert_staging_itens(conn, registros: list[dict]) -> None:
    sql = """
        INSERT INTO compra_livre.stg_item_licitacao (
            codigo_municipio, data_realizacao_licitacao, numero_licitacao,
            numero_sequencial_item_licitacao, numero_documento_negociante,
            descricao_item_licitacao, valor_vencedor_item_licitacao,
            codigo_tipo_negociante, descricao_unidade_item_licitacao,
            numero_quantidade_item_licitacao, valor_unitario_item_licitacao,
            data_referencia_doc
        )
        VALUES %s
        ON CONFLICT (codigo_municipio, numero_licitacao, numero_sequencial_item_licitacao, numero_documento_negociante)
        DO UPDATE SET
            descricao_item_licitacao = EXCLUDED.descricao_item_licitacao,
            valor_vencedor_item_licitacao = EXCLUDED.valor_vencedor_item_licitacao,
            descricao_unidade_item_licitacao = EXCLUDED.descricao_unidade_item_licitacao,
            numero_quantidade_item_licitacao = EXCLUDED.numero_quantidade_item_licitacao,
            valor_unitario_item_licitacao = EXCLUDED.valor_unitario_item_licitacao,
            carregado_em = now()
    """
    linhas = [
        (
            r["codigo_municipio"], r["data_realizacao_licitacao"], r["numero_licitacao"],
            r["numero_sequencial_item_licitacao"], r["numero_documento_negociante"],
            r["descricao_item_licitacao"], r["valor_vencedor_item_licitacao"],
            r["codigo_tipo_negociante"], r["descricao_unidade_item_licitacao"],
            r["numero_quantidade_item_licitacao"], r["valor_unitario_item_licitacao"],
            r["data_referencia_doc"],
        )
        for r in registros
    ]
    executar_muitos(conn, sql, linhas)


def limpar_staging_municipio(conn, codigo_municipio: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM compra_livre.stg_item_licitacao
            WHERE codigo_municipio = %s;
            DELETE FROM compra_livre.stg_licitante
            WHERE codigo_municipio = %s;
            """,
            (codigo_municipio, codigo_municipio),
        )


def limpar_staging_licitantes(conn, codigo_municipio: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM compra_livre.stg_licitante WHERE codigo_municipio = %s",
            (codigo_municipio,),
        )


def limpar_staging_itens(conn, codigo_municipio: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM compra_livre.stg_item_licitacao WHERE codigo_municipio = %s",
            (codigo_municipio,),
        )
