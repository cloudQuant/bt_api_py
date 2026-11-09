"""Synthetic trading-owner market composition; no provider or account access."""

import threading
import time
from dataclasses import replace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireScope

import bt_api_py.ctp_gateway_trading as module
from bt_api_py.service_components import ServiceResourceConstructionError
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.test_iter63_ctp_gateway_trading import (
    CLIENT_A,
    DOMAIN,
    FakeSession,
    _read_profile,
    _wait,
    make,
)
from tests.unit.test_service_registry import _register

ASYNC_MARKET = frozenset(
    {"market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel"}
)


def key():
    return MarketSubscriptionKey(
        "source",
        "entitlement",
        MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612"),
        "tick",
    )


class MarketCapabilities:
    def source_epoch_reset_required(self, handle):
        return False

    def retained_events_ready(self, handle):
        return False

    def poll(self, handle, *, limit):
        return ()


def setup(
    tmp_path, monkeypatch, *, ops=frozenset({"market.subscribe"}), market=True, account_ref=None
):
    _, settings, config, authority, grants, factories = make(tmp_path, monkeypatch)
    cfg = config.to_public_dict()
    if market:
        cfg["components"]["gateway"]["settings"]["bind_ports"].append("service.market_source")
        cfg["communication"]["profiles"]["server-transport"]["endpoints"]["market_bind"] = (
            "tcp://127.0.0.1:1"
        )
    config = parse_service_config(cfg)
    identity = authority.grants[CLIENT_A.principal_ref][0].identity
    direct = authority.grants[CLIENT_A.principal_ref][0].resources[0]
    market_grant = ResourceGrant(
        "gateway-a",
        "service.market_source",
        "scope-a",
        WireScope(
            DOMAIN, account_ref=settings.account_scope if account_ref is None else account_ref,
            strategy_ref=CLIENT_A.actor_ref,
        ),
        ops,
    )
    authority = replace(
        authority,
        grants={CLIENT_A.principal_ref: (PrincipalGrant(identity, (direct, market_grant)),)},
    )
    allowed = {CLIENT_A.principal_ref: frozenset({key()})}
    market_settings = module.CtpGatewayMarketSettings(frozenset({key()}), allowed)

    def build(**overrides):
        kwargs = dict(
            profile_ref="market-profile",
            settings=settings,
            grants=grants,
            request_feed_factory=lambda: factories.append("feed"),
            market_stream_factory=lambda: factories.append("market"),
            assert_admitted=lambda _: True,
            market_settings=market_settings,
        )
        kwargs.update(overrides)
        return module.build_ctp_gateway_trading_profile(
            config, authority, tmp_path / "registry-market.sqlite", **kwargs
        )

    return build, allowed, settings, authority, factories


def test_settings_copy_and_invalid_input(tmp_path, monkeypatch):
    build, allowed, _, _, factories = setup(tmp_path, monkeypatch)
    original = module.CtpGatewayMarketSettings(frozenset({key()}), allowed)
    allowed.clear()
    assert set(original.principal_allowed_keys) == {CLIENT_A.principal_ref}
    build()
    assert FakeSession.instances == [] and factories == []
    with pytest.raises(module.CtpGatewayTradingError, match="INVALID_MARKET_SETTINGS"):
        module.CtpGatewayMarketSettings(frozenset({key()}), {"a": frozenset()})
    with pytest.raises(module.CtpGatewayTradingError, match="INVALID_MARKET_SETTINGS"):
        module.CtpGatewayMarketSettings(
            frozenset({key()}), {"a": frozenset({key()})}, owner_queue_size=True
        )


@pytest.mark.parametrize(
    "ops", [frozenset({"market.snapshot"}), frozenset({"market.subscribe", "market.history"})]
)
def test_missing_subscribe_or_history_grant_rejected(tmp_path, monkeypatch, ops):
    build, _, _, _, _ = setup(tmp_path, monkeypatch, ops=ops)
    with pytest.raises(module.CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        build()


@pytest.mark.parametrize("operations", [frozenset(), ASYNC_MARKET])
def test_async_market_grant_is_explicit_and_exact_account(tmp_path, monkeypatch, operations):
    build, _, _, authority, factories = setup(
        tmp_path, monkeypatch, ops=frozenset({"market.subscribe"}) | operations
    )
    assert build() is not None
    resource = authority.grants[CLIENT_A.principal_ref][0].resources[-1]
    assert resource.operations == frozenset({"market.subscribe"}) | operations
    assert factories == [] and FakeSession.instances == []

    other = tmp_path / "other"
    other.mkdir()
    invalid, _, _, _, _ = setup(
        other, monkeypatch, ops=frozenset({"market.subscribe"}) | operations,
        account_ref="other-account",
    )
    with pytest.raises(module.CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        invalid()


@pytest.mark.parametrize(
    "async_grant", [frozenset(), ASYNC_MARKET - {"market.bootstrap_cancel"}, ASYNC_MARKET]
)
def test_async_registration_needs_explicit_grant(tmp_path, monkeypatch, async_grant):
    granted = frozenset({"market.subscribe"}) | async_grant
    build, _, settings, authority, _ = setup(tmp_path, monkeypatch, ops=granted)
    assert build() is not None
    operations = frozenset({"market.subscribe"}) | ASYNC_MARKET
    entry = authority.grants[CLIENT_A.principal_ref][0]
    registry = ServiceRegistry(
        tmp_path / "registry-grant-check.sqlite", service_id="gateway-service",
        capabilities=(ServiceCapability("gateway-a", "service.market_source", operations),),
        grants={CLIENT_A.principal_ref: (replace(entry, resources=entry.resources[-1:]),)},
        policy_version="policy-v1", requirements_version="requirements-v1",
    )
    try:
        result = _register(
            registry, identity=CLIENT_A, principal=CLIENT_A.principal_ref,
            scope=WireScope(DOMAIN, account_ref=settings.account_scope,
                            strategy_ref=CLIENT_A.actor_ref, run_id="run-a"),
            operations=tuple(sorted(operations)), instance_id="gateway-a",
            port="service.market_source", scope_ref="scope-a",
        )
        if async_grant == ASYNC_MARKET:
            assert result["ok"] is True
        else:
            assert result["ok"] is False and result["code"] == "SCOPE_DENIED"
    finally:
        registry.close()


def test_port_and_key_scope_rejected(tmp_path, monkeypatch):
    build, _, _, _, _ = setup(tmp_path, monkeypatch, market=False)
    with pytest.raises(module.CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        build()
    wrong = replace(key(), entitlement_ref="other")
    with pytest.raises(module.CtpGatewayTradingError, match="INVALID_MARKET_SETTINGS"):
        build(
            market_settings=module.CtpGatewayMarketSettings(
                frozenset({wrong}), {CLIENT_A.principal_ref: frozenset({wrong})}
            )
        )


@pytest.mark.parametrize(
    "bad_key",
    [
        replace(key(), channel="ticker"),
        replace(key(), depth=1),
        replace(key(), timeframe="1m"),
        replace(key(), instrument=replace(key().instrument, native_symbol="different")),
    ],
)
def test_profile_rejects_unsupported_ctp_key_before_session(tmp_path, monkeypatch, bad_key):
    build, _, _, _, _ = setup(tmp_path, monkeypatch)
    with pytest.raises(module.CtpGatewayTradingError, match="INVALID_MARKET_SETTINGS"):
        build(
            market_settings=module.CtpGatewayMarketSettings(
                frozenset({bad_key}), {CLIENT_A.principal_ref: frozenset({bad_key})}
            )
        )
    assert FakeSession.instances == []


def test_account_and_market_ports_can_be_explicitly_combined(tmp_path, monkeypatch):
    _, settings, config, authority, grants, read_settings = _read_profile(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.market_source")
    raw["communication"]["profiles"]["server-transport"]["endpoints"]["market_bind"] = (
        "tcp://127.0.0.1:1"
    )
    config = parse_service_config(raw)
    entry = authority.grants[CLIENT_A.principal_ref][0]
    market = ResourceGrant(
        "gateway-a",
        "service.market_source",
        "scope-a",
        WireScope(DOMAIN, account_ref=settings.account_scope, strategy_ref=CLIENT_A.actor_ref),
        frozenset({"market.subscribe"}),
    )
    authority = replace(
        authority,
        grants={CLIENT_A.principal_ref: (replace(entry, resources=(*entry.resources, market)),)},
    )
    profile = module.build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "combined-registry.sqlite",
        profile_ref="combined",
        settings=settings,
        grants=grants,
        request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True,
        account_read_settings=read_settings,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
        market_settings=module.CtpGatewayMarketSettings(
            frozenset({key()}), {CLIENT_A.principal_ref: frozenset({key()})}
        ),
    )
    assert profile is not None and FakeSession.instances == []


def test_owner_market_borrows_single_session_and_close_retry(tmp_path, monkeypatch):
    class Market(MarketCapabilities):
        def __init__(self):
            self.calls = []

        def subscribe(self, subscription, *, generation):
            self.calls.append(("subscribe", threading.get_ident()))
            return object()

        def unsubscribe(self, handle):
            self.calls.append(("unsubscribe", threading.get_ident()))
            return True

    market = Market()

    def market_provider(session):
        assert threading.get_ident() == session.owner
        return market

    monkeypatch.setattr(FakeSession, "market_provider", property(market_provider), raising=False)
    build, _, _, authority, _ = setup(tmp_path, monkeypatch)
    profile = build()
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources(
        "gateway", "gateway-a", {"role": "trading_owner", "dispatch_enabled": True}, authority.scope
    )
    owner = resources.values["binding"]._owner
    coordinator = resources.values['claim_coordinator']
    from bt_api_py.gateway_dispatch_claim import DispatchClaimError, GatewayDispatchClaimCoordinator
    assert type(coordinator) is GatewayDispatchClaimCoordinator
    assert coordinator.matches_owner(owner.binding().account_scope, owner.binding().generation)
    with pytest.raises(DispatchClaimError, match='COVERAGE_UNAVAILABLE'):
        coordinator.capture()
    proxy = resources.values["hub"]._provider
    handle = proxy.subscribe(key(), generation="test-generation")
    _wait(lambda: proxy.unsubscribe(handle) is True)
    assert len(FakeSession.instances) == 1
    assert market.calls and all(
        thread == FakeSession.instances[0].owner for _, thread in market.calls
    )
    FakeSession.instances[0].close_allowed = False
    with pytest.raises(module.CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"):
        resources.close()
    assert not owner.closed
    FakeSession.instances[0].close_allowed = True
    resources.close()
    assert owner.closed and len(FakeSession.instances) == 1


def test_hub_constructor_failure_retains_original_owner_for_cleanup(tmp_path, monkeypatch):
    import bt_api_gateway.market_hub as hub_module

    build, _, _, authority, _ = setup(tmp_path, monkeypatch)
    profile = build()
    deployment = profile.deployment_factory()
    monkeypatch.setattr(
        hub_module, "GatewayMarketHub", lambda *a, **kw: (_ for _ in ()).throw(ValueError())
    )
    with pytest.raises(ServiceResourceConstructionError) as error:
        deployment._provide_resources(
            "gateway",
            "gateway-a",
            {"role": "trading_owner", "dispatch_enabled": True},
            authority.scope,
        )
    assert len(FakeSession.instances) == 1
    FakeSession.instances[0].close_allowed = False
    with pytest.raises(module.CtpGatewayTradingError):
        error.value.resources.close()
    FakeSession.instances[0].close_allowed = True
    error.value.resources.close()
    assert len(FakeSession.instances) == 1


def test_pending_market_release_does_not_disable_owner_readiness(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class Market(MarketCapabilities):
        def subscribe(self, subscription, *, generation):
            return object()

        def unsubscribe(self, handle):
            entered.set()
            assert release.wait(3)
            return True

    market = Market()
    monkeypatch.setattr(
        FakeSession, "market_provider", property(lambda self: market), raising=False
    )
    build, _, _, authority, _ = setup(tmp_path, monkeypatch)
    profile = build(
        market_settings=module.CtpGatewayMarketSettings(
            frozenset({key()}),
            {CLIENT_A.principal_ref: frozenset({key()})},
            call_timeout_seconds=0.01,
        )
    )
    resources = profile.deployment_factory()._provide_resources(
        "gateway", "gateway-a", {"role": "trading_owner", "dispatch_enabled": True}, authority.scope
    )
    proxy = resources.values["hub"]._provider
    handle = proxy.subscribe(key(), generation="generation")
    try:
        # Release is pending on the same owner; it is not an account failure.
        assert proxy.unsubscribe(handle) is False
        assert entered.wait(1)
        assert resources.ready() is True
    finally:
        release.set()
        resources.close()


def test_uncertain_market_start_requires_confirmed_native_owner_cleanup(tmp_path, monkeypatch):
    from bt_api_gateway.market_hub import GatewayMarketHubError
    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    class Market(MarketCapabilities):
        def subscribe(self, subscription, *, generation):
            raise ValueError("unknown native outcome")

        def unsubscribe(self, handle):
            return True

    market = Market()
    monkeypatch.setattr(
        FakeSession, "market_provider", property(lambda self: market), raising=False
    )
    build, _, settings, authority, _ = setup(tmp_path, monkeypatch)
    profile = build()
    resources = profile.deployment_factory()._provide_resources(
        "gateway", "gateway-a", {"role": "trading_owner", "dispatch_enabled": True}, authority.scope
    )
    hub = resources.values["hub"]
    hub.set_publisher(lambda _: None)
    contender = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, "contender")
    try:
        with pytest.raises(GatewayMarketHubError, match="PROVIDER_SUBSCRIBE_FAILED"):
            hub.subscribe(CLIENT_A.principal_ref, key(), "registration")
        FakeSession.instances[0].close_allowed = False
        with pytest.raises(module.CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"):
            resources.close()
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
        FakeSession.instances[0].close_allowed = True
        resources.close()
        assert resources.values["binding"]._owner.closed
        contender.acquire()
        contender.release()
    finally:
        FakeSession.instances[0].close_allowed = True
        resources.close()


def test_close_with_many_handles_waits_only_for_original_owner(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class Market(MarketCapabilities):
        def subscribe(self, subscription, *, generation):
            return object()

        def unsubscribe(self, handle):
            raise AssertionError("native unsubscribe must not run before session close")

    market = Market()
    monkeypatch.setattr(
        FakeSession, "market_provider", property(lambda self: market), raising=False
    )
    build, _, settings, authority, _ = setup(tmp_path, monkeypatch)
    keys = frozenset(
        replace(
            key(),
            instrument=replace(key().instrument, instrument_id=f"rb{i}", native_symbol=f"rb{i}"),
        )
        for i in range(6)
    )
    resources = (
        build(market_settings=module.CtpGatewayMarketSettings(keys, {CLIENT_A.principal_ref: keys}))
        .deployment_factory()
        ._provide_resources(
            "gateway",
            "gateway-a",
            {"role": "trading_owner", "dispatch_enabled": True},
            authority.scope,
        )
    )
    hub = resources.values["hub"]
    proxy = hub._provider
    owner = resources.values["binding"]._owner
    handles = [proxy.subscribe(item, generation=f"generation-{i}") for i, item in enumerate(keys)]
    assert len(handles) == 6

    def blocker():
        entered.set()
        assert release.wait(5)

    owner.submit_service_call(blocker)
    assert entered.wait(1)
    try:
        started = time.monotonic()
        with pytest.raises(module.CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"):
            resources.close()
        assert time.monotonic() - started < 2.0
        assert not owner.closed and not release.is_set()
        assert resources.values["hub"] is hub and hub._provider is proxy
        from bt_api_gateway.session_guard import (
            LocalAccountSessionGuard,
            LocalAccountSessionGuardError,
        )

        contender = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, "other")
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
    finally:
        release.set()
        resources.close()
    assert owner.closed and len(FakeSession.instances) == 1
    contender.acquire()
    contender.release()
