"""API response schemas. Kept separate from ORM models so the database can
change shape without breaking the contract the frontend depends on."""

from __future__ import annotations

import uuid
from datetime import datetime

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
