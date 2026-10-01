from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.clients.pncp_client import PNCPClient, PNCPClienteError
from src.config import Config
from src.matching.scorer import avaliar_match
from src.load.load_pncp import contratacoes_alteradas, upsert_contratacoes_pncp
from src.quality.validators import validar_contratacoes_pncp
from src.transform.normalize import (
    normalizar_contratacao_pncp,
    normalizar_item_pncp,
    para_datetime,
)


def _config_teste(**alteracoes):
    valores = {
        "database_url": "postgresql://fake",
        "tce_base_url": "https://api-dados-abertos.tce.ce.gov.br/sim",
        "opencnpj_base_url": "https://api.opencnpj.org",
        "ibge_base_url": "https://servicodados.ibge.gov.br/api/v1/localidades",
        "uf": "CE",
        "tce_page_size": 2,
        "http_timeout_segundos": 5,
        "opencnpj_dias_validade_cache": 30,
        "opencnpj_max_workers": 5,
        "pncp_page_size": 2,
        "pncp_modalidades": (8, 9),
        "pncp_intervalo_requisicoes_segundos": 0,
    }
    valores.update(alteracoes)
    return Config(**valores)


def _pagina(data, total_paginas):
    resposta = MagicMock()
    resposta.status_code = 200
    resposta.content = b"json"
    resposta.json.return_value = {"data": data, "totalPaginas": total_paginas}
    resposta.raise_for_status = MagicMock()
    return resposta


@patch("src.clients.pncp_client.requests.get")
def test_pncp_pagina_por_total_e_parametros_oficiais(mock_get):
    mock_get.side_effect = [
        _pagina([{"numeroControlePNCP": "a"}], 2),
        _pagina([{"numeroControlePNCP": "b"}], 2),
    ]
    cliente = PNCPClient(_config_teste())

    paginas = list(cliente.listar_contratacoes_publicadas(
        "2026-01-01", "2026-01-31", 8, "CE"
    ))

    assert paginas == [([{"numeroControlePNCP": "a"}], 1), ([{"numeroControlePNCP": "b"}], 2)]
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0].kwargs["params"] == {
        "dataInicial": "20260101", "dataFinal": "20260131",
        "codigoModalidadeContratacao": 8, "uf": "CE", "pagina": 1,
        "tamanhoPagina": 2,
    }
    assert mock_get.call_args_list[1].kwargs["params"]["pagina"] == 2


@patch("src.clients.pncp_client.requests.get")
def test_pncp_204_e_corpo_vazio_sao_sem_resultado(mock_get):
    sem_conteudo = MagicMock(status_code=204)
    vazio = MagicMock(status_code=200, content=b"")
    mock_get.side_effect = [sem_conteudo, vazio]
    cliente = PNCPClient(_config_teste())

    assert list(cliente.listar_contratacoes_com_proposta_aberta("2026-01-31", 8, "CE")) == []
    assert list(cliente.listar_contratacoes_publicadas("2026-01-01", "2026-01-31", 8, "CE")) == []


@patch("src.clients.pncp_client.time.sleep")
@patch("src.clients.pncp_client.requests.get")
@pytest.mark.parametrize("status", [429, 503])
def test_pncp_retrata_status_transitorio(mock_get, _sleep, status):
    resposta_erro = MagicMock(status_code=status)
    resposta_erro.headers = {"Retry-After": "1"}
    resposta_erro.raise_for_status.side_effect = requests.HTTPError(
        response=resposta_erro
    )
    mock_get.side_effect = [resposta_erro, _pagina([], 0)]

    assert list(PNCPClient(_config_teste()).listar_contratacoes_publicadas(
        "2026-01-01", "2026-01-31", 8, "CE"
    )) == []
    assert mock_get.call_count == 2
    _sleep.assert_called_once()


@patch("src.clients.pncp_client.time.sleep")
@patch("src.clients.pncp_client.requests.get")
def test_pncp_429_sem_retry_after_usa_espera_configurada(mock_get, sleep):
    resposta_erro = MagicMock(status_code=429)
    resposta_erro.headers = {}
    resposta_erro.raise_for_status.side_effect = requests.HTTPError(
        response=resposta_erro
    )
    mock_get.side_effect = [resposta_erro, _pagina([], 0)]

    assert list(PNCPClient(_config_teste()).listar_contratacoes_publicadas(
        "2026-01-01", "2026-01-31", 8, "CE"
    )) == []
    sleep.assert_called_once_with(30)


@patch("src.clients.pncp_client.requests.get")
def test_pncp_erro_4xx_e_permanente(mock_get):
    resposta = MagicMock(status_code=400)
    resposta.raise_for_status.side_effect = requests.HTTPError(response=resposta)
    mock_get.return_value = resposta

    with pytest.raises(PNCPClienteError, match="permanente"):
        list(PNCPClient(_config_teste()).listar_contratacoes_publicadas(
            "2026-01-01", "2026-01-31", 8, "CE"
        ))
    assert mock_get.call_count == 1


@patch("src.clients.pncp_client.requests.get")
def test_listar_itens_usa_endpoint_de_detalhe_documentado(mock_get):
    resposta = MagicMock(status_code=200, content=b"json")
    resposta.json.return_value = {"itens": [{"numeroItem": 1}]}
    resposta.raise_for_status = MagicMock()
    mock_get.return_value = resposta

    paginas = list(PNCPClient(_config_teste()).listar_itens("12345678000199", 2026, 4))

    assert paginas == [([{"numeroItem": 1}], 1)]
    assert mock_get.call_args.args[0].endswith("/orgaos/12345678000199/compras/2026/4")


def test_normalizar_contratacao_preserva_datas_link_e_objeto():
    resultado = normalizar_contratacao_pncp({
        "numeroControlePNCP": "12345678000199-1-000001/2026",
        "numeroCompra": "Aviso 4/2026",
        "anoCompra": 2026,
        "sequencialCompra": 4,
        "modalidadeId": 8,
        "modalidadeNome": "Dispensa",
        "dataAtualizacao": "2026-09-30T10:11:12",
        "dataPublicacaoPncp": "2026-09-29T08:00:00",
        "dataAberturaProposta": "2026-09-30T10:00:00",
        "dataEncerramentoProposta": "2026-10-01T12:00:00",
        "orgaoEntidade": {"cnpj": "12.345.678/0001-99", "razaoSocial": "Órgão"},
        "unidadeOrgao": {"ufSigla": "CE", "codigoIbge": "2304400", "municipioNome": "Fortaleza"},
        "objetoCompra": "Aquisição de papel higiênico para escolas",
    })

    assert resultado["data_atualizacao"] == datetime(2026, 9, 30, 10, 11, 12, tzinfo=timezone.utc)
    assert resultado["link_edital_pncp"] == "https://pncp.gov.br/app/editais/12345678000199/2026/4"
    assert resultado["objeto_normalizado"] == "aquisicao papel higienico escolas"
    assert resultado["orgao_cnpj"] == "12345678000199"
    assert resultado["exclusivo_me_epp"] is None


def test_normalizar_datas_nulas_e_item_pncp():
    assert para_datetime(None) is None
    contratacao = normalizar_contratacao_pncp({"objetoCompra": None})
    assert contratacao["data_atualizacao"] is None
    assert contratacao["link_edital_pncp"] is None
    item = normalizar_item_pncp({
        "numeroItem": 2, "descricao": "Papel toalete", "quantidade": "10",
        "unidadeMedida": "pacote", "valorUnitarioEstimado": "12.50",
        "codigoItem": 123,
    })
    assert item["numero_item"] == 2
    assert item["quantidade"] == 10.0
    assert item["valor_estimado"] == 12.5
    assert item["codigo_catalogo"] == 123


def test_validar_contratacao_descarta_com_motivo():
    validacao = validar_contratacoes_pncp([
        {"numero_controle_pncp": None, "orgao_cnpj": "123", "objeto_compra": ""}
    ])
    assert validacao.validos == []
    assert validacao.invalidos[0][1] == "numero_controle_pncp ausente"


def test_upsert_reprocessa_novas_ou_com_timestamp_alterado():
    data_gravada = datetime(2026, 9, 1, tzinfo=timezone.utc)
    cursor = MagicMock()
    cursor.fetchall.return_value = [("igual", data_gravada), ("alterado", data_gravada)]
    conexao_fake = MagicMock()
    conexao_fake.cursor.return_value.__enter__.return_value = cursor
    registros = [
        {"numero_controle_pncp": "igual", "data_atualizacao": data_gravada},
        {"numero_controle_pncp": "alterado", "data_atualizacao": datetime(2026, 9, 2, tzinfo=timezone.utc)},
        {"numero_controle_pncp": "novo", "data_atualizacao": data_gravada},
    ]

    assert contratacoes_alteradas(conexao_fake, registros) == {"alterado", "novo"}


def test_upsert_contratacao_preserva_criado_em_e_so_atualiza_versao(monkeypatch):
    import src.load.load_pncp as load_pncp

    chamadas = []

    def simular_execute_values(cursor, sql, linhas, fetch=False):
        chamadas.append((sql, linhas, fetch))
        return [("pncp-1",)]

    monkeypatch.setattr(load_pncp, "execute_values", simular_execute_values)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = MagicMock()
    registro = {
        "numero_controle_pncp": "pncp-1", "orgao_cnpj": "12345678000199",
        "objeto_compra": "Compra de papel", "data_atualizacao": datetime(2026, 9, 2, tzinfo=timezone.utc),
    }

    assert upsert_contratacoes_pncp(conn, [registro]) == {"pncp-1"}
    assert "ON CONFLICT (numero_controle_pncp) DO UPDATE" in chamadas[0][0]
    assert "IS DISTINCT FROM EXCLUDED.data_atualizacao_pncp" in chamadas[0][0]
    bloco_update = chamadas[0][0].split("DO UPDATE SET", 1)[1]
    assert "criado_em =" not in bloco_update


def _contratacao(**alteracoes):
    base = {
        "numero_controle_pncp": "pncp-1",
        "objeto_compra": "Aquisição de papel higiênico",
        "objeto_normalizado": "aquisicao papel higienico",
        "uf": "CE",
        "codigo_ibge_municipio": "2304400",
        "nome_municipio": "Fortaleza",
        "data_abertura_proposta": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "data_encerramento_proposta": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "exclusivo_me_epp": False,
    }
    base.update(alteracoes)
    return base


def test_matching_higiene_casa_e_ti_nao_casa():
    perfil = {
        "cnpj": "12345678000199",
        "produtos_palavras_chave": ["higiene e limpeza"],
        "ufs_atuacao": ["CE"],
        "municipios_atuacao": [],
    }
    empresa = {"porte_empresa": "EPP", "cnae_principal": "4646-0/02"}

    assert avaliar_match(_contratacao(), [], perfil, empresa, agora=datetime(2026, 9, 30, tzinfo=timezone.utc))
    perfil_ti = {**perfil, "produtos_palavras_chave": ["software", "informatica"]}
    assert avaliar_match(_contratacao(), [], perfil_ti, {"porte_empresa": "EPP", "cnae_principal": "6201-5/01"}, agora=datetime(2026, 9, 30, tzinfo=timezone.utc)) is None


def test_matching_respeita_uf_municipio_exclusividade_e_prazo():
    perfil = {
        "cnpj": "12345678000199", "produtos_palavras_chave": ["papel higienico"],
        "ufs_atuacao": ["CE"], "municipios_atuacao": ["2304400"],
    }
    empresa = {"porte_empresa": "DEMAIS", "cnae_principal": None}
    agora = datetime(2026, 9, 30, tzinfo=timezone.utc)

    assert avaliar_match(_contratacao(uf="PI"), [], perfil, empresa, agora=agora) is None
    assert avaliar_match(_contratacao(nome_municipio="Sobral", codigo_ibge_municipio="2312908"), [], perfil, empresa, agora=agora) is None
    assert avaliar_match(_contratacao(exclusivo_me_epp=True), [], perfil, empresa, agora=agora) is None
    empresa_me = {**empresa, "porte_empresa": "ME"}
    assert avaliar_match(_contratacao(exclusivo_me_epp=True), [], perfil, empresa_me, agora=agora)
    assert avaliar_match(
        _contratacao(data_encerramento_proposta=datetime(2026, 9, 30, tzinfo=timezone.utc)),
        [], perfil, empresa_me, agora=agora,
    ) is None
