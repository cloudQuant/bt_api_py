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
cannot guarantee an atomic copy of concurrently mutated objects. The automatic
builder captures one private candidate per venue. Scope batches check that
candidate, and final composition checks its complete content independently.
"""

from __future__ import annotations

import json
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


def _large_scope_batches(captured, requirements):
    """Bound and validate the entire shard before checking exact scope views.

    The CTP batch helper normalizes its entire input with a 20k-item limit.
    Large valid shards can exceed that limit even though each scope fits. The
    independent full-shard checks and final provider composition remain
    mandatory; the scope views do not stand in for either of them.
    """
    from bt_api_ctp.calendar_auto_candidate import build_auto_candidate
    from bt_api_ctp.calendar_scope_batches import (
        ScopeBatchResult,
        _requirements,
        _view,
        check_scope_batches,
    )

    from bt_api_py.trading_calendar_candidate_inbox import (
        _check_explicit_intervals,
        _validated_baseline,
    )

    content = _validated_baseline(captured.candidate.content)
    _check_explicit_intervals(content)
    grouped, total = _requirements(
        requirements, captured.venue, captured.coverage_start, captured.coverage_end
    )
    checked = 0
    gaps = []
    for key, items in sorted(grouped.items()):
        view = _view(content, key)
        scoped = build_auto_candidate(
            venue=captured.venue,
            coverage_start=captured.coverage_start,
            coverage_end=captured.coverage_end,
            layers=view.content,
        )
        original_scoped = deepcopy(scoped)
        result = check_scope_batches(scoped, requirements=tuple(items))
        if (
            scoped != original_scoped
            or type(result) is not ScopeBatchResult
            or (
                result.verification,
                result.coverage,
                result.source_authentication,
                result.utc_coverage,
                result.publication,
                result.global_relations,
                result.final_composition,
            )
            != (
                "UNVERIFIED",
                "CALLER_DECLARED_SCOPE_ONLY",
                "SOURCE_NOT_AUTHENTICATED",
                "UTC_NOT_PROVEN",
                "NOT_AUTHORIZED",
                "OTHER_SCOPE_RELATIONS_NOT_CHECKED",
                "FINAL_COMPOSITION_AND_CONFLICT_CHECK_REQUIRED",
            )
        ):
            raise ValueError("invalid scope result")
        if result.status != "SCOPES_CHECKED_UNVERIFIED" or result.gaps:
            raise ValueError("scope check failed")
        expected = sum((item.end_day - item.start_day).days + 1 for item in items)
        if result.requested_days != expected or result.checked_days != expected:
            raise ValueError("incomplete scope check")
        checked += result.checked_days
        gaps.extend(result.gaps)
    return ScopeBatchResult("SCOPES_CHECKED_UNVERIFIED", tuple(gaps), total, checked)


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
    batch_status: str = "NOT_CHECKED"


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
    utc_evidence: str = "UTC_NOT_PROVEN"
    publication: str = "NOT_AUTHORIZED"


def validate_and_compose_simulated(
    *, mode: str, shards: tuple[VenueShard, ...]
) -> ShardedValidationResult:
    """Validate detached shard snapshots, then compose only if all checks pass."""
    if type(mode) is not str or mode != SIMULATION_ONLY:
        return ShardedValidationResult("INVALID_INPUT", "SIMULATION_MODE_REQUIRED")
    try:
        from bt_api_ctp.calendar_auto_candidate import (
            MAX_JSON_DEPTH,
            MAX_JSON_NODES,
            AutoCandidateResult,
            build_auto_candidate,
        )
        from bt_api_ctp.calendar_candidate_coverage import (
            MAX_DAYS_PER_REQUIREMENT,
            MAX_REQUIREMENTS,
            CoverageRequirement,
        )
        from bt_api_ctp.calendar_scope_batches import ScopeBatchResult, check_scope_batches
        from bt_api_ctp.calendar_snapshot import CalendarSnapshot
        from bt_api_ctp.calendar_supplement import MAX_LIST_ITEMS, SupplementCandidate
    except ImportError:
        return ShardedValidationResult("DEPENDENCY_UNAVAILABLE", "CTP_UNAVAILABLE")

    def state(captured, requirements):
        if (
            type(captured) is not AutoCandidateResult
            or type(captured.candidate) is not SupplementCandidate
            or type(captured.candidate.unused_source_ids) is not tuple
            or any(type(item) is not str for item in captured.candidate.unused_source_ids)
            or type(captured.coverage_start) is not date
            or type(captured.coverage_end) is not date
            or any(
                type(getattr(captured, field)) is not str
                for field in (
                    "status",
                    "venue",
                    "reason",
                    "verification",
                    "coverage",
                    "publication",
                )
            )
            or type(captured.candidate.verification) is not str
        ):
            raise ValueError
        active = set()
        nodes = 0

        def check_plain(value, depth=0):
            nonlocal nodes
            nodes += 1
            if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
                raise ValueError
            if value is None or type(value) in (str, int, bool):
                return
            if type(value) not in (list, dict) or id(value) in active:
                raise ValueError
            active.add(id(value))
            try:
                if type(value) is dict:
                    if any(type(key) is not str for key in value):
                        raise ValueError
                    for item in value.values():
                        check_plain(item, depth + 1)
                else:
                    for item in value:
                        check_plain(item, depth + 1)
            finally:
                active.remove(id(value))

        check_plain(captured.candidate.content)
        if type(requirements) is not tuple or any(
            type(req) is not CoverageRequirement
            or any(
                type(getattr(req, field)) is not str
                for field in ("venue", "kind", "product", "contract")
            )
            or type(req.start_day) is not date
            or type(req.end_day) is not date
            for req in requirements
        ):
            raise ValueError
        req_state = tuple(
            (type(req), req.venue, req.kind, req.product, req.contract, req.start_day, req.end_day)
            for req in requirements
        )
        encoded = json.dumps(
            {
                "status": captured.status,
                "venue": captured.venue,
                "coverage_start": captured.coverage_start.isoformat(),
                "coverage_end": captured.coverage_end.isoformat(),
                "reason": captured.reason,
                "verification": captured.verification,
                "coverage": captured.coverage,
                "publication": captured.publication,
                "content": captured.candidate.content,
                "candidate_verification": captured.candidate.verification,
                "unused_source_ids": captured.candidate.unused_source_ids,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return encoded, req_state

    def unchanged(captured, requirements, original_state):
        try:
            return state(captured, requirements) == original_state
        except Exception:
            return False

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
            # The builder safely captures the full source once; both the batch
            # checker and final composer operate on this detached candidate.
            captured = build_auto_candidate(
                venue=shard.venue,
                coverage_start=shard.coverage_start,
                coverage_end=shard.coverage_end,
                layers=shard.candidate.content,
            )
            if (
                type(captured) is not AutoCandidateResult
                or captured.status != "CANDIDATE"
                or type(captured.candidate) is not SupplementCandidate
                or captured.candidate.unused_source_ids != shard.candidate.unused_source_ids
                or (captured.verification, captured.coverage, captured.publication)
                != ("UNVERIFIED", "NOT_PROVEN", "NOT_AUTHORIZED")
            ):
                raise ValueError
            copied = captured.candidate
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
                    captured,
                    state(captured, requirements),
                )
            )
    except Exception:
        return ShardedValidationResult("INVALID_INPUT", "INVALID_SHARDS_OR_SCOPE")
    # This public entry can be called without review_bundle. Preflight the
    # complete detached six-venue state before any CTP scope snapshot or final
    # composite snapshot is constructed.
    try:
        from bt_api_py.trading_calendar_snapshot_budget import check_snapshot_budget

        contents = [json.loads(original_state[0])["content"] for *_, original_state in detached]
        combined = {
            layer: [row for content in contents for row in content[layer]]
            for layer in ("trading_days", "trading_hours", "exceptions")
        }
        check_snapshot_budget(combined, {venue for venue, *_ in detached})
    except Exception:
        return ShardedValidationResult("INVALID_INPUT", "SNAPSHOT_BUDGET_UNAVAILABLE")
    evidence = []
    for venue, start, end, requirements, captured, original_state in sorted(detached):
        try:
            if not unchanged(captured, requirements, original_state):
                return ShardedValidationResult(
                    "CHECK_FAILED", "CHECKER_MUTATED_INPUT", tuple(evidence)
                )
            result = check_scope_batches(captured, requirements=requirements)
            if result.status == "STRUCTURAL_GAPS" and result.reason == "FULL_CANDIDATE_INVALID":
                from bt_api_ctp.calendar_candidate_normalize import _bounded_total

                try:
                    _bounded_total(captured.candidate.content)
                except ValueError as exc:
                    if str(exc) != "candidate exceeds total item bound":
                        raise
                    result = _large_scope_batches(captured, requirements)
            if not unchanged(captured, requirements, original_state):
                return ShardedValidationResult(
                    "CHECK_FAILED", "CHECKER_MUTATED_INPUT", tuple(evidence)
                )
            if type(result) is not ScopeBatchResult or (
                result.verification,
                result.coverage,
                result.source_authentication,
                result.utc_coverage,
                result.publication,
                result.global_relations,
                result.final_composition,
            ) != (
                "UNVERIFIED",
                "CALLER_DECLARED_SCOPE_ONLY",
                "SOURCE_NOT_AUTHENTICATED",
                "UTC_NOT_PROVEN",
                "NOT_AUTHORIZED",
                "OTHER_SCOPE_RELATIONS_NOT_CHECKED",
                "FINAL_COMPOSITION_AND_CONFLICT_CHECK_REQUIRED",
            ):
                raise ValueError
            if type(result.gaps) is not tuple:
                raise ValueError
            evidence.append(
                ShardEvidence(
                    venue,
                    start,
                    end,
                    requirements,
                    "STRUCTURAL_COMPLETE_UNVERIFIED"
                    if result.status == "SCOPES_CHECKED_UNVERIFIED"
                    else result.status,
                    result.requested_days,
                    result.checked_days,
                    result.gaps,
                    result.status,
                )
            )
            expected = sum((req.end_day - req.start_day).days + 1 for req in requirements)
            if (
                result.status != "SCOPES_CHECKED_UNVERIFIED"
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
    if any(
        not unchanged(captured, requirements, original_state)
        for _, _, _, requirements, captured, original_state in detached
    ):
        return ShardedValidationResult("CHECK_FAILED", "CHECKER_MUTATED_INPUT", tuple(evidence))
    try:
        groups = tuple(
            VenueContent.from_layers(venue, start, end, json.loads(original_state[0])["content"])
            for venue, start, end, _, _, original_state in detached
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
