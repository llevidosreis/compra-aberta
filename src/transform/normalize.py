"""
Normalização dos registros crus vindos das APIs do TCE-CE, antes de
irem para as tabelas de staging.

A API devolve tudo como string (mesmo campos numéricos), então aqui
convertemos para os tipos reais e tratamos os poucos casos de valor
ausente/vazio que aparecem na prática.
"""
from datetime import date, datetime
import re
import unicodedata
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

def para_datetime(valor: Any) -> datetime | None:
    """Preserva o horário do PNCP para avaliar o prazo real das propostas."""
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


_STOPWORDS = {
    "a", "as", "ao", "aos", "com", "da", "das", "de", "do", "dos",
    "e", "em", "entre", "para", "por", "o", "os", "um", "uma", "uns",
    "umas", "no", "na", "nos", "nas", "ou", "que", "se", "sob", "sobre",
}


def normalizar_texto_busca(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").lower())
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    palavras = re.findall(r"[a-z0-9]+", texto)
    return " ".join(palavra for palavra in palavras if palavra not in _STOPWORDS)


def _primeiro(*valores: Any) -> Any:
    return next((valor for valor in valores if valor not in (None, "")), None)


def _para_inteiro_opcional(valor: Any) -> int | None:
    try:
        return int(valor) if valor not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _bool_opcional(valor: Any) -> bool | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    texto = str(valor).strip().lower()
    if texto in {"true", "1", "sim", "s", "yes"}:
        return True
    if texto in {"false", "0", "nao", "não", "n", "no"}:
        return False
    return None


def normalizar_contratacao_pncp(bruto: dict[str, Any]) -> dict[str, Any]:
    orgao = bruto.get("orgaoEntidade") or bruto.get("orgao") or {}
    unidade = bruto.get("unidadeOrgao") or bruto.get("unidade") or {}
    numero_controle = _primeiro(bruto.get("numeroControlePNCP"), bruto.get("numero_controle_pncp"))
    cnpj_orgao = limpar_cnpj(_primeiro(orgao.get("cnpj"), bruto.get("orgaoCnpj"), bruto.get("cnpjOrgao")))
    ano = _primeiro(bruto.get("anoCompra"), bruto.get("ano_compra"))
    sequencial = _primeiro(bruto.get("sequencialCompra"), bruto.get("sequencial_compra"))
    numero_compra = _primeiro(bruto.get("numeroCompra"), bruto.get("numero_compra"))
    modalidade_codigo = _primeiro(bruto.get("modalidadeId"), bruto.get("codigoModalidadeContratacao"), bruto.get("modalidade_codigo"))
    objeto = _primeiro(bruto.get("objetoCompra"), bruto.get("objeto_compra"))
    return {
        "numero_controle_pncp": str(numero_controle).strip() if numero_controle else None,
        "numero_compra": str(numero_compra).strip() if numero_compra else None,
        "ano_compra": _para_inteiro_opcional(ano),
        "sequencial_compra": _para_inteiro_opcional(sequencial),
        "modalidade_codigo": _para_inteiro_opcional(modalidade_codigo),
        "modalidade_nome": _primeiro(bruto.get("modalidadeNome"), bruto.get("modalidade_nome")),
        "data_publicacao": para_datetime(_primeiro(bruto.get("dataPublicacaoPncp"), bruto.get("dataPublicacaoPNCP"), bruto.get("data_publicacao"))),
        "data_atualizacao": para_datetime(_primeiro(bruto.get("dataAtualizacao"), bruto.get("dataAtualizacaoGlobal"), bruto.get("data_atualizacao"))),
        "data_abertura_proposta": para_datetime(_primeiro(bruto.get("dataAberturaProposta"), bruto.get("data_abertura_proposta"))),
        "data_encerramento_proposta": para_datetime(_primeiro(bruto.get("dataEncerramentoProposta"), bruto.get("data_encerramento_proposta"))),
        "orgao_cnpj": cnpj_orgao,
        "orgao_razao_social": _primeiro(orgao.get("razaoSocial"), bruto.get("orgaoRazaoSocial"), bruto.get("orgao_razao_social")),
        "uf": _primeiro(unidade.get("ufSigla"), bruto.get("uf")),
        "codigo_ibge_municipio": (
            str(codigo_ibge)
            if (codigo_ibge := _primeiro(unidade.get("codigoIbge"), bruto.get("codigoIbgeMunicipio"), bruto.get("codigo_ibge_municipio"))) is not None
            else None
        ),
        "nome_municipio": _primeiro(unidade.get("municipioNome"), bruto.get("municipioNome"), bruto.get("nome_municipio")),
        "objeto_compra": objeto,
        "link_edital_pncp": (
            f"https://pncp.gov.br/app/editais/{cnpj_orgao}/{ano}/{sequencial}"
            if cnpj_orgao and ano not in (None, "") and sequencial not in (None, "")
            else None
        ),
        "objeto_normalizado": normalizar_texto_busca(objeto),
        "exclusivo_me_epp": _bool_opcional(_primeiro(bruto.get("exclusivoMeEpp"), bruto.get("exclusivoME_EPP"), bruto.get("exclusivo_me_epp"))),
    }


def normalizar_item_pncp(bruto: dict[str, Any]) -> dict[str, Any]:
    return {
        "numero_item": _primeiro(bruto.get("numeroItem"), bruto.get("numero_item")),
        "descricao": _primeiro(bruto.get("descricao"), bruto.get("descricaoItem"), bruto.get("descricaoItemCompra")),
        "quantidade": para_numero(_primeiro(bruto.get("quantidade"), bruto.get("quantidadeItem"))),
        "unidade": _primeiro(bruto.get("unidadeMedida"), bruto.get("unidadeFornecimento"), bruto.get("unidade")),
        "valor_estimado": para_numero(_primeiro(bruto.get("valorUnitarioEstimado"), bruto.get("valorEstimado"), bruto.get("valorUnitario"))),
        "codigo_catalogo": _primeiro(bruto.get("codigoItemCatalogo"), bruto.get("codigoCatalogo"), bruto.get("codigoItem"), bruto.get("catalogoId")),
        "categoria_catalogo": _primeiro(bruto.get("nomeCatalogo"), bruto.get("categoriaItemNome"), bruto.get("categoria")),
        "objeto_normalizado": normalizar_texto_busca(_primeiro(bruto.get("descricao"), bruto.get("descricaoItem"), bruto.get("descricaoItemCompra"))),
    }


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
