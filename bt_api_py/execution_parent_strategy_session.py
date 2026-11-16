"""A separately registered strict Execution parent client session.

Health covers this session's selected registration only; it is not evidence of
Gateway readiness, provider facts, or trading authority.
"""
from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from bt_api_base.service_protocol import WireRole
from bt_api_py.execution_parent_client import ExecutionParentClient
from bt_api_py.execution_parent_grant import OPERATIONS
from bt_api_py.gateway_strategy_client import _CleanupNotStarted, _strict_close_snapshot
from bt_api_py.service_config import ClientConfig, parse_client_config
from bt_api_py.service_runtime import (BoundExecutionParentTarget, ClientCurveKeys,
                                       ServiceClientRuntime, ZmqTransportFactory)
from bt_api_py.service_runtime_thread import (RuntimeSnapshot, ThreadedRuntimeError,
                                              ThreadedServiceClientRuntime)

# Local custody only: this is not an account or cross-process writer fence.
_RETAINED_EXECUTION_SESSIONS = {}
_MOUNT = 'broker.execution_parent'


class ExecutionParentStrategySession:
    """Own one non-restartable, strictly cleaned Execution registration."""

    def __init__(self, config: ClientConfig, *, target: BoundExecutionParentTarget,
                 security_profiles: Mapping[str, ClientCurveKeys], timeout_seconds=5.0,
                 queue_size=32):
        try:
            if type(config) is not ClientConfig or type(target) is not BoundExecutionParentTarget:
                raise ValueError
            config_copy = parse_client_config(config.to_public_dict())
            BoundExecutionParentTarget.__post_init__(target)
            target_copy = replace(target, scope=replace(target.scope), identity=replace(target.identity),
                                  expected_peer=replace(target.expected_peer))
            all_bindings = [binding for client in config_copy.clients.values()
                            for binding in client['settings']['bindings']
                            if binding['mount'] == _MOUNT]
            if len(all_bindings) != 1 or config_copy.process_role != 'strategy_client':
                raise ValueError
            binding = all_bindings[0]
            profile = config_copy.communication['profiles'][target_copy.transport_ref]
            scope, identity, peer = target_copy.scope, target_copy.identity, target_copy.expected_peer
            if (binding['required'] is not True
                    or any(binding[name] != getattr(target_copy, name) for name in
                           ('mount', 'service_id', 'instance_id', 'scope_ref', 'transport_ref'))
                    or binding['account_ref'] != scope.account_ref
                    or config_copy.registration['strategy_ref'] != scope.strategy_ref
                    or identity.actor_ref != scope.strategy_ref
                    or peer.role is not WireRole.SERVICE or peer.actor_ref != target_copy.service_id
                    or profile['backend'] != 'zmq'
                    or profile['security_profile_ref'] != target_copy.security_profile_ref
                    or profile['endpoints'].get('rpc_connect') != target_copy.endpoint
                    or not isinstance(security_profiles, Mapping)
                    or set(security_profiles) != {target_copy.security_profile_ref}):
                raise ValueError
            keys = security_profiles[target_copy.security_profile_ref]
            if (type(keys) is not ClientCurveKeys
                    or any(type(value) is not bytes or len(value) != 40
                           for value in (keys.server_key, keys.public_key, keys.secret_key))
                    or hashlib.sha256(keys.server_key).hexdigest() != target_copy.server_key_fingerprint):
                raise ValueError
            frozen_keys = MappingProxyType({target_copy.security_profile_ref:
                ClientCurveKeys(keys.server_key, keys.public_key, keys.secret_key)})
        except Exception:
            raise ValueError('INVALID_EXECUTION_PARENT_CONFIGURATION') from None

        def resolve_scope(scope_ref):
            if scope_ref != target_copy.scope_ref:
                raise ValueError('INVALID_SCOPE')
            return replace(scope)

        def resolve_peer(service_id):
            if service_id != target_copy.service_id:
                raise ValueError('INVALID_PEER')
            return replace(peer)

        def factory():
            return ServiceClientRuntime(
                config_copy, identity=replace(identity), scope_resolver=resolve_scope,
                peer_resolver=resolve_peer,
                transport_factory=ZmqTransportFactory(frozen_keys, strict_single_connection=True),
                run_id=target_copy.run_id, requested_operations={_MOUNT: OPERATIONS},
                registration_mounts=frozenset({_MOUNT}), require_unregister_ack=True,
                cleanup_unknown=lambda: False, on_cleanup_sticky=lambda: None)

        runtime = ThreadedServiceClientRuntime(factory, queue_size=queue_size,
            require_unregister_ack=True, cleanup_unknown=lambda: False,
            on_cleanup_sticky=lambda _runtime: None)
        self._client = ExecutionParentClient(runtime, target=target_copy, timeout_seconds=timeout_seconds)
        self._runtime = self._original_runtime = runtime
        self._target = target_copy
        self._lock = threading.Lock()
        self._close_requested = self._start_uncertain = self._cleanup_uncertain = False
        self._start_scope_rejected = False
        self._closed_snapshot = None

    @property
    def target(self):
        return replace(self._target, scope=replace(self._target.scope),
                       identity=replace(self._target.identity), expected_peer=replace(self._target.expected_peer))

    @property
    def client(self):
        return self._client

    @property
    def runtime(self):
        return self._runtime

    @property
    def cleanup_confirmed(self):
        if (self._runtime is not self._original_runtime or self._closed_snapshot is None
                or self._cleanup_uncertain):
            return False
        try:
            return self._runtime.status() == self._closed_snapshot
        except Exception:
            return False

    def start(self, *, timeout_seconds=5.0):
        if not self._lock.acquire(blocking=False):
            raise ThreadedRuntimeError('CLEANUP_FAILED')
        try:
            if (self._close_requested or self._cleanup_uncertain or self._start_uncertain
                    or self._start_scope_rejected
                    or self._runtime is not self._original_runtime
                    or type(self._runtime) is not ThreadedServiceClientRuntime
                    or self._runtime.strict_cleanup_required is not True):
                raise ThreadedRuntimeError('CLEANUP_POLICY_UNPROVEN')
            _RETAINED_EXECUTION_SESSIONS[id(self)] = self
            try:
                snapshot = self._runtime.start(timeout_seconds=timeout_seconds)
                if (type(snapshot) is not RuntimeSnapshot or snapshot.strict_unregister_ack is not True
                        or snapshot.state != 'running' or snapshot.healthy is not True
                        or snapshot.thread_alive is not True or snapshot.identity != self._target.identity
                        or snapshot.run_id != self._target.run_id or self._close_requested):
                    raise ThreadedRuntimeError('CLEANUP_POLICY_UNPROVEN')
                active = [binding for binding in snapshot.bindings if binding.active is True]
                if (not active or any(binding.scope != self._target.scope for binding in active)
                        or len(active) != 1 or active[0].mount != self._target.mount
                        or active[0].service_id != self._target.service_id
                        or active[0].instance_id != self._target.instance_id
                        or active[0].scope_ref != self._target.scope_ref
                        or active[0].required is not True):
                    self._start_scope_rejected = True
                    raise ThreadedRuntimeError('REGISTRATION_SCOPE_MISMATCH')
                return snapshot
            except BaseException:
                if not self._start_scope_rejected:
                    self._start_uncertain = True
                raise
        finally:
            self._lock.release()

    def status(self):
        return self._runtime.status()

    def close(self, *, timeout_seconds=5.0):
        self._close_requested = True
        if not self._lock.acquire(blocking=False):
            raise ThreadedRuntimeError('CLEANUP_FAILED')
        try:
            if self._runtime is not self._original_runtime or self._cleanup_uncertain:
                raise ThreadedRuntimeError('CLEANUP_FAILED')
            if self._closed_snapshot is not None:
                return self._closed_snapshot
            try:
                valid_timeout = (type(timeout_seconds) in (int, float)
                                 and math.isfinite(timeout_seconds) and timeout_seconds > 0)
            except OverflowError:
                valid_timeout = False
            if not valid_timeout:
                raise ThreadedRuntimeError('CLEANUP_NOT_STARTED')
            _RETAINED_EXECUTION_SESSIONS[id(self)] = self
            self._cleanup_uncertain = True
            try:
                snapshot = _strict_close_snapshot(self._runtime, timeout_seconds,
                    allow_close=not self._start_uncertain, identity=self._target.identity,
                    run_id=self._target.run_id)
                if snapshot is None:
                    raise ThreadedRuntimeError('CLEANUP_FAILED')
                self._closed_snapshot = snapshot
                self._cleanup_uncertain = False
                _RETAINED_EXECUTION_SESSIONS.pop(id(self), None)
                return snapshot
            except _CleanupNotStarted:
                self._cleanup_uncertain = False
                raise ThreadedRuntimeError('CLEANUP_NOT_STARTED') from None
            except Exception:
                raise ThreadedRuntimeError('CLEANUP_FAILED') from None
        finally:
            self._lock.release()
