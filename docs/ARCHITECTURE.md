# ChaseLess Architecture

ChaseLess is a modular monolith with three independently runnable processes: a Next.js web app,
a FastAPI API, and a Celery worker/scheduler. The API and worker import the same Python domain
package. PostgreSQL is durable truth; Redis is a queue and short-lived coordination layer only.

## Authorization boundary

1. Provider events and customer context are normalized into a recovery case.
2. LangGraph runs the explicit diagnose → candidate → policy → propose loop.
3. Recovery Budget Autopilot selects safe customer/action pairs under portfolio constraints.
4. The plan and policy evidence are committed before execution.
5. The action executor re-evaluates policy immediately before calling a provider.
6. Only a verified Razorpay or simulator outcome event can record recovered money.

The LLM cannot modify amounts, payment URLs, merchant identity, deadlines, opt-out text, policy
rules, budgets, or idempotency keys. P0 remains functional when `LLM_PROVIDER=disabled`.

When enabled, diagnosis uses Gemini as a bounded advisory provider and Groq as its fallback. The
rules engine always produces the baseline diagnosis first. An LLM cannot change a known
deterministic failure class, cannot declare a case non-recoverable without deterministic evidence,
and can move the natural-recovery score by at most 0.10. Invalid output, timeouts, rate limits, or
provider outages fall back to the next provider and ultimately to the rules baseline. The selected
diagnosis source is persisted in the case and audit trail. LLM output never authorizes or executes
an action; the deterministic policy gate and budget allocator retain that authority.

## Webhook durability

The webhook endpoint verifies the HMAC over raw bytes, inserts the provider event and an outbox
row in one transaction, then returns. Celery dispatches the outbox later. Duplicate task delivery
is harmless because webhook IDs, payment facts, recovery actions, and provider references are
uniquely constrained.

Out-of-order facts use provider occurrence time. A delayed `subscription.pending` cannot regress
an active subscription or reopen a case after a newer charged event.

## Data and privacy

- Money uses integer minor units and ISO currency.
- Contact data is minimized/masked in reviewer mode.
- No card, mandate credential, API secret, or full payment instrument is stored.
- Audit events are append-only under the application contract and include version/hash evidence.
- Merchant policies are immutable versioned snapshots once used by a recovery run.
