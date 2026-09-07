"""Page-level endpoints: OCR text, word boxes, and rendered page images."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Document, Page
from app.schemas import PageDetail, PageList, PageSummary

router = APIRouter(prefix="/documents", tags=["pages"])


@router.get("/{document_id}/pages", response_model=PageList)
def list_pages(document_id: uuid.UUID, db: Session = Depends(get_db)) -> PageList:
    if db.get(Document, document_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")

    rows = db.scalars(
        select(Page).where(Page.document_id == document_id).order_by(Page.page_number)
    ).all()
    return PageList(items=[PageSummary.model_validate(row) for row in rows])


@router.get("/{document_id}/pages/{page_number}", response_model=PageDetail)
def get_page(
    document_id: uuid.UUID, page_number: int, db: Session = Depends(get_db)
) -> PageDetail:
    """Devuelve el texto OCR y las palabras con su bounding box.

    El frontend usa `ocr_words` para dibujar el resaltado sobre la imagen
    cuando el usuario hace clic en un campo extraido.
    """
    page = db.scalar(
        select(Page).where(
            Page.document_id == document_id, Page.page_number == page_number
        )
    )
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found.")
    return PageDetail.model_validate(page)


@router.get("/{document_id}/pages/{page_number}/image")
def get_page_image(
    document_id: uuid.UUID, page_number: int, db: Session = Depends(get_db)
) -> FileResponse:
    page = db.scalar(
        select(Page).where(
            Page.document_id == document_id, Page.page_number == page_number
        )
    )
    if page is None or not page.image_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page image not found.")

    path = Path(page.image_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page image file is missing.")
    return FileResponse(path, media_type="image/jpeg")
