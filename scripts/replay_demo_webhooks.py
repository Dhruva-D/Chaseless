"""Replay clearly labelled Razorpay-shaped fixtures through the real webhook boundary.

This exists for demo resilience when a provider Test Mode account cannot complete a
recurring-card mandate. It uses the configured webhook secret so the API exercises
the same raw-body signature verification, inbox, outbox and worker path as Razorpay.
It must never be presented as a provider-originated payment.

Run inside the API container so the configured Test Mode webhook secret is available:

    docker compose exec api python -m scripts.replay_demo_webhooks --mode recovery
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
from chaseless.core.settings import get_settings


@dataclass(frozen=True)
class ReplayEvent:
    event_id: str
    body: bytes


def fixture_payload(
    *,
    event_type: str,
    subscription_id: str,
    amount_minor: int,
    currency: str,
    created_at: int,
    fixture_id: str,
) -> bytes:
    """Build the minimum provider-shaped subscription payload used by the processor."""
    is_charge = event_type == "subscription.charged"
    payload: dict[str, Any] = {
        "event": event_type,
        "created_at": created_at,
        "contains": ["subscription", "payment"],
        "payload": {
            "subscription": {
                "entity": {
                    "id": subscription_id,
                    "customer_id": f"cust_{fixture_id}",
                    "plan_id": "plan_chaseless_demo",
                    "status": "active" if is_charge else "pending",
                    "current_start": created_at,
                    "notes": {
                        "demo_source": "synthetic_fixture",
                        "fixture_id": fixture_id,
                    },
                }
            },
            "payment": {
                "entity": {
                    "id": f"pay_{fixture_id}_{'charged' if is_charge else 'failed'}",
                    "invoice_id": f"inv_{fixture_id}",
                    "status": "captured" if is_charge else "failed",
                    "amount": amount_minor,
                    "currency": currency,
                    "error_code": None if is_charge else "INSUFFICIENT_FUNDS",
                    "notes": {
                        "demo_source": "synthetic_fixture",
                        "fixture_id": fixture_id,
                    },
                }
            },
        },
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def replay_event(event_id: str, body: bytes) -> ReplayEvent:
    return ReplayEvent(event_id=event_id, body=body)


def post_event(*, client: httpx.Client, event: ReplayEvent, secret: str) -> dict[str, Any]:
    signature = hmac.new(secret.encode("utf-8"), event.body, hashlib.sha256).hexdigest()
    response = client.post(
        "/api/v1/webhooks/razorpay",
        content=event.body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": event.event_id,
        },
    )
    response.raise_for_status()
    return dict(response.json())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("failure", "recovery"),
        default="recovery",
        help="failure sends pending only; recovery sends pending then charged",
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--subscription-id", default="sub_chaseless_fixture_demo")
    parser.add_argument("--amount-minor", type=int, default=50_000)
    parser.add_argument("--currency", default="INR")
    args = parser.parse_args()
    if args.amount_minor <= 0:
        parser.error("--amount-minor must be positive")

    secret = get_settings().razorpay_webhook_secret
    if not secret:
        raise SystemExit("RAZORPAY_WEBHOOK_SECRET must be configured before replaying fixtures")

    now = int(time.time())
    fixture_id = f"fixture_{now}"
    pending = replay_event(
        f"evt_chaseless_{fixture_id}_pending",
        fixture_payload(
            event_type="subscription.pending",
            subscription_id=args.subscription_id,
            amount_minor=args.amount_minor,
            currency=args.currency,
            created_at=now,
            fixture_id=fixture_id,
        ),
    )
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=15.0) as client:
        accepted = post_event(client=client, event=pending, secret=secret)
        duplicate = post_event(client=client, event=pending, secret=secret)
        print(f"fixture pending accepted: {accepted}")
        print(f"fixture pending duplicate: {duplicate}")
        if args.mode == "recovery":
            charged = replay_event(
                f"evt_chaseless_{fixture_id}_charged",
                fixture_payload(
                    event_type="subscription.charged",
                    subscription_id=args.subscription_id,
                    amount_minor=args.amount_minor,
                    currency=args.currency,
                    created_at=now + 1,
                    fixture_id=fixture_id,
                ),
            )
            charged_result = post_event(client=client, event=charged, secret=secret)
            print(f"fixture charged accepted: {charged_result}")
    print("Synthetic fixture replay complete. It is not a Razorpay-originated payment.")


if __name__ == "__main__":
    main()
