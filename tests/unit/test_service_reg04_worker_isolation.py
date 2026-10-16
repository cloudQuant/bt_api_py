"""REG04 INCR-1: multi-worker vs strategy-claim and handoff isolation proofs.

Four host-level nodes prove, with real registry/host/runtime machinery:

1. A spawned CURVE execution account-owner keeps two application workers
   (distinct client_session_id, manual-book scope) and one strategy run
   (adoption-capable writer claim) isolated: the strategy claim stays held for
   its exact run, a second strategy run is rejected with
   DUPLICATE_ACTIVE_RUN, and both workers keep renewing and registering.
   Account-only writes and a foreign manual book are SCOPE_DENIED.
2. A ServiceHost refuses to start a ``service.group_reservations`` component
   without an exact TrustedGroupReservationPolicy
   (GROUP_RESERVATION_POLICY_REQUIRED) before any route can be exposed, while
   the same host with the policy proceeds past that gate.
3. A real ServiceRegistry denies a reservation-service registration with the
   full ACL/capability grant but no trusted policy (SCOPE_DENIED) and fences
   every unbound/misattributed reservation call before any handler runs.
4. A spawned monitor-control host rejects wrong wire scope, wrong command
   route, and a wrong authenticated actor with SCOPE_DENIED, exposing no
   internal owner operation, and applies exactly one legitimate signed FREEZE.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import multiprocessing
import socket
import sqlite3
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
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
    CancelObservation,
    ExecutionScope,
    OrderIntent,
    ProviderObservation,
    SqliteExecutionStore,
)
from bt_api_monitor.control import (
    ControlAction,
    DurableControlLedger,
)
from bt_api_monitor.ingress import (
    CONTROL_COMMAND_HMAC_DOMAIN,
    ControlIssuerPolicy,
    ControlScopeGrant,
    HmacControlCommandVerifier,
    HmacControlKey,
)
from bt_api_monitor.service_control import (
    ControlCommandBinding,
    ControlIngress,
    MonitorControlService,
    MonitorControlTarget,
    monitor_freeze_cause_id,
)
from bt_api_risk import AccountScope, DurableRiskGate, RiskPolicy

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
    TrustedGroupReservationPolicy,
)
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_SENT_AT = "2026-10-16T00:00:00Z"
_MAX_MESSAGE_BYTES = 65_536
_REQUEST_TIMEOUT_MS = 5_000

# ---------------------------------------------------------------------------
# Node 1 constants: spawned execution account owner.
# ---------------------------------------------------------------------------

_DOMAIN = "paper:reg04-worker-isolation"
_ACCOUNT = "reg04-execution-account"
_STRATEGY = "reg04-strategy"
_APP_BOOK = "reg04-app-book-a"
_OTHER_BOOK = "reg04-other-book-b"
_INSTANCE = "reg04-execution-owner"
_ENTRY_PORT = "service.order_entry"
_SERVICE_ID = "reg04-worker-isolation-service"
_SERVICE_PRINCIPAL = "reg04-worker-isolation-principal"
_EXECUTION_SCOPE_REF = "reg04-execution-owner-root-v1"
_WORKER_SCOPE_REF = "worker-book-a-scope"
_STRATEGY_SCOPE_REF = "strategy-scope-a"
_PROBE_UNALLOCATED_SCOPE_REF = "probe-unallocated-account-a"
_PROBE_BOOK_SCOPE_REF = "probe-book-a-scope"
_WORKER_A_PRINCIPAL = "worker-a-principal"
_WORKER_B_PRINCIPAL = "worker-b-principal"
_STRATEGY_A_PRINCIPAL = "strategy-a-principal"
_STRATEGY_B_PRINCIPAL = "strategy-b-principal"
_PROBE_PRINCIPAL = "probe-principal"
_ENTRY_OPERATIONS = frozenset({"orders.submit", "orders.cancel"})


class _SyntheticProvider:
    """Observational provider used only to build the execution owner."""

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        return ProviderObservation.accepted(intent.intent_id, f"synthetic-{intent.intent_id}")

    def cancel(self, intent) -> CancelObservation:
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _execution_service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _EXECUTION_SCOPE_REF,
        "transport_ref": "reg04-execution-listener-v1",
        "registration_policy_ref": "reg04-worker-isolation-registration-v1",
        "service_requirements_ref": "reg04-worker-isolation-requirements-v1",
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE,
                    "bind_ports": [_ENTRY_PORT],
                    "options": {"role": "account_owner"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "reg04-execution-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": "reg04-server-curve-v1",
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _worker_client_config(endpoint: str, *, application_ref: str) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": application_ref},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.orders",
                            "service_id": _SERVICE_ID,
                            "instance_id": _INSTANCE,
                            "scope_ref": _WORKER_SCOPE_REF,
                            "transport_ref": "reg04-execution-connect-v1",
                            "account_ref": _ACCOUNT,
                        }
                    ],
                    "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "reg04-execution-connect-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": "reg04-client-curve-v1",
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _strategy_client_config(endpoint: str) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _STRATEGY},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "broker.orders",
                            "service_id": _SERVICE_ID,
                            "instance_id": _INSTANCE,
                            "scope_ref": _STRATEGY_SCOPE_REF,
                            "transport_ref": "reg04-execution-connect-v1",
                            "account_ref": _ACCOUNT,
                        }
                    ],
                    "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "reg04-execution-connect-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": "reg04-client-curve-v1",
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _app_book_scope() -> WireScope:
    return WireScope(_DOMAIN, account_ref=_ACCOUNT, manual_book_ref=_APP_BOOK)


def _other_book_scope() -> WireScope:
    return WireScope(_DOMAIN, account_ref=_ACCOUNT, manual_book_ref=_OTHER_BOOK)


def _account_only_scope() -> WireScope:
    return WireScope(_DOMAIN, account_ref=_ACCOUNT)


def _strategy_template_scope() -> WireScope:
    return WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)


def _execution_service_identity() -> WireIdentity:
    return WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)


def _execution_authority(config, keys: dict[str, bytes]) -> TrustedServiceAuthority:
    template = _strategy_template_scope()
    workers = {
        _WORKER_A_PRINCIPAL: WireIdentity(WireRole.APPLICATION_CLIENT, _WORKER_A_PRINCIPAL, "worker-a"),
        _WORKER_B_PRINCIPAL: WireIdentity(WireRole.APPLICATION_CLIENT, _WORKER_B_PRINCIPAL, "worker-b"),
    }
    strategy_clients = {
        _STRATEGY_A_PRINCIPAL: WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_A_PRINCIPAL, _STRATEGY),
        _STRATEGY_B_PRINCIPAL: WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_B_PRINCIPAL, _STRATEGY),
    }
    probe_identity = WireIdentity(WireRole.APPLICATION_CLIENT, _PROBE_PRINCIPAL, "probe")
    grants = {
        _WORKER_A_PRINCIPAL: (
            PrincipalGrant(
                workers[_WORKER_A_PRINCIPAL],
                (
                    ResourceGrant(
                        _INSTANCE, _ENTRY_PORT, _WORKER_SCOPE_REF, _app_book_scope(), _ENTRY_OPERATIONS
                    ),
                ),
            ),
        ),
        _WORKER_B_PRINCIPAL: (
            PrincipalGrant(
                workers[_WORKER_B_PRINCIPAL],
                (
                    ResourceGrant(
                        _INSTANCE, _ENTRY_PORT, _WORKER_SCOPE_REF, _app_book_scope(), _ENTRY_OPERATIONS
                    ),
                ),
            ),
        ),
        _STRATEGY_A_PRINCIPAL: (
            PrincipalGrant(
                strategy_clients[_STRATEGY_A_PRINCIPAL],
                (
                    ResourceGrant(
                        _INSTANCE,
                        _ENTRY_PORT,
                        _STRATEGY_SCOPE_REF,
                        template,
                        frozenset({"orders.submit"}),
                    ),
                ),
            ),
        ),
        _STRATEGY_B_PRINCIPAL: (
            PrincipalGrant(
                strategy_clients[_STRATEGY_B_PRINCIPAL],
                (
                    ResourceGrant(
                        _INSTANCE,
                        _ENTRY_PORT,
                        _STRATEGY_SCOPE_REF,
                        template,
                        frozenset({"orders.submit"}),
                    ),
                ),
            ),
        ),
        _PROBE_PRINCIPAL: (
            PrincipalGrant(
                probe_identity,
                (
                    ResourceGrant(
                        _INSTANCE,
                        _ENTRY_PORT,
                        _PROBE_UNALLOCATED_SCOPE_REF,
                        _account_only_scope(),
                        frozenset({"orders.submit"}),
                    ),
                    ResourceGrant(
                        _INSTANCE,
                        _ENTRY_PORT,
                        _PROBE_BOOK_SCOPE_REF,
                        _app_book_scope(),
                        frozenset({"orders.submit"}),
                    ),
                ),
            ),
        ),
    }
    principals = {
        keys["worker_a_public"]: _WORKER_A_PRINCIPAL,
        keys["worker_b_public"]: _WORKER_B_PRINCIPAL,
        keys["strategy_a_public"]: _STRATEGY_A_PRINCIPAL,
        keys["strategy_b_public"]: _STRATEGY_B_PRINCIPAL,
        keys["probe_public"]: _PROBE_PRINCIPAL,
    }
    return TrustedServiceAuthority(
        identity=_execution_service_identity(),
        scope=template,
        scope_ref=config.scope_ref,
        scope_version="reg04-execution-scope-v1",
        security_ref="reg04-server-curve-v1",
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="reg04-worker-isolation-registration-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="reg04-worker-isolation-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals=principals,
        grants=grants,
    )


def _claim_rows(registry_path: str, domain: str) -> tuple[tuple[str, str | None], ...]:
    uri = Path(registry_path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            "SELECT run_id, active_registration_id FROM strategy_write_claims "
            "WHERE execution_domain_ref = ? ORDER BY strategy_ref, account_ref",
            (domain,),
        ).fetchall()
        return tuple((str(row[0]), None if row[1] is None else str(row[1])) for row in rows)
    finally:
        connection.close()


def _claim_row(registry_path: str, domain: str, strategy_ref: str, account_ref: str):
    uri = Path(registry_path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT run_id, active_registration_id FROM strategy_write_claims "
            "WHERE execution_domain_ref = ? AND strategy_ref = ? AND account_ref = ?",
            (domain, strategy_ref, account_ref),
        ).fetchone()
        return None if row is None else (str(row[0]), None if row[1] is None else str(row[1]))
    finally:
        connection.close()


def _execution_owner_host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    store_path: str,
    keys: dict[str, bytes],
) -> None:
    config = parse_service_config(raw_config)
    store = SqliteExecutionStore(store_path)
    host = None

    def resource_provider(component, _instance_id, _options, _scope):
        if component != "execution":
            raise AssertionError("the selected service contains only the execution owner")
        target_scope = _strategy_template_scope()
        return ComponentResources.borrowed(
            {
                "store": store,
                "targets": {
                    target_scope: ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY, "20261016")
                },
                "dispatch": _SyntheticProvider(),
                "admission": {"allow_unprotected": True},
            },
            ready=lambda: True,
        )

    try:
        host = ServiceHost(
            config,
            _execution_authority(config, keys),
            registry_path,
            resource_provider,
        )
        try:
            host.start()
        except ServiceHostError as error:
            control.send(("startup_failed", error.code, host.state))
            store.close()
            return
        control.send(("ready", host.endpoint, host.state))
        while True:
            if control.poll():
                command = control.recv()
                if command[0] == "close":
                    host.close()
                    store.close()
                    control.send(("closed", host.state))
                    return
                if command[0] == "claim":
                    _, domain, strategy_ref, account_ref = command
                    control.send(
                        ("claim", _claim_row(registry_path, domain, strategy_ref, account_ref))
                    )
                    continue
                raise AssertionError("unknown execution owner host command")
            host.serve_once(timeout_ms=20)
    except BaseException as error:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(BaseException):
            store.close()
        with suppress(Exception):
            control.send(("host_error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        control.close()


def _claim_command(record: dict[str, Any], domain: str, strategy_ref: str, account_ref: str):
    from tests.unit.test_service_account_integration import _receive

    record["pipe"].send(("claim", domain, strategy_ref, account_ref))
    reply = _receive(record["pipe"])
    assert reply[0] == "claim", reply
    return reply[1]


def _worker_runtime(
    endpoint: str,
    *,
    application_ref: str,
    principal_ref: str,
    scope: WireScope,
    client_session_id: str,
    curve_keys: tuple[bytes, bytes],
    server_public: bytes,
) -> ServiceClientRuntime:
    config = parse_client_config(_worker_client_config(endpoint, application_ref=application_ref))
    return ServiceClientRuntime(
        config,
        identity=WireIdentity(WireRole.APPLICATION_CLIENT, principal_ref, application_ref),
        scope_resolver=lambda _ref: scope,
        peer_resolver=lambda _service: _execution_service_identity(),
        transport_factory=ZmqTransportFactory(
            {"reg04-client-curve-v1": ClientCurveKeys(server_public, curve_keys[0], curve_keys[1])}
        ),
        client_session_id=client_session_id,
    )


def _strategy_runtime(
    endpoint: str,
    *,
    principal_ref: str,
    run_id: str,
    curve_keys: tuple[bytes, bytes],
    server_public: bytes,
) -> ServiceClientRuntime:
    config = parse_client_config(_strategy_client_config(endpoint))
    return ServiceClientRuntime(
        config,
        identity=WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, _STRATEGY),
        scope_resolver=lambda _ref: WireScope(_DOMAIN, account_ref=_ACCOUNT),
        peer_resolver=lambda _service: _execution_service_identity(),
        transport_factory=ZmqTransportFactory(
            {"reg04-client-curve-v1": ClientCurveKeys(server_public, curve_keys[0], curve_keys[1])}
        ),
        run_id=run_id,
        requested_operations={"broker.orders": frozenset({"orders.submit"})},
    )


def _raw_register(
    transport,
    *,
    identity: WireIdentity,
    scope: WireScope,
    registration_key: str,
    client_session_id: str,
    scope_ref: str,
    operations: tuple[str, ...],
) -> dict[str, Any]:
    request = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation="service.register",
            schema_id="sdk.service.control",
            schema_version=1,
            sent_at_utc=_SENT_AT,
        ),
        {
            "registration_key": registration_key,
            "client_session_id": client_session_id,
            "instance_id": _INSTANCE,
            "port": _ENTRY_PORT,
            "scope_ref": scope_ref,
            "operations": list(operations),
        },
    )
    return dict(transport.request(request, timeout=5.0).payload)


def test_spawned_execution_owner_isolates_application_workers_from_strategy_claim(
    tmp_path: Path,
) -> None:
    from tests.unit.test_service_account_integration import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    worker_a_public, worker_a_secret = zmq.curve_keypair()
    worker_b_public, worker_b_secret = zmq.curve_keypair()
    strategy_a_public, strategy_a_secret = zmq.curve_keypair()
    strategy_b_public, strategy_b_secret = zmq.curve_keypair()
    probe_public, probe_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "worker_a_public": worker_a_public,
        "worker_b_public": worker_b_public,
        "strategy_a_public": strategy_a_public,
        "strategy_b_public": strategy_b_public,
        "probe_public": probe_public,
    }
    raw_config = _execution_service_config(endpoint)
    registry_path = str(tmp_path / "reg04-worker-isolation-registry.sqlite3")
    store_path = str(tmp_path / "reg04-worker-execution.sqlite3")

    host_record = None
    records: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []
    worker_a = worker_b = strategy_a = strategy_b = None
    direct_transport = None
    try:
        host_record = _start_process(
            context,
            _execution_owner_host_process,
            (raw_config, registry_path, store_path, keys),
            "reg04-worker-isolation-host",
        )
        records.append(host_record)
        ready = _receive(host_record["pipe"])
        assert ready == ("ready", endpoint, "started"), ready

        worker_a = _worker_runtime(
            endpoint,
            application_ref="worker-a",
            principal_ref=_WORKER_A_PRINCIPAL,
            scope=_app_book_scope(),
            client_session_id="worker-session-a",
            curve_keys=(worker_a_public, worker_a_secret),
            server_public=server_public,
        )
        receipt_a = worker_a.start()
        assert receipt_a.degraded_count == 0, receipt_a
        assert len(receipt_a.bindings) == 1
        assert receipt_a.bindings[0].mount == "application.orders"
        assert receipt_a.bindings[0].operations == _ENTRY_OPERATIONS
        assert receipt_a.bindings[0].scope.manual_book_ref == _APP_BOOK
        assert receipt_a.bindings[0].scope.strategy_ref is None

        worker_b = _worker_runtime(
            endpoint,
            application_ref="worker-b",
            principal_ref=_WORKER_B_PRINCIPAL,
            scope=_app_book_scope(),
            client_session_id="worker-session-b",
            curve_keys=(worker_b_public, worker_b_secret),
            server_public=server_public,
        )
        receipt_b = worker_b.start()
        assert receipt_b.degraded_count == 0, receipt_b
        assert worker_a.client_session_id != worker_b.client_session_id

        strategy_a = _strategy_runtime(
            endpoint,
            principal_ref=_STRATEGY_A_PRINCIPAL,
            run_id="run-a",
            curve_keys=(strategy_a_public, strategy_a_secret),
            server_public=server_public,
        )
        receipt_s = strategy_a.start()
        assert receipt_s.degraded_count == 0, receipt_s
        assert len(receipt_s.bindings) == 1
        assert receipt_s.bindings[0].scope.strategy_ref == _STRATEGY
        assert receipt_s.bindings[0].scope.run_id == "run-a"
        assert receipt_s.bindings[0].scope.manual_book_ref is None
        strategy_a_registration_id = receipt_s.bindings[0].registration_id

        # The strategy writer claim stays held for the exact run while two
        # application workers coexist on the same account owner.
        assert _claim_command(host_record, _DOMAIN, _STRATEGY, _ACCOUNT) == (
            "run-a",
            strategy_a_registration_id,
        )

        # A second strategy run on the same trusted owner and account is
        # rejected at registration; the writer claim is not disturbed.
        strategy_b = _strategy_runtime(
            endpoint,
            principal_ref=_STRATEGY_B_PRINCIPAL,
            run_id="run-b",
            curve_keys=(strategy_b_public, strategy_b_secret),
            server_public=server_public,
        )
        with pytest.raises(ServiceRuntimeError) as duplicate:
            strategy_b.start()
        assert duplicate.value.code == "DUPLICATE_ACTIVE_RUN"
        assert strategy_b.state == "failed"
        assert _claim_command(host_record, _DOMAIN, _STRATEGY, _ACCOUNT) == (
            "run-a",
            strategy_a_registration_id,
        )

        # Authenticated raw probes over the real wire: an unallocated
        # account-only write and a foreign manual book are SCOPE_DENIED, while
        # the exact granted manual book still registers new application work.
        parsed_worker_config = parse_client_config(
            _worker_client_config(endpoint, application_ref="worker-a")
        )
        profile = parsed_worker_config.communication["profiles"]["reg04-execution-connect-v1"]
        probe_factory = ZmqTransportFactory(
            {"reg04-client-curve-v1": ClientCurveKeys(server_public, probe_public, probe_secret)}
        )
        direct_transport = probe_factory(profile, _execution_service_identity())
        probe_identity = WireIdentity(WireRole.APPLICATION_CLIENT, _PROBE_PRINCIPAL, "probe")

        account_only = _raw_register(
            direct_transport,
            identity=probe_identity,
            scope=_account_only_scope(),
            registration_key="probe-account-only",
            client_session_id="probe-session-account-only",
            scope_ref=_PROBE_UNALLOCATED_SCOPE_REF,
            operations=("orders.submit",),
        )
        assert account_only["ok"] is False, account_only
        assert account_only["code"] == "SCOPE_DENIED"

        other_book = _raw_register(
            direct_transport,
            identity=probe_identity,
            scope=_other_book_scope(),
            registration_key="probe-other-book",
            client_session_id="probe-session-other-book",
            scope_ref=_PROBE_BOOK_SCOPE_REF,
            operations=("orders.submit",),
        )
        assert other_book["ok"] is False, other_book
        assert other_book["code"] == "SCOPE_DENIED"

        accepted_probe = _raw_register(
            direct_transport,
            identity=probe_identity,
            scope=_app_book_scope(),
            registration_key="probe-exact-book",
            client_session_id="probe-session-exact-book",
            scope_ref=_PROBE_BOOK_SCOPE_REF,
            operations=("orders.submit",),
        )
        assert accepted_probe["ok"] is True, accepted_probe

        # Both workers and the admitted strategy keep renewing without
        # degradation after the rejected second run.
        renewal_a = worker_a.renew()
        assert renewal_a.degraded_count == 0, renewal_a
        assert len(renewal_a.bindings) == 1
        renewal_b = worker_b.renew()
        assert renewal_b.degraded_count == 0, renewal_b
        renewal_s = strategy_a.renew()
        assert renewal_s.degraded_count == 0, renewal_s
        assert strategy_a.run_id == "run-a"

        direct_transport.close()
        direct_transport = None
        strategy_a.close()
        assert strategy_a.cleanup_failures == 0
        assert strategy_a.state == "closed"
        strategy_a = None
        worker_b.close()
        assert worker_b.cleanup_failures == 0
        assert worker_b.state == "closed"
        worker_b = None
        worker_a.close()
        assert worker_a.cleanup_failures == 0
        assert worker_a.state == "closed"
        worker_a = None
        strategy_b.close()
        assert strategy_b.state == "closed"
        strategy_b = None

        closed = _close_normally(host_record, expected_tag="closed")
        assert closed == ("closed", "closed"), closed

        # Durable state: exactly one writer claim existed for this domain; the
        # strategy unregistration released its live registration but kept the
        # exact logical run as an adoption offer. Workers never created claims.
        assert _claim_rows(registry_path, _DOMAIN) == (("run-a", None),)
    finally:
        for label, closer, current in (
            ("direct_transport", lambda item: item.close(), direct_transport),
            ("strategy_a", lambda item: item.close(), strategy_a),
            ("strategy_b", lambda item: item.close(), strategy_b),
            ("worker_b", lambda item: item.close(), worker_b),
            ("worker_a", lambda item: item.close(), worker_a),
        ):
            if current is None:
                continue
            try:
                closer(current)
            except Exception as error:
                failures.append(f"{label}:{getattr(error, 'code', type(error).__name__)}")
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


# ---------------------------------------------------------------------------
# Nodes 2 and 3 constants: reservation policy gate.
# ---------------------------------------------------------------------------

_RESERVATION_DOMAIN = "live:reg04-venue"
_RESERVATION_ACCOUNT = "reg04-account-a"
_RESERVATION_STRATEGY = "reg04-reservation-strategy"
_RESERVATION_PORTFOLIO = "reg04-portfolio-a"
_RESERVATION_SCOPE_REF = "reg04-account-scope-a"
_RESERVATION_RUN = "reg04-run-a"
_RESERVATION_PRINCIPAL = "reg04-reservation-coordinator"
_RISK_OWNER_INSTANCE = "reg04-risk-owner"
_RESERVATION_PORT = "service.group_reservations"


def _reservation_scope() -> WireScope:
    return WireScope(
        _RESERVATION_DOMAIN,
        account_ref=_RESERVATION_ACCOUNT,
        strategy_ref=_RESERVATION_STRATEGY,
        portfolio_ref=_RESERVATION_PORTFOLIO,
    )


def _reservation_coordinator_identity() -> WireIdentity:
    return WireIdentity(WireRole.SERVICE, _RESERVATION_PRINCIPAL, _RESERVATION_PRINCIPAL)


def _reservation_policy(identity: WireIdentity | None = None) -> TrustedGroupReservationPolicy:
    identity = identity or _reservation_coordinator_identity()
    return TrustedGroupReservationPolicy(
        instance_id=_RISK_OWNER_INSTANCE,
        principal_ref=_RESERVATION_PRINCIPAL,
        identity=identity,
        scope_ref=_RESERVATION_SCOPE_REF,
        scope=_reservation_scope(),
        run_id=_RESERVATION_RUN,
        account_ref=_RESERVATION_ACCOUNT,
    )


def _reservation_service_config() -> dict[str, Any]:
    endpoint = _endpoint()
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "reg04-reservation-service",
        "scope_ref": "reg04-reservation-root-v1",
        "transport_ref": "reg04-reservation-listener-v1",
        "registration_policy_ref": "reg04-reservation-registration-v1",
        "service_requirements_ref": "reg04-reservation-requirements-v1",
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _RISK_OWNER_INSTANCE,
                    "bind_ports": [_RESERVATION_PORT],
                    "options": {"role": "group_reservations"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "reg04-reservation-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": "reg04-reservation-server-v1",
                    "options": {
                        "high_water_mark": 16,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _reservation_authority(
    config,
    *,
    server_keys: tuple[bytes, bytes],
    coordinator_public: bytes,
    policies: tuple[TrustedGroupReservationPolicy, ...] = (),
) -> TrustedServiceAuthority:
    identity = _reservation_coordinator_identity()
    policy = _reservation_policy(identity)
    grant = ResourceGrant(
        _RISK_OWNER_INSTANCE,
        _RESERVATION_PORT,
        _RESERVATION_SCOPE_REF,
        _reservation_scope(),
        policy.operations,
    )
    return TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "reg04-reservation-service", "reg04-reservation-service"),
        scope=_reservation_scope(),
        scope_ref=config.scope_ref,
        scope_version="reg04-reservation-root-scope-v1",
        security_ref="reg04-reservation-server-v1",
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="reg04-reservation-registration-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="reg04-reservation-requirements-v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals={coordinator_public: _RESERVATION_PRINCIPAL},
        grants={_RESERVATION_PRINCIPAL: (PrincipalGrant(identity, (grant,)),)},
        group_reservation_policies=policies,
    )


def _reservation_service_registry(
    path: Path,
    *,
    policies: tuple[TrustedGroupReservationPolicy, ...],
) -> ServiceRegistry:
    policy = _reservation_policy()
    capability = ServiceCapability(
        _RISK_OWNER_INSTANCE,
        _RESERVATION_PORT,
        policy.operations,
        policy.write_operations,
    )
    grant = ResourceGrant(
        _RISK_OWNER_INSTANCE,
        _RESERVATION_PORT,
        _RESERVATION_SCOPE_REF,
        _reservation_scope(),
        policy.operations,
    )
    return ServiceRegistry(
        path,
        service_id="reg04-reservation-service",
        capabilities=(capability,),
        grants={_RESERVATION_PRINCIPAL: (PrincipalGrant(_reservation_coordinator_identity(), (grant,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        trusted_group_reservation_policies=policies,
    )


def test_host_start_refuses_group_reservations_without_trusted_reservation_policy(
    tmp_path: Path,
) -> None:
    config = parse_service_config(_reservation_service_config())
    server_keys = zmq.curve_keypair()
    coordinator_public, _coordinator_secret = zmq.curve_keypair()

    def make_provider(calls: list[tuple[str, str]]):
        def provider(component, instance_id, _options, _scope):
            calls.append((component, instance_id))
            # No route may ever reach the reservation adapter; these synthetic
            # values only feed the local resource-resolution step.
            return ComponentResources.borrowed(
                {"accepted_group_store": object(), "group_runtime_bundle": object()},
                ready=lambda: True,
            )

        return provider

    denied_calls: list[tuple[str, str]] = []
    denied_host = ServiceHost(
        config,
        _reservation_authority(
            config, server_keys=server_keys, coordinator_public=coordinator_public
        ),
        str(tmp_path / "reg04-reservation-denied-registry.sqlite3"),
        make_provider(denied_calls),
    )
    with pytest.raises(ServiceHostError) as denied:
        denied_host.start()
    assert denied.value.code == "GROUP_RESERVATION_POLICY_REQUIRED"
    assert denied_calls == [("risk", _RISK_OWNER_INSTANCE)]
    assert denied_host.state == "failed"
    assert denied_host.endpoint is None
    assert denied_host.summary["start_failure"] == "GROUP_RESERVATION_POLICY_REQUIRED"

    # The exact same component resolves past the policy gate once the
    # authority carries the matching trusted policy, so the refusal above is
    # attributable to the missing policy (and only that gate) alone.
    contrast_calls: list[tuple[str, str]] = []
    contrast_host = ServiceHost(
        config,
        _reservation_authority(
            config,
            server_keys=server_keys,
            coordinator_public=coordinator_public,
            policies=(_reservation_policy(),),
        ),
        str(tmp_path / "reg04-reservation-contrast-registry.sqlite3"),
        make_provider(contrast_calls),
    )
    with pytest.raises(ServiceHostError) as contrast:
        contrast_host.start()
    assert contrast.value.code in {
        "GROUP_RUNTIME_BUNDLE_INVALID",
        "GROUP_RUNTIME_BUNDLE_UNAVAILABLE",
    }
    assert contrast.value.code != "GROUP_RESERVATION_POLICY_REQUIRED"
    assert contrast_calls == [("risk", _RISK_OWNER_INSTANCE)]
    assert contrast_host.state == "failed"
    assert contrast_host.endpoint is None


def _reservation_request(
    operation: str,
    payload: dict[str, Any],
    *,
    identity: WireIdentity,
    scope: WireScope,
) -> WireEnvelope:
    return WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id="sdk.service.control" if operation.startswith("service.") else "sdk.service.call",
            schema_version=1,
            sent_at_utc=_SENT_AT,
        ),
        payload,
    )


def _reservation_register(
    registry: ServiceRegistry,
    *,
    identity: WireIdentity,
    scope: WireScope,
    suffix: str,
) -> dict[str, Any]:
    policy = _reservation_policy()
    response = registry.handle(
        identity.principal_ref,
        _reservation_request(
            "service.register",
            {
                "registration_key": f"reg04-registration-{suffix}",
                "client_session_id": f"reg04-session-{suffix}",
                "instance_id": _RISK_OWNER_INSTANCE,
                "port": _RESERVATION_PORT,
                "scope_ref": _RESERVATION_SCOPE_REF,
                "operations": tuple(sorted(policy.operations)),
            },
            identity=identity,
            scope=scope,
        ),
    )
    return dict(response)


def _reservation_dispatch(
    registry: ServiceRegistry,
    *,
    identity: WireIdentity,
    scope: WireScope,
    registration: dict[str, Any],
    operation: str = "reservations.reserve",
    registration_id: str | None = None,
) -> dict[str, Any]:
    response = registry.handle(
        identity.principal_ref,
        _reservation_request(
            operation,
            {
                "registration_id": registration_id or registration["registration_id"],
                "service_epoch": registration["service_epoch"],
                "instance_id": _RISK_OWNER_INSTANCE,
                "arguments": {"accepted_group_ref": "reg04-group-a"},
            },
            identity=identity,
            scope=scope,
        ),
    )
    return dict(response)


def test_reservation_route_without_trusted_policy_is_denied_with_zero_handler_calls(
    tmp_path: Path,
) -> None:
    identity = _reservation_coordinator_identity()
    scope = _reservation_scope()
    calls: list[str] = []

    def handler(_context, arguments):
        calls.append(str(arguments["accepted_group_ref"]))
        return {"submitted": True}

    denied_registry = _reservation_service_registry(
        tmp_path / "reg04-reservation-no-policy.sqlite3", policies=()
    )
    try:
        for operation in _reservation_policy().operations:
            denied_registry.register_handler(_RISK_OWNER_INSTANCE, operation, handler)
        # The full ACL grant and capability are present; only the trusted
        # operator policy is missing, so registration is denied.
        denied = _reservation_register(
            denied_registry, identity=identity, scope=scope, suffix="no-policy"
        )
        assert denied["ok"] is False, denied
        assert denied["code"] == "SCOPE_DENIED"
        assert calls == []
    finally:
        denied_registry.close()

    registry = _reservation_service_registry(
        tmp_path / "reg04-reservation-policy.sqlite3",
        policies=(_reservation_policy(),),
    )
    try:
        for operation in _reservation_policy().operations:
            registry.register_handler(_RISK_OWNER_INSTANCE, operation, handler)
        registered = _reservation_register(
            registry, identity=identity, scope=scope, suffix="policy"
        )
        assert registered["ok"] is True, registered
        registration = registered["result"]

        # An eligible registration is not a run authority: the write cannot
        # reach any handler until the trusted accepted-group callback binds
        # the exact operator-configured logical run.
        unbound = _reservation_dispatch(
            registry, identity=identity, scope=scope, registration=registration
        )
        assert unbound["code"] == "DUPLICATE_ACTIVE_RUN"
        assert calls == []

        assert (
            registry.bind_trusted_group_run(
                _RESERVATION_DOMAIN,
                _RESERVATION_STRATEGY,
                _RESERVATION_ACCOUNT,
                registration_id=registration["registration_id"],
            )
            == _RESERVATION_RUN
        )

        # Wrong wire scope, a forged actor on the same authenticated
        # principal, and an unknown registration all stop before the handler.
        wrong_scope = _reservation_dispatch(
            registry,
            identity=identity,
            scope=WireScope(
                _RESERVATION_DOMAIN,
                account_ref="reg04-account-b",
                strategy_ref=_RESERVATION_STRATEGY,
                portfolio_ref=_RESERVATION_PORTFOLIO,
            ),
            registration=registration,
        )
        assert wrong_scope["code"] == "SCOPE_DENIED"
        assert calls == []

        forged = _reservation_dispatch(
            registry,
            identity=WireIdentity(WireRole.SERVICE, _RESERVATION_PRINCIPAL, "forged-actor"),
            scope=scope,
            registration=registration,
        )
        assert forged["code"] == "DENIED"
        assert calls == []

        unknown = _reservation_dispatch(
            registry,
            identity=identity,
            scope=scope,
            registration=registration,
            registration_id="reg04-missing-registration",
        )
        assert unknown["code"] == "NOT_REGISTERED"
        assert calls == []

        accepted = _reservation_dispatch(
            registry, identity=identity, scope=scope, registration=registration
        )
        assert accepted["ok"] is True, accepted
        assert accepted["result"] == {"submitted": True}
        assert calls == ["reg04-group-a"]
    finally:
        registry.close()


# ---------------------------------------------------------------------------
# Node 4 constants: spawned monitor-control host.
# ---------------------------------------------------------------------------

_MC_SERVICE_ID = "reg04-monitor-control-service"
_MC_SERVICE_PRINCIPAL = "reg04-monitor-control-principal"
_MC_SERVICE_INSTANCE = "reg04-monitor-control-owner"
_MC_SERVICE_SCOPE_REF = "reg04-monitor-control-route-v1"
_MC_CALLER_PRINCIPAL = "reg04-caller-principal"
_MC_INTRUDER_PRINCIPAL = "reg04-intruder-principal"
_MC_CALLER_IDENTITY = WireIdentity(
    WireRole.APPLICATION_CLIENT,
    _MC_CALLER_PRINCIPAL,
    "reg04-monitor-application",
    delegate_ref="operator-delegation:reg04-desk",
)
_MC_INTRUDER_IDENTITY = WireIdentity(
    WireRole.APPLICATION_CLIENT,
    _MC_INTRUDER_PRINCIPAL,
    "reg04-monitor-intruder-application",
    delegate_ref="operator-delegation:reg04-intruder",
)
_MC_OWNER_IDENTITY = WireIdentity(
    WireRole.SERVICE,
    "reg04-risk-owner",
    "account-owner:reg04",
    delegate_ref="monitor-owner-delegation:reg04",
)
_MC_WIRE_SCOPE = WireScope(
    execution_domain_ref="paper:reg04-monitor-control",
    account_ref="reg04-account-control",
)
_MC_RISK_SCOPE = AccountScope("reg04", "reg04-account-control", "simulation")
_MC_ACCOUNT_ID = "reg04-account-control"
_MC_MODE = "simulation"
_MC_COMMAND_SCOPE = "account:reg04-control"
_MC_ISSUER = "operator:reg04-desk"
_MC_KEY_ID = "reg04-monitor-control-key"
_MC_RECEIPT = "d" * 64
_MC_SECRET = b"reg04-local-monitor-control-key-material-v1"
_MC_NOW = 1_800_000_000.0
_MC_EXECUTOR_ID = "risk-owner:reg04"
_MC_RISK_POLICY = RiskPolicy(
    policy_id="reg04-monitor-control-risk-policy-v1",
    max_increase_notional=Decimal("250"),
    max_increase_count=8,
    permit_ttl_seconds=86_400,
)


class _McKeyResolver:
    def __init__(self, key: HmacControlKey) -> None:
        self._key = key

    def resolve(self, key_id: str) -> HmacControlKey | None:
        return self._key if key_id == self._key.key_id else None


class _McPolicyResolver:
    def __init__(self, policy: ControlIssuerPolicy) -> None:
        self._policy = policy

    def resolve(self, issuer: str) -> ControlIssuerPolicy | None:
        return self._policy if issuer == self._policy.issuer else None


def _mc_verifier() -> HmacControlCommandVerifier:
    key = HmacControlKey(
        key_id=_MC_KEY_ID,
        issuer=_MC_ISSUER,
        secret=_MC_SECRET,
        not_before_utc=_MC_NOW - 100,
        expires_at_utc=_MC_NOW + 100,
    )
    policy = ControlIssuerPolicy(
        issuer=_MC_ISSUER,
        policy_id="reg04-monitor-control-policy-v1",
        grants=(
            ControlScopeGrant(
                account_id=_MC_ACCOUNT_ID,
                mode=_MC_MODE,
                scope=_MC_COMMAND_SCOPE,
                actions=(ControlAction.FREEZE, ControlAction.RESUME),
                receipt_digests=(_MC_RECEIPT,),
                manual_resume_allowed=True,
            ),
        ),
        not_before_utc=_MC_NOW - 100,
        expires_at_utc=_MC_NOW + 100,
    )
    return HmacControlCommandVerifier(
        _McKeyResolver(key),
        _McPolicyResolver(policy),
        max_command_ttl_seconds=60,
    )


def _mc_signed_request(
    command_id: str,
    *,
    action: ControlAction = ControlAction.FREEZE,
    issuer_sequence: int = 1,
    account_id: str = _MC_ACCOUNT_ID,
    mode: str = _MC_MODE,
    scope: str = _MC_COMMAND_SCOPE,
    issuer: str = _MC_ISSUER,
) -> dict[str, Any]:
    claims = {
        "schema_version": 2,
        "key_id": _MC_KEY_ID,
        "issuer": issuer,
        "command_id": command_id,
        "account_id": account_id,
        "mode": mode,
        "scope": scope,
        "action": action.value,
        "reason": "reg04 synthetic integration control",
        "receipt_digest": _MC_RECEIPT,
        "issued_at": _MC_NOW - 1,
        "expires_at": _MC_NOW + 20,
        "issuer_sequence": issuer_sequence,
        "manual_resume_authorized": action is ControlAction.RESUME,
        "target_freeze_cause_id": None,
    }
    payload = json.dumps(
        claims,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    key_id = _MC_KEY_ID.encode("ascii")
    signed = CONTROL_COMMAND_HMAC_DOMAIN + len(key_id).to_bytes(2, "big") + key_id + payload
    signature = hmac.new(_MC_SECRET, signed, hashlib.sha256).digest()
    return {
        "schema_version": 1,
        "payload_b64": base64.b64encode(payload).decode("ascii"),
        "signature_b64": base64.b64encode(signature).decode("ascii"),
    }


def _mc_freeze_cause_id(command_id: str) -> str:
    from bt_api_monitor.control import ControlCommand

    return monitor_freeze_cause_id(
        ControlCommand(
            command_id=command_id,
            account_id=_MC_ACCOUNT_ID,
            mode=_MC_MODE,
            scope=_MC_COMMAND_SCOPE,
            action=ControlAction.FREEZE,
            issuer=_MC_ISSUER,
            key_id=_MC_KEY_ID,
            reason="reg04 synthetic integration control",
            receipt_digest=_MC_RECEIPT,
            issued_at=_MC_NOW - 1,
            expires_at=_MC_NOW + 20,
            manual_resume_authorized=False,
            schema_version=2,
        )
    )


def _mc_service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _MC_SERVICE_ID,
        "scope_ref": "reg04-monitor-control-root-v1",
        "transport_ref": "reg04-monitor-listener-v1",
        "registration_policy_ref": "reg04-monitor-registration-v1",
        "service_requirements_ref": "reg04-monitor-requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _MC_SERVICE_INSTANCE,
                    "bind_ports": ["service.monitor_control"],
                    "options": {"role": "control"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "reg04-monitor-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": "reg04-server-curve-v1",
                    "options": {
                        "high_water_mark": 16,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _mc_client_config(endpoint: str, *, application_ref: str) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": application_ref},
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.monitor",
                            "service_id": _MC_SERVICE_ID,
                            "instance_id": _MC_SERVICE_INSTANCE,
                            "scope_ref": _MC_SERVICE_SCOPE_REF,
                            "transport_ref": "reg04-monitor-connect-v1",
                        }
                    ],
                    "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "reg04-monitor-connect-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": "reg04-client-curve-v1",
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "high_water_mark": 16,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _mc_resource_provider(paths: dict[str, str], effects: dict[str, int]):
    def provider(component, _instance_id, _options, _scope):
        if component != "monitor":
            raise AssertionError("the selected service contains only monitor control")

        gate = DurableRiskGate(paths["risk"], _MC_RISK_POLICY, clock=lambda: _MC_NOW)
        real_activate = gate.activate_freeze_once

        def count_freeze_effect(scope, cause_id, reason):
            effects["risk_freeze_calls"] += 1
            return real_activate(scope, cause_id, reason)

        gate.activate_freeze_once = count_freeze_effect

        ledger = DurableControlLedger(paths["control"], clock=lambda: _MC_NOW)
        ingress = ControlIngress(ledger, _mc_verifier(), clock=lambda: _MC_NOW)
        binding = ControlCommandBinding(
            issuer=_MC_ISSUER,
            account_id=_MC_ACCOUNT_ID,
            mode=_MC_MODE,
            scope=_MC_COMMAND_SCOPE,
            actions=frozenset({ControlAction.FREEZE, ControlAction.RESUME}),
        )
        target = MonitorControlTarget(
            ingress=ingress,
            caller_bindings={_MC_CALLER_IDENTITY: binding},
            owner_identity=_MC_OWNER_IDENTITY,
            executor_id=_MC_EXECUTOR_ID,
        )
        service = MonitorControlService({_MC_WIRE_SCOPE: target})

        from bt_api_py.service_monitor_control import (
            MonitorControlRuntime,
            MonitorRiskControlOwner,
        )

        owner = MonitorRiskControlOwner(
            service,
            gate,
            _MC_RISK_SCOPE,
            wire_scope=_MC_WIRE_SCOPE,
            owner_identity=_MC_OWNER_IDENTITY,
            executor_id=_MC_EXECUTOR_ID,
            account_id=_MC_ACCOUNT_ID,
            mode=_MC_MODE,
            command_scope=_MC_COMMAND_SCOPE,
        )
        owners = {_MC_WIRE_SCOPE: owner}
        runtime = MonitorControlRuntime(service, owners)
        if frozenset({"monitor.control"}) != runtime.OPERATIONS:
            raise AssertionError("monitor runtime exposed a non-public operation")

        def close_resources() -> None:
            gate.close()

        return ComponentResources.owned(
            {"service": service, "owners": owners},
            ready=lambda: True,
            close=close_resources,
        )

    return provider


def _mc_host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    paths: dict[str, str],
    effects: dict[str, int],
    server_public: bytes,
    server_secret: bytes,
    caller_public: bytes,
    intruder_public: bytes,
) -> None:
    host = None
    try:
        config = parse_service_config(raw_config)
        caller_resource = ResourceGrant(
            _MC_SERVICE_INSTANCE,
            "service.monitor_control",
            _MC_SERVICE_SCOPE_REF,
            _MC_WIRE_SCOPE,
            frozenset({"monitor.control"}),
        )
        # Intentional wrong-actor setup: the intruder is a fully authenticated
        # principal with the same scope/route grant as the caller, so the only
        # thing that can stop it is the caller_bindings identity check inside
        # the monitor control service.
        intruder_resource = ResourceGrant(
            _MC_SERVICE_INSTANCE,
            "service.monitor_control",
            _MC_SERVICE_SCOPE_REF,
            _MC_WIRE_SCOPE,
            frozenset({"monitor.control"}),
        )
        grants = {
            _MC_CALLER_PRINCIPAL: (PrincipalGrant(_MC_CALLER_IDENTITY, (caller_resource,)),),
            _MC_INTRUDER_PRINCIPAL: (PrincipalGrant(_MC_INTRUDER_IDENTITY, (intruder_resource,)),),
        }
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(WireRole.SERVICE, _MC_SERVICE_PRINCIPAL, _MC_SERVICE_ID),
                scope=_MC_WIRE_SCOPE,
                scope_ref=config.scope_ref,
                scope_version="reg04-monitor-scope-v1",
                security_ref="reg04-server-curve-v1",
                security_version="curve-key-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="reg04-monitor-policy-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="reg04-monitor-requirements-v1",
                curve_public_key=server_public,
                curve_secret_key=server_secret,
                principals={
                    caller_public: _MC_CALLER_PRINCIPAL,
                    intruder_public: _MC_INTRUDER_PRINCIPAL,
                },
                grants=grants,
            ),
            registry_path,
            _mc_resource_provider(paths, effects),
        )
        host.start()
        control.send(("ready", host.endpoint))
        while True:
            if control.poll(0.01):
                command = control.recv()
                if command[0] == "close":
                    break
                if command[0] == "counts":
                    control.send(("counts", dict(effects)))
                    continue
                raise AssertionError("unknown monitor control host command")
            host.serve_once(25)
        host.close()
        control.send(("closed", dict(effects), host.state))
    except BaseException as exc:
        host_state = None
        cleanup_failures: tuple[str, ...] = ()
        if host is not None:
            with suppress(BaseException):
                host_state = host.state
                cleanup_failures = tuple(host.summary.get("cleanup_failures", ()))
            with suppress(BaseException):
                host.close()
        code = getattr(exc, "code", type(exc).__name__)
        with suppress(Exception):
            control.send(("error", code, host_state, cleanup_failures))
        raise
    finally:
        control.close()


def _mc_direct_request(
    transport,
    binding,
    *,
    operation: str,
    arguments: dict[str, Any],
    identity: WireIdentity = _MC_CALLER_IDENTITY,
    scope: WireScope = _MC_WIRE_SCOPE,
):
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
            sent_at_utc=_SENT_AT,
        ),
        {
            "registration_id": binding.registration_id,
            "service_epoch": binding.service_epoch,
            "instance_id": binding.instance_id,
            "arguments": arguments,
        },
    )
    return transport.request(request, timeout=5.0)


def _mc_ledger_row(path: str, command_id: str):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT status, executor_id, outcome, effect_ref "
            "FROM monitor_control_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        return (
            None
            if row is None
            else (
                str(row[0]),
                None if row[1] is None else str(row[1]),
                None if row[2] is None else str(row[2]),
                None if row[3] is None else str(row[3]),
            )
        )
    finally:
        connection.close()


def _mc_client_runtime(
    endpoint: str,
    *,
    application_ref: str,
    identity: WireIdentity,
    curve_keys: tuple[bytes, bytes],
    server_public: bytes,
) -> ServiceClientRuntime:
    config = parse_client_config(_mc_client_config(endpoint, application_ref=application_ref))
    return ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _ref: _MC_WIRE_SCOPE,
        peer_resolver=lambda _service: WireIdentity(
            WireRole.SERVICE, _MC_SERVICE_PRINCIPAL, _MC_SERVICE_ID
        ),
        transport_factory=ZmqTransportFactory(
            {"reg04-client-curve-v1": ClientCurveKeys(server_public, curve_keys[0], curve_keys[1])}
        ),
        requested_operations={"application.monitor": frozenset({"monitor.control"})},
    )


def test_spawned_monitor_control_rejects_wrong_scope_route_and_actor(tmp_path: Path) -> None:
    from tests.unit.test_service_account_integration import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _close_normally,
        _client_command,
        _receive,
        _start_process,
    )

    paths = {
        "risk": str(tmp_path / "reg04-monitor-risk.sqlite3"),
        "control": str(tmp_path / "reg04-monitor-control.sqlite3"),
    }
    effects = {"risk_freeze_calls": 0}
    server_public, server_secret = zmq.curve_keypair()
    caller_public, caller_secret = zmq.curve_keypair()
    intruder_public, intruder_secret = zmq.curve_keypair()
    endpoint = _endpoint()
    service_config = _mc_service_config(endpoint)
    process_context = multiprocessing.get_context("spawn")

    host_record = None
    records: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []
    caller_runtime = intruder_runtime = None
    direct_transport = None
    try:
        host_record = _start_process(
            process_context,
            _mc_host_process,
            (
                service_config,
                str(tmp_path / "reg04-monitor-registry.sqlite3"),
                paths,
                effects,
                server_public,
                server_secret,
                caller_public,
                intruder_public,
            ),
            "reg04-monitor-control-host",
        )
        records.append(host_record)
        ready = _receive(host_record["pipe"])
        assert ready == ("ready", endpoint), ready

        caller_runtime = _mc_client_runtime(
            endpoint,
            application_ref="reg04-monitor-application",
            identity=_MC_CALLER_IDENTITY,
            curve_keys=(caller_public, caller_secret),
            server_public=server_public,
        )
        receipt = caller_runtime.start()
        assert receipt.degraded_count == 0, receipt
        assert len(receipt.bindings) == 1
        binding = receipt.bindings[0]
        assert binding.mount == "application.monitor"
        assert binding.operations == frozenset({"monitor.control"})

        client_config = parse_client_config(
            _mc_client_config(endpoint, application_ref="reg04-monitor-application")
        )
        profile = client_config.communication["profiles"]["reg04-monitor-connect-v1"]
        transport_factory = ZmqTransportFactory(
            {"reg04-client-curve-v1": ClientCurveKeys(server_public, caller_public, caller_secret)}
        )
        direct_transport = transport_factory(
            profile, WireIdentity(WireRole.SERVICE, _MC_SERVICE_PRINCIPAL, _MC_SERVICE_ID)
        )

        # Wrong wire scope: the authenticated caller names another account.
        wrong_wire_scope = _mc_direct_request(
            direct_transport,
            binding,
            operation="monitor.control",
            arguments=_mc_signed_request("freeze-wrong-wire-scope"),
            scope=WireScope(
                execution_domain_ref=_MC_WIRE_SCOPE.execution_domain_ref,
                account_ref="reg04-other-account",
            ),
        )
        assert wrong_wire_scope.payload["ok"] is False
        assert wrong_wire_scope.payload["code"] == "SCOPE_DENIED"

        # No privilege escalation into internal owner operations over the wire.
        internal = _mc_direct_request(
            direct_transport,
            binding,
            operation="monitor.claim",
            arguments={"schema_version": 2, "command_id": "freeze-internal-probe"},
        )
        assert internal.payload["ok"] is False
        assert internal.payload["code"] == "CAPABILITY_UNAVAILABLE"

        # Wrong command route: signed claims for another command scope or
        # another account never reach the owner.
        for arguments in (
            _mc_signed_request("reject-cross-scope", scope="account:reg04-other"),
            _mc_signed_request("reject-cross-account", account_id="reg04-other-account"),
        ):
            with pytest.raises(ServiceRuntimeError) as denied:
                caller_runtime.call("application.monitor", "monitor.control", arguments)
            assert denied.value.code == "SCOPE_DENIED"

        # Wrong actor: a fully authenticated application caller that is not
        # the delegated desk identity is refused before any control effect.
        intruder_runtime = _mc_client_runtime(
            endpoint,
            application_ref="reg04-monitor-intruder-application",
            identity=_MC_INTRUDER_IDENTITY,
            curve_keys=(intruder_public, intruder_secret),
            server_public=server_public,
        )
        intruder_receipt = intruder_runtime.start()
        assert intruder_receipt.degraded_count == 0, intruder_receipt
        with pytest.raises(ServiceRuntimeError) as intruder_denied:
            intruder_runtime.call(
                "application.monitor",
                "monitor.control",
                _mc_signed_request("freeze-intruder", issuer_sequence=2),
            )
        assert intruder_denied.value.code == "SCOPE_DENIED"

        counts = _client_command(host_record, "counts")
        assert counts == ("counts", {"risk_freeze_calls": 0}), counts

        # The one legitimate caller still applies exactly its signed FREEZE.
        expected_cause = _mc_freeze_cause_id("freeze-reg04")
        applied = caller_runtime.call(
            "application.monitor",
            "monitor.control",
            _mc_signed_request("freeze-reg04", issuer_sequence=10),
        )
        assert applied["command_id"] == "freeze-reg04"
        assert applied["status"] == "acknowledged"
        assert applied["outcome"] == "applied"
        assert applied["owner_final"] is True
        assert applied["effect_ref"] == expected_cause
        assert applied["effect_result"]["cause_id"] == expected_cause
        assert applied["effect_result"]["action"] == "freeze"

        direct_transport.close()
        direct_transport = None
        intruder_runtime.close()
        assert intruder_runtime.cleanup_failures == 0
        assert intruder_runtime.state == "closed"
        intruder_runtime = None
        caller_runtime.close()
        assert caller_runtime.cleanup_failures == 0
        assert caller_runtime.state == "closed"
        caller_runtime = None

        closed = _close_normally(host_record, expected_tag="closed")
        assert closed[0] == "closed"
        assert closed[1] == {"risk_freeze_calls": 1}
        assert closed[2] == "closed"

        for rejected in ("freeze-wrong-wire-scope", "reject-cross-scope", "reject-cross-account", "freeze-intruder"):
            assert _mc_ledger_row(paths["control"], rejected) is None
        assert _mc_ledger_row(paths["control"], "freeze-reg04") == (
            "acknowledged",
            _MC_EXECUTOR_ID,
            "applied",
            expected_cause,
        )
    finally:
        for label, closer, current in (
            ("direct_transport", lambda item: item.close(), direct_transport),
            ("intruder_runtime", lambda item: item.close(), intruder_runtime),
            ("caller_runtime", lambda item: item.close(), caller_runtime),
        ):
            if current is None:
                continue
            try:
                closer(current)
            except Exception as error:
                failures.append(f"{label}:{getattr(error, 'code', type(error).__name__)}")
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
