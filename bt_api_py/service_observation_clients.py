"""SDK-owned read-only service observation clients and runtime lifetime.

Backtrader Store/Feed bindings are supplied by a separate framework adapter.
The selected provider resolves native instrument metadata through an explicit
route registration; this module never interprets exchange-native payloads.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from bt_api_py.provider_entrypoints import load_route_entrypoint
from bt_api_py.service_observation_assembly import (
    ServiceObservationError,
    ServiceObservationInstrument,
    _build_service_observation_runtime,
    parse_service_account_configuration,
    validate_service_observation_config,
)

_KEEPALIVE: list[tuple[Any, ...]] = []
_KEEPALIVE_LOCK = threading.Lock()
_NO_BOOTSTRAP = object()


def _retain_uncertain_resources(runtime: Any, account_client: Any,
                                clients: Mapping[str, Any]) -> None:
    resources = (runtime, account_client, clients)
    with _KEEPALIVE_LOCK:
        if any(
            type(retained) is tuple
            and len(retained) == len(resources)
            and all(held is current for held, current in zip(retained, resources, strict=True))
            for retained in _KEEPALIVE
        ):
            return
        _KEEPALIVE.append(resources)


def _market_clients_never_started(clients: Mapping[str, Any]) -> bool:
    """Prove this group never created a market producer before close."""
    from bt_api_py.service_market import ServiceMarketClient

    return isinstance(clients, Mapping) and all(
        type(client) is ServiceMarketClient
        and getattr(client, "_state", None) == "new"
        and getattr(client, "_subscriber", None) is None
        and getattr(client, "_remote_active", None) is False
        and getattr(client, "_remote_unknown", None) is False
        and getattr(client, "_unsubscription_uncertain", None) is False
        and getattr(client, "_uncertain_registration_id", None) is None
        for client in clients.values()
    )


def _local_facades_closed(account_client: Any, clients: Mapping[str, Any]) -> bool:
    """Check concrete SDK facade state after SDK-owned close calls."""
    from bt_api_py.service_account import ServiceAccountClient
    from bt_api_py.service_market import ServiceMarketClient

    account_closed = account_client is None or (
        type(account_client) is ServiceAccountClient
        and getattr(account_client, "_closed", None) is True
        and getattr(account_client, "_current_view", None) is None
        and getattr(account_client, "_last_view", None) is None
        and getattr(account_client, "_scope_epoch", None) is None
        and getattr(account_client, "_history_by_generation", None) == {}
        and getattr(account_client, "_history_bytes", None) == 0
    )
    market_closed = isinstance(clients, Mapping) and all(
        type(client) is ServiceMarketClient
        and getattr(client, "_state", None) == "closed"
        and getattr(client, "_subscriber", None) is None
        and getattr(client, "_remote_active", None) is False
        and getattr(client, "_remote_unknown", None) is False
        and getattr(client, "_unsubscription_uncertain", None) is False
        and getattr(client, "_uncertain_registration_id", None) is None
        for client in clients.values()
    )
    return account_closed and market_closed


def _unconfirmed_report(report: Mapping[str, Any], reason: str) -> Mapping[str, Any]:
    value = dict(report)
    value["status"] = "UNCONFIRMED"
    value["facades_closed"] = False
    value["errors"] = (*tuple(value.get("errors", ())), reason)
    return MappingProxyType(value)


class _ServiceObservationAdmissionLease:
    """One reserved owner-group slot and its strong references."""

    __slots__ = ("_admission", "_owners", "_released")

    def __init__(self, admission: ServiceObservationAdmission) -> None:
        self._admission = admission
        self._owners: tuple[Any, ...] = ()
        self._released = False

    def retain(self, *owners: Any) -> None:
        with self._admission._lock:
            if not self._released:
                self._owners = tuple(owners)

    def release_after_confirmed_cleanup(self) -> None:
        with self._admission._lock:
            if self._released:
                return
            self._released = True
            self._admission._leases.discard(self)
            self._owners = ()


class ServiceObservationAdmission:
    """Shared fail-fast cap for admitted service-observation owner groups.

    Pass the same instance to every SDK opener that should share one capacity
    budget. Its slots cover reservations, active groups, and groups whose
    cleanup is not confirmed. Started market producers lack a verified
    quiescence contract, so those groups remain occupied after close. This is
    a bound on this SDK admission path, not a process-wide cap for callers that
    omit the shared instance. Omitting admission preserves uncapped behavior.
    """

    __slots__ = ("_capacity", "_lock", "_leases")

    def __init__(self, max_groups: int) -> None:
        if type(max_groups) is not int or max_groups <= 0:
            raise ServiceObservationError("SERVICE_ADMISSION_CAPACITY_INVALID")
        self._capacity = max_groups
        self._lock = threading.Lock()
        self._leases: set[_ServiceObservationAdmissionLease] = set()

    @property
    def max_groups(self) -> int:
        return self._capacity

    @property
    def in_use(self) -> int:
        with self._lock:
            return len(self._leases)

    def _reserve(self) -> _ServiceObservationAdmissionLease:
        with self._lock:
            if len(self._leases) >= self._capacity:
                raise ServiceObservationError("SERVICE_RESOURCE_CAPACITY_EXHAUSTED")
            lease = _ServiceObservationAdmissionLease(self)
            self._leases.add(lease)
            return lease


def _close_resources(runtime: Any, account_client: Any, clients: Mapping[str, Any], *,
                     close_facades: bool, facades_confirmed: bool,
                     facade_errors: tuple[str, ...] = ()) -> Mapping[str, Any]:
    errors: list[str] = list(facade_errors)
    if close_facades:
        for client in clients.values():
            try:
                client.close()
            except Exception:
                errors.append("market_client_close_failed")
        if account_client is not None:
            try:
                account_client.close()
            except Exception:
                errors.append("account_client_close_failed")
        facades_confirmed = not errors
    try:
        runtime.close()
    except Exception:
        errors.append("runtime_close_failed")
    runtime_closed = getattr(runtime, "state", None) == "closed"
    uncertain = (
        not facades_confirmed
        or not runtime_closed
        or getattr(runtime, "cleanup_failures", 1) != 0
        or getattr(runtime, "unregister_uncertainties", 1) != 0
        or getattr(runtime, "unknown_registration_count", 1) != 0
        or bool(errors)
    )
    result = MappingProxyType(
        {
            "status": "UNCONFIRMED" if uncertain else "PASS",
            "facades_closed": facades_confirmed,
            "runtime_closed": runtime_closed,
            "runtime_cleanup_failures": getattr(runtime, "cleanup_failures", None),
            "runtime_unregister_uncertainties": getattr(runtime, "unregister_uncertainties", None),
            "runtime_unknown_registrations": getattr(runtime, "unknown_registration_count", None),
            "errors": tuple(errors),
        }
    )
    if uncertain:
        _retain_uncertain_resources(runtime, account_client, clients)
    return result


class ServiceObservationClients:
    """Owner-thread handle for a service runtime and its neutral read facades."""

    def __init__(self, *, runtime: Any, account_client: Any, account_view: Any,
                 metadata: Mapping[str, Any], clients: Mapping[str, Any],
                 keys: Mapping[str, Any], resources: Mapping[str, Any],
                 account_config: Mapping[str, Any],
                 admission_lease: _ServiceObservationAdmissionLease | None = None) -> None:
        self.runtime = runtime
        self.account_client = account_client
        self.account_view = account_view
        self.metadata = MappingProxyType(dict(metadata))
        self.market_clients = MappingProxyType(dict(clients))
        self.market_keys = MappingProxyType(dict(keys))
        self.resources = MappingProxyType(dict(resources))
        self.account_config = MappingProxyType(dict(account_config))
        self._admission_lease = admission_lease
        self._owner_thread = threading.get_ident()
        self._close_report: Mapping[str, Any] | None = None
        self._admission_close_in_progress = False
        self._admission_close_uncertain = False
        self._started: set[str] = set()
        self._bootstraps: dict[str, Any] = {}

    def assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise ServiceObservationError("SERVICE_SESSION_WRONG_THREAD")

    @property
    def close_report(self) -> Mapping[str, Any] | None:
        """Return the recorded SDK resource-close result, if a close was attempted."""
        return self._close_report

    def refresh_account(self) -> Any:
        self.assert_owner()
        if self._close_report is not None:
            raise ServiceObservationError("SERVICE_SESSION_CLOSED")
        self.account_view = self.account_client.refresh()
        return self.account_view

    def _market_client(self, subscription_id: str) -> Any:
        self.assert_owner()
        if self._close_report is not None:
            raise ServiceObservationError("SERVICE_SESSION_CLOSED")
        client = self.market_clients.get(subscription_id)
        if client is None:
            raise ServiceObservationError("SERVICE_INSTRUMENT_UNKNOWN")
        return client

    def is_subscribed(self, subscription_id: str) -> bool:
        self._market_client(subscription_id)
        return subscription_id in self._started

    def _record_subscription(self, subscription_id: str, bootstrap: Any = _NO_BOOTSTRAP) -> Any:
        client = self._market_client(subscription_id)
        if bootstrap is not _NO_BOOTSTRAP:
            if (
                getattr(client, "healthy", None) is not True
                or getattr(client, "recovery_required", None) is not False
                or getattr(bootstrap, "stream_id", None) != client.key.stream_id
            ):
                raise ServiceObservationError("SERVICE_MARKET_START_UNHEALTHY")
            self._bootstraps[subscription_id] = bootstrap
            descriptor = getattr(bootstrap, "source_descriptor", None)
            if descriptor != getattr(client, "source_descriptor", None):
                raise ServiceObservationError("SERVICE_SOURCE_DESCRIPTOR_CHANGED")
        else:
            descriptor = getattr(client, "source_descriptor", None)
        if getattr(client, "_require_source_descriptor", False) is True and descriptor is None:
            raise ServiceObservationError("SERVICE_SOURCE_DESCRIPTOR_UNAVAILABLE")
        if descriptor is not None and (
            descriptor.key != client.key
            or descriptor.stream_id != client.key.stream_id
            or (bootstrap is not _NO_BOOTSTRAP and descriptor.epoch != bootstrap.snapshot_epoch)
        ):
            raise ServiceObservationError("SERVICE_SOURCE_DESCRIPTOR_MISMATCH")
        self._started.add(subscription_id)
        return getattr(client, "source_descriptor", None)

    def subscribe(self, subscription_id: str) -> Any:
        client = self._market_client(subscription_id)
        if subscription_id in self._started:
            return getattr(client, "source_descriptor", None)
        return self._record_subscription(subscription_id, client.start())

    def accept_external_subscription(self, subscription_id: str) -> Any:
        """Record a client started by an explicitly injected framework bridge."""
        client = self._market_client(subscription_id)
        if subscription_id in self._started:
            return getattr(client, "source_descriptor", None)
        return self._record_subscription(subscription_id)

    def initial_snapshot(self, subscription_id: str) -> Any:
        self._market_client(subscription_id)
        bootstrap = self._bootstraps.get(subscription_id)
        if bootstrap is None:
            return None
        if subscription_id not in self._started:
            raise ServiceObservationError("SERVICE_MARKET_NOT_STARTED")
        return getattr(bootstrap, "snapshot", None)

    def poll(self, subscription_id: str, *, timeout_ms: int = 0) -> Any:
        client = self._market_client(subscription_id)
        if subscription_id not in self._started:
            self.subscribe(subscription_id)
        try:
            return client.poll(timeout_ms=timeout_ms)
        except Exception:
            raise ServiceObservationError("SERVICE_MARKET_POLL_FAILED") from None

    def descriptor(self, subscription_id: str) -> Any:
        return getattr(self._market_client(subscription_id), "source_descriptor", None)

    def close(self, *, close_facades: bool = True, facades_confirmed: bool = True,
              facade_errors: tuple[str, ...] = ()) -> Mapping[str, Any]:
        """Close SDK resources once, or record a caller-owned facade close."""
        self.assert_owner()
        if self._close_report is not None:
            return self._close_report
        admission_enabled = self._admission_lease is not None
        if not admission_enabled:
            # Preserve the legacy unbounded path and its caller-owned facade
            # contract. The admission mode below deliberately requires stronger
            # local evidence before returning a slot.
            result = _close_resources(
                self.runtime, self.account_client, self.market_clients,
                close_facades=close_facades, facades_confirmed=facades_confirmed,
                facade_errors=facade_errors,
            )
            self._close_report = result
            if result["status"] != "PASS":
                raise ServiceObservationError("SERVICE_CLEANUP_UNCONFIRMED")
            return result
        if admission_enabled and self._admission_close_uncertain:
            raise ServiceObservationError("SERVICE_CLEANUP_UNCONFIRMED")
        if admission_enabled and self._admission_close_in_progress:
            self._admission_close_uncertain = True
            _retain_uncertain_resources(self.runtime, self.account_client, self.market_clients)
            self._admission_lease.retain(
                self.runtime, self.account_client, self.market_clients,
            )
            raise ServiceObservationError("SERVICE_CLEANUP_UNCONFIRMED")
        never_started = _market_clients_never_started(self.market_clients)
        self._admission_close_in_progress = True
        if not close_facades:
            # A caller boolean is not proof that Backtrader's proxy/Store has
            # stopped using these SDK clients.
            facades_confirmed = False
        try:
            result = _close_resources(
                self.runtime, self.account_client, self.market_clients,
                close_facades=close_facades, facades_confirmed=facades_confirmed,
                facade_errors=facade_errors,
            )
            if self._admission_close_uncertain:
                result = _unconfirmed_report(result, "reentrant_close_unverified")
            elif not close_facades:
                result = _unconfirmed_report(result, "external_facade_close_unverified")
            elif not never_started:
                # The default market factory has no verified producer
                # quiescence contract. A started client remains uncertain
                # even if its local close method returns successfully.
                result = _unconfirmed_report(result, "market_producer_quiescence_unverified")
            elif result["status"] == "PASS" and not _local_facades_closed(
                self.account_client, self.market_clients,
            ):
                result = _unconfirmed_report(result, "local_facade_close_state_unverified")
            if result["status"] != "PASS":
                self._close_report = result
                self._admission_close_uncertain = True
                _retain_uncertain_resources(
                    self.runtime, self.account_client, self.market_clients,
                )
                self._admission_lease.retain(
                    self.runtime, self.account_client, self.market_clients,
                )
                raise ServiceObservationError("SERVICE_CLEANUP_UNCONFIRMED")
            # Capacity returns only after the complete SDK-owned local cleanup
            # predicate is confirmed.
            self._admission_lease.release_after_confirmed_cleanup()
            self._close_report = result
            return result
        except BaseException:
            if self._close_report is None:
                # With admission, an interrupted or reentrant close is sticky:
                # the opaque local owners and capacity cannot be replayed or
                # released without a separate reconciliation receipt.
                self._admission_close_uncertain = True
                _retain_uncertain_resources(self.runtime, self.account_client, self.market_clients)
                self._admission_lease.retain(
                    self.runtime, self.account_client, self.market_clients,
                )
            raise
        finally:
            if admission_enabled:
                self._admission_close_in_progress = False


def _explicit_market_key(item: ServiceObservationInstrument, market: Mapping[str, Any]) -> Any:
    from bt_api_base.market_protocol import MarketSubscriptionKey

    try:
        raw = item.market_key
        key = raw if type(raw) is MarketSubscriptionKey else MarketSubscriptionKey.from_dict(raw)
    except Exception:
        raise ServiceObservationError("SERVICE_MARKET_KEY_INVALID") from None
    if key.source_ref != market["source_ref"] or key.entitlement_ref != market["entitlement_ref"]:
        raise ServiceObservationError("SERVICE_MARKET_SCOPE_MISMATCH")
    return key


def open_service_observation_clients(
    service_config: Mapping[str, Any],
    *,
    instruments: tuple[ServiceObservationInstrument, ...] | list[ServiceObservationInstrument],
    provider_route: str | None = None,
    allow_write: bool = False,
    environ: Mapping[str, str] | None = None,
    timeout_seconds: float = 10.0,
    admission: ServiceObservationAdmission | None = None,
) -> ServiceObservationClients:
    """Compose an explicit read-only client runtime without creating a Store.

    The account ``snapshot_format`` is ``typed`` by default (iteration 60.08,
    G5): the bounded Base map contract, at most 64 KiB and 256 entries per
    container, with canonical decimal spelling required. Unsupported or
    unrepresentable typed snapshots fail without fallback. ``generic`` is an
    explicitly listed legacy selection (``GENERIC_SERVICE_OPERATIONS``).
    """
    if allow_write is not False:
        raise ServiceObservationError("SERVICE_OBSERVATION_WRITE_UNSUPPORTED")
    if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0 or timeout_seconds > 300:
        raise ServiceObservationError("SERVICE_TIMEOUT_INVALID")
    if type(instruments) not in (tuple, list) or not instruments:
        raise ServiceObservationError("SERVICE_INSTRUMENTS_INVALID")
    if any(type(item) is not ServiceObservationInstrument for item in instruments):
        raise ServiceObservationError("SERVICE_INSTRUMENTS_INVALID")
    if len({item.subscription_id for item in instruments}) != len(instruments):
        raise ServiceObservationError("SERVICE_INSTRUMENT_DUPLICATE")
    service = validate_service_observation_config(service_config)
    env = os.environ if environ is None else environ
    if not isinstance(env, Mapping):
        raise ServiceObservationError("SERVICE_ENVIRONMENT_INVALID")
    from bt_api_base.account_protocol import AccountResourceKey
    from bt_api_base.market_protocol import MarketSubscriptionKey
    from bt_api_base.service_protocol import WireScope

    from bt_api_py.service_account import ServiceAccountClient, ServiceAccountError
    from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory

    need_metadata = any(item.instrument_id is not None for item in instruments)
    parser = resolver = None
    if need_metadata:
        if type(provider_route) is not str or not provider_route or provider_route != provider_route.strip():
            raise ServiceObservationError("SERVICE_PROVIDER_ROUTE_REQUIRED")
        try:
            parser_factory = load_route_entrypoint("bt_api.service_instrument_metadata_parsers", provider_route)
            resolver = load_route_entrypoint("bt_api.service_observation_instrument_resolvers", provider_route)
            parser = parser_factory(error_type=ServiceAccountError)
        except Exception:
            raise ServiceObservationError("SERVICE_PROVIDER_METADATA_UNAVAILABLE") from None
        if not callable(parser):
            raise ServiceObservationError("SERVICE_PROVIDER_METADATA_UNAVAILABLE")

    if admission is not None and type(admission) is not ServiceObservationAdmission:
        raise ServiceObservationError("SERVICE_ADMISSION_INVALID")
    admission_lease = admission._reserve() if admission is not None else None
    runtime_bundle = None
    runtime = None
    account_client = None
    clients: dict[str, Any] = {}
    try:
        runtime_bundle = _build_service_observation_runtime(
            service, env, require_instrument_metadata=need_metadata,
        )
        runtime = runtime_bundle[0]
        _, _, _, curve_keys, account, market, domain_ref, _ = runtime_bundle
        if admission_lease is not None:
            admission_lease.retain(runtime, account_client, clients)
        account_key, coverage = parse_service_account_configuration(account)
        market_factory = ZmqMarketTransportFactory(curve_keys)
        runtime.start()
        if runtime.healthy is not True:
            raise ServiceObservationError("SERVICE_RUNTIME_NOT_HEALTHY")
        account_client = ServiceAccountClient(
            runtime, account["mount"], account_ref=account["account_ref"],
            expected_account=account_key, required_coverage=coverage,
            require_open_orders=True, instrument_metadata_parser=parser,
            snapshot_format=account.get("snapshot_format", "typed"),
        )
        if admission_lease is not None:
            admission_lease.retain(runtime, account_client, clients)
        account_view = account_client.refresh()
        if account_view.observation.account_key != account_key:
            raise ServiceObservationError("SERVICE_ACCOUNT_IDENTITY_MISMATCH")
        metadata: dict[str, Any] = {}
        keys: dict[str, Any] = {}
        resources: dict[str, Any] = {}
        for item in instruments:
            if item.market_key is not None:
                key = _explicit_market_key(item, market)
            else:
                if resolver is None:
                    raise ServiceObservationError("SERVICE_PROVIDER_METADATA_UNAVAILABLE")
                metadata_value, key = resolver(
                    item, account_client, account_view, market, account_key,
                    error_type=ServiceObservationError,
                )
                if type(key) is not MarketSubscriptionKey:
                    raise ServiceObservationError("SERVICE_MARKET_KEY_INVALID")
                if key.source_ref != market["source_ref"] or key.entitlement_ref != market["entitlement_ref"]:
                    raise ServiceObservationError("SERVICE_MARKET_SCOPE_MISMATCH")
                metadata[item.subscription_id] = metadata_value
            if item.resource is not None:
                try:
                    resource = item.resource if type(item.resource) is AccountResourceKey else AccountResourceKey.from_dict(item.resource)
                except Exception:
                    raise ServiceObservationError("SERVICE_RESOURCE_INVALID") from None
                if resource not in coverage:
                    raise ServiceObservationError("SERVICE_RESOURCE_OUTSIDE_COVERAGE")
                if resource.product_type != key.instrument.product_type:
                    raise ServiceObservationError("SERVICE_RESOURCE_PRODUCT_MISMATCH")
                resources[item.subscription_id] = resource
            require_descriptor = (
                key.channel != "bar" if item.require_source_descriptor is None
                else item.require_source_descriptor
            )
            clients[item.subscription_id] = ServiceMarketClient(
                runtime, market["mount"], key, subscriber_factory=market_factory,
                stream_scope=WireScope(domain_ref), require_source_descriptor=require_descriptor,
            )
            keys[item.subscription_id] = key
        return ServiceObservationClients(
            runtime=runtime, account_client=account_client, account_view=account_view,
            metadata=metadata, clients=clients, keys=keys, resources=resources,
            account_config=account, admission_lease=admission_lease,
        )
    except BaseException as error:
        if runtime is None and runtime_bundle is not None:
            runtime = runtime_bundle[0]
        if admission_lease is None:
            # Keep the legacy no-admission BaseException behavior: do not
            # attempt SDK startup rollback for interrupts on this opt-out path.
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            if runtime is None:
                raise
            _close_resources(
                runtime, account_client, clients,
                close_facades=True, facades_confirmed=False,
            )
            if isinstance(error, ServiceObservationError):
                raise
            raise ServiceObservationError("SERVICE_SESSION_START_FAILED") from None
        if runtime is None:
            admission_lease.release_after_confirmed_cleanup()
            raise
        admission_lease.retain(runtime, account_client, clients)
        cleanup_confirmed = False
        try:
            cleanup = _close_resources(
                runtime, account_client, clients,
                close_facades=True, facades_confirmed=False,
            )
            cleanup_confirmed = cleanup.get("status") == "PASS"
        except BaseException:
            _retain_uncertain_resources(runtime, account_client, clients)
        if cleanup_confirmed:
            admission_lease.release_after_confirmed_cleanup()
        else:
            admission_lease.retain(runtime, account_client, clients)
        if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        if isinstance(error, ServiceObservationError):
            raise
        raise ServiceObservationError("SERVICE_SESSION_START_FAILED") from None


__all__ = (
    "ServiceObservationAdmission",
    "ServiceObservationClients",
    "open_service_observation_clients",
)
