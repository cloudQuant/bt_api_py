"""Real trading owner queue and retryable product cleanup, with synthetic native Trader."""

import importlib
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

import bt_api_py.ctp_gateway_return_maintenance as return_module
import bt_api_py.ctp_gateway_trading as trading
import bt_api_py.gateway_instrument_product_service as product_module
import bt_api_py.gateway_instrument_service as spec_module
from bt_api_py.gateway_instrument_product_codec import BEGIN, OPERATIONS, STATUS
from bt_api_py.gateway_instrument_product_config import InstrumentProductGrant
from bt_api_py.service_components import ServiceResourceConstructionError
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import (
    ActiveRegistrationContext,
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
)
from tests.integration.test_iter63_gateway_direct_service import CLIENT_A, DOMAIN
from tests.test_iter63_ctp_gateway_trading import _read_profile, _resources, _wait

IDENTITY = WireIdentity(WireRole.SERVICE, "product-principal", "product-reader")


def profile_setup(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests")
    )
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    native_row = importlib.import_module("test_iter63_gateway_instrument_query").row
    real_loader, real_maintenance = (
        trading._load_ctp_types,
        return_module.CtpGatewayReturnMaintenance,
    )
    real_spec = spec_module.GatewayInstrumentService
    account = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, direct, read = _read_profile(
        tmp_path, monkeypatch, account_key=account
    )
    monkeypatch.setattr(trading, "_load_ctp_types", real_loader)
    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", real_maintenance)
    monkeypatch.setattr(spec_module, "GatewayInstrumentService", real_spec)
    settings = replace(settings, close_timeout_seconds=0.5, connect_timeout_seconds=1)
    read = replace(
        read,
        required_coverage=(AccountResourceKey("future", "futures"),),
        refresh_interval_seconds=60,
        max_staleness_seconds=120,
    )
    key = read.observation_instruments[0]
    scope = WireScope(DOMAIN, account_ref=settings.account_scope)
    resource = ResourceGrant(
        "gateway-a", "service.instrument_product_read", "scope-a", scope, OPERATIONS
    )
    public, _ = zmq.curve_keypair()
    authority = replace(
        authority,
        principals={**authority.principals, public: IDENTITY.principal_ref},
        grants={
            **authority.grants,
            IDENTITY.principal_ref: (PrincipalGrant(IDENTITY, (resource,)),),
        },
    )
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.instrument_product_read")
    config = parse_service_config(raw)
    native_calls, natives, feeds = [], [], []

    def start(trader, **_kwargs):
        native = helper._NativeApi()
        native.client = trader

        def account_query(_field, request_id):
            trader._handle_query_callback(
                "account",
                {
                    "AccountID": "account",
                    "BrokerID": "9999",
                    "CurrencyID": "CNY",
                    "Balance": 1000,
                    "Available": 800,
                },
                None,
                request_id,
                False,
            )
            trader._handle_query_callback("account", None, None, request_id, True)
            return 0

        def positions(_field, request_id):
            trader._handle_query_callback("positions", None, None, request_id, True)
            return 0

        def instrument(field, request_id):
            native_calls.append((time.monotonic(), field.InstrumentID, field.ExchangeID, trader))
            trader._handle_query_callback(
                "instruments", native_row(InstrumentID=field.InstrumentID), None, request_id, False
            )
            trader._handle_query_callback("instruments", None, None, request_id, True)
            return 0

        native.ReqQryTradingAccount = account_query
        native.ReqQryInvestorPosition = positions
        native.ReqQryInstrument = instrument
        trader._api = native
        helper._login(trader, native, session_id=19)
        trader._query_interval = 0.05
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
        value = CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline.invalid:30001",
            md_front="tcp://offline.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=account.account_key_id,
        )
        feeds.append(value)
        return value

    grant = InstrumentProductGrant(
        IDENTITY.principal_ref, IDENTITY.actor_ref, DOMAIN, settings.account_scope, frozenset({key})
    )
    profile = trading.build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "registry-owner.sqlite",
        profile_ref="owner-test",
        settings=settings,
        grants=direct,
        request_feed_factory=feed,
        market_stream_factory=lambda *_: pytest.fail("market session forbidden"),
        assert_admitted=lambda _: False,
        account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
        instrument_product_grants=frozenset({grant}),
    )
    return profile, authority, settings, key, native_calls, natives, feeds


def contexts(account):
    product = ServiceCallContext(
        IDENTITY,
        WireScope(DOMAIN, account_ref=account),
        "product-reg",
        IDENTITY.principal_ref,
        lambda _: True,
    )
    strategy = ServiceCallContext(
        CLIENT_A,
        WireScope(DOMAIN, account_ref=account, strategy_ref=CLIENT_A.actor_ref, run_id="test-run"),
        "strategy-reg",
        CLIENT_A.principal_ref,
        lambda _: True,
    )

    def active(ctx, port, operations):
        return ActiveRegistrationContext(
            ctx,
            "gateway-a",
            port,
            "scope-a",
            operations,
            frozenset(),
            "epoch",
            "policy",
            "requirements",
            time.time() + 60,
        )

    return (
        product,
        strategy,
        active(product, "service.instrument_product_read", OPERATIONS),
        active(strategy, "service.account_read", spec_module.INSTRUMENT_OPERATIONS),
    )


def test_real_owner_interleaves_product_and_strategy_query(tmp_path, monkeypatch):
    profile, authority, settings, key, calls, natives, feeds = profile_setup(tmp_path, monkeypatch)
    resources = _resources(profile, authority)
    try:
        product, spec = (
            resources.values["instrument_product_service"],
            resources.values["instrument_service"],
        )
        owner = resources.values["binding"]._owner
        product_ctx, spec_ctx, product_active, spec_active = contexts(settings.account_scope)
        args = {"operation_id": "a" * 32, "instrument": key.to_dict()}
        assert product.handle(BEGIN, context=product_ctx, arguments=args)["status"] == "pending"
        assert (
            spec.handle("account.instrument_spec.begin", context=spec_ctx, arguments=args)["status"]
            == "pending"
        )
        deadline = time.monotonic() + 5
        seen = {}
        while time.monotonic() < deadline and len(seen) != 2:
            product.maintain_registrations((product_active,))
            spec.maintain_registrations((spec_active,))
            if "product" not in seen:
                result = product.handle(
                    STATUS, context=product_ctx, arguments={"operation_id": "a" * 32}
                )
                if result["status"] == "ready":
                    seen["product"] = result
                else:
                    assert result["status"] == "pending", result
            if "spec" not in seen:
                result = spec.handle(
                    "account.instrument_spec.status",
                    context=spec_ctx,
                    arguments={"operation_id": "a" * 32},
                )
                if result["status"] == "ready":
                    seen["spec"] = result
                else:
                    assert result["status"] == "pending", result
            time.sleep(0.005)
        assert (
            set(seen) == {"product", "spec"} and seen["product"]["snapshot"]["product_id"] == "rb"
        )
        assert len(natives) == len(feeds) == 1 and len(calls) == 2
        assert all(row[3] is natives[0].client for row in calls)
        assert product._owner is spec._owner is owner and product._session is spec._session
        assert calls[1][0] - calls[0][0] >= 0.04
    finally:
        resources.close()


def test_product_cleanup_false_retains_owner_session_and_guard_until_retry(tmp_path, monkeypatch):
    profile, authority, settings, key, _, natives, feeds = profile_setup(tmp_path, monkeypatch)
    resources = _resources(profile, authority)
    service = resources.values["instrument_product_service"]
    owner, session = resources.values["binding"]._owner, service._session
    product_ctx, _, product_active, _ = contexts(settings.account_scope)
    service.handle(
        BEGIN,
        context=product_ctx,
        arguments={"operation_id": "b" * 32, "instrument": key.to_dict()},
    )
    service.maintain_registrations((product_active,))
    job = service._jobs[(product_ctx.registration_id, "b" * 32)]
    _wait(lambda: job.handle is not None and job.call.done)
    reader, handle = job.reader, job.handle
    allow = threading.Event()
    attempts = []
    original = service.cleanup_on_owner

    def cleanup():
        result = original() if allow.is_set() else False
        attempts.append((result, threading.current_thread()))
        return result

    monkeypatch.setattr(service, "cleanup_on_owner", cleanup)
    contender = LocalAccountSessionGuard(Path(settings.guard_root), settings.account_scope, "other")
    try:
        with pytest.raises(trading.CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"):
            resources.close()
        assert any(result is False for result, _ in attempts)
        assert all(thread is owner._thread for _, thread in attempts)
        assert resources.values["binding"]._owner is owner and service._session is session
        assert job.reader is reader and job.handle is handle and not owner.closed
        assert len(natives) == len(feeds) == 1 and feeds[0].trader_client is natives[0].client
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
    finally:
        allow.set()
        resources.close()
    assert owner.closed and job.handle is None and any(result is True for result, _ in attempts)
    contender.acquire()
    contender.release()


def test_product_construction_failure_hands_back_partial_owner_for_cleanup(tmp_path, monkeypatch):
    profile, authority, settings, _, _, natives, feeds = profile_setup(tmp_path, monkeypatch)
    captured = []

    def fail(owner, session, journal, grants):
        captured.append((owner, session, journal, grants))
        raise ValueError("synthetic construction failure")

    monkeypatch.setattr(product_module, "GatewayInstrumentProductService", fail)
    contender = LocalAccountSessionGuard(Path(settings.guard_root), settings.account_scope, "other")
    with pytest.raises(ServiceResourceConstructionError) as error:
        _resources(profile, authority, cleanup_on_error=False)
    owner, session, _, _ = captured[0]
    try:
        assert not owner.closed and len(natives) == len(feeds) == 1
        assert (
            feeds[0].trader_client is natives[0].client and session._owner_thread is owner._thread
        )
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
    finally:
        error.value.resources.close()
    assert owner.closed
    contender.acquire()
    contender.release()
