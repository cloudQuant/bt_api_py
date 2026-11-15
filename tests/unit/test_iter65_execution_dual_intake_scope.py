"""Combined strategy intake and independently issued service action policy."""
from dataclasses import replace
import sqlite3
import threading
import time
import os

import pytest
from bt_api_py.execution_parent_grant import SUBMIT, QUERY
from bt_api_py.service_registry import ServiceDispatchAdmissionError, ServiceRegistry, _ExecutionActionBinding
from tests.unit.test_service_registry import _next_request, _register
from tests.unit.test_iter65_execution_parent_registry_grant import fixture, call, KEY, OTHER
from tests.unit.test_iter65_execution_action_grant import policy, request


def issued(operation=SUBMIT):
    data = fixture(operation=operation, system_clock=True)
    registry, _, identity, scope, _, received, registration = data
    assert registration['ok']
    original = {'request_id': 'one', 'spec': {'instrument': KEY.to_dict(),
        'plan_version': 'plan1', 'account_scope': scope.account_ref,
        'strategy_scope': scope.strategy_ref, 'execution_domain_ref': scope.execution_domain_ref}}
    args = original if operation == SUBMIT else {'request_id': 'one'}
    response = registry.handle('principal-a', _next_request(operation, {
        'registration_id': registration['result']['registration_id'],
        'service_epoch': registration['result']['service_epoch'],
        'instance_id': 'exec-a', 'arguments': args}, identity=identity, scope=scope))
    assert response['ok'], response
    context, wire_args = received[-1]
    grant = replace(policy(), actions=frozenset({'accept', 'query'}))
    registry.configure_execution_actions(grants=(grant,))
    return data, context, wire_args, grant, original


def bind(registry, grant, operation=SUBMIT):
    return registry.bind_execution_action(request(grant, 'accept' if operation == SUBMIT else 'query'))


def cleanup(data, context):
    context.dispatch_admission.close()
    data[0].close()


@pytest.mark.parametrize('operation', [SUBMIT, QUERY])
def test_both_scopes_and_exact_original_facts(operation):
    data, context, args, grant, original = issued(operation)
    registry, permit = data[0], context.dispatch_admission
    try:
        binding = bind(registry, grant, operation)
        parent = args if operation == SUBMIT else original
        with permit.intake_action_scope(context, args, binding) as check:
            check(parent)
            for changed in ({**parent, 'spec': {**parent['spec'], 'plan_version': 'other'}},
                            {**parent, 'spec': {**parent['spec'], 'instrument': OTHER.to_dict()}},
                            *({**parent, 'spec': {**parent['spec'], field: 'other'}}
                              for field in ('account_scope', 'strategy_scope', 'execution_domain_ref'))):
                with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
                    check(changed)
            if operation == QUERY:
                with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
                    check({**parent, 'request_id': 'another'})
            with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                registry.revoke_execution_action(grant)
            with pytest.raises(ServiceDispatchAdmissionError, match='ADMISSION_BUSY'):
                with permit.intake_action_scope(context, args, binding):
                    pass
        with pytest.raises(ServiceDispatchAdmissionError):
            check(parent)
    finally:
        cleanup(data, context)


def test_query_old_record_requires_both_current_instrument_grants_after_narrowing():
    data, initial_context, args, grant, original = issued(QUERY)
    registry, strategy_identity, scope, parent = data[0], data[2], data[3], data[4]
    contexts = [initial_context]
    try:
        wide_action = replace(grant, instruments=frozenset({KEY, OTHER}))
        assert registry.replace_execution_action_grant(grant, wide_action)
        wide_parent = replace(parent, revision='wide', instruments=frozenset({KEY, OTHER}))
        assert registry.replace_execution_parent_grant(parent, wide_parent)
        response, _ = call(registry, strategy_identity, scope, data[6]['result'], QUERY)
        assert response['ok']
        wide_context, wide_args = data[5][-1]
        contexts.append(wide_context)
        other_request = replace(request(wide_action, 'query'), instrument=OTHER)
        old_record = {**original, 'spec': {**original['spec'], 'instrument': OTHER.to_dict()}}
        with wide_context.dispatch_admission.intake_action_scope(
                wide_context, wide_args, registry.bind_execution_action(other_request)) as check:
            check(old_record)

        narrowed_parent = replace(wide_parent, revision='narrow', instruments=frozenset({KEY}))
        assert registry.replace_execution_parent_grant(wide_parent, narrowed_parent)
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with wide_context.dispatch_admission.intake_action_scope(
                    wide_context, wide_args, registry.bind_execution_action(other_request)):
                pytest.fail('old strategy generation admitted')
        response, _ = call(registry, strategy_identity, scope, data[6]['result'], QUERY)
        assert response['ok']
        narrow_context, narrow_args = data[5][-1]
        contexts.append(narrow_context)
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with narrow_context.dispatch_admission.intake_action_scope(
                    narrow_context, narrow_args, registry.bind_execution_action(other_request)):
                pytest.fail('narrowed strategy grant admitted old instrument')
        # The remaining instrument is independently still queryable.
        with narrow_context.dispatch_admission.intake_action_scope(
                narrow_context, narrow_args, registry.bind_execution_action(request(wide_action, 'query'))) as check:
            check(original)
    finally:
        for context in contexts:
            context.dispatch_admission.close()
        registry.close()


def test_submit_requires_both_instrument_grants_and_invalidates_old_generation():
    data, initial_context, args, grant, _ = issued()
    registry, identity, scope, parent = data[0], data[2], data[3], data[4]
    contexts = [initial_context]
    try:
        wide_action = replace(grant, instruments=frozenset({KEY, OTHER}))
        assert registry.replace_execution_action_grant(grant, wide_action)
        wide_parent = replace(parent, revision='wide', instruments=frozenset({KEY, OTHER}))
        assert registry.replace_execution_parent_grant(parent, wide_parent)
        submit_args = {'request_id': 'two', 'spec': {**dict(args['spec']),
                       'instrument': OTHER.to_dict()}}
        current_registration = data[6]['result']
        def submit():
            return registry.handle('principal-a', _next_request(SUBMIT, {
                'registration_id': current_registration['registration_id'],
                'service_epoch': current_registration['service_epoch'],
                'instance_id': 'exec-a', 'arguments': submit_args}, identity=identity, scope=scope))
        assert not submit()['ok']
        assert registry.handle('principal-a', _next_request('service.unregister', {
            'registration_id': current_registration['registration_id'],
            'service_epoch': current_registration['service_epoch']}, identity=identity, scope=scope))['ok']
        replacement = _register(registry, identity=identity, scope=scope, principal='principal-a',
            instance_id='exec-a', port='service.execution_parent', scope_ref='scope-a',
            operations=(SUBMIT,), key='wide-registration')
        assert replacement['ok'], replacement
        assert replacement['result']['registration_id'] != current_registration['registration_id']
        current_registration = replacement['result']
        assert submit()['ok']
        wide_context, wide_args = data[5][-1]
        contexts.append(wide_context)
        other_action = replace(request(wide_action), instrument=OTHER)
        with wide_context.dispatch_admission.intake_action_scope(
                wide_context, wide_args, registry.bind_execution_action(other_action)) as check:
            check(wide_args)
        narrowed = replace(wide_parent, revision='narrow', instruments=frozenset({KEY}))
        assert registry.replace_execution_parent_grant(wide_parent, narrowed)
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with wide_context.dispatch_admission.intake_action_scope(
                    wide_context, wide_args, registry.bind_execution_action(other_action)):
                pytest.fail('old strategy generation admitted')
        assert not submit()['ok']
    finally:
        for context in contexts:
            context.dispatch_admission.close()
        registry.close()


@pytest.mark.parametrize('cause', ['strategy_revoke', 'principal_revoke', 'frozen', 'action_revoke', 'action_rotate', 'action_reinstall', 'expired'])
def test_before_entry_revocation_and_expiry_deny(cause):
    data, context, args, grant, _ = issued()
    registry, permit, parent = data[0], context.dispatch_admission, data[4]
    try:
        binding = bind(registry, grant)
        if cause == 'strategy_revoke':
            assert registry.revoke_execution_parent(parent)
        elif cause == 'principal_revoke':
            assert registry.revoke_principal('principal-a')
        elif cause == 'frozen':
            assert registry.freeze_strategy(data[3], 'stop-1')['status'] == 'applied'
        elif cause == 'action_revoke':
            assert registry.revoke_execution_action(grant)
        elif cause == 'action_rotate':
            assert registry.replace_execution_action_grant(grant, replace(grant, revision='v2'))
        elif cause == 'action_reinstall':
            assert registry.revoke_execution_action(grant)
            registry.configure_execution_actions(grants=(grant,))
        else:
            with registry._transaction():
                registry._db.execute('UPDATE sessions SET expires_at=?', (time.time() - 1,))
        with pytest.raises(ServiceDispatchAdmissionError):
            with permit.intake_action_scope(context, args, binding):
                pytest.fail('stale authorization entered')
    finally:
        cleanup(data, context)


@pytest.mark.parametrize('mismatch', ['instrument', 'plan'])
def test_submit_rejects_action_binding_for_different_allowed_parent(mismatch):
    data, context, args, grant, _ = issued()
    registry, permit = data[0], context.dispatch_admission
    try:
        wider = replace(grant, instruments=frozenset({KEY, OTHER}),
                        plan_versions=frozenset({'plan1', 'plan2'}))
        assert registry.replace_execution_action_grant(grant, wider)
        action_request = request(wider)
        action_request = (replace(action_request, instrument=OTHER) if mismatch == 'instrument'
                          else replace(action_request, plan_version='plan2'))
        binding = registry.bind_execution_action(action_request)
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with permit.intake_action_scope(context, args, binding):
                pytest.fail('wrong parent facts admitted')
    finally:
        cleanup(data, context)


def test_expiry_at_final_check_and_no_sql_under_journal_lock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(ServiceRegistry, '_execution_parent_mono_now', lambda self: now[0])
    data, context, args, grant, _ = issued()
    registry, permit = data[0], context.dispatch_admission
    deadline = registry._execution_parent_monotonic_leases[context.registration_id][5]
    statements = []
    journal = sqlite3.connect(':memory:', isolation_level=None)
    journal.execute('CREATE TABLE parents (request_id TEXT PRIMARY KEY)')
    try:
        binding = bind(registry, grant)
        with permit.intake_action_scope(context, args, binding) as check:
            registry._db.set_trace_callback(statements.append)
            journal.execute('BEGIN IMMEDIATE')
            try:
                journal.execute('INSERT INTO parents VALUES (?)', ('request-a',))
                assert journal.execute('SELECT COUNT(*) FROM parents').fetchone()[0] == 1
                check(args)
                now[0] = deadline
                with pytest.raises(ServiceDispatchAdmissionError, match='NOT_REGISTERED'):
                    check(args)
            finally:
                journal.rollback()
        assert statements == ['COMMIT']
        assert journal.execute('SELECT COUNT(*) FROM parents').fetchone()[0] == 0
    finally:
        registry._db.set_trace_callback(None)
        cleanup(data, context)
        journal.close()


def test_fake_wrong_registry_scope_operation_and_context_denied():
    data, context, args, grant, _ = issued()
    registry, permit = data[0], context.dispatch_admission
    other_data, other_context, other_args, other_grant, _ = issued()
    try:
        good = bind(registry, grant)
        other = bind(other_data[0], other_grant)
        fake = _ExecutionActionBinding(registry, request(grant), grant, 1)
        for candidate in (None, object(), fake, other):
            with pytest.raises(ServiceDispatchAdmissionError):
                with permit.intake_action_scope(context, args, candidate):
                    pass
        for altered in (replace(context), other_context):
            with pytest.raises(ServiceDispatchAdmissionError):
                permit.intake_action_scope(altered, args, good)
        with pytest.raises(ServiceDispatchAdmissionError):
            permit.intake_action_scope(context, dict(args), good)
        for field in ('instance_id', 'account_ref', 'strategy_ref', 'execution_domain_ref'):
            mismatch = replace(grant, **{field: 'different'})
            registry.configure_execution_actions(grants=(mismatch,))
            with pytest.raises(ServiceDispatchAdmissionError):
                with permit.intake_action_scope(context, args, bind(registry, mismatch)):
                    pass
        wrong_action = bind(registry, grant, QUERY)
        with pytest.raises(ServiceDispatchAdmissionError):
            with permit.intake_action_scope(context, args, wrong_action):
                pass
    finally:
        cleanup(other_data, other_context)
        cleanup(data, context)


def test_binding_from_another_thread_cannot_enter():
    data, context, args, grant, _ = issued()
    registry, permit = data[0], context.dispatch_admission
    errors = []
    try:
        binding = bind(registry, grant)
        def attempt():
            try:
                with permit.intake_action_scope(context, args, binding):
                    errors.append('entered')
            except ServiceDispatchAdmissionError as error:
                errors.append(str(error))
        worker = threading.Thread(target=attempt)
        worker.start()
        worker.join(3)
        assert not worker.is_alive() and errors == ['SCOPE_DENIED']
    finally:
        cleanup(data, context)


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork unavailable')
def test_combined_scope_rejects_fork_reuse():
    data, context, args, grant, _ = issued()
    registry, permit = data[0], context.dispatch_admission
    binding = bind(registry, grant)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            with permit.intake_action_scope(context, args, binding):
                os.write(write_fd, b'ALLOWED')
        except ServiceDispatchAdmissionError:
            os.write(write_fd, b'DENIED')
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        assert os.read(read_fd, 64) == b'DENIED'
        assert os.waitpid(pid, 0)[1] == 0
    finally:
        os.close(read_fd)
        cleanup(data, context)


@pytest.mark.parametrize('which', ['strategy', 'action'])
def test_commit_and_revoke_linearization(tmp_path, which):
    data, context, args, grant, _ = issued()
    registry, permit = data[0], context.dispatch_admission
    path = tmp_path / 'journal.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE journal (n INTEGER)')
    entered, release, attempted, done = (threading.Event() for _ in range(4))
    errors = []

    def commit():
        try:
            binding = bind(registry, grant)
            with permit.intake_action_scope(context, args, binding) as check:
                with sqlite3.connect(path) as db:
                    db.execute('INSERT INTO journal VALUES (1)')
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError('release timed out')
                    check(args)
        except BaseException as exc:
            errors.append(exc)

    def revoke():
        try:
            attempted.set()
            assert (registry.revoke_execution_parent(data[4]) if which == 'strategy'
                    else registry.revoke_execution_action(grant))
            done.set()
        except BaseException as exc:
            errors.append(exc)

    worker, revoker = threading.Thread(target=commit), threading.Thread(target=revoke)
    try:
        worker.start()
        assert entered.wait(3)
        revoker.start()
        assert attempted.wait(3)
        assert not done.wait(.1)
        release.set()
        worker.join(3)
        revoker.join(3)
        assert not worker.is_alive() and not revoker.is_alive() and not errors
        assert done.is_set()
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT COUNT(*) FROM journal').fetchone()[0] == 1
        # Revoke-first order: a new attempt cannot begin any journal work.
        denied = []
        def retry():
            try:
                binding = bind(registry, grant)
                with permit.intake_action_scope(context, args, binding):
                    denied.append('entered')
            except ServiceDispatchAdmissionError:
                denied.append('denied')
        next_worker = threading.Thread(target=retry)
        next_worker.start()
        next_worker.join(3)
        assert not next_worker.is_alive() and denied == ['denied']
    finally:
        release.set()
        worker.join(3)
        if revoker.ident is not None:
            revoker.join(3)
        cleanup(data, context)
