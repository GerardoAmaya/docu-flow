from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "docuflow",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Amplio a proposito: con el espaciado de la API de embeddings, un
    # documento de varias paginas puede pasar varios minutos esperando turno.
    task_time_limit=1800,
    task_soft_time_limit=1740,
    result_expires=3600,
)
