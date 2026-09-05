from datetime import date

from chaseless.integrations.voice.conversation import interpret_voice_response


def test_tomorrow_commitment_is_deterministic() -> None:
    outcome = interpret_voice_response(
        speech="I will pay tomorrow", digits="", today=date(2026, 9, 1)
    )
    assert outcome.intent == "PAY_ON_DATE"
    assert outcome.commitment_date == date(2026, 9, 2)


def test_dtmf_human_help_routes_to_review() -> None:
    outcome = interpret_voice_response(speech="", digits="3", today=date(2026, 9, 1))
    assert outcome.intent == "NEEDS_HELP"
    assert outcome.commitment_date is None


def test_ambiguous_response_never_guesses_a_commitment() -> None:
    outcome = interpret_voice_response(
        speech="maybe later", digits="", today=date(2026, 9, 1)
    )
    assert outcome.intent == "UNKNOWN"
    assert outcome.commitment_date is None
