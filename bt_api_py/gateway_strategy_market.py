"""Owner-thread market facades and retained strategy-side calls."""

from __future__ import annotations

import math
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
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
    MarketClockResponse,
    MarketReceiveReceipt,
    ServiceMarketClient,
    ServiceMarketError,
    ZmqMarketTransportFactory,
)
from bt_api_py.service_runtime import PORT_OPERATIONS, ClientCurveKeys
from bt_api_py.service_runtime_thread import (
    OwnerOperationError,
    OwnerOperationContinuation,
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
        "market.bootstrap_begin",
        "market.bootstrap_status",
        "market.bootstrap_cancel",
    }
)
_ASYNC_OPERATIONS = frozenset(
    {"market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel"}
)
_LEGACY_REQUIRED = _REQUIRED - _ASYNC_OPERATIONS
_METHODS = frozenset(
    {"start", "poll", "poll_receipt", "clock_observation", "history", "recover", "accept_resync", "resync_after_regrant", "status"}
)
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class GatewayMarketReceivedEvent:
    """Event plus optional local socket-read observation; never venue proof."""

    event: Any
    receipt: MarketReceiveReceipt | None


@dataclass(frozen=True)
class GatewayMarketClockObservation:
    """Service-declared observation bound to the authenticated local RPC."""

    _observation_json: str = field(repr=False)
    request_started_monotonic_ns: int
    received_wall_ns: int
    received_monotonic_ns: int
    request_message_id: str
    reply_message_id: str
    registration_id: str
    service_epoch: str
    account_ref: str
    source_descriptor: MarketSourceDescriptor
    pid: int
    thread_id: int

    @property
    def observation(self):
        """Return a fresh copy, never the internal service response."""
        return json.loads(self._observation_json)


def _clock_observation_from_wire(raw):
    fields = {"observation", "receipt", "request_started_monotonic_ns", "registration_id",
              "service_epoch", "account_ref", "source_descriptor"}
    if type(raw) is not dict or set(raw) != fields:
        raise ValueError("INVALID_GATEWAY_CLOCK_OBSERVATION")
    obs, receipt = raw["observation"], raw["receipt"]
    receipt_fields = {"request_message_id", "reply_message_id", "correlation_id", "operation",
                      "received_wall_ns", "received_monotonic_ns", "pid", "thread_id"}
    if (type(obs) is not dict or type(receipt) is not dict or set(receipt) != receipt_fields
            or any(type(raw[name]) is not str or not raw[name]
                   for name in ("registration_id", "service_epoch", "account_ref"))
            or type(raw["request_started_monotonic_ns"]) is not int
            or any(type(receipt[name]) is not int for name in
                   ("received_wall_ns", "received_monotonic_ns", "pid", "thread_id"))
            or not 0 < raw["request_started_monotonic_ns"] <= receipt["received_monotonic_ns"] <= time.monotonic_ns()
            or receipt["received_wall_ns"] <= 0 or receipt["pid"] != os.getpid()
            or receipt["thread_id"] <= 0 or receipt["operation"] != "market.clock_observation"
            or type(receipt["request_message_id"]) is not str or not receipt["request_message_id"]
            or receipt["correlation_id"] != receipt["request_message_id"]
            or type(receipt["reply_message_id"]) is not str or not receipt["reply_message_id"]):
        raise ValueError("INVALID_GATEWAY_CLOCK_OBSERVATION")
    descriptor = MarketSourceDescriptor.from_dict(raw["source_descriptor"])
    if (MarketSourceDescriptor.from_dict(obs.get("market_source")) != descriptor
            or obs.get("account_scope") != raw["account_ref"]):
        raise ValueError("INVALID_GATEWAY_CLOCK_OBSERVATION")
    payload = json.dumps(obs, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return GatewayMarketClockObservation(payload, raw["request_started_monotonic_ns"],
        receipt["received_wall_ns"], receipt["received_monotonic_ns"], receipt["request_message_id"],
        receipt["reply_message_id"], raw["registration_id"], raw["service_epoch"],
        raw["account_ref"], descriptor, receipt["pid"], receipt["thread_id"])


def _received_event_from_wire(raw):
    if raw is None:
        return None
    if type(raw) is not dict or set(raw) != {"event", "receipt"}:
        raise ValueError("INVALID_MARKET_RECEIPT")
    event = envelope_from_wire_mapping(raw["event"])
    data = raw["receipt"]
    if data is None:
        return GatewayMarketReceivedEvent(event, None)
    fields = set(MarketReceiveReceipt.__dataclass_fields__)
    if type(data) is not dict or set(data) != fields:
        raise ValueError("INVALID_MARKET_RECEIPT")
    receipt = MarketReceiveReceipt(**data)
    if (receipt.source not in {"pub", "replay"}
            or receipt.stream_id != event.header.stream_id
            or receipt.stream_epoch != event.header.stream_epoch
            or type(receipt.sequence) is not int or receipt.sequence != event.header.sequence
            or receipt.message_id != event.header.message_id
            or type(receipt.received_wall_ns) is not int or receipt.received_wall_ns <= 0
            or type(receipt.received_monotonic_ns) is not int
            or not 0 < receipt.received_monotonic_ns <= time.monotonic_ns()
            or type(receipt.pid) is not int or receipt.pid != os.getpid()
            or type(receipt.thread_id) is not int or receipt.thread_id <= 0):
        raise ValueError("INVALID_MARKET_RECEIPT")
    if receipt.source == "pub":
        if receipt.request_message_id is not None or receipt.reply_message_id is not None:
            raise ValueError("INVALID_MARKET_RECEIPT")
    elif any(type(value) is not str or not value for value in
             (receipt.request_message_id, receipt.reply_message_id)):
        raise ValueError("INVALID_MARKET_RECEIPT")
    return GatewayMarketReceivedEvent(event, receipt)


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
    requested_operations: frozenset[str] = _REQUIRED

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
            or not ops >= _LEGACY_REQUIRED
            or ops & _ASYNC_OPERATIONS not in (frozenset(), _ASYNC_OPERATIONS)
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
                method in {"start", "poll", "poll_receipt", "recover", "resync_after_regrant", "status"}
                and arguments
            ):
                raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
            if method == "start":
                if client._get_binding().operations & _ASYNC_OPERATIONS:
                    return self._continue_bootstrap(client.begin_start())
                return _bootstrap_to_wire(client.start())
            if method in {"poll", "poll_receipt"}:
                result = (client.poll_with_receipt(timeout_ms=0) if method == "poll_receipt"
                          else client.poll(timeout_ms=0))
                try:
                    if method == "poll_receipt":
                        if result is None:
                            wire = None
                        else:
                            event, receipt = result
                            wire = {"event": envelope_to_wire_mapping(event),
                                    "receipt": None if receipt is None else asdict(receipt)}
                    else:
                        wire = None if result is None else envelope_to_wire_mapping(result)
                    wire = _copy_owner_json(wire, error_code="INVALID_OWNER_RESULT")
                    if wire is not None:
                        envelope_from_wire_mapping(wire["event"] if method == "poll_receipt" else wire)
                    return wire
                except Exception:
                    self._gaps.add(name)
                    raise OwnerOperationError("LOCAL_RESULT_GAP") from None
            if method == "clock_observation":
                if "market.clock_observation" not in self._settings.requested_operations:
                    raise OwnerOperationError("CLOCK_EVIDENCE_UNAVAILABLE")
                if set(arguments) not in (set(), {"epoch", "sequence", "message_id"}):
                    raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
                observed = client.clock_observation(**arguments)
                if type(observed) is not MarketClockResponse:
                    raise OwnerOperationError("CLOCK_EVIDENCE_UNAVAILABLE")
                return _copy_owner_json({
                    "observation": observed.observation,
                    "receipt": asdict(observed.receipt),
                    "request_started_monotonic_ns": observed.request_started_monotonic_ns,
                    "registration_id": observed.registration_id,
                    "service_epoch": observed.service_epoch,
                    "account_ref": observed.account_ref,
                    "source_descriptor": observed.source_descriptor.to_dict(),
                }, error_code="INVALID_OWNER_RESULT")
            if method == "history":
                if (
                    set(arguments) != {"request"}
                    or "market.history" not in self._settings.requested_operations
                ):
                    raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
                request = MarketHistoryRequest.from_dict(arguments["request"])
                return client.history(request).to_dict()
            if method == "recover":
                if client._get_binding().operations & _ASYNC_OPERATIONS:
                    return self._continue_bootstrap(client.begin_recover())
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
                if client._get_binding().operations & _ASYNC_OPERATIONS:
                    return self._continue_bootstrap(client.begin_resync_after_regrant())
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

    def _continue_bootstrap(self, operation):
        def resume():
            try:
                result = operation.step()
                if result is None:
                    return OwnerOperationContinuation(resume)
                return _bootstrap_to_wire(result[0])
            except ServiceMarketError as exc:
                raise OwnerOperationError(exc.code) from None

        return resume()

    def close(self) -> None:
        self._closed = True
        failed = False
        for name, client in list(self._clients.items()):
            try:
                client.close()
            except ServiceMarketError as exc:
                if exc.code == "UNSUBSCRIPTION_UNCERTAIN":
                    self._uncertain.add(name)
                    if (
                        getattr(client, "_bootstrap_operation", None) is not None
                        and not client._bootstrap_operation.done
                    ):
                        failed = True
                    else:
                        del self._clients[name]
                else:
                    failed = True
            except Exception:
                failed = True
            else:
                if client.unsubscription_uncertain:
                    self._uncertain.add(name)
                else:
                    self._uncertain.discard(name)
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
        # Only the latest event actually delivered by this subscription is
        # eligible for the Gateway's optional order-time clock check.
        self._issued_received_event = None
        self._issued_clock_observation = None
        self._issuance_lock = threading.Lock()
        self._issuance_generation = 0

    def _invalidate_issued(self):
        with self._issuance_lock:
            self._issuance_generation += 1
            self._issued_received_event = self._issued_clock_observation = None

    def _begin_poll(self):
        with self._issuance_lock:
            self._issuance_generation += 1
            generation, previous = self._issuance_generation, self._issued_received_event
            self._issued_received_event = self._issued_clock_observation = None
            return generation, previous

    def _finish_poll(self, generation, previous, result, *, with_receipt):
        with self._issuance_lock:
            if self._issuance_generation == generation:
                self._issued_received_event = previous if result is None else (result if with_receipt else None)

    # Read-only calls whose finished-but-unclaimed result may be dropped: the
    # next caller simply asks again. A poll (it moves the event cursor) and
    # control calls (start/recover/resync) keep their slot until re-issued.
    _RETIRABLE_READS = frozenset({"status", "clock_observation", "history"})

    def _retire_finished_read(self) -> bool:
        """Drop an earlier timed-out read once it has finished (caller lock held).

        Without this, one clock observation that outlived its timeout (for
        example while the Gateway owner was busy) made every later poll and
        observation fail with OWNER_OPERATION_PENDING for the process lifetime,
        because each new observation carries different event arguments.
        """
        method, _arguments, handle = self._pending
        if method not in self._RETIRABLE_READS:
            return False
        try:
            handle.result(timeout_seconds=0.001)
        except ThreadedRuntimeError as exc:
            if exc.code in {"OWNER_TIMEOUT_QUEUED", "OWNER_TIMEOUT_RUNNING"}:
                return False
            self._pending = None  # failed reads are finished too; not replayed
            return True
        except Exception:
            self._pending = None  # failed reads are finished too; not replayed
            return True
        self._pending = None
        return True

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
            if self._pending is not None:
                old_method, old_arguments, handle = self._pending
                if (method != old_method or arguments != old_arguments) and not self._retire_finished_read():
                    raise ThreadedRuntimeError("OWNER_OPERATION_PENDING")
            if self._pending is None:
                handle = self._runtime.submit_owner(f"market.{self._name}.{method}", arguments)
                self._pending = (method, arguments, handle)
            try:
                result = handle.result(timeout_seconds=timeout_seconds)
            except ThreadedRuntimeError as exc:
                if exc.code not in {"OWNER_TIMEOUT_QUEUED", "OWNER_TIMEOUT_RUNNING"}:
                    self._pending = None
                    if method in {"poll", "poll_receipt"} and exc.code == "INVALID_OWNER_RESULT":
                        self._result_gap_handle = handle
                raise
            except OwnerOperationError as exc:
                self._pending = None
                if method in {"poll", "poll_receipt"} and exc.code == "LOCAL_RESULT_GAP":
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
                    if method in {"poll", "poll_receipt"}:
                        self._result_gap_handle = handle
                        raise ThreadedRuntimeError("LOCAL_RESULT_GAP") from None
                    raise
            self._pending = None
            return result

    def start(self, *, timeout_seconds: float = 5.0) -> MarketBootstrap:
        self._invalidate_issued()
        return _bootstrap_from_wire(self._call("start", {}, timeout_seconds))

    def poll(self, *, timeout_seconds: float = 5.0):
        generation, previous = self._begin_poll()
        result = self._call(
            "poll",
            {},
            timeout_seconds,
            lambda raw: None if raw is None else envelope_from_wire_mapping(raw),
        )
        self._finish_poll(generation, previous, result, with_receipt=False)
        return result

    def poll_with_receipt(self, *, timeout_seconds: float = 5.0):
        """Return a frame with its SDK-captured socket read evidence, if available."""
        generation, previous = self._begin_poll()
        result = self._call("poll_receipt", {}, timeout_seconds, _received_event_from_wire)
        self._finish_poll(generation, previous, result, with_receipt=True)
        return result

    def clock_observation(self, *, receipt=None, timeout_seconds: float = 5.0):
        """Read an observation bound to this registered market subscription."""
        with self._issuance_lock:
            generation = self._issuance_generation
            self._issued_clock_observation = None
            issued = self._issued_received_event
            if receipt is not None:
                if (type(issued) is not GatewayMarketReceivedEvent or issued.receipt is not receipt
                        or receipt.source != "pub"):
                    raise ValueError("INVALID_MARKET_RECEIPT")
                header = issued.event.header
                arguments = {"epoch": header.stream_epoch, "sequence": header.sequence,
                             "message_id": header.message_id}
            else:
                arguments = {}
        result = self._call("clock_observation", arguments, timeout_seconds, _clock_observation_from_wire)
        with self._issuance_lock:
            if self._issuance_generation == generation:
                self._issued_clock_observation = result
        return result

    def _issued_clock_matches(self, event, receipt, observation):
        """Check identities against this client's last genuine owner results."""
        from bt_api_base.market_tick_protocol import MarketTickEvent, MarketTickPayload

        with self._issuance_lock:
            issued, current_observation = self._issued_received_event, self._issued_clock_observation
        if (type(event) is not MarketTickEvent or type(issued) is not GatewayMarketReceivedEvent
                or issued.receipt is not receipt or receipt is None
                or observation is not current_observation):
            return False
        try:
            header = issued.event.header
            return (event.data == MarketTickPayload.from_dict(issued.event.payload["data"])
                    and event.source == observation.source_descriptor
                    and event.cursor.stream_id == header.stream_id
                    and event.cursor.epoch == header.stream_epoch
                    and event.cursor.sequence == header.sequence)
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def history(
        self, request: MarketHistoryRequest, *, timeout_seconds: float = 5.0
    ) -> MarketHistoryPage:
        if type(request) is not MarketHistoryRequest:
            raise ValueError("INVALID_MARKET_HISTORY_REQUEST")
        return MarketHistoryPage.from_dict(
            self._call("history", {"request": request.to_dict()}, timeout_seconds)
        )

    def recover(self, *, timeout_seconds: float = 5.0) -> MarketBootstrap:
        self._invalidate_issued()
        return _bootstrap_from_wire(self._call("recover", {}, timeout_seconds))

    def accept_resync(
        self, *, acknowledge_data_loss: bool, timeout_seconds: float = 5.0
    ) -> MarketBootstrap:
        self._invalidate_issued()
        if type(acknowledge_data_loss) is not bool:
            raise ValueError("INVALID_ACKNOWLEDGEMENT")
        return _bootstrap_from_wire(
            self._call(
                "accept_resync", {"acknowledge_data_loss": acknowledge_data_loss}, timeout_seconds
            )
        )

    def resync_after_regrant(self, *, timeout_seconds: float = 5.0) -> MarketBootstrap:
        self._invalidate_issued()
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
        if (not result["healthy"] or result["recovery_required"] or result["resync_required"]
                or result["authorization_interrupted"] is not None or result["local_result_gap"]):
            self._invalidate_issued()
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
