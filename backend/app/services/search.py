"""Hybrid search: dense vectors + Postgres full-text, fused with RRF."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.services.embeddings import embed_query

logger = logging.getLogger(__name__)

# Constante estandar de Reciprocal Rank Fusion. Amortigua el peso de las
# primeras posiciones para que un unico resultado dominante en una de las dos
# listas no se lleve todo el ranking.
RRF_K = 60


@dataclass
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page_number: int | None
    content: str
    score: float
    vector_rank: int | None
    text_rank: int | None


def hybrid_search(
    db: Session,
    query: str,
    *,
    limit: int = 10,
    candidates: int = 40,
    document_id: uuid.UUID | None = None,
) -> list[SearchHit]:
    """Busca en los dos indices y fusiona los rankings.

    La rama vectorial encuentra parafrasis ("cuanto pague de impuestos" contra
    "IVA 13%"). La lexica encuentra literales que los embeddings pierden:
    numeros de factura, NIT, montos exactos. Ninguna de las dos sola alcanza.

    RRF fusiona por POSICION, no por puntaje. Es deliberado: una distancia
    coseno y un ts_rank no son comparables entre si, y normalizarlos exige
    calibrar pesos que cambian con cada corpus.
    """
    vector = embed_query(query)
    filter_clause = "AND c.document_id = :document_id" if document_id else ""

    statement = sql_text(f"""
        WITH vector_hits AS (
            SELECT c.id,
                   ROW_NUMBER() OVER (ORDER BY c.embedding <=> CAST(:vector AS vector)) AS rank
            FROM chunks c
            WHERE c.embedding IS NOT NULL {filter_clause}
            ORDER BY c.embedding <=> CAST(:vector AS vector)
            LIMIT :candidates
        ),
        text_hits AS (
            SELECT c.id,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank(c.tsv, websearch_to_tsquery('spanish', :query)) DESC
                   ) AS rank
            FROM chunks c
            WHERE c.tsv @@ websearch_to_tsquery('spanish', :query) {filter_clause}
            LIMIT :candidates
        ),
        fused AS (
            SELECT COALESCE(v.id, t.id) AS id,
                   COALESCE(1.0 / (:k + v.rank), 0.0)
                     + COALESCE(1.0 / (:k + t.rank), 0.0) AS score,
                   v.rank AS vector_rank,
                   t.rank AS text_rank
            FROM vector_hits v
            FULL OUTER JOIN text_hits t ON v.id = t.id
        )
        SELECT f.id, f.score, f.vector_rank, f.text_rank,
               c.document_id, c.page_number, c.content, d.filename
        FROM fused f
        JOIN chunks c ON c.id = f.id
        JOIN documents d ON d.id = c.document_id
        ORDER BY f.score DESC
        LIMIT :limit
    """)

    params = {
        "vector": str(vector),
        "query": query,
        "candidates": candidates,
        "limit": limit,
        "k": RRF_K,
    }
    if document_id:
        params["document_id"] = str(document_id)

    rows = db.execute(statement, params).all()

    return [
        SearchHit(
            chunk_id=row.id,
            document_id=row.document_id,
            filename=row.filename,
            page_number=row.page_number,
            content=row.content,
            score=float(row.score),
            vector_rank=int(row.vector_rank) if row.vector_rank else None,
            text_rank=int(row.text_rank) if row.text_rank else None,
        )
        for row in rows
    ]
