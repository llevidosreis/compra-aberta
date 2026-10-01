from collections import Counter
from datetime import date, timedelta
import logging

import psycopg2.extras

from src.clients.pncp_client import PNCPClient
from src.config import Config
from src.db import conexao
from src.load.load_pncp import (
    contratacoes_alteradas,
    upsert_contratacoes_pncp,
    upsert_itens_pncp,
    upsert_matches,
)
from src.matching.scorer import avaliar_match
from src.pipeline.control import (
    atualizar_start_index,
    buscar_execucao_incompleta,
    concluir_execucao,
    iniciar_execucao,
    registrar_erro,
)
from src.quality.validators import validar_contratacoes_pncp, validar_itens_pncp
from src.transform.deduplicate import deduplicar_por_chave
from src.transform.normalize import normalizar_contratacao_pncp, normalizar_item_pncp

logger = logging.getLogger(__name__)


def _itens_da_contratacao(client: PNCPClient, contratacao: dict) -> list[dict]:
    cnpj = contratacao.get("orgao_cnpj")
    ano = contratacao.get("ano_compra")
    sequencial = contratacao.get("sequencial_compra")
    if not cnpj or not ano or not sequencial:
        return []
    normalizados = []
    for pagina, _ in client.listar_itens(cnpj, ano, sequencial):
        normalizados.extend(normalizar_item_pncp(item) for item in pagina)
    resultado = validar_itens_pncp(normalizados)
    for registro, motivo in resultado.invalidos:
        logger.warning("PNCP item descartado controle=%s motivo=%s", contratacao["numero_controle_pncp"], motivo)
    return deduplicar_por_chave(resultado.validos, chave=lambda item: (str(item["numero_item"]),))


def _calcular_matches(conn, contratacao: dict, itens: list[dict], limiar: float) -> None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """SELECT p.cnpj, p.produtos_palavras_chave, p.ufs_atuacao,
                      p.municipios_atuacao, e.porte_empresa, e.cnae_principal,
                      e.cnaes_secundarios
               FROM compra_livre.perfil_cliente p
               JOIN compra_livre.dim_empresa e ON e.cnpj = p.cnpj
               WHERE p.ativo = TRUE"""
        )
        perfis = [dict(linha) for linha in cursor.fetchall()]

    matches = []
    for perfil in perfis:
        empresa = {
            "porte_empresa": perfil.get("porte_empresa"),
            "cnae_principal": perfil.get("cnae_principal"),
            "cnaes_secundarios": perfil.get("cnaes_secundarios"),
        }
        match = avaliar_match(contratacao, itens, perfil, empresa, limiar)
        if match:
            matches.append(match)
    cnpjs_compatíveis = [match["cnpj"] for match in matches]
    with conn.cursor() as cursor:
        if cnpjs_compatíveis:
            cursor.execute(
                """DELETE FROM compra_livre.pncp_match
                   WHERE numero_controle_pncp = %s AND NOT (cnpj = ANY(%s))""",
                (contratacao["numero_controle_pncp"], cnpjs_compatíveis),
            )
        else:
            cursor.execute(
                "DELETE FROM compra_livre.pncp_match WHERE numero_controle_pncp = %s",
                (contratacao["numero_controle_pncp"],),
            )
    upsert_matches(conn, contratacao["numero_controle_pncp"], matches)


def _carregar_modalidade(
    config: Config,
    client: PNCPClient,
    modalidade: int,
    modo: str,
    data_inicio: str | None,
    data_fim: str,
    listar,
) -> None:
    fonte = f"pncp_contratacoes_m{modalidade}"
    with conexao(config) as conn:
        pendente = buscar_execucao_incompleta(
            conn, fonte, modo, None, data_inicio, data_fim
        )
        if pendente:
            execucao = pendente
            pagina_inicial = max(1, execucao.ultimo_start_index + 1)
            logger.info("Retomando %s da página %d", fonte, pagina_inicial)
        else:
            execucao = iniciar_execucao(conn, fonte, modo, None, data_inicio, data_fim)
            pagina_inicial = 1

    try:
        for pagina_bruta, pagina in listar(pagina_inicial):
            normalizados = [normalizar_contratacao_pncp(registro) for registro in pagina_bruta]
            resultado = validar_contratacoes_pncp(normalizados)
            for registro, motivo in resultado.invalidos:
                logger.warning("PNCP contratação descartada motivo=%s registro=%s", motivo, registro.get("numero_controle_pncp"))
            validos = deduplicar_por_chave(
                resultado.validos, chave=lambda registro: (registro["numero_controle_pncp"],)
            )

            with conexao(config) as conn:
                candidatos = contratacoes_alteradas(conn, validos)
            itens_por_controle = {}
            if candidatos:
                for contratacao in validos:
                    if contratacao["numero_controle_pncp"] in candidatos:
                        itens_por_controle[contratacao["numero_controle_pncp"]] = _itens_da_contratacao(client, contratacao)

            with conexao(config) as conn:
                alterados = upsert_contratacoes_pncp(conn, validos)
                itens_persistir = []
                contratos_por_controle = {
                    item["numero_controle_pncp"]: item
                    for item in validos
                    if item["numero_controle_pncp"] in alterados
                }
                for controle in alterados:
                    itens = itens_por_controle.get(controle, [])
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "DELETE FROM compra_livre.pncp_item WHERE numero_controle_pncp = %s",
                            (controle,),
                        )
                    upsert_itens_pncp(conn, [
                        {**item, "numero_controle_pncp": controle} for item in itens
                    ])
                    _calcular_matches(
                        conn, contratos_por_controle[controle], itens, config.pncp_limiar_match
                    )
                # Para PNCP, ultimo_start_index registra a última página concluída.
                atualizar_start_index(conn, execucao.id, pagina)

            motivos = Counter(motivo for _, motivo in resultado.invalidos)
            logger.info(
                "pncp modalidade=%s página=%d recebidos=%d válidos=%d descartados=%d alterados=%d motivos=%s",
                modalidade, pagina, len(pagina_bruta), len(resultado.validos),
                len(resultado.invalidos), len(alterados), dict(motivos),
            )

        with conexao(config) as conn:
            concluir_execucao(conn, execucao.id)
    except Exception as erro:
        with conexao(config) as conn:
            registrar_erro(conn, execucao.id, str(erro))
        raise


def executar_historico(config: Config, data_inicio: str, data_fim: str) -> None:
    client = PNCPClient(config)
    modalidades_com_erro = []
    for modalidade in config.pncp_modalidades:
        try:
            _carregar_modalidade(
                config, client, modalidade, "historico", data_inicio, data_fim,
                lambda pagina: client.listar_contratacoes_publicadas(
                    data_inicio, data_fim, modalidade, config.uf, pagina
                ),
            )
        except Exception:
            modalidades_com_erro.append(str(modalidade))
            logger.exception("Falha ao carregar modalidade PNCP %s; continuando", modalidade)
    if modalidades_com_erro:
        raise RuntimeError("Falha nas modalidades PNCP: " + ", ".join(modalidades_com_erro))


def executar_incremental(config: Config, dias: int, somente_abertos: bool = True) -> None:
    if dias < 0:
        raise ValueError("dias não pode ser negativo")
    hoje = date.today()
    data_inicio = (hoje - timedelta(days=dias)).isoformat()
    data_fim = hoje.isoformat()
    client = PNCPClient(config)
    modalidades_com_erro = []

    for modalidade in config.pncp_modalidades:
        consultas = []
        if somente_abertos:
            consultas.append((
                "incremental_abertos", None, data_fim,
                lambda pagina: client.listar_contratacoes_com_proposta_aberta(
                    data_fim, modalidade, config.uf, pagina
                ),
            ))
        else:
            consultas.append((
                "incremental_publicadas", data_inicio, data_fim,
                lambda pagina: client.listar_contratacoes_publicadas(
                    data_inicio, data_fim, modalidade, config.uf, pagina
                ),
            ))
        consultas.append((
            "incremental_atualizadas", data_inicio, data_fim,
            lambda pagina: client.listar_contratacoes_atualizadas(
                data_inicio, data_fim, modalidade, config.uf, pagina
            ),
        ))
        try:
            for modo, inicio, fim, listar in consultas:
                _carregar_modalidade(config, client, modalidade, modo, inicio, fim, listar)
        except Exception:
            modalidades_com_erro.append(str(modalidade))
            logger.exception("Falha ao carregar modalidade PNCP %s; continuando", modalidade)

    if modalidades_com_erro:
        raise RuntimeError("Falha nas modalidades PNCP: " + ", ".join(dict.fromkeys(modalidades_com_erro)))