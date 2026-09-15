"""
Validações de qualidade aplicadas antes de qualquer registro entrar
nas tabelas finais.

A filosofia aqui é: registros inválidos são separados e reportados,
não travam o pipeline inteiro. Uma licitação com problema não pode
impedir a carga de todas as outras.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResultadoValidacao:
    validos: list[dict[str, Any]] = field(default_factory=list)
    invalidos: list[tuple[dict[str, Any], str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.validos) + len(self.invalidos)


def validar_licitantes(registros: list[dict[str, Any]]) -> ResultadoValidacao:
    resultado = ResultadoValidacao()
    for registro in registros:
        motivo = _motivo_invalido_comum(registro)
        if motivo:
            resultado.invalidos.append((registro, motivo))
        else:
            resultado.validos.append(registro)
    return resultado


def validar_itens(registros: list[dict[str, Any]]) -> ResultadoValidacao:
    resultado = ResultadoValidacao()
    for registro in registros:
        motivo = _motivo_invalido_comum(registro)
        if not motivo and registro.get("numero_sequencial_item_licitacao") is None:
            motivo = "numero_sequencial_item_licitacao ausente"
        if not motivo and registro.get("valor_vencedor_item_licitacao") is not None and registro["valor_vencedor_item_licitacao"] < 0:
            motivo = "valor_vencedor_item_licitacao negativo"

        if motivo:
            resultado.invalidos.append((registro, motivo))
        else:
            resultado.validos.append(registro)
    return resultado


def _motivo_invalido_comum(registro: dict[str, Any]) -> str | None:
    if not registro.get("codigo_municipio"):
        return "codigo_municipio ausente"
    if not registro.get("numero_licitacao"):
        return "numero_licitacao ausente"
    if not registro.get("numero_documento_negociante"):
        return "numero_documento_negociante (CNPJ) ausente"
    if len(registro["numero_documento_negociante"]) not in (11, 14):
        return "numero_documento_negociante com tamanho inválido (não é CPF nem CNPJ)"
    return None
