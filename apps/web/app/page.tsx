import { RootRefresh } from "@/components/root-refresh";
import { RecoveryTable } from "@/components/recovery-table";
import { apiGet, DashboardSummary, formatMoney, RecoveryCase } from "@/lib/api";
import { demoCases } from "@/lib/demo-data";

export const dynamic = "force-dynamic";

const fallbackSummary: DashboardSummary = {
  revenue_at_risk_minor: demoCases.reduce((sum, item) => sum + item.risk_amount_minor, 0),
  verified_recovered_minor: 0,
  active_cases: demoCases.length,
  stopped_cases: 0,
  escalated_cases: 0,
  customers_contacted: 0,
  contacts_avoided: 0,
  recovery_spend_minor: 0,
  action_counts: {},
  state_counts: { DIAGNOSED: demoCases.length },
};

export default async function Home() {
  let summary = fallbackSummary;
  let cases = demoCases;
  let offline = false;
  try {
    [summary, cases] = await Promise.all([
      apiGet<DashboardSummary>("/dashboard/summary"),
      apiGet<RecoveryCase[]>("/recovery-cases?limit=50"),
    ]);
    if (!cases.length || !cases[0]?.customer_name) {
      cases = demoCases;
      offline = true;
    }
  } catch { offline = true; }

  return <div className="shell commandShell">
    <section className="rootHeader">
      <div><div className="eyebrow">Recovery operations</div><h1>Pending recoveries</h1><p>Open a record to inspect the failure, customer history, recommended action and execution timeline.</p></div>
      <RootRefresh />
    </section>

    {offline && <div className="demoWarning">The API is unavailable. Showing local fallback cases only.</div>}

    <section className="stats rootStats" aria-label="Recovery metrics">
      <article className="stat heroStat"><div className="statLabel">Revenue at risk</div><div className="statValue">{formatMoney(summary.revenue_at_risk_minor)}</div><div className="statMeta">Across {summary.active_cases} open recovery cases</div></article>
      <article className="stat"><div className="statLabel">Open cases</div><div className="statValue">{summary.active_cases}</div><div className="statMeta">Awaiting a recovery outcome</div></article>
      <article className="stat"><div className="statLabel">Verified recovered</div><div className="statValue positiveText">{formatMoney(summary.verified_recovered_minor)}</div><div className="statMeta">Confirmed through provider evidence</div></article>
      <article className="stat"><div className="statLabel">Contacts avoided</div><div className="statValue">{summary.contacts_avoided}</div><div className="statMeta">Cases held by policy or native retry</div></article>
    </section>

    <section className="panel recoveryQueue">
      <div className="panelHead queueHead"><div><h2>Recovery queue</h2><p>Click a row to open the case.</p></div><div className="queueSummary"><strong>{formatMoney(cases.reduce((sum, item) => sum + item.risk_amount_minor, 0))}</strong><span>visible risk</span></div></div>
      <RecoveryTable cases={cases} />
    </section>
  </div>;
}
