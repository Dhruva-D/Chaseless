const PUBLIC_API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const SERVER_API_URL = process.env.API_INTERNAL_URL ?? PUBLIC_API_URL;

export const API_URL =
  typeof window === "undefined" ? SERVER_API_URL : PUBLIC_API_URL;

export type DashboardSummary = {
  revenue_at_risk_minor: number;
  verified_recovered_minor: number;
  active_cases: number;
  stopped_cases: number;
  escalated_cases: number;
  customers_contacted: number;
  contacts_avoided: number;
  recovery_spend_minor: number;
  action_counts: Record<string, number>;
  state_counts: Record<string, number>;
};

export type RecoveryCase = {
  id: string;
  state: string;
  risk_amount_minor: number;
  currency: string;
  natural_recovery_score: number;
  diagnosis: {
    failure_class?: string;
    confidence?: number;
    evidence?: string[];
  };
  created_at: string;
  customer_name: string;
  customer_segment: string;
  source_type: string;
  provider_reference: string;
  recommended_action: string;
  next_step: string;
  recovery_priority: string;
};

export type OperationsHealth = {
  status: "ready" | "degraded";
  database: "ready";
  redis: "ready" | "unavailable";
  pending_outbox_events: number;
  failed_webhook_events: number;
};

export async function apiGet<T>(path: string): Promise<T> {
  const headers = new Headers();
  if (typeof window === "undefined") {
    headers.set(
      "x-internal-token",
      process.env.INTERNAL_SERVICE_TOKEN ?? "development-service-token",
    );
  }
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store", headers });
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json() as Promise<T>;
}

export function formatMoney(minor: number, currency = "INR") {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(minor / 100);
}

export function formatDateTime(value: string) {
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`;
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: "Asia/Kolkata",
  }).format(new Date(normalized));
}
