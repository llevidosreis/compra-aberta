from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

from src.pipeline import full_load
from src.load.load_fatos import (
    transformar_fato_item_licitacao,
    transformar_fato_licitacao,
    transformar_fato_participacao_empresa,
)
from src.load.load_staging import upsert_staging_itens


def _sql_executado(funcao):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value

    funcao(conn, "010")

    return cursor.execute.call_args.args[0], cursor.execute.call_args.args[1]


def test_fato_item_usa_chave_unica_que_aceita_cnpj_nulo():
    sql, _ = _sql_executado(transformar_fato_item_licitacao)

    assert "COALESCE(cnpj, '')" in sql


@patch("src.load.load_staging.executar_muitos")
def test_carga_staging_usa_chave_unica_que_aceita_documento_nulo(executar_muitos):
    registro = {
        "codigo_municipio": "010",
        "data_realizacao_licitacao": None,
        "numero_licitacao": "001/2025",
        "numero_sequencial_item_licitacao": 1,
        "numero_documento_negociante": None,
        "descricao_item_licitacao": "Item",
        "valor_vencedor_item_licitacao": 100,
        "codigo_tipo_negociante": None,
        "descricao_unidade_item_licitacao": None,
        "numero_quantidade_item_licitacao": None,
        "valor_unitario_item_licitacao": None,
        "data_referencia_doc": None,
    }

    upsert_staging_itens(MagicMock(), [registro])

    sql, linhas = executar_muitos.call_args.args[1:]
    assert "COALESCE(numero_documento_negociante, '')" in sql
    assert linhas[0][4] is None


def test_participacoes_nao_atribuem_itens_sem_cnpj():
    sql, _ = _sql_executado(transformar_fato_participacao_empresa)

    assert "AND cnpj IS NOT NULL" in sql


def test_total_da_licitacao_inclui_itens_sem_cnpj_e_preserva_fallback():
    sql, parametros = _sql_executado(transformar_fato_licitacao)

    assert "SUM(COALESCE(valor_vencedor, 0))" in sql
    assert "COUNT(DISTINCT cnpj)" in sql
    assert "COUNT(*) AS quantidade_itens" in sql
    assert "FULL OUTER JOIN participacoes" in sql
    assert "COALESCE(i.quantidade_itens, 0)" in sql
    assert parametros == ("010", "010")


@contextmanager
def _conexao_falsa(_config):
    yield MagicMock()


def test_full_load_nao_substitui_fatos_de_todo_o_municipio(monkeypatch):
    municipio = SimpleNamespace(codigo_municipio="010", nome="Amontada")
    cliente = MagicMock()
    cliente.listar_municipios.return_value = [municipio]
    monkeypatch.setattr(full_load, "TCEClient", MagicMock(return_value=cliente))
    monkeypatch.setattr(full_load, "OpenCNPJClient", MagicMock())
    monkeypatch.setattr(full_load, "conexao", _conexao_falsa)
    monkeypatch.setattr(full_load, "upsert_municipios", MagicMock())
    monkeypatch.setattr(full_load, "carregar_licitantes", MagicMock())
    monkeypatch.setattr(full_load, "carregar_itens", MagicMock())
    monkeypatch.setattr(full_load, "enriquecer_empresas_pendentes", MagicMock(return_value=0))
    transformar = MagicMock()
    monkeypatch.setattr(full_load, "transformar_fatos_do_municipio", transformar)
    monkeypatch.setattr(
        "src.load.load_staging.limpar_staging_municipio", MagicMock()
    )

    full_load.executar(SimpleNamespace(uf="CE"), "2024-01-01", "2025-12-31")

    transformar.assert_called_once_with(ANY, "010")


def test_full_load_publica_fatos_se_enriquecimento_falhar(monkeypatch):
    municipio = SimpleNamespace(codigo_municipio="010", nome="Amontada")
    cliente = MagicMock()
    cliente.listar_municipios.return_value = [municipio]
    monkeypatch.setattr(full_load, "TCEClient", MagicMock(return_value=cliente))
    monkeypatch.setattr(full_load, "OpenCNPJClient", MagicMock())
    monkeypatch.setattr(full_load, "conexao", _conexao_falsa)
    monkeypatch.setattr(full_load, "upsert_municipios", MagicMock())
    monkeypatch.setattr(full_load, "carregar_licitantes", MagicMock())
    monkeypatch.setattr(full_load, "carregar_itens", MagicMock())
    eventos = []

    def falhar_enriquecimento(*_args, **_kwargs):
        eventos.append("enriquecimento")
        raise RuntimeError("OpenCNPJ indisponivel")

    monkeypatch.setattr(full_load, "enriquecer_empresas_pendentes", falhar_enriquecimento)
    transformar = MagicMock()
    transformar.side_effect = lambda *_args: eventos.append("fatos")
    monkeypatch.setattr(full_load, "transformar_fatos_do_municipio", transformar)

    with pytest.raises(RuntimeError, match="fatos TCE foram processados"):
        full_load.executar(SimpleNamespace(uf="CE"), "2024-01-01", "2025-12-31")

    transformar.assert_called_once_with(ANY, "010")
    assert eventos == ["fatos", "enriquecimento"]


def test_full_load_publica_cada_municipio_antes_de_coletar_o_proximo(
    monkeypatch,
):
    municipios = [
        SimpleNamespace(codigo_municipio="010", nome="Amontada"),
        SimpleNamespace(codigo_municipio="011", nome="Aquiraz"),
    ]
    cliente = MagicMock()
    cliente.listar_municipios.return_value = municipios
    monkeypatch.setattr(full_load, "TCEClient", MagicMock(return_value=cliente))
    opencnpj = MagicMock()
    monkeypatch.setattr(full_load, "OpenCNPJClient", opencnpj)
    monkeypatch.setattr(full_load, "conexao", _conexao_falsa)
    monkeypatch.setattr(full_load, "upsert_municipios", MagicMock())
    eventos = []

    monkeypatch.setattr(
        full_load,
        "carregar_licitantes",
        lambda _config, _client, codigo, *_args, **_kwargs: eventos.append(
            f"licitantes:{codigo}"
        ),
    )
    monkeypatch.setattr(
        full_load,
        "carregar_itens",
        lambda _config, _client, codigo, *_args, **_kwargs: eventos.append(
            f"itens:{codigo}"
        ),
    )
    monkeypatch.setattr(
        full_load,
        "transformar_fatos_do_municipio",
        lambda _conn, codigo: eventos.append(f"fatos:{codigo}"),
    )

    full_load.executar(
        SimpleNamespace(uf="CE"),
        "2025-01-01",
        "2025-12-31",
        enriquecer=False,
    )

    assert eventos == [
        "licitantes:010",
        "itens:010",
        "fatos:010",
        "licitantes:011",
        "itens:011",
        "fatos:011",
    ]
    opencnpj.assert_not_called()


def test_full_load_filtra_municipios_por_inicial_sem_filtrar_dimensao(monkeypatch):
    municipios = [
        SimpleNamespace(codigo_municipio="004", nome="Acaraú"),
        SimpleNamespace(codigo_municipio="010", nome="Amontada"),
        SimpleNamespace(codigo_municipio="099", nome="Fortaleza"),
    ]
    cliente = MagicMock()
    cliente.listar_municipios.return_value = municipios
    monkeypatch.setattr(full_load, "TCEClient", MagicMock(return_value=cliente))
    monkeypatch.setattr(full_load, "conexao", _conexao_falsa)
    upsert = MagicMock()
    monkeypatch.setattr(full_load, "upsert_municipios", upsert)
    eventos = []
    monkeypatch.setattr(
        full_load,
        "carregar_licitantes",
        lambda _config, _client, codigo, *_args, **_kwargs: eventos.append(
            f"licitantes:{codigo}"
        ),
    )
    monkeypatch.setattr(
        full_load,
        "carregar_itens",
        lambda _config, _client, codigo, *_args, **_kwargs: eventos.append(
            f"itens:{codigo}"
        ),
    )
    monkeypatch.setattr(
        full_load,
        "transformar_fatos_do_municipio",
        lambda _conn, codigo: eventos.append(f"fatos:{codigo}"),
    )

    full_load.executar(
        SimpleNamespace(uf="CE"),
        "2025-01-01",
        "2025-12-31",
        enriquecer=False,
        inicial="a",
    )

    upsert.assert_called_once_with(ANY, municipios)
    assert eventos == [
        "licitantes:004",
        "itens:004",
        "fatos:004",
        "licitantes:010",
        "itens:010",
        "fatos:010",
    ]


def test_filtrar_municipios_por_inicial_ignora_acentos():
    municipios = [
        SimpleNamespace(codigo_municipio="001", nome="Água Branca"),
        SimpleNamespace(codigo_municipio="002", nome="Fortaleza"),
    ]

    assert full_load._filtrar_municipios_por_inicial(municipios, "a") == [
        municipios[0]
    ]


def test_filtrar_municipios_por_inicial_rejeita_letra_sem_resultados():
    municipios = [SimpleNamespace(codigo_municipio="004", nome="Acaraú")]

    with pytest.raises(ValueError, match="Nenhum município encontrado"):
        full_load._filtrar_municipios_por_inicial(municipios, "Z")
