"""Configuration-to-registration tests, including a real CURVE child server."""

from __future__ import annotations

import multiprocessing
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal", "strategy-s")
PEER = WireIdentity(WireRole.SERVICE, "service-principal", "account-service")
SCOPE = WireScope("paper:demo", account_ref="account-a", strategy_ref="strategy-s")
CAPABILITY = ServiceCapability("gateway-a", "service.account_read", frozenset({"account.snapshot"}))


def _config(endpoint="tcp://127.0.0.1:7601", *, extra=None, timeout_ms=1000):
    raw = {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": "strategy-s"},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.account",
                            "service_id": "account-service",
                            "instance_id": "gateway-a",
                            "scope_ref": "account-a",
                            "account_ref": "account-a",
                            "transport_ref": "account-zmq",
                        }
                    ],
                    "options": {"request_timeout_ms": timeout_ms},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "account-zmq": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": "client-keys",
                }
            },
        },
    }
    if extra is not None:
        raw["clients"]["monitor"] = {
            "config_schema_version": 1,
            "settings": {
                "bindings": [
                    {
                        "mount": "runtime.telemetry",
                        "service_id": "monitor-service",
                        "instance_id": "monitor",
                        "scope_ref": "account-a",
                        "transport_ref": "missing-monitor",
                        "required": extra,
                    }
                ]
            },
        }
        raw["communication"]["profiles"]["missing-monitor"] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": "tcp://127.0.0.1:7602"},
            "security_profile_ref": "monitor-keys",
        }
    return parse_client_config(raw)


def _registry(path, calls, *, handler=None):
    registry = ServiceRegistry(
        path,
        service_id="account-service",
        capabilities=(CAPABILITY,),
        grants={
            CLIENT.principal_ref: (
                PrincipalGrant(
                    CLIENT,
                    (
                        ResourceGrant(
                            "gateway-a",
                            "service.account_read",
                            "account-a",
                            SCOPE,
                            CAPABILITY.operations,
                        ),
                    ),
                ),
            )
        },
        policy_version="access-v1",
        requirements_version="readiness-v1",
    )

    def snapshot(context, arguments):
        calls.append((context, arguments))
        return {"account_ref": context.scope.account_ref, "balance": 125.0, "source": "synthetic"}

    registry.register_handler("gateway-a", "account.snapshot", handler or snapshot)
    return registry


class LocalTransport:
    def __init__(self, registry, principal_ref=CLIENT.principal_ref):
        self.registry = registry
        self.principal_ref = principal_ref
        self.requests = []
        self.closed = False
        self.timeouts = []
        self.fail_business = False

    def request(self, request, *, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.fail_business and request.header.operation == "account.snapshot":
            raise TimeoutError("synthetic uncertain outcome")
        result = self.registry.handle(self.principal_ref, request)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=PEER,
            ),
            result,
        )

    def close(self):
        self.closed = True


def _runtime(config, factory, *, scopes=lambda _: SCOPE, **kwargs):
    return ServiceClientRuntime(
        config,
        identity=CLIENT,
        scope_resolver=scopes,
        peer_resolver=lambda _: PEER,
        transport_factory=factory,
        run_id="run-one",
        **kwargs,
    )


def test_zero_configuration_never_creates_transport():
    def forbidden(*_):
        pytest.fail("zero configuration must not activate transport")

    runtime = _runtime(parse_client_config({}), forbidden)
    assert runtime.start().bindings == ()
    runtime.close()
    assert runtime.cleanup_failures == 0


def test_config_proxy_registers_calls_renews_and_unregisters(tmp_path):
    calls = []
    registry = _registry(tmp_path / "registry.sqlite", calls)
    transport = LocalTransport(registry)
    runtime = _runtime(_config(timeout_ms=150), lambda *_: transport)
    try:
        receipt = runtime.start()
        assert receipt.bindings[0].policy_version == "access-v1"
        assert (
            runtime.call("store.account", "account.snapshot", {}, account_ref="account-a")[
                "balance"
            ]
            == 125
        )
        assert calls[0][0].scope.run_id == "run-one"
        runtime.renew()
        runtime.close()
        assert transport.closed and runtime.cleanup_failures == 0
        assert set(transport.timeouts) == {0.15}
        prior_call = next(r for r in transport.requests if r.header.operation == "account.snapshot")
        assert registry.handle(CLIENT.principal_ref, prior_call)["ok"] is False
        assert len(calls) == 1
    finally:
        runtime.close()
        registry.close()


def test_required_second_service_failure_releases_first_registration(tmp_path):
    calls = []
    registry = _registry(tmp_path / "registry.sqlite", calls)
    transport = LocalTransport(registry)

    def factory(profile, _):
        if profile["security_profile_ref"] == "monitor-keys":
            raise ConnectionError("unavailable service")
        return transport

    runtime = _runtime(_config(extra=True), factory)
    try:
        with pytest.raises(ServiceRuntimeError):
            runtime.start()
        assert runtime.state == "failed" and transport.closed
        assert [r.header.operation for r in transport.requests][-1] == "service.unregister"
        assert runtime.bindings == () and calls == []
    finally:
        runtime.close()
        registry.close()


def test_optional_monitor_failure_keeps_registered_read_proxy(tmp_path):
    calls = []
    registry = _registry(tmp_path / "registry.sqlite", calls)
    transport = LocalTransport(registry)

    def factory(profile, _):
        if profile["security_profile_ref"] == "monitor-keys":
            raise ConnectionError("unavailable service")
        return transport

    runtime = _runtime(_config(extra=False), factory)
    try:
        assert runtime.start().degraded_count == 1
        assert runtime.call("store.account", "account.snapshot", {})["account_ref"] == "account-a"
        with pytest.raises(ServiceRuntimeError):
            runtime.call("runtime.telemetry", "telemetry.append", {})
        with pytest.raises(ServiceRuntimeError):
            runtime.call("store.account", "account.snapshot", {}, account_ref="account-b")
        assert len(calls) == 1
    finally:
        runtime.close()
        registry.close()


def test_client_never_retries_uncertain_business_request(tmp_path):
    registry = _registry(tmp_path / "registry.sqlite", [])
    transport = LocalTransport(registry)
    runtime = _runtime(_config(), lambda *_: transport)
    try:
        runtime.start()
        transport.fail_business = True
        with pytest.raises(TimeoutError):
            runtime.call("store.account", "account.snapshot", {})
        assert sum(r.header.operation == "account.snapshot" for r in transport.requests) == 1
    finally:
        runtime.close()
        registry.close()


def test_resolved_account_scope_must_match_config_before_connect():
    def forbidden(*_):
        pytest.fail("invalid scope must fail before transport")

    runtime = _runtime(
        _config(), forbidden, scopes=lambda _: replace(SCOPE, account_ref="account-b")
    )
    with pytest.raises(ServiceRuntimeError):
        runtime.start()
    assert runtime.state == "failed"


def _curve_server_process(connection, db_path, server_keys, client_public):
    """Spawned child owns its registry, authenticator, context and socket."""
    from bt_api_transport_zmq.server import CurveServerCredentials
    from bt_api_transport_zmq.service_v2 import ZmqServiceServer

    calls = []
    registry = _registry(db_path, calls)
    server = None
    try:
        server = ZmqServiceServer(
            "tcp://127.0.0.1:*",
            PEER,
            {client_public: CLIENT.principal_ref},
            registry.handle,
            credentials=CurveServerCredentials(*server_keys),
        )
        connection.send(server.endpoint)
        while not connection.poll():
            server.serve_once(50)
        assert connection.recv() == "stop"
        connection.send(
            {"calls": len(calls), "trusted_scope": calls[0][0].scope.account_ref if calls else None}
        )
    finally:
        if server is not None:
            server.close()
        registry.close()
        connection.close()


def test_configured_proxy_real_curve_tcp_across_spawned_process(tmp_path):
    import zmq

    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(
        target=_curve_server_process,
        args=(child, str(tmp_path / "real.sqlite"), server_keys, client_public),
    )
    runtime = None
    process.start()
    child.close()
    try:
        assert parent.poll(15), "server did not report a bound endpoint"
        endpoint = parent.recv()
        factory = ZmqTransportFactory(
            {"client-keys": ClientCurveKeys(server_keys[0], client_public, client_secret)}
        )
        runtime = _runtime(_config(endpoint, timeout_ms=2000), factory)
        receipt = runtime.start()
        assert len(receipt.bindings) == 1 and receipt.degraded_count == 0
        assert runtime.call("store.account", "account.snapshot", {}) == {
            "account_ref": "account-a",
            "balance": 125.0,
            "source": "synthetic",
        }
        # Bypass the SDK proxy to exercise the server's own authorization with
        # a valid certificate and forged identity/account/action claims.
        direct = factory(_config(endpoint).communication["profiles"]["account-zmq"], PEER)
        binding = receipt.bindings[0]
        try:
            for identity, scope, operation, code in [
                (
                    replace(CLIENT, actor_ref="another-strategy"),
                    binding.scope,
                    "account.snapshot",
                    "DENIED",
                ),
                (
                    CLIENT,
                    replace(binding.scope, account_ref="account-b"),
                    "account.snapshot",
                    "SCOPE_DENIED",
                ),
                (CLIENT, binding.scope, "orders.submit", "CAPABILITY_UNAVAILABLE"),
            ]:
                request = WireEnvelope(
                    WireHeader(
                        protocol_version=2,
                        kind=WireMessageKind.REQUEST,
                        message_id=uuid4().hex,
                        correlation_id=None,
                        identity=identity,
                        scope=scope,
                        operation=operation,
                        schema_id="sdk.service.call",
                        schema_version=1,
                        sent_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    ),
                    {
                        "registration_id": binding.registration_id,
                        "service_epoch": binding.service_epoch,
                        "instance_id": binding.instance_id,
                        "arguments": {},
                    },
                )
                denied = direct.request(request, timeout=2)
                assert denied.payload["ok"] is False and denied.payload["code"] == code
        finally:
            direct.close()
        runtime.renew()
        runtime.close()
        assert runtime.cleanup_failures == 0
        parent.send("stop")
        assert parent.poll(10)
        assert parent.recv() == {"calls": 1, "trusted_scope": "account-a"}
        process.join(10)
        assert process.exitcode == 0
    finally:
        if runtime is not None:
            runtime.close()
        if process.is_alive():
            with suppress(EOFError, BrokenPipeError):
                parent.send("stop")
            process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        process.close()


@pytest.mark.parametrize(
    "field,value", [("expires_at", -1), ("policy_version", ""), ("operations", [{}])]
)
def test_malformed_success_receipt_releases_remote_registration(tmp_path, field, value):
    registry = _registry(tmp_path / "malformed.sqlite", [])

    class MalformedReceiptTransport(LocalTransport):
        def request(self, request, *, timeout):
            reply = super().request(request, timeout=timeout)
            if request.header.operation == "service.register":
                result = dict(reply.payload["result"])
                result[field] = value
                return WireEnvelope(reply.header, {**reply.payload, "result": result})
            return reply

    transport = MalformedReceiptTransport(registry)
    runtime = _runtime(_config(), lambda *_: transport)
    try:
        with pytest.raises(ServiceRuntimeError, match="INVALID_SERVICE_REPLY"):
            runtime.start()
        assert runtime.bindings == () and runtime.cleanup_failures == 0
        assert transport.closed
        assert [r.header.operation for r in transport.requests][-1] == "service.unregister"
    finally:
        runtime.close()
        registry.close()


def test_two_account_bindings_require_explicit_account_route(tmp_path):
    raw = _config().to_public_dict()
    bindings = raw["clients"]["gateway"]["settings"]["bindings"]
    bindings.append({**bindings[0], "account_ref": "account-b", "scope_ref": "account-b"})
    scopes = {"account-a": SCOPE, "account-b": replace(SCOPE, account_ref="account-b")}
    registry = ServiceRegistry(
        tmp_path / "two.sqlite",
        service_id="account-service",
        capabilities=(CAPABILITY,),
        grants={
            CLIENT.principal_ref: (
                PrincipalGrant(
                    CLIENT,
                    tuple(
                        ResourceGrant(
                            "gateway-a", "service.account_read", ref, scope, CAPABILITY.operations
                        )
                        for ref, scope in scopes.items()
                    ),
                ),
            )
        },
        policy_version="access-v1",
        requirements_version="readiness-v1",
    )
    registry.register_handler(
        "gateway-a",
        "account.snapshot",
        lambda context, _: {
            "account": context.scope.account_ref,
        },
    )
    transport = LocalTransport(registry)
    runtime = _runtime(parse_client_config(raw), lambda *_: transport, scopes=scopes.__getitem__)
    try:
        assert len(runtime.start().bindings) == 2
        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            runtime.call("store.account", "account.snapshot", {})
        for ref in scopes:
            assert runtime.call("store.account", "account.snapshot", {}, account_ref=ref) == {
                "account": ref
            }
    finally:
        runtime.close()
        registry.close()
    assert runtime.cleanup_failures == 0


def test_application_monitor_optional_control_failure_preserves_query(tmp_path):
    app = WireIdentity(WireRole.APPLICATION_CLIENT, "afi-worker", "afi")
    scope = WireScope("paper:demo", portfolio_ref="portfolio-a")
    query = ServiceCapability("monitor", "service.monitor_query", frozenset({"monitor.query"}))
    control = ServiceCapability(
        "monitor",
        "service.monitor_control",
        frozenset({"monitor.control"}),
        frozenset({"monitor.control"}),
    )
    registry = ServiceRegistry(
        tmp_path / "monitor.sqlite",
        service_id="account-service",
        capabilities=(query, control),
        grants={
            app.principal_ref: (
                PrincipalGrant(
                    app,
                    (ResourceGrant("monitor", query.port, "portfolio-a", scope, query.operations),),
                ),
            )
        },
        policy_version="access-v1",
        requirements_version="readiness-v1",
    )
    registry.register_handler("monitor", "monitor.query", lambda *_: {"status": "visible"})
    raw = _config().to_public_dict()
    raw["process_role"] = "application_client"
    raw["registration"] = {"application_ref": "afi"}
    raw["clients"] = {
        "monitor": {
            "config_schema_version": 1,
            "settings": {
                "bindings": [
                    {
                        "mount": "application.monitor",
                        "service_id": "account-service",
                        "instance_id": "monitor",
                        "scope_ref": "portfolio-a",
                        "transport_ref": "account-zmq",
                    }
                ]
            },
        }
    }
    transport = LocalTransport(registry, app.principal_ref)
    runtime = ServiceClientRuntime(
        parse_client_config(raw),
        identity=app,
        scope_resolver=lambda _: scope,
        peer_resolver=lambda _: PEER,
        transport_factory=lambda *_: transport,
        requested_operations={
            "application.monitor": frozenset({"monitor.query", "monitor.control"})
        },
    )
    try:
        result = runtime.start()
        assert len(result.bindings) == 1 and result.degraded_count == 1
        assert runtime.call("application.monitor", "monitor.query", {}) == {"status": "visible"}
        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            runtime.call("application.monitor", "monitor.control", {})
    finally:
        runtime.close()
        registry.close()
    assert runtime.cleanup_failures == 0
    assert sum(r.header.operation == "service.unregister" for r in transport.requests) == 1


def test_malformed_unregister_ack_is_not_reported_as_clean_shutdown(tmp_path):
    registry = _registry(tmp_path / "ack.sqlite", [])

    class BadAckTransport(LocalTransport):
        def request(self, request, *, timeout):
            reply = super().request(request, timeout=timeout)
            if request.header.operation == "service.unregister":
                return WireEnvelope(reply.header, {**reply.payload, "result": {}})
            return reply

    transport = BadAckTransport(registry)
    runtime = _runtime(_config(), lambda *_: transport)
    try:
        runtime.start()
        runtime.close()
        assert runtime.state == "closed" and transport.closed
        assert runtime.cleanup_failures == 1
    finally:
        runtime.close()
        registry.close()


def test_handler_exception_after_effect_has_unknown_business_outcome(tmp_path):
    calls = []

    def incomplete_handler(context, arguments):
        calls.append((context, arguments))
        raise RuntimeError("synthetic failure after state change")

    registry = _registry(tmp_path / "effect.sqlite", calls, handler=incomplete_handler)
    transport = LocalTransport(registry)
    runtime = _runtime(_config(), lambda *_: transport)
    try:
        runtime.start()
        with pytest.raises(ServiceCallOutcomeUnknownError) as error:
            runtime.call("store.account", "account.snapshot", {})
        assert len(calls) == 1
        assert error.value.message_id == transport.requests[-1].header.message_id
        assert error.value.operation == "account.snapshot"
        assert sum(r.header.operation == "account.snapshot" for r in transport.requests) == 1
    finally:
        runtime.close()
        registry.close()
