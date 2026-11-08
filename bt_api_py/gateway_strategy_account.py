"""Account observation on an existing threaded strategy runtime.

All service access remains in ServiceAccountClient on the runtime's owner
thread. Returned views are observations, not durable readiness or authority.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.wire_codec_v1 import (
    account_observation_from_typed_map,
    account_observation_to_typed_map,
)

from bt_api_py.service_account import AccountStateView, ServiceAccountClient, ServiceAccountError
from bt_api_py.service_runtime_thread import (
    OwnerOperationError,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)

_MAX_RESULT_BYTES = 8 * 1024 * 1024
_SNAPSHOT_OPERATIONS = frozenset({"account.snapshot.typed"})
_VIEW_FIELDS = frozenset(
    {
        "schema_version",
        "observation",
        "cursor",
        "snapshot_id",
        "session_generation",
        "received_at_monotonic",
        "fresh_until_monotonic",
    }
)


def _ref(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 256
    except UnicodeError:
        return False


def _measure(value: object) -> None:
    remaining = [100_000]
    active = set()

    def check(item: object, depth: int) -> None:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 32:
            raise ValueError
        if item is None or type(item) in (bool, int, str):
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError
            return
        if type(item) not in (dict, list) or id(item) in active:
            raise ValueError
        active.add(id(item))
        try:
            if type(item) is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError
                    check(key, depth + 1)
                    check(child, depth + 1)
            else:
                for child in item:
                    check(child, depth + 1)
        finally:
            active.remove(id(item))

    try:
        check(value, 0)
        encoded = json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode("ascii")
        if len(encoded) > _MAX_RESULT_BYTES:
            raise ValueError
    except Exception:
        raise OwnerOperationError("INVALID_ACCOUNT_VIEW") from None


@dataclass(frozen=True)
class GatewayAccountClientSettings:
    expected_account: AccountKey
    required_coverage: tuple[AccountResourceKey, ...]
    requested_operations: frozenset[str]
    require_open_orders: bool = False

    def __post_init__(self) -> None:
        try:
            if (
                type(self.expected_account) is not AccountKey
                or type(self.required_coverage) is not tuple
                or not self.required_coverage
                or any(type(item) is not AccountResourceKey for item in self.required_coverage)
                or len(set(self.required_coverage)) != len(self.required_coverage)
                or type(self.requested_operations) is not frozenset
                or self.requested_operations != _SNAPSHOT_OPERATIONS
                or type(self.require_open_orders) is not bool
            ):
                raise ValueError
            account = AccountKey.from_dict(self.expected_account.to_dict())
            coverage = tuple(
                AccountResourceKey.from_dict(item.to_dict()) for item in self.required_coverage
            )
            object.__setattr__(self, "expected_account", account)
            object.__setattr__(self, "required_coverage", coverage)
            object.__setattr__(self, "requested_operations", frozenset(self.requested_operations))
        except Exception:
            raise ValueError("INVALID_ACCOUNT_SETTINGS") from None


def _encode_view(view: AccountStateView) -> dict[str, Any]:
    try:
        if type(view) is not AccountStateView:
            raise ValueError
        result = {
            "schema_version": 1,
            "observation": account_observation_to_typed_map(view.observation),
            "cursor": view.cursor,
            "snapshot_id": view.snapshot_id,
            "session_generation": view.session_generation,
            "received_at_monotonic": view.received_at_monotonic,
            "fresh_until_monotonic": view.fresh_until_monotonic,
        }
        # Validate the wrapper independently of the original facade.
        _decode_view(result, check_fresh=False)
        return result
    except OwnerOperationError:
        raise
    except Exception:
        raise OwnerOperationError("INVALID_ACCOUNT_VIEW") from None


def _decode_view(raw: object, *, check_fresh: bool) -> AccountStateView:
    try:
        if (
            type(raw) is not dict
            or set(raw) != _VIEW_FIELDS
            or type(raw["schema_version"]) is not int
            or raw["schema_version"] != 1
        ):
            raise ValueError
        _measure(raw)
        cursor, snapshot, generation = raw["cursor"], raw["snapshot_id"], raw["session_generation"]
        received, until = raw["received_at_monotonic"], raw["fresh_until_monotonic"]
        if (
            type(cursor) is not int
            or not 0 <= cursor < 2**63
            or not _ref(snapshot)
            or not _ref(generation)
            or type(received) not in (int, float)
            or type(until) not in (int, float)
            or not math.isfinite(received)
            or not math.isfinite(until)
            or until <= received
        ):
            raise ValueError
        observation = account_observation_from_typed_map(raw["observation"])
        if check_fresh and time.monotonic() >= until:
            raise OwnerOperationError("STALE_ACCOUNT_STATE")
        return AccountStateView(observation, cursor, snapshot, generation, received, until)
    except OwnerOperationError:
        raise
    except Exception:
        raise OwnerOperationError("INVALID_ACCOUNT_VIEW") from None


class GatewayAccountOwner:
    """Trusted account facade lifecycle, confined to the shared owner thread."""

    def __init__(
        self,
        settings: GatewayAccountClientSettings,
        *,
        account_ref: str,
        scope_ref: str,
        mount: str = "store.account",
    ) -> None:
        if type(settings) is not GatewayAccountClientSettings or not all(
            _ref(x) for x in (account_ref, scope_ref, mount)
        ):
            raise ValueError("INVALID_ACCOUNT_CONFIGURATION")
        self._settings = GatewayAccountClientSettings(
            settings.expected_account,
            settings.required_coverage,
            settings.requested_operations,
            settings.require_open_orders,
        )
        self._account_ref, self._scope_ref, self._mount = account_ref, scope_ref, mount
        self._pid = os.getpid()
        self._thread = None
        self._client = None
        self._closed = False

    @property
    def cleanup_uncertainties(self) -> int:
        return 0

    def _check(self) -> None:
        if os.getpid() != self._pid:
            raise OwnerOperationError("WRONG_PROCESS")
        if self._thread is not None and threading.current_thread() is not self._thread:
            raise OwnerOperationError("WRONG_OWNER_THREAD")

    def start(self, raw_runtime: object) -> None:
        self._check()
        if self._closed or self._client is not None:
            raise OwnerOperationError("ACCOUNT_CLIENT_NOT_NEW")
        self._thread = threading.current_thread()
        try:
            self._client = ServiceAccountClient(
                raw_runtime,
                self._mount,
                account_ref=self._account_ref,
                expected_account=self._settings.expected_account,
                required_coverage=self._settings.required_coverage,
                require_open_orders=self._settings.require_open_orders,
                expected_scope_ref=self._scope_ref,
                snapshot_format="typed",
            )
        except ServiceAccountError as exc:
            raise OwnerOperationError(exc.code) from None

    def invoke(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._check()
        if self._closed or self._client is None:
            raise OwnerOperationError("ACCOUNT_CLIENT_UNAVAILABLE")
        if type(operation) is not str or type(arguments) is not dict:
            raise OwnerOperationError("INVALID_OWNER_OPERATION")
        try:
            if operation == "validate":
                if set(arguments) != {"view"}:
                    raise OwnerOperationError("INVALID_OWNER_OPERATION")
                _decode_view(arguments["view"], check_fresh=True)
                current = _encode_view(self._client.snapshot())
                canonical = lambda value: json.dumps(
                    value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
                )
                if canonical(arguments["view"]) != canonical(current):
                    raise OwnerOperationError("STALE_ACCOUNT_STATE")
                return {"valid": True}
            if operation not in {"refresh", "snapshot", "status"} or arguments:
                raise OwnerOperationError("INVALID_OWNER_OPERATION")
            if operation == "status":
                return {"healthy": self._client.healthy}
            view = self._client.refresh() if operation == "refresh" else self._client.snapshot()
            return _encode_view(view)
        except ServiceAccountError as exc:
            raise OwnerOperationError(exc.code) from None

    def close(self) -> None:
        self._check()
        if self._closed:
            return
        if self._client is not None:
            self._client.close()
        self._client = None
        self._closed = True


class GatewayStrategyAccountClient:
    """Retain timed-out calls; timeout_seconds applies to each owner wait stage."""

    def __init__(
        self, threaded_runtime: ThreadedServiceClientRuntime, *, operation_prefix: str = "account."
    ) -> None:
        if type(threaded_runtime) is not ThreadedServiceClientRuntime:
            raise TypeError("INVALID_THREADED_RUNTIME")
        if (
            type(operation_prefix) is not str
            or len(operation_prefix) > 128
            or re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\.", operation_prefix, re.ASCII)
            is None
        ):
            raise ValueError("INVALID_OWNER_OPERATION_PREFIX")
        self._runtime = threaded_runtime
        self._prefix = operation_prefix
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._pending = None

    def _call(self, operation: str, timeout_seconds: float):
        if os.getpid() != self._pid:
            raise ThreadedRuntimeError("WRONG_PROCESS")
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= threading.TIMEOUT_MAX
        ):
            raise ValueError("INVALID_TIMEOUT")
        if not self._lock.acquire(blocking=False):
            raise ThreadedRuntimeError("OWNER_OPERATION_PENDING")
        try:
            name = self._prefix + operation
            if self._pending is not None and self._pending[0] != name:
                raise ThreadedRuntimeError("OWNER_OPERATION_PENDING")
            resumed = self._pending is not None
            if self._pending is None:
                handle = self._runtime.submit_owner(name, {})
                self._pending = (name, handle, "read", None)
            while True:
                _, handle, stage, original = self._pending
                if stage == "submit_validate":
                    try:
                        _decode_view(original, check_fresh=True)
                    except OwnerOperationError:
                        self._pending = None
                        raise
                    handle = self._runtime.submit_owner(
                        self._prefix + "validate", {"view": original}
                    )
                    self._pending = (name, handle, "validate", original)
                    resumed = False
                    continue
                if stage == "submit_status":
                    handle = self._runtime.submit_owner(name, {})
                    self._pending = (name, handle, "read", None)
                    resumed = False
                    continue
                try:
                    result = handle.result(timeout_seconds=timeout_seconds)
                except ThreadedRuntimeError as exc:
                    if exc.code not in {"OWNER_TIMEOUT_QUEUED", "OWNER_TIMEOUT_RUNNING"}:
                        self._pending = None
                    raise
                except Exception:
                    self._pending = None
                    raise
                self._pending = None
                if stage == "validate":
                    if (
                        type(result) is not dict
                        or result != {"valid": True}
                        or type(result["valid"]) is not bool
                    ):
                        raise OwnerOperationError("INVALID_ACCOUNT_VIEW")
                    result = original
                if resumed:
                    if operation == "status":
                        self._pending = (name, None, "submit_status", None)
                    else:
                        _decode_view(result, check_fresh=True)
                        self._pending = (name, None, "submit_validate", result)
                    continue
                break
            status = self._runtime.status()
            if status.state != "running" or not status.thread_alive or not status.healthy:
                raise ThreadedRuntimeError("NOT_READY")
            if operation == "status":
                if (
                    type(result) is not dict
                    or set(result) != {"healthy"}
                    or type(result["healthy"]) is not bool
                ):
                    raise OwnerOperationError("INVALID_ACCOUNT_STATUS")
                return {"healthy": result["healthy"]}
            return _decode_view(result, check_fresh=True)
        finally:
            self._lock.release()

    def refresh(self, *, timeout_seconds: float = 5.0) -> AccountStateView:
        return self._call("refresh", timeout_seconds)

    def snapshot(self, *, timeout_seconds: float = 5.0) -> AccountStateView:
        return self._call("snapshot", timeout_seconds)

    def status(self, *, timeout_seconds: float = 5.0) -> dict[str, bool]:
        return self._call("status", timeout_seconds)
