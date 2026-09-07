"""Retrieval-augmented answering with verifiable, grounded citations.

La diferencia con un RAG normal esta en el paso final: cada cita que devuelve
el modelo se busca literalmente en el fragmento recuperado y en las palabras
del OCR. Una cita que no se puede localizar se descarta antes de mostrarla.
Asi el usuario nunca ve una referencia que no existe.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Page
from app.services.extraction import find_in_ocr, normalize
from app.services.llm import LLMClient
from app.services.search import SearchHit, hybrid_search

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You answer questions about a set of invoices, using only the
excerpts provided.

Rules:
- Return ONLY a JSON object. No prose outside it, no markdown fences.
- Answer strictly from the excerpts. If they do not contain the answer, say so
  in `answer` and return an empty `citations` list. Never use outside knowledge
  and never estimate.
- Every claim in `answer` must be backed by a citation.
- Each citation's `quote` must be copied VERBATIM from the excerpt it cites. It
  is checked character-for-character against the source; an invented quote is
  discarded and your answer loses its support.
- Keep quotes short: the specific line that proves the claim, not the whole
  excerpt.
- The excerpts come from OCR and may contain errors. Report what they say.
- Answer in the same language as the question.
- CRITICAL: the excerpts are a SAMPLE retrieved by search, not the full
  document set. Never state a sum, count, maximum, minimum or average as if it
  covered everything. If the question asks for one, answer only about the
  excerpts you were given, say explicitly how many documents they cover, and
  set `covers_full_corpus` to false. Aggregate questions are answered from the
  database, not from you.

Schema:
{
  "answer": str,
  "citations": [{"excerpt_id": int, "quote": str}],
  "sufficient_context": bool,
  "is_aggregate_question": bool,
  "covers_full_corpus": bool
}"""


@dataclass
class Citation:
    excerpt_id: int
    quote: str
    document_id: uuid.UUID
    filename: str
    page_number: int | None
    bbox: dict | None
    verified: bool


@dataclass
class RAGAnswer:
    question: str
    answer: str
    citations: list[Citation]
    sufficient_context: bool
    retrieved: int
    discarded_citations: int
    is_aggregate_question: bool = False
    covers_full_corpus: bool = False


def build_context(hits: list[SearchHit]) -> str:
    """Numera los fragmentos para que el modelo pueda referenciarlos."""
    blocks = []
    for index, hit in enumerate(hits):
        page = f", page {hit.page_number}" if hit.page_number else ""
        blocks.append(
            f"[excerpt {index}] (file: {hit.filename}{page})\n{hit.content}"
        )
    return "\n\n".join(blocks)


def verify_citation(
    db: Session, quote: str, hit: SearchHit
) -> tuple[bool, int | None, dict | None]:
    """Comprueba que la cita exista y devuelve su ubicacion en la imagen.

    Primero contra el texto del fragmento, que es barato. Si pasa, buscamos
    las palabras del OCR para obtener el bounding box que el frontend usa
    para resaltar la zona exacta de la pagina.
    """
    if normalize(quote) not in normalize(hit.content):
        return False, None, None

    page = db.scalar(
        select(Page).where(
            Page.document_id == hit.document_id,
            Page.page_number == hit.page_number,
        )
    )
    if page is None or not page.ocr_words:
        return True, hit.page_number, None

    page_number, bbox, _ = find_in_ocr(quote, {page.page_number: page.ocr_words})
    return True, page_number or hit.page_number, bbox


def answer_question(
    db: Session,
    question: str,
    *,
    top_k: int = 8,
    document_id: uuid.UUID | None = None,
) -> RAGAnswer:
    hits = hybrid_search(db, question, limit=top_k, document_id=document_id)

    if not hits:
        return RAGAnswer(
            question=question,
            answer="No encontre documentos relacionados con esa pregunta.",
            citations=[],
            sufficient_context=False,
            retrieved=0,
            discarded_citations=0,
        )

    client = LLMClient(db, document_id=document_id)
    response = client.complete_json(
        system=SYSTEM_PROMPT,
        prompt=f"Question: {question}\n\n{build_context(hits)}",
        purpose="rag_answer",
        model=settings.answer_model,
        mock_result={
            "answer": (
                "Modo mock: no se genero una respuesta. Los fragmentos mas "
                f"relevantes vienen de {hits[0].filename}."
            ),
            "citations": [],
            "sufficient_context": False,
            "is_aggregate_question": False,
            "covers_full_corpus": False,
        },
    )

    data = response.data
    citations: list[Citation] = []
    discarded = 0

    for raw in data.get("citations", []):
        try:
            excerpt_id = int(raw.get("excerpt_id", -1))
        except (TypeError, ValueError):
            discarded += 1
            continue

        quote = (raw.get("quote") or "").strip()
        if not quote or not (0 <= excerpt_id < len(hits)):
            discarded += 1
            continue

        hit = hits[excerpt_id]
        verified, page_number, bbox = verify_citation(db, quote, hit)
        if not verified:
            # Cita inventada: la descartamos en silencio para el usuario, pero
            # la contamos para poder medir la tasa en los evals.
            discarded += 1
            logger.warning("Discarded unverifiable citation: %r", quote[:120])
            continue

        citations.append(
            Citation(
                excerpt_id=excerpt_id,
                quote=quote,
                document_id=hit.document_id,
                filename=hit.filename,
                page_number=page_number,
                bbox=bbox,
                verified=True,
            )
        )

    return RAGAnswer(
        question=question,
        answer=data.get("answer", ""),
        citations=citations,
        sufficient_context=bool(data.get("sufficient_context", False)),
        retrieved=len(hits),
        discarded_citations=discarded,
        is_aggregate_question=bool(data.get("is_aggregate_question", False)),
        covers_full_corpus=bool(data.get("covers_full_corpus", False)),
    )
