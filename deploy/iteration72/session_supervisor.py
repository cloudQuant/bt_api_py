"""Fail-closed Iteration 72 session orchestration state machine.

This module deliberately contains no HTTP, process, credential, account, or
signal adapter.  Callers must inject already-reviewed ports.  The state
machine records only finite public outcomes and never retries, rolls back, or
escalates an UNKNOWN operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol
from uuid import UUID

SESSION_SCHEMA = "bt_api_py.iteration72.session_supervisor/v1"
RECEIPT_SCHEMA = "bt_api_py.iteration72.session_receipt/v1"

COMPONENT_START_ORDER = (
    "monitor",
    "monitor_query",
    "gateway",
    "gateway_web",
    "risk",
    "risk_web",
    "execution",
    "runtime",
    "web",
)
COMPONENT_STOP_ORDER = tuple(reversed(COMPONENT_START_ORDER))

_STATUSES = frozenset({"INTENT", "APPLIED", "BLOCKED", "UNKNOWN"})
_PHASES = frozenset(
    {
        "NEW",
        "PREFLIGHT_READY",
        "STARTING",
        "RUNNING",
        "BLOCKED",
        "UNKNOWN",
        "STOPPING",
        "STOPPED",
    }
)
_ACTIONS = frozenset(
    {
        "component_preflight",
        "runtime_preflight",
        "component_start",
        "risk_r05",
        "strategy_group_start",
        "strategy_group_status",
        "strategy_stop",
        "component_stop",
        "session_result",
    }
)
_RECEIPT_SUBJECTS = frozenset(
    {
        *COMPONENT_START_ORDER,
        "session",
        "runtime_group",
        "risk_r05_before_execution",
        "risk_r05_before_strategy_group",
        "strategy_unit",
    }
)
_RECEIPT_CODES = frozenset(
    {
        "intent",
        "component_preflight_ready",
        "component_preflight_blocked",
        "component_preflight_unknown",
        "runtime_preflight_ready",
        "runtime_preflight_blocked",
        "runtime_preflight_unknown",
        "component_started",
        "component_start_blocked",
        "component_start_unknown",
        "gateway_reference_missing",
        "risk_r05_ready",
        "risk_r05_unready",
        "risk_r05_unknown",
        "risk_gateway_mismatch",
        "strategy_group_started",
        "strategy_group_start_blocked",
        "strategy_group_start_partial",
        "strategy_group_start_unknown",
        "strategy_group_start_mismatch",
        "strategy_group_running",
        "strategy_group_status_unknown",
        "strategy_group_status_mismatch",
        "strategy_stop_applied",
        "strategy_stop_blocked",
        "strategy_stop_unknown",
        "component_stop_applied",
        "component_stop_blocked",
        "component_stop_unknown",
        "session_preflight_ready",
        "session_blocked",
        "session_ready",
        "session_running",
        "session_unknown",
        "session_stopped",
        "manual_observation_required",
    }
)
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_PORT_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_SHA256_REFERENCE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "session_id",
        "sequence",
        "phase",
        "action",
        "subject",
        "status",
        "code",
        "reference",
        "exit_code",
    }
)


def _canonical_uuid(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("session_id must be a string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("session_id must be a UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise ValueError("session_id must use canonical lowercase UUID form")
    return canonical


def _safe_name(value: str, *, field: str) -> str:
    if not isinstance(value, str) or _SAFE_NAME.fullmatch(value) is None:
        raise ValueError(f"{field} must be a finite public name")
    return value


def _safe_reference(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256_REFERENCE.fullmatch(value) is None:
        raise ValueError("reference must be a sha256 digest reference")
    return value


def _safe_exit_code(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("exit_code must be an integer or None")
    if not -(2**31) <= value < 2**31:
        raise ValueError("exit_code is outside the signed 32-bit range")
    return value


def _normalise_units(values: Sequence[str], *, allow_empty: bool) -> tuple[str, ...]:
    units = tuple(_safe_name(value, field="unit") for value in values)
    if not allow_empty and not units:
        raise ValueError("at least one strategy unit is required")
    if len(set(units)) != len(units):
        raise ValueError("strategy units must be unique")
    return tuple(sorted(units))


def _target_reference(units: Sequence[str]) -> str:
    payload = "\n".join(units).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _unit_reference(unit: str) -> str:
    payload = b"strategy-unit\0" + unit.encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _json_object_without_duplicates(
    pairs: list[tuple[str, object]]
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


@dataclass(frozen=True, slots=True)
class StepResult:
    """Finite result returned by an injected single-target port."""

    status: str
    code: str
    reference: str | None = None
    exit_code: int | None = None

    def __post_init__(self) -> None:
        if self.status not in {"APPLIED", "BLOCKED", "UNKNOWN"}:
            raise ValueError("invalid step status")
        if (
            not isinstance(self.code, str)
            or _SAFE_PORT_CODE.fullmatch(self.code) is None
        ):
            raise ValueError("invalid finite port code")
        _safe_reference(self.reference)
        _safe_exit_code(self.exit_code)


@dataclass(frozen=True, slots=True)
class GroupResult:
    """Finite group result; unit sets are checked again against the plan."""

    status: str
    code: str
    started_units: tuple[str, ...] = ()
    not_started_units: tuple[str, ...] = ()
    uncertain_units: tuple[str, ...] = ()
    reference: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"APPLIED", "BLOCKED", "UNKNOWN"}:
            raise ValueError("invalid group status")
        if (
            not isinstance(self.code, str)
            or _SAFE_PORT_CODE.fullmatch(self.code) is None
        ):
            raise ValueError("invalid finite group code")
        groups = []
        for field_name in ("started_units", "not_started_units", "uncertain_units"):
            value = _normalise_units(getattr(self, field_name), allow_empty=True)
            object.__setattr__(self, field_name, value)
            groups.append(set(value))
        if groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2]:
            raise ValueError("group unit classifications must be disjoint")
        _safe_reference(self.reference)


@dataclass(frozen=True, slots=True)
class RiskGateObservation:
    """Point-in-time R-05 observation tied to the active Gateway digest."""

    checked: bool
    ready: bool
    gateway_reference: str | None
    observation_reference: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.checked, bool) or not isinstance(self.ready, bool):
            raise TypeError("checked and ready must be booleans")
        if self.ready and not self.checked:
            raise ValueError("an unchecked R-05 observation cannot be ready")
        _safe_reference(self.gateway_reference)
        _safe_reference(self.observation_reference)


@dataclass(frozen=True, slots=True)
class SessionPlan:
    """Immutable public plan for one supervisor attempt."""

    session_id: str
    units: tuple[str, ...]
    web_runtime_control_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _canonical_uuid(self.session_id))
        object.__setattr__(
            self, "units", _normalise_units(self.units, allow_empty=False)
        )
        if not isinstance(self.web_runtime_control_enabled, bool):
            raise TypeError("web_runtime_control_enabled must be bool")
        if self.web_runtime_control_enabled:
            raise ValueError(
                "Iteration 72 Web Runtime write control must remain disabled"
            )


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    exit_code: int
    status: str
    code: str
    phase: str

    def __post_init__(self) -> None:
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise TypeError("session exit_code must be an integer")
        if self.exit_code not in {0, 2, 3}:
            raise ValueError("session exit_code must be 0, 2, or 3")
        if self.status not in {"APPLIED", "BLOCKED", "UNKNOWN"}:
            raise ValueError("invalid session outcome status")
        if self.code not in _RECEIPT_CODES:
            raise ValueError("invalid session outcome code")
        if self.phase not in _PHASES:
            raise ValueError("invalid session phase")


@dataclass(frozen=True, slots=True)
class ReceiptEvent:
    schema: str
    session_id: str
    sequence: int
    phase: str
    action: str
    subject: str
    status: str
    code: str
    reference: str | None = None
    exit_code: int | None = None

    def __post_init__(self) -> None:
        if self.schema != RECEIPT_SCHEMA:
            raise ValueError("invalid receipt schema")
        _canonical_uuid(self.session_id)
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("receipt sequence must be a positive integer")
        if self.phase not in _PHASES:
            raise ValueError("invalid receipt phase")
        if self.action not in _ACTIONS:
            raise ValueError("invalid receipt action")
        if self.subject not in _RECEIPT_SUBJECTS:
            raise ValueError("invalid receipt subject")
        if self.status not in _STATUSES:
            raise ValueError("invalid receipt status")
        if self.code not in _RECEIPT_CODES:
            raise ValueError("invalid receipt code")
        _safe_reference(self.reference)
        _safe_exit_code(self.exit_code)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ReceiptEvent:
        if frozenset(value) != _RECEIPT_KEYS:
            raise ValueError("receipt row has an unexpected field set")
        return cls(
            schema=value["schema"],  # type: ignore[arg-type]
            session_id=value["session_id"],  # type: ignore[arg-type]
            sequence=value["sequence"],  # type: ignore[arg-type]
            phase=value["phase"],  # type: ignore[arg-type]
            action=value["action"],  # type: ignore[arg-type]
            subject=value["subject"],  # type: ignore[arg-type]
            status=value["status"],  # type: ignore[arg-type]
            code=value["code"],  # type: ignore[arg-type]
            reference=value["reference"],  # type: ignore[arg-type]
            exit_code=value["exit_code"],  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "phase": self.phase,
            "action": self.action,
            "subject": self.subject,
            "status": self.status,
            "code": self.code,
            "reference": self.reference,
            "exit_code": self.exit_code,
        }


class ReceiptPort(Protocol):
    @property
    def next_sequence(self) -> int:
        ...

    def append(self, event: ReceiptEvent) -> None:
        ...


class ComponentPort(Protocol):
    def preflight(self, component: str) -> StepResult:
        ...

    def start(self, component: str) -> StepResult:
        ...

    def stop_cooperatively(self, component: str) -> StepResult:
        ...


class RuntimePort(Protocol):
    def preflight_target_set(self, units: tuple[str, ...]) -> StepResult:
        ...

    def start_all_confirmed(
        self, session_id: str, units: tuple[str, ...]
    ) -> GroupResult:
        ...

    def status_group(self, session_id: str, units: tuple[str, ...]) -> GroupResult:
        ...

    def stop_cooperatively_confirmed(self, session_id: str, unit: str) -> StepResult:
        ...


class RiskGatePort(Protocol):
    def observe_r05(self, expected_gateway_reference: str) -> RiskGateObservation:
        ...


class _ReceiptWriteError(RuntimeError):
    """Internal control-flow marker after receipt custody becomes uncertain."""


class JsonlReceipt:
    """Strict single-owner append-only JSONL receipt.

    Existing rows are fully revalidated.  Each append checks the exact prior
    file identity, size, and digest before O_APPEND.  Concurrent writers are
    not merged: a detected change fails closed and requires manual recovery.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        session_id: str,
        *,
        max_bytes: int = 1_048_576,
    ) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("receipt path must be absolute")
        self.session_id = _canonical_uuid(session_id)
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes < 1024
        ):
            raise ValueError("max_bytes must be an integer of at least 1024")
        self.max_bytes = max_bytes
        self._lock = Lock()
        self._owner_path = self.path.with_name(f"{self.path.name}.owner")
        self._owner_fd: int | None = None
        self._owner_identity: tuple[int, int, int] | None = None
        self._owner_pid = os.getpid()
        self._closed = False
        self._validate_parent()
        self._claim_owner()
        try:
            existing = self._read_existing()
            self._size = len(existing)
            self._digest = hashlib.sha256(existing).digest()
            self._identity = self._path_identity()
            self._next_sequence = self._validate_rows(existing)
        except BaseException:
            with suppress(Exception):
                self.close()
            raise

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    def __enter__(self) -> JsonlReceipt:
        if self._closed:
            raise RuntimeError("receipt owner is closed")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()

    def _validate_parent(self) -> None:
        if not self.path.parent.exists() or not self.path.parent.is_dir():
            raise ValueError("receipt parent directory must already exist")
        for candidate in (self.path.parent, *self.path.parent.parents):
            if candidate.is_symlink():
                raise ValueError("receipt path may not traverse a symlink")

    def _path_identity(self) -> tuple[int, int, int] | None:
        try:
            metadata = os.lstat(self.path)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("receipt must be a regular non-symlink file")
        return (metadata.st_dev, metadata.st_ino, metadata.st_ctime_ns)

    def _owner_path_identity(self) -> tuple[int, int, int] | None:
        try:
            metadata = os.lstat(self._owner_path)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("receipt owner claim is not a regular file")
        return self._descriptor_identity(metadata)

    def _claim_owner(self) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._owner_path, flags, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(
                "receipt owner claim already exists; manual recovery is required"
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("receipt owner descriptor is not a regular file")
            os.fsync(descriptor)
            self._owner_fd = descriptor
            self._owner_identity = self._descriptor_identity(metadata)
        except BaseException:
            os.close(descriptor)
            with suppress(OSError):
                os.unlink(self._owner_path)
            raise

    def _verify_owner(self) -> None:
        if self._closed or self._owner_fd is None or self._owner_identity is None:
            raise RuntimeError("receipt owner claim is not active")
        if os.getpid() != self._owner_pid:
            raise RuntimeError("receipt owner claim cannot be used by a forked process")
        descriptor_identity = self._descriptor_identity(os.fstat(self._owner_fd))
        if descriptor_identity != self._owner_identity:
            raise RuntimeError("receipt owner descriptor identity changed")
        if self._owner_path_identity() != self._owner_identity:
            raise RuntimeError("receipt owner path identity changed")

    def close(self) -> None:
        if not hasattr(self, "_lock"):
            return
        if os.getpid() != self._owner_pid:
            descriptor = self._owner_fd
            self._owner_fd = None
            self._owner_identity = None
            self._closed = True
            if descriptor is not None:
                os.close(descriptor)
            return
        with self._lock:
            if self._closed:
                return
            self._closed = True
            descriptor = self._owner_fd
            identity = self._owner_identity
            self._owner_fd = None
            self._owner_identity = None
            if descriptor is None or identity is None:
                return
            descriptor_identity = self._descriptor_identity(os.fstat(descriptor))
            os.close(descriptor)
            if (
                descriptor_identity != identity
                or self._owner_path_identity() != identity
            ):
                raise RuntimeError("receipt owner claim changed before close")
            os.unlink(self._owner_path)

    def _read_existing(self) -> bytes:
        identity = self._path_identity()
        if identity is None:
            return b""
        data = self.path.read_bytes()
        if len(data) > self.max_bytes:
            raise ValueError("existing receipt exceeds max_bytes")
        if self._path_identity() != identity:
            raise RuntimeError("receipt identity changed while reading")
        return data

    def _validate_rows(self, data: bytes) -> int:
        if not data:
            return 1
        if not data.endswith(b"\n"):
            raise ValueError("receipt must end on a complete JSONL row")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("receipt is not strict UTF-8") from exc
        next_sequence = 1
        for raw_line in text.splitlines():
            try:
                decoded = json.loads(
                    raw_line,
                    object_pairs_hook=_json_object_without_duplicates,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError("receipt contains invalid JSON") from exc
            if not isinstance(decoded, dict):
                raise ValueError("receipt row must be an object")
            event = ReceiptEvent.from_mapping(decoded)
            if event.session_id != self.session_id or event.sequence != next_sequence:
                raise ValueError("receipt session or sequence mismatch")
            next_sequence += 1
        return next_sequence

    @staticmethod
    def _descriptor_identity(metadata: os.stat_result) -> tuple[int, int, int]:
        return (metadata.st_dev, metadata.st_ino, metadata.st_ctime_ns)

    def append(self, event: ReceiptEvent) -> None:
        if not isinstance(event, ReceiptEvent):
            raise TypeError("event must be ReceiptEvent")
        if event.session_id != self.session_id or event.sequence != self._next_sequence:
            raise ValueError("receipt append session or sequence mismatch")
        payload = (
            json.dumps(
                event.to_mapping(),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if self._size + len(payload) > self.max_bytes:
            raise ValueError("receipt append would exceed max_bytes")

        with self._lock:
            self._verify_owner()
            prior_identity = self._path_identity()
            if prior_identity != self._identity:
                raise RuntimeError("receipt identity changed before append")
            flags = os.O_RDWR | os.O_APPEND
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            if prior_identity is None:
                flags |= os.O_CREAT | os.O_EXCL
            descriptor = os.open(self.path, flags, 0o600)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise RuntimeError("receipt descriptor is not a regular file")
                descriptor_identity = self._descriptor_identity(metadata)
                if prior_identity is not None and descriptor_identity != prior_identity:
                    raise RuntimeError("receipt descriptor identity mismatch")
                os.lseek(descriptor, 0, os.SEEK_SET)
                chunks: list[bytes] = []
                remaining = self.max_bytes + 1
                while remaining:
                    chunk = os.read(descriptor, min(65_536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                current = b"".join(chunks)
                if (
                    len(current) != self._size
                    or hashlib.sha256(current).digest() != self._digest
                ):
                    raise RuntimeError("receipt content changed before append")
                offset = 0
                while offset < len(payload):
                    written = os.write(descriptor, payload[offset:])
                    if written <= 0:
                        raise RuntimeError("receipt append made no progress")
                    offset += written
                os.fsync(descriptor)
                metadata = os.fstat(descriptor)
                if metadata.st_size != self._size + len(payload):
                    raise RuntimeError("receipt append size mismatch")
                new_identity = self._descriptor_identity(metadata)
            finally:
                os.close(descriptor)

            self._size += len(payload)
            self._digest = hashlib.sha256(current + payload).digest()
            self._identity = new_identity
            self._next_sequence += 1


class SessionSupervisor:
    """One-shot fail-closed supervisor for an injected Iteration 72 session."""

    def __init__(
        self,
        plan: SessionPlan,
        *,
        components: ComponentPort,
        runtime: RuntimePort,
        risk: RiskGatePort,
        receipt: ReceiptPort,
    ) -> None:
        if receipt.next_sequence != 1:
            raise ValueError(
                "existing receipt requires manual recovery; automatic resume is forbidden"
            )
        self.plan = plan
        self.components = components
        self.runtime = runtime
        self.risk = risk
        self.receipt = receipt
        self.phase = "NEW"
        self._sequence = 1
        self._preflight_attempted = False
        self._start_attempted = False
        self._stop_attempted = False
        self._uncertain = False
        self._group_started = False
        self._started_components: list[str] = []
        self._gateway_reference: str | None = None
        self._last_outcome: SessionOutcome | None = None
        self._target_reference = _target_reference(plan.units)

    def _event(
        self,
        *,
        action: str,
        subject: str,
        status: str,
        code: str,
        reference: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        event = ReceiptEvent(
            schema=RECEIPT_SCHEMA,
            session_id=self.plan.session_id,
            sequence=self._sequence,
            phase=self.phase,
            action=action,
            subject=subject,
            status=status,
            code=code,
            reference=reference,
            exit_code=exit_code,
        )
        try:
            self.receipt.append(event)
        except Exception as exc:
            self._uncertain = True
            self.phase = "UNKNOWN"
            self._last_outcome = SessionOutcome(
                exit_code=3,
                status="UNKNOWN",
                code="manual_observation_required",
                phase="UNKNOWN",
            )
            raise _ReceiptWriteError from exc
        self._sequence += 1

    def _guard_receipt(self, operation) -> SessionOutcome:
        try:
            return operation()
        except _ReceiptWriteError:
            if self._last_outcome is None:
                raise RuntimeError("receipt failure did not seal the session") from None
            return self._last_outcome

    def _outcome(self, *, status: str, code: str, exit_code: int) -> SessionOutcome:
        outcome = SessionOutcome(
            exit_code=exit_code, status=status, code=code, phase=self.phase
        )
        self._event(
            action="session_result",
            subject="session",
            status=status,
            code=code,
            exit_code=exit_code,
        )
        self._last_outcome = outcome
        return outcome

    def _blocked(self, code: str = "session_blocked") -> SessionOutcome:
        self.phase = "BLOCKED"
        return self._outcome(status="BLOCKED", code=code, exit_code=2)

    def _unknown(self, code: str = "session_unknown") -> SessionOutcome:
        self._uncertain = True
        self.phase = "UNKNOWN"
        return self._outcome(status="UNKNOWN", code=code, exit_code=3)

    @staticmethod
    def _step_class(result: object, *, success_code: str) -> str:
        if not isinstance(result, StepResult):
            return "UNKNOWN"
        if result.status == "APPLIED" and result.code == success_code:
            return "APPLIED"
        if result.status == "BLOCKED":
            return "BLOCKED"
        return "UNKNOWN"

    def preflight(self) -> SessionOutcome:
        return self._guard_receipt(self._preflight)

    def _preflight(self) -> SessionOutcome:
        if self._preflight_attempted:
            if self._last_outcome is None:
                raise RuntimeError("preflight state is inconsistent")
            return self._last_outcome
        if self.phase != "NEW":
            raise RuntimeError("preflight is valid only from NEW")
        self._preflight_attempted = True

        for component in COMPONENT_START_ORDER:
            self._event(
                action="component_preflight",
                subject=component,
                status="INTENT",
                code="intent",
            )
            try:
                result = self.components.preflight(component)
            except Exception:
                result = None
            classification = self._step_class(result, success_code="ready")
            if classification == "APPLIED":
                self._event(
                    action="component_preflight",
                    subject=component,
                    status="APPLIED",
                    code="component_preflight_ready",
                    reference=result.reference,  # type: ignore[union-attr]
                )
                continue
            if classification == "BLOCKED":
                self._event(
                    action="component_preflight",
                    subject=component,
                    status="BLOCKED",
                    code="component_preflight_blocked",
                )
                return self._blocked()
            self._event(
                action="component_preflight",
                subject=component,
                status="UNKNOWN",
                code="component_preflight_unknown",
            )
            return self._unknown()

        self._event(
            action="runtime_preflight",
            subject="runtime_group",
            status="INTENT",
            code="intent",
            reference=self._target_reference,
        )
        try:
            runtime_result = self.runtime.preflight_target_set(self.plan.units)
        except Exception:
            runtime_result = None
        classification = self._step_class(runtime_result, success_code="ready")
        if classification == "BLOCKED":
            self._event(
                action="runtime_preflight",
                subject="runtime_group",
                status="BLOCKED",
                code="runtime_preflight_blocked",
                reference=self._target_reference,
            )
            return self._blocked()
        if classification != "APPLIED":
            self._event(
                action="runtime_preflight",
                subject="runtime_group",
                status="UNKNOWN",
                code="runtime_preflight_unknown",
                reference=self._target_reference,
            )
            return self._unknown()
        self._event(
            action="runtime_preflight",
            subject="runtime_group",
            status="APPLIED",
            code="runtime_preflight_ready",
            reference=self._target_reference,
        )
        self.phase = "PREFLIGHT_READY"
        return self._outcome(
            status="APPLIED", code="session_preflight_ready", exit_code=0
        )

    def _observe_r05(self, subject: str) -> SessionOutcome | None:
        if self._gateway_reference is None:
            return self._unknown()
        self._event(action="risk_r05", subject=subject, status="INTENT", code="intent")
        try:
            observation = self.risk.observe_r05(self._gateway_reference)
        except Exception:
            observation = None
        if not isinstance(observation, RiskGateObservation) or not observation.checked:
            self._event(
                action="risk_r05",
                subject=subject,
                status="UNKNOWN",
                code="risk_r05_unknown",
            )
            return self._unknown()
        if observation.gateway_reference != self._gateway_reference:
            self._event(
                action="risk_r05",
                subject=subject,
                status="UNKNOWN",
                code="risk_gateway_mismatch",
                reference=observation.observation_reference,
            )
            return self._unknown()
        if not observation.ready:
            self._event(
                action="risk_r05",
                subject=subject,
                status="BLOCKED",
                code="risk_r05_unready",
                reference=observation.observation_reference,
            )
            return self._blocked()
        if observation.observation_reference is None:
            self._event(
                action="risk_r05",
                subject=subject,
                status="UNKNOWN",
                code="risk_r05_unknown",
            )
            return self._unknown()
        self._event(
            action="risk_r05",
            subject=subject,
            status="APPLIED",
            code="risk_r05_ready",
            reference=observation.observation_reference,
        )
        return None

    def start_session(self) -> SessionOutcome:
        return self._guard_receipt(self._start_session)

    def _start_session(self) -> SessionOutcome:
        if self._start_attempted:
            if self._last_outcome is None:
                raise RuntimeError("start state is inconsistent")
            return self._last_outcome
        self._start_attempted = True
        if self.phase == "NEW":
            result = self.preflight()
            if result.code != "session_preflight_ready":
                return result
        if self.phase != "PREFLIGHT_READY":
            if self._last_outcome is None:
                raise RuntimeError("session is not startable")
            return self._last_outcome

        self.phase = "STARTING"
        for component in COMPONENT_START_ORDER:
            if component == "execution":
                blocked = self._observe_r05("risk_r05_before_execution")
                if blocked is not None:
                    return blocked
            self._event(
                action="component_start",
                subject=component,
                status="INTENT",
                code="intent",
            )
            try:
                result = self.components.start(component)
            except Exception:
                result = None
            classification = self._step_class(result, success_code="started")
            if classification == "BLOCKED":
                self._event(
                    action="component_start",
                    subject=component,
                    status="BLOCKED",
                    code="component_start_blocked",
                )
                return self._blocked()
            if classification != "APPLIED":
                self._event(
                    action="component_start",
                    subject=component,
                    status="UNKNOWN",
                    code="component_start_unknown",
                )
                return self._unknown()
            if component == "gateway":
                if result.reference is None:  # type: ignore[union-attr]
                    self._event(
                        action="component_start",
                        subject=component,
                        status="UNKNOWN",
                        code="gateway_reference_missing",
                    )
                    return self._unknown()
                self._gateway_reference = result.reference  # type: ignore[union-attr]
            self._started_components.append(component)
            self._event(
                action="component_start",
                subject=component,
                status="APPLIED",
                code="component_started",
                reference=result.reference,  # type: ignore[union-attr]
                exit_code=result.exit_code,  # type: ignore[union-attr]
            )

        blocked = self._observe_r05("risk_r05_before_strategy_group")
        if blocked is not None:
            return blocked

        self._event(
            action="strategy_group_start",
            subject="runtime_group",
            status="INTENT",
            code="intent",
            reference=self._target_reference,
        )
        try:
            group = self.runtime.start_all_confirmed(
                self.plan.session_id, self.plan.units
            )
        except Exception:
            group = None

        expected = self.plan.units
        if (
            isinstance(group, GroupResult)
            and group.status == "APPLIED"
            and group.code == "started"
            and group.started_units == expected
            and not group.not_started_units
            and not group.uncertain_units
        ):
            self._group_started = True
            self._event(
                action="strategy_group_start",
                subject="runtime_group",
                status="APPLIED",
                code="strategy_group_started",
                reference=group.reference or self._target_reference,
            )
            self.phase = "RUNNING"
            return self._outcome(status="APPLIED", code="session_ready", exit_code=0)

        if (
            isinstance(group, GroupResult)
            and group.status == "BLOCKED"
            and not group.started_units
            and group.not_started_units == expected
            and not group.uncertain_units
        ):
            self._event(
                action="strategy_group_start",
                subject="runtime_group",
                status="BLOCKED",
                code="strategy_group_start_blocked",
                reference=group.reference,
            )
            return self._blocked()

        if isinstance(group, GroupResult) and (
            group.started_units or group.not_started_units or group.uncertain_units
        ):
            code = "strategy_group_start_partial"
        elif isinstance(group, GroupResult) and group.status == "APPLIED":
            code = "strategy_group_start_mismatch"
        else:
            code = "strategy_group_start_unknown"
        self._event(
            action="strategy_group_start",
            subject="runtime_group",
            status="UNKNOWN",
            code=code,
            reference=group.reference if isinstance(group, GroupResult) else None,
        )
        return self._unknown()

    def status(self) -> SessionOutcome:
        return self._guard_receipt(self._status)

    def _status(self) -> SessionOutcome:
        if self.phase == "UNKNOWN" or self._uncertain:
            if self._last_outcome is None:
                raise RuntimeError("unknown state has no outcome")
            return self._last_outcome
        if self.phase != "RUNNING":
            if self._last_outcome is None:
                raise RuntimeError("status is available only for a started session")
            return self._last_outcome
        self._event(
            action="strategy_group_status",
            subject="runtime_group",
            status="INTENT",
            code="intent",
            reference=self._target_reference,
        )
        try:
            group = self.runtime.status_group(self.plan.session_id, self.plan.units)
        except Exception:
            group = None
        if (
            isinstance(group, GroupResult)
            and group.status == "APPLIED"
            and group.code == "running"
            and group.started_units == self.plan.units
            and not group.not_started_units
            and not group.uncertain_units
        ):
            self._event(
                action="strategy_group_status",
                subject="runtime_group",
                status="APPLIED",
                code="strategy_group_running",
                reference=group.reference or self._target_reference,
            )
            return self._outcome(status="APPLIED", code="session_running", exit_code=0)
        code = (
            "strategy_group_status_mismatch"
            if isinstance(group, GroupResult) and group.status == "APPLIED"
            else "strategy_group_status_unknown"
        )
        self._event(
            action="strategy_group_status",
            subject="runtime_group",
            status="UNKNOWN",
            code=code,
            reference=group.reference if isinstance(group, GroupResult) else None,
        )
        return self._unknown()

    def stop_session(self) -> SessionOutcome:
        return self._guard_receipt(self._stop_session)

    def _stop_session(self) -> SessionOutcome:
        if self._stop_attempted:
            if self._last_outcome is None:
                raise RuntimeError("stop state is inconsistent")
            return self._last_outcome
        if self.phase == "UNKNOWN" or self._uncertain:
            self._stop_attempted = True
            self.phase = "UNKNOWN"
            return self._outcome(
                status="UNKNOWN",
                code="manual_observation_required",
                exit_code=3,
            )
        if self.phase not in {"RUNNING", "BLOCKED", "PREFLIGHT_READY"}:
            if self._last_outcome is None:
                raise RuntimeError("session cannot be stopped from this phase")
            return self._last_outcome

        self._stop_attempted = True
        self.phase = "STOPPING"
        if self._group_started:
            for unit in reversed(self.plan.units):
                unit_reference = _unit_reference(unit)
                self._event(
                    action="strategy_stop",
                    subject="strategy_unit",
                    status="INTENT",
                    code="intent",
                    reference=unit_reference,
                )
                try:
                    result = self.runtime.stop_cooperatively_confirmed(
                        self.plan.session_id, unit
                    )
                except Exception:
                    result = None
                classification = self._step_class(result, success_code="stopped")
                if classification == "BLOCKED":
                    self._event(
                        action="strategy_stop",
                        subject="strategy_unit",
                        status="BLOCKED",
                        code="strategy_stop_blocked",
                        reference=unit_reference,
                    )
                    return self._blocked()
                if classification != "APPLIED":
                    self._event(
                        action="strategy_stop",
                        subject="strategy_unit",
                        status="UNKNOWN",
                        code="strategy_stop_unknown",
                        reference=unit_reference,
                    )
                    return self._unknown()
                self._event(
                    action="strategy_stop",
                    subject="strategy_unit",
                    status="APPLIED",
                    code="strategy_stop_applied",
                    reference=unit_reference,
                    exit_code=result.exit_code,  # type: ignore[union-attr]
                )

        started = set(self._started_components)
        for component in COMPONENT_STOP_ORDER:
            if component not in started:
                continue
            self._event(
                action="component_stop",
                subject=component,
                status="INTENT",
                code="intent",
            )
            try:
                result = self.components.stop_cooperatively(component)
            except Exception:
                result = None
            classification = self._step_class(result, success_code="stopped")
            if classification == "BLOCKED":
                self._event(
                    action="component_stop",
                    subject=component,
                    status="BLOCKED",
                    code="component_stop_blocked",
                )
                return self._blocked()
            if classification != "APPLIED":
                self._event(
                    action="component_stop",
                    subject=component,
                    status="UNKNOWN",
                    code="component_stop_unknown",
                )
                return self._unknown()
            self._event(
                action="component_stop",
                subject=component,
                status="APPLIED",
                code="component_stop_applied",
                reference=result.reference,  # type: ignore[union-attr]
                exit_code=result.exit_code,  # type: ignore[union-attr]
            )

        self.phase = "STOPPED"
        return self._outcome(status="APPLIED", code="session_stopped", exit_code=0)
