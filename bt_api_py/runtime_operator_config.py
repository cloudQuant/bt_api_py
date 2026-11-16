"""Strict explicit deployment input for the programmatic Runtime service.

Validation reads public configuration only. It never reads tokens or CURVE
keys, creates state, connects to services, or grants operator authority.
"""
from __future__ import annotations

import json
import math
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_run_contract import AccessMode, parse_runtime_config
from bt_api_runtime.control_auth import MappingAuthenticator, identity_from_operator_config
from bt_api_runtime.gateway_control import GatewayDelegationProof

from bt_api_py.monitor_service_config import read_bounded
from bt_api_py.runtime_gateway_control import (
    RuntimeGatewayControlRoute,
    TrustedRuntimeControlBinding,
)
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_runtime import ClientCurveKeys

SCHEMA = 'bt_api_py.runtime_operator.v1'
TOKEN_SCHEMA = 'bt_api_py.runtime_subject_tokens.v1'
_LIMIT = 1024 * 1024


class RuntimeOperatorError(ValueError):
    """Finite error; never include deployment inputs or secret values."""


def _map(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError()
    return value


def _text(value, limit=128):
    if (type(value) is not str or not value or value != value.strip() or not value.isprintable()
            or len(value.encode('utf-8')) > limit):
        raise ValueError()
    return value


def _path(value):
    value = _text(value, 4096)
    if not os.path.isabs(value) or os.path.normpath(value) != value:
        raise ValueError()
    return Path(value)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _json(data):
    def invalid(_):
        raise ValueError()
    return json.loads(data.decode('utf-8', 'strict'), object_pairs_hook=_pairs, parse_constant=invalid)


def _identity(raw, role):
    raw = _map(raw, ('principal_ref', 'actor_ref'))
    return WireIdentity(role, _text(raw['principal_ref']), _text(raw['actor_ref']))


def _keys_config(raw):
    raw = _map(raw, ('server_public', 'client_public', 'client_secret'))
    return tuple(_path(raw[key]) for key in ('server_public', 'client_public', 'client_secret'))


def _timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 60:
        raise ValueError()
    return float(value)


def _gateway_client(client, identity, route):
    if (client.process_role != 'application_client'
            or client.registration.get('application_ref') != identity.actor_ref
            or set(client.clients) != {'gateway'}):
        raise ValueError()
    bindings = client.clients['gateway']['settings']['bindings']
    if len(bindings) != 1:
        raise ValueError()
    item = bindings[0]
    if (item['mount'] != 'application.direct_write' or item['required'] is not True
            or item['service_id'] != route.service_id or item['instance_id'] != route.instance_id
            or item['scope_ref'] != route.scope_ref or item.get('account_ref') != route.scope.account_ref
            or item.get('portfolio_ref') is not None):
        raise ValueError()
    profiles = client.communication['profiles']
    if set(profiles) != {item['transport_ref']}:
        raise ValueError()
    profile = profiles[item['transport_ref']]
    if profile['backend'] != 'zmq' or set(profile['endpoints']) != {'rpc_connect'}:
        raise ValueError()


def _monitor_client(client, identity, peer, scope, scope_ref):
    public = client.to_public_dict()
    if (public['process_role'] != 'service' or public['service_id'] != identity.actor_ref
            or set(public['clients']) != {'monitor'}):
        raise ValueError()
    bindings = public['clients']['monitor']['settings']['bindings']
    if len(bindings) != 1:
        raise ValueError()
    item = bindings[0]
    if (item['mount'] != 'upstream.telemetry' or item['required'] is not True
            or item['scope_ref'] != scope_ref or item['service_id'] != peer.actor_ref
            or item.get('account_ref') not in (None, scope.account_ref)):
        raise ValueError()
    profile = public['communication']['profiles'][item['transport_ref']]
    if profile['backend'] != 'zmq' or 'rpc_connect' not in profile['endpoints']:
        raise ValueError()


@dataclass(frozen=True, repr=False)
class RuntimeOperatorConfig:
    runtime: object
    token_file: Path
    unit_strategies: object
    gateway: object
    monitor: object


def load_config(path):
    """Read only the JSON envelope and referenced public Runtime YAML/JSON."""
    try:
        envelope = _json(read_bounded(_path(os.fspath(path)), _LIMIT, protected=False))
        top = _map(envelope, ('schema', 'runtime_config_file', 'subject_token_file', 'gateway', 'monitor'))
        if top['schema'] != SCHEMA:
            raise ValueError()
        runtime_path = _path(top['runtime_config_file'])
        runtime = parse_runtime_config(read_bounded(runtime_path, _LIMIT, protected=False),
                                       base_dir=runtime_path.parent)
        if runtime.api.transport != 'http_json' or runtime.api.identity_mechanism != 'subject_token':
            raise ValueError()
        if not runtime.operators or len({o.name for o in runtime.operators}) != len(runtime.operators):
            raise ValueError()
        gateway_units = {u.name for u in runtime.units if u.access is AccessMode.GATEWAY}
        gateway = None
        unit_strategies = {}
        if top['gateway'] is not None:
            raw = _map(top['gateway'], ('identity', 'expected_peer', 'timeout_seconds', 'routes'))
            identity = _identity(raw['identity'], WireRole.APPLICATION_CLIENT)
            peer = _identity(raw['expected_peer'], WireRole.SERVICE)
            timeout = _timeout(raw['timeout_seconds'])
            routes = raw['routes']
            if type(routes) is not list or not routes or len(routes) > 1024:
                raise ValueError()
            entries = []
            seen = set()
            for item in routes:
                item = _map(item, ('unit', 'strategy_uuid', 'route', 'client_config',
                                   'client_config_fingerprint', 'curve_keys', 'delegation'))
                unit = _text(item['unit'])
                strategy = _text(item['strategy_uuid'])
                if str(uuid.UUID(strategy)) != strategy:
                    raise ValueError()
                if unit in unit_strategies or strategy in seen:
                    raise ValueError()
                unit_strategies[unit] = strategy
                seen.add(strategy)
                spec = _map(item['route'], ('service_id', 'instance_id', 'scope_ref',
                                            'execution_domain_ref', 'account_ref'))
                scope = WireScope(_text(spec['execution_domain_ref']), account_ref=_text(spec['account_ref']),
                                  strategy_ref=strategy)
                route = RuntimeGatewayControlRoute(_text(spec['service_id']), _text(spec['instance_id']),
                                                   _text(spec['scope_ref']), scope)
                client = parse_client_config(item['client_config'])
                _gateway_client(client, identity, route)
                digest = _text(item['client_config_fingerprint'])
                if digest != client.fingerprint() or peer.actor_ref != route.service_id:
                    raise ValueError()
                delegation = _map(item['delegation'], ('delegation_id', 'operator_ref', 'expires_at', 'revoked'))
                proof = GatewayDelegationProof(_text(delegation['delegation_id']),
                    _text(delegation['operator_ref']), delegation['expires_at'], delegation['revoked'])
                if proof.operator_ref not in {o.name for o in runtime.operators}:
                    raise ValueError()
                entries.append((strategy, route, client, digest, _keys_config(item['curve_keys']), proof))
            gateway = (identity, peer, timeout, tuple(entries))
        if set(unit_strategies) != gateway_units or (bool(gateway_units) != (gateway is not None)):
            raise ValueError()
        monitor = None
        if top['monitor'] is not None:
            raw = _map(top['monitor'], ('identity', 'expected_peer', 'execution_domain_ref', 'account_ref',
                                        'scope_ref', 'run_id', 'timeout_seconds', 'client_config', 'curve_keys'))
            monitor = (_identity(raw['identity'], WireRole.SERVICE),
                       _identity(raw['expected_peer'], WireRole.SERVICE),
                       WireScope(_text(raw['execution_domain_ref']), account_ref=_text(raw['account_ref'])),
                       _text(raw['scope_ref']), _text(raw['run_id']), _timeout(raw['timeout_seconds']),
                       parse_service_config(raw['client_config']), _keys_config(raw['curve_keys']))
            _monitor_client(monitor[6], monitor[0], monitor[1], monitor[2], monitor[3])
        if runtime.monitor_events.enabled != (monitor is not None):
            raise ValueError()
        return RuntimeOperatorConfig(runtime, _path(top['subject_token_file']), unit_strategies, gateway, monitor)
    except Exception:
        raise RuntimeOperatorError('INVALID_CONFIGURATION') from None


def _protected(path, limit):
    # The shared reader validates a stable no-symlink path, owner, link count,
    # and absence of group/other permissions. Require exactly 0600 as well.
    try:
        before = os.stat(path, follow_symlinks=False)
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise ValueError()
        data = read_bounded(path, limit, protected=True)
        after = os.stat(path, follow_symlinks=False)
        if (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError()
        return data
    except Exception:
        raise RuntimeOperatorError('PROTECTED_INPUT_UNAVAILABLE') from None


def load_authenticator(config):
    try:
        raw = _map(_json(_protected(config.token_file, 65536)), ('schema', 'tokens'))
        if raw['schema'] != TOKEN_SCHEMA or type(raw['tokens']) is not list or not 1 <= len(raw['tokens']) <= 1024:
            raise ValueError()
        identities = {o.name: identity_from_operator_config(o) for o in config.runtime.operators}
        mapping = {}
        for item in raw['tokens']:
            item = _map(item, ('token', 'operator'))
            token, operator = _text(item['token'], 512), _text(item['operator'])
            if len(token.encode('utf-8')) < 32 or token in mapping or operator not in identities:
                raise ValueError()
            mapping[token] = identities[operator]
        return MappingAuthenticator(mapping)
    except Exception:
        raise RuntimeOperatorError('SUBJECT_TOKENS_INVALID') from None


def _load_keys(paths):
    try:
        keys = tuple(_protected(path, 4096).strip() for path in paths)
        import zmq
        from zmq.utils import z85
        if any(len(key) != 40 or len(z85.decode(key)) != 32 for key in keys):
            raise ValueError()
        if zmq.curve_public(keys[2]) != keys[1]:
            raise ValueError()
        return ClientCurveKeys(*keys)
    except Exception:
        raise RuntimeOperatorError('CURVE_KEYS_INVALID') from None


def build_bridges(config, event_store):
    """Build fresh, unstarted bridges from explicit deployment declarations."""
    from bt_api_py.runtime_gateway_control import RuntimeGatewayControlBridge
    from bt_api_py.runtime_monitor_bridge import RuntimeMonitorBridge
    try:
        gateway = None
        if config.gateway is not None:
            identity, peer, timeout, entries = config.gateway
            routes, clients, proofs, profiles = {}, {}, {}, {}
            for strategy, route, client, digest, paths, proof in entries:
                bindings = client.clients['gateway']['settings']['bindings']
                profile_ref = client.communication['profiles'][bindings[0]['transport_ref']]['security_profile_ref']
                keys = _load_keys(paths)
                if profile_ref in profiles and profiles[profile_ref] != keys:
                    raise ValueError()
                profiles[profile_ref] = keys
                routes[strategy], clients[strategy] = route, client
                proofs[strategy] = TrustedRuntimeControlBinding(route, identity, peer, digest, proof)
            gateway = RuntimeGatewayControlBridge(routes, client_configs=clients, client_identity=identity,
                expected_gateway=peer, security_profiles=profiles, proofs=proofs, timeout_seconds=timeout)
        monitor = None
        if config.monitor is not None:
            identity, peer, scope, scope_ref, run_id, timeout, client, paths = config.monitor
            monitor = RuntimeMonitorBridge(config=client, identity=identity, scope=scope, scope_ref=scope_ref,
                expected_peer=peer, curve_keys=_load_keys(paths), event_store=event_store, run_id=run_id,
                timeout_seconds=timeout)
        return gateway, monitor
    except Exception:
        raise RuntimeOperatorError('BRIDGE_CONFIGURATION_INVALID') from None
