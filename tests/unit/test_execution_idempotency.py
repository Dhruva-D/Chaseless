import uuid

import pytest
from chaseless.db.models import RecoveryRun
from chaseless.services.recovery import execute_run


def make_run(db):
    run = RecoveryRun(
        merchant_id=uuid.uuid4(),
        policy_version_id=uuid.uuid4(),
        status="APPROVED",
        budget_minor=1000,
        contact_budget=1,
    )
    db.add(run)
    db.commit()
    return run


def test_execute_idempotency_key_is_replay_safe(db) -> None:
    run = make_run(db)

    execute_run(db, run, idempotency_key="execute-1")
    db.refresh(run)
    assert run.execute_idempotency_key == "execute-1"
    assert run.status == "EXECUTING"

    # A retry with the same key does not enqueue another execution.
    before = db.query(RecoveryRun).count()
    execute_run(db, run, idempotency_key="execute-1")
    assert db.query(RecoveryRun).count() == before

    with pytest.raises(ValueError, match="another Idempotency-Key"):
        execute_run(db, run, idempotency_key="execute-2")


def test_execution_key_cannot_be_reused_for_another_run(db) -> None:
    first = make_run(db)
    second = make_run(db)
    execute_run(db, first, idempotency_key="execute-shared")

    with pytest.raises(ValueError, match="another recovery run"):
        execute_run(db, second, idempotency_key="execute-shared")
