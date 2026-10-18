"""Owner-bound CTP callback reconciliation views for the service composition.

This module consumes only callback ingress already persisted by the CTP
session owner.  It does not submit orders, resolve UNKNOWN commands, or turn a
local queue/native return code into provider state.  The read view intentionally
does not claim to be a complete execution observation: the public Store API
does not expose a durable per-trade ledger readback or an average fill price.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from bt_api_execution import (
    ContractValidationError,
    CtpCallbackIngressAuditResultV1,
    CtpCallbackSessionBindingV1,
    CtpCallbackSessionOwnerHandle,
    CtpCallbackSessionStoreAdapter,
    CtpDispatchCallbackApplyResult,
    CtpDispatchCorrelationKey,
    CtpDispatchProjection,
    CtpDispatchTradeFactApplyResult,
    CtpOrderIdentityReservation,
    CtpProjectedOrderState,
    ExecutionScope,
    InvalidStateTransition,
    OrderIntent,
    OrderType,
    PositionEffect,
    Side,
    SqliteExecutionStore,
    WriterLease,
)


_OFFSET_FLAGS = {
    (PositionEffect.OPEN, "ALL"): "0",
    (PositionEffect.CLOSE, "ALL"): "1",
    (PositionEffect.CLOSE, "TODAY"): "3",
    (PositionEffect.CLOSE, "YESTERDAY"): "4",
    (PositionEffect.CLOSE_TODAY, "TODAY"): "3",
    (PositionEffect.CLOSE_YESTERDAY, "YESTERDAY"): "4",
}
_DIRECTION_FLAGS = {Side.BUY: "0", Side.SELL: "1"}


@dataclass(frozen=True, slots=True)
class CtpOwnerOrderProjectionView:
    """One Store-backed submit projection bound to its active CTP session.

    ``projection.local_dispatch_outcome`` describes the local dispatch
    receipt, while ``provider_order_state`` is populated only by a verified
    callback or reconciliation projection.  This value is an internal read
    model, not a ``ProviderObservation`` or a complete fill ledger.
    """

    intent_id: str
    instrument: str
    requested_quantity: Decimal
    reservation: CtpOrderIdentityReservation
    session_binding: CtpCallbackSessionBindingV1
    projection: CtpDispatchProjection

    def __post_init__(self) -> None:
        if type(self.intent_id) is not str or not self.intent_id:
            raise ContractValidationError("invalid CTP projected intent id")
        if type(self.instrument) is not str or not self.instrument:
            raise ContractValidationError("invalid CTP projected instrument")
        if (
            type(self.requested_quantity) is not Decimal
            or self.requested_quantity <= 0
            or self.requested_quantity != self.requested_quantity.to_integral_value()
        ):
            raise ContractValidationError("invalid projected CTP integer-lot quantity")
        if type(self.reservation) is not CtpOrderIdentityReservation:
            raise ContractValidationError("typed CTP order reservation is required")
        if type(self.session_binding) is not CtpCallbackSessionBindingV1:
            raise ContractValidationError("typed CTP session binding is required")
        if type(self.projection) is not CtpDispatchProjection:
            raise ContractValidationError("typed CTP dispatch projection is required")
        action = self.projection.submit_action
        if (
            self.projection.operation != "SUBMIT"
            or action is None
            or action.managed_intent_id != self.intent_id
            or action.runtime_order_id != self.reservation.runtime_order_id
            or action.order_ref != self.reservation.order_ref
        ):
            raise ContractValidationError("CTP projection differs from its reserved order")

    @property
    def command_status(self) -> str:
        """Return local outbox status; this is not provider acknowledgement."""

        return self.projection.command_status

    @property
    def local_dispatch_outcome(self) -> str | None:
        """Return the persisted local receipt outcome without promoting it."""

        return self.projection.local_dispatch_outcome

    @property
    def provider_order_state(self) -> CtpProjectedOrderState:
        """Return the exact typed Store projection for provider order state."""

        assert self.projection.submit_action is not None
        return self.projection.submit_action.order_state


class CtpOwnerCallbackReconciliationPort:
    """Apply one persisted callback under its exact live account owner.

    The port requires the exact SQLite Store, scope, writer lease, callback
    session adapter, owner handle, and session generation already held by the
    account owner.  It can project integer-lot LIMIT orders from the same Store's
    OrderRef reservation and outbox.  Provider authorization and native send
    remain the responsibility of the existing execution owner composition.
    """

    def __init__(
        self,
        store: SqliteExecutionStore,
        scope: ExecutionScope,
        writer_lease: WriterLease,
        session_adapter: CtpCallbackSessionStoreAdapter,
        *,
        session_generation_id: str,
        callback_verifier: Any,
        trade_fact_verifier: Any,
    ) -> None:
        if type(store) is not SqliteExecutionStore:
            raise ContractValidationError("exact execution Store is required")
        if type(scope) is not ExecutionScope or scope.provider != "ctp":
            raise ContractValidationError("canonical CTP execution scope is required")
        if scope.environment != "simulation" or not scope.trading_day:
            raise ContractValidationError("CTP simulation scope with trading day is required")
        if type(writer_lease) is not WriterLease:
            raise ContractValidationError("typed CTP writer lease is required")
        if type(session_adapter) is not CtpCallbackSessionStoreAdapter:
            raise ContractValidationError("exact CTP callback session adapter is required")
        if (
            session_adapter.store is not store
            or session_adapter.scope != scope
            or session_adapter.writer_lease is not writer_lease
            or type(session_adapter.owner_handle) is not CtpCallbackSessionOwnerHandle
        ):
            raise ContractValidationError("CTP callback adapter is not bound to this owner")
        if (
            type(session_generation_id) is not str
            or not session_generation_id
            or session_generation_id != session_generation_id.strip()
        ):
            raise ContractValidationError("exact CTP session generation is required")
        if not callable(getattr(callback_verifier, "verify_callback", None)):
            raise ContractValidationError("trusted CTP callback verifier is required")
        if not callable(getattr(trade_fact_verifier, "verify_trade_fact", None)):
            raise ContractValidationError("trusted CTP trade verifier is required")

        self._store = store
        self._scope = scope
        self._writer_lease = writer_lease
        self._session_adapter = session_adapter
        self._owner_handle = session_adapter.owner_handle
        self._session_generation_id = session_generation_id
        self._callback_verifier = callback_verifier
        self._trade_fact_verifier = trade_fact_verifier
        self._session_binding = self._read_current_binding()

    def _read_current_binding(self) -> CtpCallbackSessionBindingV1:
        self._store.assert_writer_lease(self._scope, self._writer_lease)
        facts = self._store.read_ctp_callback_session_context_facts(self._owner_handle)
        if type(facts) is not tuple or len(facts) != 3:
            raise InvalidStateTransition("CTP owner session facts are unavailable")
        binding = facts[0]
        if type(binding) is not CtpCallbackSessionBindingV1:
            raise InvalidStateTransition("typed active CTP session binding is unavailable")
        if (
            binding.owner_intent_id != self._owner_handle.owner_intent_id
            or binding.account_key != self._scope.account_key
            or binding.scope_key != self._scope.key
            or binding.trading_day != self._scope.trading_day
            or binding.session_generation_id != self._session_generation_id
        ):
            raise InvalidStateTransition("CTP session generation differs from this owner")
        return binding

    def _assert_current_owner(self) -> CtpCallbackSessionBindingV1:
        if (
            self._session_adapter.store is not self._store
            or self._session_adapter.scope != self._scope
            or self._session_adapter.writer_lease is not self._writer_lease
            or self._session_adapter.owner_handle is not self._owner_handle
        ):
            raise InvalidStateTransition("CTP callback owner binding changed")
        current = self._read_current_binding()
        if current != self._session_binding:
            raise InvalidStateTransition("CTP callback session binding changed")
        return current

    def apply_next_ingress(
        self,
    ) -> (
        CtpDispatchCallbackApplyResult
        | CtpDispatchTradeFactApplyResult
        | CtpCallbackIngressAuditResultV1
        | None
    ):
        """Apply the next already-persisted event through the trusted Store adapter.

        The exact result from the Store is returned.  In particular, a trade
        result has no synthesized price or fill average, and this method never
        resubmits an UNKNOWN command.
        """

        self._assert_current_owner()
        result = self._session_adapter.apply_next_ingress(
            callback_verifier=self._callback_verifier,
            trade_fact_verifier=self._trade_fact_verifier,
        )
        if result is None or type(result) in {
            CtpDispatchCallbackApplyResult,
            CtpDispatchTradeFactApplyResult,
            CtpCallbackIngressAuditResultV1,
        }:
            return result
        raise InvalidStateTransition("CTP Store adapter returned an unknown apply result")

    def read_order_projection(
        self, intent: OrderIntent, command_id: str
    ) -> CtpOwnerOrderProjectionView | None:
        """Read an integer-lot LIMIT command and its typed provider projection.

        Missing reservation/command returns ``None``.  An existing command is
        returned only when its intent, instrument, request, OrderRef, active
        session generation and session-binding digest all match this owner.
        The projection preserves UNKNOWN and keeps local dispatch separate from
        provider order state.
        """

        binding = self._assert_current_owner()
        if type(intent) is not OrderIntent or intent.scope != self._scope:
            raise ContractValidationError("exact same-scope CTP order intent is required")
        if (
            intent.order_type is not OrderType.LIMIT
            or intent.quantity <= 0
            or intent.quantity != intent.quantity.to_integral_value()
            or intent.price is None
        ):
            raise ContractValidationError("CTP projection requires a positive integer-lot LIMIT intent")
        if type(command_id) is not str or not command_id or command_id != command_id.strip():
            raise ContractValidationError("invalid CTP command id")

        durable_intent = self._store.get_intent(intent.intent_id, scope=self._scope)
        if durable_intent is None or durable_intent != intent:
            raise InvalidStateTransition("CTP intent differs from the durable execution record")

        reservation = self._store.read_ctp_order_identity(self._scope, intent.intent_id)
        if reservation is None:
            return None
        if type(reservation) is not CtpOrderIdentityReservation or (
            reservation.account_key != self._scope.account_key
            or reservation.scope_key != self._scope.key
            or reservation.trading_day != self._scope.trading_day
            or reservation.managed_intent_id != intent.intent_id
        ):
            raise InvalidStateTransition("CTP OrderRef reservation differs from intent scope")

        command = self._store.read_ctp_dispatch_command(self._scope, command_id)
        if command is None:
            return None
        if (
            command.account_key != self._scope.account_key
            or command.scope_key != self._scope.key
            or command.trading_day != self._scope.trading_day
            or command.operation != "SUBMIT"
            or command.command_id != command_id
            or command.reservation_managed_intent_id != intent.intent_id
            or command.order_ref != reservation.order_ref
            or command.session_binding_sha256 != binding.session_binding_sha256
        ):
            raise InvalidStateTransition("CTP outbox command differs from owner intent")
        correlation = command.correlation_key
        if type(correlation) is not CtpDispatchCorrelationKey or (
            correlation.version != 2
            or correlation.account_key != self._scope.account_key
            or correlation.scope_key != self._scope.key
            or correlation.trading_day != self._scope.trading_day
            or correlation.operation != "SUBMIT"
            or correlation.command_id != command_id
            or correlation.request_payload_sha256 != command.request_payload_sha256
            or correlation.reservation_managed_intent_id != intent.intent_id
            or correlation.managed_action_id != intent.intent_id
            or correlation.runtime_order_id != reservation.runtime_order_id
            or correlation.order_ref != reservation.order_ref
            or correlation.session_binding_sha256 != binding.session_binding_sha256
            or correlation.session_generation_id != binding.session_generation_id
            or correlation.dispatch_front_id != binding.dispatch_front_id
            or correlation.dispatch_session_id != binding.dispatch_session_id
        ):
            raise InvalidStateTransition("CTP outbox correlation differs from active session")
        self._validate_intent_request(intent, reservation, command.request_payload)

        projection = self._store.read_ctp_dispatch_projection(self._scope, command_id)
        if type(projection) is not CtpDispatchProjection or (
            projection.command_id != command_id
            or projection.operation != "SUBMIT"
            or projection.command_status != command.status
            or projection.submit_action is None
        ):
            raise InvalidStateTransition("typed CTP order projection is unavailable")
        return CtpOwnerOrderProjectionView(
            intent_id=intent.intent_id,
            instrument=intent.instrument,
            requested_quantity=intent.quantity,
            reservation=reservation,
            session_binding=binding,
            projection=projection,
        )

    @staticmethod
    def _validate_intent_request(
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        payload: Any,
    ) -> None:
        if not isinstance(payload, Mapping):
            raise InvalidStateTransition("CTP request payload is unavailable")
        expected_direction = _DIRECTION_FLAGS[intent.side]
        expected_offset = _OFFSET_FLAGS.get((intent.position_effect, intent.position_bucket))
        if expected_offset is None:
            raise ContractValidationError("unsupported CTP position effect and bucket")
        if (
            payload.get("InstrumentID") != intent.instrument
            or payload.get("OrderRef") != reservation.order_ref
            or payload.get("Direction") != expected_direction
            or payload.get("CombOffsetFlag") != expected_offset
            or type(payload.get("VolumeTotalOriginal")) is not int
            or payload["VolumeTotalOriginal"] != int(intent.quantity)
            or payload.get("OrderPriceType") != "2"
        ):
            raise InvalidStateTransition("CTP request differs from integer-lot LIMIT intent")
        try:
            request_price = Decimal(str(payload.get("LimitPrice")))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise InvalidStateTransition("CTP LIMIT price is unreadable") from error
        if not request_price.is_finite() or request_price != intent.price:
            raise InvalidStateTransition("CTP LIMIT price differs from intent")


__all__ = [
    "CtpOwnerCallbackReconciliationPort",
    "CtpOwnerOrderProjectionView",
]
