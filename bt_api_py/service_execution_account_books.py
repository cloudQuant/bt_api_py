"""Authenticated execution account-book reads composed from trusted resources.

The resource source is installed by the ServiceHost composition root. RPC
arguments never carry snapshots, allocation claims, owner lists, or coverage
flags. Account-wide detail is returned only to a separately authorized
account-level reader; strategy and manual-book requests are projected to their
own virtual book.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    AccountBookReconciliation,
    AccountFinancialProjectionV1,
    AllocationOwnerKind,
    ExecutionAccountBookService,
    ExecutionAccountSnapshot,
    ExecutionAllocationBaseline,
    ExecutionRecord,
    ExecutionScope,
    ExecutionScopeDirectory,
    ExecutionState,
    ExternalActivitySnapshot,
    FinancialDataUnavailableError,
    FinancialOwnerDirectoryV1,
    FinancialOwnerKind,
    FinancialSourceKind,
    OwnerFinancialBookV1,
    SqliteExecutionStore,
)
from bt_api_execution.service_order_entry import (
    CANCEL_OPERATION,
    QUERY_OPERATION,
    SUBMIT_OPERATION,
)
from bt_api_execution.service_order_entry import (
    ExecutionOrderEntryService as _ExecutionOrderEntryService,
)

ACCOUNT_BOOKS_QUERY_OPERATION = "execution.account_books.query"
SUPPORTED_OPERATIONS = frozenset(
    {SUBMIT_OPERATION, CANCEL_OPERATION, QUERY_OPERATION, ACCOUNT_BOOKS_QUERY_OPERATION}
)
_ORDER_OPERATIONS = frozenset({SUBMIT_OPERATION, CANCEL_OPERATION, QUERY_OPERATION})
_MAX_RESULT_BYTES = 48 * 1024


@dataclass(frozen=True, slots=True)
class ExecutionAccountBookReadInputsV1:
    """Typed facts read from one host-bound account-owner resource."""

    directory: ExecutionScopeDirectory
    baseline_snapshot: ExecutionAccountSnapshot
    current_snapshot: ExecutionAccountSnapshot
    allocation_baseline: ExecutionAllocationBaseline | None
    external_activity: ExternalActivitySnapshot | None

    def __post_init__(self) -> None:
        if type(self.directory) is not ExecutionScopeDirectory:
            raise TypeError("invalid trusted owner directory")
        if type(self.baseline_snapshot) is not ExecutionAccountSnapshot:
            raise TypeError("invalid baseline account snapshot")
        if type(self.current_snapshot) is not ExecutionAccountSnapshot:
            raise TypeError("invalid current account snapshot")
        if (
            self.allocation_baseline is not None
            and type(self.allocation_baseline) is not ExecutionAllocationBaseline
        ):
            raise TypeError("invalid allocation baseline")
        if (
            self.external_activity is not None
            and type(self.external_activity) is not ExternalActivitySnapshot
        ):
            raise TypeError("invalid external activity snapshot")


class ExecutionAccountBookSource(Protocol):
    """Trusted service resource for account-level authorization and facts."""

    def authorize_account_view(
        self,
        *,
        principal_ref: str,
        identity: WireIdentity,
        scope: WireScope,
        account_key: str,
    ) -> bool:
        """Authorize a full account projection for this authenticated reader."""

    def authorize_manual_book_view(
        self,
        *,
        principal_ref: str,
        identity: WireIdentity,
        scope: WireScope,
        account_key: str,
        manual_book_ref: str,
    ) -> bool:
        """Authorize a manual-book-only projection."""

    def read_inputs(
        self,
        *,
        account_key: str,
        journal_generation: str,
        execution_cursor: int,
    ) -> ExecutionAccountBookReadInputsV1:
        """Read the account owner directory and snapshots at a trusted cut."""


class ExecutionAccountFinancialDirectorySource(Protocol):
    """Optional trusted resource for the persisted financial projection.

    Implementations must resolve this complete directory from host-bound
    account resources. RPC callers never provide an owner list or baseline.
    """

    def read_financial_owner_directory(self, *, account_key: str) -> FinancialOwnerDirectoryV1:
        """Return the complete, trusted directory used to install the ledger."""


class ExecutionAccountBookReadAdapter:
    """Resolve authorized views and delegate accounting to the package service."""

    def __init__(
        self,
        store: SqliteExecutionStore,
        targets: Mapping[WireScope, ExecutionScope],
        source: ExecutionAccountBookSource,
    ) -> None:
        if type(store) is not SqliteExecutionStore:
            raise TypeError("account-book reads require the account owner's SQLite store")
        if not isinstance(targets, Mapping) or not targets:
            raise TypeError("account-book reads require trusted owner targets")
        copied: dict[WireScope, ExecutionScope] = {}
        for wire_scope, execution_scope in targets.items():
            if type(wire_scope) is not WireScope or type(execution_scope) is not ExecutionScope:
                raise TypeError("invalid trusted owner target")
            if wire_scope.run_id is not None:
                raise TypeError("owner targets must be run-independent templates")
            if wire_scope.account_ref != execution_scope.account_ref:
                raise TypeError("owner target crosses account scope")
            if (wire_scope.strategy_ref is None) == (wire_scope.manual_book_ref is None):
                raise TypeError("owner target requires exactly one strategy or manual book")
            copied[wire_scope] = execution_scope
        account_keys = {execution_scope.account_key for execution_scope in copied.values()}
        account_refs = {execution_scope.account_ref for execution_scope in copied.values()}
        domains = {wire_scope.execution_domain_ref for wire_scope in copied}
        if len(account_keys) != 1 or len(account_refs) != 1 or len(domains) != 1:
            raise TypeError("one account-book service must bind exactly one account")
        if not callable(getattr(source, "read_inputs", None)) or not callable(
            getattr(source, "authorize_account_view", None)
        ):
            raise TypeError("account-book source is not a trusted read resource")
        self._store = store
        self._targets = MappingProxyType(copied)
        self._source = source
        self._service = ExecutionAccountBookService(store)
        self._account_key = next(iter(account_keys))
        self._account_ref = next(iter(account_refs))
        self._execution_domain_ref = next(iter(domains))

    def handle(
        self,
        operation: str,
        *,
        principal_ref: str,
        identity: WireIdentity,
        scope: WireScope,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Return one complete account view or a caller's single virtual book."""

        if operation != ACCOUNT_BOOKS_QUERY_OPERATION:
            return _reply(False, "UNKNOWN_OPERATION", {})
        if not _is_principal(principal_ref) or type(identity) is not WireIdentity:
            return _reply(False, "SCOPE_DENIED", {})
        if type(scope) is not WireScope or not isinstance(arguments, Mapping):
            return _reply(False, "INVALID_REQUEST", {})
        if arguments:
            # All cut points and completeness facts come from the bound source.
            return _reply(False, "INVALID_REQUEST", {})
        if (
            scope.execution_domain_ref != self._execution_domain_ref
            or scope.account_ref != self._account_ref
            or scope.account_refs
        ):
            return _reply(False, "SCOPE_DENIED", {})

        view = self._authorized_view(principal_ref, identity, scope)
        if view is None:
            return _reply(False, "SCOPE_DENIED", {})
        try:
            journal = self._store.journal_source_identity()
            journal_generation = journal["generation"]
            if type(journal_generation) is not str or not journal_generation:
                return _reply(False, "UNAVAILABLE", {})
            bounds = self._store.execution_outbox_bounds()
            if type(bounds) is not tuple or len(bounds) != 4:
                return _reply(False, "UNAVAILABLE", {})
            execution_cursor = 0 if bounds[2] is None else bounds[2]
            inputs = self._source.read_inputs(
                account_key=self._account_key,
                journal_generation=journal_generation,
                execution_cursor=execution_cursor,
            )
        except Exception:
            return _reply(False, "UNAVAILABLE", {})
        if type(inputs) is not ExecutionAccountBookReadInputsV1:
            return _reply(False, "UNAVAILABLE", {})
        if (
            inputs.current_snapshot.journal_generation != journal_generation
            or inputs.current_snapshot.execution_cursor != execution_cursor
        ):
            # The package reconciler intentionally accepts historical cursors
            # for partial analysis. This RPC promises a current account view,
            # so its trusted source must bind the exact live journal cut.
            return _reply(False, "UNAVAILABLE", {})
        if not self._directory_matches_targets(inputs.directory):
            return _reply(False, "UNAVAILABLE", {})
        try:
            reconciliation = self._service.reconcile(
                inputs.directory,
                inputs.baseline_snapshot,
                inputs.current_snapshot,
                allocation_baseline=inputs.allocation_baseline,
                external_activity=inputs.external_activity,
            )
            financial = self._read_financial_projection(
                view,
                journal_generation=journal_generation,
                execution_cursor=execution_cursor,
            )
            # The package reconciler has its own before/after outbox check, but
            # its initial sample can occur after the trusted snapshot source
            # captured ``execution_cursor``. Bind both phases to this adapter's
            # original cut so a newly appended row cannot make a stale snapshot
            # look complete.
            final_journal = self._store.journal_source_identity()
            final_bounds = self._store.execution_outbox_bounds()
            if final_journal.get("generation") != journal_generation or final_bounds != bounds:
                return _reply(False, "UNAVAILABLE", {})
            result = (
                _full_projection(reconciliation)
                if view is _FULL_ACCOUNT_VIEW
                else _owner_projection(reconciliation, view)
            )
            # The persisted financial ledger is a separately sourced cut. It
            # is deliberately nested and labelled so callers cannot mistake
            # it for the legacy reconciliation's provider/activity snapshot.
            result["financial"] = financial
            projected = _json_value(result)
            encoded = json.dumps(projected, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except Exception:
            return _reply(False, "UNAVAILABLE", {})
        if len(encoded) > _MAX_RESULT_BYTES:
            # This surface does not silently truncate a book or its coverage.
            return _reply(False, "REPLY_TOO_LARGE", {})
        return _reply(True, "OK", projected)

    def _read_financial_projection(
        self,
        view: tuple[AllocationOwnerKind, str],
        *,
        journal_generation: str,
        execution_cursor: int,
    ) -> Mapping[str, Any]:
        read_directory = getattr(self._source, "read_financial_owner_directory", None)
        if not callable(read_directory):
            # Keep existing resource bundles usable while making the absence
            # of this optional view explicit to authorized query callers.
            return _financial_unavailable("FINANCIAL_DIRECTORY_UNAVAILABLE")
        try:
            directory = read_directory(account_key=self._account_key)
        except Exception:
            return _financial_unavailable("FINANCIAL_DIRECTORY_UNAVAILABLE")
        if (
            type(directory) is not FinancialOwnerDirectoryV1
            or directory.account_key != self._account_key
            or not directory.complete
            or not _financial_directory_matches_targets(directory, self._targets)
        ):
            return _financial_unavailable("FINANCIAL_DIRECTORY_MISMATCH")

        try:
            projection = self._store.read_account_financial_projection(
                self._account_key,
                expected_directory=directory,
            )
        except FinancialDataUnavailableError:
            return _financial_unavailable("PERSISTED_PROJECTION_UNAVAILABLE")
        except Exception:
            return _financial_unavailable("PERSISTED_PROJECTION_UNAVAILABLE")
        if (
            type(projection) is not AccountFinancialProjectionV1
            or not projection.complete
            or projection.cut.account_key != self._account_key
            or projection.cut.owner_directory_revision != directory.revision
        ):
            return _financial_unavailable("INCOMPLETE_PERSISTED_PROJECTION")

        try:
            execution_watermark = projection.cut.watermark(FinancialSourceKind.EXECUTION_OUTBOX)
        except Exception:
            return _financial_unavailable("PERSISTED_PROJECTION_UNAVAILABLE")
        if (
            execution_watermark.generation != journal_generation
            or execution_watermark.cursor != execution_cursor
        ):
            return _financial_unavailable("FINANCIAL_EXECUTION_CUT_MISMATCH")

        expected_owner_keys = {item.key for item in directory.owners}
        book_keys: list[tuple[str, str]] = []
        for book in projection.books:
            if (
                type(book) is not OwnerFinancialBookV1
                or book.ledger_cut != projection.cut
                or book.owner.key not in expected_owner_keys
            ):
                return _financial_unavailable("INCOMPLETE_PERSISTED_PROJECTION")
            book_keys.append(book.owner.key)
        if len(book_keys) != len(set(book_keys)) or set(book_keys) != expected_owner_keys:
            return _financial_unavailable("INCOMPLETE_PERSISTED_PROJECTION")

        cut_facts = {
            "source": "PERSISTED_EXECUTION_FINANCIAL_PROJECTION",
            "freshness": "PERSISTED_CUT_NOT_LIVE_SOURCE_VERIFIED",
            "cut_relationship": "INDEPENDENT_OF_LEGACY_ACCOUNT_BOOK_CUT",
            "complete": projection.complete,
            "ledger_cut": projection.cut,
        }
        if view is _FULL_ACCOUNT_VIEW:
            return {
                "available": True,
                **cut_facts,
                "state": projection.state,
                "books": projection.books,
                "unallocated": {
                    "cash": projection.unallocated_cash_residual,
                    "positions": projection.unallocated_position_residual,
                    "fees": projection.unallocated_fee_residual,
                },
            }

        owner_kind, owner_ref = view
        try:
            financial_kind = FinancialOwnerKind(owner_kind.value)
        except (TypeError, ValueError):
            return _financial_unavailable("OWNER_FINANCIAL_BOOK_UNAVAILABLE")
        book = next(
            (
                item
                for item in projection.books
                if item.owner.key == (financial_kind.value, owner_ref)
            ),
            None,
        )
        if book is None:
            return _financial_unavailable("OWNER_FINANCIAL_BOOK_UNAVAILABLE")
        return {
            "available": True,
            **cut_facts,
            "view": "OWNER_BOOK",
            "owner_kind": financial_kind.value,
            "owner_ref": owner_ref,
            # Account-wide projection state and UNALLOCATED residuals are
            # intentionally absent from strategy/manual views.
            "books": (book,),
        }

    def _authorized_view(
        self, principal_ref: str, identity: WireIdentity, scope: WireScope
    ) -> tuple[AllocationOwnerKind, str] | None:
        if _is_account_scope(scope):
            if identity.role is not WireRole.APPLICATION_CLIENT:
                return None
            try:
                allowed = self._source.authorize_account_view(
                    principal_ref=principal_ref,
                    identity=identity,
                    scope=scope,
                    account_key=self._account_key,
                )
            except Exception:
                return None
            return None if allowed is not True else _FULL_ACCOUNT_VIEW

        target = _resolve_target(self._targets, scope)
        if target is None:
            return None
        wire_scope, execution_scope = target
        if wire_scope.strategy_ref is not None:
            if (
                identity.role is not WireRole.STRATEGY_CLIENT
                or identity.actor_ref != wire_scope.strategy_ref
                or scope.strategy_ref != wire_scope.strategy_ref
                or scope.manual_book_ref is not None
            ):
                return None
            return AllocationOwnerKind.STRATEGY, execution_scope.strategy_id

        if (
            identity.role is not WireRole.APPLICATION_CLIENT
            or scope.manual_book_ref != wire_scope.manual_book_ref
            or not callable(getattr(self._source, "authorize_manual_book_view", None))
        ):
            return None
        try:
            allowed = self._source.authorize_manual_book_view(
                principal_ref=principal_ref,
                identity=identity,
                scope=scope,
                account_key=self._account_key,
                manual_book_ref=wire_scope.manual_book_ref,
            )
        except Exception:
            return None
        if allowed is not True:
            return None
        return AllocationOwnerKind.MANUAL_BOOK, wire_scope.manual_book_ref

    def _directory_matches_targets(self, directory: ExecutionScopeDirectory) -> bool:
        if (
            type(directory) is not ExecutionScopeDirectory
            or directory.account_key != self._account_key
        ):
            return False
        target_rows = {
            _owner_tuple(wire_scope, execution_scope)
            for wire_scope, execution_scope in self._targets.items()
        }
        directory_rows = {
            (
                item.scope.key,
                item.wire_scope,
                item.owner_kind,
                item.owner_ref,
            )
            for item in directory.scopes
        }
        return directory_rows == target_rows


_FULL_ACCOUNT_VIEW: tuple[AllocationOwnerKind, str] = (AllocationOwnerKind.UNALLOCATED, "*")


class ExecutionAccountOwnerService:
    """Compatibility composition of managed orders and optional book reads."""

    SUPPORTED_OPERATIONS = SUPPORTED_OPERATIONS

    def __init__(
        self,
        store: SqliteExecutionStore,
        targets: Mapping[WireScope, ExecutionScope],
        dispatch: object,
        *,
        admission_gate: object | None = None,
        basic_admission_gate: object | None = None,
        cancel_admission_gate: object | None = None,
        allow_unprotected: bool = False,
        writer_id: str = "execution-order-entry",
        lease_ttl_ns: int = 30_000_000_000,
        account_books_source: ExecutionAccountBookSource | None = None,
    ) -> None:
        self._orders = _ExecutionOrderEntryService(
            store,
            targets,
            dispatch,
            admission_gate=admission_gate,
            basic_admission_gate=basic_admission_gate,
            cancel_admission_gate=cancel_admission_gate,
            allow_unprotected=allow_unprotected,
            writer_id=writer_id,
            lease_ttl_ns=lease_ttl_ns,
        )
        self._books = (
            None
            if account_books_source is None
            else ExecutionAccountBookReadAdapter(store, targets, account_books_source)
        )
        self.supported_operations = (
            _ORDER_OPERATIONS if self._books is None else SUPPORTED_OPERATIONS
        )

    def handle(
        self,
        operation: str,
        *,
        principal_ref: str,
        identity: WireIdentity,
        scope: WireScope,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if operation == ACCOUNT_BOOKS_QUERY_OPERATION:
            if self._books is None:
                return _reply(False, "UNKNOWN_OPERATION", {})
            return self._books.handle(
                operation,
                principal_ref=principal_ref,
                identity=identity,
                scope=scope,
                arguments=arguments,
            )
        return self._orders.handle(
            operation,
            principal_ref=principal_ref,
            identity=identity,
            scope=scope,
            arguments=arguments,
        )

    def recover_interrupted_dispatches(self) -> tuple[ExecutionRecord, ...]:
        return self._orders.recover_interrupted_dispatches()


# Keep the existing service-component constructor name available to code that
# resolves the account-owner adapter by its established symbol.
ExecutionOrderEntryService = ExecutionAccountOwnerService


def validate_service_options(options: Mapping[str, object], bind_ports: tuple[str, ...]):
    """Reuse the package's closed account-owner role validator."""

    from bt_api_execution.service_component import validate_service_options as validate

    return validate(options, bind_ports)


def _owner_tuple(
    wire_scope: WireScope, execution_scope: ExecutionScope
) -> tuple[str, WireScope, AllocationOwnerKind, str]:
    if wire_scope.strategy_ref is not None:
        kind = AllocationOwnerKind.STRATEGY
        owner_ref = execution_scope.strategy_id
    else:
        kind = AllocationOwnerKind.MANUAL_BOOK
        owner_ref = wire_scope.manual_book_ref
    return execution_scope.key, wire_scope, kind, owner_ref


def _financial_directory_matches_targets(
    directory: FinancialOwnerDirectoryV1,
    targets: Mapping[WireScope, ExecutionScope],
) -> bool:
    """Bind persisted owner keys to the host's complete configured routes."""

    expected: dict[tuple[str, str], tuple[str | None, ...]] = {}
    for wire_scope, execution_scope in targets.items():
        if wire_scope.strategy_ref is not None:
            owner_key = (FinancialOwnerKind.STRATEGY.value, execution_scope.strategy_id)
            if owner_key in expected:
                return False
            expected[owner_key] = (execution_scope.key,)
        else:
            owner_key = (FinancialOwnerKind.MANUAL_BOOK.value, wire_scope.manual_book_ref)
            if owner_key in expected:
                return False
            # Manual books may be account-level books with no execution scope,
            # or may name the configured source scope explicitly.
            expected[owner_key] = (None, execution_scope.key)
    actual = {owner.key: owner.execution_scope_key for owner in directory.owners}
    if len(actual) != len(directory.owners) or set(actual) != set(expected):
        return False
    return all(actual[key] in allowed_scopes for key, allowed_scopes in expected.items())


def _resolve_target(
    targets: Mapping[WireScope, ExecutionScope], requested: WireScope
) -> tuple[WireScope, ExecutionScope] | None:
    exact = targets.get(requested)
    if exact is not None:
        return requested, exact
    matches = [
        (template, execution_scope)
        for template, execution_scope in targets.items()
        if template.run_id is None
        and requested.run_id is not None
        and template.strategy_ref is not None
        and requested.strategy_ref == template.strategy_ref
        and all(
            getattr(template, name) == getattr(requested, name)
            for name in (
                "execution_domain_ref",
                "account_ref",
                "account_refs",
                "strategy_ref",
                "manual_book_ref",
                "portfolio_ref",
                "group_id",
                "leg_id",
            )
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _financial_unavailable(code: str) -> Mapping[str, Any]:
    return {
        "available": False,
        "code": code,
        "freshness": "NOT_ESTABLISHED",
        "cut_relationship": "INDEPENDENT_OF_LEGACY_ACCOUNT_BOOK_CUT",
    }


def _is_account_scope(scope: WireScope) -> bool:
    return (
        scope.account_ref is not None
        and not scope.account_refs
        and scope.strategy_ref is None
        and scope.run_id is None
        and scope.manual_book_ref is None
        and scope.portfolio_ref is None
        and scope.group_id is None
        and scope.leg_id is None
    )


def _owner_projection(
    reconciliation: AccountBookReconciliation,
    owner: tuple[AllocationOwnerKind, str],
) -> dict[str, Any]:
    kind, owner_ref = owner
    order_refs = {
        item.provider_order_ref
        for item in reconciliation.external_orders
        if item.owner_kind is kind and item.owner_ref == owner_ref
    }
    fills = tuple(
        item
        for item in reconciliation.external_fills
        if (item.owner_kind is kind and item.owner_ref == owner_ref)
        or item.provider_order_ref in order_refs
    )
    orders = tuple(
        item
        for item in reconciliation.external_orders
        if item.owner_kind is kind and item.owner_ref == owner_ref
    )
    return {
        "view": "OWNER_BOOK",
        "owner_kind": kind.value,
        "owner_ref": owner_ref,
        "coverage": {
            "complete": reconciliation.coverage.complete,
            "scope_directory_revision": reconciliation.coverage.scope_directory_revision,
            "journal_generation": reconciliation.coverage.journal_generation,
            "baseline_execution_cursor": reconciliation.coverage.baseline_execution_cursor,
            "current_execution_cursor": reconciliation.coverage.current_execution_cursor,
        },
        "books": tuple(
            book
            for book in reconciliation.books
            if book.owner_kind is kind and book.owner_ref == owner_ref
        ),
        "managed_orders": tuple(
            item
            for item in reconciliation.managed_orders
            if item.owner_kind is kind and item.owner_ref == owner_ref
        ),
        "managed_fills": tuple(
            item
            for item in reconciliation.managed_fills
            if item.owner_kind is kind and item.owner_ref == owner_ref
        ),
        "external_orders": orders,
        "external_fills": fills,
    }


def _full_projection(reconciliation: AccountBookReconciliation) -> dict[str, Any]:
    return {
        "view": "ACCOUNT",
        "coverage": reconciliation.coverage,
        "state": reconciliation.state,
        "books": reconciliation.books,
        "managed_orders": reconciliation.managed_orders,
        "managed_fills": reconciliation.managed_fills,
        "external_orders": reconciliation.external_orders,
        "external_fills": reconciliation.external_fills,
        "provider_position_change": reconciliation.provider_position_change,
        "position_residual": reconciliation.position_residual,
        "fee_residual": reconciliation.fee_residual,
    }


def _json_value(value: object) -> Any:
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("response mapping keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    raise TypeError("unsupported account-book response value")


def _reply(ok: bool, code: str, result: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"ok": ok, "code": code, "result": _json_value(dict(result))}


def _is_principal(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


__all__ = [
    "ACCOUNT_BOOKS_QUERY_OPERATION",
    "ExecutionAccountFinancialDirectorySource",
    "ExecutionAccountBookReadAdapter",
    "ExecutionAccountBookReadInputsV1",
    "ExecutionAccountBookSource",
    "ExecutionAccountOwnerService",
    "ExecutionOrderEntryService",
    "ExecutionRecord",
    "ExecutionState",
    "QUERY_OPERATION",
    "SUPPORTED_OPERATIONS",
    "validate_service_options",
]
