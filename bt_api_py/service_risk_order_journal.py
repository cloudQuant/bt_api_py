"""SDK composition for the Risk-owned durable direct-order journal.

The facade owns exactly one exact ``bt_api_risk.risk_order_journal.
RiskOrderJournal`` core and binds the three supported capability ports to it
(provider dispatch profile, Gateway position source, Execution cancel source).
It does not subclass the core: the core's writer fence and composition slot
trust only the exact core type, and a subclass can never obtain that trust.
Ports are instance-bound once; the core rejects missing, replaced, changed or
cross-core ports, and every use still runs the core's own fence, scope and
evidence checks. This is not a hostile-code sandbox.
"""
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
from bt_api_risk.risk_order_journal_ports import (
    RiskCancelResolutionSourcePort,
    RiskDispatchProfileSourcePort,
    RiskGatewayPositionSourcePort,
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


class _DispatchProfilePort(RiskDispatchProfileSourcePort):
    """Provider dispatch-evidence declaration for this exact core."""

    def profile_for(self, provider: str):
        return _risk_dispatch_profile(provider)


class _GatewayPositionSourcePort(RiskGatewayPositionSourcePort):
    """Gateway full-position source revalidation for this exact core."""

    def verify_gateway_position_source_receipt(self, receipt, **kwargs):
        return _verify_gateway_position_source_receipt(receipt, **kwargs)


class _CancelResolutionSourcePort(
    RiskExecutionJournalBridge, RiskCancelResolutionSourcePort
):
    """Execution cancel-source bridge adapted to one exact core.

    The evidence algorithms stay in ``RiskExecutionJournalBridge``; the core
    supplies the connection, row decoding and event-chain helpers.
    """

    def __init__(self, journal: object) -> None:
        RiskCancelResolutionSourcePort.__init__(self, journal)
        RiskExecutionJournalBridge._init_cancel_resolution_sources(self)

    def _connection(self, **kwargs):
        return self.journal._connection(**kwargs)

    @staticmethod
    def _assert_current_in(connection, fence):
        return _RiskOrderJournal._assert_current_in(connection, fence)

    def _command_from_row(self, connection, row, scope):
        return self.journal._command_from_row(connection, row, scope)

    def _event_from_row(self, row, scope):
        return self.journal._event_from_row(row, scope)

    @staticmethod
    def _verify_event_chains(connection, row, scope, revision, *, target_revision):
        return _RiskOrderJournal._verify_event_chains(
            connection, row, scope, revision, target_revision=target_revision,
        )

    def _risk_dispatch_profile(self, provider):
        return self.journal._risk_dispatch_profile(provider)


class RiskOrderJournal:
    """SDK facade over exactly one exact Risk core and its bound ports."""

    def __init__(self, path, *, writer_id: str, _defer_initialization: bool = False) -> None:
        core = _RiskOrderJournal(
            path, writer_id=writer_id, _defer_initialization=_defer_initialization,
        )
        core.bind_risk_journal_ports(
            dispatch_profile=_DispatchProfilePort(core),
            gateway_position_source=_GatewayPositionSourcePort(core),
            cancel_resolution_source=_CancelResolutionSourcePort(core),
        )
        self._core = core

    @property
    def core(self) -> _RiskOrderJournal:
        """Return the exact core that owns the ledger, fence and ports."""

        return self._core

    def writer_fence(self, *args, **kwargs):
        """Delegate to the exact core so the fence gate sees the core type."""

        return self._core.writer_fence(*args, **kwargs)

    @classmethod
    def open_fenced(cls, *args, **kwargs):
        """Open the exact core; callers receive that core, not this facade."""

        return _RiskOrderJournal.open_fenced(*args, **kwargs)

    def __getattr__(self, name):
        # __getattr__ runs only after normal lookup; _core is in __dict__.
        try:
            core = object.__getattribute__(self, "_core")
        except AttributeError:
            raise AttributeError(name) from None
        return getattr(core, name)


__all__ = (
    "DispatchProofRecoverySource", "RiskOrderCommand", "RiskOrderJournal",
    "RiskOrderJournalError", "RiskOrderJournalEvent", "RiskOrderWriterFence",
    "canonical_json", "sha256_text",
)
