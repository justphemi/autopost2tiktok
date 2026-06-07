
from celery import Celery
from app.config import settings

celery_app = Celery(
    "boltreels",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_acks_late=True,           # Don't ack until task is done (safe re-queue on crash)
    worker_prefetch_multiplier=1,  # Process one job at a time per worker
)
