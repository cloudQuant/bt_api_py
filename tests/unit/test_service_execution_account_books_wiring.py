"""Account-book operation opt-in and owner-component startup wiring."""

from __future__ import annotations

import multiprocessing
import socket
from contextlib import suppress
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionScope

from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_DOMAIN = "paper:exe03-account-books-wiring"
_SERVICE_ID = "exe03-account-books-wiring-service"
_SERVICE_PRINCIPAL = "exe03-account-books-wiring-service-principal"
_INSTANCE = "exe03-account-books-wiring-owner"
_SCOPE_REF = "exe03-account-books-wiring-scope-v1"
_SERVER_SECURITY_REF = "exe03-account-books-wiring-server-curve-v1"
_CLIENT_SECURITY_REF = "exe03-account-books-wiring-client-curve-v1"
_REGISTRATION_POLICY_REF = "exe03-account-books-wiring-registration-v1"
_REQUIREMENTS_REF = "exe03-account-books-wiring-requirements-v1"
_ACCOUNT = "exe03-account-books-account"
_STRATEGY = "exe03-account-books-strategy"
_STRATEGY_REF = "exe03-account-books-strategy-scope-v1"
_QUERY_PORT = "service.order_query"
_ENTRY_PORT = "service.order_entry"
_QUERY = "orders.query"
_BOOKS = "execution.account_books.query"
_ENTRY_OPERATIONS = frozenset({"orders.submit", "orders.cancel"})
_SERVER_QUERY_OPERATIONS = frozenset({_QUERY, _BOOKS})
_LEGACY_QUERY_SUBSET = frozenset({_QUERY})
_MAX_MESSAGE_BYTES = 65_536

_ACCOUNT_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT)
_STRATEGY_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_SERVICE_IDENTITY = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _service_config(endpoint: str, bind_ports: tuple[str, ...] = (_QUERY_PORT,)) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": "exe03-account-books-wiring-listener-v1",
        "registration_policy_ref": _REGISTRATION_POLICY_REF,
        "service_requirements_ref": _REQUIREMENTS_REF,
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE,
                    "bind_ports": list(bind_ports),
                    "options": {"role": "account_owner"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "exe03-account-books-wiring-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _client_config(endpoint: str, actor_ref: str) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": actor_ref},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.order_query",
                            "service_id": _SERVICE_ID,
                            "instance_id": _INSTANCE,
                            "scope_ref": _SCOPE_REF,
                            "transport_ref": "exe03-account-books-wiring-connect-v1",
                            "account_ref": _ACCOUNT,
                        }
                    ],
                    "options": {"request_timeout_ms": 2_000},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "exe03-account-books-wiring-connect-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": _CLIENT_SECURITY_REF,
                    "options": {
                        "request_timeout_ms": 2_000,
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _authority(
    config,
    server_keys: tuple[bytes, bytes],
    clients: tuple[tuple[bytes, str, WireIdentity, dict[str, frozenset[str]]], ...],
) -> TrustedServiceAuthority:
    server_public, server_secret = server_keys
    principals = {client_public: principal for client_public, principal, _, _ in clients}
    grants: dict[str, tuple[PrincipalGrant, ...]] = {}
    for _, principal, identity, operations_by_port in clients:
        resources = tuple(
            ResourceGrant(
                _INSTANCE,
                port,
                _STRATEGY_REF if identity.role is WireRole.STRATEGY_CLIENT else _SCOPE_REF,
                _STRATEGY_SCOPE if identity.role is WireRole.STRATEGY_CLIENT else _ACCOUNT_SCOPE,
                operations,
            )
            for port, operations in operations_by_port.items()
        )
        grants[principal] = (PrincipalGrant(identity, resources),)
    return TrustedServiceAuthority(
        identity=_SERVICE_IDENTITY,
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="exe03-account-books-wiring-scope-version-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="exe03-account-books-wiring-curve-version-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="exe03-account-books-wiring-policy-version-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="exe03-account-books-wiring-requirements-version-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals=principals,
        grants=grants,
    )


class _UnusedDispatch:
    def submit(self, _intent):
        raise AssertionError("query-only service dispatched an order")

    def cancel(self, _intent):
        raise AssertionError("query-only service dispatched a cancellation")


class _UnusedAccountBooksSource:
    def authorize_account_view(self, **_kwargs) -> bool:
        return True

    def read_inputs(self, **_kwargs):
        raise AssertionError("capability registration must not read account snapshots")


def _query_only_host_process(control, raw_config, registry_path: str, store_path: str, keys):
    """Start the real host/registry while observing forbidden writer startup hooks."""

    host = None
    store = None
    counters = {"writer_lock_attempts": 0, "recovery_attempts": 0, "resource_closes": 0}
    try:
        from bt_api_execution import SqliteExecutionStore

        from bt_api_py import service_components
        from bt_api_py.service_components import ComponentResources
        from bt_api_py.service_execution_account_books import ExecutionOrderEntryService

        def reject_writer_lock(_store):
            counters["writer_lock_attempts"] += 1
            raise AssertionError("query-only host requested the execution writer lock")

        def reject_recovery(_service):
            counters["recovery_attempts"] += 1
            raise AssertionError("query-only host attempted dispatch recovery")

        service_components._acquire_execution_owner_lock = reject_writer_lock
        ExecutionOrderEntryService.recover_interrupted_dispatches = reject_recovery

        config = parse_service_config(raw_config)
        store = SqliteExecutionStore(store_path)
        targets = {_STRATEGY_SCOPE: ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY)}

        def resource_provider(component, instance_id, _options, _scope):
            if component != "execution" or instance_id != _INSTANCE:
                raise AssertionError("unexpected component resource request")

            def close_resources() -> None:
                counters["resource_closes"] += 1
                store.close()

            return ComponentResources.owned(
                {
                    "store": store,
                    "targets": targets,
                    "dispatch": _UnusedDispatch(),
                    "admission": {"allow_unprotected": True},
                    "account_books": _UnusedAccountBooksSource(),
                },
                ready=lambda: True,
                close=close_resources,
            )

        key_by_name = {name: public_key for name, public_key, _ in keys}
        client_specs = (
            (
                key_by_name["legacy"],
                "exe03-books-legacy-principal",
                WireIdentity(
                    WireRole.APPLICATION_CLIENT,
                    "exe03-books-legacy-principal",
                    "exe03-books-legacy-application",
                ),
                {_QUERY_PORT: _LEGACY_QUERY_SUBSET},
            ),
            (
                key_by_name["authorized"],
                "exe03-books-authorized-principal",
                WireIdentity(
                    WireRole.APPLICATION_CLIENT,
                    "exe03-books-authorized-principal",
                    "exe03-books-authorized-application",
                ),
                {_QUERY_PORT: _SERVER_QUERY_OPERATIONS},
            ),
            (
                key_by_name["denied"],
                "exe03-books-denied-principal",
                WireIdentity(
                    WireRole.APPLICATION_CLIENT,
                    "exe03-books-denied-principal",
                    "exe03-books-denied-application",
                ),
                {_QUERY_PORT: _LEGACY_QUERY_SUBSET},
            ),
        )
        authority = _authority(config, keys[0][1:], client_specs)
        host = ServiceHost(config, authority, registry_path, resource_provider)
        host.start()
        component_summary = host.summary["components"]
        # ServiceHost summaries expose immutable MappingProxyType rows. Reduce
        # them to the public plain-data projection before sending across the
        # multiprocessing pipe.
        capabilities = tuple(
            {
                "port": str(capability["port"]),
                "operations": tuple(sorted(str(op) for op in capability["operations"])),
            }
            for capability in component_summary[0]["capabilities"]
        )
        control.send(("ready", str(host.endpoint), dict(counters), capabilities))
        while True:
            if control.poll(0.01):
                command = control.recv()
                if command[0] == "metrics":
                    control.send(("metrics", dict(counters), str(host.state)))
                    continue
                if command[0] == "close":
                    host.close()
                    control.send(("closed", dict(counters), str(host.state)))
                    return
                raise AssertionError("unknown host command")
            host.serve_once(timeout_ms=20)
    except BaseException as error:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        elif store is not None:
            with suppress(BaseException):
                store.close()
        with suppress(Exception):
            control.send(("host_error", str(getattr(error, "code", type(error).__name__))))
        raise
    finally:
        control.close()


def _client_runtime(
    endpoint: str,
    *,
    identity: WireIdentity,
    client_curve: tuple[bytes, bytes],
    server_public: bytes,
    requested_operations: frozenset[str] | None = None,
) -> ServiceClientRuntime:
    config = parse_client_config(_client_config(endpoint, identity.actor_ref))
    kwargs = {}
    if requested_operations is not None:
        kwargs["requested_operations"] = {"application.order_query": requested_operations}
    return ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _scope_ref: _ACCOUNT_SCOPE,
        peer_resolver=lambda _service_id: _SERVICE_IDENTITY,
        transport_factory=ZmqTransportFactory(
            {
                _CLIENT_SECURITY_REF: ClientCurveKeys(
                    server_public,
                    client_curve[0],
                    client_curve[1],
                )
            }
        ),
        **kwargs,
    )


@pytest.mark.unit
def test_exe03_order_query_runtime_requires_opt_in_and_server_grant(tmp_path) -> None:
    from tests.unit.test_service_account_integration import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _close_normally,
        _receive,
        _start_process,
    )

    server_public, server_secret = zmq.curve_keypair()
    client_keys = {name: zmq.curve_keypair() for name in ("legacy", "authorized", "denied")}
    endpoint = _endpoint()
    raw_config = _service_config(endpoint)
    child_keys = (
        ("server", server_public, server_secret),
        *((name, public_key, secret_key) for name, (public_key, secret_key) in client_keys.items()),
    )
    context = multiprocessing.get_context("spawn")
    host_record = _start_process(
        context,
        _query_only_host_process,
        (
            raw_config,
            str(tmp_path / "exe03-account-books-wiring-registry.sqlite3"),
            str(tmp_path / "exe03-account-books-wiring-execution.sqlite3"),
            child_keys,
        ),
        "exe03-account-books-wiring-query-host",
    )
    runtimes: list[ServiceClientRuntime] = []
    forced: list[str] = []
    failures: list[str] = []
    try:
        ready = _receive(host_record["pipe"])
        assert ready[0] == "ready", ready
        endpoint = ready[1]
        assert ready[2] == {
            "writer_lock_attempts": 0,
            "recovery_attempts": 0,
            "resource_closes": 0,
        }
        assert ready[3] == ({"port": _QUERY_PORT, "operations": (_BOOKS, _QUERY)},)

        legacy_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT,
            "exe03-books-legacy-principal",
            "exe03-books-legacy-application",
        )
        legacy = _client_runtime(
            endpoint,
            identity=legacy_identity,
            client_curve=client_keys["legacy"],
            server_public=server_public,
        )
        runtimes.append(legacy)
        legacy.start()
        assert len(legacy.bindings) == 1
        assert legacy.bindings[0].operations == _LEGACY_QUERY_SUBSET
        with pytest.raises(ServiceRuntimeError) as not_registered:
            legacy.call("application.order_query", _BOOKS, {})
        assert not_registered.value.code == "AMBIGUOUS_OR_MISSING_ROUTE"
        legacy.close()
        runtimes.remove(legacy)

        authorized_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT,
            "exe03-books-authorized-principal",
            "exe03-books-authorized-application",
        )
        authorized = _client_runtime(
            endpoint,
            identity=authorized_identity,
            client_curve=client_keys["authorized"],
            server_public=server_public,
            requested_operations=frozenset({_QUERY, _BOOKS}),
        )
        runtimes.append(authorized)
        authorized.start()
        assert len(authorized.bindings) == 1
        assert authorized.bindings[0].operations == frozenset({_QUERY, _BOOKS})
        authorized.close()
        runtimes.remove(authorized)

        denied_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT,
            "exe03-books-denied-principal",
            "exe03-books-denied-application",
        )
        denied = _client_runtime(
            endpoint,
            identity=denied_identity,
            client_curve=client_keys["denied"],
            server_public=server_public,
            requested_operations=frozenset({_QUERY, _BOOKS}),
        )
        runtimes.append(denied)
        with pytest.raises(ServiceRuntimeError) as no_grant:
            denied.start()
        assert no_grant.value.code == "SCOPE_DENIED"
        assert denied.unknown_registration_count == 0
        assert denied.cleanup_failures == 0
        denied.close()
        runtimes.remove(denied)
        assert denied.state == "closed"

        host_record["pipe"].send(("metrics",))
        metrics = _receive(host_record["pipe"])
        assert metrics == (
            "metrics",
            {
                "writer_lock_attempts": 0,
                "recovery_attempts": 0,
                "resource_closes": 0,
            },
            "started",
        )
        closed = _close_normally(host_record, expected_tag="closed")
        assert closed == (
            "closed",
            {
                "writer_lock_attempts": 0,
                "recovery_attempts": 0,
                "resource_closes": 1,
            },
            "closed",
        )
    finally:
        for runtime in reversed(runtimes):
            with suppress(BaseException):
                runtime.close()
        for record in (host_record,):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "bind_ports", "include_none_source", "include_extra_key", "expected_code"),
    (
        ("none-source", (_QUERY_PORT,), True, False, "INVALID_ACCOUNT_BOOK_RESOURCES"),
        ("extra-query-key", (_QUERY_PORT,), False, True, "RESOURCE_KEYS_MISMATCH"),
        ("books-without-query-port", (_ENTRY_PORT,), False, False, "RESOURCE_KEYS_MISMATCH"),
    ),
)
def test_exe03_account_books_resource_miswiring_fails_closed_and_cleans_up(
    tmp_path,
    monkeypatch,
    case: str,
    bind_ports: tuple[str, ...],
    include_none_source: bool,
    include_extra_key: bool,
    expected_code: str,
) -> None:
    from bt_api_py import service_components
    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_execution_account_books import ExecutionOrderEntryService

    counts = {"writer_lock_attempts": 0, "recovery_attempts": 0, "resource_closes": 0}

    def reject_writer_lock(_store):
        counts["writer_lock_attempts"] += 1
        raise AssertionError("invalid resource configuration acquired a writer lock")

    def reject_recovery(_service):
        counts["recovery_attempts"] += 1
        raise AssertionError("invalid resource configuration attempted recovery")

    monkeypatch.setattr(service_components, "_acquire_execution_owner_lock", reject_writer_lock)
    monkeypatch.setattr(
        ExecutionOrderEntryService, "recover_interrupted_dispatches", reject_recovery
    )

    endpoint = _endpoint()
    raw_config = _service_config(endpoint, bind_ports)
    config = parse_service_config(raw_config)
    server_keys = zmq.curve_keypair()
    client_public, _client_secret = zmq.curve_keypair()
    principal = f"exe03-books-resource-{case}"
    identity = WireIdentity(
        WireRole.APPLICATION_CLIENT,
        principal,
        f"exe03-books-resource-app-{case}",
    )
    operations_by_port = {}
    if _QUERY_PORT in bind_ports:
        operations_by_port[_QUERY_PORT] = _SERVER_QUERY_OPERATIONS
    if _ENTRY_PORT in bind_ports:
        operations_by_port[_ENTRY_PORT] = _ENTRY_OPERATIONS
    authority = _authority(
        config,
        server_keys,
        ((client_public, principal, identity, operations_by_port),),
    )

    resource_values: dict[str, object] = {
        "store": object(),
        "targets": object(),
        "dispatch": object(),
        "admission": {"allow_unprotected": True},
    }
    if _QUERY_PORT in bind_ports:
        resource_values["account_books"] = (
            None if include_none_source else _UnusedAccountBooksSource()
        )
    else:
        # The optional account-book resource is invalid unless the query port
        # is part of this component's declared capability set.
        resource_values["account_books"] = _UnusedAccountBooksSource()
    if include_extra_key:
        resource_values["unexpected"] = object()

    def resource_provider(_component, _instance_id, _options, _scope):
        def close_resource() -> None:
            counts["resource_closes"] += 1

        return ComponentResources.owned(
            resource_values,
            ready=lambda: True,
            close=close_resource,
        )

    host = ServiceHost(
        config,
        authority,
        tmp_path / f"{case}-registry.sqlite3",
        resource_provider,
    )
    with pytest.raises(ServiceHostError) as rejected:
        host.start()
    assert rejected.value.code == expected_code
    assert counts == {
        "writer_lock_attempts": 0,
        "recovery_attempts": 0,
        "resource_closes": 1,
    }
    assert host.state == "failed"
    assert host.summary["cleanup_failures"] == ()
    host.close()
    assert host.state == "closed"
