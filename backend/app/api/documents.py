"""Document upload and listing endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Document, DocumentStatus
from app.schemas import DocumentList, DocumentSummary, UploadResponse
from app.services.storage import (
    FileTooLarge,
    UnsupportedFileType,
    store_upload,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResponse:
    """Recibe un archivo, lo deduplica por hash y encola su procesamiento.

    Subir el mismo archivo dos veces devuelve el documento original con
    `duplicate: true` en vez de un error. Asi el cliente puede reintentar
    una subida fallida sin miedo a crear registros repetidos.
    """
    try:
        stored = store_upload(file.file)
    except UnsupportedFileType as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except FileTooLarge as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc

    existing = db.scalar(
        select(Document).where(Document.content_hash == stored.content_hash)
    )
    if existing is not None:
        return UploadResponse(
            document=DocumentSummary.model_validate(existing), duplicate=True
        )

    document = Document(
        filename=file.filename or f"upload{stored.content_hash[:8]}",
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        content_hash=stored.content_hash,
        storage_path=stored.storage_path,
        status=DocumentStatus.pending,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # El encolado va DESPUES del commit a proposito. Si encolamos antes, el
    # worker puede levantar la tarea y no encontrar la fila todavia.
    from app.workers.tasks import process_document

    process_document.delay(str(document.id))

    return UploadResponse(
        document=DocumentSummary.model_validate(document), duplicate=False
    )


@router.get("", response_model=DocumentList)
def list_documents(
    status_filter: DocumentStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> DocumentList:
    """Lista documentos, del mas reciente al mas antiguo."""
    conditions = []
    if status_filter is not None:
        conditions.append(Document.status == status_filter)

    total = db.scalar(
        select(func.count()).select_from(Document).where(*conditions)
    ) or 0

    rows = db.scalars(
        select(Document)
        .where(*conditions)
        .order_by(Document.uploaded_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return DocumentList(
        items=[DocumentSummary.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentSummary)
def get_document(document_id: uuid.UUID, db: Session = Depends(get_db)) -> DocumentSummary:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return DocumentSummary.model_validate(document)


@router.post("/{document_id}/reprocess", response_model=DocumentSummary)
def reprocess_document(
    document_id: uuid.UUID, db: Session = Depends(get_db)
) -> DocumentSummary:
    """Reencola un documento. Util cuando mejoras el prompt de extraccion
    y queres volver a correr el pipeline sin resubir el archivo."""
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")

    document.status = DocumentStatus.pending
    document.error_message = None
    db.commit()
    db.refresh(document)

    from app.workers.tasks import process_document

    process_document.delay(str(document.id))
    return DocumentSummary.model_validate(document)
