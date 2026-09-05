"""Run explicitly requested real-provider tests against configured test recipients only.

Examples:
    docker compose exec api python -m scripts.provider_smoke_test --sarvam
    docker compose exec api python -m scripts.provider_smoke_test --send-whatsapp
    docker compose exec api python -m scripts.provider_smoke_test --place-voice-call
"""

from __future__ import annotations

import argparse

from chaseless.core.settings import get_settings
from chaseless.integrations.messaging.client import (
    send_email_message,
    send_sms_message,
    send_whatsapp_message,
)
from chaseless.integrations.voice.client import place_twilio_test_call, synthesize_sarvam_speech

TEST_TEXT = "This is a ChaseLess test notification. No payment is requested."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sarvam", action="store_true", help="generate Sarvam test audio")
    parser.add_argument(
        "--send-whatsapp", action="store_true", help="send only to Twilio test recipient"
    )
    parser.add_argument(
        "--send-sms", action="store_true", help="send only to Twilio SMS test recipient"
    )
    parser.add_argument(
        "--send-email", action="store_true", help="send only to configured email test recipient"
    )
    parser.add_argument(
        "--place-voice-call", action="store_true", help="call only Twilio test recipient"
    )
    args = parser.parse_args()
    if not any(
        (args.sarvam, args.send_whatsapp, args.send_sms, args.send_email, args.place_voice_call)
    ):
        parser.error("Choose at least one provider test")
    settings = get_settings()
    if args.sarvam:
        sarvam_result = synthesize_sarvam_speech(settings, text=TEST_TEXT)
        print(
            "Sarvam TTS verified: "
            f"request_id={sarvam_result.request_id}, audio_bytes={len(sarvam_result.audio_bytes)}"
        )
    if args.send_whatsapp:
        if not settings.twilio_whatsapp_test_to:
            raise SystemExit("TWILIO_WHATSAPP_TEST_TO is not configured")
        whatsapp_result = send_whatsapp_message(
            settings.model_copy(update={"messaging_provider": "twilio"}),
            recipient_e164=settings.twilio_whatsapp_test_to,
            body=TEST_TEXT,
        )
        print(
            "Twilio WhatsApp accepted: "
            f"reference={whatsapp_result.reference}, state={whatsapp_result.delivery_state}"
        )
    if args.send_sms:
        if not settings.twilio_sms_test_to:
            raise SystemExit("TWILIO_SMS_TEST_TO is not configured")
        sms_result = send_sms_message(
            settings.model_copy(update={"messaging_provider": "twilio"}),
            recipient_e164=settings.twilio_sms_test_to,
            body=TEST_TEXT,
        )
        print(
            f"Twilio SMS accepted: reference={sms_result.reference}, "
            f"state={sms_result.delivery_state}"
        )
    if args.send_email:
        if not settings.email_test_to:
            raise SystemExit("EMAIL_TEST_TO is not configured")
        email_result = send_email_message(
            settings.model_copy(update={"messaging_provider": "twilio"}),
            recipient=settings.email_test_to,
            subject="ChaseLess test notification",
            body=TEST_TEXT,
        )
        print(
            f"Twilio email accepted: reference={email_result.reference}, "
            f"state={email_result.delivery_state}"
        )
    if args.place_voice_call:
        call_result = place_twilio_test_call(settings, text=TEST_TEXT)
        print(f"Twilio Voice accepted: sid={call_result.sid}, status={call_result.status}")


if __name__ == "__main__":
    main()
