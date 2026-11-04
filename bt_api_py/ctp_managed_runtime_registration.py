"""CTP route factory for provider-owned or explicitly bootstrapped runtimes.

This parent-SDK adapter is the discovery boundary for the CTP package. It
keeps the provider configuration payload private, either borrows an exact
ready CTP runtime or creates one from typed non-secret dependencies after
consuming that payload, then delegates feed/provider assembly to the existing
CTP runtime composition. Backtrader never constructs or receives a native
TraderClient. A bootstrapped route owns and closes its runtime; a borrowed
route closes only its selected request feed.

Read-only routes require no grant or authorization source and return the
generic SDK's write-denying facade.  Managed paper routes require a distinct
host-authenticated authority source already installed in the runtime's
dependency factory.  That source must bind and validate the exact operator
grant and enforce it at each correlated claim and dispatch.  A carrier,
profile, configuration, grant DTO, or runtime binding is not permission.
Missing paper-arm, account, economics, correlation, or authorization
capabilities remain unavailable.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Protocol, runtime_checkable

from bt_api_base.managed_runtime_protocol import (
    ManagedRuntimeFactoryRegistryPort,
    ManagedRuntimeProviderProduct,
    OperatorWriteGrant,
    RuntimeThreadConstraint,
    SelectedManagedRuntimeRoute,
)
from bt_api_base.provider_configuration_protocol import ProviderConfiguration
from .ctp_managed_runtime_host import (
    CtpManagedRuntimeBootstrapDependenciesV1,
    CtpManagedRuntimeHostError,
    _CtpManagedRuntimeBootstrapStateV1,
    _resolve_ctp_managed_runtime_bootstrap_owner,
    create_ctp_managed_runtime_binding,
)

__all__ = (
    "CTP_FUTURE_MANAGED_RUNTIME_ROUTE",
    "CtpManagedRuntimeBootstrapDependenciesV1",
    "CtpManagedRuntimeOwnerBindingV1",
    "CtpManagedRuntimeRegistrationError",
    "create_ctp_managed_runtime_binding",
    "register_ctp_managed_runtime_provider",
)


from bt_api_ctp.managed_runtime_route_validation import CTP_FUTURE_MANAGED_RUNTIME_ROUTE
_DEFAULT_TIMEOUT_SECONDS = 5.0
_ERROR_CODES = frozenset(
    {
        "ctp_managed_runtime_route_invalid",
        "ctp_managed_runtime_configuration_invalid",
        "ctp_managed_runtime_binding_invalid",
        "ctp_managed_runtime_not_ready",
        "ctp_managed_runtime_owner_unavailable",
        "ctp_managed_runtime_dependency_factory_unavailable",
        "ctp_managed_runtime_authority_source_unavailable",
        "ctp_managed_runtime_authority_source_mismatch",
        "ctp_managed_runtime_operator_grant_invalid",
        "ctp_managed_runtime_operator_grant_rejected",
        "ctp_managed_runtime_issuer_unavailable",
        "ctp_managed_runtime_evidence_unavailable",
        "ctp_managed_runtime_paper_arm_unavailable",
        "ctp_managed_runtime_configuration_payload_unavailable",
        "ctp_managed_runtime_feed_unavailable",
        "ctp_managed_runtime_context_unavailable",
        "ctp_managed_runtime_context_binding_invalid",
        "ctp_managed_runtime_environment_unverified",
        "ctp_managed_runtime_registered_sim_authority_unavailable",
        "ctp_managed_runtime_order_capability_unavailable",
        "ctp_managed_runtime_grant_binding_unavailable",
        "ctp_managed_runtime_composition_failed",
        "ctp_managed_runtime_cleanup_unconfirmed",
        "ctp_managed_runtime_registry_invalid",
        "ctp_managed_runtime_bootstrap_unavailable",
        "ctp_managed_runtime_owner_thread_required",
        "ctp_managed_runtime_serve_failed",
        "ctp_managed_runtime_serve_unavailable",
        "ctp_managed_runtime_host_already_used",
    }
)


class CtpManagedRuntimeRegistrationError(RuntimeError):
    """Finite, value-free failure from CTP managed route registration."""

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in _ERROR_CODES else (
            "ctp_managed_runtime_composition_failed"
        )
        super().__init__(self.code)


@dataclass(frozen=True, repr=False, eq=False)
class CtpManagedRuntimeOwnerBindingV1:
    """Opaque route carrier for an existing runtime and durable issuer.

    This object is not a runtime seal, grant, arm, or authorization.  The
    selected CTP runtime and its injected authority source still validate the
    exact context/session before returning a managed product.
    """

    runtime: object = field(repr=False, compare=False)
    order_correlation_issuer: object = field(repr=False, compare=False)
    market_stream: object | None = field(default=None, repr=False, compare=False)
    owner_call_timeout_seconds: float = field(
        default=_DEFAULT_TIMEOUT_SECONDS,
        repr=False,
        compare=False,
    )
    instrument_metadata_timeout_seconds: float = field(
        default=_DEFAULT_TIMEOUT_SECONDS,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.runtime is None or self.order_correlation_issuer is None:
            raise TypeError("runtime and durable correlation issuer are required")
        for name in (
            "owner_call_timeout_seconds",
            "instrument_metadata_timeout_seconds",
        ):
            value = getattr(self, name)
            if (
                type(value) not in (int, float)
                or isinstance(value, bool)
                or not isfinite(float(value))
                or not 0.01 <= float(value) <= 30.0
            ):
                raise TypeError("runtime composition timeout is invalid")

    def __repr__(self) -> str:
        return "<CTP managed runtime owner binding>"


@runtime_checkable
class CtpManagedOperatorGrantAuthorityV1(Protocol):
    """Host-authenticated operator-grant hooks required for managed paper.

    ``bind_operator_grant`` must validate and bind the exact route grant to
    the already-authenticated runtime, session, durable issuer, and any
    required SDK paper-arm state.  It must not treat configuration/profile
    data or the grant DTO itself as proof.  The per-order hooks on
    ``CtpOrderProviderAuthoritySourceV1`` must reserve an order count once by
    correlation at ``claim`` and revalidate expiry, count, and notional at
    ``dispatch`` without counting the same correlation again.  They must
    also validate the underlying CTP write arm.  These hooks do not replace
    the existing BasicAdmission or Risk gates.
    """

    def bind_operator_grant(
        self,
        route: SelectedManagedRuntimeRoute,
        *,
        runtime: object,
        runtime_context: object,
        order_correlation_issuer: object,
    ) -> object: ...

    def validate_operator_grant_binding(
        self,
        binding: object,
        route: SelectedManagedRuntimeRoute,
        *,
        runtime: object,
        runtime_context: object,
        order_correlation_issuer: object,
    ) -> bool: ...

    def revoke_operator_grant_binding(
        self,
        binding: object,
        *,
        runtime: object,
        runtime_context: object,
    ) -> bool: ...


class _CtpManagedRuntimeFactory:
    def __call__(
        self,
        route: SelectedManagedRuntimeRoute,
        /,
    ) -> ManagedRuntimeProviderProduct:
        return _create_product(route)


def register_ctp_managed_runtime_provider(
    registry: ManagedRuntimeFactoryRegistryPort,
) -> None:
    """Register exactly the CTP futures managed-runtime route."""

    if not isinstance(registry, ManagedRuntimeFactoryRegistryPort):
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_registry_invalid"
        )
    try:
        registry.register(
            CTP_FUTURE_MANAGED_RUNTIME_ROUTE,
            _CtpManagedRuntimeFactory(),
        )
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_registry_invalid"
        ) from None


def _create_product(route: SelectedManagedRuntimeRoute) -> ManagedRuntimeProviderProduct:
    _validate_route(route)
    bootstrap_owner = None
    candidate_owner = route.runtime_binding.runtime_owner
    bootstrap_owner = _resolve_ctp_managed_runtime_bootstrap_owner(candidate_owner)
    runtime = None
    issuer = None
    market_stream = None
    owner_binding = None
    exchange_params = None
    source = None
    dependency_factory = None
    composition = None
    grant_binding = None
    runtime_context = None
    try:
        if bootstrap_owner is not None:
            bootstrap_owner._claim()
            owner_binding = bootstrap_owner
            (
                runtime,
                issuer,
                market_stream,
                owner_binding,
                exchange_params,
            ) = _bootstrap_runtime_for_route(route, bootstrap_owner)
        else:
            runtime, issuer, market_stream, owner_binding = _selected_owner(route)
        _validate_runtime_binding(runtime, route)

        if route.access_mode == "managed_execution":
            source, dependency_factory = _managed_authority(route, runtime, issuer)

        if exchange_params is None:
            exchange_params = _consume_route_configuration(route.configuration, runtime)

        from bt_api_py.ctp_runtime_composition import compose_ctp_runtime_bt_api

        composition = compose_ctp_runtime_bt_api(
            runtime=runtime,
            exchange_params=exchange_params,
            order_correlation_issuer=issuer,
            market_stream=market_stream,
            owner_call_timeout_seconds=(
                owner_binding.owner_call_timeout_seconds
                if owner_binding is not None
                else _DEFAULT_TIMEOUT_SECONDS
            ),
            instrument_metadata_timeout_seconds=(
                owner_binding.instrument_metadata_timeout_seconds
                if owner_binding is not None
                else _DEFAULT_TIMEOUT_SECONDS
            ),
        )
        runtime_context = _validate_composed_context(
            route,
            runtime,
            composition,
            market_stream=market_stream,
            owner_binding=owner_binding,
        )
        if route.access_mode == "read_only":
            return ManagedRuntimeProviderProduct(
                api=composition.api,
                close_route=composition.close_api,
                runtime_binding_verified=True,
                operator_grant_verified=False,
                thread_constraint=_runtime_thread_constraint(runtime),
            )

        if source is None or dependency_factory is None or issuer is None:
            raise CtpManagedRuntimeRegistrationError(
                "ctp_managed_runtime_authority_source_unavailable"
            )
        context_valid = _call_on_runtime(
            runtime,
            lambda: _managed_context_is_ready(runtime_context, source, runtime),
            timeout_seconds=(
                owner_binding.owner_call_timeout_seconds
                if owner_binding is not None
                else _DEFAULT_TIMEOUT_SECONDS
            ),
        )
        if type(context_valid) is not bool or context_valid is not True:
            raise CtpManagedRuntimeRegistrationError(
                "ctp_managed_runtime_order_capability_unavailable"
            )
        grant_binding = _bind_operator_grant(
            route,
            runtime,
            runtime_context,
            issuer,
            source,
            owner_binding,
        )
        closer = _ManagedRouteCloser(
            composition,
            runtime,
            runtime_context,
            source,
            grant_binding,
            owner_binding.owner_call_timeout_seconds,
            bootstrap_owner=bootstrap_owner,
        )
        return ManagedRuntimeProviderProduct(
            api=composition.api,
            close_route=closer.close,
            runtime_binding_verified=True,
            operator_grant_verified=True,
            serve_route=(
                None if bootstrap_owner is None else bootstrap_owner._serve_once
            ),
            thread_constraint=_runtime_thread_constraint(runtime),
        )
    except CtpManagedRuntimeRegistrationError:
        cleanup_confirmed = _cleanup_failed_composition(
            composition,
            runtime,
            runtime_context,
            source,
            grant_binding,
            owner_binding,
        )
        if not cleanup_confirmed:
            raise CtpManagedRuntimeRegistrationError(
                "ctp_managed_runtime_cleanup_unconfirmed"
            ) from None
        raise
    except Exception:
        cleanup_confirmed = _cleanup_failed_composition(
            composition,
            runtime,
            runtime_context,
            source,
            grant_binding,
            owner_binding,
        )
        if not cleanup_confirmed:
            raise CtpManagedRuntimeRegistrationError(
                "ctp_managed_runtime_cleanup_unconfirmed"
            ) from None
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_composition_failed"
        ) from None


def _validate_route(route: SelectedManagedRuntimeRoute) -> None:
    from bt_api_ctp.managed_runtime_route_validation import validate_ctp_managed_runtime_route

    validate_ctp_managed_runtime_route(
        route, error_type=CtpManagedRuntimeRegistrationError,
        validate_grant_bounds=_validate_grant_bounds,
    )


def _validate_grant_bounds(
    route: SelectedManagedRuntimeRoute,
    grant: OperatorWriteGrant,
) -> None:
    try:
        expires_at = datetime.fromisoformat(grant.expires_at_utc[:-1] + "+00:00")
    except (ValueError, OverflowError):
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_operator_grant_invalid"
        ) from None
    if (
        grant.mode != "paper"
        or grant.provider_ref != route.configuration.instrument.provider
        or grant.environment != route.environment
        or route.configuration.instrument.native_symbol not in grant.symbols
        or grant.allow_live_write is not False
        or expires_at.tzinfo is None
        or expires_at <= datetime.now(timezone.utc)
    ):
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_operator_grant_rejected"
        )


def _bootstrap_runtime_for_route(
    route: SelectedManagedRuntimeRoute,
    owner: _CtpManagedRuntimeBootstrapStateV1,
) -> tuple[object, object, object | None, object, dict[str, object]]:
    """Consume the exact CTP payload and bootstrap one owner on this thread."""

    if route.access_mode != "managed_execution":
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_bootstrap_unavailable"
        )
    dependencies = owner._dependencies
    dependency_factory = dependencies.order_provider_dependency_factory
    try:
        from bt_api_py.ctp_order_provider_dependencies import (
            CtpOrderProviderAuthoritySourceV1,
            CtpRuntimeOrderProviderDependenciesFactoryV1,
        )
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_dependency_factory_unavailable"
        ) from None
    try:
        source = dependency_factory.authority_source
        valid_factory = type(dependency_factory) is CtpRuntimeOrderProviderDependenciesFactoryV1
        valid_source = (
            isinstance(source, CtpOrderProviderAuthoritySourceV1)
            and isinstance(source, CtpManagedOperatorGrantAuthorityV1)
        )
    except Exception:
        valid_factory = False
        valid_source = False
        source = None
    if (
        not valid_factory
        or not valid_source
        or source is not route.authorization_source
    ):
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_authority_source_mismatch"
        )

    try:
        from bt_api_py.ctp_execution_runtime import (
            DIRECT_MANAGED_OWNER_OPTIONS,
            CtpExecutionResourceProvider,
            CtpTraderConnectionConfig,
        )

        payload = _consume_bootstrap_route_payload(route.configuration)
        credentials = payload.credential_values
        options = payload.provider_options
        trader = CtpTraderConnectionConfig(
            front=options["td_front"],
            md_front=options["md_front"],
            broker_id=credentials["broker_id"],
            user_id=credentials["investor_id"],
            password=credentials["password"],
            app_id=credentials["app_id"],
            auth_code=credentials["auth_code"],
            ctp_env_profile="config_front_pair",
        )
        exchange_params = _build_exchange_params(payload, trader)
        runtime_config = dependencies.build_runtime_config(trader)
        resource_provider = CtpExecutionResourceProvider(runtime_config)
        try:
            # The direct route serves no orders.submit/orders.cancel wire
            # handler; the sentinel keeps it apart from a ServiceHost build,
            # which must carry typed command bindings (iteration 60).
            resources = resource_provider(
                "execution",
                owner._instance_id,
                DIRECT_MANAGED_OWNER_OPTIONS,
                runtime_config.service_scope,
            )
        except Exception as error:
            runtime = resource_provider.last_runtime
            if runtime is not None:
                owner._install_runtime(runtime)
            partial_resources = getattr(error, "resources", None)
            if partial_resources is not None:
                owner._install_resources(partial_resources)
            raise
        runtime = resource_provider.last_runtime
        if runtime is None:
            raise CtpManagedRuntimeRegistrationError(
                "ctp_managed_runtime_not_ready"
            )
        owner._install_runtime(runtime)
        owner._install_resources(resources)
        owner._mark_ready()
    except CtpManagedRuntimeRegistrationError:
        raise
    except CtpManagedRuntimeHostError as error:
        code = (
            "ctp_managed_runtime_host_already_used"
            if error.code == "ctp_managed_runtime_host_already_used"
            else "ctp_managed_runtime_not_ready"
        )
        raise CtpManagedRuntimeRegistrationError(code) from None
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_not_ready"
        ) from None
    return (
        runtime,
        owner._order_correlation_issuer,
        owner._market_stream,
        owner,
        exchange_params,
    )


def _consume_bootstrap_route_payload(
    configuration: ProviderConfiguration,
) -> object:
    try:
        from bt_api_ctp.simnow_route_configuration import (
            _CtpProviderConfigurationPayload,
            _consume_ctp_provider_configuration,
        )

        payload = _consume_ctp_provider_configuration(configuration)
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_configuration_payload_unavailable"
        ) from None
    if type(payload) is not _CtpProviderConfigurationPayload:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_configuration_payload_unavailable"
        )
    credentials = payload.credential_values
    options = payload.provider_options
    required_credentials = {
        "broker_id",
        "investor_id",
        "password",
        "app_id",
        "auth_code",
    }
    if (
        set(credentials) != required_credentials
        or not isinstance(options, Mapping)
        or options.get("environment") != "simulation"
        or options.get("ctp_environment") != "simnow"
        or options.get("auto_settlement_confirm") is not False
        or type(options.get("td_front")) is not str
        or not options.get("td_front")
        or type(options.get("md_front")) is not str
        or not options.get("md_front")
        or any(type(value) is not str or not value for value in credentials.values())
    ):
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_configuration_payload_unavailable"
        )
    return payload


def _build_exchange_params(
    payload: object,
    trader: object,
) -> dict[str, object]:
    """Create provider-private BtApi route settings from the consumed payload."""

    try:
        from bt_api_ctp.simnow_route_configuration import _CtpProviderConfigurationPayload
        from bt_api_py.ctp_execution_runtime import CtpTraderConnectionConfig

        if (
            type(payload) is not _CtpProviderConfigurationPayload
            or type(trader) is not CtpTraderConnectionConfig
        ):
            raise ValueError
        credentials = payload.credential_values
        options = payload.provider_options
        expected = {
            "td_front": trader.front,
            "md_front": trader.md_front,
            "broker_id": credentials["broker_id"],
            "user_id": credentials["investor_id"],
            "password": credentials["password"],
            "app_id": credentials["app_id"],
            "auth_code": credentials["auth_code"],
        }
        if (
            options.get("td_front") != trader.front
            or options.get("md_front") != trader.md_front
            or credentials["broker_id"] != trader.broker_id
            or credentials["investor_id"] != trader.user_id
            or credentials["password"] != trader.password
            or credentials["app_id"] != trader.app_id
            or credentials["auth_code"] != trader.auth_code
            or options.get("auto_settlement_confirm") is not False
            or options.get("environment") != "simulation"
        ):
            raise ValueError
        route_settings = dict(options)
        route_settings.update(expected)
        route_settings.update(
            {
                "ctp_env_profile": trader.ctp_env_profile,
                "ctp_environment": "simnow",
                "environment": "simulation",
                "asset_type": "FUTURE",
                "auto_settlement_confirm": False,
            }
        )
        return route_settings
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_configuration_payload_unavailable"
        ) from None


def _selected_owner(
    route: SelectedManagedRuntimeRoute,
) -> tuple[object, object | None, object | None, CtpManagedRuntimeOwnerBindingV1 | None]:
    try:
        from bt_api_py.ctp_execution_runtime import CtpExecutionRuntime
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_owner_unavailable"
        ) from None
    owner = route.runtime_binding.runtime_owner
    if route.access_mode == "read_only":
        if type(owner) is not CtpExecutionRuntime:
            raise CtpManagedRuntimeRegistrationError(
                "ctp_managed_runtime_binding_invalid"
            )
        return owner, None, None, None
    if type(owner) is not CtpManagedRuntimeOwnerBindingV1:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_binding_invalid"
        )
    if type(owner.runtime) is not CtpExecutionRuntime:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_owner_unavailable"
        )
    try:
        from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_issuer_unavailable"
        ) from None
    if type(owner._order_correlation_issuer) is not DurableOrderCorrelationIssuer:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_issuer_unavailable"
        )
    return (
        owner._runtime,
        owner._order_correlation_issuer,
        owner._market_stream,
        owner,
    )


def _runtime_thread_constraint(runtime: object) -> RuntimeThreadConstraint | None:
    """Bind products to the exact runtime thread without owner-thread dispatch."""

    try:
        from bt_api_py.ctp_execution_runtime import CtpExecutionRuntime

        if type(runtime) is not CtpExecutionRuntime:
            return None
        owner_thread = runtime.owner_thread
    except Exception:
        return None
    if not isinstance(owner_thread, threading.Thread):
        return None

    def verify_current_thread() -> bool:
        try:
            return (
                type(runtime) is CtpExecutionRuntime
                and runtime.owner_thread is owner_thread
                and threading.current_thread() is owner_thread
            )
        except Exception:
            return False

    try:
        return RuntimeThreadConstraint(
            affinity="owner_thread",
            verify_current_thread=verify_current_thread,
        )
    except Exception:
        return None


def _validate_runtime_binding(runtime: object, route: SelectedManagedRuntimeRoute) -> None:
    try:
        from bt_api_execution.contracts import ExecutionScope
        from bt_api_py.ctp_execution_runtime import (
            CtpExecutionRuntime,
            CtpTraderConnectionConfig,
        )
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_owner_unavailable"
        ) from None
    if type(runtime) is not CtpExecutionRuntime:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_owner_unavailable"
        )

    def validate_on_owner() -> bool:
        try:
            config = runtime.config
            trader = config.trader
            scope = config.execution_scope
            return (
                runtime.is_ready() is True
                and runtime._phase == "READY"
                and runtime._closed is False
                and type(trader) is CtpTraderConnectionConfig
                and trader.ctp_env_profile == "config_front_pair"
                and type(scope) is ExecutionScope
                and scope.provider == "ctp"
                and scope.environment == "simulation"
                and route.environment == scope.environment
            )
        except Exception:
            return False

    try:
        result = _call_on_runtime(runtime, validate_on_owner)
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_not_ready"
        ) from None
    if type(result) is not bool or result is not True:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_not_ready"
        )


def _managed_authority(
    route: SelectedManagedRuntimeRoute,
    runtime: object,
    issuer: object | None,
) -> tuple[object, object]:
    if route.authorization_source is None or issuer is None:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_authority_source_unavailable"
        )
    try:
        from bt_api_py.ctp_order_provider_dependencies import (
            CtpOrderProviderAuthoritySourceV1,
            CtpRuntimeOrderProviderDependenciesFactoryV1,
        )
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_dependency_factory_unavailable"
        ) from None
    try:
        dependency_factory, source = _call_on_runtime(
            runtime,
            lambda: (
                runtime.config.order_provider_dependency_factory,
                runtime.config.order_provider_dependency_factory.authority_source,
            ),
        )
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_dependency_factory_unavailable"
        ) from None
    if (
        type(dependency_factory) is not CtpRuntimeOrderProviderDependenciesFactoryV1
        or source is not route.authorization_source
        or not isinstance(source, CtpOrderProviderAuthoritySourceV1)
        or not isinstance(source, CtpManagedOperatorGrantAuthorityV1)
    ):
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_authority_source_mismatch"
        )
    return source, dependency_factory


def _consume_route_configuration(
    configuration: ProviderConfiguration,
    runtime: object,
) -> dict[str, object]:
    def consume_on_owner() -> dict[str, object]:
        try:
            from bt_api_py.ctp_execution_runtime import CtpTraderConnectionConfig

            trader = runtime.config.trader
            if type(trader) is not CtpTraderConnectionConfig:
                return {}
            payload = _consume_bootstrap_route_payload(configuration)
            return _build_exchange_params(payload, trader)
        except Exception:
            return {}

    settings = _call_on_runtime(runtime, consume_on_owner)
    if type(settings) is not dict or not settings:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_configuration_payload_unavailable"
        )
    return settings


def _validate_composed_context(
    route: SelectedManagedRuntimeRoute,
    runtime: object,
    composition: object,
    *,
    market_stream: object | None,
    owner_binding: object | None,
) -> object:
    try:
        api = composition.api
        feed = api.exchange_feeds.get(CTP_FUTURE_MANAGED_RUNTIME_ROUTE)
        if feed is None:
            raise CtpManagedRuntimeRegistrationError(
                "ctp_managed_runtime_feed_unavailable"
            )
        timeout = (
            owner_binding.owner_call_timeout_seconds
            if owner_binding is not None
            else _DEFAULT_TIMEOUT_SECONDS
        )
        metadata_timeout = (
            owner_binding.instrument_metadata_timeout_seconds
            if owner_binding is not None
            else _DEFAULT_TIMEOUT_SECONDS
        )
        context = runtime.build_selected_feed_order_provider_context(
            feed,
            market_stream=market_stream,
            owner_call_timeout_seconds=float(timeout),
            instrument_metadata_timeout_seconds=float(metadata_timeout),
        )
        if context is None or runtime.validate_selected_feed_order_provider_context(
            feed,
            context,
            exchange_name=CTP_FUTURE_MANAGED_RUNTIME_ROUTE,
            timeout_seconds=float(timeout),
        ) is not True:
            raise CtpManagedRuntimeRegistrationError(
                "ctp_managed_runtime_context_binding_invalid"
            )
        if route.access_mode == "managed_execution":
            if context.order_provider_authority_source is not route.authorization_source:
                raise CtpManagedRuntimeRegistrationError(
                    "ctp_managed_runtime_authority_source_mismatch"
                )
            environment_is_verified = _call_on_runtime(
                runtime,
                lambda: _selected_feed_is_verified_demo(runtime, feed),
                timeout_seconds=float(timeout),
            )
            if type(environment_is_verified) is not bool or not environment_is_verified:
                raise CtpManagedRuntimeRegistrationError(
                    "ctp_managed_runtime_environment_unverified"
                )
        return context
    except CtpManagedRuntimeRegistrationError:
        raise
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_context_unavailable"
        ) from None


def _selected_feed_is_verified_demo(runtime: object, feed: object) -> bool:
    """Require the same connected CTP feed to prove its demo environment."""

    try:
        from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture

        runtime._assert_owner_thread()
        if (
            type(feed) is not CtpRequestDataFuture
            or runtime._borrowed_request_feed is not feed
            or feed._connected is not True
            or feed.trader_client is not runtime._trader
        ):
            return False
        get_environment_info = getattr(feed, "get_environment_info", None)
        if not callable(get_environment_info):
            return False
        info = get_environment_info()
        return (
            type(info) is dict
            and info.get("environment") == "demo"
            and info.get("verified") is True
            and info.get("simulated") is True
        )
    except Exception:
        return False


def _managed_context_is_ready(
    runtime_context: object,
    source: object,
    runtime: object,
) -> bool:
    try:
        from bt_api_ctp.instrument_metadata import CtpInstrumentMetadataReader
        from bt_api_ctp.managed_order_authority import CtpRegisteredSimNativeAuthority
    except Exception:
        return False
    try:
        runtime._assert_owner_thread()
        trader_client = runtime._trader
        owner = runtime_context.execution_economics_owner
        issuer = runtime_context.execution_economics_issuer
        return (
            runtime_context.order_provider_authority_source is source
            and type(runtime_context.registered_sim_authority)
            is CtpRegisteredSimNativeAuthority
            and runtime_context.registered_sim_authority.is_bound_to_client(
                trader_client
            )
            is True
            and type(runtime_context.instrument_metadata_reader)
            is CtpInstrumentMetadataReader
            and runtime_context.instrument_metadata_reader.request_feed
            is runtime._borrowed_request_feed
            and callable(runtime_context.request_account_validator)
            and callable(runtime_context.submit_context_resolver)
            and callable(runtime_context.cancel_authorization_validator)
            and owner is not None
            and issuer is not None
            and getattr(owner, "_issuer", None) is issuer
            and getattr(owner, "_supports_economics", False) is True
            and getattr(owner, "_supports_rules_snapshots", False) is True
        )
    except Exception:
        return False


def _bind_operator_grant(
    route: SelectedManagedRuntimeRoute,
    runtime: object,
    runtime_context: object,
    issuer: object,
    source: object,
    owner_binding: object,
) -> object:
    try:
        binding = _call_on_runtime(
            runtime,
            lambda: source.bind_operator_grant(
                route,
                runtime=runtime,
                runtime_context=runtime_context,
                order_correlation_issuer=issuer,
            ),
            timeout_seconds=float(owner_binding.owner_call_timeout_seconds),
        )
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_grant_binding_unavailable"
        ) from None
    if binding is None or type(binding) in (bool, str, bytes, int, float):
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_grant_binding_unavailable"
        )
    if _validate_grant_binding(
        route,
        runtime,
        runtime_context,
        issuer,
        source,
        binding,
        float(owner_binding.owner_call_timeout_seconds),
    ) is not True:
        revoked = _revoke_grant_binding(
            runtime,
            runtime_context,
            source,
            binding,
            float(owner_binding.owner_call_timeout_seconds),
        )
        if revoked is not True:
            raise CtpManagedRuntimeRegistrationError(
                "ctp_managed_runtime_cleanup_unconfirmed"
            )
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_grant_binding_unavailable"
        )
    return binding


def _validate_grant_binding(
    route: SelectedManagedRuntimeRoute,
    runtime: object,
    runtime_context: object,
    issuer: object,
    source: object,
    binding: object,
    timeout_seconds: float,
) -> bool:
    try:
        result = _call_on_runtime(
            runtime,
            lambda: source.validate_operator_grant_binding(
                binding,
                route,
                runtime=runtime,
                runtime_context=runtime_context,
                order_correlation_issuer=issuer,
            ),
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        return False
    return type(result) is bool and result is True


def _revoke_grant_binding(
    runtime: object,
    runtime_context: object,
    source: object,
    binding: object,
    timeout_seconds: float,
) -> bool:
    try:
        result = _call_on_runtime(
            runtime,
            lambda: source.revoke_operator_grant_binding(
                binding,
                runtime=runtime,
                runtime_context=runtime_context,
            ),
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        return False
    return type(result) is bool and result is True


class _ManagedRouteCloser:
    def __init__(
        self,
        composition: object,
        runtime: object,
        runtime_context: object,
        source: object,
        grant_binding: object,
        timeout_seconds: float,
        *,
        bootstrap_owner: _CtpManagedRuntimeBootstrapStateV1 | None = None,
    ) -> None:
        self._composition = composition
        self._runtime = runtime
        self._runtime_context = runtime_context
        self._source = source
        self._grant_binding = grant_binding
        self._timeout_seconds = timeout_seconds
        self._bootstrap_owner = bootstrap_owner
        self._lock = threading.Lock()
        self._attempted = False

    def close(self) -> None:
        with self._lock:
            if self._attempted:
                raise CtpManagedRuntimeRegistrationError(
                    "ctp_managed_runtime_cleanup_unconfirmed"
                )
            self._attempted = True
        revoked = _revoke_grant_binding(
            self._runtime,
            self._runtime_context,
            self._source,
            self._grant_binding,
            self._timeout_seconds,
        )
        api_closed = True
        try:
            self._composition.close_api(
                timeout_seconds=float(self._timeout_seconds)
            )
        except Exception:
            api_closed = False
        runtime_closed = True
        if self._bootstrap_owner is not None:
            try:
                self._bootstrap_owner._close_runtime_once()
            except Exception:
                runtime_closed = False
        if not revoked or not api_closed or not runtime_closed:
            raise CtpManagedRuntimeRegistrationError(
                "ctp_managed_runtime_cleanup_unconfirmed"
            )


def _cleanup_failed_composition(
    composition: object | None,
    runtime: object,
    runtime_context: object | None,
    source: object | None,
    grant_binding: object | None,
    owner_binding: object | None,
) -> bool:
    timeout = (
        owner_binding.owner_call_timeout_seconds
        if owner_binding is not None
        else _DEFAULT_TIMEOUT_SECONDS
    )
    cleanup_confirmed = True
    if grant_binding is not None:
        if runtime_context is None or source is None:
            cleanup_confirmed = False
        elif _revoke_grant_binding(
            runtime,
            runtime_context,
            source,
            grant_binding,
            float(timeout),
        ) is not True:
            cleanup_confirmed = False
    if composition is not None:
        try:
            composition.close_api(timeout_seconds=float(timeout))
        except Exception:
            cleanup_confirmed = False
    if type(owner_binding) is _CtpManagedRuntimeBootstrapStateV1:
        try:
            owner_binding._close_runtime_once()
        except Exception:
            cleanup_confirmed = False
    return cleanup_confirmed


def _call_on_runtime(
    runtime: object,
    callback: Any,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    call = getattr(runtime, "call_on_owner_thread", None)
    if not callable(call):
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_owner_unavailable"
        )
    try:
        result = call(callback, timeout_seconds=float(timeout_seconds))
    except CtpManagedRuntimeRegistrationError:
        raise
    except Exception:
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_owner_unavailable"
        ) from None
    if hasattr(result, "__await__"):
        raise CtpManagedRuntimeRegistrationError(
            "ctp_managed_runtime_owner_unavailable"
        )
    return result
