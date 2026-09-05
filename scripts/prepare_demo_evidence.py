"""Prepare reproducible benchmark and signed synthetic webhook evidence."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

from chaseless.core.settings import get_settings
from chaseless.db.session import session_scope
from chaseless.services.webhooks import ingest_razorpay_webhook

from evaluation.run_benchmark import generate_benchmark
from scripts.replay_demo_webhooks import fixture_payload


def main() -> None:
    settings = get_settings()
    generate_benchmark(output_dir=settings.evaluation_output_dir)

    now = int(time.time())
    fixture_id = f"reset_{now}_{uuid.uuid4().hex[:8]}"
    body = fixture_payload(
        event_type="subscription.pending",
        subscription_id=f"sub_chaseless_{fixture_id}",
        amount_minor=50_000,
        currency="INR",
        created_at=now,
        fixture_id=fixture_id,
    )
    signature = hmac.new(
        settings.razorpay_webhook_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    with session_scope() as db:
        ingest_razorpay_webhook(
            db,
            raw_body=body,
            signature=signature,
            provider_event_id=f"evt_chaseless_{fixture_id}_pending",
            current_secret=settings.razorpay_webhook_secret,
            previous_secret=settings.razorpay_previous_webhook_secret,
        )

    print("Prepared benchmark and signed synthetic webhook evidence.")


if __name__ == "__main__":
    main()
