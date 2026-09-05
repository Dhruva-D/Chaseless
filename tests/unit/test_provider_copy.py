from __future__ import annotations

from typing import Any

import httpx
from chaseless.core.settings import Settings
from chaseless.integrations.messaging.client import send_sms_message, send_whatsapp_message
from chaseless.integrations.voice.client import place_twilio_test_call


class _Response:
    def __init__(self, payload: dict[str, str]) -> None:
        self._payload = payload
        self.status_code = 201

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return self._payload


def _settings() -> Settings:
    return Settings(
        api_public_url="https://example.test",
        messaging_provider="twilio",
        voice_provider="twilio",
        twilio_account_sid="AC00000000000000000000000000000000",
        twilio_auth_token="secret",
        twilio_whatsapp_from="+10000000000",
        twilio_whatsapp_content_sid="HX_SAMPLE_SHOULD_NOT_BE_USED",
        twilio_sms_from="+10000000000",
        twilio_sms_template="sample_should_not_be_used",
        twilio_voice_from="+10000000000",
        twilio_voice_test_to="+919999999999",
    )


def test_whatsapp_and_sms_use_case_specific_body(monkeypatch: Any) -> None:
    requests: list[dict[str, str]] = []

    def fake_post(*args: Any, **kwargs: Any) -> _Response:
        requests.append(kwargs["data"])
        return _Response({"sid": "SM123"})

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = _settings()
    send_whatsapp_message(settings, recipient_e164="+919999999999", body="custom whatsapp")
    send_sms_message(settings, recipient_e164="+919999999999", body="custom sms")

    assert requests[0]["Body"] == "custom whatsapp"
    assert "ContentSid" not in requests[0]
    assert requests[1]["Body"] == "custom sms"


def test_voice_uses_signed_chaseless_twiml_url(monkeypatch: Any) -> None:
    request: dict[str, str] = {}

    def fake_post(*args: Any, **kwargs: Any) -> _Response:
        request.update(kwargs["data"])
        return _Response({"sid": "CA123", "status": "queued"})

    monkeypatch.setattr(httpx, "post", fake_post)
    place_twilio_test_call(
        _settings(),
        text="ChaseLess says your payment is pending.",
        recipient_e164="+919999999999",
    )

    assert request["Url"].startswith("https://example.test/api/v1/voice/twiml?")
    assert "signature=" in request["Url"]
    assert "Twiml" not in request
