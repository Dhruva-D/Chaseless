"use client";

import { useState } from "react";
import { formatMoney } from "@/lib/api";

type Candidate = {
  payment_id: string;
  amount_minor: number;
  currency: string;
  occurred_at: string;
  failure_code: string | null;
  failure_reason: string | null;
  failure_description: string | null;
  subscription_id: string | null;
  invoice_id: string | null;
  eligible: boolean;
  skip_reason: string | null;
};
type ImportResult = { imported_case_ids: string[]; skipped_payment_ids: string[] };

export function RazorpayTestImporter() {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [state, setState] = useState<"idle" | "loading" | "importing">("idle");
  const [message, setMessage] = useState("");
  const eligible = candidates.filter((item) => item.eligible);

  async function preview() {
    setState("loading"); setMessage("");
    try {
      const response = await fetch("/api/recovery/razorpay/test-import/preview?count=25");
      const payload = await response.json() as Candidate[] | { detail?: string };
      if (!response.ok || !Array.isArray(payload)) throw new Error("detail" in payload ? payload.detail : "Could not load Test Mode payments");
      setCandidates(payload);
      setMessage(payload.length ? "Preview loaded. Nothing has been imported or contacted." : "No failed payments were found in the latest 25 Test Mode records.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not load Test Mode payments"); }
    finally { setState("idle"); }
  }

  async function importEligible() {
    if (!eligible.length) return;
    setState("importing"); setMessage("");
    try {
      const response = await fetch("/api/recovery/razorpay/test-import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ payment_ids: eligible.map((item) => item.payment_id) }) });
      const payload = await response.json() as ImportResult | { detail?: string };
      if (!response.ok || !("imported_case_ids" in payload)) throw new Error("detail" in payload ? payload.detail : "Import failed");
      setMessage(`${payload.imported_case_ids.length} recovery case${payload.imported_case_ids.length === 1 ? "" : "s"} imported. No action was sent; open the command center to inspect them.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Import failed"); }
    finally { setState("idle"); }
  }

  return <section className="importPanel">
    <div className="importIntro"><div><div className="eyebrow">Read-only external data</div><h2>Razorpay Test Mode import</h2><p>Fetch recent failed payments with Test credentials kept on the server. Only failures linked to a Razorpay subscription can become recovery cases.</p></div><button className="button buttonSecondary" onClick={preview} disabled={state !== "idle"}>{state === "loading" ? "Loading…" : "Preview latest failures"}</button></div>
    {message && <p className="importMessage">{message}</p>}
    {candidates.length > 0 && <><div className="importSummary"><span>{candidates.length} failed payments found</span><span>{eligible.length} eligible subscription cases</span><span>{candidates.length - eligible.length} safely skipped</span></div><div className="tableWrap"><table className="importTable"><thead><tr><th>Payment</th><th>Failure signal</th><th>Amount</th><th>Subscription link</th><th>Import decision</th></tr></thead><tbody>{candidates.map((item) => <tr key={item.payment_id}><td><strong>{item.payment_id}</strong><br /><span className="statMeta">{new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" }).format(new Date(item.occurred_at))}</span></td><td>{item.failure_reason ?? item.failure_code ?? "Unknown failure"}<br /><span className="statMeta">{item.failure_description ?? "No description provided"}</span></td><td className="amount">{formatMoney(item.amount_minor, item.currency)}</td><td>{item.subscription_id ?? "No linked subscription"}</td><td><span className={`status ${item.eligible ? "RECOVERED_VERIFIED" : "STOPPED"}`}>{item.eligible ? "Eligible" : "Skipped"}</span><br /><span className="statMeta">{item.eligible ? "Create diagnosed case only" : item.skip_reason}</span></td></tr>)}</tbody></table></div><div className="importFooter"><p>Import preserves a minimal, PII-free evidence record. It does not send messages, create payment links, or treat an API read as a signed webhook.</p><button className="button buttonPrimary" onClick={importEligible} disabled={!eligible.length || state !== "idle"}>{state === "importing" ? "Importing cases…" : `Import ${eligible.length} eligible case${eligible.length === 1 ? "" : "s"}`}</button></div></>}
  </section>;
}
