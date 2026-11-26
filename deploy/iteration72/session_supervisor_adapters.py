"""Fail-closed HTTP adapters for the Iteration 72 session supervisor.

The adapters intentionally do not load ``session_supervisor.py`` themselves.
That file is commonly loaded by path, and loading a second copy would create
different result-class identities.  The composing owner must pass the exact
``StepResult``, ``GroupResult`` and ``RiskGateObservation`` constructors from
the already loaded supervisor module.

Only bounded public projections leave these adapters.  Runtime and Risk
credentials, Runtime confirmation tokens, raw replies and exception text are
never returned or included in ``repr`` output.  Every network/control action
is single-attempt.  An ambiguous response is UNKNOWN and is never retried with
a new command identifier.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid5

CONTROL_RESPONSE_SCHEMA = "bt_api_runtime.control/v1"
RISK_STATUS_PATH = "/api/v1/risk/status?order_cut_admission=true"
RISK_STATUS_PROOF_PATH = (
    "/api/v1/risk/status?order_cut_admission=true&order_cut_proof=true"
)
MAX_RISK_RESPONSE_BYTES = 64 * 1024
COMPONENT_NAMES = frozenset(
    {
        "monitor",
        "monitor_query",
        "gateway",
        "gateway_web",
        "risk",
        "risk_web",
        "execution",
        "runtime",
        "web",
    }
)
_STOP_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.stop\Z")

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_SHA256_REFERENCE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONFIRMATION_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,256}\Z")
_RISK_SESSION_TOKEN = re.compile(r"[a-f0-9]{64}\Z")
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "credential",
        "authorization",
        "api_key",
        "apikey",
        "private_key",
        "client_secret_key",
        "auth_code",
        "confirmation_token",
        "session_token",
    }
)
_UNIT_ROW_KEYS = frozenset(
    {
        "unit",
        "access",
        "state",
        "code",
        "identity_state",
        "phase",
        "heartbeat_age_s",
        "heartbeat_fresh",
        "process_alive",
        "run_id",
        "stop_step",
        "clean",
        "pending_fields",
        "exit_code_observed",
        "detail",
    }
)
_UNIT_STATES = frozenset(
    {
        "NOT_INITIALIZED",
        "INITIALIZED_NO_RUN",
        "STARTING",
        "RUNNING",
        "STALE",
        "STOPPING",
        "STOPPED",
        "STOPPED_WITH_WORKING_ORDERS",
        "STOPPED_WITH_WRITE_IN_FLIGHT",
        "BLOCKED",
        "CRASHED",
        "KILLED",
        "LOST",
        "UNMANAGED_RUNNING",
        "UNKNOWN",
    }
)


class RuntimeControlClient(Protocol):
    """The reviewed method surface supplied by ``ControlClientAdapter``."""

    def list_units(self, *, credential: str | None = None) -> object:
        ...

    def unit_action(
        self,
        action: str,
        unit_name: str | None = None,
        *,
        credential: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        command_id: str | None = None,
        reason: str = "",
        confirm: str | None = None,
        all_units: bool = False,
        force: bool = False,
    ) -> object:
        ...


def _canonical_session_id(value: str) -> str:
    if type(value) is not str:
        raise ValueError("session_id must be a canonical UUID")
    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("session_id must be a canonical UUID") from exc
    if canonical != value:
        raise ValueError("session_id must be a canonical UUID")
    return canonical


def _normalise_units(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("units must be a finite sequence")
    units = tuple(values)
    if not units or any(
        type(item) is not str or _SAFE_NAME.fullmatch(item) is None for item in units
    ):
        raise ValueError("units must contain finite public names")
    if len(units) != len(set(units)):
        raise ValueError("units must be unique")
    return tuple(sorted(units))


def _safe_reference(value: str) -> str:
    if type(value) is not str or _SHA256_REFERENCE.fullmatch(value) is None:
        raise ValueError("gateway_reference must be a sha256 reference")
    return value


def _reference(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(payload),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _bounded_json_tree(
    value: object,
    *,
    allowed_sensitive_root_keys: frozenset[str] = frozenset(),
) -> bool:
    """Validate one bounded JSON tree without returning any raw value."""

    stack: list[tuple[object, int, bool]] = [(value, 0, True)]
    nodes = 0
    while stack:
        item, depth, root = stack.pop()
        nodes += 1
        if nodes > 16_384 or depth > 12:
            return False
        if item is None or type(item) is bool or type(item) is int:
            continue
        if type(item) is float:
            if not math.isfinite(item):
                return False
            continue
        if type(item) is str:
            try:
                if (
                    len(item) > 16_384
                    or len(item.encode("utf-8", errors="strict")) > 65_536
                ):
                    return False
            except UnicodeError:
                return False
            continue
        if type(item) is list:
            if len(item) > 4096:
                return False
            stack.extend((child, depth + 1, False) for child in item)
            continue
        if type(item) is dict:
            if len(item) > 256:
                return False
            for key, child in item.items():
                if type(key) is not str:
                    return False
                lowered = key.lower()
                if lowered in _SENSITIVE_KEYS and not (
                    root and lowered in allowed_sensitive_root_keys
                ):
                    return False
                stack.append((child, depth + 1, False))
            continue
        return False
    return True


def _response_mapping(
    response: object,
    *,
    allowed_sensitive_root_keys: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    try:
        http_status = response.http_status  # type: ignore[attr-defined]
        mapping = response.to_mapping()  # type: ignore[attr-defined]
    except Exception:
        return None
    if type(http_status) is not int or http_status != 200 or type(mapping) is not dict:
        return None
    schema = mapping.get("schema")
    if type(schema) is not str or schema != CONTROL_RESPONSE_SCHEMA:
        return None
    status = mapping.get("status")
    code = mapping.get("code")
    if (
        type(status) is not str
        or type(code) is not str
        or _SAFE_CODE.fullmatch(code) is None
    ):
        return None
    if not _bounded_json_tree(
        mapping, allowed_sensitive_root_keys=allowed_sensitive_root_keys
    ):
        return None
    return mapping


def _canonical_uuid_or_none(value: object) -> bool:
    if value is None:
        return True
    if type(value) is not str:
        return False
    try:
        return str(UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


def _valid_unit_row(row: object) -> bool:
    if type(row) is not dict or frozenset(row) != _UNIT_ROW_KEYS:
        return False
    if type(row["unit"]) is not str or _SAFE_NAME.fullmatch(row["unit"]) is None:
        return False
    if type(row["access"]) is not str or row["access"] not in {
        "direct",
        "gateway",
    }:
        return False
    if (
        type(row["state"]) is not str
        or row["state"] not in _UNIT_STATES
        or type(row["code"]) is not str
    ):
        return False
    if _SAFE_CODE.fullmatch(row["code"]) is None:
        return False
    if type(row["identity_state"]) is not str or row["identity_state"] not in {
        "present",
        "not_initialized",
        "mismatch",
        "corrupt",
        "unknown",
    }:
        return False
    if (row["phase"] is not None and type(row["phase"]) is not str) or row[
        "phase"
    ] not in {
        None,
        "INITIALIZING",
        "RECONCILING",
        "WARMUP",
        "LIVE",
        "STOPPING",
        "STOPPED",
    }:
        return False
    age = row["heartbeat_age_s"]
    if age is not None:
        if type(age) not in {int, float}:
            return False
        try:
            finite_age = float(age)
        except (OverflowError, TypeError, ValueError):
            return False
        if not math.isfinite(finite_age) or finite_age < 0.0:
            return False
    if (
        type(row["heartbeat_fresh"]) is not bool
        or type(row["process_alive"]) is not bool
    ):
        return False
    if not _canonical_uuid_or_none(row["run_id"]):
        return False
    if (row["stop_step"] is not None and type(row["stop_step"]) is not str) or row[
        "stop_step"
    ] not in {
        None,
        "NONE",
        "A1_PENDING",
        "A2_WAIT",
        "E1_PENDING",
        "E1_WAIT",
        "E2_PENDING",
        "E2_WAIT",
        "E3_PENDING",
        "E3_WAIT",
        "DONE",
    }:
        return False
    if type(row["clean"]) is not bool or type(row["pending_fields"]) is not list:
        return False
    if any(
        type(item) is not str or _SAFE_CODE.fullmatch(item) is None
        for item in row["pending_fields"]
    ):
        return False
    exit_code = row["exit_code_observed"]
    if exit_code is not None and (
        type(exit_code) is not int or type(exit_code) is bool
    ):
        return False
    return type(row["detail"]) is dict and _bounded_json_tree(row["detail"])


def _startable_row(row: Mapping[str, Any]) -> bool:
    common = (
        row["identity_state"] == "present"
        and row["process_alive"] is False
        and row["heartbeat_fresh"] is False
        and row["pending_fields"] == []
    )
    if not common:
        return False
    if row["state"] == "INITIALIZED_NO_RUN":
        return (
            row["code"] == "identity_present"
            and row["run_id"] is None
            and row["phase"] is None
            and row["stop_step"] is None
            and row["clean"] is False
            and row["exit_code_observed"] is None
        )
    return _clean_stopped_row(row)


def _clean_stopped_row(row: Mapping[str, Any]) -> bool:
    return (
        row["identity_state"] == "present"
        and row["process_alive"] is False
        and row["heartbeat_fresh"] is False
        and row["pending_fields"] == []
        and row["state"] == "STOPPED"
        and row["code"] == "stopped"
        and row["run_id"] is not None
        and row["phase"] in {None, "STOPPED"}
        and row["stop_step"] == "DONE"
        and row["clean"] is True
        and type(row["exit_code_observed"]) is int
        and type(row["exit_code_observed"]) is not bool
        and row["exit_code_observed"] == 0
    )


def _running_row(row: Mapping[str, Any]) -> bool:
    detail = row["detail"]
    return (
        row["state"] == "RUNNING"
        and row["code"] == "running"
        and row["identity_state"] == "present"
        and row["phase"] == "LIVE"
        and row["heartbeat_fresh"] is True
        and row["process_alive"] is True
        and row["run_id"] is not None
        and row["clean"] is False
        and row["pending_fields"] == []
        and row["exit_code_observed"] is None
        and detail.get("last_error_code") is None
        and detail.get("write_block_reasons") == []
    )


class RuntimeHttpPort:
    """Strict Runtime adapter for one supervisor attempt.

    The public Runtime start-all endpoint always targets every configured unit.
    This adapter therefore validates that the visible configured set equals the
    supervisor plan both during preflight and immediately before confirmation.
    The Runtime performs its own authoritative preflight under the lifecycle
    lock before spawning the first process.
    """

    _START_REASON = "iteration72_session_supervisor_start_all"
    _STOP_REASON = "iteration72_session_supervisor_cooperative_stop"
    __slots__ = (
        "_client",
        "_step_result",
        "_group_result",
        "_lock",
        "_planned_units",
        "_session_id",
        "_start_attempted",
        "_stop_attempted",
    )

    def __init__(
        self,
        client: RuntimeControlClient,
        *,
        step_result_factory: Callable[..., object],
        group_result_factory: Callable[..., object],
    ) -> None:
        if not callable(getattr(client, "list_units", None)) or not callable(
            getattr(client, "unit_action", None)
        ):
            raise TypeError("client must implement the Runtime control surface")
        if not callable(step_result_factory) or not callable(group_result_factory):
            raise TypeError("result factories must be callable")
        self._client = client
        self._step_result = step_result_factory
        self._group_result = group_result_factory
        self._lock = Lock()
        self._planned_units: tuple[str, ...] | None = None
        self._session_id: str | None = None
        self._start_attempted = False
        self._stop_attempted: set[str] = set()

    def __repr__(self) -> str:
        return "RuntimeHttpPort()"

    @classmethod
    def from_http(
        cls,
        *,
        host: str,
        port: int,
        token: str,
        step_result_factory: Callable[..., object],
        group_result_factory: Callable[..., object],
        timeout: float = 5.0,
    ) -> RuntimeHttpPort:
        """Build from the reviewed loopback-only Runtime HTTP client."""

        from bt_api_runtime.control_http import ControlClientAdapter, HttpControlClient

        if type(timeout) not in {int, float} or type(timeout) is bool:
            raise ValueError("Runtime timeout is invalid")
        timeout_value = float(timeout)
        if not math.isfinite(timeout_value) or not 0.0 < timeout_value <= 30.0:
            raise ValueError("Runtime timeout is invalid")
        client = HttpControlClient(
            host=host, port=port, token=token, timeout=timeout_value
        )
        return cls(
            ControlClientAdapter(client),
            step_result_factory=step_result_factory,
            group_result_factory=group_result_factory,
        )

    def _step(
        self,
        status: str,
        code: str,
        *,
        reference: str | None = None,
        exit_code: int | None = None,
    ) -> object:
        return self._step_result(status, code, reference=reference, exit_code=exit_code)

    def _group(
        self,
        status: str,
        code: str,
        *,
        started: Sequence[str] = (),
        not_started: Sequence[str] = (),
        uncertain: Sequence[str] = (),
        reference: str | None = None,
    ) -> object:
        return self._group_result(
            status,
            code,
            started_units=tuple(started),
            not_started_units=tuple(not_started),
            uncertain_units=tuple(uncertain),
            reference=reference,
        )

    def _read_rows(
        self, expected: tuple[str, ...]
    ) -> tuple[str, dict[str, dict[str, Any]] | None, str | None]:
        try:
            response = self._client.list_units()
        except Exception:
            return "UNKNOWN", None, None
        mapping = _response_mapping(response)
        if mapping is None:
            return "UNKNOWN", None, None
        if mapping.get("status") != "OK" or mapping.get("code") != "ok":
            return "UNKNOWN", None, None
        rows = mapping.get("units")
        count = mapping.get("count")
        if type(rows) is not list or type(count) is not int or type(count) is bool:
            return "UNKNOWN", None, None
        if count != len(rows) or any(not _valid_unit_row(row) for row in rows):
            return "UNKNOWN", None, None
        by_name = {row["unit"]: row for row in rows}
        if len(by_name) != len(rows):
            return "UNKNOWN", None, None
        if tuple(sorted(by_name)) != expected:
            return "BLOCKED", None, None
        reference = _reference(
            {
                "kind": "runtime-unit-snapshot",
                "units": [
                    {
                        "unit": name,
                        "state": by_name[name]["state"],
                        "code": by_name[name]["code"],
                        "identity_state": by_name[name]["identity_state"],
                        "phase": by_name[name]["phase"],
                        "heartbeat_fresh": by_name[name]["heartbeat_fresh"],
                        "process_alive": by_name[name]["process_alive"],
                        "clean": by_name[name]["clean"],
                    }
                    for name in expected
                ],
            }
        )
        return "OK", by_name, reference

    def preflight_target_set(self, units: tuple[str, ...]) -> object:
        try:
            expected = _normalise_units(units)
        except Exception:
            return self._step("UNKNOWN", "runtime_target_invalid")
        with self._lock:
            outcome, rows, reference = self._read_rows(expected)
            if outcome == "BLOCKED":
                return self._step("BLOCKED", "runtime_target_mismatch")
            if outcome != "OK" or rows is None:
                return self._step("UNKNOWN", "runtime_status_unknown")
            if not all(_startable_row(rows[name]) for name in expected):
                return self._step(
                    "BLOCKED", "runtime_unit_not_startable", reference=reference
                )
            if self._planned_units is not None and self._planned_units != expected:
                return self._step("UNKNOWN", "runtime_target_changed")
            self._planned_units = expected
            return self._step("APPLIED", "ready", reference=reference)

    @staticmethod
    def _command_id(session_id: str, label: str) -> str:
        return str(uuid5(UUID(session_id), label))

    @staticmethod
    def _unit_set_token(units: tuple[str, ...]) -> str:
        return hashlib.sha256("\0".join(units).encode("utf-8")).hexdigest()

    @staticmethod
    def _confirmation_token(
        mapping: Mapping[str, Any],
        *,
        command_id: str,
        action: str,
        all_units: bool,
    ) -> str | None:
        if (
            mapping.get("status") != "CONFIRMATION_REQUIRED"
            or mapping.get("code") != "confirmation_required"
            or mapping.get("command_id") != command_id
            or mapping.get("action") != action
            or (
                mapping.get("all_units", False) is not all_units
                or (all_units and "all_units" not in mapping)
            )
        ):
            return None
        token = mapping.get("confirmation_token")
        if type(token) is not str or _CONFIRMATION_TOKEN.fullmatch(token) is None:
            return None
        return token

    @staticmethod
    def _classifications(
        mapping: Mapping[str, Any], expected: tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] | None:
        groups: list[tuple[str, ...]] = []
        seen: set[str] = set()
        for key in ("started_units", "not_started_units", "uncertain_units"):
            raw = mapping.get(key)
            if type(raw) is not list:
                return None
            try:
                values = tuple(sorted(raw))
            except TypeError:
                return None
            if any(type(item) is not str or item not in expected for item in values):
                return None
            if len(values) != len(set(values)) or seen.intersection(values):
                return None
            seen.update(values)
            groups.append(values)
        if seen != set(expected):
            return None
        return groups[0], groups[1], groups[2]

    @staticmethod
    def _start_unit_results(
        mapping: Mapping[str, Any],
        expected: tuple[str, ...],
        command_id: str,
    ) -> tuple[dict[str, Any], ...] | None:
        raw = mapping.get("units")
        if type(raw) is not list or len(raw) > len(expected):
            return None
        results: list[dict[str, Any]] = []
        names: list[str] = []
        for item in raw:
            if type(item) is not dict or set(item) != {
                "unit",
                "status",
                "code",
                "state",
                "command_id",
            }:
                return None
            unit = item["unit"]
            status = item["status"]
            code = item["code"]
            state = item["state"]
            child_id = item["command_id"]
            if (
                type(unit) is not str
                or unit not in expected
                or type(status) is not str
                or type(code) is not str
                or _SAFE_CODE.fullmatch(code) is None
                or (
                    state is not None
                    and (type(state) is not str or state not in _UNIT_STATES)
                )
                or type(child_id) is not str
            ):
                return None
            try:
                expected_child = str(uuid5(UUID(command_id), unit))
            except (TypeError, ValueError, AttributeError):
                return None
            if child_id != expected_child:
                return None
            names.append(unit)
            results.append(item)
        if len(names) != len(set(names)) or tuple(names) != expected[: len(names)]:
            return None
        return tuple(results)

    @staticmethod
    def _derived_start_classifications(
        unit_results: tuple[dict[str, Any], ...],
        expected: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        started: list[str] = []
        not_started: list[str] = []
        uncertain: list[str] = []
        for item in unit_results:
            unit = item["unit"]
            status = item["status"]
            code = item["code"]
            state = item["state"]
            if status == "APPLIED" and code == "started" and state == "STARTING":
                started.append(unit)
            elif status == "UNKNOWN" or state == "UNKNOWN":
                uncertain.append(unit)
            else:
                not_started.append(unit)
        not_started.extend(expected[len(unit_results) :])
        return tuple(started), tuple(not_started), tuple(uncertain)

    def start_all_confirmed(self, session_id: str, units: tuple[str, ...]) -> object:
        try:
            canonical_session = _canonical_session_id(session_id)
            expected = _normalise_units(units)
        except Exception:
            return self._group("UNKNOWN", "runtime_target_invalid")
        with self._lock:
            if self._start_attempted:
                return self._group(
                    "UNKNOWN", "runtime_start_already_attempted", uncertain=expected
                )
            self._start_attempted = True
            if self._planned_units != expected:
                return self._group(
                    "UNKNOWN", "runtime_target_changed", uncertain=expected
                )
            if self._session_id is not None and self._session_id != canonical_session:
                return self._group(
                    "UNKNOWN", "runtime_session_changed", uncertain=expected
                )
            self._session_id = canonical_session

            outcome, rows, snapshot_reference = self._read_rows(expected)
            if outcome == "BLOCKED":
                return self._group(
                    "BLOCKED",
                    "runtime_target_mismatch",
                    not_started=expected,
                )
            if outcome != "OK" or rows is None:
                return self._group(
                    "UNKNOWN", "runtime_status_unknown", uncertain=expected
                )
            if not all(_startable_row(rows[name]) for name in expected):
                return self._group(
                    "BLOCKED",
                    "runtime_unit_not_startable",
                    not_started=expected,
                    reference=snapshot_reference,
                )

            command_id = self._command_id(
                canonical_session,
                "iteration72:runtime:start-all:" + self._unit_set_token(expected),
            )
            request = {
                "action": "start",
                "unit_name": None,
                "parameters": {},
                "command_id": command_id,
                "reason": self._START_REASON,
                "all_units": True,
                "force": False,
            }
            try:
                first = self._client.unit_action(confirm=None, **request)
            except Exception:
                return self._group(
                    "UNKNOWN", "runtime_start_unknown", uncertain=expected
                )
            first_mapping = _response_mapping(
                first,
                allowed_sensitive_root_keys=frozenset({"confirmation_token"}),
            )
            if first_mapping is None:
                return self._group(
                    "UNKNOWN", "runtime_start_unknown", uncertain=expected
                )
            token = self._confirmation_token(
                first_mapping,
                command_id=command_id,
                action="start",
                all_units=True,
            )
            if token is None:
                return self._group(
                    "UNKNOWN", "runtime_confirmation_unknown", uncertain=expected
                )
            try:
                final = self._client.unit_action(confirm=token, **request)
            except Exception:
                return self._group(
                    "UNKNOWN", "runtime_start_unknown", uncertain=expected
                )
            finally:
                token = None
            final_mapping = _response_mapping(final)
            if final_mapping is None:
                return self._group(
                    "UNKNOWN", "runtime_start_unknown", uncertain=expected
                )
            if (
                final_mapping.get("command_id") != command_id
                or final_mapping.get("action") != "start"
                or final_mapping.get("all_units") is not True
            ):
                return self._group(
                    "UNKNOWN", "runtime_start_unknown", uncertain=expected
                )
            classified = self._classifications(final_mapping, expected)
            unit_results = self._start_unit_results(final_mapping, expected, command_id)
            if classified is None or unit_results is None:
                return self._group(
                    "UNKNOWN", "runtime_start_unknown", uncertain=expected
                )
            if (
                self._derived_start_classifications(unit_results, expected)
                != classified
            ):
                return self._group(
                    "UNKNOWN", "runtime_start_unknown", uncertain=expected
                )
            started, not_started, uncertain = classified
            result_reference = _reference(
                {
                    "kind": "runtime-start-all",
                    "command_id": command_id,
                    "status": final_mapping["status"],
                    "code": final_mapping["code"],
                    "started_units": list(started),
                    "not_started_units": list(not_started),
                    "uncertain_units": list(uncertain),
                }
            )
            if (
                final_mapping["status"] == "APPLIED"
                and final_mapping["code"] == "started"
            ):
                if (
                    started == expected
                    and not not_started
                    and not uncertain
                    and len(unit_results) == len(expected)
                    and all(
                        item["status"] == "APPLIED"
                        and item["code"] == "started"
                        and item["state"] == "STARTING"
                        for item in unit_results
                    )
                ):
                    return self._group(
                        "APPLIED",
                        "started",
                        started=started,
                        reference=result_reference,
                    )
                return self._group(
                    "UNKNOWN",
                    "runtime_start_unknown",
                    uncertain=expected,
                    reference=result_reference,
                )
            if (
                final_mapping["status"] == "REJECTED"
                and final_mapping["code"] == "start_preflight_blocked"
            ):
                if (
                    not started
                    and not_started == expected
                    and not uncertain
                    and not unit_results
                ):
                    return self._group(
                        "BLOCKED",
                        "start_preflight_blocked",
                        not_started=not_started,
                        reference=result_reference,
                    )
                return self._group(
                    "UNKNOWN",
                    "runtime_start_unknown",
                    uncertain=expected,
                    reference=result_reference,
                )
            return self._group(
                "UNKNOWN",
                "start_set_partial",
                started=started,
                not_started=not_started,
                uncertain=uncertain,
                reference=result_reference,
            )

    def status_group(self, session_id: str, units: tuple[str, ...]) -> object:
        try:
            canonical_session = _canonical_session_id(session_id)
            expected = _normalise_units(units)
        except Exception:
            return self._group("UNKNOWN", "runtime_target_invalid")
        with self._lock:
            if self._session_id != canonical_session or self._planned_units != expected:
                return self._group(
                    "UNKNOWN", "runtime_session_changed", uncertain=expected
                )
            outcome, rows, snapshot_reference = self._read_rows(expected)
            if outcome != "OK" or rows is None:
                return self._group(
                    "UNKNOWN", "runtime_status_unknown", uncertain=expected
                )
            running = tuple(name for name in expected if _running_row(rows[name]))
            uncertain = tuple(name for name in expected if name not in running)
            if running == expected:
                return self._group(
                    "APPLIED",
                    "running",
                    started=running,
                    reference=snapshot_reference,
                )
            return self._group(
                "UNKNOWN",
                "runtime_status_not_running",
                started=running,
                uncertain=uncertain,
                reference=snapshot_reference,
            )

    def stop_cooperatively_confirmed(self, session_id: str, unit: str) -> object:
        try:
            canonical_session = _canonical_session_id(session_id)
            target = _normalise_units((unit,))[0]
        except Exception:
            return self._step("UNKNOWN", "runtime_target_invalid")
        with self._lock:
            if (
                self._session_id != canonical_session
                or self._planned_units is None
                or target not in self._planned_units
            ):
                return self._step("UNKNOWN", "runtime_session_changed")
            if target in self._stop_attempted:
                return self._step("UNKNOWN", "runtime_stop_already_attempted")
            self._stop_attempted.add(target)
            command_id = self._command_id(
                canonical_session,
                "iteration72:runtime:cooperative-stop:" + target,
            )
            request = {
                "action": "stop",
                "unit_name": target,
                "parameters": {"cooperative_only": True},
                "command_id": command_id,
                "reason": self._STOP_REASON,
                "all_units": False,
                "force": False,
            }
            try:
                first = self._client.unit_action(confirm=None, **request)
            except Exception:
                return self._step("UNKNOWN", "runtime_stop_unknown")
            first_mapping = _response_mapping(
                first,
                allowed_sensitive_root_keys=frozenset({"confirmation_token"}),
            )
            if first_mapping is None:
                return self._step("UNKNOWN", "runtime_stop_unknown")
            token = self._confirmation_token(
                first_mapping,
                command_id=command_id,
                action="stop",
                all_units=False,
            )
            if token is None or first_mapping.get("unit") != target:
                return self._step("UNKNOWN", "runtime_confirmation_unknown")
            try:
                final = self._client.unit_action(confirm=token, **request)
            except Exception:
                return self._step("UNKNOWN", "runtime_stop_unknown")
            finally:
                token = None
            final_mapping = _response_mapping(final)
            if final_mapping is None:
                return self._step("UNKNOWN", "runtime_stop_unknown")
            result_reference = _reference(
                {
                    "kind": "runtime-cooperative-stop",
                    "command_id": command_id,
                    "unit": target,
                    "status": final_mapping["status"],
                    "code": final_mapping["code"],
                }
            )
            if (
                final_mapping.get("command_id") != command_id
                or final_mapping.get("action") != "stop"
                or final_mapping.get("unit") != target
            ):
                return self._step(
                    "UNKNOWN", "runtime_stop_unknown", reference=result_reference
                )
            if (
                final_mapping["status"] == "APPLIED"
                and final_mapping["code"] == "stopped"
                and final_mapping.get("state") == "STOPPED"
            ):
                outcome, rows, status_reference = self._read_rows(self._planned_units)
                if (
                    outcome != "OK"
                    or rows is None
                    or not _clean_stopped_row(rows[target])
                ):
                    return self._step(
                        "UNKNOWN",
                        "runtime_stop_unknown",
                        reference=result_reference,
                    )
                result_reference = _reference(
                    {
                        "kind": "runtime-cooperative-stop",
                        "command_id": command_id,
                        "unit": target,
                        "status": final_mapping["status"],
                        "code": final_mapping["code"],
                        "status_reference": status_reference,
                    }
                )
                return self._step(
                    "APPLIED",
                    "stopped",
                    reference=result_reference,
                    exit_code=rows[target]["exit_code_observed"],
                )
            if final_mapping["status"] == "REJECTED":
                return self._step(
                    "BLOCKED", "runtime_stop_blocked", reference=result_reference
                )
            return self._step(
                "UNKNOWN", "runtime_stop_unknown", reference=result_reference
            )


class RiskWebHttpPort:
    """One-shot-per-call R-05 reader for the local Risk Web adapter.

    By default (``include_proof=False``), this port requests only
    ``order_cut_admission=true`` and returns ``gateway_reference=None`` and
    ``observation_reference=None`` because that 2-field reply carries no
    source-bound proof.

    When ``include_proof=True``, this port requests
    ``order_cut_admission=true&order_cut_proof=true`` and strictly verifies the
    4-field proof payload returned from that single Risk service call.
    """

    __slots__ = (
        "_origin",
        "_host",
        "_port",
        "_session_token",
        "_gateway_reference",
        "_observation_factory",
        "_timeout",
        "_include_proof",
        "_lock",
    )

    def __init__(
        self,
        *,
        origin: str,
        session_token: str,
        gateway_reference: str,
        observation_factory: Callable[..., object],
        timeout: float = 5.0,
        include_proof: bool = False,
    ) -> None:
        if type(origin) is not str:
            raise ValueError("Risk origin must be loopback HTTP")
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError:
            port = None
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or port is None
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or origin.rstrip("/") != f"http://127.0.0.1:{port}"
        ):
            raise ValueError("Risk origin must be loopback HTTP")
        if (
            type(session_token) is not str
            or _RISK_SESSION_TOKEN.fullmatch(session_token) is None
        ):
            raise ValueError("Risk session token is invalid")
        if type(timeout) not in {int, float} or type(timeout) is bool:
            raise ValueError("Risk timeout is invalid")
        timeout_value = float(timeout)
        if not math.isfinite(timeout_value) or not 0.0 < timeout_value <= 30.0:
            raise ValueError("Risk timeout is invalid")
        if not callable(observation_factory):
            raise TypeError("observation_factory must be callable")
        if type(include_proof) is not bool:
            raise ValueError("include_proof must be bool")
        self._origin = origin.rstrip("/")
        self._host = "127.0.0.1"
        self._port = port
        self._session_token = session_token
        self._gateway_reference = _safe_reference(gateway_reference)
        self._observation_factory = observation_factory
        self._timeout = timeout_value
        self._include_proof = include_proof
        self._lock = Lock()

    def __repr__(self) -> str:
        return (
            f"RiskWebHttpPort(origin={self._origin!r}, "
            f"gateway_reference={self._gateway_reference!r}, "
            f"include_proof={self._include_proof!r})"
        )

    def _observation(
        self,
        checked: bool,
        ready: bool,
        *,
        gateway_reference: str | None,
        reference: str | None,
    ) -> object:
        return self._observation_factory(
            checked,
            ready,
            gateway_reference,
            reference,
        )

    def _unknown(self) -> object:
        return self._observation(False, False, gateway_reference=None, reference=None)

    def observe_r05(self, expected_gateway_reference: str) -> object:
        with self._lock:
            if expected_gateway_reference != self._gateway_reference:
                return self._unknown()
            path = (
                RISK_STATUS_PROOF_PATH if self._include_proof else RISK_STATUS_PATH
            )
            connection = http.client.HTTPConnection(
                self._host, self._port, timeout=self._timeout
            )
            try:
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Accept": "application/json",
                        "Host": f"127.0.0.1:{self._port}",
                        "x-risk-session": self._session_token,
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                body = response.read(MAX_RISK_RESPONSE_BYTES + 1)
                status = int(response.status)
                media_type = (
                    (response.getheader("content-type") or "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
            except Exception:
                return self._unknown()
            finally:
                connection.close()
            if (
                status != 200
                or media_type != "application/json"
                or len(body) > MAX_RISK_RESPONSE_BYTES
            ):
                return self._unknown()
            try:
                decoded = json.loads(
                    body.decode("utf-8", errors="strict"),
                    object_pairs_hook=_pairs_without_duplicates,
                    parse_constant=_reject_constant,
                )
            except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
                return self._unknown()
            if (
                type(decoded) is not dict
                or set(decoded) != {"status", "summary"}
                or decoded.get("status") != "OBSERVED"
                or type(decoded.get("summary")) is not dict
                or not _bounded_json_tree(decoded)
            ):
                return self._unknown()
            result = decoded["summary"].get("order_cut_admission")
            if self._include_proof:
                if (
                    type(result) is not dict
                    or set(result)
                    != {
                        "checked",
                        "ready",
                        "gateway_reference",
                        "observation_reference",
                    }
                    or type(result.get("checked")) is not bool
                    or type(result.get("ready")) is not bool
                    or (result["ready"] and not result["checked"])
                ):
                    return self._unknown()
                gw_ref = result.get("gateway_reference")
                obs_ref = result.get("observation_reference")
                if gw_ref is not None and (
                    type(gw_ref) is not str
                    or _SHA256_REFERENCE.fullmatch(gw_ref) is None
                ):
                    return self._unknown()
                if obs_ref is not None and (
                    type(obs_ref) is not str
                    or _SHA256_REFERENCE.fullmatch(obs_ref) is None
                ):
                    return self._unknown()
                if result["ready"] and (gw_ref is None or obs_ref is None):
                    return self._unknown()
                return self._observation(
                    result["checked"],
                    result["ready"],
                    gateway_reference=gw_ref,
                    reference=obs_ref,
                )
            if (
                type(result) is not dict
                or set(result) != {"checked", "ready"}
                or type(result.get("checked")) is not bool
                or type(result.get("ready")) is not bool
                or (result["ready"] and not result["checked"])
            ):
                return self._unknown()
            return self._observation(
                result["checked"],
                result["ready"],
                gateway_reference=None,
                reference=None,
            )


def _validate_positive_timeout(value: float, *, name: str, maximum: float) -> float:
    if type(value) not in {int, float} or type(value) is bool:
        raise ValueError(f"{name} is invalid")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 < numeric <= maximum:
        raise ValueError(f"{name} is invalid")
    return numeric


def _validate_non_symlink_parent(path: Path) -> bool:
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        return False
    for candidate in (parent, *parent.parents):
        try:
            if candidate.is_symlink():
                return False
        except OSError:
            return False
    return True


def _validate_stop_file_path(stop_file: Path) -> Path:
    resolved_input = Path(stop_file)
    if not resolved_input.is_absolute():
        raise ValueError("stop_file must be an absolute path")
    if _STOP_FILE_NAME.fullmatch(resolved_input.name) is None:
        raise ValueError("stop_file must have a finite safe .stop filename")
    return resolved_input


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    """Explicit single-attempt launch & cooperative-stop contract for one component."""

    argv: tuple[str, ...]
    stop_file: Path
    ready_check: Callable[[], str | bool | None]
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    preflight_check: Callable[[], bool] | None = None
    start_timeout_seconds: float = 30.0
    stop_timeout_seconds: float = 35.0
    poll_interval_seconds: float = 0.05
    protect_stop_file: Callable[[Path], None] | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None

    def __post_init__(self) -> None:
        if isinstance(self.argv, (str, bytes)) or not isinstance(self.argv, Sequence):
            raise ValueError("argv must be a non-empty sequence of strings")
        argv_tuple = tuple(self.argv)
        if not argv_tuple or any(
            type(item) is not str or not item or "\x00" in item for item in argv_tuple
        ):
            raise ValueError("argv must contain non-empty strings without NUL")
        object.__setattr__(self, "argv", argv_tuple)
        object.__setattr__(self, "stop_file", _validate_stop_file_path(self.stop_file))
        if not callable(self.ready_check):
            raise TypeError("ready_check must be callable")
        if self.cwd is not None:
            cwd_path = Path(self.cwd)
            if not cwd_path.is_absolute():
                raise ValueError("cwd must be an absolute path")
            object.__setattr__(self, "cwd", cwd_path)
        if self.env is not None:
            if not isinstance(self.env, Mapping) or any(
                type(k) is not str
                or not k
                or "\x00" in k
                or type(v) is not str
                or "\x00" in v
                for k, v in self.env.items()
            ):
                raise ValueError("env must map non-empty strings to strings")
            object.__setattr__(self, "env", dict(self.env))
        if self.preflight_check is not None and not callable(self.preflight_check):
            raise TypeError("preflight_check must be callable")
        object.__setattr__(
            self,
            "start_timeout_seconds",
            _validate_positive_timeout(
                self.start_timeout_seconds,
                name="start_timeout_seconds",
                maximum=300.0,
            ),
        )
        object.__setattr__(
            self,
            "stop_timeout_seconds",
            _validate_positive_timeout(
                self.stop_timeout_seconds,
                name="stop_timeout_seconds",
                maximum=300.0,
            ),
        )
        object.__setattr__(
            self,
            "poll_interval_seconds",
            _validate_positive_timeout(
                self.poll_interval_seconds,
                name="poll_interval_seconds",
                maximum=5.0,
            ),
        )
        if self.protect_stop_file is not None and not callable(self.protect_stop_file):
            raise TypeError("protect_stop_file must be callable")
        for attr in ("stdout_path", "stderr_path"):
            raw = getattr(self, attr)
            if raw is not None:
                path_val = Path(raw)
                if not path_val.is_absolute():
                    raise ValueError(f"{attr} must be an absolute path")
                object.__setattr__(self, attr, path_val)

    def __repr__(self) -> str:
        return (
            f"ComponentSpec(stop_file_name={self.stop_file.name!r}, "
            f"start_timeout_seconds={self.start_timeout_seconds!r}, "
            f"stop_timeout_seconds={self.stop_timeout_seconds!r})"
        )


class LocalComponentProcessPort:
    """Fail-closed single-attempt ComponentPort using cooperative zero-byte stop markers.

    Each component may be started once and stopped cooperatively once.  Stopping
    writes a new zero-byte ``.stop`` marker via ``O_CREAT | O_EXCL`` and waits
    for the process to exit with code 0.  This adapter never calls
    ``terminate()``, ``kill()``, or OS signals; timeouts and non-zero exits stay
    ``UNKNOWN`` for manual operator observation.
    """

    __slots__ = (
        "_specs",
        "_step_factory",
        "_monotonic",
        "_sleep",
        "_lock",
        "_processes",
        "_log_handles",
        "_start_attempted",
        "_stop_attempted",
    )

    def __init__(
        self,
        specs: Mapping[str, ComponentSpec],
        *,
        step_factory: Callable[..., object],
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(specs, Mapping) or not specs:
            raise ValueError("specs must be a non-empty mapping")
        if not callable(step_factory):
            raise TypeError("step_factory must be callable")
        if not callable(monotonic) or not callable(sleep):
            raise TypeError("monotonic and sleep must be callable")
        validated: dict[str, ComponentSpec] = {}
        for name, spec in specs.items():
            if type(name) is not str or name not in COMPONENT_NAMES:
                raise ValueError(f"unsupported component name: {name!r}")
            if not isinstance(spec, ComponentSpec):
                raise TypeError("each component spec must be a ComponentSpec")
            validated[name] = spec
        self._specs = validated
        self._step_factory = step_factory
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = Lock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._log_handles: dict[str, list[Any]] = {}
        self._start_attempted: set[str] = set()
        self._stop_attempted: set[str] = set()

    def __repr__(self) -> str:
        return f"LocalComponentProcessPort(components={sorted(self._specs)!r})"

    def _step(
        self,
        status: str,
        code: str,
        *,
        reference: str | None = None,
        exit_code: int | None = None,
    ) -> object:
        return self._step_factory(
            status=status,
            code=code,
            reference=reference,
            exit_code=exit_code,
        )

    def _close_handles(self, component: str) -> None:
        handles = self._log_handles.pop(component, [])
        for handle in handles:
            try:
                handle.close()
            except Exception:
                pass

    @staticmethod
    def _stop_file_absent(stop_file: Path) -> bool:
        try:
            os.lstat(stop_file)
            return False
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _create_zero_byte_stop_file(self, spec: ComponentSpec) -> bool:
        if not _validate_non_symlink_parent(spec.stop_file):
            return False
        if not self._stop_file_absent(spec.stop_file):
            return False
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(spec.stop_file, flags, 0o600)
        except OSError:
            return False
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        try:
            meta = os.lstat(spec.stop_file)
            if (
                stat.S_ISLNK(meta.st_mode)
                or not stat.S_ISREG(meta.st_mode)
                or meta.st_size != 0
            ):
                return False
            if spec.protect_stop_file is not None:
                spec.protect_stop_file(spec.stop_file)
        except Exception:
            return False
        return True

    def preflight(self, component: str) -> object:
        with self._lock:
            spec = self._specs.get(component)
            if spec is None:
                return self._step("UNKNOWN", "component_not_configured")
            if not _validate_non_symlink_parent(spec.stop_file):
                return self._step("BLOCKED", "stop_dir_invalid")
            if not self._stop_file_absent(spec.stop_file):
                return self._step("BLOCKED", "stop_marker_present")
            if spec.cwd is not None:
                try:
                    if (
                        not spec.cwd.exists()
                        or not spec.cwd.is_dir()
                        or spec.cwd.is_symlink()
                    ):
                        return self._step("BLOCKED", "cwd_invalid")
                except OSError:
                    return self._step("BLOCKED", "cwd_invalid")
            exe_path = Path(spec.argv[0])
            if exe_path.is_absolute():
                try:
                    if not exe_path.exists() or not exe_path.is_file():
                        return self._step("BLOCKED", "executable_missing")
                except OSError:
                    return self._step("BLOCKED", "executable_missing")
            for log_path in (spec.stdout_path, spec.stderr_path):
                if log_path is not None and not _validate_non_symlink_parent(log_path):
                    return self._step("BLOCKED", "log_dir_invalid")
            if spec.preflight_check is not None:
                try:
                    ok = spec.preflight_check() is True
                except Exception:
                    return self._step("UNKNOWN", "preflight_check_unknown")
                if not ok:
                    return self._step("BLOCKED", "preflight_check_blocked")
            return self._step(
                "APPLIED",
                "ready",
                reference=_reference(
                    {"kind": "component-preflight", "component": component}
                ),
            )

    def start(self, component: str) -> object:
        with self._lock:
            spec = self._specs.get(component)
            if spec is None:
                return self._step("UNKNOWN", "component_not_configured")
            if component in self._start_attempted:
                return self._step("UNKNOWN", "component_start_already_attempted")
            self._start_attempted.add(component)
            if not _validate_non_symlink_parent(spec.stop_file):
                return self._step("BLOCKED", "stop_dir_invalid")
            if not self._stop_file_absent(spec.stop_file):
                return self._step("BLOCKED", "stop_marker_present")
            handles: list[Any] = []
            stdout_target: Any = subprocess.DEVNULL
            stderr_target: Any = subprocess.DEVNULL
            try:
                if spec.stdout_path is not None:
                    stdout_handle = open(spec.stdout_path, "ab")
                    handles.append(stdout_handle)
                    stdout_target = stdout_handle
                if spec.stderr_path is not None:
                    stderr_handle = open(spec.stderr_path, "ab")
                    handles.append(stderr_handle)
                    stderr_target = stderr_handle
                proc = subprocess.Popen(
                    list(spec.argv),
                    shell=False,
                    cwd=str(spec.cwd) if spec.cwd is not None else None,
                    env=dict(spec.env) if spec.env is not None else None,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_target,
                    stderr=stderr_target,
                )
            except Exception:
                for handle in handles:
                    try:
                        handle.close()
                    except Exception:
                        pass
                return self._step("UNKNOWN", "component_spawn_unknown")
            self._processes[component] = proc
            self._log_handles[component] = handles

            deadline = self._monotonic() + spec.start_timeout_seconds
            while True:
                exited = proc.poll()
                if exited is not None:
                    self._close_handles(component)
                    return self._step(
                        "UNKNOWN",
                        "component_exited_early",
                        exit_code=int(exited),
                    )
                try:
                    ready = spec.ready_check()
                except Exception:
                    return self._step("UNKNOWN", "component_ready_check_unknown")
                if component == "gateway":
                    if (
                        type(ready) is str
                        and _SHA256_REFERENCE.fullmatch(ready) is not None
                    ):
                        return self._step("APPLIED", "started", reference=ready)
                else:
                    if (
                        type(ready) is str
                        and _SHA256_REFERENCE.fullmatch(ready) is not None
                    ):
                        return self._step("APPLIED", "started", reference=ready)
                    if ready is True:
                        return self._step(
                            "APPLIED",
                            "started",
                            reference=_reference(
                                {
                                    "kind": "component-start",
                                    "component": component,
                                    "pid": proc.pid,
                                }
                            ),
                        )
                remaining = deadline - self._monotonic()
                if remaining <= 0.0:
                    return self._step("UNKNOWN", "component_start_timeout")
                self._sleep(min(spec.poll_interval_seconds, remaining))

    def stop_cooperatively(self, component: str) -> object:
        with self._lock:
            spec = self._specs.get(component)
            if spec is None:
                return self._step("UNKNOWN", "component_not_configured")
            if component in self._stop_attempted:
                return self._step("UNKNOWN", "component_stop_already_attempted")
            self._stop_attempted.add(component)
            proc = self._processes.get(component)
            if proc is None:
                return self._step("UNKNOWN", "component_not_started")
            observed = proc.poll()
            if observed is not None:
                self._close_handles(component)
                if observed == 0:
                    return self._step(
                        "APPLIED",
                        "stopped",
                        reference=_reference(
                            {
                                "kind": "component-stop",
                                "component": component,
                                "exit_code": 0,
                            }
                        ),
                        exit_code=0,
                    )
                return self._step(
                    "UNKNOWN",
                    "component_already_exited_nonzero",
                    exit_code=int(observed),
                )
            if not self._create_zero_byte_stop_file(spec):
                return self._step("UNKNOWN", "stop_marker_write_failed")
            try:
                exit_code = int(proc.wait(timeout=spec.stop_timeout_seconds))
            except subprocess.TimeoutExpired:
                return self._step("UNKNOWN", "component_stop_timeout")
            except Exception:
                return self._step("UNKNOWN", "component_stop_unknown")
            self._close_handles(component)
            if exit_code == 0:
                return self._step(
                    "APPLIED",
                    "stopped",
                    reference=_reference(
                        {
                            "kind": "component-stop",
                            "component": component,
                            "exit_code": 0,
                        }
                    ),
                    exit_code=0,
                )
            return self._step(
                "UNKNOWN",
                "component_stop_nonzero_exit",
                exit_code=exit_code,
            )
