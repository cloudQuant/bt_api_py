"""SDK pump and real Risk ledger: failed batch arithmetic never grants an ACK.

UNIT/FAKE only: a temporary SQLite journal and synthetic Gateway observations.
"""

import hashlib
import json
from decimal import Decimal, localcontext

import pytest
from bt_api_base.risk_service_protocol import RISK_ORDER_OPERATIONS
from bt_api_risk.pretrade import CallerIdentity
from bt_api_risk.pretrade.controls import derive_effect
from bt_api_risk.pretrade.ledger import PretradeDecimalEncodingError

from tests.unit.test_iter64_risk_service_component import (
    DAY, payload, service as _service_fixture,
)
from tests.unit.test_iter64_risk_service_gateway import (
    EPOCH, Observer, account_event, page, pump,
)
from tests.unit.test_iter64_risk_bypass_trade_mapping import trade_event


@pytest.fixture
def service(tmp_path):
    yield from _service_fixture.__wrapped__(tmp_path)


def _seed_historical_permit(service):
    """Seed a held historical permit; this is not source or write authority."""
    caller = CallerIdentity("principal-a", "strategy-a", "strategy",
                            frozenset({"strategy-a"}), "run-1", RISK_ORDER_OPERATIONS)
    view, canonical, error = service.build_view(caller, "held", payload("held"), None, None)
    assert error is None
    effect, position_side, bucket = derive_effect(view.side, view.offset, view.instrument.venue)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    now = 1_900_000_000_000_000_000
    values = {
        "permit_id": "held", "strategy_ref": view.strategy_ref, "request_key": "held",
        "run_id": "run-1", "principal_ref": "principal-a", "origin": "strategy",
        "view_json": json.dumps(view.to_mapping(), sort_keys=True), "view_digest": view.digest(),
        "payload_json": canonical, "payload_digest": digest,
        "instrument_id": view.instrument.instrument_id,
        "instrument_json": json.dumps(view.instrument.to_dict(), sort_keys=True),
        "side": view.side, "order_offset": view.offset, "effect": effect,
        "position_side": position_side, "bucket": bucket, "quantity": str(view.quantity),
        "limit_price": str(view.limit_price), "trading_day": DAY, "policy_version": 1,
        "state": "RESERVED", "reservation_id": "held-reservation",
        "owner_generation": "owner-1", "reservation_generation": "owner-1",
        "gw_digest": digest, "gw_version": 1, "gateway_state": "RESERVED",
        "created_ns": now, "expires_ns": now + 10_000_000_000, "updated_ns": now,
    }
    with service._ledger.transaction() as txn:
        txn.insert_permit(values)


@pytest.mark.parametrize("counter,precision", [("1.23", 2), ("NaN", 28)])
def test_failed_trade_arithmetic_rolls_back_page_and_never_acknowledges(
    service, counter, precision, monkeypatch
):
    _seed_historical_permit(service)
    first = account_event(1, key="first")
    second = trade_event(2, key="second", quantity="0.01")
    observer = Observer([page([first, second])], offered=2)
    value = pump(service._engine, observer)
    with service._ledger.transaction() as txn:
        txn._c.execute("INSERT INTO risk_pretrade_counters VALUES (?,?,?,?,?)",
                       (txn._scope, "rb", DAY, "open_volume", counter))
        before_permit = txn.permit("held")
    before_calls = list(service.gateway.calls)
    completed = []
    original = service._engine._apply_event

    def record_completed(txn, event, day, now, post):
        original(txn, event, day, now, post)
        completed.append(event.sequence)

    monkeypatch.setattr(service._engine, "_apply_event", record_completed)

    with localcontext() as context:
        context.prec = precision
        with pytest.raises(PretradeDecimalEncodingError):
            value.poll()

    assert completed == [0]  # The first row was applied before the second raised.
    assert len(observer.reads) == 1 and observer.acks == []
    assert value._after == 0 and value.acknowledged == 0
    assert value.last_code == "observation_apply_failed" and value.verified_epoch is None
    with service._ledger.transaction() as txn:
        cursor = txn.cursor()
        assert cursor["epoch"] == EPOCH and cursor["last_sequence"] == -1
        assert cursor["continuity"] == "UNVERIFIED"
        assert txn._c.execute("SELECT count(*) FROM risk_pretrade_orders").fetchone()[0] == 0
        assert txn._c.execute("SELECT count(*) FROM risk_pretrade_trades").fetchone()[0] == 0
        assert txn._c.execute("SELECT value FROM risk_pretrade_counters WHERE scope_key=?",
                              (txn._scope,)).fetchone()[0] == counter
        permit = txn.permit("held")
        assert permit.state == before_permit.state == "RESERVED"
        assert permit.occupancy == before_permit.occupancy == Decimal(1)
    assert service.gateway.calls == before_calls == []


def test_exact_trade_counter_commits_and_acknowledges_same_fake_path(service):
    _seed_historical_permit(service)
    observer = Observer([page([account_event(1, key="first"),
                               trade_event(2, key="second", quantity="0.01")])], offered=2)
    value = pump(service._engine, observer)
    with service._ledger.transaction() as txn:
        txn._c.execute("INSERT INTO risk_pretrade_counters VALUES (?,?,?,?,?)",
                       (txn._scope, "rb", DAY, "open_volume", "1.23"))
    assert value.poll() == 2
    assert observer.acks == [2] and value.verified_epoch == EPOCH
    with service._ledger.transaction() as txn:
        assert txn.cursor()["last_sequence"] == 1
        assert txn.counter("rb", DAY, "open_volume") == Decimal("1.24")
        assert txn._c.execute("SELECT count(*) FROM risk_pretrade_orders").fetchone()[0] == 2
        assert txn._c.execute("SELECT count(*) FROM risk_pretrade_trades").fetchone()[0] == 1
        assert txn.permit("held").occupancy == Decimal(1)
    assert service.gateway.calls == []
