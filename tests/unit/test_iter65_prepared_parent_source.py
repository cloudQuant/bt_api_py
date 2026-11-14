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


def test_matching_original_sends_only_source_and_binds_each_anchor(environment):
    rig, _, lease, preparation, threaded, target, peer, runtime, request = environment
    cap = threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    assert type(cap) is _LiveParentSourceCapability and not cap._revoked.is_set()
    assert peer.source_calls == 1 and not peer.registers
    owner, entry, saved_prep, bindings, parent_ref, canonical, saved_target, spec = runtime._live_parent_capabilities[cap]
    assert owner is runtime and saved_prep is preparation and bindings[2] is lease
    assert parent_ref == request['spec']['parent_ref'] and saved_target == target
    assert canonical == preparation.read_prepared(parent_ref).canonical_record
    assert spec == preparation.read_prepared(parent_ref).gateway_spec_json
    assert entry[7] == (rig.scope.account_ref, rig.scope.strategy_ref, rig.scope.execution_domain_ref,
                        peer.journal_id, peer.generation, peer.revision)
    assert type(entry[8]) is transport.ServiceReplyReceipt and entry[8].operation == 'direct.parent.source'
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        threaded.register_live_parent(cap, target, dict(request['spec']))
    assert cap._revoked.is_set() and not peer.registers


def test_other_run_and_fake_preparation_fail_before_source(environment):
    rig, _, _, preparation, threaded, target, peer, _, request = environment
    other = replace(target, scope=replace(target.scope, run_id='other-run'), run_id='other-run')
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        threaded.capture_prepared_parent_source(other, preparation, request['spec']['parent_ref'])
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        threaded.capture_prepared_parent_source(target, object(), request['spec']['parent_ref'])
    assert peer.source_calls == 0 and not peer.registers


def test_valid_service_for_account_b_rejects_local_account_a_before_source(environment, monkeypatch):
    rig, _, _, preparation, _, _, _, runtime, request = environment
    scope_b = replace(rig.scope, account_ref='account-b')
    peer_b = ParentPeer(scope_b, rig.identity)
    monkeypatch.setattr(transport.zmq, 'Context', lambda **_kwargs: Context(peer_b))
    keys = runtime._factory._profiles['synthetic-key']
    def build():
        raw_config = config().to_public_dict()
        raw_config['clients']['gateway']['settings']['bindings'][0]['account_ref'] = scope_b.account_ref
        return ServiceClientRuntime(parse_service_config(raw_config), identity=IDENTITY, scope_resolver=lambda _: scope_b,
            peer_resolver=lambda _: PEER, transport_factory=ZmqTransportFactory({'synthetic-key': keys},
                strict_single_connection=True), run_id=scope_b.run_id,
            requested_operations={'upstream.direct_write': OPS})
    service_b = ThreadedServiceClientRuntime(build)
    try:
        binding, = service_b.start().bindings
        target_b = BoundServiceTarget('upstream.direct_write', 'service.direct_write', 'synthetic-service',
            'synthetic-instance', 'synthetic-account', scope_b, IDENTITY, scope_b.run_id, 'gateway',
            'synthetic-key', 'tcp://127.0.0.1:1', PEER, hashlib.sha256(keys.server_key).hexdigest(),
            binding.registration_id, binding.service_epoch)
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            service_b.capture_prepared_parent_source(target_b, preparation, request['spec']['parent_ref'])
        assert peer_b.source_calls == 0 and not peer_b.registers
    finally:
        service_b.close()


@pytest.mark.parametrize('mutation', ['record', 'identity', 'lease', 'target'])
def test_change_during_source_revokes_and_cannot_issue_capability(environment, mutation):
    _, store, lease, preparation, threaded, target, peer, runtime, request = environment
    def mutate():
        if mutation == 'target':
            object.__setattr__(target, 'run_id', 'other-run')
        else:
            with store._transaction() as cursor:
                if mutation == 'record':
                    cursor.execute("UPDATE execution_parent_records SET request_sha256=? WHERE parent_id=?",
                                   ('f' * 64, request['spec']['parent_ref']))
                elif mutation == 'identity':
                    target_scope = preparation._scope
                    parent_scope = ExecutionScope(target_scope.provider, target_scope.environment,
                        target_scope.account_ref, target_scope.strategy_id).key
                    key = _key('local_parent', parent_scope, request['spec']['parent_ref'])
                    body = json.loads(cursor.execute('SELECT value FROM execution_meta WHERE key=?', (key,)).fetchone()[0])
                    body['identity']['actor_ref'] = 'other'
                    for index in body['index_keys']:
                        cursor.execute('UPDATE execution_meta SET value=? WHERE key=?', (canonical_json(body), index))
                else:
                    cursor.execute('UPDATE execution_writer_leases SET expires_at_ns=1 WHERE owner_id=?',
                                   (lease.owner_id,))
    peer.before_source = mutate
    try:
        with pytest.raises((ServiceCallOutcomeUnknownError, ThreadedRuntimeError)):
            threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    finally:
        if mutation == 'target':
            object.__setattr__(target, 'run_id', target.scope.run_id)
    assert peer.source_calls == 1 and not peer.registers and not runtime._live_parent_capabilities


@pytest.mark.parametrize('field', ['principal', 'actor', 'role', 'delegate', 'account', 'strategy',
                                    'domain', 'run', 'missing', 'extra'])
def test_issued_source_subject_must_exactly_match_local_intake(environment, field):
    rig, _, _, preparation, threaded, target, peer, runtime, request = environment
    subject = {'identity': _identity(rig.identity), 'scope': _wire_scope(rig.scope)}
    if field in ('principal', 'actor', 'role', 'delegate'):
        key = {'principal': 'principal_ref', 'actor': 'actor_ref', 'role': 'role', 'delegate': 'delegate_ref'}[field]
        subject['identity'][key] = 'other'
    elif field in ('account', 'strategy', 'domain', 'run'):
        key = {'account': 'account_ref', 'strategy': 'strategy_ref',
               'domain': 'execution_domain_ref', 'run': 'run_id'}[field]
        subject['scope'][key] = 'other'
    elif field == 'missing':
        del subject['identity']['delegate_ref']
    else:
        subject['identity']['extra'] = 'other'
    peer.subject_override = subject
    with pytest.raises(ServiceCallOutcomeUnknownError):
        threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    assert peer.source_calls == 1 and not peer.registers and not runtime._live_parent_capabilities


def test_other_rpc_revokes_existing_prepared_capability(environment):
    _, _, _, preparation, threaded, target, peer, runtime, request = environment
    cap = threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    args = {'account_scope': target.scope.account_ref, 'strategy_scope': target.scope.strategy_ref,
            'execution_domain_ref': target.scope.execution_domain_ref}
    threaded.call_bound(target, 'direct.parent.source', args)
    assert cap._revoked.is_set() and cap not in runtime._live_parent_capabilities
    assert peer.source_calls == 2 and not peer.registers


def test_new_strict_source_revokes_prepared_capability(environment):
    _, _, _, preparation, threaded, target, peer, runtime, request = environment
    cap = threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    spec = json.loads(preparation.read_prepared(request['spec']['parent_ref']).gateway_spec_json)
    next_cap = threaded.capture_live_parent_source(target, spec)
    assert cap._revoked.is_set() and cap not in runtime._live_parent_capabilities
    assert next_cap in runtime._live_parent_capabilities and peer.source_calls == 2


def test_no_host_or_store_lock_is_held_during_source(environment):
    rig, store, _, preparation, threaded, target, peer, _, request = environment
    observed = []
    def before_source():
        def try_locks():
            host_acquired = rig.host._lock.acquire(blocking=False)
            store_acquired = store._lock.acquire(blocking=False)
            observed.append((host_acquired, store_acquired))
            if store_acquired:
                store._lock.release()
            if host_acquired:
                rig.host._lock.release()
        probe = threading.Thread(target=try_locks)
        probe.start()
        probe.join(2)
        assert not probe.is_alive()
    peer.before_source = before_source
    threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    assert observed == [(True, True)]


@pytest.mark.parametrize('kind', ['prepared_source', 'source', 'call_bound', 'call'])
def test_queued_timeout_revokes_previously_issued_capability(environment, monkeypatch, kind):
    _, _, _, preparation, threaded, target, peer, runtime, request = environment
    cap = threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    spec = json.loads(preparation.read_prepared(request['spec']['parent_ref']).gateway_spec_json)
    args = {'account_scope': target.scope.account_ref, 'strategy_scope': target.scope.strategy_ref,
            'execution_domain_ref': target.scope.execution_domain_ref}
    entered, release = threading.Event(), threading.Event()
    original_maintain = runtime.maintain
    def blocked_maintain():
        entered.set()
        assert release.wait(5)
        return original_maintain()
    monkeypatch.setattr(runtime, 'maintain', blocked_maintain)
    with threaded._condition:
        threaded._condition.notify_all()
    try:
        assert entered.wait(2)
        with pytest.raises((ServiceRuntimeRequestNotSentError, ThreadedRuntimeError)):
            if kind == 'prepared_source':
                threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'],
                                                        timeout_seconds=0.1)
            elif kind == 'source':
                threaded.capture_live_parent_source(target, spec, timeout_seconds=0.1)
            elif kind == 'call_bound':
                threaded.call_bound(target, 'direct.parent.source', args, timeout_seconds=0.1)
            else:
                threaded.call('upstream.direct_write', 'direct.parent.source', args, timeout_seconds=0.1)
        assert cap._revoked.is_set() and cap not in runtime._live_parent_capabilities
        assert peer.source_calls == 1 and not peer.registers
    finally:
        release.set()


def test_forged_and_cross_runtime_capabilities_do_not_authorize_register(environment):
    rig, _, _, preparation, threaded, target, peer, runtime, request = environment
    cap = threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    fake = _LiveParentSourceCapability()
    assert fake not in runtime._live_parent_capabilities
    spec = json.loads(preparation.read_prepared(request['spec']['parent_ref']).gateway_spec_json)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        threaded.register_live_parent(fake, target, spec)
    # Even presenting a real capability to a different exact runtime cannot
    # send a registration and immediately removes it from its issuer.
    cap = threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    keys = runtime._factory._profiles['synthetic-key']
    def build_other():
        raw_config = config().to_public_dict()
        raw_config['clients']['gateway']['settings']['bindings'][0]['account_ref'] = rig.scope.account_ref
        return ServiceClientRuntime(parse_service_config(raw_config), identity=IDENTITY, scope_resolver=lambda _: rig.scope,
            peer_resolver=lambda _: PEER, transport_factory=ZmqTransportFactory({'synthetic-key': keys},
                strict_single_connection=True), run_id=rig.scope.run_id,
            requested_operations={'upstream.direct_write': OPS})
    other = ThreadedServiceClientRuntime(build_other)
    try:
        other.start()
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            other.register_live_parent(cap, target, spec)
        assert cap._revoked.is_set() and cap not in runtime._live_parent_capabilities
        assert not peer.registers
    finally:
        other.close()


def test_abandoned_source_never_leaves_a_usable_capability(environment):
    _, _, _, preparation, threaded, target, peer, runtime, request = environment
    entered, release = threading.Event(), threading.Event()
    def block():
        entered.set()
        assert release.wait(5)
    peer.before_source = block
    failures = []
    def capture():
        try:
            threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'],
                                                   timeout_seconds=0.1)
        except Exception as error:
            failures.append(error)
    caller = threading.Thread(target=capture)
    caller.start()
    try:
        assert entered.wait(2)
        caller.join(2)
        assert not caller.is_alive() and len(failures) == 1
        assert isinstance(failures[0], ThreadedRuntimeError)
    finally:
        release.set()
        caller.join(2)
    peer.before_source = None
    # A timed-out strict reply retires the captured socket; it cannot be
    # silently reused as a replacement proof.
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    assert not runtime._live_parent_capabilities and peer.source_calls == 1 and not peer.registers


def test_timeout_during_post_source_local_recheck_revokes_issued_capability(environment, monkeypatch):
    _, _, _, preparation, threaded, target, peer, runtime, request = environment
    original = LocalGatewayParentPreparation.read_prepared
    entered, release = threading.Event(), threading.Event()
    count = []
    def delayed(self, ref):
        snapshot = original(self, ref)
        count.append(1)
        if len(count) == 2:
            entered.set()
            assert release.wait(5)
        return snapshot
    monkeypatch.setattr(LocalGatewayParentPreparation, 'read_prepared', delayed)
    failures = []
    def capture():
        try:
            threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'],
                                                   timeout_seconds=0.1)
        except Exception as error:
            failures.append(error)
    caller = threading.Thread(target=capture)
    caller.start()
    try:
        assert entered.wait(2)
        caller.join(2)
        assert not caller.is_alive() and len(failures) == 1
    finally:
        release.set()
        caller.join(2)
    # Queue a second capture: the owner must finish and discard the first
    # before it can issue this new, distinct capability.
    cap = threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    assert list(runtime._live_parent_capabilities) == [cap]
    assert peer.source_calls == 2 and not peer.registers


def test_close_during_source_revokes_before_blocked_cleanup(environment, monkeypatch):
    _, _, _, preparation, threaded, target, peer, runtime, request = environment
    source_entered, source_release = threading.Event(), threading.Event()
    cleanup_entered, cleanup_release = threading.Event(), threading.Event()
    issued, failures, closed = [], [], []
    def hold_source():
        source_entered.set()
        assert source_release.wait(5)
    peer.before_source = hold_source
    original_capture = runtime.capture_prepared_parent_source
    def capture_and_record(*args, **kwargs):
        capability = original_capture(*args, **kwargs)
        issued.append(capability)
        return capability
    original_close = runtime.close
    def hold_cleanup():
        cleanup_entered.set()
        assert cleanup_release.wait(5)
        return original_close()
    monkeypatch.setattr(runtime, 'capture_prepared_parent_source', capture_and_record)
    monkeypatch.setattr(runtime, 'close', hold_cleanup)
    def capture():
        try:
            threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
        except Exception as error:
            failures.append(error)
    def close():
        try:
            closed.append(threaded.close(timeout_seconds=5))
        except Exception as error:
            closed.append(error)
    caller = threading.Thread(target=capture)
    closer = threading.Thread(target=close)
    caller.start()
    try:
        assert source_entered.wait(2)
        closer.start()
        with threaded._condition:
            assert threaded._condition.wait_for(lambda: threaded._stop, timeout=2)
        source_release.set()
        assert cleanup_entered.wait(2)
        caller.join(2)
        assert not caller.is_alive() and len(failures) == 1
        assert isinstance(failures[0], ThreadedRuntimeError)
        assert len(issued) == 1 and issued[0]._revoked.is_set()
        assert issued[0] not in runtime._live_parent_capabilities
        assert not peer.registers
    finally:
        source_release.set()
        cleanup_release.set()
        caller.join(2)
        closer.join(5)
    assert not closer.is_alive() and len(closed) == 1 and not isinstance(closed[0], Exception)


def test_close_revokes_previously_returned_capability_before_cleanup(environment, monkeypatch):
    _, _, _, preparation, threaded, target, peer, runtime, request = environment
    cap = threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    cleanup_entered, cleanup_release = threading.Event(), threading.Event()
    original_close = runtime.close
    def hold_cleanup():
        cleanup_entered.set()
        assert cleanup_release.wait(5)
        return original_close()
    monkeypatch.setattr(runtime, 'close', hold_cleanup)
    closed = []
    def close():
        try:
            closed.append(threaded.close(timeout_seconds=5))
        except Exception as error:
            closed.append(error)
    closer = threading.Thread(target=close)
    closer.start()
    try:
        assert cleanup_entered.wait(2)
        assert cap._revoked.is_set() and cap not in runtime._live_parent_capabilities
        assert not peer.registers
    finally:
        cleanup_release.set()
        closer.join(5)
    assert not closer.is_alive() and len(closed) == 1 and not isinstance(closed[0], Exception)


def test_forked_capability_revocation_never_takes_inherited_registry_lock(environment):
    _, _, _, preparation, threaded, target, _, runtime, request = environment
    cap = threaded.capture_prepared_parent_source(target, preparation, request['spec']['parent_ref'])
    held, release = threading.Event(), threading.Event()
    def lock_registry():
        with runtime._live_parent_capabilities_lock:
            with cap._revoked._cond:
                held.set()
                release.wait(5)
    holder = threading.Thread(target=lock_registry)
    holder.start()
    try:
        assert held.wait(2)
        assert _fork_results([cap._revoke]) == ['SUCCEEDED']
        assert not cap._revoked.is_set()
    finally:
        release.set()
        holder.join(5)
    assert not holder.is_alive() and cap in runtime._live_parent_capabilities


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
