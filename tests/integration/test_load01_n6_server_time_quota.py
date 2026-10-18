"""Exercise N6 query admission through the real local SDK/Gateway service path."""

from __future__ import annotations

import queue
import socket
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_owner import GatewaySessionOwner
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_operator import GatewayAccountSessionBinding, GatewayOperatorDeployment
from bt_api_py.service_account_certification import (
    ServiceAccountCertificationClient,
    ServiceAccountCertificationError,
)
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.unit._load01_query_quota_probe import (
    MAX_QUEUE_WAIT_SECONDS,
    N6SyntheticServerTimeProvider,
    ServerTimeQuotaProvider,
    capture_two_server_time_queries,
    summarize_server_time_probe,
)
from tests.unit.test_service_gateway_hundred_strategy_integration import (
    _ACCOUNT_KEYS,
    _CountedSyntheticAccountProvider,
    _RESOURCE,
)


_DOMAIN = "synthetic:load01-n6-query-route"
_SERVICE_ID = "load01-n6-query-gateway"
_INSTANCE = "load01-n6-query-gateway-instance"
_SCOPE_REF = "load01-n6-query-account-scope-v1"
_SECURITY_REF = "load01-n6-query-curve-v1"
_SERVER_TRANSPORT = "load01-n6-query-listener-v1"
_CLIENT_TRANSPORT = "load01-n6-query-client-v1"
_ACCOUNT_REF = "account:load01-n6-query-probe"
_SESSION_REF = "session:load01-n6-query-probe-v1"
_STRATEGY_REF = "strategy:load01-n6-query-probe"
_PRINCIPAL_REF = "principal:load01-n6-query-probe"
_SERVICE_PRINCIPAL = "principal:load01-n6-query-service"
_OPERATIONS = frozenset({"account.server_time_probe"})


def _startup_exception_projection(exc: BaseException) -> tuple[dict[str, str | None], ...]:
    """Expose a bounded exception type/code chain without exception messages."""

    pending = [exc]
    seen: set[int] = set()
    projected: list[dict[str, str | None]] = []
    while pending and len(projected) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        exception_type = type(current)
        module_name = getattr(exception_type, "__module__", None)
        qualified_name = getattr(exception_type, "__qualname__", None)
        type_name = (
            f"{module_name}.{qualified_name}"
            if type(module_name) is str and type(qualified_name) is str
            else "unknown"
        )
        try:
            instance_values = vars(current)
        except TypeError:
            instance_values = {}
        code = instance_values.get("code") if type(instance_values) is dict else None
        if (
            type(code) is not str
            or not 1 <= len(code) <= 64
            or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in code)
        ):
            code = None
        projected.append({"type": type_name, "code": code})
        for nested in (current.__cause__, current.__context__):
            if isinstance(nested, BaseException) and id(nested) not in seen:
                pending.append(nested)
    return tuple(projected)


def _free_endpoint() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"
    finally:
        probe.close()


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": _SERVER_TRANSPORT,
        "registration_policy_ref": "load01-n6-query-policy-v1",
        "service_requirements_ref": "load01-n6-query-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE,
                    "bind_ports": ["service.account_read", "service.provider_session"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": _SESSION_REF,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _SERVER_TRANSPORT: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {"high_water_mark": 32, "max_message_bytes": 262_144},
                }
            },
        },
    }


def _client_config(endpoint: str) -> dict[str, Any]:
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
                            "mount": "store.account",
                            "service_id": _SERVICE_ID,
                            "instance_id": _INSTANCE,
                            "scope_ref": _SCOPE_REF,
                            "account_ref": _ACCOUNT_REF,
                            "transport_ref": _CLIENT_TRANSPORT,
                        }
                    ],
                    "options": {"request_timeout_ms": 5_000, "heartbeat_interval_seconds": 5},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _CLIENT_TRANSPORT: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": 5_000,
                        "high_water_mark": 32,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }


def test_n6_server_time_query_is_admitted_then_owner_denied_without_provider_dispatch(
    tmp_path: Path,
) -> None:
    endpoint = _free_endpoint()
    service_config = parse_service_config(_service_config(endpoint))
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    domain_scope = WireScope(_DOMAIN)
    resource_scope = WireScope(_DOMAIN, account_ref=_ACCOUNT_REF)
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF)
    grant = ResourceGrant(
        _INSTANCE,
        "service.account_read",
        _SCOPE_REF,
        WireScope(_DOMAIN, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF),
        _OPERATIONS,
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=domain_scope,
        scope_ref=_SCOPE_REF,
        scope_version="load01-n6-query-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="load01-n6-query-security-v1",
        registration_policy_ref=service_config.registration_policy_ref,
        registration_policy_version="load01-n6-query-policy-v1",
        service_requirements_ref=service_config.service_requirements_ref,
        service_requirements_version="load01-n6-query-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: _PRINCIPAL_REF},
        grants={_PRINCIPAL_REF: (PrincipalGrant(strategy_identity, (grant,)),)},
    )
    journal = GatewayEventJournal(tmp_path / "n6-query-journal.sqlite3")
    account_state_scope = "state:load01-n6-query-account"
    journal_scope = "journal:load01-n6-query-account"
    account_scope = GatewayServiceAccountScope(
        account_ref=_ACCOUNT_REF,
        execution_domain_ref=_DOMAIN,
        journal_account_scope=journal_scope,
        account_read_strategy_scope=account_state_scope,
        shared_account_facts_scope=account_state_scope,
        shared_fact_strategy_refs=frozenset({_STRATEGY_REF}),
    )
    principal = GatewayPrincipal(
        _PRINCIPAL_REF,
        frozenset({journal_scope}),
        frozenset({account_state_scope}),
        frozenset({GatewayCommandKind.READ}),
    )
    owner_provider: dict[str, ServerTimeQuotaProvider] = {}
    raw_provider = _CountedSyntheticAccountProvider(0)
    admission_path = tmp_path / "n6-query-quota.sqlite3"

    def provider_factory(_session_ref: str, _credential: object) -> ServerTimeQuotaProvider:
        provider = ServerTimeQuotaProvider(
            N6SyntheticServerTimeProvider(raw_provider),
            admission_path,
            _ACCOUNT_REF,
        )
        owner_provider["provider"] = provider
        return provider

    startup: queue.Queue[tuple[str, object]] = queue.Queue()
    stop = threading.Event()
    deployment_holder: dict[str, GatewayOperatorDeployment] = {}

    def serve_gateway() -> None:
        deployment: GatewayOperatorDeployment | None = None
        startup_phase = "guard_setup"
        try:
            guard_dir = tmp_path / "n6-query-guard"
            guard_dir.mkdir()
            startup_phase = "account_binding"
            binding = GatewayAccountSessionBinding(
                account_session_ref=_SESSION_REF,
                journal=journal,
                account_key=_ACCOUNT_KEYS[0],
                account_scope=account_scope,
                state_scope=account_state_scope,
                guard=LocalAccountSessionGuard(
                    guard_dir,
                    _ACCOUNT_KEYS[0].account_key_id,
                    "load01-n6-query-owner",
                ),
                required_coverage=(_RESOURCE,),
                principals={_PRINCIPAL_REF: principal},
                refresh_interval_seconds=3_600.0,
                max_staleness_seconds=7_200.0,
                # The provider wrapper treats this timeout as its admission
                # deadline; the SDK RPC keeps its independent five-second limit.
                request_timeout_seconds=MAX_QUEUE_WAIT_SECONDS,
                quota_admission=None,
            )
            startup_phase = "session_owner"
            owner = GatewaySessionOwner(
                {_SESSION_REF: "credential:synthetic"},
                resolve_credential=lambda _ref: object(),
                provider_factory=provider_factory,
            )
            startup_phase = "deployment_construct"
            deployment = GatewayOperatorDeployment(
                service_config,
                authority,
                tmp_path / "n6-query-registry.sqlite3",
                resource_provider=lambda *_args: (_ for _ in ()).throw(
                    AssertionError("query probe bypassed the configured Gateway owner")
                ),
                session_owner=owner,
                account_session=binding,
            )
            deployment_holder["deployment"] = deployment
            startup_phase = "deployment_start"
            deployment.start()
            startup.put(("started", deployment.endpoint))
            startup_phase = "serve_once"
            while not stop.is_set():
                deployment.serve_once(10)
        except BaseException as exc:
            startup.put(
                (
                    "error",
                    {
                        "phase": startup_phase,
                        "exception_chain": _startup_exception_projection(exc),
                    },
                )
            )
        finally:
            if deployment is not None and deployment.state not in {"closed", "failed"}:
                with suppress(BaseException):
                    deployment.close()

    host_thread = threading.Thread(target=serve_gateway, name="load01-n6-query-owner", daemon=True)
    host_thread.start()
    runtime: ServiceClientRuntime | None = None
    client: ServiceAccountCertificationClient | None = None
    try:
        status, value = startup.get(timeout=10.0)
        assert status == "started", value
        assert value == endpoint
        runtime = ServiceClientRuntime(
            parse_client_config(_client_config(endpoint)),
            identity=strategy_identity,
            scope_resolver=lambda _ref: resource_scope,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {_SECURITY_REF: ClientCurveKeys(server_public, client_public, client_secret)}
            ),
            requested_operations={"store.account": _OPERATIONS},
        )
        registration = runtime.start()
        assert registration.degraded_count == 0
        client = ServiceAccountCertificationClient(
            runtime,
            "store.account",
            account_ref=_ACCOUNT_REF,
            expected_account=_ACCOUNT_KEYS[0],
            expected_scope_ref=_SCOPE_REF,
        )
        outcomes = capture_two_server_time_queries(
            client, runtime, _ACCOUNT_REF, ServiceAccountCertificationError
        )
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            try:
                if runtime is not None:
                    runtime.close()
            finally:
                stop.set()
                host_thread.join(10.0)
                assert not host_thread.is_alive(), "N6 synthetic Gateway owner did not close"

    assert deployment_holder["deployment"].state == "closed"
    assert "provider" in owner_provider
    summary = summarize_server_time_probe(
        outcomes,
        owner_provider["provider"].quota_server_time_facts_snapshot(),
    )
    assert summary["status"] == "RUN_PENDING"
    assert summary["provider_server_time_dispatches"] == 1
    assert summary["owner_typed_quota_denials"] == {"DEADLINE_EXCEEDED": 1}
