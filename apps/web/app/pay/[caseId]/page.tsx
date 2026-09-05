import Link from "next/link";
import { DemoCheckout } from "@/components/demo-checkout";
import { apiGet, RecoveryCase } from "@/lib/api";

type CheckoutCase = RecoveryCase & { episode_key: string };

export default async function TestPaymentPage({ params, searchParams }: { params: Promise<{ caseId: string }>; searchParams: Promise<{ simulation_id?: string }> }) {
  const { caseId } = await params;
  const { simulation_id: simulationId } = await searchParams;
  const item = await apiGet<CheckoutCase>(`/recovery-cases/${caseId}`);
  if (!simulationId) return <div className="checkoutShell"><section className="checkoutCard"><h1>Invalid Test Payment Link</h1><p>This link is missing its recovery simulation reference.</p><Link className="caseLink" href={`/cases/${caseId}`}>Return to case →</Link></section></div>;
  return <div className="checkoutShell"><DemoCheckout caseId={caseId} simulationId={simulationId} amountMinor={item.risk_amount_minor} customerName={item.customer_name} reference={item.provider_reference} /></div>;
}
