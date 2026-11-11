"""Public, providerless Gateway strategy configuration and inert composition.

The configuration contains declarations only. It does not load secrets,
connect, attest a market source, or grant account or trading authority.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_direct_client import GatewayClientRoute, _check_binding_route
from bt_api_py.gateway_execution_time import GatewayExecutionTimePolicy
from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.gateway_strategy_instrument import GatewayInstrumentClientSettings
from bt_api_py.gateway_strategy_market import (
    GatewayMarketClientSettings,
    GatewayMarketSubscriptionSettings,
)
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_market import ExpectedMarketSourceIdentity
from bt_api_py.service_runtime import PORT_OPERATIONS


class GatewayStrategyConfigError(ValueError):
    """A finite error that never includes configuration values."""


class _RiskSelectionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


# D64-01: the tree must say explicitly whether PLACE goes through Risk.
RISK_SELECTION_NONE = "none"


@dataclass(frozen=True, slots=True)
class GatewayRiskSelection:
    """Public description of the strategy's Risk client; it grants nothing."""

    client_config: object
    route: object
    peer: WireIdentity
    requested_operations: frozenset

    @property
    def security_profile_ref(self):
        profile = next(iter(self.client_config.communication["profiles"].values()))
        return profile["security_profile_ref"]


def _mapping(value, required, optional=()):
    if (
        type(value) is not dict
        or not set(required) <= value.keys()
        or value.keys() - set(required) - set(optional)
    ):
        raise ValueError
    return value


def _list(value):
    if type(value) is not list or not value:
        raise ValueError
    return value


def _operations(value):
    value = _list(value)
    if any(type(item) is not str or not item for item in value) or len(set(value)) != len(value):
        raise ValueError
    return frozenset(value)


def _plain(value, depth=0):
    if depth > 32:
        raise ValueError
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _plain(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _plain(item, depth + 1)
        return
    raise ValueError


def _identity(value):
    value = _mapping(value, {"role", "principal_ref", "actor_ref", "delegate_ref"})
    if type(value["role"]) is not str:
        raise ValueError
    return WireIdentity(
        WireRole(value["role"]), value["principal_ref"], value["actor_ref"], value["delegate_ref"]
    )


def _route(value):
    return GatewayClientRoute(*_route_fields(value))


def _account(value):
    if value is None:
        return None
    value = _mapping(
        value,
        {"expected_account", "required_coverage", "requested_operations"},
        {"require_open_orders"},
    )
    return GatewayAccountClientSettings(
        AccountKey.from_dict(value["expected_account"]),
        tuple(AccountResourceKey.from_dict(item) for item in _list(value["required_coverage"])),
        _operations(value["requested_operations"]),
        value.get("require_open_orders", False),
    )


def _market(value):
    if value is None:
        return None
    value = _mapping(value, {"subscriptions"}, {"requested_operations"})
    raw = value["subscriptions"]
    if type(raw) is not dict:
        raise ValueError
    subscriptions = {}
    for name, item in raw.items():
        item = _mapping(
            item,
            {"key"},
            {
                "queue_capacity",
                "queue_max_bytes",
                "head_check_interval_seconds",
                "require_source_descriptor",
                "expected_source_identity",
            },
        )
        fields = dict(item)
        fields["key"] = MarketSubscriptionKey.from_dict(fields["key"])
        if fields.get("expected_source_identity") is not None:
            expected = _mapping(
                fields["expected_source_identity"],
                {"source", "clock_domain_id", "clock_source_session_id", "clock_source"},
            )
            fields["expected_source_identity"] = ExpectedMarketSourceIdentity(**expected)
        subscriptions[name] = GatewayMarketSubscriptionSettings(**fields)
    kwargs = {"subscriptions": subscriptions}
    if "requested_operations" in value:
        kwargs["requested_operations"] = _operations(value["requested_operations"])
    return GatewayMarketClientSettings(**kwargs)


def _instrument(value):
    if value is None:
        return None
    value = _mapping(value, {"instruments", "requested_operations"})
    return GatewayInstrumentClientSettings(
        tuple(MarketInstrumentKey.from_dict(item) for item in _list(value["instruments"])),
        _operations(value["requested_operations"]),
    )


def _validate_client(client, binding, route, peer, operations, account, market, instrument):
    # Mirror the existing session's public configuration cross-checks without
    # constructing its runtime or introducing transport credentials.
    if client.process_role != "strategy_client" or set(client.clients) != {"gateway"}:
        raise ValueError
    bindings = client.clients["gateway"]["settings"]["bindings"]
    expected = {"broker.direct_write"}
    if account is not None or instrument is not None:
        expected.add("store.account")
    if market is not None:
        expected.add("store.market")
    indexed = {item["mount"]: item for item in bindings}
    if len(bindings) != len(indexed) or set(indexed) != expected:
        raise ValueError
    selected = indexed["broker.direct_write"]
    for item in indexed.values():
        if item["required"] is not True or any(
            item[name] != selected[name]
            for name in ("service_id", "instance_id", "scope_ref", "transport_ref")
        ):
            raise ValueError
        if item["mount"] != "store.market" and item.get("account_ref") != selected["account_ref"]:
            raise ValueError
    profiles = client.communication["profiles"]
    if set(profiles) != {selected["transport_ref"]}:
        raise ValueError
    profile = profiles[selected["transport_ref"]]
    endpoints = {"rpc_connect"} if market is None else {"rpc_connect", "market_connect"}
    if profile["backend"] != "zmq" or set(profile["endpoints"]) != endpoints:
        raise ValueError
    if (
        selected["service_id"] != route.service_id
        or selected["instance_id"] != route.instance_id
        or selected["scope_ref"] != route.scope_ref
        or selected["account_ref"] != binding.account_scope
        or client.registration["strategy_ref"] != binding.strategy_scope
        or route.identity.delegate_ref is not None
        or peer.role is not WireRole.SERVICE
        or peer.actor_ref != route.service_id
        or peer.delegate_ref is not None
        or len(route.scope.run_id) > 128
    ):
        raise ValueError
    _check_binding_route(binding, route)
    if (
        not operations <= PORT_OPERATIONS["service.direct_write"]
        or (
            operations & {"direct.reserve", "direct.release", "direct.dispatch"}
            and "direct.command_status" not in operations
        )
        or ("direct.cancel" in operations and "direct.cancel_status" not in operations)
        # A strategy that may reserve must be able to withdraw a reservation that
        # was never dispatched. Without direct.release such an order could not be
        # ended and blocked every later order on its instrument (I63-E136).
        or ("direct.reserve" in operations and "direct.release" not in operations)
    ):
        raise ValueError


def _risk(value, client, route, operations):
    """Parse ``risk``: ``"none"`` or the Risk client, described like the Gateway client."""
    if type(value) is str and value == RISK_SELECTION_NONE:
        return None
    from bt_api_py.risk_service_client import RiskClientRoute, validate_risk_client_config

    try:
        value = _mapping(value, {"client_config", "route", "peer", "requested_operations"})
        risk_client = parse_client_config(value["client_config"])
        parsed = _route_fields(value["route"])
        risk_route = RiskClientRoute(*parsed)
        peer = _identity(value["peer"])
        risk_operations = _operations(value["requested_operations"])
        validate_risk_client_config(risk_client, risk_route, peer, risk_operations)
        gateway_profile = next(iter(client.communication["profiles"].values()))
        risk_profile = next(iter(risk_client.communication["profiles"].values()))
        if (
            # The Risk route is the same strategy run on the same account.
            risk_route.scope != route.scope
            or risk_route.identity.actor_ref != route.identity.actor_ref
            # Separate server keys need separate profile names.
            or risk_profile["security_profile_ref"] == gateway_profile["security_profile_ref"]
            # PLACE never goes to the Gateway directly while Risk is selected (D64-02).
            or operations & {"direct.reserve", "direct.dispatch"}
            # Orders placed through Risk are observed and cancelled as delegated
            # commands, also while Risk is unavailable (D64-02).
            or not {"direct.delegated_command_status", "direct.delegated_cancel",
                    "direct.delegated_cancel_status"} <= operations
        ):
            raise ValueError
    except Exception:
        raise _RiskSelectionError("risk_selection_invalid") from None
    return GatewayRiskSelection(risk_client, risk_route, peer, risk_operations)


def _route_fields(value):
    value = _mapping(
        value, {"mount", "service_id", "instance_id", "scope_ref", "scope", "identity"}
    )
    scope = _mapping(
        value["scope"],
        {
            "execution_domain_ref",
            "account_ref",
            "account_refs",
            "strategy_ref",
            "run_id",
            "manual_book_ref",
            "portfolio_ref",
            "group_id",
            "leg_id",
        },
    )
    if type(scope["account_refs"]) is not list:
        raise ValueError
    return (
        value["mount"],
        value["service_id"],
        value["instance_id"],
        value["scope_ref"],
        WireScope(**scope),
        _identity(value["identity"]),
    )


def _decode(raw):
    if type(raw) is dict and "risk" not in raw:
        raise _RiskSelectionError("risk_selection_missing")
    raw = _mapping(
        raw,
        {
            "schema_version",
            "exchange_name",
            "client_config",
            "binding",
            "route",
            "peer",
            "requested_operations",
            "execution_time_policy",
            "instruments_by_ref",
            "risk",
        },
        {"account_settings", "market_settings", "instrument_settings", "strategy_finance_currency"},
    )
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError
    exchange = raw["exchange_name"]
    if type(exchange) is not str or not exchange or exchange != exchange.strip():
        raise ValueError
    client = parse_client_config(raw["client_config"])
    binding = GatewayCommandBinding(
        **_mapping(raw["binding"], {"journal_id", "account_scope", "strategy_scope", "principal"})
    )
    route, peer = _route(raw["route"]), _identity(raw["peer"])
    operations = _operations(raw["requested_operations"])
    policy = GatewayExecutionTimePolicy.from_mapping(raw["execution_time_policy"])
    if policy.route != exchange:
        raise ValueError
    instruments = raw["instruments_by_ref"]
    if type(instruments) is not dict or not instruments:
        raise ValueError
    for ref in instruments:
        if (
            type(ref) is not str
            or not ref
            or ref != ref.strip()
            or not ref.isprintable()
            or len(ref.encode("utf-8")) > 256
        ):
            raise ValueError
    instruments = {ref: MarketInstrumentKey.from_dict(value) for ref, value in instruments.items()}
    account, market, instrument = (
        _account(raw.get("account_settings")),
        _market(raw.get("market_settings")),
        _instrument(raw.get("instrument_settings")),
    )
    _validate_client(client, binding, route, peer, operations, account, market, instrument)
    currency = raw.get("strategy_finance_currency")
    if "strategy_finance_currency" in raw and (
        type(currency) is not str or not 0 < len(currency) <= 256
        or currency != currency.strip() or not currency.isprintable()
    ):
        raise ValueError
    risk = _risk(raw["risk"], client, route, operations)
    return {
        "exchange_name": exchange,
        "client_config": client,
        "binding": binding,
        "route": route,
        "peer": peer,
        "requested_operations": operations,
        "execution_time_policy": policy,
        "instruments_by_ref": instruments,
        "account_settings": account,
        "market_settings": market,
        "instrument_settings": instrument,
        "strategy_finance_currency": currency,
        "risk": risk,
    }


@dataclass(frozen=True, slots=True, init=False)
class GatewayStrategyConfig:
    """Detached public configuration; properties return independently copied values."""

    _json: str = field(repr=False)

    def _values(self):
        return _decode(json.loads(self._json))

    def to_public_dict(self):
        return json.loads(self._json)

    @property
    def exchange_name(self):
        return self._values()["exchange_name"]

    @property
    def execution_time_policy(self):
        return self._values()["execution_time_policy"]

    @property
    def instruments_by_ref(self):
        return MappingProxyType(self._values()["instruments_by_ref"])

    @property
    def strategy_finance_currency(self):
        """Explicit read dimension only; never an authorization or financial fact."""
        return self._values()["strategy_finance_currency"]

    @property
    def client_config(self):
        return self._values()["client_config"]

    @property
    def binding(self):
        return self._values()["binding"]

    @property
    def route(self):
        return self._values()["route"]

    @property
    def peer(self):
        return self._values()["peer"]

    @property
    def requested_operations(self):
        return self._values()["requested_operations"]

    @property
    def account_settings(self):
        return self._values()["account_settings"]

    @property
    def market_settings(self):
        return self._values()["market_settings"]

    @property
    def instrument_settings(self):
        return self._values()["instrument_settings"]

    @property
    def risk_selection(self):
        """The selected Risk client (D64-01), or None for an explicit ``"none"``."""
        return self._values()["risk"]

    @staticmethod
    def _split_security_profiles(values, security_profiles):
        """With Risk selected, the caller supplies both profiles under distinct names."""
        risk = values["risk"]
        if risk is None:
            return security_profiles, None
        gateway_ref = next(iter(values["client_config"].communication["profiles"].values()))[
            "security_profile_ref"
        ]
        risk_ref = risk.security_profile_ref
        if not isinstance(security_profiles, Mapping) or set(security_profiles) != {
            gateway_ref,
            risk_ref,
        }:
            raise ValueError("INVALID_CLIENT_CONFIGURATION")
        return (
            {gateway_ref: security_profiles[gateway_ref]},
            {risk_ref: security_profiles[risk_ref]},
        )

    def create_session(
        self, *, host, security_profiles, cancel_host=None, delegated_cancel_host=None
    ):
        values = self._values()
        gateway_profiles, _ = self._split_security_profiles(values, security_profiles)
        return GatewayStrategyClientSession(
            values["client_config"],
            binding=values["binding"],
            route=values["route"],
            peer=values["peer"],
            security_profiles=gateway_profiles,
            host=host,
            cancel_host=cancel_host,
            delegated_cancel_host=delegated_cancel_host,
            requested_operations=values["requested_operations"],
            account_settings=values["account_settings"],
            market_settings=values["market_settings"],
            instrument_settings=values["instrument_settings"],
        )

    def create_api(
        self,
        *,
        host,
        security_profiles,
        cancel_host=None,
        owns_session=True,
        delegated_cancel_host=None,
    ):
        if type(owns_session) is not bool:
            raise TypeError("INVALID_SESSION_OWNERSHIP")
        if not owns_session:
            raise ValueError("CREATED_SESSION_REQUIRES_OWNERSHIP")
        values = self._values()
        _, risk_profiles = self._split_security_profiles(values, security_profiles)
        session = self.create_session(
            host=host,
            security_profiles=security_profiles,
            cancel_host=cancel_host,
            delegated_cancel_host=delegated_cancel_host,
        )
        risk_client = None
        if values["risk"] is not None:
            from bt_api_py.risk_service_client import RiskServiceClient

            risk = values["risk"]
            try:
                risk_client = RiskServiceClient.compose(
                    risk.client_config,
                    route=risk.route,
                    peer=risk.peer,
                    security_profiles=risk_profiles,
                    requested_operations=risk.requested_operations,
                )
            except BaseException:
                session.close()  # never started: releases nothing remote
                raise
        return GatewayBtApi(
            session,
            exchange_name=self.exchange_name,
            owns_session=owns_session,
            risk_client=risk_client,
        )


def parse_gateway_strategy_config(raw):
    """Parse a complete public tree; secret values are supplied separately.

    ``risk`` is required (D64-01): ``"none"`` or the Risk client. A missing key
    is ``risk_selection_missing`` and any other value ``risk_selection_invalid``;
    every other defect stays ``INVALID_GATEWAY_STRATEGY_CONFIG``.
    """
    try:
        _plain(raw)
        encoded = json.dumps(raw, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        detached = json.loads(encoded)
        _decode(detached)
        config = object.__new__(GatewayStrategyConfig)
        object.__setattr__(config, "_json", encoded)
        return config
    except _RiskSelectionError as error:
        raise GatewayStrategyConfigError(error.code) from None
    except Exception:
        raise GatewayStrategyConfigError("INVALID_GATEWAY_STRATEGY_CONFIG") from None
