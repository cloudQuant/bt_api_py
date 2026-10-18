"""Trusted evidence for the current Store-backed CTP callback ingress path.

The verifier binds canonical mapper output to the exact unapplied raw record
read from the same Store and to the ACTIVE TraderClient source which installed
that Store adapter. It is not an exchange acknowledgement by itself; the Store
performs the final owner, lease, cursor, command, and event checks in its apply
transaction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from threading import current_thread
from typing import Any

from bt_api_execution import (
    ContractValidationError,
    CtpCallbackIngressEventV1,
    CtpCallbackSessionBindingV1,
    CtpCallbackSessionOwnerHandle,
    CtpCallbackSessionStoreAdapter,
    CtpDispatchCorrelationKey,
    CtpDispatchCallbackKey,
    CtpDispatchCommand,
    CtpNativeSessionContext,
    CtpVerifiedCallbackEvidence,
    CtpVerifiedTradeFactEvidence,
    ExecutionScope,
    InvalidStateTransition,
    SqliteExecutionStore,
    WriterLease,
    WriterLeaseUnavailable,
    canonical_json,
    map_ctp_native_order_action_callback,
    map_ctp_native_order_return,
    map_ctp_native_trade_fact_v2,
    payload_sha256,
)
from bt_api_execution.ctp_callback_session import (
    _flattened_fields,
    _named_arg,
    _source_session,
)
from bt_api_execution.ctp_native_callbacks import map_ctp_native_order_insert_callback
from bt_api_py.ctp_service_execution import CtpOwnerCallbackReconciliationPort


_VERIFIER_ID = "ctp.owner_ingress_evidence.v1"
_MAX_EVIDENCE_TTL_NS = 30_000_000_000
_ERROR_CODES = frozenset(
    {
        "CALLBACK_OWNER_UNAVAILABLE",
        "CALLBACK_SOURCE_MISMATCH",
        "CALLBACK_EVENT_UNAVAILABLE",
        "CALLBACK_PAYLOAD_MISMATCH",
        "CALLBACK_STATE_UNVERIFIABLE",
        "TRADE_SOURCE_MISMATCH",
        "TRADE_PAYLOAD_MISMATCH",
        "EVIDENCE_TTL_INVALID",
    }
)


class CtpCallbackEvidenceError(ContractValidationError):
    """Finite public rejection for an unavailable or mismatched CTP source."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_CODES:
            raise ValueError("unsupported CTP callback evidence error code")
        self.code = code
        super().__init__("CTP callback source evidence was rejected")


def _reject(code: str) -> None:
    raise CtpCallbackEvidenceError(code)


def _same_bound_method(method: Any, instance: Any, function: Any) -> bool:
    return getattr(method, "__self__", None) is instance and getattr(
        method, "__func__", None
    ) is function


class CtpSessionIngressEvidenceVerifierV1:
    """Verify callbacks and trades against one exact ACTIVE CTP source owner.

    This verifier has no native-call or provider authority. It only returns
    short-lived typed evidence for Store's existing atomic callback/trade apply
    methods. It is confined to the creating thread and never acquires the
    adapter's non-reentrant apply lock.
    """

    def __init__(
        self,
        store: SqliteExecutionStore,
        scope: ExecutionScope,
        writer_lease: WriterLease,
        session_adapter: CtpCallbackSessionStoreAdapter,
        native_trader_client: Any,
        *,
        session_generation_id: str,
        evidence_ttl_ns: int = 5_000_000_000,
    ) -> None:
        if type(store) is not SqliteExecutionStore:
            raise CtpCallbackEvidenceError("CALLBACK_OWNER_UNAVAILABLE")
        if (
            type(scope) is not ExecutionScope
            or scope.provider != "ctp"
            or scope.environment != "simulation"
            or not scope.trading_day
            or type(writer_lease) is not WriterLease
            or type(session_adapter) is not CtpCallbackSessionStoreAdapter
            or session_adapter.store is not store
            or session_adapter.scope != scope
            or session_adapter.writer_lease is not writer_lease
            or type(session_adapter.owner_handle) is not CtpCallbackSessionOwnerHandle
        ):
            raise CtpCallbackEvidenceError("CALLBACK_OWNER_UNAVAILABLE")
        if (
            type(session_generation_id) is not str
            or not session_generation_id
            or session_generation_id != session_generation_id.strip()
        ):
            raise CtpCallbackEvidenceError("CALLBACK_OWNER_UNAVAILABLE")
        if (
            type(evidence_ttl_ns) is not int
            or not 0 < evidence_ttl_ns <= _MAX_EVIDENCE_TTL_NS
        ):
            raise CtpCallbackEvidenceError("EVIDENCE_TTL_INVALID")

        # The composition already owns a TraderClient. Resolve its exact type
        # lazily here, without starting a client or creating a native API.
        from bt_api_ctp.ctp.client import TraderClient

        if type(native_trader_client) is not TraderClient:
            raise CtpCallbackEvidenceError("CALLBACK_OWNER_UNAVAILABLE")
        self._store = store
        self._scope = scope
        self._writer_lease = writer_lease
        self._session_adapter = session_adapter
        self._owner_handle = session_adapter.owner_handle
        self._trader = native_trader_client
        self._thread = current_thread()
        self._session_generation_id = session_generation_id
        self._evidence_ttl_ns = evidence_ttl_ns
        self._binding = self._read_current_binding()

    def _read_current_binding(self) -> CtpCallbackSessionBindingV1:
        if current_thread() is not self._thread:
            _reject("CALLBACK_OWNER_UNAVAILABLE")
        lock = getattr(self._trader, "_query_state_lock", None)
        if lock is None:
            _reject("CALLBACK_OWNER_UNAVAILABLE")
        with lock:
            try:
                self._store.assert_writer_lease(self._scope, self._writer_lease)
                facts = self._store.read_ctp_callback_session_context_facts(
                    self._owner_handle
                )
            except (WriterLeaseUnavailable, InvalidStateTransition):
                _reject("CALLBACK_OWNER_UNAVAILABLE")
            if (
                type(facts) is not tuple
                or len(facts) != 3
                or type(facts[0]) is not CtpCallbackSessionBindingV1
            ):
                _reject("CALLBACK_OWNER_UNAVAILABLE")
            binding = facts[0]
            if (
                binding.owner_intent_id != self._owner_handle.owner_intent_id
                or binding.account_key != self._scope.account_key
                or binding.scope_key != self._scope.key
                or binding.trading_day != self._scope.trading_day
                or binding.session_generation_id != self._session_generation_id
            ):
                _reject("CALLBACK_OWNER_UNAVAILABLE")

            with self._store._lock:
                if (
                    self._store._issued_ctp_callback_session_owners.get(
                        self._owner_handle.owner_intent_id
                    )
                    is not self._owner_handle
                    or self._store._issued_ctp_callback_session_adapters.get(
                        self._owner_handle.owner_intent_id
                    )
                    is not self._session_adapter
                    or self._store._active_ctp_callback_sessions.get(
                        self._owner_handle.owner_intent_id
                    )
                    != binding
                ):
                    _reject("CALLBACK_OWNER_UNAVAILABLE")
            self._assert_trader_source(binding)
            return binding

    def _assert_trader_source(self, binding: CtpCallbackSessionBindingV1) -> None:
        state = getattr(self._trader, "_callback_ingress", None)
        if (
            state is None
            or getattr(state, "owner_handle", None) is not self._owner_handle
            or getattr(state, "phase", None) != "ACTIVE"
            or getattr(state, "poisoned", None) is not False
            or getattr(state, "active_session", None) is None
            or getattr(state, "append_sink", None) is not self._session_adapter.sink
            or not _same_bound_method(
                getattr(state, "session_binder", None),
                self._session_adapter,
                type(self._session_adapter).bind_session,
            )
            or not _same_bound_method(
                getattr(state, "command_binding_verifier", None),
                self._session_adapter,
                type(self._session_adapter).verify_native_call,
            )
        ):
            _reject("CALLBACK_OWNER_UNAVAILABLE")

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
            _reject("CALLBACK_OWNER_UNAVAILABLE")

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
            _reject("CALLBACK_OWNER_UNAVAILABLE")
        api = getattr(self._trader, "_api", None)
        spi = getattr(self._trader, "_spi", None)
        if (
            api is None
            or spi is None
            or getattr(self._trader, "_session_native_api", None) is not api
            or getattr(spi, "_native_api", None) is not api
            or getattr(spi, "_callback_source_tags", None) is not tags
        ):
            _reject("CALLBACK_OWNER_UNAVAILABLE")

    def _assert_current(self) -> CtpCallbackSessionBindingV1:
        binding = self._read_current_binding()
        if binding != self._binding:
            _reject("CALLBACK_OWNER_UNAVAILABLE")
        return binding

    def _read_event(
        self, callback_name: str | None = None
    ) -> tuple[CtpCallbackIngressEventV1, dict[str, Any]]:
        try:
            event = self._store.read_next_ctp_callback_ingress(self._owner_handle)
        except InvalidStateTransition:
            _reject("CALLBACK_OWNER_UNAVAILABLE")
        if (
            type(event) is not CtpCallbackIngressEventV1
            or event.owner_handle is not self._owner_handle
            or event.callback_class != "ROUTEABLE"
            or event.source_phase != "ACTIVE"
            or (callback_name is not None and event.callback_name != callback_name)
        ):
            _reject("CALLBACK_EVENT_UNAVAILABLE")
        try:
            payload = json.loads(event.record_payload_json)
        except (TypeError, ValueError):
            _reject("CALLBACK_SOURCE_MISMATCH")
        binding = self._binding
        expected_tags = (
            binding.source_instance_id,
            binding.native_client_epoch,
            binding.native_api_source_id,
            binding.native_spi_source_id,
            binding.native_api_generation,
            binding.source_connection_generation,
        )
        if (
            type(payload) is not dict
            or canonical_json(payload) != event.record_payload_json
            or payload.get("schema") != "ctp_trader_callback_ingress.v2"
            or payload.get("owner_intent_id") != self._owner_handle.owner_intent_id
            or payload.get("callback_name") != event.callback_name
            or payload.get("callback_class") != "ROUTEABLE"
            or payload.get("phase") != "ACTIVE"
            or payload.get("sequence") != event.source_sequence
            or payload.get("digest") != event.record_digest_sha256
            or payload.get("capture_complete") is not True
            or payload.get("missing_getters") != []
            or event.source_tags != expected_tags
            or event.connection_generation != binding.connection_generation
            or payload.get("source_instance_id") != binding.source_instance_id
            or payload.get("native_client_epoch") != binding.native_client_epoch
            or payload.get("native_api_source_id") != binding.native_api_source_id
            or payload.get("native_spi_source_id") != binding.native_spi_source_id
            or payload.get("native_api_generation") != binding.native_api_generation
            or payload.get("source_connection_generation")
            != binding.source_connection_generation
            or payload.get("connection_generation") != binding.connection_generation
        ):
            _reject("CALLBACK_SOURCE_MISMATCH")
        return event, payload

    def _read_command_for_event(
        self, event: CtpCallbackIngressEventV1, command: CtpDispatchCommand
    ) -> tuple[CtpNativeSessionContext, dict[str, Any], dict[str, Any]]:
        if type(command) is not CtpDispatchCommand:
            _reject("CALLBACK_SOURCE_MISMATCH")
        try:
            correlated = self._store.find_ctp_dispatch_command_for_ingress(event)
            durable = self._store.read_ctp_dispatch_command(self._scope, command.command_id)
        except InvalidStateTransition:
            _reject("CALLBACK_SOURCE_MISMATCH")
        if correlated is None or correlated != command or durable != command:
            _reject("CALLBACK_SOURCE_MISMATCH")
        correlation = command.correlation_key
        native = command.native_request_payload
        if (
            type(correlation) is not CtpDispatchCorrelationKey
            or correlation.version != 2
            or correlation.operation != command.operation
            or command.account_key != self._scope.account_key
            or command.scope_key != self._scope.key
            or command.trading_day != self._scope.trading_day
            or command.session_binding_sha256 != self._binding.session_binding_sha256
            or correlation.account_key != self._scope.account_key
            or correlation.scope_key != self._scope.key
            or correlation.trading_day != self._scope.trading_day
            or correlation.session_generation_id != self._binding.session_generation_id
            or correlation.dispatch_front_id != self._binding.dispatch_front_id
            or correlation.dispatch_session_id != self._binding.dispatch_session_id
            or command.status not in {"COMPLETED", "UNKNOWN"}
            or command.local_queue_receipt_queued is not True
            or type(command.local_queue_receipt_id) is not str
            or not command.local_queue_receipt_id
            or not isinstance(native, Mapping)
            or command.native_request_payload_sha256 != payload_sha256(dict(native))
        ):
            _reject("CALLBACK_SOURCE_MISMATCH")
        session, binding = _source_session(self._session_adapter)
        if binding != self._binding or type(session) is not CtpNativeSessionContext:
            _reject("CALLBACK_OWNER_UNAVAILABLE")
        return session, dict(native), dict(command.request_payload)

    def _assert_request_identity(
        self,
        command: CtpDispatchCommand,
        native_payload: Mapping[str, Any],
        fields: Mapping[str, Any],
        source_callback: str,
        login_broker_id: str,
        login_user_id: str,
    ) -> None:
        correlation = command.correlation_key
        expected_order_ref = (
            correlation.order_ref
            if command.operation == "SUBMIT"
            else command.cancel_target_order_ref
            if command.operation == "CANCEL"
            else None
        )
        if (
            fields.get("BrokerID") != login_broker_id
            or fields.get("UserID") != login_user_id
            or fields.get("InstrumentID") != native_payload.get("InstrumentID")
            or fields.get("InvestorID") != native_payload.get("InvestorID")
            or fields.get("OrderRef") != expected_order_ref
        ):
            _reject("CALLBACK_SOURCE_MISMATCH")
        for name in ("BrokerID", "InvestorID", "UserID", "InstrumentID", "OrderRef"):
            if fields.get(name) != native_payload.get(name):
                _reject("CALLBACK_SOURCE_MISMATCH")
        exchange = fields.get("ExchangeID")
        if exchange is not None and exchange != native_payload.get("ExchangeID"):
            _reject("CALLBACK_SOURCE_MISMATCH")
        if source_callback != "OnRtnOrder" and exchange != native_payload.get("ExchangeID"):
            _reject("CALLBACK_SOURCE_MISMATCH")

    @staticmethod
    def _order_projection_state(
        command: CtpDispatchCommand, native_payload: Mapping[str, Any], fields: Mapping[str, Any]
    ) -> str:
        requested = native_payload.get("VolumeTotalOriginal")
        traded = fields.get("VolumeTraded")
        remaining = fields.get("VolumeTotal")
        status = fields.get("OrderStatus")
        submit_status = fields.get("OrderSubmitStatus")
        if (
            type(requested) is not int
            or requested <= 0
            or type(traded) is not int
            or type(remaining) is not int
            or traded < 0
            or remaining < 0
            or traded + remaining != requested
            or type(status) is not str
            or type(submit_status) is not str
        ):
            _reject("CALLBACK_STATE_UNVERIFIABLE")
        if status == "0" and submit_status == "3" and traded == requested and remaining == 0:
            return "FILLED"
        if (
            status == "3"
            and submit_status in {"1", "3", "5"}
            and traded == 0
            and remaining == requested
        ):
            return "ACKNOWLEDGED"
        if (
            status == "1"
            and submit_status in {"1", "3", "5"}
            and 0 < traded < requested
            and remaining > 0
        ):
            return "PARTIALLY_FILLED"
        if status == "5" and submit_status in {"1", "3"} and traded < requested:
            return "CANCELLED"
        if status == "4" and submit_status == "4" and traded == 0 and remaining == requested:
            return "REJECTED"
        _reject("CALLBACK_STATE_UNVERIFIABLE")

    def verify_callback(
        self,
        command: CtpDispatchCommand,
        callback: CtpDispatchCallbackKey,
        callback_payload: Mapping[str, Any],
        *,
        now_ns: int,
    ) -> CtpVerifiedCallbackEvidence:
        if type(callback) is not CtpDispatchCallbackKey or type(callback_payload) is not dict:
            _reject("CALLBACK_PAYLOAD_MISMATCH")
        binding = self._assert_current()
        event, raw = self._read_event()
        if callback_payload.get("source_callback") != event.callback_name:
            _reject("CALLBACK_PAYLOAD_MISMATCH")
        session, native_payload, _logical_payload = self._read_command_for_event(event, command)
        try:
            native_field = _flattened_fields(raw, 0)
            response_info = _flattened_fields(raw, 1)
            callback_name = event.callback_name
            if callback_name == "OnRtnOrder":
                envelope = map_ctp_native_order_return(command.correlation_key, session, native_field)
            elif callback_name in {"OnRspOrderInsert", "OnErrRtnOrderInsert"}:
                envelope = map_ctp_native_order_insert_callback(
                    command.correlation_key,
                    session,
                    callback_name,
                    native_field,
                    response_info if response_info else None,
                    request_id=_named_arg(raw, 2) if callback_name == "OnRspOrderInsert" else None,
                    is_last=_named_arg(raw, 3) if callback_name == "OnRspOrderInsert" else None,
                )
            elif callback_name in {"OnRspOrderAction", "OnErrRtnOrderAction"}:
                envelope = map_ctp_native_order_action_callback(
                    command.correlation_key,
                    session,
                    callback_name,
                    native_field,
                    response_info if response_info else None,
                    request_id=_named_arg(raw, 2) if callback_name == "OnRspOrderAction" else None,
                    is_last=_named_arg(raw, 3) if callback_name == "OnRspOrderAction" else None,
                )
            else:
                _reject("CALLBACK_EVENT_UNAVAILABLE")
        except CtpCallbackEvidenceError:
            raise
        except (ContractValidationError, KeyError, TypeError, ValueError):
            _reject("CALLBACK_SOURCE_MISMATCH")

        expected_payload = envelope.to_payload()
        expected_payload["ingress_source"] = {
            "owner_intent_id": self._owner_handle.owner_intent_id,
            "source_sequence": event.source_sequence,
            "record_digest_sha256": event.record_digest_sha256,
        }
        try:
            payload_matches = canonical_json(callback_payload) == canonical_json(
                expected_payload
            )
        except (TypeError, ValueError):
            _reject("CALLBACK_PAYLOAD_MISMATCH")
        if callback != envelope.callback_key or not payload_matches:
            _reject("CALLBACK_PAYLOAD_MISMATCH")

        flattened = dict(envelope.native_fields)
        facts = self._store.read_ctp_callback_session_context_facts(self._owner_handle)
        if type(facts) is not tuple or len(facts) != 3:
            _reject("CALLBACK_OWNER_UNAVAILABLE")
        login_broker_id, login_user_id = facts[1], facts[2]
        if type(login_broker_id) is not str or type(login_user_id) is not str:
            _reject("CALLBACK_OWNER_UNAVAILABLE")
        self._assert_request_identity(
            command,
            native_payload,
            flattened,
            event.callback_name,
            login_broker_id,
            login_user_id,
        )

        if callback.callback_family == "CANCEL_ACTION":
            error_id = envelope.response_error_id
            if type(error_id) is not int:
                _reject("CALLBACK_STATE_UNVERIFIABLE")
            if event.callback_name.startswith("OnErrRtn") and error_id == 0:
                _reject("CALLBACK_STATE_UNVERIFIABLE")
            state = "ACKNOWLEDGED" if error_id == 0 else "REJECTED"
        elif event.callback_name == "OnRtnOrder":
            state = self._order_projection_state(command, native_payload, flattened)
        else:
            error_id = envelope.response_error_id
            if type(error_id) is not int:
                _reject("CALLBACK_STATE_UNVERIFIABLE")
            if event.callback_name.startswith("OnErrRtn") and error_id == 0:
                _reject("CALLBACK_STATE_UNVERIFIABLE")
            state = "ACKNOWLEDGED" if error_id == 0 else "REJECTED"

        if type(now_ns) is not int or now_ns <= 0:
            _reject("CALLBACK_STATE_UNVERIFIABLE")
        self._assert_current()
        if self._store.read_next_ctp_callback_ingress(self._owner_handle) is not event:
            _reject("CALLBACK_EVENT_UNAVAILABLE")
        if self._store.read_ctp_dispatch_command(self._scope, command.command_id) != command:
            _reject("CALLBACK_SOURCE_MISMATCH")
        callback_payload_digest = payload_sha256(callback_payload)
        source_digest = payload_sha256(
            {
                "schema": "ctp_owner_callback_ingress_evidence.v1",
                "owner_intent_id": self._owner_handle.owner_intent_id,
                "account_key": self._scope.account_key,
                "scope_key": self._scope.key,
                "trading_day": self._scope.trading_day,
                "session_binding_sha256": binding.session_binding_sha256,
                "command_id": command.command_id,
                "command_binding_sha256": command.authority_binding_sha256,
                "source_sequence": event.source_sequence,
                "record_digest_sha256": event.record_digest_sha256,
                "source_tags": list(event.source_tags),
                "connection_generation": event.connection_generation,
                "callback_key": callback.to_payload(),
                "callback_payload_sha256": callback_payload_digest,
            }
        )
        return CtpVerifiedCallbackEvidence(
            evidence_type="ctp_verified_callback.v1",
            callback_key=callback,
            callback_payload_sha256=callback_payload_digest,
            projection_state=state,
            source_digest_sha256=source_digest,
            verifier_id=_VERIFIER_ID,
            verified_at_ns=now_ns,
            expires_at_ns=now_ns + self._evidence_ttl_ns,
        )

    def verify_trade_fact(
        self,
        command: CtpDispatchCommand,
        trade_fact: Any,
        event: CtpCallbackIngressEventV1,
        *,
        now_ns: int,
    ) -> CtpVerifiedTradeFactEvidence:
        binding = self._assert_current()
        if (
            type(event) is not CtpCallbackIngressEventV1
            or event.owner_handle is not self._owner_handle
            or event.callback_name != "OnRtnTrade"
        ):
            _reject("TRADE_SOURCE_MISMATCH")
        current, raw = self._read_event("OnRtnTrade")
        if current is not event:
            _reject("TRADE_SOURCE_MISMATCH")
        _session, _native_payload, _logical_payload = self._read_command_for_event(
            event, command
        )
        if command.operation != "SUBMIT" or command.correlation_key is None:
            _reject("TRADE_SOURCE_MISMATCH")
        try:
            native_field = _flattened_fields(raw, 0)
            session, session_binding = _source_session(self._session_adapter)
            expected_fact = map_ctp_native_trade_fact_v2(session, native_field)
        except (ContractValidationError, KeyError, TypeError, ValueError):
            _reject("TRADE_SOURCE_MISMATCH")
        try:
            fact_matches = canonical_json(trade_fact.to_payload()) == canonical_json(
                expected_fact.to_payload()
            )
        except (AttributeError, TypeError, ValueError):
            fact_matches = False
        if (
            session_binding != binding
            or type(trade_fact) is not type(expected_fact)
            or not fact_matches
        ):
            _reject("TRADE_PAYLOAD_MISMATCH")
        facts = self._store.read_ctp_callback_session_context_facts(self._owner_handle)
        if (
            type(facts) is not tuple
            or len(facts) != 3
            or dict(trade_fact.native_fields).get("BrokerID") != facts[1]
            or dict(trade_fact.native_fields).get("UserID") != facts[2]
        ):
            _reject("TRADE_SOURCE_MISMATCH")
        if type(now_ns) is not int or now_ns <= 0:
            _reject("EVIDENCE_TTL_INVALID")
        self._assert_current()
        if self._store.read_next_ctp_callback_ingress(self._owner_handle) is not event:
            _reject("TRADE_SOURCE_MISMATCH")
        if self._store.read_ctp_dispatch_command(self._scope, command.command_id) != command:
            _reject("TRADE_SOURCE_MISMATCH")
        fact_payload = trade_fact.to_payload()
        fact_digest = payload_sha256(fact_payload)
        source_digest = payload_sha256(
            {
                "schema": "ctp_owner_trade_ingress_evidence.v1",
                "owner_intent_id": self._owner_handle.owner_intent_id,
                "account_key": self._scope.account_key,
                "scope_key": self._scope.key,
                "trading_day": self._scope.trading_day,
                "session_binding_sha256": binding.session_binding_sha256,
                "command_id": command.command_id,
                "command_binding_sha256": command.authority_binding_sha256,
                "source_sequence": event.source_sequence,
                "record_digest_sha256": event.record_digest_sha256,
                "source_tags": list(event.source_tags),
                "connection_generation": event.connection_generation,
                "event_id": trade_fact.event_id,
                "trade_fact_sha256": fact_digest,
            }
        )
        return CtpVerifiedTradeFactEvidence(
            evidence_type="ctp_verified_trade_fact.v2",
            owner_intent_id=self._owner_handle.owner_intent_id,
            source_sequence=event.source_sequence,
            ingress_record_digest_sha256=event.record_digest_sha256,
            event_id=trade_fact.event_id,
            trade_fact_sha256=fact_digest,
            source_digest_sha256=source_digest,
            verifier_id=_VERIFIER_ID,
            verified_at_ns=now_ns,
            expires_at_ns=now_ns + self._evidence_ttl_ns,
        )


def create_ctp_owner_callback_reconciliation_port(
    store: SqliteExecutionStore,
    scope: ExecutionScope,
    writer_lease: WriterLease,
    session_adapter: CtpCallbackSessionStoreAdapter,
    native_trader_client: Any,
    *,
    session_generation_id: str,
    evidence_ttl_ns: int = 5_000_000_000,
) -> CtpOwnerCallbackReconciliationPort:
    """Bind the production session adapter to its exact live TraderClient source."""

    verifier = CtpSessionIngressEvidenceVerifierV1(
        store,
        scope,
        writer_lease,
        session_adapter,
        native_trader_client,
        session_generation_id=session_generation_id,
        evidence_ttl_ns=evidence_ttl_ns,
    )
    return CtpOwnerCallbackReconciliationPort(
        store,
        scope,
        writer_lease,
        session_adapter,
        session_generation_id=session_generation_id,
        callback_verifier=verifier,
        trade_fact_verifier=verifier,
    )


__all__ = [
    "CtpCallbackEvidenceError",
    "CtpSessionIngressEvidenceVerifierV1",
    "create_ctp_owner_callback_reconciliation_port",
]
