"""Persistência das dotações associadas às licitações."""

from src.db import executar_muitos
from src.transform.naturezas import NATUREZAS_DESPESA, codigo_natureza


def _deduplicar_dotacoes(registros: list[dict]) -> list[dict]:
    unicos: dict[tuple, dict] = {}
    for registro in registros:
        chave = (
            registro["codigo_municipio"], registro["numero_licitacao"],
            registro["exercicio_orcamento"], registro["codigo_orgao"],
            registro["codigo_unidade_orcamentaria"], registro["codigo_funcao"],
            registro["codigo_subfuncao"], registro["codigo_programa"],
            registro["codigo_projeto_atividade"], registro["numero_projeto_atividade"],
            registro["numero_subprojeto_atividade"], registro["codigo_elemento_despesa"],
            registro["tipo_fonte"], registro["codigo_fonte"],
            registro["data_referencia_doc"],
        )
        unicos[chave] = registro
    return list(unicos.values())


def inserir_naturezas_catalogo(conn) -> None:
    linhas = list(NATUREZAS_DESPESA.items())
    executar_muitos(
        conn,
        """
        INSERT INTO compra_livre.dim_natureza_despesa (codigo_natureza, nome_natureza)
        VALUES %s
        ON CONFLICT (codigo_natureza) DO UPDATE
        SET nome_natureza = EXCLUDED.nome_natureza, atualizado_em = now()
        """,
        linhas,
    )


def upsert_dotacoes(conn, registros: list[dict]) -> None:
    linhas = [
        (
            r["codigo_municipio"], r["numero_licitacao"],
            r["data_realizacao_licitacao"], r["exercicio_orcamento"],
            r["codigo_orgao"], r["codigo_unidade_orcamentaria"],
            r["codigo_funcao"], r["codigo_subfuncao"], r["codigo_programa"],
            r["codigo_projeto_atividade"], r["numero_projeto_atividade"],
            r["numero_subprojeto_atividade"], r["codigo_elemento_despesa"],
            codigo_natureza(r["codigo_elemento_despesa"]), r["tipo_fonte"],
            r["codigo_fonte"], r["valor_dotacao_doc"], r["data_referencia_doc"],
        )
        for r in _deduplicar_dotacoes(registros)
    ]
    executar_muitos(
        conn,
        """
        INSERT INTO compra_livre.fato_dotacao_licitacao (
            codigo_municipio, numero_licitacao, data_realizacao_licitacao,
            exercicio_orcamento, codigo_orgao, codigo_unidade_orcamentaria,
            codigo_funcao, codigo_subfuncao, codigo_programa,
            codigo_projeto_atividade, numero_projeto_atividade,
            numero_subprojeto_atividade, codigo_elemento_despesa,
            codigo_natureza, tipo_fonte, codigo_fonte, valor_dotacao_doc,
            data_referencia_doc
        )
        VALUES %s
        ON CONFLICT ON CONSTRAINT uq_fato_dotacao_licitacao DO UPDATE SET
            data_realizacao_licitacao = EXCLUDED.data_realizacao_licitacao,
            codigo_funcao = EXCLUDED.codigo_funcao,
            codigo_subfuncao = EXCLUDED.codigo_subfuncao,
            codigo_programa = EXCLUDED.codigo_programa,
            codigo_projeto_atividade = EXCLUDED.codigo_projeto_atividade,
            numero_projeto_atividade = EXCLUDED.numero_projeto_atividade,
            numero_subprojeto_atividade = EXCLUDED.numero_subprojeto_atividade,
            codigo_natureza = EXCLUDED.codigo_natureza,
            tipo_fonte = EXCLUDED.tipo_fonte,
            valor_dotacao_doc = EXCLUDED.valor_dotacao_doc,
            atualizado_em = now()
        """,
        linhas,
    )
