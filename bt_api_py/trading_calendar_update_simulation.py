"""SIMULATION_ONLY: pure calendar-update decisions, never publication authority.

Inputs and validators are caller-supplied synthetic claims, not exchange facts.
No filesystem, network, account, clock, scheduler, alert, YAML, review workflow,
or store operation is performed. Directory tokens are opaque simulation labels,
not proof of physical-directory identity or policy freshness. A real publisher
must recheck configuration, candidate, evidence and concurrency independently.

The provisional venue payload contains only material calendar content: the four
SDK-style layers plus explicit coverage. Check/fetch/run timestamps are separate
attempt metadata. Every byte admitted into a layer is treated as material; callers
must not place volatile metadata there. Common volatile keys are rejected rather
than silently erased. This is not a frozen production schema or semantic diff.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

SIMULATION_ONLY = "SIMULATION_ONLY"
_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_LAYERS = ("trading_days", "trading_hours", "exceptions", "sources")
_VOLATILE = frozenset(
    {
        "checked_at",
        "checked_at_utc",
        "check_time",
        "retrieved_at",
        "retrieved_at_utc",
        "fetched_at",
        "fetched_at_utc",
        "fetch_time",
        "run_id",
        "last_success",
        "last_success_at",
        "last_success_at_utc",
    }
)


def _text(value: Any) -> bool:
    return type(value) is str and _TEXT.fullmatch(value) is not None


def _utc(value: Any) -> bool:
    return (
        type(value) is datetime and value.tzinfo is not None and value.utcoffset() == timedelta(0)
    )


def _json_value(value: Any) -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or key in _VOLATILE:
                raise ValueError("invalid or volatile content key")
            _json_value(item)
        return
    raise ValueError("invalid JSON content")


def _canonical(value: Any) -> bytes:
    try:
        _json_value(value)
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid stable content") from exc


@dataclass(frozen=True)
class VenueContent:
    """Immutable material content with a caller-supplied, inclusive coverage range."""

    venue: str
    coverage_start: date
    coverage_end: date
    canonical_layers: bytes

    def __post_init__(self) -> None:
        if (
            not _text(self.venue)
            or type(self.coverage_start) is not date
            or type(self.coverage_end) is not date
            or self.coverage_start > self.coverage_end
            or type(self.canonical_layers) is not bytes
        ):
            raise ValueError("invalid venue content")
        try:
            value = json.loads(self.canonical_layers.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("invalid venue JSON") from exc
        if (
            type(value) is not dict
            or set(value) != set(_LAYERS)
            or any(type(value[key]) not in (dict, list) for key in _LAYERS)
            or _canonical(value) != self.canonical_layers
        ):
            raise ValueError("invalid canonical four-layer content")

    @classmethod
    def from_layers(
        cls, venue: str, coverage_start: date, coverage_end: date, layers: dict[str, Any]
    ) -> VenueContent:
        return cls(venue, coverage_start, coverage_end, _canonical(layers))


@dataclass(frozen=True)
class PriorVenue:
    content: VenueContent
    last_success_at_utc: datetime | None


@dataclass(frozen=True)
class VenueAttempt:
    venue: str
    checked_at_utc: datetime
    status: str  # SUCCESS or FAILED; neither is a claim about a real exchange.
    conflict: str  # CLEAR, CONFLICT or UNKNOWN; supplied as simulated evidence.
    candidate: VenueContent | None = None
    failure_code: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class SimulationConfig:
    directory_token: str
    read_ok: bool
    settings: dict[str, Any] | None


@dataclass(frozen=True)
class VenueValidation:
    """Five simulation checks; only exact booleans all True admit a value."""

    source: bool
    scope: bool
    structure: bool
    conflict_free: bool
    complete: bool


class SimulationValidator(Protocol):
    def validate_venue(
        self,
        content: VenueContent,
        attempt: VenueAttempt,
        required_start: date,
        required_end: date,
        *,
        retained: bool,
    ) -> VenueValidation:
        """Evaluate one candidate or retained previous value in the simulation."""
        ...

    def validate_composite(
        self, contents: tuple[VenueContent, ...], required_start: date, required_end: date
    ) -> bool:
        """Evaluate simulated cross-venue dependencies and composite completeness."""
        ...


@dataclass(frozen=True)
class VenueReport:
    venue: str
    check: str
    last_success_at_utc: datetime | None
    coverage_start: date | None
    coverage_end: date | None
    changed_fields: tuple[str, ...]
    reason: str | None
    retention_reason: str | None


@dataclass(frozen=True)
class SimulationResult:
    mode: str
    outcome: str
    policy: str | None
    policy_origin: str | None
    reports: tuple[VenueReport, ...]
    candidate: tuple[VenueContent, ...] | None
    candidate_sha256: str | None
    reason: str | None = None
    requires_policy_recheck: bool = True


def _policy(directory_token: str, config: Any) -> tuple[str, str] | None:
    if (
        not _text(directory_token)
        or type(config) is not SimulationConfig
        or config.directory_token != directory_token
        or type(config.read_ok) is not bool
        or not config.read_ok
        or type(config.settings) is not dict
    ):
        return None
    try:
        values = config.settings.copy()
        if not set(values) <= {"review_mode"}:
            return None
        if "review_mode" not in values:
            return "auto", "default"
        value = values["review_mode"]
    except RuntimeError:
        return None
    if type(value) is not str or value not in ("auto", "manual"):
        return None
    return value, "explicit"


def _diff(previous: VenueContent | None, candidate: VenueContent) -> tuple[str, ...]:
    if previous is None:
        return ("coverage",) + _LAYERS
    old = json.loads(previous.canonical_layers)
    new = json.loads(candidate.canonical_layers)
    changed = []
    if (previous.coverage_start, previous.coverage_end) != (
        candidate.coverage_start,
        candidate.coverage_end,
    ):
        changed.append("coverage")
    changed.extend(key for key in _LAYERS if _canonical(old[key]) != _canonical(new[key]))
    return tuple(changed)


def _admitted(check: Any) -> bool:
    return type(check) is VenueValidation and all(
        getattr(check, name) is True
        for name in ("source", "scope", "structure", "conflict_free", "complete")
    )


def _result(
    outcome: str,
    policy: str | None = None,
    origin: str | None = None,
    reports: tuple[VenueReport, ...] = (),
    candidate: tuple[VenueContent, ...] | None = None,
    digest: str | None = None,
    reason: str | None = None,
) -> SimulationResult:
    return SimulationResult(
        SIMULATION_ONLY, outcome, policy, origin, reports, candidate, digest, reason
    )


def simulate_update(
    *,
    mode: str,
    directory_token: str,
    config: SimulationConfig,
    expected_venues: tuple[str, ...],
    required_start: date,
    required_end: date,
    prior: tuple[PriorVenue, ...],
    attempts: tuple[VenueAttempt, ...],
    evaluated_at_utc: datetime,
    validator: SimulationValidator,
    candidate_builder: Callable[[tuple[VenueContent, ...]], Any] | None = None,
) -> SimulationResult:
    """Make an in-memory hypothetical decision; no output represents a publication."""
    if type(mode) is not str or mode != SIMULATION_ONLY:
        raise ValueError("explicit SIMULATION_ONLY mode is required")
    policy = _policy(directory_token, config)
    if policy is None:
        return _result("CONFIG_ERROR", reason="CONFIG_UNAVAILABLE_OR_INVALID")
    name, origin = policy
    if (
        type(expected_venues) is not tuple
        or not expected_venues
        or any(not _text(venue) for venue in expected_venues)
        or len(set(expected_venues)) != len(expected_venues)
        or type(required_start) is not date
        or type(required_end) is not date
        or required_start > required_end
        or not _utc(evaluated_at_utc)
        or type(prior) is not tuple
        or type(attempts) is not tuple
        or not callable(getattr(validator, "validate_venue", None))
        or not callable(getattr(validator, "validate_composite", None))
        or (candidate_builder is not None and not callable(candidate_builder))
    ):
        return _result("INPUT_ERROR", name, origin, reason="INVALID_INPUT")
    old_by_venue = {}
    for row in prior:
        if (
            type(row) is not PriorVenue
            or type(row.content) is not VenueContent
            or row.content.venue not in expected_venues
            or row.content.venue in old_by_venue
            or (
                row.last_success_at_utc is not None
                and (
                    not _utc(row.last_success_at_utc) or row.last_success_at_utc > evaluated_at_utc
                )
            )
        ):
            return _result("INPUT_ERROR", name, origin, reason="INVALID_PRIOR")
        old_by_venue[row.content.venue] = row
    attempts_by_venue = {}
    for attempt in attempts:
        if (
            type(attempt) is not VenueAttempt
            or attempt.venue not in expected_venues
            or attempt.venue in attempts_by_venue
            or not _utc(attempt.checked_at_utc)
            or attempt.checked_at_utc > evaluated_at_utc
            or attempt.status not in ("SUCCESS", "FAILED")
            or attempt.conflict not in ("CLEAR", "CONFLICT", "UNKNOWN")
            or (attempt.run_id is not None and not _text(attempt.run_id))
            or (
                attempt.status == "SUCCESS"
                and (
                    type(attempt.candidate) is not VenueContent
                    or attempt.candidate.venue != attempt.venue
                    or attempt.failure_code is not None
                )
            )
            or (
                attempt.status == "FAILED"
                and (attempt.candidate is not None or not _text(attempt.failure_code))
            )
        ):
            return _result("INPUT_ERROR", name, origin, reason="INVALID_ATTEMPT")
        old = old_by_venue.get(attempt.venue)
        if (
            old is not None
            and old.last_success_at_utc is not None
            and attempt.checked_at_utc < old.last_success_at_utc
        ):
            return _result("INPUT_ERROR", name, origin, reason="CHECK_TIME_REGRESSION")
        attempts_by_venue[attempt.venue] = attempt

    reports = []
    combined = []
    blocked = False
    failures = False
    changed = False
    for venue in sorted(expected_venues):
        old = old_by_venue.get(venue)
        attempt = attempts_by_venue.get(venue)
        old_time = old.last_success_at_utc if old is not None else None
        selected = None
        reason = None
        retention_reason = None
        check = "FAILED_BLOCKED"
        diff: tuple[str, ...] = ()
        if attempt is None:
            reason = "MISSING_ATTEMPT"
        elif attempt.conflict != "CLEAR":
            reason = "KNOWN_CONFLICT" if attempt.conflict == "CONFLICT" else "CONFLICT_UNCERTAIN"
        elif attempt.status == "SUCCESS":
            proposed = attempt.candidate
            try:
                assessment = validator.validate_venue(
                    proposed, attempt, required_start, required_end, retained=False
                )
            except Exception:
                reason = "VALIDATOR_ERROR"
            else:
                if (
                    _admitted(assessment)
                    and proposed.coverage_start <= required_start
                    and proposed.coverage_end >= required_end
                ):
                    selected = proposed
                    diff = _diff(old.content if old else None, proposed)
                    check = "SUCCESS_CHANGED" if diff else "SUCCESS_UNCHANGED"
                else:
                    reason = "CANDIDATE_VALIDATION_FAILED"
                    # A negative/unknown conflict result is never safe for fallback.
                    if (
                        type(assessment) is not VenueValidation
                        or assessment.conflict_free is not True
                    ):
                        retention_reason = "CONFLICT_UNCERTAIN"
        else:
            reason = attempt.failure_code
        if selected is None:
            failures = True
            can_retain = (
                attempt is not None
                and attempt.conflict == "CLEAR"
                and old is not None
                and reason != "VALIDATOR_ERROR"
                and retention_reason is None
                and old.content.coverage_start <= required_start
                and old.content.coverage_end >= required_end
            )
            if can_retain:
                try:
                    retained = validator.validate_venue(
                        old.content, attempt, required_start, required_end, retained=True
                    )
                except Exception:
                    retention_reason = "VALIDATOR_ERROR"
                else:
                    if _admitted(retained):
                        selected = old.content
                        check = "FAILED_CARRIED"
                    else:
                        retention_reason = "RETAINED_VALIDATION_FAILED"
            if selected is None:
                if retention_reason is None:
                    if old is None:
                        retention_reason = "NO_PRIOR"
                    elif (
                        old.content.coverage_start > required_start
                        or old.content.coverage_end < required_end
                    ):
                        retention_reason = "PRIOR_COVERAGE_INSUFFICIENT"
                    else:
                        retention_reason = "RETAINMENT_UNPROVEN"
                blocked = True
        else:
            changed = changed or bool(diff)
        if selected is not None:
            combined.append(selected)
        reports.append(
            VenueReport(
                venue,
                check,
                attempt.checked_at_utc if check.startswith("SUCCESS_") else old_time,
                selected.coverage_start
                if selected
                else (old.content.coverage_start if old else None),
                selected.coverage_end if selected else (old.content.coverage_end if old else None),
                diff,
                reason,
                retention_reason,
            )
        )
    report_tuple = tuple(reports)
    if blocked:
        return _result("BLOCKED", name, origin, report_tuple, reason="VENUE_UNAVAILABLE")
    if _policy(directory_token, config) != policy:
        return _result("CONFIG_ERROR", name, origin, report_tuple, reason="CONFIG_CHANGED")
    if not changed:
        return _result(
            "NO_CHANGES_WITH_FAILURES" if failures else "NO_CHANGES", name, origin, report_tuple
        )
    expected = tuple(combined)
    try:
        built = expected if candidate_builder is None else candidate_builder(expected)
        if (
            type(built) is not tuple
            or built != expected
            or any(type(item) is not VenueContent for item in built)
        ):
            return _result("BLOCKED", name, origin, report_tuple, reason="BUILDER_INVALID")
    except Exception:
        return _result("BLOCKED", name, origin, report_tuple, reason="BUILDER_ERROR")
    try:
        if validator.validate_composite(built, required_start, required_end) is not True:
            return _result("BLOCKED", name, origin, report_tuple, reason="COMPOSITE_UNPROVEN")
    except Exception:
        return _result("BLOCKED", name, origin, report_tuple, reason="VALIDATOR_ERROR")
    if _policy(directory_token, config) != policy:
        return _result("CONFIG_ERROR", name, origin, report_tuple, reason="CONFIG_CHANGED")
    payload = [
        {
            "venue": item.venue,
            "coverage_start": item.coverage_start.isoformat(),
            "coverage_end": item.coverage_end.isoformat(),
            "layers": json.loads(item.canonical_layers),
        }
        for item in built
    ]
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    return _result(
        "WOULD_PUBLISH" if name == "auto" else "NEEDS_REVIEW",
        name,
        origin,
        report_tuple,
        built,
        digest,
    )
