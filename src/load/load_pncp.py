import json

from psycopg2.extras import execute_values

from src.db import executar_muitos
from src.load.load_dimensoes import upsert_empresas_basicas


def contratacoes_alteradas(conn, registros: list[dict]) -> set[str]:
    controles = [r["numero_controle_pncp"] for r in registros]
    if not controles:
        return set()
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT numero_controle_pncp, data_atualizacao_pncp
               FROM compra_livre.pncp_contratacao
               WHERE numero_controle_pncp = ANY(%s)""",
            (controles,),
        )
        existentes = dict(cursor.fetchall())
    return {
        r["numero_controle_pncp"]
        for r in registros
        if r["numero_controle_pncp"] not in existentes
        or existentes[r["numero_controle_pncp"]] != r.get("data_atualizacao")
    }


def upsert_contratacoes_pncp(conn, registros: list[dict]) -> set[str]:
    """Preserva criado_em e retorna apenas novos controles/versões alteradas."""
    sql = """
        INSERT INTO compra_livre.pncp_contratacao (
            numero_controle_pncp, numero_compra, ano_compra, sequencial_compra,
            modalidade_codigo, modalidade_nome, data_atualizacao,
            data_publicacao, data_abertura_proposta, data_encerramento_proposta, orgao_cnpj,
            orgao_razao_social, uf, codigo_ibge_municipio, codigo_municipio,
            nome_municipio, objeto_compra, link_edital_pncp,
            objeto_normalizado, exclusivo_me_epp, data_atualizacao_pncp
        )
        SELECT v.numero_controle_pncp, v.numero_compra, v.ano_compra,
               v.sequencial_compra, v.modalidade_codigo, v.modalidade_nome,
               v.data_atualizacao, v.data_publicacao, v.data_abertura_proposta,
               v.data_encerramento_proposta, v.orgao_cnpj,
               v.orgao_razao_social, v.uf, v.codigo_ibge_municipio,
               m.codigo_municipio, v.nome_municipio, v.objeto_compra,
               v.link_edital_pncp, v.objeto_normalizado, v.exclusivo_me_epp,
               v.data_atualizacao
        FROM (VALUES %s) AS v(
            numero_controle_pncp, numero_compra, ano_compra, sequencial_compra,
            modalidade_codigo, modalidade_nome, data_atualizacao,
            data_publicacao, data_abertura_proposta, data_encerramento_proposta, orgao_cnpj,
            orgao_razao_social, uf, codigo_ibge_municipio, nome_municipio,
            objeto_compra, link_edital_pncp, objeto_normalizado, exclusivo_me_epp
        )
        LEFT JOIN compra_livre.dim_municipio m
          ON m.codigo_ibge = v.codigo_ibge_municipio
        ON CONFLICT (numero_controle_pncp) DO UPDATE SET
            numero_compra = EXCLUDED.numero_compra,
            ano_compra = EXCLUDED.ano_compra,
            sequencial_compra = EXCLUDED.sequencial_compra,
            modalidade_codigo = EXCLUDED.modalidade_codigo,
            modalidade_nome = EXCLUDED.modalidade_nome,
            data_atualizacao = EXCLUDED.data_atualizacao,
            data_abertura_proposta = EXCLUDED.data_abertura_proposta,
            data_encerramento_proposta = EXCLUDED.data_encerramento_proposta,
            orgao_cnpj = EXCLUDED.orgao_cnpj,
            orgao_razao_social = EXCLUDED.orgao_razao_social,
            uf = EXCLUDED.uf,
            codigo_ibge_municipio = EXCLUDED.codigo_ibge_municipio,
            codigo_municipio = EXCLUDED.codigo_municipio,
            nome_municipio = EXCLUDED.nome_municipio,
            objeto_compra = EXCLUDED.objeto_compra,
            link_edital_pncp = EXCLUDED.link_edital_pncp,
            objeto_normalizado = EXCLUDED.objeto_normalizado,
            exclusivo_me_epp = EXCLUDED.exclusivo_me_epp,
            data_atualizacao_pncp = EXCLUDED.data_atualizacao_pncp,
            atualizado_em = now()
        WHERE compra_livre.pncp_contratacao.data_atualizacao_pncp
              IS DISTINCT FROM EXCLUDED.data_atualizacao_pncp
        RETURNING numero_controle_pncp
    """
    linhas = [
        (
            r["numero_controle_pncp"], r.get("numero_compra"), r.get("ano_compra"),
            r.get("sequencial_compra"), r.get("modalidade_codigo"), r.get("modalidade_nome"),
            r.get("data_atualizacao"), r.get("data_publicacao"),
            r.get("data_abertura_proposta"), r.get("data_encerramento_proposta"),
            r["orgao_cnpj"], r.get("orgao_razao_social"),
            r.get("uf"), r.get("codigo_ibge_municipio"), r.get("nome_municipio"),
            r["objeto_compra"], r.get("link_edital_pncp"), r.get("objeto_normalizado") or "",
            r.get("exclusivo_me_epp"),
        )
        for r in registros
    ]
    if not linhas:
        return set()
    with conn.cursor() as cursor:
        alteradas = execute_values(cursor, sql, linhas, fetch=True)
    return {linha[0] for linha in alteradas}


def upsert_itens_pncp(conn, registros: list[dict]) -> None:
    sql = """
        INSERT INTO compra_livre.pncp_item (
            numero_controle_pncp, numero_item, descricao, quantidade,
            unidade, valor_estimado, codigo_catalogo, categoria_catalogo,
            objeto_normalizado
        ) VALUES %s
        ON CONFLICT (numero_controle_pncp, numero_item) DO UPDATE SET
            descricao = EXCLUDED.descricao,
            quantidade = EXCLUDED.quantidade,
            unidade = EXCLUDED.unidade,
            valor_estimado = EXCLUDED.valor_estimado,
            codigo_catalogo = EXCLUDED.codigo_catalogo,
            categoria_catalogo = EXCLUDED.categoria_catalogo,
            objeto_normalizado = EXCLUDED.objeto_normalizado,
            atualizado_em = now()
    """
    linhas = [
        (
            r["numero_controle_pncp"], str(r["numero_item"]), r.get("descricao"),
            r.get("quantidade"), r.get("unidade"), r.get("valor_estimado"),
            str(r["codigo_catalogo"]) if r.get("codigo_catalogo") is not None else None,
            r.get("categoria_catalogo"), r.get("objeto_normalizado") or "",
        )
        for r in registros
    ]
    executar_muitos(conn, sql, linhas)


def upsert_perfil_cliente(conn, perfil: dict) -> None:
    """Cria a dimensão básica antes da FK do perfil; OpenCNPJ enriquece depois."""
    cnpj = perfil["cnpj"]
    upsert_empresas_basicas(conn, {cnpj: perfil.get("razao_social") or ""})
    executar_muitos(
        conn,
        """INSERT INTO compra_livre.perfil_cliente
               (cnpj, produtos_palavras_chave, ufs_atuacao, municipios_atuacao, ativo)
           VALUES %s
           ON CONFLICT (cnpj) DO UPDATE SET
               produtos_palavras_chave = EXCLUDED.produtos_palavras_chave,
               ufs_atuacao = EXCLUDED.ufs_atuacao,
               municipios_atuacao = EXCLUDED.municipios_atuacao,
               ativo = EXCLUDED.ativo, atualizado_em = now()""",
        [(
            cnpj, perfil.get("produtos_palavras_chave") or [],
            perfil.get("ufs_atuacao") or [], perfil.get("municipios_atuacao") or [],
            perfil.get("ativo", True),
        )],
    )


def upsert_matches(conn, numero_controle: str, matches: list[dict]) -> None:
    linhas = [
        (numero_controle, match["cnpj"], match["score"], json.dumps(match["motivos"], ensure_ascii=True))
        for match in matches
    ]
    executar_muitos(
        conn,
        """INSERT INTO compra_livre.pncp_match
               (numero_controle_pncp, cnpj, score, motivos)
           VALUES %s
           ON CONFLICT (numero_controle_pncp, cnpj) DO UPDATE SET
               score = EXCLUDED.score, motivos = EXCLUDED.motivos""",
        linhas,
    )