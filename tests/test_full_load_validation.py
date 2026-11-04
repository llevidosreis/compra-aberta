from unittest.mock import MagicMock

import pytest

from src.pipeline import full_load


@pytest.mark.parametrize(
    ("data_inicio", "data_fim", "mensagem"),
    [
        ("data-invalida", "2026-01-31", "YYYY-MM-DD"),
        ("2024-01-01", "2023-12-31", "posterior"),
        ("20240101", "2024-01-31", "YYYY-MM-DD"),
    ],
)
def test_full_load_rejeita_periodo_invalido_antes_de_criar_clientes(
    monkeypatch, data_inicio, data_fim, mensagem
):
    tce_client = MagicMock()
    opencnpj_client = MagicMock()
    monkeypatch.setattr(full_load, "TCEClient", tce_client)
    monkeypatch.setattr(full_load, "OpenCNPJClient", opencnpj_client)

    with pytest.raises(ValueError, match=mensagem):
        full_load.executar(MagicMock(), data_inicio, data_fim)

    tce_client.assert_not_called()
    opencnpj_client.assert_not_called()
