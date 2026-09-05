import Link from "next/link";
import { ApproveAllActions, ReviewDecision } from "@/components/review-actions";
import { apiGet, formatDateTime, formatMoney } from "@/lib/api";

type ReviewItem = {
  action_id: string;
  case_id: string;
  action_type: string;
  action_status: string;
  case_state: string;
  amount_minor: number;
  currency: string;
  requires_approval: boolean;
  created_at: string;
};

export default async function ReviewPage() {
  const items = await apiGet<ReviewItem[]>("/review-queue");
  return (
    <div className="shell">
      <section className="hero">
        <div>
          <div className="eyebrow">Human in the loop</div>
          <h1>Review queue</h1>
          <p>Only policy-escalated or approval-required recovery cases appear here.</p>
        </div>
        <div className="reviewHeaderActions">
          <span className="tag">{items.length} open</span>
          {items.length > 0 && <ApproveAllActions items={items} />}
        </div>
      </section>
      {items.length > 0 && <div className="proof">These actions can contact a customer or create a payment link, so execution pauses here until a human approves them.</div>}
      <section className="panel">
        {items.length ? <div className="tableWrap"><table><thead><tr><th>Case</th><th>Action</th><th>Amount</th><th>Reason</th><th>Created (IST)</th><th>Decision</th></tr></thead><tbody>{items.map((item) => <tr key={item.action_id}><td><Link className="caseLink" href={`/cases/${item.case_id}`}>{item.case_id.slice(0, 8)}</Link></td><td>{item.action_type.replaceAll("_", " ")}</td><td className="amount">{formatMoney(item.amount_minor, item.currency)}</td><td>{item.requires_approval ? "Approval required" : "Human escalation"}</td><td>{formatDateTime(item.created_at)}</td><td>{item.requires_approval ? <ReviewDecision actionId={item.action_id} /> : <Link className="caseLink" href={`/cases/${item.case_id}`}>Open case</Link>}</td></tr>)}</tbody></table></div> : <div className="empty">No cases require human attention. Approved deliveries may take a few seconds to appear in the case timeline.</div>}
      </section>
    </div>
  );
}
