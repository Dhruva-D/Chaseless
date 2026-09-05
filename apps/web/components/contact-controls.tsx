"use client";

import { FormEvent, useState } from "react";

export function ContactControls({ customerId }: { customerId: string }) {
  const [e164, setE164] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch(
        `/api/recovery/customers/${customerId}/contact-endpoints/whatsapp`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ e164, consent }),
        },
      );
      const payload = (await response.json()) as {
        detail?: string;
        masked_endpoint?: string;
        consent?: boolean;
      };
      if (!response.ok) throw new Error(payload.detail ?? "Could not save contact settings");
      setE164("");
      setMessage(
        payload.consent
          ? `Consent recorded securely for ${payload.masked_endpoint}.`
          : `Consent withdrawn for ${payload.masked_endpoint}.`,
      );
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Could not save contact settings");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panelHead">
        <div><h2>Contact permission</h2><p>Encrypted endpoint storage with explicit channel consent.</p></div>
      </div>
      <form className="contactForm" onSubmit={submit}>
        <label>
          WhatsApp number (E.164)
          <input
            value={e164}
            onChange={(event) => setE164(event.target.value)}
            placeholder="+919876543210"
            required
          />
        </label>
        <label className="checkLabel">
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
          Customer explicitly consents to WhatsApp recovery messages
        </label>
        <button className="button buttonSecondary" disabled={busy} type="submit">
          {busy ? "Saving…" : "Save permission"}
        </button>
      </form>
      {message && <p className="statMeta">{message}</p>}
    </section>
  );
}
