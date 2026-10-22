"""
Compara os totais calculados pelo comando `pagamentos` com os valores do
Portal da Transparência dos Municípios (TCE-CE), aba Prefeitura.

O portal não permite leitura automática, então os 7 valores do portal são
DIGITADOS (copie e cole do jeito que aparecem, ex.: "R$ 1.083.280,71").
O script lê o relatório JSON mais recente de saidas/ e mostra, por
natureza, o valor calculado, o do portal e a diferença.

Uso:
    python scripts/comparar_com_portal.py --municipio 010 --ano 2025

Opções:
    --relatorio CAMINHO   usa um relatório específico (padrão: o mais recente)
    --fornecedores 35     lista os fornecedores dessa natureza para conferir
                          com a página de favorecidos do portal
"""
import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

NATUREZAS = [
    ("39", "Outros Serviços de Terceiros - PJ"),
    ("30", "Material de Consumo"),
    ("51", "Obras e Instalações"),
    ("52", "Equipamentos e Material Permanente"),
    ("32", "Material, Bem ou Serviço p/ Distribuição Gratuita"),
    ("40", "Serviços de TI e Comunicação - PJ"),
    ("35", "Serviços de Consultoria"),
]
CENTAVO = Decimal("0.01")


def brl(valor: Decimal) -> str:
    texto = f"{valor.quantize(CENTAVO):,.2f}"
    return "R$ " + texto.replace(",", "_").replace(".", ",").replace("_", ".")


def ler_valor_portal(texto: str) -> Decimal | None:
    """Aceita 'R$ 1.083.280,71', '1.083.280,71', '1083280.71' ou vazio (= 0)."""
    limpo = texto.strip().replace("R$", "").replace(" ", "").replace(" ", "")
    if limpo in ("", "-"):
        return Decimal("0")
    if "," in limpo:                       # formato brasileiro
        limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return Decimal(limpo)
    except InvalidOperation:
        return None


def achar_relatorio(municipio: str, ano: int, pasta: str) -> Path:
    padrao = f"pagamentos_{municipio}_{ano:04d}00_*.json"
    arquivos = sorted(Path(pasta).glob(padrao))
    if not arquivos:
        sys.exit(
            f"Nenhum relatório '{padrao}' em {pasta}/.\n"
            f"Rode antes: python -m src.main pagamentos --municipio {municipio} --ano {ano}"
        )
    return arquivos[-1]


def mostrar_orgaos(relatorio: dict, caminho: Path) -> None:
    """Lista os órgãos do cadastro e quanto cada um somou em cada natureza."""
    print(f"\nRelatório: {caminho}\n")
    print("Órgãos do cadastro do TCE para este exercício:")
    print(f"  {'Cód':<5}{'Tipo':<6}{'Situação':<12}Nome")
    for orgao in relatorio["orgaos"]:
        situacao = "EXCLUÍDO" if orgao["excluido"] else "incluído"
        print(f"  {orgao['codigo_orgao']:<5}{orgao['codigo_tipo_unidade'] or '':<6}"
              f"{situacao:<12}{orgao['nome_orgao']}")

    por_orgao = relatorio.get("totais_por_orgao")
    if not por_orgao:
        print("\nEste relatório é de uma versão anterior e não tem o total por órgão.")
        print("Rode o comando pagamentos de novo para gerar um relatório atualizado.")
        return

    codigos = [c for c, _ in NATUREZAS]
    print("\nTotal pago por órgão em cada natureza (só órgãos incluídos no cálculo):")
    print(f"{'Órg':<5}{'Tp':<4}{'Nome':<32}" + "".join(f"{'[' + c + ']':>17}" for c in codigos)
          + f"{'TOTAL':>19}")
    for codigo, item in por_orgao.items():
        valores = [Decimal(item["por_elemento"][c]) for c in codigos]
        print(f"{codigo:<5}{item['codigo_tipo_unidade'] or '':<4}{(item['nome_orgao'] or '')[:31]:<32}"
              + "".join(f"{brl(v):>17}" for v in valores) + f"{brl(Decimal(item['total'])):>19}")
    print("\nCompare com as abas do portal: se um órgão aqui pertence a outra aba "
          "(ex.: autarquia, fundo, instituto de previdência), ele não deveria somar em 'Prefeitura'.")


def testar_exclusoes(relatorio: dict, portal: dict[str, Decimal]) -> None:
    """Procura a regra que reproduz o portal, testando combinações de:
      - excluir órgãos com tipo diferente de 00 (Prefeitura) e 03 (secretaria);
      - contar ou não pagamentos estornados;
      - cortar os pagamentos em uma referência (mês) anterior ao fim da coleta.
    Mostra as combinações que mais se aproximam do portal.
    """
    from itertools import combinations

    agregado = relatorio.get("diagnostico_agregado")
    if not agregado:
        print("\nEste relatório é de uma versão anterior: rode o comando pagamentos de novo.")
        return

    codigos_nat = [c for c, _ in NATUREZAS]
    tipos = {o["codigo_orgao"]: (o.get("codigo_tipo_unidade") or "") for o in relatorio["orgaos"]}
    nomes = {o["codigo_orgao"]: o.get("nome_orgao") or "" for o in relatorio["orgaos"]}
    orgaos = sorted({linha["orgao"] for linha in agregado})
    referencias = sorted({linha["referencia"] for linha in agregado})
    posicao_ref = {ref: i for i, ref in enumerate(referencias)}

    # acumulado[orgao][estornado][i][natureza] = soma até a referência i (inclusive)
    acumulado = {
        o: {e: [{c: Decimal("0") for c in codigos_nat} for _ in referencias] for e in (False, True)}
        for o in orgaos
    }
    for linha in agregado:
        i = posicao_ref[linha["referencia"]]
        acumulado[linha["orgao"]][linha["estornado"]][i][linha["elemento"]] += Decimal(linha["valor"])
    for o in orgaos:
        for e in (False, True):
            serie = acumulado[o][e]
            for i in range(1, len(serie)):
                for c in codigos_nat:
                    serie[i][c] += serie[i - 1][c]

    total_orgao = {o: sum(acumulado[o][False][-1].values(), Decimal("0")) for o in orgaos}
    candidatos = [o for o in orgaos if tipos.get(o, "") not in ("00", "03")]
    candidatos = sorted(candidatos, key=lambda o: -total_orgao[o])[:8]

    # cortes testados: últimos 12 meses coletados (o fim da coleta é o primeiro)
    cortes = list(range(len(referencias) - 1, max(-1, len(referencias) - 13), -1))

    resultados = []
    for tamanho in range(0, len(candidatos) + 1):
        for combo in combinations(candidatos, tamanho):
            incluidos = [o for o in orgaos if o not in combo]
            for com_estornados in (False, True):
                for corte in cortes:
                    calculado = {c: Decimal("0") for c in codigos_nat}
                    for o in incluidos:
                        base = acumulado[o][False][corte]
                        for c in codigos_nat:
                            calculado[c] += base[c]
                        if com_estornados:
                            extra = acumulado[o][True][corte]
                            for c in codigos_nat:
                                calculado[c] += extra[c]
                    batem = sum(calculado[c] == portal[c] for c in codigos_nat)
                    erro = sum(abs(calculado[c] - portal[c]) for c in codigos_nat)
                    resultados.append((batem, erro, combo, com_estornados, referencias[corte], calculado))
    resultados.sort(key=lambda r: (-r[0], r[1]))

    print("\nÓrgãos candidatos a exclusão (tipo diferente de 00/03):")
    for o in candidatos:
        print(f"  {o:<4} tipo {tipos.get(o, ''):<4} {nomes.get(o, '')}  (total {brl(total_orgao[o])})")
    print(f"Cortes testados: pagamentos até {', '.join(referencias[c] for c in cortes)}")

    print("\nMelhores combinações:")
    for batem, erro, combo, com_estornados, corte, _ in resultados[:8]:
        excluir = ", ".join(f"{o} (tipo {tipos.get(o, '')})" for o in combo) or "nenhum"
        print(f"  {batem}/7 batem | diferença {brl(erro):>18} | pagamentos até {corte} | "
              f"estornados {'CONTAM' if com_estornados else 'não contam'} | excluir órgãos: {excluir}")

    batem, erro, combo, com_estornados, corte, calculado = resultados[0]
    print("\nDetalhe da melhor combinação:")
    for c, nome in NATUREZAS:
        dif = calculado[c] - portal[c]
        print(f"  [{c}] {nome[:40]:<41}{brl(calculado[c]):>20}{brl(portal[c]):>20}{brl(dif):>18}"
              f"  {'OK' if dif == 0 else 'DIVERGE'}")
    if batem == 7:
        print("\nEncontrada uma regra que reproduz o portal nas 7 naturezas.")
    else:
        print("\nNenhuma combinação reproduz as 7 naturezas: a causa é outra. Mande este resultado.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compara o cálculo com o portal do TCE")
    parser.add_argument("--municipio", required=True, help="código TCE de 3 dígitos")
    parser.add_argument("--ano", required=True, type=int)
    parser.add_argument("--relatorio", help="caminho de um relatório específico")
    parser.add_argument("--saida", default="saidas", help="pasta dos relatórios")
    parser.add_argument("--fornecedores", help="código da natureza para listar fornecedores")
    parser.add_argument(
        "--testar-exclusoes", action="store_true",
        help="depois de digitar os valores do portal, testa excluir combinações de "
             "órgãos com tipo diferente de 00/03 e mostra qual bate com o portal",
    )
    parser.add_argument(
        "--orgaos", action="store_true",
        help="só mostra os órgãos (incluídos/excluídos) e o total de cada um por natureza",
    )
    args = parser.parse_args()

    municipio = args.municipio.zfill(3)
    caminho = Path(args.relatorio) if args.relatorio else achar_relatorio(municipio, args.ano, args.saida)
    relatorio = json.loads(caminho.read_text(encoding="utf-8"))

    if args.orgaos:
        mostrar_orgaos(relatorio, caminho)
        return
    parametros = relatorio["parametros"]
    totais = relatorio["totais_controle_todos_os_portes"]

    print(f"\nRelatório: {caminho}")
    print(f"Município {parametros['codigo_municipio']} | exercício {parametros['exercicio_orcamento']} | "
          f"pagamentos {parametros['referencia_pagamentos_inicio']} a {parametros['referencia_pagamentos_fim']}")
    excluidos = [f"{o['codigo_orgao']} {o['nome_orgao']}" for o in relatorio["orgaos"] if o["excluido"]]
    print(f"Órgãos excluídos: {excluidos or 'NENHUM — confira a Câmara!'}")
    print("\nAbra no navegador (aba Prefeitura):")
    print(f"  https://municipios-transparencia.tce.ce.gov.br/index.php/municipios/despesas/"
          f"mun/{municipio}/versao/{args.ano}")
    print("\n" + "!" * 78)
    print(f"  ATENÇÃO: o endereço no navegador precisa terminar em  /mun/{municipio}/versao/{args.ano}")
    print("  O título da página NÃO mostra o ano. Comparar com a página de outro ano")
    print("  faz todas as naturezas divergirem, mesmo com o cálculo certo.")
    print("!" * 78)
    print("\nDigite o valor de cada natureza como aparece no portal "
          "(ex.: R$ 1.083.280,71). Enter vazio = R$ 0,00.\n")

    linhas = []
    for codigo, nome in NATUREZAS:
        while True:
            texto = input(f"  [{codigo}] {nome}: ")
            valor_portal = ler_valor_portal(texto)
            if valor_portal is not None:
                break
            print("     Valor não reconhecido. Exemplo: R$ 1.083.280,71")
        calculado = Decimal(totais[codigo]["valor"])
        linhas.append((codigo, nome, calculado, valor_portal, calculado - valor_portal))

    # ---------------- tabela de resultado ----------------
    print("\n" + "=" * 104)
    print(f"{'Nat':<4}{'Natureza':<46}{'Calculado':>18}{'Portal':>18}{'Diferença':>18}")
    print("-" * 104)
    total_calc, total_portal, divergentes = Decimal("0"), Decimal("0"), 0
    for codigo, nome, calculado, portal, diferenca in linhas:
        total_calc += calculado
        total_portal += portal
        marca = "OK" if diferenca == 0 else "DIVERGE"
        divergentes += diferenca != 0
        print(f"{codigo:<4}{nome[:45]:<46}{brl(calculado):>18}{brl(portal):>18}{brl(diferenca):>18}  {marca}")
    print("-" * 104)
    print(f"{'':<4}{'TOTAL':<46}{brl(total_calc):>18}{brl(total_portal):>18}"
          f"{brl(total_calc - total_portal):>18}")
    print("=" * 104)

    if divergentes == 0:
        print("\nTodas as 7 naturezas batem com o portal (diferença R$ 0,00).")
    else:
        print(f"\n{divergentes} natureza(s) divergem. Possíveis causas:")
        abaixo = sum(1 for _, _, _, _, d in linhas if d < 0)
        acima = sum(1 for _, _, _, _, d in linhas if d > 0)
        fim_coleta = parametros["referencia_pagamentos_fim"]
        if acima == 0 and abaixo == divergentes:
            print(f"  >> TODAS abaixo do portal: provavelmente faltam meses. A coleta foi até "
                  f"{fim_coleta}. Confira a 'Última atualização' no rodapé do portal e rode "
                  f"de novo com --pagamentos-ate no mês dela.")
        elif abaixo == 0 and acima == divergentes:
            print(f"  >> TODAS acima do portal: provavelmente há órgão a mais no cálculo ou a "
                  f"coleta (até {fim_coleta}) passou da 'Última atualização' do portal. "
                  f"Teste com --testar-exclusoes.")
        elif acima and abaixo:
            print(f"  >> Umas acima e outras abaixo: confira se a página do portal é a "
                  f"versao/{args.ano} (o título da página não mostra o ano).")
        print(f"  - PRIMEIRO confira o ano: a página do portal é a versao/{args.ano}? "
              f"(se divergem quase todas, é o motivo mais comum);")
        print(f"  - a coleta foi até {fim_coleta}: deve ser o mês da 'Última atualização' do portal;")
        print("  - o portal foi atualizado depois da coleta (rode o comando pagamentos de novo);")
        print("  - período de pagamentos diferente (--pagamentos-ate);")
        print("  - Câmara não excluída ou outro órgão a excluir (veja 'Órgãos excluídos' acima);")
        print("  - pendências no relatório (seção 'pendencias' do JSON).")
        print("Use --fornecedores <código> para comparar fornecedor a fornecedor.")

    pendencias = relatorio.get("pendencias") or []
    if pendencias:
        print(f"\nAtenção: o relatório tem {len(pendencias)} pendência(s); veja a seção 'pendencias'.")

    # grava a comparação ao lado do relatório
    destino = caminho.with_name(caminho.stem + "_comparacao_portal.csv")
    with destino.open("w", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(["natureza", "nome", "calculado", "portal", "diferenca"])
        for codigo, nome, calculado, portal, diferenca in linhas:
            escritor.writerow([codigo, nome, calculado.quantize(CENTAVO),
                               portal.quantize(CENTAVO), diferenca.quantize(CENTAVO)])
    print(f"\nComparação salva em {destino} (abre no Excel).")

    if args.testar_exclusoes:
        testar_exclusoes(relatorio, {codigo: portal for codigo, _, _, portal, _ in linhas})

    # ---------------- fornecedores de uma natureza ----------------
    if args.fornecedores:
        natureza = args.fornecedores
        fornecedores = [f for f in relatorio["fornecedores"] if f["elemento_despesa"] == natureza]
        print(f"\nFornecedores da natureza {natureza} (todos os portes), maior valor primeiro:")
        print(f"{'CNPJ/CPF':<16}{'Nome':<50}{'Porte':<24}{'Pagamentos':>11}{'Valor pago':>20}")
        for f in sorted(fornecedores, key=lambda x: Decimal(x["valor_pago"]), reverse=True):
            print(f"{f['documento']:<16}{(f['nome_negociante'] or '')[:49]:<50}"
                  f"{f['categoria_porte']:<24}{f['quantidade_pagamentos']:>11}"
                  f"{brl(Decimal(f['valor_pago'])):>20}")
        print("\nNo portal, clique no nome da natureza para ver os favorecidos e compare.")


if __name__ == "__main__":
    main()