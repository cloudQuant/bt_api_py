"""Explicit order-cut settings and account-scoped reader admission."""

from dataclasses import replace

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.ctp_gateway_order_cut_source import CtpGatewayOrderCutSettings
from bt_api_py.ctp_gateway_trading import (
    CtpGatewayReturnSettings,
    CtpGatewayTradingError,
    build_ctp_gateway_trading_profile,
)
from bt_api_py.gateway_direct_service import ACCOUNT_ORDER_CUT_OPERATIONS, GatewayControlGrant
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.test_iter63_ctp_gateway_trading import CLIENT_A, DOMAIN, _read_profile


@pytest.mark.parametrize(
    "timeout,refresh,principals",
    [
        (True, 1, frozenset({"reader"})),
        (0, 1, frozenset({"reader"})),
        (301, 1, frozenset({"reader"})),
        (1, False, frozenset({"reader"})),
        (1, 0, frozenset({"reader"})),
        (1, 3601, frozenset({"reader"})),
        (1, 1, ()),
        (1, 1, frozenset()),
        (1, 1, frozenset({""})),
    ],
)
def test_settings_reject_bad_bounds(timeout, refresh, principals):
    with pytest.raises(ValueError):
        CtpGatewayOrderCutSettings(timeout, refresh, principals)


def test_profile_requires_matching_service_account_reader_before_resources(tmp_path, monkeypatch):
    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["options"].update(
        cancel_enabled=True,
        control_enabled=True,
        strategy_ledger_enabled=True,
        account_extensions_enabled=True,
        account_facts_financial_enabled=True,
    )
    config = parse_service_config(raw)
    reader = WireIdentity(WireRole.SERVICE, "reader", "reader-service")
    resource = ResourceGrant(
        "gateway-a",
        "service.account_read",
        "scope-a",
        WireScope(DOMAIN, account_ref=settings.account_scope),
        frozenset({"account.events.progress"}) | ACCOUNT_ORDER_CUT_OPERATIONS,
    )
    public, _ = zmq.curve_keypair()
    authority = replace(
        authority,
        principals={**authority.principals, public: "reader"},
        grants={**authority.grants, "reader": (PrincipalGrant(reader, (resource,)),)},
    )
    control = {
        CLIENT_A.principal_ref: GatewayControlGrant(
            CLIENT_A.actor_ref, True, frozenset(), frozenset({"direct.control.status"})
        )
    }
    kwargs = {
        "profile_ref": "cut",
        "settings": settings,
        "grants": grants,
        "request_feed_factory": lambda: None,
        "market_stream_factory": lambda *_: None,
        "assert_admitted": lambda _: True,
        "account_read_settings": read,
        "account_read_principals": frozenset({CLIENT_A.principal_ref, "reader"}),
        "return_settings": CtpGatewayReturnSettings(read.observation_instruments),
        "control_grants": control,
    }
    cut = CtpGatewayOrderCutSettings(1, 2, frozenset({"reader"}))
    profile = build_ctp_gateway_trading_profile(
        config, authority, tmp_path / "registry-test", order_cut_observation=cut, **kwargs
    )
    assert profile is not None
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        build_ctp_gateway_trading_profile(config, authority, tmp_path / "registry-test", **kwargs)
    incomplete_resource = replace(resource, operations=frozenset({"account.events.progress"}))
    incomplete_authority = replace(
        authority,
        grants={**authority.grants, "reader": (PrincipalGrant(reader, (incomplete_resource,)),)},
    )
    with pytest.raises(CtpGatewayTradingError, match="INVALID_ORDER_CUT_SETTINGS"):
        build_ctp_gateway_trading_profile(
            config,
            incomplete_authority,
            tmp_path / "registry-test",
            order_cut_observation=cut,
            **kwargs,
        )
    with pytest.raises(CtpGatewayTradingError, match="INVALID_ORDER_CUT_SETTINGS"):
        build_ctp_gateway_trading_profile(
            config,
            authority,
            tmp_path / "registry-test",
            order_cut_observation=replace(cut, principals=frozenset({CLIENT_A.principal_ref})),
            **kwargs,
        )
    with pytest.raises(CtpGatewayTradingError, match="INVALID_ORDER_CUT_SETTINGS"):
        build_ctp_gateway_trading_profile(
            config,
            authority,
            tmp_path / "registry-test",
            order_cut_observation=cut,
            **{**kwargs, "return_settings": None},
        )


def test_protected_bundle_accepts_only_explicit_unique_authorized_readers(tmp_path, monkeypatch):
    import json
    from copy import deepcopy

    from bt_api_py.ctp_gateway_local_profile import (
        CtpLocalProfileError,
        simnow_trading_profile_factory,
    )
    from tests.unit.test_iter63_ctp_gateway_local_profile import (
        _composing_config,
        _composing_doc,
        _fixture,
        _observation,
        _observer,
    )

    doc, file, config = _fixture(tmp_path, monkeypatch)
    raw, _ = _composing_config(config, account_facts_financial_enabled=True)
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
    config = parse_service_config(raw)
    _composing_doc(doc)
    doc["account_observation"] = _observation(doc, tmp_path, [_observer(doc)])
    doc["account_observation"]["observers"][0]["resources"][0]["operations"].extend(
        sorted(ACCOUNT_ORDER_CUT_OPERATIONS)
    )
    doc["order_cut_observation"] = {
        "query_timeout_seconds": 1,
        "refresh_interval_seconds": 2,
        "principals": ["principal-observer"],
    }

    def load():
        file.write_text(json.dumps(doc), encoding="utf-8")
        return simnow_trading_profile_factory.create_deployment(config)

    assert load().state == "new"
    original = deepcopy(doc["order_cut_observation"])
    for bad in (
        None,
        {**original, "extra": 1},
        {**original, "principals": []},
        {**original, "principals": ["principal-observer", "principal-observer"]},
        {**original, "principals": ["unknown"]},
    ):
        doc["order_cut_observation"] = bad
        with pytest.raises((CtpLocalProfileError, CtpGatewayTradingError)):
            load()


def test_formal_profile_exposes_real_owner_source_and_drains_native_queries(tmp_path, monkeypatch):
    import importlib
    from pathlib import Path

    from bt_api_base.account_protocol import AccountKey
    from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.session_guard import LocalAccountSessionGuard

    from bt_api_py import ctp_gateway_return_maintenance as return_module
    from bt_api_py import ctp_gateway_trading as trading
    from tests.test_iter63_ctp_gateway_trading import _wait

    real_loader, real_maintenance = (
        trading._load_ctp_types,
        return_module.CtpGatewayReturnMaintenance,
    )
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, grants, read = _read_profile(
        tmp_path, monkeypatch, account_key=key
    )
    monkeypatch.setattr(trading, "_load_ctp_types", real_loader)
    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", real_maintenance)
    guard = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, settings.owner_id)
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal(
        settings.journal_path,
        settings.account_scope,
        generation=generation,
        expected_journal_id=settings.expected_journal_id,
        minimum_revision=0,
    )
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    journal.enable_account_extensions(guard)
    journal.enable_parent_authority(guard)
    journal.enable_account_facts_financial(guard)
    journal.enable_observed_order_source(guard)
    guard.release()
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["options"].update(
        cancel_enabled=True,
        control_enabled=True,
        strategy_ledger_enabled=True,
        account_extensions_enabled=True,
        account_facts_financial_enabled=True,
    )
    config = parse_service_config(raw)
    reader = WireIdentity(WireRole.SERVICE, "reader", "reader-service")
    grant = ResourceGrant(
        "gateway-a",
        "service.account_read",
        "scope-a",
        WireScope(DOMAIN, account_ref=settings.account_scope),
        frozenset({"account.events.progress"}) | ACCOUNT_ORDER_CUT_OPERATIONS,
    )
    public, _ = zmq.curve_keypair()
    authority = replace(
        authority,
        principals={**authority.principals, public: "reader"},
        grants={**authority.grants, "reader": (PrincipalGrant(reader, (grant,)),)},
    )
    from threading import Event

    native_calls, natives = [], []
    hold_cut, cut_held = Event(), Event()
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests")
    )
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")

    class Native(helper._NativeApi):
        def ReqQryOrder(self, field, rid):
            native_calls.append("orders")
            if (
                hold_cut.is_set()
                and getattr(self, "source", None) is not None
                and self.source._session._order_query_cut is not None
            ):
                self.held_rid = rid
                cut_held.set()
                return 0
            self.client._handle_query_callback("orders", None, None, rid, True)
            return 0

        def ReqQryTrade(self, field, rid):
            native_calls.append("trades")
            self.client._handle_query_callback("trades", None, None, rid, True)
            return 0

        def ReqQryInvestorPosition(self, field, rid):
            native_calls.append("positions")
            self.client._handle_query_callback("positions", None, None, rid, True)
            return 0

        def ReqQryTradingAccount(self, field, rid):
            from types import SimpleNamespace

            native_calls.append("account")
            self.client._handle_query_callback(
                "account",
                SimpleNamespace(
                    CurrencyID="CNY",
                    Balance=1000.0,
                    Available=900.0,
                    CurrMargin=100.0,
                    BrokerID="9999",
                    AccountID="account",
                    TradingDay=helper.TODAY,
                ),
                None,
                rid,
                True,
            )
            return 0

    def start(trader, **kwargs):
        native = Native()
        native.client = trader
        trader._api = native
        natives.append(native)
        helper._login(trader, native, session_id=19)

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
            trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=key.account_key_id,
        )

    control = {
        CLIENT_A.principal_ref: GatewayControlGrant(
            CLIENT_A.actor_ref, True, frozenset(), frozenset({"direct.control.status"})
        )
    }
    cut = CtpGatewayOrderCutSettings(2, 0.1, frozenset({"reader"}))
    profile = build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "registry-test",
        profile_ref="cut",
        settings=settings,
        grants=grants,
        request_feed_factory=feed,
        market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True,
        account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref, "reader"}),
        return_settings=CtpGatewayReturnSettings(
            read.observation_instruments, query_interval_seconds=0.05
        ),
        control_grants=control,
        order_cut_observation=cut,
    )
    deployment = profile.deployment_factory()
    try:
        resources = deployment._provide_resources(
            "gateway",
            "gateway-a",
            config.components["gateway"]["settings"]["options"],
            authority.scope,
        )
    except Exception as error:
        if hasattr(error, "resources"):
            error.resources.close()
        raise
    try:
        source = resources.values["order_cut_source"]
        owner = resources.values["binding"]._owner

        def current():
            return owner.submit_service_call(source.current).result(timeout_seconds=1)

        _wait(lambda: current() is not None)
        first = owner.submit_service_call(lambda: source._coordinator).result(timeout_seconds=1)
        _wait(lambda: {"orders", "trades", "positions", "account"} <= set(native_calls))
        _wait(
            lambda: owner.submit_service_call(
                lambda: source._coordinator is not first and source.current() is not None
            ).result(timeout_seconds=1)
        )
        natives[0].source = source
        hold_cut.set()
        owner.submit_service_call(lambda: setattr(source._coordinator, "_deadline", 0.0)).result(
            timeout_seconds=1
        )
        assert cut_held.wait(3)
        count = len(native_calls)
        assert not Event().wait(0.15)
        assert len(native_calls) == count
        from types import SimpleNamespace

        from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

        natives[0].client._spi.OnRtnOrder(
            SimpleNamespace(
                **_order(TradingDay=helper.TODAY, InstrumentID="rb2612", ExchangeID="SHFE")
            )
        )
        _wait(
            lambda: owner.submit_service_call(
                lambda: source._maintenance._observed_order_source.status().source_prefix > 0
            ).result(timeout_seconds=1)
        )
        hold_cut.clear()
        natives[0].client._spi.OnRspQryOrder(None, None, natives[0].held_rid, True)
        _wait(lambda: current() is not None)
        assert source.principals == frozenset({"reader"}) and source.execution_domain_ref == DOMAIN
        assert "orders" in native_calls
    finally:
        if cut_held.is_set() and hold_cut.is_set():
            hold_cut.clear()
            natives[0].client._spi.OnRspQryOrder(None, None, natives[0].held_rid, True)
        resources.close()
