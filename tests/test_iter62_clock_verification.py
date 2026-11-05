"""Iteration 62: SDK clock evidence and market-tick clock qualification."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from bt_api_base.market_protocol import (
    MarketInstrumentKey,
    MarketSourceDescriptor,
    MarketSubscriptionKey,
)
from bt_api_base.market_tick_protocol import (
    MarketClockQuality,
    MarketEventCursor,
    MarketFreshnessStatus,
    MarketTickPayload,
    bind_market_tick_event,
)
from bt_api_py.clock_verification import ClockVerifier, qualify_market_tick_event

INSTRUMENT = MarketInstrumentKey(
    provider="ctp",
    venue="SHFE",
    environment="demo",
    product_type="future",
    instrument_id="rb2701",
    native_symbol="rb2701",
)
KEY = MarketSubscriptionKey(
    source_ref="ctp-direct-market",
    entitlement_ref="ctp-direct-entitlement",
    instrument=INSTRUMENT,
    channel="tick",
)
SOURCE = MarketSourceDescriptor(
    key=KEY,
    stream_id=KEY.stream_id,
    epoch="epoch-1",
    provider="ctp",
    source="ctp.native.md",
    connection_generation=1,
    clock_domain_id="clock-domain-1",
    clock_source_session_id="clock-session-1",
    clock_source="ctp.exchange",
)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _unqualified_event(event_time: datetime, received: datetime):
    payload = MarketTickPayload.from_dict(
        {
            "schema": "sdk.market.tick",
            "schema_version": 2,
            "instrument": INSTRUMENT.to_dict(),
            "last_price": 3000.0,
            "bid_price": 2999.0,
            "ask_price": 3001.0,
            "bid_size": 1.0,
            "ask_size": 1.0,
            "lower_limit": None,
            "upper_limit": None,
            "turnover": None,
            "open_interest": None,
            "delta_volume": 1.0,
            "cumulative_volume": 10.0,
            "volume_complete": True,
            "volume_quality": "continuous",
            "continuity_status": "continuous",
            "trading_date": "2026-11-06",
            "event_session_date": "2026-11-05",
            "provider_event_time_utc": _utc(event_time),
            "event_time_provenance": "provider_reported",
            "received_at_utc": _utc(received),
            "received_monotonic_ns": time.monotonic_ns(),
            "provider_source": "ctp.native.md",
            "quality_contract_ref": "rules-1",
            "provider_connection_generation": 1,
            "provider_sequence": 1,
            "provider_subscription_epoch": 1,
            "clock_domain_id": "clock-domain-1",
            "clock_source_session_id": "clock-session-1",
            "clock_source": "ctp.exchange",
            "source_clock_quality": "unknown",
            "receive_clock_quality": "unknown",
            "source_clock_error_ms": None,
            "receive_clock_error_ms": None,
            "freshness_status": "unavailable",
            "freshness_reason_codes": ["SOURCE_CLOCK_CALIBRATION_NOT_PROVIDED"],
            "execution_eligible": False,
            "quality_flags": [],
        }
    )
    return bind_market_tick_event(payload, SOURCE, MarketEventCursor(SOURCE.stream_id, "epoch-1", 1))


def _verifier(offset_ms=0.0, bound_ms=50.0):
    return ClockVerifier(
        servers=("fake",),
        query=lambda host, timeout: (int(offset_ms * 1e6), int(bound_ms * 1e6)),
    )


def test_evidence_and_bound_limit():
    verifier = _verifier(bound_ms=50.0)
    evidence = verifier.evidence()
    assert evidence is not None and evidence.is_valid()
    too_wide = ClockVerifier(servers=("fake",), query=lambda h, t: (0, int(900 * 1e6)))
    assert too_wide.evidence() is None and too_wide.last_error == "clock_error_bound_too_large"
    failing = ClockVerifier(servers=("fake",), query=lambda h, t: (_ for _ in ()).throw(OSError()))
    assert failing.evidence() is None and failing.last_error == "clock_evidence_unavailable"


def test_venue_time_check_tolerance():
    verifier = _verifier()
    now_ns = time.time_ns()
    local = datetime.fromtimestamp(now_ns / 1e9, timezone(timedelta(hours=8)))
    ok = verifier.check_venue_time(local.strftime("%H:%M:%S"), now_ns)
    assert ok is not None and ok.consistent
    off = (local + timedelta(seconds=10)).strftime("%H:%M:%S")
    bad = verifier.check_venue_time(off, now_ns)
    assert bad is not None and not bad.consistent


def test_qualified_fresh_and_stale_and_passthrough():
    verifier = _verifier(offset_ms=280.0)
    evidence = verifier.evidence()
    now = datetime.now(timezone.utc)
    now_ns = time.time_ns()
    local = datetime.fromtimestamp(now_ns / 1e9, timezone(timedelta(hours=8)))
    check = verifier.check_venue_time(local.strftime("%H:%M:%S"), now_ns)

    fresh = qualify_market_tick_event(
        _unqualified_event(now - timedelta(milliseconds=500), now), evidence, check
    )
    assert fresh.clock.source_clock_quality is MarketClockQuality.VERIFIED
    assert fresh.clock.receive_clock_quality is MarketClockQuality.VERIFIED
    assert fresh.clock.freshness_status is MarketFreshnessStatus.FRESH
    assert fresh.data_clock_qualified is True
    assert fresh.data.execution_eligible is True

    stale = qualify_market_tick_event(
        _unqualified_event(now - timedelta(seconds=5), now), evidence, check
    )
    assert stale.clock.freshness_status is MarketFreshnessStatus.STALE
    assert stale.data.freshness_reason_codes == ("EVENT_TIME_STALE",)
    assert stale.data_clock_qualified is False

    original = _unqualified_event(now, now)
    assert qualify_market_tick_event(original, None, check) is original
    assert qualify_market_tick_event(original, evidence, None) is original


def test_venue_lag_within_latency_budget_is_consistent():
    """SimNow 2026-11-05: login venue time ~2.1 s behind the corrected local clock."""
    verifier = _verifier()
    now_ns = time.time_ns()
    lagged = datetime.fromtimestamp(now_ns / 1e9 - 2.1, timezone(timedelta(hours=8)))
    hms = lagged.strftime("%H:%M:%S")
    within = verifier.check_venue_time(hms, now_ns, latency_allowance_ms=2000.0)
    assert within is not None and within.consistent
    strict = verifier.check_venue_time(hms, now_ns, latency_allowance_ms=0.0)
    assert strict is not None and not strict.consistent


def test_sdk_policy_ref_when_provider_attests_no_rules_and_gap_blocks():
    from dataclasses import replace as _replace

    verifier = _verifier()
    evidence = verifier.evidence()
    now = datetime.now(timezone.utc)
    now_ns = time.time_ns()
    local = datetime.fromtimestamp(now_ns / 1e9, timezone(timedelta(hours=8)))
    check = verifier.check_venue_time(local.strftime("%H:%M:%S"), now_ns)
    base = _unqualified_event(now - timedelta(milliseconds=300), now)
    no_rules = bind_market_tick_event(
        _replace(base.data, quality_contract_ref=None), base.source, base.cursor
    )
    qualified = qualify_market_tick_event(no_rules, evidence, check)
    assert qualified.data.execution_eligible is True
    assert qualified.data.quality_contract_ref.startswith("bt_api_py.market_clock_qualification.v1")
    gap = bind_market_tick_event(
        _replace(base.data, continuity_status="gap"), base.source, base.cursor
    )
    assert qualify_market_tick_event(gap, evidence, check).data.execution_eligible is False


def test_venue_epoch_check_uses_midpoint_round_trip_and_budget():
    verifier = _verifier()  # zero offset, 50 ms bound
    sent = time.time_ns()
    received = sent + 40_000_000  # 40 ms round trip
    mid_ms = (sent + received) / 2e6
    ok = verifier.check_venue_epoch(mid_ms + 300.0, sent, received)
    assert ok is not None and ok.consistent
    assert abs(ok.tolerance_ms - (20.0 + 1.0 + 50.0 + 2000.0)) < 1e-6
    far = verifier.check_venue_epoch(mid_ms + 5000.0, sent, received)
    assert far is not None and not far.consistent
    assert verifier.check_venue_epoch(mid_ms, received, sent) is None  # received before sent
