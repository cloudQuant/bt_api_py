from __future__ import annotations

import multiprocessing
from contextlib import suppress
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

import bt_api_py.service_runtime as service_runtime_module
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "startup-strategy", "startup-strategy")
PEER = WireIdentity(WireRole.SERVICE, "startup-service", "account-service")
CAPABILITY = ServiceCapability("gateway-a", "service.account_read", frozenset({"account.snapshot"}))


class ManualClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _scopes(account_refs: tuple[str, ...]) -> dict[str, WireScope]:
    return {
        account_ref: WireScope(
            "paper:startup",
            account_ref=account_ref,
            strategy_ref="startup-strategy",
        )
        for account_ref in account_refs
    }


def _multi_account_config(endpoint: str, account_refs: tuple[str, ...], *, timeout_ms=5000):
    raw = {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": "startup-strategy"},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.account",
                            "service_id": "account-service",
                            "instance_id": "gateway-a",
                            "scope_ref": account_ref,
                            "account_ref": account_ref,
                            "transport_ref": "account-zmq",
                        }
                        for account_ref in account_refs
                    ],
                    "options": {
                        "request_timeout_ms": timeout_ms,
                        "heartbeat_interval_seconds": 10,
                    },
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
                    "options": {"request_timeout_ms": timeout_ms},
                }
            },
        },
    }
    return parse_client_config(raw)


def _registry(path, account_refs: tuple[str, ...], clock: ManualClock, *, lease_seconds=30):
    scopes = _scopes(account_refs)
    registry = ServiceRegistry(
        path,
        service_id="account-service",
        capabilities=(CAPABILITY,),
        grants={
            CLIENT.principal_ref: (
                PrincipalGrant(
                    CLIENT,
                    tuple(
                        ResourceGrant(
                            "gateway-a",
                            "service.account_read",
                            account_ref,
                            scope,
                            CAPABILITY.operations,
                        )
                        for account_ref, scope in scopes.items()
                    ),
                ),
            )
        },
        policy_version="startup-access-v1",
        requirements_version="startup-readiness-v1",
        lease_seconds=lease_seconds,
        clock=clock,
    )
    registry.register_handler(
        "gateway-a",
        "account.snapshot",
        lambda context, _: {"account_ref": context.scope.account_ref},
    )
    return registry, scopes


class RegistryTransport:
    def __init__(self, registry, *, after_reply=None, peer=PEER, principal_ref=None):
        self.registry = registry
        self.after_reply = after_reply
        self.peer = peer
        self.principal_ref = principal_ref or CLIENT.principal_ref
        self.requests = []
        self.timeouts = []
        self.closed = False

    def request(self, request, *, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        reply = self.registry.handle(self.principal_ref, request)
        if self.after_reply is not None:
            self.after_reply(request, timeout, reply)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="startup-reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=self.peer,
            ),
            reply,
        )

    def close(self):
        self.closed = True


def _runtime(config, transport, scopes, clock):
    return ServiceClientRuntime(
        config,
        identity=CLIENT,
        scope_resolver=scopes.__getitem__,
        peer_resolver=lambda _: PEER,
        transport_factory=lambda *_: transport,
        run_id="startup-run",
        monotonic_clock=clock,
    )


def test_start_maintains_all_required_leases_during_long_multibinding_startup(tmp_path):
    account_refs = tuple(f"account-{index}" for index in range(5))
    clock = ManualClock()
    registry, scopes = _registry(
        tmp_path / "startup-multi.sqlite", account_refs, clock, lease_seconds=30
    )
    runtime_box = {}
    timeout_observations = []

    def after_reply(request, timeout, _reply):
        runtime = runtime_box["runtime"]
        if runtime.state == "starting" and request.header.operation != "service.unregister":
            remaining = [
                binding.lease_deadline - clock()
                for binding in runtime.bindings
                if binding.active and binding.required
            ]
            if remaining:
                budget = min(5.0, min(remaining) * 0.5)
                timeout_observations.append((request.header.operation, timeout, budget))
                assert timeout <= budget + 1e-9
            clock.advance(timeout * 0.8)

    transport = RegistryTransport(registry, after_reply=after_reply)
    runtime = _runtime(
        _multi_account_config("tcp://127.0.0.1:7601", account_refs), transport, scopes, clock
    )
    runtime_box["runtime"] = runtime
    try:
        summary = runtime.start()
        assert clock() > 30
        assert len(summary.bindings) == len(account_refs)
        assert runtime.state == "registered" and runtime.healthy
        assert all(binding.lease_deadline > clock() for binding in summary.bindings)
        assert (
            sum(request.header.operation == "service.renew" for request in transport.requests) >= 1
        )
        assert timeout_observations
        runtime.close()
        assert runtime.state == "closed" and runtime.cleanup_failures == 0
        assert registry._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    finally:
        runtime.close()
        registry.close()


def test_late_successful_registration_receipt_is_retained_for_startup_rollback(tmp_path):
    account_refs = ("account-a", "account-b")
    clock = ManualClock()
    registry, scopes = _registry(
        tmp_path / "startup-late-register.sqlite", account_refs, clock, lease_seconds=30
    )
    registration_count = 0

    def after_reply(request, _timeout, _reply):
        nonlocal registration_count
        if request.header.operation == "service.register":
            registration_count += 1
            clock.advance(9 if registration_count == 1 else 25)

    transport = RegistryTransport(registry, after_reply=after_reply)
    runtime = _runtime(
        _multi_account_config("tcp://127.0.0.1:7601", account_refs), transport, scopes, clock
    )
    try:
        with pytest.raises(ServiceRuntimeError, match="REQUIRED_LEASE_EXPIRED"):
            runtime.start()
        unregister_requests = [
            request
            for request in transport.requests
            if request.header.operation == "service.unregister"
        ]
        assert len(unregister_requests) == 2
        assert runtime.unknown_registration_count == 0
        assert runtime.state == "failed" and transport.closed
        assert runtime.cleanup_failures == 0
        assert registry._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    finally:
        runtime.close()
        registry.close()


def test_interrupt_during_receipt_validation_keeps_unregister_handle_for_retry(
    tmp_path, monkeypatch
):
    class StartupInterruption(BaseException):
        pass

    class InterruptFirstUnregisterTransport(RegistryTransport):
        def __init__(self, registry):
            super().__init__(registry)
            self.unregister_attempts = 0

        def request(self, request, *, timeout):
            if request.header.operation == "service.unregister":
                self.unregister_attempts += 1
                if self.unregister_attempts == 1:
                    raise StartupInterruption()
            return super().request(request, timeout=timeout)

    account_refs = ("account-a",)
    clock = ManualClock()
    registry, scopes = _registry(
        tmp_path / "startup-interrupted-receipt.sqlite", account_refs, clock
    )
    transport = InterruptFirstUnregisterTransport(registry)

    def interrupt_receipt_validation(_result):
        raise StartupInterruption()

    monkeypatch.setattr(
        ServiceClientRuntime,
        "_lease_remaining",
        staticmethod(interrupt_receipt_validation),
    )
    runtime = _runtime(
        _multi_account_config("tcp://127.0.0.1:7601", account_refs),
        transport,
        scopes,
        clock,
    )
    try:
        with pytest.raises(StartupInterruption):
            runtime.start()
        assert transport.unregister_attempts == 2
        assert runtime.state == "failed" and transport.closed
        assert runtime.unknown_registration_count == 0
        assert runtime.unregister_uncertainties == 0
        assert runtime.cleanup_failures == 0
        assert registry._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    finally:
        runtime.close()
        registry.close()


def test_interrupt_before_cleanup_handle_is_stored_marks_registration_unknown(
    tmp_path, monkeypatch
):
    class StartupInterruption(BaseException):
        pass

    account_refs = ("account-a",)
    clock = ManualClock()
    registry, scopes = _registry(
        tmp_path / "startup-unlocalized-receipt.sqlite", account_refs, clock
    )
    transport = RegistryTransport(registry)

    def interrupt_placeholder_construction(**_kwargs):
        raise StartupInterruption()

    monkeypatch.setattr(
        service_runtime_module,
        "RegisteredBinding",
        interrupt_placeholder_construction,
    )
    runtime = _runtime(
        _multi_account_config("tcp://127.0.0.1:7601", account_refs),
        transport,
        scopes,
        clock,
    )
    try:
        with pytest.raises(StartupInterruption):
            runtime.start()
        assert runtime.state == "failed" and transport.closed
        assert runtime.unknown_registration_count == 1
        assert runtime.unregister_uncertainties == 0
        assert registry._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    finally:
        runtime.close()
        registry.close()


def test_startup_renewal_response_after_old_deadline_does_not_revive_binding(tmp_path):
    account_refs = ("account-a", "account-b")
    clock = ManualClock()
    registry, scopes = _registry(
        tmp_path / "startup-late-renew.sqlite", account_refs, clock, lease_seconds=9
    )

    def after_reply(request, _timeout, _reply):
        if request.header.operation == "service.register":
            clock.advance(2.5)
        elif request.header.operation == "service.discover" and clock() >= 2.5:
            clock.advance(0.5)
        elif request.header.operation == "service.renew":
            clock.advance(7)

    transport = RegistryTransport(registry, after_reply=after_reply)
    runtime = _runtime(
        _multi_account_config("tcp://127.0.0.1:7601", account_refs), transport, scopes, clock
    )
    try:
        with pytest.raises(ServiceRuntimeError, match="REQUIRED_LEASE_EXPIRED"):
            runtime.start()
        operations = [request.header.operation for request in transport.requests]
        assert operations.count("service.register") == 1
        assert operations.count("service.renew") == 1
        assert operations.count("service.unregister") == 1
        assert runtime.state == "failed" and runtime.unknown_registration_count == 0
        assert runtime.cleanup_failures == 0
        assert registry._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    finally:
        runtime.close()
        registry.close()


def test_expired_required_lease_in_optional_port_fails_start_and_rolls_back(tmp_path):
    clock = ManualClock()
    application = WireIdentity(WireRole.APPLICATION_CLIENT, "startup-monitor", "monitor-app")
    monitor_peer = WireIdentity(WireRole.SERVICE, "startup-monitor-service", "monitor-service")
    monitor_scope = WireScope("paper:startup")
    query = ServiceCapability("monitor-a", "service.monitor_query", frozenset({"monitor.query"}))
    control = ServiceCapability(
        "monitor-a", "service.monitor_control", frozenset({"monitor.control"})
    )
    registry = ServiceRegistry(
        tmp_path / "startup-optional-expiry.sqlite",
        service_id="monitor-service",
        capabilities=(query, control),
        grants={
            application.principal_ref: (
                PrincipalGrant(
                    application,
                    (
                        ResourceGrant(
                            "monitor-a",
                            query.port,
                            "monitor-root",
                            monitor_scope,
                            query.operations,
                        ),
                        ResourceGrant(
                            "monitor-a",
                            control.port,
                            "monitor-root",
                            monitor_scope,
                            control.operations,
                        ),
                    ),
                ),
            )
        },
        policy_version="startup-access-v1",
        requirements_version="startup-readiness-v1",
        lease_seconds=30,
        clock=clock,
    )
    registry.register_handler("monitor-a", "monitor.query", lambda _context, _args: {})
    registry.register_handler("monitor-a", "monitor.control", lambda _context, _args: {})
    raw_config = {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": "monitor-app"},
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.monitor",
                            "service_id": "monitor-service",
                            "instance_id": "monitor-a",
                            "scope_ref": "monitor-root",
                            "transport_ref": "monitor-zmq",
                        }
                    ],
                    "options": {
                        "request_timeout_ms": 5000,
                        "heartbeat_interval_seconds": 10,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "monitor-zmq": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": "tcp://127.0.0.1:7602"},
                    "security_profile_ref": "client-keys",
                    "options": {"request_timeout_ms": 5000},
                }
            },
        },
    }
    registrations = 0

    def after_reply(request, _timeout, _reply):
        nonlocal registrations
        if request.header.operation == "service.register":
            registrations += 1
            clock.advance(9 if registrations == 1 else 22)

    transport = RegistryTransport(
        registry,
        after_reply=after_reply,
        peer=monitor_peer,
        principal_ref=application.principal_ref,
    )
    runtime = ServiceClientRuntime(
        parse_client_config(raw_config),
        identity=application,
        scope_resolver={"monitor-root": monitor_scope}.__getitem__,
        peer_resolver=lambda _: monitor_peer,
        transport_factory=lambda *_: transport,
        run_id="startup-run",
        requested_operations={
            "application.monitor": frozenset({"monitor.query", "monitor.control"})
        },
        monotonic_clock=clock,
    )
    try:
        with pytest.raises(ServiceRuntimeError, match="REQUIRED_LEASE_EXPIRED"):
            runtime.start()
        operations = [request.header.operation for request in transport.requests]
        assert operations.count("service.register") == 2
        assert operations.count("service.unregister") == 2
        assert runtime.state == "failed" and runtime.cleanup_failures == 0
        assert runtime.unregister_uncertainties == 0
        assert registry._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    finally:
        runtime.close()
        registry.close()


def _curve_multiscope_server(connection, db_path, server_keys, client_public, account_refs):
    from bt_api_transport_zmq.server import CurveServerCredentials
    from bt_api_transport_zmq.service_v2 import ZmqServiceServer

    scopes = _scopes(tuple(account_refs))
    registry = ServiceRegistry(
        db_path,
        service_id="account-service",
        capabilities=(CAPABILITY,),
        grants={
            CLIENT.principal_ref: (
                PrincipalGrant(
                    CLIENT,
                    tuple(
                        ResourceGrant(
                            "gateway-a",
                            "service.account_read",
                            account_ref,
                            scope,
                            CAPABILITY.operations,
                        )
                        for account_ref, scope in scopes.items()
                    ),
                ),
            )
        },
        policy_version="startup-access-v1",
        requirements_version="startup-readiness-v1",
    )
    registry.register_handler(
        "gateway-a", "account.snapshot", lambda context, _: {"account": context.scope.account_ref}
    )
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
            {"sessions": registry._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]}
        )
    finally:
        if server is not None:
            server.close()
        registry.close()
        connection.close()


@pytest.mark.integration
def test_startup_lease_multi_scope_new_node_uses_real_curve_service_process(tmp_path):
    import zmq

    account_refs = ("account-a", "account-b")
    scopes = _scopes(account_refs)
    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(
        target=_curve_multiscope_server,
        args=(
            child,
            str(tmp_path / "startup-curve.sqlite"),
            server_keys,
            client_public,
            account_refs,
        ),
    )
    process.start()
    child.close()
    runtime = None
    try:
        assert parent.poll(15), "multi-scope CURVE server did not report a bound endpoint"
        endpoint = parent.recv()
        factory = ZmqTransportFactory(
            {"client-keys": ClientCurveKeys(server_keys[0], client_public, client_secret)}
        )
        runtime = ServiceClientRuntime(
            _multi_account_config(endpoint, account_refs),
            identity=CLIENT,
            scope_resolver=scopes.__getitem__,
            peer_resolver=lambda _: PEER,
            transport_factory=factory,
            run_id="startup-run",
            monotonic_clock=ManualClock(),
        )
        summary = runtime.start()
        assert len(summary.bindings) == 2 and runtime.healthy
        runtime.close()
        assert runtime.state == "closed" and runtime.cleanup_failures == 0
        parent.send("stop")
        assert parent.poll(10), "multi-scope CURVE server did not report clean registry state"
        assert parent.recv() == {"sessions": 0}
        process.join(10)
        assert process.exitcode == 0
    finally:
        if runtime is not None:
            with suppress(Exception):
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
