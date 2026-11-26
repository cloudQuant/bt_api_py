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
import re
from collections.abc import Callable, Mapping, Sequence
from threading import Lock
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid5

CONTROL_RESPONSE_SCHEMA = "bt_api_runtime.control/v1"
RISK_STATUS_PATH = "/api/v1/risk/status?order_cut_admission=true"
MAX_RISK_RESPONSE_BYTES = 64 * 1024

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

    The Risk reply does not contain a Gateway generation reference or a
    source-bound observation reference.  The port therefore returns both
    references as ``None`` even when ``checked`` and ``ready`` are true.  The
    supervisor will keep R-05 UNKNOWN until the Risk service and Web contract
    expose those two proofs from the same service call.
    """

    __slots__ = (
        "_origin",
        "_host",
        "_port",
        "_session_token",
        "_gateway_reference",
        "_observation_factory",
        "_timeout",
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
        self._origin = origin.rstrip("/")
        self._host = "127.0.0.1"
        self._port = port
        self._session_token = session_token
        self._gateway_reference = _safe_reference(gateway_reference)
        self._observation_factory = observation_factory
        self._timeout = timeout_value
        self._lock = Lock()

    def __repr__(self) -> str:
        return (
            f"RiskWebHttpPort(origin={self._origin!r}, "
            f"gateway_reference={self._gateway_reference!r})"
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
            connection = http.client.HTTPConnection(
                self._host, self._port, timeout=self._timeout
            )
            try:
                connection.request(
                    "GET",
                    RISK_STATUS_PATH,
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
