"""Synthetic direct-write operator over a real local Host and CURVE."""

from __future__ import annotations

import threading
import time

import pytest
from bt_api_base.service_protocol import WireScope

from bt_api_py.gateway_operator import GatewayOperatorDeployment
from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplication,
    GatewayOperatorApplicationError,
    GatewayOperatorLaunchConfig,
    GatewayOperatorProfile,
    GatewayOperatorProfileRegistry,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    DOMAIN,
    SERVICE,
    _client_config,
    _service_config,
)
from tests.unit.test_iter63_gateway_direct_service import _order
from tests.unit.test_iter63_gateway_operator_trading_owner import _binding, _setup


def test_registered_operator_profile_serves_real_curve_direct_write(tmp_path):
    endpoint, authority, (server_public, client_public, client_secret) = _setup()
    ready = threading.Event()
    ready.set()
    owner_ids = []
    resource_args = []
    closed = []

    def factory():
        owner_ids.append(threading.get_ident())

        def provider(component, instance, options, scope):
            owner_ids.append(threading.get_ident())
            resource_args.append((component, instance, dict(options), scope))
            return ComponentResources.owned(
                {"binding": _binding(tmp_path)},
                ready=ready.is_set,
                close=lambda: closed.append(threading.get_ident()),
            )

        return GatewayOperatorDeployment(
            _service_config(endpoint),
            authority,
            tmp_path / "registry.sqlite",
            resource_provider=provider,
        )

    profile = GatewayOperatorProfile("trading-profile", "trading_owner", factory)
    app = GatewayOperatorApplication.from_launch_config(
        GatewayOperatorLaunchConfig("trading-profile"),
        GatewayOperatorProfileRegistry((profile,)),
        poll_interval_ms=5,
    )
    runtime = ThreadedServiceClientRuntime(
        lambda: ServiceClientRuntime(
            _client_config(endpoint, CLIENT_A),
            identity=CLIENT_A,
            scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
            peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory(
                {"security-a": ClientCurveKeys(server_public, client_public, client_secret)}
            ),
            run_id="run-a",
        )
    )
    try:
        assert app.start(timeout_seconds=10)["ready"] is True
        assert resource_args == [
            (
                "gateway",
                "gateway-a",
                {"role": "trading_owner", "dispatch_enabled": False},
                WireScope(DOMAIN),
            )
        ]
        runtime.start(timeout_seconds=10)
        initial = runtime.call(
            "broker.direct_write",
            "direct.reserve",
            {"request_key": "key-a", "payload": _order(key="key-a"), "ttl_seconds": 10},
            timeout_seconds=10,
        )
        assert initial["status"] == "OK" and initial["record"]["state"] == "RESERVED"
        observed = runtime.call(
            "broker.direct_write",
            "direct.command_status",
            {"request_key": "key-a"},
            timeout_seconds=10,
        )
        assert observed["record"]["reservation_id"] == initial["record"]["reservation_id"]
        record = observed["record"]
        released = runtime.call(
            "broker.direct_write",
            "direct.release",
            {
                "request_key": "key-a",
                "reservation_id": record["reservation_id"],
                "digest": record["digest"],
                "expected_version": record["version"],
                "operation_id": "release-a",
            },
            timeout_seconds=10,
        )
        assert released["record"]["state"] == "RELEASED"
        ready.clear()
        deadline = time.monotonic() + 3
        while app.status["ready"] and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        assert app.status["ready"] is False
    finally:
        runtime.close(timeout_seconds=10)
        app.stop(timeout_seconds=10)
    assert app.status["state"] == "closed"
    assert len(owner_ids) == 2 and owner_ids[0] == owner_ids[1] == closed[0]
    assert owner_ids[0] != threading.get_ident()


def test_application_retries_failed_owned_close_on_owner_thread(tmp_path):
    endpoint, authority, _ = _setup()
    attempts = []

    def factory():
        def close():
            attempts.append(threading.get_ident())
            if len(attempts) == 1:
                raise RuntimeError("synthetic cleanup failure")

        def provider(*args):
            return ComponentResources.owned(
                {"binding": _binding(tmp_path)}, ready=lambda: True, close=close
            )

        return GatewayOperatorDeployment(
            _service_config(endpoint),
            authority,
            tmp_path / "registry.sqlite",
            resource_provider=provider,
        )

    app = GatewayOperatorApplication(
        GatewayOperatorProfile("trading-profile", "trading_owner", factory), poll_interval_ms=5
    )
    app.start(timeout_seconds=10)
    try:
        with pytest.raises(GatewayOperatorApplicationError):
            app.stop(timeout_seconds=10)
        assert app.status["state"] == "uncertain"
        assert app.stop(timeout_seconds=10)["state"] == "closed"
    finally:
        if app.status["state"] != "closed":
            app.stop(timeout_seconds=10)
    assert len(attempts) == 2 and attempts[0] == attempts[1] != threading.get_ident()


@pytest.mark.parametrize("first_close_fails", [False, True])
def test_mismatched_started_candidate_is_closed_or_retained_for_retry(tmp_path, first_close_fails):
    endpoint, authority, _ = _setup()
    attempts = []

    def factory():
        def close():
            attempts.append(threading.get_ident())
            if first_close_fails and len(attempts) == 1:
                raise RuntimeError("synthetic cleanup failure")

        def provider(*args):
            return ComponentResources.owned(
                {"binding": _binding(tmp_path)}, ready=lambda: True, close=close
            )

        deployment = GatewayOperatorDeployment(
            _service_config(endpoint),
            authority,
            tmp_path / "registry.sqlite",
            resource_provider=provider,
        )
        deployment.start()
        return deployment

    app = GatewayOperatorApplication(
        GatewayOperatorProfile("mismatch", "market_hub", factory), poll_interval_ms=5
    )
    try:
        with pytest.raises(GatewayOperatorApplicationError):
            app.start(timeout_seconds=10)
        if first_close_fails:
            assert app.status["state"] == "uncertain"
            assert app.stop(timeout_seconds=10)["state"] == "closed"
        else:
            assert app.status["state"] == "failed"
            assert app.status["error_code"] == "APPLICATION_PROFILE_ROLE_MISMATCH"
    finally:
        if app.status["state"] == "uncertain":
            app.stop(timeout_seconds=10)
    assert len(attempts) == (2 if first_close_fails else 1)
    assert all(owner_id == attempts[0] != threading.get_ident() for owner_id in attempts)
