"""Trusted CTP resource injection for configured Gateway operator profiles.

Installers register the returned profile factory as an entry point. The loader
is trusted application code: public YAML neither instantiates it nor grants
permission. No CTP session, key or journal is opened by this module at import.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from bt_api_py.ctp_gateway_trading import (
    CtpGatewayAccountReadSettings,
    CtpGatewayMarketSettings,
    CtpGatewayReturnSettings,
    CtpGatewayTradingSettings,
    build_ctp_gateway_trading_profile,
    reconcile_ctp_gateway_account_session,
)
from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant
from bt_api_py.gateway_operator import GatewayOperatorDeployment
from bt_api_py.gateway_operator_application import GatewayOperatorProfileFactory
from bt_api_py.service_config import ServiceConfig
from bt_api_py.service_host import TrustedServiceAuthority


@dataclass(frozen=True, slots=True)
class CtpGatewayTrustedResources:
    """Resources resolved explicitly by installed operator code on the owner thread."""

    authority: TrustedServiceAuthority = field(repr=False)
    registry_path: str | Path = field(repr=False)
    settings: CtpGatewayTradingSettings = field(repr=False)
    grants: Mapping[str, GatewayDirectGrant] = field(repr=False)
    request_feed_factory: Callable[..., object] = field(repr=False)
    market_stream_factory: Callable[..., object] = field(repr=False)
    assert_admitted: Callable[[object], object] = field(repr=False)
    return_settings: CtpGatewayReturnSettings | None = field(default=None, repr=False)
    account_read_settings: CtpGatewayAccountReadSettings | None = field(default=None, repr=False)
    account_read_principals: frozenset[str] = field(default_factory=frozenset, repr=False)
    market_settings: CtpGatewayMarketSettings | None = field(default=None, repr=False)
    control_grants: Mapping[str, GatewayControlGrant] | None = field(default=None, repr=False)
    upgrade_controls_from_v9: bool = False
    upgrade_strategy_ledger_from_v10: bool = False
    upgrade_account_extensions_from_v11: bool = False
    clock_verifier_factory: Callable[[], object] | None = field(default=None, repr=False)
    clock_allowed_servers: frozenset[str] | None = field(default=None, repr=False)
    clock_refresh_settings: object | None = field(default=None, repr=False)
    trusted_external_classifier: Callable[..., object] | None = field(default=None, repr=False)
    required_monitor: object | None = field(default=None, repr=False)
    lease_drain_settings: object | None = field(default=None, repr=False)
    delegation_settings: object | None = field(default=None, repr=False)
    parent_settings: object | None = field(default=None, repr=False)
    parent_authority_factory: Callable[..., object] | None = field(default=None, repr=False)
    upgrade_parent_authority_from_v12: bool = False
    upgrade_account_facts_financial_from_v13: bool = False
    upgrade_actor_stream_from_v14: bool = False
    upgrade_strategy_origin_from_v15: bool = False
    strategy_origin_source_factory: Callable[..., object] | None = field(default=None, repr=False)
    strategy_financial_source_factory: Callable[..., object] | None = field(default=None, repr=False)
    upgrade_control_cancel_from_v16: bool = False
    upgrade_account_fact_recovery_from_v17: bool = False
    upgrade_account_event_recovery_from_v18: bool = False
    strategy_ledger_external_baseline: object | None = field(default=None, repr=False)
    strategy_ledger_comparison_refresh_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.grants, Mapping):
            raise ValueError("CTP_TRUSTED_RESOURCES_INVALID")
        object.__setattr__(self, "grants", MappingProxyType(dict(self.grants)))
        if self.control_grants is not None:
            if not isinstance(self.control_grants, Mapping):
                raise ValueError("CTP_TRUSTED_RESOURCES_INVALID")
            object.__setattr__(self, "control_grants", MappingProxyType(dict(self.control_grants)))


def create_ctp_gateway_profile_factory(
    profile_ref: str,
    *,
    resource_loader: Callable[[ServiceConfig], CtpGatewayTrustedResources],
) -> GatewayOperatorProfileFactory:
    """Bind an installed factory to a trusted loader without resolving it yet."""
    if not callable(resource_loader):
        raise ValueError("CTP_TRUSTED_RESOURCES_INVALID")

    def create(config: ServiceConfig) -> GatewayOperatorDeployment:
        resources = resource_loader(config)
        if type(resources) is not CtpGatewayTrustedResources:
            raise ValueError("CTP_TRUSTED_RESOURCES_INVALID")
        profile = build_ctp_gateway_trading_profile(
            config,
            resources.authority,
            resources.registry_path,
            profile_ref=profile_ref,
            settings=resources.settings,
            grants=resources.grants,
            request_feed_factory=resources.request_feed_factory,
            market_stream_factory=resources.market_stream_factory,
            assert_admitted=resources.assert_admitted,
            return_settings=resources.return_settings,
            account_read_settings=resources.account_read_settings,
            account_read_principals=resources.account_read_principals,
            market_settings=resources.market_settings,
            control_grants=resources.control_grants,
            upgrade_controls_from_v9=resources.upgrade_controls_from_v9,
            upgrade_strategy_ledger_from_v10=resources.upgrade_strategy_ledger_from_v10,
            upgrade_account_extensions_from_v11=resources.upgrade_account_extensions_from_v11,
            clock_verifier_factory=resources.clock_verifier_factory,
            clock_allowed_servers=resources.clock_allowed_servers,
            clock_refresh_settings=resources.clock_refresh_settings,
            trusted_external_classifier=resources.trusted_external_classifier,
            required_monitor=resources.required_monitor,
            lease_drain_settings=resources.lease_drain_settings,
            delegation_settings=resources.delegation_settings,
            parent_settings=resources.parent_settings,
            parent_authority_factory=resources.parent_authority_factory,
            upgrade_parent_authority_from_v12=resources.upgrade_parent_authority_from_v12,
            upgrade_account_facts_financial_from_v13=resources.upgrade_account_facts_financial_from_v13,
            upgrade_actor_stream_from_v14=resources.upgrade_actor_stream_from_v14,
            upgrade_strategy_origin_from_v15=resources.upgrade_strategy_origin_from_v15,
            strategy_origin_source_factory=resources.strategy_origin_source_factory,
            strategy_financial_source_factory=resources.strategy_financial_source_factory,
            upgrade_control_cancel_from_v16=resources.upgrade_control_cancel_from_v16,
            upgrade_account_fact_recovery_from_v17=resources.upgrade_account_fact_recovery_from_v17,
            upgrade_account_event_recovery_from_v18=resources.upgrade_account_event_recovery_from_v18,
            strategy_ledger_external_baseline=resources.strategy_ledger_external_baseline,
            strategy_ledger_comparison_refresh_seconds=(
                resources.strategy_ledger_comparison_refresh_seconds),
        )
        return profile.deployment_factory()

    def reconcile(config: ServiceConfig, operator_ref: str, reason: str) -> Mapping[str, object]:
        resources = resource_loader(config)
        if type(resources) is not CtpGatewayTrustedResources:
            raise ValueError("CTP_TRUSTED_RESOURCES_INVALID")
        return reconcile_ctp_gateway_account_session(
            resources.settings,
            resources.registry_path,
            resources.account_read_settings,
            operator_ref=operator_ref,
            reason=reason,
        )

    return GatewayOperatorProfileFactory(
        profile_ref, "trading_owner", create, reconcile_session=reconcile
    )
