from datetime import date
from types import SimpleNamespace

from src import api


def test_rotas_de_empresa_filtram_porte_nao_verificado(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    monkeypatch.setenv("OPENCNPJ_DIAS_VALIDADE_CACHE", "30")
    consultas = []

    def linhas(sql, parametros):
        consultas.append(sql)
        return []

    monkeypatch.setattr(api, "_linhas", linhas)

    api.empresas(porte_empresa=None, cnae_principal=None, limit=50, offset=0)
    api.participacoes(
        codigo_municipio=None,
        porte_empresa=None,
        cnpj=None,
        limit=50,
        offset=0,
    )
    api.itens_licitacao("010", "001/2026", limit=100, offset=0)
    api.licitacoes_detalhadas(
        codigo_municipio=None,
        codigo_natureza=None,
        ano=None,
        limit=100,
        offset=0,
    )
    assert len(consultas) == 4
    for sql in consultas:
        assert "enriquecido IS TRUE" in sql
        assert "encontrado_opencnpj IS TRUE" in sql
        assert "opcao_mei" in sql
        assert "fonte_atualizada_em IS NOT NULL" in sql
        assert "porte_empresa" in sql

    assert "i.cnpj IS NULL OR" not in consultas[2]


def test_comparativo_classificacao_expone_agregados_e_mantem_desconhecidos_no_universo(
    monkeypatch,
):
    monkeypatch.setattr(
        api,
        "carregar_config_banco",
        lambda: SimpleNamespace(opencnpj_dias_validade_cache=30),
    )
    consultas = []
    categorias = ("ME", "MEI", "EPP", "OUTRO", "DESCONHECIDA")

    def linhas(sql, parametros):
        consultas.append((sql, parametros))
        return [
            {
                "total_itens": 100,
                "total_licitacoes": 25,
                "valor_total_universo": "1000.00",
                "classificacao": categoria,
                "quantidade_itens": itens,
                "quantidade_licitacoes": licitacoes,
                "valor_total": valor,
            }
            for categoria, itens, licitacoes, valor in (
                ("ME", 10, 4, "100.00"),
                ("MEI", 5, 3, "50.00"),
                ("EPP", 20, 7, "200.00"),
                ("OUTRO", 15, 6, "150.00"),
                ("DESCONHECIDA", 50, 15, "500.00"),
            )
        ]

    monkeypatch.setattr(api, "_linhas", linhas)

    resultado = api.comparativo_classificacao(
        codigo_municipio="010",
        data_inicio=date(2025, 1, 1),
        data_fim=date(2025, 12, 31),
    )

    sql, parametros = consultas[0]
    assert "LEFT JOIN compra_livre.dim_empresa" in sql
    assert "i.codigo_municipio = %s" in sql
    assert "i.data_realizacao_licitacao >= %s" in sql
    assert "i.data_realizacao_licitacao <= %s" in sql
    assert "DESCONHECIDA" in sql
    assert "INTERVAL '30 days'" in sql
    assert parametros == (date(2025, 1, 1), date(2025, 12, 31), "010")
    assert resultado["universo"] == {
        "quantidade_itens": 100,
        "quantidade_licitacoes": 25,
        "valor_total": "1000.00",
    }
    assert [row["classificacao"] for row in resultado["por_classificacao"]] == list(
        categorias
    )
    assert resultado["por_classificacao"][-1]["quantidade_itens"] == 50
