"""In-memory Risk market invalidation; no host or external market source."""

from decimal import Decimal
import threading
from types import SimpleNamespace

import pytest

from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.market_tick_protocol import MarketTickPayload
from bt_api_py.risk_service_gateway import RiskFactsCache, RiskMarketFeed


def _key(name):
    instrument = MarketInstrumentKey("test", "SHFE", "test", "future", name, name)
    return MarketSubscriptionKey("source", "entitlement", instrument, "tick")


def _frame(key, sequence, day="20261114"):
    tick = SimpleNamespace(
        instrument=key.instrument, last_price=100 + sequence,
        provider_event_time_utc="2026-11-14T01:00:00Z", quality_contract_ref="q",
        continuity_status="continuous", trading_date=day,
    )
    return SimpleNamespace(
        event=SimpleNamespace(
            payload={"key": key.to_dict(), "data": {"tick": tick}},
            header=SimpleNamespace(stream_epoch="epoch", sequence=sequence, message_id=f"m{sequence}"),
        ),
        receipt=SimpleNamespace(source="pub", received_wall_ns=99),
    )


class _Client:
    def __init__(self):
        self.frames = []
        self.last = None

    def poll_with_receipt(self, *, timeout_seconds):
        if not self.frames:
            return None
        value = self.frames.pop(0)
        if isinstance(value, Exception):
            raise value
        self.last = value
        return value

    def clock_observation(self, *, receipt, timeout_seconds):
        assert receipt is self.last.receipt


@pytest.fixture(autouse=True)
def _synthetic_tick(monkeypatch):
    monkeypatch.setattr(MarketTickPayload, "from_dict", classmethod(lambda cls, raw: raw["tick"]))


@pytest.fixture
def setup_feed():
    first, second = _key("rb"), _key("hc")
    first_client, second_client = _Client(), _Client()
    facts = RiskFactsCache({})
    feed = RiskMarketFeed(
        {"first": (first_client, first, "rb"), "second": (second_client, second, "hc")},
        facts, timeout_seconds=0, max_events=4,
    )
    return feed, facts, first, second, first_client, second_client


@pytest.mark.parametrize("failure", ["malformed", "foreign", "exception"])
def test_failed_subscription_revokes_prior_price_reference_and_day_only_for_it(setup_feed, failure):
    feed, facts, first, second, client, other = setup_feed
    client.frames = [_frame(first, 1, "20261116")]
    other.frames = [_frame(second, 1, "20261114")]
    assert feed.poll() == 2
    assert facts.trading_day() == "20261116"

    if failure == "malformed":
        client.frames = [SimpleNamespace(event=SimpleNamespace(payload={}))]
    elif failure == "foreign":
        client.frames = [_frame(second, 2)]
    else:
        client.frames = [RuntimeError("synthetic poll failure")]
    assert feed.poll() == 0
    assert facts.reference_price(first.instrument) is None
    assert facts.market_ref(first.instrument) is None
    assert facts.reference_price(second.instrument).last_price == Decimal(101)
    assert facts.market_ref(second.instrument)["sequence"] == 1
    assert facts.trading_day() == "20261114"
    assert feed.failures == 1


@pytest.mark.parametrize("bad_last", [True, False])
def test_valid_and_invalid_frames_apply_in_received_order(setup_feed, bad_last):
    feed, facts, first, _second, client, _other = setup_feed
    bad = SimpleNamespace(event=SimpleNamespace(payload={}))
    valid = _frame(first, 2, "20261115")
    client.frames = [valid, bad] if bad_last else [bad, valid]
    assert feed.poll() == 1
    if bad_last:
        assert facts.reference_price(first.instrument) is None
        assert facts.market_ref(first.instrument) is None
        assert facts.trading_day() is None
    else:
        assert facts.reference_price(first.instrument).last_price == Decimal(102)
        assert facts.market_ref(first.instrument)["sequence"] == 2
        assert facts.trading_day() == "20261115"


def test_bad_frames_count_toward_poll_budget_and_later_valid_frame_recovers(setup_feed):
    feed, facts, first, _second, client, _other = setup_feed
    bad = lambda: SimpleNamespace(event=SimpleNamespace(payload={}))
    client.frames = [bad(), bad(), bad(), bad(), _frame(first, 3)]
    assert feed.poll() == 0
    assert feed.failures == 4 and len(client.frames) == 1
    assert facts.market_ref(first.instrument) is None
    assert feed.poll() == 1
    assert facts.market_ref(first.instrument)["sequence"] == 3


def test_exception_after_valid_frame_revokes_uncommitted_candidate(setup_feed):
    feed, facts, first, _second, client, _other = setup_feed
    client.frames = [_frame(first, 1), RuntimeError("synthetic poll failure")]
    assert feed.poll() == 1
    assert facts.reference_price(first.instrument) is None
    assert facts.market_ref(first.instrument) is None
    assert facts.trading_day() is None


@pytest.mark.parametrize("invalid", ["99999999", "20260229", "２０２６１１１４", "20261131", "2026-11-14"])
def test_invalid_compact_calendar_day_never_replaces_valid_day(invalid):
    facts = RiskFactsCache({})
    facts.note_event_day(invalid)
    facts.note_event_day("20261114")
    assert facts.trading_day() == "20261114"


def test_market_day_is_revoked_on_invalid_tick_or_forget_without_clearing_event_day():
    facts = RiskFactsCache({})
    facts.note_tick("rb", None, None, "20261118")
    facts.note_tick("hc", None, None, "20261116")
    facts.note_event_day("20261115")
    facts.note_tick("rb", None, None, "99999999")
    assert facts.trading_day() == "20261116"
    facts.forget_market()
    assert facts.trading_day() == "20261115"
    facts.forget_event_day()
    assert facts.trading_day() is None


def test_valid_market_date_does_not_create_price_or_market_reference():
    key = _key("rb")
    facts = RiskFactsCache({})
    facts.note_tick("rb", None, None, "20261114")
    assert facts.trading_day() == "20261114"
    assert facts.reference_price(key.instrument) is None
    assert facts.market_ref(key.instrument) is None


def test_duplicate_or_mismatched_subscription_ids_are_rejected_before_start():
    first, second = _key("rb"), _key("hc")
    with pytest.raises(ValueError, match="RISK_MARKET_SUBSCRIPTION_INVALID"):
        RiskMarketFeed(
            {"one": (_Client(), first, "rb"), "two": (_Client(), first, "rb")},
            RiskFactsCache({}), timeout_seconds=0,
        )
    with pytest.raises(ValueError, match="RISK_MARKET_SUBSCRIPTION_INVALID"):
        RiskMarketFeed({"one": (_Client(), second, "rb")}, RiskFactsCache({}), timeout_seconds=0)


@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.0, "1", None])
def test_event_limit_must_be_a_positive_exact_integer(invalid):
    with pytest.raises(ValueError, match="RISK_MARKET_EVENT_LIMIT_INVALID"):
        RiskMarketFeed({}, RiskFactsCache({}), timeout_seconds=0, max_events=invalid)


@pytest.mark.parametrize("failure", ["malformed", "exception"])
def test_busy_first_subscription_cannot_starve_a_later_subscription(setup_feed, failure):
    feed, facts, first, second, client, other = setup_feed
    client.frames = [_frame(first, 1)]
    other.frames = [_frame(second, 1)]
    assert feed.poll() == 2
    # Rotate the next starting point back to the first subscription.
    assert feed.poll() == 0
    client.frames = [_frame(first, n) for n in range(2, 12)]
    other.frames = ([SimpleNamespace(event=SimpleNamespace(payload={}))] if failure == "malformed"
                    else [RuntimeError("synthetic poll failure")])
    assert feed.poll() == 4
    assert facts.market_ref(second.instrument)["sequence"] == 1
    # The still-busy first subscription cannot occupy consecutive starts.
    assert feed.poll() == (3 if failure == "malformed" else 4)
    assert facts.market_ref(second.instrument) is None
    assert facts.reference_price(second.instrument) is None
    assert facts.market_ref(first.instrument)["sequence"] == (8 if failure == "malformed" else 9)


def test_concurrent_poll_is_refused_without_interleaving_cache_updates():
    key = _key("rb")
    entered, release = threading.Event(), threading.Event()

    class BlockingClient(_Client):
        def poll_with_receipt(self, *, timeout_seconds):
            entered.set()
            assert release.wait(2)
            return super().poll_with_receipt(timeout_seconds=timeout_seconds)

    client = BlockingClient()
    client.frames = [_frame(key, 1)]
    facts = RiskFactsCache({})
    feed = RiskMarketFeed({"first": (client, key, "rb")}, facts, timeout_seconds=0)
    results = []
    worker = threading.Thread(target=lambda: results.append(feed.poll()))
    worker.start()
    try:
        assert entered.wait(2)
        with pytest.raises(RuntimeError, match="RISK_MARKET_POLL_BUSY"):
            feed.poll()
        assert facts.market_ref(key.instrument) is None
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert results == [1]
    assert facts.market_ref(key.instrument)["sequence"] == 1


def test_reentrant_poll_fails_immediately_and_outer_poll_can_finish():
    key = _key("rb")
    observed = []

    class ReentrantClient(_Client):
        def poll_with_receipt(self, *, timeout_seconds):
            with pytest.raises(RuntimeError, match="RISK_MARKET_POLL_BUSY"):
                feed.poll()
            observed.append(True)
            return super().poll_with_receipt(timeout_seconds=timeout_seconds)

    client = ReentrantClient()
    client.frames = [_frame(key, 1)]
    facts = RiskFactsCache({})
    feed = RiskMarketFeed({"first": (client, key, "rb")}, facts, timeout_seconds=0)
    assert feed.poll() == 1
    assert len(observed) == 2
    assert facts.market_ref(key.instrument)["sequence"] == 1
