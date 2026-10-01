from src.config import carregar_config_banco


def test_config_banco_nao_valida_configuracoes_do_pipeline(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://usuario:senha@localhost:5432/banco")
    monkeypatch.setenv("PNCP_PAGE_SIZE", "invalido")
    monkeypatch.setenv("PNCP_MODALIDADES", "")

    config = carregar_config_banco()

    assert config.database_url == "postgresql://usuario:senha@localhost:5432/banco"
