"""One account-owner lifecycle for ordinary and grouped order adapters.

The two existing adapters retain their independent admission and authorization
checks.  This composition adds only a shared in-process serialization point and
keeps the existing store, dispatcher, target set, and writer identity aligned.
The surrounding ServiceComponent remains the sole owner of shared resources.
"""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from types import MappingProxyType
from typing import Any

from bt_api_execution.service_order_entry import ExecutionOrderEntryService

from .service_execution_account_books import ExecutionAccountOwnerService
from .service_execution_group import (
    GROUP_ACCOUNT_OWNER_OPERATIONS,
    GROUP_ORDER_ENTRY_PORT,
    GROUP_ORDER_QUERY_PORT,
    ExecutionGroupOrderEntryService,
    ServiceExecutionGroupError,
)
from .service_registry import ActiveRegistrationContext, ServiceCallContext


_ORDINARY_OPERATIONS = frozenset({"orders.submit", "orders.cancel", "orders.query"})
SUPPORTED_OPERATIONS = _ORDINARY_OPERATIONS | GROUP_ACCOUNT_OWNER_OPERATIONS


def validate_service_options(
    options: Mapping[str, object], bind_ports: tuple[str, ...]
) -> Mapping[str, str]:
    """Accept only the fixed two-port account/group owner role."""

    if (
        not isinstance(options, Mapping)
        or set(options) != {"role"}
        or options.get("role") != "account_group_owner"
        or type(options.get("role")) is not str
        or type(bind_ports) is not tuple
        or len(bind_ports) != 2
        or set(bind_ports) != {GROUP_ORDER_ENTRY_PORT, GROUP_ORDER_QUERY_PORT}
    ):
        raise ServiceExecutionGroupError("INVALID_OPTIONS")
    return MappingProxyType({"role": "account_group_owner"})


class ExecutionAccountGroupOwnerService:
    """Serialize the existing account and group adapters under one owner.

    Both adapters are constructed by the component factory from the same
    store, target mapping, dispatcher and writer id.  Their own authorization
    and admission logic remains authoritative for each operation family.
    """

    def __init__(
        self,
        ordinary: ExecutionAccountOwnerService,
        grouped: ExecutionGroupOrderEntryService,
        *,
        store: object,
        targets: Mapping[object, object],
        dispatch: object,
        writer_id: str,
    ) -> None:
        if type(ordinary) is not ExecutionAccountOwnerService:
            raise ValueError("ordinary account adapter is invalid")
        if type(grouped) is not ExecutionGroupOrderEntryService:
            raise ValueError("group order adapter is invalid")
        if not isinstance(targets, Mapping) or not targets:
            raise ValueError("execution targets are invalid")
        if type(writer_id) is not str or not writer_id or writer_id != writer_id.strip():
            raise ValueError("writer id is invalid")

        ordinary_order_entry = getattr(ordinary, "_orders", None)
        grouped_order_entry = getattr(grouped, "_order_entry", None)
        if (
            type(ordinary_order_entry) is not ExecutionOrderEntryService
            or type(grouped_order_entry) is not ExecutionOrderEntryService
            or ordinary_order_entry._store is not store
            or grouped_order_entry._store is not store
            or ordinary_order_entry._dispatch is not dispatch
            or grouped_order_entry._dispatch is not dispatch
            or ordinary_order_entry._writer_id != writer_id
            or grouped_order_entry._writer_id != writer_id
            or getattr(grouped, "_instance_id", None) != writer_id
            or not self._same_targets(ordinary_order_entry._targets, targets)
            or not self._target_subset(
                grouped_order_entry._targets, ordinary_order_entry._targets
            )
        ):
            raise ValueError("account adapters do not share one execution owner")

        if (
            not _ORDINARY_OPERATIONS.issubset(ordinary.supported_operations)
            or not GROUP_ACCOUNT_OWNER_OPERATIONS.issubset(
                grouped.supported_operations
            )
        ):
            raise ValueError("account adapter operations are incomplete")

        self._ordinary = ordinary
        self._grouped = grouped
        self._lock = RLock()
        self._closed = False

    @staticmethod
    def _same_targets(
        actual: Mapping[object, object], expected: Mapping[object, object]
    ) -> bool:
        if len(actual) != len(expected):
            return False
        return all(actual.get(key) is target for key, target in expected.items())

    @staticmethod
    def _target_subset(
        subset: Mapping[object, object], superset: Mapping[object, object]
    ) -> bool:
        if not subset or len(subset) > len(superset):
            return False
        return all(
            key in superset
            and type(target) is type(superset[key])
            and target == superset[key]
            for key, target in subset.items()
        )

    @property
    def supported_operations(self) -> frozenset[str]:
        # Do not inherit OWNER_CANCEL or other owner-control operations from
        # the ordinary adapter as part of this composition.
        return SUPPORTED_OPERATIONS

    @property
    def ready(self) -> bool:
        with self._lock:
            return not self._closed and self._grouped.ready is True

    def handle(
        self,
        operation: str,
        *,
        context: ServiceCallContext,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        with self._lock:
            if self._closed:
                return {"ok": False, "code": "CLOSED", "result": {}}
            if operation in _ORDINARY_OPERATIONS:
                return self._ordinary.handle(
                    operation,
                    principal_ref=context.principal_ref,
                    identity=context.identity,
                    scope=context.scope,
                    arguments=arguments,
                )
            if operation in GROUP_ACCOUNT_OWNER_OPERATIONS:
                return self._grouped.handle(
                    operation, context=context, arguments=arguments
                )
            return {"ok": False, "code": "UNKNOWN_OPERATION", "result": {}}

    def maintain_registrations(
        self, contexts: tuple[ActiveRegistrationContext, ...]
    ) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("account group owner is closed")
            self._grouped.maintain_registrations(contexts)

    def recover_interrupted_dispatches(self) -> tuple[object, ...]:
        """Run the shared ordinary dispatch recovery once at component startup."""

        with self._lock:
            if self._closed:
                raise RuntimeError("account group owner is closed")
            return self._ordinary.recover_interrupted_dispatches()

    def close(self) -> None:
        """Drop the grouped adapter's registration view, retaining resources."""

        with self._lock:
            if self._closed:
                return
            # Resource teardown belongs to ServiceComponent.  If this fails,
            # leave the wrapper open so the owner-lock callback can be retried.
            self._grouped.close()
            self._closed = True


__all__ = [
    "ExecutionAccountGroupOwnerService",
    "SUPPORTED_OPERATIONS",
    "validate_service_options",
]
