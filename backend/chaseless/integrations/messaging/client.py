from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from typing import Literal

import httpx

from chaseless.core.settings import Settings


class MessagingConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MessagingResult:
    provider: str
    reference: str
    delivery_state: Literal["mocked", "queued"]


def _whatsapp_address(value: str) -> str:
    return value if value.startswith("whatsapp:") else f"whatsapp:{value}"


def send_whatsapp_message(
    settings: Settings,
    *,
    recipient_e164: str,
    body: str,
) -> MessagingResult:
    """Send only through explicitly configured providers; mock is the safe default."""
    if settings.messaging_provider == "mock":
        return MessagingResult(provider="mock", reference="mock-whatsapp", delivery_state="mocked")
    if settings.messaging_provider != "twilio":
        raise MessagingConfigurationError("Unsupported messaging provider")
    credentials = (
        settings.twilio_account_sid,
        settings.twilio_auth_token,
        settings.twilio_whatsapp_from,
    )
    if not all(credentials):
        raise MessagingConfigurationError("Twilio WhatsApp credentials are incomplete")
    data = {
        "To": _whatsapp_address(recipient_e164),
        "From": _whatsapp_address(settings.twilio_whatsapp_from),
    }
    # Recovery copy is generated for the specific case. A ContentSid here would
    # silently replace it with Twilio's sample appointment template.
    data["Body"] = body
    response = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        data=data,
        timeout=15.0,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        payload = response.json()
        code = payload.get("code") if isinstance(payload, dict) else None
        message = payload.get("message") if isinstance(payload, dict) else None
        if code == 572002:
            raise MessagingConfigurationError(
                "Twilio trial requires the configured WhatsApp test recipient "
                "to be added as a verified recipient in Twilio Console"
            ) from exc
        if code == 21654 and message == "ContentSid Required":
            raise MessagingConfigurationError(
                "Twilio Try Out requires a predefined ContentSid; custom ChaseLess WhatsApp "
                "copy and payment links require a registered sender or WhatsApp Cloud API"
            ) from exc
        if code == 63016:
            raise MessagingConfigurationError(
                "Twilio Try Out requires an approved WhatsApp ContentSid/template for "
                "outbound delivery; a custom ChaseLess payment message needs a registered "
                "WhatsApp sender or WhatsApp Cloud API"
            ) from exc
        raise MessagingConfigurationError(
            f"Twilio WhatsApp rejected the test: {code or response.status_code}"
        ) from exc
    payload = response.json()
    sid = payload.get("sid")
    if not isinstance(sid, str) or not sid:
        raise MessagingConfigurationError("Twilio response did not include a message SID")
    return MessagingResult(provider="twilio", reference=sid, delivery_state="queued")


def send_sms_message(settings: Settings, *, recipient_e164: str, body: str) -> MessagingResult:
    """Send an SMS only when a Twilio SMS sender is explicitly configured."""
    if settings.messaging_provider == "mock":
        return MessagingResult(provider="mock", reference="mock-sms", delivery_state="mocked")
    if settings.messaging_provider != "twilio":
        raise MessagingConfigurationError("Unsupported messaging provider")
    if not all((settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_sms_from)):
        raise MessagingConfigurationError("Twilio SMS credentials are incomplete")
    response = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        data={
            "To": recipient_e164,
            "From": settings.twilio_sms_from,
            # Never replace recovery copy with Twilio's Try Out SMS template.
            "Body": body,
        },
        timeout=15.0,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        payload = response.json()
        code = payload.get("code") if isinstance(payload, dict) else None
        if code == 572006:
            raise MessagingConfigurationError(
                "Twilio Trial permits only its predefined SMS samples; custom ChaseLess "
                "SMS requires an upgraded Twilio account"
            ) from exc
        raise MessagingConfigurationError(
            f"Twilio SMS rejected the test: {code or response.status_code}"
        ) from exc
    sid = response.json().get("sid")
    if not isinstance(sid, str) or not sid:
        raise MessagingConfigurationError("Twilio response did not include an SMS SID")
    return MessagingResult(provider="twilio-sms", reference=sid, delivery_state="queued")


def send_email_message(
    settings: Settings, *, recipient: str, subject: str, body: str
) -> MessagingResult:
    """Send through Twilio Email API or authenticated SMTP."""
    if settings.messaging_provider == "mock":
        return MessagingResult(provider="mock", reference="mock-email", delivery_state="mocked")
    if settings.messaging_provider == "twilio":
        if not all((settings.twilio_account_sid, settings.twilio_auth_token)):
            raise MessagingConfigurationError("Twilio email credentials are incomplete")
        sender = settings.email_from or f"{settings.twilio_account_sid}@twilio.email"
        safe_html = "<p>" + escape(body).replace("\n", "<br/>") + "</p>"
        response = httpx.post(
            "https://comms.twilio.com/v1/Emails",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            json={
                "from": {"address": sender, "name": "ChaseLess Recovery"},
                "to": [{"address": recipient}],
                "content": {
                    "subject": subject,
                    "html": safe_html,
                },
            },
            timeout=15.0,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            payload = response.json()
            message = payload.get("message") if isinstance(payload, dict) else None
            if isinstance(message, str) and "approved template" in message:
                raise MessagingConfigurationError(
                    "Twilio Trial permits only its predefined email sample; custom ChaseLess "
                    "email requires SMTP/SendGrid credentials or an upgraded account"
                ) from exc
            raise MessagingConfigurationError("Twilio email delivery failed") from exc
        payload = response.json()
        reference = payload.get("operationId") or payload.get("id") or "twilio-email-accepted"
        return MessagingResult(
            provider="twilio-email", reference=str(reference), delivery_state="queued"
        )
    if not all(
        (settings.smtp_host, settings.smtp_username, settings.smtp_password, settings.email_from)
    ):
        raise MessagingConfigurationError("SMTP email credentials are incomplete")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = settings.email_from, recipient, subject
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise MessagingConfigurationError("SMTP email delivery failed") from exc
    return MessagingResult(provider="smtp", reference="smtp-accepted", delivery_state="queued")
