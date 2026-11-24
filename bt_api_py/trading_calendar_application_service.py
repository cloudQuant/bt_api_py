"""Registry-authorized, simulation-only read facade for a trusted calendar root.

The application service never accepts a filesystem path or a calendar scope
from a caller.  Directory references, the owning ``TradingCalendarStore``,
and allowed principal/actor pairs are fixed by trusted server composition.
Only the two read operations below are exposed.  They project bounded summary
fields from the existing calendar owner APIs and never return source text,
contracts, filesystem paths, or publication authority.

``WireIdentity`` and Registry context establish only the configured service
principal binding.  This adapter does not authenticate a human actor or the
calendar source and is permanently ``SIMULATION_ONLY``.

Owner reads are admitted one at a time through a non-blocking semaphore.  The
candidate owner API can read a payload up to 96 MiB, so this keeps that memory
exposure to one owner call per service instance.  This is an overload bound,
not a processing deadline or latency guarantee.
The SDK ``ServiceRegistry`` may also run its own session-expiry housekeeping
transaction for these application operations; therefore the overall RPC must
not be described as stateless or side-effect free.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from threading import BoundedSemaphore
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py import trading_calendar_review_bundle
from bt_api_py.service_registry import ServiceCallContext, ServiceRegistry
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX
from bt_api_py.trading_calendar_store import (
    CalendarReadBudget,
    CalendarSnapshot,
    ReceiptConsistentCurrent,
    TradingCalendarStore,
)

CALENDAR_APPLICATION_PORT = "service.calendar_application"
CALENDAR_CURRENT_READ_OPERATION = "calendar.application.current.read.v1"
CALENDAR_CANDIDATE_READ_OPERATION = "calendar.application.candidate.read.v1"
CALENDAR_APPLICATION_OPERATIONS = frozenset(
    {CALENDAR_CURRENT_READ_OPERATION, CALENDAR_CANDIDATE_READ_OPERATION}
)
CALENDAR_APPLICATION_SCHEMA = "sdk.trading_calendar.application_read/1"
SIMULATION_ONLY = "SIMULATION_ONLY"
_MAX_REPLY_BYTES = 16 * 1024
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_CANDIDATE_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_VENUES = frozenset({"SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX"})
_CURRENT_COVERAGE = frozenset({"DECLARED_UNVERIFIED", "NOT_PRESENT", "UNAVAILABLE"})
_CURRENT_INTEGRITY = frozenset({"NOT_ASSERTED", "STORE_INTEGRITY_ONLY"})
_CURRENT_SEMANTICS = frozenset({"NOT_VERIFIED", "SEMANTICS_NOT_VERIFIED"})
_SOURCE_BINDING_STATES = frozenset(
    {"MATCH_UNAUTHENTICATED", "MISMATCH_OR_LOSSY_ENCODING", "UNBOUND", "TEXT_MISSING_OR_INVALID"}
)
_REVIEW_STATES = frozenset({"PARTIAL", "STRUCTURALLY_READY_UNVERIFIED"})
_REVIEW_REASONS = frozenset(
    {
        "INVALID_OR_INCOMPLETE_INPUT",
        "DECLARED_SCOPES_CHECKED_SIMULATED",
        "DECLARATIONS_OR_SIMULATED_CHECK_INCOMPLETE",
        "SNAPSHOT_BUDGET_UNAVAILABLE",
    }
)
_CHECKER_STATES = frozenset(
    {
        "NOT_RUN",
        "READY_SIMULATED",
        "INVALID_INPUT",
        "DEPENDENCY_UNAVAILABLE",
        "CHECK_FAILED",
        "COMPOSITE_REJECTED",
        "COMPOSITE_ERROR",
    }
)


def _reference(value: object) -> bool:
    return type(value) is str and _REFERENCE.fullmatch(value) is not None


def _date_ref(value: object) -> str | None:
    if type(value) is not str:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, OverflowError):
        return None
    return value if parsed.isoformat() == value else None


def _token(value: object) -> str | None:
    if type(value) is not str or not 1 <= len(value) <= 64:
        return None
    if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in value):
        return None
    return value


@dataclass(frozen=True, slots=True)
class CalendarDirectoryBinding:
    """One explicit server-side directory alias bound to the calendar owner."""

    directory_ref: str
    store: TradingCalendarStore = field(repr=False, compare=False)
    mode: str = SIMULATION_ONLY

    def __post_init__(self) -> None:
        if not _reference(self.directory_ref) or type(self.mode) is not str or self.mode != SIMULATION_ONLY:
            raise ValueError("INVALID_CALENDAR_DIRECTORY_BINDING")
        if type(self.store) is not TradingCalendarStore:
            raise TypeError("directory binding requires the TradingCalendarStore owner")
        root = self.store.root
        if not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts:
            raise ValueError("INVALID_CALENDAR_DIRECTORY_BINDING")


@dataclass(frozen=True, slots=True)
class CalendarApplicationGrant:
    """Exact trusted Registry subject and directory/operation grant."""

    principal_ref: str
    actor_ref: str
    execution_domain_ref: str
    directory_ref: str
    operations: frozenset[str]

    def __post_init__(self) -> None:
        if not all(
            _reference(value)
            for value in (
                self.principal_ref,
                self.actor_ref,
                self.execution_domain_ref,
                self.directory_ref,
            )
        ):
            raise ValueError("INVALID_CALENDAR_APPLICATION_GRANT")
        if (
            type(self.operations) is not frozenset
            or not self.operations
            or not self.operations.issubset(CALENDAR_APPLICATION_OPERATIONS)
        ):
            raise ValueError("INVALID_CALENDAR_APPLICATION_GRANT")


class TradingCalendarApplicationService:
    """Expose bounded, read-only calendar owner views to a trusted app client.

    A host must declare one exact ``ServiceCapability`` at
    ``service.calendar_application`` with these two operations and an empty
    ``write_operations`` set, plus matching Registry grants.  This service
    adds a second server-side grant for each directory alias.  Neither grant
    authenticates a human or calendar source.
    """

    def __init__(
        self,
        *,
        directories: tuple[CalendarDirectoryBinding, ...],
        grants: frozenset[CalendarApplicationGrant],
    ) -> None:
        if (
            type(directories) is not tuple
            or not directories
            or len(directories) > 64
            or any(type(item) is not CalendarDirectoryBinding for item in directories)
            or len({item.directory_ref for item in directories}) != len(directories)
        ):
            raise ValueError("INVALID_CALENDAR_DIRECTORIES")
        if (
            type(grants) is not frozenset
            or not grants
            or len(grants) > 4096
            or any(type(grant) is not CalendarApplicationGrant for grant in grants)
            or not {grant.directory_ref for grant in grants}.issubset(
                {item.directory_ref for item in directories}
            )
        ):
            raise ValueError("INVALID_CALENDAR_APPLICATION_GRANTS")
        self._directories = MappingProxyType({item.directory_ref: item for item in directories})
        self._grants = grants
        self._read_slots = BoundedSemaphore(1)

    def register_handlers(self, registry: ServiceRegistry, *, instance_id: str) -> None:
        """Bind only the exact read-only routes to the host's Registry."""
        if type(registry) is not ServiceRegistry or registry._closed or not _reference(instance_id):
            raise ValueError("INVALID_CALENDAR_APPLICATION_REGISTRY")
        capabilities = tuple(
            capability
            for capability in registry._capabilities.values()
            if capability.instance_id == instance_id
            and capability.port == CALENDAR_APPLICATION_PORT
        )
        if (
            len(capabilities) != 1
            or capabilities[0].operations != CALENDAR_APPLICATION_OPERATIONS
            or capabilities[0].write_operations
        ):
            raise ValueError("CALENDAR_APPLICATION_REQUIRES_EXACT_READ_ONLY_CAPABILITY")
        for operation in sorted(CALENDAR_APPLICATION_OPERATIONS):
            registry.register_handler(
                instance_id,
                operation,
                lambda context, arguments, route=operation: self.handle(
                    route, context=context, arguments=arguments
                ),
            )

    def handle(
        self,
        operation: str,
        *,
        context: ServiceCallContext,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Handle one Registry-issued application request."""
        if operation not in CALENDAR_APPLICATION_OPERATIONS:
            raise ValueError("INVALID_CALENDAR_APPLICATION_OPERATION")
        if not self._trusted_context(context, arguments):
            raise ValueError("REGISTRY_CONTEXT_REQUIRED")
        required = {"directory_ref"}
        if operation == CALENDAR_CANDIDATE_READ_OPERATION:
            required.add("candidate_id")
        if not isinstance(arguments, Mapping) or set(arguments) != required:
            return self._finish(context, self._unknown("INVALID_REQUEST"))
        directory_ref = arguments.get("directory_ref")
        if not _reference(directory_ref):
            return self._finish(context, self._unknown("INVALID_REQUEST"))
        if not any(
            grant.principal_ref == context.principal_ref
            and grant.actor_ref == context.identity.actor_ref
            and grant.execution_domain_ref == context.scope.execution_domain_ref
            and grant.directory_ref == directory_ref
            and operation in grant.operations
            for grant in self._grants
        ):
            return self._finish(context, self._unknown("UNAVAILABLE_OR_UNAUTHORIZED"))
        binding = self._directories.get(directory_ref)
        if binding is None or binding.mode != SIMULATION_ONLY:
            return self._finish(context, self._unknown("UNAVAILABLE_OR_UNAUTHORIZED"))

        if not self._read_slots.acquire(blocking=False):
            return self._finish(context, self._unknown("BUSY"))
        try:
            if operation == CALENDAR_CURRENT_READ_OPERATION:
                result = self._read_current(binding)
            else:
                candidate_id = arguments.get("candidate_id")
                if type(candidate_id) is not str or _CANDIDATE_ID.fullmatch(candidate_id) is None:
                    result = self._unknown("INVALID_REQUEST")
                else:
                    result = self._read_candidate(binding, candidate_id)
        except Exception:
            result = self._unknown(
                "CURRENT_UNAVAILABLE_OR_INVALID"
                if operation == CALENDAR_CURRENT_READ_OPERATION
                else "CANDIDATE_UNAVAILABLE_OR_INVALID"
            )
        finally:
            self._read_slots.release()
        return self._finish(context, result)

    @staticmethod
    def _trusted_context(context: object, arguments: object) -> bool:
        return (
            type(context) is ServiceCallContext
            and type(context.identity) is WireIdentity
            and type(context.scope) is WireScope
            and context.identity.role is WireRole.APPLICATION_CLIENT
            and context.identity.delegate_ref is None
            and context.identity.principal_ref == context.principal_ref
            and context.scope.account_ref is None
            and not context.scope.account_refs
            and context.scope.strategy_ref is None
            and context.scope.run_id is None
            and context.scope.manual_book_ref is None
            and context.scope.portfolio_ref is None
            and context.scope.group_id is None
            and context.scope.leg_id is None
            and _reference(context.identity.actor_ref)
            and _reference(context.principal_ref)
            and _reference(context.scope.execution_domain_ref)
            and _reference(context.registration_id)
            and _reference(context.service_epoch)
            and callable(context.result_fits)
            and context._is_registry_wire_arguments(arguments)
        )

    @staticmethod
    def _unknown(reason: str) -> dict[str, Any]:
        return {
            "schema": CALENDAR_APPLICATION_SCHEMA,
            "status": "UNKNOWN",
            "reason": reason,
            "mode": SIMULATION_ONLY,
            "principal_binding": "TRUSTED_REGISTRY_CONTEXT",
            "actor_authentication": "NOT_PROVEN",
            "source_authentication": "NOT_PROVEN",
            "coverage_authentication": "NOT_PROVEN",
            "publication": "NOT_AUTHORIZED",
        }

    @classmethod
    def _read_current(cls, binding: CalendarDirectoryBinding) -> dict[str, Any]:
        try:
            current = binding.store.read_current_with_consistent_receipts(
                budget=CalendarReadBudget(8 * 1024 * 1024, 32 * 1024 * 1024, 4096)
            )
            if type(current) is not ReceiptConsistentCurrent or type(current.snapshot) is not CalendarSnapshot:
                return cls._unknown("CURRENT_UNAVAILABLE_OR_INVALID")
            snapshot = current.snapshot
            if (
                type(snapshot.sequence) is not int
                or snapshot.sequence < 0
                or _SHA256.fullmatch(snapshot.content_sha256) is None
                or snapshot.coverage_status not in _CURRENT_COVERAGE
                or snapshot.integrity_status not in _CURRENT_INTEGRITY
                or snapshot.semantics_status not in _CURRENT_SEMANTICS
                or current.receipt_consistency != "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED"
            ):
                return cls._unknown("CURRENT_UNAVAILABLE_OR_INVALID")
            publication_sha256 = snapshot.publication_sha256
            return {
                "schema": CALENDAR_APPLICATION_SCHEMA,
                "status": "CURRENT_VISIBLE_UNAUTHENTICATED",
                "reason": "OK",
                "directory_ref": binding.directory_ref,
                "mode": SIMULATION_ONLY,
                "sequence": snapshot.sequence,
                "content_sha256": snapshot.content_sha256,
                "publication_sha256": publication_sha256
                if type(publication_sha256) is str and _SHA256.fullmatch(publication_sha256)
                else None,
                "coverage_status": snapshot.coverage_status,
                "integrity_status": snapshot.integrity_status,
                "semantics_status": snapshot.semantics_status,
                "receipt_consistency": current.receipt_consistency,
                "receipt_authentication": "NOT_PROVEN",
                "principal_binding": "TRUSTED_REGISTRY_CONTEXT",
                "actor_authentication": "NOT_PROVEN",
                "identity_authentication": "NOT_PROVEN",
                "runtime_binding": "NOT_PROVEN",
                "source_authentication": "NOT_PROVEN",
                "coverage_authentication": "NOT_PROVEN",
                "publication": "NOT_AUTHORIZED",
            }
        except Exception:
            return cls._unknown("CURRENT_UNAVAILABLE_OR_INVALID")

    @classmethod
    def _read_candidate(
        cls, binding: CalendarDirectoryBinding, candidate_id: str
    ) -> dict[str, Any]:
        try:
            owner_result = trading_calendar_review_bundle.show(
                root=str(binding.store.root),
                candidate_id=candidate_id,
                filesystem=TRUSTED_LOCAL_POSIX,
            )
        except Exception:
            return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
        if (
            type(owner_result) is not dict
            or owner_result.get("status") != "VISIBLE_UNAUTHENTICATED"
            or owner_result.get("candidate_id") != candidate_id
            or type(owner_result.get("bundle")) is not dict
        ):
            return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
        body = owner_result["bundle"]
        report = body.get("report")
        input_data = body.get("input")
        if (
            type(report) is not dict
            or type(input_data) is not dict
            or report.get("checker_mode") != SIMULATION_ONLY
            or report.get("source_authentication") != "NOT_PROVEN"
            or report.get("coverage_authentication") != "NOT_PROVEN"
            or report.get("publication") != "NOT_AUTHORIZED"
        ):
            return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
        payload_sha256 = owner_result.get("payload_sha256")
        if type(payload_sha256) is not str or _SHA256.fullmatch(payload_sha256) is None:
            return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
        review_status = _token(report.get("status"))
        review_reason = _token(report.get("reason"))
        checker_status = _token(report.get("checker_status"))
        raw_checker_reason = report.get("checker_reason")
        checker_reason = "" if type(raw_checker_reason) is str and raw_checker_reason == "" else _token(raw_checker_reason)
        source_text_status = _token(report.get("source_text_status"))
        unresolved_status = _token(report.get("unresolved_status"))
        shards = input_data.get("shards")
        if type(shards) is not list or len(shards) != 6:
            return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
        ranges: list[dict[str, Any]] = []
        seen: set[str] = set()
        scope_count = 0
        for shard in shards:
            if type(shard) is not dict or shard.get("venue") not in _VENUES:
                return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
            venue = shard["venue"]
            start, end = _date_ref(shard.get("coverage_start")), _date_ref(shard.get("coverage_end"))
            requirements = shard.get("requirements")
            if (
                venue in seen
                or start is None
                or end is None
                or start > end
                or type(requirements) is not list
                or len(requirements) > 10000
            ):
                return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
            seen.add(venue)
            scope_count += len(requirements)
            if scope_count > 60000:
                return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
            ranges.append(
                {
                    "venue": venue,
                    "start": start,
                    "end": end,
                    "declared_scope_count": len(requirements),
                }
            )
        if seen != _VENUES or any(
            value is None
            for value in (
                review_status,
                review_reason,
                checker_status,
                checker_reason,
                source_text_status,
                unresolved_status,
            )
        ):
            return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
        if (
            review_status not in _REVIEW_STATES
            or review_reason not in _REVIEW_REASONS
            or checker_status not in _CHECKER_STATES
            or source_text_status not in {"DECLARED_UNAUTHENTICATED", "MISSING_OR_INCOMPLETE"}
            or unresolved_status not in {"NONE_DECLARED", "MISSING_OR_UNRESOLVED"}
        ):
            return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
        source_bindings = report.get("source_bindings")
        if type(source_bindings) is not list or len(source_bindings) > 4096:
            return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
        binding_counts = dict.fromkeys(_SOURCE_BINDING_STATES, 0)
        for item in source_bindings:
            if type(item) is not dict or item.get("status") not in _SOURCE_BINDING_STATES:
                return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
            binding_counts[item["status"]] += 1
        snapshot_sha256 = None
        manifest_sha256 = None
        binding_sha256 = None
        composite = report.get("composite")
        if composite is not None:
            if type(composite) is not dict:
                return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
            hashes = tuple(composite.get(name) for name in ("snapshot_sha256", "manifest_sha256", "binding_sha256"))
            if any(type(value) is not str or _SHA256.fullmatch(value) is None for value in hashes):
                return cls._unknown("CANDIDATE_UNAVAILABLE_OR_INVALID")
            snapshot_sha256, manifest_sha256, binding_sha256 = hashes
        return {
            "schema": CALENDAR_APPLICATION_SCHEMA,
            "status": "CANDIDATE_VISIBLE_UNAUTHENTICATED",
            "reason": "OK",
            "directory_ref": binding.directory_ref,
            "candidate_id": candidate_id,
            "payload_sha256": payload_sha256,
            "mode": SIMULATION_ONLY,
            "review_status": review_status,
            "review_reason": review_reason,
            "checker_status": checker_status,
            "checker_reason": checker_reason,
            "declared_ranges": sorted(ranges, key=lambda row: row["venue"]),
            "declared_scope_count": scope_count,
            "source_text_status": source_text_status,
            "source_binding_counts": binding_counts,
            "unresolved_status": unresolved_status,
            "snapshot_sha256": snapshot_sha256,
            "manifest_sha256": manifest_sha256,
            "binding_sha256": binding_sha256,
            "principal_binding": "TRUSTED_REGISTRY_CONTEXT",
            "actor_authentication": "NOT_PROVEN",
            "source_authentication": "NOT_PROVEN",
            "coverage_authentication": "NOT_PROVEN",
            "publication": "NOT_AUTHORIZED",
        }

    @staticmethod
    def _finish(context: ServiceCallContext, result: dict[str, Any]) -> Mapping[str, Any]:
        try:
            encoded = json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        except (TypeError, ValueError, UnicodeEncodeError):
            result = TradingCalendarApplicationService._unknown("RESULT_INVALID")
            encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("ascii")
        if len(encoded) > _MAX_REPLY_BYTES:
            result = TradingCalendarApplicationService._unknown("RESULT_TOO_LARGE")
        fits = context.result_fits(result)
        if type(fits) is not bool or not fits:
            return TradingCalendarApplicationService._unknown("RESULT_TOO_LARGE")
        return result


def assemble_trading_calendar_application_service(
    *,
    registry: ServiceRegistry,
    instance_id: str,
    directories: tuple[CalendarDirectoryBinding, ...],
    grants: frozenset[CalendarApplicationGrant],
) -> TradingCalendarApplicationService:
    """Create the handler and bind its exact read-only routes to a Registry.

    This is a local composition helper only.  It does not create a transport,
    Web endpoint, Registry capability, Registry principal grant, or trusted
    directory configuration; the caller must provide those server-owned
    inputs explicitly.  Keep this helper in the composition root that already
    owns the Registry and server-side calendar-directory configuration.
    """
    service = TradingCalendarApplicationService(
        directories=directories,
        grants=grants,
    )
    service.register_handlers(registry, instance_id=instance_id)
    return service
