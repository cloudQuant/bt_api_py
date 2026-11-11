"""I63-E130: the account owner serves other work while a return query is outstanding.

The real CTP trading profile, owner thread, return maintenance and CTP session,
with only the native boundary substituted. The full-day trade query is answered
only when the test releases it, as a slow SimNow front does.
"""

import importlib
import threading
import time
from dataclasses import replace
from pathlib import Path

from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
from bt_api_ctp.gateway_session import CtpGatewaySession

import bt_api_py.ctp_gateway_return_maintenance as return_module
from bt_api_py import ctp_gateway_trading as module
from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
from tests.test_iter63_ctp_gateway_trading import CLIENT_A, _read_profile, _resources, _wait


def test_owner_serves_calls_and_defers_other_queries_while_a_return_query_waits(
        tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests"))
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    real_loader = module._load_ctp_types
    real_maintenance = return_module.CtpGatewayReturnMaintenance
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch, account_key=key)
    settings = replace(settings, write_enabled=True, maintenance_interval_seconds=0.01,
                       settlement_refresh_seconds=0.05)
    read = replace(read, required_coverage=(AccountResourceKey("future", "futures"),))
    monkeypatch.setattr(module, "_load_ctp_types", real_loader)
    maintenances = []

    class RecordingMaintenance(real_maintenance):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            maintenances.append(self)

    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", RecordingMaintenance)
    held, release = [], threading.Event()
    lock = threading.Lock()
    overlaps = []  # (what, held trade queries) whenever another query starts

    def outstanding():
        with lock:
            return len(held)

    # The owner starts every account read here (request and response since I63-E130).
    original_read = CtpGatewaySession.begin_read_account

    def read_account(session, **kwargs):
        overlaps.append(("account_read", outstanding()))
        return original_read(session, **kwargs)

    monkeypatch.setattr(CtpGatewaySession, "begin_read_account", read_account)
    original_tick = module.GatewaySettlementController.tick

    def tick(controller):
        overlaps.append(("settlement", outstanding()))
        return original_tick(controller)

    monkeypatch.setattr(module.GatewaySettlementController, "tick", tick)

    def start(trader, **kwargs):
        native = helper._NativeApi()
        native.client = trader

        def immediate(kind, rows):
            def call(field, request_id):
                for row in rows:
                    trader._handle_query_callback(kind, row, None, request_id, False)
                trader._handle_query_callback(kind, None, None, request_id, True)
                return 0
            return call

        def held_trades(field, request_id):
            with lock:
                held.append(request_id)

            def answer():
                release.wait(10)
                with lock:
                    held.remove(request_id)
                trader._handle_query_callback("trades", None, None, request_id, True)

            threading.Thread(target=answer, daemon=True).start()
            return 0

        native.ReqQryTradingAccount = immediate("account", [{
            "AccountID": "account", "BrokerID": "9999", "CurrencyID": "CNY",
            "Balance": 1000, "Available": 800}])
        native.ReqQryInvestorPosition = immediate("positions", [])
        native.ReqQryOrder = immediate("orders", [])
        native.ReqQryTrade = held_trades
        trader._api = native
        helper._login(trader, native, session_id=19)

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(TraderClient, "stop_and_wait", lambda trader, **kwargs: CtpNativeStopReceipt(
        trader.connection_generation, False, True, True, False, False))

    def feed():
        return CtpRequestDataFuture(
            None, broker_id="9999", user_id="account", password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False, ctp_env_profile="config_front_pair",
            ctp_environment="simnow", trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=key.account_key_id)

    profile = build_ctp_gateway_trading_profile(
        config, authority, tmp_path / "e130.sqlite", profile_ref="e130", settings=settings,
        grants=grants, request_feed_factory=feed, market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True, account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
        return_settings=module.CtpGatewayReturnSettings(
            read.observation_instruments, query_timeout_seconds=30, query_interval_seconds=30),
    )
    resources = _resources(profile, authority)
    try:
        owner = resources.values["binding"]._owner
        _wait(lambda: outstanding() == 1)
        returns = maintenances[0]
        before = len(overlaps)
        latencies = []
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            started = time.monotonic()
            assert owner.submit_service_call(lambda: 7).result(timeout_seconds=1.0) == 7
            latencies.append(time.monotonic() - started)
            time.sleep(0.02)
        # The query is still waiting for the venue; the owner answered anyway.
        assert outstanding() == 1 and returns._done == {"orders"}
        assert len(latencies) >= 10 and max(latencies) < 0.3, latencies
        # Nothing else that queries the venue started while it waited.
        assert [entry for entry in overlaps if entry[1]] == []
        assert len(overlaps) == before
        release.set()
        _wait(lambda: returns._done == {"orders", "trades"})
        # Account reads and settlement observations resume afterwards.
        _wait(lambda: {what for what, _ in overlaps[before:]} == {"account_read", "settlement"})
        assert resources.ready()
    finally:
        release.set()
        resources.close()
