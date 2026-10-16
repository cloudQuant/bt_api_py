from __future__ import annotations

from dataclasses import replace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError


_GROUP_ENTRY_OPERATIONS = frozenset(
    {"orders.submit_group_leg", "orders.resolve_group_leg"}
)
_GROUP_QUERY_OPERATIONS = frozenset({"orders.query_group_leg"})
_ORDINARY_ENTRY_OPERATIONS = frozenset({"orders.submit", "orders.cancel"})
_OWNER_SERVICE_ID = "account-owner-service"
_OWNER_INSTANCE_ID = "account-owner"
_ORDER_SCOPE_REF = "account-a-orders"
_QUERY_SCOPE_REF = "account-a-order-query"
_RUN_ID = "strategy-run-a"
_COORDINATOR = WireIdentity(
    WireRole.SERVICE, "group-coordinator-principal", "group-coordinator-service"
)
_STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal", "strategy-a")
_LEGACY_GROUP_CLIENT_OPERATIONS = frozenset({"groups.submit", "groups.query"})
_GROUP_CLIENT_CANCEL_OPERATION = "groups.cancel"
_APPLICATION = WireIdentity(
    WireRole.APPLICATION_CLIENT, "application-principal", "application-a"
)
_OWNER = WireIdentity(WireRole.SERVICE, "account-owner-principal", _OWNER_SERVICE_ID)
_GROUP_SCOPE = WireScope(
    "paper-domain",
    account_ref="account-a",
    strategy_ref="strategy-a",
    portfolio_ref="portfolio-a",
)
_APPLICATION_SCOPE = WireScope(
    "paper-domain", account_ref="account-a", manual_book_ref="manual-book-a"
)


def _coordinator_config():
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _COORDINATOR.actor_ref,
            "scope_ref": "coordinator-root-scope",
            "transport_ref": "listener",
            "registration_policy_ref": "coordinator-access-v1",
            "service_requirements_ref": "coordinator-requirements-v1",
            "components": {
                "execution": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": "group-coordinator",
                        "bind_ports": ["service.order_group_entry"],
                        "options": {
                            "role": "group_coordinator",
                            "portfolio_ref": "portfolio-a",
                        },
                    },
                }
            },
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "upstream.orders",
                                "service_id": _OWNER_SERVICE_ID,
                                "instance_id": _OWNER_INSTANCE_ID,
                                "scope_ref": _ORDER_SCOPE_REF,
                                "account_ref": "account-a",
                                "transport_ref": "owner-rpc",
                            },
                            {
                                "mount": "upstream.order_query",
                                "service_id": _OWNER_SERVICE_ID,
                                "instance_id": _OWNER_INSTANCE_ID,
                                "scope_ref": _QUERY_SCOPE_REF,
                                "account_ref": "account-a",
                                "transport_ref": "owner-rpc",
                            },
                        ],
                        "options": {"request_timeout_ms": 1000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "listener": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": "tcp://127.0.0.1:7600"},
                        "security_profile_ref": "listener-security",
                    },
                    "owner-rpc": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7601"},
                        "security_profile_ref": "owner-security",
                    },
                },
            },
        }
    )


def _application_config():
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": "application-a"},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "application.orders",
                                "service_id": _OWNER_SERVICE_ID,
                                "instance_id": _OWNER_INSTANCE_ID,
                                "scope_ref": _ORDER_SCOPE_REF,
                                "account_ref": "account-a",
                                "transport_ref": "owner-rpc",
                            }
                        ]
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "owner-rpc": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7601"},
                        "security_profile_ref": "owner-security",
                    }
                },
            },
        }
    )


def _strategy_group_client_config():
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": _STRATEGY.actor_ref},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.order_groups",
                                "service_id": _COORDINATOR.actor_ref,
                                "instance_id": "group-coordinator",
                                "scope_ref": "group-coordinator-scope",
                                "portfolio_ref": "portfolio-a",
                                "transport_ref": "coordinator-rpc",
                            }
                        ],
                        "options": {"request_timeout_ms": 1000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "coordinator-rpc": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7602"},
                        "security_profile_ref": "coordinator-security",
                    }
                },
            },
        }
    )


class RegistryTransport:
    """In-process wire fake that exercises the real ServiceRegistry protocol."""

    def __init__(
        self,
        registry: ServiceRegistry,
        authenticated_principal: str,
        *,
        response_identity: WireIdentity = _OWNER,
    ) -> None:
        self.registry = registry
        self.authenticated_principal = authenticated_principal
        self.response_identity = response_identity
        self.requests: list[WireEnvelope] = []
        self.closed = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        del timeout
        self.requests.append(request)
        payload = self.registry.handle(self.authenticated_principal, request)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=self.response_identity,
            ),
            payload,
        )

    def close(self) -> None:
        self.closed = True


def _group_registry(path, *, grant_query: bool = True) -> ServiceRegistry:
    resources = [
        ResourceGrant(
            _OWNER_INSTANCE_ID,
            "service.order_entry",
            _ORDER_SCOPE_REF,
            _GROUP_SCOPE,
            _GROUP_ENTRY_OPERATIONS,
        ),
    ]
    if grant_query:
        resources.append(
            ResourceGrant(
                _OWNER_INSTANCE_ID,
                "service.order_query",
                _QUERY_SCOPE_REF,
                _GROUP_SCOPE,
                _GROUP_QUERY_OPERATIONS,
            )
        )
    return ServiceRegistry(
        path,
        service_id=_OWNER_SERVICE_ID,
        capabilities=(
            ServiceCapability(
                _OWNER_INSTANCE_ID,
                "service.order_entry",
                _GROUP_ENTRY_OPERATIONS,
                _GROUP_ENTRY_OPERATIONS,
            ),
            ServiceCapability(
                _OWNER_INSTANCE_ID, "service.order_query", _GROUP_QUERY_OPERATIONS
            ),
        ),
        grants={_COORDINATOR.principal_ref: (PrincipalGrant(_COORDINATOR, tuple(resources)),)},
        policy_version="access-v1",
        requirements_version="requirements-v1",
        trusted_group_write_policies=(
            TrustedGroupWritePolicy(
                _OWNER_INSTANCE_ID,
                _COORDINATOR.principal_ref,
                _COORDINATOR,
                _ORDER_SCOPE_REF,
                _GROUP_SCOPE,
                _RUN_ID,
            ),
        ),
        trusted_group_resolution_policies=(
            TrustedGroupResolutionPolicy(
                _OWNER_INSTANCE_ID,
                _COORDINATOR.principal_ref,
                _COORDINATOR,
                _ORDER_SCOPE_REF,
                _GROUP_SCOPE,
                _RUN_ID,
                "account-a",
            ),
        ),
    )


def _group_runtime(
    registry: ServiceRegistry,
    *,
    requested_operations=None,
) -> tuple[ServiceClientRuntime, RegistryTransport]:
    transport = RegistryTransport(registry, _COORDINATOR.principal_ref)
    runtime = ServiceClientRuntime(
        _coordinator_config(),
        identity=_COORDINATOR,
        scope_resolver=lambda _scope_ref: _GROUP_SCOPE,
        peer_resolver=lambda _service_id: _OWNER,
        transport_factory=lambda *_args: transport,
        requested_operations=(
            requested_operations
            if requested_operations is not None
            else {
                "upstream.orders": _GROUP_ENTRY_OPERATIONS,
                "upstream.order_query": _GROUP_QUERY_OPERATIONS,
            }
        ),
        run_id=_RUN_ID,
    )
    return runtime, transport


def test_group_coordinator_explicit_operations_register_with_matching_ports(tmp_path):
    registry = _group_registry(tmp_path / "group-owner.sqlite")
    runtime, transport = _group_runtime(registry)
    try:
        runtime.start()
        registrations = [
            request.payload
            for request in transport.requests
            if request.header.operation == "service.register"
        ]
        assert {
            (payload["port"], frozenset(payload["operations"])) for payload in registrations
        } == {
            ("service.order_entry", _GROUP_ENTRY_OPERATIONS),
            ("service.order_query", _GROUP_QUERY_OPERATIONS),
        }
        assert {
            (binding.mount, binding.operations) for binding in runtime.bindings
        } == {
            ("upstream.orders", _GROUP_ENTRY_OPERATIONS),
            ("upstream.order_query", _GROUP_QUERY_OPERATIONS),
        }
    finally:
        runtime.close()
        registry.close()


def test_strategy_group_client_defaults_to_legacy_operations_and_cancel_is_opt_in(
    tmp_path,
):
    advertised = frozenset(
        _LEGACY_GROUP_CLIENT_OPERATIONS | {_GROUP_CLIENT_CANCEL_OPERATION}
    )
    registry = ServiceRegistry(
        tmp_path / "group-client-default.sqlite",
        service_id=_COORDINATOR.actor_ref,
        capabilities=(
            ServiceCapability(
                "group-coordinator",
                "service.order_group_entry",
                advertised,
                frozenset({"groups.submit", _GROUP_CLIENT_CANCEL_OPERATION}),
            ),
        ),
        grants={
            _STRATEGY.principal_ref: (
                PrincipalGrant(
                    _STRATEGY,
                    (
                        ResourceGrant(
                            "group-coordinator",
                            "service.order_group_entry",
                            "group-coordinator-scope",
                            _GROUP_SCOPE,
                            _LEGACY_GROUP_CLIENT_OPERATIONS,
                        ),
                    ),
                ),
            )
        },
        policy_version="access-v1",
        requirements_version="requirements-v1",
    )

    def make_runtime(requested_operations=None):
        transport = RegistryTransport(
            registry,
            _STRATEGY.principal_ref,
            response_identity=_COORDINATOR,
        )
        kwargs = {}
        if requested_operations is not None:
            kwargs["requested_operations"] = requested_operations
        runtime = ServiceClientRuntime(
            _strategy_group_client_config(),
            identity=_STRATEGY,
            scope_resolver=lambda _scope_ref: _GROUP_SCOPE,
            peer_resolver=lambda _service_id: _COORDINATOR,
            transport_factory=lambda *_args: transport,
            run_id=_RUN_ID,
            **kwargs,
        )
        return runtime, transport

    runtime, transport = make_runtime()
    try:
        runtime.start()
        registrations = [
            request.payload
            for request in transport.requests
            if request.header.operation == "service.register"
        ]
        assert len(registrations) == 1
        assert registrations[0]["port"] == "service.order_group_entry"
        assert frozenset(registrations[0]["operations"]) == (
            _LEGACY_GROUP_CLIENT_OPERATIONS
        )
        assert runtime.bindings[0].operations == _LEGACY_GROUP_CLIENT_OPERATIONS
    finally:
        runtime.close()

    cancel_runtime, cancel_transport = make_runtime(
        {"broker.order_groups": advertised}
    )
    try:
        with pytest.raises(ServiceRuntimeError) as exc_info:
            cancel_runtime.start()
        assert exc_info.value.code == "SCOPE_DENIED"
        register_requests = [
            request.payload
            for request in cancel_transport.requests
            if request.header.operation == "service.register"
        ]
        assert len(register_requests) == 1
        assert frozenset(register_requests[0]["operations"]) == advertised
    finally:
        cancel_runtime.close()
        registry.close()


@pytest.mark.parametrize(
    ("mount", "operations"),
    [
        ("upstream.orders", _GROUP_QUERY_OPERATIONS),
        ("upstream.order_query", _GROUP_ENTRY_OPERATIONS),
    ],
    ids=("query-op-on-orders-mount", "entry-ops-on-query-mount"),
)
def test_group_leg_operations_are_rejected_on_the_wrong_mount(mount, operations):
    with pytest.raises(ServiceRuntimeError) as exc_info:
        ServiceClientRuntime(
            _coordinator_config(),
            identity=_COORDINATOR,
            scope_resolver=lambda _scope_ref: _GROUP_SCOPE,
            peer_resolver=lambda _service_id: _OWNER,
            transport_factory=lambda *_args: pytest.fail(
                "invalid operation mapping must be rejected before transport creation"
            ),
            requested_operations={mount: operations},
        )
    assert exc_info.value.code == "INVALID_ACTION_REQUEST"


def test_group_leg_registration_still_requires_the_query_resource_grant(tmp_path):
    registry = _group_registry(
        tmp_path / "missing-query-grant.sqlite", grant_query=False
    )
    runtime, transport = _group_runtime(registry)
    try:
        with pytest.raises(ServiceRuntimeError) as exc_info:
            runtime.start()
        assert exc_info.value.code == "SCOPE_DENIED"
        assert any(
            request.header.operation == "service.register"
            and request.payload["port"] == "service.order_query"
            and frozenset(request.payload["operations"]) == _GROUP_QUERY_OPERATIONS
            for request in transport.requests
        )
    finally:
        runtime.close()
        registry.close()


def test_application_order_defaults_do_not_expand_to_group_leg_operations(tmp_path):
    registry = ServiceRegistry(
        tmp_path / "application-defaults.sqlite",
        service_id=_OWNER_SERVICE_ID,
        capabilities=(
            ServiceCapability(
                _OWNER_INSTANCE_ID,
                "service.order_entry",
                _ORDINARY_ENTRY_OPERATIONS | _GROUP_ENTRY_OPERATIONS,
                _ORDINARY_ENTRY_OPERATIONS | _GROUP_ENTRY_OPERATIONS,
            ),
        ),
        grants={
            _APPLICATION.principal_ref: (
                PrincipalGrant(
                    _APPLICATION,
                    (
                        ResourceGrant(
                            _OWNER_INSTANCE_ID,
                            "service.order_entry",
                            _ORDER_SCOPE_REF,
                            _APPLICATION_SCOPE,
                            _ORDINARY_ENTRY_OPERATIONS,
                        ),
                    ),
                ),
            )
        },
        policy_version="access-v1",
        requirements_version="requirements-v1",
    )
    transport = RegistryTransport(registry, _APPLICATION.principal_ref)
    runtime = ServiceClientRuntime(
        _application_config(),
        identity=_APPLICATION,
        scope_resolver=lambda _scope_ref: _APPLICATION_SCOPE,
        peer_resolver=lambda _service_id: _OWNER,
        transport_factory=lambda *_args: transport,
    )
    try:
        runtime.start()
        registration = next(
            request.payload
            for request in transport.requests
            if request.header.operation == "service.register"
        )
        assert registration["port"] == "service.order_entry"
        assert frozenset(registration["operations"]) == _ORDINARY_ENTRY_OPERATIONS
    finally:
        runtime.close()
        registry.close()
