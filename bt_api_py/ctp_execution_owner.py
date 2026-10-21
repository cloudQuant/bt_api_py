"""Single-threaded execution controller borrowing an already-active CTP session.

This controller owns neither the TraderClient lifecycle nor its native API. It
requires the same live Store owner, callback adapter, active session, and
writer lease already held by the caller. Local queue/native receipts describe
dispatch only; provider state is read from the callback reconciliation port.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from threading import current_thread
from typing import Any

from bt_api_execution import (
    ContractValidationError,
    CtpCallbackSessionBindingV1,
    CtpCallbackSessionOwnerHandle,
    CtpCallbackSessionStoreAdapter,
    CtpDispatchAuthorityVerifier,
    CtpDispatchCommand,
    CtpDispatchReceipt,
    CtpManagedNativeCallBindingV2,
    CtpOrderTargetProjectionHandle,
    CtpVerifiedOrderTargetProjection,
    CtpOwnerCallbackObservationNotReadyV1,
    CtpSessionNativeCallClaim,
    ExecutionScope,
    InvalidStateTransition,
    ProviderObservation,
    SqliteExecutionStore,
    WriterLease,
    WriterLeaseUnavailable,
    canonical_json,
    payload_sha256,
)
from bt_api_execution.ctp_single_worker_candidate import (
    CtpManagedDispatchBinding,
    CtpManagedPreparedDispatch,
    CtpManagedSingleWorkerCandidate,
)
from bt_api_py.ctp_service_execution import CtpOwnerCallbackReconciliationPort


@dataclass(frozen=True, slots=True)
class CtpExecutionDispatchResult:
    """Durable local dispatch state; this is not a provider acknowledgement."""

    binding: CtpManagedDispatchBinding
    command: CtpDispatchCommand


def _make_native_order_field(payload: Mapping[str, Any]) -> Any:
    """Build a CTP leaf from the Store-claimed, canonical native payload."""

    if not isinstance(payload, Mapping) or not payload:
        raise ContractValidationError("Store-claimed CTP native payload is required")
    # Keep the generated wrapper and its native extension out of module import.
    from bt_api_ctp.ctp.ctp_structs_order import CThostFtdcInputOrderField

    field = CThostFtdcInputOrderField()
    for name, value in payload.items():
        if type(name) is not str or not name or name == "OrderActionRef":
            raise ContractValidationError("invalid field in CTP submit payload")
        setattr(field, name, value)
    return field


def _make_native_order_action_field(payload: Mapping[str, Any]) -> Any:
    """Build a CTP delete-action leaf from a Store-claimed native payload."""

    if not isinstance(payload, Mapping) or not payload:
        raise ContractValidationError("Store-claimed CTP native cancel payload is required")
    # Keep the generated wrapper and its native extension out of module import.
    from bt_api_ctp.ctp.ctp_structs_order import CThostFtdcInputOrderActionField

    field = CThostFtdcInputOrderActionField()
    for name, value in payload.items():
        if type(name) is not str or not name or not hasattr(field, name):
            raise ContractValidationError("invalid field in CTP cancel payload")
        if name == "OrderActionRef" and (type(value) is not int or value <= 0):
            raise ContractValidationError("invalid Store-issued CTP ActionRef")
        setattr(field, name, value)
    return field


class CtpExecutionOwner:
    """Borrow one ACTIVE TraderClient session to dispatch queued commands.

    The instance is confined to its creating Python thread. It does not create,
    start, stop, or close the TraderClient/native session. SUBMIT continues to
    use its existing verifier; CANCEL requires the separate same-owner cancel
    authority issuer and a fresh Store-issued target projection.
    """

    def __init__(
        self,
        store: SqliteExecutionStore,
        scope: ExecutionScope,
        writer_lease: WriterLease,
        authority_verifier: CtpDispatchAuthorityVerifier,
        trader: Any,
        session_adapter: CtpCallbackSessionStoreAdapter,
        reconciliation_port: CtpOwnerCallbackReconciliationPort,
        *,
        session_generation_id: str,
    ) -> None:
        if type(store) is not SqliteExecutionStore:
            raise ContractValidationError("exact execution Store is required")
        if (
            type(scope) is not ExecutionScope
            or scope.provider != "ctp"
            or scope.environment != "simulation"
            or not scope.trading_day
        ):
            raise ContractValidationError("canonical CTP simulation scope is required")
        if type(writer_lease) is not WriterLease:
            raise ContractValidationError("typed CTP writer lease is required")
        if not callable(getattr(authority_verifier, "verify_action", None)):
            raise ContractValidationError("trusted CTP action verifier is required")
        if type(session_adapter) is not CtpCallbackSessionStoreAdapter:
            raise ContractValidationError("exact CTP callback session adapter is required")
        if (
            session_adapter.store is not store
            or session_adapter.scope != scope
            or session_adapter.writer_lease is not writer_lease
            or type(session_adapter.owner_handle) is not CtpCallbackSessionOwnerHandle
        ):
            raise ContractValidationError("CTP callback adapter is not bound to this Store owner")
        if type(reconciliation_port) is not CtpOwnerCallbackReconciliationPort:
            raise ContractValidationError("exact CTP callback reconciliation port is required")
        if (
            reconciliation_port._store is not store
            or reconciliation_port._scope != scope
            or reconciliation_port._writer_lease is not writer_lease
            or reconciliation_port._session_adapter is not session_adapter
            or reconciliation_port._owner_handle is not session_adapter.owner_handle
            or reconciliation_port._session_generation_id != session_generation_id
        ):
            raise ContractValidationError("CTP reconciliation port differs from borrowed owner")
        if (
            type(session_generation_id) is not str
            or not session_generation_id
            or session_generation_id != session_generation_id.strip()
        ):
            raise ContractValidationError("exact CTP session generation is required")
        # The object is supplied by a live composition that has already
        # constructed TraderClient; this local import only verifies its class.
        from bt_api_ctp.ctp.client import TraderClient

        if type(trader) is not TraderClient:
            raise ContractValidationError("an exact CTP TraderClient is required")
        for method_name in ("acquire_managed_native_call_lease", "submit_order_insert_with_lease"):
            if not callable(getattr(trader, method_name, None)):
                raise ContractValidationError("managed TraderClient submit methods are required")

        self._store = store
        self._scope = scope
        self._writer_lease = writer_lease
        self._authority_verifier = authority_verifier
        self._trader = trader
        self._session_adapter = session_adapter
        self._reconciliation_port = reconciliation_port
        self._owner_handle = session_adapter.owner_handle
        self._session_generation_id = session_generation_id
        self._thread = current_thread()
        self._risk_bridge: Any | None = None
        self._risk_bridge_binding_attempted = False
        self._candidate = CtpManagedSingleWorkerCandidate(
            store, scope, writer_lease, authority_verifier
        )
        self._session_binding = self._read_active_session_binding()
        self._assert_trader_session(self._session_binding)

    def bind_risk_dispatch_bridge(self, bridge: Any) -> None:
        """Bind the exact Risk journal bridge once for every fresh submit."""

        from bt_api_py.ctp_risk_dispatch_bridge import CtpRiskDispatchBridge

        self._assert_worker_thread()
        if self._risk_bridge_binding_attempted:
            raise InvalidStateTransition("CTP Risk bridge binding is one-shot")
        self._risk_bridge_binding_attempted = True
        if type(bridge) is not CtpRiskDispatchBridge:
            raise ContractValidationError("exact CTP Risk dispatch bridge is required")
        self._assert_active_owner()
        bridge.bind_execution_owner(self)
        self._risk_bridge = bridge

    def _assert_worker_thread(self) -> None:
        if current_thread() is not self._thread:
            raise ContractValidationError("CTP execution owner is confined to its creating thread")

    def _read_active_session_binding(self) -> CtpCallbackSessionBindingV1:
        self._store.assert_writer_lease(self._scope, self._writer_lease)
        facts = self._store.read_ctp_callback_session_context_facts(self._owner_handle)
        if (
            type(facts) is not tuple
            or len(facts) != 3
            or type(facts[0]) is not CtpCallbackSessionBindingV1
        ):
            raise InvalidStateTransition("active CTP callback session is unavailable")
        binding = facts[0]
        if (
            binding.owner_intent_id != self._owner_handle.owner_intent_id
            or binding.account_key != self._scope.account_key
            or binding.scope_key != self._scope.key
            or binding.trading_day != self._scope.trading_day
            or binding.session_generation_id != self._session_generation_id
        ):
            raise InvalidStateTransition("active CTP session differs from borrowed scope")
        if self._reconciliation_port._session_binding != binding:
            raise InvalidStateTransition("reconciliation port holds another CTP session")
        return binding

    @staticmethod
    def _same_bound_method(method: Any, instance: Any, function: Any) -> bool:
        return getattr(method, "__self__", None) is instance and getattr(
            method, "__func__", None
        ) is function

    def _assert_trader_session(self, binding: CtpCallbackSessionBindingV1) -> None:
        state = getattr(self._trader, "_callback_ingress", None)
        if (
            state is None
            or getattr(state, "owner_handle", None) is not self._owner_handle
            or getattr(state, "phase", None) != "ACTIVE"
            or getattr(state, "poisoned", None) is not False
            or getattr(state, "active_session", None) is None
            or getattr(state, "append_sink", None) is not self._session_adapter.sink
            or not self._same_bound_method(
                getattr(state, "session_binder", None),
                self._session_adapter,
                type(self._session_adapter).bind_session,
            )
            or not self._same_bound_method(
                getattr(state, "command_binding_verifier", None),
                self._session_adapter,
                type(self._session_adapter).verify_native_call,
            )
        ):
            raise InvalidStateTransition("TraderClient is not bound to this ACTIVE callback owner")

        session = state.active_session
        expected_session = {
            "owner_intent_id": binding.owner_intent_id,
            "account_key": binding.account_key,
            "scope_key": binding.scope_key,
            "trading_day": binding.trading_day,
            "session_generation_id": binding.session_generation_id,
            "connection_generation": binding.connection_generation,
            "dispatch_front_id": binding.dispatch_front_id,
            "dispatch_session_id": binding.dispatch_session_id,
            "source_instance_id": binding.source_instance_id,
            "native_client_epoch": binding.native_client_epoch,
            "native_api_source_id": binding.native_api_source_id,
            "native_spi_source_id": binding.native_spi_source_id,
            "native_api_generation": binding.native_api_generation,
            "source_connection_generation": binding.source_connection_generation,
            "session_binding_sha256": binding.session_binding_sha256,
        }
        if any(getattr(session, name, None) != value for name, value in expected_session.items()):
            raise InvalidStateTransition("TraderClient active session identity differs from Store")

        tags = getattr(state, "current_source_tags_object", None)
        expected_tags = {
            "source_instance_id": binding.source_instance_id,
            "native_client_epoch": binding.native_client_epoch,
            "native_api_source_id": binding.native_api_source_id,
            "native_spi_source_id": binding.native_spi_source_id,
            "native_api_generation": binding.native_api_generation,
            "connection_generation": binding.source_connection_generation,
        }
        if tags is None or any(getattr(tags, name, None) != value for name, value in expected_tags.items()):
            raise InvalidStateTransition("TraderClient callback source differs from Store session")
        api = getattr(self._trader, "_api", None)
        spi = getattr(self._trader, "_spi", None)
        if (
            api is None
            or spi is None
            or getattr(self._trader, "_session_native_api", None) is not api
            or getattr(spi, "_native_api", None) is not api
            or getattr(spi, "_callback_source_tags", None) is not tags
        ):
            raise InvalidStateTransition("TraderClient API/SPI source identity differs")

    def _assert_active_owner(self) -> CtpCallbackSessionBindingV1:
        binding = self._read_active_session_binding()
        if binding != self._session_binding:
            raise InvalidStateTransition("borrowed CTP session generation changed")
        self._assert_trader_session(binding)
        return binding

    @staticmethod
    def _queue_receipt_values(
        prepared: CtpManagedPreparedDispatch, queue_receipt: Mapping[str, Any]
    ) -> tuple[str, bool]:
        if not isinstance(queue_receipt, Mapping):
            raise ContractValidationError("CTP local queue receipt mapping is required")
        if set(queue_receipt) != {"kind", "command", "receipt_id", "queued"}:
            raise ContractValidationError("CTP local queue receipt fields are not exact")
        receipt_id = queue_receipt.get("receipt_id")
        queued = queue_receipt.get("queued")
        if (
            queue_receipt.get("kind") != "command_receipt"
            or queue_receipt.get("command") != "submit"
            or type(receipt_id) is not str
            or receipt_id != prepared.local_queue_receipt_id
            or type(queued) is not bool
        ):
            raise ContractValidationError("CTP queue receipt differs from prepared SUBMIT")
        return receipt_id, queued

    @staticmethod
    def _cancel_queue_receipt_values(
        prepared: CtpManagedPreparedDispatch, queue_receipt: Mapping[str, Any]
    ) -> tuple[str, bool]:
        if not isinstance(queue_receipt, Mapping):
            raise ContractValidationError("CTP local queue receipt mapping is required")
        if set(queue_receipt) != {"kind", "command", "receipt_id", "queued"}:
            raise ContractValidationError("CTP local queue receipt fields are not exact")
        receipt_id = queue_receipt.get("receipt_id")
        queued = queue_receipt.get("queued")
        if (
            queue_receipt.get("kind") != "command_receipt"
            or queue_receipt.get("command") != "cancel"
            or type(receipt_id) is not str
            or receipt_id != prepared.local_queue_receipt_id
            or type(queued) is not bool
        ):
            raise ContractValidationError("CTP queue receipt differs from prepared CANCEL")
        return receipt_id, queued

    @staticmethod
    def _cancel_target_matches_prepared(
        prepared: CtpManagedPreparedDispatch,
        handle: CtpOrderTargetProjectionHandle,
        scope: ExecutionScope,
    ) -> bool:
        if (
            type(handle) is not CtpOrderTargetProjectionHandle
            or type(handle.projection) is not CtpVerifiedOrderTargetProjection
            or prepared.operation != "cancel"
        ):
            return False
        projection = handle.projection
        return (
            projection.account_key == scope.account_key
            and projection.scope_key == scope.key
            and projection.trading_day == scope.trading_day
            and projection.managed_intent_id == prepared.managed_intent_id
            and projection.runtime_order_id == prepared.runtime_order_id
            and projection.order_ref == prepared.order_ref
            and projection.exchange_id == prepared.cancel_target_exchange_id
            and projection.order_sys_id == prepared.cancel_target_order_sys_id
            and projection.front_id == prepared.cancel_target_front_id
            and projection.session_id == prepared.cancel_target_session_id
            and projection.session_generation_id == prepared.session_generation_id
        )

    @staticmethod
    def _prepared_matches_cancel_command(
        prepared: CtpManagedPreparedDispatch,
        binding: CtpManagedDispatchBinding,
        reservation: Any,
    ) -> bool:
        return (
            binding.operation == "cancel"
            and prepared.operation == binding.operation
            and prepared.command_id == binding.command_id
            and prepared.managed_intent_id == binding.managed_intent_id
            and prepared.runtime_order_id == binding.runtime_order_id
            and prepared.order_ref == binding.order_ref
            and dict(prepared.request_payload) == dict(binding.request_payload)
            and prepared.order_ref_reservation == reservation
            and prepared.managed_cancel_intent_id == binding.managed_action_id
            and prepared.cancel_target_exchange_id == binding.cancel_target_exchange_id
            and prepared.cancel_target_order_sys_id == binding.cancel_target_order_sys_id
            and prepared.cancel_target_front_id == binding.cancel_target_front_id
            and prepared.cancel_target_session_id == binding.cancel_target_session_id
            and prepared.approval_use_id == binding.approval_use_id
            and prepared.approval_digest == binding.approval_digest
            and dict(prepared.session_binding) == dict(binding.session_binding)
            and prepared.session_generation_id == binding.session_generation_id
            and prepared.dispatch_front_id == binding.dispatch_front_id
            and prepared.dispatch_session_id == binding.dispatch_session_id
            and prepared.native_request_id == binding.native_request_id
            and prepared.local_queue_receipt_id == binding.local_queue_receipt_id
        )

    @staticmethod
    def _prepared_matches_command(
        prepared: CtpManagedPreparedDispatch,
        binding: CtpManagedDispatchBinding,
        reservation: Any,
    ) -> bool:
        return (
            binding.operation == "submit"
            and prepared.operation == binding.operation
            and prepared.command_id == binding.command_id
            and prepared.managed_intent_id == binding.managed_intent_id
            and prepared.runtime_order_id == binding.runtime_order_id
            and prepared.order_ref == binding.order_ref
            and dict(prepared.request_payload) == dict(binding.request_payload)
            and prepared.order_ref_reservation == reservation
            and prepared.approval_use_id == binding.approval_use_id
            and prepared.approval_digest == binding.approval_digest
            and dict(prepared.session_binding) == dict(binding.session_binding)
            and prepared.session_generation_id == binding.session_generation_id
            and prepared.dispatch_front_id == binding.dispatch_front_id
            and prepared.dispatch_session_id == binding.dispatch_session_id
            and prepared.native_request_id == binding.native_request_id
            and prepared.local_queue_receipt_id == binding.local_queue_receipt_id
        )

    @staticmethod
    def _receipt(
        command: CtpDispatchCommand, outcome: str, payload: Mapping[str, Any]
    ) -> CtpDispatchReceipt:
        return CtpDispatchReceipt(
            receipt_type="ctp_dispatch_receipt.v2",
            command_id=command.command_id,
            account_key=command.account_key,
            scope_key=command.scope_key,
            trading_day=command.trading_day,
            operation=command.operation,
            request_payload_sha256=command.request_payload_sha256,
            reservation_managed_intent_id=command.reservation_managed_intent_id,
            order_ref=command.order_ref,
            cancel_target_order_ref=command.cancel_target_order_ref,
            cancel_target_exchange_id=command.cancel_target_exchange_id,
            cancel_target_order_sys_id=command.cancel_target_order_sys_id,
            cancel_target_front_id=command.cancel_target_front_id,
            cancel_target_session_id=command.cancel_target_session_id,
            approval_use_id=command.approval_use_id,
            approval_digest=command.approval_digest,
            session_binding_sha256=command.session_binding_sha256,
            outcome=outcome,
            native_receipt_payload=dict(payload),
            correlation_key=command.correlation_key,
            local_queue_receipt_id=command.local_queue_receipt_id,
        )

    def _existing_result(
        self,
        prepared: CtpManagedPreparedDispatch,
        queue_queued: bool,
        command: CtpDispatchCommand,
    ) -> CtpExecutionDispatchResult:
        if type(command) is not CtpDispatchCommand:
            raise InvalidStateTransition("durable CTP command is unavailable")
        reservation = self._store.read_ctp_order_identity(self._scope, prepared.managed_intent_id)
        if not self._prepared_matches_command(
            prepared,
            CtpManagedDispatchBinding.from_command(command, reservation),
            reservation,
        ):
            raise ContractValidationError("prepared SUBMIT differs from durable CTP command")
        if command.local_queue_receipt_queued is not queue_queued:
            raise ContractValidationError("queue disposition differs from durable CTP command")
        return CtpExecutionDispatchResult(
            CtpManagedDispatchBinding.from_command(command, reservation), command
        )

    def _existing_cancel_result(
        self,
        prepared: CtpManagedPreparedDispatch,
        queue_queued: bool,
        command: CtpDispatchCommand,
        cancel_target_projection: CtpOrderTargetProjectionHandle,
    ) -> CtpExecutionDispatchResult:
        if type(command) is not CtpDispatchCommand or command.operation != "CANCEL":
            raise InvalidStateTransition("durable CTP CANCEL command is unavailable")
        reservation = self._store.read_ctp_order_identity(self._scope, prepared.managed_intent_id)
        binding = CtpManagedDispatchBinding.from_command(command, reservation)
        if (
            command.account_key != self._scope.account_key
            or command.scope_key != self._scope.key
            or command.trading_day != self._scope.trading_day
            or not self._prepared_matches_cancel_command(prepared, binding, reservation)
            or not self._cancel_target_matches_prepared(
                prepared, cancel_target_projection, self._scope
            )
        ):
            raise ContractValidationError("prepared CANCEL differs from durable CTP command")
        if command.local_queue_receipt_queued is not queue_queued:
            raise ContractValidationError("queue disposition differs from durable CTP CANCEL")
        return CtpExecutionDispatchResult(binding, command)

    def dispatch_submit(
        self,
        prepared: CtpManagedPreparedDispatch,
        queue_receipt: Mapping[str, Any],
        *,
        position_baseline_authorization: object | None = None,
        recovery_close_authorization: object | None = None,
    ) -> CtpExecutionDispatchResult:
        """Stage and send one SUBMIT through the Store-issued session binding."""

        self._assert_worker_thread()
        if type(prepared) is not CtpManagedPreparedDispatch:
            raise ContractValidationError("typed managed CTP prepared dispatch is required")
        if prepared.operation != "submit":
            raise ContractValidationError("this borrowed-session controller accepts SUBMIT only")
        if (
            position_baseline_authorization is not None
            and recovery_close_authorization is not None
        ):
            raise ContractValidationError("CTP position dispatch authorizations are exclusive")
        _receipt_id, queue_queued = self._queue_receipt_values(prepared, queue_receipt)

        existing = self._store.read_ctp_dispatch_command(self._scope, prepared.command_id)
        if type(existing) is CtpDispatchCommand and existing.status != "READY":
            # Read an in-flight/terminal row before requiring a healthy owner.
            # CLAIMED and UNKNOWN are never made claimable or sent again.
            return self._existing_result(prepared, queue_queued, existing)

        binding = self._assert_active_owner()
        if (
            prepared.session_generation_id != binding.session_generation_id
            or prepared.dispatch_front_id != binding.dispatch_front_id
            or prepared.dispatch_session_id != binding.dispatch_session_id
        ):
            raise ContractValidationError("prepared SUBMIT belongs to another active CTP session")

        staged_binding = self._candidate.stage_prepared_dispatch(
            prepared,
            position_baseline_authorization=position_baseline_authorization,
            recovery_close_authorization=recovery_close_authorization,
        )
        queued_binding = self._candidate.record_managed_queue_receipt(
            prepared.command_id,
            staged_binding,
            queue_receipt,
            owner_handle=self._owner_handle,
            position_baseline_authorization=position_baseline_authorization,
            recovery_close_authorization=recovery_close_authorization,
        )
        command = self._store.read_ctp_dispatch_command(self._scope, prepared.command_id)
        if type(command) is not CtpDispatchCommand:
            raise InvalidStateTransition("staged CTP SUBMIT disappeared")
        reservation = self._store.read_ctp_order_identity(self._scope, prepared.managed_intent_id)
        durable_binding = CtpManagedDispatchBinding.from_command(command, reservation)
        if queued_binding != durable_binding or not self._prepared_matches_command(
            prepared, durable_binding, reservation
        ):
            raise InvalidStateTransition("staged CTP SUBMIT binding changed")
        if command.local_queue_receipt_queued is not queue_queued:
            raise InvalidStateTransition("durable CTP queue receipt differs from caller")
        if queue_queued is False or command.status != "READY":
            return CtpExecutionDispatchResult(durable_binding, command)

        if self._risk_bridge is not None:
            # The owner-held binding cannot be omitted by a direct caller.
            # A failure leaves the durable Risk claim unresolved and prevents
            # the native claim/call; existing commands return above unchanged.
            self._risk_bridge.before_native_dispatch(
                owner=self,
                prepared=prepared,
                command=command,
                reservation=reservation,
                dispatch_binding=durable_binding,
            )

        claim = self._store.claim_ctp_dispatch_command_for_session(
            self._scope,
            prepared.command_id,
            owner_handle=self._owner_handle,
            writer_lease=self._writer_lease,
            authority_verifier=self._authority_verifier,
            required_local_queue_receipt_id=durable_binding.local_queue_receipt_id,
            position_baseline_authorization=position_baseline_authorization,
            recovery_close_authorization=recovery_close_authorization,
        )
        if claim is None:
            current = self._store.read_ctp_dispatch_command(self._scope, prepared.command_id)
            if type(current) is not CtpDispatchCommand:
                raise InvalidStateTransition("claimed CTP SUBMIT disappeared")
            return self._existing_result(prepared, queue_queued, current)
        if type(claim) is not CtpSessionNativeCallClaim or type(claim.command) is not CtpDispatchCommand:
            raise InvalidStateTransition("Store returned an invalid session-bound CTP claim")
        if claim.command.command_id != prepared.command_id or claim.command.operation != "SUBMIT":
            raise InvalidStateTransition("Store claim differs from prepared CTP SUBMIT")

        outcome = "UNKNOWN"
        receipt_payload: Mapping[str, Any] = {
            "kind": "managed_native_request",
            "outcome": "UNKNOWN",
        }
        try:
            native_binding = claim.binding
            try:
                native_payload = json.loads(native_binding.native_request_payload_json)
            except (TypeError, ValueError) as error:
                raise InvalidStateTransition(
                    "Store native binding payload is not valid JSON"
                ) from error
            if (
                type(native_binding) is not CtpManagedNativeCallBindingV2
                or native_binding.command_id != claim.command.command_id
                or native_binding.owner_intent_id != self._owner_handle.owner_intent_id
                or native_binding.account_key != self._scope.account_key
                or native_binding.scope_key != self._scope.key
                or native_binding.trading_day != self._scope.trading_day
                or native_binding.session_generation_id != self._session_generation_id
                or native_binding.native_request_id != durable_binding.native_request_id
                or native_binding.operation != "SUBMIT"
                or native_binding.native_action_ref is not None
                or type(native_payload) is not dict
                or canonical_json(native_payload)
                != native_binding.native_request_payload_json
                or payload_sha256(native_payload)
                != native_binding.native_request_payload_sha256
                or native_payload != dict(durable_binding.native_request_payload)
                or native_binding.native_request_payload_sha256
                != durable_binding.native_request_payload_sha256
            ):
                raise InvalidStateTransition("Store native binding differs from queued SUBMIT")
            native_field = _make_native_order_field(native_payload)
            if not hasattr(native_field, "RequestID"):
                raise InvalidStateTransition("native CTP submit field lacks RequestID")
            native_field.RequestID = native_binding.native_request_id
            native_lease = self._trader.acquire_managed_native_call_lease(
                self._owner_handle, native_binding
            )
            native_result = self._trader.submit_order_insert_with_lease(
                native_lease, native_field, native_binding.native_request_id
            )
            if type(native_result) is int and native_result == 0:
                outcome = "QUEUED"
                receipt_payload = {
                    "kind": "managed_native_request",
                    "request_id": native_binding.native_request_id,
                    "return_code": 0,
                }
        except Exception:
            # The claim is one-shot; failure cannot prove the native call absent.
            # Do not log exception text or release/replace the borrowed session.
            pass

        try:
            completed = self._session_adapter.complete_native_receipt(
                self._receipt(claim.command, outcome, receipt_payload)
            )
        except WriterLeaseUnavailable:
            if outcome != "UNKNOWN":
                raise
            # A native ambiguity poisons the callback/session family. Store
            # intentionally keeps that claim in flight and forbids receipt
            # completion after the fence; return only its exact durable row.
            current = self._store.read_ctp_dispatch_command(
                self._scope, prepared.command_id
            )
            if (
                type(current) is not CtpDispatchCommand
                or current.status != "CLAIMED"
                or current.native_receipt_payload is not None
                or current.native_receipt_sha256 is not None
                or current.completion_echo_sha256 is not None
            ):
                raise InvalidStateTransition(
                    "ambiguous CTP native claim did not remain durably in flight"
                )
            return self._existing_result(prepared, queue_queued, current)
        return CtpExecutionDispatchResult(durable_binding, completed)

    def dispatch_cancel(
        self,
        prepared: CtpManagedPreparedDispatch,
        queue_receipt: Mapping[str, Any],
        *,
        cancel_target_projection: CtpOrderTargetProjectionHandle,
        authority_verifier: Any,
    ) -> CtpExecutionDispatchResult:
        """Dispatch one authorized CANCEL through the borrowed ACTIVE session.

        The issuer is also the Store's one-use dispatch verifier. A native
        ambiguity is persisted as UNKNOWN and this method never resends it.
        """

        self._assert_worker_thread()
        if type(prepared) is not CtpManagedPreparedDispatch or prepared.operation != "cancel":
            raise ContractValidationError("typed managed CTP CANCEL dispatch is required")
        from bt_api_py.ctp_cancel_authority import CtpCancelAuthorityIssuerV1

        if (
            type(authority_verifier) is not CtpCancelAuthorityIssuerV1
            or authority_verifier._owner is not self
            or authority_verifier._store is not self._store
            or authority_verifier._scope != self._scope
            or authority_verifier._writer_lease is not self._writer_lease
            or authority_verifier._session_adapter is not self._session_adapter
            or authority_verifier._owner_handle is not self._owner_handle
        ):
            raise ContractValidationError("exact same-owner CTP cancel issuer is required")
        if not self._cancel_target_matches_prepared(
            prepared, cancel_target_projection, self._scope
        ):
            raise ContractValidationError("CANCEL target projection differs from prepared action")
        _receipt_id, queue_queued = self._cancel_queue_receipt_values(prepared, queue_receipt)

        existing = self._store.read_ctp_dispatch_command(self._scope, prepared.command_id)
        if type(existing) is CtpDispatchCommand and existing.status != "READY":
            return self._existing_cancel_result(
                prepared, queue_queued, existing, cancel_target_projection
            )

        session_binding = self._assert_active_owner()
        if (
            prepared.session_generation_id != session_binding.session_generation_id
            or prepared.dispatch_front_id != session_binding.dispatch_front_id
            or prepared.dispatch_session_id != session_binding.dispatch_session_id
        ):
            raise ContractValidationError("prepared CANCEL belongs to another active CTP session")

        staged_binding = self._candidate.stage_prepared_dispatch(
            prepared, cancel_target_projection=cancel_target_projection
        )
        queued_binding = self._candidate.record_managed_queue_receipt(
            prepared.command_id, staged_binding, queue_receipt
        )
        command = self._store.read_ctp_dispatch_command(self._scope, prepared.command_id)
        if type(command) is not CtpDispatchCommand or command.operation != "CANCEL":
            raise InvalidStateTransition("staged CTP CANCEL disappeared")
        reservation = self._store.read_ctp_order_identity(self._scope, prepared.managed_intent_id)
        durable_binding = CtpManagedDispatchBinding.from_command(command, reservation)
        if (
            queued_binding != durable_binding
            or not self._prepared_matches_cancel_command(prepared, durable_binding, reservation)
            or not self._cancel_target_matches_prepared(
                prepared, cancel_target_projection, self._scope
            )
        ):
            raise InvalidStateTransition("staged CTP CANCEL binding changed")
        if command.local_queue_receipt_queued is not queue_queued:
            raise InvalidStateTransition("durable CTP CANCEL queue receipt differs from caller")
        if queue_queued is False or command.status != "READY":
            return CtpExecutionDispatchResult(durable_binding, command)

        if not callable(getattr(self._trader, "submit_order_action_with_lease", None)):
            raise ContractValidationError("managed TraderClient cancel method is unavailable")
        claim = self._store.claim_ctp_dispatch_command_for_session(
            self._scope,
            prepared.command_id,
            owner_handle=self._owner_handle,
            writer_lease=self._writer_lease,
            authority_verifier=authority_verifier,
            required_local_queue_receipt_id=durable_binding.local_queue_receipt_id,
        )
        if claim is None:
            current = self._store.read_ctp_dispatch_command(self._scope, prepared.command_id)
            if type(current) is not CtpDispatchCommand:
                raise InvalidStateTransition("claimed CTP CANCEL disappeared")
            return self._existing_cancel_result(
                prepared, queue_queued, current, cancel_target_projection
            )
        if type(claim) is not CtpSessionNativeCallClaim or type(claim.command) is not CtpDispatchCommand:
            raise InvalidStateTransition("Store returned an invalid session-bound CTP CANCEL claim")
        if claim.command.command_id != prepared.command_id or claim.command.operation != "CANCEL":
            raise InvalidStateTransition("Store claim differs from prepared CTP CANCEL")

        outcome = "UNKNOWN"
        receipt_payload: Mapping[str, Any] = {
            "kind": "managed_native_request",
            "outcome": "UNKNOWN",
        }
        try:
            native_binding = claim.binding
            try:
                logical_payload = json.loads(native_binding.request_payload_json)
                native_payload = json.loads(native_binding.native_request_payload_json)
            except (TypeError, ValueError) as error:
                raise InvalidStateTransition(
                    "Store native cancel binding payload is not valid JSON"
                ) from error
            if (
                type(native_binding) is not CtpManagedNativeCallBindingV2
                or native_binding.command_id != claim.command.command_id
                or native_binding.owner_intent_id != self._owner_handle.owner_intent_id
                or native_binding.account_key != self._scope.account_key
                or native_binding.scope_key != self._scope.key
                or native_binding.trading_day != self._scope.trading_day
                or native_binding.operation != "CANCEL"
                or native_binding.reservation_managed_intent_id != prepared.managed_intent_id
                or native_binding.managed_action_id != prepared.managed_cancel_intent_id
                or native_binding.runtime_order_id != prepared.runtime_order_id
                or native_binding.order_ref != prepared.order_ref
                or native_binding.native_request_id != durable_binding.native_request_id
                or native_binding.native_action_ref != durable_binding.native_action_ref
                or native_binding.cancel_target_order_ref != durable_binding.cancel_target_order_ref
                or native_binding.cancel_target_exchange_id
                != durable_binding.cancel_target_exchange_id
                or native_binding.cancel_target_order_sys_id
                != durable_binding.cancel_target_order_sys_id
                or native_binding.cancel_target_front_id != durable_binding.cancel_target_front_id
                or native_binding.cancel_target_session_id != durable_binding.cancel_target_session_id
                or native_binding.session_binding_sha256 != durable_binding.session_binding_sha256
                or native_binding.session_generation_id != self._session_generation_id
                or native_binding.dispatch_front_id != durable_binding.dispatch_front_id
                or native_binding.dispatch_session_id != durable_binding.dispatch_session_id
                or type(native_binding.native_request_id) is not int
                or not 1 <= native_binding.native_request_id <= 2_147_483_647
                or native_binding.native_request_id != prepared.native_request_id
                or type(native_binding.native_action_ref) is not int
                or native_binding.native_action_ref <= 0
                or type(logical_payload) is not dict
                or canonical_json(logical_payload) != native_binding.request_payload_json
                or payload_sha256(logical_payload) != native_binding.request_payload_sha256
                or logical_payload != dict(durable_binding.request_payload)
                or native_binding.request_payload_sha256 != durable_binding.request_payload_sha256
                or type(native_payload) is not dict
                or canonical_json(native_payload) != native_binding.native_request_payload_json
                or payload_sha256(native_payload) != native_binding.native_request_payload_sha256
                or native_payload != dict(durable_binding.native_request_payload)
                or native_binding.native_request_payload_sha256
                != durable_binding.native_request_payload_sha256
                or native_payload.get("OrderActionRef") != native_binding.native_action_ref
                or native_payload.get("OrderRef") != prepared.order_ref
                or native_payload.get("ExchangeID") != prepared.cancel_target_exchange_id
                or native_payload.get("OrderSysID") != prepared.cancel_target_order_sys_id
                or native_payload.get("FrontID") != prepared.cancel_target_front_id
                or native_payload.get("SessionID") != prepared.cancel_target_session_id
                or native_payload.get("ActionFlag") != "0"
            ):
                raise InvalidStateTransition("Store native binding differs from queued CTP CANCEL")
            native_field = _make_native_order_action_field(native_payload)
            if not hasattr(native_field, "RequestID"):
                raise InvalidStateTransition("native CTP cancel field lacks RequestID")
            native_field.RequestID = native_binding.native_request_id
            native_lease = self._trader.acquire_managed_native_call_lease(
                self._owner_handle, native_binding
            )
            native_result = self._trader.submit_order_action_with_lease(
                native_lease,
                native_field,
                native_binding.native_request_id,
                runtime_action_id=prepared.managed_cancel_intent_id,
                managed_cancel_intent_id=prepared.managed_cancel_intent_id,
            )
            if type(native_result) is int and native_result == 0:
                outcome = "QUEUED"
                receipt_payload = {
                    "kind": "managed_native_request",
                    "request_id": native_binding.native_request_id,
                    "action_ref": native_binding.native_action_ref,
                    "return_code": 0,
                }
        except Exception:
            # The claim is one-shot; failure cannot prove the native call absent.
            # Do not log exception text or release/replace the borrowed session.
            pass

        try:
            completed = self._session_adapter.complete_native_receipt(
                self._receipt(claim.command, outcome, receipt_payload)
            )
        except WriterLeaseUnavailable:
            if outcome != "UNKNOWN":
                raise
            current = self._store.read_ctp_dispatch_command(self._scope, prepared.command_id)
            if (
                type(current) is not CtpDispatchCommand
                or current.status != "CLAIMED"
                or current.native_receipt_payload is not None
                or current.native_receipt_sha256 is not None
                or current.completion_echo_sha256 is not None
            ):
                raise InvalidStateTransition(
                    "ambiguous CTP CANCEL claim did not remain durably in flight"
                )
            return self._existing_cancel_result(
                prepared, queue_queued, current, cancel_target_projection
            )
        return CtpExecutionDispatchResult(durable_binding, completed)

    def pump_callbacks(self, max_events: int = 32) -> tuple[Any, ...]:
        """Apply at most ``max_events`` persisted callback events on this thread."""

        self._assert_worker_thread()
        if type(max_events) is not int or not 1 <= max_events <= 256:
            raise ContractValidationError("callback pump bound must be an integer from 1 to 256")
        results: list[Any] = []
        for _ in range(max_events):
            result = self._reconciliation_port.apply_next_ingress()
            if result is None:
                break
            results.append(result)
        return tuple(results)

    def read_provider_observation(
        self,
        intent: Any,
        dispatch_binding: CtpManagedDispatchBinding,
    ) -> ProviderObservation | CtpOwnerCallbackObservationNotReadyV1:
        """Read provider state only from the trusted durable callback snapshot."""

        self._assert_worker_thread()
        return self._reconciliation_port.read_provider_observation(intent, dispatch_binding)


__all__ = ["CtpExecutionDispatchResult", "CtpExecutionOwner"]
