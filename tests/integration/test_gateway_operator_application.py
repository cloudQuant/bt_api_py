"""Local synthetic process lifecycle through the service-v2 Gateway Host."""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_operator import (
    GatewayOkxMarketBinding,
    GatewayOperatorDeployment,
)
from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplication,
    GatewayOperatorProfile,
    GatewayOperatorProfileRegistry,
    load_gateway_operator_launch_config,
)
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.integration.test_gateway_operator_deployment import (
    _DOMAIN_REF,
    _KEY,
    _MARKET_OPERATIONS,
    _PRINCIPAL_REF,
    _SECURITY_REF,
    _STRATEGY_REF,
    _market_authority,
    _market_client_config,
    _market_service_config,
    _SyntheticOkxStream,
)

if TYPE_CHECKING:
    from bt_api_base.market_protocol import MarketSubscriptionKey
    from bt_api_okx.market_provider import BoundedOkxTickerQueue


def _free_endpoint_pair() -> tuple[str, str]:
    probes: list[socket.socket] = []
    try:
        for _ in range(2):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            probes.append(probe)
        first = f"tcp://127.0.0.1:{probes[0].getsockname()[1]}"
        second = f"tcp://127.0.0.1:{probes[1].getsockname()[1]}"
        assert first != second
        return first, second
    finally:
        for probe in probes:
            probe.close()


@pytest.mark.integration
def test_registered_operator_profile_serves_synthetic_market_and_stops_cleanly(
    tmp_path: Path,
) -> None:
    rpc_bind, market_bind = _free_endpoint_pair()
    config = parse_service_config(_market_service_config(rpc_bind, market_bind))
    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    authority, service_identity = _market_authority(config, server_keys, client_public)
    streams: list[_SyntheticOkxStream] = []
    owner_threads: list[int] = []
    deployments: list[GatewayOperatorDeployment] = []

    def stream_factory(key: MarketSubscriptionKey, sink: BoundedOkxTickerQueue):
        assert key.channel == "tickers"
        stream = _SyntheticOkxStream(sink)
        streams.append(stream)
        return stream

    def deployment_factory() -> GatewayOperatorDeployment:
        import threading

        owner_threads.append(threading.get_ident())
        deployment = GatewayOperatorDeployment(
            config,
            authority,
            tmp_path / "operator-application-registry.sqlite3",
            resource_provider=lambda *_args: pytest.fail(
                "market profile must use the existing operator-owned Hub"
            ),
            okx_market=GatewayOkxMarketBinding(
                key_universe=(_KEY,),
                principal_allowed_keys={_PRINCIPAL_REF: frozenset({_KEY})},
                stream_factory=stream_factory,
                connect_timeout_seconds=1.0,
            ),
        )
        deployments.append(deployment)
        return deployment

    profile = GatewayOperatorProfile("test-okx-market-v1", "market_hub", deployment_factory)
    registry = GatewayOperatorProfileRegistry((profile,))
    launch_config_path = tmp_path / "operator-profile.yaml"
    launch_config_path.write_text(
        "gateway_operator_config_schema_version: 1\nprofile_ref: test-okx-market-v1\n",
        encoding="utf-8",
    )
    application = GatewayOperatorApplication.from_launch_config(
        load_gateway_operator_launch_config(launch_config_path), registry, poll_interval_ms=5
    )

    runtime: ServiceClientRuntime | None = None
    market: ServiceMarketClient | None = None
    try:
        try:
            status = application.start(timeout_seconds=10)
        except Exception as exc:
            raise AssertionError(dict(application.status)) from exc
        assert status["state"] == "running"
        assert status["ready"] is True
        assert status["role"] == "market_hub"
        assert "endpoint" not in status and "account_ref" not in repr(status)

        client_config = parse_client_config(_market_client_config(rpc_bind, market_bind))
        client_security = ClientCurveKeys(server_keys[0], client_public, client_secret)
        runtime = ServiceClientRuntime(
            client_config,
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF),
            scope_resolver=lambda _ref: WireScope(
                _DOMAIN_REF,
                account_ref="account:operator-gateway",
                strategy_ref=_STRATEGY_REF,
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
        streams[0].emit()

        deadline = time.monotonic() + 5
        event = None
        while time.monotonic() < deadline and event is None:
            event = market.poll(timeout_ms=25)
        assert event is not None
        assert event.header.schema_id == "sdk.market.event"
        assert event.payload["data"]["symbol"] == "BTC-USDT-SWAP"
        assert event.payload["data"]["last_price"] == 30_000.0
    finally:
        if market is not None:
            market.close()
        if runtime is not None:
            runtime.close()
        final_status = application.stop(timeout_seconds=10)

    assert final_status["state"] == "closed"
    assert deployments[0].state == "closed"
    assert len(owner_threads) == 1
    assert streams[0].stop_calls == 1
