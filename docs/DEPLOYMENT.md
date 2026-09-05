# Deployment

ChaseLess ships as one Docker Compose stack: PostgreSQL, Redis, migration job, FastAPI, Celery
worker, Celery beat, and Next.js. API and worker use the same image and domain package, avoiding a
distributed-service contract inside the recovery core.

## Required deployment steps

1. Copy `.env.example` to a secret-managed `.env` and replace every security placeholder.
2. Set Razorpay Test Mode key ID, key secret, and webhook secret. Production/live mode is rejected
   by configuration in P0.
3. Set `WEB_PUBLIC_URL`, `API_PUBLIC_URL`, and the exact `CORS_ORIGINS` for the deployed hosts.
4. Run `docker compose up --build -d`; the migration job must succeed before API/worker startup.
5. Seed the controlled reviewer portfolio only in a reviewer environment: `docker compose exec api python -m scripts.seed_demo`.
6. Expose only ports 3000 and 8000 through TLS. Keep PostgreSQL and Redis private.
7. Configure Razorpay to send supported events to
   `https://<api-host>/api/v1/webhooks/razorpay` and run the signed test sequence in
   `docs/RAZORPAY_TEST_MODE.md`.

Set `ENV_FILE` when validating or launching with a non-default environment filename, for example
`ENV_FILE=.env.staging docker compose up --build -d`.

For the optional diagnosis advisory path, configure `LLM_PROVIDER=gemini`,
`LLM_MODEL=gemini-3.6-flash`, `LLM_FALLBACK_PROVIDER=groq`, and
`LLM_FALLBACK_MODEL=openai/gpt-oss-20b` with their corresponding API keys. Keep these values in the
deployment secret store. ChaseLess remains operational with `LLM_PROVIDER=disabled`.

## Contact data and messaging

Customer contact endpoints are stored only as Fernet-encrypted E.164 values. Before enabling any
non-mock messaging path, set a unique `FIELD_ENCRYPTION_KEY` in the deployment secret store. Generate
one locally (never paste the result into source control or chat) with:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`MESSAGING_PROVIDER=mock` is the safe default and sends nothing externally. A real provider must be
enabled deliberately, with its provider credentials present, a valid encrypted customer endpoint, and
recorded channel consent. Missing consent, endpoint data, encryption configuration, or provider
configuration must fail closed; they must never trigger a fallback send.

## Operations

- Back up PostgreSQL; Redis is not durable business truth.
- Poll `GET /api/v1/operations/health` and alert when Redis is unavailable, failed webhook events are
  non-zero, or pending outbox events grow instead of draining. The dashboard displays the same
  operational snapshot for the Buildathon submission.
- Rotate webhook secrets by setting both current and previous secrets during the overlap window.
- Scale workers horizontally; unique constraints and processed markers tolerate at-least-once task
  delivery.
- Alert on webhook processing errors, growing undispatched outbox rows, failed actions, and readiness
  failures.
- Never enable real messaging or Razorpay live mode for the Buildathon deployment.

SQLite, the mock messaging adapter, and the development service token are local conveniences and
are not deployment defaults.
