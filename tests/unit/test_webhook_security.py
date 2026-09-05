import hashlib
import hmac

from chaseless.integrations.razorpay.security import verify_webhook_signature


def test_webhook_signature_uses_raw_body() -> None:
    body = b'{"event":"subscription.pending","value":"exact bytes"}'
    secret = "webhook-secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, signature, secret)
    assert not verify_webhook_signature(body + b" ", signature, secret)


def test_previous_secret_is_accepted_during_rotation() -> None:
    body = b"{}"
    signature = hmac.new(b"old", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, signature, "new", "old")
