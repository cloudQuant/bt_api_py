"""SDK composition for the Risk-owned durable direct-order journal."""
from __future__ import annotations

from bt_api_risk.risk_order_journal import (
    RiskOrderJournal as _RiskOrderJournal,
    _decimal_text_matches as _decimal_text_matches,
    _fence_digest as _fence_digest,
    _is_v2_fill_event as _is_v2_fill_event,
    _is_v2_order_event as _is_v2_order_event,
    _post_ack_unknown_payload_valid as _post_ack_unknown_payload_valid,
    _provider_dispatch_binding_claim_valid,
    _provider_zero_fill_dispatch_history_valid,
    _reconciliation_evidence_sha256 as _reconciliation_evidence_sha256,
    _terminal_close_observation_history as _terminal_close_observation_history,
    _terminal_close_source_fences as _terminal_close_source_fences,
    _terminal_exposure_lifecycle as _terminal_exposure_lifecycle,
    _terminal_exposure_source_projection as _terminal_exposure_source_projection,
    _valid_exposure_applied_payload as _valid_exposure_applied_payload,
    _valuation_facts_sha256 as _valuation_facts_sha256,
    canonical_json,
    sha256_text,
)
from .service_execution_risk_journal_bridge import RiskExecutionJournalBridge

from bt_api_risk.risk_order_journal_contracts import (
    CancelResolutionTargetBindingV1 as CancelResolutionTargetBindingV1,
    DispatchProofRecoverySource,
    RiskOrderCommand,
    RiskOrderJournalError,
    RiskOrderJournalEvent,
    RiskOrderWriterFence,
)


def _risk_dispatch_profile(provider: str):
    from .provider_entrypoints import ProviderEntryPointError
    from .risk_dispatch_evidence import risk_dispatch_evidence_profile

    try:
        return risk_dispatch_evidence_profile(provider)
    except ProviderEntryPointError:
        raise RiskOrderJournalError("RISK_DISPATCH_EVIDENCE_PROVIDER_UNAVAILABLE") from None


class RiskOrderJournal(RiskExecutionJournalBridge, _RiskOrderJournal):
    """Bind SDK-selected provider evidence and Gateway source verification."""

    _risk_dispatch_profile = staticmethod(_risk_dispatch_profile)

    @staticmethod
    def _verify_gateway_position_source_receipt(receipt, **kwargs):
        from .gateway_risk_position_source import verify_gateway_position_source_receipt

        return verify_gateway_position_source_receipt(
            receipt, **kwargs, journal_error_type=RiskOrderJournalError,
            sha256_text=sha256_text, canonical_json=canonical_json,
        )


def _ctp_dispatch_binding_claim_valid(value, *, claim, risk_scope_key):
    return _provider_dispatch_binding_claim_valid(
        value, claim=claim, risk_scope_key=risk_scope_key, profile_for=_risk_dispatch_profile,
    )


def _ctp_zero_fill_dispatch_history_valid(value, **kwargs):
    return _provider_zero_fill_dispatch_history_valid(
        value, **kwargs, profile_for=_risk_dispatch_profile,
    )


__all__ = (
    "DispatchProofRecoverySource", "RiskOrderCommand", "RiskOrderJournal",
    "RiskOrderJournalError", "RiskOrderJournalEvent", "RiskOrderWriterFence",
    "canonical_json", "sha256_text",
)
