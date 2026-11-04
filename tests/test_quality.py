from src.quality.validators import validar_itens, validar_licitantes


def _licitante_valido(**sobrescreve):
    base = {
        "codigo_municipio": "0100107",
        "numero_licitacao": "001/2026",
        "numero_documento_negociante": "31748439000120",
    }
    base.update(sobrescreve)
    return base


def _item_valido(**sobrescreve):
    base = _licitante_valido()
    base.update({
        "numero_sequencial_item_licitacao": 1,
        "valor_vencedor_item_licitacao": 100.0,
    })
    base.update(sobrescreve)
    return base


def test_validar_licitantes_aceita_registro_completo():
    resultado = validar_licitantes([_licitante_valido()])
    assert len(resultado.validos) == 1
    assert len(resultado.invalidos) == 0


def test_validar_licitantes_rejeita_sem_municipio():
    resultado = validar_licitantes([_licitante_valido(codigo_municipio="")])
    assert len(resultado.validos) == 0
    assert len(resultado.invalidos) == 1


def test_validar_licitantes_rejeita_cnpj_com_tamanho_invalido():
    resultado = validar_licitantes([_licitante_valido(numero_documento_negociante="123")])
    assert len(resultado.invalidos) == 1
    assert "tamanho inválido" in resultado.invalidos[0][1]


def test_validar_itens_rejeita_valor_negativo():
    resultado = validar_itens([_item_valido(valor_vencedor_item_licitacao=-10.0)])
    assert len(resultado.invalidos) == 1
    assert "negativo" in resultado.invalidos[0][1]


def test_validar_itens_rejeita_valor_vencedor_que_falhou_na_normalizacao():
    registro = _item_valido(
        valor_vencedor_item_licitacao=None,
        _erros_normalizacao={
            "valor_vencedor_item_licitacao": {
                "motivo": "valor_vencedor_item_licitacao com valor numerico invalido",
                "valor_original": "'NaN'",
            }
        },
    )

    resultado = validar_itens([registro])

    assert resultado.validos == []
    assert resultado.invalidos == [
        (registro, "valor_vencedor_item_licitacao com valor numerico invalido")
    ]


def test_validar_itens_aceita_valor_vencedor_zero_ou_ausente_sem_erro_de_parse():
    resultado = validar_itens([
        _item_valido(valor_vencedor_item_licitacao=0),
        _item_valido(numero_sequencial_item_licitacao=2, valor_vencedor_item_licitacao=None),
    ])

    assert len(resultado.validos) == 2
    assert resultado.invalidos == []


def test_validar_itens_rejeita_sequencia_ausente():
    resultado = validar_itens([_item_valido(numero_sequencial_item_licitacao=None)])
    assert len(resultado.invalidos) == 1
    assert "sequencial" in resultado.invalidos[0][1]


def test_validar_itens_aceita_registro_completo():
    resultado = validar_itens([_item_valido()])
    assert len(resultado.validos) == 1


def test_validar_itens_preserva_item_sem_fornecedor_identificado():
    resultado = validar_itens([
        _item_valido(numero_documento_negociante=None),
        _item_valido(numero_documento_negociante="DOCUMENTO_INVALIDO"),
    ])

    assert len(resultado.validos) == 2
    assert [item["numero_documento_negociante"] for item in resultado.validos] == [
        None,
        None,
    ]
    assert resultado.invalidos == []
