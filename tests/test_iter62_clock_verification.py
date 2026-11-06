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
    """SimNow 2026-11-05: login venue time ~2.1 s behind the corrected local clock.

    The measured 2.1 s already includes the whole-second truncation, so the
    corrected local time is pinned at N.1 s and the venue second at N - 2
    (integer arithmetic; a wall-clock phase would add up to 1 s and flake).
    """
    verifier = _verifier()
    offset_ns = verifier.evidence().offset_ns
    whole = time.time_ns() // 1_000_000_000
    now_ns = whole * 1_000_000_000 + 100_000_000 - offset_ns  # corrected = N.1 s
    venue = datetime.fromtimestamp(whole - 2, timezone(timedelta(hours=8)))
    hms = venue.strftime("%H:%M:%S")
    within = verifier.check_venue_time(hms, now_ns, latency_allowance_ms=2000.0)
    assert within is not None and within.consistent
    assert abs(within.difference_ms + 2100.0) < 1.0
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


class _FlakyQuery:
    """SNTP stand-in: answers per a script of booleans (False = lost exchange)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, host, timeout):
        self.calls += 1
        ok = self.script.pop(0) if self.script else True
        if not ok:
            raise OSError("synthetic lost SNTP exchange")
        return (int(5e6), int(40e6))


def test_failed_remeasurement_keeps_evidence_that_is_still_valid():
    """SimNow 2026-11-06: one lost SNTP exchange must not void valid evidence."""
    query = _FlakyQuery([True, False])
    verifier = ClockVerifier(servers=("fake",), query=query, validity_seconds=900.0)
    first = verifier.refresh()
    assert first is not None
    assert verifier.refresh() is None  # the re-measurement failed
    assert verifier.last_error == "clock_evidence_unavailable"
    assert verifier.evidence(refresh_if_expired=False) is first  # still trusted


def test_failed_remeasurement_does_not_extend_expired_evidence():
    query = _FlakyQuery([True, False])
    verifier = ClockVerifier(servers=("fake",), query=query, validity_seconds=0.05)
    assert verifier.refresh() is not None
    time.sleep(0.08)
    assert verifier.refresh() is None
    assert verifier.evidence(refresh_if_expired=False) is None


def test_background_refresh_retries_a_failed_measurement_soon():
    """The tick path never refreshes inline, so a failed first measurement must
    be retried within seconds, not after a full interval."""
    query = _FlakyQuery([False])
    verifier = ClockVerifier(servers=("fake",), query=query)
    verifier.start_background_refresh(interval_seconds=300.0)
    try:
        deadline = time.monotonic() + 5.0
        while verifier.evidence(refresh_if_expired=False) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert verifier.evidence(refresh_if_expired=False) is not None
        assert query.calls == 2
    finally:
        verifier.stop_background_refresh()


def test_closing_the_facade_stops_the_background_refresh():
    """``BtApi.close()`` must not leave the SNTP refresh thread running (iteration 62)."""
    from bt_api_py.bt_api import BtApi

    query = _FlakyQuery([True])
    api = BtApi(
        {},
        market_clock_verification={
            "servers": ("fake",),
            "query": query,
            "refresh_interval_seconds": 300.0,
        },
    )
    verifier = api.market_clock_verifier
    thread = verifier._refresher
    assert thread is not None and thread.is_alive()
    api.close()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert verifier._refresher is None


class _BlockedSntp:
    """SNTP stand-in for a network that drops every exchange (counts the attempts)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, host, timeout):
        self.calls += 1
        raise OSError("synthetic: SNTP blocked")


def _tick_path_facade(verifier, observation_reader):
    """Just enough of ``BtApi`` for its per-tick qualification methods."""
    import types

    from bt_api_py.bt_api import BtApi

    provider = types.SimpleNamespace(**observation_reader)
    facade = types.SimpleNamespace(
        _market_clock_verifier=verifier,
        _market_clock_max_latency_seconds=2.0,
        direct_writes=lambda: types.SimpleNamespace(provider=lambda name: provider),
    )
    facade._venue_clock_check = types.MethodType(BtApi._venue_clock_check, facade)
    facade._venue_epoch_clock_check = types.MethodType(BtApi._venue_epoch_clock_check, facade)
    return lambda exchange, event: BtApi._qualify_market_tick(facade, exchange, event)


def test_tick_qualification_never_runs_sntp_inline_without_evidence():
    """The tick path (``ServiceMarketStream``/owner pump) must not block on SNTP.

    Without valid evidence the per-tick venue check called ``evidence()`` with
    an inline refresh, so on a network dropping SNTP every tick waited
    ``timeout x servers`` (here 3 queries per tick) on the stream owner thread.
    Only the background refresher measures; the tick stays unqualified.
    """
    query = _BlockedSntp()
    verifier = ClockVerifier(servers=("a", "b", "c"), query=query, timeout_seconds=0.01)
    now_ns = time.time_ns()
    hms = datetime.fromtimestamp(now_ns / 1e9, timezone(timedelta(hours=8))).strftime("%H:%M:%S")
    login = {
        "login_clock_observation": lambda: {
            "exchange_times": {"SHFE": hms},
            "local_received_ns": now_ns,
            "connection_generation": 1,
        }
    }
    server_time = {
        "login_clock_observation": lambda: None,
        "server_time_observation": lambda: {
            "venue_epoch_ms": now_ns / 1e6,
            "local_sent_ns": now_ns,
            "local_received_ns": now_ns,
        },
    }
    now = datetime.now(timezone.utc)
    for reader in (login, server_time):
        qualify = _tick_path_facade(verifier, reader)
        for _ in range(3):
            event = _unqualified_event(now - timedelta(milliseconds=200), now)
            assert qualify("CTP___FUTURE", event) is event  # unqualified, passed through
    assert query.calls == 0


def test_venue_checks_can_skip_the_inline_refresh():
    query = _BlockedSntp()
    verifier = ClockVerifier(servers=("a",), query=query, timeout_seconds=0.01)
    now_ns = time.time_ns()
    assert verifier.check_venue_time("09:00:00", now_ns, refresh_if_expired=False) is None
    assert verifier.check_venue_epoch(now_ns / 1e6, now_ns, now_ns, refresh_if_expired=False) is None
    assert query.calls == 0
    # The default keeps the previous behaviour for explicit (non-tick) callers.
    assert verifier.check_venue_time("09:00:00", now_ns) is None
    assert query.calls == 1
