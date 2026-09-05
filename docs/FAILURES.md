# Failure Modes and Safe Degradation

| Failure | Safe behavior | Evidence |
|---|---|---|
| Invalid webhook signature | Reject before parsing or persistence | Signature unit test |
| Duplicate webhook | Return success without a second event/action | Integration test |
| Out-of-order pending after charged | Store fact but do not regress/create a case | Integration test |
| Redis unavailable after webhook | Durable outbox remains pending | Inbox/outbox transaction |
| Worker delivers task twice | Idempotency and processed markers make second run a no-op | DB constraints |
| LLM disabled/timeout/invalid | Rules produce bounded diagnosis and action candidates | Deterministic P0 path |
| Policy changes before action | Re-check blocks stale action | Executor authorization gate |
| Customer opts out or is fatigued | Contact denied; WAIT/STOP remains possible | Policy tests |
| Quiet hours | Contact is deferred; no override is invented | Policy engine |
| Payment Link amount mismatch | Do not close case; mark action failed | Event processor |
| Razorpay credentials unavailable | WAIT/STOP/mock messaging and benchmark remain demonstrable | Provider boundary |
| Halted subscription | Do not claim native retries continue; use remediation/link/manual review | Action eligibility |

Known P0 limitations: single-merchant server-token authentication, mocked messaging, no
legal-compliance claim across jurisdictions, and no causal claim from a single live recovery.
