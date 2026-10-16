"""History facade tests over a synthetic registered market service."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from bt_api_base.market_history_protocol import (
    MarketBar,
    MarketHistoryPage,
    MarketHistoryRequest,
)
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_market import ServiceMarketClient, ServiceMarketError
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError

_CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "history-client", "history-strategy")
_PEER = WireIdentity(WireRole.SERVICE, "history-service-principal", "history-service")
_DOMAIN = "paper:history-unit"
_PUBLIC_SCOPE = WireScope(_DOMAIN)
_CLIENT_SCOPE = WireScope(_DOMAIN, strategy_ref="history-strategy")
_BOOTSTRAP_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_HISTORY_OPERATION = "market.history"
_HISTORY_OPERATIONS = frozenset({_HISTORY_OPERATION})
_ALL_OPERATIONS = _BOOTSTRAP_OPERATIONS | {_HISTORY_OPERATION}
_KEY = MarketSubscriptionKey(
    source_ref="history-source",
    entitlement_ref="history-public-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="unit",
        environment="paper",
        product_type="spot",
        instrument_id="asset-history",
        native_symbol="ASSET-HISTORY",
    ),
    channel="bar",
    timeframe="1m",
)
_OTHER_KEY = replace(
    _KEY,
    instrument=replace(_KEY.instrument, instrument_id="other", native_symbol="OTHER"),
)
_START = "2026-10-14T00:00:00Z"
_NEXT = "2026-10-14T00:01:00Z"
_END = "2026-10-14T00:02:00Z"
_REQUEST = MarketHistoryRequest(_KEY, _START, _END, 1)


class _HistoryService:
    def __init__(self) -> None:
        self.registrations: set[str] = set()
        self.history_calls: list[dict[str, object]] = []
        self.subscription_calls: list[str] = []
        self.history_response: object = None

    def handle(self, context, arguments, operation: str):
        if operation == _HISTORY_OPERATION:
            self.history_calls.append(dict(arguments))
            if self.history_response is None:
                raise AssertionError("history response was not configured")
            return self.history_response

        if operation in {"market.subscribe", "market.unsubscribe"}:
            self.subscription_calls.append(operation)

        key = MarketSubscriptionKey.from_dict(arguments["key"])
        stream_id = key.stream_id
        if operation == "market.subscribe":
            was_subscribed = context.registration_id in self.registrations
            self.registrations.add(context.registration_id)
            return {
                "stream_id": stream_id,
                "epoch": "history-epoch-one",
                "head": 0,
                "oldest_sequence": 1,
                "already_subscribed": was_subscribed,
            }
        if operation == "market.unsubscribe":
            self.registrations.discard(context.registration_id)
            return {
                "stream_id": stream_id,
                "epoch": "history-epoch-one",
                "head": 0,
                "upstream_released": not self.registrations,
            }
        if context.registration_id not in self.registrations:
            raise AssertionError("market read arrived without a subscription")
        if operation == "market.snapshot":
            return {
                "snapshot_kind": "latest_event",
                "complete": False,
                "stream_id": stream_id,
                "epoch": "history-epoch-one",
                "head": 0,
                "event": None,
            }
        if operation == "market.head":
            return {
                "stream_id": stream_id,
                "epoch": "history-epoch-one",
                "head": 0,
                "oldest_sequence": 1,
                "latest_observed_at_utc": None,
            }
        if operation == "market.read_after":
            return {
                "stream_id": stream_id,
                "epoch": "history-epoch-one",
                "head": 0,
                "oldest_sequence": 1,
                "after": arguments["after"],
                "next_after": arguments["after"],
                "limit": arguments["limit"],
                "has_more": False,
                "gap": False,
                "gap_reason": None,
                "events": [],
            }
        raise AssertionError(f"unexpected market operation: {operation}")


class _LocalTransport:
    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry
        self.fail_after_history = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        del timeout
        operation = request.header.operation
        result = self.registry.handle(_CLIENT.principal_ref, request)
        if operation == _HISTORY_OPERATION and self.fail_after_history:
            self.fail_after_history = False
            raise TimeoutError("injected lost history reply")
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=_PEER,
            ),
            result,
        )

    def close(self) -> None:
        return None


class _Subscriber:
    def receive(self, *, timeout: float | None = None) -> None:
        del timeout
        return None

    def close(self) -> None:
        return None


class _SubscriberFactory:
    def __call__(self, _binding, _scope) -> _Subscriber:
        return _Subscriber()


def _page(request: MarketHistoryRequest = _REQUEST) -> MarketHistoryPage:
    return MarketHistoryPage(
        request=request,
        bars=(
            MarketBar(
                open_time_utc=_START,
                close_time_utc=_NEXT,
                open="10.0",
                high="11.0",
                low="9.0",
                close="10.5",
                volume="2.0",
            ),
        ),
        complete=False,
        next_start_time_utc=_NEXT,
        observed_at_utc=_END,
    )


def _make_client(
    tmp_path,
    *,
    history_granted: bool = True,
    subscriber_factory=None,
):
    capability_operations = _ALL_OPERATIONS
    granted_operations = _ALL_OPERATIONS if history_granted else _BOOTSTRAP_OPERATIONS
    capability = ServiceCapability(
        "history-instance", "service.market_source", capability_operations
    )
    grant = ResourceGrant(
        "history-instance",
        "service.market_source",
        "history-scope-v1",
        _CLIENT_SCOPE,
        granted_operations,
    )
    registry = ServiceRegistry(
        tmp_path / f"history-{uuid4().hex}.sqlite3",
        service_id="history-service",
        capabilities=(capability,),
        grants={_CLIENT.principal_ref: (PrincipalGrant(_CLIENT, (grant,)),)},
        policy_version="history-policy-v1",
        requirements_version="history-requirements-v1",
        lease_seconds=30,
    )
    service = _HistoryService()
    for operation in _ALL_OPERATIONS:
        registry.register_handler(
            "history-instance",
            operation,
            lambda context, arguments, op=operation: service.handle(context, arguments, op),
        )
    registry.set_ready(True)
    transport = _LocalTransport(registry)
    raw = {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _CLIENT.actor_ref},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.market",
                            "service_id": "history-service",
                            "instance_id": "history-instance",
                            "scope_ref": "history-scope-v1",
                            "transport_ref": "history-client-profile",
                        }
                    ],
                    "options": {"request_timeout_ms": 1_000},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "history-client-profile": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_connect": "tcp://127.0.0.1:7711",
                        "market_connect": "tcp://127.0.0.1:7712",
                    },
                    "security_profile_ref": "history-keys",
                    "options": {
                        "request_timeout_ms": 1_000,
                        "high_water_mark": 128,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }
    config = parse_client_config(raw)
    runtime = ServiceClientRuntime(
        config,
        identity=_CLIENT,
        scope_resolver=lambda _scope_ref: _CLIENT_SCOPE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=lambda _profile, _peer: transport,
        run_id="history-run-1",
        requested_operations={
            "store.market": (
                _BOOTSTRAP_OPERATIONS | _HISTORY_OPERATIONS
                if history_granted
                else _BOOTSTRAP_OPERATIONS
            )
            if subscriber_factory is not None
            else (_HISTORY_OPERATIONS if history_granted else _BOOTSTRAP_OPERATIONS)
        },
    )
    runtime.start()
    client = ServiceMarketClient(
        runtime,
        "store.market",
        _KEY,
        subscriber_factory=subscriber_factory,
        stream_scope=_PUBLIC_SCOPE,
        head_check_interval_seconds=10.0,
    )
    service.history_response = _page().to_dict()
    return client, runtime, registry, service, transport


def _live_cursor_state(client: ServiceMarketClient) -> tuple[object, ...]:
    return (
        client._cursor_epoch,
        client._cursor_sequence,
        client._queued_epoch,
        client._queued_sequence,
        tuple(client._queue),
        client.queue_bytes,
    )


def _close_all(client, runtime, registry) -> None:
    try:
        if client._state not in {"new", "closed"}:
            client.close()
    finally:
        if runtime.state != "closed":
            runtime.close()
        registry.close()


def test_history_routes_exact_request_and_preserves_live_cursor(tmp_path):
    client, runtime, registry, service, _transport = _make_client(tmp_path)
    try:
        before = _live_cursor_state(client)
        assert client._state == "new"
        assert client._get_history_binding().operations == _HISTORY_OPERATIONS
        with pytest.raises(ServiceMarketError) as start_error:
            client.start()
        assert start_error.value.code == "HISTORY_ONLY_CLIENT"
        page = client.history(_REQUEST)
        assert page == _page()
        assert page.request == _REQUEST
        assert service.history_calls == [_REQUEST.to_dict()]
        assert service.subscription_calls == []
        assert client._state == "new"
        assert _live_cursor_state(client) == before
    finally:
        _close_all(client, runtime, registry)


def test_explicit_bar_subscriber_still_starts_as_a_stream(tmp_path):
    class _Subscriber:
        def __init__(self):
            self.closed = False

        def receive(self, _timeout_ms):
            return None

        def close(self):
            self.closed = True

    subscriber = _Subscriber()
    factory_calls = []

    def subscriber_factory(binding, stream_scope):
        factory_calls.append((binding, stream_scope))
        return subscriber

    client, runtime, registry, service, _transport = _make_client(
        tmp_path, subscriber_factory=subscriber_factory
    )
    try:
        bootstrap = client.start()
        assert bootstrap.stream_id == _KEY.stream_id
        assert client._state == "started"
        assert len(factory_calls) == 1
        assert service.subscription_calls == ["market.subscribe"]
    finally:
        _close_all(client, runtime, registry)
    assert subscriber.closed is True


def test_history_without_explicit_grant_fails_without_rpc(tmp_path):
    client, runtime, registry, service, _transport = _make_client(tmp_path, history_granted=False)
    try:
        before = _live_cursor_state(client)
        with pytest.raises(ServiceMarketError) as error:
            client.history(_REQUEST)
        assert error.value.code == "HISTORY_UNAVAILABLE"
        assert service.history_calls == []
        assert service.subscription_calls == []
        assert _live_cursor_state(client) == before
    finally:
        _close_all(client, runtime, registry)


def test_history_requires_exact_trusted_bar_key(tmp_path):
    client, runtime, registry, service, _transport = _make_client(tmp_path)
    try:
        before = _live_cursor_state(client)
        wrong_key_request = replace(_REQUEST, key=_OTHER_KEY)
        with pytest.raises(ServiceMarketError) as error:
            client.history(wrong_key_request)
        assert error.value.code == "HISTORY_KEY_MISMATCH"
        assert service.history_calls == []
        assert service.subscription_calls == []
        assert _live_cursor_state(client) == before
    finally:
        _close_all(client, runtime, registry)


def test_history_rejects_mismatched_reply_and_page_over_limit(tmp_path):
    client, runtime, registry, service, _transport = _make_client(tmp_path)
    try:
        before = _live_cursor_state(client)
        service.history_response = _page(replace(_REQUEST, key=_OTHER_KEY)).to_dict()
        with pytest.raises(ServiceMarketError) as error:
            client.history(_REQUEST)
        assert error.value.code == "HISTORY_REQUEST_MISMATCH"
        assert _live_cursor_state(client) == before

        oversized = _page().to_dict()
        oversized["bars"].append(oversized["bars"][0])
        service.history_response = oversized
        with pytest.raises(ServiceMarketError) as error:
            client.history(_REQUEST)
        assert error.value.code == "INVALID_HISTORY_REPLY"
        assert _live_cursor_state(client) == before
    finally:
        _close_all(client, runtime, registry)


def test_history_epoch_change_discards_reply_without_cursor_mutation(tmp_path, monkeypatch):
    client, runtime, registry, service, _transport = _make_client(tmp_path)
    try:
        before = _live_cursor_state(client)
        original_view = runtime.market_binding_view
        calls = 0

        def changed_on_postcheck(mount, *, required_operations):
            nonlocal calls
            calls += 1
            binding = original_view(mount, required_operations=required_operations)
            if calls == 2:
                return replace(binding, service_epoch="history-epoch-two")
            return binding

        monkeypatch.setattr(runtime, "market_binding_view", changed_on_postcheck)
        with pytest.raises(ServiceMarketError) as error:
            client.history(_REQUEST)
        assert error.value.code == "HISTORY_EPOCH_CHANGED"
        assert len(service.history_calls) == 1
        assert client.recovery_required
        assert _live_cursor_state(client) == before
    finally:
        _close_all(client, runtime, registry)


def test_history_rpc_failure_is_not_retried_or_projected_as_empty(tmp_path):
    client, runtime, registry, service, transport = _make_client(tmp_path)
    try:
        before = _live_cursor_state(client)
        transport.fail_after_history = True
        with pytest.raises(ServiceMarketError) as error:
            client.history(_REQUEST)
        assert error.value.code == "CONTROL_CALL_FAILED"
        assert len(service.history_calls) == 1
        assert _live_cursor_state(client) == before
    finally:
        _close_all(client, runtime, registry)


def test_history_malformed_route_reply_is_not_an_empty_page(tmp_path, monkeypatch):
    client, runtime, registry, service, _transport = _make_client(tmp_path)
    try:
        before = _live_cursor_state(client)
        history_calls = 0
        original_call = runtime.call

        def fail_history_route(mount, operation, arguments, **kwargs):
            nonlocal history_calls
            if operation == _HISTORY_OPERATION:
                history_calls += 1
                raise ServiceRuntimeError("AMBIGUOUS_OR_MISSING_ROUTE")
            return original_call(mount, operation, arguments, **kwargs)

        monkeypatch.setattr(runtime, "call", fail_history_route)
        with pytest.raises(ServiceMarketError) as error:
            client.history(_REQUEST)
        assert error.value.code == "CONTROL_CALL_FAILED"
        assert error.value.cause_code == "AMBIGUOUS_OR_MISSING_ROUTE"
        assert history_calls == 1
        assert service.history_calls == []
        assert _live_cursor_state(client) == before
    finally:
        _close_all(client, runtime, registry)
