from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from src.load.load_fatos import substituir_fatos_municipio_periodo
from src.load.load_staging import limpar_staging_itens_periodo
from src.pipeline import reprocessar


def test_limpeza_de_staging_fica_limitada_ao_municipio_e_periodo():
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value

    limpar_staging_itens_periodo(conn, "013", "2023-01-01", "2026-08-20")

    sql, parametros = cursor.execute.call_args.args
    assert "data_realizacao_licitacao BETWEEN %s AND %s" in sql
    assert parametros == ("013", "2023-01-01", "2026-08-20")


def test_substituicao_de_fatos_reconstroi_apenas_licitacoes_afetadas():
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value

    substituir_fatos_municipio_periodo(
        conn, "013", "2023-01-01", "2026-08-20"
    )

    comandos = [call.args[0] for call in cursor.execute.call_args_list]
    assert len(comandos) == 7
    assert "CREATE TEMP TABLE licitacoes_reprocessar" in comandos[0]
    assert "fato_licitacao" in comandos[0]
    assert "fato_item_licitacao" in comandos[0]
    assert "fato_participacao_empresa" in comandos[0]
    assert "stg_item_licitacao" in comandos[0]
    assert all("USING licitacoes_reprocessar" in sql for sql in comandos[1:4])
    assert "JOIN licitacoes_reprocessar" in comandos[4]
    assert "JOIN licitacoes_reprocessar" in comandos[5]
    assert "JOIN licitacoes_reprocessar" in comandos[6]
    assert cursor.execute.call_args_list[0].args[1] == (
        "013", "2023-01-01", "2026-08-20",
        "013", "2023-01-01", "2026-08-20",
        "013", "2023-01-01", "2026-08-20",
        "013", "2023-01-01", "2026-08-20",
    )


@contextmanager
def _conexao_falsa(_config):
    yield MagicMock()


def test_reprocessamento_passa_municipio_periodo_e_preserva_staging_fora_do_periodo(
    monkeypatch,
):
    config = MagicMock()
    cliente = MagicMock()
    carregar = MagicMock()
    substituir = MagicMock()
    limpar = MagicMock()
    monkeypatch.setattr(reprocessar, "TCEClient", MagicMock(return_value=cliente))
    monkeypatch.setattr(reprocessar, "carregar_itens", carregar)
    monkeypatch.setattr(reprocessar, "substituir_fatos_municipio_periodo", substituir)
    monkeypatch.setattr(reprocessar, "limpar_staging_itens_periodo", limpar)
    monkeypatch.setattr(reprocessar, "conexao", _conexao_falsa)

    reprocessar.executar(config, " 013 ", "2023-01-01", "2026-08-20")

    carregar.assert_called_once_with(
        config, cliente, "013", "2023-01-01", "2026-08-20",
        "reprocessamento",
        intervalo_staging=("2023-01-01", "2026-08-20"),
    )
    substituir.assert_called_once_with(
        pytest.ANY, "013", "2023-01-01", "2026-08-20"
    )
    limpar.assert_called_once_with(
        pytest.ANY, "013", "2023-01-01", "2026-08-20"
    )


@pytest.mark.parametrize(
    ("codigo_municipio", "data_inicio", "data_fim", "mensagem"),
    [
        ("013", "2026-08-20", "2023-01-01", "posterior"),
        (" ", "2023-01-01", "2026-08-20", "vazio"),
        ("013", "data-invalida", "2026-08-20", "Invalid isoformat"),
    ],
)
def test_reprocessamento_rejeita_parametros_invalidos(
    monkeypatch, codigo_municipio, data_inicio, data_fim, mensagem
):
    cliente = MagicMock()
    monkeypatch.setattr(reprocessar, "TCEClient", cliente)

    with pytest.raises(ValueError, match=mensagem):
        reprocessar.executar(
            MagicMock(), codigo_municipio, data_inicio, data_fim
        )

    cliente.assert_not_called()
