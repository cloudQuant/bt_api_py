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
import os
import stat
from dataclasses import fields
from pathlib import Path

from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.ctp_gateway_operator_profile import CtpGatewayTrustedResources, create_ctp_gateway_profile_factory
from bt_api_py.ctp_gateway_trading import CtpGatewayMarketSettings, CtpGatewayTradingSettings
from bt_api_py.gateway_direct_service import GatewayDirectGrant
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

_RESOURCE_ENV = "BT_API_CTP_GATEWAY_RESOURCES_FILE"
_MAX_BYTES = 262_144


class CtpLocalProfileError(ValueError):
    """Value-free local resource error."""


def _invalid():
    raise CtpLocalProfileError("CTP_LOCAL_RESOURCES_INVALID") from None


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


def _protected_json(path):
    """Read without symlink traversal or accepting mutable/public resource files."""
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


def _strategy_ledger_upgrade(value):
    """Optional ``{"upgrade_from_v10": bool}``: explicit journal upgrade for the
    strategy ledger (positions and close capacity; the operator config sets
    ``strategy_ledger_enabled``). The journal never downgrades."""
    if value is None:
        return False
    if type(value) is dict:
        value = {name: item for name, item in value.items()
                 if name not in ("external_baseline", "account_comparison")}
    value = _mapping(value, ("upgrade_from_v10",))
    if type(value["upgrade_from_v10"]) is not bool:
        _invalid()
    return value["upgrade_from_v10"]


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
    from bt_api_py.gateway_direct_service import CONTROL_OPERATIONS, GatewayControlGrant

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
    lease_drain = None
    if type(value) is dict and "lease_drain" in value:
        # Optional {"strategies": [strategy refs], "automatic_resume": bool}: when
        # the lease of one of these strategies expires, the Gateway applies a
        # DRAIN cause for it (off unless listed here).
        from bt_api_py.gateway_lease_drain import GatewayLeaseDrainSettings

        declared = _mapping(value["lease_drain"], ("strategies", "automatic_resume"))
        if type(declared["strategies"]) is not list or type(declared["automatic_resume"]) is not bool:
            _invalid()
        lease_drain = GatewayLeaseDrainSettings(
            frozenset(_string(item) for item in declared["strategies"]),
            automatic_resume=declared["automatic_resume"])
        if len(lease_drain.strategies) != len(declared["strategies"]):
            _invalid()
        value = {name: item for name, item in value.items() if name != "lease_drain"}
    value = _mapping(value, ("operators", "upgrade_controls_from_v9"))
    if (type(value["upgrade_controls_from_v9"]) is not bool or type(value["operators"]) is not list
            or not value["operators"]):
        _invalid()
    control = {}
    for operator in value["operators"]:
        operator = _mapping(operator, ("identity", "curve_public_key", "resources",
                                       "account_scope_allowed", "strategies"))
        identity = _identity(operator["identity"])
        public = _curve(operator["curve_public_key"])
        if (identity.role is not WireRole.APPLICATION_CLIENT or identity.principal_ref in grants
                or public in principals or type(operator["resources"]) is not list
                or not operator["resources"] or type(operator["account_scope_allowed"]) is not bool
                or type(operator["strategies"]) is not list):
            _invalid()
        entries, operations = [], set()
        for item in operator["resources"]:
            item = _mapping(item, ("instance_id", "port", "scope_ref", "scope", "operations"))
            item_operations = _string_set(item["operations"])
            if item["port"] != "service.direct_write" or not item_operations <= CONTROL_OPERATIONS:
                _invalid()  # an operator never receives trading operations here
            entries.append(ResourceGrant(item["instance_id"], item["port"], item["scope_ref"],
                                         _scope(item["scope"]), item_operations))
            operations |= item_operations
        principals[public] = identity.principal_ref
        grants[identity.principal_ref] = (PrincipalGrant(identity, tuple(entries)),)
        control[identity.principal_ref] = GatewayControlGrant(
            identity.actor_ref, operator["account_scope_allowed"],
            frozenset(_string(item) for item in operator["strategies"]), frozenset(operations))
    return control, value["upgrade_controls_from_v9"], drain, lease_drain


def _strategy_origin_source_factory(instruments):
    """Name the complete instrument of a strategy order from the operator's own list.

    The list is the bundle's ``returns.instruments``: the same trusted
    declaration the strategy ledger resolves against. An order for anything
    else has no origin and is refused. The source is built on the owner thread.
    """
    declared = {(item.venue, item.native_symbol): item for item in instruments}

    def factory(_journal, _guard, _session):
        from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource

        def resolve(_context, order):
            return declared[(order.exchange_id, order.symbol)]

        def recheck(instrument):
            return declared.get((instrument.venue, instrument.native_symbol)) == instrument

        return GatewayStrategyOriginSource(resolve=resolve, recheck=recheck)

    return factory


def _clock_settings(value):
    """Trusted Gateway clock policy: SNTP servers plus periodic refresh."""
    if value is None:
        return None
    from bt_api_py.clock_verification import ClockVerifier
    from bt_api_py.gateway_server_clock_refresh import GatewayServerClockRefreshSettings

    value = _mapping(value, ("servers", "max_error_bound_ms", "max_sample_validity_seconds",
                             "refresh_interval_seconds"))
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
        refresh_interval_seconds=value["refresh_interval_seconds"])
    return (lambda: ClockVerifier(servers=servers)), frozenset(servers), refresh


def _load_resources(config, *, trading):
    try:
        keys = ("schema_version", "registry_path", "settings", "credentials", "authority", "clients",
                "market") + (("returns",) if trading else ())
        raw = _protected_json(os.environ.get(_RESOURCE_ENV))
        # A trading bundle may add a "clock" section (trusted SNTP policy).
        optional = {"clock", "controls", "strategy_ledger"} if trading else set()
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
        control_grants, upgrade_controls, drain_upgrade, lease_drain = None, False, None, None
        if trading and raw.get("controls") is not None:
            control_grants, upgrade_controls, drain_upgrade, lease_drain = _controls(
                raw["controls"], principals, grants)
        authority = TrustedServiceAuthority(**authority_raw, principals=principals, grants=grants)
        market = None
        if raw["market"] is not None:
            market_keys = ("subscriptions", "principal_allowed_indices")
            if type(raw["market"]) is dict and "dispatch_max_latency_seconds" in raw["market"]:
                market_keys += ("dispatch_max_latency_seconds",)
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
            market = CtpGatewayMarketSettings(frozenset(keys), allowed,
                                              dispatch_max_latency_seconds=float(latency))
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
            clock = _clock_settings(raw.get("clock"))
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
                drain_options = dict(
                    upgrade_parent_authority_from_v12=drain_upgrade,
                    upgrade_account_facts_financial_from_v13=drain_upgrade,
                    upgrade_actor_stream_from_v14=drain_upgrade,
                    upgrade_strategy_origin_from_v15=drain_upgrade,
                    upgrade_control_cancel_from_v16=drain_upgrade,
                    strategy_origin_source_factory=_strategy_origin_source_factory(
                        return_settings.instruments))
            # Admission is the route WriteAdmission plus Registry grants; the
            # operator gate is open only in this explicitly installed profile.
            return CtpGatewayTrustedResources(
                authority, _string(raw["registry_path"]), settings, direct,
                request_feed_factory, market_stream_factory, lambda _record: True,
                return_settings=return_settings, market_settings=market,
                clock_verifier_factory=verifier_factory, clock_allowed_servers=allowed_servers,
                clock_refresh_settings=refresh, control_grants=control_grants,
                upgrade_controls_from_v9=upgrade_controls,
                upgrade_strategy_ledger_from_v10=_strategy_ledger_upgrade(
                    raw.get("strategy_ledger")),
                strategy_ledger_external_baseline=_strategy_ledger_external_baseline(
                    raw.get("strategy_ledger")),
                upgrade_account_extensions_from_v11=upgrade_extensions,
                strategy_ledger_comparison_refresh_seconds=comparison_refresh,
                lease_drain_settings=lease_drain,
                **drain_options,
            )
        return CtpGatewayTrustedResources(
            authority, _string(raw["registry_path"]), settings, direct,
            request_feed_factory, market_stream_factory, lambda _record: False, market_settings=market,
        )
    except CtpLocalProfileError:
        raise
    except Exception:
        _invalid()


profile_factory = create_ctp_gateway_profile_factory("ctp-local-readonly", resource_loader=load_local_ctp_resources)
simnow_trading_profile_factory = create_ctp_gateway_profile_factory(
    "ctp-simnow-trading", resource_loader=load_simnow_trading_resources)
