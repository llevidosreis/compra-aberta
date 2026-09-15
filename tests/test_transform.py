from src.transform.deduplicate import deduplicar_por_chave
from src.transform.normalize import limpar_cnpj, normalizar_item, normalizar_licitante, para_data, para_numero
from src.transform.naturezas import codigo_natureza
from src.load.load_dotacoes import _deduplicar_dotacoes
from src.transform.normalize import normalizar_dotacao


def test_limpar_cnpj_remove_mascara():
    assert limpar_cnpj("31.748.439/0001-20") == "31748439000120"


def test_limpar_cnpj_valor_vazio():
    assert limpar_cnpj(None) is None
    assert limpar_cnpj("") is None


def test_para_data_formato_iso():
    assert para_data("2026-03-15").isoformat() == "2026-03-15"


def test_para_data_formato_brasileiro():
    assert para_data("15/03/2026").isoformat() == "2026-03-15"


def test_para_data_invalida_retorna_none():
    assert para_data("data-quebrada") is None


def test_para_numero_aceita_string():
    assert para_numero("123.45") == 123.45


def test_para_numero_vazio_retorna_none():
    assert para_numero("") is None
    assert para_numero(None) is None


def test_normalizar_licitante_limpa_cnpj():
    bruto = {
        "codigo_municipio": "0100107",
        "numero_licitacao": "001/2026",
        "numero_documento_negociante": "31.748.439/0001-20",
        "nome_negociante": "EMPRESA EXEMPLO LTDA",
    }
    resultado = normalizar_licitante(bruto)
    assert resultado["numero_documento_negociante"] == "31748439000120"
    assert resultado["codigo_municipio"] == "0100107"


def test_normalizar_item_converte_numeros():
    bruto = {
        "codigo_municipio": "0100107",
        "numero_licitacao": "001/2026",
        "numero_sequencial_item_licitacao": "3",
        "numero_documento_negociante": "31748439000120",
        "valor_vencedor_item_licitacao": "1500.50",
    }
    resultado = normalizar_item(bruto)
    assert resultado["numero_sequencial_item_licitacao"] == 3
    assert resultado["valor_vencedor_item_licitacao"] == 1500.50


def test_normalizar_datas_e_preserva_sequencia_ausente():
    resultado = normalizar_item({
        "codigo_municipio": "010",
        "numero_licitacao": "001/2026",
        "data_realizacao_licitacao": "15/03/2026",
        "numero_documento_negociante": "31748439000120",
    })
    assert resultado["data_realizacao_licitacao"].isoformat() == "2026-03-15"
    assert resultado["numero_sequencial_item_licitacao"] is None


def test_deduplicar_por_chave_mantem_ultimo():
    registros = [
        {"id": "a", "valor": 1},
        {"id": "a", "valor": 2},
        {"id": "b", "valor": 3},
    ]
    resultado = deduplicar_por_chave(registros, chave=lambda r: (r["id"],))
    assert len(resultado) == 2
    valores = {r["id"]: r["valor"] for r in resultado}
    assert valores["a"] == 2


def test_codigo_natureza_classifica_elementos_basicos_e_sufixos():
    assert codigo_natureza("33903900") == "39"
    assert codigo_natureza("33903999") == "39"
    assert codigo_natureza("44905200") == "52"
    assert codigo_natureza("33909900") is None


def test_normalizar_dotacao_preserva_codigo_e_normaliza_campos():
    resultado = normalizar_dotacao({
        "codigo_municipio": "010",
        "numero_licitacao": "001/2025",
        "exercicio_orcamento": 202500,
        "codigo_elemento_despesa": "33.903.900",
        "codigo_orgao": None,
    })
    assert resultado["codigo_elemento_despesa"] == "33903900"
    assert resultado["codigo_orgao"] == ""


def test_deduplicar_dotacoes_preserva_fontes_diferentes():
    base = {
        "codigo_municipio": "010",
        "numero_licitacao": "001/2025",
        "exercicio_orcamento": 202500,
        "codigo_orgao": "01",
        "codigo_unidade_orcamentaria": "01",
        "codigo_funcao": "04",
        "codigo_subfuncao": "122",
        "codigo_programa": "0100",
        "codigo_projeto_atividade": "2",
        "numero_projeto_atividade": "001",
        "numero_subprojeto_atividade": "0000",
        "codigo_elemento_despesa": "33903900",
        "codigo_fonte": "500000000",
        "data_referencia_doc": 202501,
    }
    registros = [
        {**base, "tipo_fonte": "1"},
        {**base, "tipo_fonte": "2"},
        {**base, "tipo_fonte": "1", "valor_dotacao_doc": 99},
    ]
    resultado = _deduplicar_dotacoes(registros)
    assert len(resultado) == 2
    assert {r["tipo_fonte"] for r in resultado} == {"1", "2"}
