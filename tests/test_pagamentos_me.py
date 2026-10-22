"""
Testes offline da apuração de pagamentos + recorte ME (sem rede, sem banco).

O cenário reproduz o exemplo de Consultoria (elemento 35) de Acaraú/2025
do guia: ISYSERVICE R$ 761.880,06 + SIST R$ 120.000,00 (com nota de
02/02/2026, referência 202602) + WESLEY R$ 201.400,65 = R$ 1.083.280,71;
Câmara (R$ 24.000,00) excluída; ME = R$ 881.880,06.
"""
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from src.clients.tce_despesas_client import (
    TCEDespesasClient,
    TCEDespesasErro,
    referencias_mensais,
)
from src.config import Config
from src.enrich.porte_fornecedores import (
    SIT_CLASSIFICADO,
    SIT_CNPJ_DIVERGENTE,
    SIT_NAO_ENCONTRADO,
    classificacao_de_resposta,
    classificar_cnpjs,
)
from src.pipeline import despesas_me
from src.transform.apuracao_pagamentos import (
    CAT_CPF,
    CAT_DEMAIS,
    CAT_ME,
    CAT_ME_EXCLUIDA_MEI,
    CAT_SEM_CLASSIFICACAO,
    REGRA_MEI_EXCLUIR,
    aplicar_filtros_financeiros,
    apurar_por_porte,
    identificar_orgaos_camara,
)
from src.transform.cruzamento import cruzar_pagamentos_empenhos
from src.transform.despesas import (
    cnpj_valido,
    cpf_valido,
    data_iso,
    documento_comparacao,
    elemento_despesa,
    normalizar_empenho,
    normalizar_orgao,
    normalizar_pagamento,
    normalizar_porte,
    tipo_documento,
)

CONFIG = Config(
    database_url="", tce_base_url="https://tce.teste/sim",
    opencnpj_base_url="https://opencnpj.teste", ibge_base_url="",
    uf="CE", tce_page_size=1000, http_timeout_segundos=5,
    opencnpj_dias_validade_cache=30, opencnpj_max_workers=2,
)

ISY, SIST, WESLEY, FRANKLIN = (
    "02530104000133", "17016110000144", "26940943000198", "14866182000192",
)


# ----------------------------------------------------------------------
# Massa de dados no formato da API do TCE
# ----------------------------------------------------------------------

def _orgaos():
    base = {"codigo_municipio": "004", "exercicio_orcamento": 202500}
    return [
        {**base, "codigo_orgao": "01", "nome_orgao": "Câmara Municipal de Acaraú", "codigo_tipo_unidade": "02"},
        {**base, "codigo_orgao": "02", "nome_orgao": "Gabinete da Prefeita", "codigo_tipo_unidade": "01"},
        {**base, "codigo_orgao": "04", "nome_orgao": "Secretaria de Administração e Finanças", "codigo_tipo_unidade": "03"},
        {**base, "codigo_orgao": "AA", "nome_orgao": "Prefeitura Municipal", "codigo_tipo_unidade": "00"},
    ]


def _empenho(orgao, data, numero, elemento, documento, nome, ref):
    return {
        "codigo_municipio": "004", "exercicio_orcamento": 202500,
        "codigo_orgao": orgao, "codigo_unidade_orcamentaria": "01  ",
        "data_emissao_empenho": f"{data}T00:00:00", "numero_empenho": numero,
        "codigo_elemento_despesa": elemento, "numero_documento_negociante": documento,
        "codigo_tipo_negociante": "1", "nome_negociante": nome,
        "data_referencia_doc": ref, "valor_empenhado": Decimal("999999.0000"),
    }


def _pagamento(orgao, data, numero, nota, data_nota, valor, ref, estornado=0):
    return {
        "codigo_municipio": "004", "exercicio_orcamento": 202500,
        "codigo_orgao": orgao, "codigo_unidade_orcamentaria": "01  ",
        "data_emissao_empenho": f"{data}T00:00:00", "numero_empenho": numero,
        "numero_sub_empenho": "001", "numero_nota_pagamento": nota,
        "data_nota_pagamento": f"{data_nota}T00:00:00",
        "data_referencia_doc": ref, "valor_nota_pagamento": Decimal(valor),
        "valor_empenhado_a_pagar": Decimal("0.0000"), "estado_de_estornado": estornado,
    }


def _empenhos():
    return [
        _empenho("04", "2025-01-10", "10010001", "33903500", "02.530.104/0001-33", "ISYSERVICE CONTABILIDADE LTDA", 202501),
        _empenho("02", "2025-05-15", "15050002", "33903500", SIST, "SIST SISTEMAS DE INFORMATICA LTDA -ME", 202505),
        _empenho("04", "2025-02-03", "03020001", "33903500", WESLEY, "WESLEY SILVEIRA ADVOCACIA", 202502),
        # Câmara: precisa ficar fora da aba Prefeitura
        _empenho("01", "2025-01-02", "02010001", "33903500", FRANKLIN, "E FRANKLIN DE VITERBO CONTABILIDADE", 202501),
        _empenho("01", "2025-01-02", "02010002", "33903500", WESLEY, "WESLEY SILVEIRA ADVOCACIA", 202501),
        # mesmo número de empenho em OUTRO órgão: a chave completa diferencia
        _empenho("02", "2025-01-10", "10010001", "33903000", "07663941000154", "COMERCIAL X", 202501),
        # natureza fora do recorte (31 = pessoal)
        _empenho("04", "2025-01-23", "23010004", "31909600", "07663941000154", "PREFEITURA DE ITAREMA", 202501),
        # favorecido pessoa física (CPF válido) em natureza do recorte
        _empenho("04", "2025-03-01", "01030001", "33903000", "529.982.247-25", "FULANO", 202503),
    ]


def _pagamentos():
    return [
        _pagamento("04", "2025-01-10", "10010001", "24010001", "2025-01-24", "300000.0000", 202501),
        _pagamento("04", "2025-01-10", "10010001", "24020001", "2025-02-24", "300000.0000", 202502),
        _pagamento("04", "2025-01-10", "10010001", "24030001", "2025-03-24", "161880.0600", 202503),
        # estornado: não entra
        _pagamento("04", "2025-01-10", "10010001", "24030002", "2025-03-24", "999.9900", 202503, estornado=1),
        _pagamento("02", "2025-05-15", "15050002", "20050001", "2025-05-20", "110000.0000", 202505),
        # pagamento de 2026 vinculado ao orçamento 2025 (guia: SIST, nota 02020032)
        _pagamento("02", "2025-05-15", "15050002", "02020032", "2026-02-02", "10000.0000", 202602),
        _pagamento("04", "2025-02-03", "03020001", "10020001", "2025-02-10", "200000.0000", 202502),
        _pagamento("04", "2025-02-03", "03020001", "10020002", "2025-02-10", "1400.6500", 202502),
        # Câmara: R$ 18.000,00 + R$ 6.000,00
        _pagamento("01", "2025-01-02", "02010001", "30010001", "2025-01-30", "18000.0000", 202501),
        _pagamento("01", "2025-01-02", "02010002", "30010002", "2025-01-30", "6000.0000", 202501),
        # mesmo número de empenho, órgão 02, elemento 30
        _pagamento("02", "2025-01-10", "10010001", "24010099", "2025-01-24", "500.0000", 202501),
        # fora das naturezas
        _pagamento("04", "2025-01-23", "23010004", "30010003", "2025-01-30", "15226.6100", 202501),
        # CPF
        _pagamento("04", "2025-03-01", "01030001", "05030001", "2025-03-05", "700.0000", 202503),
    ]


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------

class FakeTCE:
    def __init__(self, empenhos=None, pagamentos=None):
        self._empenhos = empenhos if empenhos is not None else _empenhos()
        self._pagamentos = pagamentos if pagamentos is not None else _pagamentos()
        self.chamadas = []

    def listar_orgaos(self, municipio, exercicio):
        return _orgaos()

    def listar_empenhos(self, municipio, exercicio, ini, fim):
        self.chamadas.append(("empenhos", ini, fim))
        return [dict(e) for e in self._empenhos]

    def listar_pagamentos(self, municipio, exercicio, ini, fim):
        self.chamadas.append(("pagamentos", ini, fim))
        return [dict(p) for p in self._pagamentos if str(p["data_referencia_doc"]) <= fim]


RESPOSTAS_OPENCNPJ = {
    ISY: {"cnpj": ISY, "razao_social": "ISYSERVICE CONTABILIDADE LTDA",
          "porte_empresa": "Microempresa (ME)", "opcao_simples": "S", "opcao_mei": "N",
          "updated_at": "2026-10-14T09:48:02Z"},
    SIST: {"cnpj": SIST, "razao_social": "SIST SISTEMAS DE INFORMATICA LTDA",
           "porte_empresa": "Microempresa (ME)", "opcao_simples": "S", "opcao_mei": "N"},
    WESLEY: {"cnpj": WESLEY, "razao_social": "WESLEY SILVEIRA SOCIEDADE INDIVIDUAL DE ADVOCACIA",
             "porte_empresa": "Demais", "opcao_simples": "S", "opcao_mei": "N"},
}


class FakeOpenCNPJ:
    referencia_base = "NAO_INFORMADA"

    def __init__(self, respostas=None):
        self.respostas = respostas if respostas is not None else RESPOSTAS_OPENCNPJ
        self.consultas = []

    def consultar(self, cnpj):
        from src.enrich.porte_fornecedores import _sem_cadastro
        self.consultas.append(cnpj)
        if cnpj not in self.respostas:
            return _sem_cadastro(cnpj, SIT_NAO_ENCONTRADO, "404", self.referencia_base)
        return classificacao_de_resposta(cnpj, self.respostas[cnpj], self.referencia_base)


def _pipeline_em_memoria(regra_mei="incluir", respostas=None):
    empenhos = [normalizar_empenho(e) for e in _empenhos()]
    pagamentos = [normalizar_pagamento(p) for p in _pagamentos()]
    orgaos = [normalizar_orgao(o) for o in _orgaos()]
    cruz = cruzar_pagamentos_empenhos(empenhos, pagamentos)
    camara = identificar_orgaos_camara(orgaos)
    filtros = aplicar_filtros_financeiros(
        cruz.pagamentos, "004", "202500",
        {o["codigo_orgao"] for o in orgaos}, set(camara),
    )
    client = FakeOpenCNPJ(respostas)
    cnpjs = sorted({p["documento"] for p in filtros.validos if p["tipo_documento"] == "CNPJ"})
    classificacoes, _ = classificar_cnpjs(cnpjs, client, {}, 30, max_workers=1)
    return cruz, filtros, apurar_por_porte(filtros.validos, classificacoes, regra_mei)


# ----------------------------------------------------------------------
# Passo 3/4: referências e paginação
# ----------------------------------------------------------------------

def test_referencias_viram_o_ano_sem_mes_13():
    refs = referencias_mensais("202511", "202602")
    assert refs == ["202511", "202512", "202601", "202602"]
    assert len(referencias_mensais("202501", "202609")) == 21


@pytest.mark.parametrize("ref", ["202513", "2025", "abcdef"])
def test_referencia_invalida(ref):
    with pytest.raises(ValueError):
        referencias_mensais(ref, "202612")


class _Resposta:
    def __init__(self, status, corpo):
        self.status_code = status
        self._texto = json.dumps(corpo)

    def json(self, parse_float=float):
        return json.loads(self._texto, parse_float=parse_float)


class _SessaoFake:
    """Simula a API: 5 registros por mês; page_size do teste = 2."""

    def __init__(self, falhar_em=None):
        self.chamadas = []
        self.falhar_em = falhar_em

    def get(self, url, params, timeout):
        self.chamadas.append(dict(params))
        ref = params["data_referencia_doc"]
        if ref == self.falhar_em:
            return _Resposta(500, {"erro": "x"})
        todos = [{"data_referencia_doc": int(ref), "n": i, "valor": 1.10} for i in range(5)]
        ini, qtd = params["$start_index"], params["$count"]
        return _Resposta(200, {"elements": todos[ini: ini + qtd]})


def test_paginacao_por_mes_e_reinicio_do_start_index():
    sessao = _SessaoFake()
    client = TCEDespesasClient(replace(CONFIG, tce_page_size=2), session=sessao, pausa_base_segundos=0)
    registros = client.listar_pagamentos("004", "202500", "202512", "202601")

    assert len(registros) == 10
    indices = [(c["data_referencia_doc"], c["$start_index"]) for c in sessao.chamadas]
    assert indices == [("202512", 0), ("202512", 2), ("202512", 4),
                       ("202601", 0), ("202601", 2), ("202601", 4)]
    # exercício não muda quando a referência entra em 2026
    assert {c["exercicio_orcamento"] for c in sessao.chamadas} == {"202500"}
    # valores chegam como Decimal, sem passar por float
    assert all(isinstance(r["valor"], Decimal) for r in registros)


def test_falha_da_api_nao_vira_mes_vazio():
    sessao = _SessaoFake(falhar_em="202602")
    client = TCEDespesasClient(CONFIG, session=sessao, pausa_base_segundos=0)
    with pytest.raises(TCEDespesasErro):
        client.listar_pagamentos("004", "202500", "202601", "202603")
    # 3 tentativas no mês com erro; não seguiu para 202603
    assert [c["data_referencia_doc"] for c in sessao.chamadas].count("202602") == 3
    assert "202603" not in [c["data_referencia_doc"] for c in sessao.chamadas]


def test_pagina_repetida_interrompe_para_nao_somar_duas_vezes():
    class SessaoIgnoraStart(_SessaoFake):
        def get(self, url, params, timeout):
            params = {**params, "$start_index": 0}
            return super().get(url, params, timeout)

    client = TCEDespesasClient(replace(CONFIG, tce_page_size=2),
                               session=SessaoIgnoraStart(), pausa_base_segundos=0)
    with pytest.raises(TCEDespesasErro, match="mesma página"):
        client.listar_empenhos("004", "202500", "202501", "202501")


# ----------------------------------------------------------------------
# Passo 5: normalização e cruzamento
# ----------------------------------------------------------------------

def test_normalizacao_preserva_texto_e_remove_espacos():
    p = normalizar_pagamento(_pagamentos()[0])
    assert p["codigo_unidade_orcamentaria"] == "01"
    assert p["exercicio_orcamento"] == "202500"
    assert p["data_emissao_empenho"] == "2025-01-10"
    assert p["numero_sub_empenho"] == "001"
    assert p["valor_nota_pagamento"] == Decimal("300000.0000")
    assert data_iso("02/02/2026") == "2026-02-02"


def test_cruzamento_pela_chave_completa_sem_data_referencia():
    cruz, _, _ = _pipeline_em_memoria()
    assert not cruz.pendencias
    assert len(cruz.pagamentos) == len(_pagamentos())
    # o mesmo numero_empenho 10010001 existe nos órgãos 02 e 04
    elementos = {(p["codigo_orgao"], p["numero_nota_pagamento"]): p["elemento_despesa"]
                 for p in cruz.pagamentos}
    assert elementos[("04", "24010001")] == "35"
    assert elementos[("02", "24010099")] == "30"
    # pagamento de 202602 cruzou com empenho de 202505
    sist_2026 = next(p for p in cruz.pagamentos if p["numero_nota_pagamento"] == "02020032")
    assert sist_2026["data_referencia_doc"] == "202602"
    assert sist_2026["data_referencia_empenho"] == "202505"


def test_duplicado_identico_descartado_e_conflitante_vira_pendencia():
    empenhos = [normalizar_empenho(e) for e in _empenhos()]
    pagamentos = [normalizar_pagamento(p) for p in _pagamentos()]
    pagamentos.append(dict(pagamentos[0]))                       # idêntico
    conflito = dict(pagamentos[1]); conflito["valor_nota_pagamento"] = Decimal("1")
    pagamentos.append(conflito)                                   # conflitante
    cruz = cruzar_pagamentos_empenhos(empenhos, pagamentos)
    assert cruz.pagamentos_duplicados_identicos == 1
    assert [p.tipo for p in cruz.pendencias] == ["pagamento_duplicado_conflitante"]
    assert len(cruz.pagamentos) == len(_pagamentos()) - 1


def test_pagamento_sem_empenho_vira_pendencia():
    empenhos = [normalizar_empenho(e) for e in _empenhos()[1:]]   # sem ISYSERVICE
    pagamentos = [normalizar_pagamento(p) for p in _pagamentos()]
    cruz = cruzar_pagamentos_empenhos(empenhos, pagamentos)
    assert sum(p.tipo == "pagamento_sem_empenho" for p in cruz.pendencias) == 4


# ----------------------------------------------------------------------
# Passos 2, 6 e 7: órgãos, filtros e total de controle
# ----------------------------------------------------------------------

def test_camara_identificada_pelo_cadastro():
    camara = identificar_orgaos_camara([normalizar_orgao(o) for o in _orgaos()])
    assert set(camara) == {"01"}


def test_elemento_quinta_e_sexta_posicoes():
    assert elemento_despesa("33903500") == "35"
    assert elemento_despesa("44905200") == "52"
    assert elemento_despesa("33503500") == "35"
    assert elemento_despesa("33909235") == "92"   # não procura '35' em qualquer lugar
    assert elemento_despesa("339035") is None


def test_total_controle_consultoria_reproduz_portal():
    _, filtros, apuracao = _pipeline_em_memoria()
    assert apuracao.controle["35"]["valor"] == Decimal("1083280.71")
    assert apuracao.controle["35"]["quantidade"] == 7
    assert filtros.excluidos["camara"]["valor"] == Decimal("24000.00")
    assert filtros.excluidos["estornado"]["valor"] == Decimal("999.99")
    assert filtros.excluidos["fora_das_sete_naturezas"]["quantidade"] == 1


def test_sem_excluir_camara_total_seria_1107280_71():
    cruz, _, _ = _pipeline_em_memoria()
    filtros = aplicar_filtros_financeiros(
        cruz.pagamentos, "004", "202500", {"01", "02", "04", "AA"}, set()
    )
    total_35 = sum(p["valor_nota_pagamento"] for p in filtros.validos
                   if p["elemento_despesa"] == "35")
    assert total_35 == Decimal("1107280.71")


def test_estado_estorno_desconhecido_vira_pendencia():
    cruz, _, _ = _pipeline_em_memoria()
    cruz.pagamentos[0]["estado_de_estornado"] = None
    filtros = aplicar_filtros_financeiros(cruz.pagamentos, "004", "202500",
                                          {"01", "02", "04", "AA"}, {"01"})
    assert [p.tipo for p in filtros.pendencias] == ["estado_estorno_desconhecido"]


# ----------------------------------------------------------------------
# Passos 8 a 11: documento, porte, ME e reconciliação
# ----------------------------------------------------------------------

def test_documentos():
    assert documento_comparacao("02.530.104/0001-33") == ISY
    assert cnpj_valido(ISY) and cnpj_valido(SIST) and cnpj_valido(WESLEY)
    assert not cnpj_valido("02530104000134")
    assert cpf_valido("52998224725")
    assert tipo_documento("52998224725") == "CPF"
    assert tipo_documento("123") == "INVALIDO"
    assert tipo_documento(None) == "AUSENTE"
    # CNPJ alfanumérico (exemplo oficial da Receita)
    assert cnpj_valido("12ABC34501DE35")


def test_normalizar_porte():
    assert normalizar_porte("Microempresa (ME)") == "01"
    assert normalizar_porte("Empresa de Pequeno Porte (EPP)") == "03"
    assert normalizar_porte("Demais") == "05"
    assert normalizar_porte("Não informado") == "00"
    assert normalizar_porte("05") == "05"
    assert normalizar_porte("") is None
    assert normalizar_porte("Grande") is None   # desconhecido não vira Demais


def test_resposta_opencnpj_com_cnpj_divergente():
    item = classificacao_de_resposta(ISY, {**RESPOSTAS_OPENCNPJ[ISY], "cnpj": SIST}, "X")
    assert item.situacao_classificacao == SIT_CNPJ_DIVERGENTE
    assert item.porte_codigo is None


def test_subtotal_me_consultoria_e_reconciliacao():
    _, _, apuracao = _pipeline_em_memoria()
    cat35 = apuracao.por_categoria["35"]
    assert cat35[CAT_ME]["valor"] == Decimal("881880.06")
    assert cat35[CAT_DEMAIS]["valor"] == Decimal("201400.65")
    assert cat35["EPP"]["valor"] == 0
    assert apuracao.por_categoria["30"][CAT_CPF]["valor"] == Decimal("700.00")
    assert apuracao.reconciliado
    sist = next(f for f in apuracao.fornecedores
                if f["elemento_despesa"] == "35" and f["documento"] == SIST)
    assert sist["valor_pago"] == Decimal("120000.00")      # inclui a nota de 2026
    assert sist["quantidade_pagamentos"] == 2


def test_nao_encontrado_fica_sem_classificacao_e_reconcilia():
    respostas = {k: v for k, v in RESPOSTAS_OPENCNPJ.items() if k != SIST}
    _, _, apuracao = _pipeline_em_memoria(respostas=respostas)
    cat35 = apuracao.por_categoria["35"]
    assert cat35[CAT_ME]["valor"] == Decimal("761880.06")
    assert cat35[CAT_SEM_CLASSIFICACAO]["valor"] == Decimal("120000.00")
    assert apuracao.reconciliado


def test_regra_excluir_mei_exige_indicador_confirmado():
    respostas = dict(RESPOSTAS_OPENCNPJ)
    respostas[SIST] = {**respostas[SIST], "opcao_mei": ""}   # indicador ausente
    _, _, apuracao = _pipeline_em_memoria(REGRA_MEI_EXCLUIR, respostas)
    cat35 = apuracao.por_categoria["35"]
    assert cat35[CAT_ME]["valor"] == Decimal("761880.06")
    assert cat35[CAT_ME_EXCLUIDA_MEI]["valor"] == Decimal("120000.00")
    assert apuracao.reconciliado


def test_cache_evita_nova_consulta():
    client = FakeOpenCNPJ()
    primeiro, _ = classificar_cnpjs([ISY], client, {}, 30, max_workers=1)
    assert primeiro[ISY].situacao_classificacao == SIT_CLASSIFICADO
    _, consultados = classificar_cnpjs([ISY], client, primeiro, 30, max_workers=1)
    assert consultados == [] and client.consultas == [ISY]


# ----------------------------------------------------------------------
# Pipeline completo, modo sem banco
# ----------------------------------------------------------------------

def test_pipeline_sem_banco_gera_relatorio(tmp_path):
    relatorio = despesas_me.executar(
        CONFIG, "004", 2025,
        referencia_pagamentos_fim="202609",
        usar_banco=False, pasta_saida=str(tmp_path),
        tce_client=FakeTCE(), opencnpj_client=FakeOpenCNPJ(),
    )
    assert relatorio["parametros"]["exercicio_orcamento"] == "202500"
    assert relatorio["totais_controle_todos_os_portes"]["35"]["valor"] == Decimal("1083280.71")
    assert relatorio["reconciliado"]
    conferencia = relatorio["conferencia"]
    assert conferencia["mesmo_recorte_de_pagamentos"]
    assert all(f["ok"] for f in conferencia["consultoria_por_fornecedor"])
    assert [o["codigo_orgao"] for o in relatorio["orgaos"] if o["excluido"]] == ["01"]
    # relatório e cache gravados
    assert (tmp_path / "cache_porte_opencnpj.json").exists()
    salvo = json.loads(open(relatorio["arquivo_relatorio"], encoding="utf-8").read())
    assert salvo["por_categoria"]["35"]["ME"]["valor"] == "881880.06"


def test_pipeline_limite_202512_perde_nota_de_2026(tmp_path):
    relatorio = despesas_me.executar(
        CONFIG, "004", 2025, referencia_pagamentos_fim="202512",
        usar_banco=False, pasta_saida=str(tmp_path),
        tce_client=FakeTCE(), opencnpj_client=FakeOpenCNPJ(),
    )
    sist = next(f for f in relatorio["fornecedores"]
                if f["elemento_despesa"] == "35" and f["documento"] == SIST)
    assert sist["valor_pago"] == Decimal("110000.00")
    assert relatorio["conferencia"]["mesmo_recorte_de_pagamentos"] is False


# ----------------------------------------------------------------------
# Paginação instável da API do TCE (observada em Amontada/2025: cada
# coleta trazia 12.306 registros, mas com 45 a 503 repetidos no lugar
# de registros que não vinham)
# ----------------------------------------------------------------------

class _SessaoOrdemInstavel:
    """Cada página é recortada de uma ordenação diferente dos registros."""

    def __init__(self, total=2500, omitir_sempre=None):
        import random
        self.random = random.Random(42)
        self.total = total
        self.omitir_sempre = omitir_sempre   # registro que nunca vem (perda real)
        self.chamadas = 0

    def get(self, url, params, timeout):
        self.chamadas += 1
        ids = list(range(self.total))
        if self.omitir_sempre is not None:
            # a API entrega o mesmo total, mas com um repetido no lugar do omitido
            ids[self.omitir_sempre] = 0
        self.random.shuffle(ids)
        ini, qtd = params["$start_index"], params["$count"]
        elementos = [{"data_referencia_doc": int(params["data_referencia_doc"]),
                      "numero_nota_pagamento": f"{i:08d}", "valor_nota_pagamento": "1.00"}
                     for i in ids[ini: ini + qtd]]
        return _Resposta(200, {"elements": elementos})


def test_paginacao_instavel_recupera_todos_os_registros():
    sessao = _SessaoOrdemInstavel(total=2500)
    client = TCEDespesasClient(CONFIG, session=sessao, pausa_base_segundos=0)
    registros = client.listar_pagamentos("010", "202500", "202501", "202501")

    notas = {r["numero_nota_pagamento"] for r in registros}
    assert len(notas) == 2500 == len(registros)          # nada faltando, nada repetido
    resumo = client.relatorio_coleta[0]
    assert resumo["completo"] and resumo["passadas"] > 1  # precisou reler


def test_mes_estavel_nao_relê():
    sessao = _SessaoFake()
    client = TCEDespesasClient(replace(CONFIG, tce_page_size=2), session=sessao, pausa_base_segundos=0)
    client.listar_pagamentos("004", "202500", "202501", "202501")
    assert client.relatorio_coleta[0]["passadas"] == 1
    assert len(sessao.chamadas) == 3                      # 3 páginas, uma passada só


def test_repeticao_estavel_nao_vira_pendencia():
    # leituras instáveis + um registro que vem repetido em TODAS as leituras
    sessao = _SessaoOrdemInstavel(total=1500, omitir_sempre=7)
    client = TCEDespesasClient(CONFIG, session=sessao, pausa_base_segundos=0)
    registros = client.listar_pagamentos("010", "202500", "202501", "202501")
    resumo = client.relatorio_coleta[0]
    assert resumo["completo"]
    assert resumo["repeticao_estavel"] == 1
    assert len(registros) == 1499


class _SessaoQueNaoEstabiliza(_SessaoOrdemInstavel):
    """Cada passada traz registros que nunca vieram antes (dados mudando)."""

    def __init__(self, total):
        super().__init__(total=total)
        self.passada = 0

    def get(self, url, params, timeout):
        if params["$start_index"] == 0:
            self.passada += 1
        # 1100 registros fixos + 1 registro inédito por passada + repetições
        # para completar o total: a união cresce sempre, mas nunca chega ao total
        ids = list(range(1100)) + [5000 + self.passada] + [0] * (self.total - 1101)
        ini, qtd = params["$start_index"], params["$count"]
        elementos = [{"data_referencia_doc": int(params["data_referencia_doc"]),
                      "numero_nota_pagamento": f"{i:08d}"} for i in ids[ini: ini + qtd]]
        return _Resposta(200, {"elements": elementos})


def test_coleta_que_nao_estabiliza_vira_pendencia(tmp_path):
    sessao = _SessaoQueNaoEstabiliza(total=1200)
    client = TCEDespesasClient(CONFIG, session=sessao, pausa_base_segundos=0)
    client.listar_pagamentos("010", "202500", "202501", "202501")
    resumo = client.relatorio_coleta[0]
    assert not resumo["completo"]
    assert resumo["passadas"] == TCEDespesasClient.MAX_PASSADAS