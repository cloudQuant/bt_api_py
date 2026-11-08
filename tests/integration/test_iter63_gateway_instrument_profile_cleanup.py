"""Synthetic CTP profile cleanup retains its actual session guard on contention."""

import importlib
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.service_protocol import WireScope
from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

import bt_api_py.ctp_gateway_trading as trading
import bt_api_py.ctp_gateway_return_maintenance as return_module
import bt_api_py.gateway_instrument_service as instrument_module
from bt_api_py.service_components import ServiceResourceConstructionError
from bt_api_py.service_registry import ActiveRegistrationContext, ServiceCallContext
from tests.integration.test_iter63_gateway_direct_service import CLIENT_A, DOMAIN
from tests.test_iter63_ctp_gateway_trading import _read_profile, _resources, _wait


def test_profile_retains_guard_and_same_session_until_instrument_cleanup_retry(
    tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests")
    )
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    real_loader = trading._load_ctp_types
    real_maintenance = return_module.CtpGatewayReturnMaintenance
    real_instrument_service = instrument_module.GatewayInstrumentService
    account = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, grants, read = _read_profile(
        tmp_path, monkeypatch, account_key=account
    )
    monkeypatch.setattr(trading, "_load_ctp_types", real_loader)
    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", real_maintenance)
    monkeypatch.setattr(instrument_module, "GatewayInstrumentService", real_instrument_service)
    settings = replace(settings, close_timeout_seconds=0.5, connect_timeout_seconds=1)
    read = replace(
        read,
        required_coverage=(AccountResourceKey("future", "futures"),),
        refresh_interval_seconds=60,
        max_staleness_seconds=120,
    )
    natives, feeds = [], []
    requested = threading.Event()

    def start(trader, **_kwargs):
        native = helper._NativeApi()
        native.client = trader

        def response(kind, rows):
            def query(_field, request_id):
                for row in rows:
                    trader._handle_query_callback(kind, row, None, request_id, False)
                trader._handle_query_callback(kind, None, None, request_id, True)
                return 0

            return query

        native.ReqQryTradingAccount = response(
            "account",
            [
                {
                    "AccountID": "account",
                    "BrokerID": "9999",
                    "CurrencyID": "CNY",
                    "Balance": 1000,
                    "Available": 800,
                }
            ],
        )
        native.ReqQryInvestorPosition = response("positions", [])

        def instrument(_field, _request_id):
            requested.set()
            return 0

        native.ReqQryInstrument = instrument
        trader._api = native
        helper._login(trader, native, session_id=19)
        natives.append(native)

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(
        TraderClient,
        "stop_and_wait",
        lambda trader, **_: CtpNativeStopReceipt(
            trader.connection_generation, False, True, True, False, False
        ),
    )

    def feed():
        result = CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=account.account_key_id,
        )
        feeds.append(result)
        return result

    profile = trading.build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "cleanup-registry.sqlite",
        profile_ref="cleanup-profile",
        settings=settings,
        grants=grants,
        request_feed_factory=feed,
        market_stream_factory=lambda *_: pytest.fail("market must stay lazy"),
        assert_admitted=lambda _: True,
        account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
    )
    resources = _resources(profile, authority)
    service = resources.values["instrument_service"]
    cleanup_results = []
    original_cleanup = service.cleanup_on_owner

    def observe_cleanup():
        result = original_cleanup()
        cleanup_results.append((result, threading.current_thread()))
        return result

    monkeypatch.setattr(service, "cleanup_on_owner", observe_cleanup)
    owner = resources.values["binding"]._owner
    session = service._session
    context = ServiceCallContext(
        CLIENT_A,
        WireScope(
            DOMAIN,
            account_ref=settings.account_scope,
            strategy_ref=CLIENT_A.actor_ref,
            run_id="cleanup-test",
        ),
        "registration",
        CLIENT_A.principal_ref,
        lambda _: True,
    )
    active = ActiveRegistrationContext(
        context,
        "gateway-a",
        "service.account_read",
        "scope-a",
        instrument_module.INSTRUMENT_OPERATIONS,
        frozenset(),
        "epoch",
        "policy",
        "requirements",
        time.time() + 60,
    )
    contender = LocalAccountSessionGuard(Path(settings.guard_root), settings.account_scope, "other")
    locked, release = threading.Event(), threading.Event()
    barrier_entered, barrier_release = threading.Event(), threading.Event()
    holder = None
    closer = None
    try:
        service.handle(
            "account.instrument_spec.begin",
            context=context,
            arguments={
                "operation_id": "a" * 32,
                "instrument": read.observation_instruments[0].to_dict(),
            },
        )
        service.maintain_registrations((active,))
        job = service._jobs[("registration", "a" * 32)]
        _wait(lambda: job.handle is not None and job.call.done)
        # begin reserves a real provider handle; only a later poll enters the
        # native request, which this cleanup test deliberately has not sent.
        assert not requested.is_set()
        reader, handle = job.reader, job.handle
        native = natives[0]
        assert len(natives) == len(feeds) == 1 and session._owner_thread is owner._thread

        def barrier():
            barrier_entered.set()
            assert barrier_release.wait(5)

        owner.submit_service_call(barrier)
        assert barrier_entered.wait(3)

        def hold():
            with native.client._query_state_lock:
                locked.set()
                release.wait(5)

        holder = threading.Thread(target=hold, name="synthetic-trader-lock")
        holder.start()
        assert locked.wait(2)
        close_errors = []

        def close():
            try:
                resources.close()
            except Exception as error:
                close_errors.append(error)

        closer = threading.Thread(target=close, name="synthetic-resource-close")
        closer.start()
        _wait(lambda: owner._closing)
        barrier_release.set()
        closer.join(2)
        assert not closer.is_alive()
        assert len(close_errors) == 1 and isinstance(
            close_errors[0], trading.CtpGatewayTradingError
        )
        assert str(close_errors[0]) == "TRADING_OWNER_CLOSE_UNCONFIRMED"
        assert any(result is False for result, _ in cleanup_results)
        assert all(thread is owner._thread for _, thread in cleanup_results)
        assert job.reader is reader and job.handle is handle
        assert service._session is session and resources.values["binding"]._owner is owner
        assert feeds[0].trader_client is native.client and not owner.closed
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
        with pytest.raises(ServiceResourceConstructionError):
            _resources(profile, authority)
        assert service._session is session and not owner.closed
    finally:
        barrier_release.set()
        release.set()
        if closer is not None:
            closer.join(3)
            assert not closer.is_alive()
        if holder is not None:
            holder.join(3)
            assert not holder.is_alive()
        resources.close()
    assert owner.closed and job.handle is None and len(natives) == 1
    assert any(result is True for result, _ in cleanup_results)
    contender.acquire()
    contender.release()
