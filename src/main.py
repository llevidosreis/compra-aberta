# src/main.py

"""
Ponto de entrada do pipeline.
"""

import argparse
import logging
import sys

from src.config import carregar_config
from src.pipeline import dotacoes, full_load, incremental, pncp


def configurar_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main():
    configurar_logging()

    parser = argparse.ArgumentParser(
        description="Pipeline de dados Compra Livre"
    )

    subparsers = parser.add_subparsers(
        dest="modo",
        required=True,
    )

    parser_full = subparsers.add_parser(
        "full",
        help="Carga completa",
    )

    parser_full.add_argument(
        "--data-inicio",
        required=True,
        help="YYYY-MM-DD",
    )

    parser_full.add_argument(
        "--data-fim",
        required=True,
        help="YYYY-MM-DD",
    )

    parser_incremental = subparsers.add_parser(
        "incremental",
        help="Carga incremental",
    )

    parser_incremental.add_argument(
        "--dias",
        type=int,
        default=7,
        help="Janela retroativa em dias",
    )

    parser_dotacoes = subparsers.add_parser(
        "dotacoes",
        help="Coleta dotações associadas às licitações existentes",
    )
    parser_dotacoes.add_argument("--data-inicio", required=True, help="YYYY-MM-DD")
    parser_dotacoes.add_argument("--data-fim", required=True, help="YYYY-MM-DD")

    parser_pncp_historico = subparsers.add_parser(
        "pncp-historico", help="Carga histórica de contratações PNCP"
    )
    parser_pncp_historico.add_argument("--data-inicio", required=True, help="YYYY-MM-DD")
    parser_pncp_historico.add_argument("--data-fim", required=True, help="YYYY-MM-DD")

    parser_pncp_incremental = subparsers.add_parser(
        "pncp-incremental", help="Carga incremental de contratações PNCP"
    )
    parser_pncp_incremental.add_argument("--dias", type=int, default=7, help="Janela retroativa em dias")

    args = parser.parse_args()
    config = carregar_config()

    if args.modo == "full":
        full_load.executar(
            config,
            args.data_inicio,
            args.data_fim,
        )

    elif args.modo == "incremental":
        incremental.executar(
            config,
            args.dias,
        )
    elif args.modo == "dotacoes":
        dotacoes.executar(config, args.data_inicio, args.data_fim)
    elif args.modo == "pncp-historico":
        pncp.executar_historico(config, args.data_inicio, args.data_fim)
    elif args.modo == "pncp-incremental":
        pncp.executar_incremental(config, args.dias)



if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        logging.warning("Pipeline interrompido pelo usuário.")
        sys.exit(130)

    except Exception:
        logging.exception(
            "Pipeline encerrado com erro."
        )
        sys.exit(1)