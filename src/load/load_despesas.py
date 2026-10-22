"""Persistência da apuração de pagamentos no schema compra_livre."""
from datetime import datetime
from typing import Any

from src.db import executar_muitos
from src.enrich.porte_fornecedores import ClassificacaoPorte
from src.transform.apuracao_pagamentos import Apuracao
from src.transform.cruzamento import Pendencia


def upsert_orgaos(conn, orgaos: list[dict[str, Any]], camara: dict[str, str]) -> None:
    executar_muitos(
        conn,
        """
        INSERT INTO compra_livre.dim_orgao_tce
            (codigo_municipio, exercicio_orcamento, codigo_orgao, nome_orgao,
             codigo_tipo_unidade, eh_camara, motivo_camara)
        VALUES %s
        ON CONFLICT (codigo_municipio, exercicio_orcamento, codigo_orgao) DO UPDATE
        SET nome_orgao = EXCLUDED.nome_orgao,
            codigo_tipo_unidade = EXCLUDED.codigo_tipo_unidade,
            eh_camara = EXCLUDED.eh_camara,
            motivo_camara = EXCLUDED.motivo_camara,
            atualizado_em = now()
        """,
        [
            (o["codigo_municipio"], o["exercicio_orcamento"], o["codigo_orgao"],
             o["nome_orgao"], o["codigo_tipo_unidade"],
             o["codigo_orgao"] in camara, camara.get(o["codigo_orgao"]))
            for o in orgaos
        ],
    )


def substituir_empenhos_pagamentos(
    conn,
    codigo_municipio: str,
    exercicio_orcamento: str,
    empenhos: dict[tuple, dict[str, Any]],
    pagamentos: dict[tuple, dict[str, Any]],
) -> None:
    """A coleta é o conjunto completo do recorte: substitui o anterior na mesma transação."""
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM compra_livre.fato_pagamento_despesa "
            "WHERE codigo_municipio = %s AND exercicio_orcamento = %s",
            (codigo_municipio, exercicio_orcamento),
        )
        cursor.execute(
            "DELETE FROM compra_livre.fato_empenho_despesa "
            "WHERE codigo_municipio = %s AND exercicio_orcamento = %s",
            (codigo_municipio, exercicio_orcamento),
        )

    executar_muitos(
        conn,
        """
        INSERT INTO compra_livre.fato_empenho_despesa
            (codigo_municipio, exercicio_orcamento, codigo_orgao,
             codigo_unidade_orcamentaria, data_emissao_empenho, numero_empenho,
             codigo_elemento_despesa, elemento_despesa, documento_original,
             documento, tipo_documento, codigo_tipo_negociante, nome_negociante,
             data_referencia_doc, valor_empenhado)
        VALUES %s
        """,
        [
            (e["codigo_municipio"], e["exercicio_orcamento"], e["codigo_orgao"],
             e["codigo_unidade_orcamentaria"], e["data_emissao_empenho"],
             e["numero_empenho"], e["codigo_elemento_despesa"], e["elemento_despesa"],
             e["documento_original"], e["documento"], e["tipo_documento"],
             e["codigo_tipo_negociante"], e["nome_negociante"],
             e["data_referencia_doc"], e["valor_empenhado"])
            for e in empenhos.values()
        ],
    )
    executar_muitos(
        conn,
        """
        INSERT INTO compra_livre.fato_pagamento_despesa
            (codigo_municipio, exercicio_orcamento, codigo_orgao,
             codigo_unidade_orcamentaria, data_emissao_empenho, numero_empenho,
             numero_sub_empenho, numero_nota_pagamento, data_nota_pagamento,
             data_referencia_doc, valor_nota_pagamento, estado_de_estornado)
        VALUES %s
        """,
        [
            (p["codigo_municipio"], p["exercicio_orcamento"], p["codigo_orgao"],
             p["codigo_unidade_orcamentaria"], p["data_emissao_empenho"],
             p["numero_empenho"], p["numero_sub_empenho"], p["numero_nota_pagamento"],
             p["data_nota_pagamento"], p["data_referencia_doc"],
             p["valor_nota_pagamento"], p["estado_de_estornado"])
            for p in pagamentos.values()
        ],
    )


def carregar_cache_porte(conn, fonte: str, referencia: str) -> dict[str, ClassificacaoPorte]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT cnpj_completo, cnpj_basico, porte_original, porte_codigo,
                   indicador_mei, opcao_simples, razao_social, fonte_cadastral,
                   referencia_base_cadastral, atualizado_na_fonte, consultado_em,
                   situacao_classificacao, detalhe
            FROM compra_livre.cadastro_porte_fornecedor
            WHERE fonte_cadastral = %s AND referencia_base_cadastral = %s
            """,
            (fonte, referencia),
        )
        linhas = cursor.fetchall()
    cache = {}
    for linha in linhas:
        consultado = linha[10]
        item = ClassificacaoPorte(
            *linha[:10],
            consultado_em=consultado.isoformat() if isinstance(consultado, datetime) else str(consultado),
            situacao_classificacao=linha[11],
            detalhe=linha[12],
        )
        cache[item.cnpj_completo] = item
    return cache


def salvar_classificacoes(conn, itens: list[ClassificacaoPorte]) -> None:
    executar_muitos(
        conn,
        """
        INSERT INTO compra_livre.cadastro_porte_fornecedor
            (cnpj_completo, fonte_cadastral, referencia_base_cadastral, cnpj_basico,
             porte_original, porte_codigo, indicador_mei, opcao_simples,
             razao_social, atualizado_na_fonte, consultado_em,
             situacao_classificacao, detalhe)
        VALUES %s
        ON CONFLICT (cnpj_completo, fonte_cadastral, referencia_base_cadastral) DO UPDATE
        SET cnpj_basico = EXCLUDED.cnpj_basico,
            porte_original = EXCLUDED.porte_original,
            porte_codigo = EXCLUDED.porte_codigo,
            indicador_mei = EXCLUDED.indicador_mei,
            opcao_simples = EXCLUDED.opcao_simples,
            razao_social = EXCLUDED.razao_social,
            atualizado_na_fonte = EXCLUDED.atualizado_na_fonte,
            consultado_em = EXCLUDED.consultado_em,
            situacao_classificacao = EXCLUDED.situacao_classificacao,
            detalhe = EXCLUDED.detalhe
        """,
        [
            (i.cnpj_completo, i.fonte_cadastral, i.referencia_base_cadastral,
             i.cnpj_basico, i.porte_original, i.porte_codigo, i.indicador_mei,
             i.opcao_simples, i.razao_social, i.atualizado_na_fonte,
             i.consultado_em, i.situacao_classificacao, i.detalhe)
            for i in itens
        ],
    )


def iniciar_coleta(conn, parametros: dict[str, Any]) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO compra_livre.coleta_pagamentos
                (codigo_municipio, exercicio_orcamento,
                 referencia_empenhos_inicio, referencia_empenhos_fim,
                 referencia_pagamentos_inicio, referencia_pagamentos_fim,
                 regra_mei, fonte_cadastral, referencia_base_cadastral)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                parametros["codigo_municipio"], parametros["exercicio_orcamento"],
                parametros["referencia_empenhos_inicio"], parametros["referencia_empenhos_fim"],
                parametros["referencia_pagamentos_inicio"], parametros["referencia_pagamentos_fim"],
                parametros["regra_mei"], parametros["fonte_cadastral"],
                parametros["referencia_base_cadastral"],
            ),
        )
        return cursor.fetchone()[0]


def registrar_erro_coleta(conn, coleta_id: int, mensagem: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE compra_livre.coleta_pagamentos
            SET status = 'erro', mensagem_erro = %s, finalizado_em = now()
            WHERE id = %s
            """,
            (mensagem[:2000], coleta_id),
        )


def salvar_resultado_coleta(
    conn,
    coleta_id: int,
    apuracao: Apuracao,
    pendencias: list[Pendencia],
    orgaos_excluidos: list[str],
    quantidade_empenhos: int,
    quantidade_pagamentos: int,
) -> None:
    executar_muitos(
        conn,
        """
        INSERT INTO compra_livre.resumo_pagamento_categoria
            (coleta_id, elemento_despesa, categoria_porte, quantidade_pagamentos, valor_pago)
        VALUES %s
        """,
        [
            (coleta_id, elemento, categoria, item["quantidade"], item["valor"])
            for elemento, categorias in apuracao.por_categoria.items()
            for categoria, item in categorias.items()
        ],
    )
    executar_muitos(
        conn,
        """
        INSERT INTO compra_livre.resumo_pagamento_fornecedor
            (coleta_id, elemento_despesa, documento, tipo_documento, nome_negociante,
             categoria_porte, porte_codigo, situacao_classificacao,
             quantidade_pagamentos, valor_pago)
        VALUES %s
        """,
        [
            (coleta_id, f["elemento_despesa"], f["documento"], f["tipo_documento"],
             f["nome_negociante"], f["categoria_porte"], f["porte_codigo"],
             f["situacao_classificacao"], f["quantidade_pagamentos"], f["valor_pago"])
            for f in apuracao.fornecedores
        ],
    )
    executar_muitos(
        conn,
        """
        INSERT INTO compra_livre.pendencia_coleta_pagamentos
            (coleta_id, tipo, chave, detalhe, valor, documento)
        VALUES %s
        """,
        [
            (coleta_id, p.tipo, "|".join("" if v is None else str(v) for v in p.chave),
             p.detalhe, p.valor, p.documento)
            for p in pendencias
        ],
    )
    status = "concluido" if not pendencias and apuracao.reconciliado else "concluido_com_pendencias"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE compra_livre.coleta_pagamentos
            SET status = %s, orgaos_excluidos = %s,
                quantidade_empenhos = %s, quantidade_pagamentos = %s,
                quantidade_pendencias = %s, reconciliado = %s,
                finalizado_em = now()
            WHERE id = %s
            """,
            (status, orgaos_excluidos, quantidade_empenhos, quantidade_pagamentos,
             len(pendencias), apuracao.reconciliado, coleta_id),
        )