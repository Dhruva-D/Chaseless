from chaseless.domain.types import Diagnosis, RecoveryContext

INSTRUMENT_CODES = {
    "BAD_REQUEST_ERROR",
    "CARD_EXPIRED",
    "CARD_DECLINED",
    "MANDATE_INVALID",
    "TOKEN_EXPIRED",
}
LIQUIDITY_CODES = {"INSUFFICIENT_FUNDS", "LOW_BALANCE", "BANK_ACCOUNT_INSUFFICIENT"}
NON_RECOVERABLE_CODES = {"ACCOUNT_CLOSED", "CUSTOMER_DECEASED", "FRAUD_BLOCK"}


def diagnose(context: RecoveryContext) -> Diagnosis:
    code = (context.failure_code or "UNKNOWN").upper()
    evidence = [f"provider_failure_code={code}"]

    if code in NON_RECOVERABLE_CODES:
        return Diagnosis(
            failure_class="NON_RECOVERABLE",
            confidence=0.98,
            natural_recovery_score=0.01,
            evidence=evidence,
        )

    if code in INSTRUMENT_CODES:
        return Diagnosis(
            failure_class="INSTRUMENT_ISSUE",
            confidence=0.9,
            natural_recovery_score=0.08,
            evidence=evidence + ["instrument remediation is required"],
        )

    if code in LIQUIDITY_CODES:
        natural = 0.45
        if context.median_recovery_hours is not None and context.median_recovery_hours <= 48:
            natural += 0.2
            evidence.append("historically recovered within 48h")
        return Diagnosis(
            failure_class="TEMPORARY_LIQUIDITY",
            confidence=0.86,
            natural_recovery_score=min(natural, 0.85),
            evidence=evidence,
        )

    natural = 0.18
    if context.successful_payments >= 6:
        natural += 0.18
        evidence.append("strong successful-payment history")
    if context.prior_failures == 0:
        natural += 0.12
        evidence.append("first observed failure")
    if context.subscription_status.lower() == "pending":
        natural += 0.12
        evidence.append("Razorpay native retries may still run")

    return Diagnosis(
        failure_class="UNKNOWN",
        confidence=0.55,
        natural_recovery_score=min(natural, 0.8),
        evidence=evidence + ["ambiguous failure; bounded recommendation only"],
    )
