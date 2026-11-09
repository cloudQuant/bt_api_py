"""CTP Gateway owner settlement boundary with synthetic native only."""
from __future__ import annotations

import hashlib
import importlib
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError
from bt_api_py import ctp_gateway_trading as module
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError, build_ctp_gateway_trading_profile
from tests.test_iter63_ctp_gateway_trading import make, _resources, _wait


@pytest.fixture
def configured(tmp_path, monkeypatch):
    ctp_tests = Path(__file__).resolve().parents[2] / "bt_api" / "bt_api_ctp" / "tests"
    monkeypatch.syspath_prepend(str(ctp_tests))
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    real_loader = module._load_ctp_types
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, write_enabled=True, account_key=key)
    monkeypatch.setattr(module, "_load_ctp_types", real_loader)
    settings = replace(settings, connect_timeout_seconds=0.5, close_timeout_seconds=0.2,
                       maintenance_interval_seconds=0.02)
    entered, release = threading.Event(), threading.Event()
    should_block = threading.Event()
    providers, natives, states, stops = [], [], [], []
    provider_init = CtpDirectWriteProvider.__init__
    def capture_provider(self, *args, **kwargs):
        provider_init(self, *args, **kwargs)
        providers.append(self)
    monkeypatch.setattr(CtpDirectWriteProvider, "__init__", capture_provider)
    publish = module.GatewaySettlementController._publish
    def capture_state(self, status, *args, **kwargs):
        states.append(status)
        return publish(self, status, *args, **kwargs)
    monkeypatch.setattr(module.GatewaySettlementController, "_publish", capture_state)

    class Native(helper._NativeApi):
        def ReqQrySettlementInfoConfirm(self, field, request_id):
            if should_block.is_set():
                entered.set()
                if not release.wait(8):
                    raise TimeoutError("synthetic native query unreleased")
            return super().ReqQrySettlementInfoConfirm(field, request_id)

        def ReqSettlementInfoConfirm(self, field, request_id):
            from types import SimpleNamespace

            result = super().ReqSettlementInfoConfirm(field, request_id)
            # Synthetic venue: accept and acknowledge the confirmation.
            self.confirmation_day = helper.TODAY
            self.client._spi.OnRspSettlementInfoConfirm(
                SimpleNamespace(BrokerID="9999", InvestorID="account", ConfirmDate=helper.TODAY),
                SimpleNamespace(ErrorID=0, ErrorMsg=""), request_id, True)
            return result

    def start(trader, **_kwargs):
        native = Native()
        native.client = trader
        native.confirmation_day = "20261105"
        trader._api = native
        helper._login(trader, native, session_id=19)
        natives.append(native)

    def stop(trader, *, timeout):
        stops.append(threading.get_ident())
        return CtpNativeStopReceipt(trader.connection_generation, False, True, True, False, False)

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(TraderClient, "stop_and_wait", stop)
    def feed():
        result = CtpRequestDataFuture(None, broker_id="9999", user_id="account", password="synthetic",
            td_front="tcp://offline-td.invalid:30001", md_front="tcp://offline-md.invalid:30011",
            auto_settlement_confirm=False, ctp_env_profile="config_front_pair", ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root), flow_account_scope=settings.account_scope)
        result._connect_timeout = 0.05
        return result
    def build():
        return build_ctp_gateway_trading_profile(config, authority, tmp_path / "settlement-registry.sqlite",
            profile_ref="synthetic-settlement", settings=settings, grants=grants, request_feed_factory=feed,
            market_stream_factory=lambda *_: pytest.fail("market must remain lazy"), assert_admitted=lambda _: True,
            return_settings=module.CtpGatewayReturnSettings((MarketInstrumentKey(
                "ctp", "SHFE", "simnow", "future", "rb2612", "rb2612"),)))
    return locals()


def test_blocked_native_read_retains_owner_and_guard_until_explicit_release(configured):
    c = configured
    c["should_block"].set()
    resources = _resources(c["build"](), c["authority"])
    owner = resources.values["binding"]._owner
    try:
        assert c["entered"].wait(5)
        provider, = c["providers"]
        assert provider._lock.acquire(timeout=0.5)
        provider._lock.release()
        assert owner.status().thread_alive
        start = time.monotonic()
        with pytest.raises(CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"):
            resources.close()
        assert time.monotonic() - start < 2
        assert owner.status().thread_alive and not owner.status().accepting
        assert owner.binding() is None  # Closing is no longer publicly usable.
        with owner._condition:
            assert owner._binding is not None and owner._journal is resources.values["binding"]._journal
        assert c["stops"] == []
        contender = LocalAccountSessionGuard(Path(c["settings"].guard_root), c["settings"].account_scope, "other")
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
        assert "confirm_settlement" not in c["natives"][0].calls
    finally:
        c["release"].set()
        _wait(lambda: not owner.status().thread_alive)
        resources.close()
    assert len(c["stops"]) == 1
    contender.acquire()
    contender.release()


@pytest.mark.parametrize("historical", [False, True])
def test_unconfirmed_settlement_claims_once_and_historical_marker_is_never_repeated(
        configured, historical):
    """Decision A: a new day is confirmed once through its marker and claim;
    an existing UNKNOWN marker is only read back, never sent again."""
    c = configured
    settings = c["settings"]
    if historical:
        account_binding = hashlib.sha256(b"9999:account").hexdigest()[:16]
        with sqlite3.connect(settings.journal_path) as db:
            db.execute("INSERT INTO direct_journal_settlement VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (settings.account_scope, module.ROUTE, c["helper"].TODAY, account_binding, "historical-owner",
                 1, "historical-observation", "historical-marker", "UNKNOWN", 2, None))
            db.execute("UPDATE direct_journal_meta SET revision=revision+1")
    resources = _resources(c["build"](), c["authority"])
    try:
        expected = "READBACK_REQUIRED" if historical else "CONFIRMED"
        _wait(lambda: expected in c["states"])
        if historical:
            _wait(lambda: c["states"].count("READBACK_REQUIRED") >= 2)
            assert c["natives"][0].calls.count("query_settlement") >= 2
            assert "confirm_settlement" not in c["natives"][0].calls
        else:
            # Further maintenance ticks read back only; never a second send.
            _wait(lambda: c["states"].count("CONFIRMED") >= 2)
            assert c["natives"][0].calls.count("confirm_settlement") == 1
        assert c["natives"][0].calls.count("query_settlement") >= 1
        with sqlite3.connect(settings.journal_path) as db:
            rows = db.execute("SELECT status,marker_id FROM direct_journal_settlement").fetchall()
        if historical:
            assert rows == [("UNKNOWN", "historical-marker")]
        else:
            assert len(rows) == 1 and rows[0][0] == "CONFIRMED" and rows[0][1]
    finally:
        resources.close()
