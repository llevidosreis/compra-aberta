from contextlib import contextmanager
from unittest.mock import ANY, MagicMock, patch

from src.config import Config
from src.pipeline.control import Execucao
from src.pipeline.dotacoes import executar


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
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchall.return_value = [("010",)]
    yield conn


@patch("src.pipeline.dotacoes.conexao", _conexao_falsa)
@patch("src.pipeline.dotacoes.concluir_execucao")
@patch("src.pipeline.dotacoes.atualizar_start_index")
@patch("src.pipeline.dotacoes.upsert_dotacoes")
@patch("src.pipeline.dotacoes.inserir_naturezas_catalogo")
@patch("src.pipeline.dotacoes.buscar_execucao_incompleta", return_value=None)
@patch("src.pipeline.dotacoes.iniciar_execucao", return_value=Execucao(1, 0, "em_andamento"))
@patch("src.pipeline.dotacoes.TCEClient")
def test_checkpoint_dotacoes_usa_proximo_offset_fixo(
    cliente_cls,
    _iniciar,
    _buscar,
    _catalogo,
    _upsert,
    atualizar,
    _concluir,
):
    # Página curta seguida de término explícito: o próximo offset confirmado
    # continua sendo page_size, igual ao usado pelo cliente na próxima chamada.
    cliente_cls.return_value.listar_dotacoes_utilizadas.return_value = [
        ([{"codigo_municipio": "010", "numero_licitacao": "1"}], 0)
    ]

    executar(_config_teste(), "2026-01-01", "2026-01-31")

    assert atualizar.call_args.args[2] == 1000


@patch("src.pipeline.dotacoes.conexao", _conexao_falsa)
@patch("src.pipeline.dotacoes.concluir_execucao")
@patch("src.pipeline.dotacoes.reabrir_execucao")
@patch("src.pipeline.dotacoes.atualizar_start_index")
@patch("src.pipeline.dotacoes.upsert_dotacoes")
@patch("src.pipeline.dotacoes.inserir_naturezas_catalogo")
@patch("src.pipeline.dotacoes.buscar_execucao_incompleta", return_value=Execucao(3, 1000, "erro"))
@patch("src.pipeline.dotacoes.TCEClient")
def test_dotacoes_reabre_execucao_com_erro(
    cliente_cls,
    _buscar,
    _catalogo,
    _upsert,
    _atualizar,
    reabrir,
    _concluir,
):
    cliente_cls.return_value.listar_dotacoes_utilizadas.return_value = []

    executar(_config_teste(), "2026-01-01", "2026-01-31")

    reabrir.assert_called_once_with(ANY, 3)
