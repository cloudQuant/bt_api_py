"""Synthetic returns through real SDK direct coordinator and Gateway ledger.

The facts here are hand-constructed synthetic inputs, not provider provenance,
polling, transport or actual account evidence.
"""

import sqlite3
from decimal import Decimal

import pytest
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectOrderStatus,
    DirectSubmitResult,
    NativeOrderIdentity,
)
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_direct_dispatch import compose_gateway_direct_dispatcher
from bt_api_py.write_admission import WriteAdmission
from tests.integration.test_iter63_gateway_direct_dispatch import Provider

ACCOUNT, ROUTE, STRATEGY, PRINCIPAL = "synthetic-account", "SYNTHETIC", "strategy", "principal"
DAY = "20300101"
INSTRUMENT = MarketInstrumentKey("synthetic", "venue", "paper", "future", "X", "X")


class ReturnProvider(Provider):
    """Existing synthetic direct provider with an in-submit return hook."""

    def __init__(self, db):
        super().__init__(db)
        self.on_submit = None
        self.fail_not_sent = False
        self.return_errors = []

    def submit_order(self, reservation, order, grant, verifier):
        result = super().submit_order(reservation, order, grant, verifier)
        if self.on_submit is not None:
            try:
                self.on_submit(reservation.identity)
            except Exception as exc:
                self.return_errors.append((type(exc).__name__, str(exc)))
                raise
        if self.fail_not_sent:
            return DirectSubmitResult(False, reservation.identity, error_code="synthetic_not_sent")
        return result


def _request(key):
    return OrderRequest(
        symbol="X",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        account_id=ACCOUNT,
        client_order_id=key,
        price=Decimal("120"),
        idempotency_key=key,
        offset="open",
    )


@pytest.fixture
def context(tmp_path):
    root = tmp_path / "guard"
    root.mkdir()
    guard = LocalAccountSessionGuard(root, ACCOUNT, "synthetic-owner")
    generation = guard.acquire()
    db = tmp_path / "journal.sqlite"
    journal = GatewayDirectCommandJournal.create(db, ACCOUNT, generation=generation)
    provider = ReturnProvider(db)
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _: provider)
    dispatcher = compose_gateway_direct_dispatcher(
        coordinator, ROUTE, journal, guard, lambda _: True, expected_local_provider=provider
    )
    ledger = GatewayDirectExecutionLedger(journal, guard)
    try:
        yield journal, guard, ledger, provider, coordinator, dispatcher
    finally:
        guard.release()


def _reserve(journal, key):
    return journal.reserve(STRATEGY, key, PRINCIPAL, _request(key).to_dict())


def _dispatch(dispatcher, row):
    return dispatcher.dispatch(
        row.strategy_scope,
        row.request_key,
        row.principal,
        row.reservation_id,
        row.digest,
        row.version,
    )


def _keys(coordinator, original):
    initial = coordinator.strong_identity_keys(ROUTE, original)
    expanded = NativeOrderIdentity(
        ROUTE,
        original.client_order_id,
        {**dict(original.fields), "exchange_id": "SHFE", "order_sys_id": "synthetic-sys"},
        1,
    )
    keys = coordinator.strong_identity_keys(ROUTE, expanded)
    added = set(keys) - set(initial)
    assert len(added) == 1
    return initial, expanded, next(iter(added))


def _order(original, initial, expanded, venue):
    return GatewayReturnFact(
        account_scope=ACCOUNT,
        route=ROUTE,
        instrument=INSTRUMENT,
        trading_day=DAY,
        kind=DirectExecutionEventKind.ORDER,
        source_ref="synthetic-order",
        identity=expanded,
        identity_keys=tuple(sorted((*initial, venue))),
        binding_keys=initial,
        venue_key=venue,
        status=DirectOrderStatus.ACCEPTED,
        side="buy",
        offset="open",
        order_quantity="2",
        filled_quantity="0",
        remaining_quantity="2",
        session_generation=1,
    )


def _trade(expanded, venue, key="trade-one"):
    return GatewayReturnFact(
        account_scope=ACCOUNT,
        route=ROUTE,
        instrument=INSTRUMENT,
        trading_day=DAY,
        kind=DirectExecutionEventKind.TRADE,
        source_ref="synthetic-trade",
        identity=expanded,
        identity_keys=(venue,),
        venue_key=venue,
        trade_key=(DAY, key),
        side="buy",
        offset="open",
        trade_quantity="1",
        trade_price="120",
        session_generation=1,
    )


def test_early_order_and_fill_do_not_break_original_dispatch_finish(context):
    journal, guard, ledger, provider, coordinator, dispatcher = context
    row = _reserve(journal, "early")
    received = []

    def during_submit(identity):
        original = journal.get(STRATEGY, row.request_key, PRINCIPAL)
        assert original.status == "DISPATCHING" and original.native_identity_json is not None
        initial, expanded, venue = _keys(coordinator, identity)
        assert original.strong_identity_keys == initial
        order, trade = _order(identity, initial, expanded, venue), _trade(expanded, venue)
        received.extend((order, trade, ledger.ingest(order), ledger.ingest(trade)))
        unchanged = journal.get(STRATEGY, row.request_key, PRINCIPAL)
        assert unchanged.native_identity_json == original.native_identity_json
        assert unchanged.strong_identity_keys == initial and unchanged.status == "DISPATCHING"

    provider.on_submit = during_submit
    final = _dispatch(dispatcher, row)
    assert final.status == "SENT" and len(provider.sends) == 1, provider.return_errors
    assert final.strong_identity_keys == coordinator.strong_identity_keys(ROUTE, provider.sends[0])
    order, trade, order_result, trade_result = received
    assert order_result.disposition == trade_result.disposition == "ATTRIBUTED"
    assert trade_result.applied is True and ledger.position(STRATEGY, INSTRUMENT) == "1"
    assert ledger.ingest(trade).applied is False
    with sqlite3.connect(provider.db) as db:
        revision = db.execute("SELECT revision FROM direct_journal_meta").fetchone()[0]
    reopened = GatewayDirectCommandJournal(
        provider.db,
        ACCOUNT,
        generation=journal.generation,
        expected_journal_id=journal.journal_id,
        minimum_revision=revision,
    )
    again = GatewayDirectExecutionLedger(reopened, guard)
    assert again.position(STRATEGY, INSTRUMENT) == "1"
    assert len(again.read_events(STRATEGY, after_sequence=0, limit=100)) == 2
    assert again.ingest(trade).applied is False


def test_trade_before_order_learning_is_reconciled_once(context):
    journal, _guard, ledger, provider, coordinator, dispatcher = context
    row = _reserve(journal, "late-match")
    observed = []

    def during_submit(identity):
        initial, expanded, venue = _keys(coordinator, identity)
        trade = _trade(expanded, venue)
        first = ledger.ingest(trade)
        assert first.disposition == "UNATTRIBUTED" and first.applied is False
        assert len(ledger.pending(limit=256)) == 1
        assert ledger.position(STRATEGY, INSTRUMENT) is None
        observed.append((trade, ledger.ingest(_order(identity, initial, expanded, venue))))

    provider.on_submit = during_submit
    assert _dispatch(dispatcher, row).status == "SENT"
    trade, order_result = observed[0]
    assert order_result.disposition == "ATTRIBUTED"
    ledger.reconcile_pending(limit=256)
    assert ledger.position(STRATEGY, INSTRUMENT) == "1"
    assert ledger.pending(limit=256) == ()
    assert ledger.ingest(trade).applied is False
    ledger.reconcile_pending(limit=256)
    assert ledger.position(STRATEGY, INSTRUMENT) == "1"
    assert len(ledger.read_events(STRATEGY, after_sequence=0, limit=100)) == 2


def test_late_trade_after_not_sent_is_kept_without_changing_command(context):
    journal, _guard, ledger, provider, coordinator, dispatcher = context
    row = _reserve(journal, "contradiction")
    provider.fail_not_sent = True
    result = _dispatch(dispatcher, row)
    assert result.status == "NOT_SENT"
    identity = provider.sends[0]
    initial, expanded, venue = _keys(coordinator, identity)
    order = ledger.ingest(_order(identity, initial, expanded, venue))
    trade = ledger.ingest(_trade(expanded, venue))
    assert order.disposition == trade.disposition == "ATTRIBUTED"
    assert trade.applied is True and ledger.position(STRATEGY, INSTRUMENT) == "1"
    assert journal.get(STRATEGY, row.request_key, PRINCIPAL).status == "NOT_SENT"
