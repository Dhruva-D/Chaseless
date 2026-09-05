"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { formatMoney } from "@/lib/api";

type AutomationState = {
  status: "IDLE" | "SCHEDULED" | "EXECUTING" | "WAITING_FOR_RESPONSE" | "WAITING_FOR_PAYMENT" | "HUMAN_REVIEW" | "RECOVERED" | "FAILED" | "BLOCKED";
  action_id: string | null; action_type: string | null; action_status: string | null;
  rationale: string | null; decision_source: string | null; payment_url: string | null;
  delivery_channel: string | null; delivery_provider: string | null;
  delivery_warning: string | null;
  voice_response: { intent?: string; commitment_date?: string; confidence?: number } | null;
  error: string | null; updated_at: string | null;
};

function friendly(value: string | null) {
  return (value ?? "Pending").replaceAll("_", " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
}

async function request(path: string, init?: RequestInit) {
  const response = await fetch(`/api/recovery${path}`, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(payload.detail ?? "Recovery automation request failed");
  }
  return response.json() as Promise<AutomationState>;
}

export function RecoveryAutomation({ caseId, amountMinor, diagnosis }: { caseId: string; amountMinor: number; diagnosis: string }) {
  const router = useRouter();
  const [state, setState] = useState<AutomationState | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const read = () => request(`/recovery-cases/${caseId}/automation`).then((next) => { if (alive) setState((previous) => { if (previous?.updated_at !== next.updated_at || previous?.status !== next.status) router.refresh(); return next; }); }).catch(() => undefined);
    void read();
    const timer = window.setInterval(() => { void read(); }, 2200);
    return () => { alive = false; window.clearInterval(timer); };
  }, [caseId, router]);

  async function start() {
    setStarting(true); setError("");
    try {
      setState(await request(`/recovery-cases/${caseId}/automation/start`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command_id: crypto.randomUUID() }),
      }));
      router.refresh();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not begin automation"); }
    finally { setStarting(false); }
  }

  const status = state?.status ?? "IDLE";
  const active = status === "SCHEDULED" || status === "EXECUTING";
  const awaitingHuman = status === "HUMAN_REVIEW";
  const awaitingOutcome = status === "WAITING_FOR_RESPONSE" || status === "WAITING_FOR_PAYMENT";
  const steps = [
    ["Detected", "Razorpay failure is already recorded for this recovery episode."],
    ["Diagnosed", friendly(diagnosis)],
    ["Decision", state?.rationale ?? "Gemini evaluates only the actions cleared by policy."],
    ["Action", state?.action_type ? `${friendly(state.action_type)} ${active ? "is being triggered automatically" : "completed"}.` : "Waiting for an authorized decision."],
    ["Response / payment", state?.voice_response ? `Customer response: ${friendly(state.voice_response.intent ?? "received")}.` : status === "WAITING_FOR_PAYMENT" ? "Razorpay Payment Link sent; waiting for the signed paid webhook." : status === "WAITING_FOR_RESPONSE" ? "Call is live; the customer’s Twilio speech response will appear here automatically." : "Awaiting the action outcome."],
    ["Verified recovery", status === "RECOVERED" ? "Razorpay confirmed the payment and recovery is recorded." : "Only a signed Razorpay event can mark this as recovered."],
  ];
  const completed = status === "IDLE" ? 2 : status === "SCHEDULED" ? 3 : status === "EXECUTING" ? 3 : status === "RECOVERED" ? 6 : 4;

  return <section className={status === "RECOVERED" ? "simulation recoveredSimulation" : "simulation"}>
    <div className="simulationTop"><div><div className="eyebrow">AI recovery orchestration · audited</div><h2>{status === "IDLE" ? `Recover ${formatMoney(amountMinor)} automatically` : `Recovery automation: ${friendly(status)}`}</h2><p>One click runs detection, diagnosis, LLM decision, and the policy-bounded outbound action.</p></div><div className="simControl"><button className="button buttonPrimary" disabled={starting || active || awaitingHuman || awaitingOutcome || status === "RECOVERED"} onClick={start}>{starting ? "Starting automation…" : active ? "Automation is running…" : status === "WAITING_FOR_RESPONSE" ? "Waiting for customer response" : status === "WAITING_FOR_PAYMENT" ? "Waiting for Razorpay payment" : awaitingHuman ? "Human review requested" : status === "RECOVERED" ? "Recovered and verified" : "▶ Simulate AI recovery"}</button><span><i className="persistDot" /> Sends only to the configured test recipient</span>{state?.delivery_warning && <strong className="simWarning">{state.delivery_warning}</strong>}{(error || state?.error) && <strong className="simError">{error || state?.error}</strong>}</div></div>
    <div className="simGrid"><div className="simSteps">{steps.map(([name, detail], index) => { const number = index + 1; const done = number <= completed; const current = number === completed + 1 && active; return <div className={`simStep ${done ? "done" : ""} ${current ? "active" : ""}`} key={name}><div className="stepMarker">{done ? "✓" : number}</div><div><strong>{name}</strong><p>{detail}</p><span>{number === 3 && state?.decision_source ? `Decision source: ${state.decision_source}` : number === 4 && state?.delivery_provider ? `Delivered via ${state.delivery_provider}${state.delivery_channel ? ` · ${state.delivery_channel}` : ""}` : "Durable audit event"}</span></div></div>; })}</div>
      <aside className="decisionCard"><div className="decisionHead"><span>AI decision memo</span><strong>{friendly(state?.action_type ?? "pending")}</strong></div><div className="decisionRows"><div><span>Revenue at risk</span><strong>{formatMoney(amountMinor)}</strong></div><div><span>Diagnosis</span><strong>{friendly(diagnosis)}</strong></div></div><div className="whyBox"><strong>Why this action</strong><p>{state?.rationale ?? "The explanation appears only after Gemini has selected a policy-authorized action."}</p></div>{state?.payment_url && <a className="testPaymentLink" href={state.payment_url} target="_blank" rel="noreferrer">Open actual Razorpay Test Payment Link →</a>}{state?.voice_response && <div className="promiseEvidence"><strong>Voice response captured</strong><span>{friendly(state.voice_response.intent ?? "unknown")}{state.voice_response.commitment_date ? ` · expected ${state.voice_response.commitment_date}` : ""}</span></div>}<div className="stopRule"><span>■</span><p><strong>Safety boundary</strong> Gemini chooses only from actions allowed by consent, contact caps, and policy. A webhook—not this screen—verifies payment.</p></div></aside>
    </div>
  </section>;
}
