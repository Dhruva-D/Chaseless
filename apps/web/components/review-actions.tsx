"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

type ReviewAction = {
  action_id: string;
  requires_approval: boolean;
};

async function decide(actionId: string, approve: boolean) {
  const response = await fetch(`/api/recovery/recovery-actions/${actionId}/approval`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      approve,
      reason: approve ? "Approved by demo merchant" : "Rejected by demo merchant",
    }),
  });
  if (!response.ok) throw new Error(await response.text());
}

export function ApproveAllActions({ items }: { items: ReviewAction[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function approveAll() {
    setBusy(true);
    setError("");
    try {
      for (const item of items.filter((entry) => entry.requires_approval)) {
        await decide(item.action_id, true);
      }
      router.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Bulk approval failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <button className="button buttonPrimary" disabled={busy} onClick={approveAll}>
        {busy ? "Approving and sending…" : "Approve all and send"}
      </button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

export function ReviewDecision({ actionId }: { actionId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(approve: boolean) {
    setBusy(true);
    setError("");
    try {
      await decide(actionId, approve);
      router.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Decision failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="reviewActionButtons">
      <button className="button buttonPrimary" disabled={busy} onClick={() => submit(true)}>
        {busy ? "Working…" : "Approve & send"}
      </button>
      <button className="button buttonSecondary" disabled={busy} onClick={() => submit(false)}>
        Reject
      </button>
      {error && <span className="error">{error}</span>}
    </div>
  );
}
