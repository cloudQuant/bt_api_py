"""Experimental preview of local inputs and two optional SHFE source fetches.

Usage: python -m bt_api_py.trading_calendar_preview --config /absolute/config.yaml
  --calendar-root /absolute/calendar --supplement /absolute/supplement.yaml
  --budget-seconds 30 [--enable-shfe]
  [--report-dir /absolute/existing/directory --trusted-local-posix]
  [--retain-candidate --trusted-local-posix]
  [--catalog-cache-dir /absolute/cache --catalog-scope HASH --catalog-trading-day YYYYMMDD]
  [--product-page-url HTTPS_URL --product-page-file /absolute/page.html]
  [--discover-product-navigation]
  [--product-source {CU_FUTURE,CU_OPTION,BC_OPTION}]

No CURRENT is read or written, no candidate is approved or published, and no
account or email is accessed. Input diagnostics, source attempts and results are
finite summaries only: paths, configuration values, source data and exceptions
are not printed. The other five venues remain INCOMPLETE. Even a successful
two-source fetch is UNVERIFIED, not an official calendar or release decision.

The CLI supervises its own worker in a new POSIX session with a total deadline,
and kills that owned process group on timeout. OS process creation and an
uninterruptible kernel process cannot be strictly bounded; cleanup uncertainty
is explicit. Windows and network-filesystem behavior are NOT_RUN. The callable
``run_preview`` is for embedding/tests and cannot forcibly interrupt a blocking
injected port; use the supervised CLI for a process deadline. Source timeouts
and JSON do not prove provenance, coverage, freshness or a stable input file.
Optional retention happens only after supervision, outside the preview budget;
the caller's filesystem declaration is not independently verified. Retention
does not publish a calendar and has no strict bound on local filesystem calls.
The original ``source_status`` and ``attempts`` fields describe the trading
hours page; ``holiday_status`` and ``holiday_attempts`` are additive fields
for the holiday page. Parsing status is separate from transport status, and an
interrupted or failed child can leave whether parsing ran UNKNOWN.
Candidate retention occurs inside the supervised worker after input validation;
the preview report contains only its ID and finite result, never candidate data.
The explicit candidate-inbox show command displays it. Candidate retention can
optionally observe an explicitly selected local CTP catalog. The three
catalog options require candidate retention and the filesystem declaration;
they do not authorize CTP sessions or network queries. The scope and cache path
are not included in normal output or the candidate. Catalog identities,
freshness, metadata and source remain unverified declarations.
The optional product page is a caller-supplied local file and declared URL;
neither authenticates its origin, selects an effective record or proves coverage.
Navigation discovery is explicit and uses the same in-memory local bytes; it
never fetches discovered paths, and retained digest equality is not provenance.
An explicit product source enables a bounded network request for one fixed page;
its observed status and matching source fields still do not authenticate it.
A requested retention result NOT_COMMITTED returns 24, COMMIT_UNKNOWN returns 25, and UNKNOWN takes
priority across both retentions. Unattempted or successful retention preserves
the preview exit code; retained data is not an approved release.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from bt_api_py.trading_calendar_check import (
    CalendarCheckReport,
    _check_calendar_inputs_with_candidate,
)

_DEFAULT_INTERVAL = 600.0
_MAX_BUDGET = 86400.0
_MAX_RETRIES = 2
_PER_ATTEMPT = 10.0
_CLEANUP_ALLOWANCE = 1.0
_MAX_JSON = 65536
_TRANSPORT = frozenset(
    {
        "HTTP_OK",
        "HTTP_ERROR",
        "REDIRECT",
        "TLS_ERROR",
        "NETWORK_TIMEOUT",
        "NETWORK_ERROR",
        "RESPONSE_TOO_LARGE",
        "EMPTY_RESPONSE",
        "PARSER_ERROR",
        "RESULT_TOO_LARGE",
        "CHILD_ERROR",
        "CHILD_TIMEOUT",
        "CHILD_CLEANUP_UNCERTAIN",
        "SUPERVISOR_UNSUPPORTED",
    }
)
_PAGE = frozenset(
    {
        "CANDIDATE",
        "NEEDS_REVIEW",
        "CONFLICT",
        "STRUCTURE_CHANGED",
        "INVALID_UTF8",
        "INVALID_SOURCE",
        "INVALID_INPUT",
        "INPUT_TOO_LARGE",
    }
)
_CHECK_CODES = frozenset(
    {
        "SUPPLEMENT_UNVERIFIED",
        "INVALID_ABSOLUTE_PATH",
        "CONFIG_DEPENDENCY_UNAVAILABLE",
        "CONFIG_READ_FAILED",
        "CONFIG_NOT_REGULAR_FILE",
        "CONFIG_TOO_LARGE",
        "CONFIG_ENCODING_INVALID",
        "CONFIG_YAML_INVALID",
        "CONFIG_DUPLICATE_KEY",
        "CONFIG_ALIAS_FORBIDDEN",
        "CONFIG_ROOT_INVALID",
        "CONFIG_KEY_INVALID",
        "CONFIG_TOO_COMPLEX",
        "CONFIG_TOO_DEEP",
        "CONFIG_CHANGED",
        "CONFIG_PLATFORM_UNSUPPORTED",
        "CONFIG_INVALID",
        "calendar_config_invalid",
        "calendar_config_invalid_mode",
        "calendar_config_invalid_file_path",
        "calendar_config_unknown_field",
        "calendar_review_mode_invalid",
        "calendar_root_invalid",
        "calendar_root_invalid_home",
        "CALENDAR_ROOT_UNCONFIGURED",
        "EXTERNAL_ROOT_REQUIRED",
        "CALENDAR_ROOT_MISMATCH",
        "CALENDAR_ROOT_UNAVAILABLE",
        "CALENDAR_ROOT_NOT_DIRECTORY",
        "CTP_READER_UNAVAILABLE",
        "SUPPLEMENT_MISSING",
        "SUPPLEMENT_UNSUPPORTED",
        "SUPPLEMENT_DEPENDENCY_UNAVAILABLE",
        "SUPPLEMENT_INVALID_PATH",
        "SUPPLEMENT_SYMLINK",
        "SUPPLEMENT_NOT_REGULAR",
        "SUPPLEMENT_CHANGED",
        "SUPPLEMENT_TOO_LARGE",
        "SUPPLEMENT_INVALID_UTF8",
        "SUPPLEMENT_INVALID_CONTENT",
        "SUPPLEMENT_READ_ERROR",
        "SUPPLEMENT_PARSER_ERROR",
        "SUPPLEMENT_READER_ERROR",
    }
)


@dataclass(frozen=True)
class Attempt:
    number: int
    transport_status: str
    source_status: str
    verification: str
    product_count: int
    parse_status: str = "NOT_ATTEMPTED"


@dataclass(frozen=True)
class PreviewReport:
    status: str
    reason: str
    exit_code: int
    input_status: str = "NOT_CHECKED"
    input_code: str = "NOT_CHECKED"
    supplement_status: str = "NOT_CHECKED"
    source_status: str = "NOT_REQUESTED"
    attempts: tuple[Attempt, ...] = ()
    source_reason: str = "NOT_REQUESTED"
    holiday_status: str = "NOT_REQUESTED"
    holiday_attempts: tuple[Attempt, ...] = ()
    holiday_reason: str = "NOT_REQUESTED"
    holiday_coverage: str = "NOT_PROVEN"
    holiday_conversion_status: str = "NOT_ATTEMPTED"
    holiday_closed_count: int | None = None
    holiday_unresolved_night_count: int | None = None
    merge_status: str = "NOT_ATTEMPTED"
    merge_reason: str = "NONE"
    merge_source_count: int | None = None
    merge_day_count: int | None = None
    merge_hour_count: int | None = None
    merge_exception_count: int | None = None
    merge_hour_conflicts: str = "NOT_CHECKED"
    merge_exception_conflicts: str = "NOT_CHECKED"
    merge_cross_layer_conflicts: str = "NOT_CHECKED"
    normalization_status: str = "NOT_ATTEMPTED"
    normalization_scope: str = "FOUR_LAYER_STRUCTURE_ONLY"
    normalization_nights: str = "UNRESOLVED_NIGHTS_NOT_INCLUDED"
    substantive_change: str = "NOT_ASSESSED"
    source_page_changes: str = "NOT_CHECKED"
    other_five_venues: str = "NOT_IMPLEMENTED"
    completeness: str = "INCOMPLETE"
    verification: str = "UNVERIFIED"
    publication: str = "NOT_ATTEMPTED"
    maturity: str = "EXPERIMENTAL_PREVIEW"
    candidate_id: str = ""
    candidate_status: str = "NOT_REQUESTED"
    candidate_reason: str = "NONE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _valid_number(value: object, minimum: float, maximum: float) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum
    except (OverflowError, ValueError):
        return False


def _default_collector(**kwargs):
    from bt_api_ctp.calendar_shfe_fetch import collect_shfe_page

    return collect_shfe_page(**kwargs)


def _default_holiday_collector(**kwargs):
    from bt_api_ctp.calendar_shfe_fetch import collect_shfe_holiday

    return collect_shfe_holiday(**kwargs)


def _default_holiday_converter(candidate):
    from bt_api_ctp.calendar_shfe_candidates import convert_shfe_holiday

    return convert_shfe_holiday(candidate)


def _default_merge(**kwargs):
    from bt_api_ctp.calendar_candidate_merge import merge_calendar_candidates

    return merge_calendar_candidates(**kwargs)


def _default_normalizer(candidate):
    from bt_api_ctp.calendar_candidate_normalize import normalize_calendar_candidate

    return normalize_calendar_candidate(candidate)


def _mark_source_cleanup_uncertain(exc):
    """Preserve the exception while marking the unproved child cleanup."""
    with contextlib.suppress(Exception):
        exc.child_cleanup = "CHILD_CLEANUP_UNCERTAIN"
        if hasattr(exc, "add_note"):
            exc.add_note("CHILD_CLEANUP_UNCERTAIN")


def _normalization_summary(result: object) -> str:
    """Copy only the finite outcome; never expose bytes or their digest."""
    try:
        if (
            result.verification,
            result.coverage,
            result.publication,
            result.comparison,
            result.unresolved_nights,
        ) != (
            "UNVERIFIED",
            "NOT_PROVEN",
            "NOT_AUTHORIZED",
            "FOUR_LAYER_STRUCTURE_ONLY",
            "NOT_INCLUDED",
        ):
            raise ValueError
        if result.status == "INVALID_INPUT":
            if (
                result.json_bytes is not None
                or result.sha256 is not None
                or result.reason != "INVALID_OR_AMBIGUOUS_INPUT"
            ):
                raise ValueError
            return "INVALID_INPUT"
        if (
            result.status != "NORMALIZED"
            or result.reason != ""
            or type(result.json_bytes) is not bytes
            or not result.json_bytes
            or type(result.sha256) is not str
            or len(result.sha256) != 64
            or any(c not in "0123456789abcdef" for c in result.sha256)
        ):
            raise ValueError
        return "NORMALIZED"
    except Exception:
        return "NORMALIZATION_PROTOCOL_ERROR"


def _merge_summary(result: object) -> tuple[str, str, tuple[int, ...] | None]:
    try:
        required = {
            "TRADING_HOURS_CONFLICTS_NOT_CHECKED",
            "EXCEPTIONS_CONFLICTS_NOT_CHECKED",
            "CROSS_LAYER_CONFLICTS_NOT_CHECKED",
            "SOURCE_AUTHORITY_UNVERIFIED",
            "COVERAGE_NOT_PROVEN",
            "NIGHT_TRADING_DAY_UNRESOLVED",
        }
        if (
            (result.verification, result.coverage, result.publication)
            != ("UNVERIFIED", "NOT_PROVEN", "NOT_AUTHORIZED")
            or type(result.remaining_checks) is not tuple
            or not required <= set(result.remaining_checks)
        ):
            raise ValueError
        if result.status == "CONFLICT":
            if (
                result.reason not in {"SOURCE_CONFLICT", "TRADING_DAY_CONFLICT"}
                or result.supplement is not None
            ):
                raise ValueError
            return "CONFLICT", result.reason, None
        if result.status == "INVALID_INPUT":
            if result.reason != "INVALID_OR_UNSUPPORTED_INPUT" or result.supplement is not None:
                raise ValueError
            return "INVALID_INPUT", result.reason, None
        if (
            result.status != "PARTIAL"
            or result.reason != ""
            or result.supplement.verification != "UNVERIFIED"
        ):
            raise ValueError
        content = result.supplement.content
        layers = ("sources", "trading_days", "trading_hours", "exceptions")
        if type(content) is not dict or set(content) != set(layers):
            raise ValueError
        if any(type(content[key]) is not list or len(content[key]) > 1000 for key in layers):
            raise ValueError
        return "PARTIAL", "NONE", tuple(len(content[key]) for key in layers)
    except Exception:
        return "MERGE_PROTOCOL_ERROR", "NONE", None


def _conversion_summary(result: object) -> tuple[str, int | None, int | None]:
    """Expose only bounded counts; candidate content and provenance stay private."""
    try:
        if (result.verification, result.coverage, result.publication) != (
            "UNVERIFIED",
            "NOT_PROVEN",
            "NOT_AUTHORIZED",
        ):
            raise ValueError
        if result.status == "INVALID_CANDIDATE":
            if result.supplement is not None or result.unresolved_nights != ():
                raise ValueError
            return "INVALID_CANDIDATE", None, None
        if result.status != "CANDIDATE" or result.supplement.verification != "UNVERIFIED":
            raise ValueError
        content = result.supplement.content
        if type(content) is not dict or set(content) != {
            "sources",
            "trading_days",
            "trading_hours",
            "exceptions",
        }:
            raise ValueError
        days = content["trading_days"]
        nights = result.unresolved_nights
        if (
            type(days) is not list
            or not 1 <= len(days) <= 366
            or any(
                type(row) is not dict or row.get("venue") != "SHFE" or row.get("status") != "closed"
                for row in days
            )
            or type(nights) is not tuple
            or not 1 <= len(nights) <= 32
            or content["trading_hours"] != []
            or content["exceptions"] != []
        ):
            raise ValueError
        return "CANDIDATE", len(days), len(nights)
    except Exception:
        return "CONVERSION_PROTOCOL_ERROR", None, None


def _attempt(result: object, number: int, *, holiday: bool = False) -> Attempt:
    """Copy only validated finite metadata; never return content or exceptions."""
    try:
        transport = result.transport_status
        source_status = result.status
        page = result.page
        if (
            type(transport) is not str
            or transport not in _TRANSPORT
            or type(source_status) is not str
            or result.verification != "UNVERIFIED"
            or (holiday and result.coverage != "NOT_PROVEN")
        ):
            raise ValueError
        if transport == "HTTP_OK":
            if (
                page is None
                or source_status not in _PAGE
                or page.status != source_status
                or page.verification != "UNVERIFIED"
                or (
                    holiday
                    and (
                        result.coverage != "NOT_PROVEN"
                        or page.coverage != "NOT_PROVEN"
                        or type(page.rows) is not tuple
                        or len(page.rows) > 32
                    )
                )
                or (not holiday and (type(page.products) is not tuple or len(page.products) > 1024))
            ):
                raise ValueError
            count = 0 if holiday else len(page.products)
        else:
            if source_status != transport or page is not None:
                raise ValueError
            count = 0
        if transport == "HTTP_OK":
            parse_status = source_status
        elif transport == "PARSER_ERROR":
            parse_status = "PARSER_ERROR"
        elif transport in {
            "CHILD_ERROR",
            "CHILD_TIMEOUT",
            "CHILD_CLEANUP_UNCERTAIN",
            "RESULT_TOO_LARGE",
        }:
            parse_status = "UNKNOWN"
        else:
            parse_status = "NOT_ATTEMPTED"
        return Attempt(number, transport, source_status, "UNVERIFIED", count, parse_status)
    except Exception:
        return Attempt(
            number,
            "COLLECTOR_PROTOCOL_ERROR",
            "COLLECTOR_PROTOCOL_ERROR",
            "UNVERIFIED",
            0,
            "UNKNOWN",
        )


def run_preview(
    *,
    config_file: str,
    calendar_root: str,
    supplement_file: str,
    budget_seconds: float,
    enable_shfe: bool = False,
    gateway_enabled: bool = False,
    direct_write_enabled: bool = False,
    max_retries: int = _MAX_RETRIES,
    retry_interval_seconds: float = _DEFAULT_INTERVAL,
    checker: Callable = _check_calendar_inputs_with_candidate,
    collector: Callable | None = None,
    holiday_collector: Callable | None = None,
    holiday_converter: Callable | None = None,
    merger: Callable | None = None,
    normalizer: Callable | None = None,
    monotonic: Callable = time.monotonic,
    wait: Callable = time.sleep,
    progress: Callable | None = None,
    candidate_id: str = "",
    candidate_writer: Callable | None = None,
) -> PreviewReport:
    """Run one preview with a bounded budget and injectable ports.

    The budget is measured from before the local check. Blocking in-process
    ports cannot be preempted by this function; the CLI's worker supervisor
    provides the owned-process deadline. Ports are trusted, not source proof.
    """
    if (
        not _valid_number(budget_seconds, 0.001, _MAX_BUDGET)
        or not _valid_number(retry_interval_seconds, 0, _MAX_BUDGET)
        or type(max_retries) is not int
        or not 0 <= max_retries <= _MAX_RETRIES
        or any(
            type(flag) is not bool for flag in (enable_shfe, gateway_enabled, direct_write_enabled)
        )
        or not all(callable(port) for port in (checker, monotonic, wait))
        or (collector is not None and not callable(collector))
        or (holiday_collector is not None and not callable(holiday_collector))
        or (holiday_converter is not None and not callable(holiday_converter))
        or (merger is not None and not callable(merger))
        or (normalizer is not None and not callable(normalizer))
        or (progress is not None and not callable(progress))
        or type(candidate_id) is not str
        or (
            candidate_id
            and (len(candidate_id) != 32 or any(c not in "0123456789abcdef" for c in candidate_id))
        )
        or bool(candidate_id) != (candidate_writer is not None)
        or (candidate_writer is not None and not callable(candidate_writer))
    ):
        return PreviewReport("ERROR", "INVALID_ARGUMENTS", 2)
    try:
        started = monotonic()
        if not _valid_number(started, -float("inf"), float("inf")):
            raise ValueError
        deadline = started + budget_seconds
    except Exception:
        return PreviewReport("ERROR", "CLOCK_UNAVAILABLE", 2)
    try:
        checked_result = checker(
            config_file=config_file,
            calendar_root=calendar_root,
            supplement_file=supplement_file,
            gateway_enabled=gateway_enabled,
            direct_write_enabled=direct_write_enabled,
        )
        if type(checked_result) is tuple and len(checked_result) == 2:
            checked, supplement_candidate = checked_result
        else:
            checked, supplement_candidate = checked_result, None
        if type(checked) is not CalendarCheckReport or checked.code not in _CHECK_CODES:
            raise ValueError
    except Exception:
        return PreviewReport(
            "ERROR",
            "INPUT_CHECK_FAILED",
            20,
            input_status="ERROR",
            input_code="CHECK_ERROR",
            candidate_id=candidate_id,
            candidate_status="NOT_ATTEMPTED" if candidate_id else "NOT_REQUESTED",
        )
    input_code = checked.code
    input_status = checked.status if checked.status in {"ERROR", "CHECKED"} else "ERROR"
    supplement = (
        checked.supplement_status
        if checked.supplement_status
        in {
            "NOT_CHECKED",
            "CANDIDATE",
            "MISSING",
            "INVALID_PATH",
            "SYMLINK",
            "NOT_REGULAR",
            "CHANGED",
            "TOO_LARGE",
            "INVALID_UTF8",
            "INVALID_CONTENT",
            "READ_ERROR",
            "PARSER_ERROR",
            "UNSUPPORTED",
            "DEPENDENCY_UNAVAILABLE",
        }
        else "UNKNOWN"
    )
    candidate_status = "NOT_ATTEMPTED" if candidate_id else "NOT_REQUESTED"
    candidate_reason = "NONE"
    base = {"input_status": input_status, "input_code": input_code, "supplement_status": supplement}
    if not (
        checked.exit_code == 0
        and input_status == "CHECKED"
        and input_code == "SUPPLEMENT_UNVERIFIED"
        and supplement == "CANDIDATE"
        and checked.source_verification == "UNVERIFIED"
    ):
        return PreviewReport(
            "ERROR",
            "INPUT_CHECK_FAILED",
            20,
            candidate_id=candidate_id,
            candidate_status=candidate_status,
            **base,
        )

    def emit(report: PreviewReport) -> None:
        if progress is not None:
            progress(report)

    def retain(*, merged, supplement, hours_page, source_checks) -> None:
        nonlocal candidate_status, candidate_reason
        candidate_status = "IN_PROGRESS"
        try:
            from bt_api_py.trading_calendar_candidate_inbox import CandidateRetention

            retained = candidate_writer(
                merged=merged,
                supplement=supplement,
                hours_page=hours_page,
                source_checks=source_checks,
            )
            if (
                type(retained) is not CandidateRetention
                or retained.candidate_id != candidate_id
                or retained.status not in {"COMMITTED", "NOT_COMMITTED", "COMMIT_UNKNOWN"}
                or retained.reason
                not in {
                    "NONE",
                    "INVALID_ID",
                    "UNSUPPORTED_FILESYSTEM",
                    "INVALID_OR_TOO_LARGE",
                    "INVALID_DIRECTORY",
                    "ID_EXISTS",
                    "IO_ERROR",
                    "CHILD_CLEANUP_UNCERTAIN",
                }
                or (retained.status == "COMMITTED" and retained.reason != "NONE")
            ):
                raise ValueError("invalid candidate retention result")
            candidate_status, candidate_reason = retained.status, retained.reason
        except Exception:
            candidate_status, candidate_reason = "COMMIT_UNKNOWN", "CHILD_CLEANUP_UNCERTAIN"
        except BaseException as exc:
            _mark_source_cleanup_uncertain(exc)
            raise

    try:
        emit(
            PreviewReport(
                "INCOMPLETE",
                "INPUT_CHECK_COMPLETE",
                10,
                source_status="NOT_ATTEMPTED" if enable_shfe else "NOT_REQUESTED",
                holiday_status="NOT_ATTEMPTED" if enable_shfe else "NOT_REQUESTED",
                candidate_id=candidate_id,
                candidate_status=candidate_status,
                **base,
            )
        )
    except Exception:
        return PreviewReport("ERROR", "PROGRESS_UNAVAILABLE", 22, **base)
    try:
        now = monotonic()
        if not _valid_number(now, started, float("inf")):
            raise ValueError
    except Exception:
        return PreviewReport("ERROR", "CLOCK_UNAVAILABLE", 2, **base)
    if now >= deadline:
        return PreviewReport(
            "INCOMPLETE",
            "BUDGET_EXHAUSTED",
            21,
            source_status="NOT_ATTEMPTED" if enable_shfe else "NOT_REQUESTED",
            holiday_status="NOT_ATTEMPTED" if enable_shfe else "NOT_REQUESTED",
            **base,
        )
    if not enable_shfe:
        if candidate_writer is not None and supplement_candidate is not None:
            absent = {"status": "NOT_REQUESTED", "reason": "NOT_REQUESTED", "attempts": []}
            retain(
                merged=None,
                supplement=supplement_candidate,
                hours_page=None,
                source_checks={
                    "hours": absent,
                    "holiday": absent,
                    "holiday_conversion_status": "NOT_ATTEMPTED",
                    "merge_status": "NOT_ATTEMPTED",
                    "merge_reason": "NONE",
                    "normalization_status": "NOT_ATTEMPTED",
                },
            )
        return PreviewReport(
            "INCOMPLETE",
            "SHFE_NOT_REQUESTED",
            10,
            candidate_id=candidate_id,
            candidate_status=candidate_status,
            candidate_reason=candidate_reason,
            **base,
        )

    # The original source_status/attempts remain the trading-hours fields.
    # Holiday fields are additive; each source has its own retry counter.
    sources = [
        (collector or _default_collector, False),
        (holiday_collector or _default_holiday_collector, True),
    ]
    histories: list[list[Attempt]] = [[], []]
    reasons = ["NOT_ATTEMPTED", "NOT_ATTEMPTED"]
    conversion_status = "NOT_ATTEMPTED"
    closed_count = None
    night_count = None
    merge_status = "NOT_ATTEMPTED"
    merge_reason = "NONE"
    merge_counts = None
    normalization_status = "NOT_ATTEMPTED"
    hours_page = None

    def report(status: str, reason: str, exit_code: int) -> PreviewReport:
        return PreviewReport(
            status,
            reason,
            exit_code,
            source_status=histories[0][-1].source_status if histories[0] else "NOT_ATTEMPTED",
            attempts=tuple(histories[0]),
            source_reason=reasons[0],
            holiday_status=histories[1][-1].source_status if histories[1] else "NOT_ATTEMPTED",
            holiday_attempts=tuple(histories[1]),
            holiday_reason=reasons[1],
            holiday_conversion_status=conversion_status,
            holiday_closed_count=closed_count,
            holiday_unresolved_night_count=night_count,
            merge_status=merge_status,
            merge_reason=merge_reason,
            merge_source_count=merge_counts[0] if merge_counts is not None else None,
            merge_day_count=merge_counts[1] if merge_counts is not None else None,
            merge_hour_count=merge_counts[2] if merge_counts is not None else None,
            merge_exception_count=merge_counts[3] if merge_counts is not None else None,
            normalization_status=normalization_status,
            candidate_id=candidate_id,
            candidate_status=candidate_status,
            candidate_reason=candidate_reason,
            **base,
        )

    def source_checks():
        return {
            "hours": {
                "status": histories[0][-1].source_status if histories[0] else "NOT_ATTEMPTED",
                "reason": reasons[0],
                "attempts": [asdict(item) for item in histories[0]],
            },
            "holiday": {
                "status": histories[1][-1].source_status if histories[1] else "NOT_ATTEMPTED",
                "reason": reasons[1],
                "attempts": [asdict(item) for item in histories[1]],
            },
            "holiday_conversion_status": conversion_status,
            "merge_status": merge_status,
            "merge_reason": merge_reason,
            "normalization_status": normalization_status,
        }

    for index, (collect, is_holiday) in enumerate(sources):
        attempts = histories[index]
        while True:
            remaining = deadline - now
            allocation = min(_PER_ATTEMPT, remaining - _CLEANUP_ALLOWANCE)
            if allocation <= 0:
                reasons[index] = "BUDGET_EXHAUSTED"
                return report("INCOMPLETE", "BUDGET_EXHAUSTED", 21)
            pending = Attempt(
                len(attempts) + 1, "IN_PROGRESS", "IN_PROGRESS", "UNVERIFIED", 0, "IN_PROGRESS"
            )
            attempts.append(pending)
            try:
                emit(report("INCOMPLETE", "IN_PROGRESS", 10))
            except Exception:
                attempts.pop()
                return report("ERROR", "PROGRESS_UNAVAILABLE", 22)
            resolved = collect
            try:
                if collect is _default_collector:
                    from bt_api_ctp.calendar_shfe_fetch import collect_shfe_page as resolved
                elif collect is _default_holiday_collector:
                    from bt_api_ctp.calendar_shfe_fetch import collect_shfe_holiday as resolved
            except Exception:
                resolved = None
            if resolved is None:
                attempt = Attempt(
                    len(attempts), "CTP_UNAVAILABLE", "CTP_UNAVAILABLE", "UNVERIFIED", 0, "UNKNOWN"
                )
            else:
                try:
                    result = resolved(
                        connect_timeout=min(3.0, allocation),
                        read_timeout=min(3.0, allocation),
                        total_timeout=allocation,
                    )
                    attempt = _attempt(result, len(attempts), holiday=is_holiday)
                    if attempt.transport_status == "COLLECTOR_PROTOCOL_ERROR":
                        raise ValueError("collector protocol unknown")
                except Exception:
                    attempt = Attempt(
                        len(attempts),
                        "CHILD_CLEANUP_UNCERTAIN",
                        "CHILD_CLEANUP_UNCERTAIN",
                        "UNVERIFIED",
                        0,
                        "UNKNOWN",
                    )
                except BaseException as exc:
                    attempts[-1] = Attempt(
                        len(attempts),
                        "CHILD_CLEANUP_UNCERTAIN",
                        "CHILD_CLEANUP_UNCERTAIN",
                        "UNVERIFIED",
                        0,
                        "UNKNOWN",
                    )
                    reasons[index] = "CHILD_CLEANUP_UNCERTAIN"
                    with contextlib.suppress(BaseException):
                        emit(report("INCOMPLETE", "CHILD_CLEANUP_UNCERTAIN", 23))
                    _mark_source_cleanup_uncertain(exc)
                    raise
            attempts[-1] = attempt
            if attempt.transport_status == "CHILD_CLEANUP_UNCERTAIN":
                # No later progress or clock failure may hide uncertain cleanup.
                reasons[index] = "CHILD_CLEANUP_UNCERTAIN"
                return report("INCOMPLETE", "CHILD_CLEANUP_UNCERTAIN", 23)
            if (
                not is_holiday
                and attempt.transport_status == "HTTP_OK"
                and attempt.source_status in {"CANDIDATE", "NEEDS_REVIEW"}
            ):
                hours_page = result.page
            try:
                emit(report("INCOMPLETE", "IN_PROGRESS", 10))
            except Exception:
                return report("ERROR", "PROGRESS_UNAVAILABLE", 22)
            try:
                finished = monotonic()
                if not _valid_number(finished, now, float("inf")):
                    raise ValueError
            except Exception:
                return report("ERROR", "CLOCK_UNAVAILABLE", 2)
            now = finished
            if now >= deadline:
                reasons[index] = "BUDGET_EXHAUSTED"
                return report("INCOMPLETE", "BUDGET_EXHAUSTED", 21)
            if attempt.transport_status not in {"NETWORK_TIMEOUT", "CHILD_TIMEOUT"}:
                reasons[index] = (
                    "CANDIDATE" if attempt.source_status == "CANDIDATE" else "SHFE_NOT_VERIFIED"
                )
                if (
                    is_holiday
                    and attempt.transport_status == "HTTP_OK"
                    and attempt.source_status == "CANDIDATE"
                ):
                    conversion_status = "IN_PROGRESS"
                    try:
                        emit(report("INCOMPLETE", "IN_PROGRESS", 10))
                    except Exception:
                        return report("ERROR", "PROGRESS_UNAVAILABLE", 22)
                    try:
                        conversion = (holiday_converter or _default_holiday_converter)(result.page)
                        conversion_status, closed_count, night_count = _conversion_summary(
                            conversion
                        )
                    except ImportError:
                        conversion_status = "CONVERTER_UNAVAILABLE"
                    except Exception:
                        conversion_status = "CONVERSION_ERROR"
                    try:
                        emit(report("INCOMPLETE", "IN_PROGRESS", 10))
                    except Exception:
                        return report("ERROR", "PROGRESS_UNAVAILABLE", 22)
                    try:
                        finished = monotonic()
                        if not _valid_number(finished, now, float("inf")):
                            raise ValueError
                    except Exception:
                        return report("ERROR", "CLOCK_UNAVAILABLE", 2)
                    now = finished
                    if now >= deadline:
                        return report("INCOMPLETE", "BUDGET_EXHAUSTED", 21)
                    if conversion_status != "CANDIDATE":
                        reasons[index] = "SHFE_NOT_VERIFIED"
                    elif supplement_candidate is None:
                        merge_status = "INPUT_CANDIDATE_UNAVAILABLE"
                    else:
                        merge_status = "IN_PROGRESS"
                        try:
                            emit(report("INCOMPLETE", "IN_PROGRESS", 10))
                        except Exception:
                            return report("ERROR", "PROGRESS_UNAVAILABLE", 22)
                        try:
                            merged = (merger or _default_merge)(
                                supplement=supplement_candidate,
                                holiday=conversion,
                                supplement_coverage="NOT_PROVEN",
                                supplement_publication="NOT_AUTHORIZED",
                            )
                            merge_status, merge_reason, merge_counts = _merge_summary(merged)
                        except ImportError:
                            merge_status = "MERGE_UNAVAILABLE"
                        except Exception:
                            merge_status = "MERGE_ERROR"
                        try:
                            emit(report("INCOMPLETE", "IN_PROGRESS", 10))
                        except Exception:
                            return report("ERROR", "PROGRESS_UNAVAILABLE", 22)
                        try:
                            finished = monotonic()
                            if not _valid_number(finished, now, float("inf")):
                                raise ValueError
                        except Exception:
                            return report("ERROR", "CLOCK_UNAVAILABLE", 2)
                        now = finished
                        if now >= deadline:
                            return report("INCOMPLETE", "BUDGET_EXHAUSTED", 21)
                        if merge_status == "PARTIAL":
                            normalization_status = "IN_PROGRESS"
                            try:
                                emit(report("INCOMPLETE", "IN_PROGRESS", 10))
                            except Exception:
                                return report("ERROR", "PROGRESS_UNAVAILABLE", 22)
                            try:
                                normalized = (normalizer or _default_normalizer)(merged.supplement)
                                normalization_status = _normalization_summary(normalized)
                            except ImportError:
                                normalization_status = "NORMALIZER_UNAVAILABLE"
                            except Exception:
                                normalization_status = "NORMALIZATION_ERROR"
                            try:
                                emit(report("INCOMPLETE", "IN_PROGRESS", 10))
                            except Exception:
                                return report("ERROR", "PROGRESS_UNAVAILABLE", 22)
                            try:
                                finished = monotonic()
                                if not _valid_number(finished, now, float("inf")):
                                    raise ValueError
                            except Exception:
                                return report("ERROR", "CLOCK_UNAVAILABLE", 2)
                            now = finished
                            if now >= deadline:
                                return report("INCOMPLETE", "BUDGET_EXHAUSTED", 21)
                            if (
                                candidate_writer is not None
                                and normalization_status == "NORMALIZED"
                            ):
                                candidate_status = "IN_PROGRESS"
                                try:
                                    emit(report("INCOMPLETE", "IN_PROGRESS", 10))
                                except Exception:
                                    candidate_status = "NOT_ATTEMPTED"
                                    return report("ERROR", "PROGRESS_UNAVAILABLE", 22)
                                retain(
                                    merged=merged,
                                    supplement=None,
                                    hours_page=hours_page,
                                    source_checks=source_checks(),
                                )
                                try:
                                    emit(report("INCOMPLETE", "IN_PROGRESS", 10))
                                except Exception:
                                    return report("ERROR", "PROGRESS_UNAVAILABLE", 22)
                break
            if len(attempts) > max_retries:
                reasons[index] = "RETRY_LIMIT_REACHED"
                break
            if now + retry_interval_seconds + _CLEANUP_ALLOWANCE >= deadline:
                reasons[index] = "RETRY_BUDGET_EXHAUSTED"
                break
            wake = now + retry_interval_seconds
            while now < wake:
                try:
                    wait(min(30.0, wake - now))
                    current = monotonic()
                    if not _valid_number(current, now, float("inf")) or current <= now:
                        raise ValueError
                    now = current
                except Exception:
                    return report("ERROR", "CLOCK_UNAVAILABLE", 2)
                if now >= deadline:
                    reasons[index] = "BUDGET_EXHAUSTED"
                    return report("INCOMPLETE", "BUDGET_EXHAUSTED", 21)
    if (
        candidate_writer is not None
        and candidate_status == "NOT_ATTEMPTED"
        and merge_status
        in {"NOT_ATTEMPTED", "MERGE_ERROR", "MERGE_UNAVAILABLE", "MERGE_PROTOCOL_ERROR"}
        and conversion_status
        in {
            "NOT_ATTEMPTED",
            "INVALID_CANDIDATE",
            "CONVERTER_UNAVAILABLE",
            "CONVERSION_ERROR",
            "CONVERSION_PROTOCOL_ERROR",
            "CANDIDATE",
        }
        and supplement_candidate is not None
    ):
        retain(
            merged=None,
            supplement=supplement_candidate,
            hours_page=hours_page,
            source_checks=source_checks(),
        )
    if merge_status == "CONFLICT":
        return report("INCOMPLETE", "MERGE_CONFLICT", 10)
    if merge_status in {
        "INPUT_CANDIDATE_UNAVAILABLE",
        "INVALID_INPUT",
        "MERGE_UNAVAILABLE",
        "MERGE_ERROR",
        "MERGE_PROTOCOL_ERROR",
    }:
        return report("ERROR", "MERGE_FAILED", 22)
    if normalization_status in {
        "INVALID_INPUT",
        "NORMALIZER_UNAVAILABLE",
        "NORMALIZATION_ERROR",
        "NORMALIZATION_PROTOCOL_ERROR",
    }:
        return report("ERROR", "NORMALIZATION_FAILED", 22)
    if "RETRY_BUDGET_EXHAUSTED" in reasons:
        return report("INCOMPLETE", "RETRY_BUDGET_EXHAUSTED", 21)
    if "RETRY_LIMIT_REACHED" in reasons:
        return report("INCOMPLETE", "RETRY_LIMIT_REACHED", 10)
    if reasons != ["CANDIDATE", "CANDIDATE"]:
        return report("INCOMPLETE", "SHFE_NOT_VERIFIED", 10)
    return report("INCOMPLETE", "OTHER_VENUES_NOT_IMPLEMENTED", 10)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError


def _retain_report(report: PreviewReport, directory: str) -> dict[str, str]:
    """Canonicalize module identity, then return only bounded retention metadata."""
    try:
        # `python -m` executes this module as __main__; the store imports its
        # canonical name and intentionally rejects foreign class identities.
        from bt_api_py import trading_calendar_preview as canonical
        from bt_api_py.trading_calendar_preview_reports import (
            TRUSTED_LOCAL_POSIX,
            PreviewRetentionResult,
            retain_preview_report,
        )

        data = report.to_dict()
        for key in ("attempts", "holiday_attempts"):
            data[key] = tuple(canonical.Attempt(**item) for item in data[key])
        detached = canonical.PreviewReport(**data)
    except Exception:
        return {
            "status": "NOT_COMMITTED",
            "visibility": "NOT_OBSERVED",
            "run_id": "",
            "reason": "DEPENDENCY_OR_REPORT_ERROR",
        }
    try:
        result = retain_preview_report(
            detached, directory=directory, filesystem=TRUSTED_LOCAL_POSIX
        )
        if type(result) is not PreviewRetentionResult:
            raise ValueError
        if type(result.run_id) is not str or (
            result.run_id
            and (
                len(result.run_id) != 32 or any(c not in "0123456789abcdef" for c in result.run_id)
            )
        ):
            raise ValueError
        if (result.durability, result.verification, result.publication) != (
            "UNCONFIRMED",
            "UNVERIFIED",
            "NOT_AUTHORIZED",
        ):
            raise ValueError
        valid = (
            (
                result.status == "COMMITTED"
                and result.visibility == "VISIBLE"
                and result.reason == ""
                and bool(result.run_id)
            )
            or (
                result.status == "NOT_COMMITTED"
                and result.visibility == "NOT_OBSERVED"
                and result.reason
                in {
                    "UNSUPPORTED_FILESYSTEM",
                    "INVALID_REPORT",
                    "INVALID_DIRECTORY",
                    "RANDOM_UNAVAILABLE",
                    "RUN_ID_EXISTS",
                    "IO_OR_STATE_ERROR",
                }
            )
            or (
                result.status == "COMMIT_UNKNOWN"
                and result.visibility in {"VISIBLE", "NOT_OBSERVED"}
                and result.reason == "IO_OR_STATE_ERROR"
                and bool(result.run_id)
            )
        )
        if not valid:
            raise ValueError
        return {
            "status": result.status,
            "visibility": result.visibility,
            "run_id": result.run_id,
            "reason": result.reason,
        }
    except Exception:
        # Once the port has been invoked, neither an exception nor malformed
        # response proves whether its write became visible.
        return {
            "status": "COMMIT_UNKNOWN",
            "visibility": "NOT_OBSERVED",
            "run_id": "",
            "reason": "RETENTION_OUTCOME_UNKNOWN",
        }


def _supervise_core(args: dict[str, Any]) -> PreviewReport:
    # SIGCHLD dispositions that auto-reap or invoke another handler cannot
    # preserve the Popen child's identity for safe group cleanup.
    import contextlib
    import threading

    if (
        os.name != "posix"
        or not hasattr(os, "killpg")
        or not hasattr(signal, "SIGCHLD")
        or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
    ):
        return PreviewReport("ERROR", "SUPERVISOR_UNSUPPORTED", 22)
    interrupted = [False]
    original_sigint = signal.getsignal(signal.SIGINT)
    capture_enabled = (
        original_sigint == signal.default_int_handler
        and threading.current_thread() is threading.main_thread()
        and threading.active_count() == 1
    )
    if not capture_enabled:
        # Refuse to start a child when safe group supervision is unavailable.
        return PreviewReport("ERROR", "SUPERVISOR_UNSUPPORTED", 22)

    def record_interrupt(signum, frame):
        interrupted[0] = True

    def safe_to_signal(child):
        return (
            capture_enabled
            and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL
            and signal.getsignal(signal.SIGINT) is record_interrupt
            and threading.current_thread() is threading.main_thread()
            and threading.active_count() == 1
            and child.returncode is None
        )

    def stop_for_interrupt(child):
        # Only called before communicate or after its TimeoutExpired, never
        # after it returned. The group is ours and the child is not reaped by
        # this supervisor. Native reapers/SA_NOCLDWAIT remain unsupported.
        if safe_to_signal(child):
            with contextlib.suppress(Exception):
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=1.0)
        raise KeyboardInterrupt

    handler_changed = False
    try:
        if capture_enabled:
            signal.signal(signal.SIGINT, record_interrupt)

        def execute():
            nonlocal args
            started = time.monotonic()
            deadline = started + args["budget_seconds"]
            args = dict(args)
            args["supervisor_deadline"] = deadline
            if "product_source" in args:
                args["product_deadline"] = deadline
            command = [sys.executable, "-m", "bt_api_py.trading_calendar_preview", "--_worker"]
            try:
                child = subprocess.Popen(  # noqa: S603 - fixed interpreter/module; data is on stdin
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    shell=False,
                )
            except Exception:
                return PreviewReport("ERROR", "WORKER_START_FAILED", 22)
            if interrupted[0]:
                # Popen returned a handle, and communicate has not reaped it.
                stop_for_interrupt(child)
            payload = json.dumps(args, ensure_ascii=True).encode("ascii")
            supplied = False
            while True:
                if interrupted[0]:
                    stop_for_interrupt(child)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    output, _ = child.communicate(
                        input=payload if not supplied else None,
                        timeout=min(
                            0.2 if capture_enabled else 0.25 if "product_source" in args else 30.0,
                            remaining,
                        ),
                    )
                    if interrupted[0]:
                        # communicate returned and may have reaped the child: never signal.
                        raise KeyboardInterrupt
                    if (
                        child.returncode not in (0, 2, 10, 20, 21, 22, 23)
                        or len(output) > _MAX_JSON
                    ):
                        return PreviewReport("ERROR", "WORKER_PROTOCOL_ERROR", 22)
                    lines = output.splitlines()
                    return _parse_worker_result(lines[-1] if lines else b"", child.returncode)
                except subprocess.TimeoutExpired as exc:
                    supplied = True
                    if interrupted[0]:
                        stop_for_interrupt(child)
                    if exc.output and len(exc.output) <= _MAX_JSON:
                        for line in reversed(exc.output.splitlines()):
                            try:
                                code = json.loads(line).get("exit_code")
                                progress = _parse_worker_result(line, code)
                            except Exception:
                                progress = None
                            if progress is not None and (
                                progress.candidate_reason == "CHILD_CLEANUP_UNCERTAIN"
                                or progress.reason == "CHILD_CLEANUP_UNCERTAIN"
                            ):
                                try:
                                    # Worker is alive, awaiting the supervisor's cleanup.
                                    if not safe_to_signal(child):
                                        raise OSError("unsafe process supervision")
                                    os.killpg(child.pid, signal.SIGKILL)
                                    child.communicate(timeout=1.0)
                                except Exception:
                                    return replace(
                                        progress,
                                        status="ERROR",
                                        reason="WORKER_CLEANUP_UNCERTAIN",
                                        exit_code=23,
                                    )
                                return replace(
                                    progress,
                                    status="ERROR",
                                    reason="WORKER_CLEANUP_UNCERTAIN",
                                    exit_code=23,
                                )
                except Exception:
                    # communicate may have reaped the leader before failing;
                    # its PID is no longer a safe process-group handle.
                    return PreviewReport("ERROR", "WORKER_CLEANUP_UNCERTAIN", 23)
            try:
                # The new session contains only this worker and its descendants.
                if not safe_to_signal(child):
                    raise OSError("unsafe process supervision")
                os.killpg(child.pid, signal.SIGKILL)
                output, _ = child.communicate(timeout=1.0)
            except ProcessLookupError:
                try:
                    output, _ = child.communicate(timeout=1.0)
                except Exception:
                    return PreviewReport("ERROR", "WORKER_CLEANUP_UNCERTAIN", 23)
            except Exception:
                return PreviewReport("ERROR", "WORKER_CLEANUP_UNCERTAIN", 23)
            latest = None
            if len(output) <= _MAX_JSON:
                for line in reversed(output.splitlines()):
                    candidate = _parse_worker_result(line, 10)
                    if candidate.reason in {"INPUT_CHECK_COMPLETE", "IN_PROGRESS"}:
                        latest = candidate
                        break
            if latest is None:
                latest = PreviewReport("INCOMPLETE", "BUDGET_EXHAUSTED", 21)
            for field, status_field in (
                ("attempts", "source_status"),
                ("holiday_attempts", "holiday_status"),
            ):
                history = getattr(latest, field)
                if history and history[-1].transport_status == "IN_PROGRESS":
                    interrupted_attempt = replace(
                        history[-1],
                        transport_status="INTERRUPTED",
                        source_status="INTERRUPTED",
                        parse_status="INTERRUPTED",
                    )
                    latest = replace(
                        latest,
                        **{
                            status_field: "INTERRUPTED",
                            field: (*history[:-1], interrupted_attempt),
                        },
                    )
                    # Reaping the worker cannot prove its collector descendant exited.
                    return replace(
                        latest, status="ERROR", reason="WORKER_CLEANUP_UNCERTAIN", exit_code=23
                    )
            if latest.holiday_conversion_status == "IN_PROGRESS":
                latest = replace(latest, holiday_conversion_status="INTERRUPTED")
            if latest.merge_status == "IN_PROGRESS":
                latest = replace(latest, merge_status="INTERRUPTED")
            if latest.normalization_status == "IN_PROGRESS":
                latest = replace(latest, normalization_status="INTERRUPTED")
            if (
                "product_source" in args
                or "candidate_id" in args
                or args.get("enable_shfe") is True
            ):
                return replace(
                    latest, status="ERROR", reason="WORKER_CLEANUP_UNCERTAIN", exit_code=23
                )
            return replace(latest, status="INCOMPLETE", reason="BUDGET_EXHAUSTED", exit_code=21)

        result = execute()
    except BaseException as exc:
        # Preserve the original interruption while exposing the cleanup limit.
        with contextlib.suppress(Exception):
            exc.worker_cleanup = "WORKER_CLEANUP_UNCERTAIN"
            if hasattr(exc, "add_note"):
                exc.add_note("WORKER_CLEANUP_UNCERTAIN")
        raise
    finally:
        if capture_enabled and signal.getsignal(signal.SIGINT) is record_interrupt:
            signal.signal(signal.SIGINT, original_sigint)
        else:
            handler_changed = True
    if interrupted[0]:
        exc = KeyboardInterrupt()
        exc.worker_cleanup = "WORKER_CLEANUP_UNCERTAIN"
        if hasattr(exc, "add_note"):
            exc.add_note("WORKER_CLEANUP_UNCERTAIN")
        raise exc
    if handler_changed:
        return replace(
            result,
            status="ERROR",
            reason="WORKER_CLEANUP_UNCERTAIN",
            exit_code=23,
            candidate_status="COMMIT_UNKNOWN" if result.candidate_id else result.candidate_status,
            candidate_reason="IO_ERROR" if result.candidate_id else result.candidate_reason,
        )
    return result


def _supervise(args: dict[str, Any]) -> PreviewReport:
    try:
        return _supervise_core(args)
    except BaseException as exc:
        # This also covers an interruption while restoring the host handler.
        import contextlib

        with contextlib.suppress(Exception):
            exc.worker_cleanup = "WORKER_CLEANUP_UNCERTAIN"
            if hasattr(exc, "add_note"):
                exc.add_note("WORKER_CLEANUP_UNCERTAIN")
        raise


def _parse_worker_result(output: bytes, returncode: int) -> PreviewReport:
    """Accept only finite expected fields, never echo child data on failure."""
    try:
        data = json.loads(output)
        if type(data) is not dict or set(data) != set(PreviewReport.__dataclass_fields__):
            raise ValueError
        if data["status"] not in {"ERROR", "INCOMPLETE"} or data["exit_code"] != returncode:
            raise ValueError
        if data["reason"] not in {
            "INVALID_ARGUMENTS",
            "CLOCK_UNAVAILABLE",
            "INPUT_CHECK_FAILED",
            "BUDGET_EXHAUSTED",
            "SHFE_NOT_REQUESTED",
            "CHILD_CLEANUP_UNCERTAIN",
            "OTHER_VENUES_NOT_IMPLEMENTED",
            "SHFE_NOT_VERIFIED",
            "RETRY_LIMIT_REACHED",
            "RETRY_BUDGET_EXHAUSTED",
            "INPUT_CHECK_COMPLETE",
            "IN_PROGRESS",
            "PROGRESS_UNAVAILABLE",
            "MERGE_CONFLICT",
            "MERGE_FAILED",
            "NORMALIZATION_FAILED",
        }:
            raise ValueError
        if data["input_status"] not in {"NOT_CHECKED", "ERROR", "CHECKED"}:
            raise ValueError
        if data["input_code"] not in _CHECK_CODES | {"NOT_CHECKED", "CHECK_ERROR"}:
            raise ValueError
        if data["supplement_status"] not in {
            "NOT_CHECKED",
            "CANDIDATE",
            "MISSING",
            "INVALID_PATH",
            "SYMLINK",
            "NOT_REGULAR",
            "CHANGED",
            "TOO_LARGE",
            "INVALID_UTF8",
            "INVALID_CONTENT",
            "READ_ERROR",
            "PARSER_ERROR",
            "UNSUPPORTED",
            "DEPENDENCY_UNAVAILABLE",
            "UNKNOWN",
        }:
            raise ValueError
        allowed_sources = (
            _TRANSPORT
            | _PAGE
            | {
                "NOT_REQUESTED",
                "NOT_ATTEMPTED",
                "CTP_UNAVAILABLE",
                "COLLECTOR_ERROR",
                "COLLECTOR_PROTOCOL_ERROR",
                "IN_PROGRESS",
                "INTERRUPTED",
            }
        )
        if (
            data["source_status"] not in allowed_sources
            or data["holiday_status"] not in allowed_sources
        ):
            raise ValueError
        if (
            data["other_five_venues"],
            data["completeness"],
            data["verification"],
            data["publication"],
            data["maturity"],
            data["holiday_coverage"],
        ) != (
            "NOT_IMPLEMENTED",
            "INCOMPLETE",
            "UNVERIFIED",
            "NOT_ATTEMPTED",
            "EXPERIMENTAL_PREVIEW",
            "NOT_PROVEN",
        ):
            raise ValueError
        candidate_id = data["candidate_id"]
        if type(candidate_id) is not str or (
            candidate_id
            and (len(candidate_id) != 32 or any(c not in "0123456789abcdef" for c in candidate_id))
        ):
            raise ValueError
        if data["candidate_status"] not in {
            "NOT_REQUESTED",
            "NOT_ATTEMPTED",
            "IN_PROGRESS",
            "COMMITTED",
            "NOT_COMMITTED",
            "COMMIT_UNKNOWN",
        } or data["candidate_reason"] not in {
            "NONE",
            "INVALID_ID",
            "UNSUPPORTED_FILESYSTEM",
            "INVALID_OR_TOO_LARGE",
            "INVALID_DIRECTORY",
            "ID_EXISTS",
            "IO_ERROR",
            "CHILD_CLEANUP_UNCERTAIN",
        }:
            raise ValueError
        if (not candidate_id) != (data["candidate_status"] == "NOT_REQUESTED"):
            raise ValueError
        if (
            data["candidate_status"]
            in {"NOT_REQUESTED", "NOT_ATTEMPTED", "IN_PROGRESS", "COMMITTED"}
            and data["candidate_reason"] != "NONE"
        ):
            raise ValueError
        allowed_reasons = {
            "NOT_REQUESTED",
            "NOT_ATTEMPTED",
            "CANDIDATE",
            "SHFE_NOT_VERIFIED",
            "RETRY_LIMIT_REACHED",
            "RETRY_BUDGET_EXHAUSTED",
            "BUDGET_EXHAUSTED",
            "CHILD_CLEANUP_UNCERTAIN",
        }
        if (
            data["source_reason"] not in allowed_reasons
            or data["holiday_reason"] not in allowed_reasons
        ):
            raise ValueError
        conversion_status = data["holiday_conversion_status"]
        if conversion_status not in {
            "NOT_ATTEMPTED",
            "IN_PROGRESS",
            "CANDIDATE",
            "INVALID_CANDIDATE",
            "CONVERTER_UNAVAILABLE",
            "CONVERSION_ERROR",
            "CONVERSION_PROTOCOL_ERROR",
            "INTERRUPTED",
        }:
            raise ValueError
        closed, nights = data["holiday_closed_count"], data["holiday_unresolved_night_count"]
        if conversion_status == "CANDIDATE":
            if (
                type(closed) is not int
                or not 1 <= closed <= 366
                or type(nights) is not int
                or not 1 <= nights <= 32
                or data["holiday_status"] != "CANDIDATE"
            ):
                raise ValueError
        elif closed is not None or nights is not None:
            raise ValueError
        if (
            data["merge_hour_conflicts"],
            data["merge_exception_conflicts"],
            data["merge_cross_layer_conflicts"],
        ) != ("NOT_CHECKED", "NOT_CHECKED", "NOT_CHECKED"):
            raise ValueError
        if data["merge_status"] not in {
            "NOT_ATTEMPTED",
            "IN_PROGRESS",
            "PARTIAL",
            "CONFLICT",
            "INVALID_INPUT",
            "MERGE_ERROR",
            "MERGE_UNAVAILABLE",
            "MERGE_PROTOCOL_ERROR",
            "INPUT_CANDIDATE_UNAVAILABLE",
            "INTERRUPTED",
        }:
            raise ValueError
        merge_counts = tuple(
            data[key]
            for key in (
                "merge_source_count",
                "merge_day_count",
                "merge_hour_count",
                "merge_exception_count",
            )
        )
        if data["merge_status"] == "PARTIAL":
            if any(type(count) is not int or not 0 <= count <= 1000 for count in merge_counts):
                raise ValueError
        elif any(count is not None for count in merge_counts):
            raise ValueError
        if (
            (
                data["merge_status"] == "CONFLICT"
                and data["merge_reason"] not in {"SOURCE_CONFLICT", "TRADING_DAY_CONFLICT"}
            )
            or (
                data["merge_status"] == "INVALID_INPUT"
                and data["merge_reason"] != "INVALID_OR_UNSUPPORTED_INPUT"
            )
            or (
                data["merge_status"] not in {"CONFLICT", "INVALID_INPUT"}
                and data["merge_reason"] != "NONE"
            )
        ):
            raise ValueError
        if data["merge_status"] != "NOT_ATTEMPTED" and conversion_status != "CANDIDATE":
            raise ValueError
        if (
            data["normalization_scope"],
            data["normalization_nights"],
            data["substantive_change"],
            data["source_page_changes"],
        ) != (
            "FOUR_LAYER_STRUCTURE_ONLY",
            "UNRESOLVED_NIGHTS_NOT_INCLUDED",
            "NOT_ASSESSED",
            "NOT_CHECKED",
        ):
            raise ValueError
        if data["normalization_status"] not in {
            "NOT_ATTEMPTED",
            "IN_PROGRESS",
            "NORMALIZED",
            "INVALID_INPUT",
            "NORMALIZER_UNAVAILABLE",
            "NORMALIZATION_ERROR",
            "NORMALIZATION_PROTOCOL_ERROR",
            "INTERRUPTED",
        }:
            raise ValueError
        if data["normalization_status"] != "NOT_ATTEMPTED" and data["merge_status"] != "PARTIAL":
            raise ValueError
        if (
            data["reason"] == "OTHER_VENUES_NOT_IMPLEMENTED"
            and data["normalization_status"] != "NORMALIZED"
        ):
            raise ValueError
        if data["reason"] == "NORMALIZATION_FAILED" and data["normalization_status"] not in {
            "INVALID_INPUT",
            "NORMALIZER_UNAVAILABLE",
            "NORMALIZATION_ERROR",
            "NORMALIZATION_PROTOCOL_ERROR",
        }:
            raise ValueError
        for field in ("attempts", "holiday_attempts"):
            if type(data[field]) is not list or len(data[field]) > 3:
                raise ValueError
            attempts = []
            for index, item in enumerate(data[field], 1):
                if type(item) is not dict or set(item) != set(Attempt.__dataclass_fields__):
                    raise ValueError
                if (
                    type(item["number"]) is not int
                    or item["number"] != index
                    or item["transport_status"] not in allowed_sources
                    or item["source_status"] not in allowed_sources
                    or item["verification"] != "UNVERIFIED"
                    or item["parse_status"]
                    not in _PAGE
                    | {"NOT_ATTEMPTED", "IN_PROGRESS", "INTERRUPTED", "PARSER_ERROR", "UNKNOWN"}
                    or type(item["product_count"]) is not int
                    or not 0 <= item["product_count"] <= 1024
                ):
                    raise ValueError
                attempts.append(Attempt(**item))
            data[field] = tuple(attempts)
        if conversion_status != "NOT_ATTEMPTED" and (
            data["holiday_status"] != "CANDIDATE"
            or not data["holiday_attempts"]
            or data["holiday_attempts"][-1].transport_status != "HTTP_OK"
            or data["holiday_attempts"][-1].source_status != "CANDIDATE"
        ):
            raise ValueError
        return PreviewReport(**data)
    except Exception:
        return PreviewReport("ERROR", "WORKER_PROTOCOL_ERROR", 22)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv == ["--_worker"]:
        supervisor_deadline = None
        product_deadline = None
        candidate_id = ""
        latest_progress = [None]
        pending_cleanup = [None]
        work_started = False

        def hold_for_cleanup(report):
            # Give the parent time beyond its deadline while this leader is held.
            with contextlib.suppress(Exception):
                print(json.dumps(report.to_dict(), ensure_ascii=True, sort_keys=True), flush=True)
            deadline = supervisor_deadline if supervisor_deadline is not None else product_deadline
            until = max(time.monotonic() + 1.0, deadline + 1.0 if deadline is not None else 0)
            while time.monotonic() < until:
                time.sleep(max(0.0, min(0.25, until - time.monotonic())))

        try:
            raw = sys.stdin.buffer.read(32769)
            if len(raw) > 32768:
                raise ValueError
            args = json.loads(raw)
            if type(args) is not dict:
                raise ValueError
            supervisor_deadline = args.pop("supervisor_deadline", None)
            if supervisor_deadline is not None and (
                type(supervisor_deadline) not in (int, float)
                or not math.isfinite(supervisor_deadline)
                or not _valid_number(args.get("budget_seconds"), 0.001, _MAX_BUDGET)
                or supervisor_deadline > time.monotonic() + args["budget_seconds"] + 1
            ):
                raise ValueError("invalid supervisor deadline")
            required = {
                "config_file",
                "calendar_root",
                "supplement_file",
                "budget_seconds",
                "enable_shfe",
                "gateway_enabled",
                "direct_write_enabled",
                "max_retries",
                "retry_interval_seconds",
            }
            candidate_keys = {"candidate_id", "trusted_local_posix"}
            catalog_keys = {"catalog_cache_dir", "catalog_scope", "catalog_trading_day"}
            product_keys = {"product_page_url", "product_page_file"}
            navigation_keys = {"discover_product_navigation"}
            product_source_keys = {"product_source", "product_deadline"}
            if type(args) is not dict or set(args) not in (
                required,
                required | candidate_keys,
                required | candidate_keys | catalog_keys,
                required | candidate_keys | product_keys,
                required | candidate_keys | catalog_keys | product_keys,
                required | candidate_keys | product_keys | navigation_keys,
                required | candidate_keys | catalog_keys | product_keys | navigation_keys,
                required | candidate_keys | product_source_keys,
                required | candidate_keys | catalog_keys | product_source_keys,
            ):
                raise ValueError
            catalog_options = None
            if catalog_keys <= set(args):
                catalog_options = {key: args.pop(key) for key in catalog_keys}
                if any(type(value) is not str or not value for value in catalog_options.values()):
                    raise ValueError("invalid catalog settings")
            product_options = None
            if product_keys <= set(args):
                product_options = {key: args.pop(key) for key in product_keys}
                if any(type(value) is not str or not value for value in product_options.values()):
                    raise ValueError("invalid product settings")
            navigation_requested = args.pop("discover_product_navigation", False)
            if type(navigation_requested) is not bool or (
                navigation_requested and product_options is None
            ):
                raise ValueError("navigation needs explicit local product")
            product_source = args.pop("product_source", None)
            product_deadline = args.pop("product_deadline", None)
            if (product_source is None) != (product_deadline is None):
                raise ValueError("invalid product source")
            if product_source is not None and (
                product_source not in {"CU_FUTURE", "CU_OPTION", "BC_OPTION"}
                or type(product_deadline) not in (int, float)
                or not math.isfinite(product_deadline)
                or product_deadline > time.monotonic() + args["budget_seconds"] + 1
            ):
                raise ValueError("invalid product source")
            candidate_id = args.pop("candidate_id", "")
            if candidate_id:
                if args.pop("trusted_local_posix", None) is not True:
                    raise ValueError("explicit filesystem declaration required")
                from bt_api_py.trading_calendar_candidate_inbox import (
                    TRUSTED_LOCAL_POSIX,
                    CandidateRetention,
                    catalog_observation,
                    retain_candidate,
                    unknown_catalog,
                )

                def candidate_writer(*, merged, supplement, hours_page, source_checks):
                    catalog = None
                    sensitive_values = ()
                    if navigation_requested:
                        private_paths = [
                            args["config_file"],
                            args["calendar_root"],
                            args["supplement_file"],
                            product_options["product_page_file"],
                        ]
                        if catalog_options is not None:
                            private_paths.extend(
                                (
                                    catalog_options["catalog_cache_dir"],
                                    catalog_options["catalog_scope"],
                                )
                            )
                        sensitive_values = tuple(
                            private_paths + [os.path.normpath(path) for path in private_paths]
                        )
                    if catalog_options is not None:
                        try:
                            from bt_api_ctp.calendar_local_catalog import read_local_catalog

                            snapshot = read_local_catalog(
                                cache_dir=catalog_options["catalog_cache_dir"],
                                scope=catalog_options["catalog_scope"],
                                trading_day=catalog_options["catalog_trading_day"],
                                filesystem=TRUSTED_LOCAL_POSIX,
                            )
                            catalog = catalog_observation(
                                snapshot,
                                requested_day=catalog_options["catalog_trading_day"],
                                scope=catalog_options["catalog_scope"],
                                cache_dir=catalog_options["catalog_cache_dir"],
                                additional_sensitive=sensitive_values
                                if navigation_requested
                                else (
                                    product_options["product_page_file"],
                                    os.path.normpath(product_options["product_page_file"]),
                                )
                                if product_options is not None
                                else (),
                            )
                        except ImportError:
                            catalog = unknown_catalog(
                                "READER_UNAVAILABLE", catalog_options["catalog_trading_day"]
                            )
                        except Exception:
                            catalog = unknown_catalog(
                                "CACHE_INVALID", catalog_options["catalog_trading_day"]
                            )
                    product = None
                    product_navigation = None
                    product_fetch = None
                    if product_options is not None:
                        from bt_api_py.trading_calendar_product_observation import (
                            observe_product_page,
                        )

                        sensitive = ()
                        if catalog_options is not None:
                            cache_path = catalog_options["catalog_cache_dir"]
                            sensitive = (
                                catalog_options["catalog_scope"],
                                cache_path,
                                os.path.normpath(cache_path),
                            )
                        if navigation_requested:
                            from bt_api_py.trading_calendar_product_navigation_observation import (
                                observe_product_and_navigation,
                            )

                            product, product_navigation = observe_product_and_navigation(
                                source_url=product_options["product_page_url"],
                                path=product_options["product_page_file"],
                                filesystem=TRUSTED_LOCAL_POSIX,
                                additional_sensitive=sensitive_values,
                            )
                        else:
                            product = observe_product_page(
                                source_url=product_options["product_page_url"],
                                path=product_options["product_page_file"],
                                filesystem=TRUSTED_LOCAL_POSIX,
                                additional_sensitive=sensitive,
                            )
                    if product_source is not None:
                        from bt_api_py.trading_calendar_product_fetch_observation import (
                            fetch_observation,
                        )

                        private_paths = [
                            args["config_file"],
                            args["calendar_root"],
                            args["supplement_file"],
                        ]
                        if catalog_options is not None:
                            private_paths.extend(
                                (
                                    catalog_options["catalog_cache_dir"],
                                    catalog_options["catalog_scope"],
                                )
                            )
                        sensitive = tuple(
                            private_paths + [os.path.normpath(path) for path in private_paths]
                        )
                        reserve = min(5.0, max(1.0, args["budget_seconds"] * 0.2))
                        remaining = min(10.0, product_deadline - time.monotonic() - reserve)
                        product_fetch = fetch_observation(
                            product_source, total_timeout=remaining, sensitive=sensitive
                        )
                    retained = retain_candidate(
                        root=args["calendar_root"],
                        candidate_id=candidate_id,
                        merged=merged,
                        supplement=supplement,
                        hours_page=hours_page,
                        source_checks=source_checks,
                        filesystem=TRUSTED_LOCAL_POSIX,
                        catalog=catalog,
                        product=product,
                        product_fetch=product_fetch,
                        product_navigation=product_navigation,
                        sensitive_values=sensitive_values,
                    )
                    if (
                        product_fetch is not None
                        and product_fetch["transport_status"] == "CHILD_CLEANUP_UNCERTAIN"
                    ):
                        return CandidateRetention(
                            "COMMIT_UNKNOWN", "CHILD_CLEANUP_UNCERTAIN", candidate_id
                        )
                    return retained
            else:
                if (
                    catalog_options is not None
                    or product_options is not None
                    or product_source is not None
                    or "trusted_local_posix" in args
                ):
                    raise ValueError("catalog requires an explicit candidate")
                candidate_writer = None

            def progress(report: PreviewReport) -> None:
                latest_progress[0] = report
                if report.reason == "CHILD_CLEANUP_UNCERTAIN":
                    pending_cleanup[0] = report
                print(json.dumps(report.to_dict(), ensure_ascii=True, sort_keys=True), flush=True)

            work_started = True
            report = run_preview(
                **args,
                progress=progress,
                candidate_id=candidate_id,
                candidate_writer=candidate_writer,
            )
            if (
                report.candidate_reason == "CHILD_CLEANUP_UNCERTAIN"
                or report.reason == "CHILD_CLEANUP_UNCERTAIN"
            ):
                hold_for_cleanup(report)
        except BaseException as exc:
            if isinstance(exc, Exception) and not work_started:
                report = PreviewReport("ERROR", "INVALID_ARGUMENTS", 2)
            else:
                _mark_source_cleanup_uncertain(exc)
                last = pending_cleanup[0] or latest_progress[0]
                if last is None:
                    last = PreviewReport("INCOMPLETE", "INPUT_CHECK_COMPLETE", 10)
                report = replace(
                    last,
                    status="INCOMPLETE",
                    reason="CHILD_CLEANUP_UNCERTAIN",
                    exit_code=23,
                    candidate_id=candidate_id,
                    candidate_status="COMMIT_UNKNOWN" if candidate_id else "NOT_REQUESTED",
                    candidate_reason="CHILD_CLEANUP_UNCERTAIN" if candidate_id else "NONE",
                )
                hold_for_cleanup(report)
                if not isinstance(exc, Exception):
                    raise
        print(json.dumps(report.to_dict(), ensure_ascii=True, sort_keys=True))
        return report.exit_code
    parser = _Parser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--calendar-root", required=True)
    parser.add_argument("--supplement", required=True)
    parser.add_argument("--budget-seconds", required=True, type=float)
    parser.add_argument("--enable-shfe", action="store_true")
    parser.add_argument("--gateway-enabled", action="store_true")
    parser.add_argument("--direct-write-enabled", action="store_true")
    parser.add_argument("--max-retries", type=int, default=_MAX_RETRIES)
    parser.add_argument("--retry-interval-seconds", type=float, default=_DEFAULT_INTERVAL)
    parser.add_argument("--report-dir")
    parser.add_argument("--retain-candidate", action="store_true")
    parser.add_argument("--trusted-local-posix", action="store_true")
    parser.add_argument("--catalog-cache-dir")
    parser.add_argument("--catalog-scope")
    parser.add_argument("--catalog-trading-day")
    parser.add_argument("--product-page-url")
    parser.add_argument("--product-page-file")
    parser.add_argument("--discover-product-navigation", action="store_true")
    parser.add_argument("--product-source", choices=("CU_FUTURE", "CU_OPTION", "BC_OPTION"))
    retention_requested = any(
        arg == "--trusted-local-posix" or arg == "--report-dir" or arg.startswith("--report-dir=")
        for arg in argv
    )
    retention = (
        {
            "status": "NOT_ATTEMPTED",
            "visibility": "NOT_OBSERVED",
            "run_id": "",
            "reason": "INVALID_ARGUMENTS",
        }
        if retention_requested
        else None
    )
    try:
        args = parser.parse_args(argv)
        catalog_values = (args.catalog_cache_dir, args.catalog_scope, args.catalog_trading_day)
        if any(value is not None for value in catalog_values) and (
            not all(type(value) is str and value for value in catalog_values)
            or not args.retain_candidate
            or not args.trusted_local_posix
        ):
            raise _UsageError
        product_values = (args.product_page_url, args.product_page_file)
        if any(value is not None for value in product_values) and (
            not all(type(value) is str and value for value in product_values)
            or not args.retain_candidate
            or not args.trusted_local_posix
        ):
            raise _UsageError
        if args.discover_product_navigation and (
            not all(type(value) is str and value for value in product_values)
            or not args.retain_candidate
            or not args.trusted_local_posix
        ):
            raise _UsageError
        if args.product_source is not None and (
            not args.retain_candidate
            or not args.trusted_local_posix
            or any(value is not None for value in product_values)
        ):
            raise _UsageError
        retention_requested = args.report_dir is not None or (
            args.trusted_local_posix and not args.retain_candidate
        )
        if (
            (retention_requested and (args.report_dir is None or not args.trusted_local_posix))
            or (args.retain_candidate and not args.trusted_local_posix)
            or (args.trusted_local_posix and not (retention_requested or args.retain_candidate))
        ):
            retention = {
                "status": "NOT_ATTEMPTED",
                "visibility": "NOT_OBSERVED",
                "run_id": "",
                "reason": "INCOMPLETE_OPTIONS",
            }
            raise _UsageError
        if (
            not _valid_number(args.budget_seconds, 0.001, _MAX_BUDGET)
            or not _valid_number(args.retry_interval_seconds, 0, _MAX_BUDGET)
            or not 0 <= args.max_retries <= _MAX_RETRIES
        ):
            raise _UsageError
        values = {
            "config_file": args.config,
            "calendar_root": args.calendar_root,
            "supplement_file": args.supplement,
            "budget_seconds": args.budget_seconds,
            "enable_shfe": args.enable_shfe,
            "gateway_enabled": args.gateway_enabled,
            "direct_write_enabled": args.direct_write_enabled,
            "max_retries": args.max_retries,
            "retry_interval_seconds": args.retry_interval_seconds,
        }
        if args.retain_candidate:
            try:
                candidate_id = uuid.uuid4().hex
                if len(candidate_id) != 32 or any(
                    c not in "0123456789abcdef" for c in candidate_id
                ):
                    raise ValueError("invalid candidate ID")
            except Exception as exc:
                raise _UsageError from exc
            values["candidate_id"] = candidate_id
            values["trusted_local_posix"] = True
            if args.catalog_cache_dir is not None:
                values.update(
                    {
                        "catalog_cache_dir": args.catalog_cache_dir,
                        "catalog_scope": args.catalog_scope,
                        "catalog_trading_day": args.catalog_trading_day,
                    }
                )
            if args.product_page_url is not None:
                values.update(
                    {
                        "product_page_url": args.product_page_url,
                        "product_page_file": args.product_page_file,
                    }
                )
                if args.discover_product_navigation:
                    values["discover_product_navigation"] = True
            if args.product_source is not None:
                values["product_source"] = args.product_source
        try:
            report = _supervise(values)
            if type(report) is not PreviewReport:
                raise ValueError("invalid supervisor result")
        except Exception:
            report = PreviewReport("ERROR", "WORKER_COMMUNICATION_ERROR", 22)
        if args.retain_candidate and (
            report.candidate_id != candidate_id
            or report.candidate_status == "IN_PROGRESS"
            or report.reason
            in {
                "BUDGET_EXHAUSTED",
                "WORKER_CLEANUP_UNCERTAIN",
                "WORKER_COMMUNICATION_ERROR",
                "WORKER_PROTOCOL_ERROR",
            }
        ):
            report = replace(
                report,
                candidate_id=candidate_id,
                candidate_status="COMMIT_UNKNOWN",
                candidate_reason="IO_ERROR",
            )
        if retention_requested:
            retention = _retain_report(report, args.report_dir)
    except _UsageError:
        report = PreviewReport("ERROR", "INVALID_ARGUMENTS", 2)
    if not retention_requested:
        print(json.dumps(report.to_dict(), ensure_ascii=True, sort_keys=True))
        if report.candidate_status == "COMMIT_UNKNOWN":
            return 25
        if report.candidate_status == "NOT_COMMITTED":
            return 24
        return report.exit_code
    retention.update(
        {
            "retention_budget": "OUTSIDE_PREVIEW_BUDGET",
            "total_cli_time_bound": "NOT_PROVEN",
            "filesystem_verification": "NOT_PROVEN",
        }
    )
    print(
        json.dumps(
            {"preview": report.to_dict(), "retention": retention}, ensure_ascii=True, sort_keys=True
        )
    )
    if retention["status"] == "COMMIT_UNKNOWN" or report.candidate_status == "COMMIT_UNKNOWN":
        return 25
    if retention["status"] == "NOT_COMMITTED" or report.candidate_status == "NOT_COMMITTED":
        return 24
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
