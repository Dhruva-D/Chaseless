import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from chaseless.db.models import AuditEvent


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def append_audit(
    db: Session,
    *,
    merchant_id: uuid.UUID,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_kind: str,
    actor_type: str,
    actor_id: str,
    decision: dict[str, Any],
    policy_version: int | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        merchant_id=merchant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_kind=event_kind,
        actor_type=actor_type,
        actor_id=actor_id,
        decision=decision,
        policy_version=policy_version,
        input_hash=canonical_hash(decision.get("input", {})),
        output_hash=canonical_hash(decision.get("output", decision)),
        request_id=request_id,
    )
    db.add(event)
    return event
