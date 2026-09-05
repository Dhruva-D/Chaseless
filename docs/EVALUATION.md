# Reproducible Evaluation

The benchmark compares three strategies on the same seeded customer population:

1. Native/do-nothing recovery.
2. Fixed dunning using the same nudge for every eligible customer.
3. ChaseLess adaptive allocation.

Each synthetic customer has visible features and a separate hidden world model. The decision
engine receives only visible features. A common outcome random variable is reused across the three
strategies, providing a matched counterfactual comparison instead of three unrelated samples.

Run:

```bash
docker compose exec api python -m evaluation.run_benchmark --seed 20260901 --customers 10000
```

The committed run recovered ₹88.35 lakh for ChaseLess versus ₹70.14 lakh for fixed dunning:
₹18.22 lakh incremental recovery, 323 contacts avoided, and zero policy violations.

Artifacts:

- `evaluation/results/results.json`: configuration hash and aggregate metrics.
- `evaluation/results/results.csv`: case/strategy/action/outcome rows.

The development test uses a smaller seed. Submission results use the declared benchmark seed and
must not be silently replaced after policy tuning.

## Metric interpretation

- **Verified recovery:** confirmed by a provider/simulator outcome event.
- **Natural recovery:** recovery under WAIT/native recovery.
- **Action-associated recovery:** live recovery after an eligible ChaseLess action; not a causal
  claim by itself.
- **Incremental recovery:** matched simulator difference between ChaseLess and a baseline.

The matched-world evaluation measures comparative system behavior; it does not predict a
merchant's production uplift.
