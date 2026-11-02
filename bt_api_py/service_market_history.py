"""Provider-neutral, bounded collection of paginated market history.

The collector stays on the injected client's owner thread and calls that same
client for every page. Each page remains independently authorized and bound by
the client's history method; this module adds only the cross-page cursor and
typed-row checks needed to return one complete interval.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from bt_api_base.market_history_protocol import (
    MAX_MARKET_HISTORY_BARS,
    MarketBar,
    MarketHistoryPage,
    MarketHistoryRequest,
)
from bt_api_base.market_protocol import (
    MARKET_EVENT_OPERATION,
    MARKET_EVENT_SCHEMA,
    MARKET_EVENT_SCHEMA_VERSION,
    MarketSubscriptionKey,
)
from bt_api_base.service_protocol import WireEnvelope, WireMessageKind, validate_event


_ERROR_CODES = frozenset(
    {
        "CLOCK_INVALID",
        "HISTORY_LIMIT_INVALID",
        "HISTORY_PAGE_LIMIT",
        "HISTORY_ROW_LIMIT",
        "HISTORY_PAGE_INVALID",
        "HISTORY_BAR_INVALID",
        "HISTORY_ORDER_INVALID",
        "HISTORY_OVERLAP",
        "HISTORY_CURSOR_INVALID",
        "HISTORY_WINDOW_INVALID",
        "HISTORY_WINDOW_UNALIGNED",
        "HISTORY_SINCE_UNSUPPORTED",
        "HISTORY_BAR_CONFLICT",
        "HISTORY_DUPLICATE_BAR",
        "MARKET_BAR_TIMEFRAME_MISMATCH",
        "MARKET_BAR_ALIGNMENT_INVALID",
        "MARKET_BAR_FUTURE",
        "MARKET_BAR_NOT_CLOSED_AT_SENT",
        "MARKET_KEY_INVALID",
        "MARKET_CLIENT_INVALID",
        "MARKET_CLIENT_KEY_CHANGED",
    }
)


class ServiceBarHistoryHandoff:
    """Own one consumer's projected-history to live-bar transition.

    The caller supplies its projected-bar identity and error type. The state
    does not issue market-source, clock, account, or execution evidence.
    """

    def __init__(self, *, bar_identity, error_type):
        self._bar_identity = bar_identity
        self._error_type = error_type
        self._bootstrap = None
        self._pending = None
        self._history_by_open = {}
        self._last_open = None
        self._last_identity = None

    def stage_bootstrap(self, snapshot):
        if snapshot is not None:
            self._bootstrap = snapshot

    def apply_history(self, rows):
        by_open = {row["timestamp"]: self._bar_identity(row) for row in rows}
        self._history_by_open = by_open
        latest, self._bootstrap = self._bootstrap, None
        if latest is not None:
            opened = latest["timestamp"]
            found = by_open.get(opened)
            if found is not None:
                if found != self._bar_identity(latest):
                    raise self._error_type("HISTORY_LIVE_CONFLICT")
            elif rows and _parse_utc(opened, "open_time_utc") <= _parse_utc(
                rows[-1]["timestamp"], "open_time_utc"
            ):
                raise self._error_type("HISTORY_LIVE_ORDER_INVALID")
            else:
                self._pending = latest
        if rows:
            self._last_open = rows[-1]["timestamp"]
            self._last_identity = self._bar_identity(rows[-1])

    def take_pending(self):
        pending, self._pending = self._pending, None
        if pending is None:
            return False, None
        return True, self._remember_emitted(pending)

    def accept_live(self, bar):
        open_time = bar["timestamp"]
        identity = self._bar_identity(bar)
        if self._last_open is not None and _parse_utc(open_time, "open_time_utc") < _parse_utc(
            self._last_open, "last_emitted_open"
        ):
            raise self._error_type("LIVE_BAR_OUT_OF_ORDER")
        previous = self._history_by_open.get(open_time)
        if previous is not None:
            if previous != identity:
                raise self._error_type("HISTORY_LIVE_CONFLICT")
            return None
        return self._remember_emitted(bar)

    def _remember_emitted(self, bar):
        open_time = bar["timestamp"]
        identity = self._bar_identity(bar)
        if self._last_open is not None:
            if _parse_utc(open_time, "open_time_utc") < _parse_utc(
                self._last_open, "open_time_utc"
            ):
                raise self._error_type("LIVE_BAR_OUT_OF_ORDER")
            if open_time == self._last_open:
                if self._last_identity != identity:
                    raise self._error_type("HISTORY_LIVE_CONFLICT")
                return None
        self._last_open = open_time
        self._last_identity = identity
        return dict(bar)


class MarketHistoryCollectionError(RuntimeError):
    """Finite, value-free error raised by the history collector."""

    def __init__(self, code: str) -> None:
        self.code = (
            code
            if type(code) is str and code in _ERROR_CODES
            else "HISTORY_PAGE_INVALID"
        )
        super().__init__(self.code)


def fixed_timeframe_seconds(value: str) -> int:
    """Return seconds for one supported fixed interval label."""

    match = (
        re.fullmatch(r"([1-9][0-9]{0,11})([smhdw])", value)
        if type(value) is str
        else None
    )
    if match is None:
        raise ValueError("timeframe must be a positive fixed interval")
    count = int(match.group(1))
    return count * {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }[match.group(2)]


def validate_market_history_limits(
    *, history_page_size: int, max_history_pages: int, max_history_rows: int,
    default_history_limit: int,
) -> None:
    """Validate caller-supplied bounds for one paginated history reader."""
    if type(history_page_size) is not int or not 1 <= history_page_size <= 1000:
        raise ValueError("history_page_size must be between 1 and 1000")
    if type(max_history_pages) is not int or not 1 <= max_history_pages <= 10000:
        raise ValueError("max_history_pages must be positive and bounded")
    if type(max_history_rows) is not int or not 1 <= max_history_rows <= 1000000:
        raise ValueError("max_history_rows must be positive and bounded")
    if (
        type(default_history_limit) is not int
        or not 1 <= default_history_limit <= max_history_rows
    ):
        raise ValueError("default_history_limit must fit max_history_rows")


def require_service_bar_healthy(client: Any, *, error_type: type[Exception], code: str) -> None:
    """Require the selected neutral bar client to report a healthy stream."""
    if (
        getattr(client, "healthy", None) is not True
        or getattr(client, "recovery_required", None) is not False
    ):
        raise error_type(code)


def validate_service_bar_bootstrap(client: Any, bootstrap: Any, *, error_type: type[Exception]):
    """Validate supplied bar-stream bootstrap facts without starting the client."""
    require_service_bar_healthy(client, error_type=error_type, code="MARKET_START_UNHEALTHY")
    key = client.key
    if type(key) is not MarketSubscriptionKey:
        raise error_type("MARKET_KEY_INVALID")
    if key.channel != "bar":
        raise error_type("MARKET_KEY_INVALID")
    epoch = getattr(bootstrap, "snapshot_epoch", None)
    sequence = getattr(bootstrap, "snapshot_sequence", None)
    if (
        type(epoch) is not str
        or not epoch
        or type(sequence) is not int
        or sequence < 0
        or getattr(bootstrap, "stream_id", None) != key.stream_id
        or getattr(bootstrap, "resync_required", None) is not False
        or getattr(bootstrap, "snapshot_kind", None) != "latest_event"
        or getattr(bootstrap, "snapshot_complete", None) is not False
        or getattr(bootstrap, "full_baseline_available", None) is not False
    ):
        raise error_type("MARKET_BOOTSTRAP_INVALID")
    return epoch, sequence


def start_service_bar_stream(client: Any, *, error_type: type[Exception]):
    """Start one neutral bar client and validate its bootstrap metadata."""
    bootstrap = client.start()
    epoch, sequence = validate_service_bar_bootstrap(client, bootstrap, error_type=error_type)
    return bootstrap, epoch, sequence


def service_bar_bootstrap_snapshot(bootstrap: Any, sequence: int, *, error_type: type[Exception]):
    """Return the optional snapshot only when it agrees with the bootstrap cursor."""
    snapshot = getattr(bootstrap, "snapshot", None)
    if (snapshot is not None and sequence == 0) or (snapshot is None and sequence != 0):
        raise error_type("MARKET_BOOTSTRAP_INVALID")
    return snapshot


def poll_service_bar_stream(client: Any, *, error_type: type[Exception]):
    """Poll once with the same health fence before and after the call."""
    require_service_bar_healthy(client, error_type=error_type, code="MARKET_STREAM_UNHEALTHY")
    event = client.poll(timeout_ms=0)
    require_service_bar_healthy(client, error_type=error_type, code="MARKET_STREAM_UNHEALTHY")
    return event


def validate_service_bar_event(
    event: Any,
    *,
    key: MarketSubscriptionKey,
    scope_provider: Any,
    epoch: Any,
    expected_sequence: int,
    timeframe: str,
    clock: Any,
    error_type: type[Exception],
) -> MarketBar:
    """Validate an existing service event and return its neutral bar DTO."""
    if type(event) is not WireEnvelope:
        raise error_type("MARKET_EVENT_INVALID")
    header = event.header
    scope = scope_provider()
    try:
        validate_event(
            event,
            expected_scope=scope,
            expected_stream_id=key.stream_id,
            expected_stream_epoch=epoch,
            expected_sequence=expected_sequence,
        )
    except Exception:
        raise error_type("MARKET_EVENT_INVALID") from None
    if (
        header.kind is not WireMessageKind.EVENT
        or header.operation != MARKET_EVENT_OPERATION
        or header.schema_id != MARKET_EVENT_SCHEMA
        or header.schema_version != MARKET_EVENT_SCHEMA_VERSION
        or set(event.payload) != {"key", "data"}
    ):
        raise error_type("MARKET_EVENT_INVALID")
    try:
        event_key = MarketSubscriptionKey.from_dict(event.payload["key"])
        if event_key != key:
            raise ValueError
        bar = MarketBar.from_dict(event.payload["data"])
        sent_at = _parse_utc(header.sent_at_utc, "sent_at_utc")
        now = clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError
        try:
            validate_market_bar_interval(
                bar,
                timeframe,
                now_utc=now.astimezone(timezone.utc),
                sent_at_utc=sent_at,
            )
        except MarketHistoryCollectionError as error:
            raise error_type(error.code) from None
        return bar
    except error_type:
        raise
    except Exception:
        raise error_type("MARKET_EVENT_INVALID") from None


class ServiceBarStreamSession:
    """Own one neutral bar stream's bootstrap epoch and consumed sequence.

    The injected projector is called before advancing the poll cursor, so a
    caller that cannot project a validated bar does not commit that event.
    The caller retains the underlying client's connection and close lifecycle.
    """

    def __init__(self, client: Any, *, timeframe: str, clock: Any, project_bar: Any, error_type):
        self._client = client
        self._timeframe = timeframe
        self._clock = clock
        self._project_bar = project_bar
        self._error_type = error_type
        self._epoch = None
        self._sequence = None

    def start(self):
        bootstrap, epoch, sequence = start_service_bar_stream(
            self._client, error_type=self._error_type
        )
        self._epoch = epoch
        self._sequence = sequence
        snapshot = service_bar_bootstrap_snapshot(
            bootstrap, sequence, error_type=self._error_type
        )
        if snapshot is None:
            return None
        return self._project_event(snapshot, expected_sequence=sequence)

    def poll(self):
        event = poll_service_bar_stream(self._client, error_type=self._error_type)
        if event is None:
            return None
        projected = self._project_event(event, expected_sequence=self._sequence + 1)
        self._sequence = event.header.sequence
        return projected

    def _project_event(self, event, *, expected_sequence):
        bar = validate_service_bar_event(
            event,
            key=self._client.key,
            scope_provider=lambda: self._client.stream_scope,
            epoch=self._epoch,
            expected_sequence=expected_sequence,
            timeframe=self._timeframe,
            clock=self._clock,
            error_type=self._error_type,
        )
        try:
            return self._project_bar(bar)
        except self._error_type:
            raise
        except Exception:
            raise self._error_type("MARKET_EVENT_INVALID") from None


def validate_market_bar_interval(
    bar: MarketBar,
    timeframe: str,
    *,
    now_utc: datetime,
    sent_at_utc: datetime | None = None,
) -> None:
    """Validate a closed bar against a fixed interval and local time bound."""

    if type(bar) is not MarketBar:
        raise MarketHistoryCollectionError("HISTORY_BAR_INVALID")
    try:
        duration = fixed_timeframe_seconds(timeframe)
        opened = _parse_utc(bar.open_time_utc, "open_time_utc")
        closed = _parse_utc(bar.close_time_utc, "close_time_utc")
        now = _aware_utc(now_utc, "CLOCK_INVALID")
        sent_at = None if sent_at_utc is None else _aware_utc(sent_at_utc, "CLOCK_INVALID")
    except MarketHistoryCollectionError:
        raise
    except Exception:
        raise MarketHistoryCollectionError("HISTORY_BAR_INVALID") from None

    if (closed - opened).total_seconds() != duration:
        raise MarketHistoryCollectionError("MARKET_BAR_TIMEFRAME_MISMATCH")
    if (
        opened.microsecond
        or closed.microsecond
        or _epoch_seconds(opened) % duration
        or _epoch_seconds(closed) % duration
    ):
        raise MarketHistoryCollectionError("MARKET_BAR_ALIGNMENT_INVALID")
    if closed > now:
        raise MarketHistoryCollectionError("MARKET_BAR_FUTURE")
    if sent_at is not None and closed > sent_at:
        raise MarketHistoryCollectionError("MARKET_BAR_NOT_CLOSED_AT_SENT")


def collect_market_history(
    client: Any,
    *,
    history_start_utc: str | None,
    history_end_utc: str | None,
    since: Any,
    now_utc: datetime,
    limit: int | None,
    default_limit: int,
    page_size: int,
    max_pages: int,
    max_rows: int,
) -> tuple[MarketBar, ...]:
    """Collect one complete inclusive-start/exclusive-end history interval.

    The same exact subscription key and the same injected client are retained
    across all page calls. The client performs its ordinary authorization and
    service-binding checks for each page. ``MarketHistoryPage.complete`` means
    provider exhaustion of the requested interval; it does not imply that every
    time interval contains a bar.
    """

    key = getattr(client, "key", None)
    history = getattr(client, "history", None)
    if type(key) is not MarketSubscriptionKey or key.channel != "bar":
        raise MarketHistoryCollectionError("MARKET_KEY_INVALID")
    if not callable(history):
        raise MarketHistoryCollectionError("MARKET_CLIENT_INVALID")

    if (history_start_utc is None) != (history_end_utc is None):
        raise MarketHistoryCollectionError("HISTORY_WINDOW_INVALID")
    if type(max_rows) is not int or not 1 <= max_rows <= 1_000_000:
        raise MarketHistoryCollectionError("HISTORY_LIMIT_INVALID")
    if (
        type(default_limit) is not int
        or not 1 <= default_limit <= max_rows
        or type(page_size) is not int
        or not 1 <= page_size <= MAX_MARKET_HISTORY_BARS
        or type(max_pages) is not int
        or not 1 <= max_pages <= 10_000
    ):
        raise MarketHistoryCollectionError("HISTORY_LIMIT_INVALID")
    if limit is None:
        total_limit = default_limit
    elif type(limit) is int and 1 <= limit <= max_rows:
        total_limit = limit
    else:
        raise MarketHistoryCollectionError("HISTORY_LIMIT_INVALID")

    try:
        duration = fixed_timeframe_seconds(key.timeframe)
        now = _aware_utc(now_utc, "CLOCK_INVALID")
    except MarketHistoryCollectionError:
        raise
    except Exception:
        raise MarketHistoryCollectionError("MARKET_KEY_INVALID") from None

    if history_end_utc is None:
        end = _floor_time(now, duration)
        start = end - timedelta(seconds=duration * total_limit)
        if since is not None:
            start = max(start, _floor_time(_parse_since(since), duration))
    else:
        try:
            start = _parse_utc(history_start_utc, "history_start_utc")
            end = _parse_utc(history_end_utc, "history_end_utc")
        except Exception:
            raise MarketHistoryCollectionError("HISTORY_WINDOW_INVALID") from None
        if since is not None:
            start = max(start, _floor_time(_parse_since(since), duration))

    if start >= end or end > now:
        raise MarketHistoryCollectionError("HISTORY_WINDOW_INVALID")
    if _epoch_seconds(start) % duration or _epoch_seconds(end) % duration:
        raise MarketHistoryCollectionError("HISTORY_WINDOW_UNALIGNED")

    bars: list[MarketBar] = []
    cursor = start
    pages = 0
    previous_open: datetime | None = None
    previous_close: datetime | None = None
    page: MarketHistoryPage | None = None

    while cursor < end:
        if pages >= max_pages:
            raise MarketHistoryCollectionError("HISTORY_PAGE_LIMIT")
        remaining = total_limit - len(bars)
        if remaining <= 0:
            raise MarketHistoryCollectionError("HISTORY_ROW_LIMIT")
        page_limit = min(page_size, remaining)
        request = MarketHistoryRequest(
            key=key,
            start_time_utc=_format_utc(cursor),
            end_time_utc=_format_utc(end),
            limit=page_limit,
        )
        page = history(request)
        pages += 1
        if getattr(client, "key", None) != key:
            raise MarketHistoryCollectionError("MARKET_CLIENT_KEY_CHANGED")
        if type(page) is not MarketHistoryPage or page.request != request:
            raise MarketHistoryCollectionError("HISTORY_PAGE_INVALID")
        if len(page.bars) > page_limit or len(bars) + len(page.bars) > total_limit:
            raise MarketHistoryCollectionError("HISTORY_ROW_LIMIT")

        for bar in page.bars:
            if type(bar) is not MarketBar:
                raise MarketHistoryCollectionError("HISTORY_BAR_INVALID")
            try:
                opened = _parse_utc(bar.open_time_utc, "open_time_utc")
                closed = _parse_utc(bar.close_time_utc, "close_time_utc")
            except Exception:
                raise MarketHistoryCollectionError("HISTORY_BAR_INVALID") from None
            validate_market_bar_interval(bar, key.timeframe, now_utc=now)
            if previous_open is not None and opened <= previous_open:
                raise MarketHistoryCollectionError("HISTORY_ORDER_INVALID")
            if previous_close is not None and opened < previous_close:
                raise MarketHistoryCollectionError("HISTORY_OVERLAP")
            previous_open, previous_close = opened, closed
            bars.append(bar)

        if page.complete:
            break
        try:
            next_start = _parse_utc(page.next_start_time_utc, "next_start_time_utc")
        except Exception:
            raise MarketHistoryCollectionError("HISTORY_CURSOR_INVALID") from None
        if next_start <= cursor or next_start >= end or next_start != previous_close:
            raise MarketHistoryCollectionError("HISTORY_CURSOR_INVALID")
        cursor = next_start

    if page is None or not page.complete:
        raise MarketHistoryCollectionError("HISTORY_PAGE_LIMIT")

    by_open: dict[datetime, tuple[Any, ...]] = {}
    for bar in bars:
        opened = _parse_utc(bar.open_time_utc, "open_time_utc")
        identity = _bar_identity(bar)
        if opened in by_open:
            if by_open[opened] != identity:
                raise MarketHistoryCollectionError("HISTORY_BAR_CONFLICT")
            raise MarketHistoryCollectionError("HISTORY_DUPLICATE_BAR")
        by_open[opened] = identity

    return tuple(bars)


def _bar_identity(bar: MarketBar) -> tuple[Any, ...]:
    return (
        _parse_utc(bar.open_time_utc, "open_time_utc"),
        _parse_utc(bar.close_time_utc, "close_time_utc"),
        Decimal(bar.open),
        Decimal(bar.high),
        Decimal(bar.low),
        Decimal(bar.close),
        Decimal(bar.volume),
        None if bar.open_interest is None else Decimal(bar.open_interest),
    )


def _aware_utc(value: Any, error_code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MarketHistoryCollectionError(error_code)
    return value.astimezone(timezone.utc)


def _parse_since(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MarketHistoryCollectionError("HISTORY_SINCE_UNSUPPORTED")
    return value.astimezone(timezone.utc)


def _parse_utc(value: Any, field: str) -> datetime:
    if (
        type(value) is not str
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value)
        is None
    ):
        raise ValueError(f"{field} must be a UTC timestamp ending in Z")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError:
        raise ValueError(f"{field} is invalid") from None


def _format_utc(value: datetime) -> str:
    result = (
        value.astimezone(timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="microseconds")
    )
    if "." in result:
        whole, fraction = result.split(".", 1)
        fraction = fraction.rstrip("0")
        result = whole if not fraction else f"{whole}.{fraction}"
    return result + "Z"


# Public aliases preserve the history collector's existing UTC rules.
parse_market_utc = _parse_utc
format_market_utc = _format_utc


def _epoch_seconds(value: datetime) -> int:
    return math.floor(value.timestamp())


def _floor_time(value: datetime, duration: int) -> datetime:
    seconds = _epoch_seconds(value)
    return datetime.fromtimestamp(seconds - seconds % duration, tz=timezone.utc)


__all__ = [
    "MarketHistoryCollectionError",
    "ServiceBarHistoryHandoff",
    "ServiceBarStreamSession",
    "parse_market_utc",
    "format_market_utc",
    "collect_market_history",
    "fixed_timeframe_seconds",
    "validate_market_bar_interval",
    "validate_market_history_limits",
    "validate_service_bar_bootstrap",
    "start_service_bar_stream",
    "service_bar_bootstrap_snapshot",
    "require_service_bar_healthy",
    "poll_service_bar_stream",
    "validate_service_bar_event",
]
