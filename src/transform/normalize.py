"""
Normalização dos registros crus vindos das APIs do TCE-CE, antes de
irem para as tabelas de staging.

A API devolve tudo como string (mesmo campos numéricos), então aqui
convertemos para os tipos reais e tratamos os poucos casos de valor
ausente/vazio que aparecem na prática.
"""
from datetime import date, datetime
from typing import Any


def limpar_cnpj(valor: str | None) -> str | None:
    if not valor:
        return None
    return "".join(caractere for caractere in valor if caractere.isdigit())


def para_data(valor: Any) -> date | None:
    """A documentação não fixa um único formato de data nos retornos do
    TCE-CE, então tentamos os formatos observados na prática (ISO e
    dd/mm/aaaa) antes de desistir e deixar o campo nulo.
    """
    if not valor:
        return None
    if isinstance(valor, date):
        return valor

    texto = str(valor).strip()
    for formato in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def para_numero(valor: Any) -> float | None:
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def normalizar_dotacao(bruto: dict[str, Any]) -> dict[str, Any]:
    codigo_elemento = bruto.get("codigo_elemento_despesa")
    codigo_elemento = (
        "".join(ch for ch in str(codigo_elemento) if ch.isdigit())
        if codigo_elemento not in (None, "")
        else None
    )
    return {
        "codigo_municipio": str(bruto.get("codigo_municipio") or "").strip(),
        "data_realizacao_licitacao": para_data(bruto.get("data_realizacao_licitacao")),
        "numero_licitacao": str(bruto.get("numero_licitacao") or "").strip(),
        "exercicio_orcamento": bruto.get("exercicio_orcamento"),
        "codigo_orgao": str(bruto.get("codigo_orgao") or "").strip(),
        "codigo_funcao": str(bruto.get("codigo_funcao") or "").strip(),
        "codigo_subfuncao": str(bruto.get("codigo_subfuncao") or "").strip(),
        "codigo_programa": str(bruto.get("codigo_programa") or "").strip(),
        "codigo_projeto_atividade": str(bruto.get("codigo_projeto_atividade") or "").strip(),
        "numero_projeto_atividade": str(bruto.get("numero_projeto_atividade") or "").strip(),
        "numero_subprojeto_atividade": str(bruto.get("numero_subprojeto_atividade") or "").strip(),
        "codigo_elemento_despesa": codigo_elemento,
        "tipo_fonte": str(bruto.get("tipo_fonte") or "").strip(),
        "codigo_fonte": str(bruto.get("codigo_fonte") or "").strip(),
        "valor_dotacao_doc": para_numero(bruto.get("valor_dotacao_doc")),
        "data_referencia_doc": bruto.get("data_referencia_doc"),
        "codigo_unidade_orcamentaria": str(bruto.get("codigo_unidade_orcamentaria") or "").strip(),
    }


def normalizar_licitante(bruto: dict[str, Any]) -> dict[str, Any]:
    return {
        "codigo_municipio": str(bruto.get("codigo_municipio") or "").strip(),
        "data_realizacao_licitacao": para_data(bruto.get("data_realizacao_licitacao")),
        "numero_licitacao": str(bruto.get("numero_licitacao") or "").strip(),
        "numero_documento_negociante": limpar_cnpj(bruto.get("numero_documento_negociante")),
        "codigo_tipo_negociante": bruto.get("codigo_tipo_negociante"),
        "nome_negociante": bruto.get("nome_negociante"),
        "endereco_negociante": bruto.get("endereco_negociante"),
        "fone_negociante": bruto.get("fone_negociante"),
        "cep_negociante": bruto.get("cep_negociante"),
        "nome_municipio_negociante": bruto.get("nome_municipio_negociante"),
        "codigo_uf": bruto.get("codigo_uf"),
        "data_referencia_doc": bruto.get("data_referencia_doc"),
    }


def normalizar_item(bruto: dict[str, Any]) -> dict[str, Any]:
    return {
        "codigo_municipio": str(bruto.get("codigo_municipio") or "").strip(),
        "data_realizacao_licitacao": para_data(bruto.get("data_realizacao_licitacao")),
        "numero_licitacao": str(bruto.get("numero_licitacao") or "").strip(),
        "numero_sequencial_item_licitacao": (
            int(bruto["numero_sequencial_item_licitacao"])
            if bruto.get("numero_sequencial_item_licitacao") not in (None, "")
            else None
        ),
        "numero_documento_negociante": limpar_cnpj(bruto.get("numero_documento_negociante")),
        "descricao_item_licitacao": bruto.get("descricao_item_licitacao"),
        "valor_vencedor_item_licitacao": para_numero(bruto.get("valor_vencedor_item_licitacao")),
        "codigo_tipo_negociante": bruto.get("codigo_tipo_negociante"),
        "descricao_unidade_item_licitacao": bruto.get("descricao_unidade_item_licitacao"),
        "numero_quantidade_item_licitacao": para_numero(bruto.get("numero_quantidade_item_licitacao")),
        "valor_unitario_item_licitacao": para_numero(bruto.get("valor_unitario_item_licitacao")),
        "data_referencia_doc": bruto.get("data_referencia_doc"),
    }
