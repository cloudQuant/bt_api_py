"""Public ``bt_api_py`` identity facade for neutral market-stream contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping

from bt_api_base.market_stream_protocol import (
    DEFAULT_MARKET_STREAM_QUEUE_CAPACITY,
    MAX_MARKET_STREAM_QUEUE_CAPACITY,
    MarketStreamContractError,
    MarketStreamEvent,
    MarketStreamIdentity,
    MarketStreamIdentityReceipt,
    MarketStreamIdentityStatus,
    MarketStreamPollResult,
    MarketStreamPort,
    MarketStreamRequest,
    MarketStreamSelector,
    MarketStreamState,
    MarketStreamSubscription,
)
from bt_api_base.market_orderbook_protocol import (
    MarketOrderBookContractError,
    MarketOrderBookEvent,
    MarketOrderBookLevel,
)
from bt_api_base.market_stream_provider_protocol import (
    MarketStreamChannelCapability,
    MarketStreamProviderAdapter,
    MarketStreamProviderBinding,
    MarketStreamProviderChannelBinding,
    MarketStreamProviderContractError,
    MarketStreamProviderFactory,
    MarketStreamProviderRegistryPort,
    MarketStreamProviderSession,
    SelectedMarketStreamRoute,
)


def orderbook_midpoint(bids, asks):
    """Project a supplied top of book without attesting source or freshness."""
    if not bids or not asks:
        return None, "missing_top_of_book"
    bid, ask = float(bids[0][0]), float(asks[0][0])
    if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask < bid:
        return None, "invalid_top_of_book"
    return (bid + ask) / 2.0, None


def market_event_kind(event: object) -> str:
    """Classify a neutral event without interpreting venue-specific payloads."""
    if isinstance(event, Mapping):
        return str(event.get("kind") or "market").lower()
    if getattr(event, "key", None) is not None and getattr(event, "cursor", None) is not None:
        return "tick"
    return str(getattr(event, "event_type", "market") or "market").lower()


def polled_market_event_header(event, *, error_type):
    """Validate a normalized polled event and return its kind and symbol."""
    if not isinstance(event, Mapping):
        raise error_type("SDK poll_events yielded a non-mapping event")
    kind = event.get("kind")
    if type(kind) is not str or not kind or kind != kind.strip():
        raise error_type("SDK poll_events yielded an event without a canonical kind")
    kind = kind.lower()
    symbol = event.get("symbol")
    return kind, symbol


def normalize_market_channels(channels):
    """Validate an explicit provider-neutral tick/order-book channel selection."""
    if channels is None:
        channels = ("tick",)
    if type(channels) is str:
        channels = (channels,)
    if type(channels) not in (tuple, list) or not channels:
        raise ValueError("market_channels must be a non-empty tick/orderbook sequence")
    normalized = tuple(channels)
    if (
        any(type(channel) is not str or channel not in {"tick", "orderbook"}
            for channel in normalized)
        or len(set(normalized)) != len(normalized)
    ):
        raise ValueError("market_channels may contain unique tick and orderbook names")
    return normalized


def normalize_orderbook_depth(depth):
    """Validate the explicitly configured neutral order-book depth."""
    if depth is None:
        return None
    if type(depth) is not int or not 1 <= depth <= 1000:
        raise ValueError("orderbook_depth must be an exact integer from 1 through 1000")
    return depth


class MarketStreamError(RuntimeError):
    """Finite, value-free failure from the SDK market-stream owner."""

    _CODES = frozenset(
        {
            "MARKET_STREAM_CHANNEL_UNAVAILABLE",
            "MARKET_STREAM_CLOSED",
            "MARKET_STREAM_DUPLICATE_SUBSCRIPTION",
            "MARKET_STREAM_OWNER_THREAD_MISMATCH",
            "MARKET_STREAM_POLL_INVALID",
            "MARKET_STREAM_REQUEST_INVALID",
            "MARKET_STREAM_PROJECTOR_UNAVAILABLE",
            "MARKET_STREAM_PROJECTOR_INVALID",
            "MARKET_STREAM_ROUTE_UNAVAILABLE",
            "MARKET_STREAM_ROUTE_CHANGED",
            "MARKET_STREAM_SUBSCRIPTION_UNKNOWN",
            "MARKET_STREAM_START_UNVERIFIED",
            "MARKET_STREAM_RECOVERY_NOT_REQUIRED",
            "MARKET_STREAM_RESYNC_ACKNOWLEDGEMENT_REQUIRED",
            "MARKET_STREAM_RESYNC_UNVERIFIED",
            "MARKET_STREAM_CLOSE_UNVERIFIED",
            "MARKET_STREAM_PROVIDER_CAPABILITY_UNAVAILABLE",
            "MARKET_STREAM_PROVIDER_CHANNEL_UNAVAILABLE",
            "MARKET_STREAM_PROVIDER_EVENT_INVALID",
            "MARKET_STREAM_PROVIDER_SESSION_INVALID",
            "MARKET_STREAM_PROVIDER_START_UNVERIFIED",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "MARKET_STREAM_PROJECTOR_INVALID"
        super().__init__(self.code)


def market_stream_identity_matches(identity, subscription, channel: str) -> bool:
    """Check neutral identity shape and subscription binding, not its issuer."""
    from .facade_protocol import MarketTickIdentityStatus, MarketTickStreamIdentity
    from .service_contracts import MarketSourceDescriptor

    if channel == "tick":
        identity_type, identity_status = MarketTickStreamIdentity, MarketTickIdentityStatus
    else:
        identity_type, identity_status = MarketStreamIdentityReceipt, MarketStreamIdentityStatus
    return bool(
        type(identity) is identity_type
        and identity.status is identity_status.AVAILABLE
        and type(identity.source_descriptor) is MarketSourceDescriptor
        and identity.source_descriptor.key == subscription.key
        and identity.key == subscription.key
    )


def market_stream_poll_result_valid(result, subscription, channel: str, *, pump_only: bool) -> bool:
    """Check one neutral poll result against its subscription and poll mode."""
    from .market_tick_contracts import MarketTickEvent
    from .service_contracts import MarketSourceDescriptor

    if (
        type(result) is not MarketStreamPollResult
        or result.subscription is not subscription
        or type(result.source) is not MarketSourceDescriptor
        or result.source.key != subscription.key
        or type(result.state) is not MarketStreamState
        or type(result.queue_depth) is not int
        or result.queue_depth < 0
        or type(result.dropped_events) is not int
        or result.dropped_events < 0
        or type(result.events) is not tuple
        or result.cursor != (result.events[-1].cursor if result.events else None)
        or (pump_only and result.events)
        or (not pump_only and len(result.events) > 1)
    ):
        return False
    event_type = MarketTickEvent if channel == "tick" else MarketOrderBookEvent
    return all(
        type(event) is event_type
        and event.source == result.source
        and event.key == subscription.key
        for event in result.events
    )


def market_stream_resync_result_valid(result, subscription, channel: str) -> bool:
    """Check an explicit resync result without asserting provider authority."""
    from .market_tick_contracts import MarketTickEvent

    event_type = MarketTickEvent if channel == "tick" else MarketOrderBookEvent
    return bool(
        type(result) is MarketStreamPollResult
        and result.subscription is subscription
        and result.source.key == subscription.key
        and result.state is MarketStreamState.ACTIVE
        and type(result.queue_depth) is int
        and result.queue_depth >= 0
        and type(result.dropped_events) is int
        and result.dropped_events >= 0
        and all(
            type(event) is event_type
            and event.source == result.source
            and event.key == subscription.key
            for event in result.events
        )
    )


def select_market_stream(api, *, error_type):
    """Select and shape-check the SDK stream port for an existing facade."""
    stream = api.market_stream()
    if not isinstance(stream, MarketStreamPort):
        raise error_type("SDK market-stream selector returned an invalid port")
    return stream


def subscribe_market_stream(stream, request, *, error_type):
    """Create one SDK subscription before the caller retains its token."""
    subscription = stream.subscribe(request)
    if type(subscription) is not MarketStreamSubscription:
        raise error_type("SDK market-stream subscribe returned an invalid handle")
    return subscription


def market_stream_subscription_matches_request(subscription, request, instrument, channel, depth):
    """Check the returned neutral handle against the exact submitted request."""
    from bt_api_py.service_contracts import MarketSubscriptionKey

    return not (
        subscription.request is not request
        or type(subscription.key) is not MarketSubscriptionKey
        or subscription.key.instrument != instrument
        or subscription.key.channel != channel
        or subscription.key.depth != depth
    )


def market_stream_retained_binding_matches(binding, api, route, instrument, channel, depth):
    """Check retained SDK subscription facts without creating provider authority."""
    return not (
        binding.get("api") is not api
        or binding.get("route") != route
        or binding.get("instrument") != instrument
        or binding.get("channel") != channel
        or binding.get("depth") != depth
        or not isinstance(binding.get("stream"), MarketStreamPort)
        or type(binding.get("subscription")) is not MarketStreamSubscription
        or binding["subscription"].request.exchange_name != route
        or binding["subscription"].request.instrument != instrument
        or binding["subscription"].request.channel != channel
        or binding["subscription"].request.depth != depth
    )


def market_stream_port_identity_matches(stream, subscription, channel):
    """Read and check the stream's existing neutral identity receipt."""
    identity = stream.stream_identity(subscription)
    return market_stream_identity_matches(identity, subscription, channel)


def read_market_stream_queue_depth(stream, subscription, *, error_type):
    """Read an exact nonnegative SDK queue depth."""
    depth = stream.queue_depth(subscription)
    if type(depth) is not int or depth < 0:
        raise error_type("SDK market-stream queue depth is invalid")
    return depth


def poll_market_stream(stream, subscription, channel, *, pump_only, error_type):
    """Invoke the SDK poll operation and check its neutral result contract."""
    if type(subscription) is not MarketStreamSubscription:
        raise error_type("SDK market-stream subscription is invalid")
    if pump_only:
        result = stream.pump(subscription, timeout_ms=0)
    else:
        result = stream.poll(subscription, timeout_ms=0, max_events=1)
    if not market_stream_poll_result_valid(result, subscription, channel, pump_only=pump_only):
        raise error_type("SDK market-stream returned an invalid poll result")
    return result


def accept_market_stream_resync(stream, subscription, channel, *, acknowledge_data_loss, error_type):
    """Invoke explicit resync acknowledgement and validate its SDK receipt."""
    result = stream.accept_resync(subscription, acknowledge_data_loss=acknowledge_data_loss)
    if not market_stream_resync_result_valid(result, subscription, channel):
        raise error_type("SDK market-stream recovery result is unverified")
    return result


def unsubscribe_market_stream(stream, subscription, *, error_type):
    """Require a verified SDK unsubscribe without owning token retention."""
    closed = stream.unsubscribe(subscription)
    if type(closed) is not bool or closed is not True:
        raise error_type("SDK market-stream unsubscribe is unverified")


def market_stream_consumer_contracts():
    """Load the neutral identity, subscription, and event types used by consumers."""
    from bt_api_py.facade_protocol import MarketTickIdentityStatus, MarketTickStreamIdentity
    from bt_api_py.market_tick_contracts import MarketTickEvent
    from bt_api_py.service_contracts import MarketSourceDescriptor, MarketSubscriptionKey
    from bt_api_py.service_market_tick_projection import unavailable_market_tick_stream_identity

    return (
        MarketTickIdentityStatus,
        MarketTickStreamIdentity,
        MarketStreamError,
        MarketStreamPollResult,
        MarketStreamPort,
        MarketStreamRequest,
        MarketStreamSelector,
        MarketStreamState,
        MarketStreamSubscription,
        MarketTickEvent,
        MarketSourceDescriptor,
        MarketSubscriptionKey,
        unavailable_market_tick_stream_identity,
        MarketOrderBookEvent,
        MarketStreamIdentityReceipt,
        MarketStreamIdentityStatus,
    )


def decode_market_event_continuity(snapshot_or_delta, continuity, sequence, previous_sequence):
    """Decode neutral event continuity fields without creating source evidence."""
    snapshot_kind = str(snapshot_or_delta or "snapshot").strip().lower()
    status = str(continuity or "unknown").strip().lower()
    try:
        normalized_sequence = int(sequence or 0)
    except (TypeError, ValueError):
        normalized_sequence = 0
    try:
        normalized_previous = (
            int(previous_sequence) if previous_sequence not in (None, "") else None
        )
    except (TypeError, ValueError):
        normalized_previous = None
    return snapshot_kind, status, normalized_sequence, normalized_previous


__all__ = [
    "DEFAULT_MARKET_STREAM_QUEUE_CAPACITY",
    "MAX_MARKET_STREAM_QUEUE_CAPACITY",
    "MarketStreamContractError",
    "MarketStreamEvent",
    "MarketStreamError",
    "MarketStreamIdentityReceipt",
    "MarketStreamIdentityStatus",
    "MarketOrderBookContractError",
    "MarketOrderBookEvent",
    "MarketOrderBookLevel",
    "MarketStreamChannelCapability",
    "MarketStreamProviderAdapter",
    "MarketStreamProviderBinding",
    "MarketStreamProviderChannelBinding",
    "MarketStreamProviderContractError",
    "MarketStreamProviderFactory",
    "MarketStreamProviderRegistryPort",
    "MarketStreamProviderSession",
    "MarketStreamIdentity",
    "MarketStreamPollResult",
    "MarketStreamPort",
    "MarketStreamRequest",
    "MarketStreamSelector",
    "MarketStreamState",
    "MarketStreamSubscription",
    "SelectedMarketStreamRoute",
    "market_stream_identity_matches",
    "select_market_stream",
    "subscribe_market_stream",
    "market_stream_subscription_matches_request",
    "market_stream_retained_binding_matches",
    "market_stream_port_identity_matches",
    "read_market_stream_queue_depth",
    "poll_market_stream",
    "accept_market_stream_resync",
    "unsubscribe_market_stream",
    "market_stream_consumer_contracts",
    "market_event_kind",
    "normalize_market_channels",
    "normalize_orderbook_depth",
    "orderbook_midpoint",
    "polled_market_event_header",
    "market_stream_poll_result_valid",
    "market_stream_resync_result_valid",
    "decode_market_event_continuity",
]
