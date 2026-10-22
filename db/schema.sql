-- ============================================================================
-- Compra Livre — schema PostgreSQL (Supabase)
--
-- Decisões de modelagem:
--
-- 1. dim_municipio e dim_empresa são dimensões clássicas: dados que mudam
--    pouco e são reaproveitados por muitas licitações/itens.
--
-- 2. A API do TCE-CE entrega dois níveis de granularidade diferentes:
--       - itens_compoem_bens_servicos -> nível de ITEM (numero_sequencial_item_licitacao)
--       - a regra de agregação do documento (somar valor_vencedor_item_licitacao
--         por CNPJ + numero_licitacao) é uma pergunta de negócio diferente:
--         "quanto essa empresa faturou nessa licitação", não "quanto vale esse item".
--    Por isso existem duas tabelas fato separadas em vez de uma só:
--       - fato_item_licitacao: granularidade de item (não perde informação).
--       - fato_participacao_empresa: já agregada por CNPJ + licitação,
--         exatamente como a documentação manda somar. É essa tabela que
--         alimenta as análises de ME/EPP e o dashboard, sem precisar
--         re-somar toda vez.
--
-- 3. fato_licitacao guarda o "cabeçalho" da licitação (1 linha por
--    codigo_municipio + numero_licitacao), com o valor total consolidado
--    de todas as empresas. Facilita contagens e listagens do Full Stack
--    sem precisar agregar em tempo de consulta.
--
-- 4. stg_* (staging) recebe o dado bruto da API antes de qualquer
--    transformação, servindo de base para reprocessamento sem precisar
--    chamar a API de novo, e para auditoria em caso de divergência.
--
-- 5. pipeline_execucoes controla a paginação (start_index de retomada) e
--    o status de cada execução, permitindo retomar uma carga interrompida
--    sem duplicar nem perder registros — obrigatório dado o limite de
--    1000 registros por chamada da API do TCE-CE.
--
-- 6. Todas as tabelas fato/staging têm codigo_municipio como parte da
--    chave, nunca só numero_licitacao: o número da licitação não é único
--    globalmente, só dentro do município que a realizou.
--
-- 7. codigo_municipio, em TODO o banco, é o código próprio do TCE-CE
--    (3 dígitos, ex: "010"), NÃO o código IBGE de 7 dígitos. É esse
--    valor que a API do TCE-CE espera como parâmetro e que ela devolve
--    dentro de cada registro (campo "codigo_municipio" no JSON) — então
--    usar o mesmo valor em todo o banco evita qualquer tradução na hora
--    de gravar. A tradução entre os dois códigos é feita uma única vez,
--    na carga de dim_municipio, a partir do endpoint oficial do TCE-CE
--    que já entrega os dois lado a lado (/sim/dv_municipios).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS compra_livre;
SET search_path TO compra_livre, public;

-- ----------------------------------------------------------------------------
-- DIMENSÕES
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_municipio (
    codigo_municipio    TEXT PRIMARY KEY,       -- código do TCE-CE (3 dígitos, ex: "010"), usado em todo o banco
    nome                TEXT NOT NULL,
    uf                  CHAR(2) NOT NULL DEFAULT 'CE',
    codigo_ibge         TEXT,                    -- código IBGE de 7 dígitos, guardado só como referência/enriquecimento
    codigo_geonames     TEXT,
    atualizado_em       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dim_empresa (
    cnpj                    TEXT PRIMARY KEY,           -- numero_documento_negociante, sem máscara
    razao_social             TEXT,
    nome_fantasia             TEXT,
    situacao_cadastral       TEXT,
    data_inicio_atividade    DATE,
    cnae_principal            TEXT,
    cnaes_secundarios        JSONB,                     -- lista de códigos, formato livre do OpenCNPJ
    porte_empresa             TEXT,                      -- ME, EPP, DEMAIS etc.
    uf_empresa                CHAR(2),
    municipio_empresa        TEXT,
    -- controle do enriquecimento / cache:
    enriquecido               BOOLEAN NOT NULL DEFAULT FALSE,
    encontrado_opencnpj      BOOLEAN,                    -- NULL = nunca consultado, FALSE = consultado e não encontrado
    consultado_em             TIMESTAMPTZ,
    criado_em                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dim_empresa_porte ON dim_empresa (porte_empresa);

-- Catálogo extensível das naturezas informadas pelo elemento de despesa.
CREATE TABLE IF NOT EXISTS dim_natureza_despesa (
    codigo_natureza TEXT PRIMARY KEY,
    nome_natureza   TEXT NOT NULL,
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO dim_natureza_despesa (codigo_natureza, nome_natureza)
VALUES
    ('39', 'OUTROS SERVIÇOS DE TERCEIROS – PESSOA JURÍDICA'),
    ('51', 'OBRAS E INSTALAÇÕES'),
    ('30', 'MATERIAL DE CONSUMO'),
    ('52', 'EQUIPAMENTOS E MATERIAL PERMANENTE'),
    ('32', 'MATERIAL, BEM OU SERVIÇO PARA DISTRIBUIÇÃO GRATUITA'),
    ('35', 'SERVIÇOS DE CONSULTORIA'),
    ('40', 'SERVIÇOS DE TECNOLOGIA DA INFORMAÇÃO E COMUNICAÇÃO – PESSOA JURÍDICA')
ON CONFLICT (codigo_natureza) DO UPDATE
SET nome_natureza = EXCLUDED.nome_natureza, atualizado_em = now();

-- ----------------------------------------------------------------------------
-- STAGING (dado bruto, pré-transformação)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_licitante (
    id                          BIGSERIAL PRIMARY KEY,
    codigo_municipio            TEXT NOT NULL,
    data_realizacao_licitacao   DATE,
    numero_licitacao            TEXT NOT NULL,
    numero_documento_negociante TEXT NOT NULL,
    codigo_tipo_negociante       TEXT,
    nome_negociante              TEXT,
    endereco_negociante          TEXT,
    fone_negociante               TEXT,
    cep_negociante                 TEXT,
    nome_municipio_negociante    TEXT,
    codigo_uf                     TEXT,
    data_referencia_doc           BIGINT,
    carregado_em                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (codigo_municipio, numero_licitacao, numero_documento_negociante)
);

CREATE TABLE IF NOT EXISTS stg_item_licitacao (
    id                                  BIGSERIAL PRIMARY KEY,
    codigo_municipio                    TEXT NOT NULL,
    data_realizacao_licitacao           DATE,
    numero_licitacao                    TEXT NOT NULL,
    numero_sequencial_item_licitacao    BIGINT NOT NULL,
    numero_documento_negociante         TEXT NOT NULL,
    descricao_item_licitacao            TEXT,
    valor_vencedor_item_licitacao       NUMERIC(18,2),
    codigo_tipo_negociante              TEXT,
    descricao_unidade_item_licitacao    TEXT,
    numero_quantidade_item_licitacao    NUMERIC(18,4),
    valor_unitario_item_licitacao       NUMERIC(18,4),
    data_referencia_doc                 BIGINT,
    carregado_em                        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (codigo_municipio, numero_licitacao, numero_sequencial_item_licitacao, numero_documento_negociante)
);

-- ----------------------------------------------------------------------------
-- FATOS
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fato_item_licitacao (
    id                                  BIGSERIAL PRIMARY KEY,
    codigo_municipio                    TEXT NOT NULL REFERENCES dim_municipio (codigo_municipio),
    numero_licitacao                    TEXT NOT NULL,
    numero_sequencial_item_licitacao    BIGINT NOT NULL,
    cnpj                                 TEXT NOT NULL REFERENCES dim_empresa (cnpj),
    descricao_item_licitacao            TEXT,
    unidade                              TEXT,
    quantidade                           NUMERIC(18,4),
    valor_unitario                       NUMERIC(18,4),
    valor_vencedor                       NUMERIC(18,2),
    data_realizacao_licitacao           DATE,
    atualizado_em                        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (codigo_municipio, numero_licitacao, numero_sequencial_item_licitacao, cnpj)
);

CREATE INDEX IF NOT EXISTS idx_fato_item_municipio ON fato_item_licitacao (codigo_municipio);
CREATE INDEX IF NOT EXISTS idx_fato_item_cnpj ON fato_item_licitacao (cnpj);
CREATE INDEX IF NOT EXISTS idx_fato_item_municipio_licitacao
    ON fato_item_licitacao (codigo_municipio, numero_licitacao);

-- Agregado por CNPJ + licitação, seguindo literalmente a regra da documentação
-- (seção 2 do PDF): soma de valor_vencedor_item_licitacao quando o mesmo par
-- CNPJ + numero_licitacao aparece em mais de um registro.
CREATE TABLE IF NOT EXISTS fato_participacao_empresa (
    id                       BIGSERIAL PRIMARY KEY,
    codigo_municipio         TEXT NOT NULL REFERENCES dim_municipio (codigo_municipio),
    numero_licitacao         TEXT NOT NULL,
    cnpj                      TEXT NOT NULL REFERENCES dim_empresa (cnpj),
    valor_total_vencido      NUMERIC(18,2) NOT NULL,
    quantidade_itens_vencidos INTEGER NOT NULL,
    data_realizacao_licitacao DATE,
    atualizado_em             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (codigo_municipio, numero_licitacao, cnpj)
);

CREATE INDEX IF NOT EXISTS idx_participacao_cnpj ON fato_participacao_empresa (cnpj);
CREATE INDEX IF NOT EXISTS idx_participacao_municipio ON fato_participacao_empresa (codigo_municipio);
CREATE INDEX IF NOT EXISTS idx_participacao_municipio_licitacao
    ON fato_participacao_empresa (codigo_municipio, numero_licitacao);

-- Cabeçalho da licitação: 1 linha por município + número, com valor total
-- somando todas as empresas participantes (facilita listagem no Full Stack).
CREATE TABLE IF NOT EXISTS fato_licitacao (
    id                          BIGSERIAL PRIMARY KEY,
    codigo_municipio            TEXT NOT NULL REFERENCES dim_municipio (codigo_municipio),
    numero_licitacao            TEXT NOT NULL,
    data_realizacao_licitacao   DATE,
    valor_total_licitacao       NUMERIC(18,2) NOT NULL DEFAULT 0,
    quantidade_empresas         INTEGER NOT NULL DEFAULT 0,
    quantidade_itens            INTEGER NOT NULL DEFAULT 0,
    atualizado_em                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (codigo_municipio, numero_licitacao)
);

CREATE TABLE IF NOT EXISTS fato_dotacao_licitacao (
    id                          BIGSERIAL PRIMARY KEY,
    codigo_municipio            TEXT NOT NULL REFERENCES dim_municipio (codigo_municipio),
    numero_licitacao            TEXT NOT NULL,
    data_realizacao_licitacao   DATE,
    exercicio_orcamento         INTEGER NOT NULL,
    codigo_orgao                TEXT,
    codigo_unidade_orcamentaria TEXT,
    codigo_funcao               TEXT,
    codigo_subfuncao            TEXT,
    codigo_programa             TEXT,
    codigo_projeto_atividade    TEXT,
    numero_projeto_atividade    TEXT,
    numero_subprojeto_atividade TEXT,
    codigo_elemento_despesa     TEXT NOT NULL,
    codigo_natureza             TEXT REFERENCES dim_natureza_despesa (codigo_natureza),
    tipo_fonte                  TEXT,
    codigo_fonte                TEXT,
    valor_dotacao_doc           NUMERIC(18,4),
    data_referencia_doc         BIGINT NOT NULL,
    atualizado_em               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_fato_dotacao_licitacao UNIQUE (
        codigo_municipio, numero_licitacao, exercicio_orcamento,
        codigo_orgao, codigo_unidade_orcamentaria, codigo_funcao,
        codigo_subfuncao, codigo_programa, codigo_projeto_atividade,
        numero_projeto_atividade, numero_subprojeto_atividade,
        codigo_elemento_despesa, tipo_fonte, codigo_fonte, data_referencia_doc
    )
);

CREATE INDEX IF NOT EXISTS idx_dotacao_municipio_licitacao
    ON fato_dotacao_licitacao (codigo_municipio, numero_licitacao);
CREATE INDEX IF NOT EXISTS idx_dotacao_natureza
    ON fato_dotacao_licitacao (codigo_natureza);

-- Ajusta instalações criadas pela primeira versão da tabela, cuja chave não
-- distinguia dotações de projetos/atividades diferentes.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'compra_livre.fato_dotacao_licitacao'::regclass
          AND conname IN (
              'fato_dotacao_licitacao_codigo_municipio_numero_licitacao_exercicio_orcamento_codigo_orgao_codigo_unidade_orcamentaria_codigo_elemento_despesa_codigo_fonte_data_referencia_doc_key',
              'fato_dotacao_licitacao_codigo_municipio_numero_licitacao_ex_key'
          )
    ) THEN
        EXECUTE format(
            'ALTER TABLE compra_livre.fato_dotacao_licitacao DROP CONSTRAINT %I',
            (
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'compra_livre.fato_dotacao_licitacao'::regclass
                  AND conname IN (
                      'fato_dotacao_licitacao_codigo_municipio_numero_licitacao_exercicio_orcamento_codigo_orgao_codigo_unidade_orcamentaria_codigo_elemento_despesa_codigo_fonte_data_referencia_doc_key',
                      'fato_dotacao_licitacao_codigo_municipio_numero_licitacao_ex_key'
                  )
                LIMIT 1
            )
        );
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'compra_livre.fato_dotacao_licitacao'::regclass
          AND conname = 'uq_fato_dotacao_licitacao'
    ) THEN
        ALTER TABLE compra_livre.fato_dotacao_licitacao
        DROP CONSTRAINT uq_fato_dotacao_licitacao;
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'compra_livre.fato_dotacao_licitacao'::regclass
          AND conname = 'uq_fato_dotacao_licitacao'
    ) THEN
        ALTER TABLE compra_livre.fato_dotacao_licitacao
        ADD CONSTRAINT uq_fato_dotacao_licitacao UNIQUE (
            codigo_municipio, numero_licitacao, exercicio_orcamento,
            codigo_orgao, codigo_unidade_orcamentaria, codigo_funcao,
            codigo_subfuncao, codigo_programa, codigo_projeto_atividade,
            numero_projeto_atividade, numero_subprojeto_atividade,
            codigo_elemento_despesa, tipo_fonte, codigo_fonte, data_referencia_doc
        );
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- CONTROLE DO PIPELINE
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pipeline_execucoes (
    id                BIGSERIAL PRIMARY KEY,
    fonte             TEXT NOT NULL,               -- 'tce_licitantes', 'tce_itens', 'ibge_municipios', 'opencnpj'
    modo              TEXT NOT NULL,                -- 'full' ou 'incremental'
    codigo_municipio TEXT,                          -- NULL quando não se aplica (ex.: ibge_municipios)
    data_inicio       DATE,
    data_fim           DATE,
    ultimo_start_index INTEGER NOT NULL DEFAULT 0,  -- ponto de retomada em caso de interrupção
    status             TEXT NOT NULL DEFAULT 'em_andamento', -- em_andamento, concluido, erro
    mensagem_erro      TEXT,
    iniciado_em         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalizado_em        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pipeline_fonte_status ON pipeline_execucoes (fonte, status);
CREATE INDEX IF NOT EXISTS idx_pipeline_resume
    ON pipeline_execucoes (fonte, modo, codigo_municipio, data_inicio, data_fim, status);

-- Migração segura para instalações anteriores que usavam TEXT para datas.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'compra_livre'
          AND table_name = 'stg_licitante'
          AND column_name = 'data_realizacao_licitacao'
          AND data_type = 'text'
    ) THEN
        ALTER TABLE compra_livre.stg_licitante
        ALTER COLUMN data_realizacao_licitacao TYPE DATE USING (
            CASE
                WHEN data_realizacao_licitacao ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN LEFT(data_realizacao_licitacao, 10)::date
                WHEN data_realizacao_licitacao ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$' THEN TO_DATE(data_realizacao_licitacao, 'DD/MM/YYYY')
                ELSE NULL
            END
        );
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'compra_livre'
          AND table_name = 'stg_item_licitacao'
          AND column_name = 'data_realizacao_licitacao'
          AND data_type = 'text'
    ) THEN
        ALTER TABLE compra_livre.stg_item_licitacao
        ALTER COLUMN data_realizacao_licitacao TYPE DATE USING (
            CASE
                WHEN data_realizacao_licitacao ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN LEFT(data_realizacao_licitacao, 10)::date
                WHEN data_realizacao_licitacao ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$' THEN TO_DATE(data_realizacao_licitacao, 'DD/MM/YYYY')
                ELSE NULL
            END
        );
    END IF;
END $$;

-- ============================================================================
-- PAGAMENTOS POR NATUREZA E RECORTE DE MICROEMPRESAS
-- (guia "Como reproduzir os valores do Portal da Transparência do TCE")
--
-- Medida financeira: valor_nota_pagamento das notas NÃO estornadas,
-- vinculadas aos empenhos do exercício (inclui pagamentos posteriores ao
-- ano, ex.: referência 202602 com exercicio_orcamento 202500).
--
-- Códigos e números ficam em TEXT para preservar zeros à esquerda.
-- Não há FK para dim_municipio para que a apuração rode mesmo antes da
-- carga de licitações.
-- ============================================================================

-- Cadastro de órgãos do exercício (passo 2). eh_camara é calculado a cada
-- coleta a partir do próprio cadastro — nunca fixado como '01'.
CREATE TABLE IF NOT EXISTS dim_orgao_tce (
    codigo_municipio     TEXT NOT NULL,
    exercicio_orcamento  TEXT NOT NULL,
    codigo_orgao         TEXT NOT NULL,
    nome_orgao           TEXT,
    codigo_tipo_unidade  TEXT,
    eh_camara            BOOLEAN NOT NULL DEFAULT FALSE,
    motivo_camara        TEXT,
    atualizado_em        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (codigo_municipio, exercicio_orcamento, codigo_orgao)
);

-- Empenhos: um registro por chave completa de 6 campos (passo 5.2).
CREATE TABLE IF NOT EXISTS fato_empenho_despesa (
    codigo_municipio             TEXT NOT NULL,
    exercicio_orcamento          TEXT NOT NULL,
    codigo_orgao                 TEXT NOT NULL,
    codigo_unidade_orcamentaria  TEXT NOT NULL,
    data_emissao_empenho         DATE NOT NULL,
    numero_empenho               TEXT NOT NULL,
    codigo_elemento_despesa      TEXT,
    elemento_despesa             TEXT,          -- 5ª e 6ª posições
    documento_original           TEXT,          -- como veio da API
    documento                    TEXT,          -- sem pontuação, ainda texto
    tipo_documento               TEXT,          -- CNPJ, CPF, INVALIDO, AUSENTE
    codigo_tipo_negociante       TEXT,
    nome_negociante              TEXT,
    data_referencia_doc          TEXT,
    valor_empenhado              NUMERIC(18,4), -- só conferência; NÃO é somado
    atualizado_em                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (codigo_municipio, exercicio_orcamento, codigo_orgao,
                 codigo_unidade_orcamentaria, data_emissao_empenho, numero_empenho)
);
CREATE INDEX IF NOT EXISTS idx_empenho_desp_documento ON fato_empenho_despesa (documento);

-- Notas de pagamento: chave do empenho + sub-empenho + nota (passo 5.3).
CREATE TABLE IF NOT EXISTS fato_pagamento_despesa (
    codigo_municipio             TEXT NOT NULL,
    exercicio_orcamento          TEXT NOT NULL,
    codigo_orgao                 TEXT NOT NULL,
    codigo_unidade_orcamentaria  TEXT NOT NULL,
    data_emissao_empenho         DATE NOT NULL,
    numero_empenho               TEXT NOT NULL,
    numero_sub_empenho           TEXT NOT NULL,
    numero_nota_pagamento        TEXT NOT NULL,
    data_nota_pagamento          DATE,
    data_referencia_doc          TEXT,
    valor_nota_pagamento         NUMERIC(18,4),
    estado_de_estornado          TEXT,
    atualizado_em                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (codigo_municipio, exercicio_orcamento, codigo_orgao,
                 codigo_unidade_orcamentaria, data_emissao_empenho, numero_empenho,
                 numero_sub_empenho, numero_nota_pagamento)
);

-- Classificação cadastral: um resultado por CNPJ completo + fonte + referência
-- da base (passo 9). referencia_base_cadastral = 'NAO_INFORMADA' quando a
-- fonte não informa a edição — a ausência fica registrada, não inventada.
CREATE TABLE IF NOT EXISTS cadastro_porte_fornecedor (
    cnpj_completo              TEXT NOT NULL,
    fonte_cadastral            TEXT NOT NULL,
    referencia_base_cadastral  TEXT NOT NULL DEFAULT 'NAO_INFORMADA',
    cnpj_basico                TEXT NOT NULL,
    porte_original             TEXT,          -- exatamente o que a fonte respondeu
    porte_codigo               TEXT,          -- 01 ME, 03 EPP, 05 Demais, 00 Não informado
    indicador_mei              BOOLEAN,       -- NULL = não informado
    opcao_simples              BOOLEAN,       -- informativo; NÃO substitui o porte
    razao_social               TEXT,
    atualizado_na_fonte        TEXT,
    consultado_em              TIMESTAMPTZ NOT NULL,
    situacao_classificacao     TEXT NOT NULL, -- classificado, nao_encontrado, falha_consulta...
    detalhe                    TEXT,
    PRIMARY KEY (cnpj_completo, fonte_cadastral, referencia_base_cadastral)
);

-- Uma linha por execução da apuração, com todos os parâmetros do recorte.
CREATE TABLE IF NOT EXISTS coleta_pagamentos (
    id                          BIGSERIAL PRIMARY KEY,
    codigo_municipio            TEXT NOT NULL,
    exercicio_orcamento         TEXT NOT NULL,
    referencia_empenhos_inicio  TEXT NOT NULL,
    referencia_empenhos_fim     TEXT NOT NULL,
    referencia_pagamentos_inicio TEXT NOT NULL,
    referencia_pagamentos_fim   TEXT NOT NULL,
    orgaos_excluidos            TEXT[] NOT NULL DEFAULT '{}',
    regra_mei                   TEXT NOT NULL,
    fonte_cadastral             TEXT NOT NULL,
    referencia_base_cadastral   TEXT NOT NULL,
    quantidade_empenhos         INTEGER,
    quantidade_pagamentos       INTEGER,
    quantidade_pendencias       INTEGER,
    reconciliado                BOOLEAN,
    status                      TEXT NOT NULL DEFAULT 'em_andamento', -- em_andamento, concluido, concluido_com_pendencias, erro
    mensagem_erro               TEXT,
    iniciado_em                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalizado_em               TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_coleta_pag_mun_exerc
    ON coleta_pagamentos (codigo_municipio, exercicio_orcamento, finalizado_em DESC);

-- Totais por elemento e categoria de porte (passos 7 e 11). A soma das
-- categorias de um elemento reconcilia com o total de controle.
CREATE TABLE IF NOT EXISTS resumo_pagamento_categoria (
    coleta_id            BIGINT NOT NULL REFERENCES coleta_pagamentos (id) ON DELETE CASCADE,
    elemento_despesa     TEXT NOT NULL,
    categoria_porte      TEXT NOT NULL,   -- ME, EPP, DEMAIS, SEM_CLASSIFICACAO...
    quantidade_pagamentos INTEGER NOT NULL,
    valor_pago           NUMERIC(18,4) NOT NULL,
    PRIMARY KEY (coleta_id, elemento_despesa, categoria_porte)
);

-- Total por fornecedor em cada natureza (passo 11).
CREATE TABLE IF NOT EXISTS resumo_pagamento_fornecedor (
    coleta_id              BIGINT NOT NULL REFERENCES coleta_pagamentos (id) ON DELETE CASCADE,
    elemento_despesa       TEXT NOT NULL,
    documento              TEXT NOT NULL,
    tipo_documento         TEXT,
    nome_negociante        TEXT,
    categoria_porte        TEXT NOT NULL,
    porte_codigo           TEXT,
    situacao_classificacao TEXT,
    quantidade_pagamentos  INTEGER NOT NULL,
    valor_pago             NUMERIC(18,4) NOT NULL,
    PRIMARY KEY (coleta_id, elemento_despesa, documento)
);
CREATE INDEX IF NOT EXISTS idx_resumo_forn_categoria
    ON resumo_pagamento_fornecedor (coleta_id, categoria_porte);

-- Pendências rastreáveis (pagamento sem empenho, duplicação conflitante,
-- órgão desconhecido, estado de estorno desconhecido...).
CREATE TABLE IF NOT EXISTS pendencia_coleta_pagamentos (
    id          BIGSERIAL PRIMARY KEY,
    coleta_id   BIGINT NOT NULL REFERENCES coleta_pagamentos (id) ON DELETE CASCADE,
    tipo        TEXT NOT NULL,
    chave       TEXT NOT NULL,
    detalhe     TEXT,
    valor       NUMERIC(18,4),
    documento   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pendencia_coleta ON pendencia_coleta_pagamentos (coleta_id, tipo);

-- ----------------------------------------------------------------------------
-- PNCP, perfis de clientes e compatibilidade edital x empresa
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS compra_livre.pncp_contratacao (
    numero_controle_pncp        TEXT PRIMARY KEY,
    numero_compra               TEXT,
    ano_compra                  INTEGER,
    sequencial_compra           INTEGER,
    modalidade_codigo          INTEGER,
    modalidade_nome            TEXT,
    data_publicacao             TIMESTAMPTZ,
    data_atualizacao            TIMESTAMPTZ,
    data_abertura_proposta      TIMESTAMPTZ,
    data_encerramento_proposta  TIMESTAMPTZ,
    orgao_cnpj                  TEXT NOT NULL,
    orgao_razao_social          TEXT,
    uf                          CHAR(2),
    codigo_ibge_municipio       TEXT,
    codigo_municipio            TEXT REFERENCES compra_livre.dim_municipio (codigo_municipio),
    nome_municipio              TEXT,
    objeto_compra               TEXT NOT NULL,
    link_edital_pncp            TEXT,
    objeto_normalizado          TEXT NOT NULL DEFAULT '',
    exclusivo_me_epp            BOOLEAN,
    criado_em                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em               TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_atualizacao_pncp       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pncp_contratacao_uf_data
    ON compra_livre.pncp_contratacao (uf, data_atualizacao_pncp DESC);
CREATE INDEX IF NOT EXISTS idx_pncp_contratacao_municipio
    ON compra_livre.pncp_contratacao (codigo_municipio);

CREATE TABLE IF NOT EXISTS compra_livre.pncp_item (
    numero_controle_pncp TEXT NOT NULL REFERENCES compra_livre.pncp_contratacao (numero_controle_pncp) ON DELETE CASCADE,
    numero_item          TEXT NOT NULL,
    descricao            TEXT,
    quantidade           NUMERIC(18,4),
    unidade              TEXT,
    valor_estimado       NUMERIC(18,4),
    codigo_catalogo      TEXT,
    categoria_catalogo   TEXT,
    objeto_normalizado   TEXT NOT NULL DEFAULT '',
    atualizado_em        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (numero_controle_pncp, numero_item)
);

CREATE TABLE IF NOT EXISTS compra_livre.perfil_cliente (
    cnpj                    TEXT PRIMARY KEY REFERENCES compra_livre.dim_empresa (cnpj),
    produtos_palavras_chave TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ufs_atuacao             TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    municipios_atuacao      TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ativo                   BOOLEAN NOT NULL DEFAULT TRUE,
    atualizado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS compra_livre.pncp_match (
    numero_controle_pncp TEXT NOT NULL REFERENCES compra_livre.pncp_contratacao (numero_controle_pncp) ON DELETE CASCADE,
    cnpj                 TEXT NOT NULL REFERENCES compra_livre.dim_empresa (cnpj),
    score                NUMERIC(5,4) NOT NULL CHECK (score >= 0 AND score <= 1),
    motivos              JSONB NOT NULL DEFAULT '{}'::JSONB,
    criado_em            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (numero_controle_pncp, cnpj)
);

CREATE INDEX IF NOT EXISTS idx_pncp_match_criado_em
    ON compra_livre.pncp_match (criado_em);