from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import httpx

from chaseless.core.settings import Settings


class RazorpayConfigurationError(RuntimeError):
    pass


class RazorpayClient:
    def __init__(self, settings: Settings) -> None:
        if settings.razorpay_mode != "test":
            raise RazorpayConfigurationError("ChaseLess P0 only permits Razorpay Test Mode")
        if not settings.razorpay_key_id or not settings.razorpay_key_secret:
            raise RazorpayConfigurationError("Razorpay Test Mode credentials are not configured")
        if not settings.razorpay_key_id.startswith("rzp_test_"):
            raise RazorpayConfigurationError("Only Razorpay Test Mode keys may be used for import")
        self._base_url = settings.razorpay_api_base_url.rstrip("/")
        self._auth = (settings.razorpay_key_id, settings.razorpay_key_secret)

    def create_payment_link(
        self,
        *,
        amount_minor: int,
        currency: str,
        reference_id: str,
        case_id: str,
        action_id: str,
        expire_by: datetime,
        customer_name: str | None = None,
        customer_contact: str | None = None,
        customer_email: str | None = None,
        notify_sms: bool = False,
        notify_email: bool = False,
    ) -> dict[str, Any]:
        if len(reference_id) > 40:
            raise ValueError("Razorpay Payment Link reference_id cannot exceed 40 characters")
        if expire_by <= datetime.now(UTC):
            raise ValueError("Payment Link expiry must be in the future")
        payload = {
            "amount": amount_minor,
            "currency": currency,
            "accept_partial": False,
            "reference_id": reference_id,
            "description": "Outstanding subscription payment",
            "expire_by": int(expire_by.timestamp()),
            "notify": {"sms": notify_sms, "email": notify_email},
            "reminder_enable": False,
            "notes": {"case_id": case_id, "action_id": action_id},
        }
        customer = {
            key: value
            for key, value in {
                "name": customer_name,
                "contact": customer_contact,
                "email": customer_email,
            }.items()
            if value
        }
        if customer:
            payload["customer"] = customer
        with httpx.Client(base_url=self._base_url, auth=self._auth, timeout=15.0) as client:
            response = client.post("/v1/payment_links", json=payload)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    def fetch_recent_payments(self, *, count: int) -> list[dict[str, Any]]:
        """Return the newest Test Mode payment records without mutating Razorpay."""
        with httpx.Client(base_url=self._base_url, auth=self._auth, timeout=15.0) as client:
            response = client.get("/v1/payments", params={"count": count})
            response.raise_for_status()
            payload = cast(dict[str, Any], response.json())
        return [item for item in payload.get("items", []) if isinstance(item, dict)]

    def fetch_invoice(self, invoice_id: str) -> dict[str, Any] | None:
        with httpx.Client(base_url=self._base_url, auth=self._auth, timeout=15.0) as client:
            response = client.get(f"/v1/invoices/{invoice_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    def fetch_subscription(self, subscription_id: str) -> dict[str, Any] | None:
        with httpx.Client(base_url=self._base_url, auth=self._auth, timeout=15.0) as client:
            response = client.get(f"/v1/subscriptions/{subscription_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
