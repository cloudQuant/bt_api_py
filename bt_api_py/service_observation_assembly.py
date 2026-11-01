"""SDK-owned read-only service observation configuration and runtime assembly.

This module creates no Backtrader object and grants no provider or account authority.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


class ServiceObservationError(RuntimeError):
    """Finite errors that never include configuration, credentials, or rows."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code):
            raise ValueError("code must be a finite uppercase ASCII identifier")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ServiceObservationInstrument:
    """One explicitly named read subscription and its optional account mapping.

    ``market_key`` may be a typed ``MarketSubscriptionKey`` or its exact
    ``to_dict()`` shape. Callers may instead provide ``instrument_id`` and
    ``exchange_id`` for resolution by an explicitly selected provider.

    ``resource`` is an explicit ``AccountResourceKey`` or its exact two-field
    mapping.  It is never inferred from a market instrument or provider
    metadata.
    """

    subscription_id: str
    market_key: Any = None
    instrument_id: str | None = None
    exchange_id: str | None = None
    resource: Any = None
    qty_unit: str | None = None
    quantity_multiplier: str = "1"
    max_event_age_seconds: str = "2"
    require_source_descriptor: bool | None = None

    def __post_init__(self) -> None:
        if type(self.subscription_id) is not str or not self.subscription_id or self.subscription_id != self.subscription_id.strip():
            raise ValueError("subscription_id must be non-empty text")
        if self.market_key is None:
            if type(self.instrument_id) is not str or not self.instrument_id or self.instrument_id != self.instrument_id.strip():
                raise ValueError("instrument_id is required when market_key is absent")
            if type(self.exchange_id) is not str or not self.exchange_id or self.exchange_id != self.exchange_id.strip():
                raise ValueError("exchange_id is required when market_key is absent")
        elif self.instrument_id is not None or self.exchange_id is not None:
            raise ValueError("market_key and metadata identity are mutually exclusive")
        if self.qty_unit is not None and (
            type(self.qty_unit) is not str or not self.qty_unit or self.qty_unit != self.qty_unit.strip()
        ):
            raise ValueError("qty_unit must be non-empty text when supplied")
        if type(self.quantity_multiplier) is not str:
            raise ValueError("quantity_multiplier must be text")
        if type(self.max_event_age_seconds) is not str:
            raise ValueError("max_event_age_seconds must be text")
        if self.require_source_descriptor is not None and type(self.require_source_descriptor) is not bool:
            raise ValueError("require_source_descriptor must be bool or None")


_ACCOUNT_OPERATIONS = frozenset({"account.snapshot", "account.instrument_metadata"})


_MARKET_OPERATIONS = frozenset(
    {"market.subscribe", "market.unsubscribe", "market.snapshot", "market.head", "market.read_after"}
)


_SERVICE_FIELDS = frozenset(
    {"client_config", "identity", "service_principals", "curve_key_envs", "account", "market"}
)


_ACCOUNT_FIELDS = frozenset(
    {
        "mount",
        "account_ref",
        "key",
        "required_coverage",
    }
)


_MARKET_FIELDS = frozenset({"mount", "source_ref", "entitlement_ref"})


_SERVICE_FILE_MAX_BYTES = 1024 * 1024


def _require_service_mapping(value: Any, code: str, fields: set[str] | frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise ServiceObservationError(code)
    return value


def _require_service_text(value: Any, code: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ServiceObservationError(code)
    return value


def validate_service_observation_config(value: Any) -> Mapping[str, Any]:
    """Validate the explicit service-only configuration envelope."""
    return _require_service_mapping(value, "SERVICE_CONFIG_INVALID", _SERVICE_FIELDS)


def load_service_observation_config(path: str | os.PathLike[str]) -> Mapping[str, Any]:
    """Read one explicit service-only YAML/JSON file without echoing values.

    The file may contain the service mapping directly or as its sole ``service``
    member. Credential material is not accepted by this format; callers provide
    CURVE keys through named process environment variables.
    """

    if not isinstance(path, (str, os.PathLike)) or isinstance(path, bool):
        raise ServiceObservationError("SERVICE_CONFIG_PATH_INVALID")
    try:
        config_path = Path(path).expanduser().resolve(strict=True)
        if not config_path.is_file():
            raise OSError
        size = config_path.stat().st_size
        if type(size) is not int or size <= 0 or size > _SERVICE_FILE_MAX_BYTES:
            raise ServiceObservationError("SERVICE_CONFIG_SIZE_INVALID")
        raw = config_path.read_bytes()
        if len(raw) != size:
            raise ServiceObservationError("SERVICE_CONFIG_READ_INCOMPLETE")
        text = raw.decode("utf-8", errors="strict")
    except ServiceObservationError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, UnicodeError):
        raise ServiceObservationError("SERVICE_CONFIG_UNAVAILABLE") from None
    try:
        import yaml

        loaded = yaml.safe_load(text)
    except Exception:
        raise ServiceObservationError("SERVICE_CONFIG_PARSE_FAILED") from None
    if not isinstance(loaded, Mapping):
        raise ServiceObservationError("SERVICE_CONFIG_INVALID")
    if set(loaded) == {"service"}:
        loaded = loaded["service"]
    service = _require_service_mapping(loaded, "SERVICE_CONFIG_INVALID", _SERVICE_FIELDS)
    return MappingProxyType(dict(service))


def _build_service_observation_runtime(
    service: Mapping[str, Any],
    env: Mapping[str, str],
    *,
    require_instrument_metadata: bool,
):
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory

    try:
        parsed = parse_client_config(copy.deepcopy(service["client_config"]))
        public = parsed.to_public_dict()
    except Exception:
        raise ServiceObservationError("SERVICE_CLIENT_CONFIG_INVALID") from None

    identity = _require_service_mapping(
        service.get("identity"),
        "SERVICE_IDENTITY_CONFIG_INVALID",
        {"principal_ref", "strategy_ref", "execution_domain_ref"},
    )
    principal_ref = _require_service_text(identity["principal_ref"], "SERVICE_PRINCIPAL_REF_INVALID")
    strategy_ref = _require_service_text(identity["strategy_ref"], "SERVICE_STRATEGY_REF_INVALID")
    domain_ref = _require_service_text(identity["execution_domain_ref"], "SERVICE_DOMAIN_REF_INVALID")
    if public.get("registration", {}).get("strategy_ref") != strategy_ref:
        raise ServiceObservationError("SERVICE_STRATEGY_IDENTITY_MISMATCH")
    clients = public.get("clients", {})
    if set(clients) != {"gateway"}:
        raise ServiceObservationError("SERVICE_CLIENT_SET_UNSUPPORTED")
    bindings = clients["gateway"].get("settings", {}).get("bindings")
    if type(bindings) is not list or len(bindings) != 2:
        raise ServiceObservationError("SERVICE_BINDING_SET_INVALID")
    by_mount = {
        item.get("mount"): item
        for item in bindings
        if isinstance(item, Mapping) and type(item.get("mount")) is str
    }
    if len(by_mount) != 2 or set(by_mount) != {"store.account", "store.market"}:
        raise ServiceObservationError("SERVICE_BINDING_SET_INVALID")

    account = _require_service_mapping(service.get("account"), "SERVICE_ACCOUNT_CONFIG_INVALID", _ACCOUNT_FIELDS)
    market = _require_service_mapping(service.get("market"), "SERVICE_MARKET_CONFIG_INVALID", _MARKET_FIELDS)
    if account["mount"] != "store.account" or market["mount"] != "store.market":
        raise ServiceObservationError("SERVICE_BINDING_SET_INVALID")
    account_ref = _require_service_text(account["account_ref"], "SERVICE_ACCOUNT_REF_INVALID")
    if by_mount["store.account"].get("account_ref") != account_ref:
        raise ServiceObservationError("SERVICE_ACCOUNT_SCOPE_MISMATCH")
    _require_service_text(market["source_ref"], "SERVICE_MARKET_SOURCE_REF_INVALID")
    _require_service_text(market["entitlement_ref"], "SERVICE_MARKET_ENTITLEMENT_REF_INVALID")

    peers = service.get("service_principals")
    service_ids = {item.get("service_id") for item in bindings}
    if (
        not isinstance(peers, Mapping)
        or set(peers) != service_ids
        or any(type(value) is not str or not value or value != value.strip() for value in peers.values())
    ):
        raise ServiceObservationError("SERVICE_PEER_IDENTITIES_INVALID")

    def scope_resolver(scope_ref: str):
        if scope_ref == by_mount["store.account"].get("scope_ref"):
            return WireScope(domain_ref, account_ref=account_ref, strategy_ref=strategy_ref)
        if scope_ref == by_mount["store.market"].get("scope_ref"):
            return WireScope(domain_ref, strategy_ref=strategy_ref)
        raise ServiceObservationError("SERVICE_SCOPE_REF_UNBOUND")

    def peer_resolver(service_id: str):
        peer = peers.get(service_id)
        if peer is None:
            raise ServiceObservationError("SERVICE_PEER_IDENTITY_UNBOUND")
        return WireIdentity(WireRole.SERVICE, peer, service_id)

    profiles = public.get("communication", {}).get("profiles", {})
    if not isinstance(profiles, Mapping):
        raise ServiceObservationError("SERVICE_TRANSPORT_CONFIG_INVALID")
    references = set()
    for binding in bindings:
        profile = profiles.get(binding.get("transport_ref"))
        if not isinstance(profile, Mapping):
            raise ServiceObservationError("SERVICE_TRANSPORT_CONFIG_INVALID")
        references.add(_require_service_text(profile.get("security_profile_ref"), "SERVICE_SECURITY_REF_INVALID"))
    curve_refs = service.get("curve_key_envs")
    if not isinstance(curve_refs, Mapping) or set(curve_refs) != references:
        raise ServiceObservationError("SERVICE_CURVE_REFERENCES_INVALID")
    curve_keys = {}
    for reference in sorted(references):
        env_refs = _require_service_mapping(
            curve_refs[reference],
            "SERVICE_CURVE_KEY_REFERENCES_INVALID",
            {"server_key_env", "client_public_key_env", "client_secret_key_env"},
        )
        values = []
        for field in ("server_key_env", "client_public_key_env", "client_secret_key_env"):
            name = _require_service_text(env_refs[field], "SERVICE_CURVE_ENV_NAME_INVALID")
            key = env.get(name)
            if type(key) is not str or len(key) != 40 or not key.isascii():
                raise ServiceObservationError("SERVICE_CURVE_KEY_UNAVAILABLE")
            try:
                values.append(key.encode("ascii"))
            except UnicodeEncodeError:
                raise ServiceObservationError("SERVICE_CURVE_KEY_UNAVAILABLE") from None
        curve_keys[reference] = ClientCurveKeys(*values)

    account_operations = (
        _ACCOUNT_OPERATIONS
        if require_instrument_metadata
        else frozenset({"account.snapshot"})
    )
    requested_operations = {
        "store.account": account_operations,
        "store.market": _MARKET_OPERATIONS,
    }
    try:
        runtime = ServiceClientRuntime(
            parsed,
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref),
            scope_resolver=scope_resolver,
            peer_resolver=peer_resolver,
            transport_factory=ZmqTransportFactory(curve_keys),
            requested_operations=requested_operations,
        )
    except Exception:
        raise ServiceObservationError("SERVICE_RUNTIME_CONFIG_INVALID") from None
    return runtime, parsed, public, curve_keys, account, market, domain_ref, by_mount


def parse_service_account_configuration(account: Mapping[str, Any]):
    from bt_api_base.account_protocol import AccountKey, AccountResourceKey
    try:
        key_value = account["key"]
        account_key = key_value if type(key_value) is AccountKey else AccountKey.from_dict(key_value)
        raw_coverage = account["required_coverage"]
        if type(raw_coverage) not in {list, tuple} or not raw_coverage:
            raise ValueError
        coverage = tuple(
            item if type(item) is AccountResourceKey else AccountResourceKey.from_dict(item)
            for item in raw_coverage
        )
        if len(set(coverage)) != len(coverage):
            raise ValueError
    except Exception:
        raise ServiceObservationError("SERVICE_ACCOUNT_COVERAGE_INVALID") from None
    return account_key, coverage

__all__ = (
    "ServiceObservationError", "ServiceObservationInstrument", "load_service_observation_config",
    "parse_service_account_configuration", "validate_service_observation_config",
)
