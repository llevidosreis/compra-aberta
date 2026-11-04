"""Coleta todos os municípios no período informado e aplica upserts."""

import logging
import unicodedata
from datetime import date
from time import monotonic

from src.clients.opencnpj_client import OpenCNPJClient
from src.clients.tce_client import MunicipioTCE, TCEClient
from src.config import Config
from src.db import conexao
from src.enrich.enrich_empresas import enriquecer_empresas_pendentes
from src.load.load_dimensoes import upsert_municipios
from src.load.load_fatos import transformar_fatos_do_municipio
from src.pipeline.extract_common import carregar_itens, carregar_licitantes

logger = logging.getLogger(__name__)


def _normalizar_letra_inicial(valor: str) -> str:
    normalizado = "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", valor.strip())
        if not unicodedata.combining(caractere)
    ).upper()
    if len(normalizado) != 1 or not normalizado.isalpha():
        raise ValueError("A inicial do município deve ser uma única letra.")
    return normalizado


def _filtrar_municipios_por_inicial(
    municipios: list[MunicipioTCE], inicial: str | None
) -> list[MunicipioTCE]:
    if inicial is None:
        return municipios

    letra = _normalizar_letra_inicial(inicial)
    selecionados = [
        municipio
        for municipio in municipios
        if "".join(
            caractere
            for caractere in unicodedata.normalize("NFD", municipio.nome.strip())
            if not unicodedata.combining(caractere)
        ).upper().startswith(letra)
    ]
    if not selecionados:
        raise ValueError(f"Nenhum município encontrado com inicial {letra}.")
    return selecionados


def _validar_periodo(data_inicio: str, data_fim: str) -> None:
    try:
        inicio = date.fromisoformat(data_inicio)
        fim = date.fromisoformat(data_fim)
    except ValueError as erro:
        raise ValueError(
            "data_inicio e data_fim devem estar no formato YYYY-MM-DD."
        ) from erro

    if inicio.isoformat() != data_inicio or fim.isoformat() != data_fim:
        raise ValueError(
            "data_inicio e data_fim devem estar no formato YYYY-MM-DD."
        )
    if inicio > fim:
        raise ValueError("data_inicio não pode ser posterior a data_fim.")


def executar(
    config: Config,
    data_inicio: str,
    data_fim: str,
    *,
    enriquecer: bool = True,
    inicial: str | None = None,
) -> None:
    _validar_periodo(data_inicio, data_fim)
    if inicial is not None:
        inicial = _normalizar_letra_inicial(inicial)
    tce_client = TCEClient(config)

    inicio_execucao = monotonic()
    logger.info(
        "Iniciando carga completa: %s até %s",
        data_inicio,
        data_fim,
    )

    # ============================================================
    # 1. CARREGAR MUNICÍPIOS
    # ============================================================

    try:
        municipios = tce_client.listar_municipios()

        with conexao(config) as conn:
            upsert_municipios(conn, municipios)
        logger.info(
            "dim_municipio atualizada com a lista oficial de municípios do %s",
            config.uf,
        )

    except Exception:
        logger.exception(
            "Falha crítica ao obter/carregar a lista de municípios. "
            "Pipeline não pode continuar."
        )
        raise

    municipios = _filtrar_municipios_por_inicial(municipios, inicial)
    logger.info(
        "Carga selecionada para %d município(s)%s",
        len(municipios),
        f" com inicial {inicial.strip().upper()}" if inicial else "",
    )

    municipios_com_erro = []
    municipios_processados = []
    opencnpj_client = OpenCNPJClient(config) if enriquecer else None

    # ============================================================
    # 2. EXTRAÇÃO E PUBLICAÇÃO PROGRESSIVA POR MUNICÍPIO
    # ============================================================

    for municipio in municipios:
        logger.info(
            "=== Município %s (codigo_municipio=%s) ===",
            municipio.nome,
            municipio.codigo_municipio,
        )

        try:
            carregar_licitantes(
                config,
                tce_client,
                municipio.codigo_municipio,
                data_inicio,
                data_fim,
                "full",
                limpar_staging=False,
            )

            carregar_itens(
                config,
                tce_client,
                municipio.codigo_municipio,
                data_inicio,
                data_fim,
                "full",
                limpar_staging=False,
            )

            logger.info(
                "Transformando e publicando fatos do município %s (código=%s).",
                municipio.nome,
                municipio.codigo_municipio,
            )
            with conexao(config) as conn:
                transformar_fatos_do_municipio(
                    conn,
                    municipio.codigo_municipio,
                )

            municipios_processados.append(municipio)
            logger.info(
                "Município publicado: código=%s progresso=%d/%d",
                municipio.codigo_municipio,
                len(municipios_processados),
                len(municipios),
            )

        except Exception as erro:
            municipios_com_erro.append(municipio.codigo_municipio)

            logger.exception(
                "ERRO ao processar o município %s "
                "(codigo=%s). O município será ignorado "
                "e o pipeline continuará. Erro: %s",
                municipio.nome,
                municipio.codigo_municipio,
                erro,
            )

            continue

    # ============================================================
    # 3. ENRIQUECIMENTO OPEN CNPJ (OPCIONAL)
    # ============================================================

    erro_enriquecimento = None
    if enriquecer:
        try:
            logger.info("Iniciando enriquecimento global via OpenCNPJ.")
            total_enriquecido = enriquecer_empresas_pendentes(
                config,
                opencnpj_client,
                data_inicio=data_inicio,
                data_fim=data_fim,
            )
            logger.info("Enriquecimento global concluído: %d empresas.", total_enriquecido)
        except Exception as erro:
            logger.exception("ERRO no enriquecimento global via OpenCNPJ.")
            erro_enriquecimento = erro
    else:
        logger.info(
            "Enriquecimento OpenCNPJ adiado; use o comando enriquecer "
            "com o período carregado."
        )

    # ============================================================
    # 5. RESULTADO FINAL
    # ============================================================

    if municipios_com_erro:
        municipios_unicos = list(dict.fromkeys(municipios_com_erro))

        logger.error(
            "Carga completa finalizada COM ERROS. "
            "%d município(s) apresentaram falha: %s",
            len(municipios_unicos),
            ", ".join(municipios_unicos),
        )

        raise RuntimeError(
            "A carga completa terminou com erro em "
            f"{len(municipios_unicos)} município(s): "
            f"{', '.join(municipios_unicos)}"
        )

    if erro_enriquecimento:
        raise RuntimeError(
            "Os fatos TCE foram processados, mas o enriquecimento OpenCNPJ falhou."
        ) from erro_enriquecimento

    logger.info(
        "Carga completa finalizada com sucesso. %d/%d municípios publicados "
        "em %.1f segundos.",
        len(municipios_processados),
        len(municipios),
        monotonic() - inicio_execucao,
    )