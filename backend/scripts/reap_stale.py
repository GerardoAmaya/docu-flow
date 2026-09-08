#!/usr/bin/env python3
"""Requeue documents left stranded in a non-terminal state.

Si el worker muere a mitad de un documento —un despliegue, un reinicio, un
OOM— la fila queda en `ocr_running`, `extracting` o `embedding` para siempre.
Nadie la va a retomar: Celery perdio la tarea y nada vuelve a mirar esa fila.

Se corre al arrancar el contenedor, antes de levantar el worker. Solo toca
documentos que llevan mas de `stale_after_minutes` sin avanzar, para no
reencolar los que estan procesandose ahora mismo en otra replica.

Uso:
    python -m scripts.reap_stale
    python -m scripts.reap_stale --minutes 30 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_, select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.models import Document, DocumentStatus  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("reap_stale")

# Estados intermedios: el documento entro al pipeline y no salio.
STRANDED_STATES = [
    DocumentStatus.pending,
    DocumentStatus.ocr_running,
    DocumentStatus.extracting,
    DocumentStatus.embedding,
]


def reap(minutes: int, dry_run: bool) -> int:
    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    db = SessionLocal()
    try:
        stranded = db.scalars(
            select(Document)
            .where(Document.status.in_(STRANDED_STATES))
            # processed_at es NULL mientras no termina, asi que nos apoyamos
            # en uploaded_at para medir cuanto lleva atascado.
            .where(or_(Document.uploaded_at < cutoff, Document.uploaded_at.is_(None)))
            .order_by(Document.uploaded_at)
        ).all()

        if not stranded:
            logger.info("No hay documentos atascados.")
            return 0

        logger.info("%s documentos atascados mas de %s minutos:", len(stranded), minutes)
        for document in stranded:
            logger.info("  %s (%s)", document.filename, document.status.value)

        if dry_run:
            logger.info("dry-run: no se reencolo nada.")
            return len(stranded)

        # Importado aca adentro para que --dry-run funcione sin Redis vivo.
        from app.workers.tasks import process_document

        for document in stranded:
            # El estado original se captura antes de pisarlo: es el dato util
            # para saber en que etapa se cae el pipeline cuando pasa seguido.
            previous = document.status.value
            document.status = DocumentStatus.pending
            document.error_message = (
                f"Reencolado automaticamente: quedo en {previous} tras un reinicio del worker."
            )
        db.commit()

        for document in stranded:
            process_document.delay(str(document.id))

        logger.info("%s documentos reencolados.", len(stranded))
        return len(stranded)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--minutes",
        type=int,
        default=15,
        help="Antiguedad minima para considerar un documento atascado.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        reap(args.minutes, args.dry_run)
    except Exception:
        # Un fallo aca no debe impedir que el servicio arranque: es una tarea
        # de mantenimiento, no una precondicion.
        logger.exception("El barrido fallo; el arranque continua igual.")


if __name__ == "__main__":
    main()
