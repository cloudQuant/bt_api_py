"""Protected local CTP operator resources, with native writes disabled.

The environment variable contains a *path*, never credentials. Only an
operator-owned mode-0600 JSON file in an operator-owned private directory is
accepted. Native writes and settlement confirmation remain disabled. The
service still requires the dispatch endpoint and may persist logical commands.
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


def load_local_ctp_resources(config):
    """Resolve protected local authority and CTP resources on the owner thread."""
    try:
        raw = _mapping(_protected_json(os.environ.get(_RESOURCE_ENV)),
                       ("schema_version", "registry_path", "settings", "credentials", "authority", "clients", "market"))
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            _invalid()
        if type(raw["settings"]) is not dict:
            _invalid()
        settings_raw = dict(raw["settings"])
        if set(settings_raw) - {item.name for item in fields(CtpGatewayTradingSettings)}:
            _invalid()
        if settings_raw.get("write_enabled", False) is not False:
            _invalid()
        settings_raw["write_enabled"] = False
        settings_raw["account_key"] = AccountKey.from_dict(settings_raw["account_key"])
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
        authority = TrustedServiceAuthority(**authority_raw, principals=principals, grants=grants)
        market = None
        if raw["market"] is not None:
            value = _mapping(raw["market"], ("subscriptions", "principal_allowed_indices"))
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
            market = CtpGatewayMarketSettings(frozenset(keys), allowed)
        options = dict(credentials, ctp_env_profile="config_front_pair", ctp_environment="simnow",
                       trusted_flow_root=str(settings.trusted_flow_root), flow_account_scope=settings.account_scope)
        def request_feed_factory():
            from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
            return CtpRequestDataFuture(None, auto_settlement_confirm=False, **options)
        def market_stream_factory(_key, queue):
            from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream
            return CtpMarketStream(queue, **options)
        return CtpGatewayTrustedResources(
            authority, _string(raw["registry_path"]), settings, direct,
            request_feed_factory, market_stream_factory, lambda _record: False, market_settings=market,
        )
    except CtpLocalProfileError:
        raise
    except Exception:
        _invalid()


profile_factory = create_ctp_gateway_profile_factory("ctp-local-readonly", resource_loader=load_local_ctp_resources)
