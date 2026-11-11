"""Sealed CTP fake-native queries through the real projection and durable ledger."""

from __future__ import annotations

import json
import os
import signal
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.account_protocol import AccountKey
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, NativeOrderIdentity
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_ctp.ctp.client import TraderClient, _TraderSpi
from bt_api_ctp.gateway_return_query import CtpGatewayReturnQueryReader
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

import bt_api_py.ctp_gateway_return_ingestion as ingestion
from bt_api_py.ctp_gateway_return_ingestion import (
    CtpGatewayReturnIngestionError,
    CtpGatewayReturnIngestor,
)
from bt_api_py.direct_write import project_direct_strong_identity_keys

ACCOUNT_KEY = AccountKey("ctp", "simnow", "simnow", "account", "9999")
ACCOUNT = ACCOUNT_KEY.account_key_id
DAY = "20261106"
INSTRUMENT = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701")


class FakeNative:
    def __init__(self):
        self.client = None
        self.rows = {"orders": [], "trades": []}
        self.calls = []

    def ReqAuthenticate(self, field, request_id):
        return 0

    def _answer(self, kind, request_id):
        self.calls.append(kind)
        for row in self.rows[kind]:
            self.client._handle_query_callback(kind, row, None, request_id, False)
        self.client._handle_query_callback(kind, None, None, request_id, True)
        return 0

    def ReqQryOrder(self, field, request_id):
        return self._answer("orders", request_id)

    def ReqQryTrade(self, field, request_id):
        return self._answer("trades", request_id)


def _reader(*, broker="9999", investor="account", day=DAY):
    client = TraderClient(
        "tcp://offline-neutral-td.invalid:30001",
        broker,
        investor,
        "synthetic",
        auto_settlement_confirm=False,
    )
    native = FakeNative()
    native.client = client
    client._api = native
    client._on_front_connected()
    with client._query_state_lock:
        client._authentication_state = "authenticated"
        client._login_state = "logging_in"
        request_id = client._next_request_id()
        client._login_request_id = request_id
        client._login_connection_generation = client._connection_generation
        spi = _TraderSpi(client, native)
        client._spi = spi
    client._session_native_api = native
    client._session_native_front = client._bound_front
    client._query_interval = 0
    spi.OnRspUserLogin(
        SimpleNamespace(
            BrokerID=broker,
            UserID=investor,
            TradingDay=day,
            FrontID=7,
            SessionID=19,
            MaxOrderRef="0",
        ),
        SimpleNamespace(ErrorID=0, ErrorMsg=""),
        request_id,
        True,
    )
    assert client.get_session_state()["read_only_ready"] is True
    reader = CtpGatewayReturnQueryReader(
        client, environment="simnow", broker_id=broker, investor_id=investor
    )
    return reader, client, native


def _order(**changes):
    return dict(
        {
            "BrokerID": "9999",
            "InvestorID": "account",
            "TradingDay": DAY,
            "ExchangeID": "SHFE",
            "InstrumentID": "rb2701",
            "OrderRef": "000000000001",
            "FrontID": 7,
            "SessionID": 19,
            "OrderSysID": "sys-1",
            "Direction": "0",
            "CombOffsetFlag": "0",
            "OrderStatus": "1",
            "OrderSubmitStatus": "3",
            "OrderPriceType": "2",
            "LimitPrice": 100.0,
            "VolumeTotalOriginal": 2,
            "VolumeTraded": 1,
            "VolumeTotal": 1,
        },
        **changes,
    )


def _trade(**changes):
    return dict(
        {
            "BrokerID": "9999",
            "InvestorID": "account",
            "TradingDay": DAY,
            "ExchangeID": "SHFE",
            "InstrumentID": "rb2701",
            "OrderRef": "000000000001",
            "OrderSysID": "sys-1",
            "TradeID": "trade-1",
            "Direction": "0",
            "OffsetFlag": "0",
            "Volume": 1,
            "Price": 100.0,
            "TradeTime": "09:30:00",
        },
        **changes,
    )


@pytest.fixture
def owner_guard(tmp_path):
    directory = tmp_path / "guard"
    directory.mkdir()
    guard = LocalAccountSessionGuard(directory, ACCOUNT, "owner")
    guard.acquire()
    try:
        yield guard
    finally:
        guard.release()


@pytest.fixture
def system(tmp_path, owner_guard):
    reader, client, native = _reader()
    guard = owner_guard
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", ACCOUNT, generation=guard.acquire()
    )
    ledger = GatewayDirectExecutionLedger(journal, guard)
    ingestor = CtpGatewayReturnIngestor(
        reader,
        ledger,
        account_key=ACCOUNT_KEY,
        instruments=(INSTRUMENT,),
    )
    return ingestor, reader, client, native, journal, ledger


def _command(journal, *, day=DAY, key="key-a", ref="000000000001"):
    row = journal.reserve(
        "strategy-a",
        key,
        "principal-a",
        {"symbol": "rb2701", "side": "buy", "offset": "open", "quantity": "2"},
    )
    identity = NativeOrderIdentity(
        "CTP___FUTURE",
        ref,
        {
            "trading_day": day,
            "order_ref": ref,
            "front_id": 7,
            "session_id": 19,
        },
        None,
    )
    keys = project_direct_strong_identity_keys(identity, day_scoped_ids=True)
    wire = {
        "exchange_name": "CTP___FUTURE",
        "client_order_id": identity.client_order_id,
        "fields_json": json.dumps(dict(identity.fields), sort_keys=True, separators=(",", ":")),
        "session_generation": None,
    }
    journal.begin_dispatch(
        "strategy-a",
        key,
        row.reservation_id,
        row.digest,
        "principal-a",
        row.version,
        wire,
        keys,
    )
    return row


def _fact_count(journal):
    with journal._transaction() as db:
        return db.execute("SELECT COUNT(*) FROM direct_execution_facts").fetchone()[0]


def test_empty_sealed_query_reports_only_a_local_empty_observation(system):
    ingestor, _, _, native, journal, _ = system
    summary = ingestor.read_and_ingest("orders", timeout=1)
    assert summary.kind == "orders" and summary.trading_day == DAY
    assert (summary.input_count, summary.ingested_count, summary.attributed_count) == (0, 0, 0)
    assert _fact_count(journal) == 0 and native.calls == ["orders"]


def test_trade_before_order_sealed_query_replays_and_reconciles_once(system, tmp_path, owner_guard):
    ingestor, _reader_obj, _client, native, journal, ledger = system
    _command(journal)
    native.rows["trades"] = [_trade()]
    first = ingestor.read_and_ingest("trades", timeout=1)
    assert (first.input_count, first.ingested_count, first.unattributed_count) == (1, 1, 1)
    assert len(ledger.pending(limit=10)) == 1
    native.rows["orders"] = [_order()]
    order = ingestor.read_and_ingest("orders", timeout=1)
    assert (order.attributed_count, order.unattributed_count) == (1, 0)
    assert len(ledger.reconcile_pending(limit=10)) == 1
    assert ledger.position("strategy-a", INSTRUMENT) == "1"
    assert ingestor.read_and_ingest("trades", timeout=1).ingested_count == 1
    assert ledger.position("strategy-a", INSTRUMENT) == "1"
    assert len(ledger.read_events("strategy-a", after_sequence=0, limit=10)) == 2
    with journal._transaction() as db:
        revision = journal._assert_anchor(db)
    reopened = GatewayDirectCommandJournal(
        tmp_path / "journal.sqlite",
        ACCOUNT,
        generation=journal.generation,
        expected_journal_id=journal.journal_id,
        minimum_revision=revision,
    )
    reloaded = GatewayDirectExecutionLedger(reopened, owner_guard)
    assert reloaded.position("strategy-a", INSTRUMENT) == "1"
    events = reloaded.read_events("strategy-a", after_sequence=0, limit=10)
    assert len(events) == 2
    trade = next(
        event.fact for event in events if event.fact.kind is DirectExecutionEventKind.TRADE
    )
    assert reloaded.ingest(trade).applied is False
    assert reloaded.position("strategy-a", INSTRUMENT) == "1"
    assert native.calls == ["trades", "orders", "trades"]


def test_weak_trade_order_ref_is_not_promoted_to_strong_key(system):
    ingestor, _, _, native, _, ledger = system
    native.rows["trades"] = [_trade(OrderSysID="")]
    summary = ingestor.read_and_ingest("trades", timeout=1)
    assert summary.unattributed_count == 1
    pending = ledger.pending(limit=10)
    assert len(pending) == 1
    assert pending[0].fact.identity.client_order_id == "000000000001"
    assert pending[0].fact.identity_keys == pending[0].fact.binding_keys == ()
    assert pending[0].fact.venue_key is None


def test_same_native_trade_id_on_two_days_applies_twice_under_same_guard(system):
    ingestor, _, _, native, journal, ledger = system
    _command(journal)
    native.rows["orders"] = [_order()]
    native.rows["trades"] = [_trade()]
    assert ingestor.read_and_ingest("orders", timeout=1).attributed_count == 1
    assert ingestor.read_and_ingest("trades", timeout=1).attributed_count == 1
    assert ledger.position("strategy-a", INSTRUMENT) == "1"

    next_day, next_ref = "20261107", "000000000002"
    reader_b, _, native_b = _reader(day=next_day)
    _command(journal, day=next_day, key="key-b", ref=next_ref)
    second = CtpGatewayReturnIngestor(
        reader_b, ledger, account_key=ACCOUNT_KEY, instruments=(INSTRUMENT,)
    )
    native_b.rows["orders"] = [_order(TradingDay=next_day, OrderRef=next_ref)]
    native_b.rows["trades"] = [_trade(TradingDay=next_day, OrderRef=next_ref)]
    assert second.read_and_ingest("orders", timeout=1).attributed_count == 1
    assert second.read_and_ingest("trades", timeout=1).attributed_count == 1
    assert ledger.position("strategy-a", INSTRUMENT) == "2"
    assert second.read_and_ingest("trades", timeout=1).ingested_count == 1
    assert ledger.position("strategy-a", INSTRUMENT) == "2"
    events = ledger.read_events("strategy-a", after_sequence=0, limit=10)
    trade_keys = [
        event.fact.trade_key
        for event in events
        if event.fact.kind is DirectExecutionEventKind.TRADE
    ]
    assert trade_keys == [
        (DAY, "SHFE", "rb2701", "trade-1"),
        (next_day, "SHFE", "rb2701", "trade-1"),
    ]


@pytest.mark.parametrize("invalid", ["malformed_symbol", "bad_row"])
def test_all_rows_project_before_any_ledger_mutation(system, invalid):
    # An unconfigured but well-formed contract no longer fails the batch (see
    # test_unconfigured_contract_rows_are_ingested_next_to_configured_ones).
    ingestor, _, _, native, journal, _ = system
    second = (
        _trade(TradeID="trade-2", InstrumentID="")
        if invalid == "malformed_symbol"
        else _trade(TradeID="trade-2", Volume=-1)
    )
    native.rows["trades"] = [_trade(), second]
    before = _fact_count(journal)
    with pytest.raises(CtpGatewayReturnIngestionError, match="PROJECTION_FAILED"):
        ingestor.read_and_ingest("trades", timeout=1)
    assert _fact_count(journal) == before


def test_partial_ingest_failure_reports_prefix_then_replay_is_idempotent(system, monkeypatch):
    ingestor, _, _, native, journal, ledger = system
    native.rows["trades"] = [_trade(), _trade(TradeID="trade-2")]
    original = GatewayDirectExecutionLedger.ingest
    calls = []

    def fail_second(self, fact):
        calls.append(fact)
        if len(calls) == 2:
            raise OSError("synthetic failure")
        return original(self, fact)

    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest", fail_second)
    with pytest.raises(CtpGatewayReturnIngestionError, match="INGEST_FAILED") as error:
        ingestor.read_and_ingest("trades", timeout=1)
    assert error.value.completed_count == 1 and error.value.in_flight_may_have_committed
    assert _fact_count(journal) == 1
    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest", original)
    result = ingestor.read_and_ingest("trades", timeout=1)
    assert result.ingested_count == 2 and _fact_count(journal) == 2
    assert len(ledger.pending(limit=10)) == 2


def test_postcommit_exception_retains_prefix_and_replay_is_idempotent(system, monkeypatch):
    ingestor, _, _, native, journal, ledger = system
    native.rows["trades"] = [_trade(), _trade(TradeID="trade-2")]
    original = GatewayDirectExecutionLedger.ingest
    calls = []

    def lose_reply(self, fact):
        result = original(self, fact)
        calls.append(fact)
        if len(calls) == 2:
            raise OSError("synthetic post-commit loss")
        return result

    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest", lose_reply)
    with pytest.raises(CtpGatewayReturnIngestionError, match="INGEST_FAILED") as error:
        ingestor.read_and_ingest("trades", timeout=1)
    assert error.value.completed_count == 1 and error.value.in_flight_may_have_committed
    assert _fact_count(journal) == 2
    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest", original)
    assert ingestor.read_and_ingest("trades", timeout=1).ingested_count == 2
    assert _fact_count(journal) == 2 and len(ledger.pending(limit=10)) == 2


def test_nonunique_venue_projection_rejected_before_write(system, monkeypatch):
    ingestor, _, _, native, journal, _ = system
    native.rows["orders"] = [_order()]
    original = ingestion.project_gateway_return

    def ambiguous(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, venue_identity=result.fact.identity)

    monkeypatch.setattr(ingestion, "project_gateway_return", ambiguous)
    with pytest.raises(CtpGatewayReturnIngestionError, match="PROJECTION_FAILED"):
        ingestor.read_and_ingest("orders", timeout=1)
    assert _fact_count(journal) == 0


def test_source_changed_after_projection_rejects_before_any_write(system, monkeypatch):
    ingestor, _, client, native, journal, _ = system
    native.rows["trades"] = [_trade()]
    original = ingestion.project_gateway_return

    def change_source(*args, **kwargs):
        result = original(*args, **kwargs)
        client._connection_generation += 1
        return result

    monkeypatch.setattr(ingestion, "project_gateway_return", change_source)
    with pytest.raises(CtpGatewayReturnIngestionError, match="SOURCE_STALE"):
        ingestor.read_and_ingest("trades", timeout=1)
    assert _fact_count(journal) == 0


@pytest.mark.parametrize("row_count", [1, 2])
def test_source_changed_after_first_commit_reports_confirmed_prefix(system, monkeypatch, row_count):
    ingestor, _, client, native, journal, _ = system
    native.rows["trades"] = [_trade(), _trade(TradeID="trade-2")][:row_count]
    original = GatewayDirectExecutionLedger.ingest

    def change_source(self, fact):
        result = original(self, fact)
        client._connection_generation += 1
        return result

    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest", change_source)
    with pytest.raises(CtpGatewayReturnIngestionError, match="SOURCE_STALE") as error:
        ingestor.read_and_ingest("trades", timeout=1)
    assert error.value.completed_count == 1
    assert _fact_count(journal) == 1


def test_configuration_rejects_wrong_account_environment_and_duplicate(system):
    _, reader, _, native, _, ledger = system
    options = {
        "account_key": ACCOUNT_KEY,
        "instruments": (INSTRUMENT,),
    }
    for change in (
        {"account_key": replace(ACCOUNT_KEY, account_id="other")},
        {"account_key": replace(ACCOUNT_KEY, subaccount_id="other")},
        {"account_key": replace(ACCOUNT_KEY, subaccount_id=None)},
        {"account_key": replace(ACCOUNT_KEY, provider="other")},
        {"account_key": replace(ACCOUNT_KEY, venue="other")},
        {"account_key": replace(ACCOUNT_KEY, environment="other")},
        {"instruments": (replace(INSTRUMENT, environment="other"),)},
        {"instruments": (replace(INSTRUMENT, venue="other"),)},
        {"instruments": (INSTRUMENT, INSTRUMENT)},
        {"instruments": ()},
    ):
        with pytest.raises(CtpGatewayReturnIngestionError, match="INVALID_CONFIGURATION"):
            CtpGatewayReturnIngestor(reader, ledger, **(options | change))
    assert native.calls == []


def test_sealed_readers_cannot_cross_canonical_accounts_even_with_empty_batch(system, tmp_path):
    _, reader_a, _, native_a, _, ledger_a = system
    reader_b, _, native_b = _reader(investor="account-b")
    account_b = replace(ACCOUNT_KEY, account_id="account-b")
    directory = tmp_path / "guard-b"
    directory.mkdir()
    guard_b = LocalAccountSessionGuard(directory, account_b.account_key_id, "owner-b")
    generation = guard_b.acquire()
    try:
        journal_b = GatewayDirectCommandJournal.create(
            tmp_path / "journal-b.sqlite", account_b.account_key_id, generation=generation
        )
        ledger_b = GatewayDirectExecutionLedger(journal_b, guard_b)
        for reader, ledger, account in (
            (reader_b, ledger_a, ACCOUNT_KEY),
            (reader_b, ledger_a, account_b),
            (reader_a, ledger_b, ACCOUNT_KEY),
            (reader_a, ledger_b, account_b),
        ):
            with pytest.raises(CtpGatewayReturnIngestionError, match="INVALID_CONFIGURATION"):
                CtpGatewayReturnIngestor(
                    reader, ledger, account_key=account, instruments=(INSTRUMENT,)
                )
        assert native_a.calls == native_b.calls == []
        assert _fact_count(journal_b) == 0
    finally:
        guard_b.release()


def test_owner_thread_gate_precedes_query(system):
    ingestor, _, _, native, _, _ = system
    errors = []

    def worker():
        try:
            ingestor.read_and_ingest("orders", timeout=1)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(2)
    assert not thread.is_alive() and len(errors) == 1 and errors[0].code == "OWNER_REQUIRED"
    assert native.calls == []


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork unavailable")
def test_owner_pid_gate_precedes_inherited_reader_access(system):
    ingestor, _, _, native, _, _ = system
    pid = os.fork()
    if pid == 0:
        signal.alarm(2)
        try:
            ingestor.read_and_ingest("orders", timeout=1)
        except CtpGatewayReturnIngestionError as error:
            os._exit(0 if error.code == "OWNER_REQUIRED" else 1)
        except BaseException:
            os._exit(1)
        os._exit(1)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert native.calls == []


# SimNow 2026-11-11 13:51: the Gateway restarted with fewer configured contracts
# than the account had traded that day. One order on such a contract failed the
# whole query (PROJECTION_FAILED <- KeyError) and the stream never became
# continuous, so no order was admitted (D63-09, D63-59 say otherwise).
OTHER = MarketInstrumentKey("ctp", "CZCE", "simnow", "future", "SA701", "SA701")


def _facts_by_symbol(journal):
    with journal._transaction() as db:
        rows = db.execute("SELECT disposition, fact_json FROM direct_execution_facts").fetchall()
    return {json.loads(raw)["instrument"]["native_symbol"]: (disposition, json.loads(raw)["instrument"])
            for disposition, raw in rows}


def test_unconfigured_contract_rows_are_ingested_next_to_configured_ones(system):
    ingestor, _, _, native, journal, _ = system
    _command(journal)
    native.rows["orders"] = [
        _order(ExchangeID="CFFEX", InstrumentID="IF2612", OrderRef="000000000009", OrderSysID="s9"),
        _order(),
        _order(ExchangeID="GFEX", InstrumentID="si2701", OrderRef="000000000010", OrderSysID="s10"),
    ]
    summary = ingestor.read_and_ingest("orders", timeout=1)
    assert (summary.input_count, summary.ingested_count) == (3, 3)
    assert (summary.attributed_count, summary.unattributed_count) == (1, 2)
    facts = _facts_by_symbol(journal)
    # The configured contract is handled exactly as before.
    assert facts["rb2701"][1] == INSTRUMENT.to_dict()
    assert facts["rb2701"][0] != "UNATTRIBUTED"
    # The others carry a key built from their own exchange and id, and stay
    # unattributed: the contract-block watcher freezes only those contracts.
    for symbol, venue in (("IF2612", "CFFEX"), ("si2701", "GFEX")):
        assert facts[symbol] == (
            "UNATTRIBUTED",
            MarketInstrumentKey("ctp", venue, "simnow", "future", symbol, symbol).to_dict(),
        )


def test_own_order_on_contract_removed_from_configuration_keeps_its_key(system):
    _, reader, _, native, journal, ledger = system
    _command(journal)  # placed while rb2701 was configured
    native.rows["orders"] = [_order()]
    native.rows["trades"] = [_trade()]
    narrowed = CtpGatewayReturnIngestor(
        reader, ledger, account_key=ACCOUNT_KEY, instruments=(OTHER,)
    )
    assert narrowed.read_and_ingest("orders", timeout=1).attributed_count == 1
    assert narrowed.read_and_ingest("trades", timeout=1).attributed_count == 1
    # The same key the earlier configuration used: one position, not two.
    assert ledger.position("strategy-a", INSTRUMENT) == "1"


def test_row_without_any_instrument_key_is_skipped_with_counted_warning(system, caplog):
    import logging

    ingestor, _, _, native, journal, _ = system
    foreign = _trade(ExchangeID="NYMEX", InstrumentID="CL2701", TradeID="trade-x")
    native.rows["trades"] = [foreign, _trade()]
    with caplog.at_level(logging.WARNING, logger="bt_api_py.ctp_gateway_return_ingestion"):
        for _ in range(4):
            summary = ingestor.read_and_ingest("trades", timeout=1)
    assert (summary.input_count, summary.ingested_count, summary.unattributed_count) == (1, 1, 1)
    assert set(_facts_by_symbol(journal)) == {"rb2701"}
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3  # the 1st, 2nd and 4th skip of that contract
    assert all("NYMEX CL2701" in text for text in warnings)
    assert [text.split("(")[1].split(")")[0] for text in warnings] == [
        "1 so far", "2 so far", "4 so far"]
