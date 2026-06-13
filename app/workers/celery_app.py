"""
app/workers/celery_app.py
──────────────────────────
Celery application factory.

Queues
──────
  default    – general background tasks (email, push notifications, …)
  telemetry  – high-throughput sensor data processing

Task autodiscovery scans the app.workers.tasks package.
"""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "rawbin",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.tasks.telemetry",
        "app.workers.tasks.notifications",
    ],
)

from kombu import Queue

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Queues
    task_default_queue="default",
    task_queues=(
        Queue("default", routing_key="default"),
        Queue("telemetry", routing_key="telemetry"),
    ),
    # Reliability
    task_acks_late=True,          # Ack only after task completes (ECS-safe)
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, # Prevent starvation of other workers
    # Results
    result_expires=3600,          # Clean up results after 1 hour
)
