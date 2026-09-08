"""Pipeline tasks.

Paso 4: OCR real. La extraccion estructurada llega en el paso 5 y los
embeddings en el 6; sus TODO estan marcados abajo.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Chunk, Document, DocumentStatus, ExtractedField, Invoice, Page
from app.services.embeddings import chunk_text, embed_passages
from app.services.extraction import (
    SYSTEM_PROMPT,
    InvoiceExtraction,
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
    max_retries=5,
    # Con el espaciado de la API un documento puede tardar minutos. Reintentar
    # a los 30 segundos solo agrega presion sobre el mismo limite.
    default_retry_delay=120,
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

        document.status = DocumentStatus.embedding
        db.commit()
        chunk_count = _index_chunks(db, document, pages)

        # Cualquiera de las dos senales manda el documento a revision: OCR
        # pobre o campos que no pasaron la verificacion.
        document.status = (
            DocumentStatus.needs_review
            if low_confidence_pages or fields_needing_review
            else DocumentStatus.completed
        )
        document.processed_at = datetime.now(UTC)
        db.commit()

        logger.info(
            "Processed document %s: %s pages, %s low-confidence pages, "
            "%s fields to review, %s chunks indexed",
            document_id,
            len(pages),
            low_confidence_pages,
            fields_needing_review,
            chunk_count,
        )
        return {
            "document_id": document_id,
            "pages": len(pages),
            "low_confidence_pages": low_confidence_pages,
            "fields_needing_review": fields_needing_review,
            "chunks": chunk_count,
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
        raise self.retry(exc=exc) from exc
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


def _index_chunks(db, document, ocr_pages) -> int:
    """Parte el texto de cada pagina y guarda los chunks con su embedding.

    Vectorizamos todos los fragmentos en una sola llamada al modelo: el costo
    dominante es cargar el batch, no procesarlo, asi que hacerlo uno por uno
    seria varias veces mas lento.
    """
    page_ids = {p.page_number: p.id for p in document.pages}

    pending: list[tuple[int, str]] = []
    for page in ocr_pages:
        pending.extend(chunk_text(page.text, page.page_number))

    if not pending:
        return 0

    vectors = embed_passages([content for _, content in pending])

    for index, ((page_number, content), vector) in enumerate(
        zip(pending, vectors, strict=True)
    ):
        db.add(
            Chunk(
                document_id=document.id,
                page_id=page_ids.get(page_number),
                page_number=page_number,
                chunk_index=index,
                content=content,
                embedding=vector,
            )
        )
    db.commit()
    return len(pending)
