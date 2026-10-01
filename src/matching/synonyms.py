"""Sinônimos configuráveis para compensar variações frequentes de catálogo."""

SINONIMOS: dict[str, tuple[str, ...]] = {
    "papel higienico": ("papel toalete", "papel sanitario", "papel tissue", "higiene limpeza"),
    "papel toalete": ("papel higienico", "papel sanitario", "higiene limpeza"),
    "papel sanitario": ("papel higienico", "papel toalete", "higiene limpeza"),
    "material limpeza": ("produtos limpeza", "higiene limpeza"),
    "produtos limpeza": ("material limpeza", "higiene limpeza"),
}

CNAE_PALAVRAS_CHAVE: dict[str, tuple[str, ...]] = {
    "4646": ("higiene", "limpeza", "papel higienico"),
    "4649": ("limpeza", "material limpeza", "papel higienico"),
    "812": ("limpeza", "higienizacao"),
    "620": ("tecnologia", "software", "informatica"),
}