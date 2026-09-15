"""
Upsert das dimensões: municípios e empresas.

Usamos "INSERT ... ON CONFLICT DO UPDATE" (upsert) em vez de DELETE +
INSERT: preserva o histórico de enriquecimento das empresas (não
apaga o cache) e permite rodar o pipeline várias vezes sem duplicar
nem perder dado já carregado — exigência explícita do projeto.
"""
from src.clients.opencnpj_client import DadosEmpresa
from src.clients.tce_client import MunicipioTCE
from src.db import executar_muitos


def upsert_municipios(conn, municipios: list[MunicipioTCE]) -> None:
    sql = """
        INSERT INTO compra_livre.dim_municipio (codigo_municipio, nome, uf, codigo_ibge, codigo_geonames)
        VALUES %s
        ON CONFLICT (codigo_municipio) DO UPDATE SET
            nome = EXCLUDED.nome,
            uf = EXCLUDED.uf,
            codigo_ibge = EXCLUDED.codigo_ibge,
            codigo_geonames = EXCLUDED.codigo_geonames,
            atualizado_em = now()
    """
    linhas = [
        (m.codigo_municipio, m.nome, m.uf, m.codigo_ibge, m.codigo_geonames)
        for m in municipios
    ]
    executar_muitos(conn, sql, linhas)


def upsert_empresas_basicas(conn, cnpjs_nomes: dict[str, str]) -> None:
    """Garante que toda empresa citada em uma licitação já exista em
    dim_empresa (mesmo sem enriquecimento ainda), usando o nome que
    veio do próprio TCE-CE como valor provisório. Isso evita erro de
    chave estrangeira ao carregar os fatos antes do enriquecimento
    rodar.
    """
    sql = """
        INSERT INTO compra_livre.dim_empresa (cnpj, razao_social)
        VALUES %s
        ON CONFLICT (cnpj) DO NOTHING
    """
    linhas = [(cnpj, nome) for cnpj, nome in cnpjs_nomes.items()]
    executar_muitos(conn, sql, linhas)


def upsert_empresas_enriquecidas(conn, empresas: list[DadosEmpresa]) -> None:
    sql = """
        INSERT INTO compra_livre.dim_empresa (
            cnpj, razao_social, nome_fantasia, situacao_cadastral,
            data_inicio_atividade, cnae_principal, cnaes_secundarios,
            porte_empresa, uf_empresa, municipio_empresa,
            enriquecido, encontrado_opencnpj, consultado_em
        )
        VALUES %s
        ON CONFLICT (cnpj) DO UPDATE SET
            razao_social = COALESCE(EXCLUDED.razao_social, compra_livre.dim_empresa.razao_social),
            nome_fantasia = EXCLUDED.nome_fantasia,
            situacao_cadastral = EXCLUDED.situacao_cadastral,
            data_inicio_atividade = EXCLUDED.data_inicio_atividade,
            cnae_principal = EXCLUDED.cnae_principal,
            cnaes_secundarios = EXCLUDED.cnaes_secundarios,
            porte_empresa = EXCLUDED.porte_empresa,
            uf_empresa = EXCLUDED.uf_empresa,
            municipio_empresa = EXCLUDED.municipio_empresa,
            enriquecido = TRUE,
            encontrado_opencnpj = EXCLUDED.encontrado_opencnpj,
            consultado_em = EXCLUDED.consultado_em,
            atualizado_em = now()
    """
    import json
    from datetime import datetime, timezone

    agora = datetime.now(timezone.utc)
    linhas = [
        (
            empresa.cnpj,
            empresa.razao_social,
            empresa.nome_fantasia,
            empresa.situacao_cadastral,
            empresa.data_inicio_atividade,
            empresa.cnae_principal,
            json.dumps(empresa.cnaes_secundarios),
            empresa.porte_empresa,
            empresa.uf,
            empresa.municipio,
            True,
            empresa.encontrado,
            agora,
        )
        for empresa in empresas
    ]
    executar_muitos(conn, sql, linhas)
