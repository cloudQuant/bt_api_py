"""Exact-route provider configuration facade.

Provider packages own private configuration parsing and route-specific option
validation.  Consumers receive a redacted, provider-neutral descriptor and
pass it back to the SDK when they need to construct a client.  This module
does not infer a provider from a configuration file or exchange-name prefix.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import metadata as importlib_metadata
from pathlib import Path
from threading import RLock
from typing import Any

from bt_api_base.provider_configuration_protocol import (
    ProviderConfiguration,
    ProviderConfigurationError,
    ProviderConfigurationFactory,
    ProviderConfigurationHandle,
    ProviderConfigurationRegistryPort,
    ProviderConfigurationRequest,
    ProviderConfigurationSelection,
)


PROVIDER_CONFIGURATION_ENTRY_POINT_GROUP = "bt_api.provider_configurations"

class ProviderConfigurationRegistry(ProviderConfigurationRegistryPort):
    """Immutable exact-route configuration factory registry."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._factories: dict[str, ProviderConfigurationFactory] = {}
        self._sources: dict[str, tuple[str, str, str] | None] = {}

    def register(self, exchange_name: str, factory: ProviderConfigurationFactory) -> None:
        _require_exchange_name(exchange_name)
        if not callable(factory):
            raise ProviderConfigurationError("PROVIDER_CONFIGURATION_ENTRY_POINT_INVALID")
        with self._lock:
            if exchange_name in self._factories:
                raise ProviderConfigurationError("PROVIDER_CONFIGURATION_CAPABILITY_CONFLICT")
            self._factories[exchange_name] = factory
            self._sources[exchange_name] = None

    def has_capability(self, exchange_name: str) -> bool:
        _require_exchange_name(exchange_name)
        with self._lock:
            return exchange_name in self._factories

    def configure(self, request: ProviderConfigurationRequest) -> ProviderConfiguration:
        if type(request) is not ProviderConfigurationRequest:
            raise ProviderConfigurationError("PROVIDER_CONFIGURATION_REQUEST_INVALID")
        with self._lock:
            factory = self._factories.get(request.exchange_name)
        if factory is None:
            raise ProviderConfigurationError("PROVIDER_CONFIGURATION_CAPABILITY_UNAVAILABLE")
        try:
            result = factory(request)
        except ProviderConfigurationError:
            raise
        except Exception:
            raise ProviderConfigurationError("PROVIDER_CONFIGURATION_FAILED") from None
        _validate_result(result, request)
        return result

    def _register_entry_point(
        self,
        exchange_name: str,
        factory: ProviderConfigurationFactory,
        source: tuple[str, str, str],
    ) -> None:
        self.register(exchange_name, factory)
        with self._lock:
            self._sources[exchange_name] = source

    def _entry_point_source(self, exchange_name: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._sources.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


class ProviderConfigurationRouter:
    """Load only the unique configuration entry point for one exact route."""

    def __init__(self, registry: ProviderConfigurationRegistry | None = None) -> None:
        self._registry = _select_registry(registry)
        self._lock = RLock()

    def configure(self, request: ProviderConfigurationRequest) -> ProviderConfiguration:
        if type(request) is not ProviderConfigurationRequest:
            raise ProviderConfigurationError("PROVIDER_CONFIGURATION_REQUEST_INVALID")
        self._load_selected_provider(request.exchange_name)
        return self._registry.configure(request)

    def _load_selected_provider(self, exchange_name: str) -> None:
        candidates = _route_entry_points(exchange_name)
        if len(candidates) > 1:
            raise ProviderConfigurationError("PROVIDER_CONFIGURATION_ENTRY_POINT_CONFLICT")
        if not candidates:
            if self._registry.has_capability(exchange_name):
                return
            raise ProviderConfigurationError("PROVIDER_CONFIGURATION_CAPABILITY_UNAVAILABLE")
        entry_point = candidates[0]
        source = _entry_point_identity(entry_point, exchange_name)
        with self._lock:
            if self._registry.has_capability(exchange_name):
                if self._registry._entry_point_source(exchange_name) == source:
                    return
                raise ProviderConfigurationError("PROVIDER_CONFIGURATION_CAPABILITY_CONFLICT")
            try:
                registrar = entry_point.load()
            except Exception:
                raise ProviderConfigurationError("PROVIDER_CONFIGURATION_IMPORT_FAILED") from None
            if not callable(registrar):
                raise ProviderConfigurationError("PROVIDER_CONFIGURATION_ENTRY_POINT_INVALID")
            isolated = ProviderConfigurationRegistry()
            try:
                registrar(isolated)
            except Exception:
                raise ProviderConfigurationError(
                    "PROVIDER_CONFIGURATION_REGISTRATION_FAILED"
                ) from None
            if isolated._registered_routes() != (exchange_name,):
                raise ProviderConfigurationError(
                    "PROVIDER_CONFIGURATION_REGISTRATION_SCOPE_INVALID"
                )
            with isolated._lock:
                factory = isolated._factories[exchange_name]
            try:
                self._registry._register_entry_point(exchange_name, factory, source)
            except ProviderConfigurationError:
                raise ProviderConfigurationError(
                    "PROVIDER_CONFIGURATION_CAPABILITY_CONFLICT"
                ) from None


_DEFAULT_REGISTRY = ProviderConfigurationRegistry()


def register_provider_configuration_factory(
    exchange_name: str,
    factory: ProviderConfigurationFactory,
    *,
    registry: ProviderConfigurationRegistry | None = None,
) -> None:
    _select_registry(registry).register(exchange_name, factory)


def load_provider_configuration(
    exchange_name: str,
    config_path: Path | None = None,
    *,
    selection: ProviderConfigurationSelection | None = None,
    credential_values: Mapping[str, str] | None = None,
    credential_names: Mapping[str, str] | None = None,
    credential_env_file: Path | None = None,
    registry: ProviderConfigurationRegistry | None = None,
) -> ProviderConfiguration:
    request = ProviderConfigurationRequest(
        exchange_name=exchange_name,
        config_path=config_path,
        selection=selection,
        credential_values={} if credential_values is None else credential_values,
        credential_names={} if credential_names is None else credential_names,
        credential_env_file=credential_env_file,
    )
    if registry is None:
        return _DEFAULT_ROUTER.configure(request)
    return ProviderConfigurationRouter(registry).configure(request)


def create_configured_api(configuration: ProviderConfiguration, *, debug: bool = False) -> Any:
    """Reject unsafe direct construction from a provider configuration.

    A neutral ``BtApi`` client accepts route options in the framework-facing
    process and cannot consume provider-owned secrets without exposing them to
    that process. Runtime construction belongs to the exact registered
    provider's managed-runtime factory instead.
    """

    if type(configuration) is not ProviderConfiguration or type(debug) is not bool:
        raise ProviderConfigurationError("PROVIDER_CONFIGURATION_RESULT_INVALID")
    raise ProviderConfigurationError("PROVIDER_CONFIGURATION_CAPABILITY_UNAVAILABLE")


def default_provider_configuration_registry() -> ProviderConfigurationRegistry:
    return _DEFAULT_REGISTRY


def _select_registry(registry: ProviderConfigurationRegistry | None) -> ProviderConfigurationRegistry:
    if registry is None:
        return _DEFAULT_REGISTRY
    if type(registry) is not ProviderConfigurationRegistry:
        raise ProviderConfigurationError("PROVIDER_CONFIGURATION_REQUEST_INVALID")
    return registry


def _route_entry_points(exchange_name: str) -> tuple[object, ...]:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=PROVIDER_CONFIGURATION_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(discovered.get(PROVIDER_CONFIGURATION_ENTRY_POINT_GROUP, ()))
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None)
                == PROVIDER_CONFIGURATION_ENTRY_POINT_GROUP
            )
    except Exception:
        raise ProviderConfigurationError("PROVIDER_CONFIGURATION_FAILED") from None
    return tuple(
        entry_point
        for entry_point in candidates
        if getattr(entry_point, "name", None) == exchange_name
    )


def _entry_point_identity(entry_point: object, exchange_name: str) -> tuple[str, str, str]:
    value = getattr(entry_point, "value", None)
    distribution = getattr(entry_point, "dist", None)
    distribution_name = getattr(distribution, "name", "") if distribution is not None else ""
    if (
        type(value) is not str
        or not value
        or type(distribution_name) is not str
        or getattr(entry_point, "name", None) != exchange_name
    ):
        raise ProviderConfigurationError("PROVIDER_CONFIGURATION_ENTRY_POINT_INVALID")
    return exchange_name, value, distribution_name


def _validate_result(result: Any, request: ProviderConfigurationRequest) -> None:
    if type(result) is not ProviderConfiguration or result.exchange_name != request.exchange_name:
        raise ProviderConfigurationError("PROVIDER_CONFIGURATION_RESULT_INVALID")
    selection = request.selection
    if selection is not None and (
        result.environment != selection.environment
        or result.instrument != selection.instrument
        or result.profile_ref != selection.profile_ref
        or (
            selection.provider_profile_handle is not None
            and result.provider_configuration_handle is not selection.provider_profile_handle
        )
    ):
        raise ProviderConfigurationError("PROVIDER_CONFIGURATION_RESULT_INVALID")
    try:
        ProviderConfiguration.__post_init__(result)
    except Exception:
        raise ProviderConfigurationError("PROVIDER_CONFIGURATION_RESULT_INVALID") from None


def _require_exchange_name(value: object) -> None:
    _require_text(value, "exchange_name")


def _require_text(value: object, name: str) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
        or len(value.encode("utf-8", errors="strict")) > 256
    ):
        raise ProviderConfigurationError("PROVIDER_CONFIGURATION_ROUTE_INVALID")


# Built after every helper it calls (``_select_registry``) is defined.
_DEFAULT_ROUTER = ProviderConfigurationRouter(_DEFAULT_REGISTRY)


__all__ = [
    "PROVIDER_CONFIGURATION_ENTRY_POINT_GROUP",
    "ProviderConfiguration",
    "ProviderConfigurationError",
    "ProviderConfigurationHandle",
    "ProviderConfigurationSelection",
    "ProviderConfigurationFactory",
    "ProviderConfigurationRegistryPort",
    "ProviderConfigurationRegistry",
    "ProviderConfigurationRequest",
    "ProviderConfigurationRouter",
    "default_provider_configuration_registry",
    "create_configured_api",
    "load_provider_configuration",
    "register_provider_configuration_factory",
]
