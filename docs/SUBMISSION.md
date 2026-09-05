# Track 03 Submission Notes

## Project statement

ChaseLess is an AI-assisted revenue recovery control plane for Razorpay merchants. It detects
failed subscription payments and overdue receivables, diagnoses the failure with provider-backed
evidence, selects the next best action using recovery economics, executes a bounded outreach or
payment-link workflow, and verifies recovery only from trusted provider events.

## What is automated

One recovery command creates a durable run, invokes the advisory model, evaluates deterministic
policy, executes the selected provider action, records delivery evidence, and keeps the case open
until payment verification. The voice path can place a consented Twilio call, capture a spoken
promise-to-pay response, and schedule the secure Razorpay follow-up link.

## Safety and governance

The LLM is advisory and schema-constrained. It cannot create or alter an amount, recipient, URL,
policy, budget, opt-out state or idempotency key. The executor re-checks policy immediately before
provider calls. Contact caps, consent channels, quiet hours, EIRV floors, intervention limits and
human review are deterministic controls. Every decision and provider reference is persisted in the
case audit trail.

## Evaluation claims

The benchmark is a matched-world simulation over the same customer population and random outcome
variable. It reports recovered amount, contact volume and policy violations for three strategies.
The repository includes committed JSON/CSV artifacts and the command used to regenerate them. The
benchmark is evidence of comparative system behavior, not a causal production forecast.

## Provider modes

The application is wired to Razorpay Test Mode and optional Gemini, Groq, Twilio and Sarvam
adapters. Provider credentials are injected through `.env` and are never committed. Twilio Trial
accounts can restrict custom SMS/WhatsApp/email content; when that happens, the workflow records
the limitation and falls back to Razorpay-native Payment Link notification where supported. A
production merchant must configure approved senders/templates or SMTP/SendGrid before enabling
custom branded delivery.

## Reviewer checklist

- Start at `/` and open one row in the recovery portfolio.
- Click **Simulate AI recovery** once; watch the automatic state transition.
- Inspect the AI rationale and decision source (`llm:gemini`, `llm:groq`, or explicit fallback).
- Inspect the outbound provider reference and generated Razorpay Test Payment Link.
- On the voice case, review captured intent and promise-to-pay evidence.
- Open Proof and confirm recovery is counted only after a signed event.
- Run the benchmark command and compare the committed result artifacts.
