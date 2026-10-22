"""
Conferência dos totais calculados contra valores de referência (passos 7 e 12).

Os valores de Acaraú/2025 vêm do guia: portal em 19/10/2026, coleta de
pagamentos até a referência 202609. Eles só são comparáveis com uma coleta
do MESMO recorte; com outra data de atualização do portal, diferenças são
esperadas e devem ser investigadas, não "corrigidas" nos pagamentos.
"""
from decimal import Decimal
from typing import Any

# (codigo_municipio, exercicio_orcamento) -> referência
REFERENCIAS: dict[tuple[str, str], dict[str, Any]] = {
    ("004", "202500"): {
        "descricao": "Acaraú/2025 — portal TCE em 19/10/2026, pagamentos 202501 a 202609",
        "referencia_pagamentos_fim": "202609",
        "totais_por_elemento": {
            "39": Decimal("66318441.19"),
            "30": Decimal("29531293.65"),
            "51": Decimal("31324092.95"),
            "52": Decimal("5166925.30"),
            "32": Decimal("1877012.00"),
            "40": Decimal("10483.64"),
            "35": Decimal("1083280.71"),
        },
        "total_geral": Decimal("135311529.44"),
        "quantidade_empenhos": 6712,
        "quantidade_pagamentos": 11956,
        # Passo 12: Consultoria (elemento 35), todos os portes, só Prefeitura
        "fornecedores_elemento_35": {
            "02530104000133": Decimal("761880.06"),
            "17016110000144": Decimal("120000.00"),
            "26940943000198": Decimal("201400.65"),
        },
        "quantidade_pagamentos_elemento_35": 128,
        # Relatório histórico do consultor (referência cadastral desconhecida)
        "consultor_me_elemento_35": Decimal("881880.06"),
    },
}


def referencia_para(codigo_municipio: str, exercicio_orcamento: str) -> dict[str, Any] | None:
    return REFERENCIAS.get((codigo_municipio, exercicio_orcamento))


def comparar(
    referencia: dict[str, Any],
    controle: dict[str, dict[str, Any]],
    fornecedores: list[dict[str, Any]],
    referencia_pagamentos_fim: str,
) -> dict[str, Any]:
    linhas = []
    total_calculado = Decimal("0")
    for elemento, esperado in referencia["totais_por_elemento"].items():
        calculado = controle[elemento]["valor"]
        total_calculado += calculado
        linhas.append({
            "elemento": elemento,
            "esperado": esperado,
            "calculado": calculado,
            "diferenca": calculado - esperado,
            "ok": calculado == esperado,
        })

    por_cnpj_35 = {
        f["documento"]: f["valor_pago"]
        for f in fornecedores
        if f["elemento_despesa"] == "35"
    }
    fornecedores_35 = [
        {
            "cnpj": cnpj,
            "esperado": esperado,
            "calculado": por_cnpj_35.get(cnpj, Decimal("0")),
            "ok": por_cnpj_35.get(cnpj) == esperado,
        }
        for cnpj, esperado in referencia["fornecedores_elemento_35"].items()
    ]

    mesmo_recorte = referencia_pagamentos_fim == referencia["referencia_pagamentos_fim"]
    return {
        "descricao": referencia["descricao"],
        "mesmo_recorte_de_pagamentos": mesmo_recorte,
        "aviso": None if mesmo_recorte else (
            "A coleta usou outro limite de referência de pagamentos; "
            "diferenças em relação à referência são esperadas."
        ),
        "por_elemento": linhas,
        "total_esperado": referencia["total_geral"],
        "total_calculado": total_calculado,
        "total_ok": total_calculado == referencia["total_geral"],
        "consultoria_por_fornecedor": fornecedores_35,
        "consultor_me_elemento_35": referencia["consultor_me_elemento_35"],
    }