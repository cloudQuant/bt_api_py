"""Shared market-stream client facade for one registered SDK market mount.

The facade owns one event subscriber and drives bounded, read-only replay on
the constructing thread. Polling also pumps runtime lease maintenance inline;
this module never starts a scheduler or automatically retries business calls.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from bt_api_base.market_history_protocol import MarketHistoryPage, MarketHistoryRequest
from bt_api_base.market_protocol import (
    MARKET_EVENT_OPERATION,
    MARKET_EVENT_SCHEMA,
    MARKET_EVENT_SCHEMA_VERSION,
    MarketSourceDescriptor,
    MarketSubscriptionKey,
)
from bt_api_base.service_protocol import (
    EventSubscriber,
    WireEnvelope,
    WireScope,
    decode_envelope,
    encode_envelope,
    validate_event,
)

from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ServiceMarketBindingView,
    ServiceRuntimeError,
    ServiceRuntimeRequestNotSentError,
)

_MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)

_BOOTSTRAP_OPERATIONS = _MARKET_OPERATIONS
_BATCH_BOOTSTRAP_OPERATION = "market.bootstrap_many"
_MAX_BOOTSTRAP_BATCH = 20
_HISTORY_OPERATION = "market.history"
_HISTORY_OPERATIONS = frozenset({_HISTORY_OPERATION})
_MAX_POLL_TIMEOUT_MS = 60_000
_MAX_QUEUE_CAPACITY = 100_000
_MAX_QUEUE_BYTES = 64 * 1024 * 1024
_MAX_READ_AFTER_LIMIT = 1_000
MARKET_WIRE_VERSION_LEGACY = 1
MARKET_WIRE_VERSION_TOPIC_FILTERED = 2
_SUPPORTED_MARKET_WIRE_VERSIONS = frozenset(
    {MARKET_WIRE_VERSION_LEGACY, MARKET_WIRE_VERSION_TOPIC_FILTERED}
)


@runtime_checkable
class TopicV2SubscriberFactory(Protocol):
    """Explicit factory extension for a negotiated topic-filtered stream."""

    def create_topic_v2(
        self,
        binding: ServiceMarketBindingView,
        stream_scope: WireScope,
        subscription_key: MarketSubscriptionKey,
    ) -> EventSubscriber:
        """Create one subscriber bound to the exact market subscription key."""


class ServiceMarketError(RuntimeError):
    """Finite diagnostics for market client lifecycle and stream failures."""

    def __init__(self, code: str, *, cause_code: str | None = None) -> None:
        self.code = code
        self.cause_code = cause_code
        super().__init__(code)


class ServiceMarketStartupCleanupError(ServiceMarketError):
    """A market factory retained a partially constructed subscriber."""

    def __init__(self, transport: EventSubscriber) -> None:
        self.transport = transport
        super().__init__("STARTUP_CLEANUP_FAILED")


@dataclass(frozen=True)
class MarketBootstrap:
    """Latest-event observation and the safe incremental replay cursor.

    The hub deliberately offers only a ``latest_event`` snapshot.  Its data is
    never described as a complete order book or market baseline.
    """

    snapshot: WireEnvelope | None
    snapshot_kind: str
    snapshot_complete: bool
    full_baseline_available: bool
    stream_id: str
    snapshot_epoch: str
    snapshot_sequence: int
    cursor_epoch: str | None
    cursor_sequence: int
    resync_required: bool
    source_descriptor: MarketSourceDescriptor | None = None


class ZmqMarketTransportFactory:
    """Create CURVE-authenticated subscribers sharing one owned ZMQ context."""

    def __init__(self, security_profiles: Mapping[str, ClientCurveKeys]) -> None:
        if not isinstance(security_profiles, Mapping):
            raise ServiceMarketError("INVALID_SECURITY_PROFILES")
        copied: dict[str, ClientCurveKeys] = {}
        for reference, keys in security_profiles.items():
            if type(reference) is not str or type(keys) is not ClientCurveKeys:
                raise ServiceMarketError("INVALID_SECURITY_PROFILES")
            copied[reference] = keys
        self._profiles = copied
        self._owner_pid = os.getpid()
        self._context_pool_init_lock = threading.Lock()
        self._context_pool = None

    def __call__(
        self, binding: ServiceMarketBindingView, stream_scope: WireScope
    ) -> EventSubscriber:
        self._validate_binding_scope(binding, stream_scope)
        if _market_wire_version(binding) != MARKET_WIRE_VERSION_LEGACY:
            raise ServiceMarketError("TOPIC_V2_FACTORY_METHOD_REQUIRED")
        return self._create_subscriber(
            binding,
            stream_scope,
            wire_version=MARKET_WIRE_VERSION_LEGACY,
            subscription_key=None,
        )

    def create_topic_v2(
        self,
        binding: ServiceMarketBindingView,
        stream_scope: WireScope,
        subscription_key: MarketSubscriptionKey,
    ) -> EventSubscriber:
        self._validate_binding_scope(binding, stream_scope)
        if _market_wire_version(binding) != MARKET_WIRE_VERSION_TOPIC_FILTERED:
            raise ServiceMarketError("TOPIC_V2_BINDING_REQUIRED")
        if type(subscription_key) is not MarketSubscriptionKey:
            raise ServiceMarketError("INVALID_MARKET_KEY")
        return self._create_subscriber(
            binding,
            stream_scope,
            wire_version=MARKET_WIRE_VERSION_TOPIC_FILTERED,
            subscription_key=subscription_key,
        )

    @staticmethod
    def _validate_binding_scope(binding: ServiceMarketBindingView, stream_scope: WireScope) -> None:
        if type(binding) is not ServiceMarketBindingView:
            raise ServiceMarketError("INVALID_BINDING_VIEW")
        if type(stream_scope) is not WireScope:
            raise ServiceMarketError("INVALID_STREAM_SCOPE")

    def _create_subscriber(
        self,
        binding: ServiceMarketBindingView,
        stream_scope: WireScope,
        *,
        wire_version: int,
        subscription_key: MarketSubscriptionKey | None,
    ) -> EventSubscriber:
        if os.getpid() != self._owner_pid:
            raise ServiceMarketError("PROCESS_BOUNDARY")
        keys = self._profiles.get(binding.security_profile_ref)
        if type(keys) is not ClientCurveKeys:
            raise ServiceMarketError("SECURITY_PROFILE_UNAVAILABLE")

        # Import the optional transport only when the market facade is started.
        from bt_api_transport_zmq.market_v2 import ZmqMarketContextPool, ZmqMarketSubscriber
        from bt_api_transport_zmq.server import CurveClientCredentials
        from bt_api_transport_zmq.service_v2 import (
            ServiceTransportStartupCleanupError,
        )

        context_pool = self._context_pool
        if context_pool is None:
            with self._context_pool_init_lock:
                if os.getpid() != self._owner_pid:
                    raise ServiceMarketError("PROCESS_BOUNDARY")
                context_pool = self._context_pool
                if context_pool is None:
                    context_pool = ZmqMarketContextPool()
                    self._context_pool = context_pool

        try:
            transport_options = {
                "credentials": CurveClientCredentials(
                    keys.server_key, keys.public_key, keys.secret_key
                ),
                "high_water_mark": binding.high_water_mark,
                "max_message_bytes": binding.max_message_bytes,
            }
            if wire_version == MARKET_WIRE_VERSION_TOPIC_FILTERED:
                transport_options["wire_version"] = MARKET_WIRE_VERSION_TOPIC_FILTERED
                transport_options["subscription_key"] = subscription_key
            return ZmqMarketSubscriber(
                binding.market_endpoint,
                binding.peer,
                stream_scope,
                context_pool=context_pool,
                **transport_options,
            )
        except ServiceTransportStartupCleanupError as error:
            raise ServiceMarketStartupCleanupError(error.transport) from None
        except TypeError:
            if wire_version == MARKET_WIRE_VERSION_TOPIC_FILTERED:
                raise ServiceMarketError("TOPIC_V2_TRANSPORT_UNSUPPORTED") from None
            raise


class ServiceMarketClient:
    """One owner-thread market subscription backed by RPC control and replay."""

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        mount: str,
        key: MarketSubscriptionKey,
        *,
        subscriber_factory: Any = None,
        stream_scope: WireScope,
        queue_capacity: int = 1_024,
        queue_max_bytes: int = 4 * 1024 * 1024,
        head_check_interval_seconds: float = 1.0,
        monotonic_clock: Any = time.monotonic,
        require_source_descriptor: bool = False,
    ) -> None:
        if type(runtime) is not ServiceClientRuntime:
            raise ServiceMarketError("INVALID_RUNTIME")
        if type(mount) is not str or not mount:
            raise ServiceMarketError("INVALID_MOUNT")
        if type(key) is not MarketSubscriptionKey:
            raise ServiceMarketError("INVALID_MARKET_KEY")
        if type(require_source_descriptor) is not bool:
            raise ServiceMarketError("INVALID_SOURCE_DESCRIPTOR_REQUIREMENT")
        if subscriber_factory is None and key.channel != "bar":
            raise ServiceMarketError("INVALID_SUBSCRIBER_FACTORY")
        if (
            subscriber_factory is not None
            and not callable(subscriber_factory)
            and not callable(getattr(subscriber_factory, "create_topic_v2", None))
        ):
            raise ServiceMarketError("INVALID_SUBSCRIBER_FACTORY")
        if type(stream_scope) is not WireScope:
            raise ServiceMarketError("INVALID_STREAM_SCOPE")
        if (
            stream_scope.account_ref is not None
            or stream_scope.account_refs
            or stream_scope.strategy_ref is not None
            or stream_scope.run_id is not None
            or stream_scope.manual_book_ref is not None
            or stream_scope.portfolio_ref is not None
            or stream_scope.group_id is not None
            or stream_scope.leg_id is not None
        ):
            raise ServiceMarketError("STREAM_SCOPE_MUST_BE_PUBLIC")
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= _MAX_QUEUE_CAPACITY:
            raise ServiceMarketError("INVALID_QUEUE_CAPACITY")
        if type(queue_max_bytes) is not int or not 1 <= queue_max_bytes <= _MAX_QUEUE_BYTES:
            raise ServiceMarketError("INVALID_QUEUE_BYTE_LIMIT")
        if (
            type(head_check_interval_seconds) not in (int, float)
            or not math.isfinite(head_check_interval_seconds)
            or head_check_interval_seconds <= 0
            or head_check_interval_seconds > 3_600
        ):
            raise ServiceMarketError("INVALID_HEAD_CHECK_INTERVAL")
        if not callable(monotonic_clock):
            raise ServiceMarketError("INVALID_MONOTONIC_CLOCK")

        self._runtime = runtime
        self.mount = mount
        self.key = key
        self._subscriber_factory = subscriber_factory
        self._require_source_descriptor = require_source_descriptor
        self._source_descriptor: MarketSourceDescriptor | None = None
        self.stream_scope = stream_scope
        self._queue_capacity = queue_capacity
        self._queue_max_bytes = queue_max_bytes
        self._head_interval = float(head_check_interval_seconds)
        self._clock = monotonic_clock
        self._owner_thread_id = threading.get_ident()
        self._state = "new"
        self._subscriber: EventSubscriber | None = None
        self._binding: ServiceMarketBindingView | None = None
        self._remote_active = False
        self._remote_unknown = False
        self._unsubscription_uncertain = False
        self._uncertain_registration_id: str | None = None
        self._recovery_required = False
        self._resync_required = False
        self._pending_resync: MarketBootstrap | None = None
        self._pending_poll_replay: tuple[str, int] | None = None
        self._pending_poll_gap_event: WireEnvelope | None = None
        self._cursor_epoch: str | None = None
        self._cursor_sequence = 0
        self._queued_epoch: str | None = None
        self._queued_sequence = 0
        self._queue: deque[tuple[WireEnvelope, int]] = deque()
        self._queue_bytes = 0
        self._next_head_check = 0.0
        self._last_bootstrap: MarketBootstrap | None = None

    @property
    def healthy(self) -> bool:
        return (
            self._state == "started"
            and self._subscriber is not None
            and not self._recovery_required
            and not self._resync_required
            and self._runtime.healthy
        )

    @property
    def recovery_required(self) -> bool:
        return self._recovery_required or self._resync_required

    @property
    def resync_required(self) -> bool:
        return self._resync_required

    @property
    def unsubscription_uncertain(self) -> bool:
        return self._unsubscription_uncertain

    @property
    def queue_count(self) -> int:
        return len(self._queue)

    @property
    def queue_bytes(self) -> int:
        return self._queue_bytes

    @property
    def last_bootstrap(self) -> MarketBootstrap | None:
        return self._last_bootstrap

    @property
    def source_descriptor(self) -> MarketSourceDescriptor | None:
        """Typed active source identity supplied by the authenticated subscribe reply.

        The descriptor is read-only to callers and is replaced only after a
        subsequent subscribe reply has passed exact validation. It is cleared
        before every subscribe attempt so callers cannot accidentally reuse a
        prior stream epoch after recovery.
        """

        if (
            self._state != "started"
            or self._recovery_required
            or self._resync_required
            or self._source_descriptor is None
            or self._source_descriptor.stream_id != self.key.stream_id
            or self._source_descriptor.epoch != self._cursor_epoch
        ):
            return None
        return self._source_descriptor

    @classmethod
    def start_many(
        cls, clients: tuple[ServiceMarketClient, ...] | list[ServiceMarketClient]
    ) -> tuple[MarketBootstrap, ...]:
        """Start a bounded same-registration group with one market bootstrap RPC.

        Existing gateways without the optional batch operation keep working:
        they fall back to the established per-client lifecycle before any
        subscriber or remote registration is created.
        """
        if type(clients) not in (tuple, list) or not 1 <= len(clients) <= _MAX_BOOTSTRAP_BATCH:
            raise ServiceMarketError("INVALID_BOOTSTRAP_BATCH")
        selected = tuple(clients)
        if any(type(client) is not cls for client in selected):
            raise ServiceMarketError("INVALID_BOOTSTRAP_BATCH")
        first = selected[0]
        for client in selected:
            client._assert_owner()
            if (
                client._state != "new"
                or client._runtime is not first._runtime
                or client.mount != first.mount
                or client.stream_scope != first.stream_scope
                or client._subscriber_factory is None
                or (client.key.channel == "bar" and client._subscriber_factory is None)
            ):
                raise ServiceMarketError("INCOMPATIBLE_BOOTSTRAP_CLIENTS")
        if len({client.key for client in selected}) != len(selected):
            raise ServiceMarketError("DUPLICATE_MARKET_KEY")

        # The optional operation is deliberately not part of the ordinary
        # single-client requirement set. Older Hub deployments therefore
        # continue through the original subscribe/snapshot/head calls.
        binding = first._get_binding()
        if _BATCH_BOOTSTRAP_OPERATION not in binding.operations:
            # A missing operation in the authenticated binding is the only
            # compatibility signal. Do not retry through the legacy path after
            # a batch request was sent or after an unrelated runtime failure.
            return tuple(client.start() for client in selected)

        for client in selected:
            other = client._get_binding()
            if (
                not _same_binding(binding, other)
                or binding.operations != other.operations
                or _market_wire_version(binding) != _market_wire_version(other)
            ):
                raise ServiceMarketError("INCOMPATIBLE_BOOTSTRAP_BINDINGS")
            client._check_public_domain(binding)
            if client._queue_max_bytes < binding.max_message_bytes:
                raise ServiceMarketError("QUEUE_TOO_SMALL_FOR_MARKET_FRAME")
            if client.key.channel == "bar" and client._subscriber_factory is None:
                raise ServiceMarketError("HISTORY_ONLY_CLIENT")

        # Bind and open every local SUB socket before the service can start an
        # upstream publisher, avoiding a gap between server subscription and
        # local topic readiness.
        try:
            for client in selected:
                client._binding = binding
                client._state = "starting"
                client._source_descriptor = None
                client._subscriber = client._make_subscriber(binding)
        except BaseException as error:
            cleanup_failed = False
            for client in selected:
                if client._binding is not None:
                    client._cleanup_failed_start()
                    cleanup_failed = cleanup_failed or client._state == "close_failed"
            if cleanup_failed:
                raise ServiceMarketError("BATCH_LOCAL_CLEANUP_FAILED") from None
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            if isinstance(error, ServiceMarketError):
                raise
            raise ServiceMarketError("START_FAILED") from None

        arguments = {
            "subscriptions": [
                {
                    "key": client.key.to_dict(),
                    **(
                        {"require_source_descriptor": True}
                        if client._require_source_descriptor
                        else {}
                    ),
                }
                for client in selected
            ]
        }
        for client in selected:
            client._remote_unknown = True
        try:
            result = first._rpc(
                _BATCH_BOOTSTRAP_OPERATION,
                arguments,
                binding=binding,
            )
        except ServiceMarketError as error:
            if error.code == "CONTROL_REQUEST_NOT_SENT":
                for client in selected:
                    client._remote_active = False
                    client._remote_unknown = False
                cleanup_failed = False
                for client in selected:
                    client._cleanup_failed_start()
                    cleanup_failed = cleanup_failed or client._state == "close_failed"
                if cleanup_failed:
                    raise ServiceMarketError("BATCH_LOCAL_CLEANUP_FAILED") from None
            else:
                for client in selected:
                    client._remote_active = False
                    client._remote_unknown = True
                    client._set_unsubscription_uncertain(binding.registration_id)
                    client._state = "close_failed"
            raise

        status = result.get("status")
        if status == "rejected":
            if set(result) != {"status", "code"}:
                status = "invalid"
            else:
                code = result["code"]
                if type(code) is not str or not code:
                    status = "invalid"
                else:
                    for client in selected:
                        client._remote_active = False
                        client._remote_unknown = False
                    cleanup_failed = False
                    for client in selected:
                        client._cleanup_failed_start()
                        cleanup_failed = cleanup_failed or client._state == "close_failed"
                    if cleanup_failed:
                        raise ServiceMarketError("BATCH_LOCAL_CLEANUP_FAILED")
                    raise ServiceMarketError("BATCH_START_REJECTED", cause_code=code)

        if status == "unknown":
            if set(result) != {"status", "code", "uncertain_stream_ids"}:
                status = "invalid"
            else:
                uncertain_ids = result["uncertain_stream_ids"]
                if (
                    type(result["code"]) is not str
                    or not result["code"]
                    or type(uncertain_ids) not in (tuple, list)
                    or any(type(value) is not str for value in uncertain_ids)
                    or len(set(uncertain_ids)) != len(uncertain_ids)
                    or not set(uncertain_ids) <= {client.key.stream_id for client in selected}
                    or not uncertain_ids
                ):
                    status = "invalid"
                else:
                    uncertain = set(uncertain_ids)
                    cleanup_failed = False
                    for client in selected:
                        if client.key.stream_id in uncertain:
                            client._remote_active = False
                            client._remote_unknown = True
                            client._set_unsubscription_uncertain(binding.registration_id)
                            client._state = "close_failed"
                        else:
                            client._remote_active = False
                            client._remote_unknown = False
                            client._cleanup_failed_start()
                            cleanup_failed = cleanup_failed or client._state == "close_failed"
                    if cleanup_failed:
                        raise ServiceMarketError("BATCH_LOCAL_CLEANUP_FAILED")
                    raise ServiceMarketError("BATCH_START_OUTCOME_UNKNOWN")

        if status != "active" or set(result) != {"status", "items"}:
            for client in selected:
                client._remote_active = False
                client._remote_unknown = True
                client._set_unsubscription_uncertain(binding.registration_id)
                client._state = "close_failed"
            raise ServiceMarketError("INVALID_BATCH_REPLY")

        raw_items = result["items"]
        if type(raw_items) not in (tuple, list) or len(raw_items) != len(selected):
            for client in selected:
                client._remote_active = False
                client._remote_unknown = True
                client._set_unsubscription_uncertain(binding.registration_id)
                client._state = "close_failed"
            raise ServiceMarketError("INVALID_BATCH_REPLY")

        try:
            parsed_items: list[tuple[MarketBootstrap, Mapping[str, Any]]] = []
            for client, raw_item in zip(selected, raw_items, strict=True):
                if not isinstance(raw_item, Mapping) or set(raw_item) != {
                    "subscription",
                    "snapshot",
                    "head",
                }:
                    raise ServiceMarketError("INVALID_BATCH_REPLY")
                subscription = raw_item["subscription"]
                client._source_descriptor = client._validate_subscription_reply(subscription)
                snapshot = client._snapshot_from_reply(raw_item["snapshot"])
                head = raw_item["head"]
                client._validate_head_reply(head)
                if head["epoch"] != snapshot.snapshot_epoch or head["head"] < snapshot.snapshot_sequence:
                    # A concurrent stream transition requires ordinary explicit
                    # resynchronization; do not claim a consistent batch cut.
                    raise ServiceMarketError("INVALID_BATCH_CURSOR")
                parsed_items.append((snapshot, head))
        except Exception:
            for client in selected:
                client._source_descriptor = None
                client._remote_active = False
                client._remote_unknown = True
                client._set_unsubscription_uncertain(binding.registration_id)
                client._state = "close_failed"
            raise ServiceMarketError("INVALID_BATCH_REPLY") from None

        bootstraps: list[MarketBootstrap] = []
        for client, (snapshot, head) in zip(selected, parsed_items, strict=True):
            client._remote_active = True
            client._remote_unknown = False
            client._unsubscription_uncertain = False
            client._uncertain_registration_id = None
            client._cursor_epoch = snapshot.snapshot_epoch
            client._cursor_sequence = snapshot.snapshot_sequence
            client._queued_epoch = snapshot.snapshot_epoch
            client._queued_sequence = snapshot.snapshot_sequence
            client._state = "started"
            client._last_bootstrap = snapshot
            client._next_head_check = client._now()
            if not client._head_and_replay(head_reply=head):
                if client._pending_resync is not None:
                    bootstrap = client._pending_resync
                else:
                    bootstrap = client._bootstrap(
                        snapshot.snapshot,
                        snapshot.snapshot_epoch,
                        snapshot.snapshot_sequence,
                        resync_required=False,
                    )
            else:
                bootstrap = client._bootstrap(
                    snapshot.snapshot,
                    snapshot.snapshot_epoch,
                    snapshot.snapshot_sequence,
                    resync_required=False,
                )
            client._last_bootstrap = bootstrap
            bootstraps.append(bootstrap)
        return tuple(bootstraps)

    def start(self) -> MarketBootstrap:
        self._assert_owner()
        if self.key.channel == "bar" and self._subscriber_factory is None:
            raise ServiceMarketError("HISTORY_ONLY_CLIENT")
        if self._state == "started":
            if self._last_bootstrap is None:
                raise ServiceMarketError("INVALID_CLIENT_STATE")
            return self._last_bootstrap
        if self._state != "new":
            raise ServiceMarketError("CLIENT_NOT_NEW")
        try:
            binding = self._get_binding()
            self._check_public_domain(binding)
            if self._queue_max_bytes < binding.max_message_bytes:
                raise ServiceMarketError("QUEUE_TOO_SMALL_FOR_MARKET_FRAME")
            self._binding = binding
            self._subscriber = self._make_subscriber(binding)
            self._state = "starting"
            self._source_descriptor = None
            subscribed = self._rpc("market.subscribe", self._subscribe_arguments())
            self._remote_unknown = True
            self._source_descriptor = self._validate_subscription_reply(subscribed)
            self._remote_active = True
            self._remote_unknown = False
            if self._uncertain_registration_id == binding.registration_id:
                self._unsubscription_uncertain = False
                self._uncertain_registration_id = None

            snapshot = self._get_snapshot()
            self._cursor_epoch = snapshot.snapshot_epoch
            self._cursor_sequence = snapshot.snapshot_sequence
            self._queued_epoch = snapshot.snapshot_epoch
            self._queued_sequence = snapshot.snapshot_sequence
            self._state = "started"
            self._last_bootstrap = snapshot
            self._next_head_check = self._now()
            if not self._head_and_replay():
                if self._pending_resync is not None:
                    return self._pending_resync
                self._last_bootstrap = self._bootstrap(
                    snapshot.snapshot,
                    snapshot.snapshot_epoch,
                    snapshot.snapshot_sequence,
                    resync_required=False,
                )
                return self._last_bootstrap
            self._last_bootstrap = self._bootstrap(
                snapshot.snapshot,
                snapshot.snapshot_epoch,
                snapshot.snapshot_sequence,
                resync_required=False,
            )
            return self._last_bootstrap
        except BaseException as error:
            self._cleanup_failed_start()
            self._source_descriptor = None
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            if isinstance(error, ServiceMarketError):
                raise
            cause_code = error.code if isinstance(error, ServiceRuntimeError) else None
            raise ServiceMarketError("START_FAILED", cause_code=cause_code) from None

    def poll(self, timeout_ms: int = 0) -> WireEnvelope | None:
        """Return the next event, replaying missed tail events when possible."""

        self._assert_owner()
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= _MAX_POLL_TIMEOUT_MS:
            raise ServiceMarketError("INVALID_POLL_TIMEOUT")
        if self._state != "started":
            raise ServiceMarketError("CLIENT_NOT_STARTED")
        self._maintain_runtime()
        self._check_binding()
        replay_page_budget = [1]

        # Events already admitted to the bounded queue have passed epoch,
        # sequence, and size validation. Deliver that work before another
        # periodic head/replay round trip can delay it.
        if self._queue:
            return self._dequeue()
        if self._resync_required:
            raise ServiceMarketError("RESYNC_REQUIRED")
        if self._recovery_required:
            raise ServiceMarketError("RECOVERY_REQUIRED")
        if self._pending_poll_replay is not None:
            self._continue_poll_replay(replay_page_budget)
            if self._queue:
                return self._dequeue()
            if self._resync_required:
                raise ServiceMarketError("RESYNC_REQUIRED")
            if self._recovery_required:
                raise ServiceMarketError("RECOVERY_REQUIRED")
            if self._pending_poll_replay is not None:
                return None

        now = self._now()
        if now >= self._next_head_check:
            self._next_head_check = now + self._head_interval
            self._head_and_replay(page_budget=replay_page_budget)
        if self._queue:
            return self._dequeue()
        if self._resync_required:
            raise ServiceMarketError("RESYNC_REQUIRED")
        if self._recovery_required:
            raise ServiceMarketError("RECOVERY_REQUIRED")
        if self._pending_poll_replay is not None:
            return None

        wait_seconds = min(timeout_ms / 1000.0, max(0.0, self._next_head_check - self._now()))
        maintenance_delay = self._runtime.next_maintenance_delay()
        if maintenance_delay is not None:
            wait_seconds = min(wait_seconds, max(0.0, maintenance_delay))
        try:
            event = self._require_subscriber().receive(timeout=wait_seconds)
        except Exception as error:
            self._recovery_required = True
            raise ServiceMarketError("RECEIVE_FAILED", cause_code=self._cause_code(error)) from None
        self._maintain_runtime()
        self._check_binding()
        if event is not None:
            self._ingest_event(event, page_budget=replay_page_budget)

        if self._queue:
            return self._dequeue()

        now = self._now()
        if (
            now >= self._next_head_check
            and not self._resync_required
            and self._pending_poll_replay is None
        ):
            self._next_head_check = now + self._head_interval
            self._head_and_replay(page_budget=replay_page_budget)

        if self._queue:
            return self._dequeue()
        if self._resync_required:
            raise ServiceMarketError("RESYNC_REQUIRED")
        if self._recovery_required:
            raise ServiceMarketError("RECOVERY_REQUIRED")
        if self._pending_poll_replay is not None:
            return None
        return None

    def history(self, request: MarketHistoryRequest) -> MarketHistoryPage:
        """Read one bounded history page without starting or changing a live stream."""

        self._assert_owner()
        if type(request) is not MarketHistoryRequest:
            raise ServiceMarketError("INVALID_HISTORY_REQUEST")
        if request.key != self.key:
            raise ServiceMarketError("HISTORY_KEY_MISMATCH")
        if self.key.channel != "bar":
            raise ServiceMarketError("HISTORY_ONLY_CLIENT_REQUIRED")
        if self._state == "started":
            if self._resync_required:
                raise ServiceMarketError("RESYNC_REQUIRED")
            if self._recovery_required:
                raise ServiceMarketError("RECOVERY_REQUIRED")
            if not self.healthy:
                raise ServiceMarketError("CLIENT_UNHEALTHY")
        elif self._state != "new":
            raise ServiceMarketError("CLIENT_NOT_AVAILABLE")

        self._maintain_runtime()
        if self._state == "started":
            binding = self._check_binding()
        else:
            binding = self._get_history_binding()
            self._check_public_domain(binding)
        if _HISTORY_OPERATION not in binding.operations:
            raise ServiceMarketError("HISTORY_UNAVAILABLE")

        result = self._rpc(_HISTORY_OPERATION, request.to_dict(), binding=binding)

        # Lease maintenance can run while the RPC is in flight. Do not accept
        # a page across a service restart or any other binding replacement.
        try:
            self._maintain_runtime()
            if self._state == "started":
                current_binding = self._check_binding()
            else:
                current_binding = self._get_history_binding()
                self._check_public_domain(current_binding)
        except ServiceMarketError:
            self._recovery_required = True
            raise
        if current_binding.service_epoch != binding.service_epoch:
            self._recovery_required = True
            raise ServiceMarketError("HISTORY_EPOCH_CHANGED")
        if not _same_binding(binding, current_binding):
            self._recovery_required = True
            raise ServiceMarketError("RUNTIME_BINDING_CHANGED")
        if _HISTORY_OPERATION not in current_binding.operations:
            self._recovery_required = True
            raise ServiceMarketError("HISTORY_UNAVAILABLE")

        try:
            page = MarketHistoryPage.from_dict(result)
        except Exception:
            raise ServiceMarketError("INVALID_HISTORY_REPLY") from None
        if page.request != request:
            raise ServiceMarketError("HISTORY_REQUEST_MISMATCH")
        return page

    def drain(self, max_events: int = 100) -> tuple[WireEnvelope, ...]:
        """Drain buffered events and pump owner-thread lease maintenance.

        This does not wait or issue market head/replay RPCs. Runtime lease
        maintenance may issue its control-plane renew RPCs.
        """

        self._assert_owner()
        if type(max_events) is not int or not 1 <= max_events <= self._queue_capacity:
            raise ServiceMarketError("INVALID_DRAIN_LIMIT")
        if self._state not in {"started", "close_failed"}:
            raise ServiceMarketError("CLIENT_NOT_STARTED")
        self._maintain_runtime()
        self._check_binding()
        events: list[WireEnvelope] = []
        while self._queue and len(events) < max_events:
            events.append(self._dequeue())
        return tuple(events)

    def recover(self) -> MarketBootstrap:
        """Explicitly recreate the subscriber and resume from the safe cursor."""

        self._assert_owner()
        if self._state in {"new", "closed"}:
            raise ServiceMarketError("CLIENT_NOT_RECOVERABLE")
        if self._queue:
            raise ServiceMarketError("DRAIN_BUFFERED_EVENTS_FIRST")
        self._clear_pending_poll_replay()
        previous_binding = self._binding
        try:
            binding = self._get_binding()
            self._check_public_domain(binding)
            registration_changed = (
                previous_binding is not None
                and binding.registration_id != previous_binding.registration_id
            )
            if registration_changed and (self._remote_active or self._remote_unknown):
                self._set_unsubscription_uncertain(previous_binding.registration_id)
                self._remote_active = False
                self._remote_unknown = False

            if self._subscriber is not None:
                self._close_subscriber()

            self._binding = binding
            self._subscriber = self._make_subscriber(binding)
            self._state = "starting"
            self._source_descriptor = None
            subscribed = self._rpc("market.subscribe", self._subscribe_arguments())
            self._remote_unknown = True
            self._source_descriptor = self._validate_subscription_reply(subscribed)
            self._remote_active = True
            self._remote_unknown = False
            if self._uncertain_registration_id == binding.registration_id:
                self._unsubscription_uncertain = False
                self._uncertain_registration_id = None

            snapshot = self._get_snapshot()
            self._state = "started"
            if self._cursor_epoch is None:
                self._cursor_epoch = snapshot.snapshot_epoch
                self._cursor_sequence = snapshot.snapshot_sequence
                self._queued_epoch = snapshot.snapshot_epoch
                self._queued_sequence = snapshot.snapshot_sequence
            elif snapshot.snapshot_epoch != self._cursor_epoch:
                self._mark_resync(snapshot)
                self._last_bootstrap = self._bootstrap(
                    snapshot.snapshot,
                    snapshot.snapshot_epoch,
                    snapshot.snapshot_sequence,
                    resync_required=True,
                )
                return self._last_bootstrap

            self._last_bootstrap = self._bootstrap(
                snapshot.snapshot,
                snapshot.snapshot_epoch,
                snapshot.snapshot_sequence,
                resync_required=self._resync_required,
            )
            if self._resync_required:
                return self._last_bootstrap
            self._recovery_required = False
            self._next_head_check = self._now() + self._head_interval
            if not self._head_and_replay():
                if self._pending_resync is not None:
                    return self._pending_resync
                self._last_bootstrap = self._bootstrap(
                    snapshot.snapshot,
                    snapshot.snapshot_epoch,
                    snapshot.snapshot_sequence,
                    resync_required=self._resync_required,
                )
                return self._last_bootstrap
            self._last_bootstrap = self._bootstrap(
                snapshot.snapshot,
                snapshot.snapshot_epoch,
                snapshot.snapshot_sequence,
                resync_required=False,
            )
            return self._last_bootstrap
        except BaseException as error:
            self._source_descriptor = None
            if self._state != "close_failed":
                self._state = "failed"
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            if isinstance(error, ServiceMarketError):
                raise
            raise ServiceMarketError(
                "RECOVERY_FAILED", cause_code=self._cause_code(error)
            ) from None

    def accept_resync(self, *, acknowledge_data_loss: bool) -> MarketBootstrap:
        """Accept an incomplete latest-event baseline after app state handling.

        The caller must first handle invalidation/rebuild of its own state from
        outside this facade, then pass ``acknowledge_data_loss=True``.  This
        never claims that the hub supplied a complete snapshot.
        """

        self._assert_owner()
        if type(acknowledge_data_loss) is not bool or not acknowledge_data_loss:
            raise ServiceMarketError("RESYNC_ACKNOWLEDGEMENT_REQUIRED")
        if not self._resync_required or self._pending_resync is None:
            raise ServiceMarketError("NO_RESYNC_PENDING")
        if self._queue:
            raise ServiceMarketError("DRAIN_BUFFERED_EVENTS_FIRST")
        if self._state != "started":
            raise ServiceMarketError("CLIENT_NOT_STARTED")
        self._clear_pending_poll_replay()
        snapshot = self._get_snapshot()
        self._cursor_epoch = snapshot.snapshot_epoch
        self._cursor_sequence = snapshot.snapshot_sequence
        self._queued_epoch = snapshot.snapshot_epoch
        self._queued_sequence = snapshot.snapshot_sequence
        self._resync_required = False
        self._recovery_required = False
        self._pending_resync = None
        self._next_head_check = self._now() + self._head_interval
        if not self._head_and_replay() and self._pending_resync is not None:
            return self._pending_resync
        self._last_bootstrap = self._bootstrap(
            snapshot.snapshot,
            snapshot.snapshot_epoch,
            snapshot.snapshot_sequence,
            resync_required=self._resync_required,
        )
        return self._last_bootstrap

    def close(self) -> None:
        """Unsubscribe and close locally; failed owner-thread closes are retryable."""

        self._assert_owner()
        self._clear_pending_poll_replay()
        if self._state == "closed":
            return
        self._source_descriptor = None
        binding = None
        with suppress(ServiceMarketError):
            binding = self._get_binding()
        previous = self._binding
        same_registration = (
            binding is not None
            and previous is not None
            and binding.registration_id == previous.registration_id
        )
        if (
            previous is not None
            and not same_registration
            and (self._remote_active or self._remote_unknown)
        ):
            self._set_unsubscription_uncertain(previous.registration_id)
        if same_registration and (self._remote_active or self._remote_unknown):
            self._try_unsubscribe(binding, preserve_uncertainty=False)
        try:
            self._close_subscriber()
        except Exception:
            self._state = "close_failed"
            raise ServiceMarketError("LOCAL_CLOSE_FAILED") from None
        except BaseException:
            self._state = "close_failed"
            raise
        self._state = "closed"
        self._queue.clear()
        self._queue_bytes = 0
        if self._unsubscription_uncertain:
            raise ServiceMarketError("UNSUBSCRIPTION_UNCERTAIN")

    def _clear_pending_poll_replay(self) -> None:
        self._pending_poll_replay = None
        self._pending_poll_gap_event = None

    def _head_and_replay(
        self,
        *,
        head_reply: Mapping[str, Any] | None = None,
        page_budget: list[int] | None = None,
    ) -> bool:
        if self._cursor_epoch is None or self._queued_epoch is None:
            self._recovery_required = True
            return False
        try:
            head = (
                self._rpc("market.head", {"key": self.key.to_dict()})
                if head_reply is None
                else head_reply
            )
            self._validate_head_reply(head)
            if head["epoch"] != self._queued_epoch:
                snapshot = self._get_snapshot()
                self._mark_resync(snapshot)
                return False
            target = head["head"]
            if target < self._queued_sequence:
                snapshot = self._get_snapshot()
                self._mark_resync(snapshot)
                return False
            complete = self._replay_to(target, page_budget=page_budget)
            if (
                not complete
                and page_budget is not None
                and page_budget[0] == 0
                and self._queued_sequence < target
                and not self._recovery_required
                and not self._resync_required
            ):
                self._pending_poll_replay = (head["epoch"], target)
            return complete
        except ServiceMarketError as error:
            if error.code not in {"RESYNC_REQUIRED", "QUEUE_CAPACITY_EXCEEDED"}:
                self._recovery_required = True
            return False

    def _continue_poll_replay(self, page_budget: list[int]) -> bool:
        pending = self._pending_poll_replay
        if pending is None:
            return True
        epoch, target = pending
        if epoch != self._queued_epoch:
            self._clear_pending_poll_replay()
            self._recovery_required = True
            return False
        try:
            complete = self._replay_to(target, page_budget=page_budget)
        except ServiceMarketError as error:
            if error.code not in {"RESYNC_REQUIRED", "QUEUE_CAPACITY_EXCEEDED"}:
                self._recovery_required = True
            self._clear_pending_poll_replay()
            return False
        if complete:
            gap_event = self._pending_poll_gap_event
            self._clear_pending_poll_replay()
            if gap_event is not None:
                self._enqueue_gap_event_if_next(gap_event)
        elif self._recovery_required or self._resync_required:
            self._clear_pending_poll_replay()
        return complete

    def _replay_to(self, target: int, *, page_budget: list[int] | None = None) -> bool:
        while self._queued_sequence < target:
            if page_budget is not None and page_budget[0] <= 0:
                binding = self._binding
                if (
                    binding is None
                    or self._queue_capacity - len(self._queue) <= 0
                    or (self._queue_max_bytes - self._queue_bytes)
                    // binding.max_message_bytes
                    <= 0
                ):
                    self._recovery_required = True
                return False
            available_count = self._queue_capacity - len(self._queue)
            available_bytes = self._queue_max_bytes - self._queue_bytes
            binding = self._binding
            if binding is None:
                self._recovery_required = True
                return False
            page_limit = min(
                _MAX_READ_AFTER_LIMIT,
                available_count,
                available_bytes // binding.max_message_bytes,
            )
            if page_limit <= 0:
                self._recovery_required = True
                return False
            after = self._queued_sequence
            if page_budget is not None:
                page_budget[0] -= 1
            page = self._rpc(
                "market.read_after",
                {
                    "key": self.key.to_dict(),
                    "epoch": self._queued_epoch,
                    "after": after,
                    "limit": page_limit,
                },
            )
            self._validate_read_after_reply(page, after=after, limit=page_limit)
            if page["gap"]:
                snapshot = self._get_snapshot()
                self._mark_resync(snapshot)
                return False
            if page["epoch"] != self._queued_epoch:
                snapshot = self._get_snapshot()
                self._mark_resync(snapshot)
                return False
            events = page["events"]
            if not events:
                self._recovery_required = True
                return False
            for raw_event in events:
                event = _decode_envelope_mapping(raw_event)
                self._validate_event(event, expected_epoch=None)
                if event.header.stream_epoch != self._queued_epoch:
                    snapshot = self._get_snapshot()
                    self._mark_resync(snapshot)
                    return False
                if event.header.sequence != self._queued_sequence + 1:
                    snapshot = self._get_snapshot()
                    self._mark_resync(snapshot)
                    return False
                if not self._enqueue(event):
                    return False
            if page["next_after"] != self._queued_sequence:
                self._recovery_required = True
                return False
            if page["has_more"] is False and self._queued_sequence < target:
                # A concurrently trimmed or malformed replay page cannot prove
                # that the cursor reached the observed head.
                self._recovery_required = True
                return False
        return True

    def _ingest_event(
        self, event: WireEnvelope, *, page_budget: list[int] | None = None
    ) -> None:
        if self._queued_epoch is None:
            self._recovery_required = True
            return
        try:
            if not self._validate_event(event, expected_epoch=None, allow_other_key=True):
                return
        except ServiceMarketError:
            self._recovery_required = True
            raise
        if event.header.stream_epoch != self._queued_epoch:
            snapshot = self._get_snapshot()
            if (
                snapshot.snapshot_epoch == self._queued_epoch
                and snapshot.snapshot_epoch == self._cursor_epoch
            ):
                # The authoritative epoch is still current. Ignore a delayed
                # frame left in the local SUB queue from before resync.
                return
            self._mark_resync(snapshot)
            return
        sequence = event.header.sequence
        if sequence <= self._queued_sequence:
            return
        if sequence != self._queued_sequence + 1:
            if not self._head_and_replay(page_budget=page_budget):
                if self._pending_poll_replay is not None:
                    self._pending_poll_gap_event = event
                return
            if sequence <= self._queued_sequence:
                return
            if sequence != self._queued_sequence + 1:
                self._recovery_required = True
                return
        self._enqueue(event)

    def _enqueue(self, event: WireEnvelope) -> bool:
        encoded_size = len(encode_envelope(event))
        if (
            len(self._queue) >= self._queue_capacity
            or self._queue_bytes + encoded_size > self._queue_max_bytes
        ):
            self._recovery_required = True
            return False
        if self._queued_epoch != event.header.stream_epoch:
            self._recovery_required = True
            return False
        if event.header.sequence != self._queued_sequence + 1:
            self._recovery_required = True
            return False
        self._queue.append((event, encoded_size))
        self._queue_bytes += encoded_size
        self._queued_sequence = event.header.sequence
        return True

    def _enqueue_gap_event_if_next(self, event: WireEnvelope) -> bool:
        if event.header.stream_epoch != self._queued_epoch:
            self._recovery_required = True
            return False
        if event.header.sequence <= self._queued_sequence:
            return True
        if event.header.sequence != self._queued_sequence + 1:
            self._recovery_required = True
            return False
        return self._enqueue(event)

    def _dequeue(self) -> WireEnvelope:
        event, encoded_size = self._queue.popleft()
        self._queue_bytes -= encoded_size
        if (
            event.header.stream_epoch != self._cursor_epoch
            or event.header.sequence != self._cursor_sequence + 1
        ):
            self._recovery_required = True
            raise ServiceMarketError("BUFFER_CURSOR_MISMATCH")
        self._cursor_sequence = event.header.sequence
        return event

    def _get_snapshot(self) -> MarketBootstrap:
        reply = self._rpc("market.snapshot", {"key": self.key.to_dict()})
        return self._snapshot_from_reply(reply)

    def _snapshot_from_reply(self, reply: Mapping[str, Any]) -> MarketBootstrap:
        self._validate_snapshot_reply(reply)
        descriptor = self._source_descriptor
        if descriptor is not None and (
            descriptor.stream_id != reply["stream_id"]
            or descriptor.epoch != reply["epoch"]
        ):
            self._source_descriptor = None
            if self._require_source_descriptor:
                raise ServiceMarketError("SOURCE_DESCRIPTOR_EPOCH_MISMATCH")
            descriptor = None
        event = None if reply["event"] is None else _decode_envelope_mapping(reply["event"])
        if event is not None:
            self._validate_event(event, expected_epoch=reply["epoch"])
            if event.header.sequence != reply["head"]:
                raise ServiceMarketError("INVALID_SNAPSHOT_EVENT")
        elif reply["head"] != 0:
            raise ServiceMarketError("INVALID_EMPTY_SNAPSHOT")
        cursor_epoch = self._cursor_epoch
        cursor_sequence = self._cursor_sequence
        if cursor_epoch is None:
            cursor_epoch = reply["epoch"]
            cursor_sequence = reply["head"]
        return MarketBootstrap(
            snapshot=event,
            snapshot_kind="latest_event",
            snapshot_complete=False,
            full_baseline_available=False,
            stream_id=reply["stream_id"],
            snapshot_epoch=reply["epoch"],
            snapshot_sequence=reply["head"],
            cursor_epoch=cursor_epoch,
            cursor_sequence=cursor_sequence,
            resync_required=False,
            source_descriptor=descriptor,
        )

    def _mark_resync(self, snapshot: MarketBootstrap) -> None:
        self._clear_pending_poll_replay()
        self._resync_required = True
        self._recovery_required = True
        self._pending_resync = MarketBootstrap(
            snapshot=snapshot.snapshot,
            snapshot_kind="latest_event",
            snapshot_complete=False,
            full_baseline_available=False,
            stream_id=snapshot.stream_id,
            snapshot_epoch=snapshot.snapshot_epoch,
            snapshot_sequence=snapshot.snapshot_sequence,
            cursor_epoch=self._cursor_epoch,
            cursor_sequence=self._cursor_sequence,
            resync_required=True,
            source_descriptor=snapshot.source_descriptor,
        )
        self._last_bootstrap = self._pending_resync

    def _bootstrap(
        self,
        snapshot: WireEnvelope | None,
        snapshot_epoch: str,
        snapshot_sequence: int,
        *,
        resync_required: bool,
    ) -> MarketBootstrap:
        descriptor = self._source_descriptor
        if descriptor is not None and (
            descriptor.stream_id != self.key.stream_id or descriptor.epoch != snapshot_epoch
        ):
            descriptor = None
        return MarketBootstrap(
            snapshot=snapshot,
            snapshot_kind="latest_event",
            snapshot_complete=False,
            full_baseline_available=False,
            stream_id=self.key.stream_id,
            snapshot_epoch=snapshot_epoch,
            snapshot_sequence=snapshot_sequence,
            cursor_epoch=self._cursor_epoch,
            cursor_sequence=self._cursor_sequence,
            resync_required=resync_required,
            source_descriptor=descriptor,
        )

    def _validate_event(
        self,
        event: WireEnvelope,
        *,
        expected_epoch: str | None,
        allow_other_key: bool = False,
    ) -> bool:
        try:
            validate_event(
                event,
                expected_scope=self.stream_scope,
            )
        except Exception:
            raise ServiceMarketError("INVALID_MARKET_EVENT") from None
        header = event.header
        if (
            header.identity != self._require_binding().peer
            or header.operation != MARKET_EVENT_OPERATION
            or header.schema_id != MARKET_EVENT_SCHEMA
            or header.schema_version != MARKET_EVENT_SCHEMA_VERSION
            or set(event.payload) != {"key", "data"}
        ):
            raise ServiceMarketError("INVALID_MARKET_EVENT")
        try:
            event_key = MarketSubscriptionKey.from_dict(event.payload["key"])
        except Exception:
            raise ServiceMarketError("INVALID_MARKET_EVENT") from None
        if not isinstance(event.payload["data"], Mapping):
            raise ServiceMarketError("INVALID_MARKET_EVENT")
        if event.header.stream_id != event_key.stream_id:
            raise ServiceMarketError("INVALID_MARKET_EVENT")
        if event_key != self.key:
            if allow_other_key:
                return False
            raise ServiceMarketError("INVALID_MARKET_EVENT")
        if expected_epoch is not None and event.header.stream_epoch != expected_epoch:
            raise ServiceMarketError("INVALID_MARKET_EVENT")
        return True

    def _subscribe_arguments(self) -> dict[str, Any]:
        arguments: dict[str, Any] = {"key": self.key.to_dict()}
        if self._require_source_descriptor:
            arguments["require_source_descriptor"] = True
        return arguments

    def _validate_subscription_reply(
        self, reply: Mapping[str, Any]
    ) -> MarketSourceDescriptor | None:
        required_fields = {
            "stream_id",
            "epoch",
            "head",
            "oldest_sequence",
            "already_subscribed",
        }
        if not isinstance(reply, Mapping):
            raise ServiceMarketError("INVALID_SUBSCRIBE_REPLY")
        fields = set(reply)
        if fields == required_fields:
            if self._require_source_descriptor:
                raise ServiceMarketError("SOURCE_DESCRIPTOR_UNAVAILABLE")
            descriptor = None
        elif fields == required_fields | {"source_descriptor"}:
            if not self._require_source_descriptor:
                raise ServiceMarketError("INVALID_SUBSCRIBE_REPLY")
            descriptor = self._parse_source_descriptor(reply["source_descriptor"])
        else:
            raise ServiceMarketError("INVALID_SUBSCRIBE_REPLY")
        _validate_stream_metadata(reply, key=self.key, oldest_required=True)
        if type(reply["already_subscribed"]) is not bool:
            raise ServiceMarketError("INVALID_SUBSCRIBE_REPLY")
        if descriptor is not None and (
            descriptor.stream_id != reply["stream_id"]
            or descriptor.epoch != reply["epoch"]
        ):
            raise ServiceMarketError("INVALID_SOURCE_DESCRIPTOR")
        return descriptor

    def _parse_source_descriptor(self, value: object) -> MarketSourceDescriptor:
        fields = {
            "key",
            "stream_id",
            "epoch",
            "provider",
            "source",
            "connection_generation",
            "clock_domain_id",
            "clock_source_session_id",
            "clock_source",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ServiceMarketError("INVALID_SOURCE_DESCRIPTOR")
        try:
            descriptor = MarketSourceDescriptor.from_dict(value)
        except Exception:
            raise ServiceMarketError("INVALID_SOURCE_DESCRIPTOR") from None
        if (
            type(descriptor) is not MarketSourceDescriptor
            or descriptor.key != self.key
            or descriptor.stream_id != self.key.stream_id
            or descriptor.provider != self.key.instrument.provider
            or descriptor.stream_id != value["stream_id"]
            or descriptor.epoch != value["epoch"]
            or type(descriptor.connection_generation) is not int
            or descriptor.connection_generation <= 0
            or any(
                type(getattr(descriptor, name)) is not str
                or not getattr(descriptor, name)
                or getattr(descriptor, name) != getattr(descriptor, name).strip()
                for name in (
                    "stream_id",
                    "epoch",
                    "provider",
                    "source",
                    "clock_domain_id",
                    "clock_source_session_id",
                    "clock_source",
                )
            )
        ):
            raise ServiceMarketError("INVALID_SOURCE_DESCRIPTOR")
        return descriptor

    def _validate_head_reply(self, reply: Mapping[str, Any]) -> None:
        _exact_fields(
            reply,
            {"stream_id", "epoch", "head", "oldest_sequence", "latest_observed_at_utc"},
        )
        _validate_stream_metadata(reply, key=self.key, oldest_required=True)
        latest = reply["latest_observed_at_utc"]
        if latest is not None and type(latest) is not str:
            raise ServiceMarketError("INVALID_HEAD_REPLY")

    def _validate_snapshot_reply(self, reply: Mapping[str, Any]) -> None:
        _exact_fields(
            reply,
            {"snapshot_kind", "complete", "stream_id", "epoch", "head", "event"},
        )
        if reply["snapshot_kind"] != "latest_event" or reply["complete"] is not False:
            raise ServiceMarketError("INVALID_SNAPSHOT_REPLY")
        _validate_stream_metadata(reply, key=self.key, oldest_required=False)
        event = reply["event"]
        if event is not None and not isinstance(event, Mapping):
            raise ServiceMarketError("INVALID_SNAPSHOT_REPLY")

    def _validate_read_after_reply(
        self, reply: Mapping[str, Any], *, after: int, limit: int
    ) -> None:
        _exact_fields(
            reply,
            {
                "stream_id",
                "epoch",
                "head",
                "oldest_sequence",
                "after",
                "next_after",
                "limit",
                "has_more",
                "gap",
                "gap_reason",
                "events",
            },
        )
        _validate_stream_metadata(reply, key=self.key, oldest_required=True)
        if (
            type(reply["after"]) is not int
            or reply["after"] != after
            or type(reply["limit"]) is not int
            or reply["limit"] != limit
            or type(reply["next_after"]) is not int
            or type(reply["has_more"]) is not bool
            or type(reply["gap"]) is not bool
            or not isinstance(reply["events"], (tuple, list))
        ):
            raise ServiceMarketError("INVALID_READ_AFTER_REPLY")
        reason = reply["gap_reason"]
        if reply["gap"]:
            if (
                type(reason) is not str
                or reason not in {"epoch_mismatch", "cursor_too_old"}
                or reply["events"]
                or reply["next_after"] != after
                or reply["has_more"] is not False
            ):
                raise ServiceMarketError("INVALID_READ_AFTER_REPLY")
            if reason == "epoch_mismatch":
                if reply["epoch"] == self._queued_epoch:
                    raise ServiceMarketError("INVALID_READ_AFTER_REPLY")
            elif (
                reply["epoch"] != self._queued_epoch
                or after >= reply["oldest_sequence"] - 1
            ):
                raise ServiceMarketError("INVALID_READ_AFTER_REPLY")
        elif reason is not None or not after <= reply["next_after"] <= reply["head"]:
            raise ServiceMarketError("INVALID_READ_AFTER_REPLY")

    def _rpc(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        binding: ServiceMarketBindingView | None = None,
    ) -> Mapping[str, Any]:
        if binding is None:
            binding = self._require_binding()
        write_operation = operation in {
            "market.subscribe",
            "market.unsubscribe",
            _BATCH_BOOTSTRAP_OPERATION,
        }
        previous_remote_active = self._remote_active
        previous_remote_unknown = self._remote_unknown
        if write_operation:
            self._remote_unknown = True
        try:
            result = self._runtime.call(
                self.mount,
                operation,
                arguments,
                scope_ref=binding.scope_ref,
            )
        except ServiceRuntimeRequestNotSentError as error:
            self._remote_active = previous_remote_active
            self._remote_unknown = previous_remote_unknown
            raise ServiceMarketError("CONTROL_REQUEST_NOT_SENT", cause_code=error.code) from None
        except ServiceCallOutcomeUnknownError as error:
            if write_operation:
                self._set_unsubscription_uncertain(binding.registration_id)
                raise ServiceMarketError("CONTROL_OUTCOME_UNKNOWN", cause_code=error.code) from None
            raise ServiceMarketError("CONTROL_CALL_FAILED", cause_code=error.code) from None
        except ServiceRuntimeError as error:
            self._remote_active = previous_remote_active
            self._remote_unknown = previous_remote_unknown
            # The runtime uses ServiceCallOutcomeUnknownError for dispatched
            # calls whose handler result cannot prove whether a side effect
            # occurred. Other finite runtime/service errors are pre-send or
            # explicit service rejections.
            raise ServiceMarketError("CONTROL_CALL_FAILED", cause_code=error.code) from None
        except TimeoutError:
            if write_operation:
                self._remote_unknown = True
                self._set_unsubscription_uncertain(binding.registration_id)
                raise ServiceMarketError("CONTROL_OUTCOME_UNKNOWN", cause_code="TIMEOUT") from None
            raise ServiceMarketError("CONTROL_CALL_FAILED", cause_code="TIMEOUT") from None
        except Exception:
            if write_operation:
                self._remote_unknown = True
                self._set_unsubscription_uncertain(binding.registration_id)
                raise ServiceMarketError(
                    "CONTROL_OUTCOME_UNKNOWN", cause_code="TRANSPORT_FAILURE"
                ) from None
            raise ServiceMarketError(
                "CONTROL_CALL_FAILED", cause_code="TRANSPORT_FAILURE"
            ) from None
        if not isinstance(result, Mapping):
            if write_operation:
                self._set_unsubscription_uncertain(binding.registration_id)
            raise ServiceMarketError("INVALID_CONTROL_REPLY")
        if operation == "market.unsubscribe":
            try:
                self._validate_unsubscribe_reply(result)
            except ServiceMarketError:
                self._set_unsubscription_uncertain(binding.registration_id)
                raise
            self._remote_active = False
            self._remote_unknown = False
            if self._uncertain_registration_id in (None, binding.registration_id):
                self._unsubscription_uncertain = False
                self._uncertain_registration_id = None
        return result

    def _validate_unsubscribe_reply(self, reply: Mapping[str, Any]) -> None:
        _exact_fields(reply, {"stream_id", "epoch", "head", "upstream_released"})
        _validate_stream_metadata(reply, key=self.key, oldest_required=False)
        if type(reply["upstream_released"]) is not bool:
            raise ServiceMarketError("INVALID_UNSUBSCRIBE_REPLY")

    def _try_unsubscribe(
        self, binding: ServiceMarketBindingView, *, preserve_uncertainty: bool
    ) -> bool:
        try:
            self._rpc("market.unsubscribe", {"key": self.key.to_dict()})
        except ServiceMarketError:
            self._set_unsubscription_uncertain(binding.registration_id)
            return False
        if preserve_uncertainty:
            self._set_unsubscription_uncertain(binding.registration_id)
        return True

    def _cleanup_failed_start(self) -> None:
        binding = self._binding
        if binding is not None and (self._remote_active or self._remote_unknown):
            self._try_unsubscribe(binding, preserve_uncertainty=False)
        if self._subscriber is not None:
            try:
                self._close_subscriber()
            except BaseException:
                self._state = "close_failed"
                return
        self._state = "failed"

    def _close_subscriber(self) -> None:
        subscriber = self._subscriber
        if subscriber is None:
            return
        try:
            subscriber.close()
        except BaseException:
            self._state = "close_failed"
            raise
        self._subscriber = None

    def _make_subscriber(self, binding: ServiceMarketBindingView) -> EventSubscriber:
        try:
            wire_version = _market_wire_version(binding)
            if wire_version == MARKET_WIRE_VERSION_LEGACY:
                factory = self._subscriber_factory
                if not callable(factory):
                    raise ServiceMarketError("LEGACY_SUBSCRIBER_FACTORY_UNSUPPORTED")
                subscriber = factory(binding, self.stream_scope)
            else:
                create_topic_v2 = getattr(self._subscriber_factory, "create_topic_v2", None)
                if not callable(create_topic_v2):
                    raise ServiceMarketError("TOPIC_V2_SUBSCRIBER_FACTORY_UNSUPPORTED")
                subscriber = create_topic_v2(binding, self.stream_scope, self.key)
        except ServiceMarketStartupCleanupError as error:
            self._subscriber = error.transport
            self._state = "close_failed"
            raise
        if not callable(getattr(subscriber, "receive", None)) or not callable(
            getattr(subscriber, "close", None)
        ):
            raise ServiceMarketError("INVALID_SUBSCRIBER")
        return subscriber

    def _get_binding(
        self, *, required_operations: frozenset[str] = _BOOTSTRAP_OPERATIONS
    ) -> ServiceMarketBindingView:
        try:
            return self._runtime.market_binding_view(
                self.mount, required_operations=required_operations
            )
        except ServiceRuntimeError as error:
            raise ServiceMarketError("RUNTIME_BINDING_UNAVAILABLE", cause_code=error.code) from None

    def _get_history_binding(self) -> ServiceMarketBindingView:
        try:
            return self._get_binding(required_operations=_HISTORY_OPERATIONS)
        except ServiceMarketError as error:
            if (
                error.code == "RUNTIME_BINDING_UNAVAILABLE"
                and error.cause_code == "MARKET_BINDING_UNAVAILABLE"
            ):
                raise ServiceMarketError("HISTORY_UNAVAILABLE") from None
            raise

    def _maintain_runtime(self) -> None:
        try:
            self._runtime.maintain()
        except ServiceRuntimeError as error:
            self._recovery_required = True
            raise ServiceMarketError("RUNTIME_MAINTENANCE_FAILED", cause_code=error.code) from None

    def _check_binding(self) -> ServiceMarketBindingView:
        current = self._get_binding()
        previous = self._binding
        if previous is None or not _same_binding(previous, current):
            self._recovery_required = True
            raise ServiceMarketError("RUNTIME_BINDING_CHANGED")
        return current

    def _check_public_domain(self, binding: ServiceMarketBindingView) -> None:
        if self.stream_scope.execution_domain_ref != binding.rpc_scope.execution_domain_ref:
            raise ServiceMarketError("STREAM_DOMAIN_MISMATCH")

    def _require_binding(self) -> ServiceMarketBindingView:
        if self._binding is None:
            raise ServiceMarketError("CLIENT_NOT_BOUND")
        return self._binding

    def _require_subscriber(self) -> EventSubscriber:
        if self._subscriber is None:
            raise ServiceMarketError("SUBSCRIBER_UNAVAILABLE")
        return self._subscriber

    def _set_unsubscription_uncertain(self, registration_id: str) -> None:
        self._unsubscription_uncertain = True
        self._uncertain_registration_id = registration_id

    def _cause_code(self, error: Exception) -> str | None:
        if isinstance(error, ServiceMarketError):
            return error.code
        if isinstance(error, ServiceRuntimeError):
            return error.code
        return None

    def _now(self) -> float:
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ServiceMarketError("INVALID_MONOTONIC_CLOCK")
        return float(value)

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceMarketError("WRONG_OWNER_THREAD")


def _same_binding(left: ServiceMarketBindingView, right: ServiceMarketBindingView) -> bool:
    return (
        left.mount == right.mount
        and left.service_id == right.service_id
        and left.instance_id == right.instance_id
        and left.scope_ref == right.scope_ref
        and left.registration_id == right.registration_id
        and left.service_epoch == right.service_epoch
        and left.transport_ref == right.transport_ref
        and left.peer == right.peer
        and _market_wire_version(left) == _market_wire_version(right)
    )


def _market_wire_version(binding: ServiceMarketBindingView) -> int:
    version = getattr(binding, "market_wire_version", MARKET_WIRE_VERSION_LEGACY)
    if type(version) is not int or version not in _SUPPORTED_MARKET_WIRE_VERSIONS:
        raise ServiceMarketError("UNSUPPORTED_MARKET_WIRE_VERSION")
    return version


def _exact_fields(value: Mapping[str, Any], fields: set[str] | frozenset[str]) -> None:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ServiceMarketError("INVALID_MARKET_REPLY")
    if set(value) != fields:
        raise ServiceMarketError("INVALID_MARKET_REPLY")


def _validate_stream_metadata(
    value: Mapping[str, Any], *, key: MarketSubscriptionKey, oldest_required: bool
) -> None:
    if (
        value.get("stream_id") != key.stream_id
        or type(value.get("epoch")) is not str
        or not value["epoch"]
        or type(value.get("head")) is not int
        or value["head"] < 0
    ):
        raise ServiceMarketError("INVALID_MARKET_REPLY")
    oldest = value.get("oldest_sequence")
    if oldest_required:
        if type(oldest) is not int or oldest < 1 or oldest > value["head"] + 1:
            raise ServiceMarketError("INVALID_MARKET_REPLY")
    elif oldest is not None and (
        type(oldest) is not int or oldest < 1 or oldest > value["head"] + 1
    ):
        raise ServiceMarketError("INVALID_MARKET_REPLY")


def _decode_envelope_mapping(raw: Any) -> WireEnvelope:
    try:
        plain = _plain_json(raw)
        encoded = json.dumps(
            plain,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
        return decode_envelope(encoded)
    except Exception:
        raise ServiceMarketError("INVALID_MARKET_ENVELOPE") from None


def _plain_json(value: Any) -> Any:
    if value is None or type(value) in (str, bool, int, float):
        if type(value) is float and not math.isfinite(value):
            raise ValueError
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError
        return {key: _plain_json(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_plain_json(item) for item in value]
    raise TypeError


__all__ = [
    "MarketBootstrap",
    "ServiceMarketClient",
    "ServiceMarketError",
    "ServiceMarketStartupCleanupError",
    "TopicV2SubscriberFactory",
    "MARKET_WIRE_VERSION_LEGACY",
    "MARKET_WIRE_VERSION_TOPIC_FILTERED",
    "ZmqMarketTransportFactory",
]
