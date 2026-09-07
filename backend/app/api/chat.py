"""RAG question answering endpoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas import ChatRequest, ChatResponse, CitationOut
from app.services.rag import answer_question

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    """Responde una pregunta sobre los documentos indexados.

    `discarded_citations` se expone a proposito: es la cantidad de referencias
    que el modelo produjo y no se pudieron verificar contra el texto original.
    Un numero alto significa que el modelo esta inventando y que hay que
    revisar el prompt o el recuperador.
    """
    result = answer_question(
        db,
        request.question,
        top_k=request.top_k,
        document_id=request.document_id,
    )
    return ChatResponse(
        question=result.question,
        answer=result.answer,
        sufficient_context=result.sufficient_context,
        retrieved_chunks=result.retrieved,
        discarded_citations=result.discarded_citations,
        is_aggregate_question=result.is_aggregate_question,
        covers_full_corpus=result.covers_full_corpus,
        citations=[
            CitationOut(
                quote=c.quote,
                document_id=c.document_id,
                filename=c.filename,
                page_number=c.page_number,
                bbox=c.bbox,
            )
            for c in result.citations
        ],
    )
