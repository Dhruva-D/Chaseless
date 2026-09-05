# API and Trust Contracts

The canonical machine-readable contract is `packages/contracts/openapi.json`. Regenerate it with
`python -m scripts.export_openapi` after changing a route or response model.

## Trust boundaries

- Razorpay calls `POST /api/v1/webhooks/razorpay`. The API verifies the HMAC against the exact raw
  body before JSON parsing and requires `X-Razorpay-Event-Id` for durable deduplication.
- Browser reads use the public FastAPI GET endpoints.
- Recovery mutations pass through the Next.js `/api/recovery/*` server route. It injects
  `X-Internal-Token`; the credential is not included in client JavaScript.
- Executing a run additionally requires `Idempotency-Key`. Repeating execution for an already
  executing run returns its current state without enqueueing duplicate actions.

## Reviewer endpoints

| Method and path | Purpose |
|---|---|
| `GET /health/live` | Process liveness |
| `GET /health/ready` | Database readiness |
| `POST /api/v1/webhooks/razorpay` | Signed provider event ingestion |
| `GET /api/v1/dashboard/summary` | Recovery and contact totals |
| `GET /api/v1/recovery-cases` | Portfolio list |
| `GET /api/v1/recovery-cases/{id}` | Customer 360 and audit timeline |
| `POST /api/v1/recovery-runs/preview` | Persist a budget-bounded plan |
| `POST /api/v1/recovery-runs/{id}/approve` | Approve a preview |
| `POST /api/v1/recovery-runs/{id}/execute` | Enqueue non-gated actions |
| `POST /api/v1/recovery-actions/{id}/approval` | Approve/reject a gated action |
| `GET /api/v1/evaluation/latest` | Read committed benchmark evidence |

Razorpay API secrets, webhook secrets, internal tokens, and full payment instrument data are never
returned by an endpoint or persisted in audit payloads.
