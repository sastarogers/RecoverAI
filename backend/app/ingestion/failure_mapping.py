"""Map low-level gateway failure codes into RecoverAI's high-level categories (§14).

Both the Razorpay normalizer and the simulator use this table, so a `BANK_TIMEOUT`
means exactly the same thing to the policy engine regardless of where it came from.
"""

from __future__ import annotations

from app.domain.enums import FailureCategory as FC

#: raw code (upper-cased) -> category
FAILURE_CODE_MAP: dict[str, FC] = {
    # --- temporary / transient ---
    "BANK_TIMEOUT": FC.TEMPORARY,
    "ISSUER_UNAVAILABLE": FC.TEMPORARY,
    "BANK_SERVER_DOWN": FC.TEMPORARY,
    "SERVICE_UNAVAILABLE": FC.TEMPORARY,
    "PAYMENT_PENDING": FC.TEMPORARY,
    # --- network ---
    "NETWORK_ERROR": FC.NETWORK_ERROR,
    "CONNECTION_RESET": FC.NETWORK_ERROR,
    "GATEWAY_ERROR": FC.NETWORK_ERROR,
    # --- timeouts ---
    "NETWORK_TIMEOUT": FC.TIMEOUT,
    "GATEWAY_TIMEOUT": FC.TIMEOUT,
    "UPI_COLLECT_EXPIRED": FC.TIMEOUT,
    "PAYMENT_TIMEOUT": FC.TIMEOUT,
    # --- bank declines ---
    "BANK_DECLINE": FC.BANK_DECLINE,
    "DO_NOT_HONOUR": FC.BANK_DECLINE,
    "TRANSACTION_NOT_PERMITTED": FC.BANK_DECLINE,
    "PAYMENT_DECLINED_BY_BANK": FC.BANK_DECLINE,
    # --- funds ---
    "INSUFFICIENT_FUNDS": FC.INSUFFICIENT_FUNDS,
    "EXCEEDS_WITHDRAWAL_LIMIT": FC.INSUFFICIENT_FUNDS,
    "LIMIT_EXCEEDED": FC.INSUFFICIENT_FUNDS,
    # --- bad details (never blind-retry) ---
    "INVALID_CARD": FC.INVALID_PAYMENT_DETAILS,
    "INVALID_CARD_NUMBER": FC.INVALID_PAYMENT_DETAILS,
    "INCORRECT_CVV": FC.INVALID_PAYMENT_DETAILS,
    "INVALID_VPA": FC.INVALID_PAYMENT_DETAILS,
    "INVALID_ACCOUNT": FC.INVALID_PAYMENT_DETAILS,
    # --- expired ---
    "CARD_EXPIRED": FC.EXPIRED_CARD,
    "EXPIRED_CARD": FC.EXPIRED_CARD,
    # --- customer must act ---
    "AUTHENTICATION_FAILED": FC.CUSTOMER_ACTION_REQUIRED,
    "OTP_INCORRECT": FC.CUSTOMER_ACTION_REQUIRED,
    "THREE_DS_FAILED": FC.CUSTOMER_ACTION_REQUIRED,
    "MANDATE_REVOKED": FC.CUSTOMER_ACTION_REQUIRED,
    "PAYMENT_METHOD_EXPIRED": FC.CUSTOMER_ACTION_REQUIRED,
    "USER_CANCELLED": FC.CUSTOMER_ACTION_REQUIRED,
    # --- permanent ---
    "CARD_BLOCKED": FC.PERMANENT,
    "ACCOUNT_CLOSED": FC.PERMANENT,
    "ACCOUNT_BLOCKED": FC.PERMANENT,
    "FRAUD_SUSPECTED": FC.PERMANENT,
    "CARD_STOLEN": FC.PERMANENT,
    "PAYMENT_METHOD_NOT_SUPPORTED": FC.PERMANENT,
    # --- checkout (no gateway failure exists) ---
    "CART_ABANDONED": FC.ABANDONED,
}

#: Razorpay `error_reason` / `error_code` fragments seen in Test Mode payloads.
RAZORPAY_REASON_HINTS: dict[str, FC] = {
    "payment_failed": FC.UNKNOWN,
    "insufficient_funds": FC.INSUFFICIENT_FUNDS,
    "card_expired": FC.EXPIRED_CARD,
    "incorrect_cvv": FC.INVALID_PAYMENT_DETAILS,
    "invalid_vpa": FC.INVALID_PAYMENT_DETAILS,
    "payment_timeout": FC.TIMEOUT,
    "network_error": FC.NETWORK_ERROR,
    "gateway_error": FC.NETWORK_ERROR,
    "bank_transfer_failed": FC.BANK_DECLINE,
    "payment_cancelled": FC.CUSTOMER_ACTION_REQUIRED,
    "authentication_failed": FC.CUSTOMER_ACTION_REQUIRED,
    "issuer_down": FC.TEMPORARY,
    "server_error": FC.TEMPORARY,
}


def categorize(raw_code: str | None, *, reason: str | None = None) -> FailureCategoryResult:
    """Resolve a raw failure code to a category.

    Unmapped codes deliberately land on UNKNOWN rather than a guess — the policy
    engine treats UNKNOWN conservatively instead of blind-retrying it.
    """
    if raw_code:
        hit = FAILURE_CODE_MAP.get(raw_code.strip().upper())
        if hit is not None:
            return FailureCategoryResult(hit, raw_code.strip().upper(), mapped=True)
    if reason:
        hit = RAZORPAY_REASON_HINTS.get(reason.strip().lower())
        if hit is not None and hit is not FC.UNKNOWN:
            return FailureCategoryResult(hit, (raw_code or reason).strip().upper(), mapped=True)
    return FailureCategoryResult(
        FC.UNKNOWN, (raw_code or reason or "UNKNOWN").strip().upper(), mapped=False
    )


class FailureCategoryResult:
    __slots__ = ("category", "code", "mapped")

    def __init__(self, category: FC, code: str, mapped: bool) -> None:
        self.category = category
        self.code = code
        self.mapped = mapped

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FailureCategory {self.code}->{self.category} mapped={self.mapped}>"


#: Which raw codes are plausible for a given payment method — used by the simulator so
#: it never emits a CARD_EXPIRED on a UPI payment.
METHOD_FAILURE_CODES: dict[str, dict[FC, tuple[str, ...]]] = {
    "upi": {
        FC.TEMPORARY: ("BANK_TIMEOUT", "ISSUER_UNAVAILABLE", "BANK_SERVER_DOWN"),
        FC.NETWORK_ERROR: ("NETWORK_ERROR", "GATEWAY_ERROR"),
        FC.TIMEOUT: ("UPI_COLLECT_EXPIRED", "NETWORK_TIMEOUT"),
        FC.BANK_DECLINE: ("BANK_DECLINE", "TRANSACTION_NOT_PERMITTED"),
        FC.INSUFFICIENT_FUNDS: ("INSUFFICIENT_FUNDS", "EXCEEDS_WITHDRAWAL_LIMIT"),
        FC.INVALID_PAYMENT_DETAILS: ("INVALID_VPA",),
        FC.CUSTOMER_ACTION_REQUIRED: ("USER_CANCELLED", "AUTHENTICATION_FAILED"),
        FC.PERMANENT: ("ACCOUNT_BLOCKED", "FRAUD_SUSPECTED"),
    },
    "card": {
        FC.TEMPORARY: ("BANK_TIMEOUT", "ISSUER_UNAVAILABLE"),
        FC.NETWORK_ERROR: ("NETWORK_ERROR", "GATEWAY_ERROR"),
        FC.TIMEOUT: ("GATEWAY_TIMEOUT", "NETWORK_TIMEOUT"),
        FC.BANK_DECLINE: ("DO_NOT_HONOUR", "BANK_DECLINE"),
        FC.INSUFFICIENT_FUNDS: ("INSUFFICIENT_FUNDS", "LIMIT_EXCEEDED"),
        FC.INVALID_PAYMENT_DETAILS: ("INCORRECT_CVV", "INVALID_CARD_NUMBER"),
        FC.EXPIRED_CARD: ("CARD_EXPIRED",),
        FC.CUSTOMER_ACTION_REQUIRED: ("THREE_DS_FAILED", "OTP_INCORRECT"),
        FC.PERMANENT: ("CARD_BLOCKED", "CARD_STOLEN", "FRAUD_SUSPECTED"),
    },
    "netbanking": {
        FC.TEMPORARY: ("BANK_SERVER_DOWN", "ISSUER_UNAVAILABLE"),
        FC.NETWORK_ERROR: ("NETWORK_ERROR",),
        FC.TIMEOUT: ("GATEWAY_TIMEOUT",),
        FC.BANK_DECLINE: ("BANK_DECLINE",),
        FC.INSUFFICIENT_FUNDS: ("INSUFFICIENT_FUNDS",),
        FC.INVALID_PAYMENT_DETAILS: ("INVALID_ACCOUNT",),
        FC.CUSTOMER_ACTION_REQUIRED: ("USER_CANCELLED",),
        FC.PERMANENT: ("ACCOUNT_CLOSED",),
    },
    "wallet": {
        FC.TEMPORARY: ("SERVICE_UNAVAILABLE",),
        FC.NETWORK_ERROR: ("NETWORK_ERROR",),
        FC.TIMEOUT: ("PAYMENT_TIMEOUT",),
        FC.BANK_DECLINE: ("TRANSACTION_NOT_PERMITTED",),
        FC.INSUFFICIENT_FUNDS: ("INSUFFICIENT_FUNDS",),
        FC.CUSTOMER_ACTION_REQUIRED: ("USER_CANCELLED",),
        FC.PERMANENT: ("ACCOUNT_BLOCKED",),
    },
    "emi": {
        FC.TEMPORARY: ("BANK_TIMEOUT",),
        FC.NETWORK_ERROR: ("GATEWAY_ERROR",),
        FC.TIMEOUT: ("GATEWAY_TIMEOUT",),
        FC.BANK_DECLINE: ("DO_NOT_HONOUR",),
        FC.INSUFFICIENT_FUNDS: ("LIMIT_EXCEEDED",),
        FC.INVALID_PAYMENT_DETAILS: ("INCORRECT_CVV",),
        FC.EXPIRED_CARD: ("CARD_EXPIRED",),
        FC.CUSTOMER_ACTION_REQUIRED: ("THREE_DS_FAILED",),
        FC.PERMANENT: ("CARD_BLOCKED",),
    },
}


def code_for(method: str, category: FC, index: int = 0) -> str:
    """Pick a raw code consistent with the payment method and category."""
    table = METHOD_FAILURE_CODES.get(method, METHOD_FAILURE_CODES["card"])
    codes = table.get(category)
    if not codes:
        for fallback in (FC.TEMPORARY, FC.NETWORK_ERROR, FC.BANK_DECLINE):
            codes = table.get(fallback)
            if codes:
                break
    if not codes:
        return "UNKNOWN"
    return codes[index % len(codes)]


def supported_categories(method: str) -> tuple[FC, ...]:
    return tuple(METHOD_FAILURE_CODES.get(method, METHOD_FAILURE_CODES["card"]).keys())
