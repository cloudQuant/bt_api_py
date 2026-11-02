"""Single-owner SDK market stream over exact route-bound tick projectors.

The owner drives the existing authenticated tick projector on its creating
thread. It retains neutral typed events, source descriptors and wire cursors,
and bounds each consumer queue. Orderbook and bar are intentionally absent
until their service adapters provide the same source/cursor contract.

The legacy ``poll_events`` orderbook/bar path remains outside this owner. Its
normalizer may fill missing observation timestamps from the local process and
derive an ``event_id`` from normalized fields; neither fact proves provider
source identity, a subscription receipt, or a resumable cursor. Enabling those
channels here requires an exact-route source receipt, provider epoch/sequence
cursor, and explicit recovery semantics from the service/provider boundary.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Deque, Optional

from bt_api_base.market_protocol import MarketSourceDescriptor, MarketSubscriptionKey
from bt_api_base.market_tick_protocol import MarketEventCursor

from .market_stream_contracts import (
    MarketStreamError,
    MarketStreamPollResult,
    MarketStreamRequest,
    MarketStreamState,
    MarketStreamSubscription,
)
from .market_tick_contracts import MarketTickEvent
from .service_market_tick_projection import (
    MarketTickIdentityStatus,
    MarketTickStreamIdentity,
    ServiceMarketTickBootstrapProjection,
    ServiceMarketTickProjector,
)

_MAX_STREAMS = 256
_MAX_SOURCE_READS_PER_POLL = 128
_MAX_POLL_EVENTS = 4096


def _serialized_owner_operation(method: Callable[..., Any]) -> Callable[..., Any]:
    """Serialize live-owner operations with cross-thread terminal close."""

    @wraps(method)
    def wrapped(self, *args: Any, **kwargs: Any) -> Any:
        with self._state_lock:
            return method(self, *args, **kwargs)

    return wrapped


@dataclass
class _SubscriptionState:
    subscription: MarketStreamSubscription
    projector: ServiceMarketTickProjector
    route_client: object
    route_environment: Optional[str]
    source: MarketSourceDescriptor
    observed_cursor: Optional[MarketEventCursor]
    state: MarketStreamState
    recovery_code: Optional[str]
    queue: Deque[MarketTickEvent] = field(default_factory=deque)
    dropped_events: int = 0
    close_attempted: bool = False


class ServiceMarketStream:
    """Own route-bound market subscriptions on one ``BtApi`` owner thread.

    ``poll`` is the sole ingress pump; this class starts no worker and creates
    no second ``BtApi``. A caller may subscribe once to each exact route/key.
    Queue overflow or provider discontinuity clears queued events and moves
    that subscription to ``RECOVERY_REQUIRED`` until the caller explicitly
    acknowledges data loss through ``accept_resync``.

    Projector operations remain owner-thread-only. A terminal ``close`` from
    another thread is allowed only after every projector has already been
    closed successfully on the owner thread.
    """

    def __init__(
        self,
        api: object,
        *,
        wake_callback: Optional[Callable[[], Any]] = None,
    ) -> None:
        if (
            not callable(getattr(api, "market_tick_projector", None))
            or not callable(getattr(api, "_selected_provider_route", None))
        ):
            raise MarketStreamError("MARKET_STREAM_PROJECTOR_UNAVAILABLE")
        if wake_callback is not None and not callable(wake_callback):
            raise MarketStreamError("MARKET_STREAM_PROJECTOR_INVALID")
        self._api = api
        self._wake_callback = wake_callback
        self._owner_thread_id = threading.get_ident()
        self._state_lock = threading.RLock()
        self._subscriptions: dict[MarketStreamSubscription, _SubscriptionState] = {}
        self._logical_subscriptions: dict[tuple[object, ...], MarketStreamSubscription] = {}
        self._uncertain_projectors: list[object] = []
        self._closing = False
        self._closed = False

    @property
    def supported_channels(self) -> tuple[str, ...]:
        return ("tick",)

    @_serialized_owner_operation
    def subscribe(self, request: MarketStreamRequest) -> MarketStreamSubscription:
        self._check_owner()
        if type(request) is not MarketStreamRequest:
            raise MarketStreamError("MARKET_STREAM_REQUEST_INVALID")
        if request.channel != "tick" or request.depth is not None:
            raise MarketStreamError("MARKET_STREAM_CHANNEL_UNAVAILABLE")
        if len(self._subscriptions) >= _MAX_STREAMS:
            raise MarketStreamError("MARKET_STREAM_PROJECTOR_UNAVAILABLE")

        logical_key = (
            request.exchange_name,
            request.instrument,
            request.channel,
            request.depth,
        )
        if logical_key in self._logical_subscriptions:
            raise MarketStreamError("MARKET_STREAM_DUPLICATE_SUBSCRIPTION")

        selected = self._selected_route(request.exchange_name)
        if selected is None:
            raise MarketStreamError("MARKET_STREAM_ROUTE_UNAVAILABLE")
        route_client, route_environment = selected

        projector = None
        try:
            projector = self._api.market_tick_projector(
                request.exchange_name,
                request.instrument,
            )
            if type(projector) is not ServiceMarketTickProjector:
                raise MarketStreamError("MARKET_STREAM_PROJECTOR_INVALID")
            selected_after = self._selected_route(request.exchange_name)
            if (
                selected_after is None
                or selected_after[0] is not route_client
                or selected_after[1] != route_environment
            ):
                raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")
            key = projector.key
            if (
                type(key) is not MarketSubscriptionKey
                or key.instrument != request.instrument
                or key.channel != "tick"
                or key.depth is not None
                or key.timeframe is not None
            ):
                raise MarketStreamError("MARKET_STREAM_PROJECTOR_INVALID")

            bootstrap = projector.start()
            if type(bootstrap) is not ServiceMarketTickBootstrapProjection:
                raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED")
            source = bootstrap.source
            if (
                type(source) is not MarketSourceDescriptor
                or source.key != key
                or source.key.instrument != request.instrument
                or source.key.channel != "tick"
                or projector.source_descriptor != source
            ):
                raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED")
            latest_event = bootstrap.latest_event
            if latest_event is not None and (
                type(latest_event) is not MarketTickEvent
                or latest_event.source != source
                or latest_event.key != key
            ):
                raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED")
            identity = projector.stream_identity
            if (
                type(identity) is not MarketTickStreamIdentity
                or identity.status is not MarketTickIdentityStatus.AVAILABLE
                or identity.source_descriptor != source
                or identity.start_receipt is None
                or identity.start_receipt.source != source
                or identity.projector_state.value == "closed"
                or (latest_event is not None and identity.cursor != latest_event.cursor)
            ):
                raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED")
            selected_after_start = self._selected_route(request.exchange_name)
            if (
                selected_after_start is None
                or selected_after_start[0] is not route_client
                or selected_after_start[1] != route_environment
            ):
                raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")

            cursor = latest_event.cursor if latest_event is not None else identity.cursor
            subscription = MarketStreamSubscription(
                token=object(),
                request=request,
                key=key,
                initial_source=source,
                initial_cursor=cursor,
            )
            bootstrap_resync = bootstrap.resync_required
            projector_resync = projector.resync_required
            if type(bootstrap_resync) is not bool or type(projector_resync) is not bool:
                raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED")
            needs_resync = bootstrap_resync or projector_resync
            state = (
                MarketStreamState.RECOVERY_REQUIRED
                if needs_resync
                else MarketStreamState.ACTIVE
            )
            recovery_code = "PROVIDER_RESYNC_REQUIRED" if needs_resync else None
            binding = _SubscriptionState(
                subscription=subscription,
                projector=projector,
                route_client=route_client,
                route_environment=route_environment,
                source=source,
                observed_cursor=cursor,
                state=state,
                recovery_code=recovery_code,
            )
            if latest_event is not None:
                binding.queue.append(latest_event)
            self._subscriptions[subscription] = binding
            self._logical_subscriptions[logical_key] = subscription
            if latest_event is not None or state is MarketStreamState.RECOVERY_REQUIRED:
                self._wake()
            return subscription
        except MarketStreamError:
            if projector is not None:
                self._close_failed_start(projector)
            raise
        except Exception:
            if projector is not None:
                self._close_failed_start(projector)
            raise MarketStreamError("MARKET_STREAM_PROJECTOR_UNAVAILABLE") from None

    @_serialized_owner_operation
    def poll(
        self,
        subscription: MarketStreamSubscription,
        *,
        timeout_ms: int = 0,
        max_events: int = 100,
    ) -> MarketStreamPollResult:
        self._check_owner()
        if (
            type(timeout_ms) is not int
            or not 0 <= timeout_ms <= 60_000
            or type(max_events) is not int
            or not 1 <= max_events <= _MAX_POLL_EVENTS
        ):
            raise MarketStreamError("MARKET_STREAM_POLL_INVALID")
        binding = self._require_binding(subscription)
        if binding.state is MarketStreamState.CLEANUP_UNVERIFIED:
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")

        delivered: list[MarketTickEvent] = []
        if binding.state is MarketStreamState.ACTIVE:
            self._check_route_current(binding)
            if binding.state is MarketStreamState.ACTIVE:
                self._drain(binding, delivered, max_events)
                if len(delivered) < max_events:
                    self._pump(
                        binding,
                        timeout_ms=timeout_ms if not delivered else 0,
                        max_source_reads=_MAX_SOURCE_READS_PER_POLL,
                    )
                    self._drain(binding, delivered, max_events)
        else:
            self._drain(binding, delivered, max_events)
        return self._result(binding, tuple(delivered))

    @_serialized_owner_operation
    def pump(
        self,
        subscription: MarketStreamSubscription,
        *,
        timeout_ms: int = 0,
        max_source_reads: int = _MAX_SOURCE_READS_PER_POLL,
    ) -> MarketStreamPollResult:
        """Pump source events into the SDK queue without consuming that queue."""
        self._check_owner()
        if (
            type(timeout_ms) is not int
            or not 0 <= timeout_ms <= 60_000
            or type(max_source_reads) is not int
            or not 1 <= max_source_reads <= _MAX_SOURCE_READS_PER_POLL
        ):
            raise MarketStreamError("MARKET_STREAM_POLL_INVALID")
        binding = self._require_binding(subscription)
        if binding.state is MarketStreamState.CLEANUP_UNVERIFIED:
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        if binding.state is MarketStreamState.ACTIVE:
            self._check_route_current(binding)
            if binding.state is MarketStreamState.ACTIVE:
                self._pump(
                    binding,
                    timeout_ms=timeout_ms,
                    max_source_reads=max_source_reads,
                )
        return self._result(binding, ())

    @_serialized_owner_operation
    def stream_identity(self, subscription: MarketStreamSubscription) -> MarketTickStreamIdentity:
        """Return the exact SDK identity receipt from this subscription's projector."""
        self._check_owner()
        binding = self._require_binding(subscription)
        if binding.state is MarketStreamState.CLEANUP_UNVERIFIED:
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        self._check_route_current(binding)
        if binding.recovery_code == "ROUTE_CHANGED":
            raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")
        try:
            identity = binding.projector.stream_identity
        except Exception:
            raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED") from None
        if type(identity) is not MarketTickStreamIdentity:
            raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED")
        if identity.status is MarketTickIdentityStatus.AVAILABLE:
            if (
                identity.source_descriptor != binding.source
                or identity.key != binding.subscription.key
                or identity.start_receipt is None
                or identity.start_receipt.source != binding.source
                or (
                    identity.cursor is not None
                    and (
                        identity.cursor.stream_id != binding.source.stream_id
                        or identity.cursor.epoch != binding.source.epoch
                    )
                )
            ):
                raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED")
        selected_after_identity = self._selected_route(
            binding.subscription.request.exchange_name
        )
        if (
            selected_after_identity is None
            or selected_after_identity[0] is not binding.route_client
            or selected_after_identity[1] != binding.route_environment
        ):
            self._mark_recovery(binding, "ROUTE_CHANGED")
            raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")
        return identity

    @_serialized_owner_operation
    def queue_depth(self, subscription: MarketStreamSubscription) -> int:
        """Read the SDK handoff queue without consuming or pumping the source."""
        self._check_owner()
        binding = self._require_binding(subscription)
        if binding.state is MarketStreamState.CLEANUP_UNVERIFIED:
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        return len(binding.queue)

    @_serialized_owner_operation
    def accept_resync(
        self,
        subscription: MarketStreamSubscription,
        *,
        acknowledge_data_loss: bool,
    ) -> MarketStreamPollResult:
        self._check_owner()
        binding = self._require_binding(subscription)
        if type(acknowledge_data_loss) is not bool or not acknowledge_data_loss:
            raise MarketStreamError("MARKET_STREAM_RESYNC_ACKNOWLEDGEMENT_REQUIRED")
        if binding.state is not MarketStreamState.RECOVERY_REQUIRED:
            raise MarketStreamError("MARKET_STREAM_RECOVERY_NOT_REQUIRED")
        self._check_route_current(binding)
        if binding.recovery_code == "ROUTE_CHANGED":
            raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")

        try:
            bootstrap = binding.projector.accept_resync(acknowledge_data_loss=True)
        except Exception:
            self._mark_recovery(binding, "PROVIDER_RESYNC_FAILED")
            raise MarketStreamError("MARKET_STREAM_RESYNC_UNVERIFIED") from None
        if type(bootstrap) is not ServiceMarketTickBootstrapProjection:
            self._mark_recovery(binding, "RESYNC_BINDING_UNVERIFIED")
            raise MarketStreamError("MARKET_STREAM_RESYNC_UNVERIFIED")
        try:
            source = bootstrap.source
            latest_event = bootstrap.latest_event
            current_source = binding.projector.source_descriptor
            bootstrap_resync = bootstrap.resync_required
            projector_resync = binding.projector.resync_required
        except Exception:
            self._mark_recovery(binding, "RESYNC_BINDING_UNVERIFIED")
            raise MarketStreamError("MARKET_STREAM_RESYNC_UNVERIFIED")
        if (
            type(source) is not MarketSourceDescriptor
            or source.key != binding.subscription.key
            or current_source != source
            or (latest_event is not None and (
                type(latest_event) is not MarketTickEvent
                or latest_event.source != source
                or latest_event.key != source.key
            ))
            or type(bootstrap_resync) is not bool
            or type(projector_resync) is not bool
        ):
            self._mark_recovery(binding, "RESYNC_BINDING_UNVERIFIED")
            raise MarketStreamError("MARKET_STREAM_RESYNC_UNVERIFIED")

        selected_after_resync = self._selected_route(
            binding.subscription.request.exchange_name
        )
        if (
            selected_after_resync is None
            or selected_after_resync[0] is not binding.route_client
            or selected_after_resync[1] != binding.route_environment
        ):
            self._mark_recovery(binding, "ROUTE_CHANGED")
            raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")

        binding.source = source
        binding.observed_cursor = latest_event.cursor if latest_event is not None else None
        binding.dropped_events += len(binding.queue)
        binding.queue.clear()
        if bootstrap_resync or projector_resync:
            binding.state = MarketStreamState.RECOVERY_REQUIRED
            binding.recovery_code = "PROVIDER_RESYNC_REQUIRED"
            return self._result(binding, ())

        binding.state = MarketStreamState.ACTIVE
        binding.recovery_code = None
        if latest_event is not None:
            binding.queue.append(latest_event)
        delivered: list[MarketTickEvent] = []
        self._drain(binding, delivered, 1)
        return self._result(binding, tuple(delivered))

    @_serialized_owner_operation
    def unsubscribe(self, subscription: MarketStreamSubscription) -> bool:
        self._check_owner()
        binding = self._require_binding(subscription)
        if binding.close_attempted:
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        binding.close_attempted = True
        try:
            binding.projector.close()
        except Exception:
            binding.state = MarketStreamState.CLEANUP_UNVERIFIED
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED") from None
        self._remove_binding(binding)
        return True

    @_serialized_owner_operation
    def close(self) -> None:
        if self._closed:
            return
        if threading.get_ident() != self._owner_thread_id:
            if self._subscriptions or self._uncertain_projectors:
                raise MarketStreamError("MARKET_STREAM_OWNER_THREAD_MISMATCH")
            # Once every provider projector has already closed on its owner
            # thread, terminal API teardown owns no thread-bound client.
            self._closing = True
            self._closed = True
            self._wake()
            return
        self._closing = True
        failed = False
        for binding in tuple(self._subscriptions.values()):
            if binding.close_attempted:
                failed = True
                continue
            binding.close_attempted = True
            try:
                binding.projector.close()
            except Exception:
                binding.state = MarketStreamState.CLEANUP_UNVERIFIED
                failed = True
                continue
            self._remove_binding(binding)
        if failed or self._uncertain_projectors:
            self._wake()
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        self._closed = True
        self._wake()

    def _pump(
        self,
        binding: _SubscriptionState,
        *,
        timeout_ms: int,
        max_source_reads: int,
    ) -> None:
        for index in range(max_source_reads):
            if binding.state is not MarketStreamState.ACTIVE:
                return
            self._check_route_current(binding)
            if binding.state is not MarketStreamState.ACTIVE:
                return
            if not self._projector_source_matches(binding):
                self._mark_recovery(binding, "EVENT_SOURCE_MISMATCH")
                return
            try:
                event = binding.projector.poll(timeout_ms if index == 0 else 0)
            except Exception:
                if self._projector_requires_resync(binding) is True:
                    self._mark_recovery(binding, "PROVIDER_RESYNC_REQUIRED")
                else:
                    self._mark_recovery(binding, "PROVIDER_POLL_FAILED")
                return
            if event is None:
                if not self._projector_source_matches(binding):
                    self._mark_recovery(binding, "EVENT_SOURCE_MISMATCH")
                elif self._projector_requires_resync(binding) is True:
                    self._mark_recovery(binding, "PROVIDER_RESYNC_REQUIRED")
                elif self._projector_requires_resync(binding) is not False:
                    self._mark_recovery(binding, "PROVIDER_POLL_FAILED")
                return
            if (
                type(event) is not MarketTickEvent
                or event.source != binding.source
                or event.key != binding.subscription.key
                or type(event.cursor) is not MarketEventCursor
                or event.cursor.stream_id != binding.source.stream_id
                or event.cursor.epoch != binding.source.epoch
            ):
                self._mark_recovery(binding, "EVENT_SOURCE_MISMATCH", dropped=1)
                return
            previous = binding.observed_cursor
            if previous is not None and (
                previous.stream_id != event.cursor.stream_id
                or previous.epoch != event.cursor.epoch
                or event.cursor.sequence != previous.sequence + 1
            ):
                self._mark_recovery(binding, "MARKET_CURSOR_DISCONTINUITY", dropped=1)
                return
            binding.observed_cursor = event.cursor
            if len(binding.queue) >= binding.subscription.request.queue_capacity:
                self._mark_recovery(binding, "CONSUMER_QUEUE_OVERFLOW", dropped=1)
                return
            binding.queue.append(event)
            self._wake()
            if len(binding.queue) >= binding.subscription.request.queue_capacity:
                try:
                    extra = binding.projector.poll(0)
                except Exception:
                    if self._projector_requires_resync(binding) is True:
                        self._mark_recovery(binding, "PROVIDER_RESYNC_REQUIRED")
                    else:
                        self._mark_recovery(binding, "PROVIDER_POLL_FAILED")
                    return
                if extra is not None:
                    self._mark_recovery(binding, "CONSUMER_QUEUE_OVERFLOW", dropped=1)
                elif not self._projector_source_matches(binding):
                    self._mark_recovery(binding, "EVENT_SOURCE_MISMATCH")
                elif self._projector_requires_resync(binding) is True:
                    self._mark_recovery(binding, "PROVIDER_RESYNC_REQUIRED")
                elif self._projector_requires_resync(binding) is not False:
                    self._mark_recovery(binding, "PROVIDER_POLL_FAILED")
                return

    def _drain(
        self,
        binding: _SubscriptionState,
        delivered: list[MarketTickEvent],
        maximum: int,
    ) -> None:
        while binding.queue and len(delivered) < maximum:
            delivered.append(binding.queue.popleft())

    def _result(
        self,
        binding: _SubscriptionState,
        events: tuple[MarketTickEvent, ...],
    ) -> MarketStreamPollResult:
        cursor = events[-1].cursor if events else None
        return MarketStreamPollResult(
            subscription=binding.subscription,
            source=binding.source,
            cursor=cursor,
            events=events,
            state=binding.state,
            queue_depth=len(binding.queue),
            dropped_events=binding.dropped_events,
            recovery_code=binding.recovery_code,
        )

    def _mark_recovery(
        self,
        binding: _SubscriptionState,
        code: str,
        *,
        dropped: int = 0,
    ) -> None:
        binding.dropped_events += len(binding.queue) + dropped
        binding.queue.clear()
        binding.state = MarketStreamState.RECOVERY_REQUIRED
        binding.recovery_code = code
        self._wake()

    def _check_route_current(self, binding: _SubscriptionState) -> None:
        selected = self._selected_route(binding.subscription.request.exchange_name)
        if (
            selected is None
            or selected[0] is not binding.route_client
            or selected[1] != binding.route_environment
        ):
            self._mark_recovery(binding, "ROUTE_CHANGED")

    def _selected_route(self, exchange_name: str):
        try:
            selected = self._api._selected_provider_route(exchange_name)
        except Exception:
            return None
        if (
            type(selected) is not tuple
            or len(selected) != 2
            or selected[0] is None
            or (selected[1] is not None and type(selected[1]) is not str)
        ):
            return None
        return selected

    @staticmethod
    def _projector_source_matches(binding: _SubscriptionState) -> bool:
        try:
            source = binding.projector.source_descriptor
        except Exception:
            return False
        return type(source) is MarketSourceDescriptor and source == binding.source

    @staticmethod
    def _projector_requires_resync(binding: _SubscriptionState) -> Optional[bool]:
        try:
            value = binding.projector.resync_required
        except Exception:
            return None
        return value if type(value) is bool else None

    def _wake(self) -> None:
        if self._wake_callback is not None:
            try:
                self._wake_callback()
            except Exception:
                # A wake failure does not retract a source event already
                # queued; the next bounded poll can still consume it.
                pass

    def _require_binding(self, subscription: object) -> _SubscriptionState:
        if type(subscription) is not MarketStreamSubscription:
            raise MarketStreamError("MARKET_STREAM_SUBSCRIPTION_UNKNOWN")
        binding = self._subscriptions.get(subscription)
        if binding is None or binding.subscription is not subscription:
            raise MarketStreamError("MARKET_STREAM_SUBSCRIPTION_UNKNOWN")
        return binding

    def _remove_binding(self, binding: _SubscriptionState) -> None:
        if self._subscriptions.get(binding.subscription) is binding:
            self._subscriptions.pop(binding.subscription, None)
        logical_key = (
            binding.subscription.request.exchange_name,
            binding.subscription.request.instrument,
            binding.subscription.request.channel,
            binding.subscription.request.depth,
        )
        if self._logical_subscriptions.get(logical_key) is binding.subscription:
            self._logical_subscriptions.pop(logical_key, None)

    def _close_failed_start(self, projector: object) -> None:
        if type(projector) is not ServiceMarketTickProjector:
            # The factory returned a value outside the exact SDK projector
            # contract. It may own resources, so retain the uncertainty and
            # refuse terminal close instead of treating it as a clean start
            # failure.
            self._uncertain_projectors.append(projector)
            self._closing = True
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED") from None
        try:
            projector.close()
        except Exception:
            self._uncertain_projectors.append(projector)
            self._closing = True
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED") from None

    def _check_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise MarketStreamError("MARKET_STREAM_OWNER_THREAD_MISMATCH")
        if self._closed or self._closing:
            raise MarketStreamError("MARKET_STREAM_CLOSED")


__all__ = ["ServiceMarketStream"]
