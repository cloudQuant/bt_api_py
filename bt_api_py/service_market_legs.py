"""Read-only leg-group time-skew review over the existing market head RPC.

The group reads ``market.head`` for every declared leg binding and judges
per-leg readiness from the provider-supplied ``latest_observed_at_utc`` source
times only.  A silent leg (head 0 or no observed time) is explicit ``no_data``
and never counted fresh; a leg whose last source time lies farther than the
declared skew from the reviewed instant keeps the whole group not ready.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bt_api_base.market_protocol import MarketSubscriptionKey

from bt_api_py.service_runtime import ServiceRuntimeError

_HEAD_OPERATION = "market.head"
_HEAD_REPLY_FIELDS = frozenset(
    {"stream_id", "epoch", "head", "oldest_sequence", "latest_observed_at_utc"}
)
_MAX_REFERENCE_BYTES = 256


class ServiceMarketLegsError(RuntimeError):
    """Finite diagnostics for leg-group configuration and head readback."""

    def __init__(self, code: str, *, cause_code: str | None = None) -> None:
        self.code = code
        self.cause_code = cause_code
        super().__init__(code)


@dataclass(frozen=True)
class MarketLegBinding:
    """One already-registered market leg: its mount and subscription key."""

    mount: str
    key: MarketSubscriptionKey


@dataclass(frozen=True)
class MarketLegStatus:
    """One leg's provider-sourced observation and its skew judgment.

    ``no_data`` marks a leg whose stream is silent (head 0 or no observed
    time); such a leg has no ``age_seconds`` and is never ready.  ``age_seconds``
    is the signed distance from the reviewed instant to the provider source
    time, so a negative value means the source time is ahead of the clock.
    """

    mount: str
    key: MarketSubscriptionKey
    head: int
    observed_at_utc: str | None
    age_seconds: float | None
    no_data: bool
    ready: bool


@dataclass(frozen=True)
class MarketLegGroupStatus:
    """Per-leg statuses plus the all-legs-ready verdict of one review."""

    ready: bool
    max_time_skew_seconds: float
    legs: tuple[MarketLegStatus, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ServiceMarketLegGroup:
    """Review readiness of N registered market legs against one declared skew.

    Every judgment combines the read-only ``market.head`` reply of each leg
    with the injected UTC clock; this class never consults a wall clock of its
    own.  A leg is ready only when its stream reports data and its source time
    is within ``max_time_skew_seconds`` of the reviewed instant, and the group
    is ready only when every leg is ready.  A head read failure aborts the
    review with an explicit error instead of guessing a verdict.
    """

    def __init__(
        self,
        runtime: Any,
        legs: Sequence[MarketLegBinding],
        *,
        max_time_skew_seconds: float,
        utc_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not callable(getattr(runtime, "call", None)):
            raise ServiceMarketLegsError("INVALID_RUNTIME")
        if (
            type(max_time_skew_seconds) not in (int, float)
            or not math.isfinite(max_time_skew_seconds)
            or max_time_skew_seconds < 0
        ):
            raise ServiceMarketLegsError("INVALID_MAX_TIME_SKEW")
        if not callable(utc_clock):
            raise ServiceMarketLegsError("INVALID_CLOCK")
        try:
            materialized = tuple(legs)
        except TypeError:
            raise ServiceMarketLegsError("INVALID_LEGS") from None
        if not materialized:
            raise ServiceMarketLegsError("EMPTY_LEG_GROUP")
        seen: set[tuple[str, MarketSubscriptionKey]] = set()
        for binding in materialized:
            if type(binding) is not MarketLegBinding:
                raise ServiceMarketLegsError("INVALID_LEG_BINDING")
            _validate_mount(binding.mount)
            if type(binding.key) is not MarketSubscriptionKey:
                raise ServiceMarketLegsError("INVALID_LEG_KEY")
            identity = (binding.mount, binding.key)
            if identity in seen:
                raise ServiceMarketLegsError("DUPLICATE_LEG")
            seen.add(identity)

        self._runtime = runtime
        self._legs = materialized
        self._max_time_skew_seconds = float(max_time_skew_seconds)
        self._clock = utc_clock

    @property
    def legs(self) -> tuple[MarketLegBinding, ...]:
        return self._legs

    @property
    def max_time_skew_seconds(self) -> float:
        return self._max_time_skew_seconds

    def review(self) -> MarketLegGroupStatus:
        """Read every leg head once and return the current group verdict."""

        now = self._now()
        legs = tuple(self._read_leg(binding, now) for binding in self._legs)
        return MarketLegGroupStatus(
            ready=all(leg.ready for leg in legs),
            max_time_skew_seconds=self._max_time_skew_seconds,
            legs=legs,
        )

    def _read_leg(self, binding: MarketLegBinding, now: datetime) -> MarketLegStatus:
        try:
            reply = self._runtime.call(
                binding.mount, _HEAD_OPERATION, {"key": binding.key.to_dict()}
            )
        except ServiceRuntimeError as error:
            raise ServiceMarketLegsError("LEG_HEAD_READ_FAILED", cause_code=error.code) from None
        except Exception:
            raise ServiceMarketLegsError("LEG_HEAD_READ_FAILED") from None
        head, observed_text = _validate_head_reply(reply, binding.key)
        if head == 0 or observed_text is None:
            return MarketLegStatus(
                mount=binding.mount,
                key=binding.key,
                head=head,
                observed_at_utc=observed_text,
                age_seconds=None,
                no_data=True,
                ready=False,
            )
        observed = _parse_source_time(observed_text)
        age = (now - observed).total_seconds()
        return MarketLegStatus(
            mount=binding.mount,
            key=binding.key,
            head=head,
            observed_at_utc=observed_text,
            age_seconds=age,
            no_data=False,
            ready=age <= self._max_time_skew_seconds,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ServiceMarketLegsError("INVALID_CLOCK_VALUE")
        return value.astimezone(UTC)


def _validate_mount(mount: Any) -> None:
    if type(mount) is not str:
        raise ServiceMarketLegsError("INVALID_LEG_MOUNT")
    try:
        encoded = mount.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ServiceMarketLegsError("INVALID_LEG_MOUNT") from None
    if (
        not encoded
        or len(encoded) > _MAX_REFERENCE_BYTES
        or mount != mount.strip()
        or not mount.isprintable()
    ):
        raise ServiceMarketLegsError("INVALID_LEG_MOUNT")


def _validate_head_reply(reply: Any, key: MarketSubscriptionKey) -> tuple[int, str | None]:
    if not isinstance(reply, Mapping) or set(reply) != _HEAD_REPLY_FIELDS:
        raise ServiceMarketLegsError("INVALID_HEAD_REPLY")
    if (
        reply["stream_id"] != key.stream_id
        or type(reply["epoch"]) is not str
        or not reply["epoch"]
        or type(reply["head"]) is not int
        or reply["head"] < 0
        or type(reply["oldest_sequence"]) is not int
        or not 1 <= reply["oldest_sequence"] <= reply["head"] + 1
    ):
        raise ServiceMarketLegsError("INVALID_HEAD_REPLY")
    observed = reply["latest_observed_at_utc"]
    if observed is not None and type(observed) is not str:
        raise ServiceMarketLegsError("INVALID_HEAD_REPLY")
    return reply["head"], observed


def _parse_source_time(text: str) -> datetime:
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise ServiceMarketLegsError("INVALID_OBSERVED_AT") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ServiceMarketLegsError("INVALID_OBSERVED_AT")
    return parsed.astimezone(UTC)


__all__ = [
    "MarketLegBinding",
    "MarketLegGroupStatus",
    "MarketLegStatus",
    "ServiceMarketLegGroup",
    "ServiceMarketLegsError",
]
