"""Pure fake local receipt-to-cache tests; no owner thread or network."""
import os
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.market_tick_protocol import MarketTickPayload
from bt_api_py.gateway_strategy_market import GatewayMarketReceivedEvent, GatewayMarketSubscriptionClient
from bt_api_py.risk_service_gateway import RiskFactsCache, RiskMarketFeed
from bt_api_py.service_market import MarketReceiveReceipt


KEY = MarketSubscriptionKey("source", "entitlement", MarketInstrumentKey(
    "test", "SHFE", "test", "future", "rb", "rb"), "tick")


def _received(source="pub", pid=None):
    tick = SimpleNamespace(instrument=KEY.instrument, last_price=Decimal(100),
        provider_event_time_utc="2026-11-14T01:00:00Z", quality_contract_ref="q",
        continuity_status="continuous", trading_date="20261114")
    event = SimpleNamespace(payload={"key": KEY.to_dict(), "data": {"tick": tick}},
        header=SimpleNamespace(stream_id=KEY.stream_id, stream_epoch="ep", sequence=1, message_id="m1"))
    receipt = MarketReceiveReceipt(source, KEY.stream_id, "ep", 1, "m1", time.time_ns(),
                                  time.monotonic_ns(), os.getpid() if pid is None else pid, 1)
    return GatewayMarketReceivedEvent(event, receipt)


def _feed(monkeypatch, received):
    monkeypatch.setattr(MarketTickPayload, "from_dict", classmethod(lambda cls, raw: raw["tick"]))
    client = GatewayMarketSubscriptionClient(None, "test")
    frames = [received]
    def poll_with_receipt(*, timeout_seconds):
        if frames:
            result = frames.pop()
            client._issued_received_event = result
            return result
        return None
    client.poll_with_receipt = poll_with_receipt
    facts = RiskFactsCache({})
    feed = RiskMarketFeed({"one": (client, KEY, "rb")}, facts, timeout_seconds=0)
    monkeypatch.setattr(feed, "_attested", lambda *_: True)
    return feed, facts, client


def test_real_feed_path_pairs_only_exact_fact_and_forgets_on_replacement(monkeypatch):
    received = _received()
    feed, facts, client = _feed(monkeypatch, received)
    assert feed.poll() == 1
    fact = facts.reference_price(KEY.instrument)
    assert facts.reference_price_with_local_receive(KEY.instrument) == (
        fact, (received.receipt.received_monotonic_ns, os.getpid()))
    facts.note_tick("rb", object(), None, None)
    assert facts.reference_price_with_local_receive(KEY.instrument) == (None, None)
    assert RiskMarketFeed._local_receive(client, _received(), KEY) is None


@pytest.mark.parametrize("source,pid", [("replay", None), ("pub", -1)])
def test_replay_and_foreign_process_do_not_get_local_receive(monkeypatch, source, pid):
    feed, facts, _ = _feed(monkeypatch, _received(source, pid))
    assert feed.poll() == 1
    assert facts.reference_price_with_local_receive(KEY.instrument)[1] is None


def test_busy_observation_lock_does_not_block_refresh_or_reuse_old_sample(monkeypatch):
    received = _received()
    feed, facts, _ = _feed(monkeypatch, received)
    assert feed.poll() == 1
    fact = facts.reference_price(KEY.instrument)
    assert facts.reference_price_with_local_receive(KEY.instrument)[1] is not None
    facts._price_receipts_lock.acquire()
    try:
        # Same object is deliberately reused, but this is a new cache entry.
        facts.note_tick("rb", fact, None, None)
        assert facts.reference_price(KEY.instrument) is fact
    finally:
        facts._price_receipts_lock.release()
    assert facts.reference_price_with_local_receive(KEY.instrument) == (fact, None)


def test_busy_observation_lock_does_not_block_forget(monkeypatch):
    feed, facts, _ = _feed(monkeypatch, _received())
    assert feed.poll() == 1
    fact = facts.reference_price(KEY.instrument)
    facts._price_receipts_lock.acquire()
    try:
        facts.forget_market()
        assert facts.reference_price(KEY.instrument) is None
    finally:
        facts._price_receipts_lock.release()
    assert facts.reference_price_with_local_receive(KEY.instrument) == (None, None)


def test_interleaved_cache_writes_never_reassign_an_old_receipt(monkeypatch):
    feed, facts, _ = _feed(monkeypatch, _received())
    assert feed.poll() == 1
    fact = facts.reference_price(KEY.instrument)
    class InterleavedPrices(dict):
        inject = True
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            if self.inject:
                self.inject = False
                # A later cache entry reuses the exact same fact object.
                facts.note_tick("rb", fact, None, None, _local_receive=(22, os.getpid()))
    facts._prices = InterleavedPrices(facts._prices)
    facts.note_tick("rb", fact, None, None, _local_receive=(11, os.getpid()))
    assert facts.reference_price(KEY.instrument) is fact
    # The earlier writer may overwrite telemetry, but cannot match the later
    # entry; losing this sample is safer than assigning it to the wrong tick.
    assert facts.reference_price_with_local_receive(KEY.instrument) == (fact, None)


def test_gather_does_not_pair_rewritten_same_object_with_new_receipt(monkeypatch):
    from bt_api_risk.pretrade.engine import PretradeEngine
    feed, facts, _ = _feed(monkeypatch, _received())
    assert feed.poll() == 1
    fact = facts.reference_price(KEY.instrument)
    original = facts._local_receive_for_entry
    def rewrite_after_entry_read(price, entry):
        facts.note_tick("rb", fact, None, None, _local_receive=(22, os.getpid()))
        return original(price, entry)
    monkeypatch.setattr(facts, "_local_receive_for_entry", rewrite_after_entry_read)
    engine = object.__new__(PretradeEngine)
    engine._facts, engine._clock = facts, lambda: 1
    snapshot = engine._gather(KEY.instrument, "s")
    assert snapshot.reference is fact
    assert snapshot.local_receive_monotonic_ns is None
    assert snapshot.local_receive_pid is None


def test_gather_preserves_price_when_observation_lock_is_busy(monkeypatch):
    from bt_api_risk.pretrade.engine import PretradeEngine
    feed, facts, _ = _feed(monkeypatch, _received())
    assert feed.poll() == 1
    fact = facts.reference_price(KEY.instrument)
    engine = object.__new__(PretradeEngine)
    engine._facts, engine._clock = facts, lambda: 1
    facts._price_receipts_lock.acquire()
    try:
        snapshot = engine._gather(KEY.instrument, "s")
    finally:
        facts._price_receipts_lock.release()
    assert snapshot.reference is fact
    assert snapshot.local_receive_monotonic_ns is None


def test_overridden_fact_reader_remains_authoritative(monkeypatch):
    from bt_api_risk.pretrade.engine import PretradeEngine
    feed, facts, _ = _feed(monkeypatch, _received())
    assert feed.poll() == 1
    monkeypatch.setattr(facts, "reference_price", lambda instrument: None)
    engine = object.__new__(PretradeEngine)
    engine._facts, engine._clock = facts, lambda: 1
    snapshot = engine._gather(KEY.instrument, "s")
    assert snapshot.reference is None
    assert snapshot.local_receive_monotonic_ns is None


def test_cached_receipt_reaches_real_engine_reference_check(monkeypatch):
    from bt_api_risk.pretrade.controls import OrderShape
    from bt_api_risk.pretrade.engine import PretradeEngine
    from bt_api_risk.pretrade.local_receive_observation import LocalReceiveObservations
    received = _received()
    feed, facts, _ = _feed(monkeypatch, received)
    assert feed.poll() == 1
    engine = object.__new__(PretradeEngine)
    engine._facts, engine._clock = facts, lambda: 1
    engine._local_receive_observations = LocalReceiveObservations()
    snapshot = engine._gather(KEY.instrument, "s")
    monkeypatch.setattr("bt_api_risk.pretrade.local_receive_observation.time.monotonic_ns",
                        lambda: received.receipt.received_monotonic_ns + 2_000_000)
    policy = SimpleNamespace(max_notional={"s": {"rb": Decimal(500)}},
                             per_order_max_reference_age_ms=1)
    order = OrderShape("s", "rb", "SHFE", "BUY", "OPEN", "INCREASE", "LONG", "OPEN",
                       Decimal(1), None)
    # This refusal is independent of telemetry: instrument facts are absent.
    assert engine._observed_notional(policy, order, snapshot) == "risk_instrument_facts_unavailable"
    assert engine._local_receive_observations.snapshot()["counts"]["exceeded"] == 1
