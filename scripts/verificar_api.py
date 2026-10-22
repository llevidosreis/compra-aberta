"""
Verificação completa da API Compra Livre (smoke test + consistência).

Testa TODOS os endpoints com a API rodando de verdade (servidor + banco):
  - status HTTP esperado (200, e também 400/404/422 nos casos de erro);
  - formato da resposta (campos obrigatórios, paginação limit/offset);
  - filtros (município, natureza, ano, porte, datas);
  - consistência dos números (percentuais entre 0 e 100, parte <= total,
    soma das categorias de porte = total de controle);
  - tempo de resposta (alerta acima de LIMITE_LENTO segundos).

Uso (com a API no ar em outro terminal: uvicorn src.api:app --reload):
    python scripts/verificar_api.py
    python scripts/verificar_api.py --url http://localhost:8000 --municipio 004 --ano 2025

Saída: uma linha por verificação (OK / FALHA / AVISO) e um resumo final.
Código de saída 1 se houver qualquer FALHA (dá para usar em CI).
"""
import argparse
import sys
import time
from decimal import Decimal
from urllib.parse import quote

import requests

LIMITE_LENTO = 5.0

# Valores do guia (portal TCE em 19/10/2026, pagamentos até 202609)
REFERENCIA_ACARAU_2025 = {
    "39": Decimal("66318441.19"),
    "30": Decimal("29531293.65"),
    "51": Decimal("31324092.95"),
    "52": Decimal("5166925.30"),
    "32": Decimal("1877012.00"),
    "40": Decimal("10483.64"),
    "35": Decimal("1083280.71"),
}


class Verificador:
    def __init__(self, url_base: str):
        self.url_base = url_base.rstrip("/")
        self.sessao = requests.Session()
        self.ok = 0
        self.falhas: list[str] = []
        self.avisos: list[str] = []

    # ------------------------------------------------------------------
    # Registro dos resultados
    # ------------------------------------------------------------------

    def _ok(self, nome: str, detalhe: str = "") -> None:
        self.ok += 1
        print(f"  OK     {nome}{' — ' + detalhe if detalhe else ''}")

    def _falha(self, nome: str, detalhe: str) -> None:
        self.falhas.append(f"{nome}: {detalhe}")
        print(f"  FALHA  {nome} — {detalhe}")

    def _aviso(self, nome: str, detalhe: str) -> None:
        self.avisos.append(f"{nome}: {detalhe}")
        print(f"  AVISO  {nome} — {detalhe}")

    def checar(self, condicao: bool, nome: str, detalhe_falha: str, detalhe_ok: str = "") -> bool:
        if condicao:
            self._ok(nome, detalhe_ok)
        else:
            self._falha(nome, detalhe_falha)
        return condicao

    # ------------------------------------------------------------------
    # Chamada HTTP
    # ------------------------------------------------------------------

    def get(self, caminho: str, params: dict | None = None, esperado: int = 200,
            nome: str | None = None):
        """Faz o GET, confere o status e devolve o JSON (ou None)."""
        nome = nome or f"GET {caminho}"
        if params:
            nome += " " + " ".join(f"{k}={v}" for k, v in params.items())
        inicio = time.monotonic()
        try:
            resposta = self.sessao.get(self.url_base + caminho, params=params, timeout=60)
        except requests.RequestException as erro:
            self._falha(nome, f"sem resposta ({erro})")
            return None
        duracao = time.monotonic() - inicio

        if resposta.status_code != esperado:
            self._falha(nome, f"status {resposta.status_code} (esperado {esperado}): "
                              f"{resposta.text[:200]}")
            return None
        if duracao > LIMITE_LENTO:
            self._aviso(nome, f"lento: {duracao:.1f}s")
        self._ok(nome, f"{resposta.status_code} em {duracao:.2f}s")
        try:
            return resposta.json()
        except ValueError:
            self._falha(nome, "resposta não é JSON")
            return None

    # ------------------------------------------------------------------
    # Validações reutilizáveis
    # ------------------------------------------------------------------

    def campos(self, item: dict, obrigatorios: list[str], nome: str) -> None:
        faltando = [c for c in obrigatorios if c not in item]
        self.checar(not faltando, f"{nome}: campos", f"faltando {faltando}")

    def paginacao(self, corpo: dict, limit: int, offset: int, nome: str) -> None:
        self.checar(
            corpo.get("limit") == limit and corpo.get("offset") == offset
            and isinstance(corpo.get("data"), list) and len(corpo["data"]) <= limit,
            f"{nome}: paginação",
            f"limit/offset/data inconsistentes: limit={corpo.get('limit')} "
            f"offset={corpo.get('offset')} itens={len(corpo.get('data') or [])}",
        )

    def percentuais(self, item: dict, nome: str) -> None:
        ruins = {
            k: v for k, v in item.items()
            if k.startswith("percentual") and v is not None and not 0 <= v <= 100
        }
        self.checar(not ruins, f"{nome}: percentuais entre 0 e 100", f"fora da faixa: {ruins}")

    def parte_menor_que_total(self, item: dict, pares: list[tuple[str, str]], nome: str) -> None:
        ruins = []
        for parte, total in pares:
            if parte in item and total in item:
                if Decimal(str(item[parte])) > Decimal(str(item[total])):
                    ruins.append(f"{parte}={item[parte]} > {total}={item[total]}")
        self.checar(not ruins, f"{nome}: parte <= total", "; ".join(ruins))


# ----------------------------------------------------------------------
# Blocos de teste
# ----------------------------------------------------------------------

def testar_sistema(v: Verificador) -> bool:
    print("\n[1] Sistema")
    raiz = v.get("/")
    if raiz is None:
        print("\nA API não respondeu. Ela está rodando? (uvicorn src.api:app --reload)")
        return False
    saude = v.get("/health")
    if saude is None:
        print("\n/health falhou: confira DATABASE_URL no .env e a conexão com o Supabase.")
        return False
    v.checar(saude.get("status") == "ok", "/health: status", f"retornou {saude}")
    resposta = v.sessao.get(v.url_base + "/openapi.json", timeout=30)
    v.checar(resposta.status_code == 200, "GET /openapi.json", f"status {resposta.status_code}")
    return True


def testar_municipios(v: Verificador, municipio: str) -> None:
    print("\n[2] Municípios")
    corpo = v.get("/municipios", {"limit": 5, "offset": 0})
    if corpo:
        v.paginacao(corpo, 5, 0, "/municipios")
        if corpo["data"]:
            v.campos(corpo["data"][0], ["codigo_municipio", "nome", "uf"], "/municipios")
        else:
            v._aviso("/municipios", "tabela dim_municipio vazia (rode o full load)")
    ce = v.get("/municipios", {"uf": "CE", "limit": 100})
    if ce and ce["data"]:
        v.checar(all(m["uf"] == "CE" for m in ce["data"]), "/municipios filtro uf",
                 "veio município de outra UF")
        codigos = {m["codigo_municipio"] for m in ce["data"]}
        v.checar(all(len(c) == 3 for c in codigos), "/municipios código TCE com 3 dígitos",
                 f"códigos fora do padrão: {[c for c in codigos if len(c) != 3][:5]}")
    v.get("/municipios", {"limit": 101}, esperado=422, nome="GET /municipios limite acima de 100")


def testar_licitacoes(v: Verificador, municipio: str) -> None:
    print("\n[3] Licitações, itens e dotações")
    corpo = v.get("/licitacoes", {"codigo_municipio": municipio, "limit": 10})
    if not corpo:
        return
    v.paginacao(corpo, 10, 0, "/licitacoes")
    if not corpo["data"]:
        v._aviso("/licitacoes", f"nenhuma licitação para o município {municipio}")
        return

    lic = corpo["data"][0]
    v.campos(lic, ["id", "codigo_municipio", "numero_licitacao", "valor_total_licitacao",
                   "quantidade_empresas", "quantidade_itens", "naturezas_despesa"], "/licitacoes")
    v.checar(all(l["codigo_municipio"] == municipio for l in corpo["data"]),
             "/licitacoes filtro município", "veio licitação de outro município")

    datas = [l["data_realizacao_licitacao"] for l in corpo["data"] if l["data_realizacao_licitacao"]]
    v.checar(datas == sorted(datas, reverse=True), "/licitacoes ordenação por data desc",
             "datas fora de ordem")

    # itens e dotações da primeira licitação; número pode ter "/"
    for licitacao in corpo["data"][:3]:
        numero = licitacao["numero_licitacao"]
        numero_url = quote(numero, safe="")
        for sufixo in ("itens", "dotacoes"):
            caminho = f"/licitacoes/{municipio}/{numero_url}/{sufixo}"
            resposta = v.sessao.get(v.url_base + caminho, timeout=60)
            nome = f"GET /licitacoes/{{m}}/{{n}}/{sufixo} n='{numero}'"
            if resposta.status_code == 404 and "/" in numero:
                v._falha(nome, "404: número da licitação contém '/' e a rota não aceita "
                               "(precisa de {numero_licitacao:path} no api.py)")
            elif resposta.status_code != 200:
                v._falha(nome, f"status {resposta.status_code}")
            else:
                dados = resposta.json()["data"]
                v.checar(all(d["numero_licitacao"] == numero for d in dados), nome,
                         "veio registro de outra licitação", f"{len(dados)} registros")
                if sufixo == "itens" and dados:
                    soma = sum(Decimal(d["valor_vencedor"] or "0") for d in dados)
                    v.checar(soma == Decimal(licitacao["valor_total_licitacao"]) or len(dados) == 100,
                             f"{nome} soma itens = valor_total_licitacao",
                             f"soma itens {soma} x total {licitacao['valor_total_licitacao']}")

    # filtros e erros
    v.get("/licitacoes", {"codigo_natureza": "30", "limit": 5})
    v.get("/licitacoes", {"codigo_natureza": "99"}, esperado=400,
          nome="GET /licitacoes natureza inexistente")
    v.get("/licitacoes", {"data_inicio": "2026-12-31", "data_fim": "2026-01-01"}, esperado=400,
          nome="GET /licitacoes data_inicio > data_fim")
    v.get("/licitacoes", {"data_inicio": "31/12/2026"}, esperado=422,
          nome="GET /licitacoes data em formato errado")


def testar_empresas(v: Verificador, municipio: str) -> None:
    print("\n[4] Participações e empresas")
    corpo = v.get("/participacoes", {"codigo_municipio": municipio, "limit": 10})
    if corpo:
        v.paginacao(corpo, 10, 0, "/participacoes")
        if corpo["data"]:
            p = corpo["data"][0]
            v.campos(p, ["cnpj", "valor_total_vencido", "quantidade_itens_vencidos"], "/participacoes")
            filtrado = v.get("/participacoes", {"cnpj": p["cnpj"], "limit": 100})
            if filtrado:
                v.checar(all(x["cnpj"] == p["cnpj"] for x in filtrado["data"]),
                         "/participacoes filtro cnpj", "veio outro CNPJ")
    v.get("/participacoes", {"cnpj": "123"}, esperado=400, nome="GET /participacoes cnpj inválido")

    empresas = v.get("/empresas", {"limit": 10})
    if empresas:
        v.paginacao(empresas, 10, 0, "/empresas")
        if empresas["data"]:
            v.campos(empresas["data"][0], ["cnpj", "porte_empresa", "atualizado_em"], "/empresas")
            sem_porte = sum(1 for e in empresas["data"] if not e["porte_empresa"])
            if sem_porte:
                v._aviso("/empresas", f"{sem_porte}/10 empresas sem porte (enriquecimento pendente?)")


def testar_indicadores(v: Verificador, municipio: str, ano: int) -> None:
    print("\n[5] Indicadores de licitações")
    gastos = v.get("/analytics/gastos-por-natureza", {"codigo_municipio": municipio})
    if gastos:
        v.checar("observacao" in gastos and isinstance(gastos.get("data"), list),
                 "/analytics/gastos-por-natureza: formato", "sem data/observacao")

    me = v.get("/analytics/microempresas", {"codigo_municipio": municipio})
    if me:
        d = me["data"]
        v.checar(all(Decimal(str(d[k])) >= 0 for k in d), "/analytics/microempresas: não negativos",
                 f"valores negativos: {d}")

    por_nat = v.get("/analytics/participacao-me-por-natureza", {"codigo_municipio": municipio})
    if por_nat:
        for item in por_nat["data"]:
            nome = f"/participacao-me-por-natureza [{item['codigo_natureza']}]"
            v.percentuais(item, nome)
            v.parte_menor_que_total(item, [
                ("licitacoes_com_me", "quantidade_licitacoes"),
                ("participacoes_me", "quantidade_participacoes"),
                ("valor_vencido_me", "valor_vencido"),
                ("itens_vencidos_me", "itens_vencidos"),
            ], nome)

    det = v.get("/analytics/licitacoes-detalhadas", {"codigo_municipio": municipio, "limit": 10})
    if det:
        v.paginacao(det, 10, 0, "/analytics/licitacoes-detalhadas")

    v.get("/analytics/participacoes-me-locais", esperado=422,
          nome="GET /analytics/participacoes-me-locais sem município")
    for rota in ("participacoes-me-locais", "participacoes-me-gerais"):
        corpo = v.get(f"/analytics/{rota}", {"codigo_municipio": municipio, "limit": 10})
        if corpo:
            v.paginacao(corpo, 10, 0, f"/analytics/{rota}")
            v.percentuais(corpo["resumo"], f"/analytics/{rota} resumo")
            r = corpo["resumo"]
            sufixo = "_me_local" if rota.endswith("locais") else "_me"
            v.parte_menor_que_total(r, [
                (f"valor_vencido{sufixo}", "valor_total_vencido"),
            ], f"/analytics/{rota} resumo")
        v.get(f"/analytics/{rota}", {"codigo_municipio": municipio, "ano": ano, "limit": 5})

    # ME gerais incluem ME locais
    locais = v.sessao.get(v.url_base + "/analytics/participacoes-me-locais",
                          params={"codigo_municipio": municipio}, timeout=60)
    gerais = v.sessao.get(v.url_base + "/analytics/participacoes-me-gerais",
                          params={"codigo_municipio": municipio}, timeout=60)
    if locais.ok and gerais.ok:
        vl = Decimal(str(locais.json()["resumo"]["valor_vencido_me_local"]))
        vg = Decimal(str(gerais.json()["resumo"]["valor_vencido_me"]))
        v.checar(vl <= vg, "ME locais <= ME gerais", f"locais {vl} > gerais {vg}")


def testar_pagamentos(v: Verificador, municipio: str, ano: int) -> None:
    print("\n[6] Pagamentos por natureza e recorte ME")
    resposta = v.sessao.get(v.url_base + "/analytics/pagamentos/resumo",
                            params={"codigo_municipio": municipio, "ano": ano}, timeout=60)
    if resposta.status_code == 404:
        v._aviso("/analytics/pagamentos/resumo",
                 f"sem apuração para {municipio}/{ano}. Rode: python -m src.main pagamentos "
                 f"--municipio {municipio} --ano {ano}")
        return
    if resposta.status_code == 500:
        v._falha("/analytics/pagamentos/resumo",
                 "erro 500: provavelmente as tabelas de pagamentos não existem. Rode o "
                 "db/schema.sql novo no Supabase ou use --sem-pagamentos")
        return
    if not v.checar(resposta.status_code == 200, "GET /analytics/pagamentos/resumo",
                    f"status {resposta.status_code}: {resposta.text[:200]}"):
        return
    corpo = resposta.json()
    coleta = corpo["coleta"]
    print(f"         coleta {coleta['coleta_id']}: pagamentos "
          f"{coleta['referencia_pagamentos_inicio']}–{coleta['referencia_pagamentos_fim']}, "
          f"status {coleta['status']}, órgãos excluídos {coleta['orgaos_excluidos']}")

    v.checar(bool(coleta["reconciliado"]), "pagamentos: reconciliação gravada",
             "categorias de porte não batem com o total de controle")
    v.checar(bool(coleta["orgaos_excluidos"]), "pagamentos: Câmara excluída",
             "nenhum órgão excluído — o total pode incluir a Câmara")

    total_me = Decimal("0")
    for item in corpo["data"]:
        soma = sum(Decimal(x) for x in item["valores_por_categoria"].values())
        v.checar(soma == Decimal(item["total_controle"]),
                 f"pagamentos [{item['codigo_natureza']}] soma das categorias = total",
                 f"categorias {soma} x total {item['total_controle']}")
        v.checar(Decimal(item["valor_me"]) == Decimal(item["valores_por_categoria"]["ME"]),
                 f"pagamentos [{item['codigo_natureza']}] valor_me = categoria ME",
                 "divergente")
        total_me += Decimal(item["valor_me"])
    v.checar(total_me == Decimal(corpo["total_me"]), "pagamentos: total_me = soma das naturezas",
             f"{total_me} x {corpo['total_me']}")

    if corpo["pendencias"]:
        v._aviso("pagamentos: pendências",
                 ", ".join(f"{p['tipo']}={p['quantidade']}" for p in corpo["pendencias"]))

    # Conferência com o guia (só faz sentido no mesmo recorte)
    if municipio == "004" and ano == 2025:
        if coleta["referencia_pagamentos_fim"] != "202609":
            v._aviso("conferência Acaraú/2025",
                     f"coleta até {coleta['referencia_pagamentos_fim']}; o guia usa 202609")
        else:
            por_nat = {i["codigo_natureza"]: Decimal(i["total_controle"]) for i in corpo["data"]}
            for elemento, esperado in REFERENCIA_ACARAU_2025.items():
                calculado = por_nat.get(elemento, Decimal("0"))
                v.checar(calculado == esperado, f"conferência portal elemento {elemento}",
                         f"calculado {calculado} x portal {esperado} "
                         f"(diferença {calculado - esperado})",
                         f"R$ {calculado}")

    # fornecedores ME de cada natureza somam o valor_me
    for item in corpo["data"]:
        if Decimal(item["valor_me"]) == 0:
            continue
        forn = v.sessao.get(v.url_base + "/analytics/pagamentos/fornecedores",
                            params={"codigo_municipio": municipio, "ano": ano,
                                    "codigo_natureza": item["codigo_natureza"],
                                    "categoria_porte": "ME", "limit": 100}, timeout=60)
        if not forn.ok:
            v._falha("GET /analytics/pagamentos/fornecedores", f"status {forn.status_code}")
            continue
        dados = forn.json()["data"]
        if len(dados) == 100:
            continue  # mais de uma página: soma parcial não serve de prova
        soma = sum(Decimal(f["valor_pago"]) for f in dados)
        v.checar(soma == Decimal(item["valor_me"]),
                 f"fornecedores ME [{item['codigo_natureza']}] somam valor_me",
                 f"{soma} x {item['valor_me']}", f"{len(dados)} fornecedores")
        v.checar(all(f["categoria_porte"] == "ME" for f in dados),
                 f"fornecedores ME [{item['codigo_natureza']}] filtro categoria", "veio outra categoria")

    v.get("/analytics/pagamentos/fornecedores",
          {"codigo_municipio": municipio, "ano": ano, "categoria_porte": "XYZ"}, esperado=400,
          nome="GET /analytics/pagamentos/fornecedores categoria inválida")
    v.get("/analytics/pagamentos/resumo", {"codigo_municipio": "999", "ano": ano}, esperado=404,
          nome="GET /analytics/pagamentos/resumo município sem coleta")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica todos os endpoints da API Compra Livre")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--municipio", default="004", help="código TCE (3 dígitos)")
    parser.add_argument("--ano", type=int, default=2025)
    parser.add_argument(
        "--sem-pagamentos", action="store_true",
        help="pula o bloco 6 (use antes de rodar o schema.sql novo e o comando pagamentos)",
    )
    args = parser.parse_args()

    v = Verificador(args.url)
    print(f"Verificando {args.url} — município {args.municipio}, ano {args.ano}")
    if testar_sistema(v):
        testar_municipios(v, args.municipio)
        testar_licitacoes(v, args.municipio)
        testar_empresas(v, args.municipio)
        testar_indicadores(v, args.municipio, args.ano)
        if args.sem_pagamentos:
            print("\n[6] Pagamentos — pulado (--sem-pagamentos)")
        else:
            testar_pagamentos(v, args.municipio, args.ano)

    print("\n" + "=" * 70)
    print(f"RESULTADO: {v.ok} OK, {len(v.falhas)} FALHA(S), {len(v.avisos)} AVISO(S)")
    for falha in v.falhas:
        print(f"  FALHA  {falha}")
    for aviso in v.avisos:
        print(f"  AVISO  {aviso}")
    return 1 if v.falhas else 0


if __name__ == "__main__":
    sys.exit(main())