"""Trusted-code composition for one CTP Gateway trading-owner profile.

Factories are installed-code closures, never imported from a manifest. This
module creates no session or native resource while building a profile. The
account owner thread alone owns the CTP session, guard and dispatcher. The
local guard is not a cross-host fence or proof of external account state.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bt_api_base.account_protocol import AccountKey
from bt_api_base.service_protocol import WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_direct_dispatch import compose_gateway_direct_dispatcher
from bt_api_py.gateway_direct_owner import (
    GatewayDirectOwner,
    GatewayDirectOwnerResources,
    GatewayOwnerStartupCleanupError,
)
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.gateway_operator import GatewayOperatorDeployment
from bt_api_py.gateway_operator_application import GatewayOperatorProfile
from bt_api_py.gateway_settlement import GatewaySettlementController
from bt_api_py.service_components import ComponentResources, ServiceResourceConstructionError
from bt_api_py.service_config import ServiceConfig
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.write_admission import WriteAdmission

ROUTE = "CTP___FUTURE"
_OPERATIONS = frozenset(
    {"direct.reserve", "direct.release", "direct.command_status", "direct.dispatch"}
)


class CtpGatewayTradingError(RuntimeError):
    """Finite composition/cleanup error without account, path or secret values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _ref(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


def _absolute(value: object) -> bool:
    try:
        return isinstance(value, (str, os.PathLike)) and os.path.isabs(os.fspath(value))
    except (TypeError, ValueError):
        return False


def _duration(value: object, *, minimum: float = 0.001, maximum: float = 3600) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


@dataclass(frozen=True, slots=True)
class CtpGatewayTradingSettings:
    account_key: AccountKey = field(repr=False)
    account_scope: str
    guard_root: str | Path
    owner_id: str
    journal_path: str | Path
    expected_journal_id: str
    minimum_revision: int
    trusted_flow_root: str | Path
    market_source_ref: str
    market_entitlement_ref: str
    write_enabled: bool = False
    startup_timeout_seconds: float = 30.0
    close_timeout_seconds: float = 5.0
    connect_timeout_seconds: float = 30.0
    session_stop_timeout_seconds: float = 3.0
    maintenance_interval_seconds: float = 1.0
    queue_size: int = 128
    max_queue_wait_seconds: float = 30.0
    busy_timeout_seconds: float = 30.0
    session_queue_capacity: int = 1024
    journal_max_records: int = 100000
    journal_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        key = self.account_key
        if (
            type(key) is not AccountKey
            or (key.provider, key.venue, key.environment) != ("ctp", "simnow", "simnow")
            or not _ref(key.account_id)
            or not _ref(key.subaccount_id)
            or self.account_scope != key.account_key_id
            or not all(
                _ref(item)
                for item in (
                    self.account_scope,
                    self.owner_id,
                    self.expected_journal_id,
                    self.market_source_ref,
                    self.market_entitlement_ref,
                )
            )
            or not all(
                _absolute(path)
                for path in (self.guard_root, self.journal_path, self.trusted_flow_root)
            )
            or type(self.minimum_revision) is not int
            or self.minimum_revision < 0
            or type(self.write_enabled) is not bool
            or type(self.queue_size) is not int
            or not 1 <= self.queue_size <= 10000
            or type(self.session_queue_capacity) is not int
            or not 1 <= self.session_queue_capacity <= 65536
            or type(self.journal_max_records) is not int
            or not 1 <= self.journal_max_records <= 1000000
            or not all(
                _duration(item)
                for item in (
                    self.startup_timeout_seconds,
                    self.close_timeout_seconds,
                    self.maintenance_interval_seconds,
                    self.max_queue_wait_seconds,
                    self.busy_timeout_seconds,
                )
            )
            or not _duration(self.connect_timeout_seconds, minimum=0.05, maximum=300)
            or not _duration(self.session_stop_timeout_seconds, minimum=0.05, maximum=30)
            or not _duration(self.journal_timeout_seconds, maximum=60)
        ):
            raise CtpGatewayTradingError("INVALID_TRADING_SETTINGS")


def _validate_config(
    config: ServiceConfig,
    authority: TrustedServiceAuthority,
    settings: CtpGatewayTradingSettings,
    grants: Mapping[str, GatewayDirectGrant],
) -> str:
    if type(config) is not ServiceConfig or type(authority) is not TrustedServiceAuthority:
        raise CtpGatewayTradingError("INVALID_TRADING_CONFIGURATION")
    try:
        config.to_public_dict()
        components = config.components
        item = components["gateway"]["settings"]
        options = item["options"]
        instance = item["instance_id"]
        if (
            set(components) != {"gateway"}
            or tuple(item["bind_ports"]) != ("service.direct_write",)
            or dict(options) != {"role": "trading_owner", "dispatch_enabled": True}
            or not _ref(instance)
            or config.clients
            or config.service_id != authority.identity.actor_ref
            or config.scope_ref != authority.scope_ref
            or config.registration_policy_ref != authority.registration_policy_ref
            or config.service_requirements_ref != authority.service_requirements_ref
            or not isinstance(grants, Mapping)
            or not grants
            or set(grants) != set(authority.principals.values())
            or set(grants) != set(authority.grants)
        ):
            raise ValueError
        for principal, grant in grants.items():
            if not _ref(principal) or type(grant) is not GatewayDirectGrant:
                raise ValueError
            entries = authority.grants[principal]
            if not entries:
                raise ValueError
            for entry in entries:
                if (
                    type(entry) is not PrincipalGrant
                    or entry.identity.role is not WireRole.STRATEGY_CLIENT
                    or entry.identity.principal_ref != principal
                    or entry.identity.actor_ref != grant.strategy_ref
                    or not entry.resources
                ):
                    raise ValueError
                for resource in entry.resources:
                    scope = resource.scope
                    if (
                        type(resource) is not ResourceGrant
                        or type(scope) is not WireScope
                        or resource.instance_id != instance
                        or resource.port != "service.direct_write"
                        or resource.scope_ref != config.scope_ref
                        or scope.execution_domain_ref != authority.scope.execution_domain_ref
                        or scope.account_ref != settings.account_scope
                        or scope.strategy_ref != grant.strategy_ref
                        or scope.account_refs
                        or scope.portfolio_ref is not None
                        or scope.manual_book_ref is not None
                        or scope.group_id is not None
                        or scope.leg_id is not None
                        or not resource.operations <= _OPERATIONS
                    ):
                        raise ValueError
        return instance
    except CtpGatewayTradingError:
        raise
    except Exception:
        raise CtpGatewayTradingError("INVALID_TRADING_CONFIGURATION") from None


def _load_ctp_types():
    """Resolve fixed provider code, only when the owner starts."""
    from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
    from bt_api_ctp.gateway_session import CtpGatewaySession

    return CtpGatewaySession, CtpDirectWriteProvider


def build_ctp_gateway_trading_profile(
    config: ServiceConfig,
    authority: TrustedServiceAuthority,
    registry_path: str | Path,
    *,
    profile_ref: str,
    settings: CtpGatewayTradingSettings,
    grants: Mapping[str, GatewayDirectGrant],
    request_feed_factory: Callable[..., object],
    market_stream_factory: Callable[..., object],
    assert_admitted: Callable[[Any], object],
) -> GatewayOperatorProfile:
    """Build a trusted profile; no guard, provider, native session or owner is started."""
    if type(settings) is not CtpGatewayTradingSettings or not _absolute(registry_path):
        raise CtpGatewayTradingError("INVALID_TRADING_SETTINGS")
    if not all(
        callable(item) for item in (request_feed_factory, market_stream_factory, assert_admitted)
    ):
        raise CtpGatewayTradingError("INVALID_TRUSTED_FACTORY")
    instance = _validate_config(config, authority, settings, grants)
    static_grants = MappingProxyType(dict(grants))

    def resources(
        component: str, instance_id: str, options: Mapping[str, object], scope: WireScope
    ):
        if (
            component != "gateway"
            or instance_id != instance
            or dict(options) != {"role": "trading_owner", "dispatch_enabled": True}
            or type(scope) is not WireScope
            or scope != authority.scope
        ):
            raise CtpGatewayTradingError("INVALID_TRADING_CONFIGURATION")
        holder: dict[str, Any] = {}

        def owner_factory():
            guard = session = None
            session_closed = False
            guard_closed = False

            def cleanup():
                nonlocal session_closed, guard_closed
                if session is not None and not session_closed:
                    if session.close() is not True:
                        raise CtpGatewayTradingError("CTP_SESSION_CLOSE_UNCONFIRMED")
                    session_closed = True
                if guard is not None and not guard_closed:
                    guard.release()
                    guard_closed = True

            try:
                guard = LocalAccountSessionGuard(
                    Path(settings.guard_root), settings.account_scope, settings.owner_id
                )
                generation = guard.acquire()
                journal = GatewayDirectCommandJournal(
                    settings.journal_path,
                    settings.account_scope,
                    generation=generation,
                    expected_journal_id=settings.expected_journal_id,
                    minimum_revision=settings.minimum_revision,
                    max_records=settings.journal_max_records,
                    timeout_seconds=settings.journal_timeout_seconds,
                )
                session_type, provider_type = _load_ctp_types()
                session = session_type(
                    settings.account_key,
                    request_feed_factory,
                    market_stream_factory,
                    trusted_flow_root=settings.trusted_flow_root,
                    flow_account_scope=settings.account_scope,
                    source_ref=settings.market_source_ref,
                    entitlement_ref=settings.market_entitlement_ref,
                    connect_timeout_seconds=settings.connect_timeout_seconds,
                    stop_timeout_seconds=settings.session_stop_timeout_seconds,
                    queue_capacity=settings.session_queue_capacity,
                )
                session.connect(generation=generation, timeout=settings.connect_timeout_seconds)
                provider = session.direct_provider
                if type(provider) is not provider_type or not _ref(session.account_binding):
                    raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")
                admission = WriteAdmission({ROUTE: settings.write_enabled})
                coordinator = DirectWriteCoordinator(
                    admission, lambda route: provider if route == ROUTE else None
                )
                if coordinator.provider(ROUTE) is not provider:
                    raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")
                state: dict[str, GatewaySettlementController] = {}

                def gate(record):
                    return (
                        admission.is_enabled(ROUTE)
                        and state["controller"].ready
                        and assert_admitted(record) is True
                    )

                dispatcher = compose_gateway_direct_dispatcher(
                    coordinator,
                    ROUTE,
                    journal,
                    guard,
                    gate,
                    expected_local_provider=provider,
                )
                controller = GatewaySettlementController(
                    coordinator,
                    ROUTE,
                    expected_local_provider=provider,
                    expected_account_binding=session.account_binding,
                    journal=journal,
                    dispatcher=dispatcher,
                )
                state["controller"] = controller
                holder["journal"] = journal

                def maintain():
                    session.refresh_session()
                    controller.tick()

                return GatewayDirectOwnerResources(
                    dispatcher,
                    journal,
                    cleanup,
                    maintain,
                    settings.maintenance_interval_seconds,
                )
            except BaseException:
                raise GatewayOwnerStartupCleanupError(cleanup) from None

        owner = GatewayDirectOwner(
            owner_factory,
            queue_size=settings.queue_size,
            max_queue_wait_seconds=settings.max_queue_wait_seconds,
            busy_timeout_seconds=settings.busy_timeout_seconds,
        )

        def close_owner():
            if owner.close(settings.close_timeout_seconds) is not True:
                raise CtpGatewayTradingError("TRADING_OWNER_CLOSE_UNCONFIRMED")

        def ready():
            status = owner.status()
            return status.thread_alive and owner.binding() is not None

        try:
            if owner.start(settings.startup_timeout_seconds) is not True:
                raise CtpGatewayTradingError("TRADING_OWNER_START_FAILED")
            journal = holder["journal"]
            binding = GatewayDirectServiceBinding(journal, static_grants, owner=owner)
            return ComponentResources.owned({"binding": binding}, ready=ready, close=close_owner)
        except BaseException:
            partial = ComponentResources.owned(
                {"binding": None}, ready=lambda: False, close=close_owner
            )
            raise ServiceResourceConstructionError(partial) from None

    def deployment_factory():
        try:
            return GatewayOperatorDeployment(
                config, authority, registry_path, resource_provider=resources
            )
        except Exception:
            raise CtpGatewayTradingError("TRADING_DEPLOYMENT_INVALID") from None

    try:
        return GatewayOperatorProfile(profile_ref, "trading_owner", deployment_factory)
    except Exception:
        raise CtpGatewayTradingError("INVALID_TRADING_PROFILE") from None
