"""Issue one CTP cancel-target projection from a fenced native order query.

The evaluator borrows an already-active :class:`CtpExecutionOwner`; it does not
own or change that client's lifecycle.  A projection is issued only when the
same TraderClient's current query result matches the Store's bounded, applied
callback window and the durable SUBMIT/reservation/session facts.  It is a
cancel-target observation, not an account snapshot or dispatch authority.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

from bt_api_execution import (
    ContractValidationError,
    CtpCallbackSessionBindingV1,
    CtpOrderIdentityReservation,
    CtpOrderTargetQueryFence,
    CtpOrderTargetQueryWindow,
    CtpVerifiedOrderTargetProjection,
    ExecutionScope,
    OrderIntent,
    OrderType,
    PositionEffect,
    Side,
    TimeInForce,
    canonical_json,
    payload_sha256,
)
from bt_api_execution.ctp_single_worker_candidate import stable_managed_command_id
from bt_api_ctp.query import QueryResult
from bt_api_ctp.containers.ctp.ctp_native_query_certificate import (
    CtpNativeQueryCertificateError,
    _query_records_digest,
    _query_result_shape,
    _record_value,
    _same_scope,
    _scope_for_client,
    _source_for_result,
)
from bt_api_py.ctp_execution_owner import CtpExecutionOwner


_VERIFIER_ID = "bt_api_py.ctp_order_target_query.v1"
_SAFE_ORDER_FIELDS = (
    "BrokerID",
    "InvestorID",
    "UserID",
    "InstrumentID",
    "ExchangeID",
    "OrderRef",
    "OrderSysID",
    "RequestID",
    "FrontID",
    "SessionID",
    "TradingDay",
    "SequenceNo",
    "NotifySequence",
    "Direction",
    "CombOffsetFlag",
    "CombHedgeFlag",
    "OrderStatus",
    "OrderSubmitStatus",
    "VolumeTotalOriginal",
    "VolumeTotal",
    "VolumeTraded",
    "LimitPrice",
)
_DIRECTION = {Side.BUY: "0", Side.SELL: "1"}
_OFFSET = {
    (PositionEffect.OPEN, "ALL"): "0",
    (PositionEffect.CLOSE, "ALL"): "1",
    (PositionEffect.CLOSE, "TODAY"): "3",
    (PositionEffect.CLOSE, "YESTERDAY"): "4",
    (PositionEffect.CLOSE_TODAY, "TODAY"): "3",
    (PositionEffect.CLOSE_YESTERDAY, "YESTERDAY"): "4",
}
_MAX_TIMEOUT_SECONDS = 20.0
_MAX_PROJECTION_TTL_NS = 5_000_000_000


@dataclass(frozen=True, slots=True)
class _QueryAttempt:
    scope: ExecutionScope
    intent: OrderIntent
    reservation: CtpOrderIdentityReservation
    command: Any
    binding: CtpCallbackSessionBindingV1
    broker_id: str
    user_id: str
    query_scope: Any
    fence: CtpOrderTargetQueryFence
    evidence: QueryResult[Any]
    query_window: CtpOrderTargetQueryWindow
    instrument_id: str
    exchange_id: str
    order_sys_id: str


def _reject(message: str, *, cause: BaseException | None = None) -> None:
    error = ContractValidationError(message)
    if cause is None:
        raise error
    raise error from cause


def _ctp_text(value: object, name: str, limit: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isascii()
        or len(value.encode("ascii")) > limit
        or any(ord(char) < 0x21 or ord(char) > 0x7E for char in value)
    ):
        _reject(f"invalid CTP {name}")
    return value


def _exact_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _reject(f"invalid CTP {name}")
    return value


def _order_offset(intent: OrderIntent, exchange_id: str) -> str:
    if (
        exchange_id in {"SHFE", "INE"}
        and intent.position_effect is PositionEffect.CLOSE
        and intent.position_bucket == "ALL"
    ):
        _reject("SHFE/INE close query requires an explicit position bucket")
    offset = _OFFSET.get((intent.position_effect, intent.position_bucket))
    if offset is None:
        _reject("unsupported CTP position effect/bucket")
    return offset


def _intent_native_payload(
    intent: OrderIntent,
    reservation: CtpOrderIdentityReservation,
    binding: CtpCallbackSessionBindingV1,
    broker_id: str,
    user_id: str,
    exchange_id: str,
) -> dict[str, Any]:
    if (
        type(intent) is not OrderIntent
        or intent.scope.provider != "ctp"
        or intent.scope.environment != "simulation"
        or intent.position_side != "NET"
        or intent.tags
        or intent.order_type is not OrderType.LIMIT
        or intent.time_in_force is not TimeInForce.DAY
        or intent.price is None
        or type(intent.price) is not Decimal
        or not intent.price.is_finite()
        or intent.price <= 0
        or type(intent.quantity) is not Decimal
        or not intent.quantity.is_finite()
        or intent.quantity <= 0
        or intent.quantity > Decimal(2_147_483_647)
        or intent.quantity != Decimal(int(intent.quantity))
    ):
        _reject("CTP order query requires the exact supported LIMIT/DAY intent")
    if intent.reduce_only is not (intent.position_effect is not PositionEffect.OPEN):
        _reject("CTP intent effect and reduce-only flag disagree")
    if reservation.managed_intent_id != intent.intent_id:
        _reject("CTP identity reservation differs from managed intent")
    if (
        reservation.account_key != intent.scope.account_key
        or reservation.scope_key != intent.scope.key
        or reservation.trading_day != intent.scope.trading_day
        or binding.account_key != reservation.account_key
        or binding.scope_key != reservation.scope_key
        or binding.trading_day != reservation.trading_day
    ):
        _reject("CTP intent reservation differs from active session scope")
    direction = _DIRECTION.get(intent.side)
    if direction is None:
        _reject("unsupported CTP side")
    native_price = float(intent.price)
    if (
        not math.isfinite(native_price)
        or native_price <= 0
        or Decimal(str(native_price)) != intent.price
    ):
        _reject("CTP LIMIT price is not exactly representable as a native double")
    return {
        "BrokerID": broker_id,
        "InvestorID": user_id,
        "UserID": user_id,
        "InstrumentID": intent.instrument,
        "ExchangeID": exchange_id,
        "OrderRef": reservation.order_ref,
        "Direction": direction,
        "CombOffsetFlag": _order_offset(intent, exchange_id),
        "CombHedgeFlag": "1",
        "VolumeTotalOriginal": int(intent.quantity),
        "MinVolume": 1,
        "ForceCloseReason": "0",
        "IsAutoSuspend": 0,
        "UserForceClose": 0,
        "ContingentCondition": "1",
        "LimitPrice": native_price,
        "OrderPriceType": "2",
        "TimeCondition": "3",
        "VolumeCondition": "1",
    }


def _reservation_payload(reservation: CtpOrderIdentityReservation) -> dict[str, Any]:
    return {
        "account_key": reservation.account_key,
        "trading_day": reservation.trading_day,
        "scope_key": reservation.scope_key,
        "managed_intent_id": reservation.managed_intent_id,
        "runtime_order_id": reservation.runtime_order_id,
        "order_ref": reservation.order_ref,
        "created_at_ns": reservation.created_at_ns,
    }


def _source_tags_payload(binding: CtpCallbackSessionBindingV1) -> tuple[Any, ...]:
    return (
        binding.source_instance_id,
        binding.native_client_epoch,
        binding.native_api_source_id,
        binding.native_spi_source_id,
        binding.native_api_generation,
        binding.source_connection_generation,
    )


def _payload_fields(payload: Mapping[str, Any], slot: int) -> dict[str, dict[str, Any]]:
    flattened = payload.get("flattened_fields")
    if type(flattened) is not list:
        _reject("CTP callback field snapshot is invalid")
    found: dict[str, dict[str, Any]] = {}
    for item in flattened:
        if type(item) is not dict or item.get("argument_slot") != slot:
            continue
        name = item.get("field_name")
        if type(name) is not str or not name or name in found:
            _reject("CTP callback field snapshot is ambiguous")
        found[name] = item
    return found


def _validate_named_args(payload: Mapping[str, Any], request_id: int, *, is_last: bool) -> None:
    args = payload.get("named_args")
    if type(args) is not list or len(args) != 4:
        _reject("CTP query callback argument snapshot is invalid")
    expected_names = ("pOrder", "pRspInfo", "nRequestID", "bIsLast")
    for slot, (item, expected_name) in enumerate(zip(args, expected_names, strict=True)):
        if (
            type(item) is not dict
            or item.get("argument_slot") != slot
            or item.get("name") != expected_name
        ):
            _reject("CTP query callback argument binding is invalid")
    if args[2].get("present") is not True or args[2].get("scalar_captured") is not True:
        _reject("CTP query callback request ID was not captured")
    if type(args[2].get("value")) is not int or args[2]["value"] != request_id:
        _reject("CTP query callback request ID differs")
    if args[3].get("present") is not True or args[3].get("scalar_captured") is not True:
        _reject("CTP query callback terminal flag was not captured")
    if type(args[3].get("value")) is not bool or args[3]["value"] is not is_last:
        _reject("CTP query callback terminal sequence is invalid")
    if (
        args[0].get("present") is not True
        or args[0].get("scalar_captured") is not False
        or args[0].get("value") is not None
        or args[1].get("present") is not True
        or args[1].get("scalar_captured") is not False
        or args[1].get("value") is not None
    ):
        _reject("CTP query callback pointer arguments are invalid")


class CtpOrderTargetQueryEvaluator:
    """Borrow one exact active owner to issue a fresh order-target projection."""

    def __init__(self, owner: CtpExecutionOwner) -> None:
        if type(owner) is not CtpExecutionOwner:
            _reject("exact borrowed CTP execution owner is required")
        owner._assert_worker_thread()
        binding = owner._assert_active_owner()
        self._owner = owner
        self._store = owner._store
        self._scope = owner._scope
        self._writer_lease = owner._writer_lease
        self._owner_handle = owner._owner_handle
        self._thread = threading.current_thread()
        self._initial_binding = binding
        self._busy = False
        self._attempt: _QueryAttempt | None = None

    def _assert_thread_and_owner(self) -> CtpCallbackSessionBindingV1:
        if threading.current_thread() is not self._thread:
            _reject("CTP order-query evaluator is confined to its creating thread")
        self._owner._assert_worker_thread()
        binding = self._owner._assert_active_owner()
        if binding != self._initial_binding:
            _reject("borrowed CTP session binding changed")
        return binding

    def _read_preflight(
        self, managed_intent_id: str, instrument_id: str, exchange_id: str, order_sys_id: str
    ) -> tuple[
        OrderIntent,
        CtpOrderIdentityReservation,
        Any,
        CtpCallbackSessionBindingV1,
        str,
        str,
        Any,
        dict[str, Any],
    ]:
        _ctp_text(managed_intent_id, "managed intent ID", 128)
        instrument_id = _ctp_text(instrument_id, "InstrumentID", 80)
        exchange_id = _ctp_text(exchange_id, "ExchangeID", 8)
        order_sys_id = _ctp_text(order_sys_id, "OrderSysID", 20)
        binding = self._assert_thread_and_owner()
        intent = self._store.get_intent(managed_intent_id, scope=self._scope)
        reservation = self._store.read_ctp_order_identity(self._scope, managed_intent_id)
        if type(intent) is not OrderIntent or type(reservation) is not CtpOrderIdentityReservation:
            _reject("durable CTP intent and OrderRef reservation are required")
        if intent.scope != self._scope or instrument_id != intent.instrument:
            _reject("CTP query filters differ from the exact durable intent")
        facts = self._store.read_ctp_callback_session_context_facts(self._owner_handle)
        if type(facts) is not tuple or len(facts) != 3 or facts[0] != binding:
            _reject("Store CTP login facts differ from the borrowed owner")
        broker_id, user_id = facts[1], facts[2]
        if type(broker_id) is not str or type(user_id) is not str or not broker_id or not user_id:
            _reject("Store CTP login identity is incomplete")
        if binding.trading_day != self._scope.trading_day:
            _reject("CTP trading day differs from the borrowed scope")

        command_id = stable_managed_command_id(
            "submit", managed_intent_id, reservation.runtime_order_id
        )
        command = self._store.read_ctp_dispatch_command(self._scope, command_id)
        if command is None:
            _reject("durable managed CTP SUBMIT command is required")
        if (
            command.command_id != command_id
            or command.operation != "SUBMIT"
            or command.account_key != self._scope.account_key
            or command.scope_key != self._scope.key
            or command.trading_day != self._scope.trading_day
            or command.reservation_managed_intent_id != managed_intent_id
            or command.order_ref != reservation.order_ref
            or command.status not in {"COMPLETED", "UNKNOWN"}
            or command.local_queue_receipt_queued is not True
            or type(command.local_queue_receipt_id) is not str
            or command.native_request_payload is None
            or command.native_request_payload_sha256 is None
            or command.session_binding_sha256 != binding.session_binding_sha256
        ):
            _reject("durable CTP SUBMIT command is not an exact sent/uncertain reservation")
        if payload_sha256(dict(command.native_request_payload)) != command.native_request_payload_sha256:
            _reject("durable CTP native request digest differs")
        expected_native = _intent_native_payload(
            intent, reservation, binding, broker_id, user_id, exchange_id
        )
        if dict(command.native_request_payload) != expected_native:
            _reject("query target differs from the durable CTP native SUBMIT")
        if order_sys_id == "":
            _reject("exact exchange OrderSysID is required")
        query_scope = _scope_for_client(self._owner._trader)
        expected_fingerprint = sha256(f"{broker_id}:{user_id}".encode("ascii")).hexdigest()[:16]
        if (
            not query_scope.read_only_ready
            or query_scope.account_fingerprint != expected_fingerprint
            or query_scope.connection_generation != binding.connection_generation
            or query_scope.trading_day != binding.trading_day
            or query_scope.broker_id != broker_id
            or query_scope.investor_id != user_id
        ):
            _reject("TraderClient query session differs from Store login facts")
        return (
            intent,
            reservation,
            command,
            binding,
            broker_id,
            user_id,
            query_scope,
            {"instrument_id": instrument_id, "exchange_id": exchange_id, "order_sys_id": order_sys_id},
        )

    @staticmethod
    def _validate_fence(
        fence: CtpOrderTargetQueryFence,
        owner: CtpExecutionOwner,
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        binding: CtpCallbackSessionBindingV1,
    ) -> None:
        if (
            type(fence) is not CtpOrderTargetQueryFence
            or fence.owner_handle is not owner._owner_handle
            or fence.account_key != owner._scope.account_key
            or fence.scope_key != owner._scope.key
            or fence.trading_day != owner._scope.trading_day
            or fence.managed_intent_id != intent.intent_id
            or fence.runtime_order_id != reservation.runtime_order_id
            or fence.order_ref != reservation.order_ref
            or fence.writer_owner_id != owner._writer_lease.owner_id
            or fence.writer_fencing_token != owner._writer_lease.fencing_token
            or fence.session_binding_sha256 != binding.session_binding_sha256
            or fence.session_generation_id != binding.session_generation_id
            or fence.connection_generation != binding.connection_generation
            or fence.dispatch_front_id != binding.dispatch_front_id
            or fence.dispatch_session_id != binding.dispatch_session_id
            or fence.source_tags != _source_tags_payload(binding)
            or fence.start_source_sequence != fence.start_applied_sequence
        ):
            _reject("Store-issued CTP query fence differs from the exact owner/session")

    @staticmethod
    def _validate_query_source(
        result: QueryResult[Any],
        query_scope: Any,
        fence: CtpOrderTargetQueryFence,
        *,
        broker_id: str,
        user_id: str,
        instrument_id: str,
        exchange_id: str,
        order_sys_id: str,
        now_monotonic: float,
        now_utc: datetime,
    ) -> Any:
        if type(result) is not QueryResult:
            _reject("exact typed CTP QueryResult is required")
        try:
            _query_result_shape(result)
            source = _source_for_result(
                result,
                query_scope,
                now_utc=now_utc,
                now_monotonic=now_monotonic,
            )
        except CtpNativeQueryCertificateError as error:
            _reject("CTP order query source is not trusted", cause=error)
        expected_filters = tuple(
            sorted(
                {
                    "BrokerID": broker_id,
                    "ExchangeID": exchange_id,
                    "InstrumentID": instrument_id,
                    "InvestorID": user_id,
                    "OrderSysID": order_sys_id,
                }.items()
            )
        )
        if (
            result.request_type != "orders"
            or type(result.request_id) is not int
            or not 0 < result.request_id <= 2_147_483_647
            or result.connection_generation != fence.connection_generation
            or result.account_fingerprint != query_scope.account_fingerprint
            or result.complete is not True
            or result.is_last_seen is not True
            or result.timed_out is not False
            or result.unsupported is not False
            or result.submit_code != 0
            or result.error_code not in (None, 0)
            or result.error_message != ""
            or result.late_callback_count != 0
            or type(result.records) is not tuple
            or len(result.records) != 1
            or source.request_intent_filters != expected_filters
            or source.request_filters != expected_filters
            or source.request_intent_parameters != ()
            or source.request_parameters != ()
            or source.explicit_request_filters != ()
            or source.started_monotonic < fence.created_at_ns / 1_000_000_000
            or source.completed_monotonic * 1_000_000_000 > fence.expires_at_ns
        ):
            _reject("CTP order query completion or exact filters are invalid")
        try:
            if _query_records_digest(result.records) != source.records_sha256:
                _reject("CTP order query records differ from their source digest")
        except (TypeError, ValueError) as error:
            _reject("CTP order query records are not canonical", cause=error)
        return source

    @staticmethod
    def _validated_query_row(
        result: QueryResult[Any],
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        binding: CtpCallbackSessionBindingV1,
        broker_id: str,
        user_id: str,
        exchange_id: str,
        order_sys_id: str,
    ) -> tuple[dict[str, Any], str, int, int, int]:
        record = result.records[0]
        row: dict[str, Any] = {}
        for name in _SAFE_ORDER_FIELDS:
            present, value = _record_value(record, name)
            if not present:
                _reject(f"CTP query row is missing {name}")
            row[name] = value
        expected_native = _intent_native_payload(
            intent, reservation, binding, broker_id, user_id, exchange_id
        )
        exact = {
            "BrokerID": broker_id,
            "InvestorID": user_id,
            "UserID": user_id,
            "InstrumentID": intent.instrument,
            "ExchangeID": exchange_id,
            "OrderRef": reservation.order_ref,
            "OrderSysID": order_sys_id,
            "FrontID": binding.dispatch_front_id,
            "SessionID": binding.dispatch_session_id,
            "TradingDay": binding.trading_day,
            "Direction": expected_native["Direction"],
            "CombOffsetFlag": expected_native["CombOffsetFlag"],
            "CombHedgeFlag": "1",
            "OrderSubmitStatus": "3",
            "VolumeTotalOriginal": int(intent.quantity),
        }
        if any(type(row[name]) is not type(value) or row[name] != value for name, value in exact.items()):
            _reject("CTP query row differs from the reserved native order identity")
        if type(row["RequestID"]) is not int or row["RequestID"] < 0:
            _reject("CTP native order RequestID is invalid")
        for name in ("SequenceNo", "NotifySequence"):
            _exact_int(row[name], name)
        quantity = int(intent.quantity)
        traded = _exact_int(row["VolumeTraded"], "VolumeTraded")
        remaining = _exact_int(row["VolumeTotal"], "VolumeTotal")
        if traded >= quantity or remaining != quantity - traded:
            _reject("CTP query row open quantity is inconsistent")
        price = row["LimitPrice"]
        if (
            type(price) is not float
            or not math.isfinite(price)
            or price <= 0
            or Decimal(str(price)) != intent.price
        ):
            _reject("CTP query row LIMIT price differs from the durable intent")
        status = row["OrderStatus"]
        if status == "3" and traded == 0:
            provider_state = "OPEN"
        elif status == "1" and traded > 0:
            provider_state = "PARTIAL"
        else:
            _reject("CTP query row does not prove a live OPEN/PARTIAL order")
        return row, provider_state, quantity, traded, remaining

    @staticmethod
    def _validate_window_row(
        window: CtpOrderTargetQueryWindow,
        fence: CtpOrderTargetQueryFence,
        result: QueryResult[Any],
        row: Mapping[str, Any],
    ) -> None:
        if (
            type(window) is not CtpOrderTargetQueryWindow
            or window.fence_id != fence.fence_id
            or window.owner_handle is not fence.owner_handle
            or window.query_request_id != result.request_id
            or window.start_source_sequence != fence.start_source_sequence
            or window.applied_source_sequence != window.end_source_sequence
            or window.observed_at_ns < fence.created_at_ns
            or window.observed_at_ns >= fence.expires_at_ns
            or len(window.events) != 2
        ):
            _reject("CTP query callback window is not one exact bounded row plus terminal")
        for index, event in enumerate(window.events):
            if (
                event.owner_handle is not fence.owner_handle
                or event.source_sequence != fence.start_source_sequence + index + 1
                or event.callback_name != "OnRspQryOrder"
                or event.callback_class != "AUDIT_QUERY"
                or event.source_phase != "ACTIVE"
                or event.source_tags != fence.source_tags
                or event.connection_generation != fence.connection_generation
            ):
                _reject("CTP query callback source differs from the Store fence")
            try:
                payload = json.loads(event.record_payload_json)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                _reject("CTP query callback payload is unreadable", cause=error)
            if (
                type(payload) is not dict
                or canonical_json(payload) != event.record_payload_json
                or payload.get("schema") != "ctp_trader_callback_ingress.v2"
                or payload.get("owner_intent_id") != fence.owner_handle.owner_intent_id
                or payload.get("callback_name") != "OnRspQryOrder"
                or payload.get("callback_class") != "AUDIT_QUERY"
                or payload.get("phase") != "ACTIVE"
                or payload.get("sequence") != event.source_sequence
                or payload.get("digest") != event.record_digest_sha256
                or payload.get("capture_complete") is not True
                or payload.get("missing_getters") != []
                or payload.get("connection_generation") != fence.connection_generation
                or payload.get("source_instance_id") != fence.source_tags[0]
                or payload.get("native_client_epoch") != fence.source_tags[1]
                or payload.get("native_api_source_id") != fence.source_tags[2]
                or payload.get("native_spi_source_id") != fence.source_tags[3]
                or payload.get("native_api_generation") != fence.source_tags[4]
                or payload.get("source_connection_generation") != fence.source_tags[5]
            ):
                _reject("CTP callback payload differs from its Store source receipt")
            _validate_named_args(payload, result.request_id, is_last=index == 1)
            if payload.get("flattened_fields") is None:
                _reject("CTP query callback lacks flattened field proof")
            order_fields = _payload_fields(payload, 0)
            response_fields = _payload_fields(payload, 1)
            if set(response_fields) != {"ErrorID"}:
                _reject("CTP query response info is incomplete")
            error_field = response_fields["ErrorID"]
            if (
                error_field.get("present") is not True
                or error_field.get("scalar_captured") is not True
                or type(error_field.get("value")) is not int
                or error_field.get("value") != 0
            ):
                _reject("CTP query callback reports an error")
            if index == 0:
                if set(order_fields) != set(_SAFE_ORDER_FIELDS):
                    _reject("CTP query callback order field set is incomplete")
                for name in _SAFE_ORDER_FIELDS:
                    item = order_fields[name]
                    value = item.get("value")
                    if (
                        item.get("present") is not True
                        or item.get("scalar_captured") is not True
                        or type(value) is not type(row[name])
                        or value != row[name]
                    ):
                        _reject("CTP native query result differs from its applied callback row")
            elif set(order_fields) != set(_SAFE_ORDER_FIELDS) or any(
                item.get("present") is not False
                or item.get("scalar_captured") is not False
                or item.get("value") is not None
                for item in order_fields.values()
            ):
                _reject("CTP query terminal callback unexpectedly contains an order row")

    def query_and_issue(
        self,
        managed_intent_id: str,
        *,
        instrument_id: str,
        exchange_id: str,
        order_sys_id: str,
        timeout: float = 5.0,
    ) -> Any:
        """Run one exact filtered native query and issue a same-Store projection.

        Failures are terminal for this query attempt.  No order, query retry,
        synthetic callback, account snapshot, or cancel authority is created.
        """

        if threading.current_thread() is not self._thread:
            _reject("CTP order-query evaluator is confined to its creating thread")
        if self._busy:
            _reject("CTP order-query evaluator does not permit reentry")
        if (
            type(timeout) not in (int, float)
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > _MAX_TIMEOUT_SECONDS
        ):
            _reject("CTP order-query timeout is outside the bounded range")
        self._busy = True
        self._attempt = None
        try:
            (
                intent,
                reservation,
                command,
                binding,
                broker_id,
                user_id,
                query_scope,
                filters,
            ) = self._read_preflight(
                managed_intent_id, instrument_id, exchange_id, order_sys_id
            )
            fence = self._store.begin_ctp_order_target_query(
                self._scope,
                managed_intent_id,
                owner_handle=self._owner_handle,
                writer_lease=self._writer_lease,
            )
            self._validate_fence(fence, self._owner, intent, reservation, binding)
            result = self._owner._trader.query_orders_result(
                instrument_id=filters["instrument_id"],
                exchange_id=filters["exchange_id"],
                order_sys_id=filters["order_sys_id"],
                timeout=float(timeout),
            )
            self._owner.pump_callbacks(256)
            self._assert_thread_and_owner()
            now_mono = time.monotonic()
            now_utc = datetime.now(timezone.utc)
            source = self._validate_query_source(
                result,
                query_scope,
                fence,
                broker_id=broker_id,
                user_id=user_id,
                instrument_id=filters["instrument_id"],
                exchange_id=filters["exchange_id"],
                order_sys_id=filters["order_sys_id"],
                now_monotonic=now_mono,
                now_utc=now_utc,
            )
            row, _state, _quantity, _traded, _remaining = self._validated_query_row(
                result,
                intent,
                reservation,
                binding,
                broker_id,
                user_id,
                filters["exchange_id"],
                filters["order_sys_id"],
            )
            if (
                source.completed_monotonic * 1_000_000_000 > time.monotonic_ns()
                or source.completed_monotonic > now_mono
            ):
                _reject("CTP query completion time is in the future")
            window = self._store.read_ctp_order_target_query_window(
                self._scope,
                fence,
                query_request_id=result.request_id,
                owner_handle=self._owner_handle,
                writer_lease=self._writer_lease,
            )
            self._validate_window_row(window, fence, result, row)
            self._attempt = _QueryAttempt(
                scope=self._scope,
                intent=intent,
                reservation=reservation,
                command=command,
                binding=binding,
                broker_id=broker_id,
                user_id=user_id,
                query_scope=query_scope,
                fence=fence,
                evidence=result,
                query_window=window,
                instrument_id=filters["instrument_id"],
                exchange_id=filters["exchange_id"],
                order_sys_id=filters["order_sys_id"],
            )
            return self._store.issue_ctp_order_target_projection(
                self._scope,
                managed_intent_id,
                result,
                verifier=self,
                owner_handle=self._owner_handle,
                writer_lease=self._writer_lease,
                query_fence=fence,
                query_window=window,
            )
        finally:
            self._attempt = None
            self._busy = False

    def verify_order_target(
        self,
        scope: ExecutionScope,
        reservation: CtpOrderIdentityReservation,
        native_query_evidence: Any,
        *,
        now_ns: int,
        query_window: CtpOrderTargetQueryWindow | None = None,
    ) -> CtpVerifiedOrderTargetProjection:
        """Store verifier callback; accepts only the live exact query attempt."""

        attempt = self._attempt
        if (
            threading.current_thread() is not self._thread
            or not self._busy
            or attempt is None
            or scope != attempt.scope
            or reservation != attempt.reservation
            or native_query_evidence is not attempt.evidence
            or query_window is not attempt.query_window
            or type(now_ns) is not int
            or now_ns <= 0
        ):
            _reject("CTP target verifier requires the exact live Store query attempt")
        binding = self._assert_thread_and_owner()
        if binding != attempt.binding:
            _reject("CTP session changed before query projection")
        current_scope = _scope_for_client(self._owner._trader)
        if not _same_scope(attempt.query_scope, current_scope):
            _reject("TraderClient query session changed during order query")
        current = self._owner._trader.get_query_result(attempt.evidence.request_id)
        if type(current) is not QueryResult:
            _reject("current CTP order-query readback is unavailable")
        now_utc = datetime.now(timezone.utc)
        now_monotonic = time.monotonic()
        original_source = self._validate_query_source(
            attempt.evidence,
            attempt.query_scope,
            attempt.fence,
            broker_id=attempt.broker_id,
            user_id=attempt.user_id,
            instrument_id=attempt.instrument_id,
            exchange_id=attempt.exchange_id,
            order_sys_id=attempt.order_sys_id,
            now_monotonic=now_monotonic,
            now_utc=now_utc,
        )
        current_source = self._validate_query_source(
            current,
            current_scope,
            attempt.fence,
            broker_id=attempt.broker_id,
            user_id=attempt.user_id,
            instrument_id=attempt.instrument_id,
            exchange_id=attempt.exchange_id,
            order_sys_id=attempt.order_sys_id,
            now_monotonic=now_monotonic,
            now_utc=now_utc,
        )
        if (
            current_source != original_source
            or current.request_id != attempt.evidence.request_id
            or current.records != attempt.evidence.records
            or _query_records_digest(current.records) != _query_records_digest(attempt.evidence.records)
        ):
            _reject("current CTP query readback changed after callback terminal")
        row, provider_state, quantity, traded, remaining = self._validated_query_row(
            current,
            attempt.intent,
            attempt.reservation,
            binding,
            attempt.broker_id,
            attempt.user_id,
            attempt.exchange_id,
            attempt.order_sys_id,
        )
        self._validate_window_row(attempt.query_window, attempt.fence, current, row)
        if now_ns < attempt.query_window.observed_at_ns or now_ns >= attempt.fence.expires_at_ns:
            _reject("CTP query projection is outside its fenced freshness interval")
        full_account_digest = sha256(
            f"{attempt.broker_id}:{attempt.user_id}".encode("ascii")
        ).hexdigest()
        binding_payload = self._store._ctp_callback_session_binding_payload(binding)
        registration_digest = payload_sha256(
            {
                "registration_type": "ctp_order_identity_registration.v1",
                "reservation": _reservation_payload(attempt.reservation),
                "session_binding": binding_payload,
            }
        )
        filters = dict(original_source.request_filters)
        query_filters_sha256 = payload_sha256(
            {"schema": "ctp_order_target_query_filters.v1", "filters": filters}
        )
        records_sha256 = _query_records_digest(current.records)
        source_evidence_sha256 = payload_sha256(
            {
                "source_type": "ctp_order_target_query_source.v1",
                "scope": attempt.scope.to_payload(),
                "fence_id": attempt.fence.fence_id,
                "reservation": _reservation_payload(attempt.reservation),
                "session_binding_sha256": binding.session_binding_sha256,
                "source_tags": attempt.fence.source_tags,
                "query_window_sha256": attempt.query_window.window_sha256,
                "query_request_id": current.request_id,
                "query_filters": filters,
                "query_records_sha256": records_sha256,
                "query_started_at_utc": original_source.started_at_utc.isoformat(),
                "query_completed_at_utc": original_source.completed_at_utc.isoformat(),
                "query_started_monotonic": original_source.started_monotonic,
                "query_completed_monotonic": original_source.completed_monotonic,
                "query_account_fingerprint": original_source.account_fingerprint,
                "query_connection_generation": original_source.connection_generation,
                "query_trading_day": original_source.trading_day,
                "order_sys_id": row["OrderSysID"],
                "order_callback_sequence_end": attempt.query_window.end_source_sequence,
            }
        )
        expires_at_ns = min(
            now_ns + _MAX_PROJECTION_TTL_NS,
            attempt.fence.expires_at_ns,
            int(original_source.trusted_expires_monotonic * 1_000_000_000),
        )
        if expires_at_ns <= now_ns:
            _reject("CTP order-query source expired before projection")
        return CtpVerifiedOrderTargetProjection(
            account_key=attempt.scope.account_key,
            scope_key=attempt.scope.key,
            trading_day=attempt.scope.trading_day or "",
            managed_intent_id=attempt.intent.intent_id,
            runtime_order_id=attempt.reservation.runtime_order_id,
            order_ref=attempt.reservation.order_ref,
            account_fingerprint_sha256=full_account_digest,
            registration_digest=registration_digest,
            instrument_id=attempt.intent.instrument,
            exchange_id=attempt.exchange_id,
            session_generation_id=binding.session_generation_id,
            connection_generation=binding.connection_generation,
            query_front_id=binding.dispatch_front_id,
            query_session_id=binding.dispatch_session_id,
            query_request_id=current.request_id,
            query_filters_sha256=query_filters_sha256,
            query_records_sha256=records_sha256,
            source_evidence_sha256=source_evidence_sha256,
            query_record_count=1,
            query_match_count=1,
            query_complete=True,
            query_terminal=True,
            query_timed_out=False,
            query_error_id=current.error_code,
            late_callback_count=current.late_callback_count,
            order_sys_id=row["OrderSysID"],
            front_id=row["FrontID"],
            session_id=row["SessionID"],
            provider_state=provider_state,
            quantity=quantity,
            traded_quantity=traded,
            remaining_quantity=remaining,
            verifier_id=_VERIFIER_ID,
            verified_at_ns=now_ns,
            expires_at_ns=expires_at_ns,
        )


__all__ = ["CtpOrderTargetQueryEvaluator"]
