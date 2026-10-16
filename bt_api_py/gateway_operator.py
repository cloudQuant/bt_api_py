"""Trusted operator composition for the normal Gateway ServiceHost path.

This module is the product-side entry for composing one parsed service
configuration with trusted authority, local resources, the Gateway account
session owner, and the existing service-v2 Market Hub. It does not read
credentials or authority from the wire/config manifest. The operator supplies
those capabilities in-process, then calls ``start`` and repeatedly calls
``serve_once`` on the same thread (or uses ``serve_until_stopped``).

Only the configured Gateway role is assembled here. Other fixed service
components are resolved through the operator's existing ``resource_provider``.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Optional

from bt_api_base.service_protocol import WireScope

from .gateway_operator_market import GatewayMarketBinding
from .service_components import ComponentResources, ServiceResourceProvider
from .service_config import ServiceConfig
from .service_host import ServiceHost, TrustedServiceAuthority, TrustedServiceClients
from .service_runtime import ServiceClientRuntime

_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OKX_SOURCE_REF = "okx-public-market-r1"
_OKX_ENTITLEMENT_REF = "public-ticker-r1"


class GatewayOperatorDeploymentError(RuntimeError):
    """Finite, value-free failure from trusted Gateway operator wiring."""

    _CODES = frozenset(
        {
            "INVALID_ACCOUNT_BINDING",
            "INVALID_DEPLOYMENT_CONFIG",
            "INVALID_MARKET_BINDING",
            "INVALID_RESOURCE_PROVIDER",
            "GATEWAY_COMPONENT_REQUIRED",
            "UNSUPPORTED_GATEWAY_ROLE",
            "ACCOUNT_SESSION_OWNER_REQUIRED",
            "ACCOUNT_PROVIDER_SESSION_PORT_REQUIRED",
            "ACCOUNT_SESSION_BINDING_REQUIRED",
            "ACCOUNT_SESSION_REFERENCE_MISMATCH",
            "ACCOUNT_PRINCIPAL_BINDING_INVALID",
            "ACCOUNT_SESSION_WORKER_UNAVAILABLE",
            "ACCOUNT_SESSION_RECOVERY_FAILED",
            "UNUSED_ACCOUNT_SESSION_BINDING",
            "MARKET_BINDING_REQUIRED",
            "MARKET_AUDIENCE_MISMATCH",
            "UNUSED_MARKET_BINDING",
            "OWNER_THREAD_REQUIRED",
            "ACCOUNT_PROVIDER_RESOLUTION_FAILED",
            "ACCOUNT_WORKER_BUILD_FAILED",
            "UNCLAIMED_PROVIDER_CLOSE_UNCONFIRMED",
            "ACCOUNT_RESOURCE_WIRING_FAILED",
            "MARKET_HUB_BUILD_FAILED",
            "MARKET_PROVIDER_CLOSE_UNCONFIRMED",
            "INVALID_STOP_EVENT",
            "INVALID_POLL_INTERVAL",
        }
    )

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in self._CODES:
            code = "GATEWAY_OPERATOR_DEPLOYMENT_FAILED"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GatewayAccountSessionBinding:
    """Trusted, local resources for one Gateway account-session worker.

    ``account_session_ref`` is matched against the parsed Gateway service
    options. The credential reference and resolver stay inside the separately
    injected ``GatewaySessionOwner``; neither is stored in this binding.
    """

    account_session_ref: str
    journal: object = field(repr=False)
    account_key: object = field(repr=False)
    account_scope: object = field(repr=False)
    state_scope: str
    guard: object = field(repr=False)
    required_coverage: tuple[object, ...]
    principals: Mapping[str, object] = field(repr=False)
    view_policies: tuple[object, ...] = field(default=(), repr=False)
    require_open_orders: bool = False
    refresh_interval_seconds: float = 1.0
    max_staleness_seconds: float = 5.0
    request_timeout_seconds: float = 2.0
    quota_admission: object | None = field(default=None, repr=False, compare=False)
    _dispatch_quota_admission: Callable[
        [str, Optional[str], Optional[str], Optional[str], str, int], None
    ] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not _valid_ref(self.account_session_ref) or not _valid_ref(self.state_scope):
            raise GatewayOperatorDeploymentError("INVALID_ACCOUNT_BINDING")
        try:
            from bt_api_base.account_protocol import AccountKey, AccountResourceKey
            from bt_api_gateway.account_view_projection import (
                GatewayAccountViewPolicy,
                validate_policy_set,
            )
            from bt_api_gateway.event_journal import GatewayEventJournal
            from bt_api_gateway.quota_admission import (
                GatewayQuotaAdmission,
                GatewayQuotaDispatchAdmission,
            )
            from bt_api_gateway.router import GatewayPrincipal
            from bt_api_gateway.service_component import GatewayServiceAccountScope
            from bt_api_gateway.session_guard import LocalAccountSessionGuard

            if (
                type(self.journal) is not GatewayEventJournal
                or type(self.account_key) is not AccountKey
                or type(self.account_scope) is not GatewayServiceAccountScope
                or type(self.guard) is not LocalAccountSessionGuard
                or self.guard.account_key_digest
                != hashlib.sha256(self.account_key.account_key_id.encode("ascii")).hexdigest()
                or (
                    self.quota_admission is not None
                    and type(self.quota_admission) is not GatewayQuotaAdmission
                )
            ):
                raise ValueError
            if (
                type(self.required_coverage) is not tuple
                or not self.required_coverage
                or any(type(item) is not AccountResourceKey for item in self.required_coverage)
                or len(set(self.required_coverage)) != len(self.required_coverage)
            ):
                raise ValueError
            if type(self.view_policies) is not tuple or any(
                type(item) is not GatewayAccountViewPolicy for item in self.view_policies
            ):
                raise ValueError
            policies = validate_policy_set(self.view_policies)
            coverage = frozenset(self.required_coverage)
            for policy in policies:
                if policy.view_scope == self.state_scope or not frozenset(
                    policy.resources
                ) <= coverage:
                    raise ValueError
            if type(self.require_open_orders) is not bool:
                raise ValueError
            for timeout in (
                self.refresh_interval_seconds,
                self.max_staleness_seconds,
                self.request_timeout_seconds,
            ):
                if (
                    isinstance(timeout, bool)
                    or not isinstance(timeout, (int, float))
                    or not math.isfinite(float(timeout))
                    or float(timeout) <= 0
                ):
                    raise ValueError
            if not isinstance(self.principals, Mapping):
                raise ValueError
            copied_principals = dict(self.principals)
            if any(
                type(principal_ref) is not str
                or not principal_ref
                or type(principal) is not GatewayPrincipal
                or principal.principal_id != principal_ref
                for principal_ref, principal in copied_principals.items()
            ):
                raise ValueError
            account = self.account_scope
            if (
                account.journal_account_scope != account.journal_account_scope.strip()
                or not account.journal_account_scope
            ):
                raise ValueError
            object.__setattr__(self, "principals", MappingProxyType(copied_principals))
            object.__setattr__(self, "view_policies", policies)
            object.__setattr__(
                self,
                "_dispatch_quota_admission",
                None
                if self.quota_admission is None
                else GatewayQuotaDispatchAdmission(self.quota_admission),
            )
        except GatewayOperatorDeploymentError:
            raise
        except BaseException:
            raise GatewayOperatorDeploymentError("INVALID_ACCOUNT_BINDING") from None

    @property
    def dispatch_quota_admission(
        self,
    ) -> Optional[Callable[[str, Optional[str], Optional[str], Optional[str], str, int], None]]:
        """Return the stable provider-neutral callback for trusted owner wiring.

        The callback uses the same local quota owner as the account provider
        session. Resource providers may explicitly inject it into Risk and
        Execution owners; GatewayOperator does not propagate it implicitly.
        """

        return self._dispatch_quota_admission


@dataclass(frozen=True, slots=True)
class GatewayOkxMarketBinding:
    """Finite trusted audience and provider settings for OKX SWAP tickers."""

    key_universe: tuple[object, ...]
    principal_allowed_keys: Mapping[str, frozenset[object]] = field(repr=False)
    stream_factory: Optional[Callable[[object, object], object]] = field(
        default=None, repr=False, compare=False
    )
    queue_capacity: int = 1_024
    connect_timeout_seconds: float = 30.0
    max_provider_events_per_maintain: int = 32

    def __post_init__(self) -> None:
        try:
            from bt_api_base.market_protocol import MarketSubscriptionKey

            keys = tuple(self.key_universe)
            if (
                not keys
                or len(keys) > 1_024
                or any(type(key) is not MarketSubscriptionKey for key in keys)
                or len(set(keys)) != len(keys)
                or any(not _is_okx_ticker_key(key) for key in keys)
            ):
                raise ValueError
            if (
                type(self.queue_capacity) is not int
                or not 1 <= self.queue_capacity <= 65_536
                or type(self.max_provider_events_per_maintain) is not int
                or not 1 <= self.max_provider_events_per_maintain <= 256
            ):
                raise ValueError
            if (
                isinstance(self.connect_timeout_seconds, bool)
                or not isinstance(self.connect_timeout_seconds, (int, float))
                or not math.isfinite(float(self.connect_timeout_seconds))
                or float(self.connect_timeout_seconds) < 0
                or (self.stream_factory is not None and not callable(self.stream_factory))
            ):
                raise ValueError
            if not isinstance(self.principal_allowed_keys, Mapping):
                raise ValueError
            universe = frozenset(keys)
            audience: dict[str, frozenset[object]] = {}
            for principal_ref, allowed in self.principal_allowed_keys.items():
                if type(principal_ref) is not str or not _valid_ref(principal_ref):
                    raise ValueError
                frozen_allowed = frozenset(allowed)
                if frozen_allowed != universe:
                    raise ValueError
                audience[principal_ref] = frozen_allowed
            if not audience:
                raise ValueError
            object.__setattr__(self, "key_universe", keys)
            object.__setattr__(self, "principal_allowed_keys", MappingProxyType(audience))
            object.__setattr__(self, "connect_timeout_seconds", float(self.connect_timeout_seconds))
        except GatewayOperatorDeploymentError:
            raise
        except BaseException:
            raise GatewayOperatorDeploymentError("INVALID_MARKET_BINDING") from None


class GatewayOperatorDeployment:
    """Compose and run the existing service-v2 Gateway Host on one owner thread.

    The operator creates ``ServiceConfig`` with ``parse_service_config`` and
    resolves ``TrustedServiceAuthority``, database/guard resources, and the
    session owner's secret resolver in trusted application code. No secrets or
    Python factories are loaded from the manifest or client request.
    """

    def __init__(
        self,
        config: ServiceConfig,
        authority: TrustedServiceAuthority,
        registry_path: str | Path,
        *,
        resource_provider: ServiceResourceProvider,
        session_owner: object | None = None,
        account_session: GatewayAccountSessionBinding | None = None,
        okx_market: GatewayOkxMarketBinding | None = None,
        market_binding: GatewayMarketBinding | None = None,
        service_directory: Mapping[str, ServiceConfig] | None = None,
        trusted_clients: TrustedServiceClients | None = None,
        migration_deployment_epoch: str | None = None,
    ) -> None:
        if type(config) is not ServiceConfig:
            raise GatewayOperatorDeploymentError("INVALID_DEPLOYMENT_CONFIG")
        try:
            config.to_public_dict()
        except BaseException:
            raise GatewayOperatorDeploymentError("INVALID_DEPLOYMENT_CONFIG") from None
        if not callable(resource_provider):
            raise GatewayOperatorDeploymentError("INVALID_RESOURCE_PROVIDER")
        gateway = config.components.get("gateway")
        if not isinstance(gateway, Mapping):
            raise GatewayOperatorDeploymentError("GATEWAY_COMPONENT_REQUIRED")
        settings = gateway.get("settings")
        if not isinstance(settings, Mapping):
            raise GatewayOperatorDeploymentError("INVALID_DEPLOYMENT_CONFIG")
        options = settings.get("options")
        ports = settings.get("bind_ports")
        instance_id = settings.get("instance_id")
        if (
            not isinstance(options, Mapping)
            or type(instance_id) is not str
            or type(ports) not in (tuple, list)
        ):
            raise GatewayOperatorDeploymentError("INVALID_DEPLOYMENT_CONFIG")
        role = options.get("role")
        if role == "account_session_worker":
            session_ref = options.get("account_session_ref")
            if "service.provider_session" not in ports:
                raise GatewayOperatorDeploymentError("ACCOUNT_PROVIDER_SESSION_PORT_REQUIRED")
            if account_session is None:
                raise GatewayOperatorDeploymentError("ACCOUNT_SESSION_BINDING_REQUIRED")
            if type(account_session) is not GatewayAccountSessionBinding:
                raise GatewayOperatorDeploymentError("INVALID_ACCOUNT_BINDING")
            if account_session.account_session_ref != session_ref:
                raise GatewayOperatorDeploymentError("ACCOUNT_SESSION_REFERENCE_MISMATCH")
            if not callable(getattr(session_owner, "create_provider", None)):
                raise GatewayOperatorDeploymentError("ACCOUNT_SESSION_OWNER_REQUIRED")
            if okx_market is not None or market_binding is not None:
                raise GatewayOperatorDeploymentError("UNUSED_MARKET_BINDING")
            if "service.account_read" in ports:
                principal_refs = frozenset(authority.principals.values())
                if (
                    not account_session.principals
                    or not set(account_session.principals) <= principal_refs
                    or account_session.account_scope.execution_domain_ref
                    != authority.scope.execution_domain_ref
                    or any(
                        account_session.account_scope.journal_account_scope
                        not in principal.account_scopes
                        for principal in account_session.principals.values()
                    )
                ):
                    raise GatewayOperatorDeploymentError("ACCOUNT_PRINCIPAL_BINDING_INVALID")
            self._session_owner = session_owner
            self._account_session = account_session
            self._okx_market = None
            self._market_binding = None
        elif role == "market_hub":
            if account_session is not None or session_owner is not None:
                raise GatewayOperatorDeploymentError("UNUSED_ACCOUNT_SESSION_BINDING")
            if market_binding is not None:
                if okx_market is not None:
                    raise GatewayOperatorDeploymentError("UNUSED_MARKET_BINDING")
                if type(market_binding) is not GatewayMarketBinding:
                    raise GatewayOperatorDeploymentError("INVALID_MARKET_BINDING")
                allowed_principals = market_binding.principal_allowed_keys
                self._okx_market = None
                self._market_binding = market_binding
            else:
                if type(okx_market) is not GatewayOkxMarketBinding:
                    raise GatewayOperatorDeploymentError("MARKET_BINDING_REQUIRED")
                allowed_principals = okx_market.principal_allowed_keys
                self._okx_market = okx_market
                self._market_binding = None
            if frozenset(allowed_principals) != frozenset(authority.principals.values()):
                raise GatewayOperatorDeploymentError("MARKET_AUDIENCE_MISMATCH")
            self._session_owner = None
            self._account_session = None
        else:
            raise GatewayOperatorDeploymentError("UNSUPPORTED_GATEWAY_ROLE")

        self._config = config
        self._authority = authority
        self._resource_provider = resource_provider
        self._gateway_instance_id = instance_id
        self._gateway_role = role
        self._gateway_ports = frozenset(ports)
        self._registry_path = registry_path
        self._owner_thread_id: int | None = None
        self._account_worker: object | None = None
        self._unclaimed_providers: list[object] = []
        self._last_cleanup_code: str | None = None
        self._cleanup_lock = threading.Lock()
        self._host = ServiceHost(
            config,
            authority,
            registry_path,
            self._provide_resources,
            service_directory=service_directory,
            trusted_clients=trusted_clients,
            migration_deployment_epoch=migration_deployment_epoch,
        )

    @property
    def service_host(self) -> ServiceHost:
        """The composed Host for trusted operator observation/control."""

        return self._host

    @property
    def endpoint(self) -> str | None:
        return self._host.endpoint

    @property
    def market_endpoint(self) -> str | None:
        return self._host.market_endpoint

    @property
    def state(self) -> str:
        return self._host.state

    @property
    def summary(self) -> Mapping[str, Any]:
        return self._host.summary

    @property
    def cleanup_status(self) -> Mapping[str, object]:
        """Finite cleanup facts; provider objects and references stay private."""

        with self._cleanup_lock:
            return MappingProxyType(
                {
                    "unclaimed_provider_count": len(self._unclaimed_providers),
                    "last_cleanup_code": self._last_cleanup_code,
                }
            )

    @property
    def account_session_worker(self) -> object | None:
        """Return the locally owned worker handle on its Host owner thread.

        This is an in-process operator capability. It is never exposed through
        ServiceHost, a client binding, or a wire response.
        """

        self._check_owner_thread()
        if self._gateway_role != "account_session_worker":
            raise GatewayOperatorDeploymentError("UNSUPPORTED_GATEWAY_ROLE")
        return self._account_worker

    @property
    def account_session_status(self) -> Mapping[str, object]:
        """Return finite worker state without provider or credential objects."""

        self._check_owner_thread()
        if self._gateway_role != "account_session_worker":
            raise GatewayOperatorDeploymentError("UNSUPPORTED_GATEWAY_ROLE")
        worker = self._account_worker
        if worker is None:
            return MappingProxyType(
                {
                    "state": "not_created",
                    "session_generation": None,
                    "ready": False,
                    "fresh_for_seconds": 0.0,
                }
            )
        try:
            status = worker.read_status()  # type: ignore[attr-defined]
            state = worker.state  # type: ignore[attr-defined]
            generation = worker.session_generation  # type: ignore[attr-defined]
            ready = status.get("ready")
            fresh_for = status.get("fresh_for_seconds")
            if (
                type(state) is not str
                or (generation is not None and type(generation) is not str)
                or type(ready) is not bool
                or isinstance(fresh_for, bool)
                or not isinstance(fresh_for, (int, float))
                or not math.isfinite(float(fresh_for))
                or float(fresh_for) < 0.0
            ):
                raise ValueError
            return MappingProxyType(
                {
                    "state": state,
                    "session_generation": generation,
                    "ready": ready,
                    "fresh_for_seconds": float(fresh_for),
                }
            )
        except BaseException as exc:
            if isinstance(exc, Exception):
                raise GatewayOperatorDeploymentError(
                    "ACCOUNT_SESSION_WORKER_UNAVAILABLE"
                ) from None
            raise

    def recover_account_session(self) -> Mapping[str, object]:
        """Explicitly recover the account session on the Host owner thread.

        Recovery is local operator control only. The account worker closes and
        releases its current generation before it reacquires a generation and
        connects again; no account credential or provider object crosses the
        service-v2 protocol.
        """

        self._check_owner_thread()
        if self._gateway_role != "account_session_worker":
            raise GatewayOperatorDeploymentError("UNSUPPORTED_GATEWAY_ROLE")
        worker = self._account_worker
        if worker is None:
            raise GatewayOperatorDeploymentError("ACCOUNT_SESSION_WORKER_UNAVAILABLE")
        try:
            worker.recover()  # type: ignore[attr-defined]
        except BaseException as exc:
            if isinstance(exc, Exception):
                raise GatewayOperatorDeploymentError(
                    "ACCOUNT_SESSION_RECOVERY_FAILED"
                ) from None
            raise
        return self.account_session_status

    def start(self) -> GatewayOperatorDeployment:
        """Start the existing Host on the caller's owner thread."""

        self._bind_owner_thread()
        self._host.start()
        return self

    def serve_once(self, timeout_ms: int = 0) -> bool:
        """Serve and maintain the Host on the same thread that started it."""

        self._check_owner_thread()
        return self._host.serve_once(timeout_ms)

    def close(self) -> None:
        """Close the Host on its owner thread, retaining normal Host uncertainty."""

        self._check_owner_thread()
        self._host.close()

    def serve_until_stopped(
        self, stop_event: object, *, poll_interval_ms: int = 100
    ) -> None:
        """Run the Host until a trusted local event is set, then close normally.

        Call this method from the dedicated service-owner thread. The stop
        event may be signalled by an operator control thread, but all Host
        lifecycle calls remain on this method's thread.
        """

        if not callable(getattr(stop_event, "is_set", None)):
            raise GatewayOperatorDeploymentError("INVALID_STOP_EVENT")
        if type(poll_interval_ms) is not int or not 1 <= poll_interval_ms <= 1_000:
            raise GatewayOperatorDeploymentError("INVALID_POLL_INTERVAL")
        self.start()
        try:
            while not stop_event.is_set():  # type: ignore[attr-defined]
                self.serve_once(poll_interval_ms)
        finally:
            if self._host.state not in {"closed", "failed"}:
                self.close()

    def retry_unclaimed_provider_cleanup(self) -> Mapping[str, int]:
        """Retry only providers retained before worker ownership was transferred."""

        self._check_owner_thread()
        with self._cleanup_lock:
            pending = tuple(self._unclaimed_providers)
            self._unclaimed_providers.clear()
        remaining: list[object] = []
        closed = 0
        for provider in pending:
            if _provider_close_confirmed(provider):
                closed += 1
            else:
                remaining.append(provider)
        with self._cleanup_lock:
            self._unclaimed_providers.extend(remaining)
            self._last_cleanup_code = (
                "UNCLAIMED_PROVIDER_CLOSE_UNCONFIRMED" if remaining else None
            )
            return MappingProxyType({"closed": closed, "pending": len(remaining)})

    def _provide_resources(
        self,
        component: str,
        instance_id: str,
        options: Mapping[str, Any],
        scope: WireScope,
    ) -> ComponentResources:
        self._bind_owner_thread()
        if component != "gateway":
            return self._resource_provider(component, instance_id, options, scope)
        if instance_id != self._gateway_instance_id or options.get("role") != self._gateway_role:
            raise GatewayOperatorDeploymentError("INVALID_DEPLOYMENT_CONFIG")
        if self._gateway_role == "market_hub":
            return self._build_market_resources(scope)
        return self._build_account_resources(options)

    def _build_market_resources(self, scope: WireScope) -> ComponentResources:
        binding = self._market_binding
        okx_binding = self._okx_market
        if (
            (binding is None and type(okx_binding) is not GatewayOkxMarketBinding)
            or (binding is not None and type(binding) is not GatewayMarketBinding)
            or type(scope) is not WireScope
        ):
            raise GatewayOperatorDeploymentError("INVALID_MARKET_BINDING")
        try:
            from bt_api_gateway.market_hub import GatewayMarketHub

            if binding is not None:
                provider = binding.provider_factory()
                if not callable(getattr(provider, "subscribe", None)) or not callable(
                    getattr(provider, "unsubscribe", None)
                ):
                    raise TypeError("market provider does not implement the Gateway provider port")
                key_universe = frozenset(binding.key_universe)
                principal_allowed_keys = binding.principal_allowed_keys
                max_provider_events = binding.max_provider_events_per_maintain
            else:
                assert okx_binding is not None
                from bt_api_okx.market_provider import OkxTickerQueueMarketProvider

                provider = OkxTickerQueueMarketProvider(
                    stream_factory=okx_binding.stream_factory,
                    queue_capacity=okx_binding.queue_capacity,
                    connect_timeout=okx_binding.connect_timeout_seconds,
                )
                key_universe = frozenset(okx_binding.key_universe)
                principal_allowed_keys = okx_binding.principal_allowed_keys
                max_provider_events = okx_binding.max_provider_events_per_maintain
            hub = GatewayMarketHub(
                provider,
                key_universe=key_universe,
                principal_allowed_keys=principal_allowed_keys,
                service_identity=self._authority.identity,
                service_scope=self._authority.scope,
                max_provider_events_per_maintain=max_provider_events,
            )

            def close_market_resources() -> None:
                try:
                    hub.close()
                except Exception:
                    _close_market_provider(provider)
                    raise
                _close_market_provider(provider)

            return ComponentResources.owned(
                {"hub": hub},
                ready=lambda: hub.ready is True,
                close=close_market_resources,
            )
        except BaseException as exc:
            if isinstance(exc, Exception):
                raise GatewayOperatorDeploymentError("MARKET_HUB_BUILD_FAILED") from None
            raise

    def _build_account_resources(self, options: Mapping[str, Any]) -> ComponentResources:
        binding = self._account_session
        owner = self._session_owner
        if (
            type(binding) is not GatewayAccountSessionBinding
            or owner is None
            or options.get("account_session_ref") != binding.account_session_ref
        ):
            raise GatewayOperatorDeploymentError("ACCOUNT_SESSION_BINDING_REQUIRED")
        try:
            provider = owner.create_provider(binding.account_session_ref)  # type: ignore[attr-defined]
        except BaseException as exc:
            if isinstance(exc, Exception):
                raise GatewayOperatorDeploymentError(
                    "ACCOUNT_PROVIDER_RESOLUTION_FAILED"
                ) from None
            raise

        try:
            from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker

            if binding.quota_admission is not None:
                from bt_api_gateway.quota_admission import (
                    GatewayQuotaProviderSession,
                    GatewayQuotaSubject,
                )

                provider = GatewayQuotaProviderSession(
                    provider,
                    binding.quota_admission,  # type: ignore[arg-type]
                    GatewayQuotaSubject(account_ref=binding.account_scope.account_ref),
                )

            worker = GatewayAccountSessionWorker(
                provider,
                binding.journal,
                account_key=binding.account_key,
                journal_account_scope=binding.account_scope.journal_account_scope,
                state_scope=binding.state_scope,
                guard=binding.guard,
                required_coverage=binding.required_coverage,
                view_policies=binding.view_policies,
                require_open_orders=binding.require_open_orders,
                refresh_interval_seconds=binding.refresh_interval_seconds,
                max_staleness_seconds=binding.max_staleness_seconds,
                request_timeout_seconds=binding.request_timeout_seconds,
            )
            values: dict[str, object] = {}
            if "service.account_read" in self._gateway_ports:
                values = {
                    "journal": binding.journal,
                    "accounts": {binding.account_scope.account_ref: binding.account_scope},
                    "principals": binding.principals,
                }
            internal_ports: dict[str, object] = {}
            if "service.provider_session" in self._gateway_ports:
                internal_ports["service.provider_session"] = worker
            resources = ComponentResources.owned(
                values,
                ready=lambda: worker.ready is True,
                close=worker.close,
                internal_ports=internal_ports,
            )
            self._account_worker = worker
            return resources
        except BaseException as exc:
            confirmed = _provider_close_confirmed(provider)
            if not confirmed:
                with self._cleanup_lock:
                    self._unclaimed_providers.append(provider)
                    self._last_cleanup_code = "UNCLAIMED_PROVIDER_CLOSE_UNCONFIRMED"
                if isinstance(exc, Exception):
                    raise GatewayOperatorDeploymentError(
                        "UNCLAIMED_PROVIDER_CLOSE_UNCONFIRMED"
                    ) from None
                raise
            if isinstance(exc, Exception):
                raise GatewayOperatorDeploymentError("ACCOUNT_WORKER_BUILD_FAILED") from None
            raise

    def _bind_owner_thread(self) -> None:
        current = threading.get_ident()
        if self._owner_thread_id is None:
            self._owner_thread_id = current
        elif self._owner_thread_id != current:
            raise GatewayOperatorDeploymentError("OWNER_THREAD_REQUIRED")

    def _check_owner_thread(self) -> None:
        if self._owner_thread_id is None or self._owner_thread_id != threading.get_ident():
            raise GatewayOperatorDeploymentError("OWNER_THREAD_REQUIRED")


def _provider_close_confirmed(provider: object) -> bool:
    try:
        close = getattr(provider, "close", None)
        if not callable(close):
            return False
        result = close()
        return result is None or result is True
    except BaseException:
        return False


def _valid_ref(value: object) -> bool:
    return type(value) is str and _SAFE_REF.fullmatch(value) is not None


def _is_okx_ticker_key(key: object) -> bool:
    try:
        instrument = key.instrument  # type: ignore[attr-defined]
        return (
            key.source_ref == _OKX_SOURCE_REF  # type: ignore[attr-defined]
            and key.entitlement_ref == _OKX_ENTITLEMENT_REF  # type: ignore[attr-defined]
            and instrument.provider == "okx"
            and instrument.venue == "OKX"
            and instrument.environment == "live"
            and instrument.product_type == "SWAP"
            and key.channel == "tickers"  # type: ignore[attr-defined]
            and key.depth is None  # type: ignore[attr-defined]
            and key.timeframe is None  # type: ignore[attr-defined]
        )
    except BaseException:
        return False


def _close_market_provider(provider: object) -> None:
    """Retry provider-owned partial-start cleanup after Hub handle release."""
    close = getattr(provider, "close", None)
    if not callable(close):
        return
    try:
        result = close()
    except Exception:
        raise GatewayOperatorDeploymentError("MARKET_PROVIDER_CLOSE_UNCONFIRMED") from None
    if result is False:
        raise GatewayOperatorDeploymentError("MARKET_PROVIDER_CLOSE_UNCONFIRMED")


__all__ = [
    "GatewayAccountSessionBinding",
    "GatewayMarketBinding",
    "GatewayOkxMarketBinding",
    "GatewayOperatorDeployment",
    "GatewayOperatorDeploymentError",
]
