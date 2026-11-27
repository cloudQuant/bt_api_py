"""Protected local CTP operator resources for the installed Gateway profiles.

The environment variable contains a *path*, never credentials. Only an
operator-owned mode-0600 JSON file in an operator-owned private directory is
accepted.

``ctp-local-readonly`` keeps native writes and settlement confirmation
disabled; the service still requires the dispatch endpoint and may persist
logical commands. ``ctp-simnow-trading`` enables native writes and automatic
settlement confirmation only for a SimNow account key (INV63-08): any other
environment with ``write_enabled`` refuses to start. It also requires the
return query instruments that dispatch readiness depends on. Neither profile
proves the configured fronts, credentials or grants are genuine.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields
from dataclasses import field as dataclass_field
from pathlib import Path
from types import MappingProxyType

from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.ctp_gateway_operator_profile import (
    CtpGatewayTrustedResources,
    GatewayOperatorProfileFactory,
    create_ctp_gateway_profile_factory,
)
from bt_api_py.ctp_gateway_trading import CtpGatewayMarketSettings, CtpGatewayTradingSettings
from bt_api_py.gateway_direct_service import GatewayDirectGrant
from bt_api_py.gateway_operator import GatewayOperatorDeployment, GatewayOperatorDeploymentError
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

_LOG = logging.getLogger(__name__)
_RESOURCE_ENV = "BT_API_CTP_GATEWAY_RESOURCES_FILE"
_MAX_BYTES = 262_144
_SIMNOW_MARKET_OBSERVE_CTP_PROFILE = "set1_group1_vpn"
_SIMNOW_MARKET_OBSERVE_FRONT_PAIR = (
    "tcp://182.254.243.31:30001",
    "tcp://182.254.243.31:30011",
)
_SIMNOW_MARKET_OBSERVE_SOURCE_REF = "ctp-simnow-profile-set1-group1-vpn-market-v1"
_SIMNOW_MARKET_OBSERVE_ENTITLEMENT_REF = "ctp-simnow-profile-set1-group1-vpn-tick-v1"
_SIMNOW_MARKET_OPERATIONS = frozenset({
    "market.subscribe",
    "market.unsubscribe",
    "market.snapshot",
    "market.head",
    "market.read_after",
})
_SIMNOW_MARKET_VENUES = frozenset({"SHFE", "DCE", "CZCE", "CFFEX", "INE", "GFEX"})
_SIMNOW_NATIVE_SYMBOL = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class _CtpSimNowMarketObserveProfileSpec:
    """Trusted, immutable selectors for one standalone SimNow market profile."""

    ctp_profile: str
    front_pair: tuple[str, str]
    source_ref: str
    entitlement_ref: str


_SIMNOW_MARKET_OBSERVE_SET1_SPEC = _CtpSimNowMarketObserveProfileSpec(
    ctp_profile=_SIMNOW_MARKET_OBSERVE_CTP_PROFILE,
    front_pair=_SIMNOW_MARKET_OBSERVE_FRONT_PAIR,
    source_ref=_SIMNOW_MARKET_OBSERVE_SOURCE_REF,
    entitlement_ref=_SIMNOW_MARKET_OBSERVE_ENTITLEMENT_REF,
)
_SIMNOW_7X24_MARKET_OBSERVE_SPEC = _CtpSimNowMarketObserveProfileSpec(
    ctp_profile="set2_7x24_4000x",
    front_pair=("tcp://182.254.243.31:40001", "tcp://182.254.243.31:40011"),
    source_ref="ctp-simnow-set2-7x24-market-v1",
    entitlement_ref="ctp-simnow-set2-7x24-quote-v2-v1",
)


class CtpLocalProfileError(ValueError):
    """Value-free local resource error."""


def _invalid():
    raise CtpLocalProfileError("CTP_LOCAL_RESOURCES_INVALID") from None


@dataclass(frozen=True, slots=True)
class CtpSimNowMarketObserveResources:
    """The small protected resource projection needed by the market-only role."""

    authority: TrustedServiceAuthority = dataclass_field(repr=False)
    registry_path: str = dataclass_field(repr=False)
    broker_id: str = dataclass_field(repr=False)
    user_id: str = dataclass_field(repr=False)
    password: str = dataclass_field(repr=False)
    key_universe: tuple[MarketSubscriptionKey, ...] = dataclass_field(repr=False)
    principal_allowed_keys: Mapping[str, frozenset[MarketSubscriptionKey]] = dataclass_field(repr=False)

    def __post_init__(self) -> None:
        if (type(self.authority) is not TrustedServiceAuthority
                or type(self.registry_path) is not str or not self.registry_path
                or any(type(value) is not str or not value
                       for value in (self.broker_id, self.user_id, self.password))
                or type(self.key_universe) is not tuple or not self.key_universe
                or len(self.key_universe) > 1_024
                or any(type(key) is not MarketSubscriptionKey for key in self.key_universe)
                or len(set(self.key_universe)) != len(self.key_universe)
                or not isinstance(self.principal_allowed_keys, Mapping)):
            _invalid()
        allowed = dict(self.principal_allowed_keys)
        if set(allowed) != set(self.authority.principals.values()):
            _invalid()
        universe = frozenset(self.key_universe)
        if any(type(principal) is not str or not keys or not frozenset(keys) <= universe
               for principal, keys in allowed.items()):
            _invalid()
        object.__setattr__(
            self,
            "principal_allowed_keys",
            MappingProxyType({principal: frozenset(keys) for principal, keys in allowed.items()}),
        )


def _mapping(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _invalid()
    return value


def _string(value):
    if type(value) is not str or not value or value != value.strip():
        _invalid()
    return value


def _string_set(value):
    if type(value) is not list or not value or any(type(x) is not str or not x for x in value):
        _invalid()
    if len(set(value)) != len(value):
        _invalid()
    return frozenset(value)


def _identity(value):
    item = _mapping(value, ("role", "principal_ref", "actor_ref", "delegate_ref"))
    return WireIdentity(WireRole(item["role"]), item["principal_ref"], item["actor_ref"], item["delegate_ref"])


def _scope(value):
    item = dict(_mapping(value, ("execution_domain_ref", "account_ref", "account_refs", "strategy_ref",
                                  "run_id", "manual_book_ref", "portfolio_ref", "group_id", "leg_id")))
    if type(item["account_refs"]) is not list:
        _invalid()
    item["account_refs"] = tuple(item["account_refs"])
    return WireScope(**item)


def _curve(value):
    result = _string(value).encode("ascii")
    if len(result) != 40:
        _invalid()
    return result


def _parse_strict_json_object(content: bytes) -> dict:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                _invalid()
            result[key] = value
        return result
    raw = json.loads(content.decode("utf-8-sig", errors="strict"), object_pairs_hook=pairs,
                     parse_constant=lambda _value: _invalid())
    if type(raw) is not dict:
        _invalid()
    return raw


def _protected_json(path):
    """Read without symlink traversal or accepting mutable/public resource files."""
    if os.name == "nt":
        if type(path) is not str or not path or "\x00" in path or not os.path.isabs(path):
            _invalid()
        target = Path(path)
        parts = target.parts
        if len(parts) < 3 or any(part in (".", "..") for part in parts):
            _invalid()
        try:
            from bt_api_py._win32_security import (
                Win32SecurityUnavailable,
                inspect_owner,
                read_bounded_windows,
            )

            parent_owner = inspect_owner(target.parent)
            if not parent_owner.is_current_user:
                _invalid()
            content = read_bounded_windows(target, _MAX_BYTES, protected=True)
            if len(content) > _MAX_BYTES:
                _invalid()
            return _parse_strict_json_object(content)
        except CtpLocalProfileError:
            raise
        except (Win32SecurityUnavailable, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            _invalid()
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _invalid()
    if type(path) is not str or not path.startswith("/") or "\x00" in path:
        _invalid()
    parts = Path(path).parts
    if not parts or any(part in (".", "..") for part in path.split("/")) or len(parts) < 3:
        _invalid()
    fds = []
    try:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(fd)
        for name in parts[1:-1]:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            fds.append(fd)
            st = os.fstat(fd)
            if st.st_uid not in (0, os.geteuid()) or (st.st_mode & 0o022 and not (st.st_mode & stat.S_ISVTX and st.st_uid == 0)):
                _invalid()
        parent = os.fstat(fd)
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            _invalid()
        resource = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        fds.append(resource)
        before = os.fstat(resource)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_mode & 0o077 or before.st_nlink != 1 or before.st_size > _MAX_BYTES):
            _invalid()
        with os.fdopen(os.dup(resource), "rb") as handle:
            content = handle.read(_MAX_BYTES + 1)
        after = os.fstat(resource)
        if len(content) > _MAX_BYTES or any(getattr(before, key) != getattr(after, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_nlink")):
            _invalid()
        return _parse_strict_json_object(content)
    except CtpLocalProfileError:
        raise
    except Exception:
        _invalid()
    finally:
        for fd in reversed(fds):
            os.close(fd)


class CtpGatewayWriteRequiresBrokerConfirmation(CtpLocalProfileError):
    """INV63-08: Gateway writes are limited to SimNow until broker confirmation."""

    def __init__(self):
        super().__init__("GATEWAY_WRITE_REQUIRES_BROKER_CONFIRMATION")


def load_local_ctp_resources(config):
    """Resolve protected local authority and CTP resources on the owner thread."""
    return _load_resources(config, trading=False)


def load_simnow_trading_resources(config):
    """Resolve the same protected resources with SimNow-only native writes."""
    return _load_resources(config, trading=True)


def _require_simnow_market_observe_config(config) -> None:
    """Accept only the standalone Gateway market-hub service role."""
    from bt_api_py.service_config import ServiceConfig

    if (type(config) is not ServiceConfig or set(config.components) != {"gateway"}
            or config.clients):
        _invalid()
    gateway = config.components.get("gateway")
    if not isinstance(gateway, Mapping):
        _invalid()
    settings = gateway.get("settings")
    if not isinstance(settings, Mapping):
        _invalid()
    options = settings.get("options")
    ports = settings.get("bind_ports")
    if (not isinstance(options, Mapping) or set(options) != {"role"}
            or options.get("role") != "market_hub"
            or type(ports) not in (tuple, list)
            or tuple(ports) != ("service.market_source",)):
        _invalid()
    if type(settings.get("instance_id")) is not str or not settings["instance_id"]:
        _invalid()


def _simnow_market_observe_key(
    key, spec: _CtpSimNowMarketObserveProfileSpec = _SIMNOW_MARKET_OBSERVE_SET1_SPEC,
) -> bool:
    if type(key) is not MarketSubscriptionKey:
        return False
    instrument = key.instrument
    return (
        key.source_ref == spec.source_ref
        and key.entitlement_ref == spec.entitlement_ref
        and key.channel == "tick"
        and key.depth is None
        and key.timeframe is None
        and instrument.provider == "ctp"
        and instrument.environment == "demo"
        and instrument.product_type == "future"
        and instrument.venue in _SIMNOW_MARKET_VENUES
        and instrument.instrument_id == instrument.native_symbol
        and _SIMNOW_NATIVE_SYMBOL.fullmatch(instrument.native_symbol) is not None
    )


def load_simnow_market_observe_resources(config) -> CtpSimNowMarketObserveResources:
    """Load only the protected authority, finite market ACL and three MD credentials."""
    return _load_simnow_market_observe_resources(config, _SIMNOW_MARKET_OBSERVE_SET1_SPEC)


def load_simnow_7x24_market_observe_resources(config) -> CtpSimNowMarketObserveResources:
    """Load bounded resources while accepting only the 7x24 market-key family."""
    return _load_simnow_market_observe_resources(config, _SIMNOW_7X24_MARKET_OBSERVE_SPEC)


def _load_simnow_market_observe_resources(
    config, spec: _CtpSimNowMarketObserveProfileSpec,
) -> CtpSimNowMarketObserveResources:
    try:
        _require_simnow_market_observe_config(config)
        raw = _protected_json(os.environ.get(_RESOURCE_ENV))
        if (type(raw) is not dict or type(raw.get("schema_version")) is not int
                or raw["schema_version"] != 1
                or type(raw.get("clients")) is not list or not 1 <= len(raw["clients"]) <= 1_024
                or type(raw.get("credentials")) is not dict):
            _invalid()

        # This entrypoint intentionally consumes only these three fields from
        # the shared protected credential object. TD/Auth/front settings in
        # that object never select the market front or construct a TD path.
        credentials = raw["credentials"]
        broker_id = _string(credentials.get("broker_id"))
        user_id = _string(credentials.get("user_id"))
        password = _string(credentials.get("password"))
        registry_path = _string(raw.get("registry_path"))

        authority_raw = _mapping(raw.get("authority"), (
            "identity", "scope", "scope_ref", "scope_version", "security_ref", "security_version",
            "registration_policy_ref", "registration_policy_version", "service_requirements_ref",
            "service_requirements_version", "curve_public_key", "curve_secret_key",
        ))
        authority_identity = _identity(authority_raw["identity"])
        authority_scope = _scope(authority_raw["scope"])
        if (authority_identity.role is not WireRole.SERVICE or authority_identity.delegate_ref is not None
                or authority_scope.account_ref is not None or authority_scope.account_refs
                or authority_scope.strategy_ref is not None or authority_scope.run_id is not None
                or authority_scope.manual_book_ref is not None or authority_scope.portfolio_ref is not None
                or authority_scope.group_id is not None or authority_scope.leg_id is not None):
            _invalid()
        public_key = _curve(authority_raw["curve_public_key"])
        secret_key = _curve(authority_raw["curve_secret_key"])
        import zmq

        if zmq.curve_public(secret_key) != public_key:
            _invalid()

        service_settings = config.components["gateway"]["settings"]
        principal_keys = {}
        grants = {}
        market_client_principals = set()
        for client in raw["clients"]:
            if type(client) is not dict or frozenset(client) not in {
                frozenset({"identity", "curve_public_key", "resources"}),
                frozenset({"identity", "curve_public_key", "resources", "symbols"}),
            }:
                _invalid()
            identity = _identity(client["identity"])
            client_public = _curve(client["curve_public_key"])
            if (identity.role is not WireRole.STRATEGY_CLIENT or identity.delegate_ref is not None
                    or identity.principal_ref in grants or client_public in principal_keys
                    or type(client["resources"]) is not list or len(client["resources"]) != 1):
                _invalid()
            resource = _mapping(client["resources"][0],
                                ("instance_id", "port", "scope_ref", "scope", "operations"))
            scope = _scope(resource["scope"])
            operations = _string_set(resource["operations"])
            if (resource["instance_id"] != service_settings["instance_id"]
                    or resource["port"] != "service.market_source"
                    or resource["scope_ref"] != config.scope_ref
                    or operations != _SIMNOW_MARKET_OPERATIONS
                    or scope.execution_domain_ref != authority_scope.execution_domain_ref
                    or scope.account_ref is None or scope.account_refs or scope.strategy_ref is None
                    or scope.run_id is not None or scope.manual_book_ref is not None
                    or scope.portfolio_ref is not None or scope.group_id is not None
                    or scope.leg_id is not None):
                _invalid()
            grant = ResourceGrant(resource["instance_id"], resource["port"], resource["scope_ref"],
                                  scope, operations)
            principal_keys[client_public] = identity.principal_ref
            grants[identity.principal_ref] = (PrincipalGrant(identity, (grant,)),)
            market_client_principals.add(identity.principal_ref)
        if (not principal_keys or len(principal_keys) != len(raw["clients"])
                or len(grants) != len(raw["clients"])):
            _invalid()

        authority = TrustedServiceAuthority(
            identity=authority_identity,
            scope=authority_scope,
            scope_ref=_string(authority_raw["scope_ref"]),
            scope_version=_string(authority_raw["scope_version"]),
            security_ref=_string(authority_raw["security_ref"]),
            security_version=_string(authority_raw["security_version"]),
            registration_policy_ref=_string(authority_raw["registration_policy_ref"]),
            registration_policy_version=_string(authority_raw["registration_policy_version"]),
            service_requirements_ref=_string(authority_raw["service_requirements_ref"]),
            service_requirements_version=_string(authority_raw["service_requirements_version"]),
            curve_public_key=public_key,
            curve_secret_key=secret_key,
            principals=principal_keys,
            grants=grants,
        )
        market = _mapping(raw.get("market"), ("subscriptions", "principal_allowed_indices"))
        if (type(market["subscriptions"]) is not list
                or not 1 <= len(market["subscriptions"]) <= 1_024
                or type(market["principal_allowed_indices"]) is not dict):
            _invalid()
        keys = tuple(MarketSubscriptionKey.from_dict(item) for item in market["subscriptions"])
        if (len(set(keys)) != len(keys)
                or any(not _simnow_market_observe_key(key, spec) for key in keys)):
            _invalid()
        allowed_indices = market["principal_allowed_indices"]
        if set(allowed_indices) != market_client_principals:
            _invalid()
        allowed = {}
        for principal, indices in allowed_indices.items():
            if (type(indices) is not list or not indices or len(set(indices)) != len(indices)
                    or any(type(index) is not int or not 0 <= index < len(keys) for index in indices)):
                _invalid()
            allowed[principal] = frozenset(keys[index] for index in indices)

        return CtpSimNowMarketObserveResources(
            authority=authority,
            registry_path=registry_path,
            broker_id=broker_id,
            user_id=user_id,
            password=password,
            key_universe=keys,
            principal_allowed_keys=allowed,
        )
    except CtpLocalProfileError:
        raise
    except Exception:
        _invalid()


def _reject_market_observe_component(component, _instance_id, _options, _scope):
    del component, _instance_id, _options, _scope
    raise GatewayOperatorDeploymentError("INVALID_DEPLOYMENT_CONFIG")


def _simnow_market_observe_provider_factory(
    resources: CtpSimNowMarketObserveResources,
    spec: _CtpSimNowMarketObserveProfileSpec = _SIMNOW_MARKET_OBSERVE_SET1_SPEC,
):
    def create_provider():
        from bt_api_ctp.ctp_env_selector import official_simnow_fronts
        from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream
        from bt_api_ctp.market_provider import CtpQuoteV2MarketProvider

        try:
            fronts = official_simnow_fronts(spec.ctp_profile)
        except Exception:
            _invalid()
        if type(fronts) is not tuple or fronts != spec.front_pair:
            _invalid()
        td_front, md_front = fronts

        def stream_factory(key, quote_queue):
            if type(key) is not MarketSubscriptionKey or key not in resources.key_universe:
                _invalid()
            return CtpMarketStream(
                data_queue=quote_queue,
                broker_id=resources.broker_id,
                user_id=resources.user_id,
                password=resources.password,
                td_front=td_front,
                md_front=md_front,
                ctp_env_profile=spec.ctp_profile,
                require_ctp_profile=spec.ctp_profile,
                auto_detect_fronts=False,
                asset_type=key.instrument.product_type.upper(),
                allow_zero_md_login_response=True,
            )

        return CtpQuoteV2MarketProvider(
            stream_factory=stream_factory,
            source_ref=spec.source_ref,
            entitlement_ref=spec.entitlement_ref,
            queue_capacity=4_096,
            connect_timeout_seconds=30.0,
            stop_timeout_seconds=5.0,
        )

    return create_provider


def create_simnow_market_observe_deployment(config):
    """Compose only the market-hub role from the protected market resources."""
    return _create_simnow_market_observe_deployment(config, _SIMNOW_MARKET_OBSERVE_SET1_SPEC)


def create_simnow_7x24_market_observe_deployment(config):
    """Compose only the market-hub role for the fixed SimNow 7x24 fronts."""
    return _create_simnow_market_observe_deployment(config, _SIMNOW_7X24_MARKET_OBSERVE_SPEC)


def _create_simnow_market_observe_deployment(
    config, spec: _CtpSimNowMarketObserveProfileSpec,
):
    resources = _load_simnow_market_observe_resources(config, spec)
    from bt_api_py.gateway_operator_market import GatewayMarketBinding

    binding = GatewayMarketBinding(
        key_universe=resources.key_universe,
        principal_allowed_keys=resources.principal_allowed_keys,
        provider_factory=_simnow_market_observe_provider_factory(resources, spec),
        max_provider_events_per_maintain=32,
    )
    return GatewayOperatorDeployment(
        config,
        resources.authority,
        resources.registry_path,
        resource_provider=_reject_market_observe_component,
        market_binding=binding,
    )


def _return_settings(value):
    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_py.ctp_gateway_trading import CtpGatewayReturnSettings

    # query_timeout_seconds is optional (I63-E097): the 0.5 s default is too
    # short for SimNow order queries once the account has orders that day.
    keys = ("instruments", "query_interval_seconds")
    if type(value) is dict and "query_timeout_seconds" in value:
        keys += ("query_timeout_seconds",)
    value = _mapping(value, keys)
    if type(value["instruments"]) is not list or not value["instruments"]:
        _invalid()
    interval = value["query_interval_seconds"]
    timeout = value.get("query_timeout_seconds", 0.5)
    if type(interval) not in (int, float) or type(timeout) not in (int, float):
        _invalid()
    return CtpGatewayReturnSettings(
        tuple(MarketInstrumentKey.from_dict(item) for item in value["instruments"]),
        query_interval_seconds=float(interval), query_timeout_seconds=float(timeout))


def _strategy_financial(value, strategies):
    """Optional section for strategy cash and value (D63-53, D63-54, D63-55)::

        {"rates_file": "/absolute/path/rates.yaml", "currency": "CNY",
         "budgets": {"<strategy ref>": "600000.00"},
         "adjustments": [{"id": "2026-11-12-a", "strategy": "<strategy ref>", "amount": "-100000.00"}],
         "refresh_seconds": 60, "price_max_age_seconds": 180}

    ``budgets`` is the fixed initial amount per strategy; a strategy without one has
    no cash figure, there is no default. ``adjustments`` are later changes, each with
    its own id; like everything here they take effect when the Gateway starts with
    them. ``rates_file`` is the shared local rates file (multiplier, margin ratio and
    commission by product), read once at start. The last three keys are optional.
    Every budget must name a strategy of a client of this bundle.
    """
    if value is None:
        return None
    from bt_api_py.ctp_gateway_strategy_financial import (
        CtpGatewayStrategyFinancialError,
        CtpStrategyFinancialSettings,
    )
    from bt_api_py.strategy_rates import StrategyRatesError, load_strategy_rates

    try:
        if type(value) is not dict or type(value.get("rates_file")) is not str:
            raise CtpGatewayStrategyFinancialError("INVALID_STRATEGY_FINANCIAL_SETTINGS")
        path = value["rates_file"]
        if not os.path.isabs(path):
            raise StrategyRatesError("RATES_FILE_PATH_NOT_ABSOLUTE")
        settings = CtpStrategyFinancialSettings.from_bundle(
            {name: item for name, item in value.items() if name != "rates_file"},
            rates=load_strategy_rates(path))
        if not set(settings.budgets) <= strategies:
            raise CtpGatewayStrategyFinancialError("STRATEGY_FINANCIAL_BUDGET_FOR_UNKNOWN_STRATEGY")
    except (CtpGatewayStrategyFinancialError, StrategyRatesError) as error:
        # The code names the faulty part; it never contains a value from the bundle.
        _LOG.error("The strategy_financial section of the resource bundle was refused: %s", error.code)
        _invalid()
    return settings


def _strategy_ledger_upgrade(value):
    """Optional ``{"upgrade_from_v10": bool}``: explicit journal upgrade for the
    strategy ledger (positions and close capacity; the operator config sets
    ``strategy_ledger_enabled``). The journal never downgrades."""
    if value is None:
        return False
    if type(value) is dict:
        value = {name: item for name, item in value.items()
                 if name not in ("external_baseline", "account_comparison", "source_cut_mode")}
    value = _mapping(value, ("upgrade_from_v10",))
    if type(value["upgrade_from_v10"]) is not bool:
        _invalid()
    return value["upgrade_from_v10"]


def _strategy_ledger_source_cut_mode(value):
    """Optional conservative source-cut mode for the strategy ledger.

    ``"none"`` (default) keeps the historical fail-closed gate.  The explicit
    ``"returns_bracket_v1"`` selects the conservative common cut built from a
    continuous return stream plus the account read's before/after sequence
    bracket; legacy baseline rows without the marker stay untrusted.
    """
    if type(value) is not dict:
        return "none"
    mode = value.get("source_cut_mode", "none")
    if mode not in ("none", "returns_bracket_v1"):
        _invalid()
    return mode


def _strategy_ledger_account_comparison(value):
    """Optional ``{"upgrade_from_v11": bool, "refresh_seconds": number}`` (D63-48).

    Together with ``account_extensions_enabled: true`` in the operator config:
    the journal is at schema 12 (explicitly upgraded from 11 when asked) and
    the Gateway compares, per instrument, the strategies' ledgers plus the
    declared external holdings with the account at a cut it can prove,
    refreshed at most every ``refresh_seconds`` (1 to 120), and logs every
    change of the outcome. Returns ``(upgrade_from_v11, refresh_seconds)``.
    """
    if type(value) is not dict or value.get("account_comparison") is None:
        return False, None
    declared = _mapping(value["account_comparison"], ("upgrade_from_v11", "refresh_seconds"))
    refresh = declared["refresh_seconds"]
    if (type(declared["upgrade_from_v11"]) is not bool or type(refresh) not in (int, float)
            or not 1 <= refresh <= 120):
        _invalid()
    return declared["upgrade_from_v11"], float(refresh)


def _strategy_ledger_external_baseline(value):
    """Optional operator declaration of holdings that predate the Gateway journal.

    ``{"operator": ..., "operation_id": ..., "positions": [{"instrument": {...},
    "direction": "long"|"short", "today_quantity": "0", "yesterday_quantity": "1"}]}``.
    The Gateway writes the external baseline only when these rows equal the
    provider's complete account read (D63-06); the declaration alone proves nothing.
    """
    if type(value) is not dict or value.get("external_baseline") is None:
        return None
    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_gateway.strategy_position_ledger import ExternalBaselinePosition

    from bt_api_py.ctp_gateway_strategy_ledger import ExternalBaselineDeclaration

    declared = _mapping(value["external_baseline"], ("operator", "operation_id", "positions"))
    if type(declared["positions"]) is not list:
        _invalid()
    positions = []
    for row in declared["positions"]:
        row = _mapping(row, ("instrument", "direction", "today_quantity", "yesterday_quantity"))
        positions.append(ExternalBaselinePosition(
            MarketInstrumentKey.from_dict(row["instrument"]), row["direction"],
            row["today_quantity"], row["yesterday_quantity"]))
    return ExternalBaselineDeclaration(_string(declared["operator"]), _string(declared["operation_id"]),
                                       tuple(positions))


def _controls(value, principals, grants):
    """Operator emergency-stop grants: application clients with control operations only."""
    from bt_api_py.gateway_direct_service import (
        CONTROL_INTENT_OPERATIONS,
        CONTROL_OPERATIONS,
        GatewayControlGrant,
        GatewayRuntimeControlProof,
    )

    drain = None
    if type(value) is dict and "drain" in value:
        # Optional {"upgrade_journal": bool}: operator-driven cancels of a DRAIN
        # cause's working orders (direct.control.drain_target) need journal
        # schema 17 and a trusted instrument identity for every strategy order.
        drain = _mapping(value["drain"], ("upgrade_journal",))
        if type(drain["upgrade_journal"]) is not bool:
            _invalid()
        drain = drain["upgrade_journal"]
        value = {name: item for name, item in value.items() if name != "drain"}
    release_audit = None
    if type(value) is dict and "release_audit" in value:
        # Optional {"upgrade_journal": bool}: the journal stores who released each
        # control cause (an operator or the Gateway mechanism) with the release, and
        # the control replies carry it. Present means the journal has it; a journal
        # without it is upgraded only with upgrade_journal true (one-way, at any
        # schema from 10 on, without turning on any other feature). Once upgraded,
        # the journal is not opened without this section.
        release_audit = _mapping(value["release_audit"], ("upgrade_journal",))
        if type(release_audit["upgrade_journal"]) is not bool:
            _invalid()
        release_audit = release_audit["upgrade_journal"]
        value = {name: item for name, item in value.items() if name != "release_audit"}
    control_intent_history = None
    control_intent_continuity = None
    if type(value) is dict and "control_intent_history" in value:
        # Optional {"upgrade_journal": bool, "upgrade_continuity"?: bool}:
        # installs/opens the Risk bound control-intent history (and optional
        # cross-generation continuity) extension on the journal. Requires
        # ``control_intent_enabled: true`` in Gateway service options; an existing
        # journal without it is upgraded only with upgrade_journal true.
        declared = value["control_intent_history"]
        optional_hist = (
            ("upgrade_continuity",)
            if type(declared) is dict and "upgrade_continuity" in declared
            else ()
        )
        parsed = _mapping(declared, ("upgrade_journal",) + optional_hist)
        if type(parsed["upgrade_journal"]) is not bool:
            _invalid()
        if "upgrade_continuity" in parsed and type(parsed["upgrade_continuity"]) is not bool:
            _invalid()
        control_intent_history = parsed["upgrade_journal"]
        control_intent_continuity = parsed.get("upgrade_continuity", False)
        value = {name: item for name, item in value.items() if name != "control_intent_history"}
    cancel_attempt_stream = None
    if type(value) is dict and "cancel_attempt_stream" in value:
        # Optional {"upgrade_journal": bool}: upgrades an existing journal to
        # install the R-04 cancel-attempt stream tables when
        # ``cancel_attempt_stream_enabled: true`` is configured on Gateway.
        parsed_cancel = _mapping(value["cancel_attempt_stream"], ("upgrade_journal",))
        if type(parsed_cancel["upgrade_journal"]) is not bool:
            _invalid()
        cancel_attempt_stream = parsed_cancel["upgrade_journal"]
        value = {name: item for name, item in value.items() if name != "cancel_attempt_stream"}
    lease_drain = None
    if type(value) is dict and "lease_drain" in value:
        # Optional (D63-28, off unless present): {"strategies": [strategy refs],
        # "expiry_seconds": number, "automatic_resume": bool}. When the lease of a
        # listed strategy has stayed expired for expiry_seconds, the Gateway applies
        # a DRAIN cause for that strategy and cancels its working orders. The
        # duration is mandatory and has no default. The cause is released once the
        # strategy has registered again and its restoration is proved: by the
        # Gateway itself when automatic_resume is true (the default when the key is
        # absent, D63-33), otherwise by an operator's resume, which needs the same
        # proof.
        from bt_api_py.gateway_lease_drain import GatewayLeaseDrainSettings

        declared = value["lease_drain"]
        if (type(declared) is not dict or not {"strategies", "expiry_seconds"} <= set(declared)
                or not set(declared) <= {"strategies", "expiry_seconds", "automatic_resume"}
                or type(declared["strategies"]) is not list
                or type(declared["expiry_seconds"]) not in (int, float)
                or type(declared.get("automatic_resume", True)) is not bool):
            _invalid()
        try:
            lease_drain = GatewayLeaseDrainSettings(
                frozenset(_string(item) for item in declared["strategies"]),
                declared["expiry_seconds"],
                automatic_resume=declared.get("automatic_resume", True))
        except ValueError:
            _invalid()
        if len(lease_drain.strategies) != len(declared["strategies"]):
            _invalid()
        value = {name: item for name, item in value.items() if name != "lease_drain"}
    value = _mapping(value, ("operators", "upgrade_controls_from_v9"))
    if (type(value["upgrade_controls_from_v9"]) is not bool or type(value["operators"]) is not list
            or not value["operators"]):
        _invalid()
    control = {}
    pending_control = []
    seen_principals = set()
    for operator in value["operators"]:
        # Optional (G64-03): "kind" ("operator", the default, "risk" for the Risk service's
        # control principal, or "runtime" for the Runtime service's D66-08 limited proxy),
        # "modes" (the apply modes it may use; required for "risk"; an operator without it
        # keeps all three) and "instrument_scope" (bool, default false: it may name one
        # contract; needs account_scope_allowed). A "risk" principal never holds
        # direct.control.resume (D64-21). A "runtime" principal must carry an explicit
        # "runtime_proof" naming an operator installed in this same bundle; it is
        # strategy-scoped and may not acquire operator operations. Such a bundle is refused
        # otherwise.
        optional = tuple(name for name in ("kind", "modes", "instrument_scope", "runtime_proof")
                         if type(operator) is dict and name in operator)
        operator = _mapping(operator, ("identity", "curve_public_key", "resources",
                                       "account_scope_allowed", "strategies") + optional)
        identity = _identity(operator["identity"])
        public = _curve(operator["curve_public_key"])
        kind = operator.get("kind", "operator")
        if (identity.role is not WireRole.APPLICATION_CLIENT
                or identity.principal_ref in seen_principals
                or public in principals or type(operator["resources"]) is not list
                or not operator["resources"] or type(operator["account_scope_allowed"]) is not bool
                or type(operator["strategies"]) is not list
                or kind not in ("operator", "risk", "runtime")
                or (kind == "risk" and "modes" not in operator)
                or (kind == "runtime" and "runtime_proof" not in operator)
                or (kind != "runtime" and "runtime_proof" in operator)
                or ("modes" in operator and (type(operator["modes"]) is not list or not operator["modes"]))
                or type(operator.get("instrument_scope", False)) is not bool):
            _invalid()
        seen_principals.add(identity.principal_ref)
        modes = _string_set(operator["modes"]) if "modes" in operator else None
        runtime_proof = None
        if kind == "runtime":
            declared = operator["runtime_proof"]
            extra = ("revoked",) if type(declared) is dict and "revoked" in declared else ()
            declared = _mapping(declared, ("delegation_id", "operator_ref", "expires_at") + extra)
            expires_at = declared["expires_at"]
            if (type(expires_at) not in (int, float) or isinstance(expires_at, bool)
                    or not math.isfinite(expires_at) or type(declared.get("revoked", False)) is not bool):
                _invalid()
            try:
                runtime_proof = GatewayRuntimeControlProof(
                    _string(declared["delegation_id"]), _string(declared["operator_ref"]),
                    float(expires_at), revoked=declared.get("revoked", False))
            except ValueError:
                _invalid()
        entries, operations = [], set()
        from bt_api_py.gateway_direct_service import CONTROL_INTENT_CONTINUITY_OPERATIONS

        allowed_item_operations = (
            CONTROL_OPERATIONS | CONTROL_INTENT_OPERATIONS | CONTROL_INTENT_CONTINUITY_OPERATIONS
            if kind == "risk" and operator["account_scope_allowed"]
            else CONTROL_OPERATIONS
        )
        for item in operator["resources"]:
            item = _mapping(item, ("instance_id", "port", "scope_ref", "scope", "operations"))
            item_operations = _string_set(item["operations"])
            item_scope = _scope(item["scope"])
            if (item["port"] != "service.direct_write"
                    or not item_operations <= allowed_item_operations
                    or (bool(item_operations & (CONTROL_INTENT_OPERATIONS | CONTROL_INTENT_CONTINUITY_OPERATIONS))
                        and item_scope.strategy_ref is not None)):
                _invalid()  # an operator never receives trading operations here
            entries.append(ResourceGrant(item["instance_id"], item["port"], item["scope_ref"],
                                         item_scope, item_operations))
            operations |= item_operations - (CONTROL_INTENT_OPERATIONS | CONTROL_INTENT_CONTINUITY_OPERATIONS)
        principals[public] = identity.principal_ref
        grants[identity.principal_ref] = (PrincipalGrant(identity, tuple(entries)),)
        pending_control.append(
            (identity, operator, frozenset(operations), kind, modes, runtime_proof))
    # A Runtime proof names a trusted operator of this same bundle; a name that is
    # not installed here proves nothing and is refused before anything is served.
    installed_operator_refs = frozenset(
        identity.actor_ref for identity, _operator, _operations, entry_kind, _modes, _proof
        in pending_control if entry_kind == "operator")
    for identity, operator, operations, kind, modes, runtime_proof in pending_control:
        if kind == "runtime" and runtime_proof.operator_ref not in installed_operator_refs:
            _invalid()
        try:
            control[identity.principal_ref] = GatewayControlGrant(
                identity.actor_ref, operator["account_scope_allowed"],
                frozenset(_string(item) for item in operator["strategies"]), operations,
                kind=kind, **({} if modes is None else {"modes": modes}),
                instrument_scope=operator.get("instrument_scope", False),
                **({} if runtime_proof is None else {"runtime_proof": runtime_proof}))
        except ValueError:
            _invalid()
    return (
        control,
        value["upgrade_controls_from_v9"],
        drain,
        lease_drain,
        release_audit,
        control_intent_history,
        control_intent_continuity,
        cancel_attempt_stream,
    )


def _delegation(value, principals, grants, direct, *, account_scope, execution_domain_ref, instruments):
    """Optional section for Risk and Execution delegates (D63-13 static registration, D63-37)::

        {"delegates": [{"identity": <service identity>, "curve_public_key": "...",
                        "kind": "risk", "revision": "risk-grant-1",
                        "strategies": {"<strategy ref>": {"instruments": [<instrument>],
                                                           "operations": ["direct.reserve", ...]}},
                        "resources": [<registry resource, as for a client>]}],
         "risk_only_submit": ["<strategy ref>"],
         "risk_first_enabled": false,
         "upgrade_parent_authority_from_v12": false}

    A delegate may act only for the listed strategies of this bundle's clients and only
    on instruments from ``returns.instruments``; ``operations`` are the logical
    operations it may perform for that strategy (``direct.reserve``,
    ``direct.dispatch``, ``direct.command_status``, ``direct.cancel``,
    ``direct.cancel_status``, ``direct.release``). ``risk_only_submit`` (optional, off
    unless present) leaves SUBMIT of each listed strategy to its Risk delegate alone.
    ``risk_first_enabled`` is optional and defaults to false. It installs only the
    explicitly granted v2 route; the separate journal write gate remains closed.

    A delegate of ``"kind": "execution"`` (63.13 base, D63-56) also names, per strategy,
    ``"parent_operations"``: a non-empty subset of ``direct.parent.register``,
    ``direct.parent.query``, ``direct.parent.cancel`` and ``direct.parent.operation``.
    The Gateway then installs a parent grant with the same revision and instruments
    and the parent authority (see ``_parent_authority_factory``); the operator config
    must set ``parent_authority_enabled: true``. ``upgrade_parent_authority_from_v12``
    (optional, only with an Execution delegate) lets an existing schema-12 journal walk
    to schema 13. Declarations prove nothing by themselves; the Registry still
    authenticates every call. Returns ``(delegation settings, parent settings or None,
    upgrade flag)``.
    """
    if value is None:
        return None, None, False
    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_gateway.direct_delegation import GatewayDelegateGrant

    from bt_api_py.gateway_delegation_service import GatewayDelegationSettings
    from bt_api_py.gateway_parent_service import GatewayParentGrant, GatewayParentSettings

    if type(value) is not dict:
        _invalid()
    value = _mapping(value, ("delegates",) + tuple(
        name for name in ("risk_only_submit", "risk_first_enabled", "upgrade_parent_authority_from_v12")
        if name in value))
    if type(value["delegates"]) is not list or not value["delegates"]:
        _invalid()
    risk_first_enabled = value.get("risk_first_enabled", False)
    if type(risk_first_enabled) is not bool:
        _invalid()
    risk_first_granted = False
    trusted = frozenset(instruments)
    strategies = frozenset(grant.strategy_ref for grant in direct.values())
    delegate_grants, parent_grants = [], []
    for delegate in value["delegates"]:
        delegate = _mapping(delegate, ("identity", "curve_public_key", "kind", "revision", "strategies", "resources"))
        identity = _identity(delegate["identity"])
        public = _curve(delegate["curve_public_key"])
        kind = delegate["kind"]
        if kind not in ("risk", "execution"):
            _LOG.error("The delegation section of the resource bundle was refused: %s", "DELEGATE_KIND_UNSUPPORTED")
            _invalid()
        if (identity.role is not WireRole.SERVICE or identity.delegate_ref is not None
                or identity.principal_ref in grants or public in principals
                or type(delegate["strategies"]) is not dict or not delegate["strategies"]
                or type(delegate["resources"]) is not list or not delegate["resources"]):
            _invalid()
        revision = _string(delegate["revision"])
        for strategy, granted in delegate["strategies"].items():
            granted = _mapping(granted, ("instruments", "operations")
                               + (("parent_operations",) if kind == "execution" else ()))
            if strategy not in strategies or type(granted["instruments"]) is not list or not granted["instruments"]:
                _invalid()
            allowed = tuple(MarketInstrumentKey.from_dict(item) for item in granted["instruments"])
            if len(set(allowed)) != len(allowed) or not set(allowed) <= trusted:
                _invalid()
            delegate_grants.append(GatewayDelegateGrant(identity, account_scope, strategy, execution_domain_ref,
                kind, revision, frozenset(allowed), _string_set(granted["operations"])))
            if kind == "execution":
                # The child's grant revision must equal its parent's: one revision for both.
                parent_grants.append(GatewayParentGrant(identity, account_scope, strategy, execution_domain_ref,
                    "execution", revision, frozenset(allowed), _string_set(granted["parent_operations"])))
        entries = []
        for item in delegate["resources"]:
            item = _mapping(item, ("instance_id", "port", "scope_ref", "scope", "operations"))
            resource_scope = _scope(item["scope"])
            operations = _string_set(item["operations"])
            if "direct.risk_first_reserve.v2" in operations:
                if (not risk_first_enabled or kind != "risk" or item["port"] != "service.direct_write"
                        or resource_scope.account_ref != account_scope
                        or resource_scope.execution_domain_ref != execution_domain_ref
                        or resource_scope.run_id is not None
                        or resource_scope.strategy_ref not in delegate["strategies"]
                        or "direct.reserve" not in delegate["strategies"][resource_scope.strategy_ref]["operations"]):
                    _invalid()
                risk_first_granted = True
            entries.append(ResourceGrant(item["instance_id"], item["port"], item["scope_ref"],
                                         resource_scope, operations))
        principals[public] = identity.principal_ref
        grants[identity.principal_ref] = (PrincipalGrant(identity, tuple(entries)),)
    risk_only = _string_set(value["risk_only_submit"]) if "risk_only_submit" in value else frozenset()
    upgrade = value.get("upgrade_parent_authority_from_v12", False)
    if type(upgrade) is not bool or (upgrade and not parent_grants):
        _invalid()
    if risk_first_enabled and not risk_first_granted:
        _invalid()
    # The instrument recheck names exactly the operator's declared return instruments.
    return (GatewayDelegationSettings(tuple(delegate_grants), lambda instrument: instrument in trusted,
                                      risk_only_submit_strategies=risk_only,
                                      risk_first_enabled=risk_first_enabled),
            GatewayParentSettings(tuple(parent_grants)) if parent_grants else None, upgrade)


def _parent_authority_factory():
    """Parent authority for the bundle's Execution delegates, built on the owner thread.

    Only same-day parents (D63-56: cross-day parents are not delivered): a parent
    allowing further trading days is refused at registration. The trading day is the
    live TD login's; without a session it is unknown and every registration and child
    check is refused. Expiry is compared with this host's clock, the clock of the
    loopback-only Execution service that wrote ``expires_at_ns``. This Gateway has no
    internal transfer between strategies (iteration 65), so no transfer can have
    completed for any parent. No close-target, rollover or Risk-child check is
    installed: children of a closing parent and Risk-placed children of a parent are
    refused.
    """
    def factory(journal, guard, session):
        from bt_api_gateway.direct_parent_authority import GatewayParentAuthority

        def trading_day():
            try:
                return session.direct_provider.trading_day()
            except Exception:
                return None

        def no_internal_transfer(_db, _spec):
            return "0"

        return GatewayParentAuthority(journal, guard, now_ns=time.time_ns, trading_day=trading_day,
                                      transfer_completed=no_internal_transfer, same_day_only=True)

    return factory


# A full CTP account read is three queries at least one query interval (1.05 s) apart.
_ACCOUNT_READ_TIMEOUT_MINIMUM = 3.0
_ACCOUNT_READ_TIMEOUT_DEFAULT = 6.0
# What an operator of the controls section may read on the account read port: the typed
# account snapshot the Web snapshot route shows (it reads what the read model holds).
# An account-wide operator's reads: the account view, and every strategy's funds and book
# (account.strategy_financial, account.strategy_book; G64-06).
_OPERATOR_READ_OPERATIONS = frozenset({"account.snapshot.typed", "account.strategy_financial",
                                       "account.strategy_book", "account.executions.recent"})


def _account_observation(value, principals, grants, *, instruments, account_operators=frozenset(),
                         order_cut_principals=frozenset(), additional_readers=frozenset()):
    """Optional section for account observers and the account read port (63.13 base, D63-56)::

        {"read_journal_path": "/absolute/path/account-read.sqlite",
         "observation_identity_path": "/absolute/path/observation-identity.sqlite",
         "expected_observation_issuer_id": "<issuer id of that identity store>",
         "state_scope": "account-state",
         "required_coverage": [{"product_type": "future", "margin_pool": "futures"}],
         "observers": [{"identity": <service identity>, "curve_public_key": "...",
                        "resources": [<registry resource on service.account_read>]}],
         "operator_reads": [{"principal_ref": "<an operator of the controls section>",
                             "resources": [<registry resource on service.account_read>]}],
         "refresh_interval_seconds": 1.0, "max_staleness_seconds": 5.0, "request_timeout_seconds": 2.0}

    Installs the account read port with its own read journal and account read model;
    the observed instruments are ``returns.instruments``. Each entry of ``observers``
    is a new service principal that only reads: its resources must be on
    ``service.account_read``, account-wide (no strategy) and hold only
    ``account.events.read_after``, ``account.events.progress`` and
    ``account.events.ack``. A delegate of the ``delegation`` section that should also
    observe lists such a resource among its own resources. Every principal with an
    ``service.account_read`` resource becomes an account reader; at least one is
    required. The last three keys are optional (defaults 1, 5 and 6 seconds). A full
    account read is three venue queries at least one query interval (1.05 s) apart, so
    it cannot end within 2.1 s plus the answers, and a read that runs past
    ``request_timeout_seconds`` stops the Gateway's maintenance: a value below 3 s is
    refused, and this section's default is 6 s, not the 2 s of the settings class. The
    optional ``operator_reads`` gives an operator of the ``controls`` section that may act
    on the whole account (``account_scope_allowed``; named by principal) its own resource
    on ``service.account_read``, account-wide, with ``account.snapshot.typed`` (the Web
    snapshot route reads the account view through it) and, optionally,
    ``account.strategy_financial`` and ``account.strategy_book`` (every strategy's funds
    and book, read by the Risk service's control principal, G64-06). The operator registers it
    separately and the Registry checks every call; an operator for strategies only gets
    no account read. The operator
    config must bind ``service.account_read`` and set ``account_extensions_enabled:
    true``. Returns ``(read settings, reader principals)``.
    """
    if value is None:
        return None, frozenset()
    from bt_api_base.account_protocol import AccountResourceKey

    from bt_api_py.ctp_gateway_trading import CtpGatewayAccountReadSettings
    from bt_api_py.gateway_account_observer import ACCOUNT_OBSERVER_OPERATIONS
    from bt_api_py.gateway_direct_service import (
        ACCOUNT_FACTS_OPERATIONS,
        ACCOUNT_ORDER_CUT_OPERATIONS,
    )

    required = ("read_journal_path", "observation_identity_path", "expected_observation_issuer_id",
                "state_scope", "required_coverage", "observers")
    timing = ("refresh_interval_seconds", "max_staleness_seconds", "request_timeout_seconds")
    if type(value) is not dict:
        _invalid()
    value = _mapping(value, required + tuple(name for name in timing + ("operator_reads",) if name in value))
    if (type(value["required_coverage"]) is not list or not value["required_coverage"]
            or type(value["observers"]) is not list
            or type(value.get("operator_reads", [])) is not list
            or any(type(value[name]) not in (int, float) for name in timing if name in value)):
        _invalid()
    timings = {name: float(value[name]) for name in timing if name in value}
    timings.setdefault("request_timeout_seconds", _ACCOUNT_READ_TIMEOUT_DEFAULT)
    if timings["request_timeout_seconds"] < _ACCOUNT_READ_TIMEOUT_MINIMUM:
        _LOG.error("The account_observation section of the resource bundle was refused: %s",
                   "ACCOUNT_READ_TIMEOUT_TOO_SHORT")
        _invalid()
    settings = CtpGatewayAccountReadSettings(
        _string(value["read_journal_path"]), _string(value["state_scope"]),
        tuple(AccountResourceKey.from_dict(item) for item in value["required_coverage"]),
        _string(value["observation_identity_path"]), _string(value["expected_observation_issuer_id"]),
        tuple(instruments), **timings)
    for observer in value["observers"]:
        observer = _mapping(observer, ("identity", "curve_public_key", "resources"))
        identity = _identity(observer["identity"])
        public = _curve(observer["curve_public_key"])
        if (identity.role is not WireRole.SERVICE or identity.delegate_ref is not None
                or identity.principal_ref in grants or public in principals
                or type(observer["resources"]) is not list or not observer["resources"]):
            _invalid()
        entries = []
        for item in observer["resources"]:
            item = _mapping(item, ("instance_id", "port", "scope_ref", "scope", "operations"))
            scope, operations = _scope(item["scope"]), _string_set(item["operations"])
            permitted = (ACCOUNT_OBSERVER_OPERATIONS | ACCOUNT_FACTS_OPERATIONS
                         | (ACCOUNT_ORDER_CUT_OPERATIONS
                            if identity.principal_ref in order_cut_principals else frozenset()))
            if (item["port"] != "service.account_read" or not operations <= permitted
                    or (operations & ACCOUNT_ORDER_CUT_OPERATIONS
                        and not ACCOUNT_ORDER_CUT_OPERATIONS <= operations)
                    or scope.strategy_ref is not None):
                _invalid()  # an observer reads the whole account; it never trades or controls
            entries.append(ResourceGrant(item["instance_id"], item["port"], item["scope_ref"], scope, operations))
        principals[public] = identity.principal_ref
        grants[identity.principal_ref] = (PrincipalGrant(identity, tuple(entries)),)
    named = set()
    for read in value.get("operator_reads", []):
        read = _mapping(read, ("principal_ref", "resources"))
        principal = _string(read["principal_ref"])
        declared = grants.get(principal)
        if (principal in named or principal not in account_operators or declared is None or len(declared) != 1
                or declared[0].identity.role is not WireRole.APPLICATION_CLIENT
                or type(read["resources"]) is not list or not read["resources"]):
            _invalid()
        named.add(principal)
        entries = []
        for item in read["resources"]:
            item = _mapping(item, ("instance_id", "port", "scope_ref", "scope", "operations"))
            scope, operations = _scope(item["scope"]), _string_set(item["operations"])
            if (item["port"] != "service.account_read" or not operations <= _OPERATOR_READ_OPERATIONS
                    or scope.strategy_ref is not None):
                _invalid()  # the account view only; never strategy-scoped, never a stream or write
            entries.append(ResourceGrant(item["instance_id"], item["port"], item["scope_ref"], scope, operations))
        grants[principal] = (PrincipalGrant(declared[0].identity, declared[0].resources + tuple(entries)),)
    readers = frozenset(principal for principal, declared in grants.items()
                        if any(resource.port == "service.account_read"
                               for entry in declared for resource in entry.resources))
    extra = frozenset(additional_readers)
    if any(type(principal) is not str or principal not in grants for principal in extra):
        _invalid()
    readers = readers | extra
    if not readers:
        _invalid()
    return settings, readers


def _strategy_origin_source_factory(instruments):
    """Name the complete instrument of a strategy order from the operator's own list.

    The list is the bundle's ``returns.instruments``: the same trusted
    declaration the strategy ledger resolves against. An order for anything
    else has no origin and is refused. The source is built on the owner thread.
    """
    from bt_api_py.gateway_strategy_origin_source import snapshot_strategy_origin_instruments

    # The factory can outlive the parsed bundle; do not retain its mutable keys.
    declared = snapshot_strategy_origin_instruments(instruments)

    def factory(_journal, _guard, _session):
        from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource

        from bt_api_base.market_protocol import MarketInstrumentKey

        return GatewayStrategyOriginSource(
            instruments=tuple(MarketInstrumentKey(*entry) for entry in declared))

    return factory


def _clock_settings(value):
    """Trusted Gateway clock policy: SNTP servers plus periodic refresh."""
    if value is None:
        return None
    from bt_api_py.clock_verification import ClockVerifier
    from bt_api_py.gateway_server_clock_refresh import GatewayServerClockRefreshSettings

    if type(value) is not dict:
        _invalid()
    value = _mapping(value, ("servers", "max_error_bound_ms", "max_sample_validity_seconds",
                             "refresh_interval_seconds") + (("history_capacity",) if "history_capacity" in value else ()))
    servers = value["servers"]
    if (type(servers) is not list or not servers or len(servers) > 8
            or len(set(servers)) != len(servers)):
        _invalid()
    servers = tuple(_string(item) for item in servers)
    error_ms = value["max_error_bound_ms"]
    if type(error_ms) not in (int, float) or not 0 < error_ms <= 500:
        _invalid()
    refresh = GatewayServerClockRefreshSettings(
        max_error_bound_ns=int(error_ms * 1_000_000),
        max_sample_validity_seconds=value["max_sample_validity_seconds"],
        refresh_interval_seconds=value["refresh_interval_seconds"],
        history_capacity=value.get("history_capacity", 0))
    return (lambda: ClockVerifier(servers=servers)), frozenset(servers), refresh


def _recovery_observation_settings(value, settings):
    """Parse only explicit local observation settings; create no journal."""
    from bt_api_py.gateway_recovery_observation import GatewayRecoveryObservationSettings

    value = _mapping(value, (
        "journal_path", "source_capacity", "page_limit", "max_events", "max_bytes",
        "sqlite_timeout_seconds", "poll_interval_seconds", "owner_wait_seconds",
        "close_timeout_seconds", "qualification_mode", "clock_max_interval_ns",
    ))
    return GatewayRecoveryObservationSettings(
        **value, scope_ref=settings.account_scope, source_ref=settings.market_source_ref
    )


def _execution_subject_scopes(value):
    if value is None:
        return None
    from bt_api_execution.contracts import ExecutionScope
    from bt_api_py.gateway_execution_subject_scopes import GatewayExecutionSubjectScopes
    value = _mapping(value, ('protected_accounts', 'aliases'))
    accounts = _string_set(value['protected_accounts'])
    if type(value['aliases']) is not list or not value['aliases']:
        _invalid()
    aliases = {}
    for row in value['aliases']:
        row = _mapping(row, ('execution_domain_ref', 'account_ref', 'strategy_ref', 'target'))
        wire = WireScope(_string(row['execution_domain_ref']), account_ref=_string(row['account_ref']),
                         strategy_ref=_string(row['strategy_ref']))
        target = row['target']
        if target is not None:
            target = _mapping(target, ('provider', 'environment', 'account_ref', 'strategy_id'))
            target = ExecutionScope(**target)
        if wire in aliases:
            _invalid()
        aliases[wire] = target
    return GatewayExecutionSubjectScopes(accounts, aliases)


def _load_resources(config, *, trading):
    try:
        keys = ("schema_version", "registry_path", "settings", "credentials", "authority", "clients",
                "market") + (("returns",) if trading else ())
        resource_path = os.environ.get(_RESOURCE_ENV)
        raw = _protected_json(resource_path)
        # A trading bundle may add a "clock" section (trusted SNTP policy).
        optional = ({"clock", "controls", "strategy_ledger", "strategy_financial", "delegation", "execution_subject_scopes", "required_monitor",
                     "account_observation", "recovery_observation", "recovery_monitor_relay", "instrument_status_read", "instrument_product_observers", "order_cut_observation"} if trading else set()) | {"calendars"}
        if type(raw) is not dict or not set(keys) <= set(raw) <= set(keys) | optional:
            _invalid()
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            _invalid()
        if type(raw["settings"]) is not dict:
            _invalid()
        settings_raw = dict(raw["settings"])
        if set(settings_raw) - {item.name for item in fields(CtpGatewayTradingSettings)}:
            _invalid()
        write_enabled = settings_raw.get("write_enabled", False)
        if type(write_enabled) is not bool or (write_enabled and not trading):
            _invalid()
        settings_raw["write_enabled"] = write_enabled
        settings_raw["account_key"] = AccountKey.from_dict(settings_raw["account_key"])
        if write_enabled and (settings_raw["account_key"].venue != "simnow"
                              or settings_raw["account_key"].environment != "simnow"):
            raise CtpGatewayWriteRequiresBrokerConfirmation()
        settings = CtpGatewayTradingSettings(**settings_raw)
        credentials = _mapping(raw["credentials"], ("broker_id", "user_id", "password", "auth_code", "app_id", "td_front", "md_front"))
        if (credentials["broker_id"] != settings.account_key.subaccount_id
                or credentials["user_id"] != settings.account_key.account_id):
            _invalid()
        for value in credentials.values():
            _string(value)
        authority_raw = dict(_mapping(raw["authority"], ("identity", "scope", "scope_ref", "scope_version",
            "security_ref", "security_version", "registration_policy_ref", "registration_policy_version",
            "service_requirements_ref", "service_requirements_version", "curve_public_key", "curve_secret_key")))
        authority_raw["identity"] = _identity(authority_raw["identity"])
        authority_raw["scope"] = _scope(authority_raw["scope"])
        authority_raw["curve_public_key"] = _curve(authority_raw["curve_public_key"])
        authority_raw["curve_secret_key"] = _curve(authority_raw["curve_secret_key"])
        import zmq
        if zmq.curve_public(authority_raw["curve_secret_key"]) != authority_raw["curve_public_key"]:
            _invalid()
        if type(raw["clients"]) is not list or not raw["clients"]:
            _invalid()
        principals, grants, direct = {}, {}, {}
        for client in raw["clients"]:
            client = _mapping(client, ("identity", "curve_public_key", "resources", "symbols"))
            identity = _identity(client["identity"])
            public = _curve(client["curve_public_key"])
            if identity.principal_ref in grants or public in principals or type(client["resources"]) is not list:
                _invalid()
            entries = []
            for item in client["resources"]:
                item = _mapping(item, ("instance_id", "port", "scope_ref", "scope", "operations"))
                entries.append(ResourceGrant(item["instance_id"], item["port"], item["scope_ref"],
                                             _scope(item["scope"]), _string_set(item["operations"])))
            principals[public] = identity.principal_ref
            grants[identity.principal_ref] = (PrincipalGrant(identity, tuple(entries)),)
            direct[identity.principal_ref] = GatewayDirectGrant(identity.actor_ref, _string_set(client["symbols"]))
        calendar_raw = raw.get("calendars")
        monitor_identities = []
        product_observer_clients = {}
        product_grants = frozenset()
        if "instrument_product_observers" in raw:
            from bt_api_base.market_protocol import MarketInstrumentKey
            from bt_api_py.gateway_instrument_product_codec import OPERATIONS
            from bt_api_py.gateway_instrument_product_config import InstrumentProductGrant

            observers = _mapping(raw["instrument_product_observers"], ("clients", "grants"))
            if (type(observers["clients"]) is not list or not observers["clients"]
                    or type(observers["grants"]) is not list or not observers["grants"]):
                _invalid()
            for client in observers["clients"]:
                client = _mapping(client, ("identity", "curve_public_key", "resources"))
                identity = _identity(client["identity"])
                public = _curve(client["curve_public_key"])
                if (identity.role is not WireRole.SERVICE or identity.delegate_ref is not None
                        or identity.principal_ref in grants or public in principals
                        or type(client["resources"]) is not list or len(client["resources"]) != 1):
                    _invalid()
                resources = []
                for item in client["resources"]:
                    item = _mapping(item, ("instance_id", "port", "scope_ref", "scope", "operations"))
                    operations = _string_set(item["operations"])
                    if item["port"] != "service.instrument_product_read" or operations != OPERATIONS:
                        _invalid()
                    resources.append(ResourceGrant(item["instance_id"], item["port"], item["scope_ref"],
                                                   _scope(item["scope"]), operations))
                principals[public] = identity.principal_ref
                grants[identity.principal_ref] = (PrincipalGrant(identity, tuple(resources)),)
                product_observer_clients[identity.principal_ref] = (public, identity, resources[0])
                monitor_identities.append(identity)
            result = []
            for item in observers["grants"]:
                item = _mapping(item, ("principal_ref", "actor_ref", "execution_domain_ref", "account_ref", "instruments"))
                if type(item["instruments"]) is not list or not item["instruments"]:
                    _invalid()
                instruments = tuple(MarketInstrumentKey.from_dict(key) for key in item["instruments"])
                if len(set(instruments)) != len(instruments):
                    _invalid()
                result.append(InstrumentProductGrant(item["principal_ref"], item["actor_ref"],
                    item["execution_domain_ref"], item["account_ref"], frozenset(instruments)))
            product_grants = frozenset(result)
            if len(product_grants) != len(result):
                _invalid()
        if type(calendar_raw) is dict and "monitor_clients" in calendar_raw:
            from bt_api_py.gateway_calendar_service import CALENDAR_MONITOR_WINDOWS_OPERATION

            clients = calendar_raw["monitor_clients"]
            if ("monitor_grants" not in calendar_raw or type(clients) is not list or not clients):
                _invalid()
            calendar_principals, calendar_keys = set(), set()
            for client in clients:
                client = _mapping(client, ("identity", "curve_public_key", "resources"))
                identity = _identity(client["identity"])
                public = _curve(client["curve_public_key"])
                if (identity.role is not WireRole.SERVICE or identity.delegate_ref is not None
                        or identity.principal_ref in calendar_principals or public in calendar_keys
                        or type(client["resources"]) is not list or len(client["resources"]) != 1):
                    _invalid()
                entries = []
                for item in client["resources"]:
                    item = _mapping(item, ("instance_id", "port", "scope_ref", "scope", "operations"))
                    operations = _string_set(item["operations"])
                    if (item["port"] != "service.calendar_read"
                            or operations != frozenset({CALENDAR_MONITOR_WINDOWS_OPERATION})):
                        _invalid()
                    entries.append(ResourceGrant(item["instance_id"], item["port"], item["scope_ref"],
                                                 _scope(item["scope"]), operations))
                prior = product_observer_clients.get(identity.principal_ref)
                if prior is not None:
                    previous_public, previous_identity, previous_resource = prior
                    current = entries[0]
                    if (public != previous_public or identity != previous_identity
                            or principals.get(public) != identity.principal_ref
                            or (current.instance_id, current.scope_ref, current.scope) != (
                                previous_resource.instance_id, previous_resource.scope_ref, previous_resource.scope)):
                        _invalid()
                    grants[identity.principal_ref] = (PrincipalGrant(identity, (previous_resource, current)),)
                else:
                    if identity.principal_ref in grants or public in principals:
                        _invalid()
                    principals[public] = identity.principal_ref
                    grants[identity.principal_ref] = (PrincipalGrant(identity, tuple(entries)),)
                    monitor_identities.append(identity)
                calendar_principals.add(identity.principal_ref)
                calendar_keys.add(public)
        control_grants, upgrade_controls, drain_upgrade, lease_drain, release_audit, control_intent_history, control_intent_continuity, cancel_attempt_stream = None, False, None, None, None, None, None, None
        if trading and raw.get("controls") is not None:
            (
                control_grants,
                upgrade_controls,
                drain_upgrade,
                lease_drain,
                release_audit,
                control_intent_history,
                control_intent_continuity,
                cancel_attempt_stream,
            ) = _controls(raw["controls"], principals, grants)
        delegation, parent_settings, upgrade_parents = None, None, False
        if trading and raw.get("delegation") is not None:
            delegation, parent_settings, upgrade_parents = _delegation(
                raw["delegation"], principals, grants, direct,
                account_scope=settings.account_scope,
                execution_domain_ref=authority_raw["scope"].execution_domain_ref,
                instruments=_return_settings(raw["returns"]).instruments)
        order_cut_observation = None
        if trading and 'order_cut_observation' in raw:
            from bt_api_py.ctp_gateway_order_cut_source import CtpGatewayOrderCutSettings
            value = _mapping(raw['order_cut_observation'],
                             ('query_timeout_seconds', 'refresh_interval_seconds', 'principals'))
            if type(value['principals']) is not list or len(set(value['principals'])) != len(value['principals']):
                _invalid()
            order_cut_observation = CtpGatewayOrderCutSettings(
                value['query_timeout_seconds'], value['refresh_interval_seconds'],
                _string_set(value['principals']))
        # After every other section: a delegate's own read resource also makes it a reader.
        read_settings, read_principals = None, frozenset()
        if trading and raw.get("account_observation") is not None:
            delegation_readers = frozenset(
                delegate.get("identity", {}).get("principal_ref")
                for delegate in (raw.get("delegation") or {}).get("delegates", [])
                if any(
                    resource.get("port") == "service.account_read"
                    and any(
                        str(operation).startswith(("account.facts.", "account.order_cut.", "account.cancel_attempts."))
                        for operation in resource.get("operations", [])
                    )
                    for resource in delegate.get("resources", [])
                )
            )
            read_settings, read_principals = _account_observation(
                raw["account_observation"], principals, grants,
                instruments=_return_settings(raw["returns"]).instruments,
                account_operators=frozenset(principal for principal, grant in (control_grants or {}).items()
                                            if grant.account_scope_allowed),
                order_cut_principals=(frozenset() if order_cut_observation is None
                                      else order_cut_observation.principals),
                additional_readers=delegation_readers)
        for monitor_identity in monitor_identities:
            if any(entry.identity.actor_ref == monitor_identity.actor_ref
                   for principal, entries in grants.items() if principal != monitor_identity.principal_ref
                   for entry in entries):
                _invalid()
        authority = TrustedServiceAuthority(**authority_raw, principals=principals, grants=grants)
        from bt_api_py.gateway_instrument_product_config import validate_product_grants
        validate_product_grants(product_grants, config=config, authority=authority,
                                account_key=settings.account_key, account_scope=settings.account_scope)
        from bt_api_py.ctp_gateway_calendar_config import parse_calendar_settings, validate_calendar_settings
        calendar_settings = (parse_calendar_settings(
            raw["calendars"], config_file=resource_path, config=config,
            authority=authority, account_scope=settings.account_scope,
        ) if "calendars" in raw else None)
        validate_calendar_settings(calendar_settings, config=config, authority=authority,
                                   account_scope=settings.account_scope)
        required_monitor = None
        if "required_monitor" in raw:
            from bt_api_py.ctp_gateway_monitor_config import build_required_monitor_settings

            required_monitor = build_required_monitor_settings(
                raw["required_monitor"], config=config, authority=authority,
                account_scope=settings.account_scope)
        market = None
        if raw["market"] is not None:
            market_keys = ("subscriptions", "principal_allowed_indices")
            if type(raw["market"]) is dict and "dispatch_max_latency_seconds" in raw["market"]:
                market_keys += ("dispatch_max_latency_seconds",)
            if type(raw["market"]) is dict and "allow_stale_quote" in raw["market"]:
                market_keys += ("allow_stale_quote",)
            value = _mapping(raw["market"], market_keys)
            if type(value["subscriptions"]) is not list or type(value["principal_allowed_indices"]) is not dict:
                _invalid()
            keys = tuple(MarketSubscriptionKey.from_dict(item) for item in value["subscriptions"])
            if len(set(keys)) != len(keys):
                _invalid()
            allowed = {}
            for principal, indices in value["principal_allowed_indices"].items():
                if (type(indices) is not list or not indices or len(set(indices)) != len(indices)
                        or any(type(index) is not int or not 0 <= index < len(keys) for index in indices)):
                    _invalid()
                allowed[principal] = frozenset(keys[index] for index in indices)
            latency = value.get("dispatch_max_latency_seconds", 2.0)
            if type(latency) not in (int, float):
                _invalid()
            allow_stale_quote = value.get("allow_stale_quote", False)
            if type(allow_stale_quote) is not bool:
                _invalid()
            market = CtpGatewayMarketSettings(frozenset(keys), allowed,
                                              dispatch_max_latency_seconds=float(latency),
                                              allow_stale_quote=allow_stale_quote)
        options = dict(credentials, ctp_env_profile="config_front_pair", ctp_environment="simnow",
                       trusted_flow_root=str(settings.trusted_flow_root), flow_account_scope=settings.account_scope)
        def request_feed_factory():
            from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
            return CtpRequestDataFuture(None, auto_settlement_confirm=False, **options)
        def market_stream_factory(_key, queue):
            from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream
            # SimNow's MD login response carries request id 0 and empty session
            # fields; without this the login is dropped as a request-id mismatch
            # and every subscription waits forever (same as ctp_account_process).
            return CtpMarketStream(queue, allow_zero_md_login_response=True, **options)
        if trading:
            instrument_status_read = None
            if 'instrument_status_read' in raw:
                from bt_api_base.market_protocol import MarketInstrumentKey

                from bt_api_py.gateway_instrument_status_service import (
                    GatewayInstrumentStatusReadSettings,
                )
                value = _mapping(raw['instrument_status_read'], ('principal_instruments', 'owner_wait_seconds'))
                if type(value['principal_instruments']) is not dict:
                    _invalid()
                instruments = {}
                for principal, keys in value['principal_instruments'].items():
                    if type(keys) is not list:
                        _invalid()
                    instruments[principal] = tuple(MarketInstrumentKey.from_dict(key) for key in keys)
                instrument_status_read = GatewayInstrumentStatusReadSettings(
                    instruments, value['owner_wait_seconds'])
            clock = _clock_settings(raw.get("clock"))
            recovery_observation = (_recovery_observation_settings(raw["recovery_observation"], settings)
                                    if "recovery_observation" in raw else None)
            recovery_monitor_relay = None
            if "recovery_monitor_relay" in raw:
                from bt_api_py.ctp_gateway_recovery_relay_config import (
                    build_recovery_monitor_settings,
                )

                recovery_monitor_relay = build_recovery_monitor_settings(
                    raw["recovery_monitor_relay"], config=config, authority=authority,
                    account_scope=settings.account_scope, observation=recovery_observation,
                )
            if market is not None and clock is None:
                # Decision B qualifies every dispatch against a fresh quote and
                # the Gateway's own verified clock; without one no order could
                # ever be dispatched, so refuse the bundle at startup instead.
                _invalid()
            verifier_factory, allowed_servers, refresh = clock or (None, None, None)
            upgrade_extensions, comparison_refresh = _strategy_ledger_account_comparison(
                raw.get("strategy_ledger"))
            return_settings = _return_settings(raw["returns"])
            drain_options = {}
            if drain_upgrade is not None:
                # Schema 17 lies behind 12 to 16; their own features stay off
                # unless enabled elsewhere, only the journal walks through.
                upgrade_extensions = upgrade_extensions or drain_upgrade
                gw_opts = (
                    config.components["gateway"]["settings"]["options"]
                    if hasattr(config, "components") and "gateway" in config.components
                    else {}
                )
                drain_options = dict(
                    upgrade_parent_authority_from_v12=drain_upgrade,
                    upgrade_account_facts_financial_from_v13=drain_upgrade,
                    upgrade_actor_stream_from_v14=drain_upgrade,
                    upgrade_strategy_origin_from_v15=drain_upgrade,
                    upgrade_control_cancel_from_v16=drain_upgrade,
                    upgrade_account_fact_recovery_from_v17=drain_upgrade,
                    upgrade_account_event_recovery_from_v18=bool(
                        drain_upgrade and gw_opts.get("account_event_recovery_enabled") is True
                    ),
                    strategy_origin_source_factory=_strategy_origin_source_factory(
                        return_settings.instruments))
            if upgrade_parents:
                drain_options["upgrade_parent_authority_from_v12"] = True
            # Admission is the route WriteAdmission plus Registry grants; the
            # operator gate is open only in this explicitly installed profile.
            return CtpGatewayTrustedResources(
                authority, _string(raw["registry_path"]), settings, direct,
                request_feed_factory, market_stream_factory, lambda _record: True,
                return_settings=return_settings, market_settings=market,
                clock_verifier_factory=verifier_factory, clock_allowed_servers=allowed_servers,
                clock_refresh_settings=refresh, control_grants=control_grants,
                instrument_status_read=instrument_status_read,
                required_monitor=required_monitor,
                upgrade_controls_from_v9=upgrade_controls,
                upgrade_strategy_ledger_from_v10=_strategy_ledger_upgrade(
                    raw.get("strategy_ledger")),
                strategy_ledger_external_baseline=_strategy_ledger_external_baseline(
                    raw.get("strategy_ledger")),
                upgrade_account_extensions_from_v11=upgrade_extensions,
                strategy_ledger_comparison_refresh_seconds=comparison_refresh,
                strategy_ledger_source_cut_mode=_strategy_ledger_source_cut_mode(
                    raw.get("strategy_ledger")),
                lease_drain_settings=lease_drain,
                strategy_financial_settings=_strategy_financial(
                    raw.get("strategy_financial"),
                    frozenset(grant.strategy_ref for grant in direct.values())),
                delegation_settings=delegation,
                parent_settings=parent_settings,
                execution_subject_scopes=_execution_subject_scopes(raw.get('execution_subject_scopes')),
                parent_authority_factory=None if parent_settings is None else _parent_authority_factory(),
                account_read_settings=read_settings,
                account_read_principals=read_principals,
                order_cut_observation=order_cut_observation,
                calendar_settings=calendar_settings,
                instrument_product_grants=product_grants,
                recovery_observation_settings=recovery_observation,
                recovery_monitor_relay=recovery_monitor_relay,
                control_release_audit_enabled=release_audit is not None,
                upgrade_control_release_audit=release_audit is True,
                upgrade_control_intent_history=control_intent_history is True,
                upgrade_control_intent_continuity=control_intent_continuity is True,
                upgrade_cancel_attempt_stream=cancel_attempt_stream is True,
                **drain_options,
            )
        return CtpGatewayTrustedResources(
            authority, _string(raw["registry_path"]), settings, direct,
            request_feed_factory, market_stream_factory, lambda _record: False, market_settings=market,
            calendar_settings=calendar_settings,
        )
    except CtpLocalProfileError:
        raise
    except Exception:
        _invalid()


profile_factory = create_ctp_gateway_profile_factory("ctp-local-readonly", resource_loader=load_local_ctp_resources)
simnow_trading_profile_factory = create_ctp_gateway_profile_factory(
    "ctp-simnow-trading", resource_loader=load_simnow_trading_resources)
simnow_market_observe_profile_factory = GatewayOperatorProfileFactory(
    "ctp-simnow-market-observe",
    "market_hub",
    create_simnow_market_observe_deployment,
)
simnow_7x24_market_observe_profile_factory = GatewayOperatorProfileFactory(
    "ctp-simnow-7x24-market-observe",
    "market_hub",
    create_simnow_7x24_market_observe_deployment,
)
