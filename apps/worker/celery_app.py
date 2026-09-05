import uuid
from datetime import UTC, datetime

from celery import Celery
from chaseless.core.settings import get_settings
from chaseless.db.models import OutboxEvent, WebhookEvent
from chaseless.db.session import session_scope
from chaseless.services.actions import execute_action
from chaseless.services.event_processor import process_webhook_event

settings = get_settings()
celery_app = Celery(
    "chaseless",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    beat_schedule={
        "dispatch-outbox": {
            "task": "chaseless.dispatch_outbox",
            "schedule": 2.0,
        }
    },
)


@celery_app.task(
    name="chaseless.process_webhook",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def process_webhook_task(self, webhook_event_id: str) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        try:
            process_webhook_event(db, uuid.UUID(webhook_event_id), settings=settings)
        except Exception as exc:
            event = db.get(WebhookEvent, uuid.UUID(webhook_event_id))
            if event:
                event.processing_error = str(exc)[:4000]
            raise self.retry(exc=exc) from exc


@celery_app.task(
    name="chaseless.execute_action",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def execute_action_task(self, action_id: str) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        execute_action(db, uuid.UUID(action_id), settings)


@celery_app.task(name="chaseless.dispatch_outbox")
def dispatch_outbox() -> int:
    dispatched = 0
    with session_scope() as db:
        rows = (
            db.query(OutboxEvent)
            .filter(
                OutboxEvent.dispatched_at.is_(None),
                OutboxEvent.available_at <= datetime.now(UTC),
            )
            .order_by(OutboxEvent.available_at)
            .limit(100)
            .with_for_update(skip_locked=True)
            .all()
        )
        for row in rows:
            if row.topic == "razorpay.webhook.received":
                process_webhook_task.delay(row.payload["webhook_event_id"])
            elif row.topic == "recovery.action.execute":
                execute_action_task.delay(row.payload["action_id"])
            else:
                continue
            row.dispatched_at = datetime.now(UTC)
            row.attempts += 1
            dispatched += 1
    return dispatched
