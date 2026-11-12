"""Risk market composition for the Gateway market gate (64.09, G64-10). UNIT evidence.

The end-to-end check against the spawned CTP Gateway gate is
``tests/integration/test_iter64_risk_market_gate.py``.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.market_tick_protocol import MarketTickPayload

from bt_api_py.risk_service_gateway import (
    RiskFactsCache,
    RiskMarketFeed,
    price_fact_from_market_event,
    risk_market_settings,
)

RB = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
KEY = MarketSubscriptionKey("src", "ent", RB, "tick")


def _received(sequence, *, day="2030-01-02", source="pub", price=3601.0):
    tick = SimpleNamespace(instrument=RB, last_price=price,
                           provider_event_time_utc="2030-01-01T01:00:00Z",
                           quality_contract_ref="q", continuity_status="continuous",
                           trading_date=day)
    event = SimpleNamespace(
        payload={"key": KEY.to_dict(), "data": {"day": day}, "tick": tick},
        header=SimpleNamespace(stream_epoch="ep", sequence=sequence, message_id="m" + str(sequence)))
    receipt = None if source is None else SimpleNamespace(source=source, received_wall_ns=99)
    return SimpleNamespace(event=event, receipt=receipt)


@pytest.fixture(autouse=True)
def _tick_from_payload(monkeypatch):
    monkeypatch.setattr(MarketTickPayload, "from_dict",
                        classmethod(lambda cls, raw: raw["__tick__"]))


def _patch(received):
    received.event.payload["data"] = {"__tick__": received.event.payload.pop("tick")}
    return received


@pytest.mark.parametrize(
    ("day", "expected"),
    [("2030-01-02", "20300102"), ("20300102", "20300102"), ("2030-02-30", None),
     ("2030-1-2", None), ("2030/01/02", None), ("", None), (None, None)],
)
def test_the_tick_trading_date_becomes_the_risk_trading_day(day, expected):
    # MarketTickPayload normalizes trading_date to ISO YYYY-MM-DD.
    _fact, _ref, got = price_fact_from_market_event(_patch(_received(1, day=day)), KEY)
    assert got == expected


class _Client:
    def __init__(self, frames, *, attest_error=None):
        self.frames = list(frames)
        self.attest_error = attest_error
        self.attested = []
        self.last = None

    def poll_with_receipt(self, timeout_seconds):
        if not self.frames:
            return None
        self.last = self.frames.pop(0)
        return self.last

    def clock_observation(self, *, receipt=None, timeout_seconds):
        # The real client binds only its last issued frame's receipt.
        assert receipt is self.last.receipt
        if self.attest_error is not None:
            raise self.attest_error
        self.attested.append(self.last.event.header.sequence)
        return object()


def _feed(client):
    facts = RiskFactsCache({})
    return RiskMarketFeed({"m0": (client, KEY, "rb")}, facts, timeout_seconds=1), facts


def test_only_the_latest_live_frame_is_attested_and_becomes_the_market_ref():
    client = _Client([_patch(_received(n)) for n in (1, 2, 3)])
    feed, facts = _feed(client)
    assert feed.poll() == 3
    assert client.attested == [3]
    assert facts.market_ref(RB) == {"key": KEY.to_dict(), "epoch": "ep", "sequence": 3,
                                    "message_id": "m3"}
    assert facts.reference_price(RB).last_price == Decimal("3601.0")
    assert facts.trading_day() == "20300102"


@pytest.mark.parametrize("source", [None, "replay"])
def test_a_frame_without_a_live_receipt_gives_a_price_but_no_market_ref(source):
    client = _Client([_patch(_received(1, source=source))])
    feed, facts = _feed(client)
    assert feed.poll() == 1
    assert client.attested == [] and feed.attestation_failures == 1
    assert facts.market_ref(RB) is None and facts.reference_price(RB) is not None


def test_a_refused_attestation_drops_the_previous_market_ref():
    client = _Client([_patch(_received(1))])
    feed, facts = _feed(client)
    feed.poll()
    assert facts.market_ref(RB)["sequence"] == 1
    client.frames.append(_patch(_received(2)))
    client.attest_error = RuntimeError("CLOCK_EVIDENCE_UNAVAILABLE")
    feed.poll()
    assert facts.market_ref(RB) is None and feed.attestation_failures == 1


def test_risk_market_settings_request_the_attestation_and_the_source_descriptor():
    settings = risk_market_settings({"m0": KEY})
    assert "market.clock_observation" in settings.requested_operations
    assert {"market.subscribe", "market.bootstrap_begin"} <= settings.requested_operations
    assert settings.subscriptions["m0"].key == KEY
    assert settings.subscriptions["m0"].require_source_descriptor is True
