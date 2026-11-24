"""Typed read-only consumer for the calendar application operations.

This adapter borrows a deployment-owned, already authenticated call dispatcher;
it does not create a ServiceClientRuntime, transport, Registry grant, or Web
endpoint.  The directory alias is fixed at construction and is never accepted
from a browser request.  The application service remains simulation-only and
does not prove actor, source, or coverage authentication.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any, Literal, Protocol

from .trading_calendar_application_service import (
    CALENDAR_APPLICATION_SCHEMA,
    CALENDAR_CANDIDATE_READ_OPERATION,
    CALENDAR_CURRENT_READ_OPERATION,
    SIMULATION_ONLY,
)

_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_CANDIDATE_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_UNKNOWN_REASONS = frozenset(
    {
        "INVALID_REQUEST",
        "UNAVAILABLE_OR_UNAUTHORIZED",
        "BUSY",
        "CURRENT_UNAVAILABLE_OR_INVALID",
        "CANDIDATE_UNAVAILABLE_OR_INVALID",
        "RESULT_INVALID",
        "RESULT_TOO_LARGE",
        "CLIENT_UNAVAILABLE",
        "REMOTE_UNAVAILABLE",
        "INVALID_RESPONSE",
    }
)
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
_VENUES = frozenset({"SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX"})
_CURRENT_SUCCESS_FIELDS = frozenset(
    {
        "schema",
        "status",
        "reason",
        "directory_ref",
        "mode",
        "sequence",
        "content_sha256",
        "publication_sha256",
        "coverage_status",
        "integrity_status",
        "semantics_status",
        "receipt_consistency",
        "receipt_authentication",
        "principal_binding",
        "actor_authentication",
        "identity_authentication",
        "runtime_binding",
        "source_authentication",
        "coverage_authentication",
        "publication",
    }
)
_CANDIDATE_SUCCESS_FIELDS = frozenset(
    {
        "schema",
        "status",
        "reason",
        "directory_ref",
        "candidate_id",
        "payload_sha256",
        "mode",
        "review_status",
        "review_reason",
        "checker_status",
        "checker_reason",
        "declared_ranges",
        "declared_scope_count",
        "source_text_status",
        "source_binding_counts",
        "unresolved_status",
        "snapshot_sha256",
        "manifest_sha256",
        "binding_sha256",
        "principal_binding",
        "actor_authentication",
        "source_authentication",
        "coverage_authentication",
        "publication",
    }
)
_UNKNOWN_FIELDS = frozenset(
    {
        "schema",
        "status",
        "reason",
        "mode",
        "principal_binding",
        "actor_authentication",
        "source_authentication",
        "coverage_authentication",
        "publication",
    }
)


class CalendarApplicationDispatcher(Protocol):
    """Trusted server-side bridge, normally marshalled to an SDK owner thread.

    The finite timeout is a per-call budget passed through this bridge. The
    bridge must enforce it; this wrapper cannot preempt a blocking callback or
    guarantee a wall-clock completion time.
    """

    def __call__(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CalendarCurrentRead:
    status: Literal["CURRENT_VISIBLE_UNAUTHENTICATED", "UNKNOWN"]
    reason: str
    directory_ref: str
    mode: Literal["SIMULATION_ONLY"] = SIMULATION_ONLY
    sequence: int | None = None
    content_sha256: str | None = None
    publication_sha256: str | None = None
    coverage_status: str | None = None
    integrity_status: str | None = None
    semantics_status: str | None = None
    receipt_consistency: str = "NOT_PROVEN"
    receipt_authentication: str = "NOT_PROVEN"
    principal_binding: str = "TRUSTED_REGISTRY_CONTEXT"
    actor_authentication: str = "NOT_PROVEN"
    identity_authentication: str = "NOT_PROVEN"
    runtime_binding: str = "NOT_PROVEN"
    source_authentication: str = "NOT_PROVEN"
    coverage_authentication: str = "NOT_PROVEN"
    publication: str = "NOT_AUTHORIZED"


@dataclass(frozen=True, slots=True)
class CalendarDeclaredRange:
    venue: str
    start: date
    end: date
    declared_scope_count: int


@dataclass(frozen=True, slots=True)
class CalendarCandidateRead:
    status: Literal["CANDIDATE_VISIBLE_UNAUTHENTICATED", "UNKNOWN"]
    reason: str
    directory_ref: str
    candidate_id: str
    mode: Literal["SIMULATION_ONLY"] = SIMULATION_ONLY
    payload_sha256: str | None = None
    review_status: str | None = None
    review_reason: str | None = None
    checker_status: str | None = None
    checker_reason: str | None = None
    declared_ranges: tuple[CalendarDeclaredRange, ...] = ()
    declared_scope_count: int = 0
    source_text_status: str | None = None
    source_binding_counts: tuple[tuple[str, int], ...] = ()
    unresolved_status: str | None = None
    snapshot_sha256: str | None = None
    manifest_sha256: str | None = None
    binding_sha256: str | None = None
    principal_binding: str = "TRUSTED_REGISTRY_CONTEXT"
    actor_authentication: str = "NOT_PROVEN"
    source_authentication: str = "NOT_PROVEN"
    coverage_authentication: str = "NOT_PROVEN"
    publication: str = "NOT_AUTHORIZED"


def _valid_reference(value: object) -> bool:
    return type(value) is str and _REFERENCE.fullmatch(value) is not None


def _valid_date(value: object) -> date | None:
    if type(value) is not str:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, OverflowError):
        return None
    return parsed if parsed.isoformat() == value else None


def _finite_token(value: object) -> str | None:
    if type(value) is not str or not 1 <= len(value) <= 64:
        return None
    if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in value):
        return None
    return value


def _finite_timeout(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def _safe_response(value: object) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    # ServiceClientRuntime.call returns the application result. The standard
    # Registry envelope is also accepted for local in-process integrations.
    if frozenset(value) == {"ok", "code", "result"}:
        if value.get("ok") is not True or value.get("code") != "OK":
            return None
        value = value.get("result")
    if not isinstance(value, Mapping):
        return None
    return value


def _common_trust_labels(value: Mapping[str, Any]) -> bool:
    return (
        value.get("schema") == CALENDAR_APPLICATION_SCHEMA
        and value.get("mode") == SIMULATION_ONLY
        and value.get("principal_binding") == "TRUSTED_REGISTRY_CONTEXT"
        and value.get("actor_authentication") == "NOT_PROVEN"
        and value.get("source_authentication") == "NOT_PROVEN"
        and value.get("coverage_authentication") == "NOT_PROVEN"
        and value.get("publication") == "NOT_AUTHORIZED"
    )


class TradingCalendarApplicationClient:
    """Read current or explicitly named candidates through one trusted bridge.

    The bridge must already target an authenticated, authorized application
    registration and marshal calls onto its runtime owner thread. This class
    does not prove those properties; the owner Registry enforces them. It also
    does not enumerate candidates or expose any write operation.
    """

    def __init__(
        self,
        dispatcher: CalendarApplicationDispatcher,
        *,
        directory_ref: str,
        timeout_seconds: float = 1.5,
    ) -> None:
        configured_timeout = _finite_timeout(timeout_seconds)
        if (
            not callable(dispatcher)
            or not _valid_reference(directory_ref)
            or configured_timeout is None
            or not 0.01 <= configured_timeout <= 1.75
        ):
            raise ValueError("INVALID_CALENDAR_APPLICATION_CLIENT")
        self._dispatcher = dispatcher
        self._directory_ref = directory_ref
        self._timeout_seconds = configured_timeout

    @property
    def directory_ref(self) -> str:
        """The server-configured alias; never a filesystem path."""

        return self._directory_ref

    @property
    def max_timeout_seconds(self) -> float:
        """Maximum per-call budget this client will pass to its dispatcher."""

        return self._timeout_seconds

    def read_current(self, *, timeout_seconds: float | None = None) -> CalendarCurrentRead:
        """Read the bound directory's current pointer and local receipts."""

        call_timeout = self._call_timeout(timeout_seconds)
        if call_timeout is None:
            return self._unknown_current("INVALID_REQUEST")
        response = self._invoke(
            CALENDAR_CURRENT_READ_OPERATION,
            {"directory_ref": self._directory_ref},
            timeout_seconds=call_timeout,
        )
        if response is None:
            return self._unknown_current("CLIENT_UNAVAILABLE")
        if not _common_trust_labels(response):
            return self._unknown_current("INVALID_RESPONSE")
        if response.get("status") == "UNKNOWN":
            return self._unknown_current(self._unknown_reason(response))
        if (
            frozenset(response) != _CURRENT_SUCCESS_FIELDS
            or not _common_trust_labels(response)
            or response.get("status") != "CURRENT_VISIBLE_UNAUTHENTICATED"
            or response.get("reason") != "OK"
            or response.get("directory_ref") != self._directory_ref
            or type(response.get("sequence")) is not int
            or response["sequence"] < 0
            or type(response.get("content_sha256")) is not str
            or _SHA256.fullmatch(response["content_sha256"]) is None
            or response.get("coverage_status") not in {"DECLARED_UNVERIFIED", "NOT_PRESENT", "UNAVAILABLE"}
            or response.get("integrity_status") not in {"NOT_ASSERTED", "STORE_INTEGRITY_ONLY"}
            or response.get("semantics_status") not in {"NOT_VERIFIED", "SEMANTICS_NOT_VERIFIED"}
            or response.get("receipt_consistency") != "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED"
            or response.get("receipt_authentication") != "NOT_PROVEN"
            or response.get("identity_authentication") != "NOT_PROVEN"
            or response.get("runtime_binding") != "NOT_PROVEN"
        ):
            return self._unknown_current("INVALID_RESPONSE")
        publication_sha256 = response.get("publication_sha256")
        if publication_sha256 is not None and (
            type(publication_sha256) is not str or _SHA256.fullmatch(publication_sha256) is None
        ):
            return self._unknown_current("INVALID_RESPONSE")
        return CalendarCurrentRead(
            status="CURRENT_VISIBLE_UNAUTHENTICATED",
            reason="OK",
            directory_ref=self._directory_ref,
            sequence=response["sequence"],
            content_sha256=response["content_sha256"],
            publication_sha256=publication_sha256,
            coverage_status=response["coverage_status"],
            integrity_status=response["integrity_status"],
            semantics_status=response["semantics_status"],
            receipt_consistency=response["receipt_consistency"],
            receipt_authentication=response["receipt_authentication"],
            principal_binding=response["principal_binding"],
            actor_authentication=response["actor_authentication"],
            identity_authentication=response["identity_authentication"],
            runtime_binding=response["runtime_binding"],
            source_authentication=response["source_authentication"],
            coverage_authentication=response["coverage_authentication"],
            publication=response["publication"],
        )

    def read_candidate(
        self,
        candidate_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> CalendarCandidateRead:
        """Read one explicitly configured candidate id through the owner."""

        if type(candidate_id) is not str or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            return self._unknown_candidate("INVALID_REQUEST", "")
        call_timeout = self._call_timeout(timeout_seconds)
        if call_timeout is None:
            return self._unknown_candidate("INVALID_REQUEST", candidate_id)
        response = self._invoke(
            CALENDAR_CANDIDATE_READ_OPERATION,
            {"directory_ref": self._directory_ref, "candidate_id": candidate_id},
            timeout_seconds=call_timeout,
        )
        if response is None:
            return self._unknown_candidate("CLIENT_UNAVAILABLE", candidate_id)
        if not _common_trust_labels(response):
            return self._unknown_candidate("INVALID_RESPONSE", candidate_id)
        if response.get("status") == "UNKNOWN":
            return self._unknown_candidate(self._unknown_reason(response), candidate_id)
        if (
            frozenset(response) != _CANDIDATE_SUCCESS_FIELDS
            or not _common_trust_labels(response)
            or response.get("status") != "CANDIDATE_VISIBLE_UNAUTHENTICATED"
            or response.get("reason") != "OK"
            or response.get("directory_ref") != self._directory_ref
            or response.get("candidate_id") != candidate_id
            or type(response.get("payload_sha256")) is not str
            or _SHA256.fullmatch(response["payload_sha256"]) is None
        ):
            return self._unknown_candidate("INVALID_RESPONSE", candidate_id)
        ranges = self._candidate_ranges(response.get("declared_ranges"))
        counts = self._source_binding_counts(response.get("source_binding_counts"))
        scope_count = response.get("declared_scope_count")
        tokens = {
            key: _finite_token(response.get(key))
            for key in (
                "review_status",
                "review_reason",
                "checker_status",
                "source_text_status",
                "unresolved_status",
            )
        }
        checker_reason = response.get("checker_reason")
        checker_reason_token = "" if checker_reason == "" else _finite_token(checker_reason)
        if (
            ranges is None
            or counts is None
            or type(scope_count) is not int
            or scope_count < 0
            or scope_count > 60000
            or sum(row.declared_scope_count for row in ranges) != scope_count
            or any(value is None for value in tokens.values())
            or tokens["review_status"] not in _REVIEW_STATES
            or tokens["review_reason"] not in _REVIEW_REASONS
            or tokens["checker_status"] not in _CHECKER_STATES
            or tokens["source_text_status"] not in {"DECLARED_UNAUTHENTICATED", "MISSING_OR_INCOMPLETE"}
            or tokens["unresolved_status"] not in {"NONE_DECLARED", "MISSING_OR_UNRESOLVED"}
            or checker_reason_token is None
        ):
            return self._unknown_candidate("INVALID_RESPONSE", candidate_id)
        hashes: dict[str, str | None] = {}
        for name in ("snapshot_sha256", "manifest_sha256", "binding_sha256"):
            value = response.get(name)
            if value is not None and (type(value) is not str or _SHA256.fullmatch(value) is None):
                return self._unknown_candidate("INVALID_RESPONSE", candidate_id)
            hashes[name] = value
        if len({value is None for value in hashes.values()}) != 1:
            return self._unknown_candidate("INVALID_RESPONSE", candidate_id)
        return CalendarCandidateRead(
            status="CANDIDATE_VISIBLE_UNAUTHENTICATED",
            reason="OK",
            directory_ref=self._directory_ref,
            candidate_id=candidate_id,
            payload_sha256=response["payload_sha256"],
            review_status=tokens["review_status"],
            review_reason=tokens["review_reason"],
            checker_status=tokens["checker_status"],
            checker_reason=checker_reason_token,
            declared_ranges=ranges,
            declared_scope_count=scope_count,
            source_text_status=tokens["source_text_status"],
            source_binding_counts=counts,
            unresolved_status=tokens["unresolved_status"],
            snapshot_sha256=hashes["snapshot_sha256"],
            manifest_sha256=hashes["manifest_sha256"],
            binding_sha256=hashes["binding_sha256"],
            principal_binding=response["principal_binding"],
            actor_authentication=response["actor_authentication"],
            source_authentication=response["source_authentication"],
            coverage_authentication=response["coverage_authentication"],
            publication=response["publication"],
        )

    def _call_timeout(self, requested: float | None) -> float | None:
        if requested is None:
            return self._timeout_seconds
        normalized = _finite_timeout(requested)
        if normalized is None or not 0.01 <= normalized <= self._timeout_seconds:
            return None
        return normalized

    def _invoke(
        self,
        operation: str,
        arguments: dict[str, str],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any] | None:
        try:
            response = _safe_response(
                self._dispatcher(
                    operation,
                    MappingProxyType(arguments),
                    timeout_seconds=timeout_seconds,
                )
            )
        except Exception:
            return None
        if response is None or response.get("schema") != CALENDAR_APPLICATION_SCHEMA:
            return None
        return response

    @staticmethod
    def _unknown_reason(response: Mapping[str, Any]) -> str:
        reason = response.get("reason")
        return reason if type(reason) is str and reason in _UNKNOWN_REASONS else "INVALID_RESPONSE"

    def _unknown_current(self, reason: str) -> CalendarCurrentRead:
        return CalendarCurrentRead(
            status="UNKNOWN", reason=reason, directory_ref=self._directory_ref
        )

    def _unknown_candidate(self, reason: str, candidate_id: str) -> CalendarCandidateRead:
        return CalendarCandidateRead(
            status="UNKNOWN",
            reason=reason,
            directory_ref=self._directory_ref,
            candidate_id=candidate_id,
        )

    @staticmethod
    def _candidate_ranges(value: object) -> tuple[CalendarDeclaredRange, ...] | None:
        if type(value) is not list or len(value) != len(_VENUES):
            return None
        result: list[CalendarDeclaredRange] = []
        seen: set[str] = set()
        for row in value:
            if type(row) is not dict or frozenset(row) != {
                "venue", "start", "end", "declared_scope_count"
            }:
                return None
            venue = row.get("venue")
            start, end = _valid_date(row.get("start")), _valid_date(row.get("end"))
            count = row.get("declared_scope_count")
            if (
                type(venue) is not str
                or venue not in _VENUES
                or venue in seen
                or start is None
                or end is None
                or start > end
                or type(count) is not int
                or count < 0
                or count > 10000
            ):
                return None
            seen.add(venue)
            result.append(CalendarDeclaredRange(venue, start, end, count))
        if seen != _VENUES:
            return None
        return tuple(sorted(result, key=lambda item: item.venue))

    @staticmethod
    def _source_binding_counts(value: object) -> tuple[tuple[str, int], ...] | None:
        if not isinstance(value, Mapping) or frozenset(value) != _SOURCE_BINDING_STATES:
            return None
        counts: list[tuple[str, int]] = []
        for state in sorted(_SOURCE_BINDING_STATES):
            count = value.get(state)
            if type(count) is not int or count < 0:
                return None
            counts.append((state, count))
        return tuple(counts)


__all__ = [
    "CalendarApplicationDispatcher",
    "CalendarCandidateRead",
    "CalendarCurrentRead",
    "CalendarDeclaredRange",
    "TradingCalendarApplicationClient",
]
