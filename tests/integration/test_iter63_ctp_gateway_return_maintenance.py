"""Real CTP session and SQLite, with only the native boundary substituted."""

import importlib
import threading
from types import SimpleNamespace

import pytest
from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
from bt_api_ctp.gateway_session import CtpGatewaySession
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance
from bt_api_py.ctp_gateway_trading import CtpGatewayReturnSettings
from bt_api_py.gateway_direct_service import GatewayReservationAdmissionDenied
from tests.integration.test_iter63_ctp_gateway_return_ingestion import (
    _command,
    _fact_count,
    _order,
    _trade,
)


@pytest.fixture
def real_owner(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests")
    )
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    instrument = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701")
    (tmp_path / "flow").mkdir(mode=0o700)
    (tmp_path / "guard").mkdir()
    natives = []

    class Native(helper._NativeApi):
        def __init__(self):
            super().__init__()
            self.rows = {"orders": [], "trades": []}
            self.fail = False

        def answer(self, kind, request_id):
            self.calls.append(kind)
            if self.fail:
                return 1
            for row in self.rows[kind]:
                self.client._handle_query_callback(kind, row, None, request_id, False)
            self.client._handle_query_callback(kind, None, None, request_id, True)
            return 0

        def ReqQryOrder(self, field, request_id):
            return self.answer("orders", request_id)

        def ReqQryTrade(self, field, request_id):
            return self.answer("trades", request_id)

    def start(trader, **kwargs):
        native = Native()
        native.client = trader
        trader._api = native
        helper._login(trader, native, session_id=19)
        natives.append(native)

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(
        TraderClient,
        "stop_and_wait",
        lambda trader, **kw: CtpNativeStopReceipt(
            trader.connection_generation, False, True, True, False, False
        ),
    )

    def feed():
        return CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(tmp_path / "flow"),
            flow_account_scope=key.account_key_id,
        )

    guard = LocalAccountSessionGuard(tmp_path / "guard", key.account_key_id, "owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", key.account_key_id, generation=generation
    )
    session = CtpGatewaySession(
        key,
        feed,
        lambda *_: None,
        trusted_flow_root=tmp_path / "flow",
        flow_account_scope=key.account_key_id,
        source_ref="source",
        entitlement_ref="entitlement",
    )
    session.connect(generation=generation, timeout=2)
    settings = CtpGatewayReturnSettings((instrument,), query_interval_seconds=0.05)
    maintenance = CtpGatewayReturnMaintenance(
        session, session.direct_provider, journal, guard, key, settings
    )
    yield maintenance, session, natives[0], journal, instrument, helper
    assert session.close()
    guard.release()


def _pair(maintenance):
    maintenance.poll()
    maintenance.query()
    maintenance.query()


def _relogin_same_spi(native, day, session_id):
    trader = native.client
    spi = trader._spi
    trader._on_front_connected()
    with trader._query_state_lock:
        trader._authentication_state = "authenticated"
        trader._login_state = "logging_in"
        request_id = trader._next_request_id()
        trader._login_request_id = request_id
        trader._login_connection_generation = trader._connection_generation
    spi.OnRspUserLogin(
        SimpleNamespace(
            BrokerID="9999",
            UserID="account",
            TradingDay=day,
            FrontID=7,
            SessionID=session_id,
            MaxOrderRef="0",
        ),
        SimpleNamespace(ErrorID=0, ErrorMsg=""),
        request_id,
        True,
    )


def test_full_pair_is_required_and_query_callback_replay_deduplicates(real_owner):
    m, session, native, journal, instrument, helper = real_owner
    with pytest.raises(GatewayReservationAdmissionDenied), m.reservation_fence(None, None):
        pass
    m.poll()
    m.query()
    assert not m.dispatch_ready()
    m.query()
    assert m.dispatch_ready()
    _command(journal, day=helper.TODAY)
    trade = _trade(TradingDay=helper.TODAY)
    order = _order(TradingDay=helper.TODAY)
    native.client._spi.OnRtnTrade(SimpleNamespace(**trade))
    assert not m.dispatch_ready()
    m.poll()
    native.client._spi.OnRtnOrder(SimpleNamespace(**order))
    m.poll()
    assert session.return_buffer.status().record_count == 0
    assert m.dispatch_ready()
    native.rows = {"orders": [order], "trades": [trade]}
    m.query()
    m.query()
    assert _fact_count(journal) == 2
    assert m._ledger.position("strategy-a", instrument) == "1"
    assert len(native.calls) == 4


def test_failed_write_keeps_entire_offer_and_unsupported_and_gap_block(real_owner, monkeypatch):
    m, session, native, journal, _instrument, helper = real_owner
    _pair(m)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    native.client._spi.OnRtnTrade(SimpleNamespace(**_trade(TradingDay=helper.TODAY)))
    original = GatewayDirectExecutionLedger.ingest
    calls = []

    def fail_second(self, fact):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError
        return original(self, fact)

    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest", fail_second)
    m.poll()
    assert session.return_buffer.status().record_count == 2
    assert _fact_count(journal) == 1
    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest", original)
    m.poll()
    assert session.return_buffer.status().record_count == 0
    assert _fact_count(journal) == 2
    native.client._spi.OnRspOrderInsert(
        SimpleNamespace(**_order(TradingDay=helper.TODAY)),
        SimpleNamespace(ErrorID=1, ErrorMsg=""),
        7,
        True,
    )
    unbound_ticket = session.return_buffer.offer()
    m.poll()
    assert m.status()["status"] == "CALLBACK_FAILED"
    assert session.return_buffer.status().record_count == 1
    assert session.return_buffer.offer() is unbound_ticket and _fact_count(journal) == 2
    assert not m.dispatch_ready()
    session.return_buffer._mark_gap("capacity_overflow")
    m.query()
    assert session.return_buffer.status().gap
    assert not m.dispatch_ready()


def test_query_failure_resets_pair_and_reader_rebuilt_on_relogin(real_owner):
    m, session, native, _journal, _instrument, helper = real_owner
    _pair(m)
    old_reader = m._ingestor._reader
    native.fail = True
    m.query()
    assert not m.dispatch_ready() and not m._done
    native.fail = False
    m.query()
    assert not m.dispatch_ready()
    m.query()
    assert m.dispatch_ready()
    _relogin_same_spi(native, helper.TODAY, 20)
    session.refresh_session()
    m.poll()
    assert m._ingestor._reader is not old_reader
    assert not m.dispatch_ready()
    m.query()
    m.query()
    assert m.dispatch_ready()


def test_old_session_offer_is_retained_after_relogin(real_owner):
    m, session, native, journal, _instrument, helper = real_owner
    _pair(m)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    _relogin_same_spi(native, helper.TODAY, 20)
    session.refresh_session()
    m.poll()
    assert m.status()["status"] == "SOURCE_STALE"
    assert session.return_buffer.status().record_count == 1
    assert not m.dispatch_ready()
    assert _fact_count(journal) == 0
    native.rows["orders"] = [_order(TradingDay=helper.TODAY)]
    m.query()
    assert _fact_count(journal) == 1
    assert session.return_buffer.status().record_count == 1
    assert not m.dispatch_ready()


def test_current_source_accepts_historical_trade_day_and_rejects_future_day(real_owner):
    m, session, native, journal, instrument, helper = real_owner
    _pair(m)
    _command(journal, day="20261105", key="old")
    _command(journal, day=helper.TODAY, key="new")
    for day in ("20261105", helper.TODAY):
        native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=day)))
        native.client._spi.OnRtnTrade(SimpleNamespace(**_trade(TradingDay=day)))
        m.poll()
    assert session.return_buffer.status().record_count == 0
    assert _fact_count(journal) == 4
    assert m._ledger.position("strategy-a", instrument) == "2"
    native.client._spi.OnRtnTrade(SimpleNamespace(**_trade(TradingDay="20261107")))
    m.poll()
    assert session.return_buffer.status().record_count == 1
    assert not m.dispatch_ready()
    assert _fact_count(journal) == 4


def test_replaced_spi_and_same_generation_source_change_fail_closed(real_owner):
    m, session, native, _journal, _instrument, helper = real_owner
    _pair(m)
    with native.client._query_state_lock:
        native.client._session_id = 21
    assert not m.dispatch_ready()
    with native.client._query_state_lock:
        native.client._session_id = 19
    helper._login(native.client, native, session_id=20, check=False)
    session.refresh_session()
    previous_reader = m._ingestor._reader
    m.poll()
    assert m._ingestor._reader is previous_reader
    assert not m.dispatch_ready()


def test_ledger_continuity_tolerates_waiting_callbacks_but_no_lost_return(real_owner, monkeypatch):
    """I63-E111: the strategy ledger needs a hole-free source, not an idle one."""
    m, session, native, journal, _instrument, helper = real_owner
    assert not m.ledger_continuous()
    m.poll()
    m.query()
    assert not m.ledger_continuous()  # only one of the two full-day queries
    m.query()
    assert m.ledger_continuous() and m.dispatch_ready()
    # A callback still waiting in the buffer is lag, not loss.
    _command(journal, day=helper.TODAY)
    native.client._spi.OnRtnTrade(SimpleNamespace(**_trade(TradingDay=helper.TODAY)))
    assert not m.dispatch_ready() and m.ledger_continuous()
    m.poll()
    assert m.ledger_continuous()
    # Another thread never gets a positive answer.
    answers = []
    thread = threading.Thread(target=lambda: answers.append(m.ledger_continuous()))
    thread.start()
    thread.join()
    assert answers == [False]
    # A callback batch that cannot be written is not continuity.
    original = GatewayDirectExecutionLedger.ingest
    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest",
                        lambda self, fact: (_ for _ in ()).throw(RuntimeError()))
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    m.poll()
    assert not m.ledger_continuous()
    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest", original)
    m.poll()
    assert m.ledger_continuous()
    # A failed full-day query restarts the pair.
    native.fail = True
    m.query()
    assert not m.ledger_continuous()
    native.fail = False
    m.query()
    m.query()
    assert m.ledger_continuous()
    # A new native session needs its own pair; a gap is never continuous.
    _relogin_same_spi(native, helper.TODAY, 21)
    session.refresh_session()
    assert not m.ledger_continuous()
    m.poll()
    m.query()
    m.query()
    assert m.ledger_continuous()
    session.return_buffer._mark_gap("capacity_overflow")
    assert not m.ledger_continuous()


def test_closing_owner_has_no_ledger_continuity(real_owner):
    m, _session, _native, _journal, _instrument, _helper = real_owner
    _pair(m)
    assert m.ledger_continuous()
    m.begin_close()
    assert not m.ledger_continuous()


@pytest.mark.timeout(30)
def test_close_is_reserved_under_the_real_reservation_fence_without_blocking(real_owner):
    """SimNow 2026-11-10 21:01: the first close reservation froze the Gateway.

    The ledger's continuity port asked ``ledger_continuous`` while the same
    owner thread held ``reservation_fence``; taking that plain lock again never
    returned. Opens never reached the port, so only a close showed it. With the
    old code this test does not return (the timeout fails it).
    """
    from contextlib import nullcontext

    from bt_api_ctp.close_offset_policy import split_close_required
    from bt_api_gateway.direct_command_journal import DirectCommandJournalError
    from bt_api_gateway.strategy_position_ledger import StrategyBaselineEvidence

    from bt_api_py.ctp_gateway_strategy_ledger import CtpGatewayStrategyLedger

    m, session, native, journal, instrument, helper = real_owner
    guard = m._ledger._guard
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    _pair(m)
    provider = session.direct_provider
    ledger = CtpGatewayStrategyLedger(
        journal=journal, guard=guard, session=session, provider=provider, returns=m,
        instruments=(instrument,), account_scope=journal.account_scope, route="CTP___FUTURE",
        service_generation=session._service_generation, split_close_required=split_close_required)
    ledger.install()
    day = provider.trading_day()
    journal.initialize_empty_strategy_ledger(
        StrategyBaselineEvidence(journal.account_scope, journal.journal_id, day, "CTP___FUTURE",
                                 journal.generation, 1, "synthetic", "positions", "orders", "cut",
                                 ("open_orders", "positions"), 100, 200, True, True, True, True),
        guard=guard, baseline_fence=nullcontext, verify_current_empty=lambda _: True)
    close = {"symbol": instrument.native_symbol, "side": "sell", "offset": "close_today",
             "quantity": "1", "quantity_unit": "contracts", "exchange_id": instrument.venue}
    # Exactly the journal's order: transaction, then the fence, then the ledger
    # ports, which ask the return source while the fence is still held.
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_CLOSE_EXCEEDS"):
        journal.reserve("strategy-a", "close-1", "principal", close,
                        reservation_fence=lambda: m.reservation_fence(None, None))
    with m.reservation_fence(None, None):
        assert m.ledger_continuous() is True
    assert journal.read_strategy_close_capacity("strategy-a", close, expected_instrument=instrument).available == 0
