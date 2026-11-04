import pytest

from src.config import carregar_config, carregar_config_banco


def test_config_banco_nao_valida_configuracoes_do_pipeline(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://usuario:senha@localhost:5432/banco")
    monkeypatch.setenv("OPENCNPJ_DIAS_VALIDADE_CACHE", "invalido")

    with pytest.raises(ValueError):
        carregar_config_banco()


def test_carregar_config_aceita_valores_validos(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://usuario:senha@localhost:5432/banco")
    monkeypatch.setenv("TCE_PAGE_SIZE", "1000")
    monkeypatch.setenv("HTTP_TIMEOUT_SEGUNDOS", "30")

    config = carregar_config()

    assert config.database_url == "postgresql://usuario:senha@localhost:5432/banco"
    assert config.tce_page_size == 1000
    assert config.http_timeout_segundos == 30


def test_carregar_config_sem_banco_permita_relatorio_offline(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    config = carregar_config(exigir_banco=False)

    assert config.database_url == ""
