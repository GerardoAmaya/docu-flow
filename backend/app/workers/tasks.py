"""Tareas del pipeline. En el paso 1 solo probamos que el worker viva.

El pipeline real (OCR -> extraccion -> embeddings) llega en los pasos 4 a 6.
"""
from app.workers.celery_app import celery_app


@celery_app.task(name="docuflow.ping")
def ping() -> str:
    return "pong"
