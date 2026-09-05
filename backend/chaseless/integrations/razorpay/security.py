import hashlib
import hmac


def verify_webhook_signature(raw_body: bytes, signature: str, *secrets: str) -> bool:
    if not signature:
        return False
    for secret in secrets:
        if not secret:
            continue
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            return True
    return False
