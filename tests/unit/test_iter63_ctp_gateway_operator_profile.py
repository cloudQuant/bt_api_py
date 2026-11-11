"""The configured CTP factory delegates to the existing trusted CTP builder."""
from __future__ import annotations

import pytest

from bt_api_py.ctp_gateway_operator_profile import (
    CtpGatewayTrustedResources,
    create_ctp_gateway_profile_factory,
)
from bt_api_py.gateway_operator import GatewayOperatorDeployment
from tests.test_iter63_ctp_gateway_trading import make


def test_real_ctp_builder_is_inert_until_trusted_factory_called(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    calls = []
    source_grants = dict(grants)
    resources = CtpGatewayTrustedResources(
        authority, tmp_path / "registry2.sqlite", settings, source_grants,
        lambda: None, lambda *_args: None, lambda _: True,
    )
    source_grants.clear()
    assert resources.grants == grants
    factory = create_ctp_gateway_profile_factory(
        "ctp-test", resource_loader=lambda cfg: calls.append(cfg) or resources
    )
    assert calls == []
    deployment = factory.create_deployment(config)
    assert type(deployment) is GatewayOperatorDeployment
    assert deployment.gateway_role == "trading_owner"
    assert deployment.state == "new"
    assert calls == [config]


def test_missing_trusted_resources_are_rejected_without_secret_values(tmp_path, monkeypatch):
    _, _, config, _, _, _ = make(tmp_path, monkeypatch)
    factory = create_ctp_gateway_profile_factory("ctp-test", resource_loader=lambda _: None)
    with pytest.raises(ValueError, match="CTP_TRUSTED_RESOURCES_INVALID"):
        factory.create_deployment(config)
    with pytest.raises(ValueError, match="CTP_TRUSTED_RESOURCES_INVALID"):
        create_ctp_gateway_profile_factory("ctp-test", resource_loader=None)


@pytest.mark.parametrize("field", ["upgrade_account_fact_recovery_from_v17", "upgrade_account_event_recovery_from_v18"])
def test_account_recovery_upgrade_requires_real_bool(tmp_path, monkeypatch, field):
    from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    resources = CtpGatewayTrustedResources(
        authority, tmp_path / "registry.sqlite", settings, grants,
        lambda: None, lambda *_args: None, lambda _: True, **{field: 1},
    )
    with pytest.raises(CtpGatewayTradingError, match="INVALID_CONTROL_CONFIGURATION"):
        create_ctp_gateway_profile_factory("ctp-test", resource_loader=lambda _: resources).create_deployment(config)


def test_trusted_lease_drain_settings_are_explicitly_forwarded(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import bt_api_py.ctp_gateway_operator_profile as module
    from bt_api_py.gateway_lease_drain import GatewayLeaseDrainSettings

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    option = GatewayLeaseDrainSettings(strategies=frozenset({"strategy-a"}), expiry_seconds=0, automatic_resume=True)
    resources = CtpGatewayTrustedResources(
        authority, tmp_path / "registry3.sqlite", settings, grants,
        lambda: None, lambda *_args: None, lambda _: True, lease_drain_settings=option,
    )
    seen = []
    marker = object()
    def builder(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(deployment_factory=lambda: marker)
    monkeypatch.setattr(module, "build_ctp_gateway_trading_profile", builder)
    factory = create_ctp_gateway_profile_factory("ctp-test", resource_loader=lambda _: resources)
    assert factory.create_deployment(config) is marker
    assert seen[0]["lease_drain_settings"] is option
    assert seen[0]["control_grants"] is None


def test_external_classifier_is_only_forwarded_from_trusted_resources(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import bt_api_py.ctp_gateway_operator_profile as module

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    classifier = lambda *_args: None
    resources = CtpGatewayTrustedResources(
        authority, tmp_path / "registry4.sqlite", settings, grants,
        lambda: None, lambda *_args: None, lambda _: True,
        upgrade_account_extensions_from_v11=True,
        trusted_external_classifier=classifier,
    )
    seen = []
    marker = object()
    def builder(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(deployment_factory=lambda: marker)
    monkeypatch.setattr(module, "build_ctp_gateway_trading_profile", builder)
    factory = create_ctp_gateway_profile_factory("ctp-test", resource_loader=lambda _: resources)
    assert factory.create_deployment(config) is marker
    assert seen[0]["upgrade_account_extensions_from_v11"] is True
    assert seen[0]["trusted_external_classifier"] is classifier


def test_delegation_settings_are_forwarded_only_from_trusted_resources(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import bt_api_py.ctp_gateway_operator_profile as module

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    delegation = object()  # The existing builder owns settings/grant validation.
    resources = CtpGatewayTrustedResources(
        authority, tmp_path / "registry5.sqlite", settings, grants,
        lambda: None, lambda *_args: None, lambda _: True, delegation_settings=delegation,
    )
    seen = []
    def builder(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(deployment_factory=lambda: None)
    monkeypatch.setattr(module, "build_ctp_gateway_trading_profile", builder)
    factory = create_ctp_gateway_profile_factory("ctp-test", resource_loader=lambda _: resources)
    factory.create_deployment(config)
    assert seen[0]["delegation_settings"] is delegation


def test_parent_authority_is_forwarded_only_from_trusted_resources(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import bt_api_py.ctp_gateway_operator_profile as module

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    parent_settings = object()
    parent_factory = lambda *_args: None
    clock_refresh = object()
    origin_factory = lambda *_args: None
    financial_factory = lambda *_args: None
    resources = CtpGatewayTrustedResources(
        authority, tmp_path / "registry6.sqlite", settings, grants,
        lambda: None, lambda *_args: None, lambda _: True,
        parent_settings=parent_settings, parent_authority_factory=parent_factory,
        upgrade_parent_authority_from_v12=True,
        upgrade_account_facts_financial_from_v13=True,
        clock_refresh_settings=clock_refresh,
        upgrade_actor_stream_from_v14=True,
        upgrade_strategy_origin_from_v15=True,
        strategy_origin_source_factory=origin_factory,
        strategy_financial_source_factory=financial_factory,
        upgrade_control_cancel_from_v16=True,
        upgrade_account_fact_recovery_from_v17=True,
        upgrade_account_event_recovery_from_v18=True,
    )
    seen = []
    def builder(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(deployment_factory=lambda: None)
    monkeypatch.setattr(module, "build_ctp_gateway_trading_profile", builder)
    create_ctp_gateway_profile_factory("ctp-test", resource_loader=lambda _: resources).create_deployment(config)
    assert seen[0]["parent_settings"] is parent_settings
    assert seen[0]["parent_authority_factory"] is parent_factory
    assert seen[0]["upgrade_parent_authority_from_v12"] is True
    assert seen[0]["upgrade_account_facts_financial_from_v13"] is True
    assert seen[0]["clock_refresh_settings"] is clock_refresh
    assert seen[0]["upgrade_actor_stream_from_v14"] is True
    assert seen[0]["upgrade_strategy_origin_from_v15"] is True
    assert seen[0]["strategy_origin_source_factory"] is origin_factory
    assert seen[0]["strategy_financial_source_factory"] is financial_factory
    assert seen[0]["upgrade_control_cancel_from_v16"] is True
    assert seen[0]["upgrade_account_fact_recovery_from_v17"] is True
    assert seen[0]["upgrade_account_event_recovery_from_v18"] is True
