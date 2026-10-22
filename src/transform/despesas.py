"""
Normalização de órgãos, empenhos e pagamentos do TCE-CE (passo 5.1 do guia)
e regras de interpretação usadas nas etapas seguintes:

  - códigos e números de documento continuam TEXTO (zeros à esquerda);
  - espaços nas extremidades são removidos ("01  " -> "01");
  - datas viram AAAA-MM-DD nas duas bases;
  - valores viram Decimal (nunca float);
  - elemento de despesa = 5ª e 6ª posições do código de 8 posições;
  - CPF/CNPJ são diferenciados por tamanho + dígito verificador;
  - porte devolvido pela fonte cadastral é convertido para o código da
    Receita Federal (01 ME, 03 EPP, 05 Demais, 00 Não informado).
"""
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# Chave completa do empenho (passo 5.2) e da nota de pagamento (passo 5.3)
CHAVE_EMPENHO: tuple[str, ...] = (
    "codigo_municipio",
    "exercicio_orcamento",
    "codigo_orgao",
    "codigo_unidade_orcamentaria",
    "data_emissao_empenho",
    "numero_empenho",
)
CHAVE_PAGAMENTO: tuple[str, ...] = CHAVE_EMPENHO + (
    "numero_sub_empenho",
    "numero_nota_pagamento",
)


# ----------------------------------------------------------------------
# Tipos básicos
# ----------------------------------------------------------------------

def texto(valor: Any) -> str | None:
    """Texto sem espaços nas extremidades; vazio vira None."""
    if valor is None:
        return None
    resultado = str(valor).strip()
    return resultado or None


def data_iso(valor: Any) -> str | None:
    """Converte '2025-01-01T00:00:00', '2025-01-01' ou '01/01/2025' para AAAA-MM-DD."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    bruto = str(valor).strip()
    for formato, tamanho in (
        ("%Y-%m-%dT%H:%M:%S.%f", None),
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d", 10),
        ("%d/%m/%Y", 10),
    ):
        try:
            trecho = bruto if tamanho is None else bruto[:tamanho]
            return datetime.strptime(trecho, formato).date().isoformat()
        except ValueError:
            continue
    return None


def decimal_ou_none(valor: Any) -> Decimal | None:
    """Valor monetário em Decimal; nunca usa float como intermediário."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError):
        return None


def sem_acentos_maiusculo(valor: str | None) -> str:
    if not valor:
        return ""
    decomposto = unicodedata.normalize("NFKD", valor)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).upper().strip()


# ----------------------------------------------------------------------
# Elemento de despesa (passo 6)
# ----------------------------------------------------------------------

def elemento_despesa(codigo_elemento_despesa: Any) -> str | None:
    """Elemento = 5ª e 6ª posições do código de 8 posições.

    Ex.: '33903500' -> '35'. Não procura '35' em qualquer parte do código.
    Código fora do formato de 8 dígitos devolve None (vira pendência).
    """
    codigo = texto(codigo_elemento_despesa)
    if not codigo or len(codigo) != 8 or not codigo.isdigit():
        return None
    return codigo[4:6]


# ----------------------------------------------------------------------
# Documento do credor (passo 8)
# ----------------------------------------------------------------------

def documento_comparacao(valor: Any) -> str | None:
    """Remove só a formatação de apresentação (. / - espaço). Continua texto."""
    bruto = texto(valor)
    if not bruto:
        return None
    limpo = "".join(c for c in bruto if c not in ".-/ \t").upper()
    return limpo or None


def _valor_caractere_cnpj(caractere: str) -> int:
    # Regra da Receita (vale para CNPJ numérico e alfanumérico): ASCII - 48
    return ord(caractere) - 48


def cnpj_valido(documento: str | None) -> bool:
    """Valida CNPJ de 14 posições (numérico ou alfanumérico) pelo DV."""
    if not documento or len(documento) != 14:
        return False
    base, dv = documento[:12], documento[12:]
    if not dv.isdigit() or not all(c.isdigit() or "A" <= c <= "Z" for c in base):
        return False
    if len(set(documento)) == 1:
        return False

    def calcular(trecho: str) -> str:
        pesos = list(range(len(trecho) - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(_valor_caractere_cnpj(c) * p for c, p in zip(trecho, pesos))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    primeiro = calcular(base)
    segundo = calcular(base + primeiro)
    return dv == primeiro + segundo


def cpf_valido(documento: str | None) -> bool:
    if not documento or len(documento) != 11 or not documento.isdigit():
        return False
    if len(set(documento)) == 1:
        return False
    for posicao in (9, 10):
        soma = sum(int(documento[i]) * (posicao + 1 - i) for i in range(posicao))
        digito = (soma * 10) % 11 % 10
        if digito != int(documento[posicao]):
            return False
    return True


def tipo_documento(documento: str | None) -> str:
    """CNPJ, CPF, INVALIDO ou AUSENTE. Não completa nem corrige documentos."""
    if not documento:
        return "AUSENTE"
    if cnpj_valido(documento):
        return "CNPJ"
    if cpf_valido(documento):
        return "CPF"
    return "INVALIDO"


def cnpj_basico(cnpj_completo: str) -> str:
    """Oito primeiras posições: chave da tabela EMPRESAS da Receita."""
    return cnpj_completo[:8]


# ----------------------------------------------------------------------
# Porte (passos 9 e 10)
# ----------------------------------------------------------------------

PORTE_ME = "01"
PORTE_EPP = "03"
PORTE_DEMAIS = "05"
PORTE_NAO_INFORMADO = "00"

_PORTES_POR_CODIGO = {
    "0": PORTE_NAO_INFORMADO, "00": PORTE_NAO_INFORMADO,
    "1": PORTE_ME, "01": PORTE_ME,
    "3": PORTE_EPP, "03": PORTE_EPP,
    "5": PORTE_DEMAIS, "05": PORTE_DEMAIS,
}
_PORTES_POR_DESCRICAO = {
    # formato observado no OpenCNPJ (campo porte_empresa)
    "MICROEMPRESA (ME)": PORTE_ME,
    "MICROEMPRESA": PORTE_ME,
    "ME": PORTE_ME,
    "EMPRESA DE PEQUENO PORTE (EPP)": PORTE_EPP,
    "EMPRESA DE PEQUENO PORTE": PORTE_EPP,
    "EPP": PORTE_EPP,
    "DEMAIS": PORTE_DEMAIS,
    "NAO INFORMADO": PORTE_NAO_INFORMADO,
}


def normalizar_porte(valor: Any) -> str | None:
    """Converte o porte da fonte para o código da Receita.

    Devolve None quando o valor está ausente ou é desconhecido — nesses casos
    o fornecedor fica SEM classificação (nunca vira ME ou Demais por suposição).
    """
    bruto = texto(valor)
    if bruto is None:
        return None
    if bruto in _PORTES_POR_CODIGO:
        return _PORTES_POR_CODIGO[bruto]
    return _PORTES_POR_DESCRICAO.get(sem_acentos_maiusculo(bruto))


def indicador_sim_nao(valor: Any) -> bool | None:
    """'S' -> True, 'N' -> False, qualquer outra coisa -> None (não comprovado)."""
    bruto = sem_acentos_maiusculo(texto(valor))
    if bruto in ("S", "SIM", "TRUE", "1"):
        return True
    if bruto in ("N", "NAO", "FALSE", "0"):
        return False
    return None


# ----------------------------------------------------------------------
# Registros
# ----------------------------------------------------------------------

def normalizar_orgao(bruto: dict[str, Any]) -> dict[str, Any]:
    return {
        "codigo_municipio": texto(bruto.get("codigo_municipio")),
        "exercicio_orcamento": texto(bruto.get("exercicio_orcamento")),
        "codigo_orgao": texto(bruto.get("codigo_orgao")),
        "nome_orgao": texto(bruto.get("nome_orgao")),
        "codigo_tipo_unidade": texto(bruto.get("codigo_tipo_unidade")),
    }


def normalizar_empenho(bruto: dict[str, Any]) -> dict[str, Any]:
    documento = documento_comparacao(bruto.get("numero_documento_negociante"))
    return {
        "codigo_municipio": texto(bruto.get("codigo_municipio")),
        "exercicio_orcamento": texto(bruto.get("exercicio_orcamento")),
        "codigo_orgao": texto(bruto.get("codigo_orgao")),
        "codigo_unidade_orcamentaria": texto(bruto.get("codigo_unidade_orcamentaria")),
        "data_emissao_empenho": data_iso(bruto.get("data_emissao_empenho")),
        "numero_empenho": texto(bruto.get("numero_empenho")),
        "codigo_elemento_despesa": texto(bruto.get("codigo_elemento_despesa")),
        "elemento_despesa": elemento_despesa(bruto.get("codigo_elemento_despesa")),
        # documento original preservado para rastreamento
        "documento_original": texto(bruto.get("numero_documento_negociante")),
        "documento": documento,
        "tipo_documento": tipo_documento(documento),
        "codigo_tipo_negociante": texto(bruto.get("codigo_tipo_negociante")),
        "nome_negociante": texto(bruto.get("nome_negociante")),
        "data_referencia_doc": texto(bruto.get("data_referencia_doc")),
        "valor_empenhado": decimal_ou_none(bruto.get("valor_empenhado")),
    }


def normalizar_pagamento(bruto: dict[str, Any]) -> dict[str, Any]:
    return {
        "codigo_municipio": texto(bruto.get("codigo_municipio")),
        "exercicio_orcamento": texto(bruto.get("exercicio_orcamento")),
        "codigo_orgao": texto(bruto.get("codigo_orgao")),
        "codigo_unidade_orcamentaria": texto(bruto.get("codigo_unidade_orcamentaria")),
        "data_emissao_empenho": data_iso(bruto.get("data_emissao_empenho")),
        "numero_empenho": texto(bruto.get("numero_empenho")),
        "numero_sub_empenho": texto(bruto.get("numero_sub_empenho")),
        "numero_nota_pagamento": texto(bruto.get("numero_nota_pagamento")),
        "data_nota_pagamento": data_iso(bruto.get("data_nota_pagamento")),
        "data_referencia_doc": texto(bruto.get("data_referencia_doc")),
        "valor_nota_pagamento": decimal_ou_none(bruto.get("valor_nota_pagamento")),
        "estado_de_estornado": texto(bruto.get("estado_de_estornado")),
    }


def chave(registro: dict[str, Any], campos: tuple[str, ...]) -> tuple:
    return tuple(registro.get(campo) for campo in campos)