"""Experimental preview of local inputs and two optional SHFE source fetches.

Usage: python -m bt_api_py.trading_calendar_preview --config /absolute/config.yaml
  --calendar-root /absolute/calendar --supplement /absolute/supplement.yaml
  --budget-seconds 30 [--enable-shfe]

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
The original ``source_status`` and ``attempts`` fields describe the trading
hours page; ``holiday_status`` and ``holiday_attempts`` are additive fields
for the holiday page. Parsing status is separate from transport status, and an
interrupted or failed child can leave whether parsing ran UNKNOWN.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from bt_api_py.trading_calendar_check import CalendarCheckReport, check_calendar_inputs

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
    other_five_venues: str = "NOT_IMPLEMENTED"
    completeness: str = "INCOMPLETE"
    verification: str = "UNVERIFIED"
    publication: str = "NOT_ATTEMPTED"
    maturity: str = "EXPERIMENTAL_PREVIEW"

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
    checker: Callable = check_calendar_inputs,
    collector: Callable | None = None,
    holiday_collector: Callable | None = None,
    holiday_converter: Callable | None = None,
    monotonic: Callable = time.monotonic,
    wait: Callable = time.sleep,
    progress: Callable | None = None,
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
        or (progress is not None and not callable(progress))
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
        checked = checker(
            config_file=config_file,
            calendar_root=calendar_root,
            supplement_file=supplement_file,
            gateway_enabled=gateway_enabled,
            direct_write_enabled=direct_write_enabled,
        )
        if type(checked) is not CalendarCheckReport or checked.code not in _CHECK_CODES:
            raise ValueError
    except Exception:
        return PreviewReport(
            "ERROR", "INPUT_CHECK_FAILED", 20, input_status="ERROR", input_code="CHECK_ERROR"
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
    base = {"input_status": input_status, "input_code": input_code, "supplement_status": supplement}
    if not (
        checked.exit_code == 0
        and input_status == "CHECKED"
        and input_code == "SUPPLEMENT_UNVERIFIED"
        and supplement == "CANDIDATE"
        and checked.source_verification == "UNVERIFIED"
    ):
        return PreviewReport("ERROR", "INPUT_CHECK_FAILED", 20, **base)

    def emit(report: PreviewReport) -> None:
        if progress is not None:
            progress(report)

    try:
        emit(
            PreviewReport(
                "INCOMPLETE",
                "INPUT_CHECK_COMPLETE",
                10,
                source_status="NOT_ATTEMPTED" if enable_shfe else "NOT_REQUESTED",
                holiday_status="NOT_ATTEMPTED" if enable_shfe else "NOT_REQUESTED",
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
        return PreviewReport("INCOMPLETE", "SHFE_NOT_REQUESTED", 10, **base)

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
            **base,
        )

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
            try:
                result = collect(
                    connect_timeout=min(3.0, allocation),
                    read_timeout=min(3.0, allocation),
                    total_timeout=allocation,
                )
                attempt = _attempt(result, len(attempts), holiday=is_holiday)
            except ImportError:
                attempt = Attempt(
                    len(attempts), "CTP_UNAVAILABLE", "CTP_UNAVAILABLE", "UNVERIFIED", 0, "UNKNOWN"
                )
            except Exception:
                attempt = Attempt(
                    len(attempts), "COLLECTOR_ERROR", "COLLECTOR_ERROR", "UNVERIFIED", 0, "UNKNOWN"
                )
            attempts[-1] = attempt
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
            if attempt.transport_status == "CHILD_CLEANUP_UNCERTAIN":
                reasons[index] = "CHILD_CLEANUP_UNCERTAIN"
                return report("INCOMPLETE", "CHILD_CLEANUP_UNCERTAIN", 23)
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


def _supervise(args: dict[str, Any]) -> PreviewReport:
    if os.name != "posix" or not hasattr(os, "killpg"):
        return PreviewReport("ERROR", "SUPERVISOR_UNSUPPORTED", 22)
    started = time.monotonic()
    deadline = started + args["budget_seconds"]
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
    payload = json.dumps(args, ensure_ascii=True).encode("ascii")
    supplied = False
    communication_failed = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            output, _ = child.communicate(
                input=payload if not supplied else None, timeout=min(30.0, remaining)
            )
            if child.returncode not in (0, 2, 10, 20, 21, 22, 23) or len(output) > _MAX_JSON:
                return PreviewReport("ERROR", "WORKER_PROTOCOL_ERROR", 22)
            lines = output.splitlines()
            return _parse_worker_result(lines[-1] if lines else b"", child.returncode)
        except subprocess.TimeoutExpired:
            supplied = True
        except Exception:
            communication_failed = True
            break
    try:
        # The new session contains only this worker and its descendants.
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
            interrupted = replace(
                history[-1],
                transport_status="INTERRUPTED",
                source_status="INTERRUPTED",
                parse_status="INTERRUPTED",
            )
            latest = replace(
                latest, **{status_field: "INTERRUPTED", field: (*history[:-1], interrupted)}
            )
            # Reaping the worker cannot prove its collector descendant exited.
            return replace(latest, status="ERROR", reason="WORKER_CLEANUP_UNCERTAIN", exit_code=23)
    if latest.holiday_conversion_status == "IN_PROGRESS":
        latest = replace(latest, holiday_conversion_status="INTERRUPTED")
    if communication_failed:
        return replace(latest, status="ERROR", reason="WORKER_COMMUNICATION_ERROR", exit_code=22)
    return replace(latest, status="INCOMPLETE", reason="BUDGET_EXHAUSTED", exit_code=21)


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
        try:
            raw = sys.stdin.buffer.read(32769)
            if len(raw) > 32768:
                raise ValueError
            args = json.loads(raw)
            if type(args) is not dict or set(args) != {
                "config_file",
                "calendar_root",
                "supplement_file",
                "budget_seconds",
                "enable_shfe",
                "gateway_enabled",
                "direct_write_enabled",
                "max_retries",
                "retry_interval_seconds",
            }:
                raise ValueError

            def progress(report: PreviewReport) -> None:
                print(json.dumps(report.to_dict(), ensure_ascii=True, sort_keys=True), flush=True)

            report = run_preview(**args, progress=progress)
        except Exception:
            report = PreviewReport("ERROR", "INVALID_ARGUMENTS", 2)
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
    try:
        args = parser.parse_args(argv)
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
        report = _supervise(values)
    except _UsageError:
        report = PreviewReport("ERROR", "INVALID_ARGUMENTS", 2)
    print(json.dumps(report.to_dict(), ensure_ascii=True, sort_keys=True))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
