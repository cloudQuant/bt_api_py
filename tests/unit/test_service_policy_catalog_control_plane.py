"""CFG06: real risk policy_catalog control plane over a registered ServiceHost.

Covers the iteration-10 clause "服务/application 角色出站 client、R policy_catalog
控制面、类型/版本错误拒绝、目录失联不改变已确认政策、账户 admission 无分析/ML
extra 可用" with a real ZMQ+CURVE ``ServiceHost`` serving the fixed
``policy_catalog`` role:

* an application-client manager runtime publishes immutable version-keyed
  policies and collects its owner-scoped effective receipt over the wire;
* a service-role outbound client (configured ``upstream.risk_policy`` binding)
  reads exactly the published version and is denied any unwritten operation;
* type/version errors are rejected in-band by the adapter with finite codes;
* after the catalog host is closed, the owner-side ``ConfirmedPolicyStore``
  pin still resolves the confirmed version (catalog loss of connection does
  not change an already-confirmed valid policy) and a fresh registration
  cannot reach the catalog;
* the group_policy control-plane role is intentionally recorded here as NOT
  yet an SDK builtin: its config parses, but component validation rejects the
  internal-only ``service.group_admission`` port surface today.
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_components import (
    ComponentResources,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from bt_api_risk.service_component import (
    ConfirmedPolicyStore,
    PolicyCatalogError,
    RiskServiceError,
    validate_service_options,
)

_DOMAIN = "cfg06:policy-governance"
_CATALOG_SERVICE_ID = "cfg06-policy-catalog"
_CATALOG_INSTANCE = "catalog-risk"
_MANAGER_PRINCIPAL = "cfg06-config-manager"
_SYNC_PRINCIPAL = "cfg06-sync-service-principal"
_SYNC_SERVICE_ID = "cfg06-sync-client-service"
_MANAGER_SCOPE = WireScope(_DOMAIN, portfolio_ref="portfolio-01")
_MANAGER_READ_SCOPE = WireScope(_DOMAIN, portfolio_ref="portfolio-02")
_SYNC_SCOPE = WireScope(_DOMAIN, portfolio_ref="portfolio-01")
_MANAGER_OPERATIONS = frozenset(
    {"policy.read", "policy.draft", "policy.approve", "policy.publish", "policy.receipt"}
)
_SYNC_OPERATIONS = frozenset({"policy.read"})
_POLICY_REF = "cfg06-policy-v2"
_POLICY_CONTENT = {"limit": 1, "mode": "bounded"}
_SERVER_OPTIONS = {"high_water_mark": 32, "max_message_bytes": 65_536}


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _base_profile(endpoint: str, security_ref: str, endpoint_key: str) -> dict[str, Any]:
    return {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": {endpoint_key: endpoint},
        "security_profile_ref": security_ref,
        "options": dict(_SERVER_OPTIONS),
    }


def _catalog_service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _CATALOG_SERVICE_ID,
        "scope_ref": "cfg06-policy-catalog-scope-v1",
        "transport_ref": "catalog-listener",
        "registration_policy_ref": "cfg06-catalog-registration-v1",
        "service_requirements_ref": "cfg06-catalog-requirements-v1",
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _CATALOG_INSTANCE,
                    "bind_ports": ["service.risk_policy"],
                    "options": {"role": "policy_catalog"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "catalog-listener": _base_profile(
                    endpoint, "cfg06-catalog-server-v1", "rpc_bind"
                )
            },
        },
    }


def _catalog_authority(keys: dict[str, bytes]) -> TrustedServiceAuthority:
    manager_identity = WireIdentity(
        WireRole.APPLICATION_CLIENT, _MANAGER_PRINCIPAL, "cfg06-config-manager"
    )
    sync_identity = WireIdentity(WireRole.SERVICE, _SYNC_PRINCIPAL, _SYNC_SERVICE_ID)
    return TrustedServiceAuthority(
        identity=WireIdentity(
            WireRole.SERVICE, f"{_CATALOG_SERVICE_ID}-principal", _CATALOG_SERVICE_ID
        ),
        scope=WireScope(_DOMAIN),
        scope_ref="cfg06-policy-catalog-scope-v1",
        scope_version="cfg06-scope-v1",
        security_ref="cfg06-catalog-server-v1",
        security_version="curve-key-v1",
        registration_policy_ref="cfg06-catalog-registration-v1",
        registration_policy_version="cfg06-registration-v1",
        service_requirements_ref="cfg06-catalog-requirements-v1",
        service_requirements_version="cfg06-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={
            keys["manager_public"]: _MANAGER_PRINCIPAL,
            keys["sync_public"]: _SYNC_PRINCIPAL,
        },
        grants={
            _MANAGER_PRINCIPAL: (
                PrincipalGrant(
                    manager_identity,
                    (
                        ResourceGrant(
                            _CATALOG_INSTANCE,
                            "service.risk_policy",
                            "cfg06-manager-policy-v1",
                            _MANAGER_SCOPE,
                            _MANAGER_OPERATIONS,
                        ),
                        ResourceGrant(
                            _CATALOG_INSTANCE,
                            "service.risk_policy",
                            "cfg06-manager-read-v1",
                            _MANAGER_READ_SCOPE,
                            _MANAGER_OPERATIONS,
                        ),
                    ),
                ),
            ),
            _SYNC_PRINCIPAL: (
                PrincipalGrant(
                    sync_identity,
                    (
                        ResourceGrant(
                            _CATALOG_INSTANCE,
                            "service.risk_policy",
                            "cfg06-sync-read-v1",
                            _SYNC_SCOPE,
                            _SYNC_OPERATIONS,
                        ),
                    ),
                ),
            ),
        },
    )


def _manager_client_config(endpoint: str, *, request_timeout_ms: int = 3_000) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": "cfg06-config-manager"},
        "clients": {
            "risk": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.risk_policy",
                            "service_id": _CATALOG_SERVICE_ID,
                            "instance_id": _CATALOG_INSTANCE,
                            "portfolio_ref": "portfolio-01",
                            "scope_ref": "cfg06-manager-policy-v1",
                            "transport_ref": "catalog-connect",
                        },
                        {
                            "mount": "application.risk_policy",
                            "service_id": _CATALOG_SERVICE_ID,
                            "instance_id": _CATALOG_INSTANCE,
                            "portfolio_ref": "portfolio-02",
                            "scope_ref": "cfg06-manager-read-v1",
                            "transport_ref": "catalog-connect",
                        },
                    ],
                    "options": {"request_timeout_ms": request_timeout_ms},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "catalog-connect": _base_profile(
                    endpoint, "cfg06-catalog-server-v1", "rpc_connect"
                )
            },
        },
    }


def _sync_service_config(endpoint: str, listener_endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SYNC_SERVICE_ID,
        "scope_ref": "cfg06-sync-scope-v1",
        "transport_ref": "sync-listener",
        "registration_policy_ref": "cfg06-sync-registration-v1",
        "service_requirements_ref": "cfg06-sync-requirements-v1",
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "sync-catalog-stub",
                    "bind_ports": ["service.risk_policy"],
                    "options": {"role": "policy_catalog"},
                },
            }
        },
        "clients": {
            "risk": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.risk_policy",
                            "service_id": _CATALOG_SERVICE_ID,
                            "instance_id": _CATALOG_INSTANCE,
                            "portfolio_ref": "portfolio-01",
                            "scope_ref": "cfg06-sync-read-v1",
                            "transport_ref": "catalog-connect",
                        }
                    ],
                    "options": {"request_timeout_ms": 3_000},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "sync-listener": _base_profile(
                    listener_endpoint, "cfg06-sync-server-v1", "rpc_bind"
                ),
                "catalog-connect": _base_profile(
                    endpoint, "cfg06-catalog-server-v1", "rpc_connect"
                ),
            },
        },
    }


def _peer_resolver(_service_id: str) -> WireIdentity:
    return WireIdentity(
        WireRole.SERVICE, f"{_CATALOG_SERVICE_ID}-principal", _CATALOG_SERVICE_ID
    )


def _canonical_digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _run_manager_script(
    endpoint: str, keys: dict[str, bytes], done: threading.Event
) -> tuple[list[tuple[Any, ...]], list[BaseException]]:
    """Run the application-client manager on its own owner thread."""

    results: list[tuple[Any, ...]] = []
    errors: list[BaseException] = []
    runtime = None
    try:
        runtime = ServiceClientRuntime(
            parse_client_config(_manager_client_config(endpoint)),
            identity=WireIdentity(
                WireRole.APPLICATION_CLIENT, _MANAGER_PRINCIPAL, "cfg06-config-manager"
            ),
            scope_resolver=lambda ref: {
                "cfg06-manager-policy-v1": _MANAGER_SCOPE,
                "cfg06-manager-read-v1": _MANAGER_READ_SCOPE,
            }[ref],
            peer_resolver=_peer_resolver,
            transport_factory=ZmqTransportFactory(
                {
                    "cfg06-catalog-server-v1": ClientCurveKeys(
                        keys["server_public"], keys["manager_public"], keys["manager_secret"]
                    )
                }
            ),
        )
        runtime.start()
        steps = (
            ("portfolio-01", "policy.draft", {"policy_ref": _POLICY_REF, "content": _POLICY_CONTENT}),
            ("portfolio-01", "policy.draft", {"policy_ref": _POLICY_REF, "content": {"limit": 2}}),
            ("portfolio-01", "policy.publish", {"policy_ref": _POLICY_REF}),
            ("portfolio-01", "policy.approve", {"policy_ref": _POLICY_REF}),
            ("portfolio-01", "policy.approve", {"policy_ref": _POLICY_REF}),
            ("portfolio-01", "policy.publish", {"policy_ref": _POLICY_REF}),
            ("portfolio-01", "policy.read", {"policy_ref": _POLICY_REF}),
            ("portfolio-01", "policy.read", {"policy_ref": "cfg06-policy-latest"}),
            ("portfolio-01", "policy.read", {"policy_ref": "cfg06-policy-v9"}),
            ("portfolio-01", "policy.draft", {"policy_ref": "cfg06-policy", "content": {"a": 1}}),
            ("portfolio-01", "policy.draft", {"policy_ref": 123, "content": {"a": 1}}),
            (
                "portfolio-01",
                "policy.draft",
                {"policy_ref": "cfg06-policy-v3", "content": [1, 2]},
            ),
            (
                "portfolio-01",
                "policy.draft",
                {"policy_ref": "cfg06-policy-v3", "content": {"a": 1}, "note": "extra"},
            ),
            ("portfolio-01", "policy.publish", {"policy_ref": "cfg06-policy-v3"}),
            (
                "portfolio-01",
                "policy.draft",
                {"policy_ref": "cfg06-policy-v4", "content": {"staged": True}},
            ),
            ("portfolio-01", "policy.approve", {"policy_ref": "cfg06-policy-v4"}),
            ("portfolio-01", "policy.read", {"policy_ref": "cfg06-policy-v4"}),
            ("portfolio-01", "policy.receipt", {"policy_ref": "cfg06-policy-v4"}),
            ("portfolio-02", "policy.receipt", {"policy_ref": _POLICY_REF}),
            ("portfolio-02", "policy.read", {"policy_ref": _POLICY_REF}),
            ("portfolio-01", "policy.receipt", {"policy_ref": _POLICY_REF}),
        )
        for portfolio_ref, operation, arguments in steps:
            body = runtime.call(
                "application.risk_policy", operation, arguments, portfolio_ref=portfolio_ref
            )
            results.append(
                (
                    portfolio_ref,
                    operation,
                    arguments.get("policy_ref"),
                    body.get("ok"),
                    body.get("code"),
                    dict(body.get("result") or {}),
                )
            )
    except BaseException as exc:  # pragma: no cover - surfaced to the test
        errors.append(exc)
    finally:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        done.set()
    return results, errors


def _serve_until(host: ServiceHost, done: threading.Event, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not done.is_set():
        if time.monotonic() > deadline:
            raise AssertionError("registered client did not finish within its bound")
        host.serve_once(25)


def _run_sync_script(
    endpoint: str, listener_endpoint: str, keys: dict[str, bytes], done: threading.Event
) -> tuple[list[tuple[Any, ...]], list[BaseException]]:
    """Run the service-role outbound client on its own owner thread."""

    results: list[tuple[Any, ...]] = []
    errors: list[BaseException] = []
    runtime = None
    try:
        runtime = ServiceClientRuntime(
            parse_service_config(_sync_service_config(endpoint, listener_endpoint)),
            identity=WireIdentity(WireRole.SERVICE, _SYNC_PRINCIPAL, _SYNC_SERVICE_ID),
            scope_resolver=lambda _ref: _SYNC_SCOPE,
            peer_resolver=_peer_resolver,
            transport_factory=ZmqTransportFactory(
                {
                    "cfg06-catalog-server-v1": ClientCurveKeys(
                        keys["server_public"], keys["sync_public"], keys["sync_secret"]
                    )
                }
            ),
            requested_operations={"upstream.risk_policy": _SYNC_OPERATIONS},
        )
        runtime.start()
        body = runtime.call(
            "upstream.risk_policy", "policy.read", {"policy_ref": _POLICY_REF}
        )
        results.append(("read", body.get("ok"), body.get("code"), dict(body.get("result") or {})))
        try:
            runtime.call("upstream.risk_policy", "policy.publish", {"policy_ref": _POLICY_REF})
        except ServiceRuntimeError as exc:
            results.append(("publish-denied", exc.code))
    except BaseException as exc:  # pragma: no cover - surfaced to the test
        errors.append(exc)
    finally:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        done.set()
    return results, errors


def test_policy_catalog_real_registration_control_plane_and_outage(tmp_path) -> None:
    endpoint = _endpoint()
    config = parse_service_config(_catalog_service_config(endpoint))
    server_public, server_secret = zmq.curve_keypair()
    manager_public, manager_secret = zmq.curve_keypair()
    sync_public, sync_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "manager_public": manager_public,
        "manager_secret": manager_secret,
        "sync_public": sync_public,
        "sync_secret": sync_secret,
    }
    catalog_path = str(tmp_path / "policy-catalog.sqlite")
    confirmed_path = str(tmp_path / "confirmed-policies.sqlite")

    def resources(component, _instance_id, options, _scope):
        if component != "risk" or options.get("role") != "policy_catalog":
            raise AssertionError("only the fixed policy_catalog route is selected")
        from bt_api_risk.service_component import PolicyCatalogStore

        return ComponentResources.borrowed(
            {"catalog": store},
            ready=lambda: True,
        )

    from bt_api_risk.service_component import PolicyCatalogStore

    store = PolicyCatalogStore(catalog_path)
    host = None
    try:
        host = ServiceHost(
            config, _catalog_authority(keys), str(tmp_path / "registry.sqlite"), resources
        )
        host.start()
        summary = host.summary
        assert summary["state"] == "started"
        catalog_component = summary["components"][0]
        assert catalog_component["capabilities"] == (
            {"port": "service.risk_policy", "operations": ("policy.approve", "policy.draft", "policy.publish", "policy.read", "policy.receipt")},
        )

        manager_done = threading.Event()
        manager_results: list[tuple[Any, ...]] = []
        manager_errors: list[BaseException] = []

        def _manager_runner() -> None:
            results, errors = _run_manager_script(endpoint, keys, manager_done)
            manager_results.extend(results)
            manager_errors.extend(errors)

        manager_thread = threading.Thread(target=_manager_runner, daemon=True)
        manager_thread.start()
        _serve_until(host, manager_done)
        manager_thread.join(10)
        assert not manager_thread.is_alive()
        assert not manager_errors, manager_errors
        assert len(manager_results) == 21
        by_step = manager_results
        digest = _canonical_digest(_POLICY_CONTENT)

        (
            draft,
            reissue,
            publish_before_approve,
            approve,
            approve_twice,
            publish,
            read,
            latest,
            missing,
            unversioned,
            wrong_type_ref,
            wrong_type_content,
            extra_key,
            publish_undrafted,
            staged_draft,
            staged_approve,
            staged_read,
            staged_receipt,
            foreign_receipt,
            foreign_read,
            owner_receipt,
        ) = by_step

        assert draft[3] is True and draft[4] == "OK"
        assert draft[5]["state"] == "draft" and draft[5]["policy_version"] == digest
        assert reissue[3] is False and reissue[4] == "POLICY_ALREADY_PUBLISHED"
        assert publish_before_approve[3] is False and publish_before_approve[4] == "POLICY_NOT_APPROVED"
        assert approve[3] is True and approve[5]["state"] == "approved"
        assert approve_twice[3] is False and approve_twice[4] == "INVALID_STATE"
        assert publish[3] is True and publish[5]["policy_version"] == digest
        assert publish[5]["owner_principal"] == _MANAGER_PRINCIPAL
        assert publish[5]["owner_scope"]["portfolio_ref"] == "portfolio-01"
        assert publish[5]["owner_scope"]["run_id"] is None
        assert publish[5]["published_utc"]
        assert read[3] is True and read[5]["content"] == _POLICY_CONTENT
        assert read[5]["policy_version"] == digest
        assert latest[3] is False and latest[4] == "VERSIONED_REFERENCE_REQUIRED"
        assert missing[3] is False and missing[4] == "POLICY_NOT_FOUND"
        assert unversioned[3] is False and unversioned[4] == "VERSIONED_REFERENCE_REQUIRED"
        assert wrong_type_ref[3] is False and wrong_type_ref[4] == "INVALID_REFERENCE"
        assert wrong_type_content[3] is False and wrong_type_content[4] == "INVALID_CONTENT"
        assert extra_key[3] is False and extra_key[4] == "INVALID_REQUEST"
        assert publish_undrafted[3] is False and publish_undrafted[4] == "POLICY_NOT_FOUND"
        assert staged_draft[3] is True and staged_approve[3] is True
        assert staged_read[3] is False and staged_read[4] == "POLICY_NOT_PUBLISHED"
        assert staged_receipt[3] is False and staged_receipt[4] == "POLICY_NOT_PUBLISHED"
        assert foreign_receipt[3] is False and foreign_receipt[4] == "SCOPE_NOT_OWNER"
        assert foreign_read[3] is True and foreign_read[5]["policy_version"] == digest
        assert owner_receipt[3] is True and owner_receipt[5]["policy_version"] == digest
        receipt = owner_receipt[5]
        assert set(receipt) == {
            "policy_ref",
            "policy_version",
            "owner_principal",
            "owner_scope",
            "published_utc",
        }

        # A configured service-role outbound client registers only its
        # requested ``policy.read`` route (the sync principal's grant covers
        # reads only), so it reads exactly the published version. A publish
        # call has no registered route on this binding: the runtime refuses
        # it locally with the committed route contract's
        # AMBIGUOUS_OR_MISSING_ROUTE before any request is sent; a binding
        # that requested publish could not register at all (SCOPE_DENIED).
        sync_done = threading.Event()
        sync_results: list[tuple[Any, ...]] = []
        sync_errors: list[BaseException] = []

        def _sync_runner() -> None:
            results, errors = _run_sync_script(
                endpoint, _endpoint(), keys, sync_done
            )
            sync_results.extend(results)
            sync_errors.extend(errors)

        sync_thread = threading.Thread(target=_sync_runner, daemon=True)
        sync_thread.start()
        _serve_until(host, sync_done)
        sync_thread.join(10)
        assert not sync_thread.is_alive()
        assert not sync_errors, sync_errors
        (sync_read, sync_publish_denied) = sync_results
        assert sync_read[1] is True and sync_read[2] == "OK"
        assert sync_read[3]["policy_version"] == digest
        assert sync_read[3]["content"] == _POLICY_CONTENT
        assert sync_publish_denied == ("publish-denied", "AMBIGUOUS_OR_MISSING_ROUTE")

        # Owner-side pin: first confirmation of the effective receipt is final.
        pins = ConfirmedPolicyStore(confirmed_path)
        pin = pins.confirm(receipt)
        assert pin["policy_version"] == digest
        assert pins.resolve(_POLICY_REF)["policy_version"] == digest
    finally:
        if host is not None:
            with suppress(BaseException):
                host.close()
        store.close()

    # Catalog outage: the closed catalog cannot be reached any more, yet the
    # confirmed policy still resolves offline from its durable pin.
    outage_runtime = ServiceClientRuntime(
        parse_client_config(_manager_client_config(endpoint, request_timeout_ms=300)),
        identity=WireIdentity(
            WireRole.APPLICATION_CLIENT, _MANAGER_PRINCIPAL, "cfg06-config-manager"
        ),
        scope_resolver=lambda ref: {
            "cfg06-manager-policy-v1": _MANAGER_SCOPE,
            "cfg06-manager-read-v1": _MANAGER_READ_SCOPE,
        }[ref],
        peer_resolver=_peer_resolver,
        transport_factory=ZmqTransportFactory(
            {
                "cfg06-catalog-server-v1": ClientCurveKeys(
                    server_public, manager_public, manager_secret
                )
            }
        ),
    )
    try:
        with pytest.raises(ServiceRuntimeError):
            outage_runtime.start()
    finally:
        with suppress(BaseException):
            outage_runtime.close()

    pins = ConfirmedPolicyStore(confirmed_path)
    try:
        unchanged = pins.resolve(_POLICY_REF)
        assert unchanged["policy_version"] == _canonical_digest(_POLICY_CONTENT)
        assert unchanged["owner_principal"] == _MANAGER_PRINCIPAL
        assert unchanged["owner_scope"]["portfolio_ref"] == "portfolio-01"
        assert pins.resolve(_POLICY_REF)["confirmed_utc"] == pins.resolve(_POLICY_REF)["confirmed_utc"]
    finally:
        pins.close()


def test_group_policy_role_has_no_sdk_builtin_public_port(tmp_path) -> None:
    """Record the current scoped gap: the risk group_policy role is not wired.

    The risk package accepts the role and its versioned ``group_policy_ref``
    (positive parse), and the SDK admits the internal-only
    ``service.group_admission`` surface only as an internal port of the base
    risk component. The fixed role table has no ``group_policy`` entry today,
    so the selection yields no public capability and a host cannot start or
    register it (``NO_SUPPORTED_PUBLIC_PORTS``) before any listener, resource
    provider, or registry work happens.
    """

    raw = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "cfg06-group-policy-service",
        "scope_ref": "cfg06-group-policy-scope-v1",
        "transport_ref": "group-policy-listener",
        "registration_policy_ref": "cfg06-group-policy-registration-v1",
        "service_requirements_ref": "cfg06-group-policy-requirements-v1",
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "group-policy-risk",
                    "bind_ports": ["service.group_admission"],
                    "options": {"role": "group_policy", "group_policy_ref": "cfg06-group-policy-v1"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "group-policy-listener": _base_profile(
                    "tcp://127.0.0.1:9", "cfg06-group-policy-server-v1", "rpc_bind"
                )
            },
        },
    }
    config = parse_service_config(raw)
    specs = component_specs(config.components)
    prepared = validate_components(specs)
    assert len(prepared) == 1
    # The internal-only surface yields no public port operations: nothing can
    # be registered for the role, so no SDK builtin public port exists.
    assert dict(prepared[0].operations_by_port) == {}
    assert dict(prepared[0].write_operations_by_port) == {}

    authority = TrustedServiceAuthority(
        identity=WireIdentity(
            WireRole.SERVICE, "cfg06-group-policy-principal", "cfg06-group-policy-service"
        ),
        scope=WireScope(_DOMAIN),
        scope_ref="cfg06-group-policy-scope-v1",
        scope_version="cfg06-scope-v1",
        security_ref="cfg06-group-policy-server-v1",
        security_version="curve-key-v1",
        registration_policy_ref="cfg06-group-policy-registration-v1",
        registration_policy_version="cfg06-registration-v1",
        service_requirements_ref="cfg06-group-policy-requirements-v1",
        service_requirements_version="cfg06-requirements-v1",
        curve_public_key=b"a" * 40,
        curve_secret_key=b"b" * 40,
        principals={b"c" * 40: "group-policy-peer"},
        grants={"group-policy-peer": ()},
    )
    resource_requests: list[tuple[str, str]] = []

    def resources(component, instance_id, _options, _scope):
        resource_requests.append((component, instance_id))
        raise AssertionError("the internal-only role must never reach resources")

    host = ServiceHost(config, authority, str(tmp_path / "group-registry.sqlite"), resources)
    try:
        with pytest.raises(ServiceHostError) as excinfo:
            host.start()
        assert excinfo.value.code == "NO_SUPPORTED_PUBLIC_PORTS"
    finally:
        with suppress(BaseException):
            host.close()
    assert resource_requests == []


def test_confirmed_policy_store_rejects_conflicts_and_bad_versions(tmp_path) -> None:
    from bt_api_risk.service_component import ConfirmedPolicyStore

    receipt = {
        "policy_ref": "cfg06-policy-v2",
        "policy_version": _canonical_digest(_POLICY_CONTENT),
        "owner_principal": _MANAGER_PRINCIPAL,
        "owner_scope": {
            "execution_domain_ref": _DOMAIN,
            "account_ref": None,
            "account_refs": [],
            "strategy_ref": None,
            "run_id": None,
            "manual_book_ref": None,
            "portfolio_ref": "portfolio-01",
            "group_id": None,
            "leg_id": None,
        },
        "published_utc": "2026-01-01T00:00:00Z",
    }
    store = ConfirmedPolicyStore(str(tmp_path / "confirmed.sqlite"))
    try:
        first = store.confirm(receipt)
        assert store.confirm(receipt)["confirmed_utc"] == first["confirmed_utc"]
        divergent = dict(receipt)
        divergent["policy_version"] = "a" * 64
        with pytest.raises(PolicyCatalogError) as conflict:
            store.confirm(divergent)
        assert conflict.value.code == "POLICY_VERSION_CONFLICT"
        bad_version = dict(receipt)
        bad_version["policy_ref"] = "cfg06-policy-v5"
        bad_version["policy_version"] = "not-a-digest"
        with pytest.raises(PolicyCatalogError) as bad:
            store.confirm(bad_version)
        assert bad.value.code == "INVALID_POLICY_VERSION"
        wrong_keys = dict(receipt)
        wrong_keys["policy_ref"] = "cfg06-policy-v6"
        wrong_keys["extra"] = True
        with pytest.raises(PolicyCatalogError) as keys:
            store.confirm(wrong_keys)
        assert keys.value.code == "INVALID_RECEIPT"
        with pytest.raises(PolicyCatalogError) as unknown:
            store.resolve("cfg06-policy-v7")
        assert unknown.value.code == "POLICY_NOT_CONFIRMED"
        with pytest.raises(PolicyCatalogError) as mutable:
            store.resolve("cfg06-policy-latest")
        assert mutable.value.code == "VERSIONED_REFERENCE_REQUIRED"
    finally:
        store.close()


def test_policy_catalog_options_fail_closed() -> None:
    ports = ("service.risk_policy",)
    with pytest.raises(RiskServiceError) as extra:
        validate_service_options({"role": "policy_catalog", "extra": True}, ports)
    assert extra.value.code == "INVALID_OPTIONS"
    with pytest.raises(RiskServiceError) as unknown:
        validate_service_options({"role": "cfg06-unknown"}, ports)
    assert unknown.value.code == "UNKNOWN_ROLE"
    with pytest.raises(RiskServiceError) as mismatch:
        validate_service_options({"role": "policy_catalog"}, ("service.risk_query",))
    assert mismatch.value.code == "PORT_ROLE_MISMATCH"
    with pytest.raises(RiskServiceError) as unversioned:
        validate_service_options(
            {"role": "account_admission", "account_policy_ref": "cfg06-account-latest"},
            ("service.order_admission",),
        )
    assert unversioned.value.code == "VERSIONED_REFERENCE_REQUIRED"
    accepted = validate_service_options({"role": "policy_catalog"}, ports)
    assert accepted == {"role": "policy_catalog"}


_SUBPROCESS_BOUNDARY = r'''
import importlib, json, os, sys, tempfile

class _Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {"numpy", "scipy", "sklearn", "pandas"}:
            raise ImportError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, _Blocker())

import bt_api_risk
import bt_api_risk.service_component as component
import bt_api_risk.order_admission
import bt_api_risk.core.risk_assessor
import bt_api_risk.portfolio_analytics
from bt_api_risk.portfolio_analytics import PortfolioAnalyticsService
from bt_api_base.service_protocol import WireScope

tmp = tempfile.mkdtemp()
store = component.PolicyCatalogStore(os.path.join(tmp, "catalog.sqlite"))
service = component.PolicyCatalogService(store)
scope = WireScope("cfg06:policy-governance", portfolio_ref="portfolio-01")
draft = service.handle(
    "policy.draft",
    principal_ref="cfg06-config-manager",
    scope=scope,
    arguments={"policy_ref": "cfg06-policy-v2", "content": {"limit": 1}},
)
service.handle("policy.approve", principal_ref="p", scope=scope, arguments={"policy_ref": "cfg06-policy-v2"})
published = service.handle("policy.publish", principal_ref="p", scope=scope, arguments={"policy_ref": "cfg06-policy-v2"})
read = service.handle("policy.read", principal_ref="p", scope=scope, arguments={"policy_ref": "cfg06-policy-v2"})
receipt = service.handle("policy.receipt", principal_ref="p", scope=scope, arguments={"policy_ref": "cfg06-policy-v2"})
unknown = service.handle("policy.erase", principal_ref="p", scope=scope, arguments={"policy_ref": "cfg06-policy-v2"})
assert draft["ok"] is True and published["ok"] is True, (draft, published)
assert read["ok"] is True and read["result"]["policy_version"] == draft["result"]["policy_version"]
assert receipt["ok"] is True and unknown["ok"] is False and unknown["code"] == "UNKNOWN_OPERATION"
pins = component.ConfirmedPolicyStore(os.path.join(tmp, "confirmed.sqlite"))
pins.confirm(receipt["result"])
assert pins.resolve("cfg06-policy-v2")["policy_version"] == draft["result"]["policy_version"]
store.close(); pins.close()

for name, marker in (("RiskManager", "analytics"), ("AnomalyDetector", "ml"), ("RiskEnsembleModel", "ml")):
    try:
        getattr(bt_api_risk, name)
    except ImportError as error:
        assert marker in str(error), (name, str(error))
    else:
        raise AssertionError(f"{name} unexpectedly loaded without its extra")
print("BOUNDARY-OK")
'''


def test_base_install_serves_policy_catalog_without_numpy_or_sklearn(tmp_path) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_BOUNDARY],
        capture_output=True,
        text=True,
        cwd=r"D:\bt_api_py",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "BOUNDARY-OK" in proc.stdout
