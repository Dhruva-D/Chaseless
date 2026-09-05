from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from chaseless.core.settings import Settings


class VoiceConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SarvamSpeech:
    request_id: str
    audio_bytes: bytes


@dataclass(frozen=True)
class TwilioCall:
    sid: str
    status: str


def build_voice_twiml_url(settings: Settings, *, text: str) -> str:
    clean_text = " ".join(text.split())
    if not clean_text or len(clean_text) > 500:
        raise VoiceConfigurationError("Voice text must contain 1 to 500 characters")
    if not settings.api_public_url.startswith("https://"):
        raise VoiceConfigurationError("Custom voice requires an HTTPS API_PUBLIC_URL")
    token = base64.urlsafe_b64encode(clean_text.encode()).decode().rstrip("=")
    signature = hmac.new(
        settings.app_session_secret.encode(), token.encode(), hashlib.sha256
    ).hexdigest()
    return (
        settings.api_public_url.rstrip("/")
        + "/api/v1/voice/twiml?"
        + urlencode({"message": token, "signature": signature})
    )


def build_interactive_voice_twiml_url(
    settings: Settings, *, action_id: uuid.UUID, text: str
) -> str:
    """Create a signed URL to a case-specific IVR, without embedding customer data."""
    clean_text = " ".join(text.split())
    if not clean_text or len(clean_text) > 700:
        raise VoiceConfigurationError("Voice text must contain 1 to 700 characters")
    if not settings.api_public_url.startswith("https://"):
        raise VoiceConfigurationError("Interactive voice requires an HTTPS API_PUBLIC_URL")
    action_token = str(action_id)
    signature = hmac.new(
        settings.app_session_secret.encode(),
        f"voice-ivr-v1:{action_token}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return (
        settings.api_public_url.rstrip("/")
        + "/api/v1/voice/twiml?"
        + urlencode({"action_id": action_token, "signature": signature, "message": clean_text})
    )


def verify_interactive_voice_signature(
    settings: Settings, *, action_id: uuid.UUID, signature: str
) -> bool:
    expected = hmac.new(
        settings.app_session_secret.encode(),
        f"voice-ivr-v1:{action_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def decode_voice_twiml_message(settings: Settings, *, message: str, signature: str) -> str:
    expected = hmac.new(
        settings.app_session_secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise VoiceConfigurationError("Voice message signature is invalid")
    try:
        padded = message + "=" * (-len(message) % 4)
        text = base64.urlsafe_b64decode(padded.encode()).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise VoiceConfigurationError("Voice message token is invalid") from exc
    clean_text = " ".join(text.split())
    if not clean_text or len(clean_text) > 500:
        raise VoiceConfigurationError("Voice text must contain 1 to 500 characters")
    return clean_text


def build_voice_audio_url(settings: Settings, *, text: str) -> str:
    clean_text = " ".join(text.split())
    if not clean_text or len(clean_text) > 700:
        raise VoiceConfigurationError("Voice audio text must contain 1 to 700 characters")
    token = base64.urlsafe_b64encode(clean_text.encode()).decode().rstrip("=")
    signature = hmac.new(
        settings.app_session_secret.encode(), f"voice-audio-v1:{token}".encode(), hashlib.sha256
    ).hexdigest()
    return (
        settings.api_public_url.rstrip("/")
        + "/api/v1/voice/audio?"
        + urlencode({"message": token, "signature": signature})
    )


def decode_voice_audio_message(settings: Settings, *, message: str, signature: str) -> str:
    expected = hmac.new(
        settings.app_session_secret.encode(), f"voice-audio-v1:{message}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise VoiceConfigurationError("Voice audio signature is invalid")
    try:
        padded = message + "=" * (-len(message) % 4)
        value = base64.urlsafe_b64decode(padded.encode()).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise VoiceConfigurationError("Voice audio token is invalid") from exc
    clean_text = " ".join(value.split())
    if not clean_text or len(clean_text) > 700:
        raise VoiceConfigurationError("Voice audio text must contain 1 to 700 characters")
    return clean_text


def synthesize_sarvam_speech(
    settings: Settings, *, text: str, language_code: str = "en-IN", speaker: str = "simran"
) -> SarvamSpeech:
    """Create short, non-sensitive test audio with Sarvam Bulbul v3."""
    if not settings.sarvam_api_key:
        raise VoiceConfigurationError("Sarvam API key is not configured")
    if not text or len(text) > 500:
        raise VoiceConfigurationError("Voice text must contain 1 to 500 characters")
    response = httpx.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={"api-subscription-key": settings.sarvam_api_key},
        json={
            "text": text,
            "language_code": language_code,
            "model": "bulbul:v3",
            "speaker": speaker,
            "output_audio_codec": "mp3",
            "sample_rate": 8000,
        },
        timeout=30.0,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        payload = response.json()
        code = payload.get("code") if isinstance(payload, dict) else None
        raise VoiceConfigurationError(
            f"Sarvam TTS rejected the test: {code or response.status_code}"
        ) from exc
    payload = response.json()
    audios = payload.get("audios")
    request_id = payload.get("request_id")
    if not isinstance(audios, list) or not audios or not isinstance(audios[0], str):
        raise VoiceConfigurationError("Sarvam response did not include audio")
    if not isinstance(request_id, str) or not request_id:
        raise VoiceConfigurationError("Sarvam response did not include a request ID")
    try:
        audio_bytes = base64.b64decode("".join(audios), validate=True)
    except ValueError as exc:
        raise VoiceConfigurationError("Sarvam returned invalid audio") from exc
    return SarvamSpeech(request_id=request_id, audio_bytes=audio_bytes)


def place_twilio_test_call(
    settings: Settings,
    *,
    text: str,
    recipient_e164: str | None = None,
    action_id: uuid.UUID | None = None,
) -> TwilioCall:
    """Call only the configured test recipient with bounded, non-collection content."""
    credentials = (
        settings.twilio_account_sid,
        settings.twilio_auth_token,
        settings.twilio_voice_from,
    )
    if not all(credentials):
        raise VoiceConfigurationError("Twilio Voice credentials are incomplete")
    if not settings.twilio_voice_test_to:
        raise VoiceConfigurationError("Twilio Voice test recipient is not configured")
    recipient = recipient_e164 or settings.twilio_voice_test_to
    if recipient != settings.twilio_voice_test_to:
        raise VoiceConfigurationError("Voice recipient is outside the configured demo allowlist")
    twiml_url = (
        build_interactive_voice_twiml_url(settings, action_id=action_id, text=text)
        if action_id
        else build_voice_twiml_url(settings, text=text)
    )
    response = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Calls.json",
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        data={
            "To": recipient,
            "From": settings.twilio_voice_from,
            # Trial Voice permits a URL but rejects the inline Twiml parameter.
            "Url": twiml_url,
        },
        timeout=30.0,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        payload = response.json()
        code = payload.get("code") if isinstance(payload, dict) else None
        raise VoiceConfigurationError(
            f"Twilio Voice rejected the test: {code or response.status_code}"
        ) from exc
    payload = response.json()
    sid = payload.get("sid")
    status = payload.get("status")
    if not isinstance(sid, str) or not sid or not isinstance(status, str) or not status:
        raise VoiceConfigurationError("Twilio response did not include a call reference")
    return TwilioCall(sid=sid, status=status)
