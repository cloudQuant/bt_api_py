"""Observed prices must come from the observation, not the original intent."""

import inspect
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade import ledger
from bt_api_risk.pretrade.controls import self_trade
from bt_api_risk.pretrade.engine import PretradeEngine
from bt_api_risk.pretrade.facts import RiskFactError

from bt_api_py.risk_service_gateway import (
    ObservationPump,
    RiskFactsCache,
    observed_event_from_account_event,
)
from tests.unit.test_iter64_r04_observation_identity_mapping_fake import (
    attributed_trade,
    event_item,
)
from tests.unit.test_iter64_risk_service_gateway import Observer, page

EPOCH = "e" * 64


def item_for(kind, principal):
    item = attributed_trade() if kind == DirectExecutionEventKind.TRADE else event_item()
    object.__setattr__(item.event, "command_principal", principal)
    observation = item.event.observation
    if kind != DirectExecutionEventKind.TRADE:
        object.__setattr__(observation, "kind", kind)
        if kind != DirectExecutionEventKind.ORDER:
            object.__setattr__(observation, "status", None)
    return item


def mapped(item, payload):
    return observed_event_from_account_event(
        item, epoch=EPOCH, risk_principal="risk-a", intent_payload=lambda _: payload,
    )


@pytest.mark.parametrize("principal", ["risk-a", "other"])
@pytest.mark.parametrize("kind", [DirectExecutionEventKind.ORDER, DirectExecutionEventKind.TRADE,
                                  DirectExecutionEventKind.CANCEL_ERROR, DirectExecutionEventKind.ORDER_ERROR])
@pytest.mark.parametrize("payload_price", ["105", "bad", None])
def test_missing_source_price_stays_unknown(principal, kind, payload_price):
    item = item_for(kind, principal)
    result = mapped(item, {"price": payload_price, "quantity": "2"})
    assert result.limit_price is None


@pytest.mark.parametrize("principal", ["risk-a", "other"])
@pytest.mark.parametrize("kind", [DirectExecutionEventKind.ORDER, DirectExecutionEventKind.TRADE,
                                  DirectExecutionEventKind.CANCEL_ERROR])
def test_source_price_wins_and_bad_source_price_is_rejected(principal, kind):
    item = item_for(kind, principal)
    object.__setattr__(item.event.observation, "limit_price", "99")
    assert mapped(item, {"price": "105", "quantity": "2"}).limit_price == Decimal(99)
    object.__setattr__(item.event.observation, "limit_price", "invalid")
    with pytest.raises(RiskFactError, match="^RISK_OBSERVATION_INVALID$"):
        mapped(item, {"price": "105", "quantity": "2"})


class MemoryLedger:
    def __init__(self, txn):
        self.txn = txn

    @contextmanager
    def transaction(self):
        yield self.txn


@contextmanager
def observation_engine():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA temp_store=MEMORY")
        assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert conn.execute("PRAGMA journal_mode=MEMORY").fetchone()[0] == "memory"
        assert next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main") == ""
        conn.row_factory = sqlite3.Row
        for statement in ledger._split_statements(ledger._SCHEMA):
            conn.execute(statement)
        scope = AccountScope("synthetic", "account", "simulation")
        fence = SimpleNamespace(scope=scope, owner_id="synthetic", fencing_token=1)
        args = (conn, scope.key, fence, None)
        if "schema_version" in inspect.signature(ledger.LedgerTxn).parameters:
            args += (3,)
        txn = ledger.LedgerTxn(*args)
        engine = PretradeEngine.__new__(PretradeEngine)
        engine._active = None
        engine._ledger = MemoryLedger(txn)
        engine._clock = lambda: 60
        engine._facts = SimpleNamespace(trading_day=lambda: "20261108")
        engine._origin = "risk:risk-a"
        yield engine, txn
    finally:
        conn.rollback()
        conn.close()


def new_pump(engine, item, *, acknowledged=0):
    observer = Observer([page([item], epoch=EPOCH)], offered=1,
                        epoch=EPOCH, acknowledged=acknowledged)
    pump = ObservationPump(
        engine, observer, RiskFactsCache({}), risk_principal="risk-a",
        intent_payload=lambda _: {"price": "105"}, timeout_seconds=1,
    )
    return pump, observer


def test_real_pump_and_engine_keep_unknown_price_as_conservative_crossing():
    item = item_for(DirectExecutionEventKind.ORDER, "risk-a")
    with observation_engine() as (engine, txn):
        pump, observer = new_pump(engine, item)
        assert pump.poll() == 1
        assert observer.acks == [1]
        order = txn.order(mapped(item, {"price": "105"}).order_identity)
        assert order["limit_price"] is None
        assert self_trade("SELL", Decimal(100), [("BUY", None)])


def test_old_acknowledged_fingerprint_with_fallback_price_is_not_silently_replayed():
    item = item_for(DirectExecutionEventKind.ORDER, "risk-a")
    with observation_engine() as (engine, txn):
        current = mapped(item, {"price": "105"})
        prior = replace(current, limit_price=Decimal(105))
        engine.reset_observations_bound(EPOCH, 0)
        assert not engine.apply_observations_bound([prior]).blocked
        pump, observer = new_pump(engine, item, acknowledged=1)
        assert pump.poll() == 0
        assert observer.acks == []
        assert pump.verified_epoch is None
        assert txn.cursor()["continuity"] == "UNVERIFIED"
        assert txn.order(prior.order_identity)["limit_price"] == "105"


def legacy_trade_item():
    item = item_for(DirectExecutionEventKind.ORDER, "risk-a")
    observation = item.event.observation
    for name, value in (
        ("kind", DirectExecutionEventKind.TRADE), ("status", None),
        ("trade_quantity", "1"), ("trade_price", "10"), ("trade_key_digest", "f" * 64),
    ):
        object.__setattr__(observation, name, value)
    return item


def seed_legacy_trade(engine, item):
    prior = replace(mapped(item, {"price": "105"}), epoch="d" * 64, limit_price=Decimal(105))
    engine.reset_observations_bound(prior.epoch, 0)
    result = engine.apply_observations_bound([prior])
    assert not result.blocked
    return prior


def test_cross_epoch_trade_fallback_fingerprint_conflict_does_not_book_twice_or_ack():
    item = legacy_trade_item()
    with observation_engine() as (engine, txn):
        prior = seed_legacy_trade(engine, item)
        before_trade = dict(txn.trade_by_id(prior.trade_id))
        before_order = dict(txn.order(prior.order_identity))
        assert len(txn.working_orders("rb")) == 1
        pump, observer = new_pump(engine, item)
        assert pump.poll() == 1  # examined/applied event count, not a new trade or an ACK
        assert pump.last_code == "observation_partial_apply"
        assert observer.acks == []
        assert pump.verified_epoch is None
        assert txn.cursor()["continuity"] == "UNVERIFIED"
        assert txn.first_audit_detail("trade_identity_conflict") is not None
        assert dict(txn.trade_by_id(prior.trade_id)) == before_trade
        assert txn._c.execute("SELECT COUNT(*) FROM risk_pretrade_trades").fetchone()[0] == 1
        after_order = txn.order(prior.order_identity)
        assert after_order["terminal"] == before_order["terminal"] == 0
        assert after_order["quantity"] == before_order["quantity"]
        assert after_order["filled_quantity"] == before_order["filled_quantity"]
        assert len(txn.working_orders("rb")) == 1


@pytest.mark.parametrize("code", ["EPOCH_MISMATCH", "INVALID_SEQUENCE", "CONSUMER_REPLAY_REQUIRED"])
def test_unavailable_historical_replay_leaves_projection_unverified_and_trade_intact(code):
    item = legacy_trade_item()
    with observation_engine() as (engine, txn):
        prior = seed_legacy_trade(engine, item)
        before_trade = dict(txn.trade_by_id(prior.trade_id))
        before_order = dict(txn.order(prior.order_identity))
        denial = SimpleNamespace(outcome="REQUEST_DENIED", code=code, page=None)
        observer = Observer([denial], offered=1, epoch=EPOCH, acknowledged=1)
        pump = ObservationPump(
            engine, observer, RiskFactsCache({}), risk_principal="risk-a",
            intent_payload=lambda _: {"price": "105"}, timeout_seconds=1,
        )
        assert pump.poll() == 0
        assert pump.last_code == "observation_replay_required"
        assert observer.acks == [] and pump.verified_epoch is None
        assert txn.cursor()["continuity"] == "UNVERIFIED"
        assert dict(txn.trade_by_id(prior.trade_id)) == before_trade
        assert txn._c.execute("SELECT COUNT(*) FROM risk_pretrade_trades").fetchone()[0] == 1
        assert dict(txn.order(prior.order_identity)) == before_order
        assert len(txn.working_orders("rb")) == 1
