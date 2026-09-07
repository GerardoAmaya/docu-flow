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
from app.models import Chunk, Document, DocumentStatus, Page
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

        # TODO paso 5: extraccion estructurada con schema y confianza
        # TODO paso 6: chunking y embeddings

        # Si alguna pagina salio con OCR pobre, el documento entero necesita
        # ojo humano: la extraccion posterior va a heredar esa basura.
        document.status = (
            DocumentStatus.needs_review
            if low_confidence_pages
            else DocumentStatus.completed
        )
        document.processed_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(
            "Processed document %s: %s pages, %s below confidence threshold",
            document_id,
            len(pages),
            low_confidence_pages,
        )
        return {
            "document_id": document_id,
            "pages": len(pages),
            "low_confidence_pages": low_confidence_pages,
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
