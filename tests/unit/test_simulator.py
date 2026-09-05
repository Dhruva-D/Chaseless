from chaseless.simulator import BenchmarkConfig, run_benchmark


def test_benchmark_is_reproducible_and_bounded() -> None:
    config = BenchmarkConfig(seed=42, customers=500, budget_minor=50_000, contact_budget=150)
    first_metrics, first_rows = run_benchmark(config)
    second_metrics, second_rows = run_benchmark(config)
    assert first_metrics == second_metrics
    assert first_rows == second_rows
    assert len(first_rows) == 1500
    assert first_metrics["chaseless"]["policy_violations"] == 0  # type: ignore[index]
    assert first_metrics["chaseless"]["contacts"] <= 150  # type: ignore[index]
