"""Trusted CTP execution-runtime host binding for neutral SDK composition.

This module prepares an opaque ``ManagedRuntimeBinding`` from explicit CTP
runtime dependencies. It deliberately does not contain provider credentials;
the exact ``ProviderConfiguration`` is consumed once by the discovered CTP
provider, which builds the private TraderClient configuration before runtime
bootstrap. The resulting route is simulation-only and owner-thread confined.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from weakref import WeakKeyDictionary

from bt_api_base.managed_runtime_protocol import ManagedRuntimeBinding

__all__ = (
    "CtpManagedRuntimeBootstrapDependenciesV1",
    "create_ctp_managed_runtime_binding",
)


_DEFAULT_TIMEOUT_SECONDS = 5.0
_BOOTSTRAP_OWNER_LOCK = threading.RLock()
_BOOTSTRAP_OWNER_STATES: WeakKeyDictionary[
    object, "_CtpManagedRuntimeBootstrapStateV1"
] = WeakKeyDictionary()


class CtpManagedRuntimeHostError(RuntimeError):
    """Finite, value-free status for a one-shot CTP host lifecycle."""

    _CODES = frozenset(
        {
            "ctp_managed_runtime_binding_invalid",
            "ctp_managed_runtime_host_already_used",
            "ctp_managed_runtime_owner_thread_required",
            "ctp_managed_runtime_not_ready",
            "ctp_managed_runtime_serve_failed",
            "ctp_managed_runtime_cleanup_unconfirmed",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else (
            "ctp_managed_runtime_binding_invalid"
        )
        super().__init__(self.code)


@dataclass(frozen=True, repr=False, eq=False)
class CtpManagedRuntimeBootstrapDependenciesV1:
    """Non-secret trusted inputs used to create one managed CTP runtime.

    Provider credentials and selected TD/MD fronts come from the opaque
    configuration payload, never from this object. These dependencies include
    the real scope, native binding, Basic/Risk factories, and selected-session
    provider dependency factory; they do not themselves grant an operator
    write. The route must separately provide the exact authority source and
    bounded operator grant.
    """

    store_path: str | Path = field(repr=False, compare=False)
    execution_scope: object = field(repr=False, compare=False)
    service_scope: object = field(repr=False, compare=False)
    lease_owner_id: str = field(repr=False, compare=False)
    native_binding: object = field(repr=False, compare=False)
    prepare_trader: Callable[[object], object] = field(repr=False, compare=False)
    basic_admission_factory: Callable[[object, object, object], object] = field(
        repr=False, compare=False
    )
    risk_admission_factory: Callable[[object], Mapping[str, object]] = field(
        repr=False, compare=False
    )
    order_provider_dependency_factory: object = field(repr=False, compare=False)
    cancel_recovery_binding_provider: object | None = field(
        default=None, repr=False, compare=False
    )
    startup_timeout_s: float = field(default=15.0, repr=False, compare=False)
    stop_timeout_s: float = field(default=2.0, repr=False, compare=False)
    lease_ttl_ns: int = field(default=30_000_000_000, repr=False, compare=False)
    callback_pump_events: int = field(default=32, repr=False, compare=False)
    drain_limit: int = field(default=4096, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            (type(self.store_path) is not str and not isinstance(self.store_path, Path))
            or not str(self.store_path).strip()
            or type(self.lease_owner_id) is not str
            or not self.lease_owner_id
            or self.lease_owner_id != self.lease_owner_id.strip()
            or self.execution_scope is None
            or self.service_scope is None
            or self.native_binding is None
            or not callable(self.prepare_trader)
            or not callable(self.basic_admission_factory)
            or not callable(self.risk_admission_factory)
        ):
            raise CtpManagedRuntimeHostError("ctp_managed_runtime_binding_invalid")
        try:
            from bt_api_execution.contracts import ExecutionScope
            from bt_api_base.service_protocol import WireScope
            from bt_api_py.ctp_admission_authority import CtpNativeDispatchBindingV1
            from bt_api_py.ctp_order_provider_dependencies import (
                CtpRuntimeOrderProviderDependenciesFactoryV1,
            )

            valid_dependencies = (
                type(self.execution_scope) is ExecutionScope
                and self.execution_scope.provider == "ctp"
                and self.execution_scope.environment == "simulation"
                and type(self.service_scope) is WireScope
                and type(self.native_binding) is CtpNativeDispatchBindingV1
                and type(self.order_provider_dependency_factory)
                is CtpRuntimeOrderProviderDependenciesFactoryV1
            )
        except Exception:
            valid_dependencies = False
        if not valid_dependencies:
            raise CtpManagedRuntimeHostError("ctp_managed_runtime_binding_invalid")
        if self.cancel_recovery_binding_provider is not None and not callable(
            getattr(self.cancel_recovery_binding_provider, "resolve_verified_binding", None)
        ):
            raise CtpManagedRuntimeHostError("ctp_managed_runtime_binding_invalid")
        for name in ("startup_timeout_s", "stop_timeout_s"):
            value = getattr(self, name)
            if (
                type(value) not in (int, float)
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not 0.0 < float(value) <= 60.0
            ):
                raise CtpManagedRuntimeHostError("ctp_managed_runtime_binding_invalid")
        if (
            type(self.lease_ttl_ns) is not int
            or not 1_000_000 <= self.lease_ttl_ns <= 300_000_000_000
            or type(self.callback_pump_events) is not int
            or not 1 <= self.callback_pump_events <= 256
            or type(self.drain_limit) is not int
            or not 1 <= self.drain_limit <= 100_000
        ):
            raise CtpManagedRuntimeHostError("ctp_managed_runtime_binding_invalid")

    def build_runtime_config(self, trader: object) -> object:
        """Build the runtime config after the provider has consumed secrets."""

        try:
            from bt_api_py.ctp_execution_runtime import (
                CtpExecutionRuntimeConfig,
                CtpTraderConnectionConfig,
            )

            if type(trader) is not CtpTraderConnectionConfig:
                raise CtpManagedRuntimeHostError(
                    "ctp_managed_runtime_binding_invalid"
                )
            return CtpExecutionRuntimeConfig(
                store_path=self.store_path,
                execution_scope=self.execution_scope,
                service_scope=self.service_scope,
                lease_owner_id=self.lease_owner_id,
                trader=trader,
                native_binding=self.native_binding,
                prepare_trader=self.prepare_trader,
                basic_admission_factory=self.basic_admission_factory,
                risk_admission_factory=self.risk_admission_factory,
                cancel_recovery_binding_provider=(
                    self.cancel_recovery_binding_provider
                ),
                startup_timeout_s=float(self.startup_timeout_s),
                stop_timeout_s=float(self.stop_timeout_s),
                lease_ttl_ns=self.lease_ttl_ns,
                callback_pump_events=self.callback_pump_events,
                drain_limit=self.drain_limit,
                order_provider_dependency_factory=(
                    self.order_provider_dependency_factory
                ),
            )
        except CtpManagedRuntimeHostError:
            raise
        except Exception:
            raise CtpManagedRuntimeHostError(
                "ctp_managed_runtime_binding_invalid"
            ) from None


class _CtpManagedRuntimeBootstrapOwnerV1:
    """Fieldless neutral token for one privately registered CTP host state."""

    __slots__ = ("__weakref__",)

    def __repr__(self) -> str:
        return "<opaque CTP managed runtime binding>"


class _CtpManagedRuntimeBootstrapStateV1:
    """Private mutable state for one runtime generation and neutral route."""

    def __init__(
        self,
        dependencies: CtpManagedRuntimeBootstrapDependenciesV1,
        *,
        instance_id: str,
        order_correlation_issuer: object,
        market_stream: object,
        owner_call_timeout_seconds: float,
        instrument_metadata_timeout_seconds: float,
    ) -> None:
        if type(dependencies) is not CtpManagedRuntimeBootstrapDependenciesV1:
            raise CtpManagedRuntimeHostError("ctp_managed_runtime_binding_invalid")
        if type(instance_id) is not str or not instance_id or instance_id != instance_id.strip():
            raise CtpManagedRuntimeHostError("ctp_managed_runtime_binding_invalid")
        try:
            from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
            from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream

            valid_issuer = type(order_correlation_issuer) is DurableOrderCorrelationIssuer
            valid_market_stream = type(market_stream) is CtpMarketStream
        except Exception:
            valid_issuer = False
            valid_market_stream = False
        if not valid_issuer or not valid_market_stream:
            raise CtpManagedRuntimeHostError("ctp_managed_runtime_binding_invalid")
        for value in (owner_call_timeout_seconds, instrument_metadata_timeout_seconds):
            if (
                type(value) not in (int, float)
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not 0.01 <= float(value) <= 30.0
            ):
                raise CtpManagedRuntimeHostError("ctp_managed_runtime_binding_invalid")
        self._dependencies = dependencies
        self._instance_id = instance_id
        self._order_correlation_issuer = order_correlation_issuer
        self._market_stream = market_stream
        self.owner_call_timeout_seconds = float(owner_call_timeout_seconds)
        self.instrument_metadata_timeout_seconds = float(
            instrument_metadata_timeout_seconds
        )
        self._runtime: object | None = None
        self._resources: object | None = None
        self._state = "new"
        self._owner_thread_id: int | None = None
        self._runtime_close_attempted = False
        self._lock = threading.RLock()

    def _claim(self) -> None:
        with self._lock:
            if self._state != "new":
                raise CtpManagedRuntimeHostError(
                    "ctp_managed_runtime_host_already_used"
                )
            self._owner_thread_id = threading.get_ident()
            self._state = "claimed"

    def _install_runtime(self, runtime: object) -> None:
        self._require_owner_thread()
        try:
            from bt_api_py.ctp_execution_runtime import CtpExecutionRuntime

            valid = type(runtime) is CtpExecutionRuntime
        except Exception:
            valid = False
        with self._lock:
            if self._state != "claimed" or self._runtime is not None or not valid:
                raise CtpManagedRuntimeHostError(
                    "ctp_managed_runtime_binding_invalid"
                )
            self._runtime = runtime

    def _install_resources(self, resources: object) -> None:
        """Retain the exact owned resources returned by the SDK provider."""

        self._require_owner_thread()
        try:
            from bt_api_py.service_components import (
                ComponentResources,
                ResourceOwnership,
            )

            valid = (
                type(resources) is ComponentResources
                and resources.ownership is ResourceOwnership.OWNED
                and callable(resources.maintain)
                and callable(resources.close)
            )
        except Exception:
            valid = False
        with self._lock:
            if (
                self._state != "claimed"
                or self._runtime is None
                or self._resources is not None
                or not valid
            ):
                raise CtpManagedRuntimeHostError(
                    "ctp_managed_runtime_binding_invalid"
                )
            self._resources = resources

    def _mark_ready(self) -> None:
        self._require_owner_thread()
        runtime = self._runtime
        if runtime is None or runtime.is_ready() is not True:
            raise CtpManagedRuntimeHostError("ctp_managed_runtime_not_ready")
        with self._lock:
            if self._state != "claimed":
                raise CtpManagedRuntimeHostError("ctp_managed_runtime_not_ready")
            self._state = "ready"

    def _require_owner_thread(self) -> None:
        if self._owner_thread_id is None or threading.get_ident() != self._owner_thread_id:
            raise CtpManagedRuntimeHostError(
                "ctp_managed_runtime_owner_thread_required"
            )

    def _serve_once(self) -> None:
        """Run one maintenance tick; never create a worker or retry it."""

        self._require_owner_thread()
        with self._lock:
            runtime = self._runtime
            resources = self._resources
            if (
                self._state != "ready"
                or self._runtime_close_attempted
                or runtime is None
                or resources is None
            ):
                raise CtpManagedRuntimeHostError("ctp_managed_runtime_not_ready")
        try:
            resources.maintain(frozenset())
        except Exception:
            with self._lock:
                self._state = "serve_failed"
            raise CtpManagedRuntimeHostError(
                "ctp_managed_runtime_serve_failed"
            ) from None
        try:
            ready = runtime.is_ready()
        except Exception:
            ready = False
        if type(ready) is not bool or ready is not True:
            with self._lock:
                self._state = "serve_failed"
            raise CtpManagedRuntimeHostError(
                "ctp_managed_runtime_serve_failed"
            )

    def _close_runtime_once(self) -> None:
        """Close the runtime on its owner thread and preserve uncertainty."""

        with self._lock:
            if self._state == "new" and self._runtime is None:
                self._runtime_close_attempted = True
                self._state = "closed"
                return
        self._require_owner_thread()
        with self._lock:
            if self._runtime_close_attempted:
                raise CtpManagedRuntimeHostError(
                    "ctp_managed_runtime_cleanup_unconfirmed"
                )
            self._runtime_close_attempted = True
            runtime = self._runtime
            resources = self._resources
        if runtime is None:
            with self._lock:
                self._state = "closed"
            return
        try:
            if resources is None:
                runtime.close()
            else:
                resources.close()
        except Exception:
            with self._lock:
                self._state = "close_unconfirmed"
            raise CtpManagedRuntimeHostError(
                "ctp_managed_runtime_cleanup_unconfirmed"
            ) from None
        if getattr(runtime, "_closed", None) is not True:
            with self._lock:
                self._state = "close_unconfirmed"
            raise CtpManagedRuntimeHostError(
                "ctp_managed_runtime_cleanup_unconfirmed"
            )
        with self._lock:
            self._state = "closed"

    def __repr__(self) -> str:
        return "<private CTP managed runtime bootstrap state>"


def _resolve_ctp_managed_runtime_bootstrap_owner(
    owner: object,
) -> _CtpManagedRuntimeBootstrapStateV1 | None:
    """Resolve only an exact opaque token for the provider's private factory."""

    if type(owner) is not _CtpManagedRuntimeBootstrapOwnerV1:
        return None
    with _BOOTSTRAP_OWNER_LOCK:
        return _BOOTSTRAP_OWNER_STATES.get(owner)


def create_ctp_managed_runtime_binding(
    dependencies: CtpManagedRuntimeBootstrapDependenciesV1,
    *,
    instance_id: str,
    order_correlation_issuer: object,
    market_stream: object,
    owner_call_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    instrument_metadata_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> ManagedRuntimeBinding:
    """Create the neutral carrier consumed by route-discovered SDK providers.

    This CTP-only host setup object contains trusted runtime hooks but no
    credentials, provider config payload, grant, or dispatch authorization.
    The returned neutral binding is accepted by the SDK composer at
    ``bt_api_py.managed_runtime_composition.compose_managed_runtime``. This
    module does not wire that composer into Backtrader's host lifecycle.
    """

    try:
        state = _CtpManagedRuntimeBootstrapStateV1(
            dependencies,
            instance_id=instance_id,
            order_correlation_issuer=order_correlation_issuer,
            market_stream=market_stream,
            owner_call_timeout_seconds=owner_call_timeout_seconds,
            instrument_metadata_timeout_seconds=instrument_metadata_timeout_seconds,
        )
        owner = _CtpManagedRuntimeBootstrapOwnerV1()
        with _BOOTSTRAP_OWNER_LOCK:
            _BOOTSTRAP_OWNER_STATES[owner] = state
        return ManagedRuntimeBinding(
            exchange_name="CTP___FUTURE",
            environment="simulation",
            runtime_owner=owner,
        )
    except CtpManagedRuntimeHostError:
        raise
    except Exception:
        raise CtpManagedRuntimeHostError(
            "ctp_managed_runtime_binding_invalid"
        ) from None
