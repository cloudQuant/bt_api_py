"""Trusted-code composition for one CTP Gateway trading-owner profile.

Factories are installed-code closures, never imported from a manifest. This
module creates no session or native resource while building a profile. The
account owner thread alone owns the CTP session, guard and dispatcher. The
local guard is not a cross-host fence or proof of external account state.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bt_api_base.account_protocol import AccountKey, AccountResourceKey
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
from bt_api_py.gateway_direct_service import (
    ACCOUNT_READ_OPERATIONS,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
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
_READ_OPERATIONS = ACCOUNT_READ_OPERATIONS


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


@dataclass(frozen=True, slots=True)
class CtpGatewayAccountReadSettings:
    journal_path: str | Path
    state_scope: str
    required_coverage: tuple[AccountResourceKey, ...]
    refresh_interval_seconds: float = 1.0
    max_staleness_seconds: float = 5.0
    request_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if (
            not _absolute(self.journal_path)
            or not _ref(self.state_scope)
            or type(self.required_coverage) is not tuple
            or not self.required_coverage
            or any(type(item) is not AccountResourceKey for item in self.required_coverage)
            or len(set(self.required_coverage)) != len(self.required_coverage)
            or not all(
                _duration(item, minimum=0.05, maximum=300)
                for item in (
                    self.refresh_interval_seconds,
                    self.max_staleness_seconds,
                    self.request_timeout_seconds,
                )
            )
        ):
            raise CtpGatewayTradingError("INVALID_ACCOUNT_READ_SETTINGS")


def _distinct_journals(left: str | Path, right: str | Path) -> bool:
    try:
        if Path(left).resolve() == Path(right).resolve():
            return False
        return not (
            os.path.exists(left) and os.path.exists(right) and os.path.samefile(left, right)
        )
    except (OSError, ValueError):
        return False


def _validate_config(
    config: ServiceConfig,
    authority: TrustedServiceAuthority,
    settings: CtpGatewayTradingSettings,
    grants: Mapping[str, GatewayDirectGrant],
    read_settings: CtpGatewayAccountReadSettings | None,
    read_principals: frozenset[str],
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
            or frozenset(item["bind_ports"])
            != (
                frozenset({"service.direct_write", "service.account_read"})
                if read_settings is not None
                else frozenset({"service.direct_write"})
            )
            or len(item["bind_ports"]) != len(set(item["bind_ports"]))
            or dict(options) != {"role": "trading_owner", "dispatch_enabled": True}
            or not _ref(instance)
            or config.clients
            or config.service_id != authority.identity.actor_ref
            or config.scope_ref != authority.scope_ref
            or config.registration_policy_ref != authority.registration_policy_ref
            or config.service_requirements_ref != authority.service_requirements_ref
            or not isinstance(grants, Mapping)
            or not grants
            or set(grants) | set(read_principals) != set(authority.principals.values())
            or set(grants) | set(read_principals) != set(authority.grants)
        ):
            raise ValueError
        for principal in set(grants) | set(read_principals):
            grant = grants.get(principal)
            if not _ref(principal) or (grant is not None and type(grant) is not GatewayDirectGrant):
                raise ValueError
            entries = authority.grants[principal]
            if not entries or len({entry.identity for entry in entries}) != 1:
                raise ValueError
            for entry in entries:
                if (
                    type(entry) is not PrincipalGrant
                    or entry.identity.role
                    not in {WireRole.STRATEGY_CLIENT, WireRole.APPLICATION_CLIENT}
                    or entry.identity.principal_ref != principal
                    or entry.identity.delegate_ref is not None
                    or not entry.resources
                ):
                    raise ValueError
                for resource in entry.resources:
                    scope = resource.scope
                    if (
                        type(resource) is not ResourceGrant
                        or type(scope) is not WireScope
                        or resource.instance_id != instance
                        or resource.port not in {"service.direct_write", "service.account_read"}
                        or resource.scope_ref != config.scope_ref
                        or scope.execution_domain_ref != authority.scope.execution_domain_ref
                        or scope.account_ref != settings.account_scope
                        or scope.account_refs
                        or scope.portfolio_ref is not None
                        or scope.manual_book_ref is not None
                        or scope.group_id is not None
                        or scope.leg_id is not None
                    ):
                        raise ValueError
                    if resource.port == "service.direct_write":
                        if (
                            grant is None
                            or entry.identity.role is not WireRole.STRATEGY_CLIENT
                            or entry.identity.actor_ref != grant.strategy_ref
                            or scope.strategy_ref != grant.strategy_ref
                            or not resource.operations <= _OPERATIONS
                        ):
                            raise ValueError
                    elif (
                        principal not in read_principals
                        or read_settings is None
                        or not resource.operations <= _READ_OPERATIONS
                        or scope.strategy_ref
                        != (
                            entry.identity.actor_ref
                            if entry.identity.role is WireRole.STRATEGY_CLIENT
                            else None
                        )
                    ):
                        raise ValueError
            ports = {resource.port for entry in entries for resource in entry.resources}
            if (principal in grants) != ("service.direct_write" in ports) or (
                principal in read_principals
            ) != ("service.account_read" in ports):
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
    account_read_settings: CtpGatewayAccountReadSettings | None = None,
    account_read_principals: frozenset[str] = frozenset(),
) -> GatewayOperatorProfile:
    """Build a trusted profile; no guard, provider, native session or owner is started."""
    if type(settings) is not CtpGatewayTradingSettings or not _absolute(registry_path):
        raise CtpGatewayTradingError("INVALID_TRADING_SETTINGS")
    if not all(
        callable(item) for item in (request_feed_factory, market_stream_factory, assert_admitted)
    ):
        raise CtpGatewayTradingError("INVALID_TRUSTED_FACTORY")
    if (
        type(account_read_principals) is not frozenset
        or any(not _ref(item) for item in account_read_principals)
        or (account_read_settings is None) != (not account_read_principals)
        or (
            account_read_settings is not None
            and (
                type(account_read_settings) is not CtpGatewayAccountReadSettings
                or not _distinct_journals(settings.journal_path, account_read_settings.journal_path)
            )
        )
    ):
        raise CtpGatewayTradingError("INVALID_ACCOUNT_READ_SETTINGS")
    instance = _validate_config(
        config, authority, settings, grants, account_read_settings, account_read_principals
    )
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
            guard = session = read_model = None
            session_closed = False
            guard_closed = False
            read_closed = False

            def cleanup():
                nonlocal session_closed, guard_closed, read_closed
                if read_model is not None and not read_closed:
                    read_model.close()
                    read_closed = True
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

                if account_read_settings is not None:
                    from bt_api_gateway.account_read_model import GatewayAccountReadModel
                    from bt_api_gateway.event_journal import GatewayEventJournal

                    if not _distinct_journals(
                        settings.journal_path, account_read_settings.journal_path
                    ):
                        raise CtpGatewayTradingError("INVALID_ACCOUNT_READ_SETTINGS")
                    read_journal = GatewayEventJournal(account_read_settings.journal_path)
                    account_binding = session.account_binding
                    native_generation = provider.session_generation()

                    def assert_current_generation(candidate):
                        dispatcher.assert_owned()
                        session.refresh_session()
                        if (
                            candidate != generation
                            or session.account_binding != account_binding
                            or provider.session_generation() != native_generation
                        ):
                            raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")

                    read_model = GatewayAccountReadModel(
                        read_journal,
                        account_key=settings.account_key,
                        journal_account_scope=settings.account_scope,
                        state_scope=account_read_settings.state_scope,
                        required_coverage=account_read_settings.required_coverage,
                        assert_current_generation=assert_current_generation,
                        refresh_interval_seconds=account_read_settings.refresh_interval_seconds,
                        max_staleness_seconds=account_read_settings.max_staleness_seconds,
                        request_timeout_seconds=account_read_settings.request_timeout_seconds,
                        require_open_orders=True,
                    )
                    read_model.start_generation(generation)
                    holder["read_model"] = read_model
                    holder["read_journal"] = read_journal
                next_read = 0.0

                def maintain():
                    nonlocal next_read
                    try:
                        session.refresh_session()
                        if read_model is not None:
                            assert_current_generation(generation)
                        controller.tick()
                    except BaseException:
                        if read_model is not None:
                            read_model.invalidate()
                        raise
                    if read_model is not None and time.monotonic() >= next_read:
                        token = read_model.begin_refresh()
                        try:
                            observation = session.read_account(
                                generation=generation,
                                timeout=account_read_settings.request_timeout_seconds,
                            )
                        except Exception:
                            read_model.fail_refresh(token)
                            assert_current_generation(generation)
                            session.refresh_session()
                        else:
                            try:
                                read_model.finish_refresh(token, observation)
                            except Exception:
                                assert_current_generation(generation)
                                session.refresh_session()
                        finally:
                            next_read = (
                                time.monotonic() + account_read_settings.refresh_interval_seconds
                            )

                return GatewayDirectOwnerResources(
                    dispatcher,
                    journal,
                    cleanup,
                    maintain,
                    min(
                        settings.maintenance_interval_seconds,
                        account_read_settings.refresh_interval_seconds,
                    )
                    if account_read_settings is not None
                    else settings.maintenance_interval_seconds,
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
            values = {"binding": binding}
            if account_read_settings is not None:
                from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
                from bt_api_gateway.service_component import (
                    GatewayReadService,
                    GatewayServiceAccountScope,
                )

                principals = {}
                strategy_refs = set()
                for principal in account_read_principals:
                    identity = authority.grants[principal][0].identity
                    if identity.role is WireRole.STRATEGY_CLIENT:
                        strategy_refs.add(identity.actor_ref)
                    principals[principal] = GatewayPrincipal(
                        principal,
                        frozenset({settings.account_scope}),
                        frozenset({account_read_settings.state_scope}),
                        frozenset({GatewayCommandKind.READ}),
                    )
                account = GatewayServiceAccountScope(
                    account_ref=settings.account_scope,
                    execution_domain_ref=authority.scope.execution_domain_ref,
                    journal_account_scope=settings.account_scope,
                    account_read_strategy_scope=account_read_settings.state_scope,
                    shared_account_facts_scope=account_read_settings.state_scope
                    if strategy_refs
                    else None,
                    shared_fact_strategy_refs=frozenset(strategy_refs),
                )
                values["read_service"] = GatewayReadService(
                    holder["read_journal"],
                    accounts={settings.account_scope: account},
                    principals=principals,
                    state_worker=holder["read_model"],
                )
            return ComponentResources.owned(values, ready=ready, close=close_owner)
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
