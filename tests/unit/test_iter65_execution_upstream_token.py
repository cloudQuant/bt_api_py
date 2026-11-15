"""Local synthetic host tests; fake transport proof is not CURVE evidence."""
from dataclasses import replace
import pytest

from bt_api_execution.submission_intake import FreshSubmissionIntake
from bt_api_py.execution_parent_grant import SUBMIT, QUERY, PORT
from tests.unit.test_iter65_fresh_intake_host import build_host, call, parent_payload


@pytest.mark.parametrize('kind', ['same_binding_deadline', 'peer_identity', 'peer_evil', 'proof_replaced', 'evil_deadline'])
def test_frozen_upstream_token_rejects_in_transaction_mutation(kind, tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch)
    restore = []
    inside_callbacks = []
    changed = []
    class Injected:
        def __le__(self, other):
            inside_callbacks.append(intake._db.in_transaction)
            return True
        def __eq__(self, other):
            inside_callbacks.append(intake._db.in_transaction)
            return False
    try:
        host.start()
        registered = call(host, 'service.register', {'registration_key': 'token-test',
            'client_session_id': 'token-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok']
        original = FreshSubmissionIntake._verify_files
        def mutate(instance):
            result = original(instance)
            if instance is not intake or not instance._db.in_transaction or changed:
                return result
            changed.append(True)
            runtime = host._upstream
            binding = runtime._bindings[0]
            if kind in {'same_binding_deadline', 'evil_deadline'}:
                old = binding.lease_deadline
                restore.append(lambda: object.__setattr__(binding, 'lease_deadline', old))
                object.__setattr__(binding, 'lease_deadline', 0.0 if kind == 'same_binding_deadline' else Injected())
            elif kind in {'peer_identity', 'peer_evil'}:
                peer = runtime._peers[binding.service_id]
                old = peer.actor_ref
                restore.append(lambda: object.__setattr__(peer, 'actor_ref', old))
                object.__setattr__(peer, 'actor_ref', 'mutated-peer' if kind == 'peer_identity' else Injected())
            else:
                key = (binding.service_id, binding.transport_ref, binding.registration_id, binding.service_epoch)
                old = runtime._authenticated_registration_peers[key]
                restore.append(lambda: runtime._authenticated_registration_peers.__setitem__(key, old))
                runtime._authenticated_registration_peers[key] = (old[0], old[1])
            return result
        monkeypatch.setattr(FreshSubmissionIntake, '_verify_files', mutate)
        reg = registered['result']
        result = call(host, SUBMIT, {'registration_id': reg['registration_id'],
            'service_epoch': reg['service_epoch'], 'instance_id': 'exec-a', 'arguments': parent_payload()})
        assert changed and not result['ok']
        assert intake._db.execute('SELECT COUNT(*) FROM received').fetchone()[0] == 0
        assert not any(inside_callbacks)
    finally:
        for reset in reversed(restore):
            reset()
        host.close()
        intake.close()
        gateway.close()


def test_token_final_check_does_not_call_runtime_authentication(tmp_path, monkeypatch):
    from bt_api_py.service_runtime import ServiceClientRuntime
    host, intake, _, gateway = build_host(tmp_path, monkeypatch)
    runtime_auth = ServiceClientRuntime.authenticated_peer_for_binding
    inside_calls = []
    try:
        host.start()
        registration = call(host, 'service.register', {'registration_key': 'no-callback',
            'client_session_id': 'no-callback-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registration['ok']
        original = FreshSubmissionIntake._verify_files
        patched = []
        def observe(instance):
            result = original(instance)
            if instance is intake and instance._db.in_transaction and not patched:
                patched.append(True)
                def monitored(runtime, binding):
                    inside_calls.append(intake._db.in_transaction)
                    return runtime_auth(runtime, binding)
                monkeypatch.setattr(ServiceClientRuntime, 'authenticated_peer_for_binding', monitored)
            return result
        monkeypatch.setattr(FreshSubmissionIntake, '_verify_files', observe)
        reg = registration['result']
        result = call(host, SUBMIT, {'registration_id': reg['registration_id'],
            'service_epoch': reg['service_epoch'], 'instance_id': 'exec-a', 'arguments': parent_payload()})
        assert patched and result['ok'] and result['result']['status'] == 'RECEIVED'
        assert not any(inside_calls)
    finally:
        host.close()
        intake.close()
        gateway.close()
