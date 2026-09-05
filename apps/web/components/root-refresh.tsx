"use client";

import { useState } from "react";

export function RootRefresh() {
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function refreshData() {
    setRefreshing(true);
    setError("");
    try {
      const response = await fetch("/api/recovery/demo/reset", { method: "POST" });
      if (!response.ok) throw new Error("Refresh failed");
      window.location.reload();
    } catch {
      setError("Could not refresh the test data. Try again.");
      setRefreshing(false);
    }
  }

  return <div className="rootRefresh">
    <button className="button buttonSecondary" onClick={refreshData} disabled={refreshing}>
      {refreshing ? "Refreshing data…" : "↻ Refresh data"}
    </button>
    {error && <span role="status">{error}</span>}
  </div>;
}
