import json

import httpx
from chaseless.core.settings import Settings
from chaseless.domain.types import Diagnosis, RecoveryContext
from chaseless.integrations.llm.client import diagnose_with_advisory


def context() -> RecoveryContext:
    return RecoveryContext(
        case_id="case-test",
        amount_minor=125000,
        currency="INR",
        subscription_status="pending",
        failure_code="INSUFFICIENT_FUNDS",
    )


def baseline() -> Diagnosis:
    return Diagnosis(
        failure_class="TEMPORARY_LIQUIDITY",
        confidence=0.86,
        natural_recovery_score=0.45,
        evidence=["provider_failure_code=INSUFFICIENT_FUNDS"],
    )


def settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="gemini",
        llm_model="gemini-test",
        llm_api_key="gemini-key",
        llm_fallback_provider="groq",
        llm_fallback_model="groq-test",
        llm_fallback_api_key="groq-key",
    )


def advisory_json(*, failure_class: str = "INSTRUMENT_ISSUE") -> str:
    return json.dumps(
        {
            "failure_class": failure_class,
            "confidence": 0.91,
            "natural_recovery_score": 0.9,
            "evidence": ["Recent issuer response suggests a transient condition"],
        }
    )


def test_gemini_advisory_is_bounded_by_deterministic_diagnosis() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "generativelanguage.googleapis.com" in str(request.url)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": advisory_json()}]}}]},
        )

    result = diagnose_with_advisory(
        context(),
        baseline(),
        settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result.failure_class == "TEMPORARY_LIQUIDITY"
    assert result.natural_recovery_score == 0.55
    assert result.source == "llm-advisory:gemini+rules-v1"


def test_groq_is_used_when_gemini_fails() -> None:
    providers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "generativelanguage.googleapis.com" in str(request.url):
            providers.append("gemini")
            return httpx.Response(503, json={"error": "unavailable"})
        providers.append("groq")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": advisory_json(failure_class="UNKNOWN")}}]},
        )

    result = diagnose_with_advisory(
        context(),
        baseline(),
        settings(),
        transport=httpx.MockTransport(handler),
    )

    assert providers == ["gemini", "groq"]
    assert result.failure_class == "TEMPORARY_LIQUIDITY"
    assert result.source == "llm-advisory:groq+rules-v1"


def test_rules_are_used_when_both_providers_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": str(request.url)})

    result = diagnose_with_advisory(
        context(),
        baseline(),
        settings(),
        transport=httpx.MockTransport(handler),
    )

    assert result.failure_class == baseline().failure_class
    assert result.natural_recovery_score == baseline().natural_recovery_score
    assert result.source == "rules-v1:llm-unavailable"
