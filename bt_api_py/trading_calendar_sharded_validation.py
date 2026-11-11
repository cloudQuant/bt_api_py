"""Explicit simulation-only validation and composition of six venue shards.

Each requirement is a caller-declared exact scope and inclusive date interval.
Intervals must lie within the shard's declared venue interval, but need not
cover that whole interval or every product. Each declared venue interval is at
most 731 days and must have exactly one explicit date record for each day.
Valid records outside the declared interval are retained, as in the composer.
Manifest coverage is a caller declaration, not proof of all product scopes,
source truth or UTC coverage. A successful structural check does not authenticate sources,
prove UTC coverage or authorize a publication. This module never publishes,
uses a store, writes files, accesses accounts or performs network operations.

The caller must not mutate inputs concurrently during snapshot copying; Python
cannot guarantee an atomic copy of concurrently mutated objects. Validation and
composition use the same detached copy, so subsequent changes to caller-owned
objects cannot insert unchecked content into the composed result.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from typing import Any

from bt_api_py.trading_calendar_composite import SIMULATION_ONLY, CompositeResult, compose_simulated
from bt_api_py.trading_calendar_update_simulation import VenueContent

_VENUES = frozenset({"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"})
_COMPOSITE_FAILURES = frozenset(
    {
        "INVALID_INPUT",
        "DUPLICATE_VENUE",
        "DUPLICATE_RECORD",
        "SOURCE_CONFLICT",
        "SOURCE_REFERENCE_MISSING",
        "VENUE_MISMATCH",
        "PROVIDER_ERROR",
        "PROVIDER_REJECTED",
        "PROVIDER_MUTATED_INPUT",
    }
)


@dataclass(frozen=True)
class VenueShard:
    venue: str
    coverage_start: date
    coverage_end: date
    candidate: Any
    requirements: tuple[Any, ...]


@dataclass(frozen=True)
class ShardEvidence:
    venue: str
    coverage_start: date
    coverage_end: date
    requirements: tuple[Any, ...]
    status: str
    requested_days: int
    checked_days: int
    gaps: tuple[Any, ...]


@dataclass(frozen=True)
class ShardedValidationResult:
    status: str
    reason: str = ""
    shards: tuple[ShardEvidence, ...] = ()
    composite: CompositeResult | None = None
    mode: str = SIMULATION_ONLY
    verification: str = "UNVERIFIED"
    coverage: str = "CALLER_DECLARED_SCOPE_ONLY"
    manifest_coverage: str = "CALLER_DECLARED_RANGE_NOT_FULLY_CHECKED"
    venue_date_layer_status: str = "NOT_PROVEN"
    source_authentication: str = "NOT_AUTHENTICATED"
    utc_coverage: str = "NOT_PROVEN"
    publication: str = "NOT_AUTHORIZED"


def validate_and_compose_simulated(
    *, mode: str, shards: tuple[VenueShard, ...]
) -> ShardedValidationResult:
    """Validate detached shard snapshots, then compose only if all checks pass."""
    if type(mode) is not str or mode != SIMULATION_ONLY:
        return ShardedValidationResult("INVALID_INPUT", "SIMULATION_MODE_REQUIRED")
    try:
        from bt_api_ctp.calendar_candidate_coverage import (
            MAX_DAYS_PER_REQUIREMENT,
            MAX_REQUIREMENTS,
            CoverageRequirement,
            StructuralCoverageResult,
            check_candidate_coverage,
        )
        from bt_api_ctp.calendar_snapshot import CalendarSnapshot
        from bt_api_ctp.calendar_supplement import MAX_LIST_ITEMS, SupplementCandidate
    except ImportError:
        return ShardedValidationResult("DEPENDENCY_UNAVAILABLE", "CTP_UNAVAILABLE")
    try:
        if (
            type(shards) is not tuple
            or len(shards) != len(_VENUES)
            or any(type(shard) is not VenueShard for shard in shards)
            or {shard.venue for shard in shards} != _VENUES
        ):
            raise ValueError
        detached = []
        for shard in shards:
            if (
                type(shard.venue) is not str
                or shard.venue not in _VENUES
                or type(shard.coverage_start) is not date
                or type(shard.coverage_end) is not date
                or shard.coverage_start > shard.coverage_end
                or (shard.coverage_end - shard.coverage_start).days + 1 > MAX_DAYS_PER_REQUIREMENT
                or type(shard.requirements) is not tuple
                or not shard.requirements
                or len(shard.requirements) > MAX_REQUIREMENTS
                or type(shard.candidate) is not SupplementCandidate
                or shard.candidate.verification != "UNVERIFIED"
                or type(shard.candidate.content) is not dict
                or type(shard.candidate.unused_source_ids) is not tuple
            ):
                raise ValueError
            if set(shard.candidate.content) != {
                "sources",
                "trading_days",
                "trading_hours",
                "exceptions",
            } or any(
                type(layer) is not list or len(layer) > MAX_LIST_ITEMS
                for layer in shard.candidate.content.values()
            ):
                raise ValueError
            requirements = deepcopy(shard.requirements)
            for req in requirements:
                if (
                    type(req) is not CoverageRequirement
                    or req.venue != shard.venue
                    or type(req.start_day) is not date
                    or type(req.end_day) is not date
                    or not shard.coverage_start
                    <= req.start_day
                    <= req.end_day
                    <= shard.coverage_end
                ):
                    raise ValueError
            # A single detached candidate is subsequently used for both phases.
            copied = deepcopy(shard.candidate)
            if any(
                type(copied.content.get(layer)) is not list
                or any(
                    type(row) is not dict or row.get("venue") != shard.venue
                    for row in copied.content[layer]
                )
                for layer in ("trading_days", "trading_hours", "exceptions")
            ):
                raise ValueError
            date_counts = {}
            for row in copied.content["trading_days"]:
                raw_day = row.get("day")
                if type(raw_day) is not str:
                    raise ValueError
                parsed_day = date.fromisoformat(raw_day)
                if parsed_day.isoformat() != raw_day:
                    raise ValueError
                if shard.coverage_start <= parsed_day <= shard.coverage_end:
                    date_counts[parsed_day] = date_counts.get(parsed_day, 0) + 1
            expected_count = (shard.coverage_end - shard.coverage_start).days + 1
            if len(date_counts) != expected_count or any(
                count != 1 for count in date_counts.values()
            ):
                return ShardedValidationResult("CHECK_FAILED", "VENUE_DATE_RECORDS_INCOMPLETE")
            detached.append(
                (
                    shard.venue,
                    shard.coverage_start,
                    shard.coverage_end,
                    requirements,
                    copied,
                )
            )
    except Exception:
        return ShardedValidationResult("INVALID_INPUT", "INVALID_SHARDS_OR_SCOPE")
    evidence = []
    for venue, start, end, requirements, copied in sorted(detached):
        try:
            result = check_candidate_coverage(copied, requirements=requirements)
            if type(result) is not StructuralCoverageResult or (
                result.verification,
                result.coverage,
                result.source_authentication,
                result.utc_coverage,
                result.publication,
            ) != (
                "UNVERIFIED",
                "CALLER_DECLARED_SCOPE_ONLY",
                "NOT_AUTHENTICATED",
                "NOT_PROVEN",
                "NOT_AUTHORIZED",
            ):
                raise ValueError
            evidence.append(
                ShardEvidence(
                    venue,
                    start,
                    end,
                    requirements,
                    result.status,
                    result.requested_days,
                    result.checked_days,
                    result.gaps,
                )
            )
            expected = sum((req.end_day - req.start_day).days + 1 for req in requirements)
            if (
                result.status != "STRUCTURAL_COMPLETE_UNVERIFIED"
                or result.gaps
                or type(result.requested_days) is not int
                or type(result.checked_days) is not int
                or result.requested_days != expected
                or result.checked_days != expected
            ):
                return ShardedValidationResult(
                    "CHECK_FAILED", "STRUCTURAL_COVERAGE_NOT_PROVEN", tuple(evidence)
                )
        except Exception:
            return ShardedValidationResult("CHECK_FAILED", "CHECK_ERROR", tuple(evidence))
    try:
        groups = tuple(
            VenueContent.from_layers(venue, start, end, copied.content)
            for venue, start, end, _, copied in detached
        )
        result = compose_simulated(
            mode=SIMULATION_ONLY, venues=groups, snapshot_factory=CalendarSnapshot
        )
        if (
            type(result) is not CompositeResult
            or result.mode != SIMULATION_ONLY
            or result.source_evidence != "UNVERIFIED"
            or result.coverage_evidence != "DECLARED_ONLY"
        ):
            raise ValueError
        if result.status != "READY_SIMULATED":
            reason = result.status if result.status in _COMPOSITE_FAILURES else "UNKNOWN_REJECTION"
            return ShardedValidationResult("COMPOSITE_REJECTED", reason, tuple(evidence))
        if any(
            type(value) is not bytes or not value
            for value in (result.snapshot_bytes, result.manifest_bytes, result.binding_bytes)
        ):
            raise ValueError
        return ShardedValidationResult(
            "READY_SIMULATED",
            shards=tuple(evidence),
            composite=result,
            venue_date_layer_status="EXPLICIT_DATES_STRUCTURALLY_COMPLETE_UNVERIFIED",
        )
    except Exception:
        return ShardedValidationResult("COMPOSITE_ERROR", "COMPOSE_ERROR", tuple(evidence))
