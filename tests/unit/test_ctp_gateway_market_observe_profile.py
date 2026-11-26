"""Synthetic tests for the standalone CTP SimNow market-observe profile."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import MappingProxyType, ModuleType

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.ctp_gateway_local_profile import (
    CtpLocalProfileError,
    create_simnow_7x24_market_observe_deployment,
    create_simnow_market_observe_deployment,
    load_simnow_7x24_market_observe_resources,
    load_simnow_market_observe_resources,
    simnow_7x24_market_observe_profile_factory,
    simnow_market_observe_profile_factory,
)
from bt_api_py.service_config import parse_service_config

_PROFILE = "set1_group1_vpn"
_PROFILE_7X24 = "set2_7x24_4000x"
_PRINCIPAL = "principal:market-observer"
_DOMAIN = "paper:market-observer"
_ACCOUNT = "account:market-observer"
_STRATEGY = "strategy:market-observer"
_OPERATIONS = frozenset({
    "market.subscribe", "market.unsubscribe", "market.snapshot", "market.head", "market.read_after",
})


def _wire_scope(scope: WireScope) -> dict[str, object]:
    return {
        name: list(value) if name == "account_refs" else value
        for name in (
            "execution_domain_ref", "account_ref", "account_refs", "strategy_ref", "run_id",
            "manual_book_ref", "portfolio_ref", "group_id", "leg_id",
        )
        for value in (getattr(scope, name),)
    }


def _wire_identity(identity: WireIdentity) -> dict[str, object]:
    return {
        "role": identity.role.value,
        "principal_ref": identity.principal_ref,
        "actor_ref": identity.actor_ref,
        "delegate_ref": identity.delegate_ref,
    }


def _config():
    from bt_api_py.ctp_gateway_composition import _service_config

    return parse_service_config(_service_config("tcp://127.0.0.1:50101", "tcp://127.0.0.1:50102"))


def _bundle(config=None, *, profile=_PROFILE):
    from bt_api_py.ctp_account_process import ctp_simnow_gateway_refs

    config = config or _config()
    refs = ctp_simnow_gateway_refs(profile)
    server_public, server_secret = zmq.curve_keypair()
    client_public, _client_secret = zmq.curve_keypair()
    service_identity = WireIdentity(WireRole.SERVICE, "principal:market-service", "actor:market-service")
    service_scope = WireScope(_DOMAIN)
    client_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)
    market_scope = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
    instance_id = config.components["gateway"]["settings"]["instance_id"]
    scope_ref = config.scope_ref
    instrument = MarketInstrumentKey("ctp", "SHFE", "demo", "future", "rb2701", "rb2701")
    key = MarketSubscriptionKey(
        refs.market_source_ref,
        refs.market_entitlement_ref,
        instrument,
        "tick",
    )
    authority = {
        "identity": _wire_identity(service_identity),
        "scope": _wire_scope(service_scope),
        "scope_ref": scope_ref,
        "scope_version": "market-scope-v1",
        "security_ref": "operator-gateway-security-v1",
        "security_version": "market-security-v1",
        "registration_policy_ref": config.registration_policy_ref,
        "registration_policy_version": "market-policy-v1",
        "service_requirements_ref": config.service_requirements_ref,
        "service_requirements_version": "market-requirements-v1",
        "curve_public_key": server_public.decode("ascii"),
        "curve_secret_key": server_secret.decode("ascii"),
    }
    resource = {
        "instance_id": instance_id,
        "port": "service.market_source",
        "scope_ref": scope_ref,
        "scope": _wire_scope(market_scope),
        "operations": sorted(_OPERATIONS),
    }
    client = {
        "identity": _wire_identity(client_identity),
        "curve_public_key": client_public.decode("ascii"),
        "resources": [resource],
        # The market loader must not use this trading/direct grant projection.
        "symbols": ["ignored-direct-symbol"],
    }
    raw = {
        "schema_version": 1,
        "registry_path": "C:/synthetic/market-registry.sqlite3",
        "authority": authority,
        "clients": [client],
        "market": {
            "subscriptions": [key.to_dict()],
            "principal_allowed_indices": {_PRINCIPAL: [0]},
        },
        "credentials": {
            "broker_id": "synthetic-broker",
            "user_id": "synthetic-user",
            "password": "synthetic-password",
            "app_id": "must-not-be-consumed",
            "auth_code": "must-not-be-consumed",
            "td_front": "tcp://untrusted.invalid:1",
            "md_front": "tcp://untrusted.invalid:2",
        },
        # Existing protected trading sections may be present, but this role
        # deliberately does not project or construct any of them.
        "settings": {"write_enabled": True, "trade_front": "untrusted"},
        "controls": {"should_not_be_used": True},
    }
    return config, raw, key, service_identity


def _fake_protected_bundle(monkeypatch, raw):
    seen_paths: list[object] = []

    def read(path):
        seen_paths.append(path)
        return raw

    monkeypatch.setenv("BT_API_CTP_GATEWAY_RESOURCES_FILE", "C:/synthetic/protected.json")
    monkeypatch.setattr("bt_api_py.ctp_gateway_local_profile._protected_json", read)
    return seen_paths


def _copy_config(config, *, role=None, ports=None, extra_component=None, clients=None):
    clone = copy.copy(config)
    components = {name: dict(value) for name, value in config.components.items()}
    gateway = components["gateway"]
    settings = dict(gateway["settings"])
    options = dict(settings["options"])
    if role is not None:
        options["role"] = role
    settings["options"] = options
    if ports is not None:
        settings["bind_ports"] = tuple(ports)
    gateway["settings"] = settings
    if extra_component is not None:
        components[extra_component] = {}
    object.__setattr__(clone, "components", MappingProxyType(components))
    if clients is not None:
        object.__setattr__(clone, "clients", MappingProxyType(clients))
    return clone


def test_market_observe_entrypoint_is_a_market_hub_factory():
    import tomllib

    project = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(
        encoding="utf-8"))
    group = project["project"]["entry-points"]["bt_api_py.gateway_operator_profile_factories"]
    assert group["ctp-simnow-market-observe"] == (
        "bt_api_py.ctp_gateway_local_profile:simnow_market_observe_profile_factory")
    assert simnow_market_observe_profile_factory.profile_ref == "ctp-simnow-market-observe"
    assert simnow_market_observe_profile_factory.role == "market_hub"
    assert group["ctp-simnow-7x24-market-observe"] == (
        "bt_api_py.ctp_gateway_local_profile:simnow_7x24_market_observe_profile_factory")
    assert simnow_7x24_market_observe_profile_factory.profile_ref == (
        "ctp-simnow-7x24-market-observe")
    assert simnow_7x24_market_observe_profile_factory.role == "market_hub"


def test_loader_projects_only_market_acl_and_three_md_credentials(monkeypatch):
    config, raw, key, _service_identity = _bundle()
    seen_paths = _fake_protected_bundle(monkeypatch, raw)

    resources = load_simnow_market_observe_resources(config)

    assert seen_paths == ["C:/synthetic/protected.json"]
    assert (resources.broker_id, resources.user_id, resources.password) == (
        "synthetic-broker", "synthetic-user", "synthetic-password")
    assert resources.key_universe == (key,)
    assert resources.principal_allowed_keys == {_PRINCIPAL: frozenset({key})}
    assert resources.authority.grants[_PRINCIPAL][0].resources[0].port == "service.market_source"
    assert "synthetic-password" not in repr(resources)
    assert "must-not-be-consumed" not in repr(resources)
    assert not hasattr(resources, "settings")
    assert not hasattr(resources, "request_feed_factory")
    assert not hasattr(resources, "account_read_settings")


@pytest.mark.parametrize(
    ("profile", "loader", "deployment_factory", "expected_source", "expected_entitlement", "expected_fronts"),
    [
        (
            _PROFILE,
            load_simnow_market_observe_resources,
            create_simnow_market_observe_deployment,
            "ctp-simnow-profile-set1-group1-vpn-market-v1",
            "ctp-simnow-profile-set1-group1-vpn-tick-v1",
            ("tcp://182.254.243.31:30001", "tcp://182.254.243.31:30011"),
        ),
        (
            _PROFILE_7X24,
            load_simnow_7x24_market_observe_resources,
            create_simnow_7x24_market_observe_deployment,
            "ctp-simnow-set2-7x24-market-v1",
            "ctp-simnow-set2-7x24-quote-v2-v1",
            ("tcp://182.254.243.31:40001", "tcp://182.254.243.31:40011"),
        ),
    ],
)
def test_factory_builds_only_lazy_market_source_and_fixed_md_profile(
    monkeypatch, profile, loader, deployment_factory, expected_source, expected_entitlement,
    expected_fronts,
):
    config, raw, key, _service_identity = _bundle(profile=profile)
    _fake_protected_bundle(monkeypatch, raw)
    resources = loader(config)
    assert resources.key_universe == (key,)
    assert key.source_ref == expected_source
    assert key.entitlement_ref == expected_entitlement
    deployment = deployment_factory(config)

    assert deployment.gateway_role == "market_hub"
    assert deployment.state == "new"
    assert deployment._session_owner is None
    assert deployment._account_session is None
    binding = deployment._market_binding
    assert binding.key_universe == (key,)
    assert binding.history_key_universe == ()
    assert binding.history_provider_factory is None

    provider_calls = []
    stream_calls = []

    class FakeProvider:
        def __init__(self, **kwargs):
            provider_calls.append(kwargs)

    class FakeStream:
        def __init__(self, **kwargs):
            stream_calls.append(kwargs)

    provider_module = ModuleType("bt_api_ctp.market_provider")
    provider_module.CtpQuoteV2MarketProvider = FakeProvider
    stream_module = ModuleType("bt_api_ctp.feeds.live_ctp_feed")
    stream_module.CtpMarketStream = FakeStream
    monkeypatch.setitem(sys.modules, "bt_api_ctp.market_provider", provider_module)
    monkeypatch.setitem(sys.modules, "bt_api_ctp.feeds.live_ctp_feed", stream_module)

    assert provider_calls == []  # binding construction does not open the MD provider
    provider = binding.provider_factory()
    assert type(provider) is FakeProvider
    kwargs = provider_calls[0]
    assert kwargs["source_ref"] == key.source_ref
    assert kwargs["entitlement_ref"] == key.entitlement_ref
    kwargs["stream_factory"](key, object())
    assert len(stream_calls) == 1
    stream = stream_calls[0]
    assert (stream["broker_id"], stream["user_id"], stream["password"]) == (
        "synthetic-broker", "synthetic-user", "synthetic-password")
    assert (stream["td_front"], stream["md_front"]) == expected_fronts
    assert stream["ctp_env_profile"] == profile
    assert stream["require_ctp_profile"] == profile
    assert stream["auto_detect_fronts"] is False
    assert "app_id" not in stream and "auth_code" not in stream
    assert "untrusted.invalid" not in repr(stream)


@pytest.mark.parametrize(
    ("profile", "creator"),
    [
        (_PROFILE, create_simnow_market_observe_deployment),
        (_PROFILE_7X24, create_simnow_7x24_market_observe_deployment),
    ],
)
def test_market_observe_provider_rejects_a_front_pair_outside_its_static_profile(
    monkeypatch, profile, creator
):
    config, raw, _key, _service_identity = _bundle(profile=profile)
    _fake_protected_bundle(monkeypatch, raw)
    deployment = creator(config)

    class FakeProvider:
        def __init__(self, **_kwargs):
            raise AssertionError("a mismatched official front pair must not construct a provider")

    class FakeStream:
        def __init__(self, **_kwargs):
            raise AssertionError("a mismatched official front pair must not construct a stream")

    provider_module = ModuleType("bt_api_ctp.market_provider")
    provider_module.CtpQuoteV2MarketProvider = FakeProvider
    stream_module = ModuleType("bt_api_ctp.feeds.live_ctp_feed")
    stream_module.CtpMarketStream = FakeStream
    monkeypatch.setitem(sys.modules, "bt_api_ctp.market_provider", provider_module)
    monkeypatch.setitem(sys.modules, "bt_api_ctp.feeds.live_ctp_feed", stream_module)

    from bt_api_ctp import ctp_env_selector

    observed = []

    def wrong_pair(name):
        observed.append(name)
        return ("tcp://198.51.100.1:1", "tcp://198.51.100.1:2")

    monkeypatch.setattr(ctp_env_selector, "official_simnow_fronts", wrong_pair)
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        deployment._market_binding.provider_factory()
    assert observed == [profile]


@pytest.mark.parametrize(
    ("rejected_profile", "loader"),
    [
        (_PROFILE_7X24, load_simnow_market_observe_resources),
        (_PROFILE, load_simnow_7x24_market_observe_resources),
    ],
)
def test_market_observe_profile_rejects_the_other_profiles_key_references(
    monkeypatch, rejected_profile, loader
):
    config, raw, _key, _service_identity = _bundle(profile=rejected_profile)
    _fake_protected_bundle(monkeypatch, raw)

    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        loader(config)


@pytest.mark.parametrize(
    "mutation",
    [
        {"role": "trading_owner"},
        {"role": "account_session_worker"},
        {"ports": ("service.market_source", "service.direct_write")},
        {"ports": ("service.market_source", "service.account_read")},
        {"ports": ("service.market_source", "service.provider_session")},
        {"extra_component": "risk"},
        {"clients": {"unexpected": object()}},
    ],
)
def test_loader_rejects_td_account_and_nonmarket_service_roles_before_resource_read(
    monkeypatch, mutation
):
    config, raw, _key, _service_identity = _bundle()
    config = _copy_config(config, **mutation)
    reads = _fake_protected_bundle(monkeypatch, raw)

    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_market_observe_resources(config)
    assert reads == []


@pytest.mark.parametrize(
    "change",
    [
        lambda key: key.update(source_ref="wrong-source"),
        lambda key: key.update(entitlement_ref="wrong-entitlement"),
        lambda key: key.update(channel="depth"),
        lambda key: key.update(depth=5),
        lambda key: key.update(timeframe="1m"),
        lambda key: key["instrument"].update(provider="other"),
        lambda key: key["instrument"].update(environment="simnow"),
        lambda key: key["instrument"].update(instrument_id="rb*"),
        lambda key: key["instrument"].update(native_symbol="rb*"),
        lambda key: key["instrument"].update(instrument_id="rb2702"),
    ],
)
def test_loader_rejects_nonexact_or_nonfinite_market_keys(monkeypatch, change):
    config, raw, _key, _service_identity = _bundle()
    changed = json.loads(json.dumps(raw))
    change(changed["market"]["subscriptions"][0])
    _fake_protected_bundle(monkeypatch, changed)

    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_market_observe_resources(config)


@pytest.mark.parametrize(
    "indices",
    [
        {},
        {_PRINCIPAL: []},
        {_PRINCIPAL: [True]},
        {_PRINCIPAL: [1]},
        {_PRINCIPAL: [0, 0]},
        {_PRINCIPAL: [0], "*": [0]},
    ],
)
def test_loader_rejects_wildcard_or_invalid_market_acl(monkeypatch, indices):
    config, raw, _key, _service_identity = _bundle()
    changed = json.loads(json.dumps(raw))
    changed["market"]["principal_allowed_indices"] = indices
    _fake_protected_bundle(monkeypatch, changed)

    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_market_observe_resources(config)


def test_loader_rejects_nonmarket_client_grants_and_history_configuration(monkeypatch):
    config, raw, _key, _service_identity = _bundle()
    changed = json.loads(json.dumps(raw))
    changed["clients"][0]["resources"][0]["port"] = "service.account_read"
    _fake_protected_bundle(monkeypatch, changed)
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_market_observe_resources(config)

    changed = json.loads(json.dumps(raw))
    changed["market"]["history_provider"] = "unexpected"
    _fake_protected_bundle(monkeypatch, changed)
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_market_observe_resources(config)


def test_market_profile_constructor_is_lazy_and_never_starts_a_service(monkeypatch):
    config, raw, _key, _service_identity = _bundle()
    _fake_protected_bundle(monkeypatch, raw)
    deployment = simnow_market_observe_profile_factory.create_deployment(config)
    assert deployment.state == "new"
    assert deployment.gateway_role == "market_hub"
