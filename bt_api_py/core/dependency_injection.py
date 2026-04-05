"""
Dependency injection container for modern bt_api_py architecture.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import Any, TypeVar, cast

from bt_api_py._compat import ParamSpec

T = TypeVar("T")
P = ParamSpec("P")

__all__ = [
    "DIContainer",
    "Container",
    "injectable",
    "singleton",
    "scoped",
    "inject",
    "inject_method",
]


class DIContainer:
    """Dependency injection container with lifetime management."""

    def __init__(self) -> None:
        self._services: dict[type, Any] = {}
        self._factories: dict[type, Callable[[], Any]] = {}
        self._singletons: dict[type, Any] = {}
        self._scoped_services: dict[type, Callable[[], Any]] = {}
        self._lock = threading.RLock()
        self._current_scope: dict[type, Any] | None = None

    def register_singleton(self, interface: type[T], implementation: type[T]) -> DIContainer:
        """Register a singleton service."""
        with self._lock:
            self._clear_registration(interface)
            self._services[interface] = implementation
            return self

    def register_transient(self, interface: type[T], implementation: type[T]) -> DIContainer:
        """Register a transient service (new instance each time)."""
        with self._lock:
            self._clear_registration(interface)
            self._factories[interface] = implementation
            return self

    def register_scoped(self, interface: type[T], implementation: type[T]) -> DIContainer:
        """Register a scoped service (one instance per scope)."""
        with self._lock:
            self._clear_registration(interface)
            self._scoped_services[interface] = implementation
            return self

    def register_instance(self, interface: type[T], instance: T) -> DIContainer:
        """Register a specific instance."""
        with self._lock:
            self._clear_registration(interface)
            self._singletons[interface] = instance
            return self

    def resolve(self, interface: type[T]) -> T:
        """Resolve a service instance."""
        with self._lock:
            # Check if we have a pre-registered singleton
            if interface in self._singletons:
                return cast("T", self._singletons[interface])

            # Check scoped services
            if interface in self._scoped_services:
                if self._current_scope is not None and interface in self._current_scope:
                    return cast("T", self._current_scope[interface])

                implementation = self._scoped_services[interface]
                instance = self._create_instance(implementation)
                if self._current_scope is not None:
                    self._current_scope[interface] = instance
                return cast("T", instance)

            # Check if service is registered as singleton
            if interface in self._services:
                implementation = self._services[interface]
                instance = self._create_instance(implementation)
                self._singletons[interface] = instance
                return cast("T", instance)

            # Check if service is registered as transient
            if interface in self._factories:
                implementation = self._factories[interface]
                return cast("T", self._create_instance(implementation))

            raise ValueError(f"Service {interface} not registered")

    def _create_instance(self, implementation: type[T]) -> T:
        """Create instance with dependency injection."""
        # Get constructor signature
        sig = inspect.signature(implementation.__init__)

        # Resolve constructor parameters
        kwargs = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            # Try to resolve parameter from container
            if param.annotation != inspect.Parameter.empty:
                try:
                    kwargs[param_name] = self.resolve(param.annotation)
                except ValueError as exc:
                    # Use default value if available
                    if param.default != inspect.Parameter.empty:
                        kwargs[param_name] = param.default
                    else:
                        raise ValueError(
                            f"Unable to resolve required dependency '{param_name}' "
                            f"for {implementation.__name__}"
                        ) from exc

        return implementation(**kwargs)

    def _clear_registration(self, interface: type[Any]) -> None:
        """Clear previous registrations and cached instances for an interface."""
        self._services.pop(interface, None)
        self._factories.pop(interface, None)
        self._singletons.pop(interface, None)
        self._scoped_services.pop(interface, None)
        if self._current_scope is not None:
            self._current_scope.pop(interface, None)

    @contextmanager
    def create_scope(self) -> Any:
        """Create a new dependency scope."""
        old_scope = self._current_scope
        self._current_scope = {}
        try:
            yield self
        finally:
            self._current_scope = old_scope

    def clear(self) -> None:
        """Clear all registered services."""
        with self._lock:
            self._services.clear()
            self._factories.clear()
            self._singletons.clear()
            self._scoped_services.clear()
            self._current_scope = None


# Global container instance
_global_container = DIContainer()


def injectable(interface: type[T]) -> Callable[[type[T]], type[T]]:
    """Decorator to register a class as injectable."""

    def decorator(cls: type[T]) -> type[T]:
        _global_container.register_transient(interface, cls)
        return cls

    return decorator


def singleton(interface: type[T]) -> Callable[[type[T]], type[T]]:
    """Decorator to register a class as singleton."""

    def decorator(cls: type[T]) -> type[T]:
        _global_container.register_singleton(interface, cls)
        return cls

    return decorator


def scoped(interface: type[T]) -> Callable[[type[T]], type[T]]:
    """Decorator to register a class as scoped."""

    def decorator(cls: type[T]) -> type[T]:
        _global_container.register_scoped(interface, cls)
        return cls

    return decorator


def inject(interface: type[T]) -> T:
    """Inject a dependency."""
    return _global_container.resolve(interface)


def inject_method(func: Callable[P, T]) -> Callable[P, T]:
    """Decorator to inject dependencies into method parameters."""
    sig = inspect.signature(func)

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        # Resolve parameters from container
        for param_name, param in sig.parameters.items():
            if param_name in kwargs:
                continue

            if param.annotation != inspect.Parameter.empty:
                try:
                    kwargs[param_name] = _global_container.resolve(param.annotation)
                except ValueError as exc:
                    if param.default != inspect.Parameter.empty:
                        kwargs[param_name] = param.default
                    else:
                        raise ValueError(
                            f"Unable to resolve required dependency '{param_name}' "
                            f"for {func.__name__}"
                        ) from exc

        return func(*args, **kwargs)

    return wrapper


class Container:
    """Legacy compatibility wrapper."""

    _instance: Container | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> Container:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_initialized"):
            self._container = DIContainer()
            self._initialized = True

    def register(
        self, interface: type[T], implementation: type[T], lifetime: str = "transient"
    ) -> None:
        """Register a service with specified lifetime."""
        if lifetime == "singleton":
            self._container.register_singleton(interface, implementation)
        elif lifetime == "transient":
            self._container.register_transient(interface, implementation)
        elif lifetime == "scoped":
            self._container.register_scoped(interface, implementation)
        else:
            raise ValueError(f"Invalid lifetime: {lifetime}")

    def resolve(self, interface: type[T]) -> T:
        """Resolve a service."""
        return self._container.resolve(interface)

    def create_scope(self) -> Any:
        """Create a new scope."""
        return self._container.create_scope()
