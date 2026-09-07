"""Hybrid search endpoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas import SearchResponse, SearchResult
from app.services.search import hybrid_search

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=2, description="Consulta en lenguaje natural"),
    limit: int = Query(default=10, ge=1, le=50),
    document_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
) -> SearchResponse:
    """Busca fragmentos combinando similitud vectorial y full-text.

    `vector_rank` y `text_rank` se exponen a proposito: dejan ver cual de las
    dos ramas encontro cada resultado, que es justo lo que hace falta para
    entender por que la busqueda hibrida gana sobre cualquiera de las dos.
    """
    hits = hybrid_search(db, q, limit=limit, document_id=document_id)
    return SearchResponse(
        query=q,
        items=[
            SearchResult(
                chunk_id=h.chunk_id,
                document_id=h.document_id,
                filename=h.filename,
                page_number=h.page_number,
                content=h.content,
                score=round(h.score, 6),
                vector_rank=h.vector_rank,
                text_rank=h.text_rank,
            )
            for h in hits
        ],
    )
