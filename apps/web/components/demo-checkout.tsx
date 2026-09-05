"use client";

import Link from "next/link";
import { useState } from "react";
import { formatMoney } from "@/lib/api";

type SimulationState = {
  simulation_id: string;
  progress: number;
  status: "RUNNING" | "COMPLETED";
  outcome: "in_progress" | "recovered" | "stopped";
};

async function request(path: string, init?: RequestInit) {
  const response = await fetch(`/api/recovery${path}`, init);
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(payload.detail ?? "Test payment could not be processed");
  }
  return response.json() as Promise<SimulationState>;
}

export function DemoCheckout({ caseId, simulationId, amountMinor, customerName, reference }: { caseId: string; simulationId: string; amountMinor: number; customerName: string; reference: string }) {
  const [status, setStatus] = useState<"ready" | "processing" | "paid">("ready");
  const [error, setError] = useState("");

  async function pay() {
    setStatus("processing"); setError("");
    try {
      let state = await request(`/recovery-cases/${caseId}/simulation`);
      if (state.simulation_id !== simulationId) throw new Error("This Test Payment Link has expired. Generate a new link from the recovery case.");
      for (let attempt = 0; attempt < 3 && state.progress < 7; attempt += 1) {
        state = await request(`/recovery-cases/${caseId}/simulation/advance`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ simulation_id: simulationId, command_id: crypto.randomUUID(), expected_progress: state.progress, promise_to_pay: false }),
        });
      }
      if (state.outcome !== "recovered") throw new Error("Payment was not verified by the recovery workflow.");
      setStatus("paid");
    } catch (caught) {
      setStatus("ready");
      setError(caught instanceof Error ? caught.message : "Test payment failed");
    }
  }

  if (status === "paid") return <section className="checkoutCard checkoutSuccess"><div className="successMark">✓</div><div className="eyebrow">Signed webhook processed</div><h1>Payment successful</h1><p>{formatMoney(amountMinor)} was verified and attributed to {reference}.</p><Link className="button buttonPrimary" href={`/cases/${caseId}`}>Return to recovered case →</Link></section>;

  return <section className="checkoutCard"><div className="checkoutBrand"><span className="brandMark">C</span><div><strong>ChaseLess Test Checkout</strong><small>Powered by a labelled Razorpay simulation</small></div></div><div className="checkoutBill"><span>Payment requested from</span><strong>{customerName}</strong><small>{reference}</small></div><div className="checkoutAmount"><span>Amount due</span><strong>{formatMoney(amountMinor)}</strong></div><div className="methodLabel">Choose a test payment method</div><div className="testMethods"><button className="selected" type="button"><span>UPI</span><small>Test success</small></button><button type="button"><span>Card</span><small>Test card</small></button><button type="button"><span>Netbanking</span><small>Demo bank</small></button></div><button className="button buttonPrimary checkoutPay" disabled={status === "processing"} onClick={pay}>{status === "processing" ? "Verifying signed test event…" : `Pay ${formatMoney(amountMinor)} in Test Mode`}</button>{error && <p className="checkoutError">{error}</p>}<div className="checkoutSafety"><span>🔒</span><p>No real money moves. Success is posted as a signed synthetic webhook and processed by the production recovery pipeline.</p></div></section>;
}
