import Link from "next/link";
import { RecoveryAutomation } from "@/components/recovery-automation";
import { apiGet, formatDateTime, formatMoney, RecoveryCase } from "@/lib/api";
import { demoCases } from "@/lib/demo-data";

type HistoryItem = { period: string; amount_minor: number; status: string };
type AutomationSummary = {
  status: string;
  action_type: string | null;
  decision_source: string | null;
};
type CaseDetail = RecoveryCase & {
  customer_id: string; episode_key: string; contact_count: number; replan_count: number;
  recovered_amount_minor: number; recovered_at: string | null;
  customer: { name?: string; email?: string; phone?: string; segment?: string; tenure_months?: number; lifetime_value_minor?: number; contacts_7d?: number; opted_out?: boolean };
  subscription: { id?: string; plan_id?: string; status?: string; amount_minor?: number };
  payment_history: HistoryItem[];
  timeline: { id: string; kind: string; aggregate_type: string; actor: string; decision: Record<string, unknown>; created_at: string }[];
};

function fallbackCase(id: string): CaseDetail | null {
  const item = demoCases.find((entry) => entry.id === id); if (!item) return null;
  return { ...item, customer_id: `cust-${id}`, episode_key: `${item.provider_reference}:cycle:2026-09`, contact_count: 0, replan_count: 0, recovered_amount_minor: 0, recovered_at: null,
    customer: { name: item.customer_name, email: `${item.customer_name.slice(0, 3).toLowerCase()}***@example.com`, phone: "+91 ******0000", segment: item.customer_segment, tenure_months: 14, lifetime_value_minor: item.risk_amount_minor * 10, contacts_7d: 0, opted_out: false },
    subscription: { id: item.provider_reference, plan_id: "plan_growth_annual", status: "halted", amount_minor: item.risk_amount_minor },
    payment_history: Array.from({ length: 8 }, (_, index) => ({ period: `M-${8-index}`, amount_minor: item.risk_amount_minor, status: index > 5 ? "failed" : "paid" })),
    timeline: [
      { id: "evt-1", kind: "EXPECTED_PAYMENT_DUE", aggregate_type: "subscription", actor: "billing-schedule", decision: {}, created_at: "2026-08-31T04:30:00Z" },
      { id: "evt-2", kind: "PAYMENT_FAILURE_DETECTED", aggregate_type: "recovery_case", actor: "razorpay-webhook", decision: {}, created_at: "2026-08-31T04:31:12Z" },
      { id: "evt-3", kind: "FAILURE_DIAGNOSED", aggregate_type: "recovery_case", actor: "diagnosis-engine-v1", decision: {}, created_at: "2026-08-31T04:31:14Z" },
    ] };
}

function timelineSummary(decision: Record<string, unknown>) {
  if (typeof decision.content_redacted === "string") return decision.content_redacted;
  if (typeof decision.intent === "string") return `Customer response: ${decision.intent.replaceAll("_", " ")}.`;
  return null;
}

export default async function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params; let offline = false; let item: CaseDetail;
  try { item = await apiGet<CaseDetail>(`/recovery-cases/${id}`); }
  catch { const demo = fallbackCase(id); if (!demo) throw new Error("Recovery case is unavailable"); item = demo; offline = true; }
  let automation: AutomationSummary | null = null;
  if (!offline) {
    try { automation = await apiGet<AutomationSummary>(`/recovery-cases/${id}/automation`); }
    catch { automation = null; }
  }
  const evidence = item.diagnosis.evidence ?? [];
  const failureCode = evidence.find((entry) => entry.startsWith("provider_failure_code="))?.split("=", 2)[1] ?? "Not provided";
  const conversations = item.timeline.filter((event) => event.aggregate_type === "conversation");
  return <div className="shell caseShell">
    <div className="crumbRow"><Link className="caseLink" href="/">← Recovery portfolio</Link><div><span className="modePill">{offline ? "Labelled simulation" : "Razorpay Test Mode"}</span><span className={`status ${item.state}`}>{item.state.replaceAll("_", " ")}</span></div></div>
    <section className="caseHeader"><div><div className="eyebrow">Recovery episode · {item.source_type}</div><h1>{item.customer_name}</h1><p>{item.provider_reference} · Created {formatDateTime(item.created_at)}</p></div><div className="caseAmount"><span>Revenue at risk</span><strong>{formatMoney(item.risk_amount_minor, item.currency)}</strong><small>{item.recovery_priority} priority</small></div></section>

    <section className="caseOverview">
      <article className="caseFact"><span>Failure diagnosis</span><strong>{(item.diagnosis.failure_class ?? "Pending").replaceAll("_", " ")}</strong><small>{Math.round((item.diagnosis.confidence ?? 0) * 100)}% confidence</small></article>
      <article className="caseFact"><span>AI decision</span><strong>{automation?.action_type ? automation.action_type.replaceAll("_", " ") : "Pending recovery run"}</strong><small>{automation?.decision_source ? `Decision source: ${automation.decision_source}` : "The AI explains its policy-authorized choice after you start recovery"}</small></article>
      <article className="caseFact"><span>Natural recovery</span><strong>{Math.round(item.natural_recovery_score * 100)}%</strong><small>Without ChaseLess intervention</small></article>
      <article className="caseFact"><span>Customer value</span><strong>{formatMoney(item.customer.lifetime_value_minor ?? item.risk_amount_minor * 8)}</strong><small>{item.customer.tenure_months ?? 8} month relationship</small></article>
    </section>

    {!offline && <RecoveryAutomation caseId={item.id} amountMinor={item.risk_amount_minor} diagnosis={item.diagnosis.failure_class ?? "UNKNOWN"} />}

    <div className="detailGrid afterSimulation">
      <section className="panel"><div className="panelHead"><div><div className="eyebrow">Detect + diagnose</div><h2>Failure evidence</h2><p>Provider signals and customer history behind the classification.</p></div><span className="tag">{Math.round((item.diagnosis.confidence ?? 0) * 100)}% confidence</span></div><ul className="evidenceList"><li>Expected charge: {formatMoney(item.risk_amount_minor)} via {item.provider_reference}</li><li>Razorpay failure code: <strong>{failureCode}</strong></li>{evidence.filter((entry) => !entry.startsWith("provider_failure_code=")).map((entry) => <li key={entry}>{entry.replaceAll("_", " ")}</li>)}<li>Customer history: {item.payment_history.filter((entry) => entry.status === "paid").length} of {item.payment_history.length} recent payments successful</li></ul></section>
      <section className="panel customerPanel"><div className="panelHead"><div><div className="eyebrow">Customer 360</div><h2>{item.customer.name ?? item.customer_name}</h2></div><span className="tag">{item.customer.segment ?? item.customer_segment}</span></div><dl className="customerFacts"><div><dt>Masked email</dt><dd>{item.customer.email ?? "Not available"}</dd></div><div><dt>Masked phone</dt><dd>{item.customer.phone ?? "Not available"}</dd></div><div><dt>Contacts in 7 days</dt><dd>{item.customer.contacts_7d ?? item.contact_count} / 3 allowed</dd></div><div><dt>Opted out</dt><dd>{item.customer.opted_out ? "Yes — automation blocked" : "No"}</dd></div></dl></section>
    </div>

    {conversations.length > 0 && <section className="panel conversationPanel"><div className="panelHead"><div><div className="eyebrow">Voice + promise intelligence</div><h2>Recovery conversation</h2><p>Redacted transcript with structured intent extraction.</p></div><span className="tag">Hinglish supported</span></div><div className="transcript">{conversations.map((event) => { const outbound = event.kind.endsWith("OUTBOUND"); const confidence = Number(event.decision.confidence); return <div className={outbound ? "transcriptTurn agent" : "transcriptTurn customer"} key={event.id}><span>{outbound ? "AI recovery agent" : item.customer_name}</span><p>{String(event.decision.content_redacted ?? "Redacted recovery message")}</p>{typeof event.decision.intent === "string" && <small>Intent: {event.decision.intent.replaceAll("_", " ")}{Number.isFinite(confidence) ? ` · ${Math.round(confidence * 100)}% confidence` : ""}</small>}</div>; })}</div></section>}

    <section className="panel auditPanel"><div className="panelHead"><div><div className="eyebrow">Verify + recover</div><h2>End-to-end audit trail</h2><p>Who decided what, when, and which provider event proved the outcome.</p></div><span className="auditSeal">Immutable log</span></div><div className="timeline">{item.timeline.map((event) => <div className="timelineItem" key={event.id}><span className="timelineDot" /><div className="timelineBody"><strong>{event.kind.replaceAll("_", " ")}</strong><p>{event.aggregate_type.replaceAll("_", " ")} · {event.actor} · {formatDateTime(event.created_at)} IST</p>{timelineSummary(event.decision) && <p>{timelineSummary(event.decision)}</p>}</div></div>)}</div></section>
  </div>;
}
