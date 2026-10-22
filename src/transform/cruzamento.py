"""
Cruzamento de pagamentos com empenhos (passos 5.2 e 5.3 do guia).

  - empenhos: um registro por chave completa de 6 campos;
  - pagamentos: um registro por chave do empenho + sub-empenho + nota;
  - cada pagamento precisa encontrar EXATAMENTE um empenho;
  - data_referencia_doc NÃO entra na igualdade;
  - repetição idêntica (mesmo conteúdo) é descartada; repetição com
    conteúdo conflitante NÃO é descartada arbitrariamente: vira pendência
    e o registro fica fora da soma até ser resolvido.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.transform.despesas import CHAVE_EMPENHO, CHAVE_PAGAMENTO, chave

# Campos usados só para rastrear a origem; não contam como conflito
_CAMPOS_RASTREIO = {"_referencia_consultada"}


@dataclass
class Pendencia:
    tipo: str
    chave: tuple
    detalhe: str
    valor: Decimal | None = None
    documento: str | None = None


@dataclass
class ResultadoIndexacao:
    unicos: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    conflitantes: dict[tuple, list[dict[str, Any]]] = field(default_factory=dict)
    duplicados_identicos: int = 0
    chave_incompleta: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ResultadoCruzamento:
    pagamentos: list[dict[str, Any]] = field(default_factory=list)
    pendencias: list[Pendencia] = field(default_factory=list)
    total_empenhos: int = 0
    total_pagamentos: int = 0
    empenhos_duplicados_identicos: int = 0
    pagamentos_duplicados_identicos: int = 0
    # registros únicos (sem conflito) para persistência
    empenhos_unicos: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    pagamentos_unicos: dict[tuple, dict[str, Any]] = field(default_factory=dict)


def _conteudo(registro: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in registro.items() if k not in _CAMPOS_RASTREIO}


def indexar_por_chave(
    registros: list[dict[str, Any]], campos: tuple[str, ...]
) -> ResultadoIndexacao:
    resultado = ResultadoIndexacao()
    for registro in registros:
        k = chave(registro, campos)
        if any(parte is None for parte in k):
            resultado.chave_incompleta.append(registro)
            continue
        if k in resultado.conflitantes:
            resultado.conflitantes[k].append(registro)
            continue
        existente = resultado.unicos.get(k)
        if existente is None:
            resultado.unicos[k] = registro
        elif _conteudo(existente) == _conteudo(registro):
            resultado.duplicados_identicos += 1
        else:
            # conflito: tira dos únicos e guarda as versões para investigação
            resultado.conflitantes[k] = [resultado.unicos.pop(k), registro]
    return resultado


def cruzar_pagamentos_empenhos(
    empenhos: list[dict[str, Any]], pagamentos: list[dict[str, Any]]
) -> ResultadoCruzamento:
    resultado = ResultadoCruzamento()
    idx_emp = indexar_por_chave(empenhos, CHAVE_EMPENHO)
    idx_pag = indexar_por_chave(pagamentos, CHAVE_PAGAMENTO)

    resultado.total_empenhos = len(idx_emp.unicos) + len(idx_emp.conflitantes)
    resultado.total_pagamentos = len(idx_pag.unicos) + len(idx_pag.conflitantes)
    resultado.empenhos_duplicados_identicos = idx_emp.duplicados_identicos
    resultado.pagamentos_duplicados_identicos = idx_pag.duplicados_identicos
    resultado.empenhos_unicos = idx_emp.unicos
    resultado.pagamentos_unicos = idx_pag.unicos

    for registro in idx_emp.chave_incompleta:
        resultado.pendencias.append(Pendencia(
            "empenho_chave_incompleta", chave(registro, CHAVE_EMPENHO),
            "Empenho sem algum dos seis campos da chave",
        ))
    for k, versoes in idx_emp.conflitantes.items():
        resultado.pendencias.append(Pendencia(
            "empenho_duplicado_conflitante", k,
            f"{len(versoes)} versões diferentes para a mesma chave de empenho",
        ))
    for registro in idx_pag.chave_incompleta:
        resultado.pendencias.append(Pendencia(
            "pagamento_chave_incompleta", chave(registro, CHAVE_PAGAMENTO),
            "Pagamento sem algum dos campos da chave",
            valor=registro.get("valor_nota_pagamento"),
        ))
    for k, versoes in idx_pag.conflitantes.items():
        resultado.pendencias.append(Pendencia(
            "pagamento_duplicado_conflitante", k,
            f"{len(versoes)} versões diferentes para a mesma nota de pagamento",
            valor=versoes[0].get("valor_nota_pagamento"),
        ))

    for k_pag, pagamento in idx_pag.unicos.items():
        k_emp = k_pag[: len(CHAVE_EMPENHO)]
        if k_emp in idx_emp.conflitantes:
            resultado.pendencias.append(Pendencia(
                "pagamento_com_empenho_ambiguo", k_pag,
                "O empenho correspondente tem versões conflitantes",
                valor=pagamento.get("valor_nota_pagamento"),
            ))
            continue
        empenho = idx_emp.unicos.get(k_emp)
        if empenho is None:
            resultado.pendencias.append(Pendencia(
                "pagamento_sem_empenho", k_pag,
                "Nenhum empenho com a mesma chave completa na coleta",
                valor=pagamento.get("valor_nota_pagamento"),
            ))
            continue

        # o pagamento recebe, via empenho, o credor e a natureza da despesa;
        # o valor somado continua sendo o da NOTA de pagamento
        cruzado = dict(pagamento)
        cruzado.update({
            "codigo_elemento_despesa": empenho["codigo_elemento_despesa"],
            "elemento_despesa": empenho["elemento_despesa"],
            "documento_original": empenho["documento_original"],
            "documento": empenho["documento"],
            "tipo_documento": empenho["tipo_documento"],
            "codigo_tipo_negociante": empenho["codigo_tipo_negociante"],
            "nome_negociante": empenho["nome_negociante"],
            "data_referencia_empenho": empenho["data_referencia_doc"],
        })
        resultado.pagamentos.append(cruzado)

    return resultado