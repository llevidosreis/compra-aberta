"""Regras determinísticas substituíveis por uma estratégia de embeddings."""
from datetime import datetime, timezone
from typing import Any, Protocol

from src.matching.synonyms import CNAE_PALAVRAS_CHAVE, SINONIMOS
from src.transform.normalize import normalizar_texto_busca, para_datetime


class Scorer(Protocol):
    def pontuar(
        self, contratacao: dict[str, Any], itens: list[dict[str, Any]], perfil: dict[str, Any], empresa: dict[str, Any]
    ) -> tuple[float, dict[str, Any]]: ...


class ScorerLexical:
    """Calcula cobertura de palavras, apoio CNAE e bônus de categoria."""

    def pontuar(self, contratacao, itens, perfil, empresa):
        texto_edital = " ".join(
            [str(contratacao.get("objeto_normalizado") or contratacao.get("objeto_compra") or "")]
            + [str(item.get("objeto_normalizado") or item.get("descricao") or "") for item in itens]
        )
        termos_edital = _termos_expandidos(texto_edital)
        palavras_perfil = perfil.get("produtos_palavras_chave") or []
        termos_perfil = _termos_expandidos(" ".join(str(item) for item in palavras_perfil))
        termos_casados = sorted(termos_edital & termos_perfil)
        cobertura = len(termos_casados) / len(termos_perfil) if termos_perfil else 0.0

        cnaes = _cnaes(empresa)
        termos_cnae = {
            termo
            for cnae in cnaes
            for prefixo, termos in CNAE_PALAVRAS_CHAVE.items()
            if cnae.startswith(prefixo)
            for termo in _termos_expandidos(" ".join(termos))
        }
        cnaes_casados = sorted(termos_edital & termos_cnae)
        bonus_cnae = 0.15 if cnaes_casados else 0.0

        palavras_catalogo = " ".join(
            str(item.get("categoria_catalogo") or "") for item in itens
        )
        termos_catalogo = _termos_expandidos(palavras_catalogo)
        catalogo_casado = sorted(termos_catalogo & termos_perfil)
        bonus_catalogo = 0.05 if catalogo_casado else 0.0

        score = min(1.0, cobertura * 0.8 + bonus_cnae + bonus_catalogo)
        return round(score, 4), {
            "termos": termos_casados,
            "cnaes": cnaes_casados,
            "catalogo": catalogo_casado,
        }


def avaliar_match(
    contratacao: dict[str, Any],
    itens: list[dict[str, Any]],
    perfil: dict[str, Any],
    empresa: dict[str, Any],
    limiar: float = 0.35,
    agora: datetime | None = None,
    scorer: Scorer | None = None,
) -> dict[str, Any] | None:
    """Retorna o match elegível ou None quando um filtro eliminatório falha."""
    agora = agora or datetime.now(timezone.utc)
    encerramento = para_datetime(contratacao.get("data_encerramento_proposta"))
    if encerramento is None or encerramento <= agora:
        return None

    ufs = {str(valor).strip().upper() for valor in perfil.get("ufs_atuacao") or []}
    if ufs and str(contratacao.get("uf") or "").upper() not in ufs:
        return None

    municipios = {
        normalizar_texto_busca(valor)
        for valor in perfil.get("municipios_atuacao") or []
    }
    municipio_edital = {
        normalizar_texto_busca(contratacao.get("codigo_ibge_municipio")),
        normalizar_texto_busca(contratacao.get("nome_municipio")),
    } - {""}
    if municipios and not municipios.intersection(municipio_edital):
        return None

    if contratacao.get("exclusivo_me_epp"):
        porte = normalizar_texto_busca(empresa.get("porte_empresa"))
        porte_compativel = porte in {"me", "microempresa", "microempresa me", "epp"}
        porte_compativel = porte_compativel or "empresa pequeno porte" in porte
        if not porte_compativel:
            return None

    score, motivos = (scorer or ScorerLexical()).pontuar(contratacao, itens, perfil, empresa)
    if score < limiar:
        return None
    return {"cnpj": perfil.get("cnpj"), "score": score, "motivos": motivos}


def _termos_expandidos(texto: str) -> set[str]:
    normalizado = normalizar_texto_busca(texto)
    termos = set(normalizado.split())
    for origem, sinonimos in SINONIMOS.items():
        origem_normalizada = normalizar_texto_busca(origem)
        if origem_normalizada and origem_normalizada in normalizado:
            for sinonimo in sinonimos:
                termos.update(normalizar_texto_busca(sinonimo).split())
        if any(normalizar_texto_busca(sinonimo) in normalizado for sinonimo in sinonimos):
            termos.update(origem_normalizada.split())
    return termos


def _cnaes(empresa: dict[str, Any]) -> list[str]:
    valores = [empresa.get("cnae_principal")]
    secundarios = empresa.get("cnaes_secundarios") or []
    if isinstance(secundarios, str):
        try:
            import json
            secundarios = json.loads(secundarios)
        except (TypeError, ValueError):
            secundarios = [secundarios]
    valores.extend(secundarios if isinstance(secundarios, list) else [])
    resultado = []
    for valor in valores:
        if isinstance(valor, dict):
            valor = valor.get("codigo") or valor.get("codigo_cnae")
        digitos = "".join(caractere for caractere in str(valor or "") if caractere.isdigit())
        if digitos:
            resultado.append(digitos)
    return resultado