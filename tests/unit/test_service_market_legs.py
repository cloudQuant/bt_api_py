"""Focused unit tests for the leg-group time-skew review module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey

from bt_api_py.service_market_legs import (
    MarketLegBinding,
    ServiceMarketLegGroup,
    ServiceMarketLegsError,
)
from bt_api_py.service_runtime import ServiceRuntimeError

_T0 = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)


def _key(instrument_id: str, *, channel: str = "trades") -> MarketSubscriptionKey:
    return MarketSubscriptionKey(
        source_ref="synthetic-market-source",
        entitlement_ref="synthetic-public-entitlement",
        instrument=MarketInstrumentKey(
            provider="synthetic",
            venue="loopback",
            environment="paper",
            product_type="spot",
            instrument_id=instrument_id,
            native_symbol=instrument_id.upper(),
        ),
        channel=channel,
    )


_KEY_A = _key("leg-instrument-a")
_KEY_B = _key("leg-instrument-b")
_MOUNT = "store.market"
_BINDING_A = MarketLegBinding(_MOUNT, _KEY_A)
_BINDING_B = MarketLegBinding(_MOUNT, _KEY_B)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _head_reply(key: MarketSubscriptionKey, head: int, observed: str | None):
    return {
        "stream_id": key.stream_id,
        "epoch": "synthetic-epoch-1",
        "head": head,
        "oldest_sequence": 1,
        "latest_observed_at_utc": observed,
    }


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _HeadRuntime:
    """Minimal stand-in for ServiceClientRuntime's request/reply surface."""

    def __init__(self, replies: dict[tuple[str, MarketSubscriptionKey], Any]) -> None:
        self.replies = dict(replies)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def call(self, mount: str, operation: str, arguments: dict[str, Any]):
        self.calls.append((mount, operation, dict(arguments)))
        key = MarketSubscriptionKey.from_dict(arguments["key"])
        entry = self.replies[(mount, key)]
        if isinstance(entry, BaseException):
            raise entry
        return entry


def _group(runtime: Any, *bindings: MarketLegBinding, skew: float = 5.0, clock=None):
    return ServiceMarketLegGroup(
        runtime,
        bindings,
        max_time_skew_seconds=skew,
        utc_clock=clock if clock is not None else _Clock(_T0),
    )


def _expect_code(code: str, function, *args, **kwargs) -> ServiceMarketLegsError:
    with pytest.raises(ServiceMarketLegsError) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code
    return failure.value


def test_silent_legs_report_explicit_no_data_and_group_not_ready():
    runtime = _HeadRuntime(
        {
            (_MOUNT, _KEY_A): _head_reply(_KEY_A, 0, None),
            (_MOUNT, _KEY_B): _head_reply(_KEY_B, 0, None),
        }
    )
    status = _group(runtime, _BINDING_A, _BINDING_B).review()

    assert status.ready is False
    assert status.max_time_skew_seconds == 5.0
    assert len(status.legs) == 2
    for leg in status.legs:
        assert leg.head == 0
        assert leg.observed_at_utc is None
        assert leg.no_data is True
        assert leg.age_seconds is None
        assert leg.ready is False
    # Each leg was judged from exactly one read-only head read on its mount.
    assert runtime.calls == [
        (_MOUNT, "market.head", {"key": _KEY_A.to_dict()}),
        (_MOUNT, "market.head", {"key": _KEY_B.to_dict()}),
    ]


def test_mixed_silence_forms_are_never_counted_fresh():
    # Data without a provider source time and a headless stream with a stray
    # observed time are both explicit no_data, never fresh.
    runtime = _HeadRuntime(
        {
            (_MOUNT, _KEY_A): _head_reply(_KEY_A, 3, None),
            (_MOUNT, _KEY_B): _head_reply(_KEY_B, 0, _iso(_T0)),
        }
    )
    status = _group(runtime, _BINDING_A, _BINDING_B).review()

    assert status.ready is False
    assert status.legs[0].head == 3 and status.legs[0].no_data is True
    assert status.legs[0].age_seconds is None and status.legs[0].ready is False
    assert status.legs[1].head == 0 and status.legs[1].no_data is True
    assert status.legs[1].ready is False


def test_one_stale_leg_blocks_group_until_it_catches_up():
    clock = _Clock(_T0 + timedelta(seconds=10))
    runtime = _HeadRuntime(
        {
            (_MOUNT, _KEY_A): _head_reply(_KEY_A, 1, _iso(_T0 + timedelta(seconds=8))),
            (_MOUNT, _KEY_B): _head_reply(_KEY_B, 1, _iso(_T0)),
        }
    )
    group = _group(runtime, _BINDING_A, _BINDING_B, clock=clock)

    first = group.review()
    assert first.ready is False
    assert first.legs[0].ready is True and first.legs[0].age_seconds == pytest.approx(2.0)
    assert first.legs[1].no_data is False
    assert first.legs[1].ready is False and first.legs[1].age_seconds == pytest.approx(10.0)

    # The fresh leg updates further; the stale leg alone still blocks the group.
    runtime.replies[(_MOUNT, _KEY_A)] = _head_reply(_KEY_A, 2, _iso(_T0 + timedelta(seconds=10)))
    second = group.review()
    assert second.legs[0].ready is True and second.legs[0].age_seconds == pytest.approx(0.0)
    assert second.legs[1].ready is False
    assert second.ready is False

    # The stale leg catches up inside the declared skew: the group turns ready.
    runtime.replies[(_MOUNT, _KEY_B)] = _head_reply(_KEY_B, 2, _iso(_T0 + timedelta(seconds=6)))
    third = group.review()
    assert third.legs[0].ready is True and third.legs[1].ready is True
    assert third.legs[1].age_seconds == pytest.approx(4.0)
    assert third.ready is True

    # As the reviewed instant advances, both legs fall out of the skew again.
    clock.now = _T0 + timedelta(seconds=20)
    fourth = group.review()
    assert fourth.legs[0].ready is False and fourth.legs[0].age_seconds == pytest.approx(10.0)
    assert fourth.legs[1].ready is False and fourth.legs[1].age_seconds == pytest.approx(14.0)
    assert fourth.ready is False


def test_age_and_ready_follow_provider_source_times_and_injected_clock():
    replies = {(_MOUNT, _KEY_A): _head_reply(_KEY_A, 1, "2026-10-16T10:00:00.500000Z")}
    clock = _Clock(_T0 + timedelta(seconds=2))

    lenient = _group(_HeadRuntime(replies), _BINDING_A, skew=2.0, clock=clock).review()
    assert lenient.legs[0].age_seconds == pytest.approx(1.5)
    assert lenient.legs[0].observed_at_utc == "2026-10-16T10:00:00.500000Z"
    assert lenient.ready is True

    strict = _group(_HeadRuntime(replies), _BINDING_A, skew=1.0, clock=clock).review()
    assert strict.legs[0].age_seconds == pytest.approx(1.5)
    assert strict.legs[0].ready is False
    assert strict.ready is False

    # Any offset representation of the same instant yields the same age.
    offset_replies = {(_MOUNT, _KEY_A): _head_reply(_KEY_A, 1, "2026-10-16T12:00:01+02:00")}
    offset = _group(
        _HeadRuntime(offset_replies), _BINDING_A, clock=_Clock(_T0 + timedelta(seconds=2))
    ).review()
    assert offset.legs[0].age_seconds == pytest.approx(1.0)
    assert offset.legs[0].ready is True

    # A source time ahead of the injected clock is as fresh as possible.
    ahead_replies = {(_MOUNT, _KEY_A): _head_reply(_KEY_A, 1, _iso(_T0 + timedelta(seconds=5)))}
    ahead = _group(_HeadRuntime(ahead_replies), _BINDING_A).review()
    assert ahead.legs[0].age_seconds == pytest.approx(-5.0)
    assert ahead.legs[0].ready is True


def test_review_reads_each_declared_leg_once_in_order():
    other_mount = "store.market.alt"
    runtime = _HeadRuntime(
        {
            (_MOUNT, _KEY_A): _head_reply(_KEY_A, 1, _iso(_T0)),
            (other_mount, _KEY_B): _head_reply(_KEY_B, 1, _iso(_T0)),
        }
    )
    group = _group(runtime, _BINDING_A, MarketLegBinding(other_mount, _KEY_B))
    status = group.review()

    assert group.legs == (_BINDING_A, MarketLegBinding(other_mount, _KEY_B))
    assert group.max_time_skew_seconds == 5.0
    assert status.ready is True
    assert [leg.mount for leg in status.legs] == [_MOUNT, other_mount]
    assert runtime.calls == [
        (_MOUNT, "market.head", {"key": _KEY_A.to_dict()}),
        (other_mount, "market.head", {"key": _KEY_B.to_dict()}),
    ]


def test_head_read_failure_fails_closed():
    timeout = _HeadRuntime({(_MOUNT, _KEY_A): ServiceRuntimeError("TIMEOUT")})
    error = _expect_code("LEG_HEAD_READ_FAILED", _group(timeout, _BINDING_A).review)
    assert error.cause_code == "TIMEOUT"

    broken = _HeadRuntime({(_MOUNT, _KEY_A): RuntimeError("wire exploded")})
    error = _expect_code("LEG_HEAD_READ_FAILED", _group(broken, _BINDING_A).review)
    assert error.cause_code is None


def test_malformed_head_replies_are_rejected_without_freshness_claims():
    def review_with(reply):
        runtime = _HeadRuntime({(_MOUNT, _KEY_A): reply})
        return _group(runtime, _BINDING_A).review

    wrong_stream = dict(_head_reply(_KEY_B, 1, _iso(_T0)))
    _expect_code("INVALID_HEAD_REPLY", review_with(wrong_stream))

    missing = _head_reply(_KEY_A, 1, _iso(_T0))
    missing.pop("oldest_sequence")
    _expect_code("INVALID_HEAD_REPLY", review_with(missing))

    extra = dict(_head_reply(_KEY_A, 1, _iso(_T0)), extra_field=True)
    _expect_code("INVALID_HEAD_REPLY", review_with(extra))

    negative_head = _head_reply(_KEY_A, -1, _iso(_T0))
    _expect_code("INVALID_HEAD_REPLY", review_with(negative_head))

    bad_oldest = dict(_head_reply(_KEY_A, 1, _iso(_T0)), oldest_sequence=0)
    _expect_code("INVALID_HEAD_REPLY", review_with(bad_oldest))

    empty_epoch = dict(_head_reply(_KEY_A, 1, _iso(_T0)), epoch="")
    _expect_code("INVALID_HEAD_REPLY", review_with(empty_epoch))

    numeric_observed = dict(_head_reply(_KEY_A, 1, _iso(_T0)), latest_observed_at_utc=1)
    _expect_code("INVALID_HEAD_REPLY", review_with(numeric_observed))

    _expect_code("INVALID_OBSERVED_AT", review_with(_head_reply(_KEY_A, 1, "not-a-timestamp")))
    _expect_code("INVALID_OBSERVED_AT", review_with(_head_reply(_KEY_A, 1, "2026-10-16T10:00:00")))


def test_group_configuration_validation():
    good_runtime = _HeadRuntime({})
    _expect_code(
        "INVALID_RUNTIME", ServiceMarketLegGroup, object(), (_BINDING_A,), max_time_skew_seconds=5.0
    )
    _expect_code(
        "EMPTY_LEG_GROUP", ServiceMarketLegGroup, good_runtime, (), max_time_skew_seconds=5.0
    )
    _expect_code(
        "INVALID_LEGS", ServiceMarketLegGroup, good_runtime, None, max_time_skew_seconds=5.0
    )
    _expect_code(
        "DUPLICATE_LEG",
        ServiceMarketLegGroup,
        good_runtime,
        (_BINDING_A, _BINDING_A),
        max_time_skew_seconds=5.0,
    )
    _expect_code(
        "INVALID_LEG_BINDING",
        ServiceMarketLegGroup,
        good_runtime,
        ((_MOUNT, _KEY_A),),
        max_time_skew_seconds=5.0,
    )
    _expect_code(
        "INVALID_LEG_MOUNT",
        ServiceMarketLegGroup,
        good_runtime,
        (MarketLegBinding("", _KEY_A),),
        max_time_skew_seconds=5.0,
    )
    _expect_code(
        "INVALID_LEG_MOUNT",
        ServiceMarketLegGroup,
        good_runtime,
        (MarketLegBinding(" store.market", _KEY_A),),
        max_time_skew_seconds=5.0,
    )
    _expect_code(
        "INVALID_LEG_KEY",
        ServiceMarketLegGroup,
        good_runtime,
        (MarketLegBinding(_MOUNT, "leg-a"),),
        max_time_skew_seconds=5.0,
    )
    for bad_skew in (-0.5, float("nan"), float("inf"), True, "5", None):
        _expect_code(
            "INVALID_MAX_TIME_SKEW",
            ServiceMarketLegGroup,
            good_runtime,
            (_BINDING_A,),
            max_time_skew_seconds=bad_skew,
        )
    _expect_code(
        "INVALID_CLOCK",
        ServiceMarketLegGroup,
        good_runtime,
        (_BINDING_A,),
        max_time_skew_seconds=5.0,
        utc_clock=5,
    )


def test_clock_must_return_an_aware_datetime():
    runtime = _HeadRuntime({(_MOUNT, _KEY_A): _head_reply(_KEY_A, 1, _iso(_T0))})
    naive = _group(runtime, _BINDING_A, clock=_Clock(datetime(2026, 10, 16, 10, 0, 0)))
    _expect_code("INVALID_CLOCK_VALUE", naive.review)
    textual = _group(runtime, _BINDING_A, clock=lambda: "2026-10-16T10:00:00Z")
    _expect_code("INVALID_CLOCK_VALUE", textual.review)
