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
        with _KEEPALIVE_LOCK:
            _KEEPALIVE.append((runtime, account_client, clients))
    return result


class ServiceObservationClients:
    """Owner-thread handle for a service runtime and its neutral read facades."""

    def __init__(self, *, runtime: Any, account_client: Any, account_view: Any,
                 metadata: Mapping[str, Any], clients: Mapping[str, Any],
                 keys: Mapping[str, Any], resources: Mapping[str, Any],
                 account_config: Mapping[str, Any]) -> None:
        self.runtime = runtime
        self.account_client = account_client
        self.account_view = account_view
        self.metadata = MappingProxyType(dict(metadata))
        self.market_clients = MappingProxyType(dict(clients))
        self.market_keys = MappingProxyType(dict(keys))
        self.resources = MappingProxyType(dict(resources))
        self.account_config = MappingProxyType(dict(account_config))
        self._owner_thread = threading.get_ident()
        self._close_report: Mapping[str, Any] | None = None
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
        result = _close_resources(
            self.runtime, self.account_client, self.market_clients,
            close_facades=close_facades, facades_confirmed=facades_confirmed,
            facade_errors=facade_errors,
        )
        self._close_report = result
        if result["status"] != "PASS":
            raise ServiceObservationError("SERVICE_CLEANUP_UNCONFIRMED")
        return result


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
) -> ServiceObservationClients:
    """Compose an explicit read-only client runtime without creating a Store."""
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

    runtime, _, _, curve_keys, account, market, domain_ref, _ = _build_service_observation_runtime(
        service, env, require_instrument_metadata=need_metadata,
    )
    account_client = None
    clients: dict[str, Any] = {}
    try:
        account_key, coverage = parse_service_account_configuration(account)
        market_factory = ZmqMarketTransportFactory(curve_keys)
        runtime.start()
        if runtime.healthy is not True:
            raise ServiceObservationError("SERVICE_RUNTIME_NOT_HEALTHY")
        account_client = ServiceAccountClient(
            runtime, account["mount"], account_ref=account["account_ref"],
            expected_account=account_key, required_coverage=coverage,
            require_open_orders=True, instrument_metadata_parser=parser,
        )
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
            account_config=account,
        )
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        _close_resources(runtime, account_client, clients, close_facades=True, facades_confirmed=False)
        if isinstance(error, ServiceObservationError):
            raise
        raise ServiceObservationError("SERVICE_SESSION_START_FAILED") from None


__all__ = ("ServiceObservationClients", "open_service_observation_clients")
