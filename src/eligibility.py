"""Regras de classificação e elegibilidade de empresas para o Compra Livre."""

from datetime import datetime, timedelta, timezone

PORTES_ME_ACEITOS = ("me", "microempresa", "microempresa (me)")
PORTES_EPP_ACEITOS = ("epp", "empresa de pequeno porte", "empresa de pequeno porte (epp)")
PORTES_DEMAIS_ACEITOS = ("demais",)


def classificar_porte(porte: str | None, opcao_mei: str | None) -> str:
    """Classifica somente valores explícitos da base Receita publicada pelo OpenCNPJ."""
    valor = " ".join((porte or "").strip().casefold().split())
    mei = (opcao_mei or "").strip().upper()

    if valor in PORTES_ME_ACEITOS:
        if mei == "S":
            return "MEI"
        if mei == "N":
            return "ME"
        return "DESCONHECIDO"
    if valor in PORTES_EPP_ACEITOS:
        return "EPP"
    if valor in PORTES_DEMAIS_ACEITOS:
        return "OUTRO"
    return "DESCONHECIDO"


def cnpj_numerico_valido(cnpj: str) -> bool:
    """Valida formato e dígitos verificadores do CNPJ numérico atual."""
    if len(cnpj) != 14 or not cnpj.isdigit() or len(set(cnpj)) == 1:
        return False

    numeros = [int(caractere) for caractere in cnpj]
    for tamanho, pesos in (
        (12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
        (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
    ):
        resto = sum(
            numero * peso
            for numero, peso in zip(numeros[:tamanho], pesos)
        ) % 11
        digito = 0 if resto < 2 else 11 - resto
        if numeros[tamanho] != digito:
            return False
    return True


def empresa_me_confirmada(
    *,
    cnpj: str,
    porte: str | None,
    opcao_mei: str | None,
    encontrado: bool | None,
    enriquecido: bool,
    consultado_em: datetime | None,
    fonte_atualizada_em: datetime | None,
    dias_validade_cache: int,
    agora: datetime | None = None,
) -> bool:
    """Aplica a regra fail-closed, incluindo evidência e atualidade do cache."""
    agora = agora or datetime.now(timezone.utc)
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=timezone.utc)

    if not cnpj_numerico_valido(cnpj):
        return False
    if not enriquecido or encontrado is not True:
        return False
    if classificar_porte(porte, opcao_mei) != "ME":
        return False
    if consultado_em is None or fonte_atualizada_em is None:
        return False

    consulta = _como_utc(consultado_em)
    atualizacao_fonte = _como_utc(fonte_atualizada_em)
    return (
        agora - timedelta(days=max(0, dias_validade_cache))
        <= consulta
        <= agora
        and atualizacao_fonte <= agora
    )


def sql_empresa_fonte_confiavel(alias: str, dias_validade_cache: int) -> str:
    """Predicado para classificar apenas empresas encontradas com cache atual."""
    dias = max(0, int(dias_validade_cache))
    return f"""
        LENGTH({alias}.cnpj) = 14
        AND {alias}.cnpj ~ '^[0-9]{{14}}$'
        AND {alias}.enriquecido IS TRUE
        AND {alias}.encontrado_opencnpj IS TRUE
        AND {alias}.consultado_em >= now() - INTERVAL '{dias} days'
        AND {alias}.consultado_em <= now()
        AND {alias}.fonte_atualizada_em IS NOT NULL
        AND {alias}.fonte_atualizada_em <= now()
    """


def sql_empresa_me_elegivel(alias: str, dias_validade_cache: int) -> str:
    """Gera a mesma regra de elegibilidade para as consultas SQL da API."""
    portes = ", ".join(f"'{valor}'" for valor in PORTES_ME_ACEITOS)
    return f"""
        {sql_empresa_fonte_confiavel(alias, dias_validade_cache)}
        AND UPPER(TRIM(COALESCE({alias}.opcao_mei, ''))) = 'N'
        AND LOWER(TRIM(COALESCE({alias}.porte_empresa, ''))) IN ({portes})
    """


def _como_utc(valor: datetime) -> datetime:
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)
