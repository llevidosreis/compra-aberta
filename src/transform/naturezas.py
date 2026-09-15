"""Catálogo e classificação das naturezas de despesa usadas pelo projeto."""

NATUREZAS_DESPESA: dict[str, str] = {
    "39": "OUTROS SERVIÇOS DE TERCEIROS – PESSOA JURÍDICA",
    "51": "OBRAS E INSTALAÇÕES",
    "30": "MATERIAL DE CONSUMO",
    "52": "EQUIPAMENTOS E MATERIAL PERMANENTE",
    "32": "MATERIAL, BEM OU SERVIÇO PARA DISTRIBUIÇÃO GRATUITA",
    "35": "SERVIÇOS DE CONSULTORIA",
    "40": "SERVIÇOS DE TECNOLOGIA DA INFORMAÇÃO E COMUNICAÇÃO – PESSOA JURÍDICA",
}


def codigo_natureza(codigo_elemento_despesa: str | None) -> str | None:
    """Obtém o elemento básico de dois dígitos do código orçamentário."""
    if not codigo_elemento_despesa:
        return None
    codigo = "".join(ch for ch in str(codigo_elemento_despesa) if ch.isdigit())
    if len(codigo) < 2:
        return None
    elemento = codigo[-4:-2] if len(codigo) >= 4 else codigo[-2:]
    return elemento if elemento in NATUREZAS_DESPESA else None
