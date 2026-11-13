"""Execution port for authenticated Gateway parent operations.

The Execution host durably claims each mutation before this client can send it.
Timeout, missing records and unverified replies never resolve UNKNOWN.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import threading
import time
from dataclasses import replace

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload, gateway_payload_digest
from bt_api_base.service_protocol import WireIdentity, WireScope

from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.service_config import ServiceConfig
from bt_api_py.service_runtime import BoundServiceTarget, ClientCurveKeys
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

REGISTER = 'direct.parent.register'
QUERY = 'direct.parent.query'
CANCEL = 'direct.parent.cancel'
OPERATION = 'direct.parent.operation'
SOURCE = 'direct.parent.source'
REGISTER_V2 = 'direct.parent.register_v2'
OPERATION_V2 = 'direct.parent.operation_v2'
CANCEL_V2 = 'direct.parent.cancel_v2'
_READINESS_OPERATIONS = (SOURCE, REGISTER_V2, OPERATION_V2, CANCEL_V2)
_FIELDS = frozenset({'account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id',
    'execution_domain_ref', 'instrument', 'quantity', 'quantity_unit', 'side', 'offset',
    'limit_price', 'creation_trading_day', 'allowed_trading_days', 'expires_at_ns',
    'grant_revision', 'target_position_ref', 'plan_digest', 'plan_version', 'time_in_force'})
_TARGET_FIELDS = frozenset({'account_scope', 'strategy_scope', 'parent_ref', 'child_id', 'request_key',
    'reservation_id', 'payload_digest', 'target_principal', 'target_version', 'native_identity',
    'strong_identity_keys', 'phase', 'cancel_operation_id'})


def _ref(value):
    try:
        return (type(value) is str and bool(value) and value == value.strip() and value.isprintable()
                and len(value.encode('utf-8', 'strict')) <= 256)
    except UnicodeError:
        return False


def _targets_valid(targets, spec, parent_operation):
    if type(targets) is not list:
        return False
    seen = {key: set() for key in ('child_id', 'request_key', 'reservation_id', 'cancel_operation_id')}
    for target in targets:
        if type(target) is not dict or set(target) != _TARGET_FIELDS:
            return False
        for key in ('child_id', 'request_key', 'reservation_id', 'target_principal', 'cancel_operation_id'):
            if not _ref(target[key]):
                return False
        if any(target[key] in values for key, values in seen.items()):
            return False
        for key, values in seen.items():
            values.add(target[key])
        if (any(target[key] != spec[key] for key in ('account_scope', 'strategy_scope', 'parent_ref'))
                or target['cancel_operation_id'] != 'parent-cancel-' + gateway_payload_digest([
                    spec['account_scope'], spec['strategy_scope'], spec['parent_ref'], target['child_id'], parent_operation])
                or type(target['payload_digest']) is not str or len(target['payload_digest']) != 64
                or any(c not in '0123456789abcdef' for c in target['payload_digest'])
                or type(target['target_version']) is not int):
            return False
        phase, native, keys = target['phase'], target['native_identity'], target['strong_identity_keys']
        if phase == 'RELEASE_PENDING':
            if target['target_version'] != 1 or native is not None or keys is not None:
                return False
        elif phase in ('WAIT_DISPATCH', 'CANCEL_PENDING'):
            if (target['target_version'] != (2 if phase == 'WAIT_DISPATCH' else 3)
                    or type(native) is not dict or not native or type(keys) is not list or not keys
                    or any(not _ref(key) for key in keys) or len(set(keys)) != len(keys)):
                return False
        else:
            return False
    return [target['child_id'] for target in targets] == sorted(seen['child_id'])


class _ParentReceipt(dict):
    """JSON-persistable result whose live provenance is bound to one client."""
    def __init__(self, value, token, registration):
        super().__init__(value)
        self._token = token
        self._registration = registration
        self._canonical = canonical_gateway_payload(value)


class _ParentSourceReceipt:
    """A live client observation; only the issuing client may verify it."""
    def __init__(self, value, token, registration, spec, seal):
        self._value = value
        self._token = token
        self._registration = registration
        self._canonical = canonical_gateway_payload(value)
        self._spec = canonical_gateway_payload(spec)
        self._seal = seal


class _ParentBoundReceipt(dict):
    """A versioned operation result with live, client-local provenance."""
    def __init__(self, value, source, anchor, token, registration, spec, seal):
        super().__init__(value)
        self._source = source
        self._token = token
        self._registration = registration
        self._canonical = canonical_gateway_payload(value)
        self._source_canonical = canonical_gateway_payload(source)
        self._anchor = canonical_gateway_payload(anchor)
        self._spec = canonical_gateway_payload(spec)
        self._seal = seal


def _trusted_parent_query_target(config, route, expected_gateway, security_profiles):
    """Freeze deployment expectations, never deriving peer/key from a response.

    The placeholder registration is replaced only with the current matching
    status registration immediately before the owner-thread bound call.
    Missing or ambiguous composition data disables the observation.
    """
    try:
        if (type(config) is not ServiceConfig or type(route) is not GatewayDelegateRoute
                or type(expected_gateway) is not WireIdentity or type(security_profiles) is not dict):
            return None
        public = config.to_public_dict()
        matches = [binding for client in public['clients'].values()
                   for binding in client['settings']['bindings']
                   if binding.get('required') is True and binding.get('mount') == route.mount
                   and binding.get('service_id') == route.service_id
                   and binding.get('instance_id') == route.instance_id
                   and binding.get('scope_ref') == route.scope_ref
                   and binding.get('account_ref') == route.scope.account_ref]
        if len(matches) != 1:
            return None
        transport_ref = matches[0]['transport_ref']
        profile = public['communication']['profiles'][transport_ref]
        security_ref = profile['security_profile_ref']
        keys = security_profiles[security_ref]
        if (profile.get('backend') != 'zmq' or type(keys) is not ClientCurveKeys
                or type(keys.server_key) is not bytes or not keys.server_key):
            return None
        return BoundServiceTarget(
            mount=route.mount, port='service.direct_write', service_id=route.service_id,
            instance_id=route.instance_id, scope_ref=route.scope_ref,
            scope=replace(route.scope), identity=replace(route.identity), run_id=route.scope.run_id,
            transport_ref=transport_ref, security_profile_ref=security_ref,
            endpoint=profile['endpoints']['rpc_connect'], expected_peer=replace(expected_gateway),
            server_key_fingerprint=hashlib.sha256(keys.server_key).hexdigest(),
            registration_id='pending-registration', service_epoch='pending-epoch')
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


class GatewayParentServiceClient:
    def __init__(self, runtime, *, route, host, timeout_seconds=5.0, _query_target=None):
        if (type(runtime) is not ThreadedServiceClientRuntime or type(route) is not GatewayDelegateRoute
                or not callable(getattr(host, 'prepare_parent_dispatch', None))
                or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 3600):
            raise ValueError('INVALID_PARENT_CLIENT')
        if _query_target is not None and type(_query_target) is not BoundServiceTarget:
            raise ValueError('INVALID_PARENT_QUERY_TARGET')
        self._runtime, self._route, self._host = runtime, route, host
        self._query_target = _query_target
        self._timeout = timeout_seconds
        self._pid, self._thread, self._token = os.getpid(), threading.current_thread(), object()
        self._bound_receipt_key = None

    def _owned(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError('PARENT_CLIENT_WRONG_OWNER')

    def _seal_bound_receipt(self, kind, value, registration, spec, anchor=None, source=None):
        """Bind live receipt fields, not a durable or remote authorization proof."""
        if self._bound_receipt_key is None:
            self._bound_receipt_key = os.urandom(32)
        def wire_binding(item):
            if type(item) is tuple:
                return [wire_binding(part) for part in item]
            if type(item) is str:
                return item
            raise ValueError('INVALID_PARENT_REGISTRATION')
        payload = canonical_gateway_payload({'kind': kind, 'value': value, 'registration': wire_binding(registration),
                                             'spec': spec, 'anchor': anchor, 'source': source})
        return hmac.digest(self._bound_receipt_key, payload.encode('utf-8'), 'sha256')

    def _spec(self, spec):
        if type(spec) is not dict or not set(spec) >= _FIELDS:
            raise ValueError('INVALID_PARENT_SPEC')
        value = {key: spec[key] for key in _FIELDS}
        value = json.loads(canonical_gateway_payload(value))
        scope = self._route.scope
        if (value['account_scope'] != scope.account_ref or value['strategy_scope'] != scope.strategy_ref
                or value['execution_domain_ref'] != scope.execution_domain_ref):
            raise ValueError('PARENT_SCOPE_MISMATCH')
        return value

    def _registration(self, operation):
        snap, route = self._runtime.status(), self._route
        matches = [b for b in snap.bindings if b.active is True and b.required is True
                   and b.mount == route.mount and b.service_id == route.service_id
                   and b.instance_id == route.instance_id and b.scope_ref == route.scope_ref
                   and type(b.scope) is WireScope and b.scope == route.scope
                   and type(b.operations) is frozenset and operation in b.operations]
        if (snap.state != 'running' or not snap.thread_alive or not snap.healthy
                or snap.identity != route.identity or snap.run_id != route.scope.run_id or len(matches) != 1
                or type(matches[0].registration_id) is not str or not matches[0].registration_id
                or type(matches[0].service_epoch) is not str or not matches[0].service_epoch):
            raise ValueError('PARENT_ROUTE_UNAVAILABLE')
        return matches[0].registration_id, matches[0].service_epoch

    def _trusted_target(self):
        """Project only composition-owned, restart-stable target expectations."""
        target, route = self._query_target, self._route
        if type(target) is not BoundServiceTarget or type(route) is not GatewayDelegateRoute:
            raise ValueError('PARENT_ROUTE_UNAVAILABLE')
        GatewayDelegateRoute.__post_init__(route)
        BoundServiceTarget.__post_init__(target)
        if (target.mount != route.mount or target.service_id != route.service_id
                or target.instance_id != route.instance_id or target.scope_ref != route.scope_ref
                or target.scope != route.scope or target.identity != route.identity
                or target.run_id != route.scope.run_id):
            raise ValueError('PARENT_ROUTE_UNAVAILABLE')
        return {
            'mount': target.mount, 'port': target.port, 'service_id': target.service_id,
            'instance_id': target.instance_id, 'scope_ref': target.scope_ref,
            'account_scope': target.scope.account_ref, 'strategy_scope': target.scope.strategy_ref,
            'execution_domain_ref': target.scope.execution_domain_ref,
            'caller_role': target.identity.role.value, 'caller_principal': target.identity.principal_ref,
            'caller_actor': target.identity.actor_ref, 'peer_role': target.expected_peer.role.value,
            'peer_principal': target.expected_peer.principal_ref, 'peer_actor': target.expected_peer.actor_ref,
            'endpoint': target.endpoint, 'transport_ref': target.transport_ref,
            'security_profile_ref': target.security_profile_ref,
            'server_key_fingerprint': target.server_key_fingerprint,
        }

    def _bound_target(self, operation):
        """Use status only for the current registration, never as trust input."""
        trusted = self._trusted_target()
        registration = self._registration(operation)
        expected = self._query_target
        target = replace(expected, scope=replace(expected.scope), identity=replace(expected.identity),
                         expected_peer=replace(expected.expected_peer),
                         registration_id=registration[0], service_epoch=registration[1])
        if self._trusted_target() != trusted:
            raise ValueError('PARENT_ROUTE_UNAVAILABLE')
        return target, registration, trusted

    def _anchor(self, anchor, spec):
        """Check a durable anchor against the current independent composition."""
        if type(anchor) is not dict or set(anchor) != {
                'account_scope', 'journal_id', 'minimum_revision', 'gateway_generation', 'trusted_target'}:
            raise ValueError('INVALID_PARENT_ORIGIN')
        value = json.loads(canonical_gateway_payload(anchor))
        if (value['account_scope'] != spec['account_scope'] or not _ref(value['journal_id'])
                or type(value['minimum_revision']) is not int or value['minimum_revision'] < 0
                or not _ref(value['gateway_generation']) or type(value['trusted_target']) is not dict
                or value['trusted_target'] != self._trusted_target()):
            raise ValueError('INVALID_PARENT_ORIGIN')
        return value

    @staticmethod
    def _source_matches(source, anchor):
        return (type(source) is dict and set(source) == {
                    'account_scope', 'journal_id', 'gateway_generation', 'journal_revision'}
                and source['account_scope'] == anchor['account_scope']
                and source['journal_id'] == anchor['journal_id']
                and _ref(source['gateway_generation'])
                and source['gateway_generation'] == anchor['gateway_generation']
                and type(source['journal_revision']) is int
                and source['journal_revision'] >= anchor['minimum_revision'])

    @staticmethod
    def _valid_source_observation(raw, scope):
        if (type(raw) is not dict or set(raw) != {'status', 'schema_version', 'operation', 'result'}
                or raw['status'] != 'OBSERVED' or type(raw['schema_version']) is not int
                or raw['schema_version'] != 1 or raw['operation'] != SOURCE):
            return False
        source = raw['result']
        return (type(source) is dict and set(source) == {'schema_version', 'account_scope', 'strategy_scope',
                    'execution_domain_ref', 'journal_id', 'gateway_generation', 'journal_revision'}
                and type(source['schema_version']) is int and source['schema_version'] == 1
                and all(source[key] == scope[key] for key in ('account_scope', 'strategy_scope', 'execution_domain_ref'))
                and _ref(source['journal_id']) and _ref(source['gateway_generation'])
                and type(source['journal_revision']) is int and 0 <= source['journal_revision'] < 2**63)

    @classmethod
    def _valid_source_readiness(cls, raw, scope):
        if (type(raw) is not dict or set(raw) != {
                'status', 'schema_version', 'operation', 'result', 'checked_operations'}
                or raw['status'] != 'OBSERVED' or type(raw['schema_version']) is not int
                or raw['schema_version'] != 2 or raw['operation'] != SOURCE
                or type(raw['checked_operations']) is not list
                or raw['checked_operations'] != list(_READINESS_OPERATIONS)):
            return False
        return cls._valid_source_observation({
            'status': 'OBSERVED', 'schema_version': 1, 'operation': SOURCE, 'result': raw['result'],
        }, scope)

    def probe_scope_source(self, *, timeout_seconds):
        """Observe current source availability, without granting durable authority.

        Unlike operation receipts this readiness probe is safe to call from a
        Session startup thread. It returns no source identity or reusable proof.
        """
        try:
            if (os.getpid() != self._pid or type(timeout_seconds) not in (int, float)
                    or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX
                    or type(self._runtime) is not ThreadedServiceClientRuntime):
                return False
            deadline = time.monotonic() + timeout_seconds
            runtime = self._runtime
            target, registration, trusted = self._bound_target(SOURCE)
            if any(self._registration(operation) != registration for operation in _READINESS_OPERATIONS):
                return False
            args = {key: trusted[key] for key in ('account_scope', 'strategy_scope', 'execution_domain_ref')}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            raw = _detach_wire(runtime.call_bound(target, SOURCE, dict(args, readiness_version=2),
                                                  timeout_seconds=remaining))
            return (time.monotonic() < deadline and self._runtime is runtime
                    and all(self._registration(operation) == registration for operation in _READINESS_OPERATIONS)
                    and self._trusted_target() == trusted and self._valid_source_readiness(raw, args))
        except Exception:
            return False

    def _bound_write_host(self, spec):
        """Only the actual session authority can consume a durable claim."""
        from bt_api_execution.parent_authority import ParentAuthority
        host = self._host
        if (type(host) is not ParentAuthority or host.gateway is not self
                or host._session_parent_route != (spec['account_scope'], spec['strategy_scope'],
                                                    spec['execution_domain_ref'])
                or host._owner_pid != self._pid or host._owner_thread is not self._thread):
            raise ValueError('PARENT_HOST_UNAVAILABLE')
        return host, ParentAuthority

    def capture_parent_source(self, spec):
        """Observe the current authorized journal before a local write claim."""
        self._owned()
        spec = self._spec(spec)
        try:
            target, registration, trusted = self._bound_target(SOURCE)
            args = {key: spec[key] for key in ('account_scope', 'strategy_scope', 'execution_domain_ref')}
            raw = _detach_wire(self._runtime.call_bound(target, SOURCE, dict(args), timeout_seconds=self._timeout))
            if (self._registration(SOURCE) != registration or self._trusted_target() != trusted
                    or not self._valid_source_observation(raw, args)):
                return None
            source = raw['result']
            anchor = {'account_scope': source['account_scope'], 'journal_id': source['journal_id'],
                      'minimum_revision': source['journal_revision'], 'gateway_generation': source['gateway_generation'],
                      'trusted_target': trusted}
            seal = self._seal_bound_receipt('source', anchor, registration, spec)
            return _ParentSourceReceipt(anchor, self._token, registration, spec, seal)
        except Exception:
            return None

    def verify_parent_source(self, receipt, spec):
        """Return a detached durable anchor only for this client's live observation."""
        self._owned()
        try:
            spec = self._spec(spec)
            if (type(receipt) is not _ParentSourceReceipt or receipt._token is not self._token
                    or receipt._spec != canonical_gateway_payload(spec)
                    or receipt._canonical != canonical_gateway_payload(receipt._value)
                    or self._registration(SOURCE) != receipt._registration
                    or type(receipt._seal) is not bytes
                    or not hmac.compare_digest(receipt._seal, self._seal_bound_receipt(
                        'source', receipt._value, receipt._registration, spec))):
                return None
            return self._anchor(receipt._value, spec)
        except Exception:
            return None

    def _bound_operation(self, operation, arguments, spec, operation_id, kind, anchor):
        try:
            anchor = self._anchor(anchor, spec)
            target, registration, trusted = self._bound_target(operation)
            wire_source = {key: anchor[key] for key in (
                'account_scope', 'journal_id', 'gateway_generation', 'minimum_revision')}
            frozen_spec = canonical_gateway_payload(spec)
            wire_arguments = json.loads(canonical_gateway_payload(dict(arguments, expected_source=wire_source)))
            raw = _detach_wire(self._runtime.call_bound(target, operation, wire_arguments, timeout_seconds=self._timeout))
            if (self._registration(operation) != registration or self._trusted_target() != trusted
                    or canonical_gateway_payload(spec) != frozen_spec
                    or type(raw) is not dict or set(raw) != {'status', 'schema_version', 'operation', 'result', 'source'}
                    or raw['status'] != 'OBSERVED' or type(raw['schema_version']) is not int
                    or raw['schema_version'] != 2 or raw['operation'] != operation
                    or not self._source_matches(raw['source'], anchor)):
                return None
            value = raw['result'] if operation == OPERATION_V2 else {
                'operation_id': operation_id, 'kind': kind, 'result': raw['result']}
            if not self._valid(value, spec, operation_id):
                return None
            binding = (operation, registration)
            seal = self._seal_bound_receipt('operation', value, binding, spec, anchor, raw['source'])
            return _ParentBoundReceipt(value, raw['source'], anchor, self._token, binding, spec, seal)
        except Exception:
            return None

    def register_parent_bound(self, spec, operation_id, anchor):
        self._owned()
        spec = self._spec(spec)
        if operation_id != spec['registration_operation_id']:
            raise ValueError('PARENT_OPERATION_MISMATCH')
        try:
            saved = self._anchor(anchor, spec)
            frozen, frozen_spec = canonical_gateway_payload(saved), canonical_gateway_payload(spec)
            host, authority_type = self._bound_write_host(spec)
            if (authority_type.prepare_parent_dispatch(host, spec, operation_id, 'REGISTER', None, saved) is not True
                    or self._bound_write_host(spec)[0] is not host
                    or canonical_gateway_payload(saved) != frozen
                    or canonical_gateway_payload(spec) != frozen_spec):
                return None
            return self._bound_operation(REGISTER_V2, {'spec': spec}, spec, operation_id, 'REGISTER', saved)
        except Exception:
            return None

    def query_operation_bound(self, spec, operation_id, anchor):
        self._owned()
        spec = self._spec(spec)
        if not _ref(operation_id):
            raise ValueError('INVALID_PARENT_OPERATION')
        args = {key: spec[key] for key in ('account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id')}
        args['operation_id'] = operation_id
        return self._bound_operation(OPERATION_V2, args, spec, operation_id, None, anchor)

    def cancel_parent_bound(self, spec, operation_id, expected_version, anchor):
        self._owned()
        spec = self._spec(spec)
        if (not _ref(operation_id) or operation_id == spec['registration_operation_id']
                or type(expected_version) is not int or expected_version < 1):
            raise ValueError('INVALID_PARENT_OPERATION')
        try:
            saved = self._anchor(anchor, spec)
            frozen, frozen_spec = canonical_gateway_payload(saved), canonical_gateway_payload(spec)
            host, authority_type = self._bound_write_host(spec)
            if (authority_type.prepare_parent_dispatch(host, spec, operation_id, 'CANCEL', expected_version, saved) is not True
                    or self._bound_write_host(spec)[0] is not host
                    or canonical_gateway_payload(saved) != frozen
                    or canonical_gateway_payload(spec) != frozen_spec):
                return None
            args = {key: spec[key] for key in ('account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id')}
            args.update(cancel_operation_id=operation_id, expected_version=expected_version)
            return self._bound_operation(CANCEL_V2, args, spec, operation_id, 'CANCEL', saved)
        except Exception:
            return None

    def verify_parent_bound(self, receipt, spec, operation_id, anchor):
        self._owned()
        try:
            spec = self._spec(spec)
            saved = self._anchor(anchor, spec)
            if (type(receipt) is not _ParentBoundReceipt or receipt._token is not self._token
                    or receipt._spec != canonical_gateway_payload(spec)
                    or receipt._canonical != canonical_gateway_payload(dict(receipt))
                    or receipt._source_canonical != canonical_gateway_payload(receipt._source)
                    or receipt._anchor != canonical_gateway_payload(saved)
                    or type(receipt._registration) is not tuple or len(receipt._registration) != 2
                    or receipt._registration[0] not in {REGISTER_V2, OPERATION_V2, CANCEL_V2}
                    or self._registration(receipt._registration[0]) != receipt._registration[1]
                    or type(receipt._seal) is not bytes
                    or not hmac.compare_digest(receipt._seal, self._seal_bound_receipt(
                        'operation', dict(receipt), receipt._registration, spec, saved, receipt._source))
                    or not self._source_matches(receipt._source, saved)
                    or not self._valid(dict(receipt), spec, operation_id)):
                return None
            return 'ACCEPTED'
        except Exception:
            return None

    def _rpc(self, operation, arguments, spec, operation_id, kind):
        try:
            registration = self._registration(operation)
            raw = _detach_wire(self._runtime.call(self._route.mount, operation, arguments,
                account_ref=spec['account_scope'], scope_ref=self._route.scope_ref,
                timeout_seconds=self._timeout))
            if (self._registration(operation) != registration or type(raw) is not dict
                    or set(raw) != {'status', 'schema_version', 'operation', 'result'}
                    or raw['status'] != 'OBSERVED' or type(raw['schema_version']) is not int
                    or raw['schema_version'] != 1 or raw['operation'] != operation):
                return None
            value = raw['result'] if operation == OPERATION else {
                'operation_id': operation_id, 'kind': kind, 'result': raw['result']}
            if not self._valid(value, spec, operation_id):
                return None
            return _ParentReceipt(value, self._token, (operation, registration))
        except Exception:
            return None

    def _valid(self, value, spec, operation_id):
        if type(value) not in (dict, _ParentReceipt) or set(value) != {'operation_id', 'kind', 'result'}:
            return False
        if value['operation_id'] != operation_id or value['kind'] not in ('REGISTER', 'CANCEL'):
            return False
        result = value['result']
        fields = {'spec', 'spec_digest', 'execution_principal', 'execution_actor_ref', 'state', 'version'}
        if value['kind'] == 'CANCEL':
            fields.add('targets')
        if type(result) is not dict or set(result) != fields:
            return False
        identity = self._route.identity
        expected = ('ACTIVE', 1) if value['kind'] == 'REGISTER' else ('CANCEL_REQUESTED', 2)
        return (result['spec'] == spec and result['spec_digest'] == gateway_payload_digest(spec)
                and result['execution_principal'] == identity.principal_ref
                and result['execution_actor_ref'] == identity.actor_ref
                and (result['state'], result['version']) == expected
                and type(result['version']) is int
                and (value['kind'] != 'CANCEL' or _targets_valid(result['targets'], spec, operation_id))
                and ((value['kind'] == 'REGISTER' and operation_id == spec['registration_operation_id'])
                     or (value['kind'] == 'CANCEL' and operation_id != spec['registration_operation_id'])))

    def register_parent(self, spec, operation_id):
        self._owned()
        spec = self._spec(spec)
        if operation_id != spec['registration_operation_id']:
            raise ValueError('PARENT_OPERATION_MISMATCH')
        try:
            if self._host.prepare_parent_dispatch(spec, operation_id, 'REGISTER', None) is not True:
                return None
        except Exception:
            return None
        return self._rpc(REGISTER, {'spec': spec}, spec, operation_id, 'REGISTER')

    def query_operation(self, spec, operation_id):
        self._owned()
        spec = self._spec(spec)
        if type(operation_id) is not str or not operation_id:
            raise ValueError('INVALID_PARENT_OPERATION')
        args = {key: spec[key] for key in ('account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id')}
        args['operation_id'] = operation_id
        return self._rpc(OPERATION, args, spec, operation_id, None)

    def query_parent(self, spec):
        """Return an authenticated observation; it is not an operation receipt."""
        self._owned()
        expected = self._query_target
        route = self._route
        if (type(expected) is not BoundServiceTarget or expected.mount != route.mount
                or expected.service_id != route.service_id or expected.instance_id != route.instance_id
                or expected.scope_ref != route.scope_ref or expected.scope != route.scope
                or expected.identity != route.identity or expected.run_id != route.scope.run_id):
            return None
        spec = self._spec(spec)
        args = {key: spec[key] for key in ('account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id')}
        try:
            registration = self._registration(QUERY)
            target = replace(expected, scope=replace(expected.scope), identity=replace(expected.identity),
                             expected_peer=replace(expected.expected_peer),
                             registration_id=registration[0], service_epoch=registration[1])
            raw = _detach_wire(self._runtime.call_bound(target, QUERY, args, timeout_seconds=self._timeout))
            if (self._registration(QUERY) != registration or type(raw) is not dict
                    or set(raw) != {'status', 'schema_version', 'operation', 'result'}
                    or raw['status'] != 'OBSERVED' or type(raw['schema_version']) is not int
                    or raw['schema_version'] != 1 or raw['operation'] != QUERY):
                return None
            result = raw['result']
            if (type(result) is not dict or set(result) != {'spec', 'spec_digest', 'execution_principal',
                    'execution_actor_ref', 'state', 'version'} or result['spec'] != spec
                    or result['spec_digest'] != gateway_payload_digest(spec)
                    or result['execution_principal'] != self._route.identity.principal_ref
                    or result['execution_actor_ref'] != self._route.identity.actor_ref
                    or (result['state'], result['version']) not in {('ACTIVE', 1), ('EXPIRED', 1), ('CANCEL_REQUESTED', 2)}
                    or type(result['version']) is not int):
                return None
            return _ParentReceipt(result, self._token, (QUERY, registration))
        except Exception:
            return None

    def cancel_parent(self, spec, operation_id, expected_version):
        self._owned()
        spec = self._spec(spec)
        if (type(operation_id) is not str or not operation_id or operation_id == spec['registration_operation_id']
                or type(expected_version) is not int or expected_version < 1):
            raise ValueError('INVALID_PARENT_OPERATION')
        try:
            if self._host.prepare_parent_dispatch(spec, operation_id, 'CANCEL', expected_version) is not True:
                return None
        except Exception:
            return None
        args = {key: spec[key] for key in ('account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id')}
        args.update(cancel_operation_id=operation_id, expected_version=expected_version)
        return self._rpc(CANCEL, args, spec, operation_id, 'CANCEL')

    def verify(self, result, spec, operation_id):
        self._owned()
        try:
            spec = self._spec(spec)
            if (type(result) is not _ParentReceipt or result._token is not self._token
                    or result._canonical != canonical_gateway_payload(dict(result))
                    or self._registration(result._registration[0]) != result._registration[1]
                    or not self._valid(result, spec, operation_id)):
                return None
            return 'ACCEPTED'
        except Exception:
            return None
