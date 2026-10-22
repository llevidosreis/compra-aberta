"""
Filtros financeiros, totais de controle e recorte por porte
(passos 2, 6, 7, 10 e 11 do guia).

Ordem obrigatória:
  1. separar Câmara x Prefeitura pelo cadastro de órgãos;
  2. manter só as 7 naturezas e só pagamentos não estornados;
  3. somar valor_nota_pagamento -> TOTAL DE CONTROLE (todos os portes);
  4. só depois classificar cada pagamento em UMA categoria de porte;
  5. a soma das categorias tem de reconciliar com o total de controle.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from src.transform.cruzamento import Pendencia
from src.transform.despesas import (
    CHAVE_PAGAMENTO,
    PORTE_DEMAIS,
    PORTE_EPP,
    PORTE_ME,
    PORTE_NAO_INFORMADO,
    chave,
    sem_acentos_maiusculo,
)
from src.transform.naturezas import NATUREZAS_DESPESA

ZERO = Decimal("0")

# Ordem de exibição do guia
ORDEM_ELEMENTOS = ("39", "30", "51", "52", "32", "40", "35")

# Categorias de porte — cada pagamento pertence a exatamente uma
CAT_ME = "ME"
CAT_EPP = "EPP"
CAT_DEMAIS = "DEMAIS"
CAT_PORTE_NAO_INFORMADO = "PORTE_NAO_INFORMADO"   # cadastro com porte 00
CAT_SEM_CLASSIFICACAO = "SEM_CLASSIFICACAO"       # não encontrado, falha, porte desconhecido
CAT_ME_EXCLUIDA_MEI = "ME_EXCLUIDA_REGRA_MEI"     # só existe com regra_mei='excluir'
CAT_CPF = "FAVORECIDO_CPF"
CAT_DOC_INVALIDO = "DOCUMENTO_INVALIDO_OU_AUSENTE"

CATEGORIAS = (
    CAT_ME, CAT_EPP, CAT_DEMAIS, CAT_PORTE_NAO_INFORMADO,
    CAT_SEM_CLASSIFICACAO, CAT_ME_EXCLUIDA_MEI, CAT_CPF, CAT_DOC_INVALIDO,
)

REGRA_MEI_INCLUIR = "incluir"   # padrão do guia: porte 01 basta
REGRA_MEI_EXCLUIR = "excluir"   # "ME, excluindo MEI": exige opcao_mei = N confirmada


class ClassificacaoCadastral(Protocol):
    porte_codigo: str | None
    indicador_mei: bool | None
    situacao_classificacao: str


# ----------------------------------------------------------------------
# Passo 2: órgãos
# ----------------------------------------------------------------------

def identificar_orgaos_camara(orgaos: list[dict[str, Any]]) -> dict[str, str]:
    """Devolve {codigo_orgao: motivo} dos órgãos do Poder Legislativo.

    Usa o cadastro do próprio exercício: codigo_tipo_unidade '02' (tipo
    informado no registro da Câmara) ou nome contendo 'CAMARA'. Nunca fixa
    '01' como código universal da Câmara.
    """
    camara: dict[str, str] = {}
    for orgao in orgaos:
        nome = sem_acentos_maiusculo(orgao.get("nome_orgao"))
        tipo = orgao.get("codigo_tipo_unidade")
        motivos = []
        if tipo == "02":
            motivos.append("codigo_tipo_unidade=02")
        if "CAMARA" in nome:
            motivos.append("nome contém CÂMARA")
        if motivos:
            camara[orgao["codigo_orgao"]] = " e ".join(motivos)
    return camara


# ----------------------------------------------------------------------
# Passo 6: filtros financeiros
# ----------------------------------------------------------------------

@dataclass
class ResultadoFiltros:
    validos: list[dict[str, Any]] = field(default_factory=list)
    pendencias: list[Pendencia] = field(default_factory=list)
    # motivo -> {"quantidade", "valor"}
    excluidos: dict[str, dict[str, Any]] = field(
        default_factory=lambda: defaultdict(lambda: {"quantidade": 0, "valor": ZERO})
    )


def aplicar_filtros_financeiros(
    pagamentos: list[dict[str, Any]],
    codigo_municipio: str,
    exercicio_orcamento: str,
    orgaos_cadastrados: set[str],
    orgaos_excluidos: set[str],
) -> ResultadoFiltros:
    resultado = ResultadoFiltros()

    def excluir(motivo: str, pagamento: dict[str, Any]) -> None:
        item = resultado.excluidos[motivo]
        item["quantidade"] += 1
        item["valor"] += pagamento.get("valor_nota_pagamento") or ZERO

    for pagamento in pagamentos:
        k = chave(pagamento, CHAVE_PAGAMENTO)
        valor = pagamento.get("valor_nota_pagamento")

        # 1. município e exercício
        if (pagamento.get("codigo_municipio") != codigo_municipio
                or pagamento.get("exercicio_orcamento") != exercicio_orcamento):
            excluir("outro_municipio_ou_exercicio", pagamento)
            continue

        # 2. Prefeitura (Câmara fora) — órgão desconhecido é pendência
        orgao = pagamento.get("codigo_orgao")
        if orgao in orgaos_excluidos:
            excluir("camara", pagamento)
            continue
        if orgao not in orgaos_cadastrados:
            resultado.pendencias.append(Pendencia(
                "orgao_nao_cadastrado", k,
                f"Órgão '{orgao}' não consta no cadastro de órgãos do exercício",
                valor=valor,
            ))
            continue

        # 3. sete naturezas (elemento = 5ª e 6ª posições)
        elemento = pagamento.get("elemento_despesa")
        if elemento is None:
            resultado.pendencias.append(Pendencia(
                "elemento_despesa_fora_do_formato", k,
                f"codigo_elemento_despesa='{pagamento.get('codigo_elemento_despesa')}' "
                "não tem 8 dígitos",
                valor=valor,
            ))
            continue
        if elemento not in NATUREZAS_DESPESA:
            excluir("fora_das_sete_naturezas", pagamento)
            continue

        # 4. estorno: só 0 é válido; 1 sai; outro valor precisa ser investigado
        estado = pagamento.get("estado_de_estornado")
        if estado == "1":
            excluir("estornado", pagamento)
            continue
        if estado != "0":
            resultado.pendencias.append(Pendencia(
                "estado_estorno_desconhecido", k,
                f"estado_de_estornado='{estado}' sem significado confirmado",
                valor=valor,
            ))
            continue

        if valor is None:
            resultado.pendencias.append(Pendencia(
                "valor_nota_pagamento_ausente", k, "Pagamento sem valor",
            ))
            continue

        resultado.validos.append(pagamento)

    return resultado


# ----------------------------------------------------------------------
# Passo 7: totais de controle (todos os portes)
# ----------------------------------------------------------------------

def totais_controle(validos: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    totais = {e: {"quantidade": 0, "valor": ZERO} for e in ORDEM_ELEMENTOS}
    for pagamento in validos:
        item = totais[pagamento["elemento_despesa"]]
        item["quantidade"] += 1
        item["valor"] += pagamento["valor_nota_pagamento"]
    return totais


# ----------------------------------------------------------------------
# Passo 10: categoria de porte de cada pagamento
# ----------------------------------------------------------------------

def categoria_porte(
    pagamento: dict[str, Any],
    cadastro: dict[str, ClassificacaoCadastral],
    regra_mei: str = REGRA_MEI_INCLUIR,
) -> str:
    tipo = pagamento.get("tipo_documento")
    if tipo == "CPF":
        return CAT_CPF
    if tipo != "CNPJ":
        return CAT_DOC_INVALIDO

    classificacao = cadastro.get(pagamento["documento"])
    if classificacao is None or classificacao.porte_codigo is None:
        return CAT_SEM_CLASSIFICACAO

    porte = classificacao.porte_codigo
    if porte == PORTE_ME:
        if regra_mei == REGRA_MEI_EXCLUIR and classificacao.indicador_mei is not False:
            # MEI confirmado OU indicador ausente: não comprova que não é MEI
            return CAT_ME_EXCLUIDA_MEI
        return CAT_ME
    if porte == PORTE_EPP:
        return CAT_EPP
    if porte == PORTE_DEMAIS:
        return CAT_DEMAIS
    if porte == PORTE_NAO_INFORMADO:
        return CAT_PORTE_NAO_INFORMADO
    return CAT_SEM_CLASSIFICACAO


# ----------------------------------------------------------------------
# Passo 11: soma por categoria e por fornecedor + reconciliação
# ----------------------------------------------------------------------

@dataclass
class Apuracao:
    controle: dict[str, dict[str, Any]]
    por_categoria: dict[str, dict[str, dict[str, Any]]]
    fornecedores: list[dict[str, Any]]
    reconciliacao: dict[str, dict[str, Any]]

    @property
    def reconciliado(self) -> bool:
        return all(item["ok"] for item in self.reconciliacao.values())


def apurar_por_porte(
    validos: list[dict[str, Any]],
    cadastro: dict[str, ClassificacaoCadastral],
    regra_mei: str = REGRA_MEI_INCLUIR,
) -> Apuracao:
    controle = totais_controle(validos)
    por_categoria = {
        e: {c: {"quantidade": 0, "valor": ZERO} for c in CATEGORIAS}
        for e in ORDEM_ELEMENTOS
    }
    fornecedores: dict[tuple, dict[str, Any]] = {}

    for pagamento in validos:
        elemento = pagamento["elemento_despesa"]
        categoria = categoria_porte(pagamento, cadastro, regra_mei)
        valor = pagamento["valor_nota_pagamento"]

        item = por_categoria[elemento][categoria]
        item["quantidade"] += 1
        item["valor"] += valor

        # agrupamento por município + exercício + elemento + documento
        documento = pagamento.get("documento") or "(sem documento)"
        k = (pagamento["codigo_municipio"], pagamento["exercicio_orcamento"],
             elemento, documento)
        fornecedor = fornecedores.get(k)
        if fornecedor is None:
            classificacao = cadastro.get(pagamento.get("documento") or "")
            fornecedor = fornecedores[k] = {
                "codigo_municipio": pagamento["codigo_municipio"],
                "exercicio_orcamento": pagamento["exercicio_orcamento"],
                "elemento_despesa": elemento,
                "documento": documento,
                "tipo_documento": pagamento.get("tipo_documento"),
                "nome_negociante": pagamento.get("nome_negociante"),
                "categoria_porte": categoria,
                "porte_codigo": getattr(classificacao, "porte_codigo", None),
                "situacao_classificacao": getattr(
                    classificacao, "situacao_classificacao", None
                ),
                "quantidade_pagamentos": 0,
                "valor_pago": ZERO,
            }
        fornecedor["quantidade_pagamentos"] += 1
        fornecedor["valor_pago"] += valor

    reconciliacao = {}
    for elemento in ORDEM_ELEMENTOS:
        soma_categorias = sum(
            (v["valor"] for v in por_categoria[elemento].values()), ZERO
        )
        qtd_categorias = sum(v["quantidade"] for v in por_categoria[elemento].values())
        reconciliacao[elemento] = {
            "total_controle": controle[elemento]["valor"],
            "soma_categorias": soma_categorias,
            "diferenca": controle[elemento]["valor"] - soma_categorias,
            "ok": (soma_categorias == controle[elemento]["valor"]
                   and qtd_categorias == controle[elemento]["quantidade"]),
        }

    lista = sorted(
        fornecedores.values(),
        key=lambda f: (ORDEM_ELEMENTOS.index(f["elemento_despesa"]), -f["valor_pago"]),
    )
    return Apuracao(controle, por_categoria, lista, reconciliacao)


def cnpjs_para_consulta(validos: list[dict[str, Any]]) -> list[str]:
    """Lista de CNPJs ÚNICOS (passo 8) — uma consulta por CNPJ, não por pagamento."""
    return sorted({
        p["documento"] for p in validos if p.get("tipo_documento") == "CNPJ"
    })