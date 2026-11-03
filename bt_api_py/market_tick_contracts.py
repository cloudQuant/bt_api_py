"""Public bt_api_py facade for provider-neutral market tick contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from types import MappingProxyType

from bt_api_base.market_tick_protocol import (
    DataClockBinding,
    MARKET_TICK_SCHEMA,
    MARKET_TICK_SCHEMA_VERSION,
    MarketClockQuality,
    MarketEventCursor,
    MarketFreshnessStatus,
    MarketTickContractError,
    MarketTickEvent,
    MarketTickPayload,
    bind_market_tick_event,
)


class MarketTickQualificationError(ValueError):
    """Finite classification of an unqualified neutral market-tick event."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# Per-type "is a Mapping" verdicts. ``isinstance(x, Mapping)`` goes through the
# ABC machinery on every call, which is measurable on the per-tick hot path.
# The verdict depends only on the type; a type registered with
# ``Mapping.register`` after its first tick here would keep the old verdict.
_IS_MAPPING_TYPE = {dict: True, MappingProxyType: True}


def _is_mapping(value):
    kind = type(value)
    verdict = _IS_MAPPING_TYPE.get(kind)
    if verdict is None:
        verdict = _IS_MAPPING_TYPE[kind] = isinstance(value, Mapping)
    return verdict


def _tick_value(tick, *names, default=None):
    if _is_mapping(tick):
        for name in names:
            if name in tick and tick[name] is not None:
                return tick[name]
        return default
    for name in names:
        value = getattr(tick, name, None)
        if value is not None:
            return value
    return default


def _set_tick_value(tick, name, value):
    if isinstance(tick, dict):
        tick[name] = value
    else:
        setattr(tick, name, value)


def finite_market_number(value):
    """Normalize a neutral market value without asserting its provenance."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or abs(number) >= 1.0e50:
        return None
    return number


def classify_market_scope(
    *, typed_market_tick, generation, subscription_epoch, highest_scope, last_generation
):
    """Classify one consumer's scope without establishing event provenance.

    Returns ``(scope, retired, flag, advance_generation)``. The caller owns
    the remembered scope and any effects on its own consumers or bar state.
    """
    if typed_market_tick:
        if not (
            type(generation) is int
            and generation > 0
            and type(subscription_epoch) is int
            and subscription_epoch > 0
        ):
            return None, False, "MARKET_SCOPE_INVALID", False
        scope = (generation, subscription_epoch)
        if highest_scope is not None and scope < highest_scope:
            return scope, True, "RETIRED_CONNECTION_SCOPE", False
        if highest_scope is not None and scope != highest_scope:
            flag = (
                "CONNECTION_GENERATION_CHANGED"
                if generation != highest_scope[0]
                else "SUBSCRIPTION_EPOCH_CHANGED"
            )
            return scope, False, flag, True
        return scope, False, None, True
    if generation not in (None, ""):
        flag = (
            "CONNECTION_GENERATION_CHANGED"
            if last_generation is not None and generation != last_generation
            else None
        )
        return None, False, flag, True
    return None, False, None, False


def prepare_market_tick_fields(tick, *, replay_adapter_available, legacy_schema):
    """Normalize generic tick fields; do not issue clock or trading evidence."""
    schema = str(_tick_value(tick, "schema_version", default="") or "").strip()
    if not schema:
        schema = legacy_schema
        _set_tick_value(tick, "schema_version", schema)
        semantics = "delta"
        _set_tick_value(tick, "volume_semantics", semantics)
        legacy = True
    else:
        semantics = str(_tick_value(tick, "volume_semantics", default="") or "").strip().lower()
        legacy = False

    typed_market_tick = schema == "sdk.market.tick.v1"
    replay_market_tick = schema == "sdk.replay.market.tick.v1"
    replay_binding_invalid = False
    if replay_market_tick:
        replay_mode = _tick_value(tick, "source_mode", default=None)
        replay_source_ref = _tick_value(tick, "replay_source_ref", default=None)
        replay_epoch = _tick_value(tick, "replay_epoch", default=None)
        replay_sequence = _tick_value(tick, "replay_sequence", default=None)
        replay_binding_invalid = (
            not replay_adapter_available
            or replay_mode not in {"replay", "synthetic"}
            or type(replay_source_ref) is not str
            or not replay_source_ref
            or type(replay_epoch) is not str
            or not replay_epoch
            or type(replay_sequence) is not int
            or replay_sequence <= 0
            or _tick_value(tick, "connection_generation", default=None) is not None
            or _tick_value(tick, "ingest_seq", default=None) is not None
            or _tick_value(tick, "recv_monotonic_ns", default=None) is not None
            or _tick_value(tick, "received_monotonic_ns", default=None) is not None
            or _tick_value(tick, "received_wall_time", default=None) is not None
            or _tick_value(tick, "clock_domain_id", default=None) is not None
        )
    raw_quality_flags = _tick_value(tick, "quality_flags", default=None)
    valid_quality_container = isinstance(raw_quality_flags, (list, tuple, set, frozenset))
    try:
        quality_items = tuple(raw_quality_flags or ()) if valid_quality_container else ()
    except TypeError:
        quality_items = ()
        valid_quality_container = False
    valid_quality_items = all(
        isinstance(flag, str) and bool(flag) and flag.strip() == flag for flag in quality_items
    )
    if typed_market_tick and (not valid_quality_container or not valid_quality_items):
        flags = {"QUOTE_QUALITY_FLAGS_INVALID"}
    elif not valid_quality_items:
        flags = {"QUOTE_QUALITY_FLAGS_INVALID"}
    else:
        flags = set(quality_items)
    if replay_market_tick and replay_binding_invalid:
        flags.add("REPLAY_BINDING_INVALID")
    if legacy:
        flags.add("LEGACY_SCHEMA")

    if semantics in {"delta", "incremental"}:
        delta = finite_market_number(_tick_value(tick, "delta_volume", "volume", default=None))
        semantics = "delta"
    elif semantics in {"cumulative", "cum", "total"}:
        delta = finite_market_number(_tick_value(tick, "delta_volume", default=None))
        semantics = "cumulative"
        if delta is None:
            flags.add("DELTA_VOLUME_MISSING")
    else:
        delta = None
        flags.add("VOLUME_SEMANTICS_UNKNOWN")
    if delta is None or delta < 0:
        flags.add("DELTA_VOLUME_INVALID")
        delta = 0.0
    _set_tick_value(tick, "volume_semantics", semantics)
    _set_tick_value(tick, "delta_volume", delta)
    cumulative = finite_market_number(
        _tick_value(tick, "cum_volume", "cumulative_volume", default=None)
    )
    if cumulative is not None:
        _set_tick_value(tick, "cum_volume", cumulative)
        _set_tick_value(tick, "cumulative_volume", cumulative)

    price = finite_market_number(_tick_value(tick, "price", "last_price", default=None))
    bid = finite_market_number(_tick_value(tick, "bid_price", default=None))
    ask = finite_market_number(_tick_value(tick, "ask_price", default=None))
    bid_size = finite_market_number(_tick_value(tick, "bid_volume", "bid_size", default=None))
    ask_size = finite_market_number(_tick_value(tick, "ask_volume", "ask_size", default=None))
    if typed_market_tick:
        flags.update(market_quote_quality_flags(
            price=price, bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size
        ))
    elif price is None or price <= 0:
        flags.add("LAST_PRICE_INVALID")
    return (
        typed_market_tick, replay_market_tick, replay_binding_invalid, flags, delta,
        price, bid, ask, bid_size, ask_size,
    )


def coerce_market_epoch_seconds(value):
    """Convert the existing seconds-or-milliseconds event timestamp convention."""
    timestamp = float(value)
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return timestamp


def parse_market_event_datetime(value):
    """Convert an event-time representation without issuing clock evidence."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(coerce_market_epoch_seconds(value), timezone.utc)
        except (OSError, OverflowError, TypeError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _utc_naive(value):
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(tzinfo=None)


def market_event_timestamp(tick, *, on_invalid_datetime=None):
    """Resolve an existing neutral event's time without asserting clock quality."""
    event_time = parse_market_event_datetime(_tick_value(tick, "event_time_utc", default=None))
    if event_time is not None:
        return event_time.timestamp()
    value = _tick_value(tick, "timestamp", default=None)
    if value is not None:
        return coerce_market_epoch_seconds(value)
    dt_value = _tick_value(tick, "datetime", "dt", default=None)
    if isinstance(dt_value, datetime):
        return _utc_naive(dt_value).replace(tzinfo=timezone.utc).timestamp()
    if isinstance(dt_value, str) and dt_value:
        try:
            parsed = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
            return _utc_naive(parsed).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            if on_invalid_datetime is not None:
                on_invalid_datetime("timestamp_datetime")
    return coerce_market_epoch_seconds(_tick_value(tick, "local_time", default=0.0) or 0.0)


def market_event_utc_naive_datetime(tick, *, on_invalid_datetime=None):
    """Resolve neutral event time for consumers requiring a naive UTC value."""
    event_time = parse_market_event_datetime(_tick_value(tick, "event_time_utc", default=None))
    if event_time is not None:
        return event_time.replace(tzinfo=None)
    timestamp_value = _tick_value(tick, "timestamp", default=None)
    if timestamp_value not in (None, ""):
        try:
            ts = coerce_market_epoch_seconds(timestamp_value)
        except (TypeError, ValueError):
            if on_invalid_datetime is not None:
                on_invalid_datetime("datetime_timestamp")
        else:
            if ts > 0:
                return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
    value = _tick_value(tick, "datetime", "dt", default=None)
    if isinstance(value, datetime):
        return _utc_naive(value)
    if isinstance(value, str) and value:
        try:
            return _utc_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            if on_invalid_datetime is not None:
                on_invalid_datetime("datetime_datetime")
    ts = market_event_timestamp(tick, on_invalid_datetime=on_invalid_datetime)
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def prepare_market_tick_timing(
    tick, *, typed_market_tick, now_monotonic_ns, event_time_max_age, receive_time_max_age,
):
    """Project supplied timestamps and age flags without issuing clock evidence."""
    flags = set()
    raw_event_time = _tick_value(tick, "event_time_utc", default=None)
    if typed_market_tick and raw_event_time in (None, ""):
        flags.add("EVENT_TIME_MISSING")
    event_dt = parse_market_event_datetime(
        raw_event_time
        if raw_event_time not in (None, "")
        else _tick_value(tick, "timestamp", "datetime", default=None)
    )
    if event_dt is None:
        flags.add("EVENT_TIME_INVALID")
    raw_recv_time = _tick_value(tick, "recv_time_utc", default=None)
    if typed_market_tick and raw_recv_time in (None, ""):
        flags.add("RECV_TIME_MISSING")
    received_wall = parse_market_event_datetime(
        raw_recv_time
        if raw_recv_time not in (None, "")
        else _tick_value(tick, "received_wall_time", "local_time", default=None)
    )
    if typed_market_tick and received_wall is None:
        flags.add("RECV_TIME_INVALID")
    if received_wall is not None and event_dt is not None:
        event_age = (received_wall - event_dt).total_seconds()
        _set_tick_value(tick, "event_age_seconds", event_age)
        maximum = max(float(event_time_max_age() or 0.0), 0.0)
        if typed_market_tick and (event_age < -0.5 or (maximum and event_age > maximum)):
            flags.add("EVENT_TIME_STALE")

    raw_recv_mono = _tick_value(tick, "recv_monotonic_ns", default=None)
    if typed_market_tick and raw_recv_mono in (None, ""):
        flags.add("RECV_MONOTONIC_MISSING")
    recv_mono = (
        raw_recv_mono
        if raw_recv_mono not in (None, "")
        else _tick_value(tick, "received_monotonic_ns", default=None)
    )
    if isinstance(recv_mono, int) and recv_mono > 0:
        recv_age = max(now_monotonic_ns() - recv_mono, 0) / 1_000_000_000.0
        _set_tick_value(tick, "recv_age_seconds", recv_age)
        maximum = max(float(receive_time_max_age() or 0.0), 0.0)
        if typed_market_tick and maximum and recv_age > maximum:
            flags.add("RECEIVE_TIME_STALE")
    elif typed_market_tick:
        flags.add("RECV_MONOTONIC_INVALID")

    tick_ts = event_dt.timestamp() if event_dt is not None else None
    raw_timestamp = finite_market_number(_tick_value(tick, "timestamp", default=None))
    if typed_market_tick and event_dt is not None and raw_timestamp is not None:
        raw_timestamp = coerce_market_epoch_seconds(raw_timestamp)
        if abs(raw_timestamp - tick_ts) > 1.0e-6:
            flags.add("EVENT_TIME_CONFLICT")
    return event_dt, tick_ts, flags


def require_qualified_market_tick(value: object) -> tuple[MarketTickEvent, datetime, datetime]:
    """Validate existing SDK clock/freshness facts without issuing new evidence."""
    if type(value) is not MarketTickEvent:
        raise MarketTickQualificationError("MARKET_TICK_EVENT_INVALID")
    clock = value.clock
    if (
        clock.source_clock_quality is not MarketClockQuality.VERIFIED
        or clock.receive_clock_quality is not MarketClockQuality.VERIFIED
    ):
        raise MarketTickQualificationError("TICK_CLOCK_UNAVAILABLE")
    if clock.freshness_status is MarketFreshnessStatus.UNAVAILABLE:
        raise MarketTickQualificationError("MARKET_DATA_FRESHNESS_UNAVAILABLE")
    if clock.freshness_status is MarketFreshnessStatus.STALE:
        raise MarketTickQualificationError("MARKET_DATA_STALE")
    if clock.freshness_status is not MarketFreshnessStatus.FRESH:
        raise MarketTickQualificationError("MARKET_TICK_EVENT_INVALID")
    if not value.data_clock_qualified:
        raise MarketTickQualificationError("TICK_CLOCK_UNAVAILABLE")
    try:
        event_time = datetime.fromisoformat(clock.provider_event_time_utc.replace("Z", "+00:00"))
        received_at = datetime.fromisoformat(clock.received_at_utc.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise MarketTickQualificationError("TICK_CLOCK_UNAVAILABLE") from None
    if (
        event_time.tzinfo is None
        or event_time.utcoffset() is None
        or received_at.tzinfo is None
        or received_at.utcoffset() is None
    ):
        raise MarketTickQualificationError("TICK_CLOCK_UNAVAILABLE")
    event_time = event_time.astimezone(timezone.utc)
    received_at = received_at.astimezone(timezone.utc)
    if event_time.timestamp() > received_at.timestamp():
        raise MarketTickQualificationError("TICK_CLOCK_UNAVAILABLE")
    return value, event_time, received_at


def market_execution_evidence_flags(
    *, execution_eligible, source_clock_quality, receive_clock_quality,
    freshness_verified, stale, stale_reason,
) -> frozenset[str]:
    """Classify supplied neutral evidence without issuing clock or trade authority."""
    flags = set()
    if execution_eligible is not True:
        flags.add("UPSTREAM_EXECUTION_INELIGIBLE")
    if source_clock_quality != "verified":
        flags.add("SOURCE_CLOCK_UNVERIFIED")
    if receive_clock_quality != "verified":
        flags.add("RECEIVE_CLOCK_UNVERIFIED")
    if freshness_verified is not True:
        flags.add("FRESHNESS_UNVERIFIED")
    if stale is not False or stale_reason != "":
        flags.add("STREAM_UNREADY")
    return frozenset(flags)


def market_quote_quality_flags(*, price, bid, ask, bid_size, ask_size) -> frozenset[str]:
    """Classify normalized SDK quote fields without issuing freshness evidence.

    Values are finite numbers or ``None``, as produced by the consuming
    adapter. Price-grid and bar-specific decisions remain with that consumer.
    """
    flags = set()
    if price is None or price <= 0:
        flags.add("LAST_PRICE_INVALID")
    if bid is None or bid <= 0:
        flags.add("BID_PRICE_INVALID")
    if ask is None or ask <= 0:
        flags.add("ASK_PRICE_INVALID")
    if bid_size is None or bid_size < 0:
        flags.add("BID_SIZE_INVALID")
    elif bid_size == 0:
        flags.add("BID_DEPTH_ZERO")
    if ask_size is None or ask_size < 0:
        flags.add("ASK_SIZE_INVALID")
    elif ask_size == 0:
        flags.add("ASK_DEPTH_ZERO")
    if bid is not None and ask is not None and bid > ask:
        flags.add("CROSSED_BOOK")
    return frozenset(flags)


def quote_capture_mapping(tick, *, symbol):
    """Project finite tick fields for a caller-owned quote window.

    This projection does not establish source or clock authenticity.
    """
    raw_event = _tick_value(tick, "event_time_utc", default=None)
    event_time = parse_market_event_datetime(
        raw_event
        if raw_event not in (None, "")
        else _tick_value(tick, "timestamp", "datetime", default=None)
    )
    if event_time is None:
        return None
    raw_receive = _tick_value(tick, "recv_time_utc", default=None)
    receive_time = parse_market_event_datetime(raw_receive) if raw_receive not in (None, "") else None
    if receive_time is None:
        wall = finite_market_number(
            _tick_value(tick, "received_wall_time", "local_time", default=None)
        )
        if wall is not None:
            receive_time = datetime.fromtimestamp(wall, timezone.utc)
    if receive_time is None:
        return None
    received_mono = _tick_value(tick, "recv_monotonic_ns", default=None)
    if received_mono in (None, ""):
        received_mono = _tick_value(tick, "received_monotonic_ns", default=None)
    if not isinstance(received_mono, int) or received_mono <= 0:
        return None
    return {
        "symbol": symbol,
        "exchange": str(
            _tick_value(tick, "exchange", "exchange_id", default="") or ""
        ),
        "event_time": event_time,
        "received_at": receive_time,
        "received_monotonic_ns": received_mono,
        "ingest_seq": _tick_value(tick, "ingest_seq", "sequence", default=None),
        "generation": _tick_value(
            tick, "connection_generation", default=None
        ),
        "trading_day": str(_tick_value(tick, "trading_day", "trading_date", default="") or ""),
        "action_day": str(_tick_value(tick, "action_day", "event_session_date", default="") or ""),
        "session_segment": str(_tick_value(tick, "session_segment", default="") or ""),
        "rules_hash": str(_tick_value(tick, "quality_contract_ref", default="") or ""),
        "clock_domain": str(_tick_value(tick, "clock_domain_id", default="") or ""),
        "clock_mode": "live",
        "quality": str(_tick_value(tick, "quality", default="") or ""),
        "volume_complete": bool(_tick_value(tick, "volume_complete", default=False)),
        "bid": finite_market_number(
            _tick_value(tick, "bid_price", "bid", default=None)
        ),
        "ask": finite_market_number(
            _tick_value(tick, "ask_price", "ask", default=None)
        ),
        "bid_qty": finite_market_number(
            _tick_value(tick, "bid_volume", "bid_size", default=None)
        ),
        "ask_qty": finite_market_number(
            _tick_value(tick, "ask_volume", "ask_size", default=None)
        ),
        "last": finite_market_number(
            _tick_value(tick, "price", "last_price", default=None)
        ),
        "source": str(_tick_value(tick, "source", default="") or ""),
        "event_time_provenance": str(
            _tick_value(tick, "event_time_provenance", default="unavailable")
        ),
    }

__all__ = [
    "DataClockBinding",
    "MARKET_TICK_SCHEMA",
    "MARKET_TICK_SCHEMA_VERSION",
    "MarketClockQuality",
    "MarketEventCursor",
    "MarketFreshnessStatus",
    "MarketTickContractError",
    "MarketTickEvent",
    "MarketTickPayload",
    "MarketTickQualificationError",
    "bind_market_tick_event",
    "classify_market_scope",
    "coerce_market_epoch_seconds",
    "finite_market_number",
    "market_event_timestamp",
    "market_event_utc_naive_datetime",
    "market_execution_evidence_flags",
    "market_quote_quality_flags",
    "parse_market_event_datetime",
    "prepare_market_tick_fields",
    "prepare_market_tick_timing",
    "require_qualified_market_tick",
    "quote_capture_mapping",
]

