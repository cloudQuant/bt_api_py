"""Production operator composition through the service-v2 Gateway Host."""

from __future__ import annotations

import queue
import socket
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.market_history_protocol import MarketHistoryRequest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_okx.containers.tickers import OkxTickerData
from bt_api_okx.feeds.live_okx.swap import OkxRequestDataSwap
from bt_api_okx.market_history_provider import OkxMarketHistoryProvider
from bt_api_okx.market_provider import BoundedOkxTickerQueue

from bt_api_py.gateway_operator import (
    GatewayAccountSessionBinding,
    GatewayMarketBinding,
    GatewayOkxMarketBinding,
    GatewayOperatorDeployment,
    GatewayOperatorDeploymentError,
)
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_market import (
    ServiceMarketClient,
    ServiceMarketError,
    ZmqMarketTransportFactory,
)
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

_SERVICE_ID = "operator-gateway-service"
_SERVICE_INSTANCE = "operator-gateway-instance"
_SCOPE_REF = "operator-gateway-scope-v1"
_SECURITY_REF = "operator-gateway-security-v1"
_TRANSPORT_REF = "operator-gateway-transport-v1"
_DOMAIN_REF = "paper:operator-gateway"
_ACCOUNT_REF = "account:operator-gateway"
_STRATEGY_REF = "strategy:operator-gateway"
_PRINCIPAL_REF = "principal:operator-gateway"
_MARKET_OPERATIONS = frozenset(
    {"market.subscribe", "market.unsubscribe", "market.snapshot", "market.head", "market.read_after"}
)
_HISTORY_OPERATION = "market.history"
_HISTORY_OPERATIONS = frozenset({_HISTORY_OPERATION})
_ACCOUNT_OPERATIONS = frozenset(
    {
        "account.ack",
        "account.configuration",
        "account.head",
        "account.progress",
        "account.read_after",
        "account.server_time_probe",
        "account.snapshot",
    }
)
_KEY = MarketSubscriptionKey(
    source_ref="okx-public-market-r1",
    entitlement_ref="public-ticker-r1",
    instrument=MarketInstrumentKey(
        provider="okx",
        venue="OKX",
        environment="live",
        product_type="SWAP",
        instrument_id="BTC-USDT-SWAP",
        native_symbol="BTC-USDT-SWAP",
    ),
    channel="tickers",
)
_HISTORY_KEY = MarketSubscriptionKey(
    source_ref="okx-public-market-r1",
    entitlement_ref="public-history-r1",
    instrument=MarketInstrumentKey(
        provider="okx",
        venue="OKX",
        environment="live",
        product_type="SWAP",
        instrument_id="BTC-USDT-SWAP",
        native_symbol="BTC-USDT-SWAP",
    ),
    channel="bar",
    timeframe="1m",
)


def _free_endpoint() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"
    finally:
        probe.close()


def _market_service_config(rpc_bind: str, market_bind: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "operator-market-policy-v1",
        "service_requirements_ref": "operator-market-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.market_source"],
                    "options": {"role": "market_hub"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_bind, "market_bind": market_bind},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {"high_water_mark": 64, "max_message_bytes": 262_144},
                }
            },
        },
    }


def _market_client_config(rpc_connect: str, market_connect: str) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _STRATEGY_REF},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.market",
                            "service_id": _SERVICE_ID,
                            "instance_id": _SERVICE_INSTANCE,
                            "scope_ref": _SCOPE_REF,
                            "transport_ref": "operator-market-client-v1",
                        }
                    ],
                    "options": {"request_timeout_ms": 2_000, "heartbeat_interval_seconds": 5},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "operator-market-client-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": rpc_connect, "market_connect": market_connect},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": 2_000,
                        "high_water_mark": 64,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }


class _SyntheticOkxStream:
    def __init__(self, sink: BoundedOkxTickerQueue) -> None:
        self.sink = sink
        self.started = False
        self.stop_calls = 0

    def start(self, *, connect_timeout: float) -> None:
        assert connect_timeout == 1.0
        self.started = True

    def emit(self) -> None:
        self.sink.put(
            OkxTickerData(
                {
                    "instType": "SWAP",
                    "instId": "BTC-USDT-SWAP",
                    "last": "30000",
                    "bidPx": "29999",
                    "askPx": "30001",
                    "bidSz": "3",
                    "askSz": "4",
                    "vol24h": "12.5",
                    "volCcy24h": "375000",
                    "open24h": "29800",
                    "high24h": "30500",
                    "low24h": "29000",
                    "ts": str(int(time.time() * 1000)),
                },
                "BTC-USDT-SWAP",
                "SWAP",
                has_been_json_encoded=True,
            )
        )

    def stop(self) -> None:
        self.stop_calls += 1


def _market_authority(
    config,
    server_keys,
    client_public,
    *,
    operations: frozenset[str] = _MARKET_OPERATIONS,
):
    service_identity = WireIdentity(WireRole.SERVICE, "principal:operator-service", _SERVICE_ID)
    service_scope = WireScope(_DOMAIN_REF)
    strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF)
    grant = ResourceGrant(
        _SERVICE_INSTANCE,
        "service.market_source",
        _SCOPE_REF,
        WireScope(_DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF),
        operations,
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=service_scope,
        scope_ref=config.scope_ref,
        scope_version="operator-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="operator-security-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="operator-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="operator-requirements-v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals={client_public: _PRINCIPAL_REF},
        grants={_PRINCIPAL_REF: (PrincipalGrant(strategy_identity, (grant,)),)},
    )
    return authority, service_identity


@pytest.mark.integration
def test_operator_factory_runs_okx_projection_through_real_service_host(tmp_path: Path) -> None:
    rpc_bind, market_bind = _free_endpoint(), _free_endpoint()
    raw_config = _market_service_config(rpc_bind, market_bind)
    config = parse_service_config(raw_config)
    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    authority, service_identity = _market_authority(config, server_keys, client_public)
    streams: list[_SyntheticOkxStream] = []

    def stream_factory(_key: MarketSubscriptionKey, sink: BoundedOkxTickerQueue):
        stream = _SyntheticOkxStream(sink)
        streams.append(stream)
        return stream

    deployment = GatewayOperatorDeployment(
        config,
        authority,
        tmp_path / "operator-market-registry.sqlite3",
        resource_provider=lambda *_args: pytest.fail("market role must use operator factory"),
        okx_market=GatewayOkxMarketBinding(
            key_universe=(_KEY,),
            principal_allowed_keys={_PRINCIPAL_REF: frozenset({_KEY})},
            stream_factory=stream_factory,
            connect_timeout_seconds=1.0,
        ),
    )
    assert deployment.service_host.__class__.__name__ == "ServiceHost"

    startup: queue.Queue[tuple[str, object, object]] = queue.Queue()
    stop = threading.Event()

    def serve_host() -> None:
        try:
            deployment.start()
            startup.put(("started", deployment.endpoint, deployment.market_endpoint))
            while not stop.is_set():
                deployment.serve_once(10)
        except BaseException as exc:
            startup.put(("error", getattr(exc, "code", "HOST_START_FAILED"), None))
        finally:
            if deployment.state not in {"closed", "failed"}:
                with suppress(BaseException):
                    deployment.close()

    owner = threading.Thread(target=serve_host, name="gateway-operator-host", daemon=True)
    owner.start()
    runtime: ServiceClientRuntime | None = None
    market: ServiceMarketClient | None = None
    try:
        startup_timeout = startup.get(timeout=10)
        assert startup_timeout
        assert startup_timeout[0] == "started", startup_timeout
        rpc_endpoint, market_endpoint = startup_timeout[1], startup_timeout[2]
        assert isinstance(rpc_endpoint, str) and isinstance(market_endpoint, str)

        raw_client = _market_client_config(rpc_endpoint, market_endpoint)
        client_config = parse_client_config(raw_client)
        client_security = ClientCurveKeys(server_keys[0], client_public, client_secret)
        runtime = ServiceClientRuntime(
            client_config,
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF),
            scope_resolver=lambda _ref: WireScope(
                _DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF
            ),
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory({_SECURITY_REF: client_security}),
            requested_operations={"store.market": _MARKET_OPERATIONS},
        )
        runtime.start()
        market = ServiceMarketClient(
            runtime,
            "store.market",
            _KEY,
            subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: client_security}),
            stream_scope=WireScope(_DOMAIN_REF),
            head_check_interval_seconds=1.0,
        )
        market.start()
        assert len(streams) == 1 and streams[0].started
        # Emit only after the client has bootstrapped its initial snapshot, so
        # this checks the live event path rather than the bootstrap snapshot.
        streams[0].emit()
        deadline = time.monotonic() + 5.0
        event = None
        while time.monotonic() < deadline and event is None:
            event = market.poll(timeout_ms=25)
        assert event is not None
        assert event.header.schema_id == "sdk.market.event"
        assert event.header.operation == "market.event"
        assert event.payload["key"] == _KEY.to_dict()
        assert event.payload["data"]["symbol"] == "BTC-USDT-SWAP"
        assert event.payload["data"]["last_price"] == 30_000.0
        assert deployment.summary["market"]["active_registrations"] == 1
    finally:
        if market is not None:
            with suppress(Exception):
                market.close()
        if runtime is not None:
            with suppress(Exception):
                runtime.close()
        stop.set()
        owner.join(10)
        assert not owner.is_alive(), "operator Host owner thread did not close"
    assert deployment.state == "closed"
    assert streams[0].stop_calls == 1


class _NoSubscriptionMarketProvider:
    def __init__(self) -> None:
        self.subscribe_calls: list[object] = []
        self.unsubscribe_calls: list[object] = []
        self.close_calls = 0
        self.owner_threads: list[int] = []

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        self.owner_threads.append(threading.get_ident())
        self.subscribe_calls.append((key, generation))
        raise AssertionError("history-only client must not subscribe to the live stream")

    def unsubscribe(self, handle: object) -> bool:
        self.owner_threads.append(threading.get_ident())
        self.unsubscribe_calls.append(handle)
        return True

    def close(self) -> None:
        self.owner_threads.append(threading.get_ident())
        self.close_calls += 1


class _SyntheticHistoryHttpLeaf(OkxRequestDataSwap):
    def __init__(self) -> None:
        super().__init__(None, environment="production")
        self.http_calls: list[dict[str, Any]] = []
        self.disconnect_calls = 0

    def http_request(
        self,
        method: str,
        url: str,
        headers: Any = None,
        body: Any = None,
        timeout: float = 10,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        self.http_calls.append(
            {
                "thread_id": threading.get_ident(),
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "timeout": timeout,
                "max_retries": max_retries,
            }
        )
        return {
            "code": "0",
            "msg": "",
            "data": [
                [
                    "1767225600000",
                    "100.0",
                    "102.0",
                    "99.0",
                    "101.0",
                    "10",
                    "1000",
                    "1000",
                    "1",
                ],
                [
                    "1767225660000",
                    "101.0",
                    "103.0",
                    "100.0",
                    "102.0",
                    "12",
                    "1200",
                    "1200",
                    "1",
                ],
            ],
        }

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        super().disconnect()


class _FailFirstHistoryClose:
    def __init__(self) -> None:
        self.close_calls = 0

    def fetch(self, _request: MarketHistoryRequest, _timeout: float) -> object:
        raise AssertionError("close-retry test must not fetch history")

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise RuntimeError("synthetic first close uncertainty")


@pytest.mark.integration
def test_history_only_client_fetches_okx_bars_through_operator_host_without_subscribing(
    tmp_path: Path,
) -> None:
    rpc_bind, market_bind = _free_endpoint(), _free_endpoint()
    config = parse_service_config(_market_service_config(rpc_bind, market_bind))
    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    authority, service_identity = _market_authority(
        config, server_keys, client_public, operations=_HISTORY_OPERATIONS
    )
    live_provider = _NoSubscriptionMarketProvider()
    history_clients: list[_SyntheticHistoryHttpLeaf] = []
    factory_threads: list[int] = []

    def make_history_provider() -> OkxMarketHistoryProvider:
        factory_threads.append(threading.get_ident())
        leaf = _SyntheticHistoryHttpLeaf()
        history_clients.append(leaf)
        return OkxMarketHistoryProvider(leaf)

    deployment = GatewayOperatorDeployment(
        config,
        authority,
        tmp_path / "operator-market-history-registry.sqlite3",
        resource_provider=lambda *_args: pytest.fail("market role must use operator factory"),
        market_binding=GatewayMarketBinding(
            key_universe=(_KEY,),
            principal_allowed_keys={_PRINCIPAL_REF: frozenset({_KEY})},
            provider_factory=lambda: live_provider,
            history_key_universe=(_HISTORY_KEY,),
            principal_allowed_history_keys={
                _PRINCIPAL_REF: frozenset({_HISTORY_KEY})
            },
            history_provider_factory=make_history_provider,
            history_timeout_seconds=2.0,
        ),
    )

    startup: queue.Queue[tuple[str, object, object]] = queue.Queue()
    stop = threading.Event()
    owner_id: list[int] = []

    def serve_host() -> None:
        owner_id.append(threading.get_ident())
        try:
            deployment.start()
            startup.put(("started", deployment.endpoint, deployment.market_endpoint))
            while not stop.is_set():
                deployment.serve_once(10)
        except BaseException as exc:
            startup.put(("error", getattr(exc, "code", "HOST_START_FAILED"), None))
        finally:
            if deployment.state not in {"closed", "failed"}:
                with suppress(BaseException):
                    deployment.close()

    owner = threading.Thread(target=serve_host, name="gateway-history-host", daemon=True)
    owner.start()
    runtime: ServiceClientRuntime | None = None
    market: ServiceMarketClient | None = None
    try:
        startup_state = startup.get(timeout=10)
        assert startup_state[0] == "started", startup_state
        rpc_endpoint, market_endpoint = startup_state[1], startup_state[2]
        assert isinstance(rpc_endpoint, str) and isinstance(market_endpoint, str)

        client_config = parse_client_config(
            _market_client_config(rpc_endpoint, market_endpoint)
        )
        client_security = ClientCurveKeys(server_keys[0], client_public, client_secret)
        rpc_transport_factory = ZmqTransportFactory({_SECURITY_REF: client_security})
        runtime = ServiceClientRuntime(
            client_config,
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF),
            scope_resolver=lambda _ref: WireScope(
                _DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF
            ),
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=rpc_transport_factory,
            run_id="operator-history-run-1",
            requested_operations={"store.market": _HISTORY_OPERATIONS},
        )
        runtime.start()
        market = ServiceMarketClient(
            runtime,
            "store.market",
            _HISTORY_KEY,
            stream_scope=WireScope(_DOMAIN_REF),
        )
        with pytest.raises(ServiceMarketError) as start_error:
            market.start()
        assert start_error.value.code == "HISTORY_ONLY_CLIENT"

        request = MarketHistoryRequest(
            _HISTORY_KEY,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:02:00Z",
            10,
        )
        page = market.history(request)
        assert page.request == request
        assert page.complete is True
        assert page.next_start_time_utc is None
        assert [bar.open_time_utc for bar in page.bars] == [
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:01:00Z",
        ]
        assert [bar.volume for bar in page.bars] == ["10", "12"]
        assert market._state == "new"
        assert live_provider.subscribe_calls == []
        assert live_provider.unsubscribe_calls == []
        assert len(history_clients) == 1
        assert len(history_clients[0].http_calls) == 1
        http_call = history_clients[0].http_calls[0]
        assert http_call["method"] == "GET"
        assert http_call["url"].startswith(
            "https://openapi.okx.com/api/v5/market/history-candles?"
        )
        assert "instId=BTC-USDT-SWAP" in http_call["url"]
        assert "bar=1m" in http_call["url"]
        assert "limit=300" in http_call["url"]
        assert not any(key.startswith("OK-ACCESS-") for key in http_call["headers"])
        assert http_call["max_retries"] == 1
        assert http_call["timeout"] <= 2.0
        assert factory_threads == owner_id
        assert http_call["thread_id"] == owner_id[0]
    finally:
        if market is not None:
            with suppress(Exception):
                market.close()
        if runtime is not None:
            with suppress(Exception):
                runtime.close()
        stop.set()
        owner.join(10)
        assert not owner.is_alive(), "operator history owner thread did not close"

    assert deployment.state == "closed"
    assert live_provider.subscribe_calls == []
    assert live_provider.close_calls == 1
    assert live_provider.owner_threads == owner_id
    assert len(history_clients) == 1
    assert history_clients[0].disconnect_calls == 1


def test_operator_retries_only_unconfirmed_history_cleanup(tmp_path: Path) -> None:
    config = parse_service_config(_market_service_config(_free_endpoint(), _free_endpoint()))
    server_keys = zmq.curve_keypair()
    client_public, _client_secret = zmq.curve_keypair()
    authority, _service_identity = _market_authority(
        config, server_keys, client_public, operations=_HISTORY_OPERATIONS
    )
    stream_provider = _NoSubscriptionMarketProvider()
    history_provider = _FailFirstHistoryClose()
    deployment = GatewayOperatorDeployment(
        config,
        authority,
        tmp_path / "operator-history-close-registry.sqlite3",
        resource_provider=lambda *_args: pytest.fail("market role must use operator factory"),
        market_binding=GatewayMarketBinding(
            key_universe=(_KEY,),
            principal_allowed_keys={_PRINCIPAL_REF: frozenset({_KEY})},
            provider_factory=lambda: stream_provider,
            history_key_universe=(_HISTORY_KEY,),
            principal_allowed_history_keys={
                _PRINCIPAL_REF: frozenset({_HISTORY_KEY})
            },
            history_provider_factory=lambda: history_provider,
            history_timeout_seconds=2.0,
        ),
    )
    deployment._bind_owner_thread()
    resources = deployment._build_market_resources(WireScope(_DOMAIN_REF))

    with pytest.raises(GatewayOperatorDeploymentError) as first_close:
        resources.close()
    assert first_close.value.code == "MARKET_HISTORY_PROVIDER_CLOSE_UNCONFIRMED"
    assert history_provider.close_calls == 1
    assert stream_provider.close_calls == 1

    resources.close()
    resources.close()
    assert history_provider.close_calls == 2
    assert stream_provider.close_calls == 1


def _account_service_config(rpc_bind: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "operator-account-policy-v1",
        "service_requirements_ref": "operator-account-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.account_read", "service.provider_session"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": "session:operator-account-v1",
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_bind},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {"high_water_mark": 64, "max_message_bytes": 262_144},
                }
            },
        },
    }


class _UnconnectedProvider:
    def __init__(self, close_results: tuple[object, ...]) -> None:
        self.close_results = list(close_results)
        self.close_calls = 0
        self.connect_calls = 0

    def connect(self, **_kwargs) -> None:
        self.connect_calls += 1

    def read_account(self, **_kwargs):
        raise AssertionError("the injected worker-construction failure precedes reads")

    def close(self):
        self.close_calls += 1
        return self.close_results.pop(0) if self.close_results else None


@pytest.mark.integration
@pytest.mark.parametrize("close_results, expected_pending", [((None,), 0), ((False, True), 1)])
def test_provider_is_closed_or_retained_if_worker_construction_fails_before_handoff(
    tmp_path: Path, monkeypatch, close_results: tuple[object, ...], expected_pending: int
) -> None:
    raw_config = _account_service_config(_free_endpoint())
    config = parse_service_config(raw_config)
    server_keys = zmq.curve_keypair()
    client_public, _client_secret = zmq.curve_keypair()
    scope = WireScope(_DOMAIN_REF)
    client_scope = WireScope(_DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF)
    grant = ResourceGrant(
        _SERVICE_INSTANCE,
        "service.account_read",
        _SCOPE_REF,
        client_scope,
        _ACCOUNT_OPERATIONS,
    )
    authority = TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "principal:operator-service", _SERVICE_ID),
        scope=scope,
        scope_ref=config.scope_ref,
        scope_version="operator-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="operator-security-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="operator-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="operator-requirements-v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals={client_public: _PRINCIPAL_REF},
        grants={
            _PRINCIPAL_REF: (
                PrincipalGrant(
                    WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF),
                    (grant,),
                ),
            )
        },
    )
    account_key = AccountKey("synthetic", "loopback", "paper", "unconnected-account")
    journal = GatewayEventJournal(tmp_path / "unconnected-gateway.sqlite3")
    account_scope = GatewayServiceAccountScope(
        _ACCOUNT_REF,
        _DOMAIN_REF,
        "journal:operator-account",
        strategy_scope_map={_STRATEGY_REF: "state:operator-account-view"},
        account_read_strategy_scope="state:operator-state",
    )
    principal = GatewayPrincipal(
        _PRINCIPAL_REF,
        frozenset({"journal:operator-account"}),
        frozenset({"state:operator-account-view", "state:operator-state"}),
        frozenset({GatewayCommandKind.READ}),
    )
    guard_directory = tmp_path / "account-guard"
    guard_directory.mkdir()
    account_binding = GatewayAccountSessionBinding(
        account_session_ref="session:operator-account-v1",
        journal=journal,
        account_key=account_key,
        account_scope=account_scope,
        state_scope="state:operator-state",
        guard=LocalAccountSessionGuard(
            guard_directory, account_key.account_key_id, "operator-test-owner"
        ),
        required_coverage=(AccountResourceKey("SWAP", "cross"),),
        principals={_PRINCIPAL_REF: principal},
    )
    provider = _UnconnectedProvider(close_results)

    class Owner:
        def __init__(self) -> None:
            self.create_calls = 0

        def create_provider(self, account_session_ref: str) -> _UnconnectedProvider:
            assert account_session_ref == "session:operator-account-v1"
            self.create_calls += 1
            return provider

    owner = Owner()
    import bt_api_gateway.account_session_worker as worker_module

    def fail_worker_init(self, *_args, **_kwargs):
        del self
        raise RuntimeError("synthetic worker construction failure")

    monkeypatch.setattr(worker_module.GatewayAccountSessionWorker, "__init__", fail_worker_init)
    deployment = GatewayOperatorDeployment(
        config,
        authority,
        tmp_path / "operator-account-registry.sqlite3",
        resource_provider=lambda *_args: pytest.fail("account role must use operator factory"),
        session_owner=owner,
        account_session=account_binding,
    )
    with pytest.raises(ServiceHostError):
        deployment.start()
    assert owner.create_calls == 1
    assert provider.connect_calls == 0
    assert provider.close_calls == 1
    assert deployment.cleanup_status["unclaimed_provider_count"] == expected_pending
    if expected_pending:
        assert deployment.cleanup_status["last_cleanup_code"] == (
            "UNCLAIMED_PROVIDER_CLOSE_UNCONFIRMED"
        )
        assert deployment.retry_unclaimed_provider_cleanup() == {"closed": 1, "pending": 0}
        assert provider.close_calls == 2
        assert deployment.cleanup_status["unclaimed_provider_count"] == 0
    assert owner.create_calls == 1
    with suppress(BaseException):
        deployment.close()
