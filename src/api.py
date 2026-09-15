"""API somente leitura para os dados analíticos do schema compra_livre."""

from datetime import date
from decimal import Decimal
from typing import Any

import psycopg2.extras
import psycopg2
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

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


@app.get("/health", tags=["sistema"])
def health() -> dict[str, str]:
    try:
        _linhas("SELECT 1 AS ok", ())
    except psycopg2.Error as erro:
        raise HTTPException(status_code=503, detail="Banco de dados indisponível") from erro
    return {"status": "ok"}


@app.get("/municipios", tags=["dimensões"])
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


@app.get("/licitacoes", tags=["licitações"])
def licitacoes(
    codigo_municipio: str | None = Query(default=None, max_length=20),
    codigo_natureza: str | None = Query(default=None, min_length=2, max_length=2),
    data_inicio: date | None = None,
    data_fim: date | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if data_inicio and data_fim and data_inicio > data_fim:
        raise HTTPException(status_code=400, detail="data_inicio não pode ser posterior a data_fim")
    filtros: list[str] = []
    valores: list[Any] = []
    if codigo_municipio:
        filtros.append("f.codigo_municipio = %s")
        valores.append(codigo_municipio)
    if codigo_natureza:
        if not _linhas(
            """
            SELECT codigo_natureza
            FROM compra_livre.dim_natureza_despesa
            WHERE codigo_natureza = %s
            """,
            (codigo_natureza,),
        ):
            raise HTTPException(
                status_code=400,
                detail="codigo_natureza não cadastrado",
            )
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


@app.get("/licitacoes/{codigo_municipio}/{numero_licitacao}/itens", tags=["licitações"])
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


@app.get("/participacoes", tags=["empresas"])
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


@app.get("/empresas", tags=["empresas"])
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
