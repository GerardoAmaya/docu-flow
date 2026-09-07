"""Pipeline tasks.

Paso 4: OCR real. La extraccion estructurada llega en el paso 5 y los
embeddings en el 6; sus TODO estan marcados abajo.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Chunk, Document, DocumentStatus, ExtractedField, Invoice, Page
from app.services.extraction import (
    InvoiceExtraction,
    SYSTEM_PROMPT,
    build_prompt,
    ground_extraction,
    heuristic_extraction,
    parse_date,
    parse_money,
)
from app.services.llm import LLMClient
from app.services.ocr import ocr_document
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="docuflow.ping")
def ping() -> str:
    return "pong"


@celery_app.task(
    name="docuflow.process_document",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def process_document(self, document_id: str) -> dict:
    """Corre el pipeline completo sobre un documento.

    Idempotente: borra los resultados previos antes de empezar, asi reprocesar
    no duplica paginas ni chunks cuando cambias el prompt o el modelo.
    """
    db = SessionLocal()
    try:
        document = db.get(Document, document_id)
        if document is None:
            logger.warning("Document %s no longer exists, skipping.", document_id)
            return {"document_id": document_id, "skipped": True}

        db.execute(delete(Chunk).where(Chunk.document_id == document.id))
        db.execute(delete(ExtractedField).where(ExtractedField.document_id == document.id))
        db.execute(delete(Invoice).where(Invoice.document_id == document.id))
        db.execute(delete(Page).where(Page.document_id == document.id))

        document.status = DocumentStatus.ocr_running
        document.error_message = None
        db.commit()

        pages = ocr_document(document.storage_path, str(document.id))

        low_confidence_pages = 0
        for page in pages:
            if page.confidence < settings.ocr_min_confidence:
                low_confidence_pages += 1
            db.add(
                Page(
                    document_id=document.id,
                    page_number=page.page_number,
                    image_path=page.image_path,
                    width_px=page.width_px,
                    height_px=page.height_px,
                    ocr_text=page.text,
                    ocr_confidence=page.confidence,
                    ocr_words=[word.as_dict() for word in page.words],
                )
            )

        document.page_count = len(pages)
        db.commit()

        document.status = DocumentStatus.extracting
        db.commit()
        fields_needing_review = _extract_fields(db, document, pages)

        # TODO paso 6: chunking y embeddings

        # Cualquiera de las dos senales manda el documento a revision: OCR
        # pobre o campos que no pasaron la verificacion.
        document.status = (
            DocumentStatus.needs_review
            if low_confidence_pages or fields_needing_review
            else DocumentStatus.completed
        )
        document.processed_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(
            "Processed document %s: %s pages, %s low-confidence pages, %s fields to review",
            document_id,
            len(pages),
            low_confidence_pages,
            fields_needing_review,
        )
        return {
            "document_id": document_id,
            "pages": len(pages),
            "low_confidence_pages": low_confidence_pages,
            "fields_needing_review": fields_needing_review,
            "status": document.status.value,
        }

    except Exception as exc:
        db.rollback()
        document = db.get(Document, document_id)
        if document is not None:
            document.status = DocumentStatus.failed
            document.error_message = str(exc)[:2000]
            db.commit()
        logger.exception("Failed to process document %s", document_id)
        raise self.retry(exc=exc)
    finally:
        db.close()


def _extract_fields(db, document, ocr_pages) -> int:
    """Corre la extraccion estructurada y persiste campos e invoice.

    Devuelve cuantos campos quedaron por debajo del umbral de revision.
    """
    page_texts = [(p.page_number, p.text) for p in ocr_pages]
    pages_words = {p.page_number: [w.as_dict() for w in p.words] for p in ocr_pages}

    client = LLMClient(db, document_id=document.id)
    response = client.complete_json(
        system=SYSTEM_PROMPT,
        prompt=build_prompt(page_texts),
        purpose="invoice_extraction",
        mock_result=heuristic_extraction(page_texts),
    )

    extraction = InvoiceExtraction.model_validate(response.data)
    fields = ground_extraction(extraction, pages_words)

    needing_review = 0
    for name, grounded in fields.items():
        if grounded.needs_review and grounded.value is not None:
            needing_review += 1
        db.add(
            ExtractedField(
                document_id=document.id,
                field_name=name,
                value_text=grounded.value,
                confidence=grounded.confidence,
                page_number=grounded.page_number,
                bbox=grounded.bbox,
                source_snippet=grounded.source_snippet,
                needs_review=grounded.needs_review and grounded.value is not None,
            )
        )

    db.add(
        Invoice(
            document_id=document.id,
            vendor_name=fields["vendor_name"].value,
            vendor_tax_id=fields["vendor_tax_id"].value,
            buyer_name=fields["buyer_name"].value,
            buyer_tax_id=fields["buyer_tax_id"].value,
            invoice_number=fields["invoice_number"].value,
            issue_date=parse_date(fields["issue_date"].value),
            due_date=parse_date(fields["due_date"].value),
            currency=(fields["currency"].value or None),
            subtotal=parse_money(fields["subtotal"].value),
            tax_amount=parse_money(fields["tax_amount"].value),
            total=parse_money(fields["total"].value),
            line_items=[item.model_dump() for item in extraction.line_items] or None,
        )
    )
    db.commit()
    return needing_review
