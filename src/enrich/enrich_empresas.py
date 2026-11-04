# src/enrich/enrich_empresas.py

"""
Enriquece empresas identificadas nas licitações via OpenCNPJ.

Somente CNPJs (14 dígitos) são enviados ao OpenCNPJ.
CPFs (11 dígitos) podem continuar existindo nos dados do TCE,
mas não são tratados como empresas para fins de enriquecimento.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

from src.cache.cnpj_cache import cnpjs_pendentes_de_consulta
from src.clients.opencnpj_client import DadosEmpresa, OpenCNPJClient
from src.config import Config
from src.db import conexao
from src.eligibility import empresa_me_confirmada
from src.load.load_dimensoes import upsert_empresas_enriquecidas

logger = logging.getLogger(__name__)

TAMANHO_LOTE = 25


def enriquecer_empresas_pendentes(
    config: Config,
    client: OpenCNPJClient,
    *,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    codigo_municipio: str | None = None,
) -> int:
    """
    Consulta CNPJs (14 dígitos) sem cache válido; opcionalmente limita aos
    itens do período e município informados.
    """
    if (data_inicio is None) != (data_fim is None):
        raise ValueError("data_inicio e data_fim devem ser informados juntos.")
    if codigo_municipio is not None and data_inicio is None:
        raise ValueError(
            "codigo_municipio requer data_inicio e data_fim para limitar o enriquecimento."
        )
    if data_inicio is not None and data_fim is not None:
        inicio = date.fromisoformat(data_inicio)
        fim = date.fromisoformat(data_fim)
        if inicio.isoformat() != data_inicio or fim.isoformat() != data_fim:
            raise ValueError("data_inicio e data_fim devem estar no formato YYYY-MM-DD.")
        if inicio > fim:
            raise ValueError("data_inicio não pode ser posterior a data_fim.")

    total_enriquecido = 0
    falhos_nesta_execucao: set[str] = set()
    while True:
        with conexao(config) as conn:
            pendentes = cnpjs_pendentes_de_consulta(
                conn,
                config,
                limite=1000,
                excluir=falhos_nesta_execucao,
                data_inicio=data_inicio,
                data_fim=data_fim,
                codigo_municipio=codigo_municipio,
            )
        if not pendentes:
            break

        logger.info("%d CNPJs pendentes no lote atual.", len(pendentes))
        lote: list[DadosEmpresa] = []
        enriquecidos_neste_lote = 0
        with ThreadPoolExecutor(
            max_workers=config.opencnpj_max_workers
        ) as executor:
            futuros = {
                executor.submit(client.consultar, cnpj): cnpj
                for cnpj in sorted(pendentes)
            }
            for futuro in as_completed(futuros):
                cnpj = futuros[futuro]
                try:
                    lote.append(futuro.result())
                except Exception:
                    falhos_nesta_execucao.add(cnpj)
                    logger.exception("Erro ao consultar CNPJ %s no OpenCNPJ.", cnpj)
                if len(lote) >= TAMANHO_LOTE:
                    _gravar_lote(config, lote)
                    total_enriquecido += len(lote)
                    enriquecidos_neste_lote += len(lote)
                    lote = []
        if lote:
            _gravar_lote(config, lote)
            total_enriquecido += len(lote)
            enriquecidos_neste_lote += len(lote)
        if enriquecidos_neste_lote == 0:
            logger.warning("Nenhum CNPJ do lote pôde ser enriquecido; interrompendo para evitar retry infinito.")
            break

    logger.info(
        "Enriquecimento concluído: %d empresas.",
        total_enriquecido,
    )

    if falhos_nesta_execucao:
        raise RuntimeError(
            f"Falha ao consultar {len(falhos_nesta_execucao)} CNPJ(s) no OpenCNPJ."
        )

    return total_enriquecido


def _gravar_lote(
    config: Config,
    lote: list[DadosEmpresa],
) -> None:
    """Grava um lote de empresas no PostgreSQL."""

    if not lote:
        return

    agora = datetime.now(timezone.utc)
    total_me_confirmadas = sum(
        empresa_me_confirmada(
            cnpj=empresa.cnpj,
            porte=empresa.porte_empresa,
            opcao_mei=empresa.opcao_mei,
            encontrado=empresa.encontrado,
            enriquecido=True,
            consultado_em=agora,
            fonte_atualizada_em=empresa.fonte_atualizada_em,
            dias_validade_cache=config.opencnpj_dias_validade_cache,
            agora=agora,
        )
        for empresa in lote
    )

    with conexao(config) as conn:
        upsert_empresas_enriquecidas(conn, lote)

    logger.info(
        "Lote OpenCNPJ persistido: %d empresas, %d classificadas como ME elegível.",
        len(lote),
        total_me_confirmadas,
    )