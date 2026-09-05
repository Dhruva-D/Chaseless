"""Prepare the complete local ChaseLess reviewer demo in one command.

Run inside the API container:

    docker compose exec api python -m scripts.run_demo

The recovery replay is explicitly synthetic and never represents a real Razorpay payment.
"""

from __future__ import annotations

import subprocess
import sys
import time

from chaseless.db.models import RecoveryCase
from chaseless.db.session import session_scope
from chaseless.domain.enums import CaseState

from evaluation.run_benchmark import main as run_benchmark
from scripts.seed_demo import main as seed_demo


def _wait_for_fixture_recovery(timeout_seconds: int = 20) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with session_scope() as db:
            recovered = (
                db.query(RecoveryCase)
                .filter(
                    RecoveryCase.episode_key.like("sub_chaseless_fixture_demo:%"),
                    RecoveryCase.state == CaseState.RECOVERED_VERIFIED.value,
                )
                .first()
            )
            if recovered is not None:
                return True
        time.sleep(1)
    return False


def main() -> None:
    seed_demo()
    original_args = sys.argv
    try:
        sys.argv = ["run_benchmark", "--customers", "10000"]
        run_benchmark()
    finally:
        sys.argv = original_args
    subprocess.run(
        [sys.executable, "-m", "scripts.replay_demo_webhooks", "--mode", "recovery"],
        check=True,
    )
    if not _wait_for_fixture_recovery():
        raise SystemExit(
            "Fixture was accepted but did not recover within 20 seconds; inspect worker logs."
        )
    print("Reviewer demo is ready at http://localhost:3000/evidence")
    print(
        "The recovered fixture is synthetic, signed, and visibly labelled in the evidence ledger."
    )


if __name__ == "__main__":
    main()
