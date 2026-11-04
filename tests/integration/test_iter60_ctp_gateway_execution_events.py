"""Iteration 60: CTP read-only provider -> Gateway per-strategy execution streams.

INTEGRATION_SIM, local only. Production ``GatewayAccountSessionWorker``,
``GatewayExecutionEventPublisher``, ``GatewayEventJournal`` (temporary SQLite)
and the SDK ``DurableOrderCorrelationIssuer`` (temporary SQLite) run with the
real ``CtpReadOnlyAccountSessionProvider``; only the CTP request feed is a
fake answering source-sealed queries. No CTP front, SimNow or account is used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.execution_correlation_protocol import OrderCorrelation
from bt_api_base.execution_row_observation_protocol import (
    EXECUTION_ORDER_EVENT_KIND,
    EXECUTION_TRADE_EVENT_KIND,
    execution_observation_from_event,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_ctp.account_provider import CtpReadOnlyAccountSessionProvider
from bt_api_ctp.ctp.client import CtpNativeStopReceipt
from bt_api_ctp.query import (
    QueryResult,
    _attach_query_source,
    _new_query_session_scope,
    _new_query_source,
    _query_records_digest,
)
from bt_api_base.execution_row_observation_protocol import strategy_client_order_id  # noqa: E402
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
from bt_api_gateway.execution_event_publisher import (
    GatewayExecutionEventPublisher,
    GatewayExecutionScopePolicy,
)
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer

_ACCOUNT = AccountKey("ctp", "simnow", "simnow", "investor-it", "broker-it")
_RESOURCE = AccountResourceKey(product_type="future", margin_pool="futures")
_IF = MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", "IF2612", "IF2612")
_ACCOUNT_SCOPE = "account:ctp-it"
_STATE_SCOPE = "strategy:account-facts"
_EXEC_A = "exec:strategy-a"
_TRADING_DAY = "20261017"
_FINGERPRINT = "fedcba9876543210"
_STRATEGY = "strategy-a"


class _Clocks:
    def __init__(self) -> None:
        self.wall = datetime(2026, 10, 17, 1, 30, tzinfo=timezone.utc).timestamp()
        self.monotonic = 500.0

    def utc(self) -> float:
        return self.wall

    def utc_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.wall, timezone.utc)

    def mono(self) -> float:
        return self.monotonic

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.monotonic += seconds


class _Trader:
    def stop_and_wait(self, *, timeout: float) -> CtpNativeStopReceipt:
        return CtpNativeStopReceipt(7, True, True, True, False, False)


def _order(sys_id: str, order_ref: str, quantity: int) -> dict[str, object]:
    return {
        "BrokerID": "broker-it",
        "InvestorID": "investor-it",
        "InstrumentID": "IF2612",
        "ExchangeID": "CFFEX",
        "TradingDay": _TRADING_DAY,
        "OrderSysID": sys_id,
        "OrderLocalID": "local-" + sys_id,
        "OrderRef": order_ref,
        "FrontID": 3,
        "SessionID": 4,
        "Direction": "0",
        "CombOffsetFlag": "0",
        "VolumeTotalOriginal": quantity,
        "VolumeTraded": quantity,
        "OrderStatus": "0",  # all traded (terminal): the account snapshot stays readable
        "OrderPriceType": "2",
        "LimitPrice": 4001.0,
        "InsertDate": _TRADING_DAY,
        "InsertTime": "09:30:00",
    }


def _trade(trade_id: str, sys_id: str, order_ref: str, quantity: int) -> dict[str, object]:
    return {
        "BrokerID": "broker-it",
        "InvestorID": "investor-it",
        "InstrumentID": "IF2612",
        "ExchangeID": "CFFEX",
        "TradingDay": _TRADING_DAY,
        "TradeID": trade_id,
        "OrderSysID": sys_id,
        "OrderRef": order_ref,
        "OrderLocalID": "local-" + sys_id,
        "Direction": "0",
        "OffsetFlag": "0",
        "Volume": quantity,
        "Price": 4000.5,
        "TradeDate": _TRADING_DAY,
        "TradeTime": "09:30:01",
    }


_ROWS = {
    "account": [
        {
            "AccountID": "investor-it",
            "BrokerID": "broker-it",
            "CurrencyID": "CNY",
            "Balance": 1000000.0,
            "Available": 900000.0,
        }
    ],
    "positions": [],
    # A managed order with an issued correlation, an external (manual) order
    # and an order whose resolver answer is not an issuer correlation.
    "orders": [
        _order("SYS-A", "000000000001", 2),
        _order("SYS-EXT", "000000000099", 1),
        _order("SYS-FORGED", "000000000002", 1),
    ],
    "trades": [
        _trade("TRD-A", "SYS-A", "000000000001", 2),
        _trade("TRD-EXT", "SYS-EXT", "000000000099", 1),
        _trade("TRD-FORGED", "SYS-FORGED", "000000000002", 1),
    ],
}


class _Feed:
    """Fake CtpRequestDataFuture; every query result is source-sealed."""

    def __init__(self, clocks: _Clocks) -> None:
        self._request_feed = None
        self._trader = None
        self._connected = False
        self._clocks = clocks
        self._request_id = 0

    def connect(self) -> None:
        self._trader = _Trader()
        self._connected = True

    def get_session_state(self):
        return {"read_only_ready": True, "auto_settlement_confirm": False}

    def get_query_session_scope(self):
        return _new_query_session_scope(
            issuer=self._trader,
            account_fingerprint=_FINGERPRINT,
            connection_generation=7,
            trading_day=_TRADING_DAY,
            broker_id="broker-it",
            investor_id="investor-it",
            read_only_ready=True,
            captured_at_utc=self._clocks.utc_datetime(),
            captured_monotonic=float(self._clocks.monotonic),
            source_tags=("source", "epoch", "api", "spi", 1, 7),
        )

    def query_account_result(self, *, timeout):
        return self._query("account")

    def query_positions_result(self, *, timeout):
        return self._query("positions")

    def query_orders_result(self, *, timeout):
        return self._query("orders")

    def query_trades_result(self, *, timeout):
        return self._query("trades")

    def _query(self, name):
        self._request_id += 1
        completed = self._clocks.utc_datetime()
        result = QueryResult(
            request_type=name,
            request_id=self._request_id,
            connection_generation=7,
            account_fingerprint=_FINGERPRINT,
            started_at_utc=completed,
            completed_at_utc=completed,
            is_last_seen=True,
            error_code=0,
            error_message="",
            timed_out=False,
            complete=True,
            records=tuple(dict(row) for row in _ROWS[name]),
        )
        source = _new_query_source(
            issuer=self._trader,
            request_type=name,
            request_id=result.request_id,
            account_fingerprint=_FINGERPRINT,
            connection_generation=7,
            trading_day=_TRADING_DAY,
            broker_id="broker-it",
            investor_id="investor-it",
            started_at_utc=completed,
            completed_at_utc=completed,
            started_monotonic=self._clocks.monotonic,
            completed_monotonic=self._clocks.monotonic,
            records_sha256=_query_records_digest(result.records),
        )
        return _attach_query_source(result, source)


def _process(tmp_path: Path, clocks: _Clocks, issued: OrderCorrelation | None):
    """One Gateway account-owner process: issuer, provider, journal, worker."""

    issuer = DurableOrderCorrelationIssuer(tmp_path / "issuer.sqlite3")
    if issued is None:
        # A strategy-attributed, venue-safe correlation (issuer v2).
        issued, handle = issuer.issue_order_correlation(
            "CTP___FUTURE", "IF2612", strategy_ref=_STRATEGY
        )
    else:  # a restart resolves the same persisted correlation
        handle = issuer.resolve_order_correlation(issued)
    forged = OrderCorrelation(
        "CTP___FUTURE",
        "IF2612",
        strategy_client_order_id(_STRATEGY, "0" * 64),
        "sdk-reservation-v1-" + "0" * 64,
    )
    references = {"000000000001": (issued, handle), "000000000002": (forged, object())}

    provider = CtpReadOnlyAccountSessionProvider(
        lambda: _Feed(clocks),
        monotonic_clock=clocks.mono,
        utc_clock=clocks.utc_datetime,
        execution_observation_issuer=issuer,
        execution_instruments=(_IF,),
        execution_order_resolver=lambda order_ref, _instrument: references.get(order_ref),
        execution_scope_ref="scope:ctp-it",
        execution_query_interval_seconds=0.0,
    )
    journal = GatewayEventJournal(tmp_path / "gateway.sqlite3", clock=clocks.utc)
    # Server configuration: strategy A's client order prefix -> its stream;
    # no account-level unattributed stream, so anything else is only counted.
    publisher = GatewayExecutionEventPublisher(
        journal,
        account_key=_ACCOUNT,
        journal_account_scope=_ACCOUNT_SCOPE,
        policy=GatewayExecutionScopePolicy.for_strategies({_STRATEGY: _EXEC_A}),
    )
    guards = tmp_path / "guards"
    guards.mkdir(exist_ok=True)
    worker = GatewayAccountSessionWorker(
        provider,
        journal,
        account_key=_ACCOUNT,
        journal_account_scope=_ACCOUNT_SCOPE,
        state_scope=_STATE_SCOPE,
        guard=LocalAccountSessionGuard(guards, _ACCOUNT.account_key_id, "worker-ctp"),
        required_coverage=(_RESOURCE,),
        clock=clocks.utc,
        monotonic_clock=clocks.mono,
        execution_publisher=publisher,
    )
    return issuer, issued, provider, journal, publisher, worker


def _events(journal: GatewayEventJournal, scope: str):
    principal = GatewayPrincipal(
        principal_id="reader",
        account_scopes=frozenset({_ACCOUNT_SCOPE}),
        strategy_scopes=frozenset({scope}),
        allowed_kinds=frozenset({GatewayCommandKind.READ}),
    )
    return journal.read_after(principal, _ACCOUNT_SCOPE, scope, cursor=None, limit=100).events


def test_ctp_fills_reach_a_strategy_stream_only_with_verified_attribution(tmp_path):
    clocks = _Clocks()
    issuer, issued, provider, journal, publisher, worker = _process(tmp_path, clocks, None)
    worker.start()
    assert worker.ready
    result = worker.publish_execution_events()
    assert worker.execution_status == "published"
    # 6 observations: the verified order and its fill are appended; the
    # external and the forged-correlation orders (and their fills) are not
    # attributed and, without an unattributed stream, only counted.
    assert (result.appended, result.duplicates, result.unattributed_dropped) == (2, 0, 4)
    stream = _events(journal, _EXEC_A)
    assert [event.kind for event in stream] == [
        EXECUTION_ORDER_EVENT_KIND,
        EXECUTION_TRADE_EVENT_KIND,
    ]
    order = execution_observation_from_event(stream[0].kind, stream[0].event_id, stream[0].payload)
    fill = execution_observation_from_event(stream[1].kind, stream[1].event_id, stream[1].payload)
    assert (order.order.intent_binding, order.order.client_order_id) == (
        "verified",
        issued.client_order_id,
    )
    assert (order.order.status, order.order.filled_quantity) == ("filled", "2")
    assert fill.trade.order_id == order.order.order_id and fill.trade.quantity == "2"
    assert (fill.fee, fill.fee_currency) == (None, None)  # CTP trades carry no fee
    assert provider.execution_observation_counters == {"ORDER_ATTRIBUTION_REFUSED": 1}
    # Nothing new on the next drain of the same generation.
    clocks.advance(0.5)
    worker.maintain()
    assert worker.execution_status == "idle"
    first_ids = [event.event_id for event in stream]
    worker.close()
    issuer.close()

    # Process restart: same issuer and Gateway files, fresh objects. The
    # provider re-derives every fact with identical ids: nothing is new.
    clocks.advance(1.0)
    issuer, _issued, provider, journal, publisher, worker = _process(tmp_path, clocks, issued)
    worker.start()
    restarted = worker.publish_execution_events()
    assert (restarted.appended, restarted.duplicates, restarted.unattributed_dropped) == (0, 2, 4)
    assert [event.event_id for event in _events(journal, _EXEC_A)] == first_ids
    worker.close()
    issuer.close()
