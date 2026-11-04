"""
Normalização dos registros crus vindos das APIs do TCE-CE, antes de
irem para as tabelas de staging.

A API devolve tudo como string (mesmo campos numéricos), então aqui
convertemos para os tipos reais e tratamos os poucos casos de valor
ausente/vazio que aparecem na prática.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any


def limpar_cnpj(valor: str | None) -> str | None:
    if not valor:
        return None
    return "".join(caractere for caractere in valor if caractere.isdigit())


def _limpar_documento_negociante(valor: Any) -> str | None:
    if not valor:
        return None
    texto = str(valor).strip()
    if not re.fullmatch(r"[0-9./()\- ]+", texto):
        return None
    documento = limpar_cnpj(texto)
    return documento if documento and len(documento) in (11, 14) else None


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


def para_numero(valor: Any) -> Decimal | None:
    if valor is None or valor == "":
        return None
    try:
        numero = Decimal(str(valor).strip())
        return numero if numero.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _erro_normalizacao(campo: str, valor: Any, motivo: str) -> dict[str, dict[str, str]]:
    return {campo: {"motivo": motivo, "valor_original": repr(valor)}}


def _data_com_diagnostico(valor: Any, campo: str) -> tuple[date | None, dict[str, dict[str, str]]]:
    resultado = para_data(valor)
    if resultado is not None or valor in (None, ""):
        return resultado, {}
    return None, _erro_normalizacao(campo, valor, f"{campo} com formato de data invalido")


def _numero_com_diagnostico(valor: Any, campo: str) -> tuple[Decimal | None, dict[str, dict[str, str]]]:
    resultado = para_numero(valor)
    if resultado is not None or valor in (None, ""):
        return resultado, {}
    return None, _erro_normalizacao(campo, valor, f"{campo} com valor numerico invalido")

def para_datetime(valor: Any) -> datetime | None:
    """Normaliza datas ISO em datetime com timezone UTC quando necessário."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    if isinstance(valor, date):
        return datetime.combine(valor, datetime.min.time(), tzinfo=timezone.utc)
    texto = str(valor).strip()
    try:
        resultado = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return resultado if resultado.tzinfo else resultado.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _inteiro_com_diagnostico(valor: Any, campo: str) -> tuple[int | None, dict[str, dict[str, str]]]:
    if valor in (None, ""):
        return None, {}
    try:
        return int(str(valor).strip()), {}
    except (TypeError, ValueError):
        return None, _erro_normalizacao(campo, valor, f"{campo} invalido")


def normalizar_dotacao(bruto: dict[str, Any]) -> dict[str, Any]:
    data_realizacao, erros_data = _data_com_diagnostico(
        bruto.get("data_realizacao_licitacao"), "data_realizacao_licitacao"
    )
    valor_dotacao, erros_valor = _numero_com_diagnostico(
        bruto.get("valor_dotacao_doc"), "valor_dotacao_doc"
    )
    codigo_elemento = bruto.get("codigo_elemento_despesa")
    codigo_elemento = (
        "".join(ch for ch in str(codigo_elemento) if ch.isdigit())
        if codigo_elemento not in (None, "")
        else None
    )
    return {
        "codigo_municipio": str(bruto.get("codigo_municipio") or "").strip(),
        "data_realizacao_licitacao": data_realizacao,
        "_erros_normalizacao": erros_data | erros_valor,
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
        "valor_dotacao_doc": valor_dotacao,
        "data_referencia_doc": bruto.get("data_referencia_doc"),
        "codigo_unidade_orcamentaria": str(bruto.get("codigo_unidade_orcamentaria") or "").strip(),
    }


def normalizar_licitante(bruto: dict[str, Any]) -> dict[str, Any]:
    data_realizacao, erros_normalizacao = _data_com_diagnostico(
        bruto.get("data_realizacao_licitacao"), "data_realizacao_licitacao"
    )
    return {
        "codigo_municipio": str(bruto.get("codigo_municipio") or "").strip(),
        "data_realizacao_licitacao": data_realizacao,
        "_erros_normalizacao": erros_normalizacao,
        "numero_licitacao": str(bruto.get("numero_licitacao") or "").strip(),
        "numero_documento_negociante": _limpar_documento_negociante(
            bruto.get("numero_documento_negociante")
        ),
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
    sequencial, erros_normalizacao = _inteiro_com_diagnostico(
        bruto.get("numero_sequencial_item_licitacao"),
        "numero_sequencial_item_licitacao",
    )
    data_realizacao, erros_data = _data_com_diagnostico(
        bruto.get("data_realizacao_licitacao"), "data_realizacao_licitacao"
    )
    valor_vencedor, erros_valor = _numero_com_diagnostico(
        bruto.get("valor_vencedor_item_licitacao"), "valor_vencedor_item_licitacao"
    )
    quantidade, erros_quantidade = _numero_com_diagnostico(
        bruto.get("numero_quantidade_item_licitacao"), "numero_quantidade_item_licitacao"
    )
    valor_unitario, erros_unitario = _numero_com_diagnostico(
        bruto.get("valor_unitario_item_licitacao"), "valor_unitario_item_licitacao"
    )
    return {
        "codigo_municipio": str(bruto.get("codigo_municipio") or "").strip(),
        "data_realizacao_licitacao": data_realizacao,
        "numero_licitacao": str(bruto.get("numero_licitacao") or "").strip(),
        "numero_sequencial_item_licitacao": sequencial,
        "_erros_normalizacao": erros_normalizacao | erros_data | erros_valor | erros_quantidade | erros_unitario,
        "numero_documento_negociante": _limpar_documento_negociante(
            bruto.get("numero_documento_negociante")
        ),
        "descricao_item_licitacao": bruto.get("descricao_item_licitacao"),
        "valor_vencedor_item_licitacao": valor_vencedor,
        "codigo_tipo_negociante": bruto.get("codigo_tipo_negociante"),
        "descricao_unidade_item_licitacao": bruto.get("descricao_unidade_item_licitacao"),
        "numero_quantidade_item_licitacao": quantidade,
        "valor_unitario_item_licitacao": valor_unitario,
        "data_referencia_doc": bruto.get("data_referencia_doc"),
    }
