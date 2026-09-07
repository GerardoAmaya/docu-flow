from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class DocumentStatus(str, enum.Enum):
    pending = "pending"
    ocr_running = "ocr_running"
    extracting = "extracting"
    embedding = "embedding"
    needs_review = "needs_review"
    completed = "completed"
    failed = "failed"


class Document(Base):
    """Un archivo subido. El hash evita reprocesar el mismo documento dos veces."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # sha256 del contenido: deduplicación barata y determinista.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        nullable=False,
        default=DocumentStatus.pending,
        index=True,
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pages: Mapped[list[Page]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    invoice: Mapped[Invoice | None] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False
    )
    fields: Mapped[list[ExtractedField]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Page(Base):
    """Una página rasterizada + su texto OCR. Guardamos ancho/alto para poder
    escalar las bounding boxes al tamaño que muestre el frontend."""

    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(1024))
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)

    ocr_text: Mapped[str | None] = mapped_column(Text)
    # Confianza media que reporta Tesseract (0-100) para esta página.
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    # Palabras con su bbox: [{"text","conf","x","y","w","h"}, ...]
    ocr_words: Mapped[list | None] = mapped_column(JSONB)

    document: Mapped[Document] = relationship(back_populates="pages")


class Chunk(Base):
    """Fragmento de texto indexado para búsqueda híbrida.

    `embedding` alimenta la parte vectorial y la columna generada `tsv`
    alimenta la parte léxica (full-text de Postgres). Fusionamos ambos
    resultados con Reciprocal Rank Fusion en la capa de servicio.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_pages.id", ondelete="SET NULL")
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))

    document: Mapped[Document] = relationship(back_populates="chunks")


class Invoice(Base):
    """Datos estructurados extraídos de una factura o recibo.

    Todo es nullable a propósito: un recibo de gasolina arrugado no trae NIT.
    Preferimos un NULL honesto a un valor inventado por el modelo.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("total >= 0", name="ck_invoices_total_non_negative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    vendor_name: Mapped[str | None] = mapped_column(String(512), index=True)
    vendor_tax_id: Mapped[str | None] = mapped_column(String(64), index=True)
    buyer_name: Mapped[str | None] = mapped_column(String(512))
    buyer_tax_id: Mapped[str | None] = mapped_column(String(64))

    invoice_number: Mapped[str | None] = mapped_column(String(128), index=True)
    issue_date: Mapped[date | None] = mapped_column(Date, index=True)
    due_date: Mapped[date | None] = mapped_column(Date)

    currency: Mapped[str | None] = mapped_column(String(3))
    subtotal: Mapped[float | None] = mapped_column(Numeric(14, 2))
    tax_amount: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total: Mapped[float | None] = mapped_column(Numeric(14, 2), index=True)

    # Líneas de detalle: [{"description","quantity","unit_price","amount"}, ...]
    line_items: Mapped[list | None] = mapped_column(JSONB)
    # Si el origen fue un DTE (JSON de facturación electrónica) guardamos el payload.
    source_payload: Mapped[dict | None] = mapped_column(JSONB)

    document: Mapped[Document] = relationship(back_populates="invoice")


class ExtractedField(Base):
    """Un campo individual con su procedencia y su confianza.

    Esta tabla es el corazón del human-in-the-loop: la UI de revisión lista
    los campos con `needs_review = true`, muestra el recorte de la imagen
    usando `bbox` y deja al usuario corregir el valor. La corrección queda
    guardada aparte del valor original, lo que nos da un set de entrenamiento
    y de evaluación gratis.
    """

    __tablename__ = "extracted_fields"
    __table_args__ = (
        UniqueConstraint("document_id", "field_name"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_fields_confidence_range"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Procedencia: dónde en el documento vive este dato.
    page_number: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict | None] = mapped_column(JSONB)  # {"x","y","w","h"} normalizado 0-1
    source_snippet: Mapped[str | None] = mapped_column(Text)

    needs_review: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false", index=True
    )
    corrected_value: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(256))

    document: Mapped[Document] = relationship(back_populates="fields")

    @property
    def final_value(self) -> str | None:
        """Lo que el humano dijo gana sobre lo que dijo el modelo."""
        return self.corrected_value if self.corrected_value is not None else self.value_text


class LLMCall(Base):
    """Una fila por llamada al modelo. Alimenta el panel de costo y latencia.

    Sin esto no puedes responder '¿cuánto cuesta procesar mil facturas?',
    que es la primera pregunta que hace cualquiera que pague la cuenta.
    """

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    was_mocked: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
