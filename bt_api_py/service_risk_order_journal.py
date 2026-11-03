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

    # -- strategy-ledger projection reads (iteration 60 R3) ---------------
    # Read-only; the host strategy ledger follows this journal exactly like it
    # follows the Execution journal outbox (``service_strategy_ledger_host``).

    def strategy_ledger_journal_identity(self, scope):
        """The record hash of the scope's first event (``None`` before any event).

        Changes when the journal file is replaced, so a ledger cursor cannot
        silently apply to another journal generation.
        """

        from contextlib import closing

        with closing(self._connection()) as connection:
            row = connection.execute(
                "SELECT record_sha256 FROM risk_order_events WHERE scope_key=? AND revision=1",
                (scope.key,),
            ).fetchone()
        return None if row is None else str(row["record_sha256"])

    def strategy_ledger_events_after(self, scope, after_revision: int, limit: int):
        """Events of ``scope`` with revision greater than ``after_revision``, in order."""

        from contextlib import closing

        if type(after_revision) is not int or after_revision < 0:
            raise RiskOrderJournalError("INVALID_EVENT_REVISION")
        if type(limit) is not int or not 0 < limit <= 10_000:
            raise RiskOrderJournalError("INVALID_EVENT_LIMIT")
        with closing(self._connection()) as connection:
            rows = connection.execute(
                "SELECT * FROM risk_order_events WHERE scope_key=? AND revision>?"
                " ORDER BY revision LIMIT ?",
                (scope.key, after_revision, limit),
            ).fetchall()
            return tuple(self._event_from_row(row, scope) for row in rows)

    def has_intent_id(self, scope, intent_id: str) -> bool:
        """Whether any principal's command of ``scope`` uses ``intent_id``."""

        from contextlib import closing

        with closing(self._connection()) as connection:
            row = connection.execute(
                "SELECT 1 FROM risk_order_commands WHERE scope_key=? AND intent_id=? LIMIT 1",
                (scope.key, intent_id),
            ).fetchone()
        return row is not None

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
