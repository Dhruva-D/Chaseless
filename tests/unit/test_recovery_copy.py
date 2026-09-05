from __future__ import annotations

from chaseless.core.settings import Settings
from chaseless.domain.enums import ActionType
from chaseless.integrations.llm.message_composer import compose_recovery_copy


def test_payment_link_is_present_when_llms_are_disabled() -> None:
    payment_url = "https://rzp.io/i/example"
    copy = compose_recovery_copy(
        Settings(llm_provider="disabled", llm_fallback_provider="disabled"),
        amount_minor=50_000,
        currency="INR",
        diagnosis="INSTRUMENT_ISSUE",
        action_type=ActionType.PAYMENT_LINK,
        payment_url=payment_url,
    )

    assert payment_url in copy.whatsapp
    assert payment_url in copy.sms
    assert payment_url in copy.email_body
    assert payment_url not in copy.voice
    assert "₹500.00" in copy.whatsapp
    assert copy.source == "deterministic-fallback-v1"


def test_voice_copy_describes_pending_payment() -> None:
    copy = compose_recovery_copy(
        Settings(llm_provider="disabled", llm_fallback_provider="disabled"),
        amount_minor=249_900,
        currency="INR",
        diagnosis="TEMPORARY_LIQUIDITY",
        action_type=ActionType.VOICE_AGENT,
    )

    assert "ChaseLess" in copy.voice
    assert "pending subscription payment" in copy.voice
    assert "₹2,499.00" in copy.voice
