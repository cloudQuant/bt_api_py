"""SDK owner for exact-route, multi-channel market stream sessions.

This owner provides one provider session per selected route/instrument and
fans mixed provider events into one bounded SDK queue per subscribed channel.
Providers are selected and channel capabilities checked before the first
provider I/O. A channel not advertised by that provider may use its existing
exact typed SDK owner when one exists (currently tick); unsupported channels
never fall through to the legacy ``poll_events`` queue.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable, Deque, Optional

from bt_api_base.market_orderbook_protocol import MarketOrderBookEvent
from bt_api_base.market_protocol import (
    MarketInstrumentKey,
    MarketSourceDescriptor,
    MarketSubscriptionKey,
)
from bt_api_base.market_stream_protocol import (
    MarketStreamIdentityReceipt,
    MarketStreamIdentityStatus,
    MarketStreamPollResult,
    MarketStreamRequest,
    MarketStreamState,
    MarketStreamSubscription,
)
from bt_api_base.market_stream_provider_protocol import (
    MarketStreamProviderBinding,
    MarketStreamProviderChannelBinding,
    MarketStreamProviderContractError,
    MarketStreamProviderSession,
    SelectedMarketStreamRoute,
)
from bt_api_base.market_tick_protocol import MarketEventCursor, MarketTickEvent

from bt_api_py.market_stream_composition import (
    MarketStreamCompositionError,
    MarketStreamProviderRouter,
    default_market_stream_provider_router,
)
from bt_api_py.market_stream_contracts import MarketStreamError

_MAX_PROVIDER_EVENTS = 256
_ACTIVE_CONTINUITY = frozenset({"continuous", "ok", "recovered", "snapshot"})


def _serialized_owner_operation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


@dataclass
class _ProviderChannelState:
    subscription: MarketStreamSubscription
    provider_binding: MarketStreamProviderChannelBinding
    session_state: "_ProviderSessionState"
    source: MarketSourceDescriptor
    observed_cursor: Optional[MarketEventCursor]
    state: MarketStreamState
    recovery_code: Optional[str]
    queue: Deque[object] = field(default_factory=deque)
    dropped_events: int = 0
    unsubscribe_attempted: bool = False


@dataclass
class _ProviderSessionState:
    route: SelectedMarketStreamRoute
    provider_binding: MarketStreamProviderBinding
    session: MarketStreamProviderSession
    channels: dict[MarketStreamSubscription, _ProviderChannelState] = field(
        default_factory=dict
    )
    by_key: dict[MarketSubscriptionKey, _ProviderChannelState] = field(default_factory=dict)
    uncertain: bool = False
    close_attempted: bool = False


class ProviderMarketStreamOwner:
    """Single SDK-owned market stream port for a ``BtApi`` facade."""

    def __init__(
        self,
        api: object,
        *,
        wake_callback: Optional[Callable[[], None]] = None,
        router: MarketStreamProviderRouter | None = None,
    ) -> None:
        self._api = api
        self._wake_callback = wake_callback
        self._router = router or default_market_stream_provider_router()
        self._owner_thread_id = threading.get_ident()
        self._lock = threading.RLock()
        self._sessions: list[_ProviderSessionState] = []
        self._subscriptions: dict[MarketStreamSubscription, _ProviderChannelState] = {}
        self._logical_subscriptions: dict[
            tuple[object, ...], MarketStreamSubscription
        ] = {}
        self._legacy = None
        self._legacy_subscriptions: set[MarketStreamSubscription] = set()
        self._uncertain_sessions: list[object] = []
        self._closing = False
        self._closed = False

    @property
    def supported_channels(self) -> tuple[str, ...]:
        """Return channel names this port can represent; availability is route-bound."""

        return ("tick", "orderbook")

    @_serialized_owner_operation
    def subscribe(self, request: MarketStreamRequest) -> MarketStreamSubscription:
        self._check_owner()
        if type(request) is not MarketStreamRequest:
            raise MarketStreamError("MARKET_STREAM_REQUEST_INVALID")
        if request.channel not in {"tick", "orderbook"}:
            raise MarketStreamError("MARKET_STREAM_CHANNEL_UNAVAILABLE")
        logical_key = self._logical_key(request)
        if logical_key in self._logical_subscriptions:
            raise MarketStreamError("MARKET_STREAM_DUPLICATE_SUBSCRIPTION")

        route = self._selected_route(request.exchange_name, request.instrument)
        existing = self._find_session(route)
        provider_binding = existing.provider_binding if existing is not None else None
        if provider_binding is None:
            try:
                resolved = self._router.resolve_request(route, request)
            except MarketStreamCompositionError as error:
                raise self._map_composition_error(error) from None
            except MarketStreamProviderContractError:
                raise MarketStreamError("MARKET_STREAM_PROVIDER_EVENT_INVALID") from None
            if resolved is None:
                if request.channel == "tick":
                    return self._legacy_subscribe(request, logical_key, route)
                raise MarketStreamError("MARKET_STREAM_CHANNEL_UNAVAILABLE")
            provider_binding, capability = resolved
            if capability is None:
                if request.channel == "tick":
                    return self._legacy_subscribe(request, logical_key, route)
                raise MarketStreamError("MARKET_STREAM_CHANNEL_UNAVAILABLE")
        else:
            capability = self._capability_for(provider_binding, request)
            if capability is None:
                if request.channel == "tick":
                    return self._legacy_subscribe(request, logical_key, route)
                raise MarketStreamError("MARKET_STREAM_CHANNEL_UNAVAILABLE")

        if existing is not None and any(
            state.subscription.request.channel == request.channel
            and state.subscription.request.depth == request.depth
            for state in existing.channels.values()
        ):
            raise MarketStreamError("MARKET_STREAM_DUPLICATE_SUBSCRIPTION")

        session_state = existing
        created_session = False
        if session_state is None:
            try:
                session = provider_binding.adapter.open_session(
                    wake_callback=self._wake,
                )
            except Exception:
                raise MarketStreamError("MARKET_STREAM_PROVIDER_SESSION_INVALID") from None
            if not isinstance(session, MarketStreamProviderSession):
                self._close_unusable_session(session)
                raise MarketStreamError("MARKET_STREAM_PROVIDER_SESSION_INVALID")
            session_state = _ProviderSessionState(
                route=route,
                provider_binding=provider_binding,
                session=session,
            )
            self._sessions.append(session_state)
            created_session = True

        try:
            provider_channel = session_state.session.subscribe(request)
        except Exception:
            if created_session:
                self._discard_empty_session(session_state)
            else:
                self._mark_session_recovery(session_state, "PROVIDER_POLL_FAILED")
            raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED") from None
        if not self._validate_provider_channel(provider_channel, request):
            if type(provider_channel) is MarketStreamProviderChannelBinding:
                self._close_failed_channel(session_state, provider_channel)
            elif created_session:
                self._discard_empty_session(session_state)
            else:
                self._mark_session_recovery(session_state, "EVENT_SOURCE_MISMATCH")
            raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED")

        selected_after = self._selected_route(request.exchange_name, request.instrument)
        if not self._same_route(route, selected_after):
            self._close_failed_channel(session_state, provider_channel)
            raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")

        subscription = MarketStreamSubscription(
            token=object(),
            request=request,
            key=provider_channel.key,
            initial_source=provider_channel.source,
            initial_cursor=provider_channel.initial_cursor,
        )
        state = _ProviderChannelState(
            subscription=subscription,
            provider_binding=provider_channel,
            session_state=session_state,
            source=provider_channel.source,
            observed_cursor=provider_channel.initial_cursor,
            state=(
                MarketStreamState.RECOVERY_REQUIRED
                if provider_channel.recovery_required
                else MarketStreamState.ACTIVE
            ),
            recovery_code=(
                "PROVIDER_RESYNC_REQUIRED" if provider_channel.recovery_required else None
            ),
        )
        if provider_channel.recovery_required:
            state.dropped_events += len(provider_channel.initial_events)
        else:
            for event in provider_channel.initial_events:
                if len(state.queue) >= request.queue_capacity:
                    self._mark_recovery(state, "CONSUMER_QUEUE_OVERFLOW", dropped=1)
                    break
                state.queue.append(event)
        session_state.channels[subscription] = state
        session_state.by_key[provider_channel.key] = state
        self._subscriptions[subscription] = state
        self._logical_subscriptions[logical_key] = subscription
        if state.queue or state.state is MarketStreamState.RECOVERY_REQUIRED:
            self._wake()
        return subscription

    @_serialized_owner_operation
    def poll(
        self,
        subscription: MarketStreamSubscription,
        *,
        timeout_ms: int = 0,
        max_events: int = 100,
    ) -> MarketStreamPollResult:
        self._check_owner()
        self._check_poll_limits(timeout_ms, max_events)
        if subscription in self._legacy_subscriptions:
            return self._require_legacy().poll(
                subscription,
                timeout_ms=timeout_ms,
                max_events=max_events,
            )
        state = self._require_channel(subscription)
        delivered = self._drain(state, max_events)
        if len(delivered) < max_events and self._session_has_active_channels(state.session_state):
            route_current = self._check_session_route(state.session_state)
            if route_current and self._session_has_active_channels(state.session_state):
                self._pump_session(
                    state.session_state,
                    timeout_ms=timeout_ms if not delivered else 0,
                    max_events=min(_MAX_PROVIDER_EVENTS, max_events - len(delivered)),
                )
                delivered.extend(self._drain(state, max_events - len(delivered)))
        return self._result(state, tuple(delivered))

    @_serialized_owner_operation
    def pump(
        self,
        subscription: MarketStreamSubscription,
        *,
        timeout_ms: int = 0,
        max_source_reads: int = 128,
    ) -> MarketStreamPollResult:
        self._check_owner()
        if (
            type(timeout_ms) is not int
            or not 0 <= timeout_ms <= 60_000
            or type(max_source_reads) is not int
            or not 1 <= max_source_reads <= _MAX_PROVIDER_EVENTS
        ):
            raise MarketStreamError("MARKET_STREAM_POLL_INVALID")
        if subscription in self._legacy_subscriptions:
            return self._require_legacy().pump(
                subscription,
                timeout_ms=timeout_ms,
                max_source_reads=max_source_reads,
            )
        state = self._require_channel(subscription)
        if self._session_has_active_channels(state.session_state):
            route_current = self._check_session_route(state.session_state)
            if route_current and self._session_has_active_channels(state.session_state):
                self._pump_session(
                    state.session_state,
                    timeout_ms=timeout_ms,
                    max_events=max_source_reads,
                )
        return self._result(state, ())

    @_serialized_owner_operation
    def stream_identity(self, subscription: MarketStreamSubscription):
        self._check_owner()
        if subscription in self._legacy_subscriptions:
            return self._require_legacy().stream_identity(subscription)
        state = self._require_channel(subscription)
        if not self._check_session_route(state.session_state):
            raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")
        if state.state is MarketStreamState.CLEANUP_UNVERIFIED:
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        return MarketStreamIdentityReceipt(
            status=MarketStreamIdentityStatus.AVAILABLE,
            start_receipt=state.provider_binding,
            source_descriptor=state.source,
            cursor=state.observed_cursor,
            projector_state=state.state,
            resync_required=state.state is MarketStreamState.RECOVERY_REQUIRED,
            unavailable_code=None,
        )

    @_serialized_owner_operation
    def queue_depth(self, subscription: MarketStreamSubscription) -> int:
        self._check_owner()
        if subscription in self._legacy_subscriptions:
            return self._require_legacy().queue_depth(subscription)
        state = self._require_channel(subscription)
        if state.state is MarketStreamState.CLEANUP_UNVERIFIED:
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        return len(state.queue)

    @_serialized_owner_operation
    def accept_resync(
        self,
        subscription: MarketStreamSubscription,
        *,
        acknowledge_data_loss: bool,
    ) -> MarketStreamPollResult:
        self._check_owner()
        if subscription in self._legacy_subscriptions:
            return self._require_legacy().accept_resync(
                subscription,
                acknowledge_data_loss=acknowledge_data_loss,
            )
        state = self._require_channel(subscription)
        if type(acknowledge_data_loss) is not bool or not acknowledge_data_loss:
            raise MarketStreamError("MARKET_STREAM_RESYNC_ACKNOWLEDGEMENT_REQUIRED")
        if state.state is not MarketStreamState.RECOVERY_REQUIRED:
            raise MarketStreamError("MARKET_STREAM_RECOVERY_NOT_REQUIRED")
        if not self._check_session_route(state.session_state):
            raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")
        try:
            binding = state.session_state.session.resync(
                state.provider_binding,
                acknowledge_data_loss=True,
            )
        except Exception:
            self._mark_session_recovery(state.session_state, "PROVIDER_RESYNC_FAILED")
            raise MarketStreamError("MARKET_STREAM_RESYNC_UNVERIFIED") from None
        if (
            not self._validate_provider_channel(binding, state.subscription.request)
            or binding.key != state.subscription.key
            or binding.source.stream_id != state.source.stream_id
            or binding.source.epoch == state.source.epoch
            or binding.recovery_required
        ):
            self._mark_session_recovery(state.session_state, "RESYNC_BINDING_UNVERIFIED")
            raise MarketStreamError("MARKET_STREAM_RESYNC_UNVERIFIED")
        if state.subscription.request.channel == "orderbook":
            if (
                len(binding.initial_events) != 1
                or type(binding.initial_events[0]) is not MarketOrderBookEvent
                or binding.initial_events[0].snapshot_or_delta != "snapshot"
                or binding.initial_events[0].recovery_snapshot is not True
                or binding.initial_events[0].stale
                or binding.initial_events[0].continuity_status not in _ACTIVE_CONTINUITY
            ):
                self._mark_session_recovery(state.session_state, "RESYNC_BINDING_UNVERIFIED")
                raise MarketStreamError("MARKET_STREAM_RESYNC_UNVERIFIED")

        state.dropped_events += len(state.queue)
        state.queue.clear()
        state.provider_binding = binding
        state.source = binding.source
        state.observed_cursor = binding.initial_cursor
        state.state = MarketStreamState.ACTIVE
        state.recovery_code = None
        for event in binding.initial_events:
            if len(state.queue) >= state.subscription.request.queue_capacity:
                self._mark_recovery(state, "CONSUMER_QUEUE_OVERFLOW", dropped=1)
                break
            state.queue.append(event)
        state.session_state.by_key[binding.key] = state
        self._wake()
        return self._result(state, tuple(self._drain(state, 1)))

    @_serialized_owner_operation
    def unsubscribe(self, subscription: MarketStreamSubscription) -> bool:
        self._check_owner()
        if subscription in self._legacy_subscriptions:
            result = self._require_legacy().unsubscribe(subscription)
            if result is True:
                self._legacy_subscriptions.discard(subscription)
                self._logical_subscriptions.pop(self._logical_key(subscription.request), None)
                return True
            return False
        state = self._require_channel(subscription)
        if state.unsubscribe_attempted:
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        state.unsubscribe_attempted = True
        try:
            result = state.session_state.session.unsubscribe(state.provider_binding)
        except Exception:
            result = False
        if result is not True:
            state.state = MarketStreamState.CLEANUP_UNVERIFIED
            state.session_state.uncertain = True
            self._uncertain_sessions.append(state.session_state.session)
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        self._remove_channel(state)
        if not state.session_state.channels:
            self._close_session(state.session_state)
        return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if threading.get_ident() != self._owner_thread_id:
                if self._sessions or self._uncertain_sessions or self._legacy_subscriptions:
                    raise MarketStreamError("MARKET_STREAM_OWNER_THREAD_MISMATCH")
                self._closing = True
                if self._legacy is not None:
                    try:
                        self._legacy.close()
                    except Exception:
                        raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED") from None
                self._closed = True
                self._wake()
                return
            self._closing = True
            failures = False
            for session_state in tuple(self._sessions):
                if session_state.uncertain:
                    failures = True
                    continue
                for state in tuple(session_state.channels.values()):
                    if state.unsubscribe_attempted:
                        failures = True
                        continue
                    state.unsubscribe_attempted = True
                    try:
                        result = session_state.session.unsubscribe(state.provider_binding)
                    except Exception:
                        result = False
                    if result is not True:
                        state.state = MarketStreamState.CLEANUP_UNVERIFIED
                        session_state.uncertain = True
                        self._uncertain_sessions.append(session_state.session)
                        failures = True
                        break
                    self._remove_channel(state)
                if not session_state.channels and not session_state.uncertain:
                    try:
                        closed = session_state.session.close()
                    except Exception:
                        closed = False
                    if closed is True:
                        self._remove_session(session_state)
                    else:
                        session_state.uncertain = True
                        self._uncertain_sessions.append(session_state.session)
                        failures = True
            if self._legacy is not None:
                try:
                    self._legacy.close()
                except Exception:
                    failures = True
            if failures or self._sessions or self._uncertain_sessions:
                self._wake()
                raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
            self._closed = True
            self._wake()

    def _pump_session(
        self,
        session_state: _ProviderSessionState,
        *,
        timeout_ms: int,
        max_events: int,
    ) -> None:
        if session_state.uncertain:
            self._mark_session_recovery(session_state, "PROVIDER_POLL_FAILED")
            return
        try:
            events = session_state.session.poll(
                timeout_ms=timeout_ms,
                max_events=max_events,
            )
        except Exception:
            self._mark_session_recovery(session_state, "PROVIDER_POLL_FAILED")
            return
        if type(events) is not tuple or len(events) > max_events:
            self._mark_session_recovery(session_state, "EVENT_SOURCE_MISMATCH")
            return
        for event in events:
            if not self._dispatch_event(session_state, event):
                self._mark_session_recovery(session_state, "EVENT_SOURCE_MISMATCH")
                return

    def _dispatch_event(self, session_state: _ProviderSessionState, event: object) -> bool:
        if not isinstance(event, (MarketTickEvent, MarketOrderBookEvent)):
            return False
        key = getattr(event, "key", None)
        state = session_state.by_key.get(key)
        if state is None:
            return False
        if not self._validate_event(state, event):
            self._mark_session_recovery(session_state, "EVENT_SOURCE_MISMATCH")
            return True
        previous = state.observed_cursor
        expected = previous.sequence + 1 if previous is not None else 1
        if event.cursor.sequence != expected:
            self._mark_recovery(state, "MARKET_CURSOR_DISCONTINUITY", dropped=1)
            return True
        state.observed_cursor = event.cursor
        if state.state is MarketStreamState.RECOVERY_REQUIRED:
            state.dropped_events += 1
            return True
        if len(state.queue) >= state.subscription.request.queue_capacity:
            self._mark_recovery(state, "CONSUMER_QUEUE_OVERFLOW", dropped=1)
            return True
        state.queue.append(event)
        if type(event) is MarketOrderBookEvent and (
            event.stale or event.continuity_status not in _ACTIVE_CONTINUITY
        ):
            state.state = MarketStreamState.RECOVERY_REQUIRED
            state.recovery_code = "PROVIDER_RESYNC_REQUIRED"
        self._wake()
        return True

    def _validate_event(self, state: _ProviderChannelState, event: object) -> bool:
        expected_type = (
            MarketTickEvent
            if state.subscription.request.channel == "tick"
            else MarketOrderBookEvent
        )
        if type(event) is not expected_type:
            return False
        try:
            source = event.source
            cursor = event.cursor
            key = event.key
        except Exception:
            return False
        return (
            type(source) is MarketSourceDescriptor
            and source == state.source
            and type(cursor) is MarketEventCursor
            and type(key) is MarketSubscriptionKey
            and key == state.subscription.key
            and cursor.stream_id == source.stream_id
            and cursor.epoch == source.epoch
        )

    def _validate_provider_channel(
        self,
        binding: object,
        request: MarketStreamRequest,
    ) -> bool:
        if type(binding) is not MarketStreamProviderChannelBinding:
            return False
        if binding.request != request:
            return False
        expected_type = MarketTickEvent if request.channel == "tick" else MarketOrderBookEvent
        return all(type(event) is expected_type for event in binding.initial_events)

    def _selected_route(
        self,
        exchange_name: str,
        instrument: MarketInstrumentKey,
    ) -> SelectedMarketStreamRoute:
        try:
            selected = self._api._selected_provider_route(exchange_name)
        except Exception:
            selected = None
        if (
            type(selected) is not tuple
            or len(selected) != 2
            or selected[0] is None
            or (selected[1] is not None and type(selected[1]) is not str)
        ):
            raise MarketStreamError("MARKET_STREAM_ROUTE_UNAVAILABLE")
        feed, environment = selected
        try:
            current = getattr(self._api, "exchange_feeds", {}).get(exchange_name)
            pinned = getattr(self._api, "_selected_route_clients", {}).get(exchange_name)
            if current is not feed or pinned is not feed:
                raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")
            return SelectedMarketStreamRoute(
                exchange_name=exchange_name,
                client=feed,
                instrument=instrument,
                environment=environment,
            )
        except MarketStreamError:
            raise
        except Exception:
            raise MarketStreamError("MARKET_STREAM_ROUTE_UNAVAILABLE") from None

    def _check_session_route(self, session_state: _ProviderSessionState) -> bool:
        request = next(iter(session_state.channels.values())).subscription.request if session_state.channels else None
        if request is None:
            return False
        try:
            current = self._selected_route(request.exchange_name, request.instrument)
        except MarketStreamError:
            current = None
        if current is None or not self._same_route(session_state.route, current):
            self._mark_session_recovery(session_state, "ROUTE_CHANGED")
            return False
        return True

    @staticmethod
    def _same_route(
        left: SelectedMarketStreamRoute,
        right: SelectedMarketStreamRoute | None,
    ) -> bool:
        return (
            right is not None
            and left.exchange_name == right.exchange_name
            and left.client is right.client
            and left.instrument == right.instrument
            and left.environment == right.environment
        )

    def _find_session(self, route: SelectedMarketStreamRoute) -> _ProviderSessionState | None:
        for state in self._sessions:
            if self._same_route(state.route, route) and not state.uncertain:
                return state
        return None

    def _capability_for(
        self,
        provider_binding: MarketStreamProviderBinding,
        request: MarketStreamRequest,
    ):
        try:
            capabilities = provider_binding.adapter.capabilities()
        except Exception:
            raise MarketStreamError("MARKET_STREAM_PROVIDER_CAPABILITY_UNAVAILABLE") from None
        if type(capabilities) is not tuple:
            raise MarketStreamError("MARKET_STREAM_PROVIDER_CAPABILITY_UNAVAILABLE")
        matches = [
            capability
            for capability in capabilities
            if getattr(capability, "supports", lambda _request: False)(request)
        ]
        return matches[0] if len(matches) == 1 else None

    def _legacy_subscribe(
        self,
        request: MarketStreamRequest,
        logical_key: tuple[object, ...],
        expected_route: SelectedMarketStreamRoute,
    ) -> MarketStreamSubscription:
        if request.channel != "tick":
            raise MarketStreamError("MARKET_STREAM_CHANNEL_UNAVAILABLE")
        legacy = self._require_legacy()
        try:
            subscription = legacy.subscribe(request)
        except MarketStreamError:
            raise
        except Exception:
            raise MarketStreamError("MARKET_STREAM_START_UNVERIFIED") from None
        selected_after = self._selected_route(request.exchange_name, request.instrument)
        if not self._same_route(expected_route, selected_after):
            try:
                closed = legacy.unsubscribe(subscription)
            except Exception:
                closed = False
            if closed is not True:
                raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED") from None
            raise MarketStreamError("MARKET_STREAM_ROUTE_CHANGED")
        self._legacy_subscriptions.add(subscription)
        self._logical_subscriptions[logical_key] = subscription
        return subscription

    def _require_legacy(self):
        if self._legacy is None:
            from bt_api_py.service_market_stream import ServiceMarketStream

            self._legacy = ServiceMarketStream(
                self._api,
                wake_callback=self._wake_callback,
            )
        return self._legacy

    def _remove_channel(self, state: _ProviderChannelState) -> None:
        session_state = state.session_state
        session_state.channels.pop(state.subscription, None)
        if session_state.by_key.get(state.subscription.key) is state:
            session_state.by_key.pop(state.subscription.key, None)
        self._subscriptions.pop(state.subscription, None)
        self._logical_subscriptions.pop(self._logical_key(state.subscription.request), None)

    def _close_session(self, session_state: _ProviderSessionState) -> None:
        if session_state.close_attempted:
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        session_state.close_attempted = True
        try:
            closed = session_state.session.close()
        except Exception:
            closed = False
        if closed is not True:
            session_state.uncertain = True
            self._uncertain_sessions.append(session_state.session)
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        self._remove_session(session_state)

    def _discard_empty_session(self, session_state: _ProviderSessionState) -> None:
        if session_state.channels:
            self._mark_session_recovery(session_state, "PROVIDER_POLL_FAILED")
            return
        try:
            closed = session_state.session.close()
        except Exception:
            closed = False
        if closed is True:
            self._remove_session(session_state)
        else:
            session_state.uncertain = True
            self._closing = True
            self._uncertain_sessions.append(session_state.session)

    def _close_failed_channel(
        self,
        session_state: _ProviderSessionState,
        binding: MarketStreamProviderChannelBinding,
    ) -> None:
        try:
            closed = session_state.session.unsubscribe(binding)
        except Exception:
            closed = False
        if closed is True:
            if not session_state.channels:
                self._close_session(session_state)
            return
        session_state.uncertain = True
        self._uncertain_sessions.append(session_state.session)
        if session_state.channels:
            self._mark_session_recovery(session_state, "EVENT_SOURCE_MISMATCH")
        raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")

    def _remove_session(self, session_state: _ProviderSessionState) -> None:
        try:
            self._sessions.remove(session_state)
        except ValueError:
            pass

    def _mark_recovery(
        self,
        state: _ProviderChannelState,
        code: str,
        *,
        dropped: int = 0,
    ) -> None:
        state.dropped_events += len(state.queue) + dropped
        state.queue.clear()
        state.state = MarketStreamState.RECOVERY_REQUIRED
        state.recovery_code = code
        self._wake()

    def _mark_session_recovery(
        self,
        session_state: _ProviderSessionState,
        code: str,
    ) -> None:
        for state in tuple(session_state.channels.values()):
            if state.state is not MarketStreamState.CLEANUP_UNVERIFIED:
                self._mark_recovery(state, code)

    @staticmethod
    def _session_has_active_channels(session_state: _ProviderSessionState) -> bool:
        return any(
            state.state is not MarketStreamState.CLEANUP_UNVERIFIED
            for state in session_state.channels.values()
        )

    @staticmethod
    def _logical_key(request: MarketStreamRequest) -> tuple[object, ...]:
        return (
            request.exchange_name,
            request.instrument,
            request.channel,
            request.depth,
        )

    @staticmethod
    def _drain(state: _ProviderChannelState, maximum: int) -> list[object]:
        result = []
        while state.queue and len(result) < maximum:
            result.append(state.queue.popleft())
        return result

    @staticmethod
    def _result(
        state: _ProviderChannelState,
        events: tuple[object, ...],
    ) -> MarketStreamPollResult:
        return MarketStreamPollResult(
            subscription=state.subscription,
            source=state.source,
            cursor=events[-1].cursor if events else None,
            events=events,
            state=state.state,
            queue_depth=len(state.queue),
            dropped_events=state.dropped_events,
            recovery_code=state.recovery_code,
        )

    def _require_channel(self, subscription: object) -> _ProviderChannelState:
        if type(subscription) is not MarketStreamSubscription:
            raise MarketStreamError("MARKET_STREAM_SUBSCRIPTION_UNKNOWN")
        state = self._subscriptions.get(subscription)
        if state is None or state.subscription is not subscription:
            raise MarketStreamError("MARKET_STREAM_SUBSCRIPTION_UNKNOWN")
        if state.state is MarketStreamState.CLEANUP_UNVERIFIED:
            raise MarketStreamError("MARKET_STREAM_CLOSE_UNVERIFIED")
        return state

    @staticmethod
    def _check_poll_limits(timeout_ms: object, max_events: object) -> None:
        if (
            type(timeout_ms) is not int
            or not 0 <= timeout_ms <= 60_000
            or type(max_events) is not int
            or not 1 <= max_events <= _MAX_PROVIDER_EVENTS
        ):
            raise MarketStreamError("MARKET_STREAM_POLL_INVALID")

    def _close_unusable_session(self, session: object) -> None:
        close = getattr(session, "close", None)
        if not callable(close):
            self._uncertain_sessions.append(session)
            self._closing = True
            return
        try:
            closed = close()
        except Exception:
            closed = False
        if closed is not True:
            self._uncertain_sessions.append(session)
            self._closing = True

    def _check_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise MarketStreamError("MARKET_STREAM_OWNER_THREAD_MISMATCH")
        if self._closed or self._closing:
            raise MarketStreamError("MARKET_STREAM_CLOSED")

    def _wake(self) -> None:
        if self._wake_callback is not None:
            try:
                self._wake_callback()
            except Exception:
                pass

    @staticmethod
    def _map_composition_error(error: MarketStreamCompositionError) -> MarketStreamError:
        code_map = {
            "MARKET_STREAM_ROUTE_INVALID": "MARKET_STREAM_REQUEST_INVALID",
            "MARKET_STREAM_ROUTE_UNAVAILABLE": "MARKET_STREAM_ROUTE_UNAVAILABLE",
            "MARKET_STREAM_ROUTE_CHANGED": "MARKET_STREAM_ROUTE_CHANGED",
            "MARKET_STREAM_PROVIDER_CAPABILITY_UNAVAILABLE": "MARKET_STREAM_CHANNEL_UNAVAILABLE",
            "MARKET_STREAM_PROVIDER_BINDING_INVALID": "MARKET_STREAM_PROVIDER_EVENT_INVALID",
            "MARKET_STREAM_PROVIDER_ADAPTER_INVALID": "MARKET_STREAM_PROVIDER_SESSION_INVALID",
            "MARKET_STREAM_PROVIDER_CAPABILITY_INVALID": "MARKET_STREAM_PROVIDER_SESSION_INVALID",
            "MARKET_STREAM_PROVIDER_INITIALIZATION_FAILED": "MARKET_STREAM_PROVIDER_SESSION_INVALID",
            "MARKET_STREAM_PROVIDER_DISCOVERY_FAILED": "MARKET_STREAM_PROVIDER_SESSION_INVALID",
        }
        return MarketStreamError(code_map.get(error.code, "MARKET_STREAM_PROVIDER_SESSION_INVALID"))


__all__ = ["ProviderMarketStreamOwner"]
