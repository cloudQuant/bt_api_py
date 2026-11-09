"""Execution port for authenticated Gateway parent operations.

The Execution host durably claims each mutation before this client can send it.
Timeout, missing records and unverified replies never resolve UNKNOWN.
"""
from __future__ import annotations

import json
import os
import threading

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload, gateway_payload_digest
from bt_api_base.service_protocol import WireScope
from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime


REGISTER = 'direct.parent.register'
QUERY = 'direct.parent.query'
CANCEL = 'direct.parent.cancel'
OPERATION = 'direct.parent.operation'
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


class GatewayParentServiceClient:
    def __init__(self, runtime, *, route, host, timeout_seconds=5.0):
        if (type(runtime) is not ThreadedServiceClientRuntime or type(route) is not GatewayDelegateRoute
                or not callable(getattr(host, 'prepare_parent_dispatch', None))
                or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 3600):
            raise ValueError('INVALID_PARENT_CLIENT')
        self._runtime, self._route, self._host = runtime, route, host
        self._timeout = timeout_seconds
        self._pid, self._thread, self._token = os.getpid(), threading.current_thread(), object()

    def _owned(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError('PARENT_CLIENT_WRONG_OWNER')

    def _spec(self, spec):
        if type(spec) is not dict or not _FIELDS <= set(spec):
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
        spec = self._spec(spec)
        args = {key: spec[key] for key in ('account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id')}
        try:
            registration = self._registration(QUERY)
            raw = _detach_wire(self._runtime.call(self._route.mount, QUERY, args,
                account_ref=spec['account_scope'], scope_ref=self._route.scope_ref, timeout_seconds=self._timeout))
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
