# Compra Livre — Pipeline de Dados

Pipeline de dados sobre licitações públicas dos municípios do Ceará, cruzando
TCE-CE, IBGE e OpenCNPJ, preparando uma base confiável para o dashboard do
time de Full Stack.

## 1. Objetivo

Construir e manter uma base PostgreSQL com licitações, itens licitados,
empresas participantes e o porte dessas empresas, permitindo identificar a
participação de micro e pequenas empresas (ME/EPP) nas compras públicas dos
municípios do Ceará.

O PNCP **não** é consumido por este pipeline — é responsabilidade do time de
Full Stack. O banco está desenhado para poder receber dados do PNCP depois,
se precisar, mas isso não é implementado aqui.

CATMAT/CATSER também não está implementado nesta versão: a documentação
oficial fornecida não define um endpoint de API confiável para consulta
programática (só uma ferramenta de busca web e uma planilha para download),
e a regra do projeto proíbe inventar contratos de API. Fica como ponto em
aberto — ver seção "Limitações" no final.

## 2. Arquitetura

```
IBGE (municípios do CE)
        │
        ▼
TCE-CE /licitantes_fornecedores_bens_servicos ──┐
        │                                        │ cruzamento por
        ▼                                        │ CNPJ + numero_licitacao
TCE-CE /itens_compoem_bens_servicos ─────────────┘
        │
        ▼
OpenCNPJ (enriquecimento, com cache em dim_empresa)
        │
        ▼
staging (Postgres) → fato_item_licitacao → fato_participacao_empresa → fato_licitacao
```

A ordem de agregação segue literalmente a regra da documentação: quando o
mesmo CNPJ aparece mais de uma vez na mesma licitação, os valores são
somados. Isso acontece em `fato_participacao_empresa`, calculada a partir de
`fato_item_licitacao` (ver `db/schema.sql`, comentários no topo, para a
justificativa completa da modelagem).

## 3. Fontes de dados

| Fonte | Uso | Paginação |
|---|---|---|
| TCE-CE `sim/dv_municipios` | lista de municípios do CE com o código próprio do TCE + código IBGE lado a lado | sim, 200/página |
| TCE-CE `sim/licitantes_fornecedores_bens_servicos` | empresas participantes | sim, 1000/página |
| TCE-CE `sim/itens_compoem_bens_servicos` | itens e valores das licitações | sim, 1000/página |
| TCE-CE `sim/dotacoes_utilizadas_contratacoes` | dotações e elementos de despesa associados às licitações | sim, 1000/página |
| OpenCNPJ | porte, CNAEs, situação cadastral das empresas | não (uma consulta por CNPJ) |

### Nota importante sobre `codigo_municipio`

A documentação original (PDF fornecido) descreve `codigo_municipio` como "o código
IBGE do município". Na prática, testando a API, isso está incorreto: o
parâmetro esperado — e o valor que a própria API devolve dentro de cada
registro — é o **código interno do TCE-CE, com 3 dígitos** (ex.: `010` =
Amontada), não o código IBGE de 7 dígitos.

O endpoint `sim/dv_municipios` (não documentado no PDF original, encontrado
testando a API diretamente) resolve isso: devolve `codigo_municipio` (TCE),
`codigo_municipio_ibge` e `codigo_municipio_geonames` juntos para os 184
municípios do Ceará. O pipeline usa esse endpoint como fonte de
`dim_municipio` e como fonte da lista de municípios a percorrer no full
load / incremental — **não usa mais a API do IBGE**.

Em todo o resto do banco (staging, fatos, `pipeline_execucoes`),
`codigo_municipio` é sempre o código do TCE-CE, nunca o IBGE. O código IBGE
fica guardado só como referência em `dim_municipio.codigo_ibge`.

Um registro do `dv_municipios` (`codigo_municipio = "001"`, `nome_municipio
= "T.C.M."`) tem `codigo_municipio_ibge = null` — não é um município real, é
um código administrativo interno do TCE, e é descartado automaticamente por
`TCEClient.listar_municipios()`.

## 4. Estrutura do projeto

```
src/
  config.py            configuração via .env, fail-fast se faltar algo
  db.py                conexão Postgres (psycopg2)
  clients/              um cliente HTTP por fonte externa (tce_client.py cobre
                        licitantes, itens E a lista de municípios; opencnpj_client.py)
  cache/                cache de CNPJ (usa a própria dim_empresa)
  transform/             normalização, deduplicação
  quality/               validação antes da carga
  enrich/                orquestra cache + OpenCNPJ
  load/                  upserts em staging, dimensões e fatos
  pipeline/               orquestração: control (retomada), full_load, incremental
  main.py                 ponto de entrada (CLI)
db/
  schema.sql              DDL completo, comentado
tests/                     testes unitários (sem depender de rede ou banco)
```

## 5. Configuração

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edite `.env` e coloque a `DATABASE_URL` do seu projeto Supabase (Project
Settings > Database > Connection string).

## 6. Instalação do banco

Rode o `db/schema.sql` no seu projeto Supabase (SQL Editor do painel, ou via
`psql "$DATABASE_URL" -f db/schema.sql`). Ele cria o schema `compra_livre` e
todas as tabelas. Pode ser rodado de novo sem erro — todos os `CREATE TABLE`
usam `IF NOT EXISTS`.

## 7. Execução

**Full load** (primeira carga ou substituição completa dos fatos de cada
município processado):

```bash
python -m src.main full --data-inicio 2026-01-01 --data-fim 2026-08-20
```

Processa todos os municípios do Ceará no período informado.

**Incremental** (carga do dia a dia, pensada para rodar agendada):

```bash
python -m src.main incremental --dias 7
```

Para coletar apenas as dotações das licitações já existentes, sem repetir a
carga de itens, participantes ou empresas:

```bash
python -m src.main dotacoes --data-inicio 2025-01-01 --data-fim 2026-12-31
```

A carga grava uma linha por dotação em `fato_dotacao_licitacao`. Os sete
códigos de natureza cadastrados ficam em `dim_natureza_despesa`; códigos de
elemento que não pertencem a esse catálogo são preservados com
`codigo_natureza` nulo.

Carrega só os últimos N dias (padrão 7).

O full load limpa os fatos do município somente depois que a extração daquele
município termina e então os reconstrói a partir do período solicitado. O
incremental preserva o histórico e faz upsert apenas dos dados da janela.
O staging é limpo após a transformação; ele não é um arquivo histórico bruto.

### Retomada automática

Se o processo cair no meio de uma paginação (queda de rede, etc.), a tabela
`pipeline_execucoes` guarda o último `start_index` associado à mesma fonte,
modo, município e período. Uma carga com outro período nunca reutiliza
acidentalmente esse checkpoint.

## 8. Cache de CNPJ

Antes de consultar o OpenCNPJ, o pipeline verifica se aquele CNPJ já está em
`dim_empresa` com uma consulta válida (dentro de `OPENCNPJ_DIAS_VALIDADE_CACHE`
dias, padrão 30). Só os CNPJs sem cache válido são de fato consultados —
reduz chamadas repetidas à API externa.

O OpenCNPJ pode responder HTTP 429 com um `Retry-After` longo. Nesse caso o
pipeline não fica bloqueado aguardando dezenas de minutos: registra os CNPJs
afetados e termina o enriquecimento do lote. Depois do período indicado pela
API, execute novamente o comando; os CNPJs ainda não enriquecidos continuarão
pendentes. Evite executar duas cargas simultâneas, pois isso aumenta o
rate limit.

## 9. Testes

```bash
pytest -q
```

Os testes cobrem normalização, validação de qualidade e a lógica de
paginação/retomada do cliente TCE-CE (com HTTP mockado — não fazem chamada
real). Não há testes de integração contra o Postgres real neste pacote,
porque o ambiente onde este projeto foi gerado não tem acesso de rede ao
Supabase — ver "Limitações".

## 10. Qualidade dos dados

Antes da carga, `src/quality/validators.py` separa registros inválidos
(município ausente, CNPJ com tamanho errado, valor negativo etc.) dos
válidos. Um registro problemático não trava o restante da carga — ele é
descartado e reportado via log de warning, com o motivo.

## 11. CATMAT/CATSER

Não implementado nesta versão — ver seção 1 e "Limitações".

## 12. Troubleshooting

- **`RuntimeError: Variável de ambiente obrigatória 'DATABASE_URL' não foi definida`**: falta o `.env` ou a variável dentro dele.
- **Erro de conexão com o Supabase**: confira se a connection string usa a porta certa (5432 direta ou 6543 pooler) e se o IP da sua máquina está liberado, se o projeto Supabase tiver restrição de rede.
- **`TCEClienteError`**: a API do TCE-CE não respondeu depois de 3 tentativas. O pipeline já registra o erro em `pipeline_execucoes` e segue para o próximo município; rode o incremental depois para tentar de novo esse município específico.
- **Município sem nenhum dado**: normal — nem todo município tem licitação no período informado. Confira em `pipeline_execucoes` se a execução daquele município terminou com `status = 'concluido'`.

## 13. Integração com o Full Stack

As tabelas prontas para consumo são:
- `fato_licitacao`: uma linha por licitação, com valor total e contagem de empresas/itens.
- `dim_natureza_despesa`: catálogo extensível das naturezas de despesa.
- `fato_dotacao_licitacao`: dotações do TCE relacionadas por município e número da licitação.
- `fato_participacao_empresa`: uma linha por empresa por licitação, já com o valor somado — a mais indicada para as análises de ME/EPP.
- `dim_empresa`: dados cadastrais e porte de cada empresa.
- `dim_municipio`: nome e UF de cada município.

Todas ficam no schema `compra_livre` do Postgres do Supabase.

## 14. API para o Full Stack

O projeto também disponibiliza uma API somente leitura sobre essas tabelas.
A `DATABASE_URL` permanece apenas no servidor da API; ela nunca deve ser
enviada ao frontend.

Instalação e execução local:

```powershell
pip install -r requirements.txt
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

Documentação interativa:

```text
http://localhost:8000/docs
```

Durante o desenvolvimento, CORS permite requisições GET de frontends em
`localhost:3000` e `localhost:5173`. Em produção, altere essa lista para o
domínio real do dashboard antes de publicar a API.

Endpoints principais:

```text
GET /health
GET /municipios
GET /licitacoes
GET /licitacoes/{codigo_municipio}/{numero_licitacao}/itens
GET /licitacoes/{codigo_municipio}/{numero_licitacao}/dotacoes
GET /participacoes
GET /empresas
GET /analytics/gastos-por-natureza
GET /analytics/microempresas
GET /analytics/participacao-me-por-natureza
GET /analytics/licitacoes-detalhadas
GET /analytics/participacoes-me-locais
```

O endpoint `GET /licitacoes` aceita `codigo_natureza` para filtrar
diretamente no banco. Por exemplo:

```text
GET /licitacoes?codigo_natureza=30
```

Todos os endpoints de listagem têm paginação por `limit` e `offset`. Os
filtros disponíveis estão descritos automaticamente no `/docs`.

Indicadores analíticos:

- `/analytics/gastos-por-natureza` soma `valor_dotacao_doc` por natureza,
  município e período. Esse valor é uma dotação associada à contratação,
  não representa pagamento efetivo.
- `/analytics/microempresas` consolida empresas ME vencedoras, licitações,
  participações, itens e valor vencido, com filtros de município, natureza e
  período.
- `/analytics/participacao-me-por-natureza` retorna os percentuais de
  licitações, participações, valor vencido e itens vencidos relacionados a
  microempresas. Como o TCE não relaciona diretamente cada item à natureza,
  a associação é feita no nível da licitação; uma licitação com várias
  naturezas aparece em cada natureza relacionada.
- `/analytics/licitacoes-detalhadas` combina licitação, valor, ano, natureza,
  empresa vencedora, porte, CNPJ e endereço disponível no staging. Use
  `codigo_municipio`, `codigo_natureza` e `ano` como filtros.
- `/analytics/participacoes-me-locais` exige `codigo_municipio` e retorna
  microempresas vencedoras cujo município cadastral coincide com o município
  consultado. O endereço fica nulo quando não foi preservado no staging.

## Limitações conhecidas

- **CATMAT/CATSER não implementado** — falta um contrato de API oficial confirmado. Ver seção 1.
- **Sem testes de integração reais contra o Supabase** — o ambiente onde este código foi gerado só tem acesso de rede a repositórios de pacotes (PyPI, npm, GitHub), não ao Supabase nem às APIs do TCE-CE/OpenCNPJ. O código foi revisado, testado unitariamente (com mocks) e teve todos os módulos importados sem erro, mas a validação final contra as APIs e o banco reais só foi possível rodando do ambiente do Levi (foi assim, inclusive, que a questão do `codigo_municipio` — ver seção 3 — foi descoberta e corrigida).
- **Datas do TCE-CE**: a documentação não fixa o formato exato de `data_realizacao_licitacao` nos retornos. `normalize.py` tenta os formatos mais comuns (ISO e dd/mm/aaaa); se a API usar outro formato, o campo fica `NULL` em vez de quebrar a carga — vale conferir os logs de warning na primeira execução real.
- **`sim/dv_municipios` não é documentado oficialmente** — foi encontrado testando a API e confirmado com dados reais (retornou os 184 municípios do Ceará corretamente), mas, como não está no PDF de documentação original, pode mudar de comportamento sem aviso. Se ele parar de funcionar, o pipeline vai falhar logo no início (na carga de `dim_municipio`), o que é mais seguro do que falhar silenciosamente como acontecia antes com o código IBGE errado.
