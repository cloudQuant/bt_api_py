"""Experimental preview of local inputs and one optional SHFE page fetch.

Usage: python -m bt_api_py.trading_calendar_preview --config /absolute/config.yaml
  --calendar-root /absolute/calendar --supplement /absolute/supplement.yaml
  --budget-seconds 30 [--enable-shfe]

No CURRENT is read or written, no candidate is approved or published, and no
account or email is accessed. Input diagnostics, source attempts and results are
finite summaries only: paths, configuration values, source data and exceptions
are not printed. The other five venues remain INCOMPLETE. Even a successful
single-source fetch is UNVERIFIED, not an official calendar or release decision.

The CLI supervises its own worker in a new POSIX session with a total deadline,
and kills that owned process group on timeout. OS process creation and an
uninterruptible kernel process cannot be strictly bounded; cleanup uncertainty
is explicit. Windows and network-filesystem behavior are NOT_RUN. The callable
``run_preview`` is for embedding/tests and cannot forcibly interrupt a blocking
injected port; use the supervised CLI for a process deadline. Source timeouts
and JSON do not prove provenance, coverage, freshness or a stable input file.
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


def _attempt(result: object, number: int) -> Attempt:
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
        ):
            raise ValueError
        if transport == "HTTP_OK":
            if (
                page is None
                or source_status not in _PAGE
                or page.status != source_status
                or page.verification != "UNVERIFIED"
                or type(page.products) is not tuple
                or len(page.products) > 1024
            ):
                raise ValueError
            count = len(page.products)
        else:
            if source_status != transport or page is not None:
                raise ValueError
            count = 0
        return Attempt(number, transport, source_status, "UNVERIFIED", count)
    except Exception:
        return Attempt(
            number, "COLLECTOR_PROTOCOL_ERROR", "COLLECTOR_PROTOCOL_ERROR", "UNVERIFIED", 0
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
                "INCOMPLETE", "INPUT_CHECK_COMPLETE", 10, source_status="NOT_ATTEMPTED", **base
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
        return PreviewReport("INCOMPLETE", "BUDGET_EXHAUSTED", 21, **base)
    if not enable_shfe:
        return PreviewReport("INCOMPLETE", "SHFE_NOT_REQUESTED", 10, **base)

    collect = collector or _default_collector
    attempts = []
    while True:
        remaining = deadline - now
        allocation = min(_PER_ATTEMPT, remaining - _CLEANUP_ALLOWANCE)
        if allocation <= 0:
            return PreviewReport(
                "INCOMPLETE",
                "BUDGET_EXHAUSTED",
                21,
                source_status=attempts[-1].source_status if attempts else "NOT_ATTEMPTED",
                attempts=tuple(attempts),
                **base,
            )
        pending = Attempt(len(attempts) + 1, "IN_PROGRESS", "IN_PROGRESS", "UNVERIFIED", 0)
        try:
            emit(
                PreviewReport(
                    "INCOMPLETE",
                    "IN_PROGRESS",
                    10,
                    source_status="IN_PROGRESS",
                    attempts=(*attempts, pending),
                    **base,
                )
            )
        except Exception:
            return PreviewReport(
                "ERROR",
                "PROGRESS_UNAVAILABLE",
                22,
                source_status=attempts[-1].source_status if attempts else "NOT_ATTEMPTED",
                attempts=tuple(attempts),
                **base,
            )
        try:
            result = collect(
                connect_timeout=min(3.0, allocation),
                read_timeout=min(3.0, allocation),
                total_timeout=allocation,
            )
            attempt = _attempt(result, len(attempts) + 1)
        except ImportError:
            attempt = Attempt(
                len(attempts) + 1, "CTP_UNAVAILABLE", "CTP_UNAVAILABLE", "UNVERIFIED", 0
            )
        except Exception:
            attempt = Attempt(
                len(attempts) + 1, "COLLECTOR_ERROR", "COLLECTOR_ERROR", "UNVERIFIED", 0
            )
        attempts.append(attempt)
        try:
            emit(
                PreviewReport(
                    "INCOMPLETE",
                    "IN_PROGRESS",
                    10,
                    source_status=attempt.source_status,
                    attempts=tuple(attempts),
                    **base,
                )
            )
        except Exception:
            return PreviewReport(
                "ERROR",
                "PROGRESS_UNAVAILABLE",
                22,
                source_status=attempt.source_status,
                attempts=tuple(attempts),
                **base,
            )
        try:
            finished = monotonic()
            if not _valid_number(finished, now, float("inf")):
                raise ValueError
        except Exception:
            return PreviewReport(
                "ERROR",
                "CLOCK_UNAVAILABLE",
                2,
                source_status=attempt.source_status,
                attempts=tuple(attempts),
                **base,
            )
        now = finished
        if attempt.transport_status == "CHILD_CLEANUP_UNCERTAIN":
            return PreviewReport(
                "INCOMPLETE",
                "CHILD_CLEANUP_UNCERTAIN",
                23,
                source_status=attempt.source_status,
                attempts=tuple(attempts),
                **base,
            )
        if now >= deadline:
            return PreviewReport(
                "INCOMPLETE",
                "BUDGET_EXHAUSTED",
                21,
                source_status=attempt.source_status,
                attempts=tuple(attempts),
                **base,
            )
        if attempt.transport_status not in {"NETWORK_TIMEOUT", "CHILD_TIMEOUT"}:
            reason = (
                "OTHER_VENUES_NOT_IMPLEMENTED"
                if attempt.source_status == "CANDIDATE"
                else "SHFE_NOT_VERIFIED"
            )
            return PreviewReport(
                "INCOMPLETE",
                reason,
                10,
                source_status=attempt.source_status,
                attempts=tuple(attempts),
                **base,
            )
        if len(attempts) > max_retries:
            return PreviewReport(
                "INCOMPLETE",
                "RETRY_LIMIT_REACHED",
                10,
                source_status=attempt.source_status,
                attempts=tuple(attempts),
                **base,
            )
        if now + retry_interval_seconds + _CLEANUP_ALLOWANCE >= deadline:
            return PreviewReport(
                "INCOMPLETE",
                "RETRY_BUDGET_EXHAUSTED",
                21,
                source_status=attempt.source_status,
                attempts=tuple(attempts),
                **base,
            )
        wake = now + retry_interval_seconds
        while now < wake:
            try:
                wait(min(30.0, wake - now))
                current = monotonic()
                if not _valid_number(current, now, float("inf")) or current <= now:
                    raise ValueError
                now = current
            except Exception:
                return PreviewReport(
                    "ERROR",
                    "CLOCK_UNAVAILABLE",
                    2,
                    source_status=attempt.source_status,
                    attempts=tuple(attempts),
                    **base,
                )
            if now >= deadline:
                return PreviewReport(
                    "INCOMPLETE",
                    "BUDGET_EXHAUSTED",
                    21,
                    source_status=attempt.source_status,
                    attempts=tuple(attempts),
                    **base,
                )


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
    interrupted_fetch = bool(
        latest.attempts and latest.attempts[-1].transport_status == "IN_PROGRESS"
    )
    if interrupted_fetch:
        interrupted = replace(
            latest.attempts[-1], transport_status="INTERRUPTED", source_status="INTERRUPTED"
        )
        latest = replace(
            latest, source_status="INTERRUPTED", attempts=(*latest.attempts[:-1], interrupted)
        )
    if interrupted_fetch:
        # Reaping the worker alone cannot prove its collector descendant exited.
        return replace(latest, status="ERROR", reason="WORKER_CLEANUP_UNCERTAIN", exit_code=23)
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
        if data["source_status"] not in allowed_sources:
            raise ValueError
        if (
            data["other_five_venues"],
            data["completeness"],
            data["verification"],
            data["publication"],
            data["maturity"],
        ) != (
            "NOT_IMPLEMENTED",
            "INCOMPLETE",
            "UNVERIFIED",
            "NOT_ATTEMPTED",
            "EXPERIMENTAL_PREVIEW",
        ):
            raise ValueError
        if type(data["attempts"]) is not list or len(data["attempts"]) > 3:
            raise ValueError
        attempts = []
        for index, item in enumerate(data["attempts"], 1):
            if type(item) is not dict or set(item) != set(Attempt.__dataclass_fields__):
                raise ValueError
            if (
                type(item["number"]) is not int
                or item["number"] != index
                or item["transport_status"] not in allowed_sources
                or item["source_status"] not in allowed_sources
                or item["verification"] != "UNVERIFIED"
                or type(item["product_count"]) is not int
                or not 0 <= item["product_count"] <= 1024
            ):
                raise ValueError
            attempts.append(Attempt(**item))
        data["attempts"] = tuple(attempts)
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
