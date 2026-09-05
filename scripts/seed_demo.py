from __future__ import annotations

from datetime import time

from chaseless.db.models import (
    Approval,
    AuditEvent,
    CandidateAction,
    ConversationEvent,
    Customer,
    Merchant,
    MerchantPolicyVersion,
    OutboxEvent,
    PaymentEvent,
    RecoveryAction,
    RecoveryCase,
    RecoveryRun,
    Subscription,
    WebhookEvent,
)
from chaseless.db.session import session_scope
from chaseless.domain.diagnosis import diagnose
from chaseless.domain.enums import ActionType, CaseState
from chaseless.domain.policy import PolicyConfig
from chaseless.domain.types import RecoveryContext

SCENARIOS = [
    ("Aarav Mehta", "CARD_EXPIRED", "halted", 249_900, 14, 1, 0, False),
    ("Kavya Rao", "INSUFFICIENT_FUNDS", "pending", 99_900, 5, 2, 0, False),
    ("Northstar Labs", "CARD_EXPIRED", "halted", 799_900, 10, 3, 1, False),
    ("Paperplane Studio", "BAD_REQUEST_ERROR", "halted", 949_900, 12, 2, 0, False),
]

CASE_META = [
    (
        "SaaS subscription",
        "Failed subscription",
        "UPDATE_PAYMENT_METHOD",
        "Send a secure email payment-method update",
        "HIGH",
    ),
    (
        "Membership",
        "Failed subscription",
        "NUDGE",
        "Send WhatsApp first, then SMS if delivery is unavailable",
        "MEDIUM",
    ),
    (
        "B2B account",
        "Overdue B2B invoice",
        "VOICE_AGENT",
        "Call the finance owner and capture a payment promise",
        "CRITICAL",
    ),
    (
        "B2B account",
        "Overdue B2B invoice",
        "PAYMENT_LINK",
        "Create and send a Razorpay Payment Link after confirmation",
        "CRITICAL",
    ),
]


def main(*, reset: bool = False) -> None:
    with session_scope() as db:
        merchant = db.query(Merchant).filter(Merchant.name == "ChaseLess Demo").one_or_none()
        # This merchant is a controlled test portfolio: consented test endpoints may execute
        # automatically, while contact caps, quiet hours, opt-outs, and economics still apply.
        policy = PolicyConfig(
            max_contacts_24h=3,
            max_contacts_7d=6,
            # This merchant exists only for the controlled buildathon test portfolio. Keep
            # outbound actions available during an evening demo; every delivery is still
            # restricted to the configured test recipient and the normal consent/contact caps.
            quiet_hours_start=time(0, 0),
            quiet_hours_end=time(0, 0),
            require_approval=set(),
            auto_allowed=set(ActionType),
        )
        if merchant is None:
            merchant = Merchant(name="ChaseLess Demo", timezone="Asia/Kolkata")
            db.add(merchant)
            db.flush()
            db.add(
                MerchantPolicyVersion(
                    merchant_id=merchant.id,
                    version=policy.version,
                    rules_json=policy.model_dump(mode="json"),
                    created_by="seed-demo",
                )
            )
        else:
            policy_row = (
                db.query(MerchantPolicyVersion)
                .filter(MerchantPolicyVersion.merchant_id == merchant.id)
                .order_by(MerchantPolicyVersion.version.desc())
                .first()
            )
            if policy_row is None:
                db.add(
                    MerchantPolicyVersion(
                        merchant_id=merchant.id,
                        version=policy.version,
                        rules_json=policy.model_dump(mode="json"),
                        created_by="seed-demo",
                    )
                )
            else:
                policy_row.rules_json = policy.model_dump(mode="json")
        if reset:
            # Remove only demo-merchant operational data; preserve the merchant and policy.
            run_ids = [
                r.id
                for r in db.query(RecoveryRun.id).filter(RecoveryRun.merchant_id == merchant.id)
            ]
            case_ids = [
                r.id
                for r in db.query(RecoveryCase.id).filter(RecoveryCase.merchant_id == merchant.id)
            ]
            action_ids = (
                [
                    r.id
                    for r in db.query(RecoveryAction.id).filter(RecoveryAction.run_id.in_(run_ids))
                ]
                if run_ids
                else []
            )
            if action_ids:
                db.query(Approval).filter(Approval.action_id.in_(action_ids)).delete(
                    synchronize_session=False
                )
            if case_ids:
                db.query(ConversationEvent).filter(ConversationEvent.case_id.in_(case_ids)).delete(
                    synchronize_session=False
                )
            db.query(RecoveryAction).filter(RecoveryAction.run_id.in_(run_ids)).delete(
                synchronize_session=False
            )
            if run_ids:
                (
                    db.query(CandidateAction)
                    .filter(CandidateAction.run_id.in_(run_ids))
                    .delete(synchronize_session=False)
                )
            db.query(RecoveryRun).filter(RecoveryRun.merchant_id == merchant.id).delete(
                synchronize_session=False
            )
            for model in (
                AuditEvent,
                PaymentEvent,
                WebhookEvent,
                RecoveryCase,
                Subscription,
                Customer,
            ):
                db.query(model).filter(model.merchant_id == merchant.id).delete(
                    synchronize_session=False
                )
            # Outbox rows have no merchant foreign key; clear pending demo work before reseeding.
            db.query(OutboxEvent).delete(synchronize_session=False)
        for index, scenario in enumerate(SCENARIOS):
            name, failure, status, amount, successes, failures, contacts, opted_out = scenario
            segment, source_type, action, next_step, priority = CASE_META[index]
            provider_id = f"cust_demo_{index}"
            customer = (
                db.query(Customer)
                .filter(
                    Customer.merchant_id == merchant.id,
                    Customer.provider_customer_id == provider_id,
                )
                .one_or_none()
            )
            if customer:
                continue
            customer = Customer(
                merchant_id=merchant.id,
                provider_customer_id=provider_id,
                display_name=name,
                email_masked=f"{name.split()[0][:3].lower()}***@example.com",
                phone_masked="+91 ******0000",
                opted_out=opted_out,
                contacts_7d=contacts,
                contact_preferences={
                    "segment": segment,
                    "source_type": source_type,
                    "recommended_action": action,
                    "next_step": next_step,
                    "priority": priority,
                    "tenure_months": max(3, successes + failures),
                    "lifetime_value_minor": amount * max(3, successes),
                    "payment_history": [
                        {
                            "period": f"M-{month}",
                            "amount_minor": amount,
                            "status": "failed" if month <= min(failures, 3) else "paid",
                        }
                        for month in range(8, 0, -1)
                    ],
                },
            )
            db.add(customer)
            db.flush()
            subscription = Subscription(
                merchant_id=merchant.id,
                customer_id=customer.id,
                razorpay_subscription_id=f"sub_demo_{index}",
                razorpay_plan_id="plan_demo_monthly",
                status=status,
                amount_minor=amount,
                currency="INR",
            )
            db.add(subscription)
            db.flush()
            case = RecoveryCase(
                merchant_id=merchant.id,
                customer_id=customer.id,
                subscription_id=subscription.id,
                episode_key=f"sub_demo_{index}:cycle:demo",
                state=CaseState.DIAGNOSED.value,
                risk_amount_minor=amount,
                currency="INR",
                contact_count=contacts,
            )
            db.add(case)
            db.flush()
            context = RecoveryContext(
                case_id=str(case.id),
                amount_minor=amount,
                subscription_status=status,
                failure_code=failure,
                prior_failures=failures,
                successful_payments=successes,
                contacts_7d=contacts,
                opted_out=opted_out,
            )
            result = diagnose(context)
            case.diagnosis = result.model_dump(mode="json")
            case.natural_recovery_score = result.natural_recovery_score
    print("Seeded ChaseLess demo merchant and recovery cases.")


if __name__ == "__main__":
    main()
