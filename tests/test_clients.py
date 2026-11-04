from unittest.mock import MagicMock, patch

import requests

from src.clients.opencnpj_client import OpenCNPJClient
from src.clients.tce_client import TCEClient, TCEClienteError
from src.config import Config


def _config_teste():
    return Config(
        database_url="postgresql://fake",
        tce_base_url="https://api-dados-abertos.tce.ce.gov.br/sim",
        opencnpj_base_url="https://api.opencnpj.org",
        uf="CE",
        tce_page_size=2,
        http_timeout_segundos=5,
        opencnpj_dias_validade_cache=30,
        opencnpj_max_workers=5,
    )


def _resposta_mock(elementos):
    resposta = MagicMock()
    resposta.status_code = 200
    resposta.json.return_value = {"name": "string", "elements": elementos}
    resposta.raise_for_status = MagicMock()
    return resposta


@patch("src.clients.tce_client.requests.get")
def test_paginacao_nao_conclui_com_pagina_curta(mock_get):
    # página 1 cheia (2 registros, igual ao page_size) -> continua
    # página 2 incompleta (1 registro) -> é a última
    mock_get.side_effect = [
        _resposta_mock([{"id": 1}, {"id": 2}]),
        _resposta_mock([{"id": 3}]),
        _resposta_mock([]),
    ]

    client = TCEClient(_config_teste())
    paginas = list(client.listar_licitantes_fornecedores("0100107", "2026-01-01", "2026-08-20"))

    assert len(paginas) == 2
    assert paginas[0] == ([{"id": 1}, {"id": 2}], 0)
    assert paginas[1] == ([{"id": 3}], 2)
    assert mock_get.call_count == 3


@patch("src.clients.tce_client.requests.get")
def test_resposta_sem_elements_falha_e_nao_vira_pagina_vazia(mock_get):
    resposta = MagicMock()
    resposta.raise_for_status = MagicMock()
    resposta.json.return_value = {"erro": "indisponivel"}
    mock_get.return_value = resposta

    try:
        list(TCEClient(_config_teste()).listar_licitantes_fornecedores(
            "010", "2026-01-01", "2026-01-02"
        ))
    except TCEClienteError as erro:
        assert "elements" in str(erro)
    else:
        raise AssertionError("resposta sem elements deve falhar")
    assert mock_get.call_count == 1


@patch("src.clients.tce_client.requests.get")
def test_pagina_repetida_interrompe_coleta_incomprovavel(mock_get):
    mock_get.side_effect = [
        _resposta_mock([{"id": 1}, {"id": 2}]),
        _resposta_mock([{"id": 1}, {"id": 2}]),
    ]

    with __import__("pytest").raises(TCEClienteError, match="Pagina repetida"):
        list(TCEClient(_config_teste()).listar_licitantes_fornecedores(
            "010", "2026-01-01", "2026-01-31"
        ))


@patch("src.clients.tce_client.requests.get")
def test_paginacao_para_quando_pagina_vazia(mock_get):
    # página cheia seguida de página vazia -> mais uma chamada extra, mas para corretamente
    mock_get.side_effect = [
        _resposta_mock([{"id": 1}, {"id": 2}]),
        _resposta_mock([]),
    ]

    client = TCEClient(_config_teste())
    paginas = list(client.listar_licitantes_fornecedores("0100107", "2026-01-01", "2026-08-20"))

    assert len(paginas) == 1
    assert mock_get.call_count == 2


@patch("src.clients.tce_client.requests.get")
def test_listar_municipios_descarta_registro_sem_codigo_ibge(mock_get):
    mock_get.side_effect = [
        _resposta_mock([
            {
                "codigo_municipio": "001",
                "nome_municipio": "T.C.M.",
                "codigo_municipio_ibge": None,
                "codigo_municipio_geonames": None,
            },
            {
                "codigo_municipio": "010",
                "nome_municipio": "AMONTADA",
                "codigo_municipio_ibge": "2300754",
                "codigo_municipio_geonames": "6320074",
            },
        ]),
        _resposta_mock([]),
    ]

    client = TCEClient(_config_teste())
    municipios = client.listar_municipios()

    assert len(municipios) == 1
    assert municipios[0].codigo_municipio == "010"
    assert municipios[0].codigo_ibge == "2300754"


@patch("src.clients.tce_client.requests.get")
def test_retomada_usa_start_index_inicial(mock_get):
    mock_get.side_effect = [_resposta_mock([{"id": 9}]), _resposta_mock([])]

    client = TCEClient(_config_teste())
    list(client.listar_licitantes_fornecedores(
        "0100107", "2026-01-01", "2026-08-20", start_index_inicial=40
    ))

    params_chamados = mock_get.call_args_list[0].kwargs["params"]
    assert params_chamados["$start_index"] == 40


@patch("src.clients.tce_client.requests.get")
def test_endpoints_tce_recebem_formato_de_data_exigido(mock_get):
    mock_get.side_effect = [
        _resposta_mock([{"id": 1}]), _resposta_mock([]),
        _resposta_mock([{"id": 1}]), _resposta_mock([]),
        _resposta_mock([{"id": 1}]), _resposta_mock([]),
    ]
    client = TCEClient(_config_teste())

    list(client.listar_itens_bens_servicos("010", "2025-01-01", "2025-12-31"))
    params_itens = mock_get.call_args.kwargs["params"]
    assert params_itens["data_inicio"] == "20250101"
    assert params_itens["data_fim"] == "20251231"

    list(client.listar_dotacoes_utilizadas("010", "2025-01-01", "2025-12-31"))
    params_dotacoes = mock_get.call_args.kwargs["params"]
    assert params_dotacoes["data_inicio"] == "20250101"
    assert params_dotacoes["data_fim"] == "20251231"

    list(client.listar_licitantes_fornecedores("010", "2025-01-01", "2025-12-31"))
    params_licitantes = mock_get.call_args.kwargs["params"]
    assert params_licitantes["data_inicio"] == "2025-01-01"
    assert params_licitantes["data_fim"] == "2025-12-31"


@patch("src.clients.opencnpj_client.time.sleep")
@patch("src.clients.opencnpj_client.requests.get")
def test_opencnpj_retrata_429_e_sucesso(mock_get, _sleep):
    primeira = MagicMock(status_code=429)
    segunda = MagicMock(status_code=200)
    segunda.json.return_value = {
        "cnpj": "31748439000120",
        "razao_social": "Empresa",
        "porte_empresa": "Microempresa (ME)",
        "opcao_mei": "N",
        "updated_at": "2026-04-14T19:56:00Z",
    }
    mock_get.side_effect = [primeira, segunda]

    resultado = OpenCNPJClient(_config_teste()).consultar("31.748.439/0001-20")

    assert resultado.encontrado is True
    assert resultado.porte_empresa == "Microempresa (ME)"
    assert resultado.opcao_mei == "N"
    assert resultado.fonte_atualizada_em.isoformat() == "2026-04-14T19:56:00+00:00"
    assert mock_get.call_count == 2


@patch("src.clients.opencnpj_client.time.sleep")
@patch("src.clients.opencnpj_client.requests.get")
def test_opencnpj_retrata_timeout_ate_falhar(mock_get, _sleep):
    mock_get.side_effect = requests.Timeout("timeout")

    try:
        OpenCNPJClient(_config_teste()).consultar("31748439000120")
    except requests.Timeout:
        pass
    else:
        raise AssertionError("timeout deveria ser propagado")

    assert mock_get.call_count == 3


@patch("src.clients.opencnpj_client.time.sleep")
@patch("src.clients.opencnpj_client.requests.get")
def test_opencnpj_nao_bloqueia_rate_limit_longo(mock_get, _sleep):
    resposta = MagicMock(status_code=429)
    resposta.headers = {"Retry-After": "1296"}
    mock_get.return_value = resposta

    try:
        OpenCNPJClient(_config_teste()).consultar("31748439000120")
    except RuntimeError as erro:
        assert "rate limit" in str(erro)
    else:
        raise AssertionError("rate limit longo deveria interromper a consulta")

    assert mock_get.call_count == 1
    _sleep.assert_not_called()


@patch("src.clients.opencnpj_client.requests.get")
def test_opencnpj_rejeita_cnpj_invalido_sem_consultar(mock_get):
    import pytest

    with pytest.raises(ValueError, match="CNPJ numérico válido"):
        OpenCNPJClient(_config_teste()).consultar("31748439000121")

    mock_get.assert_not_called()
