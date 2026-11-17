"""Local journal plus real owner runtime with a synthetic strict-frame peer."""
from dataclasses import replace
import hashlib
import json
import threading
import time

import pytest
import zmq

from bt_api_execution.local_gateway_parent_preparation import LocalGatewayParentPreparation
from bt_api_execution.atomic_submission_intake import _key
from bt_api_execution.contracts import ExecutionScope, canonical_json
from bt_api_execution.submission_intake import _identity, _wire_scope
from bt_api_py.execution_parent_grant import SUBMIT
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import (
    BoundServiceTarget, ClientCurveKeys, ServiceCallOutcomeUnknownError, ServiceClientRuntime,
    ServiceRuntimeRequestNotSentError, ZmqTransportFactory, _LiveParentSourceCapability,
)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from bt_api_transport_zmq import service_v2 as transport
from tests.unit.test_iter65_live_source_capability import Context, IDENTITY, OPS, PEER, Peer, config, setup
from test_iter65_atomic_submission_intake import atomic  # noqa: F401
from test_iter65_account_store_fork_fence import _fork_results


class ParentPeer(Peer):
    def __init__(self, scope, identity):
        super().__init__()
        self.scope = scope
        self.identity = identity
        self.subject_override = None
        self.source_calls = 0

    def handle(self, request):
        if request.header.operation != 'direct.parent.source':
            return super().handle(request)
        self.source_calls += 1
        if self.before_source is not None:
            self.before_source()
        result = {'status': 'OBSERVED', 'schema_version': 1, 'operation': 'direct.parent.source', 'result': {
            'schema_version': 1, 'account_scope': self.scope.account_ref, 'strategy_scope': self.scope.strategy_ref,
            'execution_domain_ref': self.scope.execution_domain_ref, 'journal_id': self.journal_id,
            'gateway_generation': self.generation, 'journal_revision': self.revision}}
        if request.payload['arguments'].get('subject_version') == 1:
            result['schema_version'] = 3
            result['strategy_subject'] = (self.subject_override if self.subject_override is not None else
                                          {'identity': _identity(self.identity), 'scope': _wire_scope(self.scope)})
        return result


@pytest.fixture
def environment(atomic, monkeypatch):
    rig, store, lease = atomic
    request = rig.parent()
    assert rig.call(SUBMIT, request)['ok']
    preparation = LocalGatewayParentPreparation(host=rig.host, scope=rig.target, lease=lease,
                                                 wire_scope=rig.scope, identity=rig.identity)
    preparation.prepare(request['spec']['parent_ref'])
    peer = ParentPeer(rig.scope, rig.identity)
    monkeypatch.setattr(transport.zmq, 'Context', lambda **_kwargs: Context(peer))
    server, _ = zmq.curve_keypair()
    public, secret = zmq.curve_keypair()
    keys = ClientCurveKeys(server, public, secret)
    runtimes = []
    def build():
        raw_config = config().to_public_dict()
        raw_config['clients']['gateway']['settings']['bindings'][0]['account_ref'] = rig.scope.account_ref
        runtime = ServiceClientRuntime(parse_service_config(raw_config), identity=IDENTITY, scope_resolver=lambda _: rig.scope,
            peer_resolver=lambda _: PEER, transport_factory=ZmqTransportFactory({'synthetic-key': keys},
                strict_single_connection=True), run_id=rig.scope.run_id,
            requested_operations={'upstream.direct_write': OPS})
        runtimes.append(runtime)
        return runtime
    threaded = ThreadedServiceClientRuntime(build)
    binding, = threaded.start().bindings
    target = BoundServiceTarget('upstream.direct_write', 'service.direct_write', 'synthetic-service',
        'synthetic-instance', 'synthetic-account', rig.scope, IDENTITY, rig.scope.run_id, 'gateway',
        'synthetic-key', 'tcp://127.0.0.1:1', PEER, hashlib.sha256(server).hexdigest(),
        binding.registration_id, binding.service_epoch)
    try:
        yield rig, store, lease, preparation, threaded, target, peer, runtimes[0], request
    finally:
        threaded.close()


@pytest.mark.parametrize('case', ['valid', 'other_run', 'missing_preparation', 'empty_parent'])
def test_retired_prepared_source_rejects_without_rpc_or_writes(environment, case):
    rig, store, _, preparation, threaded, target, peer, runtime, request = environment
    before = store._connection.total_changes
    if case == 'other_run':
        target = replace(target, scope=replace(target.scope, run_id='other-run'), run_id='other-run')
    elif case == 'missing_preparation':
        preparation = None
    parent_ref = '' if case == 'empty_parent' else request['spec']['parent_ref']
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        threaded.capture_prepared_parent_source(target, preparation, parent_ref)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        type(runtime).capture_prepared_parent_source(runtime, target, preparation, parent_ref,
                                                     deadline_monotonic=time.monotonic() + 5)
    assert peer.source_calls == 0 and not peer.registers
    assert store._connection.total_changes == before
    assert not runtime._live_parent_capabilities


def test_direct_runtime_owner_fork_rejects_before_inherited_lock(setup):
    runtime, target, spec, _ = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=time.monotonic() + 5)
    held, release = threading.Event(), threading.Event()
    def lock_registry():
        with runtime._live_parent_capabilities_lock:
            held.set()
            release.wait(5)
    holder = threading.Thread(target=lock_registry)
    holder.start()
    try:
        assert held.wait(2)
        assert _fork_results([runtime.close, lambda: runtime.capture_live_parent_source(
            target, spec, deadline_monotonic=time.monotonic() + 5)]) == [
                'ServiceRuntimeError', 'ServiceRuntimeError']
        assert not cap._revoked.is_set()
    finally:
        release.set()
        holder.join(5)
    assert not holder.is_alive() and cap in runtime._live_parent_capabilities
