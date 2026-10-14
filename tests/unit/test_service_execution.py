"""Focused behavior for the SDK managed-execution client facade."""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_execution import (
    CancelRecordView,
    OrderRecordView,
    ServiceExecutionClient,
    ServiceExecutionError,
    ServiceExecutionUnknownOutcomeError,
)
from bt_api_py.service_runtime import ServiceClientRuntime

_CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal", "strategy-a")
_PEER = WireIdentity(WireRole.SERVICE, "execution-principal", "execution-service")
_SCOPE = WireScope("paper:demo", account_ref="account-a", strategy_ref="strategy-a")
_SERVICE_ID = "execution-service"
_INSTANCE_ID = "execution-owner"
_ORDER_SCOPE_REF = "strategy-orders-a-v1"
_QUERY_SCOPE_REF = "strategy-order-query-a-v1"
_PROFILE_REF = "execution-zmq"


def _order_record(**overrides: Any) -> dict[str, Any]:
    record = {
        "intent_id": "intent-1",
        "scope_key": "scope:abc",
        "payload_sha256": "0" * 64,
        "state": "ACKED",
        "provider_order_id": "provider-order-1",
        "filled_quantity": "0",
        "average_price": None,
        "cumulative_commission": None,
        "permit_reference": "permit-intent-1",
        "dispatch_attempts": 1,
        "unknown_reason": None,
        "review_required": False,
        "created_at_ns": 1,
        "updated_at_ns": 2,
        "is_terminal": False,
    }
    record.update(overrides)
    return record


def _cancel_record(**overrides: Any) -> dict[str, Any]:
    record = {
        "cancel_id": "cancel-1",
        "scope_key": "scope:abc",
        "target_intent_id": "intent-1",
        "provider_order_id": "provider-order-1",
        "payload_sha256": "0" * 64,
        "state": "ACKED",
        "permit_reference": "permit-cancel-1",
        "dispatch_attempts": 1,
        "unknown_reason": None,
        "review_required": False,
        "created_at_ns": 1,
        "updated_at_ns": 2,
        "is_terminal": False,
    }
    record.update(overrides)
    return record


def _wrapped(result: Any) -> Any:
    """Mirror the registry, which returns a handler reply as the call result."""

    if isinstance(result, dict) and set(result) >= {"ok", "code", "result"}:
        return result
    return {"ok": True, "code": "OK", "result": result}


class _RpcLeaf:
    """Fake the transport leaf while using the actual client runtime."""

    def __init__(self) -> None:
        self.requests = []
        self.arguments: list[dict[str, Any]] = []
        self.submit_result: Any = None
        self.cancel_result: Any = None
        self.query_result: Any = None
        self.raise_before: BaseException | None = None
        self.raise_after: BaseException | None = None
        self.closed = False

    def request(self, request, *, timeout):
        assert timeout > 0
        self.requests.append(request)
        operation = request.header.operation
        payload = request.payload
        control = operation.startswith("service.")
        if operation == "service.discover":
            result = {
                "protocol_version": 2,
                "service_id": _SERVICE_ID,
                "ready": True,
                "capabilities": [
                    {
                        "instance_id": _INSTANCE_ID,
                        "port": "service.order_entry",
                        "operations": ["orders.submit", "orders.cancel"],
                    },
                    {
                        "instance_id": _INSTANCE_ID,
                        "port": "service.order_query",
                        "operations": ["orders.query"],
                    },
                ],
            }
        elif operation == "service.register":
            result = {
                "registration_id": "registration-1",
                "service_epoch": "epoch-1",
                "policy_version": "policy-v1",
                "requirements_version": "requirements-v1",
                "operations": payload["operations"],
                "expires_at": time.time() + 300,
                "lease_remaining_seconds": 300.0,
            }
        elif operation == "service.renew":
            result = {
                "registration_id": payload["registration_id"],
                "service_epoch": payload["service_epoch"],
                "policy_version": "policy-v1",
                "requirements_version": "requirements-v1",
                "operations": ["orders.submit", "orders.cancel", "orders.query"],
                "expires_at": time.time() + 300,
                "lease_remaining_seconds": 300.0,
            }
        elif operation == "service.unregister":
            result = {"unregistered": True, "registration_id": payload["registration_id"]}
        elif operation in {"orders.submit", "orders.cancel", "orders.query"}:
            arguments = dict(payload["arguments"])
            if self.raise_before is not None:
                error, self.raise_before = self.raise_before, None
                raise error
            self.arguments.append(arguments)
            if self.raise_after is not None:
                error, self.raise_after = self.raise_after, None
                raise error
            if operation == "orders.submit":
                result = (
                    {"found": True, "record": _order_record()}
                    if self.submit_result is None
                    else self.submit_result
                )
            elif operation == "orders.cancel":
                result = (
                    {"found": True, "record": _cancel_record()}
                    if self.cancel_result is None
                    else self.cancel_result
                )
            else:
                result = (
                    {"found": True, "record": _order_record()}
                    if self.query_result is None
                    else self.query_result
                )
        else:
            raise AssertionError(f"unexpected SDK operation: {operation}")

        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=_PEER,
            ),
            {"ok": True, "result": result if control else _wrapped(result)},
        )

    def close(self) -> None:
        self.closed = True


def _config(*, with_query: bool = True):
    bindings = [
        {
            "mount": "broker.orders",
            "service_id": _SERVICE_ID,
            "instance_id": _INSTANCE_ID,
            "scope_ref": _ORDER_SCOPE_REF,
            "transport_ref": _PROFILE_REF,
            "account_ref": "account-a",
        }
    ]
    if with_query:
        bindings.append(
            {
                "mount": "broker.order_query",
                "service_id": _SERVICE_ID,
                "instance_id": _INSTANCE_ID,
                "scope_ref": _QUERY_SCOPE_REF,
                "transport_ref": _PROFILE_REF,
                "account_ref": "account-a",
            }
        )
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": "strategy-a"},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {"bindings": bindings},
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _PROFILE_REF: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7782"},
                        "security_profile_ref": "execution-keys",
                    }
                },
            },
        }
    )


def _client(*, with_query: bool = True):
    leaves: list[_RpcLeaf] = []
    leaf = _RpcLeaf()
    runtime = ServiceClientRuntime(
        _config(with_query=with_query),
        identity=_CLIENT,
        scope_resolver=lambda _ref: _SCOPE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=lambda *_: (leaves.append(leaf), leaf)[1],
        run_id="run-a",
    )
    runtime.start()
    client = ServiceExecutionClient(
        runtime,
        "broker.orders",
        scope_ref=_ORDER_SCOPE_REF,
        query_mount="broker.order_query" if with_query else None,
        query_scope_ref=_QUERY_SCOPE_REF if with_query else None,
    )
    return runtime, leaf, client


def _submit(client: ServiceExecutionClient, **overrides):
    arguments = {
        "intent_id": "intent-1",
        "signal_id": "signal-1",
        "instrument": "BTC-USDT",
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": Decimal("2"),
        "price": Decimal("50000"),
    }
    arguments.update(overrides)
    return client.submit(**arguments)


@pytest.mark.unit
def test_submit_sends_the_exact_scopeless_intent_and_returns_a_detached_record():
    runtime, leaf, client = _client()
    try:
        view = _submit(client, tags={"desk": "a"})

        assert isinstance(view, OrderRecordView)
        assert view.state == "ACKED"
        assert view.provider_order_id == "provider-order-1"
        assert view.dispatch_attempts == 1
        assert view.is_terminal is False
        sent = leaf.arguments[-1]
        assert sent == {
            "intent_id": "intent-1",
            "signal_id": "signal-1",
            "instrument": "BTC-USDT",
            "side": "BUY",
            "position_effect": "OPEN",
            "order_type": "LIMIT",
            "quantity": "2",
            "price": "50000",
            "time_in_force": "GTC",
            "reduce_only": False,
            "metadata_version": "unknown",
            "deadline_ns": None,
            "tags": {"desk": "a"},
        }
        assert "scope" not in sent
    finally:
        runtime.close()


@pytest.mark.unit
def test_market_intent_must_not_carry_a_price():
    runtime, leaf, client = _client()
    try:
        with pytest.raises(ServiceExecutionError, match="MARKET_INTENT_MUST_NOT_CARRY_PRICE"):
            _submit(client, order_type="MARKET", price=Decimal("1"))
        assert leaf.arguments == []
        assert _submit(client, order_type="MARKET", price=None).state == "ACKED"
    finally:
        runtime.close()


@pytest.mark.unit
def test_invalid_local_arguments_never_reach_the_transport():
    runtime, leaf, client = _client()
    try:
        for overrides, code in (
            ({"side": "sideways"}, "INVALID_SIDE"),
            ({"position_effect": "HOLD"}, "INVALID_POSITION_EFFECT"),
            ({"order_type": "STOP"}, "INVALID_ORDER_TYPE"),
            ({"time_in_force": "GFA"}, "INVALID_TIME_IN_FORCE"),
            ({"quantity": "-1"}, "INVALID_QUANTITY"),
            ({"quantity": 0}, "INVALID_QUANTITY"),
            ({"quantity": True}, "INVALID_QUANTITY"),
            ({"price": "-5"}, "INVALID_PRICE"),
            ({"deadline_ns": 0}, "INVALID_DEADLINE"),
            ({"tags": {"bad key": "v"}}, "INVALID_TAGS"),
            ({"tags": {"k": 1}}, "INVALID_TAGS"),
            ({"intent_id": " bad"}, "INVALID_INTENT_ID"),
            ({"instrument": "-BTC"}, "INVALID_INSTRUMENT"),
        ):
            with pytest.raises(ServiceExecutionError, match=code):
                _submit(client, **overrides)
        assert leaf.arguments == []
    finally:
        runtime.close()


@pytest.mark.unit
def test_unknown_outcome_is_reported_locally_and_never_retried():
    runtime, leaf, client = _client()
    try:
        leaf.raise_after = TimeoutError("synthetic response loss")
        with pytest.raises(ServiceExecutionUnknownOutcomeError) as exc_info:
            _submit(client)

        assert isinstance(exc_info.value, ServiceExecutionUnknownOutcomeError)
        assert exc_info.value.identifier == "intent-1"
        assert exc_info.value.code in {"BUSINESS_OUTCOME_UNKNOWN", "TRANSPORT_OUTCOME_UNKNOWN"}
        assert len(leaf.arguments) == 1
    finally:
        runtime.close()


@pytest.mark.unit
def test_unknown_state_is_a_normal_record_the_caller_can_reconcile():
    runtime, leaf, client = _client()
    try:
        leaf.submit_result = {
            "found": True,
            "record": _order_record(state="UNKNOWN", unknown_reason="provider_timeout"),
        }
        view = _submit(client)

        assert view.state == "UNKNOWN"
        assert view.is_unknown is True
        assert view.unknown_reason == "provider_timeout"
        assert len(leaf.arguments) == 1
    finally:
        runtime.close()


@pytest.mark.unit
def test_conflict_reply_carries_the_existing_record():
    runtime, leaf, client = _client()
    try:
        leaf.submit_result = {
            "ok": False,
            "code": "INTENT_CONFLICT",
            "result": {"found": True, "record": _order_record(state="ACKED")},
        }
        with pytest.raises(ServiceExecutionError) as exc_info:
            _submit(client)

        assert exc_info.value.code == "INTENT_CONFLICT"
        assert isinstance(exc_info.value.record, OrderRecordView)
        assert exc_info.value.record.state == "ACKED"
    finally:
        runtime.close()


@pytest.mark.unit
def test_remote_business_failure_raises_a_finite_code():
    runtime, leaf, client = _client()
    try:
        leaf.submit_result = {"found": False, "record": None}
        with pytest.raises(ServiceExecutionError, match="RECORD_MISSING"):
            _submit(client)
    finally:
        runtime.close()


@pytest.mark.unit
def test_cancel_sends_the_target_identity_and_returns_a_cancel_record():
    runtime, leaf, client = _client()
    try:
        view = client.cancel(
            cancel_id="cancel-1",
            target_intent_id="intent-1",
            provider_order_id="provider-order-1",
        )

        assert isinstance(view, CancelRecordView)
        assert view.target_intent_id == "intent-1"
        assert view.provider_order_id == "provider-order-1"
        assert leaf.arguments[-1] == {
            "cancel_id": "cancel-1",
            "target_intent_id": "intent-1",
            "provider_order_id": "provider-order-1",
            "metadata_version": "unknown",
            "tags": {},
        }
    finally:
        runtime.close()


@pytest.mark.unit
def test_query_returns_none_when_the_service_has_no_record():
    runtime, leaf, client = _client()
    try:
        assert client.query("intent-1").state == "ACKED"

        leaf.query_result = {"found": False, "record": None}
        assert client.query("intent-1") is None
    finally:
        runtime.close()


@pytest.mark.unit
def test_query_requires_an_explicit_route():
    runtime, _leaf, client = _client(with_query=False)
    try:
        with pytest.raises(ServiceExecutionError, match="QUERY_NOT_CONFIGURED"):
            client.query("intent-1")
    finally:
        runtime.close()


@pytest.mark.unit
def test_malformed_remote_records_are_refused():
    runtime, leaf, client = _client()
    try:
        leaf.submit_result = {"found": True, "record": {"state": "ACKED"}}
        with pytest.raises(ServiceExecutionError, match="INVALID_REPLY"):
            _submit(client)

        leaf.submit_result = {"found": True, "record": _order_record(intent_id="intent-9")}
        with pytest.raises(ServiceExecutionError, match="INVALID_REPLY"):
            _submit(client)

        leaf.submit_result = {"found": True, "record": _order_record(dispatch_attempts="1")}
        with pytest.raises(ServiceExecutionError, match="INVALID_REPLY"):
            _submit(client)

        leaf.submit_result = {"found": True, "record": _order_record(filled_quantity="NaN")}
        with pytest.raises(ServiceExecutionError, match="INVALID_REPLY"):
            _submit(client)
    finally:
        runtime.close()


@pytest.mark.unit
def test_mount_and_route_configuration_is_validated_at_construction():
    runtime, _leaf, _client_instance = _client()
    try:
        with pytest.raises(ServiceExecutionError, match="INVALID_EXECUTION_MOUNT"):
            ServiceExecutionClient(runtime, "broker.order_query", scope_ref=_ORDER_SCOPE_REF)
        with pytest.raises(ServiceExecutionError, match="INVALID_SCOPE_REF"):
            ServiceExecutionClient(runtime, "broker.orders", scope_ref=" bad ")
        with pytest.raises(ServiceExecutionError, match="INCOMPLETE_QUERY_ROUTE"):
            ServiceExecutionClient(
                runtime,
                "broker.orders",
                scope_ref=_ORDER_SCOPE_REF,
                query_mount="broker.order_query",
            )
        with pytest.raises(ServiceExecutionError, match="INVALID_QUERY_MOUNT"):
            ServiceExecutionClient(
                runtime,
                "broker.orders",
                scope_ref=_ORDER_SCOPE_REF,
                query_mount="broker.orders",
                query_scope_ref=_QUERY_SCOPE_REF,
            )
    finally:
        runtime.close()


@pytest.mark.unit
def test_facade_borrows_the_runtime_and_never_closes_it():
    runtime, leaf, client = _client()
    try:
        _submit(client)
        assert runtime.state == "registered"
        assert leaf.closed is False
    finally:
        runtime.close()
