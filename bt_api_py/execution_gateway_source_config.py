"""Explicit local Gateway SOURCE client for a combined Execution owner.

This client only records a source observation; it never sends a registration
command for a parent, contacts Risk, or dispatches an order. Its underlying
strict Gateway registration requires both SOURCE and register_v2 grants, but
this module issues only SOURCE.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from bt_api_base.service_protocol import WireRole
from zmq.utils import z85
import zmq

from .execution_gateway_source_preparation import GatewaySourcePreparation
from .execution_read_service_host import _endpoint, _identity, _path, _text
from .monitor_service_config import read_bounded
from .service_config import parse_service_config
from .service_runtime import BoundServiceTarget, ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from .service_runtime_thread import ThreadedServiceClientRuntime

_MOUNT = 'upstream.direct_write'
_TRANSPORT = 'execution-source-gateway'
_SECURITY = 'execution-source-key'
_OPERATIONS = frozenset({'direct.parent.source', 'direct.parent.register_v2'})


class GatewaySourceConfigError(ValueError):
    pass


@dataclass(frozen=True)
class GatewaySourceConfig:
    endpoint: str
    service_id: str
    instance_id: str
    scope_ref: str
    peer: object
    strategy_identity: object
    server_public: Path
    client_public: Path
    client_secret: Path
    timeout_ms: int


def parse_config(raw, parent, management):
    try:
        fields = {'endpoint', 'service_id', 'instance_id', 'scope_ref', 'peer_identity',
                  'strategy_principal_ref', 'server_public_key_file', 'client_public_key_file',
                  'client_secret_key_file', 'timeout_ms'}
        if type(raw) is not dict or set(raw) != fields:
            raise ValueError
        endpoint = _endpoint(raw['endpoint'])
        peer = _identity(raw['peer_identity'], WireRole.SERVICE)
        principal = _text(raw['strategy_principal_ref'])
        grants = [grant for grant in parent.parent_grants if grant.identity.principal_ref == principal]
        if len(grants) != 1:
            raise ValueError
        timeout = raw['timeout_ms']
        # The synchronous control step runs on the parent owner thread. This
        # bounds the network wait, not local filesystem or SQLite latency.
        if type(timeout) is not int or not 1 <= timeout <= 250:
            raise ValueError
        keys = tuple(_path(raw[field]) for field in
                     ('server_public_key_file', 'client_public_key_file', 'client_secret_key_file'))
        if len(set(keys)) != 3:
            raise ValueError
        for path in keys:
            if (path == parent.journal_root or parent.journal_root in path.parents
                    or path == management.audit_root or management.audit_root in path.parents
                    or path == parent.registry_path):
                raise ValueError
        if endpoint in (management.endpoint,
                parent.service.communication['profiles'][parent.service.transport_ref]['endpoints']['rpc_bind']):
            raise ValueError
        result = GatewaySourceConfig(endpoint, _text(raw['service_id']), _text(raw['instance_id']),
            _text(raw['scope_ref']), peer, grants[0].identity, *keys, timeout)
        _service_config(parent, result)
        return result
    except Exception:
        raise GatewaySourceConfigError('INVALID_CONFIGURATION') from None


def validate_config(config, parent, management):
    if type(config) is not GatewaySourceConfig:
        raise GatewaySourceConfigError('INVALID_CONFIGURATION')
    try:
        peer = config.peer
        raw = {'endpoint': config.endpoint, 'service_id': config.service_id,
            'instance_id': config.instance_id, 'scope_ref': config.scope_ref,
            'peer_identity': {'role': peer.role.value, 'principal_ref': peer.principal_ref,
                              'actor_ref': peer.actor_ref},
            'strategy_principal_ref': config.strategy_identity.principal_ref,
            'server_public_key_file': str(config.server_public), 'client_public_key_file': str(config.client_public),
            'client_secret_key_file': str(config.client_secret), 'timeout_ms': config.timeout_ms}
        if parse_config(raw, parent, management) != config:
            raise ValueError
    except Exception:
        raise GatewaySourceConfigError('INVALID_CONFIGURATION') from None


def _keys(config):
    paths = (config.server_public, config.client_public, config.client_secret)
    keys, inodes = [], set()
    for path in paths:
        value = read_bounded(path, 4096, protected=True).strip()
        info = path.stat(follow_symlinks=False)
        if ((info.st_dev, info.st_ino) in inodes or len(value) != 40 or len(z85.decode(value)) != 32):
            raise GatewaySourceConfigError('INVALID_KEYS')
        inodes.add((info.st_dev, info.st_ino)); keys.append(value)
    if zmq.curve_public(keys[2]) != keys[1] or keys[0] == keys[1]:
        raise GatewaySourceConfigError('INVALID_KEYS')
    return ClientCurveKeys(*keys)


def _service_config(parent, config):
    raw = copy.deepcopy(parent.service.to_public_dict())
    if _TRANSPORT in raw['communication']['profiles']:
        raise GatewaySourceConfigError('INVALID_CONFIGURATION')
    raw['communication']['profiles'][_TRANSPORT] = {'backend': 'zmq', 'config_schema_version': 1,
        'endpoints': {'rpc_connect': config.endpoint}, 'security_profile_ref': _SECURITY}
    raw['clients'] = {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
        'mount': _MOUNT, 'service_id': config.service_id, 'instance_id': config.instance_id,
        'scope_ref': config.scope_ref, 'account_ref': parent.wire_scope.account_ref,
        'transport_ref': _TRANSPORT, 'required': True}],
        'options': {'request_timeout_ms': 2000, 'heartbeat_interval_seconds': 5}}}}
    return parse_service_config(raw)


class GatewaySourceClient:
    """Own one strict runtime; preserve the same object on uncertain cleanup."""
    def __init__(self, config, owner):
        if type(config) is not GatewaySourceConfig:
            raise GatewaySourceConfigError('INVALID_CONFIGURATION')
        self.config, self.owner = config, owner
        self.runtime = self.target = None
        self.state = 'new'

    def start(self):
        if self.state != 'new':
            raise GatewaySourceConfigError('INVALID_STATE')
        self.state = 'close_uncertain'
        config, owner = self.config, self.owner
        keys = _keys(config)
        service_config = _service_config(owner.config, config)
        factory = ZmqTransportFactory({_SECURITY: keys}, strict_single_connection=True)
        def create():
            return ServiceClientRuntime(service_config, identity=owner.config.identity,
                scope_resolver=lambda _: owner.config.wire_scope, peer_resolver=lambda _: config.peer,
                transport_factory=factory, run_id=owner.config.wire_scope.run_id,
                requested_operations={_MOUNT: _OPERATIONS}, require_unregister_ack=True)
        self.runtime = ThreadedServiceClientRuntime(create, require_unregister_ack=True)
        snapshot = self.runtime.start(timeout_seconds=8)
        if (snapshot.state != 'running' or not snapshot.healthy or snapshot.identity != owner.config.identity
                or snapshot.run_id != owner.config.wire_scope.run_id or len(snapshot.bindings) != 1):
            raise GatewaySourceConfigError('START_FAILED')
        binding, = snapshot.bindings
        fingerprint = hashlib.sha256(keys.server_key).hexdigest()
        if (binding.active is not True or binding.mount != _MOUNT or binding.service_id != config.service_id
                or binding.instance_id != config.instance_id or binding.scope_ref != config.scope_ref
                or binding.scope != owner.config.wire_scope or binding.operations != _OPERATIONS
                or binding.authenticated_peer != config.peer or binding.server_key_fingerprint != fingerprint):
            raise GatewaySourceConfigError('START_FAILED')
        self.target = BoundServiceTarget(_MOUNT, 'service.direct_write', config.service_id,
            config.instance_id, config.scope_ref, owner.config.wire_scope, owner.config.identity,
            owner.config.wire_scope.run_id, _TRANSPORT, _SECURITY, config.endpoint, config.peer,
            fingerprint, binding.registration_id, binding.service_epoch)
        self.state = 'running'

    def status(self):
        if self.state != 'running' or self.runtime is None or self.target is None:
            return 'UNREADY'
        try:
            snap = self.runtime.bounded_status(timeout_seconds=0.1)
            if (snap.state != 'running' or not snap.thread_alive or not snap.healthy
                    or snap.identity != self.target.identity or snap.run_id != self.target.run_id
                    or len(snap.bindings) != 1):
                return 'UNREADY'
            binding, = snap.bindings
            valid = (binding.active is True and binding.mount == self.target.mount
                and binding.service_id == self.target.service_id and binding.instance_id == self.target.instance_id
                and binding.scope_ref == self.target.scope_ref and binding.scope == self.target.scope
                and binding.registration_id == self.target.registration_id
                and binding.service_epoch == self.target.service_epoch and binding.operations == _OPERATIONS
                and binding.authenticated_peer == self.target.expected_peer
                and binding.server_key_fingerprint == self.target.server_key_fingerprint)
            return 'READY' if valid else 'UNREADY'
        except Exception:
            return 'UNREADY'

    def observe(self, parent_ref, *, request_id, request_sha256):
        if self.state != 'running' or self.runtime is None or self.target is None:
            return 'UNKNOWN'
        try:
            adapter = GatewaySourcePreparation(owner=self.owner, runtime=self.runtime, target=self.target,
                                               strategy_identity=self.config.strategy_identity)
            return adapter.observe(parent_ref, request_id=request_id, request_sha256=request_sha256,
                                   timeout_seconds=self.config.timeout_ms / 1000)
        except Exception:
            return 'UNKNOWN'

    def close(self):
        if self.state == 'closed':
            return
        self.state = 'close_uncertain'
        if self.runtime is not None:
            result = self.runtime.close(timeout_seconds=5)
            if result.state != 'closed' or result.thread_alive:
                raise GatewaySourceConfigError('CLEANUP_UNKNOWN')
            self.runtime = None
        self.target = None
        self.state = 'closed'


class StdinSourceControl:
    """Nonblocking, bounded JSON-lines input borrowed from the process owner."""
    def __init__(self, fd, output_fd=None):
        if (os.name != 'posix' or type(fd) is not int
                or (output_fd is not None and (type(output_fd) is not int or output_fd == fd))):
            raise GatewaySourceConfigError('CONTROL_UNAVAILABLE')
        try:
            self._was_blocking = os.get_blocking(fd)
            self._output_was_blocking = None if output_fd is None else os.get_blocking(output_fd)
            os.set_blocking(fd, False)
            if output_fd is not None:
                os.set_blocking(output_fd, False)
        except (OSError, ValueError):
            try:
                os.set_blocking(fd, self._was_blocking)
            except (OSError, ValueError, AttributeError):
                pass
            raise GatewaySourceConfigError('CONTROL_UNAVAILABLE') from None
        self._fd, self._open, self._dropping = fd, True, False
        self._buffer, self._queue = bytearray(), deque()
        self._rejected = 0
        self._output_fd, self._output = output_fd, bytearray()
        self._output_failed = False

    @property
    def accepting(self):
        return not self._output_failed and len(self._output) <= 4096 - 128

    def report(self, line):
        """Queue a finite status without waiting for the output consumer."""
        if self._output_fd is None or self._output_failed or type(line) is not str:
            return False
        try:
            data = (line + '\n').encode('ascii', 'strict')
            if not line.startswith('EXECUTION_') or len(data) > 128 or len(self._output) + len(data) > 4096:
                return False
            self._output.extend(data)
            return True
        except UnicodeError:
            return False

    def flush(self):
        if self._output_fd is None or self._output_failed or not self._output:
            return
        try:
            written = os.write(self._output_fd, self._output)
            if written <= 0:
                self._output_failed = True
            else:
                del self._output[:written]
        except BlockingIOError:
            pass
        except OSError:
            self._output_failed = True

    def poll(self):
        """Read at most 4 KiB; return at most one queued item and overflow flag."""
        if self._rejected and not self._queue:
            self._rejected -= 1
            return False, True
        if self._open and not self._rejected:
            try:
                chunk = os.read(self._fd, 4096)
            except BlockingIOError:
                chunk = None
            except OSError:
                self._open = False
                chunk = None
            if chunk == b'':
                self._open = False
                if self._buffer or self._dropping:
                    if len(self._queue) < 8 and not self._rejected:
                        self._queue.append(None)
                    else:
                        self._rejected += 1
                    self._buffer.clear(); self._dropping = False
            elif chunk:
                for value in chunk:
                    if value == 10:
                        item = None if self._dropping else bytes(self._buffer)
                        if len(self._queue) < 8 and not self._rejected:
                            self._queue.append(item)
                        else:
                            self._rejected += 1
                        self._buffer.clear(); self._dropping = False
                    elif not self._dropping:
                        if len(self._buffer) == 1024:
                            self._buffer.clear(); self._dropping = True
                        else:
                            self._buffer.append(value)
        if self._queue:
            return self._queue.popleft(), False
        if self._rejected:
            self._rejected -= 1
            return False, True
        return False, False

    @staticmethod
    def request(item):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        try:
            if type(item) is not bytes:
                raise ValueError
            value = json.loads(item.decode('utf-8', 'strict'), object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(value) is not dict or set(value) != {'parent_ref', 'request_id', 'request_sha256'}:
                raise ValueError
            return value
        except Exception:
            return None

    def close(self):
        failed = False
        try:
            os.set_blocking(self._fd, self._was_blocking)
        except (OSError, ValueError):
            failed = True
        if self._output_fd is not None:
            try:
                os.set_blocking(self._output_fd, self._output_was_blocking)
            except (OSError, ValueError):
                failed = True
        if failed:
            raise GatewaySourceConfigError('CONTROL_CLEANUP_UNKNOWN')
