"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { formatMoney, RecoveryCase } from "@/lib/api";

const FILTERS = ["All", "Failed subscription", "Overdue B2B invoice", "Mandate failure"];

function friendly(value = "Pending") {
  return value.replaceAll("_", " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
}

export function RecoveryTable({ cases }: { cases: RecoveryCase[] }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All");
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return cases.filter((item) => {
      const matchesFilter = filter === "All" || item.source_type === filter;
      const haystack = [item.customer_name, item.provider_reference, item.diagnosis.failure_class, item.next_step]
        .filter(Boolean).join(" ").toLowerCase();
      return matchesFilter && (!needle || haystack.includes(needle));
    });
  }, [cases, filter, query]);

  return (
    <>
      <div className="tableTools">
        <div className="filterGroup" aria-label="Filter recovery cases">
          {FILTERS.map((item) => (
            <button key={item} className={filter === item ? "filter active" : "filter"} onClick={() => setFilter(item)}>{item}</button>
          ))}
        </div>
        <label className="searchBox">
          <span>⌕</span>
          <input aria-label="Search cases" placeholder="Search customer, ID or reason" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
      </div>
      <div className="tableWrap">
        <table className="recoveryTable">
          <thead><tr><th>Customer & case</th><th>Revenue at risk</th><th>Diagnosis</th><th>Stage</th><th aria-label="Open case" /></tr></thead>
          <tbody>
            {visible.map((item) => (
              <tr key={item.id} tabIndex={0} role="link" onClick={() => router.push(`/cases/${item.id}`)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") router.push(`/cases/${item.id}`); }}>
                <td><strong>{item.customer_name}</strong><span className="cellMeta">{item.source_type} · {item.provider_reference}</span></td>
                <td><strong className="amount">{formatMoney(item.risk_amount_minor, item.currency)}</strong><span className={`priority ${item.recovery_priority}`}>{item.recovery_priority} priority</span></td>
                <td><strong>{friendly(item.diagnosis.failure_class ?? "Pending")}</strong><span className="cellMeta">{Math.round((item.diagnosis.confidence ?? 0) * 100)}% confidence</span></td>
                <td><span className={`status ${item.state}`}>{friendly(item.state)}</span></td>
                <td><span className="rowArrow">→</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!visible.length && <div className="empty">No recovery cases match this view.</div>}
    </>
  );
}
