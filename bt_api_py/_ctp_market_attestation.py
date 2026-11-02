"""SDK-owned parent attestation for direct CTP market ingress.

The provider consumes this evidence but cannot issue its SDK seal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


_CTP_QUOTE_V2_UNTRUSTED_IDENTITIES = frozenset(
    {
        "",
        "-",
        "--",
        "unknown",
        "unverified",
        "unset",
        "none",
        "null",
        "n/a",
        "na",
        "unavailable",
        "undefined",
        "missing",
        "pending",
        "tbd",
        "default",
        "placeholder",
    }
)
_CTP_QUOTE_V2_PARENT_ATTESTATION_SEAL = object()


def _ctp_quote_v2_trusted_text(value) -> str:
    """Return a provenance identity only when it is explicit and meaningful."""

    text = str(value or "").strip()
    return "" if text.casefold() in _CTP_QUOTE_V2_UNTRUSTED_IDENTITIES else text


@dataclass(frozen=True)
class _CtpQuoteV2ParentAttestation:
    """Opaque evidence issued only by the parent direct-CTP ingress path."""

    _seal: object
    exchange_name: str
    symbol: str
    transport: str
    registered_subscription: bool
    session_ready: bool
    source: str
    rules_hash: str
    clock_domain_id: str
    connection_generation: int
    subscription_epoch: int
    session_generation: int
    cohort_now_monotonic_ns: int
    cohort_now_epoch: float
    cohort_now_receive_clock_error_ms: float
    cohort_now_receive_clock_quality: str
    cohort_now_freshness_verified: bool


def _issue_ctp_quote_v2_parent_attestation(
    *,
    exchange_name,
    symbol,
    source,
    rules_hash,
    clock_domain_id,
    connection_generation,
    subscription_epoch,
    session_generation,
    cohort_now_monotonic_ns,
    cohort_now_epoch,
    cohort_now_receive_clock_error_ms,
    cohort_now_receive_clock_quality,
    cohort_now_freshness_verified,
):
    """Create a sealed V2 attestation after parent-owned registration checks.

    This is intentionally private.  A transport payload never supplies this
    object: ``BtApi`` issues it only after it has matched the native container
    to a registered direct CTP market stream and an active read-only session.
    """

    exchange = str(exchange_name or "").strip()
    instrument = str(symbol or "").strip()
    trusted_source = _ctp_quote_v2_trusted_text(source)
    trusted_rules = _ctp_quote_v2_trusted_text(rules_hash)
    trusted_clock_domain = _ctp_quote_v2_trusted_text(clock_domain_id)
    try:
        md_generation = int(connection_generation)
        epoch = int(subscription_epoch)
        session_epoch = int(session_generation)
        now_monotonic = int(cohort_now_monotonic_ns)
        now_epoch = float(cohort_now_epoch)
        now_error = float(cohort_now_receive_clock_error_ms)
    except (TypeError, ValueError):
        return None
    if (
        isinstance(connection_generation, bool)
        or isinstance(subscription_epoch, bool)
        or isinstance(session_generation, bool)
        or isinstance(cohort_now_monotonic_ns, bool)
        or isinstance(cohort_now_epoch, bool)
        or isinstance(cohort_now_receive_clock_error_ms, bool)
        or not exchange
        or not instrument
        or not trusted_source
        or not trusted_rules
        or not trusted_clock_domain
        or md_generation <= 0
        or epoch <= 0
        or session_epoch <= 0
        or now_monotonic <= 0
        or not math.isfinite(now_epoch)
        or not math.isfinite(now_error)
        or now_error < 0
        or str(cohort_now_receive_clock_quality or "").strip().lower() != "verified"
        or cohort_now_freshness_verified is not True
    ):
        return None
    return _CtpQuoteV2ParentAttestation(
        _CTP_QUOTE_V2_PARENT_ATTESTATION_SEAL,
        exchange,
        instrument,
        "direct",
        True,
        True,
        trusted_source,
        trusted_rules,
        trusted_clock_domain,
        md_generation,
        epoch,
        session_epoch,
        now_monotonic,
        now_epoch,
        now_error,
        "verified",
        True,
    )


def _ctp_quote_v2_parent_attestation_matches(
    attestation,
    *,
    exchange_name,
    symbol,
    source,
    rules_hash,
    clock_domain_id,
    connection_generation,
    subscription_epoch,
) -> bool:
    """Reject V2 eligibility unless the parent-issued evidence matches exactly."""

    if (
        type(attestation) is not _CtpQuoteV2ParentAttestation
        or attestation._seal is not _CTP_QUOTE_V2_PARENT_ATTESTATION_SEAL
        or attestation.transport != "direct"
        or attestation.registered_subscription is not True
        or attestation.session_ready is not True
        or attestation.session_generation <= 0
        or attestation.cohort_now_monotonic_ns <= 0
        or not math.isfinite(attestation.cohort_now_epoch)
        or not math.isfinite(attestation.cohort_now_receive_clock_error_ms)
        or attestation.cohort_now_receive_clock_error_ms < 0
        or attestation.cohort_now_receive_clock_quality != "verified"
        or attestation.cohort_now_freshness_verified is not True
    ):
        return False
    try:
        md_generation = int(connection_generation)
        epoch = int(subscription_epoch)
    except (TypeError, ValueError):
        return False
    if isinstance(connection_generation, bool) or isinstance(subscription_epoch, bool):
        return False
    return (
        str(exchange_name or "").strip() == attestation.exchange_name
        and str(symbol or "").strip() == attestation.symbol
        and _ctp_quote_v2_trusted_text(source) == attestation.source
        and _ctp_quote_v2_trusted_text(rules_hash) == attestation.rules_hash
        and _ctp_quote_v2_trusted_text(clock_domain_id) == attestation.clock_domain_id
        and md_generation > 0
        and md_generation == attestation.connection_generation
        and epoch > 0
        and epoch == attestation.subscription_epoch
    )








