"""One shared production coordinator Host for the LOAD01 N5 group routes."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from decimal import Decimal
from functools import partial
import json
import os
import re
import time
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    PortfolioGroupBudgetPolicy,
    SqliteOrderGroupStore,
    build_instrument_risk_group_budget_quote,
    risk_account_scope_fingerprint,
)
from bt_api_risk import InstrumentRiskMetadata, InstrumentRiskRegistry
import bt_api_py.service_execution_group as execution_group
from bt_api_py.service_components import BorrowedServiceClient, ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_execution_group_client import GroupOriginRunBindingStore
from bt_api_py.service_execution_group import GroupAccountRoute, GroupCoordinatorTarget
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority, TrustedServiceClients
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ZmqTransportFactory

from tests.unit._load01_group_topology import build_n5_coordinator_resources
from tests.unit.test_service_gateway_hundred_strategy_integration import _ACCOUNT_REFS
from tests.unit._load01_group_workload import N5_GROUP_QUANTITY_UNIT, N5_LEG_SPECS
from tests.unit._load01_profiles import load01_profile


_DOMAIN = "paper:load01-synthetic"
_SERVICE_ID = "load01-n5-group-coordinator-service"
_SERVICE_PRINCIPAL = "load01-n5-group-coordinator-principal"
_INSTANCE_ID = "load01-n5-group-coordinator"
_SERVICE_SCOPE_REF = "load01-n5-group-coordinator-root-v1"
_SECURITY_REF = "load01-n5-group-coordinator-curve-v1"
_LISTENER_REF = "load01-n5-group-coordinator-listener-v1"
_REGISTRATION_POLICY_REF = "load01-n5-group-coordinator-policy-v1"
_REQUIREMENTS_REF = "load01-n5-group-coordinator-requirements-v1"
_PORTFOLIO_REF = "load01-n5-portfolio-v1"
_MAX_GROUP_GROSS_NOTIONAL = Decimal("262.9133")
_N5_PROFILE = load01_profile("N5")
_N5_ACCOUNT_COUNT = len(_N5_PROFILE.strategy_indices_by_account)
_N5_GROUPS_PER_ACCOUNT = int(
    _N5_PROFILE.group_targets_per_account_per_second[0] * 1_800
)
_MAX_ACTIVE_GROUPS = _N5_GROUPS_PER_ACCOUNT * _N5_ACCOUNT_COUNT
_MAX_ACTIVE_GROSS_NOTIONAL = _MAX_GROUP_GROSS_NOTIONAL * _MAX_ACTIVE_GROUPS
_REQUEST_TIMEOUT_MS = 5_000
_MAX_MESSAGE_BYTES = 262_144
_MAX_GROUP_LIFETIME_NS = 300_000_000_000
_OWNER_CLIENT_PREFIX = "load01-n5-coordinator-owner-client-"
_OWNER_SECURITY_PREFIX = "load01-n5-coordinator-owner-security-"


class Load01N5CoordinatorError(ValueError):
    """Bounded coordinator construction or lifecycle error."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code or code != code.strip():
            raise ValueError("invalid N5 coordinator error code")
        self.code = code
        super().__init__(code)


def n5_coordinator_service_info(
    endpoint: str,
    server_public: bytes,
    *,
    target_count: int = _N5_ACCOUNT_COUNT,
) -> dict[str, Any]:
    """Return the shared service locator and per-strategy route selectors."""
    if type(endpoint) is not str or not endpoint or type(server_public) is not bytes:
        raise Load01N5CoordinatorError("COORDINATOR_SERVICE_INFO_INVALID")
    if type(target_count) is not int or target_count not in {1, _N5_ACCOUNT_COUNT}:
        raise Load01N5CoordinatorError("COORDINATOR_TARGET_CARDINALITY_INVALID")
    group_strategy_indices = _N5_PROFILE.expected_group_capable_strategies[:target_count]
    return {
        "service_id": _SERVICE_ID,
        "instance_id": _INSTANCE_ID,
        "endpoint": endpoint,
        "security_ref": _SECURITY_REF,
        "server_public": server_public,
        "peer": WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
        "strategy_bindings": {
            strategy_index: {
                "scope_ref": (
                    f"load01-n5-group-scope-a{_N5_PROFILE.account_for_strategy(strategy_index):02d}-v1"
                ),
                "portfolio_ref": _PORTFOLIO_REF,
                "origin_strategy_run_id": (
                    f"load01-n5-owner-run-a{_N5_PROFILE.account_for_strategy(strategy_index):02d}-v1"
                ),
            }
            for strategy_index in group_strategy_indices
        },
    }


def build_n5_group_metadata(*, now_ns: int | None = None) -> tuple[InstrumentRiskMetadata, ...]:
    """Build the fixed synthetic metadata with lifetime beyond the full run."""
    if now_ns is None:
        now_ns = time.time_ns()
    if type(now_ns) is not int or now_ns <= 0:
        raise Load01N5CoordinatorError("GROUP_METADATA_CLOCK_INVALID")
    return tuple(
        InstrumentRiskMetadata(
            instrument=spec.instrument,
            metadata_version=f"load01-n5-{spec.instrument.lower()}-v1",
            as_of_ns=now_ns - 1_000_000_000,
            expires_at_ns=now_ns + 3_600_000_000_000,
            tick_size=Decimal("0.01"),
            quantity_step=spec.quantity_step,
            contract_multiplier=spec.contract_multiplier,
            max_gross_notional=Decimal("100000"),
            taker_fee_bps=spec.taker_fee_bps,
            fixed_fee=spec.fixed_fee,
            max_slippage_bps=spec.max_slippage_bps,
            quantity_unit=N5_GROUP_QUANTITY_UNIT,
            valuation_unit="USD",
            quote_currency="USD",
        )
        for spec in N5_LEG_SPECS
    )


def _profile(
    endpoint: str,
    security_ref: str,
    *,
    server: bool,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    endpoints = {"rpc_bind": endpoint} if server else {"rpc_connect": endpoint}
    return {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": endpoints,
        "security_profile_ref": security_ref,
        "options": options or {"high_water_mark": 64, "max_message_bytes": _MAX_MESSAGE_BYTES},
    }


def build_n5_coordinator_service_config(
    *,
    endpoint: str,
    server_security_ref: str,
    owner_services: Mapping[int, Mapping[str, Any]],
    target_count: int = _N5_ACCOUNT_COUNT,
) -> dict[str, Any]:
    """Build one coordinator config with exact owner route bindings."""
    if type(endpoint) is not str or not endpoint or type(server_security_ref) is not str:
        raise Load01N5CoordinatorError("COORDINATOR_CONFIG_INPUT_INVALID")
    if type(target_count) is not int or target_count not in {1, _N5_ACCOUNT_COUNT}:
        raise Load01N5CoordinatorError("COORDINATOR_OWNER_CARDINALITY_INVALID")
    if tuple(sorted(owner_services)) != tuple(range(target_count)):
        raise Load01N5CoordinatorError("COORDINATOR_OWNER_CARDINALITY_INVALID")
    execution_bindings: list[dict[str, Any]] = []
    reservation_bindings: list[dict[str, Any]] = []
    profiles: dict[str, dict[str, Any]] = {
        _LISTENER_REF: _profile(
            endpoint,
            server_security_ref,
            server=True,
            options={"high_water_mark": 256, "max_message_bytes": _MAX_MESSAGE_BYTES},
        )
    }
    for account_index, owner in sorted(owner_services.items()):
        account_ref = owner.get("account_ref")
        service_id = owner.get("service_id")
        instance_id = owner.get("instance_id")
        owner_endpoint = owner.get("endpoint")
        server_public = owner.get("server_public")
        route = owner.get("n5_group_route")
        if (
            account_ref != _ACCOUNT_REFS[account_index]
            or type(service_id) is not str
            or type(instance_id) is not str
            or type(owner_endpoint) is not str
            or type(server_public) is not bytes
            or not isinstance(route, Mapping)
            or route.get("account_ref") != account_ref
            or type(route.get("reservation_instance_id")) is not str
            or not route["reservation_instance_id"]
        ):
            raise Load01N5CoordinatorError("COORDINATOR_OWNER_ROUTE_INVALID")
        transport_ref = f"{_OWNER_CLIENT_PREFIX}{account_index:02d}-v1"
        security_ref = f"{_OWNER_SECURITY_PREFIX}{account_index:02d}-v1"
        profiles[transport_ref] = _profile(
            owner_endpoint,
            security_ref,
            server=False,
            options={"request_timeout_ms": _REQUEST_TIMEOUT_MS},
        )
        for mount, route_field in (
            ("upstream.orders", "order_scope_ref"),
            ("upstream.order_query", "query_scope_ref"),
        ):
            execution_bindings.append(
                {
                    "mount": mount,
                    "service_id": service_id,
                    "instance_id": instance_id,
                    "scope_ref": route[route_field],
                    "account_ref": account_ref,
                    "transport_ref": transport_ref,
                }
            )
        reservation_bindings.append(
            {
                "mount": "upstream.account_reservations",
                "service_id": service_id,
                "instance_id": route["reservation_instance_id"],
                "scope_ref": route["reservation_scope_ref"],
                "account_ref": account_ref,
                "transport_ref": transport_ref,
            }
        )
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": _LISTENER_REF,
        "registration_policy_ref": _REGISTRATION_POLICY_REF,
        "service_requirements_ref": _REQUIREMENTS_REF,
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE_ID,
                    "bind_ports": ["service.order_group_entry"],
                    "options": {"role": "group_coordinator", "portfolio_ref": _PORTFOLIO_REF},
                },
            }
        },
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": execution_bindings,
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "heartbeat_interval_seconds": 5,
                    },
                },
            },
            "risk": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": reservation_bindings,
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "heartbeat_interval_seconds": 5,
                    },
                },
            },
        },
        "communication": {"config_schema_version": 1, "profiles": profiles},
    }


def _coordinator_target_map(
    routes: Mapping[int, Mapping[str, Any]],
    metadata: tuple[InstrumentRiskMetadata, ...],
    *,
    target_count: int = _N5_ACCOUNT_COUNT,
) -> Mapping[WireScope, GroupCoordinatorTarget]:
    if type(target_count) is not int or target_count not in {1, _N5_ACCOUNT_COUNT}:
        raise Load01N5CoordinatorError("COORDINATOR_TARGET_CARDINALITY_INVALID")
    expected_strategy_indices = _N5_PROFILE.expected_group_capable_strategies[:target_count]
    if tuple(sorted(routes)) != expected_strategy_indices:
        raise Load01N5CoordinatorError("COORDINATOR_TARGET_CARDINALITY_INVALID")
    if type(metadata) is not tuple or not metadata or any(
        type(item) is not InstrumentRiskMetadata for item in metadata
    ):
        raise Load01N5CoordinatorError("COORDINATOR_METADATA_INVALID")
    targets: dict[WireScope, GroupCoordinatorTarget] = {}
    for strategy_index, route_data in sorted(routes.items()):
        account_index = _N5_PROFILE.account_for_strategy(strategy_index)
        account_ref = route_data.get("account_ref")
        strategy_ref = route_data.get("strategy_ref")
        execution_scope = route_data.get("execution_scope")
        route_instruments = route_data.get("instruments")
        scope_ref = route_data.get("scope_ref")
        if (
            type(account_ref) is not str
            or account_ref != _ACCOUNT_REFS[account_index]
            or strategy_ref != f"load01-strategy-{strategy_index:03d}"
            or scope_ref != f"load01-n5-group-scope-a{account_index:02d}-v1"
            or getattr(execution_scope, "account_ref", None) != account_ref
            or getattr(execution_scope, "strategy_id", None) != strategy_ref
            or not isinstance(route_instruments, Mapping)
        ):
            raise Load01N5CoordinatorError("COORDINATOR_STRATEGY_ROUTE_INVALID")
        wire_scope = WireScope(
            _DOMAIN,
            account_refs=(account_ref,),
            strategy_ref=strategy_ref,
            portfolio_ref=_PORTFOLIO_REF,
        )
        target = GroupCoordinatorTarget(
            scope_ref=scope_ref,
            accounts={
                account_ref: GroupAccountRoute(
                    execution_scope=execution_scope,
                    risk_scope_fingerprint=route_data["risk_scope_fingerprint"],
                    reservation_scope_ref=route_data["reservation_scope_ref"],
                    order_scope_ref=route_data["order_scope_ref"],
                    query_scope_ref=route_data["query_scope_ref"],
                    instruments=route_instruments,
                )
            },
            max_group_lifetime_ns=_MAX_GROUP_LIFETIME_NS,
            portfolio_budget_policy=PortfolioGroupBudgetPolicy(
                portfolio_ref=_PORTFOLIO_REF,
                policy_id="load01-n5-group-budget-v1",
                policy_version=1,
                valuation_unit="USD",
                max_group_gross_notional=_MAX_GROUP_GROSS_NOTIONAL,
                max_active_gross_notional=_MAX_ACTIVE_GROSS_NOTIONAL,
                max_active_groups=_MAX_ACTIVE_GROUPS,
            ),
            budget_quote_builder=partial(
                build_instrument_risk_group_budget_quote,
                registry=InstrumentRiskRegistry(metadata),
            ),
        )
        targets[wire_scope] = target
    return MappingProxyType(targets)


def n5_coordinator_host_process(
    control,
    raw_config: dict[str, Any],
    owner_services: dict[int, dict[str, Any]],
    routes: dict[int, dict[str, Any]],
    metadata: tuple[InstrumentRiskMetadata, ...],
    registry_path: str,
    group_store_path: str,
    run_bindings_path: str,
    coordinator_server_keys: tuple[bytes, bytes],
    strategy_client_publics: dict[int, bytes],
    target_count: int = _N5_ACCOUNT_COUNT,
    outbound_diagnostic_path: str | None = None,
) -> None:
    """Run the single production coordinator ServiceHost child."""
    config = parse_service_config(raw_config)
    owner_configs = {
        index: parse_service_config(owner["service_config"])
        for index, owner in owner_services.items()
    }
    resources: dict[str, object] = {}
    host: ServiceHost | None = None
    outbound_diagnostic: dict[str, Any] = {
        "schema_version": 1,
        "max_events": 32,
        "events": [],
        "dropped_events": 0,
    }
    original_borrowed_call = (
        BorrowedServiceClient.call if outbound_diagnostic_path is not None else None
    )
    original_order_port_query = (
        execution_group._OrdersRpcPort.query
        if outbound_diagnostic_path is not None
        else None
    )

    def diagnostic_code(value: object) -> str | None:
        if (
            type(value) is str
            and 1 <= len(value) <= 96
            and value.isascii()
            and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", value) is not None
        ):
            return value
        return None

    def diagnostic_error_type(error: BaseException) -> str:
        name = type(error).__name__
        if (
            type(name) is str
            and 1 <= len(name) <= 64
            and name.isascii()
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) is not None
        ):
            return name
        return "Exception"

    def append_outbound_event(event: dict[str, Any]) -> None:
        events = outbound_diagnostic["events"]
        if len(events) < outbound_diagnostic["max_events"]:
            event["sequence"] = len(events) + outbound_diagnostic["dropped_events"]
            events.append(event)
        else:
            outbound_diagnostic["dropped_events"] += 1

    def persist_outbound_diagnostic() -> None:
        if outbound_diagnostic_path is None:
            return
        try:
            target = Path(outbound_diagnostic_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(outbound_diagnostic, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
        except BaseException:
            # The diagnostic is observational only; it must never change
            # service call results, exception propagation, or close behavior.
            return

    if original_borrowed_call is not None:
        def diagnostic_borrowed_call(self, mount, operation, arguments, **kwargs):
            if (
                type(mount) is not str
                or type(operation) is not str
                or mount not in {"upstream.orders", "upstream.order_query", "upstream.account_reservations"}
                or operation not in {
                    "orders.submit_group_leg",
                    "orders.query_group_leg",
                    "orders.resolve_group_leg",
                    "orders.cancel_group_leg",
                    "orders.query_group_cancel",
                    "reservations.reserve",
                    "reservations.query",
                    "reservations.release",
                }
            ):
                return original_borrowed_call(self, mount, operation, arguments, **kwargs)
            try:
                response = original_borrowed_call(self, mount, operation, arguments, **kwargs)
            except BaseException as error:
                try:
                    error_fields = object.__getattribute__(error, "__dict__")
                    raw_error_code = (
                        error_fields.get("code") if type(error_fields) is dict else None
                    )
                    append_outbound_event(
                        {
                            "mount": mount,
                            "operation": operation,
                            "outcome": "exception",
                            "error_type": diagnostic_error_type(error),
                            "error_code": diagnostic_code(raw_error_code),
                        }
                    )
                except BaseException:
                    pass
                raise
            try:
                is_mapping = isinstance(response, Mapping)
                raw_ok = response.get("ok") if is_mapping else None
                raw_code = response.get("code") if is_mapping else None
                append_outbound_event(
                    {
                        "mount": mount,
                        "operation": operation,
                        "outcome": "response",
                        "ok": raw_ok if type(raw_ok) is bool else None,
                        "code": diagnostic_code(raw_code),
                    }
                )
            except BaseException:
                pass
            return response

        BorrowedServiceClient.call = diagnostic_borrowed_call

    if original_order_port_query is not None:
        def diagnostic_order_port_query(self, intent_id):
            try:
                result = original_order_port_query(self, intent_id)
            except BaseException as error:
                try:
                    error_fields = object.__getattribute__(error, "__dict__")
                    raw_error_code = (
                        error_fields.get("code") if type(error_fields) is dict else None
                    )
                    append_outbound_event(
                        {
                            "boundary": "orders-port-query",
                            "operation": "orders.query_group_leg",
                            "outcome": "exception",
                            "error_type": diagnostic_error_type(error),
                            "error_code": diagnostic_code(raw_error_code),
                        }
                    )
                except BaseException:
                    pass
                raise
            try:
                append_outbound_event(
                    {
                        "boundary": "orders-port-query",
                        "operation": "orders.query_group_leg",
                        "outcome": "returned",
                        "record_present": result is not None,
                    }
                )
            except BaseException:
                pass
            return result

        execution_group._OrdersRpcPort.query = diagnostic_order_port_query

    def scope_for_ref(scope_ref: str) -> WireScope:
        for route in routes.values():
            if scope_ref in {
                route["order_scope_ref"],
                route["query_scope_ref"],
                route["reservation_scope_ref"],
            }:
                return route["owner_scope"]
        raise Load01N5CoordinatorError("UPSTREAM_SCOPE_NOT_FOUND")

    def peer_for_service(service_id: str) -> WireIdentity:
        for owner in owner_services.values():
            if owner["service_id"] == service_id:
                return owner["peer"]
        raise Load01N5CoordinatorError("UPSTREAM_PEER_NOT_FOUND")

    owner_transport_profiles: dict[str, ClientCurveKeys] = {}
    for account_index, owner in owner_services.items():
        owner_transport_profiles[f"{_OWNER_SECURITY_PREFIX}{account_index:02d}-v1"] = (
            ClientCurveKeys(
                server_key=owner["server_public"],
                public_key=coordinator_server_keys[0],
                secret_key=coordinator_server_keys[1],
            )
        )
    trusted_clients = TrustedServiceClients(
        scope_resolver=scope_for_ref,
        peer_resolver=peer_for_service,
        transport_factory=ZmqTransportFactory(owner_transport_profiles),
        requested_operations={
            "upstream.orders": frozenset(
                {
                    "orders.submit_group_leg",
                    "orders.cancel_group_leg",
                    "orders.resolve_group_leg",
                }
            ),
            "upstream.order_query": frozenset(
                {"orders.query_group_leg", "orders.query_group_cancel"}
            ),
            "upstream.account_reservations": frozenset(
                {"reservations.reserve", "reservations.query", "reservations.release"}
            ),
        },
    )
    targets = _coordinator_target_map(routes, metadata, target_count=target_count)
    group_store: SqliteOrderGroupStore | None = None
    run_bindings: GroupOriginRunBindingStore | None = None

    def resource_provider(component, _instance_id, _options, _scope):
        nonlocal group_store, run_bindings
        if component != "execution":
            raise Load01N5CoordinatorError("COORDINATOR_COMPONENT_INVALID")
        group_store = SqliteOrderGroupStore(group_store_path)
        run_bindings = GroupOriginRunBindingStore(run_bindings_path)
        resources.update({"store": group_store, "run_bindings": run_bindings})
        topology_resources = build_n5_coordinator_resources(
            group_store=group_store,
            run_bindings=run_bindings,
            targets=targets,
            expected_target_count=target_count,
        )
        return ComponentResources.borrowed(
            dict(topology_resources.component_resources), ready=lambda: True
        )

    try:
        authority_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        grants: dict[str, tuple[PrincipalGrant, ...]] = {}
        principals: dict[bytes, str] = {}
        for strategy_index, route in routes.items():
            principal_ref = f"load01-principal-{strategy_index:03d}"
            strategy_ref = f"load01-strategy-{strategy_index:03d}"
            strategy_identity = WireIdentity(
                WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref
            )
            scope_ref = route["scope_ref"]
            account_ref = route["account_ref"]
            strategy_scope = WireScope(
                _DOMAIN,
                account_refs=(account_ref,),
                strategy_ref=strategy_ref,
                portfolio_ref=_PORTFOLIO_REF,
            )
            public_key = strategy_client_publics[strategy_index]
            principals[public_key] = principal_ref
            grants[principal_ref] = (
                PrincipalGrant(
                    strategy_identity,
                    (
                        ResourceGrant(
                            _INSTANCE_ID,
                            "service.order_group_entry",
                            scope_ref,
                            strategy_scope,
                            frozenset({"groups.submit", "groups.query"}),
                        ),
                    ),
                ),
            )
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=authority_identity,
                scope=WireScope(_DOMAIN),
                scope_ref=config.scope_ref,
                scope_version="load01-n5-coordinator-scope-v1",
                security_ref=server_security_ref_from_config(raw_config),
                security_version="load01-n5-coordinator-curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="load01-n5-coordinator-policy-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="load01-n5-coordinator-requirements-v1",
                curve_public_key=coordinator_server_keys[0],
                curve_secret_key=coordinator_server_keys[1],
                principals=principals,
                grants=grants,
            ),
            registry_path,
            resource_provider,
            service_directory={
                _SERVICE_ID: config,
                **{owner_configs[i].service_id: owner_configs[i] for i in owner_configs},
            },
            trusted_clients=trusted_clients,
        )
        host.start()
        upstream = host.upstream
        if upstream is None or not upstream.healthy:
            raise Load01N5CoordinatorError("COORDINATOR_UPSTREAM_NOT_HEALTHY")
        active_bindings = tuple(
            (
                binding.mount,
                binding.scope.account_ref,
                binding.scope_ref,
                tuple(sorted(binding.operations)),
            )
            for binding in upstream.bindings
            if binding.active
        )
        if len(active_bindings) != target_count * 3:
            raise Load01N5CoordinatorError("COORDINATOR_UPSTREAM_BINDING_COUNT_INVALID")
        expected_active_bindings = {
            (
                mount,
                route["account_ref"],
                route[scope_key],
                tuple(sorted(operations)),
            )
            for route in routes.values()
            for mount, scope_key, operations in (
                (
                    "upstream.orders",
                    "order_scope_ref",
                    {
                        "orders.submit_group_leg",
                        "orders.cancel_group_leg",
                        "orders.resolve_group_leg",
                    },
                ),
                (
                    "upstream.order_query",
                    "query_scope_ref",
                    {"orders.query_group_leg", "orders.query_group_cancel"},
                ),
                (
                    "upstream.account_reservations",
                    "reservation_scope_ref",
                    {"reservations.reserve", "reservations.query", "reservations.release"},
                ),
            )
        }
        if set(active_bindings) != expected_active_bindings:
            raise Load01N5CoordinatorError("COORDINATOR_UPSTREAM_BINDING_PROFILE_INVALID")
        control.send(("ready", host.endpoint, active_bindings, os.getpid()))
        while True:
            if control.poll(0):
                command = control.recv()
                if command in {"close", ("close",)}:
                    host.close()
                    control.send(("closed", os.getpid(), host.state, upstream.state))
                    return
            host.serve_once(15)
    except BaseException as error:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(error, "code", type(error).__name__), os.getpid()))
        raise
    finally:
        if original_order_port_query is not None:
            execution_group._OrdersRpcPort.query = original_order_port_query
        if original_borrowed_call is not None:
            BorrowedServiceClient.call = original_borrowed_call
        if host is not None and host.state != "closed":
            with suppress(BaseException):
                host.close()
        persist_outbound_diagnostic()
        for resource in (run_bindings, group_store):
            close = getattr(resource, "close", None)
            if callable(close):
                close()
        control.close()


def server_security_ref_from_config(raw_config: Mapping[str, Any]) -> str:
    try:
        listener_ref = raw_config["transport_ref"]
        return raw_config["communication"]["profiles"][listener_ref]["security_profile_ref"]
    except (KeyError, TypeError):
        raise Load01N5CoordinatorError("COORDINATOR_SECURITY_REF_INVALID") from None


__all__ = [
    "Load01N5CoordinatorError",
    "build_n5_group_metadata",
    "build_n5_coordinator_service_config",
    "n5_coordinator_host_process",
    "n5_coordinator_service_info",
]
