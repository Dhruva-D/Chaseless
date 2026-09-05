import { apiGet, formatDateTime, formatMoney } from "@/lib/api";

type Strategy = { recovered_minor: number; recovered_cases: number; contacts: number; spend_minor: number; policy_violations: number };
type Result = { config: { seed: number; customers: number }; config_hash: string; metrics: { native_recovery: Strategy; fixed_dunning: Strategy; chaseless: Strategy; incremental: { vs_fixed_dunning_minor: number; contacts_avoided_vs_fixed: number } } };
type WebhookEvent = { id: string; provider_event_id: string; event_type: string; source: string; signature_valid: boolean; received_at: string; processed_at: string | null; processing_error: string | null };

export const dynamic = "force-dynamic";

function sourceSignature(event: WebhookEvent) {
  if (event.source === "razorpay_test_api_import") return "API import";
  return event.signature_valid ? "Verified" : "Rejected";
}

export default async function EvidencePage() {
  let result: Result | null = null;
  let webhookEvents: WebhookEvent[] = [];
  try { [result, webhookEvents] = await Promise.all([apiGet<Result>("/evaluation/latest"), apiGet<WebhookEvent[]>("/webhook-events?limit=12")]); } catch {
    try { webhookEvents = await apiGet<WebhookEvent[]>("/webhook-events?limit=12"); } catch {}
  }
  const strategies = result ? [["Native recovery", result.metrics.native_recovery], ["Fixed dunning", result.metrics.fixed_dunning], ["ChaseLess", result.metrics.chaseless]] as const : [];
  return <div className="shell"><section className="hero"><div><div className="eyebrow">Reviewer evidence mode</div><h1>Same customers.<br />Fewer interruptions.</h1><p>Matched-seed counterfactual evaluation. Hidden response propensities never enter the decision engine.</p></div>{result && <span className="tag">Seed {result.config.seed} · {result.config.customers.toLocaleString("en-IN")} customers</span>}</section>
    <section className="panel"><div className="panelHead"><div><h2>Strategy comparison</h2><p>Identical population, merchant budget and safety limits</p></div></div>{result ? <><div className="compare">{strategies.map(([name, strategy]) => <article className={`strategy ${name === "ChaseLess" ? "featured" : ""}`} key={name}><h3>{name}</h3><dl><div><dt>Recovered</dt><dd>{formatMoney(strategy.recovered_minor)}</dd></div><div><dt>Customers</dt><dd>{strategy.recovered_cases.toLocaleString("en-IN")}</dd></div><div><dt>Contacts</dt><dd>{strategy.contacts.toLocaleString("en-IN")}</dd></div><div><dt>Spend</dt><dd>{formatMoney(strategy.spend_minor)}</dd></div><div><dt>Violations</dt><dd>{strategy.policy_violations}</dd></div></dl></article>)}</div><div className="proof">ChaseLess recovered {formatMoney(result.metrics.incremental.vs_fixed_dunning_minor)} more than fixed dunning while avoiding {result.metrics.incremental.contacts_avoided_vs_fixed} contacts.</div><p className="statMeta">Configuration hash: {result.config_hash}</p></> : <div className="empty">No benchmark result yet. Run <code>docker compose exec api python -m scripts.run_demo</code> to prepare all reviewer evidence.</div>}</section>
    <section className="panel"><div className="panelHead"><div><h2>Webhook evidence ledger</h2><p>Raw-body signatures are verified before events enter the durable inbox.</p></div><span className="tag">{webhookEvents.length} recent events</span></div>{webhookEvents.length ? <div className="tableWrap"><table><thead><tr><th>Event</th><th>Source</th><th>Signature</th><th>Processed</th><th>Received (IST)</th></tr></thead><tbody>{webhookEvents.map((event) => <tr key={event.id}><td><strong>{event.event_type}</strong><br /><span className="statMeta">{event.provider_event_id.slice(0, 18)}</span></td><td><span className={`status ${event.source}`}>{event.source.replaceAll("_", " ")}</span></td><td>{sourceSignature(event)}</td><td>{event.processing_error ? "Failed" : event.processed_at ? "Complete" : "Queued"}</td><td>{formatDateTime(event.received_at)}</td></tr>)}</tbody></table></div> : <div className="empty">No webhook events have been received yet. Reset demo data from the command center to create signed synthetic evidence.</div>}<p className="statMeta">Synthetic fixtures and Test API imports are visibly labelled. Only signed webhook deliveries are marked verified.</p></section>
  </div>;
}
