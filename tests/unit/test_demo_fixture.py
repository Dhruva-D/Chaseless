import json

from scripts.replay_demo_webhooks import fixture_payload


def test_recovery_fixture_is_provider_shaped_and_labelled() -> None:
    payload = json.loads(
        fixture_payload(
            event_type="subscription.pending",
            subscription_id="sub_fixture_test",
            amount_minor=50_000,
            currency="INR",
            created_at=1_788_200_000,
            fixture_id="fixture_test",
        )
    )

    assert payload["event"] == "subscription.pending"
    subscription = payload["payload"]["subscription"]["entity"]
    payment = payload["payload"]["payment"]["entity"]
    assert subscription["id"] == "sub_fixture_test"
    assert subscription["notes"]["demo_source"] == "synthetic_fixture"
    assert payment["amount"] == 50_000
    assert payment["error_code"] == "INSUFFICIENT_FUNDS"
