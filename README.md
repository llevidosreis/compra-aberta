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
| TCE-CE `sim/orgaos` | órgãos do município/exercício (identifica a Câmara) — fluxo de pagamentos | não |
| TCE-CE `sim/notas_empenhos` | empenhos: credor, CNPJ/CPF, elemento de despesa — fluxo de pagamentos | sim, 1000/página, mês a mês |
| TCE-CE `sim/notas_pagamentos` | notas de pagamento: valor pago, estorno — fluxo de pagamentos | sim, 1000/página, mês a mês |

Para descobrir o código TCE de um município pelo navegador, abra
`https://api-dados-abertos.tce.ce.gov.br/sim/municipios` e busque o nome
(Ctrl+F). `dv_municipios` é o nome que aparece **dentro** da resposta (o
mesmo padrão das outras rotas: `/orgaos` responde `dv_orgaos`). Exemplos
conferidos: `004` Acaraú, `010` Amontada, `083` Itapajé, `084` Itapipoca.

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
                       (exigir_banco=False para o modo --sem-banco)
  db.py                conexão Postgres (psycopg2)
  api.py               API somente leitura (FastAPI)
  main.py              ponto de entrada (CLI): full, incremental, dotacoes, pagamentos
  clients/
    tce_client.py            licitantes, itens e lista de municípios (licitações)
    opencnpj_client.py       enriquecimento de empresas (licitações)
    tce_despesas_client.py   [pagamentos] órgãos, empenhos e pagamentos, mês a mês,
                             com releitura do mês quando a paginação do TCE é instável
  cache/                cache de CNPJ (usa a própria dim_empresa)
  transform/
    normalize.py, deduplicate.py, naturezas.py   (licitações)
    despesas.py              [pagamentos] normalização, elemento (5ª-6ª posições),
                             validação CPF/CNPJ, normalização do porte
    cruzamento.py            [pagamentos] pagamento x empenho pela chave de 6 campos
    apuracao_pagamentos.py   [pagamentos] Câmara, filtros, total de controle,
                             categorias de porte, reconciliação
  quality/
    validators.py            validação antes da carga (licitações)
    conferencia_despesas.py  [pagamentos] valores de referência do guia (Acaraú/2025)
  enrich/
    enrich_empresas.py       orquestra cache + OpenCNPJ (licitações)
    porte_fornecedores.py    [pagamentos] porte via OpenCNPJ, com cache
  load/
    load_dimensoes.py, load_staging.py, load_fatos.py, load_dotacoes.py
    load_despesas.py         [pagamentos] gravação das tabelas de pagamentos
  pipeline/
    control.py, full_load.py, incremental.py, dotacoes.py, extract_common.py
    despesas_me.py           [pagamentos] orquestra os 12 passos do guia
db/
  schema.sql                 DDL completo, comentado (inclui as tabelas de pagamentos)
  dados_exemplo.sql          dados FICTÍCIOS para testar a API num banco vazio
  remover_dados_exemplo.sql  apaga só os dados fictícios
scripts/
  verificar_api.py           testa todos os endpoints com a API no ar
  comparar_com_portal.py     compara as 7 naturezas com o portal do TCE
  comparar_fornecedores.py   compara fornecedor a fornecedor (localmente)
tests/                       testes unitários (sem depender de rede ou banco)
  test_pagamentos_me.py      [pagamentos] 29 testes, incluindo paginação instável
saidas/                      relatórios JSON/CSV gerados (não versionar)
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

No SQL Editor do Supabase, crie a query pelo **+** (seção PRIVATE). Queries
criadas na seção LOGS rodam em outro motor e dão erro ("Only singular query
allowed"). Se o Supabase avisar sobre RLS, "Run and enable RLS" é seguro: a
API conecta como dona das tabelas.

**Banco vazio para testar a API?** `db/dados_exemplo.sql` insere licitações,
itens, participações e empresas **fictícias** (todas com "EXEMPLO" no nome).
Nunca rode no banco de produção. Para remover depois:
`db/remover_dados_exemplo.sql`.

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
real).

`tests/test_pagamentos_me.py` cobre o fluxo de pagamentos com o caso de
Consultoria de Acaraú (R$ 1.083.280,71 / ME R$ 881.880,06): referências
mensais, paginação, falha da API que não vira mês vazio, paginação instável
(registros repetidos/perdidos entre páginas), chave completa, Câmara,
estornos, elemento, CPF/CNPJ, porte, regra MEI, reconciliação e o pipeline
completo no modo `--sem-banco`.

**Teste da API com servidor e banco reais:** com `uvicorn src.api:app` no ar,
em outro terminal:

```bash
python scripts/verificar_api.py --municipio 004 --ano 2025
python scripts/verificar_api.py --sem-pagamentos      # sem as tabelas de pagamentos
```

Verifica todos os endpoints (status, formato, paginação, filtros, erros 400/
404/422, percentuais, soma das categorias = total) e, para Acaraú/2025,
compara os pagamentos com o portal. Termina com código 1 se houver FALHA.
AVISO não é erro (ex.: tabela vazia, pendências conhecidas). Não há testes de integração contra o Postgres real neste pacote,
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
- **`/health` responde 503 "Banco de dados indisponível"**: a API não conecta. Para ver o erro real: `python -c "from src.config import carregar_config; import psycopg2; psycopg2.connect(carregar_config().database_url); print('CONEXAO OK')"`. A API só lê o `.env` ao iniciar: reinicie o `uvicorn` depois de editar.
- **`password authentication failed for user "postgres"`**: no Session pooler o usuário é `postgres.<código-do-projeto>`, não só `postgres`; confira também a senha (sem colchetes) e se o endereço é do SEU projeto (não o do `.env.example`).
- **`invalid dsn: invalid percent-encoded token`**: a senha tem `%`, `#`, `@`, `/`, `:`, `?` ou acento. Use senha só com letras e números (Supabase: Project Settings > Database > Reset database password).
- **`could not translate host name`**: endereço de "Direct connection" (IPv6). Use o "Session pooler" (botão Connect do Supabase).
- **`Python não foi encontrado` (Windows)**: use `py` em vez de `python` para criar o ambiente (`py -m venv .venv`) e ative com `.\.venv\Scripts\Activate.ps1`. Se o PowerShell bloquear scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- **SQL colado no terminal (`Select-Object : Não é possível localizar um parâmetro`)**: comandos `SELECT`/`CREATE` vão no SQL Editor do Supabase, não no PowerShell.
- **`ModuleNotFoundError: No module named 'src.clients.tce_client'`**: o `tce_client.py` original foi sobrescrito ou apagado (não confundir com `tce_despesas_client.py`). Recupere com `git restore src/clients/tce_client.py`.
- **Script termina sem imprimir nada**: o arquivo `.py` está vazio (conteúdo não colado ou não salvo).
- **`verificar_api.py`: "Nenhuma conexão pôde ser feita"**: a API não está no ar. Rode `uvicorn src.api:app --reload` em outro terminal e deixe aberto.
- **404 em `/licitacoes/{m}/{n}/itens` com número contendo "/"**: a rota precisa de `{numero_licitacao:path}` (já corrigido em `api.py`).
- **Pagamentos divergem do portal**: veja o checklist da seção 15 (ano da página, `--pagamentos-ate`, aba, código da natureza).
- **`coleta_possivelmente_incompleta` nas pendências**: um mês da API do TCE não estabilizou nem com releituras. Rode a coleta de novo e compare com o portal.

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
GET /analytics/participacoes-me-gerais
GET /analytics/pagamentos/resumo          (seção 15)
GET /analytics/pagamentos/fornecedores    (seção 15)
```

O número da licitação pode conter "/" (ex.: `2111.01/2024 PE`). Codifique-o
na URL (`2111.01%2F2024%20PE`); as rotas de itens e dotações usam
`{numero_licitacao:path}` para aceitá-lo.

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
  consultado. Além da lista, `resumo` retorna os percentuais de licitações,
  participações, valor vencido e itens vencidos de ME locais sobre os totais
  do município. Aceita `codigo_natureza` para restringir a análise às
  licitações daquela natureza. O endereço fica nulo quando não foi preservado
  no staging.
- `/analytics/participacoes-me-gerais` retorna todas as microempresas
  vencedoras em licitações do município, incluindo empresas de outros
  municípios, com os mesmos percentuais sobre o total filtrado. Aceita
  `codigo_natureza` e `ano`.

## 15. Pagamentos da Prefeitura por natureza e recorte de Microempresas

Implementa o guia "Como reproduzir os valores do Portal da Transparência do
TCE" (caso de referência: Acaraú/2025). Fluxo:

```
órgãos + empenhos + pagamentos (TCE, mês a mês, paginado)
  -> cruzamento pela chave completa de 6 campos
  -> Prefeitura (Câmara excluída pelo cadastro) + 7 elementos + não estornados
  -> TOTAL DE CONTROLE (todos os portes) — conferido com o portal
  -> CNPJs únicos -> porte no OpenCNPJ (cache)
  -> categoria por pagamento -> subtotal ME (porte 01) + reconciliação
```

### Validação com dados reais (21/10/2026)

Comparado com a aba Prefeitura do Portal da Transparência dos Municípios:

| Município | Ano | Pagamentos até | Resultado |
|---|---|---|---|
| Acaraú (004) | 2025 | 202609 | 7/7 naturezas ao centavo, iguais ao guia; ME em Consultoria R$ 881.880,06 |
| Amontada (010) | 2025 | 202609 | 7/7 ao centavo (R$ 100.182.902,37) |
| Itapipoca (084) | 2026 | 202609 | 7/7 ao centavo (R$ 299.158.895,20) |

Ao reproduzir, o resultado só é comparável com o portal na mesma data de
atualização (os valores mudam quando o TCE recebe novos dados).

### Paginação instável da API do TCE

Na prática, a API do TCE às vezes devolve registros repetidos entre páginas
e, no lugar deles, deixa de devolver outros (ex.: Amontada/2025 trazia
sempre 12.306 registros, com 45 a 503 repetidos por coleta; os totais mudavam
a cada execução). Por isso `tce_despesas_client.py` relê cada mês e une as
leituras pelo conteúdo completo do registro até:

- a união atingir o total por leitura (completo); ou
- 2 releituras seguidas não trazerem nada novo (estável; a diferença que
  sobra é registrada como `repeticao_estavel`, indistinguível de registro
  gravado duas vezes na origem); ou
- 8 leituras (`MAX_PASSADAS`) sem estabilizar → pendência
  `coleta_possivelmente_incompleta`.

Meses estáveis são lidos uma única vez. O log mostra `passadas=N` por mês.

### Reproduzir a conferência de Acaraú/2025

Sem gravar no banco (só relatório JSON em `saidas/`):

```bash
python -m src.main pagamentos --municipio 004 --ano 2025 --pagamentos-ate 202609 --sem-banco
```

Gravando no Postgres (rode antes o `db/schema.sql` atualizado):

```bash
python -m src.main pagamentos --municipio 004 --ano 2025 --pagamentos-ate 202609
```

O log mostra a tabela por natureza (total de controle x ME), os órgãos
excluídos e as pendências. Quando o recorte é Acaraú/2025, o pipeline
compara automaticamente com os valores do guia (R$ 135.311.529,44 no total;
Consultoria R$ 1.083.280,71 por fornecedor).

| Opção | Padrão | Uso |
|---|---|---|
| `--municipio` | — | código TCE de 3 dígitos; aceita vários |
| `--ano` | — | ano do orçamento (2025 vira `exercicio_orcamento=202500`) |
| `--empenhos-de/--empenhos-ate` | ANO01 / ANO12 | referências dos empenhos |
| `--pagamentos-de/--pagamentos-ate` | ANO01 / mês anterior ao atual | referências dos pagamentos (podem entrar no ano seguinte) |
| `--regra-mei` | `incluir` | `excluir` = ME exceto MEI; exige `opcao_mei = N` confirmada |
| `--referencia-base-cadastral` | `NAO_INFORMADA` | edição da base cadastral, se conhecida |
| `--excluir-orgao` | — | exclui outro órgão além da Câmara detectada |
| `--sem-banco` | desligado | não exige `DATABASE_URL` |

### ⚠️ Antes de comparar com o portal: checklist obrigatório

A maior parte das "divergências" encontradas na validação NÃO eram erros de
cálculo, e sim comparações feitas com recortes diferentes. Confira sempre:

1. **Ano no endereço do portal.** A página precisa terminar em
   `/mun/XXX/versao/AAAA` com o MESMO ano do `--ano`. O título da página não
   mostra o ano. Comparar 2025 com a página de 2026 faz quase todas as
   naturezas divergirem, umas para mais e outras para menos (caso real:
   Itapipoca).
2. **`--pagamentos-ate` = mês da "Última atualização" do portal** (rodapé da
   página). Ex.: atualização em 27/09/2026 → `--pagamentos-ate 202609`.
   Coletar menos meses que o portal deixa TODAS as naturezas abaixo, numa
   proporção parecida (caso real: Itapajé coletado até 202607 → ~13% a
   menos, exatamente o mês de agosto). Mês ainda não enviado ao TCE volta
   vazio e não atrapalha.
   Sem `--pagamentos-ate`, o padrão é o mês anterior ao atual.
3. **Aba Prefeitura**, nunca Câmara de Vereadores.
4. **Código da natureza** na coluna "Cód. do Tipo da Despesa" (39, 30, 51,
   52, 32, 40, 35). Há nomes parecidos (ex.: 39 Pessoa Jurídica x 36
   Pessoa Física). Natureza que não aparece no portal = R$ 0,00 (Enter no
   `comparar_com_portal.py`).

Como ler o padrão da divergência:

| Padrão | Causa mais provável |
|---|---|
| Todas abaixo, proporção parecida | Faltam meses: aumente `--pagamentos-ate` |
| Umas acima e outras abaixo | Página do portal de outro ano |
| Todas acima | Órgão a mais (ex.: Câmara não reconhecida); use `--testar-exclusoes` |
| Valores mudam a cada execução | Instabilidade da API do TCE (o cliente já relê os meses; veja pendências) |

### Ferramentas de comparação

```bash
# 7 naturezas: digita os valores do portal, mostra OK/DIVERGE e o padrão
python scripts/comparar_com_portal.py --municipio 010 --ano 2025

# lista os órgãos (incluídos/excluídos) e o total de cada um por natureza
python scripts/comparar_com_portal.py --municipio 010 --ano 2025 --orgaos

# procura a regra: combinações de excluir órgãos (tipo != 00/03), cortar
# meses e contar estornos, e mostra a que mais se aproxima do portal
python scripts/comparar_com_portal.py --municipio 010 --ano 2025 --testar-exclusoes

# lista os fornecedores de uma natureza
python scripts/comparar_com_portal.py --municipio 010 --ano 2025 --fornecedores 35

# fornecedor a fornecedor contra a tabela de favorecidos copiada do portal
# (roda local; imprime um resumo SEM dados pessoais, próprio para compartilhar)
python scripts/comparar_fornecedores.py --municipio 010 --ano 2025 --natureza 35 --arquivo saidas/portal_35.txt
```

Cada comparação usa o relatório JSON mais recente de `saidas/` e grava um CSV
ao lado. Os relatórios incluem `totais_por_orgao`, `diagnostico_agregado`
(órgão x mês x natureza x estorno) e o resultado de cada mês da coleta.
`saidas/` contém nomes e documentos de credores: não versionar nem publicar.

### Regras implementadas (resumo)

- Medida financeira: `valor_nota_pagamento` com `estado_de_estornado = 0`.
  `valor_empenhado` é guardado só para conferência.
- Câmara: identificada a cada coleta pelo cadastro `/orgaos`
  (`codigo_tipo_unidade = 02` ou nome com "Câmara"); nunca fixada como `01`.
- Elemento: 5ª e 6ª posições do código de 8 dígitos (`33903500` -> `35`).
- Falha de rede ou HTTP de erro interrompe a coleta; nunca vira mês vazio.
  Página repetida (paginação sem avanço) também interrompe.
- Duplicação idêntica é descartada; duplicação conflitante, pagamento sem
  empenho, órgão fora do cadastro e estado de estorno desconhecido viram
  pendências e ficam fora da soma.
- CNPJ x CPF por dígito verificador (aceita CNPJ alfanumérico); documento
  inválido não é corrigido.
- Porte do OpenCNPJ (`porte_empresa`, texto) normalizado para o código da
  Receita: `01` ME, `03` EPP, `05` Demais, `00` Não informado. Não
  encontrado, falha ou porte desconhecido ficam `SEM_CLASSIFICACAO`.
  Nome com "ME" e opção pelo Simples nunca substituem o porte.
- Cada pagamento cai em uma única categoria (`ME`, `EPP`, `DEMAIS`,
  `PORTE_NAO_INFORMADO`, `SEM_CLASSIFICACAO`, `ME_EXCLUIDA_REGRA_MEI`,
  `FAVORECIDO_CPF`, `DOCUMENTO_INVALIDO_OU_AUSENTE`); a soma das categorias
  precisa bater com o total de controle (`reconciliado`).

### Tabelas novas

`dim_orgao_tce`, `fato_empenho_despesa`, `fato_pagamento_despesa`,
`cadastro_porte_fornecedor`, `coleta_pagamentos`,
`resumo_pagamento_categoria`, `resumo_pagamento_fornecedor`,
`pendencia_coleta_pagamentos`.

### Endpoints novos

```text
GET /analytics/pagamentos/resumo?codigo_municipio=004&ano=2025
GET /analytics/pagamentos/fornecedores?codigo_municipio=004&ano=2025&codigo_natureza=35&categoria_porte=ME
```

`resumo` devolve, por natureza, o total de controle, o valor de cada
categoria de porte, o subtotal ME e a lista de pendências, além do período
de pagamentos e da referência cadastral, para a tela deixar isso explícito.

### Atenção

- O porte consultado hoje não comprova o porte em 2025. Para comparar com o
  relatório do consultor é preciso a mesma referência cadastral.
- O relatório do consultor fala em "despesas empenhadas", mas o portal mostra
  "Valor Pago". Este fluxo calcula **valor pago**.
- Pagamentos a pessoas com CPF inválido ou mascarado no TCE aparecem como
  pendência `documento_credor_invalido`: entram no total de controle e ficam
  fora do subtotal ME (categoria `DOCUMENTO_INVALIDO_OU_AUSENTE`).
- Rodar o mesmo município/ano de novo substitui empenhos e pagamentos no
  banco (sem duplicar); cada execução fica registrada em `coleta_pagamentos`
  e a API usa a mais recente.
- Municípios com órgãos de tipos diferentes (autarquia 05, previdência 07,
  procuradoria 10, reservas 99 — ex.: Itapipoca) foram validados com a regra
  atual (só a Câmara fica fora). Se um município novo divergir "para mais",
  use `--testar-exclusoes` antes de mudar a regra.

## Limitações conhecidas

- **CATMAT/CATSER não implementado** — falta um contrato de API oficial confirmado. Ver seção 1.
- **Porte é o atual, não o de 2025** — o OpenCNPJ informa o porte de hoje e não a edição da base; a referência fica `NAO_INFORMADA`. Para comparação histórica do subtotal ME é preciso a mesma referência cadastral do levantamento original.
- **Paginação do TCE** — mitigada com releituras (seção 15). Um registro que a API nunca devolve, mas troca por cópia de outro, é indistinguível de duplicação na origem; a garantia final é a conferência com o portal.
- **Sem testes de integração reais contra o Supabase** — o ambiente onde este código foi gerado só tem acesso de rede a repositórios de pacotes (PyPI, npm, GitHub), não ao Supabase nem às APIs do TCE-CE/OpenCNPJ. O código foi revisado, testado unitariamente (com mocks) e teve todos os módulos importados sem erro, mas a validação final contra as APIs e o banco reais só foi possível rodando do ambiente do Levi (foi assim, inclusive, que a questão do `codigo_municipio` — ver seção 3 — foi descoberta e corrigida).
- **Datas do TCE-CE**: a documentação não fixa o formato exato de `data_realizacao_licitacao` nos retornos. `normalize.py` tenta os formatos mais comuns (ISO e dd/mm/aaaa); se a API usar outro formato, o campo fica `NULL` em vez de quebrar a carga — vale conferir os logs de warning na primeira execução real.
- **`sim/dv_municipios` não é documentado oficialmente** — foi encontrado testando a API e confirmado com dados reais (retornou os 184 municípios do Ceará corretamente), mas, como não está no PDF de documentação original, pode mudar de comportamento sem aviso. Se ele parar de funcionar, o pipeline vai falhar logo no início (na carga de `dim_municipio`), o que é mais seguro do que falhar silenciosamente como acontecia antes com o código IBGE errado.