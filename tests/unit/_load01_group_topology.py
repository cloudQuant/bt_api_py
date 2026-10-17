"""Small typed resource assemblers for the LOAD01 N5 group service graph.

These helpers describe the resources consumed by the production ServiceHost
components.  They deliberately do not construct adapters, registries,
transports, provider services, or coordinator services; ServiceHost remains
the only lifecycle owner for those objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.group_store import SqliteOrderGroupStore
from bt_api_execution.store import SqliteExecutionStore
from bt_api_py.service_execution_group import (
    GroupAccountRoute,
    GroupCoordinatorTarget,
)
from bt_api_py.service_execution_group_authority import ExecutionGroupAuthorityGuard
from bt_api_py.service_execution_group_client import (
    AcceptedGroupBindingStore,
    GroupOriginRunBindingStore,
)
from bt_api_py.service_execution_group_proofs import (
    GroupProofRuntimeBundle,
    GroupProofTarget,
)
from bt_api_py.service_registry import WireScope


class Load01GroupTopologyError(ValueError):
    """Finite construction error without leaking paths or service payloads."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code or code != code.strip():
            raise ValueError("invalid topology error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class N5OwnerGroupResources:
    """Borrowed resource maps for the composite owner and reservation roles."""

    execution_component_resources: Mapping[str, object]
    reservation_component_resources: Mapping[str, object]
    proof_routes: tuple[tuple[WireScope, ExecutionScope], ...]


@dataclass(frozen=True, slots=True)
class N5CoordinatorRouteBinding:
    """One coordinator target's exact downstream account route references."""

    coordinator_scope: WireScope
    coordinator_scope_ref: str
    account_ref: str
    execution_scope: ExecutionScope
    order_scope_ref: str
    query_scope_ref: str
    reservation_scope_ref: str


@dataclass(frozen=True, slots=True)
class N5CoordinatorResources:
    """Resources and immutable route inventory for one shared coordinator."""

    component_resources: Mapping[str, object]
    route_bindings: tuple[N5CoordinatorRouteBinding, ...]


def build_n5_owner_group_resources(
    *,
    store: SqliteExecutionStore,
    targets: Mapping[WireScope, ExecutionScope],
    dispatch: object,
    admission: object,
    accepted_group_store: AcceptedGroupBindingStore,
    group_authority: ExecutionGroupAuthorityGuard,
    group_runtime_bundle: GroupProofRuntimeBundle,
) -> N5OwnerGroupResources:
    """Bind group proof targets to the existing account owner's exact store.

    ``targets`` is the ordinary execution target directory already used by
    this account's ``ExecutionAccountOwnerService``.  Every group proof scope
    must be a subset of that directory and map to the same execution scope;
    its proof target must use this exact SQLite execution store.  The maps are
    passed to the production ``account_group_owner`` and ``group_reservations``
    ServiceHost components without taking lifecycle ownership.
    """

    if type(store) is not SqliteExecutionStore:
        raise Load01GroupTopologyError("EXECUTION_STORE_INVALID")
    if not isinstance(targets, Mapping) or not targets or any(
        type(scope) is not WireScope or type(scope_value) is not ExecutionScope
        for scope, scope_value in targets.items()
    ):
        raise Load01GroupTopologyError("EXECUTION_TARGETS_INVALID")
    if type(accepted_group_store) is not AcceptedGroupBindingStore:
        raise Load01GroupTopologyError("ACCEPTED_GROUP_STORE_INVALID")
    if type(group_authority) is not ExecutionGroupAuthorityGuard:
        raise Load01GroupTopologyError("GROUP_AUTHORITY_INVALID")
    if type(group_runtime_bundle) is not GroupProofRuntimeBundle:
        raise Load01GroupTopologyError("GROUP_RUNTIME_BUNDLE_INVALID")
    if dispatch is None or admission is None:
        raise Load01GroupTopologyError("OWNER_DEPENDENCY_INVALID")

    proof_targets = group_runtime_bundle.proof_targets
    reservation_targets = group_runtime_bundle.reservation_targets
    if (
        not isinstance(proof_targets, Mapping)
        or not isinstance(reservation_targets, Mapping)
        or len(proof_targets) != 1
        or set(proof_targets) != set(reservation_targets)
    ):
        # LOAD01 has one group-capable strategy per account. Keeping this
        # invariant here prevents accidentally attaching another strategy's
        # group proof target to the same account owner.
        raise Load01GroupTopologyError("OWNER_GROUP_SCOPE_CARDINALITY_INVALID")

    proof_routes: list[tuple[WireScope, ExecutionScope]] = []
    for scope, proof_target in proof_targets.items():
        if (
            type(scope) is not WireScope
            or scope.group_id is not None
            or scope.leg_id is not None
            or scope.account_ref is None
            or scope.strategy_ref is None
            or scope not in targets
            or type(proof_target) is not GroupProofTarget
            or proof_target.execution_store is not store
            or targets[scope] != proof_target.execution_scope
            or proof_target.execution_scope.account_ref != scope.account_ref
            or proof_target.execution_scope.strategy_id != scope.strategy_ref
        ):
            raise Load01GroupTopologyError("OWNER_GROUP_SCOPE_BINDING_INVALID")
        proof_routes.append((scope, proof_target.execution_scope))

    execution_resources = MappingProxyType(
        {
            "store": store,
            "targets": targets,
            "dispatch": dispatch,
            "admission": admission,
            "accepted_group_store": accepted_group_store,
            "group_authority": group_authority,
            "group_runtime_bundle": group_runtime_bundle,
        }
    )
    reservation_resources = MappingProxyType(
        {
            "accepted_group_store": accepted_group_store,
            "group_runtime_bundle": group_runtime_bundle,
        }
    )
    return N5OwnerGroupResources(
        execution_component_resources=execution_resources,
        reservation_component_resources=reservation_resources,
        proof_routes=tuple(proof_routes),
    )


def build_n5_coordinator_resources(
    *,
    group_store: object,
    run_bindings: GroupOriginRunBindingStore,
    targets: Mapping[WireScope, GroupCoordinatorTarget],
    expected_target_count: int = 10,
) -> N5CoordinatorResources:
    """Describe one shared coordinator and its single-account routes.

    The target map is keyed by the strategy's full coordinator scope. Each
    target is required to own exactly one account route, matching LOAD01 N5's
    one-group-capable-strategy-per-account assignment. The default enforces
    the full ten-route N5 graph; a focused one-account composition can pass
    ``expected_target_count=1``. ServiceHost constructs the production
    coordinator and its borrowed upstream client from these component
    resources.
    """

    if type(group_store) is not SqliteOrderGroupStore:
        raise Load01GroupTopologyError("COORDINATOR_STORE_INVALID")
    if type(run_bindings) is not GroupOriginRunBindingStore:
        raise Load01GroupTopologyError("ORIGIN_RUN_BINDINGS_INVALID")
    if (
        type(expected_target_count) is not int
        or expected_target_count < 1
        or not isinstance(targets, Mapping)
        or len(targets) != expected_target_count
    ):
        raise Load01GroupTopologyError("N5_COORDINATOR_TARGET_CARDINALITY_INVALID")

    rows: list[N5CoordinatorRouteBinding] = []
    seen_scope_refs: set[str] = set()
    seen_target_scope_refs: set[str] = set()
    seen_accounts: set[str] = set()
    seen_strategies: set[str] = set()
    for scope, target in targets.items():
        if (
            type(scope) is not WireScope
            or scope.group_id is not None
            or scope.leg_id is not None
            or scope.run_id is not None
            or scope.strategy_ref is None
            or scope.portfolio_ref is None
            or len(scope.account_refs) != 1
            or scope.account_ref is not None
            or type(target) is not GroupCoordinatorTarget
            or target.scope_ref in seen_target_scope_refs
            or scope.strategy_ref in seen_strategies
            or len(target.accounts) != 1
        ):
            raise Load01GroupTopologyError("COORDINATOR_TARGET_SCOPE_INVALID")
        account_ref, route = next(iter(target.accounts.items()))
        if (
            type(route) is not GroupAccountRoute
            or account_ref not in scope.account_refs
            or account_ref in seen_accounts
            or route.execution_scope.account_ref != account_ref
            or route.execution_scope.strategy_id != scope.strategy_ref
            or route.order_scope_ref in seen_scope_refs
            or route.query_scope_ref in seen_scope_refs
            or route.reservation_scope_ref in seen_scope_refs
            or len(
                {
                    route.order_scope_ref,
                    route.query_scope_ref,
                    route.reservation_scope_ref,
                }
            )
            != 3
        ):
            raise Load01GroupTopologyError("COORDINATOR_ACCOUNT_ROUTE_INVALID")
        seen_scope_refs.update(
            {
                route.order_scope_ref,
                route.query_scope_ref,
                route.reservation_scope_ref,
            }
        )
        seen_target_scope_refs.add(target.scope_ref)
        seen_accounts.add(account_ref)
        seen_strategies.add(scope.strategy_ref)
        rows.append(
            N5CoordinatorRouteBinding(
                coordinator_scope=scope,
                coordinator_scope_ref=target.scope_ref,
                account_ref=account_ref,
                execution_scope=route.execution_scope,
                order_scope_ref=route.order_scope_ref,
                query_scope_ref=route.query_scope_ref,
                reservation_scope_ref=route.reservation_scope_ref,
            )
        )

    component_resources = MappingProxyType(
        {"store": group_store, "targets": MappingProxyType(dict(targets)), "run_bindings": run_bindings}
    )
    return N5CoordinatorResources(
        component_resources=component_resources,
        route_bindings=tuple(sorted(rows, key=lambda row: row.coordinator_scope.strategy_ref or "")),
    )


__all__ = [
    "Load01GroupTopologyError",
    "N5CoordinatorResources",
    "N5CoordinatorRouteBinding",
    "N5OwnerGroupResources",
    "build_n5_coordinator_resources",
    "build_n5_owner_group_resources",
]
