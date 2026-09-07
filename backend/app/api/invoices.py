"""Invoice data, human review queue, and cost dashboard endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Document, ExtractedField, Invoice, LLMCall
from app.schemas import (
    CostStats,
    FieldCorrection,
    FieldDetail,
    FieldList,
    InvoiceDetail,
    ReviewQueue,
    ReviewQueueItem,
)
from app.services.extraction import parse_date, parse_money

router = APIRouter(tags=["invoices"])

# Como se refleja cada campo corregido en la tabla invoices.
INVOICE_COLUMNS: dict[str, str] = {
    "vendor_name": "text",
    "vendor_tax_id": "text",
    "buyer_name": "text",
    "buyer_tax_id": "text",
    "invoice_number": "text",
    "currency": "text",
    "issue_date": "date",
    "due_date": "date",
    "subtotal": "money",
    "tax_amount": "money",
    "total": "money",
}


@router.get("/documents/{document_id}/invoice", response_model=InvoiceDetail)
def get_invoice(document_id: uuid.UUID, db: Session = Depends(get_db)) -> InvoiceDetail:
    invoice = db.scalar(select(Invoice).where(Invoice.document_id == document_id))
    if invoice is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No invoice extracted for this document yet."
        )
    return InvoiceDetail.model_validate(invoice)


@router.get("/documents/{document_id}/fields", response_model=FieldList)
def list_fields(
    document_id: uuid.UUID,
    only_review: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> FieldList:
    """Campos extraidos con su confianza, procedencia y bounding box.

    El frontend usa `bbox` y `page_number` para resaltar el recorte de la
    imagen junto al campo que el usuario esta corrigiendo.
    """
    conditions = [ExtractedField.document_id == document_id]
    if only_review:
        conditions.append(ExtractedField.needs_review.is_(True))

    rows = db.scalars(
        select(ExtractedField).where(*conditions).order_by(ExtractedField.field_name)
    ).all()
    return FieldList(items=[FieldDetail.model_validate(r) for r in rows])


@router.patch("/fields/{field_id}", response_model=FieldDetail)
def correct_field(
    field_id: uuid.UUID,
    correction: FieldCorrection,
    db: Session = Depends(get_db),
) -> FieldDetail:
    """Guarda la correccion humana y la propaga a la tabla invoices.

    El valor original del modelo se conserva en `value_text`. Guardar ambos
    es lo que convierte el uso normal de la app en un set de evaluacion
    etiquetado, sin pedirle a nadie que etiquete nada.
    """
    field = db.get(ExtractedField, field_id)
    if field is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Field not found.")

    field.corrected_value = correction.corrected_value
    field.reviewed_at = datetime.now(timezone.utc)
    field.reviewed_by = correction.reviewed_by
    field.needs_review = False

    # Sin esta propagacion, el usuario corrige y el dato sigue mal en la tabla
    # que alimenta los reportes. Es el bug silencioso mas facil de cometer aca.
    kind = INVOICE_COLUMNS.get(field.field_name)
    if kind:
        invoice = db.scalar(
            select(Invoice).where(Invoice.document_id == field.document_id)
        )
        if invoice is not None:
            raw = correction.corrected_value
            if kind == "money":
                setattr(invoice, field.field_name, parse_money(raw))
            elif kind == "date":
                setattr(invoice, field.field_name, parse_date(raw))
            else:
                setattr(invoice, field.field_name, raw)

    db.commit()
    db.refresh(field)
    return FieldDetail.model_validate(field)


@router.get("/review/queue", response_model=ReviewQueue)
def review_queue(
    limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)
) -> ReviewQueue:
    """Documentos con campos pendientes, del que tiene mas problemas primero."""
    pending = (
        select(
            ExtractedField.document_id.label("document_id"),
            func.count().label("pending_fields"),
            func.min(ExtractedField.confidence).label("lowest_confidence"),
        )
        .where(ExtractedField.needs_review.is_(True))
        .group_by(ExtractedField.document_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Document.id,
            Document.filename,
            Document.status,
            pending.c.pending_fields,
            pending.c.lowest_confidence,
        )
        .join(pending, pending.c.document_id == Document.id)
        .order_by(pending.c.pending_fields.desc(), pending.c.lowest_confidence.asc())
        .limit(limit)
    ).all()

    return ReviewQueue(
        items=[
            ReviewQueueItem(
                document_id=r.id,
                filename=r.filename,
                status=r.status,
                pending_fields=r.pending_fields,
                lowest_confidence=r.lowest_confidence,
            )
            for r in rows
        ]
    )


@router.get("/stats/cost", response_model=CostStats)
def cost_stats(db: Session = Depends(get_db)) -> CostStats:
    """Costo, latencia y volumen de llamadas al modelo.

    Es lo que permite responder "cuanto cuesta procesar mil facturas" con un
    numero medido y no con una estimacion.
    """
    row = db.execute(
        select(
            func.count().label("calls"),
            func.coalesce(func.sum(LLMCall.input_tokens), 0),
            func.coalesce(func.sum(LLMCall.output_tokens), 0),
            func.coalesce(func.sum(LLMCall.cost_usd), 0),
            func.coalesce(func.avg(LLMCall.latency_ms), 0),
            func.count().filter(LLMCall.was_mocked.is_(True)),
            func.count().filter(LLMCall.error.isnot(None)),
        )
    ).one()

    documents = db.scalar(select(func.count()).select_from(Document)) or 0
    total_cost = float(row[3])

    return CostStats(
        total_calls=row[0],
        input_tokens=row[1],
        output_tokens=row[2],
        total_cost_usd=round(total_cost, 6),
        avg_latency_ms=round(float(row[4]), 1),
        mocked_calls=row[5],
        failed_calls=row[6],
        documents_processed=documents,
        cost_per_document_usd=round(total_cost / documents, 6) if documents else 0.0,
        projected_cost_per_1000_docs_usd=(
            round(total_cost / documents * 1000, 2) if documents else 0.0
        ),
    )
