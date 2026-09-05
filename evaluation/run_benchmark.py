from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from chaseless.simulator import BenchmarkConfig, run_benchmark


def code_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "working-tree"


def generate_benchmark(
    *,
    seed: int = 20260901,
    customers: int = 10_000,
    budget_minor: int = 1_000_000,
    contact_budget: int = 3_500,
    output_dir: str = "evaluation/results",
) -> dict[str, object]:
    config = BenchmarkConfig(
        seed=seed,
        customers=customers,
        budget_minor=budget_minor,
        contact_budget=contact_budget,
    )
    metrics, outcomes = run_benchmark(config)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    config_json = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    summary: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "code_version": code_version(),
        "config": config.model_dump(mode="json"),
        "config_hash": hashlib.sha256(config_json.encode()).hexdigest(),
        "metrics": metrics,
    }
    (output_path / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    with (output_path / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(outcomes[0]).keys()))
        writer.writeheader()
        for outcome in outcomes:
            row = asdict(outcome)
            row["action"] = outcome.action.value
            writer.writerow(row)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run matched-seed ChaseLess benchmark")
    parser.add_argument("--seed", type=int, default=20260901)
    parser.add_argument("--customers", type=int, default=10_000)
    parser.add_argument("--budget-minor", type=int, default=1_000_000)
    parser.add_argument("--contact-budget", type=int, default=3_500)
    parser.add_argument("--output-dir", default="evaluation/results")
    args = parser.parse_args()
    summary = generate_benchmark(
        seed=args.seed,
        customers=args.customers,
        budget_minor=args.budget_minor,
        contact_budget=args.contact_budget,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
