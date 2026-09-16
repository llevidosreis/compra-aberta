"""API somente leitura para os dados analíticos do schema compra_livre."""

from datetime import date
from decimal import Decimal
from typing import Any

import psycopg2.extras
import psycopg2
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.config import carregar_config
from src.db import conexao

app = FastAPI(
    title="Compra Livre API",
    description="Dados analíticos de licitações públicas do Ceará.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


class Paginacao(BaseModel):
    limit: int
    offset: int


class Municipio(BaseModel):
    codigo_municipio: str
    nome: str
    uf: str
    codigo_ibge: str | None = None
    codigo_geonames: str | None = None


class MunicipiosResponse(Paginacao):
    data: list[Municipio]


class Licitacao(BaseModel):
    id: int
    codigo_municipio: str
    municipio: str
    numero_licitacao: str
    data_realizacao_licitacao: str | None = None
    valor_total_licitacao: str
    quantidade_empresas: int
    quantidade_itens: int
    atualizado_em: str
    naturezas_despesa: list[str]


class LicitacoesResponse(Paginacao):
    data: list[Licitacao]


class Dotacao(BaseModel):
    id: int
    codigo_municipio: str
    numero_licitacao: str
    data_realizacao_licitacao: str | None = None
    exercicio_orcamento: int
    codigo_orgao: str | None = None
    codigo_unidade_orcamentaria: str | None = None
    codigo_funcao: str | None = None
    codigo_subfuncao: str | None = None
    codigo_programa: str | None = None
    codigo_projeto_atividade: str | None = None
    numero_projeto_atividade: str | None = None
    numero_subprojeto_atividade: str | None = None
    codigo_elemento_despesa: str
    codigo_natureza: str | None = None
    nome_natureza: str | None = None
    tipo_fonte: str | None = None
    codigo_fonte: str | None = None
    valor_dotacao_doc: str | None = None
    data_referencia_doc: int


class DotacoesResponse(Paginacao):
    data: list[Dotacao]


class ItemLicitacao(BaseModel):
    id: int
    codigo_municipio: str
    numero_licitacao: str
    numero_sequencial_item_licitacao: int
    cnpj: str
    razao_social: str | None = None
    porte_empresa: str | None = None
    descricao_item_licitacao: str | None = None
    unidade: str | None = None
    quantidade: str | None = None
    valor_unitario: str | None = None
    valor_vencedor: str | None = None
    data_realizacao_licitacao: str | None = None


class ItensResponse(Paginacao):
    data: list[ItemLicitacao]


class Participacao(BaseModel):
    id: int
    codigo_municipio: str
    municipio: str
    numero_licitacao: str
    cnpj: str
    razao_social: str | None = None
    porte_empresa: str | None = None
    cnae_principal: str | None = None
    valor_total_vencido: str
    quantidade_itens_vencidos: int
    data_realizacao_licitacao: str | None = None


class ParticipacoesResponse(Paginacao):
    data: list[Participacao]


class Empresa(BaseModel):
    cnpj: str
    razao_social: str | None = None
    nome_fantasia: str | None = None
    situacao_cadastral: str | None = None
    cnae_principal: str | None = None
    porte_empresa: str | None = None
    uf_empresa: str | None = None
    municipio_empresa: str | None = None
    encontrado_opencnpj: bool | None = None
    consultado_em: str | None = None
    atualizado_em: str


class EmpresasResponse(Paginacao):
    data: list[Empresa]


class GastoPorNatureza(BaseModel):
    codigo_natureza: str
    nome_natureza: str
    valor_total_dotacao: str
    quantidade_dotacoes: int
    quantidade_licitacoes: int


class GastosPorNaturezaResponse(BaseModel):
    data: list[GastoPorNatureza]
    metrica: str
    observacao: str


class IndicadorMicroempresas(BaseModel):
    quantidade_microempresas: int
    quantidade_licitacoes: int
    quantidade_participacoes: int
    valor_total_vencido: str
    quantidade_itens_vencidos: int


class MicroempresasResponse(BaseModel):
    data: IndicadorMicroempresas
    porte_considerado: list[str]


class ParticipacaoMePorNatureza(BaseModel):
    codigo_natureza: str
    nome_natureza: str
    quantidade_licitacoes: int
    licitacoes_com_me: int
    percentual_licitacoes_com_me: float | None = None
    quantidade_participacoes: int
    participacoes_me: int
    percentual_participacoes_me: float | None = None
    valor_vencido: str
    valor_vencido_me: str
    percentual_valor_vencido_me: float | None = None
    itens_vencidos: int
    itens_vencidos_me: int
    percentual_itens_vencidos_me: float | None = None


class ParticipacaoMePorNaturezaResponse(BaseModel):
    data: list[ParticipacaoMePorNatureza]
    associacao: str
    observacao: str


class LicitacaoDetalhada(BaseModel):
    codigo_municipio: str
    municipio: str
    numero_licitacao: str
    ano_licitacao: int | None = None
    valor_total_licitacao: str
    codigo_natureza: str | None = None
    nome_natureza: str | None = None
    cnpj: str
    empresa: str | None = None
    porte_empresa: str | None = None
    endereco: str | None = None
    valor_total_vencido: str
    quantidade_itens_vencidos: int


class LicitacoesDetalhadasResponse(Paginacao):
    data: list[LicitacaoDetalhada]


class ParticipacaoMeLocal(BaseModel):
    codigo_municipio: str
    municipio_consultado: str
    numero_licitacao: str
    data_realizacao_licitacao: str | None = None
    cnpj: str
    empresa: str | None = None
    porte_empresa: str | None = None
    municipio_empresa: str | None = None
    uf_empresa: str | None = None
    endereco: str | None = None
    valor_total_vencido: str
    quantidade_itens_vencidos: int


class ParticipacoesMeLocaisResumo(BaseModel):
    total_licitacoes: int
    licitacoes_com_me_local: int
    percentual_licitacoes_com_me_local: float | None = None
    total_participacoes: int
    participacoes_me_locais: int
    percentual_participacoes_me_locais: float | None = None
    valor_total_vencido: str
    valor_vencido_me_local: str
    percentual_valor_vencido_me_local: float | None = None
    itens_vencidos: int
    itens_vencidos_me_locais: int
    percentual_itens_vencidos_me_locais: float | None = None


class ParticipacoesMeLocaisResponse(Paginacao):
    data: list[ParticipacaoMeLocal]
    resumo: ParticipacoesMeLocaisResumo


def _serializar(valor: Any) -> Any:
    if isinstance(valor, (date,)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return str(valor)
    return valor


def _linhas(sql: str, parametros: tuple[Any, ...]) -> list[dict[str, Any]]:
    config = carregar_config()
    with conexao(config) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(sql, parametros)
            return [
                {chave: _serializar(valor) for chave, valor in dict(linha).items()}
                for linha in cursor.fetchall()
            ]


def _validar_periodo(data_inicio: date | None, data_fim: date | None) -> None:
    if data_inicio and data_fim and data_inicio > data_fim:
        raise HTTPException(status_code=400, detail="data_inicio não pode ser posterior a data_fim")


def _filtros_data(
    alias: str, data_inicio: date | None, data_fim: date | None
) -> tuple[list[str], list[Any]]:
    filtros: list[str] = []
    valores: list[Any] = []
    if data_inicio:
        filtros.append(f"{alias}.data_realizacao_licitacao >= %s")
        valores.append(data_inicio)
    if data_fim:
        filtros.append(f"{alias}.data_realizacao_licitacao <= %s")
        valores.append(data_fim)
    return filtros, valores


def _porte_microempresa_sql(alias: str) -> str:
    return f"""
        LOWER(TRIM(COALESCE({alias}.porte_empresa, ''))) IN (
            'me', 'microempresa', 'microempresa (me)'
        )
    """


def _validar_codigo_natureza(codigo_natureza: str | None) -> None:
    if codigo_natureza and not _linhas(
        """
        SELECT codigo_natureza
        FROM compra_livre.dim_natureza_despesa
        WHERE codigo_natureza = %s
        """,
        (codigo_natureza,),
    ):
        raise HTTPException(status_code=400, detail="codigo_natureza não cadastrado")


@app.get("/health", tags=["sistema"])
def health() -> dict[str, str]:
    try:
        _linhas("SELECT 1 AS ok", ())
    except psycopg2.Error as erro:
        raise HTTPException(status_code=503, detail="Banco de dados indisponível") from erro
    return {"status": "ok"}


@app.get("/municipios", response_model=MunicipiosResponse, tags=["dimensões"])
def municipios(
    uf: str | None = Query(default=None, min_length=2, max_length=2),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    filtro = "WHERE uf = %s" if uf else ""
    parametros: tuple[Any, ...] = (uf.upper(), limit, offset) if uf else (limit, offset)
    rows = _linhas(
        f"""
        SELECT codigo_municipio, nome, uf, codigo_ibge, codigo_geonames
        FROM compra_livre.dim_municipio
        {filtro}
        ORDER BY nome
        LIMIT %s OFFSET %s
        """,
        parametros,
    )
    return {"data": rows, "limit": limit, "offset": offset}


@app.get("/licitacoes", response_model=LicitacoesResponse, tags=["licitações"])
def licitacoes(
    codigo_municipio: str | None = Query(default=None, max_length=20),
    codigo_natureza: str | None = Query(default=None, min_length=2, max_length=2),
    data_inicio: date | None = None,
    data_fim: date | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _validar_periodo(data_inicio, data_fim)
    filtros: list[str] = []
    valores: list[Any] = []
    if codigo_municipio:
        filtros.append("f.codigo_municipio = %s")
        valores.append(codigo_municipio)
    if codigo_natureza:
        _validar_codigo_natureza(codigo_natureza)
        filtros.append(
            """
            EXISTS (
                SELECT 1
                FROM compra_livre.fato_dotacao_licitacao fd
                WHERE fd.codigo_municipio = f.codigo_municipio
                  AND fd.numero_licitacao = f.numero_licitacao
                  AND fd.codigo_natureza = %s
            )
            """
        )
        valores.append(codigo_natureza)
    if data_inicio:
        filtros.append("f.data_realizacao_licitacao >= %s")
        valores.append(data_inicio)
    if data_fim:
        filtros.append("f.data_realizacao_licitacao <= %s")
        valores.append(data_fim)
    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    valores.extend([limit, offset])
    rows = _linhas(
        f"""
        SELECT f.id, f.codigo_municipio, m.nome AS municipio,
               f.numero_licitacao, f.data_realizacao_licitacao,
               f.valor_total_licitacao, f.quantidade_empresas,
               f.quantidade_itens, f.atualizado_em,
               COALESCE((
                   SELECT json_agg(DISTINCT nd.nome_natureza ORDER BY nd.nome_natureza)
                   FROM compra_livre.fato_dotacao_licitacao d
                   JOIN compra_livre.dim_natureza_despesa nd
                     ON nd.codigo_natureza = d.codigo_natureza
                   WHERE d.codigo_municipio = f.codigo_municipio
                     AND d.numero_licitacao = f.numero_licitacao
               ), '[]'::json) AS naturezas_despesa
        FROM compra_livre.fato_licitacao f
        JOIN compra_livre.dim_municipio m
          ON m.codigo_municipio = f.codigo_municipio
        {where}
        ORDER BY f.data_realizacao_licitacao DESC NULLS LAST, f.id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(valores),
    )
    return {"data": rows, "limit": limit, "offset": offset}


@app.get(
    "/licitacoes/{codigo_municipio}/{numero_licitacao}/dotacoes",
    response_model=DotacoesResponse,
    tags=["licitações"],
)
def dotacoes_licitacao(
    codigo_municipio: str,
    numero_licitacao: str,
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows = _linhas(
        """
        SELECT d.id, d.codigo_municipio, d.numero_licitacao,
               d.data_realizacao_licitacao, d.exercicio_orcamento,
               d.codigo_orgao, d.codigo_unidade_orcamentaria,
               d.codigo_funcao, d.codigo_subfuncao, d.codigo_programa,
               d.codigo_projeto_atividade, d.numero_projeto_atividade,
               d.numero_subprojeto_atividade, d.codigo_elemento_despesa,
               d.codigo_natureza, nd.nome_natureza, d.tipo_fonte,
               d.codigo_fonte, d.valor_dotacao_doc, d.data_referencia_doc
        FROM compra_livre.fato_dotacao_licitacao d
        LEFT JOIN compra_livre.dim_natureza_despesa nd
          ON nd.codigo_natureza = d.codigo_natureza
        WHERE d.codigo_municipio = %s
          AND d.numero_licitacao = %s
        ORDER BY d.data_referencia_doc, d.id
        LIMIT %s OFFSET %s
        """,
        (codigo_municipio, numero_licitacao, limit, offset),
    )
    return {"data": rows, "limit": limit, "offset": offset}


@app.get(
    "/licitacoes/{codigo_municipio}/{numero_licitacao}/itens",
    response_model=ItensResponse,
    tags=["licitações"],
)
def itens_licitacao(
    codigo_municipio: str,
    numero_licitacao: str,
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows = _linhas(
        """
        SELECT i.id, i.codigo_municipio, i.numero_licitacao,
               i.numero_sequencial_item_licitacao, i.cnpj,
               e.razao_social, e.porte_empresa,
               i.descricao_item_licitacao, i.unidade, i.quantidade,
               i.valor_unitario, i.valor_vencedor,
               i.data_realizacao_licitacao
        FROM compra_livre.fato_item_licitacao i
        LEFT JOIN compra_livre.dim_empresa e ON e.cnpj = i.cnpj
        WHERE i.codigo_municipio = %s
          AND i.numero_licitacao = %s
        ORDER BY i.numero_sequencial_item_licitacao, i.id
        LIMIT %s OFFSET %s
        """,
        (codigo_municipio, numero_licitacao, limit, offset),
    )
    return {"data": rows, "limit": limit, "offset": offset}


@app.get("/participacoes", response_model=ParticipacoesResponse, tags=["empresas"])
def participacoes(
    codigo_municipio: str | None = Query(default=None, max_length=20),
    porte_empresa: str | None = Query(default=None, max_length=100),
    cnpj: str | None = Query(default=None, max_length=14),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    filtros: list[str] = []
    valores: list[Any] = []
    if codigo_municipio:
        filtros.append("p.codigo_municipio = %s")
        valores.append(codigo_municipio)
    if porte_empresa:
        filtros.append("e.porte_empresa = %s")
        valores.append(porte_empresa)
    if cnpj:
        documento = "".join(ch for ch in cnpj if ch.isdigit())
        if len(documento) not in (11, 14):
            raise HTTPException(status_code=400, detail="cnpj deve conter 11 ou 14 dígitos")
        filtros.append("p.cnpj = %s")
        valores.append(documento)
    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    valores.extend([limit, offset])
    rows = _linhas(
        f"""
        SELECT p.id, p.codigo_municipio, m.nome AS municipio,
               p.numero_licitacao, p.cnpj, e.razao_social,
               e.porte_empresa, e.cnae_principal,
               p.valor_total_vencido, p.quantidade_itens_vencidos,
               p.data_realizacao_licitacao
        FROM compra_livre.fato_participacao_empresa p
        JOIN compra_livre.dim_municipio m
          ON m.codigo_municipio = p.codigo_municipio
        JOIN compra_livre.dim_empresa e ON e.cnpj = p.cnpj
        {where}
        ORDER BY p.data_realizacao_licitacao DESC NULLS LAST, p.id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(valores),
    )
    return {"data": rows, "limit": limit, "offset": offset}


@app.get("/empresas", response_model=EmpresasResponse, tags=["empresas"])
def empresas(
    porte_empresa: str | None = Query(default=None, max_length=100),
    cnae_principal: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    filtros: list[str] = []
    valores: list[Any] = []
    if porte_empresa:
        filtros.append("porte_empresa = %s")
        valores.append(porte_empresa)
    if cnae_principal:
        filtros.append("cnae_principal = %s")
        valores.append(cnae_principal)
    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    valores.extend([limit, offset])
    rows = _linhas(
        f"""
        SELECT cnpj, razao_social, nome_fantasia, situacao_cadastral,
               cnae_principal, porte_empresa, uf_empresa,
               municipio_empresa, encontrado_opencnpj,
               consultado_em, atualizado_em
        FROM compra_livre.dim_empresa
        {where}
        ORDER BY razao_social NULLS LAST, cnpj
        LIMIT %s OFFSET %s
        """,
        tuple(valores),
    )
    return {"data": rows, "limit": limit, "offset": offset}


@app.get(
    "/analytics/licitacoes-detalhadas",
    response_model=LicitacoesDetalhadasResponse,
    tags=["indicadores"],
)
def licitacoes_detalhadas(
    codigo_municipio: str | None = Query(default=None, max_length=20),
    codigo_natureza: str | None = Query(default=None, min_length=2, max_length=2),
    ano: int | None = Query(default=None, ge=2000, le=2100),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Retorna licitação, natureza e empresa vencedora na mesma linha."""
    _validar_codigo_natureza(codigo_natureza)
    filtros: list[str] = []
    valores: list[Any] = []
    if codigo_municipio:
        filtros.append("f.codigo_municipio = %s")
        valores.append(codigo_municipio)
    if codigo_natureza:
        filtros.append("n.codigo_natureza = %s")
        valores.append(codigo_natureza)
    if ano:
        filtros.append("EXTRACT(YEAR FROM f.data_realizacao_licitacao) = %s")
        valores.append(ano)
    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    valores.extend([limit, offset])
    rows = _linhas(
        f"""
        WITH naturezas AS (
            SELECT codigo_municipio, numero_licitacao,
                   d.codigo_natureza, MAX(n.nome_natureza) AS nome_natureza
            FROM compra_livre.fato_dotacao_licitacao d
            LEFT JOIN compra_livre.dim_natureza_despesa n
              ON n.codigo_natureza = d.codigo_natureza
            GROUP BY d.codigo_municipio, d.numero_licitacao, d.codigo_natureza
        ),
        participacoes AS (
            SELECT p.codigo_municipio, p.numero_licitacao, p.cnpj,
                   p.valor_total_vencido, p.quantidade_itens_vencidos,
                   e.razao_social, e.porte_empresa,
                   e.municipio_empresa, e.uf_empresa
            FROM compra_livre.fato_participacao_empresa p
            JOIN compra_livre.dim_empresa e ON e.cnpj = p.cnpj
        )
        SELECT f.codigo_municipio, m.nome AS municipio,
               f.numero_licitacao,
               EXTRACT(YEAR FROM f.data_realizacao_licitacao)::INTEGER
                   AS ano_licitacao,
               f.valor_total_licitacao,
               n.codigo_natureza, n.nome_natureza,
               p.cnpj, p.razao_social AS empresa, p.porte_empresa,
               COALESCE(sl.endereco_negociante, NULL) AS endereco,
               p.valor_total_vencido, p.quantidade_itens_vencidos
        FROM compra_livre.fato_licitacao f
        JOIN compra_livre.dim_municipio m
          ON m.codigo_municipio = f.codigo_municipio
        JOIN naturezas n
          ON n.codigo_municipio = f.codigo_municipio
         AND n.numero_licitacao = f.numero_licitacao
        JOIN participacoes p
          ON p.codigo_municipio = f.codigo_municipio
         AND p.numero_licitacao = f.numero_licitacao
        LEFT JOIN LATERAL (
            SELECT s.endereco_negociante
            FROM compra_livre.stg_licitante s
            WHERE s.codigo_municipio = p.codigo_municipio
              AND s.numero_licitacao = p.numero_licitacao
              AND s.numero_documento_negociante = p.cnpj
            ORDER BY s.id DESC
            LIMIT 1
        ) sl ON TRUE
        {where}
        ORDER BY f.data_realizacao_licitacao DESC NULLS LAST,
                 f.codigo_municipio, f.numero_licitacao, n.codigo_natureza
        LIMIT %s OFFSET %s
        """,
        tuple(valores),
    )
    return {"data": rows, "limit": limit, "offset": offset}


@app.get(
    "/analytics/participacoes-me-locais",
    response_model=ParticipacoesMeLocaisResponse,
    tags=["indicadores"],
)
def participacoes_me_locais(
    codigo_municipio: str = Query(..., min_length=1, max_length=20),
    ano: int | None = Query(default=None, ge=2000, le=2100),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Retorna ME vencedoras cujo município cadastral é o consultado."""
    filtros = ["p.codigo_municipio = %s", _porte_microempresa_sql("e")]
    valores: list[Any] = [codigo_municipio]
    resumo_filtros = ["p.codigo_municipio = %s"]
    resumo_valores: list[Any] = [codigo_municipio]
    if ano:
        filtros.append("EXTRACT(YEAR FROM p.data_realizacao_licitacao) = %s")
        valores.append(ano)
        resumo_filtros.append("EXTRACT(YEAR FROM p.data_realizacao_licitacao) = %s")
        resumo_valores.append(ano)
    valores.extend([limit, offset])
    rows = _linhas(
        f"""
        SELECT p.codigo_municipio, m.nome AS municipio_consultado,
               p.numero_licitacao, p.data_realizacao_licitacao,
               p.cnpj, e.razao_social AS empresa, e.porte_empresa,
               e.municipio_empresa, e.uf_empresa,
               COALESCE(sl.endereco_negociante, NULL) AS endereco,
               p.valor_total_vencido, p.quantidade_itens_vencidos
        FROM compra_livre.fato_participacao_empresa p
        JOIN compra_livre.dim_municipio m
          ON m.codigo_municipio = p.codigo_municipio
        JOIN compra_livre.dim_empresa e ON e.cnpj = p.cnpj
        LEFT JOIN LATERAL (
            SELECT s.endereco_negociante
            FROM compra_livre.stg_licitante s
            WHERE s.codigo_municipio = p.codigo_municipio
              AND s.numero_licitacao = p.numero_licitacao
              AND s.numero_documento_negociante = p.cnpj
            ORDER BY s.id DESC
            LIMIT 1
        ) sl ON TRUE
        WHERE {' AND '.join(filtros)}
          AND LOWER(TRIM(e.municipio_empresa)) = LOWER(TRIM(m.nome))
        ORDER BY p.data_realizacao_licitacao DESC NULLS LAST, p.id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(valores),
    )
    resumo_rows = _linhas(
        f"""
        SELECT
            COUNT(DISTINCT (p.codigo_municipio, p.numero_licitacao))
                AS total_licitacoes,
            COUNT(DISTINCT (p.codigo_municipio, p.numero_licitacao))
                FILTER (WHERE {_porte_microempresa_sql("e")}
                    AND LOWER(TRIM(e.municipio_empresa)) = LOWER(TRIM(m.nome)))
                AS licitacoes_com_me_local,
            COUNT(*) AS total_participacoes,
            COUNT(*) FILTER (
                WHERE {_porte_microempresa_sql("e")}
                  AND LOWER(TRIM(e.municipio_empresa)) = LOWER(TRIM(m.nome))
            ) AS participacoes_me_locais,
            COALESCE(SUM(p.valor_total_vencido), 0) AS valor_total_vencido,
            COALESCE(SUM(p.valor_total_vencido) FILTER (
                WHERE {_porte_microempresa_sql("e")}
                  AND LOWER(TRIM(e.municipio_empresa)) = LOWER(TRIM(m.nome))
            ), 0) AS valor_vencido_me_local,
            COALESCE(SUM(p.quantidade_itens_vencidos), 0) AS itens_vencidos,
            COALESCE(SUM(p.quantidade_itens_vencidos) FILTER (
                WHERE {_porte_microempresa_sql("e")}
                  AND LOWER(TRIM(e.municipio_empresa)) = LOWER(TRIM(m.nome))
            ), 0) AS itens_vencidos_me_locais
        FROM compra_livre.fato_participacao_empresa p
        JOIN compra_livre.dim_municipio m
          ON m.codigo_municipio = p.codigo_municipio
        JOIN compra_livre.dim_empresa e ON e.cnpj = p.cnpj
        WHERE {' AND '.join(resumo_filtros)}
        """,
        tuple(resumo_valores),
    )
    resumo = resumo_rows[0]
    for total, numerator, percentage in (
        ("total_licitacoes", "licitacoes_com_me_local", "percentual_licitacoes_com_me_local"),
        ("total_participacoes", "participacoes_me_locais", "percentual_participacoes_me_locais"),
        ("valor_total_vencido", "valor_vencido_me_local", "percentual_valor_vencido_me_local"),
        ("itens_vencidos", "itens_vencidos_me_locais", "percentual_itens_vencidos_me_locais"),
    ):
        total_value = float(resumo[total])
        resumo[percentage] = round(float(resumo[numerator]) / total_value * 100, 2) if total_value else None
    return {"data": rows, "resumo": resumo, "limit": limit, "offset": offset}


@app.get(
    "/analytics/gastos-por-natureza",
    response_model=GastosPorNaturezaResponse,
    tags=["indicadores"],
)
def gastos_por_natureza(
    codigo_municipio: str | None = Query(default=None, max_length=20),
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> dict[str, Any]:
    """Agrega o valor das dotações classificadas nas sete naturezas.

    O valor retornado é dotação associada à contratação, não pagamento efetivo.
    """
    _validar_periodo(data_inicio, data_fim)
    filtros, valores = _filtros_data("d", data_inicio, data_fim)
    if codigo_municipio:
        filtros.append("d.codigo_municipio = %s")
        valores.append(codigo_municipio)
    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    return {
        "data": _linhas(
            f"""
            SELECT d.codigo_natureza, n.nome_natureza,
                   COALESCE(SUM(d.valor_dotacao_doc), 0) AS valor_total_dotacao,
                   COUNT(*) AS quantidade_dotacoes,
                   COUNT(DISTINCT (d.codigo_municipio, d.numero_licitacao))
                       AS quantidade_licitacoes
            FROM compra_livre.fato_dotacao_licitacao d
            JOIN compra_livre.dim_natureza_despesa n
              ON n.codigo_natureza = d.codigo_natureza
            {where}
            GROUP BY d.codigo_natureza, n.nome_natureza
            ORDER BY d.codigo_natureza
            """,
            tuple(valores),
        ),
        "metrica": "valor_total_dotacao",
        "observacao": "Valor de dotacoes associado as contratacoes; nao representa pagamento efetivo.",
    }


@app.get(
    "/analytics/microempresas",
    response_model=MicroempresasResponse,
    tags=["indicadores"],
)
def indicador_microempresas(
    codigo_municipio: str | None = Query(default=None, max_length=20),
    codigo_natureza: str | None = Query(default=None, min_length=2, max_length=2),
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> dict[str, Any]:
    """Consolida empresas ME vencedoras e suas participações registradas."""
    _validar_periodo(data_inicio, data_fim)
    _validar_codigo_natureza(codigo_natureza)
    filtros, valores = _filtros_data("p", data_inicio, data_fim)
    if codigo_municipio:
        filtros.append("p.codigo_municipio = %s")
        valores.append(codigo_municipio)
    if codigo_natureza:
        filtros.append(
            """
            EXISTS (
                SELECT 1
                FROM compra_livre.fato_dotacao_licitacao d
                WHERE d.codigo_municipio = p.codigo_municipio
                  AND d.numero_licitacao = p.numero_licitacao
                  AND d.codigo_natureza = %s
            )
            """
        )
        valores.append(codigo_natureza)
    filtros.append(_porte_microempresa_sql("e"))
    where = f"WHERE {' AND '.join(filtros)}"
    rows = _linhas(
        f"""
        SELECT COUNT(DISTINCT p.cnpj) AS quantidade_microempresas,
               COUNT(DISTINCT (p.codigo_municipio, p.numero_licitacao))
                   AS quantidade_licitacoes,
               COUNT(*) AS quantidade_participacoes,
               COALESCE(SUM(p.valor_total_vencido), 0) AS valor_total_vencido,
               COALESCE(SUM(p.quantidade_itens_vencidos), 0)
                   AS quantidade_itens_vencidos
        FROM compra_livre.fato_participacao_empresa p
        JOIN compra_livre.dim_empresa e ON e.cnpj = p.cnpj
        {where}
        """,
        tuple(valores),
    )
    return {"data": rows[0], "porte_considerado": ["ME", "Microempresa", "Microempresa (ME)"]}


@app.get(
    "/analytics/participacao-me-por-natureza",
    response_model=ParticipacaoMePorNaturezaResponse,
    tags=["indicadores"],
)
def participacao_me_por_natureza(
    codigo_municipio: str | None = Query(default=None, max_length=20),
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> dict[str, Any]:
    """Calcula a presença de ME por natureza no nível da licitação.

    Uma licitação com várias naturezas aparece em cada natureza relacionada;
    os valores não são repartidos entre itens porque o TCE não fornece essa
    ligação diretamente nesta base.
    """
    _validar_periodo(data_inicio, data_fim)
    dotacao_filtros, dotacao_valores = _filtros_data("d", data_inicio, data_fim)
    if codigo_municipio:
        dotacao_filtros.append("d.codigo_municipio = %s")
        dotacao_valores.append(codigo_municipio)
    dotacao_where = (
        f"WHERE {' AND '.join(dotacao_filtros)}" if dotacao_filtros else ""
    )
    participacao_filtros, participacao_valores = _filtros_data(
        "p", data_inicio, data_fim
    )
    if codigo_municipio:
        participacao_filtros.append("p.codigo_municipio = %s")
        participacao_valores.append(codigo_municipio)
    participacao_where = (
        f"WHERE {' AND '.join(participacao_filtros)}"
        if participacao_filtros
        else ""
    )
    rows = _linhas(
        f"""
        WITH licitacoes_naturezas AS (
            SELECT DISTINCT d.codigo_municipio, d.numero_licitacao,
                            d.codigo_natureza
            FROM compra_livre.fato_dotacao_licitacao d
            {dotacao_where}
              {"AND" if dotacao_where else "WHERE"} d.codigo_natureza IS NOT NULL
        ),
        participacoes AS (
            SELECT p.codigo_municipio, p.numero_licitacao,
                   COUNT(*) AS quantidade_participacoes,
                   COUNT(*) FILTER (WHERE {_porte_microempresa_sql("e")})
                       AS participacoes_me,
                   SUM(p.valor_total_vencido) AS valor_vencido,
                   SUM(p.valor_total_vencido) FILTER (
                       WHERE {_porte_microempresa_sql("e")}
                   ) AS valor_vencido_me,
                   SUM(p.quantidade_itens_vencidos) AS itens_vencidos,
                   SUM(p.quantidade_itens_vencidos) FILTER (
                       WHERE {_porte_microempresa_sql("e")}
                   ) AS itens_vencidos_me
            FROM compra_livre.fato_participacao_empresa p
            JOIN compra_livre.dim_empresa e ON e.cnpj = p.cnpj
            {participacao_where}
            GROUP BY p.codigo_municipio, p.numero_licitacao
        ),
        agregados AS (
            SELECT ln.codigo_natureza,
                   COUNT(*) AS quantidade_licitacoes,
                   COUNT(*) FILTER (WHERE p.participacoes_me > 0)
                       AS licitacoes_com_me,
                   COALESCE(SUM(p.quantidade_participacoes), 0)
                       AS quantidade_participacoes,
                   COALESCE(SUM(p.participacoes_me), 0) AS participacoes_me,
                   COALESCE(SUM(p.valor_vencido), 0) AS valor_vencido,
                   COALESCE(SUM(p.valor_vencido_me), 0) AS valor_vencido_me,
                   COALESCE(SUM(p.itens_vencidos), 0) AS itens_vencidos,
                   COALESCE(SUM(p.itens_vencidos_me), 0) AS itens_vencidos_me
            FROM licitacoes_naturezas ln
            LEFT JOIN participacoes p
              ON p.codigo_municipio = ln.codigo_municipio
             AND p.numero_licitacao = ln.numero_licitacao
            GROUP BY ln.codigo_natureza
        )
        SELECT a.codigo_natureza, n.nome_natureza,
               a.quantidade_licitacoes, a.licitacoes_com_me,
               ROUND(100.0 * a.licitacoes_com_me
                   / NULLIF(a.quantidade_licitacoes, 0), 2)
                   AS percentual_licitacoes_com_me,
               a.quantidade_participacoes, a.participacoes_me,
               ROUND(100.0 * a.participacoes_me
                   / NULLIF(a.quantidade_participacoes, 0), 2)
                   AS percentual_participacoes_me,
               a.valor_vencido, a.valor_vencido_me,
               ROUND(100.0 * a.valor_vencido_me
                   / NULLIF(a.valor_vencido, 0), 2)
                   AS percentual_valor_vencido_me,
               a.itens_vencidos, a.itens_vencidos_me,
               ROUND(100.0 * a.itens_vencidos_me
                   / NULLIF(a.itens_vencidos, 0), 2)
                   AS percentual_itens_vencidos_me
        FROM agregados a
        JOIN compra_livre.dim_natureza_despesa n
          ON n.codigo_natureza = a.codigo_natureza
        ORDER BY a.codigo_natureza
        """,
        tuple(
        dotacao_valores
        + participacao_valores
        ),
    )
    return {
        "data": rows,
        "associacao": "licitacao",
        "observacao": "Quando uma licitacao possui varias naturezas, ela e contabilizada em cada natureza relacionada.",
    }


@app.get("/")
def read_root():
    return {"message": "API Compra Aberta está no ar! Acesse /docs para a documentação."}
