from apps.api.app.main import _webhook_source


def test_webhook_source_defaults_to_provider() -> None:
    assert _webhook_source({"payload": {"payment": {"entity": {"id": "pay_1"}}}}) == "provider"


def test_webhook_source_identifies_labelled_fixture() -> None:
    payload = {
        "payload": {
            "subscription": {
                "entity": {"notes": {"demo_source": "synthetic_fixture"}}
            }
        }
    }
    assert _webhook_source(payload) == "synthetic_fixture"
