from datetime import datetime, timedelta, timezone

import pytest

from src.eligibility import (
    classificar_porte,
    empresa_me_confirmada,
    sql_empresa_me_elegivel,
)

AGORA = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
CNPJ_VALIDO = "31748439000120"


@pytest.mark.parametrize(
    ("porte", "opcao_mei", "esperado"),
    [
        ("Microempresa (ME)", "N", "ME"),
        ("ME", "N", "ME"),
        ("Microempresa (ME)", "S", "MEI"),
        ("Empresa de Pequeno Porte (EPP)", "N", "EPP"),
        ("Demais", "N", "OUTRO"),
        ("Não informado", "", "DESCONHECIDO"),
        (None, None, "DESCONHECIDO"),
        ("Microempresa (ME)", None, "DESCONHECIDO"),
    ],
)
def test_classificar_porte_distingue_me_mei_epp_e_desconhecido(
    porte, opcao_mei, esperado
):
    assert classificar_porte(porte, opcao_mei) == esperado


def _elegivel(**sobrescreve):
    dados = {
        "cnpj": CNPJ_VALIDO,
        "porte": "Microempresa (ME)",
        "opcao_mei": "N",
        "encontrado": True,
        "enriquecido": True,
        "consultado_em": AGORA - timedelta(days=1),
        "fonte_atualizada_em": AGORA - timedelta(days=30),
        "dias_validade_cache": 30,
        "agora": AGORA,
    }
    dados.update(sobrescreve)
    return empresa_me_confirmada(**dados)


def test_me_com_consulta_confiavel_e_atual_elegivel():
    assert _elegivel() is True


@pytest.mark.parametrize(
    "alteracoes",
    [
        {"porte": "Empresa de Pequeno Porte (EPP)"},
        {"porte": "Microempresa (ME)", "opcao_mei": "S"},
        {"porte": "Demais"},
        {"porte": None},
        {"opcao_mei": ""},
        {"cnpj": ""},
        {"cnpj": "123"},
        {"cnpj": "31748439000121"},
        {"enriquecido": False},
        {"encontrado": False},
        {"encontrado": None},
        {"consultado_em": None},
        {"fonte_atualizada_em": None},
        {"consultado_em": AGORA - timedelta(days=31)},
        {"consultado_em": AGORA + timedelta(minutes=1)},
        {"fonte_atualizada_em": AGORA + timedelta(minutes=1)},
    ],
)
def test_empresa_sem_evidencia_suficiente_nao_e_elegivel(alteracoes):
    assert _elegivel(**alteracoes) is False


def test_lote_misto_inclui_somente_me_comprovada():
    empresas = [
        {"porte": "Microempresa (ME)", "opcao_mei": "N"},
        {"porte": "Microempresa (ME)", "opcao_mei": "S"},
        {"porte": "Empresa de Pequeno Porte (EPP)", "opcao_mei": "N"},
        {"porte": "Demais", "opcao_mei": "N"},
        {"porte": None, "opcao_mei": None},
    ]

    elegiveis = [
        empresa
        for empresa in empresas
        if _elegivel(**empresa)
    ]

    assert elegiveis == [{"porte": "Microempresa (ME)", "opcao_mei": "N"}]


def test_predicado_sql_exige_me_nao_mei_fonte_encontrada_e_cache_atual():
    sql = sql_empresa_me_elegivel("e", dias_validade_cache=30)

    assert "e.enriquecido IS TRUE" in sql
    assert "e.encontrado_opencnpj IS TRUE" in sql
    assert "e.consultado_em >= now() - INTERVAL '30 days'" in sql
    assert "e.fonte_atualizada_em IS NOT NULL" in sql
    assert "UPPER(TRIM(COALESCE(e.opcao_mei, ''))) = 'N'" in sql
    assert "'microempresa (me)'" in sql
    assert "'epp'" not in sql
