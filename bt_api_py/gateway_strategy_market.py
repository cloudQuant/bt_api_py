"""Owner-thread market facades and retained strategy-side calls."""

from __future__ import annotations

import math
import os
import re
import threading
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from bt_api_base.market_history_protocol import MarketHistoryPage, MarketHistoryRequest
from bt_api_base.market_protocol import MarketSourceDescriptor, MarketSubscriptionKey
from bt_api_base.service_protocol import (
    WireScope,
    envelope_from_wire_mapping,
    envelope_to_wire_mapping,
)

from bt_api_py.service_market import (
    ExpectedMarketSourceIdentity,
    MarketBootstrap,
    ServiceMarketClient,
    ServiceMarketError,
    ZmqMarketTransportFactory,
)
from bt_api_py.service_runtime import PORT_OPERATIONS, ClientCurveKeys
from bt_api_py.service_runtime_thread import (
    OwnerOperationError,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
    _copy_owner_json,
    _timeout,
)

_REQUIRED = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_METHODS = frozenset(
    {"start", "poll", "history", "recover", "accept_resync", "resync_after_regrant", "status"}
)
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class GatewayMarketSubscriptionSettings:
    key: MarketSubscriptionKey
    queue_capacity: int = 1024
    queue_max_bytes: int = 4 * 1024 * 1024
    head_check_interval_seconds: float = 1.0
    require_source_descriptor: bool = False
    expected_source_identity: ExpectedMarketSourceIdentity | None = None

    def __post_init__(self) -> None:
        if type(self.key) is not MarketSubscriptionKey:
            raise ValueError("INVALID_MARKET_SETTINGS")
        key = MarketSubscriptionKey.from_dict(self.key.to_dict())
        expected = self.expected_source_identity
        if expected is not None:
            if type(expected) is not ExpectedMarketSourceIdentity:
                raise ValueError("INVALID_MARKET_SETTINGS")
            expected = ExpectedMarketSourceIdentity(
                expected.source,
                expected.clock_domain_id,
                expected.clock_source_session_id,
                expected.clock_source,
            )
            if any(
                len(v.encode("utf-8")) > 256
                for v in (
                    expected.source,
                    expected.clock_domain_id,
                    expected.clock_source_session_id,
                    expected.clock_source,
                )
            ):
                raise ValueError("INVALID_MARKET_SETTINGS")
        if (
            type(self.queue_capacity) is not int
            or not 1 <= self.queue_capacity <= 100_000
            or type(self.queue_max_bytes) is not int
            or not 1 <= self.queue_max_bytes <= 64 * 1024 * 1024
            or type(self.head_check_interval_seconds) not in (int, float)
            or not math.isfinite(self.head_check_interval_seconds)
            or not 0 < self.head_check_interval_seconds <= 3600
            or type(self.require_source_descriptor) is not bool
            or (expected is not None and not self.require_source_descriptor)
        ):
            raise ValueError("INVALID_MARKET_SETTINGS")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "expected_source_identity", expected)
        object.__setattr__(
            self, "head_check_interval_seconds", float(self.head_check_interval_seconds)
        )


@dataclass(frozen=True)
class GatewayMarketClientSettings:
    subscriptions: Mapping[str, GatewayMarketSubscriptionSettings]
    requested_operations: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.subscriptions, Mapping) or not 1 <= len(self.subscriptions) <= 64:
            raise ValueError("INVALID_MARKET_SETTINGS")
        copied = {}
        for name, value in self.subscriptions.items():
            if (
                type(name) is not str
                or _NAME.fullmatch(name) is None
                or type(value) is not GatewayMarketSubscriptionSettings
            ):
                raise ValueError("INVALID_MARKET_SETTINGS")
            copied[name] = GatewayMarketSubscriptionSettings(
                value.key,
                value.queue_capacity,
                value.queue_max_bytes,
                value.head_check_interval_seconds,
                value.require_source_descriptor,
                value.expected_source_identity,
            )
        keys = [item.key for item in copied.values()]
        if len(set(keys)) != len(keys):
            raise ValueError("DUPLICATE_MARKET_KEY")
        ops = self.requested_operations
        if (
            type(ops) is not frozenset
            or not ops >= _REQUIRED
            or not ops <= PORT_OPERATIONS["service.market_source"]
        ):
            raise ValueError("INVALID_REQUESTED_OPERATIONS")
        object.__setattr__(self, "subscriptions", MappingProxyType(copied))
        object.__setattr__(self, "requested_operations", frozenset(ops))


def _copy_settings(settings: GatewayMarketClientSettings) -> GatewayMarketClientSettings:
    if type(settings) is not GatewayMarketClientSettings:
        raise ValueError("INVALID_MARKET_SETTINGS")
    return GatewayMarketClientSettings(settings.subscriptions, settings.requested_operations)


def _bootstrap_to_wire(value: MarketBootstrap) -> dict[str, Any]:
    if type(value) is not MarketBootstrap:
        raise ValueError("INVALID_MARKET_BOOTSTRAP")
    result = {
        "snapshot": None if value.snapshot is None else envelope_to_wire_mapping(value.snapshot),
        "snapshot_kind": value.snapshot_kind,
        "snapshot_complete": value.snapshot_complete,
        "full_baseline_available": value.full_baseline_available,
        "stream_id": value.stream_id,
        "snapshot_epoch": value.snapshot_epoch,
        "snapshot_sequence": value.snapshot_sequence,
        "cursor_epoch": value.cursor_epoch,
        "cursor_sequence": value.cursor_sequence,
        "resync_required": value.resync_required,
        "source_descriptor": None
        if value.source_descriptor is None
        else value.source_descriptor.to_dict(),
    }
    _bootstrap_from_wire(result)
    return result


def _bootstrap_from_wire(raw: Any) -> MarketBootstrap:
    fields = {
        "snapshot",
        "snapshot_kind",
        "snapshot_complete",
        "full_baseline_available",
        "stream_id",
        "snapshot_epoch",
        "snapshot_sequence",
        "cursor_epoch",
        "cursor_sequence",
        "resync_required",
        "source_descriptor",
    }
    if type(raw) is not dict or set(raw) != fields:
        raise ValueError("INVALID_MARKET_BOOTSTRAP")
    if (
        any(
            type(raw[k]) is not bool
            for k in ("snapshot_complete", "full_baseline_available", "resync_required")
        )
        or any(
            type(raw[k]) is not str or not raw[k] or len(raw[k].encode("utf-8")) > 256
            for k in ("snapshot_kind", "stream_id", "snapshot_epoch")
        )
        or (
            raw["cursor_epoch"] is not None
            and (
                type(raw["cursor_epoch"]) is not str
                or not raw["cursor_epoch"]
                or len(raw["cursor_epoch"].encode("utf-8")) > 256
            )
        )
        or any(
            type(raw[k]) is not int or not 0 <= raw[k] <= 2**63 - 1
            for k in ("snapshot_sequence", "cursor_sequence")
        )
    ):
        raise ValueError("INVALID_MARKET_BOOTSTRAP")
    return MarketBootstrap(
        snapshot=None if raw["snapshot"] is None else envelope_from_wire_mapping(raw["snapshot"]),
        snapshot_kind=raw["snapshot_kind"],
        snapshot_complete=raw["snapshot_complete"],
        full_baseline_available=raw["full_baseline_available"],
        stream_id=raw["stream_id"],
        snapshot_epoch=raw["snapshot_epoch"],
        snapshot_sequence=raw["snapshot_sequence"],
        cursor_epoch=raw["cursor_epoch"],
        cursor_sequence=raw["cursor_sequence"],
        resync_required=raw["resync_required"],
        source_descriptor=None
        if raw["source_descriptor"] is None
        else MarketSourceDescriptor.from_dict(raw["source_descriptor"]),
    )


class GatewayMarketOwner:
    """Construct and use all original market clients on the runtime owner."""

    def __init__(
        self,
        settings: GatewayMarketClientSettings,
        *,
        stream_scope: WireScope,
        security_profiles: Mapping[str, ClientCurveKeys],
        mount: str = "store.market",
    ) -> None:
        self._settings = _copy_settings(settings)
        if type(stream_scope) is not WireScope or any(
            (
                stream_scope.account_ref,
                stream_scope.account_refs,
                stream_scope.strategy_ref,
                stream_scope.run_id,
                stream_scope.manual_book_ref,
                stream_scope.portfolio_ref,
                stream_scope.group_id,
                stream_scope.leg_id,
            )
        ):
            raise ValueError("INVALID_STREAM_SCOPE")
        self._scope = WireScope(stream_scope.execution_domain_ref)
        if type(mount) is not str or not mount or len(mount) > 128:
            raise ValueError("INVALID_MOUNT")
        if (
            not isinstance(security_profiles, Mapping)
            or not security_profiles
            or len(security_profiles) > 64
        ):
            raise ValueError("INVALID_SECURITY_PROFILES")
        profiles = {}
        for name, keys in security_profiles.items():
            if (
                type(name) is not str
                or not name
                or len(name) > 128
                or type(keys) is not ClientCurveKeys
                or any(
                    type(x) is not bytes or len(x) != 40
                    for x in (keys.server_key, keys.public_key, keys.secret_key)
                )
            ):
                raise ValueError("INVALID_SECURITY_PROFILES")
            profiles[name] = ClientCurveKeys(keys.server_key, keys.public_key, keys.secret_key)
        self._profiles = MappingProxyType(profiles)
        self._mount = mount
        self._clients: dict[str, ServiceMarketClient] = {}
        self._uncertain: set[str] = set()
        self._consumers: dict[str, str] = {}
        self._gaps: set[str] = set()
        self._closed = False

    @property
    def cleanup_uncertainties(self) -> int:
        return len(self._uncertain)

    def start(self, raw: Any) -> None:
        factory = ZmqMarketTransportFactory(self._profiles)
        for name, setting in self._settings.subscriptions.items():
            self._clients[name] = ServiceMarketClient(
                raw,
                self._mount,
                setting.key,
                subscriber_factory=factory,
                stream_scope=self._scope,
                queue_capacity=setting.queue_capacity,
                queue_max_bytes=setting.queue_max_bytes,
                head_check_interval_seconds=setting.head_check_interval_seconds,
                require_source_descriptor=setting.require_source_descriptor,
                expected_source_identity=setting.expected_source_identity,
            )

    def invoke(self, operation: str, arguments: Mapping[str, Any]) -> Any:
        if self._closed:
            raise OwnerOperationError("OWNER_CLOSED")
        if type(operation) is not str or operation.count(".") != 1:
            raise OwnerOperationError("INVALID_OWNER_OPERATION")
        name, method = operation.split(".")
        if name not in self._clients or method not in _METHODS or type(arguments) is not dict:
            raise OwnerOperationError("INVALID_OWNER_OPERATION")
        if method != "status":
            if name in self._gaps:
                raise OwnerOperationError("LOCAL_RESULT_GAP")
            token = arguments.get("_consumer_token")
            if type(token) is not str or _TOKEN.fullmatch(token) is None:
                raise OwnerOperationError("INVALID_CONSUMER_TOKEN")
            previous = self._consumers.setdefault(name, token)
            if previous != token:
                raise OwnerOperationError("CONSUMER_ALREADY_BOUND")
            arguments = {k: v for k, v in arguments.items() if k != "_consumer_token"}
        client = self._clients[name]
        try:
            if (
                method in {"start", "poll", "recover", "resync_after_regrant", "status"}
                and arguments
            ):
                raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
            if method == "start":
                return _bootstrap_to_wire(client.start())
            if method == "poll":
                event = client.poll(timeout_ms=0)
                try:
                    wire = None if event is None else envelope_to_wire_mapping(event)
                    wire = _copy_owner_json(wire, error_code="INVALID_OWNER_RESULT")
                    if wire is not None:
                        envelope_from_wire_mapping(wire)
                    return wire
                except Exception:
                    self._gaps.add(name)
                    raise OwnerOperationError("LOCAL_RESULT_GAP") from None
            if method == "history":
                if (
                    set(arguments) != {"request"}
                    or "market.history" not in self._settings.requested_operations
                ):
                    raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
                request = MarketHistoryRequest.from_dict(arguments["request"])
                return client.history(request).to_dict()
            if method == "recover":
                return _bootstrap_to_wire(client.recover())
            if method == "accept_resync":
                if (
                    set(arguments) != {"acknowledge_data_loss"}
                    or type(arguments["acknowledge_data_loss"]) is not bool
                ):
                    raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
                return _bootstrap_to_wire(
                    client.accept_resync(acknowledge_data_loss=arguments["acknowledge_data_loss"])
                )
            if method == "resync_after_regrant":
                return _bootstrap_to_wire(client.resync_after_regrant())
            return {
                "healthy": client.healthy,
                "recovery_required": client.recovery_required,
                "resync_required": client.resync_required,
                "authorization_interrupted": client.authorization_interrupted,
                "authorization_generation": client.authorization_generation,
                "queue_count": client.queue_count,
                "queue_bytes": client.queue_bytes,
                "unsubscription_uncertain": client.unsubscription_uncertain,
                "local_result_gap": name in self._gaps,
                "last_bootstrap": None
                if client.last_bootstrap is None
                else _bootstrap_to_wire(client.last_bootstrap),
                "source_descriptor": None
                if client.source_descriptor is None
                else client.source_descriptor.to_dict(),
            }
        except ServiceMarketError as exc:
            raise OwnerOperationError(exc.code) from None
        except OwnerOperationError:
            raise
        except Exception:
            raise OwnerOperationError("MARKET_OPERATION_FAILED") from None

    def close(self) -> None:
        self._closed = True
        failed = False
        for name, client in list(self._clients.items()):
            try:
                client.close()
            except ServiceMarketError as exc:
                if exc.code == "UNSUBSCRIPTION_UNCERTAIN":
                    self._uncertain.add(name)
                    del self._clients[name]
                else:
                    failed = True
            except Exception:
                failed = True
            else:
                if client.unsubscription_uncertain:
                    self._uncertain.add(name)
                del self._clients[name]
        if failed:
            raise OwnerOperationError("LOCAL_CLOSE_FAILED")


class GatewayMarketSubscriptionClient:
    def __init__(self, runtime: ThreadedServiceClientRuntime, name: str):
        self._runtime, self._name = runtime, name
        self._owner_pid = os.getpid()
        self._consumer_token = uuid.uuid4().hex
        self._pending = None
        self._result_gap_handle = None
        self._lock = threading.Lock()

    @contextmanager
    def _caller_guard(self):
        if os.getpid() != self._owner_pid:
            raise ThreadedRuntimeError("WRONG_PROCESS")
        if not self._lock.acquire(blocking=False):
            raise ThreadedRuntimeError("OWNER_OPERATION_PENDING")
        try:
            yield
        finally:
            self._lock.release()

    def _call(
        self, method: str, arguments: dict[str, Any], timeout_seconds: float, decoder=None
    ) -> Any:
        if os.getpid() != self._owner_pid:
            raise ThreadedRuntimeError("WRONG_PROCESS")
        _timeout(timeout_seconds)
        arguments = _copy_owner_json(arguments, error_code="INVALID_OWNER_ARGUMENTS")
        with self._caller_guard():
            if self._result_gap_handle is not None and method != "status":
                raise ThreadedRuntimeError("LOCAL_RESULT_GAP")
            if method != "status":
                arguments["_consumer_token"] = self._consumer_token
            if self._pending is None:
                handle = self._runtime.submit_owner(f"market.{self._name}.{method}", arguments)
                self._pending = (method, arguments, handle)
            else:
                old_method, old_arguments, handle = self._pending
                if method != old_method or arguments != old_arguments:
                    raise ThreadedRuntimeError("OWNER_OPERATION_PENDING")
            try:
                result = handle.result(timeout_seconds=timeout_seconds)
            except ThreadedRuntimeError as exc:
                if exc.code not in {"OWNER_TIMEOUT_QUEUED", "OWNER_TIMEOUT_RUNNING"}:
                    self._pending = None
                    if method == "poll" and exc.code == "INVALID_OWNER_RESULT":
                        self._result_gap_handle = handle
                raise
            except OwnerOperationError as exc:
                self._pending = None
                if method == "poll" and exc.code == "LOCAL_RESULT_GAP":
                    self._result_gap_handle = handle
                raise
            except Exception:
                self._pending = None
                raise
            if decoder is not None:
                try:
                    result = decoder(result)
                except Exception:
                    self._pending = None
                    if method == "poll":
                        self._result_gap_handle = handle
                        raise ThreadedRuntimeError("LOCAL_RESULT_GAP") from None
                    raise
            self._pending = None
            return result

    def start(self, *, timeout_seconds: float = 5.0) -> MarketBootstrap:
        return _bootstrap_from_wire(self._call("start", {}, timeout_seconds))

    def poll(self, *, timeout_seconds: float = 5.0):
        return self._call(
            "poll",
            {},
            timeout_seconds,
            lambda raw: None if raw is None else envelope_from_wire_mapping(raw),
        )

    def history(
        self, request: MarketHistoryRequest, *, timeout_seconds: float = 5.0
    ) -> MarketHistoryPage:
        if type(request) is not MarketHistoryRequest:
            raise ValueError("INVALID_MARKET_HISTORY_REQUEST")
        return MarketHistoryPage.from_dict(
            self._call("history", {"request": request.to_dict()}, timeout_seconds)
        )

    def recover(self, *, timeout_seconds: float = 5.0) -> MarketBootstrap:
        return _bootstrap_from_wire(self._call("recover", {}, timeout_seconds))

    def accept_resync(
        self, *, acknowledge_data_loss: bool, timeout_seconds: float = 5.0
    ) -> MarketBootstrap:
        if type(acknowledge_data_loss) is not bool:
            raise ValueError("INVALID_ACKNOWLEDGEMENT")
        return _bootstrap_from_wire(
            self._call(
                "accept_resync", {"acknowledge_data_loss": acknowledge_data_loss}, timeout_seconds
            )
        )

    def resync_after_regrant(self, *, timeout_seconds: float = 5.0) -> MarketBootstrap:
        return _bootstrap_from_wire(self._call("resync_after_regrant", {}, timeout_seconds))

    def status(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        result = self._call("status", {}, timeout_seconds)
        fields = {
            "healthy",
            "recovery_required",
            "resync_required",
            "authorization_interrupted",
            "authorization_generation",
            "queue_count",
            "queue_bytes",
            "unsubscription_uncertain",
            "local_result_gap",
            "last_bootstrap",
            "source_descriptor",
        }
        if (
            type(result) is not dict
            or set(result) != fields
            or any(
                type(result[k]) is not bool
                for k in (
                    "healthy",
                    "recovery_required",
                    "resync_required",
                    "unsubscription_uncertain",
                    "local_result_gap",
                )
            )
            or result["authorization_interrupted"]
            not in (None, "MARKET_KEY_DENIED", "AUTHORIZATION_FLOOR")
            or any(
                type(result[k]) is not int or not 0 <= result[k] <= 2**63 - 1
                for k in ("authorization_generation", "queue_count", "queue_bytes")
            )
        ):
            raise ValueError("INVALID_MARKET_STATUS")
        result["local_result_gap"] |= self._result_gap_handle is not None
        if result.get("last_bootstrap") is not None:
            result["last_bootstrap"] = _bootstrap_from_wire(result["last_bootstrap"])
        if result.get("source_descriptor") is not None:
            result["source_descriptor"] = MarketSourceDescriptor.from_dict(
                result["source_descriptor"]
            )
        return result


class GatewayStrategyMarketClient:
    def __init__(
        self, runtime: ThreadedServiceClientRuntime, settings: GatewayMarketClientSettings
    ):
        if type(runtime) is not ThreadedServiceClientRuntime:
            raise ValueError("INVALID_RUNTIME")
        copied = _copy_settings(settings)
        self._clients = {
            name: GatewayMarketSubscriptionClient(runtime, name) for name in copied.subscriptions
        }

    def subscription(self, name: str) -> GatewayMarketSubscriptionClient:
        return self._clients[name]

    def __getitem__(self, name: str) -> GatewayMarketSubscriptionClient:
        return self.subscription(name)
