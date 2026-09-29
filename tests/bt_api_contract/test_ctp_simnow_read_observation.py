"""Offline contracts for the public CTP read-observation bridge."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bt_api_ctp.ctp.client import TraderClient, _TraderSpi

from bt_api_py import CtpSimNowExecutionAdapter, CtpSimNowExecutionError

BROKER_ID = "9999"
INVESTOR_ID = "sim-account"
INSTRUMENT_ID = "rb2701"
EXCHANGE_ID = "SHFE"
TRADING_DAY = "20260923"
CONNECTION_GENERATION = 8
HEDGE_FLAG = "1"


class _NativeQueryApi:
    def __init__(self, client: TraderClient, *, late_account_callback: bool = False):
        self.client = client
        self.late_account_callback = late_account_callback
        self.request_types = {
            "ReqQryTradingAccount": "account",
            "ReqQryInvestorPosition": "positions",
            "ReqQryOrder": "orders",
            "ReqQryTrade": "trades",
            "ReqQryInstrument": "instruments",
            "ReqQryInstrumentMarginRate": "margin_rate",
            "ReqQryInstrumentCommissionRate": "commission_rate",
        }

    def __getattr__(self, method_name):
        request_type = self.request_types.get(method_name)
        if request_type is None:
            raise AttributeError(method_name)

        def submit(_field, request_id):
            rows = self._rows(request_type)
            for index, row in enumerate(rows):
                self.client._handle_query_callback(
                    request_type,
                    row,
                    None,
                    request_id,
                    index == len(rows) - 1,
                )
            if not rows:
                self.client._handle_query_callback(request_type, None, None, request_id, True)
            if request_type == "account" and self.late_account_callback:
                self.client._handle_query_callback(request_type, None, None, request_id, False)
            return 0

        return submit

    def _rows(self, request_type: str):
        account_scope = {"BrokerID": BROKER_ID, "InvestorID": INVESTOR_ID}
        if request_type == "account":
            return ({"BrokerID": BROKER_ID, "AccountID": INVESTOR_ID},)
        if request_type == "positions":
            return ()
        if request_type == "orders":
            return (
                {
                    **account_scope,
                    "TradingDay": TRADING_DAY,
                    "InstrumentID": INSTRUMENT_ID,
                    "ExchangeID": EXCHANGE_ID,
                    "OrderRef": "000000000041",
                    "OrderStatus": "a",
                    "VolumeTotalOriginal": 2,
                    "VolumeTraded": 0,
                },
            )
        if request_type == "trades":
            return ()
        if request_type == "instruments":
            return ({"InstrumentID": INSTRUMENT_ID, "ExchangeID": EXCHANGE_ID},)
        if request_type == "margin_rate":
            return (
                {
                    **account_scope,
                    "TradingDay": TRADING_DAY,
                    "InstrumentID": INSTRUMENT_ID,
                    "ExchangeID": EXCHANGE_ID,
                    "HedgeFlag": HEDGE_FLAG,
                    "LongMarginRatioByMoney": 0.12,
                },
            )
        if request_type == "commission_rate":
            return (
                {
                    **account_scope,
                    "TradingDay": TRADING_DAY,
                    "InstrumentID": INSTRUMENT_ID,
                    "ExchangeID": EXCHANGE_ID,
                    "OpenRatioByMoney": 0.0001,
                },
            )
        raise AssertionError(request_type)


class _PublicBtApi:
    def __init__(self, client: TraderClient):
        self.profile = "set1_group1"
        self.client = client
        self.exchange_feeds = {
            "CTP___FUTURE": SimpleNamespace(trader_client=client),
        }
        self.calls = []

    def get_environment_info(self, _exchange_name):
        return {"verified": True, "environment": "demo", "transport_mode": "direct"}

    def get_ctp_session_state(self, _exchange_name):
        return {
            **self.client.get_session_state(),
            "environment_profile": self.profile,
        }

    def get_execution_identity(self, _exchange_name):
        return {
            "mode": "direct",
            "account_fingerprint": f"acct_{self.client._account_fingerprint}",
            "account_id": "offline-ctp-account",
        }

    def query_ctp_result(self, _exchange_name, query_type, **kwargs):
        self.calls.append((query_type, kwargs))
        routes = {
            "account": self.client.query_account_result,
            "positions": self.client.query_positions_result,
            "orders": self.client.query_orders_result,
            "trades": self.client.query_trades_result,
            "instruments": self.client.query_instruments_result,
            "margin_rate": self.client.query_instrument_margin_rate_result,
            "commission_rate": self.client.query_instrument_commission_rate_result,
        }
        return routes[query_type](**kwargs)


def _adapter(*, late_account_callback=False):
    client = TraderClient(
        "tcp://offline.invalid:0",
        BROKER_ID,
        INVESTOR_ID,
        "",
        auto_settlement_confirm=False,
    )
    client._connected = True
    client._authentication_state = "authenticated"
    client._login_state = "logging_in"
    client._connection_generation = CONNECTION_GENERATION
    client._login_request_id = 40
    client._login_connection_generation = CONNECTION_GENERATION
    client._req_id = 40
    client._query_interval = 0.0
    _TraderSpi(client).OnRspUserLogin(
        SimpleNamespace(
            BrokerID=BROKER_ID,
            UserID=INVESTOR_ID,
            TradingDay=TRADING_DAY,
            FrontID=4,
            SessionID=5,
            MaxOrderRef="8",
        ),
        SimpleNamespace(ErrorID=0, ErrorMsg=""),
        40,
        True,
    )
    client._api = _NativeQueryApi(client, late_account_callback=late_account_callback)
    api = _PublicBtApi(client)
    return CtpSimNowExecutionAdapter(api, selected_profile="set1_group1"), api


def test_public_read_observation_binds_native_certificate_without_authority():
    adapter, api = _adapter()

    observation = adapter.query_native_read_observation(
        instrument_id=INSTRUMENT_ID,
        exchange_id=EXCHANGE_ID,
        hedge_flag=HEDGE_FLAG,
    )

    public = observation.as_public_dict()
    certificate = public["native_query_certificate"]
    assert [query_type for query_type, _kwargs in api.calls] == [
        "account",
        "positions",
        "orders",
        "trades",
        "instruments",
        "margin_rate",
        "commission_rate",
    ]
    assert api.calls[5][1]["hedge_flag"] == HEDGE_FLAG
    assert certificate["complete"] is True
    assert len(certificate["queries"]) == 7
    request_ids = [query["request_id"] for query in certificate["queries"]]
    assert all(type(request_id) is int and request_id > 0 for request_id in request_ids)
    assert len(set(request_ids)) == len(request_ids)
    assert certificate["scope"]["connection_generation"] == CONNECTION_GENERATION
    assert certificate["scope"]["trading_day"] == TRADING_DAY
    assert certificate["scope"]["account_fingerprint_sha256"]
    for raw_identifier in (BROKER_ID, INVESTOR_ID, INSTRUMENT_ID, EXCHANGE_ID):
        assert raw_identifier not in str(public)
    margin_query = next(
        query for query in certificate["queries"] if query["request_type"] == "margin_rate"
    )
    assert margin_query["request_filter_names"] == [
        "BrokerID",
        "ExchangeID",
        "HedgeFlag",
        "InstrumentID",
        "InvestorID",
    ]
    assert margin_query["explicit_request_filter_names"] == ["HedgeFlag"]
    assert margin_query["source_provenance_validated"] is True
    assert margin_query["complete"] is True
    assert margin_query["is_last_seen"] is True
    assert margin_query["timed_out"] is False
    assert margin_query["unsupported"] is False
    assert margin_query["error_code"] in (None, 0)
    assert margin_query["error_message_present"] is False
    assert margin_query["submit_code"] in (None, 0)
    assert margin_query["late_callback_count"] == 0
    assert observation.authority_status == "NON_AUTHORIZING"
    assert observation.execution_authorized is False
    assert observation.account_open_orders_complete is False
    assert observation.account_open_orders_status == "UNPROVEN"
    assert public["account_open_orders"] == {"complete": False, "status": "UNPROVEN"}
    assert adapter.write_admitted is False


def test_late_native_callback_prevents_public_observation():
    adapter, api = _adapter(late_account_callback=True)

    with pytest.raises(
        CtpSimNowExecutionError,
        match="ctp_native_query_certificate_query_late_callback",
    ):
        adapter.query_native_read_observation(
            instrument_id=INSTRUMENT_ID,
            exchange_id=EXCHANGE_ID,
            hedge_flag=HEDGE_FLAG,
        )

    assert [query_type for query_type, _kwargs in api.calls] == ["account"]


def test_public_read_observation_requires_the_native_client_used_by_query_route():
    adapter, api = _adapter()
    api.exchange_feeds = {}

    with pytest.raises(
        CtpSimNowExecutionError,
        match="ctp_native_query_client_unavailable",
    ):
        adapter.query_native_read_observation(
            instrument_id=INSTRUMENT_ID,
            exchange_id=EXCHANGE_ID,
            hedge_flag=HEDGE_FLAG,
        )

    assert api.calls == []
