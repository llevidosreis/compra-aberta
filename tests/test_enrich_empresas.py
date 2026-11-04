from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from src.enrich import enrich_empresas


@contextmanager
def _conexao_falsa(_config):
    yield MagicMock()


def test_falha_individual_no_opencnpj_nao_e_reportada_como_sucesso(monkeypatch):
    monkeypatch.setattr(enrich_empresas, "conexao", _conexao_falsa)
    monkeypatch.setattr(
        enrich_empresas,
        "cnpjs_pendentes_de_consulta",
        lambda *_args, **_kwargs: {"31748439000120"},
    )
    cliente = MagicMock()
    cliente.consultar.side_effect = RuntimeError("timeout")

    with pytest.raises(RuntimeError, match="Falha ao consultar 1 CNPJ"):
        enrich_empresas.enriquecer_empresas_pendentes(
            MagicMock(opencnpj_max_workers=1), cliente
        )

    cliente.consultar.assert_called_once_with("31748439000120")


def test_comando_enriquecer_nao_executa_a_coleta_tce(monkeypatch):
    import sys

    from src import main

    config = object()
    cliente = object()
    chamadas = []
    monkeypatch.setattr(sys, "argv", ["src.main", "enriquecer"])
    monkeypatch.setattr(main, "carregar_config", lambda: config)
    monkeypatch.setattr(main, "OpenCNPJClient", lambda recebido: cliente)
    monkeypatch.setattr(
        main,
        "enriquecer_empresas_pendentes",
        lambda config_recebido, cliente_recebido, **escopo: chamadas.append(
            (config_recebido, cliente_recebido, escopo)
        ),
    )

    main.main()

    assert chamadas == [(config, cliente, {
        "data_inicio": None,
        "data_fim": None,
        "codigo_municipio": None,
    })]


def test_comando_enriquecer_aceita_periodo_sem_recoletar_tce(monkeypatch):
    import sys

    from src import main

    config = object()
    cliente = object()
    chamadas = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "src.main",
            "enriquecer",
            "--data-inicio",
            "2025-01-01",
            "--data-fim",
            "2025-12-31",
            "--codigo-municipio",
            "010",
        ],
    )
    monkeypatch.setattr(main, "carregar_config", lambda: config)
    monkeypatch.setattr(main, "OpenCNPJClient", lambda recebido: cliente)
    monkeypatch.setattr(
        main,
        "enriquecer_empresas_pendentes",
        lambda config_recebido, cliente_recebido, **escopo: chamadas.append(escopo),
    )

    main.main()

    assert chamadas == [
        {
            "data_inicio": "2025-01-01",
            "data_fim": "2025-12-31",
            "codigo_municipio": "010",
        }
    ]


def test_enriquecimento_rejeita_municipio_sem_periodo():
    with pytest.raises(ValueError, match="requer data_inicio"):
        enrich_empresas.enriquecer_empresas_pendentes(
            MagicMock(), MagicMock(), codigo_municipio="010"
        )