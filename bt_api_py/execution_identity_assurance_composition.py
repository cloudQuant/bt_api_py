"""Exact-route composition for provider-issued execution identity assurance.

This module owns no credentials and creates no account identity from config.
Only an explicitly registered provider adapter can issue an assurance DTO;
the route-bound wrapper retains the exact DTO and asks that same provider to
revalidate it against current source/session evidence on every use.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata

from bt_api_base.execution_identity_protocol import (
    ExecutionIdentityAssurance,
    ExecutionIdentityAssurancePort,
    ExecutionIdentityAssuranceProviderFactory,
    ExecutionIdentityAssuranceProviderRegistryPort,
    ExecutionIdentityAssuranceUnavailable,
    ExecutionIdentityRouteExpectation,
    SelectedExecutionIdentityRoute,
)

__all__ = [
    "EXECUTION_IDENTITY_ASSURANCE_PROVIDER_ENTRY_POINT_GROUP",
    "ExecutionIdentityAssuranceProviderRegistry",
    "ExecutionIdentityAssuranceProviderRouter",
    "default_execution_identity_assurance_provider_registry",
    "register_execution_identity_assurance_provider_factory",
    "ExecutionIdentityExpectationError",
    "build_execution_identity_expectations",
    "capture_execution_identity_binding",
    "validate_execution_identity_binding",
    "execution_identity_vector_contract",
]

EXECUTION_IDENTITY_ASSURANCE_PROVIDER_ENTRY_POINT_GROUP = (
    "bt_api.execution_identity_assurance_providers"
)
_MAX_ISSUED_ASSURANCES = 128


class ExecutionIdentityExpectationError(RuntimeError):
    """Finite invalid configuration while preparing route expectations."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def build_execution_identity_expectations(
    routes,
    *,
    strategy_id,
    account_aliases,
    required_environments,
    configured_environment,
):
    """Project route configuration into expectations without attesting identity."""
    if type(strategy_id) is not str or not strategy_id.strip():
        raise ExecutionIdentityExpectationError("execution_identity_snapshot_strategy_unavailable")
    strategy_id = strategy_id.strip()
    selected_routes = tuple(sorted(routes))
    if not selected_routes or len(selected_routes) != len(set(selected_routes)):
        raise ExecutionIdentityExpectationError("execution_identity_evidence_vector_invalid")
    expectations = []
    for route in selected_routes:
        alias = account_aliases.get(route) if isinstance(account_aliases, Mapping) else None
        if alias in (None, ""):
            alias = None
        elif type(alias) is not str or not alias.strip():
            raise ExecutionIdentityExpectationError("execution_identity_evidence_config_mismatch")
        else:
            alias = alias.strip()
        environment = (
            required_environments.get(route)
            if isinstance(required_environments, Mapping)
            else None
        )
        if environment in (None, ""):
            environment = configured_environment(route)
        if environment in (None, ""):
            environment = None
        elif type(environment) is not str or not environment.strip():
            raise ExecutionIdentityExpectationError("execution_identity_evidence_config_mismatch")
        else:
            environment = environment.strip().lower()
        provider = str(route or "").partition("___")[0].strip().lower()
        if not provider:
            raise ExecutionIdentityExpectationError("execution_identity_evidence_config_mismatch")
        try:
            expectation = ExecutionIdentityRouteExpectation(
                exchange_name=route,
                expected_provider=provider,
                expected_environment=environment,
                expected_strategy_id=strategy_id,
                expected_account_alias=alias,
            )
            ExecutionIdentityRouteExpectation.__post_init__(expectation)
        except Exception:
            raise ExecutionIdentityExpectationError(
                "execution_identity_evidence_config_mismatch"
            ) from None
        expectations.append(expectation)
    return tuple(expectations)


def capture_execution_identity_binding(
    api,
    expectations,
    *,
    expected_routes,
    sanitize_exception,
    exception_code,
):
    """Capture one SDK identity vector; the caller still owns its session fence."""
    from .facade_protocol import ExecutionIdentityEvidenceSelector, ExecutionIdentityVectorBinding

    if (
        type(expectations) is not tuple
        or not expectations
        or any(type(item) is not ExecutionIdentityRouteExpectation for item in expectations)
    ):
        raise ExecutionIdentityExpectationError("execution_identity_evidence_vector_invalid")
    try:
        for item in expectations:
            ExecutionIdentityRouteExpectation.__post_init__(item)
    except Exception as exc:
        sanitize_exception(exc)
        raise ExecutionIdentityExpectationError("execution_identity_evidence_vector_invalid") from None
    routes = tuple(item.exchange_name for item in expectations)
    if routes != expected_routes:
        raise ExecutionIdentityExpectationError("execution_identity_evidence_vector_invalid")
    if not isinstance(api, ExecutionIdentityEvidenceSelector):
        raise ExecutionIdentityExpectationError("execution_identity_evidence_unavailable")
    capture = getattr(api, "capture_execution_identity_evidence", None)
    if not callable(capture):
        raise ExecutionIdentityExpectationError("execution_identity_evidence_unavailable")
    try:
        binding = capture(expectations)
    except Exception as exc:
        sanitize_exception(exc)
        code = exception_code(exc, "execution_identity_evidence_unavailable")
        raise ExecutionIdentityExpectationError(code) from None
    if type(binding) is not ExecutionIdentityVectorBinding:
        raise ExecutionIdentityExpectationError("execution_identity_vector_projection_invalid")
    try:
        ExecutionIdentityVectorBinding.__post_init__(binding)
    except Exception as exc:
        sanitize_exception(exc)
        raise ExecutionIdentityExpectationError("execution_identity_vector_projection_invalid") from None
    if binding.exchange_names != routes:
        raise ExecutionIdentityExpectationError("execution_identity_vector_route_mismatch")
    return binding


def execution_identity_vector_contract(binding, expected_routes):
    """Extract exact SDK vector fields after neutral route/shape validation.

    These checks do not establish provider currentness or a caller's own
    generation fence; those remain separate responsibilities.
    """
    try:
        from .facade_protocol import ExecutionIdentityScopeRef, ExecutionIdentityVectorBinding
    except Exception:
        return None
    if type(binding) is not ExecutionIdentityVectorBinding:
        return None
    try:
        ExecutionIdentityVectorBinding.__post_init__(binding)
    except Exception:
        return None
    if (
        type(expected_routes) is not tuple
        or not expected_routes
        or any(type(route) is not str or not route.strip() for route in expected_routes)
        or tuple(sorted(expected_routes)) != expected_routes
        or len(set(expected_routes)) != len(expected_routes)
        or binding.exchange_names != expected_routes
        or type(binding.identity_scope_refs) is not tuple
        or len(binding.identity_scope_refs) != len(expected_routes)
        or any(type(ref) is not ExecutionIdentityScopeRef for ref in binding.identity_scope_refs)
    ):
        return None
    return binding.identity_scope_refs, binding.session_generations, binding.fencing_epoch


def validate_execution_identity_binding(
    api,
    binding,
    expectations,
    *,
    expected_routes,
    sanitize_exception,
) -> bool:
    """Ask the selected SDK facade to validate a matching identity vector."""
    from .facade_protocol import ExecutionIdentityEvidenceSelector, ExecutionIdentityVectorBinding

    if type(binding) is not ExecutionIdentityVectorBinding:
        return False
    if not isinstance(api, ExecutionIdentityEvidenceSelector):
        return False
    if (
        type(expectations) is not tuple
        or not expectations
        or any(type(item) is not ExecutionIdentityRouteExpectation for item in expectations)
    ):
        return False
    try:
        for item in expectations:
            ExecutionIdentityRouteExpectation.__post_init__(item)
    except Exception:
        return False
    routes = tuple(item.exchange_name for item in expectations)
    if routes != expected_routes or binding.exchange_names != routes:
        return False
    validator = getattr(api, "validate_execution_identity_evidence", None)
    if not callable(validator):
        return False
    try:
        valid = validator(binding, expectations)
    except Exception as exc:
        sanitize_exception(exc)
        return False
    return type(valid) is bool and valid is True


class ExecutionIdentityAssuranceProviderRegistry(
    ExecutionIdentityAssuranceProviderRegistryPort
):
    """Exact-route registry for trusted provider-owned identity factories."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._factories: dict[str, ExecutionIdentityAssuranceProviderFactory] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(
        self,
        exchange_name: str,
        factory: ExecutionIdentityAssuranceProviderFactory,
        /,
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_provider_unavailable"
            )
        with self._lock:
            if exchange_name in self._factories:
                raise ExecutionIdentityAssuranceUnavailable(
                    "execution_identity_assurance_provider_unavailable"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = None

    def _resolve(self, exchange_name: str) -> ExecutionIdentityAssuranceProviderFactory | None:
        with self._lock:
            return self._factories.get(exchange_name)

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factory: ExecutionIdentityAssuranceProviderFactory,
        source: tuple[str, str, str],
    ) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_provider_unavailable"
            )
        with self._lock:
            if exchange_name in self._factories:
                raise ExecutionIdentityAssuranceUnavailable(
                    "execution_identity_assurance_provider_unavailable"
                )
            self._factories[exchange_name] = factory
            self._entry_point_sources[exchange_name] = source

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


_DEFAULT_REGISTRY = ExecutionIdentityAssuranceProviderRegistry()


def default_execution_identity_assurance_provider_registry() -> (
    ExecutionIdentityAssuranceProviderRegistry
):
    """Return the process registry, which has no built-in identity provider."""

    return _DEFAULT_REGISTRY


def register_execution_identity_assurance_provider_factory(
    exchange_name: str,
    factory: ExecutionIdentityAssuranceProviderFactory,
    *,
    registry: ExecutionIdentityAssuranceProviderRegistry | None = None,
) -> None:
    selected = _DEFAULT_REGISTRY if registry is None else registry
    if type(selected) is not ExecutionIdentityAssuranceProviderRegistry:
        raise ExecutionIdentityAssuranceUnavailable(
            "execution_identity_assurance_provider_unavailable"
        )
    selected.register(exchange_name, factory)


class ExecutionIdentityAssuranceProviderRouter:
    """Cache one provider and route-bound facade per exact selected route."""

    def __init__(
        self,
        route_resolver: Callable[[str], SelectedExecutionIdentityRoute | None],
        *,
        registry: ExecutionIdentityAssuranceProviderRegistry | None = None,
    ) -> None:
        if not callable(route_resolver):
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_route_unavailable"
            )
        selected = _DEFAULT_REGISTRY if registry is None else registry
        if type(selected) is not ExecutionIdentityAssuranceProviderRegistry:
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_provider_unavailable"
            )
        self._route_resolver = route_resolver
        self._registry = selected
        self._lock = threading.RLock()
        self._providers: dict[
            str,
            tuple[SelectedExecutionIdentityRoute, ExecutionIdentityAssurancePort | None],
        ] = {}
        self._facades: dict[
            str,
            tuple[
                SelectedExecutionIdentityRoute,
                ExecutionIdentityAssurancePort | None,
                ExecutionIdentityAssurancePort,
            ],
        ] = {}

    def execution_identity_assurance(
        self,
        exchange_name: str,
    ) -> ExecutionIdentityAssurancePort:
        _require_exchange_name(exchange_name)
        route = self._resolve_route(exchange_name)
        if route is None:
            return _UnavailableExecutionIdentityAssurancePort(
                "execution_identity_assurance_route_unavailable"
            )
        return self._execution_identity_assurance_for_route(route)

    def _execution_identity_assurance_for_route(
        self,
        route: SelectedExecutionIdentityRoute,
    ) -> ExecutionIdentityAssurancePort:
        """Select a provider port for this exact currently selected route object."""
        if (
            type(route) is not SelectedExecutionIdentityRoute
            or not _same_route(self._resolve_route(route.exchange_name), route)
        ):
            return _UnavailableExecutionIdentityAssurancePort(
                "execution_identity_assurance_route_changed"
            )
        exchange_name = route.exchange_name
        provider = self._provider_for_route(route)
        with self._lock:
            cached = self._facades.get(exchange_name)
            if cached is not None and _same_route(cached[0], route) and cached[1] is provider:
                return cached[2]
            if provider is None:
                facade = _UnavailableExecutionIdentityAssurancePort(
                    "execution_identity_assurance_provider_unavailable"
                )
                self._facades[exchange_name] = (route, None, facade)
                return facade
            facade = _RouteBoundExecutionIdentityAssurancePort(
                route=route,
                route_resolver=self._route_resolver,
                provider_resolver=self._provider_for_route,
                provider=provider,
            )
            self._facades[exchange_name] = (route, provider, facade)
            return facade

    def _resolve_route(self, exchange_name: str) -> SelectedExecutionIdentityRoute | None:
        try:
            route = self._route_resolver(exchange_name)
        except Exception:
            return None
        if (
            type(route) is not SelectedExecutionIdentityRoute
            or route.exchange_name != exchange_name
        ):
            return None
        return route

    def _provider_for_route(
        self,
        route: SelectedExecutionIdentityRoute,
    ) -> ExecutionIdentityAssurancePort | None:
        with self._lock:
            cached = self._providers.get(route.exchange_name)
            if cached is not None and _same_route(cached[0], route):
                return cached[1]
            if not self._load_selected_provider(route.exchange_name):
                self._providers[route.exchange_name] = (route, None)
                return None
            factory = self._registry._resolve(route.exchange_name)
            if factory is None:
                self._providers[route.exchange_name] = (route, None)
                return None
            try:
                provider = factory(route)
                valid = provider is not None and isinstance(
                    provider,
                    ExecutionIdentityAssurancePort,
                )
            except Exception:
                provider = None
                valid = False
            if not valid:
                provider = None
            self._providers[route.exchange_name] = (route, provider)
            return provider

    def _load_selected_provider(self, exchange_name: str) -> bool:
        entry_points = _route_entry_points(exchange_name)
        if entry_points is None or len(entry_points) > 1:
            return False
        if not entry_points:
            return self._registry._resolve(exchange_name) is not None
        entry_point = entry_points[0]
        try:
            source = _entry_point_identity(entry_point, exchange_name)
        except ExecutionIdentityAssuranceUnavailable:
            return False
        registered_source = self._registry._entry_point_source(exchange_name)
        if self._registry._resolve(exchange_name) is not None:
            return registered_source == source
        try:
            registrar = entry_point.load()
        except Exception:
            return False
        if not callable(registrar):
            return False
        isolated = ExecutionIdentityAssuranceProviderRegistry()
        try:
            registrar(isolated)
        except Exception:
            return False
        if isolated._registered_routes() != (exchange_name,):
            return False
        factory = isolated._resolve(exchange_name)
        if factory is None:
            return False
        try:
            self._registry._register_entry_point_factory(exchange_name, factory, source)
        except ExecutionIdentityAssuranceUnavailable:
            return self._registry._entry_point_source(exchange_name) == source
        return True


class _UnavailableExecutionIdentityAssurancePort:
    __slots__ = ("__reason",)

    def __init__(self, reason: str) -> None:
        try:
            self.__reason = ExecutionIdentityAssuranceUnavailable(reason).code
        except Exception:
            self.__reason = "execution_identity_assurance_unavailable"

    def assure_execution_identity(self) -> ExecutionIdentityAssurance:
        raise ExecutionIdentityAssuranceUnavailable(self.__reason)

    def validate_execution_identity(
        self,
        assurance: ExecutionIdentityAssurance,
        *,
        now_utc: datetime,
    ) -> bool:
        del assurance, now_utc
        return False

    def __repr__(self) -> str:
        return "<unavailable SDK execution-identity assurance port>"


class _RouteBoundExecutionIdentityAssurancePort(ExecutionIdentityAssurancePort):
    __slots__ = (
        "__route",
        "__route_resolver",
        "__provider_resolver",
        "__provider",
        "__issued",
        "__lock",
    )

    def __init__(
        self,
        *,
        route: SelectedExecutionIdentityRoute,
        route_resolver: Callable[[str], SelectedExecutionIdentityRoute | None],
        provider_resolver: Callable[
            [SelectedExecutionIdentityRoute], ExecutionIdentityAssurancePort | None
        ],
        provider: ExecutionIdentityAssurancePort,
    ) -> None:
        self.__route = route
        self.__route_resolver = route_resolver
        self.__provider_resolver = provider_resolver
        self.__provider = provider
        self.__issued: OrderedDict[
            int,
            tuple[ExecutionIdentityAssurance, str],
        ] = OrderedDict()
        self.__lock = threading.RLock()

    def assure_execution_identity(self) -> ExecutionIdentityAssurance:
        if self.__route.environment is None:
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_route_unavailable"
            )
        if not self.__require_current_route():
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_route_changed"
            )
        try:
            assurance = self.__provider.assure_execution_identity()
        except ExecutionIdentityAssuranceUnavailable:
            raise
        except Exception:
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_provider_unavailable"
            ) from None
        if type(assurance) is not ExecutionIdentityAssurance:
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_projection_invalid"
            )
        try:
            ExecutionIdentityAssurance.__post_init__(assurance)
            if not _assurance_matches_route(assurance, self.__route):
                raise ValueError("identity assurance route does not match")
            digest = _assurance_digest(assurance)
        except Exception:
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_identity_mismatch"
            ) from None
        trusted_now = _utc_now()
        if not _assurance_is_current(assurance, trusted_now):
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_not_current"
            )
        try:
            valid = self.__provider.validate_execution_identity(
                assurance,
                now_utc=trusted_now,
            )
        except Exception:
            valid = False
        if type(valid) is not bool or valid is not True:
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_not_current"
            )
        if not self.__require_current_route() or not _assurance_is_current(
            assurance,
            _utc_now(),
        ):
            raise ExecutionIdentityAssuranceUnavailable(
                "execution_identity_assurance_route_changed"
            )
        with self.__lock:
            self.__issued[id(assurance)] = (assurance, digest)
            self.__issued.move_to_end(id(assurance))
            while len(self.__issued) > _MAX_ISSUED_ASSURANCES:
                self.__issued.popitem(last=False)
        return assurance

    def validate_execution_identity(
        self,
        assurance: ExecutionIdentityAssurance,
        *,
        now_utc: datetime,
    ) -> bool:
        if (
            type(assurance) is not ExecutionIdentityAssurance
            or not _is_utc_datetime(now_utc)
            or not _assurance_matches_route(assurance, self.__route)
        ):
            return False
        with self.__lock:
            issued = self.__issued.get(id(assurance))
        if issued is None or issued[0] is not assurance:
            return False
        try:
            ExecutionIdentityAssurance.__post_init__(assurance)
            if _assurance_digest(assurance) != issued[1]:
                return False
        except Exception:
            return False
        trusted_now = _utc_now()
        if (
            not _assurance_is_current(assurance, trusted_now)
            or not self.__require_current_route()
        ):
            return False
        try:
            valid = self.__provider.validate_execution_identity(
                assurance,
                now_utc=trusted_now,
            )
        except Exception:
            return False
        return (
            type(valid) is bool
            and valid is True
            and self.__require_current_route()
            and _assurance_is_current(assurance, _utc_now())
        )

    def __require_current_route(self) -> bool:
        try:
            current = self.__route_resolver(self.__route.exchange_name)
        except Exception:
            current = None
        if not _same_route(current, self.__route):
            return False
        try:
            return self.__provider_resolver(self.__route) is self.__provider
        except Exception:
            return False

    def __repr__(self) -> str:
        return "<route-bound SDK execution-identity assurance port>"


def _route_entry_points(exchange_name: str) -> tuple[object, ...] | None:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(
                    group=EXECUTION_IDENTITY_ASSURANCE_PROVIDER_ENTRY_POINT_GROUP
                )
            )
        elif isinstance(discovered, dict):
            candidates = tuple(
                discovered.get(EXECUTION_IDENTITY_ASSURANCE_PROVIDER_ENTRY_POINT_GROUP, ())
            )
        else:
            candidates = tuple(
                item
                for item in discovered
                if getattr(item, "group", None)
                == EXECUTION_IDENTITY_ASSURANCE_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        return None
    return tuple(item for item in candidates if getattr(item, "name", None) == exchange_name)


def _entry_point_identity(
    entry_point: object,
    exchange_name: str,
) -> tuple[str, str, str]:
    value = getattr(entry_point, "value", None)
    distribution = getattr(getattr(entry_point, "dist", None), "name", "")
    if (
        type(value) is not str
        or not value
        or type(distribution) is not str
        or getattr(entry_point, "name", None) != exchange_name
    ):
        raise ExecutionIdentityAssuranceUnavailable(
            "execution_identity_assurance_discovery_failed"
        )
    return (exchange_name, value, distribution)


def _require_exchange_name(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ExecutionIdentityAssuranceUnavailable(
            "execution_identity_assurance_route_unavailable"
        )


def _assurance_matches_route(
    assurance: ExecutionIdentityAssurance,
    route: SelectedExecutionIdentityRoute,
) -> bool:
    return (
        type(route) is SelectedExecutionIdentityRoute
        and assurance.exchange_name == route.exchange_name
        and assurance.provider == route.provider
        and assurance.environment == route.environment
        and assurance.account_ref == route.account_ref
        and assurance.identity_binding == route.identity_binding
        and assurance.session_generation == route.session_generation
    )


def _assurance_is_current(
    assurance: ExecutionIdentityAssurance,
    now_utc: datetime,
) -> bool:
    try:
        return (
            _is_utc_datetime(now_utc)
            and assurance.observed_at_utc <= now_utc < assurance.expires_at_utc
        )
    except Exception:
        return False


def _assurance_digest(assurance: ExecutionIdentityAssurance) -> str:
    ExecutionIdentityAssurance.__post_init__(assurance)
    payload = {
        "exchange_name": assurance.exchange_name,
        "provider": assurance.provider,
        "environment": assurance.environment,
        "account_ref": assurance.account_ref,
        "identity_binding": assurance.identity_binding,
        "source_ref": assurance.source_ref,
        "session_generation": assurance.session_generation,
        "observed_at_utc": assurance.observed_at_utc.isoformat(),
        "expires_at_utc": assurance.expires_at_utc.isoformat(),
        "receipt_ref": assurance.receipt_ref,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _is_utc_datetime(value: object) -> bool:
    if type(value) is not datetime or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() == timezone.utc.utcoffset(value)
    except Exception:
        return False


def _same_route(current: object, expected: SelectedExecutionIdentityRoute) -> bool:
    return (
        type(current) is SelectedExecutionIdentityRoute
        and current.exchange_name == expected.exchange_name
        and current.provider == expected.provider
        and current.account_ref == expected.account_ref
        and current.identity_binding == expected.identity_binding
        and current.session_generation == expected.session_generation
        and current.fencing_epoch == expected.fencing_epoch
        and current.client is expected.client
        and current.environment == expected.environment
        and current.ledger_binding is expected.ledger_binding
        and getattr(current, "request_receipt_context", None)
        is getattr(expected, "request_receipt_context", None)
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
