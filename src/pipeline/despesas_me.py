"""
Apuração dos pagamentos da Prefeitura nas sete naturezas e recorte de
Microempresas — implementação do guia passo a passo:

  1  recorte registrado (município, exercício, períodos, regra MEI, fonte)
  2  órgãos: identifica e exclui a Câmara pelo cadastro do exercício
  3  empenhos mês a mês, com paginação
  4  pagamentos mês a mês (inclusive referências do ano seguinte)
  5  normalização e cruzamento pela chave completa
  6  filtros: Prefeitura, 7 elementos, estado_de_estornado = 0
  7  TOTAL DE CONTROLE (todos os portes) + conferência com o portal
  8  CNPJs únicos dos credores
  9  porte via OpenCNPJ (com cache)
  10 categoria de porte por pagamento (ME = porte 01)
  11 soma por fornecedor/natureza e reconciliação com o total de controle

Saída: relatório JSON em saidas/ e, se houver banco, tabelas
coleta_pagamentos, resumo_pagamento_categoria, resumo_pagamento_fornecedor,
pendencia_coleta_pagamentos, cadastro_porte_fornecedor.
"""
import json
import logging
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.clients.tce_despesas_client import TCEDespesasClient
from src.config import Config
from src.enrich.porte_fornecedores import (
    FONTE_OPENCNPJ,
    REFERENCIA_NAO_INFORMADA,
    ClassificacaoPorte,
    OpenCNPJPorteClient,
    classificar_cnpjs,
)
from src.quality.conferencia_despesas import comparar, referencia_para
from src.transform.apuracao_pagamentos import (
    CAT_DOC_INVALIDO,
    CAT_SEM_CLASSIFICACAO,
    ORDEM_ELEMENTOS,
    REGRA_MEI_EXCLUIR,
    REGRA_MEI_INCLUIR,
    Apuracao,
    aplicar_filtros_financeiros,
    apurar_por_porte,
    cnpjs_para_consulta,
    identificar_orgaos_camara,
)
from src.transform.cruzamento import Pendencia, cruzar_pagamentos_empenhos
from src.transform.despesas import (
    normalizar_empenho,
    normalizar_orgao,
    normalizar_pagamento,
)
from src.transform.naturezas import NATUREZAS_DESPESA

logger = logging.getLogger(__name__)
CENTAVO = Decimal("0.01")


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------

def exercicio_de_ano(ano: int) -> str:
    """No SIM o orçamento de 2025 é 202500."""
    return f"{ano:04d}00"


def referencia_mes_anterior(hoje: date | None = None) -> str:
    hoje = hoje or date.today()
    ano, mes = (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)
    return f"{ano:04d}{mes:02d}"


def brl(valor: Decimal) -> str:
    texto = f"{valor.quantize(CENTAVO):,.2f}"
    return "R$ " + texto.replace(",", "_").replace(".", ",").replace("_", ".")


def _json_padrao(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return str(valor.quantize(CENTAVO))
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if isinstance(valor, tuple):
        return list(valor)
    if isinstance(valor, set):
        return sorted(valor)
    raise TypeError(f"Tipo não serializável: {type(valor)}")


def _carregar_cache_arquivo(caminho: Path) -> dict[str, ClassificacaoPorte]:
    if not caminho.exists():
        return {}
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return {item["cnpj_completo"]: ClassificacaoPorte(**item) for item in dados}


def _salvar_cache_arquivo(caminho: Path, cache: dict[str, ClassificacaoPorte]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps([c.para_dict() for c in cache.values()], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _pendencias_de_classificacao(apuracao: Apuracao) -> list[Pendencia]:
    """Fornecedores sem porte ou com documento inválido viram pendência rastreável."""
    pendencias = []
    for f in apuracao.fornecedores:
        if f["categoria_porte"] == CAT_SEM_CLASSIFICACAO:
            pendencias.append(Pendencia(
                "fornecedor_sem_classificacao",
                (f["elemento_despesa"], f["documento"]),
                f"situacao={f['situacao_classificacao']}; {f['nome_negociante']}",
                valor=f["valor_pago"], documento=f["documento"],
            ))
        elif f["categoria_porte"] == CAT_DOC_INVALIDO:
            pendencias.append(Pendencia(
                "documento_credor_invalido",
                (f["elemento_despesa"], f["documento"]),
                f"tipo={f['tipo_documento']}; {f['nome_negociante']}",
                valor=f["valor_pago"], documento=f["documento"],
            ))
    return pendencias


def _registrar_tabela(apuracao: Apuracao) -> None:
    logger.info("%-4s %-45s %20s %20s %8s", "Elem", "Natureza", "Total controle", "ME", "Reconc.")
    total, total_me = Decimal("0"), Decimal("0")
    for elemento in ORDEM_ELEMENTOS:
        controle = apuracao.controle[elemento]["valor"]
        me = apuracao.por_categoria[elemento]["ME"]["valor"]
        total += controle
        total_me += me
        logger.info(
            "%-4s %-45s %20s %20s %8s",
            elemento, NATUREZAS_DESPESA[elemento][:45], brl(controle), brl(me),
            "ok" if apuracao.reconciliacao[elemento]["ok"] else "ERRO",
        )
    logger.info("%-50s %20s %20s", "TOTAL", brl(total), brl(total_me))


# ----------------------------------------------------------------------
# Execução
# ----------------------------------------------------------------------

def executar(
    config: Config,
    codigo_municipio: str,
    ano: int,
    referencia_empenhos_inicio: str | None = None,
    referencia_empenhos_fim: str | None = None,
    referencia_pagamentos_inicio: str | None = None,
    referencia_pagamentos_fim: str | None = None,
    regra_mei: str = REGRA_MEI_INCLUIR,
    referencia_base_cadastral: str = REFERENCIA_NAO_INFORMADA,
    orgaos_excluir_adicionais: list[str] | None = None,
    usar_banco: bool = True,
    pasta_saida: str = "saidas",
    tce_client: TCEDespesasClient | None = None,
    opencnpj_client: OpenCNPJPorteClient | None = None,
) -> dict[str, Any]:
    if regra_mei not in (REGRA_MEI_INCLUIR, REGRA_MEI_EXCLUIR):
        raise ValueError("regra_mei deve ser 'incluir' ou 'excluir'")

    # ---------------- Passo 1: recorte ----------------
    codigo_municipio = codigo_municipio.strip().zfill(3)
    exercicio = exercicio_de_ano(ano)
    parametros = {
        "codigo_municipio": codigo_municipio,
        "ano": ano,
        "exercicio_orcamento": exercicio,
        "administracao": "Prefeitura (Câmara excluída pelo cadastro de órgãos)",
        "medida_financeira": "valor_nota_pagamento das notas não estornadas",
        "elementos_despesa": list(ORDEM_ELEMENTOS),
        "referencia_empenhos_inicio": referencia_empenhos_inicio or f"{ano}01",
        "referencia_empenhos_fim": referencia_empenhos_fim or f"{ano}12",
        "referencia_pagamentos_inicio": referencia_pagamentos_inicio or f"{ano}01",
        "referencia_pagamentos_fim": referencia_pagamentos_fim or referencia_mes_anterior(),
        "recorte_cadastral": "porte 01 (Microempresa)",
        "regra_mei": regra_mei,
        "fonte_cadastral": FONTE_OPENCNPJ,
        "referencia_base_cadastral": referencia_base_cadastral,
        "executado_em": datetime.now().isoformat(timespec="seconds"),
    }
    logger.info("Recorte: %s", json.dumps(parametros, ensure_ascii=False))

    tce = tce_client or TCEDespesasClient(config)
    opencnpj = opencnpj_client or OpenCNPJPorteClient(
        config, referencia_base=referencia_base_cadastral
    )

    conexao = None
    coleta_id = None
    if usar_banco:
        from src.db import conexao  # import tardio: modo sem banco não exige psycopg2
        from src.load import load_despesas
        with conexao(config) as conn:
            coleta_id = load_despesas.iniciar_coleta(conn, parametros)

    try:
        # ---------------- Passo 2: órgãos ----------------
        orgaos = [normalizar_orgao(o) for o in tce.listar_orgaos(codigo_municipio, exercicio)]
        camara = identificar_orgaos_camara(orgaos)
        for codigo in orgaos_excluir_adicionais or []:
            camara.setdefault(codigo, "informado manualmente (--excluir-orgao)")
        if not camara:
            logger.warning(
                "Nenhum órgão da Câmara identificado no cadastro; "
                "confira antes de comparar com a aba Prefeitura do portal."
            )
        for orgao in orgaos:
            logger.info(
                "Órgão %s %-55s tipo=%s %s",
                orgao["codigo_orgao"], orgao["nome_orgao"], orgao["codigo_tipo_unidade"],
                f"-> EXCLUÍDO ({camara[orgao['codigo_orgao']]})"
                if orgao["codigo_orgao"] in camara else "",
            )

        # ---------------- Passos 3 e 4: coleta ----------------
        empenhos = [normalizar_empenho(e) for e in tce.listar_empenhos(
            codigo_municipio, exercicio,
            parametros["referencia_empenhos_inicio"], parametros["referencia_empenhos_fim"],
        )]
        pagamentos = [normalizar_pagamento(p) for p in tce.listar_pagamentos(
            codigo_municipio, exercicio,
            parametros["referencia_pagamentos_inicio"], parametros["referencia_pagamentos_fim"],
        )]

        # ---------------- Passo 5: cruzamento ----------------
        cruzamento = cruzar_pagamentos_empenhos(empenhos, pagamentos)
        logger.info(
            "Cruzamento: %d empenhos únicos, %d pagamentos únicos, %d cruzados, "
            "duplicados idênticos descartados: %d empenhos / %d pagamentos",
            cruzamento.total_empenhos, cruzamento.total_pagamentos,
            len(cruzamento.pagamentos), cruzamento.empenhos_duplicados_identicos,
            cruzamento.pagamentos_duplicados_identicos,
        )

        # ---------------- Passo 6: filtros ----------------
        filtros = aplicar_filtros_financeiros(
            cruzamento.pagamentos, codigo_municipio, exercicio,
            orgaos_cadastrados={o["codigo_orgao"] for o in orgaos},
            orgaos_excluidos=set(camara),
        )
        for motivo, item in filtros.excluidos.items():
            logger.info("Excluídos (%s): %d pagamentos, %s",
                        motivo, item["quantidade"], brl(item["valor"]))

        # total por órgão x natureza (diagnóstico de divergência com o portal)
        nomes_orgaos = {o["codigo_orgao"]: o for o in orgaos}
        totais_por_orgao: dict[str, dict[str, Any]] = {}
        for pagamento in filtros.validos:
            codigo = pagamento["codigo_orgao"]
            item = totais_por_orgao.setdefault(codigo, {
                "nome_orgao": nomes_orgaos.get(codigo, {}).get("nome_orgao"),
                "codigo_tipo_unidade": nomes_orgaos.get(codigo, {}).get("codigo_tipo_unidade"),
                "por_elemento": {e: Decimal("0") for e in ORDEM_ELEMENTOS},
                "total": Decimal("0"),
            })
            item["por_elemento"][pagamento["elemento_despesa"]] += pagamento["valor_nota_pagamento"]
            item["total"] += pagamento["valor_nota_pagamento"]

        # agregado para diagnóstico: órgão x referência do pagamento x natureza
        # x estornado. Inclui estornados (estado 1) para testar se o portal os
        # conta; Câmara e naturezas fora do recorte ficam de fora.
        agregado: dict[tuple, dict[str, Any]] = {}
        for pagamento in cruzamento.pagamentos:
            elemento = pagamento.get("elemento_despesa")
            estado = pagamento.get("estado_de_estornado")
            valor = pagamento.get("valor_nota_pagamento")
            if (elemento not in NATUREZAS_DESPESA or estado not in ("0", "1")
                    or valor is None or pagamento.get("codigo_orgao") in camara
                    or pagamento.get("exercicio_orcamento") != exercicio):
                continue
            k = (pagamento["codigo_orgao"], pagamento.get("data_referencia_doc") or "",
                 elemento, estado == "1")
            item = agregado.setdefault(k, {
                "orgao": k[0], "referencia": k[1], "elemento": k[2], "estornado": k[3],
                "quantidade": 0, "valor": Decimal("0"),
            })
            item["quantidade"] += 1
            item["valor"] += valor
        diagnostico_agregado = sorted(
            agregado.values(), key=lambda i: (i["orgao"], i["referencia"], i["elemento"])
        )

        # ---------------- Passos 8 e 9: porte ----------------
        cnpjs = cnpjs_para_consulta(filtros.validos)
        caminho_cache = Path(pasta_saida) / "cache_porte_opencnpj.json"
        if usar_banco:
            with conexao(config) as conn:
                cache = load_despesas.carregar_cache_porte(
                    conn, FONTE_OPENCNPJ, referencia_base_cadastral
                )
        else:
            cache = _carregar_cache_arquivo(caminho_cache)

        classificacoes, consultados = classificar_cnpjs(
            cnpjs, opencnpj, cache,
            validade_dias=config.opencnpj_dias_validade_cache,
            max_workers=min(config.opencnpj_max_workers, 4),
        )
        situacoes = Counter(c.situacao_classificacao for c in classificacoes.values())
        logger.info("Classificação cadastral: %s", dict(situacoes))

        # ---------------- Passos 7, 10 e 11: apuração ----------------
        apuracao = apurar_por_porte(filtros.validos, classificacoes, regra_mei)
        _registrar_tabela(apuracao)
        if not apuracao.reconciliado:
            logger.error("Categorias de porte NÃO reconciliam com o total de controle.")

        # meses em que a API não estabilizou depois das releituras
        relatorio_coleta = getattr(tce, "relatorio_coleta", [])
        pendencias_coleta = [
            Pendencia(
                "coleta_possivelmente_incompleta",
                (item["rota"], item["referencia"]),
                f"{item['registros_unicos']} únicos de {item['registros_por_passada']} "
                f"recebidos por passada após {item['passadas']} passadas",
            )
            for item in relatorio_coleta if not item["completo"]
        ]

        pendencias = (
            pendencias_coleta + cruzamento.pendencias + filtros.pendencias
            + _pendencias_de_classificacao(apuracao)
        )
        for tipo, quantidade in Counter(p.tipo for p in pendencias).items():
            logger.warning("Pendência %s: %d", tipo, quantidade)

        conferencia = None
        referencia = referencia_para(codigo_municipio, exercicio)
        if referencia:
            conferencia = comparar(
                referencia, apuracao.controle, apuracao.fornecedores,
                parametros["referencia_pagamentos_fim"],
            )
            logger.info(
                "Conferência com %s: total %s (esperado %s) -> %s",
                referencia["descricao"], brl(conferencia["total_calculado"]),
                brl(conferencia["total_esperado"]),
                "OK" if conferencia["total_ok"] else "DIVERGENTE",
            )

        # ---------------- Persistência ----------------
        if usar_banco:
            with conexao(config) as conn:
                load_despesas.upsert_orgaos(conn, orgaos, camara)
                load_despesas.substituir_empenhos_pagamentos(
                    conn, codigo_municipio, exercicio,
                    cruzamento.empenhos_unicos, cruzamento.pagamentos_unicos,
                )
                load_despesas.salvar_classificacoes(
                    conn, [classificacoes[c] for c in consultados]
                )
                load_despesas.salvar_resultado_coleta(
                    conn, coleta_id, apuracao, pendencias, sorted(camara),
                    cruzamento.total_empenhos, cruzamento.total_pagamentos,
                )
        else:
            cache.update(classificacoes)
            _salvar_cache_arquivo(caminho_cache, cache)

    except Exception as erro:
        if usar_banco and coleta_id is not None:
            with conexao(config) as conn:
                load_despesas.registrar_erro_coleta(conn, coleta_id, str(erro))
        raise

    # ---------------- Relatório ----------------
    relatorio = {
        "coleta_id": coleta_id,
        "parametros": parametros,
        "orgaos": [
            {**o, "excluido": o["codigo_orgao"] in camara,
             "motivo_exclusao": camara.get(o["codigo_orgao"])}
            for o in orgaos
        ],
        "estatisticas": {
            "empenhos_examinados": cruzamento.total_empenhos,
            "pagamentos_examinados": cruzamento.total_pagamentos,
            "pagamentos_cruzados": len(cruzamento.pagamentos),
            "pagamentos_validos_no_recorte": len(filtros.validos),
            "empenhos_duplicados_identicos": cruzamento.empenhos_duplicados_identicos,
            "pagamentos_duplicados_identicos": cruzamento.pagamentos_duplicados_identicos,
            "excluidos": dict(filtros.excluidos),
            "cnpjs_unicos": len(cnpjs),
            "situacao_classificacao": dict(situacoes),
            "meses_com_releitura": [i for i in relatorio_coleta if i["passadas"] > 1],
            "meses_possivelmente_incompletos": [i for i in relatorio_coleta if not i["completo"]],
        },
        "totais_controle_todos_os_portes": {
            e: {"natureza": NATUREZAS_DESPESA[e], **apuracao.controle[e]}
            for e in ORDEM_ELEMENTOS
        },
        "totais_por_orgao": dict(sorted(totais_por_orgao.items())),
        "diagnostico_agregado": diagnostico_agregado,
        "total_controle": sum((apuracao.controle[e]["valor"] for e in ORDEM_ELEMENTOS), Decimal("0")),
        "por_categoria": apuracao.por_categoria,
        "total_me": sum(
            (apuracao.por_categoria[e]["ME"]["valor"] for e in ORDEM_ELEMENTOS), Decimal("0")
        ),
        "reconciliacao": apuracao.reconciliacao,
        "reconciliado": apuracao.reconciliado,
        "fornecedores": apuracao.fornecedores,
        "pendencias": [p.__dict__ for p in pendencias],
        "conferencia": conferencia,
    }

    pasta = Path(pasta_saida)
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / (
        f"pagamentos_{codigo_municipio}_{exercicio}_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    arquivo.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2, default=_json_padrao),
        encoding="utf-8",
    )
    logger.info("Relatório gravado em %s", arquivo)
    relatorio["arquivo_relatorio"] = str(arquivo)
    return relatorio