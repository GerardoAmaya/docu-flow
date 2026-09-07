"""Invoice data, human review queue, and cost dashboard endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text as sql_text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Document, ExtractedField, Invoice, LLMCall
from app.schemas import (
    AggregateSummary,
    CostStats,
    FieldCorrection,
    FieldDetail,
    FieldList,
    InvoiceDetail,
    ReviewQueue,
    ReviewQueueItem,
    VendorGroup,
    VendorGroupList,
    VendorTotal,
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


@router.get("/invoices/summary", response_model=AggregateSummary)
def invoice_summary(
    limit_vendors: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> AggregateSummary:
    """Agregados sobre TODAS las facturas, calculados en SQL.

    Existe porque el RAG no puede responder esto de forma confiable: un
    recuperador top-k solo ve una muestra del corpus y termina afirmando
    "el total" sobre un subconjunto. La extraccion estructurada del paso 5
    es la que hace posible responderlo bien.
    """
    totals = db.execute(
        select(
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.subtotal), 0),
            func.coalesce(func.sum(Invoice.tax_amount), 0),
            func.coalesce(func.sum(Invoice.total), 0),
            func.min(Invoice.issue_date),
            func.max(Invoice.issue_date),
        )
    ).one()

    # Cuantas facturas aportaron cada monto: un total sobre 9 de 12 facturas
    # no es el total, y el consumidor de la API tiene que poder saberlo.
    complete = db.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.total.isnot(None))
    ) or 0

    vendors = db.execute(
        select(
            Invoice.vendor_name,
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total), 0),
        )
        .where(Invoice.vendor_name.isnot(None))
        .group_by(Invoice.vendor_name)
        .order_by(func.sum(Invoice.total).desc().nulls_last())
        .limit(limit_vendors)
    ).all()

    return AggregateSummary(
        invoice_count=totals[0],
        invoices_with_total=complete,
        subtotal_sum=float(totals[1]),
        tax_sum=float(totals[2]),
        total_sum=float(totals[3]),
        earliest_issue_date=totals[4],
        latest_issue_date=totals[5],
        by_vendor=[
            VendorTotal(
                vendor_name=v[0], invoice_count=v[1], total_sum=float(v[2])
            )
            for v in vendors
        ],
    )


@router.get("/invoices/vendors", response_model=VendorGroupList)
def vendors_deduplicated(
    threshold: float = Query(default=0.55, ge=0.1, le=1.0),
    db: Session = Depends(get_db),
) -> VendorGroupList:
    """Agrupa proveedores tolerando variaciones de OCR.

    El OCR lee "Servicios Informaticos Pipil, S.A. de C.V." en un documento y
    "Servicios informaticos Pipil, S.A." en otro. Agrupar por texto exacto
    parte el mismo proveedor en dos y arruina cualquier reporte.

    Usamos similitud de trigramas de pg_trgm, apoyada en el indice
    ix_invoices_vendor_trgm. Como canonico elegimos la variante con mas
    facturas y, a igualdad, la mas larga: el OCR tiende a truncar, no a
    inventar texto de mas.

    Limitacion: la asignacion es voraz, no un clustering transitivo. Si A se
    parece a B y B a C pero A no a C, el agrupamiento depende del orden. Con
    catalogos de proveedores reales alcanza; a escala convendria un algoritmo
    de componentes conexas.
    """
    statement = sql_text("""
        WITH names AS (
            SELECT vendor_name,
                   COUNT(*) AS invoice_count,
                   COALESCE(SUM(total), 0) AS total_sum
            FROM invoices
            WHERE vendor_name IS NOT NULL
            GROUP BY vendor_name
        ),
        canonical AS (
            SELECT a.vendor_name,
                   a.invoice_count,
                   a.total_sum,
                   (
                       SELECT b.vendor_name FROM names b
                       WHERE similarity(a.vendor_name, b.vendor_name) >= :threshold
                       ORDER BY b.invoice_count DESC,
                                length(b.vendor_name) DESC,
                                b.vendor_name ASC
                       LIMIT 1
                   ) AS canonical_name
            FROM names a
        )
        SELECT canonical_name,
               SUM(invoice_count) AS invoice_count,
               SUM(total_sum) AS total_sum,
               array_agg(vendor_name ORDER BY vendor_name) AS variants
        FROM canonical
        GROUP BY canonical_name
        ORDER BY SUM(total_sum) DESC
    """)

    rows = db.execute(statement, {"threshold": threshold}).all()
    return VendorGroupList(
        threshold=threshold,
        items=[
            VendorGroup(
                canonical_name=r.canonical_name,
                invoice_count=r.invoice_count,
                total_sum=float(r.total_sum),
                variants=list(r.variants),
                merged=len(r.variants) > 1,
            )
            for r in rows
        ],
    )
