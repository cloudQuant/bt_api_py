"""Explicit one-way Runtime observation delivery to durable Monitor ingress.

The relay owner calls append; this object does not drain queues or control a
Runtime. A failed or ambiguous call is never retried here.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from types import SimpleNamespace
from datetime import datetime, timezone

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.service_config import ServiceConfig
from bt_api_py.service_monitor import _validate_receipt
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import BoundCallResult, BoundCallRoute, ThreadedServiceClientRuntime


class RuntimeMonitorBridgeError(RuntimeError):
    """Finite error without transport or event payload details."""


# Deliberately exclude arbitrary text, unit names, non-command IDs, timestamps and reasons.
_INTEGER_FIELDS = frozenset({'generation', 'attempts', 'attempts_in_window', 'max_attempts', 'steps', 'exit_code'})
_NUMBER_FIELDS = frozenset({'heartbeat_age_s', 'backoff_s', 'remaining_s'})
_BOOL_FIELDS = frozenset({'clean', 'adopted', 'delivered', 'accepted', 'force', 'kill_confirmed'})


class RuntimeMonitorBridge:
    """Own one authenticated Monitor session and implement TelemetryPort.

    The EventStore is borrowed. Its epoch and producer identity anchor IDs;
    neither the configured account scope nor observations assert account truth.
    Call close again if its bounded cleanup reports an error.
    """

    def __init__(self, *, config, identity, scope, scope_ref, expected_peer, curve_keys,
                 event_store, run_id, timeout_seconds=2.0):
        from bt_api_runtime.events import EventStore
        import zmq
        from zmq.utils import z85

        try:
            if (type(config) is not ServiceConfig or type(identity) is not WireIdentity
                    or identity.role is not WireRole.SERVICE or identity.delegate_ref is not None
                    or type(expected_peer) is not WireIdentity or expected_peer.role is not WireRole.SERVICE
                    or expected_peer.delegate_ref is not None or type(scope) is not WireScope
                    or not scope.account_ref or scope.account_refs or scope.strategy_ref is not None
                    or scope.manual_book_ref is not None or scope.portfolio_ref is not None
                    or scope.group_id is not None or scope.leg_id is not None
                    or type(run_id) is not str or not run_id or scope.run_id is not None
                    or type(scope_ref) is not str or not scope_ref
                    or type(curve_keys) is not ClientCurveKeys
                    or any(type(key) is not bytes or len(key) != 40 for key in
                           (curve_keys.server_key, curve_keys.public_key, curve_keys.secret_key))
                    or type(event_store) is not EventStore
                    or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                    or not 0 < timeout_seconds <= 60):
                raise ValueError
            z85.decode(curve_keys.server_key)
            if zmq.curve_public(curve_keys.secret_key) != curve_keys.public_key:
                raise ValueError
            stats = event_store.stats()
            epoch = stats['epoch']
            if stats['available'] is not True or type(epoch) is not str or not epoch:
                raise ValueError
            public = config.to_public_dict()
            if public['process_role'] != 'service' or set(public['clients']) != {'monitor'}:
                raise ValueError
            bindings = public['clients']['monitor']['settings']['bindings']
            if type(bindings) is not list or len(bindings) != 1:
                raise ValueError
            binding = bindings[0]
            if (binding['mount'] != 'upstream.telemetry' or binding['scope_ref'] != scope_ref
                    or binding.get('required') is not True
                    or binding.get('account_ref') not in (None, scope.account_ref)
                    or expected_peer.actor_ref != binding['service_id']):
                raise ValueError
            transport_ref = binding['transport_ref']
            profile = public['communication']['profiles'][transport_ref]
            if profile['backend'] != 'zmq':
                raise ValueError
            self._target = SimpleNamespace(
                mount='upstream.telemetry',
                service_id=binding['service_id'], instance_id=binding['instance_id'],
                scope_ref=scope_ref, scope=scope, identity=identity, run_id=run_id,
                transport_ref=transport_ref, security_profile_ref=profile['security_profile_ref'],
                endpoint=profile['endpoints']['rpc_connect'], expected_peer=expected_peer,
                server_key_fingerprint=hashlib.sha256(curve_keys.server_key).hexdigest(),
            )
            self._route = BoundCallRoute(identity, scope, scope_ref, binding['service_id'],
                                         binding['instance_id'], expected_peer)
        except Exception:
            raise RuntimeMonitorBridgeError('INVALID_CONFIGURATION') from None
        self._store, self._epoch, self._timeout = event_store, epoch, float(timeout_seconds)
        self._pid, self._owner = os.getpid(), None
        self._identity = identity
        self._state = 'new'
        self._lifecycle_lock = threading.RLock()
        self._closed_snapshot = None
        factory = ZmqTransportFactory({self._target.security_profile_ref: curve_keys})
        self._runtime = ThreadedServiceClientRuntime(
            lambda: ServiceClientRuntime(
                config, identity=identity, scope_resolver=lambda ref: scope if ref == scope_ref else None,
                peer_resolver=lambda service: expected_peer if service == self._target.service_id else None,
                transport_factory=factory, run_id=run_id,
                requested_operations={'upstream.telemetry': frozenset({'events.append'})},
                require_unregister_ack=True),
            require_unregister_ack=True)

    def start(self):
        with self._lifecycle_lock:
            if self._state != 'new' or os.getpid() != self._pid:
                raise RuntimeMonitorBridgeError('UNAVAILABLE')
            self._state = 'close_uncertain'
            try:
                snapshot = self._runtime.start(timeout_seconds=self._timeout)
                if snapshot.state != 'running' or snapshot.healthy is not True:
                    raise ValueError
            except Exception:
                raise RuntimeMonitorBridgeError('START_FAILED') from None
            self._state = 'running'
            return snapshot

    @property
    def state(self):
        return self._state

    def _check_source_event(self, event):
        candidate = self._store.lookup_event(event.sequence)
        if candidate is None or candidate.to_mapping() != event.to_mapping():
            raise ValueError

    def append(self, event):
        from bt_api_run_contract import EventKind, RESULT_CODES, RuntimeEvent

        if self._state != 'running' or os.getpid() != self._pid:
            raise RuntimeMonitorBridgeError('UNAVAILABLE')
        current = threading.current_thread()
        if self._owner is None:
            self._owner = current
        if self._owner is not current:
            raise RuntimeMonitorBridgeError('WRONG_OWNER')
        try:
            if self._store.available is not True or self._store.epoch != self._epoch:
                raise ValueError
            if (type(event) is not RuntimeEvent or type(event.kind) is not EventKind
                    or type(event.sequence) is not int or not 1 <= event.sequence < (1 << 63)
                    or (event.code is not None and event.code not in RESULT_CODES)):
                raise ValueError
            self._check_source_event(event)
            at = datetime.fromisoformat(event.at_utc.replace('Z', '+00:00'))
            if at.tzinfo is None or at.utcoffset() != timezone.utc.utcoffset(at):
                raise ValueError
            data = {'schema': 'sdk.runtime_observation.v1', 'sequence': event.sequence}
            if event.command_id is not None:
                # RuntimeEvent validates this canonical correlation ID; carry it
                # through the durable Monitor event without exposing arbitrary IDs.
                data['command_id'] = event.command_id
            details = {}
            for key, value in event.detail.items():
                if key in _BOOL_FIELDS and type(value) is bool:
                    details[key] = value
                elif key in _INTEGER_FIELDS and type(value) is int and -(1 << 31) <= value < (1 << 31):
                    details[key] = value
                elif key in _NUMBER_FIELDS and type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e9:
                    details[key] = value
            if details:
                data['detail'] = details
            if event.code is not None:
                data['code'] = event.code
            producer = [self._identity.principal_ref, self._identity.actor_ref, self._identity.delegate_ref]
            encoded = json.dumps([producer, self._epoch, event.sequence], ensure_ascii=True,
                                 separators=(',', ':')).encode('ascii')
            source_id = 'runtime-event:' + hashlib.sha256(encoded).hexdigest()
            arguments = {'schema_version': 1, 'event_id': source_id,
                         'event_type': 'runtime.' + event.kind.value + '.v1',
                         'occurred_at': at.timestamp(), 'data': data}
            snapshot = self._runtime.bounded_status(timeout_seconds=self._timeout)
            target = self._target
            matches = [b for b in snapshot.bindings if b.active is True and b.required is True
                       and b.mount == target.mount and b.service_id == target.service_id
                       and b.instance_id == target.instance_id and b.scope_ref == target.scope_ref
                       and b.scope == target.scope and b.operations == frozenset({'events.append'})
                       and b.authenticated_peer == target.expected_peer
                       and b.server_key_fingerprint == target.server_key_fingerprint]
            if (snapshot.state != 'running' or snapshot.healthy is not True
                    or snapshot.identity != target.identity or snapshot.run_id != target.run_id
                    or len(matches) != 1):
                raise ValueError
            result = self._runtime.call_bound('upstream.telemetry', 'events.append', arguments,
                                              route=self._route, timeout_seconds=self._timeout)
            if type(result) is not BoundCallResult:
                raise ValueError
            for observation in (result.before, result.after):
                if (observation.identity != target.identity or observation.run_id != target.run_id
                        or observation.scope != target.scope or observation.mount != target.mount
                        or observation.scope_ref != target.scope_ref or observation.service_id != target.service_id
                        or observation.instance_id != target.instance_id or observation.peer != target.expected_peer
                        or observation.fingerprint != target.server_key_fingerprint
                        or observation.registration_id != matches[0].registration_id
                        or observation.service_epoch != matches[0].service_epoch
                        or observation.operations != frozenset({'events.append'})
                        or observation.active is not True or observation.required is not True
                        or observation.operation != 'events.append'):
                    raise ValueError
            _validate_receipt(result.result, source_event_id=source_id)
        except Exception:
            raise RuntimeMonitorBridgeError('DELIVERY_UNAVAILABLE') from None
        return 'accepted'

    def close(self):
        with self._lifecycle_lock:
            if self._state == 'closed':
                return self._closed_snapshot
            self._state = 'close_uncertain'
            try:
                result = self._runtime.close(timeout_seconds=self._timeout)
                if (result.state != 'closed' or result.thread_alive
                        or result.cleanup_failures != 0 or result.unregister_uncertainties != 0
                        or result.unknown_registration_count != 0 or result.owner_cleanup_uncertainties != 0):
                    raise ValueError
                self._closed_snapshot = result
                self._state = 'closed'
                return result
            except Exception:
                raise RuntimeMonitorBridgeError('CLEANUP_UNCERTAIN') from None
