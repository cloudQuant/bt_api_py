"""Real spawned ServiceHost integration over synthetic local SQLite data.

This test exercises the SDK runtime, CURVE/ZAP transport, registry and all
four built-in query adapters together. Its records are local fixtures; it
does not use a brokerage adapter, load credentials, or contact an exchange.
"""

from __future__ import annotations

import multiprocessing
import socket
import sqlite3
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import zmq
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_execution import (
    ExecutionScope,
    OrderIntent,
    Side,
    SqliteExecutionStore,
)
from bt_api_gateway import (
    GatewayCommandKind,
    GatewayEventJournal,
    GatewayPrincipal,
)
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_monitor.durable import DurableOutbox, OutboxEvent
from bt_api_monitor.service_component import MonitorReadTarget
from bt_api_risk import (
    AccountScope,
    DurableRiskGate,
    IntentAction,
    RiskIntent,
    RiskPolicy,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

_SERVICE_ID = "synthetic-read-service"
_SERVICE_PRINCIPAL = "synthetic-service-principal"
_CLIENT_PRINCIPAL = "synthetic-client-principal"
_STRATEGY_REF = "strategy-synthetic"
_ACCOUNT_REF = "account-synthetic"
_DOMAIN_REF = "paper:synthetic"
_RUN_ID = "run-synthetic-001"
_EVENT_TIME = 1_800_000_000.0
_MAX_MESSAGE_BYTES = 65_536
_TIMEOUT_MS = 3_000
_BASE_SCOPE = WireScope(
    execution_domain_ref=_DOMAIN_REF,
    account_ref=_ACCOUNT_REF,
    strategy_ref=_STRATEGY_REF,
)
_RISK_ACCOUNT_SCOPE = AccountScope("synthetic", "synthetic-account", "paper")
_EXECUTION_SCOPE = ExecutionScope(
    "SYNTHETIC", "simulation", _ACCOUNT_REF, _STRATEGY_REF, "20261014"
)
_RISK_POLICY = RiskPolicy(
    policy_id="integration-policy",
    max_increase_notional=Decimal("250"),
    max_increase_count=4,
    permit_ttl_seconds=86_400,
)

_COMPONENTS = {
    "gateway": (
        "gateway-read",
        "service.account_read",
        "gateway-grant-v1",
        {
            "role": "account_session_worker",
            "account_session_ref": "synthetic-session-v1",
        },
    ),
    "monitor": ("monitor-read", "service.monitor_query", "monitor-grant-v1", {}),
    "risk": (
        "risk-read",
        "service.risk_query",
        "risk-grant-v1",
        {"role": "account_admission", "account_policy_ref": "risk-policy-v1"},
    ),
    "execution": (
        "execution-read",
        "service.order_query",
        "execution-grant-v1",
        {"role": "account_owner"},
    ),
}


def _component(instance_id: str, port: str, options: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": [port],
            "options": options,
        },
    }


def _available_tcp_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"tcp://127.0.0.1:{port}"


def _communication(endpoint: str, *, server: bool) -> dict[str, Any]:
    profile = "listener-v1" if server else "client-curve-v1"
    security = "server-curve-v1" if server else "client-curve-v1"
    options = {"high_water_mark": 32, "max_message_bytes": _MAX_MESSAGE_BYTES}
    if not server:
        options["request_timeout_ms"] = _TIMEOUT_MS
    return {
        "config_schema_version": 1,
        "profiles": {
            profile: {
                "backend": "zmq",
                "config_schema_version": 1,
                "endpoints": {"rpc_bind" if server else "rpc_connect": endpoint},
                "security_profile_ref": security,
                "options": options,
            }
        },
    }


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": "service-root-v1",
        "transport_ref": "listener-v1",
        "registration_policy_ref": "registration-policy-v1",
        "service_requirements_ref": "service-requirements-v1",
        "components": {
            name: _component(row[0], row[1], row[3]) for name, row in _COMPONENTS.items()
        },
        "communication": _communication(endpoint, server=True),
    }


def _binding(mount: str, component: str, *, account_ref: bool = False) -> dict[str, Any]:
    instance, _port, scope_ref, _options = _COMPONENTS[component]
    binding: dict[str, Any] = {
        "mount": mount,
        "service_id": _SERVICE_ID,
        "instance_id": instance,
        "scope_ref": scope_ref,
        "transport_ref": "client-curve-v1",
    }
    if account_ref:
        binding["account_ref"] = _ACCOUNT_REF
    return binding


def _client_config(endpoint: str, *, application: bool = False):
    if application:
        role, registration = "application_client", {"application_ref": "application-synthetic"}
        mounts = (("monitor", "application.monitor", False),)
    else:
        role, registration = "strategy_client", {"strategy_ref": _STRATEGY_REF}
        mounts = (
            ("gateway", "store.account", True),
            ("risk", "risk.read_model", True),
            ("execution", "broker.order_query", True),
        )
    raw: dict[str, Any] = {
        "client_config_schema_version": 1,
        "process_role": role,
        "registration": registration,
        "clients": {
            name: {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [_binding(mount, name, account_ref=account_ref)],
                    "options": {"request_timeout_ms": _TIMEOUT_MS},
                },
            }
            for name, mount, account_ref in mounts
        },
    }
    raw["communication"] = _communication(endpoint, server=False)
    return parse_client_config(raw)


class _UnusedDispatch:
    """Query-only execution component must never dispatch an order."""

    def submit(self, _intent):
        raise AssertionError("query-only service dispatched an order")

    def cancel(self, _intent):
        raise AssertionError("query-only service dispatched a cancellation")


def _resource_provider(paths: dict[str, str], closed: list[str]):
    """Construct real package resources inside the spawned service process."""

    def provider(component, _instance_id, _options, scope):
        if component == "gateway":
            journal = GatewayEventJournal(paths["gateway"])
            account = GatewayServiceAccountScope(
                account_ref=_ACCOUNT_REF,
                execution_domain_ref=_DOMAIN_REF,
                journal_account_scope="journal:synthetic-account",
                strategy_scope_map={_STRATEGY_REF: "journal:synthetic-strategy"},
            )
            principal = GatewayPrincipal(
                principal_id=_CLIENT_PRINCIPAL,
                account_scopes=frozenset({"journal:synthetic-account"}),
                strategy_scopes=frozenset({"journal:synthetic-strategy"}),
                allowed_kinds=frozenset({GatewayCommandKind.READ}),
            )
            return ComponentResources.borrowed(
                {
                    "journal": journal,
                    "accounts": {_ACCOUNT_REF: account},
                    "principals": {_CLIENT_PRINCIPAL: principal},
                },
                ready=lambda: True,
            )

        if component == "monitor":
            outbox = DurableOutbox(paths["monitor"], read_only=True)
            target = MonitorReadTarget("outbox:synthetic-strategy", ("risk.alert",))
            # DurableOutbox opens a short-lived connection per operation and
            # exposes no persistent handle to release.
            return ComponentResources.borrowed(
                {"outbox": outbox, "targets": {scope: target}},
                ready=lambda: outbox.is_read_only,
            )

        if component == "risk":
            gate = DurableRiskGate(
                paths["risk"],
                _RISK_POLICY,
                clock=lambda: _EVENT_TIME,
            )
            return ComponentResources.owned(
                {"gate": gate, "targets": {scope: _RISK_ACCOUNT_SCOPE}},
                ready=lambda: True,
                close=lambda: _close_resource("risk", gate.close, closed),
            )

        if component == "execution":
            store = SqliteExecutionStore(paths["execution"])
            # The account_owner builtin requires the full trusted write-path
            # resource set even when only the query port is bound here; the
            # dispatch port is a query-only stub and admission is explicitly
            # unprotected because this synthetic service has no risk gate.
            return ComponentResources.owned(
                {
                    "store": store,
                    "targets": {scope: _EXECUTION_SCOPE},
                    "dispatch": _UnusedDispatch(),
                    "admission": {"allow_unprotected": True},
                },
                ready=lambda: True,
                close=lambda: _close_resource("execution", store.close, closed),
            )
        raise AssertionError("fixed service selection should reject unknown components")

    return provider


def _close_resource(name: str, close, closed: list[str]) -> None:
    close()
    closed.append(name)


def _resource_grant(scope: WireScope, component: str, operation: str) -> ResourceGrant:
    instance, port, scope_ref, _options = _COMPONENTS[component]
    return ResourceGrant(instance, port, scope_ref, scope, frozenset({operation}))


def _service_host_process(
    control,
    raw_service_config: dict[str, Any],
    registry_path: str,
    paths: dict[str, str],
    server_public: bytes,
    server_secret: bytes,
    client_public: bytes,
) -> None:
    """Spawn target: the child owns the listener, registry and SQLite readers."""

    closed: list[str] = []
    host = None
    try:
        config = parse_service_config(raw_service_config)
        scope = _BASE_SCOPE
        strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, _STRATEGY_REF)
        application_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT, _CLIENT_PRINCIPAL, "application-synthetic"
        )
        grants = {
            _CLIENT_PRINCIPAL: (
                PrincipalGrant(
                    strategy_identity,
                    tuple(
                        _resource_grant(scope, component, operation)
                        for component, operation in (
                            ("gateway", "account.read_after"),
                            ("risk", "risk.snapshot"),
                            ("execution", "orders.query"),
                        )
                    ),
                ),
                PrincipalGrant(
                    application_identity,
                    (_resource_grant(scope, "monitor", "monitor.read_after"),),
                ),
            )
        }
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
                scope=scope,
                scope_ref=config.scope_ref,
                scope_version="service-scope-v1",
                security_ref="server-curve-v1",
                security_version="curve-keys-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="policy-version-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="requirements-version-v1",
                curve_public_key=server_public,
                curve_secret_key=server_secret,
                principals={client_public: _CLIENT_PRINCIPAL},
                grants=grants,
            ),
            registry_path,
            _resource_provider(paths, closed),
        )
        host.start()
        control.send(("ready", host.endpoint))
        while True:
            if control.poll(0.01) and control.recv() == "close":
                break
            host.serve_once(25)
        host.close()
        control.send(("closed", tuple(sorted(closed)), host.state))
    except BaseException as exc:
        if host is not None:
            with suppress(BaseException):
                host.close()
        code = getattr(exc, "code", type(exc).__name__)
        with suppress(Exception):
            control.send(("error", code))
        raise
    finally:
        control.close()


def _seed_local_databases(paths: dict[str, str]) -> None:
    journal = GatewayEventJournal(paths["gateway"], clock=lambda: _EVENT_TIME)
    journal.append(
        "journal:synthetic-account",
        "journal:synthetic-strategy",
        "gateway-event-1",
        "position_update",
        {"symbol": "SYNTH-USD", "quantity": "2"},
        observed_at=_EVENT_TIME,
    )

    outbox = DurableOutbox(paths["monitor"])
    outbox.append(
        OutboxEvent(
            event_id="monitor-event-1",
            scope="outbox:synthetic-strategy",
            event_type="risk.alert",
            data={"severity": "info", "source": "synthetic"},
            occurred_at=_EVENT_TIME,
        )
    )

    gate = DurableRiskGate(paths["risk"], _RISK_POLICY, clock=lambda: _EVENT_TIME)
    gate.reserve(
        RiskIntent(
            intent_id="risk-intent-1",
            scope=_RISK_ACCOUNT_SCOPE,
            action=IntentAction.INCREASE,
            notional=Decimal("12.50"),
            payload_fingerprint="synthetic-risk-payload",
        )
    )
    gate.close()

    store = SqliteExecutionStore(paths["execution"])
    lease = store.acquire_or_renew_lease(_EXECUTION_SCOPE, "synthetic-writer")
    intent = OrderIntent.limit(
        intent_id="order-synthetic-1",
        scope=_EXECUTION_SCOPE,
        signal_id="signal-synthetic-1",
        instrument="SYNTH-USD",
        side=Side.BUY,
        quantity=Decimal("2"),
        price=Decimal("25"),
    )
    store.admit_intent(intent, writer_lease=lease)
    store.close()


def _sqlite_rows(path: str) -> dict[str, tuple[tuple[object, ...], ...]]:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        result: dict[str, tuple[tuple[object, ...], ...]] = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            # The only interpolated identifier came from sqlite_master and is
            # double-quoted above, including escaped embedded quotes.
            rows = [tuple(row) for row in connection.execute(f"SELECT * FROM {quoted}")]  # noqa: S608
            result[table] = tuple(sorted(rows, key=repr))
        return result
    finally:
        connection.close()


def _direct_request(
    transport,
    *,
    identity: WireIdentity,
    scope: WireScope,
    operation: str,
    binding,
) -> Any:
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
            "arguments": {"intent_id": "order-that-must-not-be-created"},
        },
    )
    return transport.request(request, timeout=2.0)


def test_spawned_curve_host_serves_four_sqlite_read_adapters_and_closes_normally(
    tmp_path,
) -> None:
    paths = {
        name: str(tmp_path / f"{name}.sqlite3")
        for name in ("gateway", "monitor", "risk", "execution")
    }
    _seed_local_databases(paths)
    unchanged_before = {
        name: _sqlite_rows(paths[name]) for name in ("gateway", "monitor", "execution")
    }
    risk_gate_before = DurableRiskGate(paths["risk"], _RISK_POLICY, clock=lambda: _EVENT_TIME)
    risk_snapshot_before = risk_gate_before.snapshot(_RISK_ACCOUNT_SCOPE)
    risk_gate_before.close()

    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    service_config = _service_config(_available_tcp_endpoint())
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_service_host_process,
        args=(
            child,
            service_config,
            str(tmp_path / "service-registry.sqlite3"),
            paths,
            server_public,
            server_secret,
            client_public,
        ),
    )
    strategy_runtime = None
    application_runtime = None
    direct_transport = None
    shutdown = None
    process.start()
    child.close()
    try:
        assert parent.poll(20), "spawned service did not report startup"
        startup = parent.recv()
        assert startup[0] == "ready", startup
        endpoint = startup[1]
        strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, _STRATEGY_REF)
        application_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT, _CLIENT_PRINCIPAL, "application-synthetic"
        )
        service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        client_keys = ClientCurveKeys(server_public, client_public, client_secret)
        transport_factory = ZmqTransportFactory({"client-curve-v1": client_keys})

        strategy_runtime = ServiceClientRuntime(
            _client_config(endpoint),
            identity=strategy_identity,
            scope_resolver=lambda _ref: _BASE_SCOPE,
            peer_resolver=lambda _service: service_identity,
            transport_factory=transport_factory,
            run_id=_RUN_ID,
            requested_operations={
                "store.account": frozenset({"account.read_after"}),
                "risk.read_model": frozenset({"risk.snapshot"}),
                "broker.order_query": frozenset({"orders.query"}),
            },
        )
        strategy_receipt = strategy_runtime.start()
        assert strategy_receipt.degraded_count == 0
        strategy_bindings = {binding.mount: binding for binding in strategy_receipt.bindings}
        assert set(strategy_bindings) == {
            "store.account",
            "risk.read_model",
            "broker.order_query",
        }
        assert {binding.operations for binding in strategy_bindings.values()} == {
            frozenset({"account.read_after"}),
            frozenset({"risk.snapshot"}),
            frozenset({"orders.query"}),
        }
        assert all(binding.scope.run_id == _RUN_ID for binding in strategy_bindings.values())

        gateway_result = strategy_runtime.call(
            "store.account", "account.read_after", {"cursor": 0, "limit": 10}
        )
        assert gateway_result["events"][0]["event_id"] == "gateway-event-1"
        assert gateway_result["events"][0]["payload"]["quantity"] == "2"

        risk_result = strategy_runtime.call("risk.read_model", "risk.snapshot", {})
        assert risk_result["source"] == "local_risk_reservation_ledger"
        assert risk_result["increase_count"] == 1
        assert Decimal(str(risk_result["increase_notional"])) == Decimal("12.5")

        order_result = strategy_runtime.call(
            "broker.order_query",
            "orders.query",
            {"intent_id": "order-synthetic-1"},
        )
        assert order_result["result"]["found"] is True
        assert order_result["result"]["record"]["intent_id"] == "order-synthetic-1"

        application_runtime = ServiceClientRuntime(
            _client_config(endpoint, application=True),
            identity=application_identity,
            scope_resolver=lambda _ref: _BASE_SCOPE,
            peer_resolver=lambda _service: service_identity,
            transport_factory=transport_factory,
            requested_operations={"application.monitor": frozenset({"monitor.read_after"})},
        )
        app_receipt = application_runtime.start()
        assert app_receipt.degraded_count == 0
        monitor_result = application_runtime.call(
            "application.monitor",
            "monitor.read_after",
            {"after_sequence": 0, "limit": 10},
        )
        assert monitor_result["events"][0]["event_id"] == "monitor-event-1"
        assert monitor_result["events"][0]["data"]["source"] == "synthetic"

        # Bypass the SDK proxy with a valid CURVE identity to exercise the
        # server's stored registration scope and exact read-only capability.
        profile = _client_config(endpoint).communication["profiles"]["client-curve-v1"]
        direct_transport = transport_factory(profile, service_identity)
        execution_binding = strategy_bindings["broker.order_query"]
        wrong_scope = replace(execution_binding.scope, account_ref="other-account")
        denied_scope = _direct_request(
            direct_transport,
            identity=strategy_identity,
            scope=wrong_scope,
            operation="orders.query",
            binding=execution_binding,
        )
        assert denied_scope.payload["ok"] is False
        assert denied_scope.payload["code"] == "SCOPE_DENIED"

        ungranted_write = _direct_request(
            direct_transport,
            identity=strategy_identity,
            scope=execution_binding.scope,
            operation="orders.submit",
            binding=execution_binding,
        )
        assert ungranted_write.payload["ok"] is False
        assert ungranted_write.payload["code"] == "CAPABILITY_UNAVAILABLE"

        direct_transport.close()
        direct_transport = None
        application_runtime.close()
        strategy_runtime.close()
        assert application_runtime.cleanup_failures == 0
        assert strategy_runtime.cleanup_failures == 0

        parent.send("close")
        assert parent.poll(15), "spawned service did not report normal close"
        shutdown = parent.recv()
        assert shutdown[0] == "closed", shutdown
        assert shutdown[1] == ("execution", "risk")
        assert shutdown[2] == "closed"
        process.join(15)
        assert process.exitcode == 0
        assert not process.is_alive()

        unchanged_after = {
            name: _sqlite_rows(paths[name]) for name in ("gateway", "monitor", "execution")
        }
        assert unchanged_after == unchanged_before
        risk_gate_after = DurableRiskGate(paths["risk"], _RISK_POLICY, clock=lambda: _EVENT_TIME)
        try:
            assert risk_gate_after.snapshot(_RISK_ACCOUNT_SCOPE) == risk_snapshot_before
        finally:
            risk_gate_after.close()
    finally:
        if direct_transport is not None:
            with suppress(Exception):
                direct_transport.close()
        if application_runtime is not None:
            with suppress(Exception):
                application_runtime.close()
        if strategy_runtime is not None:
            with suppress(Exception):
                strategy_runtime.close()
        if process.is_alive():
            with suppress(Exception):
                parent.send("close")
            process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        if process.exitcode is not None:
            process.close()
