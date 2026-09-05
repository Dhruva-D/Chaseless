from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chaseless.db.models import Merchant, OutboxEvent, WebhookEvent
from chaseless.integrations.razorpay.security import verify_webhook_signature


class WebhookRejected(ValueError):
    pass


@dataclass(frozen=True)
class IngestResult:
    event_id: uuid.UUID | None
    duplicate: bool


def active_merchant(db: Session) -> Merchant:
    merchant = (
        db.query(Merchant).filter(Merchant.status == "active").order_by(Merchant.created_at).first()
    )
    if merchant is None:
        raise WebhookRejected("No active merchant is configured")
    return merchant


def ingest_razorpay_webhook(
    db: Session,
    *,
    raw_body: bytes,
    signature: str,
    provider_event_id: str,
    current_secret: str,
    previous_secret: str = "",
) -> IngestResult:
    if not verify_webhook_signature(raw_body, signature, current_secret, previous_secret):
        raise WebhookRejected("Invalid Razorpay webhook signature")
    if not provider_event_id:
        raise WebhookRejected("Missing x-razorpay-event-id header")
    try:
        payload: dict[str, Any] = orjson.loads(raw_body)
    except orjson.JSONDecodeError as exc:
        raise WebhookRejected("Invalid JSON payload") from exc
    event_type = str(payload.get("event", ""))
    if not event_type:
        raise WebhookRejected("Webhook event type is missing")
    merchant = active_merchant(db)
    created_at = payload.get("created_at")
    occurred_at = (
        datetime.fromtimestamp(int(created_at), tz=UTC) if created_at is not None else None
    )
    event = WebhookEvent(
        merchant_id=merchant.id,
        provider_event_id=provider_event_id,
        event_type=event_type,
        signature_valid=True,
        raw_hash=hashlib.sha256(raw_body).hexdigest(),
        payload=payload,
        occurred_at=occurred_at,
    )
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return IngestResult(event_id=None, duplicate=True)

    db.add(
        OutboxEvent(
            topic="razorpay.webhook.received",
            aggregate_id=event.id,
            payload={"webhook_event_id": str(event.id)},
        )
    )
    db.commit()
    return IngestResult(event_id=event.id, duplicate=False)
