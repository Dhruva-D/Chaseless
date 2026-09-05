from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class VoiceResponse:
    """Bounded interpretation of a caller response; it never authorizes an action."""

    intent: str
    commitment_date: date | None
    confidence: str


def interpret_voice_response(*, speech: str, digits: str, today: date) -> VoiceResponse:
    """Interpret only the small, disclosed IVR vocabulary for a safe recovery follow-up."""
    clean_speech = " ".join(speech.lower().split())
    clean_digits = "".join(character for character in digits if character.isdigit())
    if clean_digits == "1" or "tomorrow" in clean_speech:
        return VoiceResponse("PAY_ON_DATE", today + timedelta(days=1), "high")
    if clean_digits == "2" or any(
        phrase in clean_speech for phrase in ("payment link", "send link", "pay now", "today")
    ):
        return VoiceResponse("PAY_TODAY", today, "high")
    if clean_digits == "3" or any(
        phrase in clean_speech for phrase in ("help", "agent", "call me", "support")
    ):
        return VoiceResponse("NEEDS_HELP", None, "high")
    if any(phrase in clean_speech for phrase in ("not paying", "do not call", "stop calling")):
        return VoiceResponse("DECLINES_PAYMENT", None, "medium")
    match = re.search(r"(?:in )?(\d{1,2}) days?", clean_speech)
    if match:
        days = int(match.group(1))
        if 1 <= days <= 30:
            return VoiceResponse("PAY_ON_DATE", today + timedelta(days=days), "medium")
    return VoiceResponse("UNKNOWN", None, "low")
