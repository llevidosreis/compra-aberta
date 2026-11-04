from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from src.config import Config
from src.pipeline import extract_common
from src.pipeline.control import Execucao


def _config_teste():
    return Config(
        database_url="postgresql://fake",
        tce_base_url="https://tce.example/sim",
        opencnpj_base_url="https://opencnpj.example",
        uf="CE",
        tce_page_size=1000,
        http_timeout_segundos=5,
        opencnpj_dias_validade_cache=30,
        opencnpj_max_workers=1,
    )


@contextmanager
def _conexao_falsa(_config):
    yield MagicMock()


def _item_valido(sequencial: str = "1"):
    return {
        "codigo_municipio": "010",
        "numero_licitacao": "001/2026",
        "numero_sequencial_item_licitacao": sequencial,
        "numero_documento_negociante": None,
        "valor_vencedor_item_licitacao": "10.00",
    }


def _preparar_dependencias(monkeypatch, execucao):
    monkeypatch.setattr(extract_common, "conexao", _conexao_falsa)
    monkeypatch.setattr(extract_common, "buscar_execucao_incompleta", lambda *args: execucao)
    monkeypatch.setattr(extract_common, "iniciar_execucao", lambda *args: execucao)
    monkeypatch.setattr(extract_common, "limpar_staging_itens", MagicMock())
    monkeypatch.setattr(extract_common, "upsert_empresas_basicas", MagicMock())
    monkeypatch.setattr(extract_common, "registrar_erro", MagicMock())
    monkeypatch.setattr(extract_common, "concluir_execucao", MagicMock())
    monkeypatch.setattr(extract_common, "reabrir_execucao", MagicMock())


def test_falha_antes_do_upsert_nao_avanca_checkpoint(monkeypatch):
    execucao = Execucao(7, 0, "em_andamento")
    _preparar_dependencias(monkeypatch, execucao)
    atualizar = MagicMock()
    monkeypatch.setattr(extract_common, "atualizar_start_index", atualizar)

    def falhar_upsert(*_args):
        raise RuntimeError("falha de persistencia")

    monkeypatch.setattr(extract_common, "upsert_staging_itens", falhar_upsert)
    cliente = MagicMock()
    cliente.listar_itens_bens_servicos.return_value = [([_item_valido()], 0)]

    with pytest.raises(RuntimeError, match="falha de persistencia"):
        extract_common.carregar_itens(
            _config_teste(), cliente, "010", "2026-01-01", "2026-01-31", "full"
        )

    atualizar.assert_not_called()
    extract_common.registrar_erro.assert_called_once()


def test_falha_entre_paginas_retomara_da_proxima_posicao_confirmada(monkeypatch):
    execucao = Execucao(8, 0, "em_andamento")
    _preparar_dependencias(monkeypatch, execucao)
    atualizar = MagicMock()
    monkeypatch.setattr(extract_common, "atualizar_start_index", atualizar)
    monkeypatch.setattr(extract_common, "upsert_staging_itens", MagicMock())

    class ClienteQueFalhaDepoisDaPrimeiraPagina:
        def listar_itens_bens_servicos(self, *_args, **_kwargs):
            yield [_item_valido()], 0
            raise RuntimeError("falha entre paginas")

    with pytest.raises(RuntimeError, match="falha entre paginas"):
        extract_common.carregar_itens(
            _config_teste(), ClienteQueFalhaDepoisDaPrimeiraPagina(),
            "010", "2026-01-01", "2026-01-31", "full",
        )

    atualizar.assert_called_once()
    assert atualizar.call_args.args[2] == 1000


def test_retomada_usa_checkpoint_confirmado_sem_repetir_pagina(monkeypatch):
    execucao = Execucao(9, 1000, "em_andamento")
    _preparar_dependencias(monkeypatch, execucao)
    monkeypatch.setattr(extract_common, "atualizar_start_index", MagicMock())
    monkeypatch.setattr(extract_common, "upsert_staging_itens", MagicMock())
    cliente = MagicMock()
    cliente.listar_itens_bens_servicos.return_value = [([_item_valido("2")], 1000)]

    extract_common.carregar_itens(
        _config_teste(), cliente, "010", "2026-01-01", "2026-01-31", "full"
    )

    assert cliente.listar_itens_bens_servicos.call_args.kwargs["start_index_inicial"] == 1000
    assert extract_common.atualizar_start_index.call_args.args[2] == 2000
    extract_common.concluir_execucao.assert_called_once_with(pytest.ANY, 9)


def test_execucao_com_erro_reabre_sem_limpar_staging(monkeypatch):
    execucao = Execucao(10, 1000, "erro")
    _preparar_dependencias(monkeypatch, execucao)
    monkeypatch.setattr(extract_common, "atualizar_start_index", MagicMock())
    monkeypatch.setattr(extract_common, "upsert_staging_itens", MagicMock())
    cliente = MagicMock()
    cliente.listar_itens_bens_servicos.return_value = []

    extract_common.carregar_itens(
        _config_teste(), cliente, "010", "2026-01-01", "2026-01-31", "full"
    )

    extract_common.reabrir_execucao.assert_called_once_with(pytest.ANY, 10)
    extract_common.limpar_staging_itens.assert_not_called()
    assert cliente.listar_itens_bens_servicos.call_args.kwargs["start_index_inicial"] == 1000


def test_amostra_nova_preserva_staging_existente(monkeypatch):
    execucao = Execucao(11, 0, "em_andamento")
    _preparar_dependencias(monkeypatch, execucao)
    monkeypatch.setattr(extract_common, "atualizar_start_index", MagicMock())
    monkeypatch.setattr(extract_common, "upsert_staging_itens", MagicMock())
    cliente = MagicMock()
    cliente.listar_itens_bens_servicos.return_value = []

    extract_common.carregar_itens(
        _config_teste(),
        cliente,
        "010",
        "2025-01-01",
        "2025-12-31",
        "amostra",
        limpar_staging=False,
    )

    extract_common.limpar_staging_itens.assert_not_called()


def test_amostra_limitada_salva_checkpoint_sem_marcar_fonte_concluida(monkeypatch):
    execucao = Execucao(12, 0, "em_andamento")
    _preparar_dependencias(monkeypatch, execucao)
    atualizar = MagicMock()
    monkeypatch.setattr(extract_common, "atualizar_start_index", atualizar)
    monkeypatch.setattr(extract_common, "upsert_staging_itens", MagicMock())

    class ClienteComMultiplasPaginas:
        def listar_itens_bens_servicos(self, *_args, **_kwargs):
            yield [_item_valido("1")], 0
            yield [_item_valido("2")], 1000

    extract_common.carregar_itens(
        _config_teste(),
        ClienteComMultiplasPaginas(),
        "010",
        "2025-01-01",
        "2025-12-31",
        "amostra",
        limpar_staging=False,
        max_paginas=1,
    )

    atualizar.assert_called_once()
    assert atualizar.call_args.args[2] == 1000
    extract_common.concluir_execucao.assert_not_called()
