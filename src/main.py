# src/main.py

"""
Ponto de entrada do pipeline.

Os módulos de cada modo são importados só quando o modo é usado: assim um
problema em um fluxo (ex.: licitações) não impede rodar outro (ex.: pagamentos).
"""

import argparse
import logging
import sys

from src.config import carregar_config


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

    # ------------------------------------------------------------------
    # Pagamentos da Prefeitura nas 7 naturezas + recorte de Microempresas
    # ------------------------------------------------------------------
    parser_pag = subparsers.add_parser(
        "pagamentos",
        help="Apura pagamentos da Prefeitura nas 7 naturezas e o subtotal de ME",
    )
    parser_pag.add_argument(
        "--municipio", required=True, nargs="+",
        help="Código(s) TCE do município, ex.: 004 (Acaraú)",
    )
    parser_pag.add_argument("--ano", required=True, type=int, help="Ano do orçamento, ex.: 2025")
    parser_pag.add_argument("--empenhos-de", help="Referência inicial dos empenhos AAAAMM (padrão: ANO01)")
    parser_pag.add_argument("--empenhos-ate", help="Referência final dos empenhos AAAAMM (padrão: ANO12)")
    parser_pag.add_argument("--pagamentos-de", help="Referência inicial dos pagamentos AAAAMM (padrão: ANO01)")
    parser_pag.add_argument(
        "--pagamentos-ate",
        help="Referência final dos pagamentos AAAAMM (padrão: mês anterior ao atual). "
             "ATENÇÃO: para comparar com o portal, use o mês da 'Última atualização' "
             "do rodapé da página (ex.: 27/09/2026 -> 202609); coletar menos meses "
             "deixa todas as naturezas abaixo do portal. Guia de Acaraú: 202609.",
    )
    parser_pag.add_argument(
        "--regra-mei", choices=["incluir", "excluir"], default="incluir",
        help="incluir (padrão do guia): porte 01 basta; excluir: ME exceto MEI confirmado",
    )
    parser_pag.add_argument(
        "--referencia-base-cadastral", default="NAO_INFORMADA",
        help="Edição/competência da base cadastral, se conhecida",
    )
    parser_pag.add_argument(
        "--excluir-orgao", action="append", default=[],
        help="Código de órgão a excluir além da Câmara detectada (pode repetir)",
    )
    parser_pag.add_argument(
        "--sem-banco", action="store_true",
        help="Não grava no Postgres; gera só o relatório JSON em saidas/",
    )
    parser_pag.add_argument("--saida", default="saidas", help="Pasta dos relatórios")

    # ------------------------------------------------------------------
    # PNCP
    # ------------------------------------------------------------------
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
    config = carregar_config(
        exigir_banco=not (args.modo == "pagamentos" and args.sem_banco)
    )

    if args.modo == "full":
        from src.pipeline import full_load
        full_load.executar(
            config,
            args.data_inicio,
            args.data_fim,
        )

    elif args.modo == "incremental":
        from src.pipeline import incremental
        incremental.executar(
            config,
            args.dias,
        )
    elif args.modo == "dotacoes":
        from src.pipeline import dotacoes
        dotacoes.executar(config, args.data_inicio, args.data_fim)
    elif args.modo == "pncp-historico":
        from src.pipeline import pncp
        pncp.executar_historico(config, args.data_inicio, args.data_fim)

    elif args.modo == "pncp-incremental":
        from src.pipeline import pncp
        pncp.executar_incremental(config, args.dias)

    elif args.modo == "pagamentos":
        from src.pipeline import despesas_me
        for codigo_municipio in args.municipio:
            despesas_me.executar(
                config,
                codigo_municipio=codigo_municipio,
                ano=args.ano,
                referencia_empenhos_inicio=args.empenhos_de,
                referencia_empenhos_fim=args.empenhos_ate,
                referencia_pagamentos_inicio=args.pagamentos_de,
                referencia_pagamentos_fim=args.pagamentos_ate,
                regra_mei=args.regra_mei,
                referencia_base_cadastral=args.referencia_base_cadastral,
                orgaos_excluir_adicionais=args.excluir_orgao,
                usar_banco=not args.sem_banco,
                pasta_saida=args.saida,
            )


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