"""API response schemas. Kept separate from ORM models so the database can
change shape without breaking the contract the frontend depends on."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import DocumentStatus


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    mime_type: str
    size_bytes: int
    status: DocumentStatus
    page_count: int | None = None
    uploaded_at: datetime
    processed_at: datetime | None = None
    error_message: str | None = None


class UploadResponse(BaseModel):
    document: DocumentSummary
    # True cuando el archivo ya existia: no se reprocesa ni se cobran tokens.
    duplicate: bool = Field(
        description="True if this exact file was already uploaded before."
    )


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class PageSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page_number: int
    width_px: int | None = None
    height_px: int | None = None
    ocr_confidence: float | None = None


class PageDetail(PageSummary):
    ocr_text: str | None = None
    # Palabras con bbox normalizado 0-1: [{"text","conf","x","y","w","h"}, ...]
    ocr_words: list[dict] | None = None


class PageList(BaseModel):
    items: list[PageSummary]


class InvoiceDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    buyer_name: str | None = None
    buyer_tax_id: str | None = None
    invoice_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: float | None = None
    tax_amount: float | None = None
    total: float | None = None
    line_items: list[dict] | None = None


class FieldDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    field_name: str
    value_text: str | None = None
    corrected_value: str | None = None
    confidence: float
    page_number: int | None = None
    bbox: dict | None = None
    source_snippet: str | None = None
    needs_review: bool
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None


class FieldList(BaseModel):
    items: list[FieldDetail]


class FieldCorrection(BaseModel):
    corrected_value: str | None = Field(
        default=None, description="Valor correcto segun el revisor. null borra el campo."
    )
    reviewed_by: str | None = Field(default=None, max_length=256)


class ReviewQueueItem(BaseModel):
    document_id: uuid.UUID
    filename: str
    status: DocumentStatus
    pending_fields: int
    lowest_confidence: float


class ReviewQueue(BaseModel):
    items: list[ReviewQueueItem]


class CostStats(BaseModel):
    total_calls: int
    input_tokens: int
    output_tokens: int
    total_cost_usd: float
    avg_latency_ms: float
    mocked_calls: int
    failed_calls: int
    documents_processed: int
    cost_per_document_usd: float
    projected_cost_per_1000_docs_usd: float


class SearchResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page_number: int | None = None
    content: str
    score: float
    # Posicion en cada una de las dos ramas. None significa que esa rama no
    # encontro el fragmento, lo que hace visible el aporte de cada indice.
    vector_rank: int | None = None
    text_rank: int | None = None


class SearchResponse(BaseModel):
    query: str
    items: list[SearchResult]


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=20)
    # Limita la busqueda a un documento. Util para "preguntale a esta factura".
    document_id: uuid.UUID | None = None


class CitationOut(BaseModel):
    quote: str
    document_id: uuid.UUID
    filename: str
    page_number: int | None = None
    # Zona de la pagina que respalda la cita, normalizada 0-1.
    bbox: dict | None = None


class ChatResponse(BaseModel):
    question: str
    answer: str
    sufficient_context: bool
    retrieved_chunks: int
    discarded_citations: int
    citations: list[CitationOut]
