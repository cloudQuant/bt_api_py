"""Local Registry policy and revocation ordering; no external account claims."""
from dataclasses import replace
import sqlite3
import threading
import os

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.execution_action_grant import ExecutionActionGrant, ExecutionActionRequest
from bt_api_py.service_registry import ServiceDispatchAdmissionError
from tests.unit.test_iter65_execution_parent_registry_grant import fixture, KEY, OTHER


SERVICE = WireIdentity(WireRole.SERVICE, 'exec-principal', 'exec-service', None)


def policy():
    return ExecutionActionGrant('exec-a', SERVICE, 'account-a', 'strategy-a',
        'live:venue-a', 'v1', frozenset({KEY}), frozenset({'plan1'}),
        frozenset({'accept', 'dispatch_child'}))


def request(grant, action='accept'):
    return ExecutionActionRequest(grant.instance_id, grant.identity, grant.account_ref,
        grant.strategy_ref, grant.execution_domain_ref, grant.revision, KEY, 'plan1', action)


def test_parent_client_grant_never_creates_action_authority():
    registry, _, identity, _, parent, *_ = fixture()
    grant = policy()
    try:
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            registry.bind_execution_action(request(grant))
        with pytest.raises(ValueError, match='INVALID_EXECUTION_ACTION_GRANT'):
            replace(grant, identity=identity)
        with pytest.raises(ValueError, match='INVALID_EXECUTION_ACTION_REQUEST'):
            replace(request(grant), identity=identity)
        assert parent.identity.role is WireRole.STRATEGY_CLIENT
    finally:
        registry.close()


def test_exact_identity_scope_instrument_version_and_independent_actions():
    registry, *_ = fixture()
    grant = policy()
    try:
        registry.configure_execution_actions(grants=(grant,))
        req = request(grant)
        binding = registry.bind_execution_action(req)
        with binding.scope() as check:
            check()
            with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                registry.revoke_execution_action(grant)
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            check()
        variants = [replace(req, identity=replace(SERVICE, actor_ref='other')),
                    replace(req, instance_id='other'), replace(req, account_ref='other'),
                    replace(req, strategy_ref='other'), replace(req, execution_domain_ref='other'),
                    replace(req, grant_revision='v2'), replace(req, instrument=OTHER),
                    replace(req, plan_version='plan2')]
        variants += [replace(req, action=action) for action in ('query', 'cancel', 'reconcile', 'child')]
        for other in variants:
            with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
                registry.bind_execution_action(other)
        with pytest.raises(ValueError, match='INVALID_EXECUTION_ACTION_REQUEST'):
            replace(req, action='arbitrary')
    finally:
        registry.close()


def test_cas_and_revocation_invalidate_delayed_binding_even_after_reinstall():
    registry, *_ = fixture()
    grant = policy()
    try:
        registry.configure_execution_actions(grants=(grant,))
        old = registry.bind_execution_action(request(grant))
        new = replace(grant, revision='v2', actions=frozenset({'cancel'}))
        assert registry.replace_execution_action_grant(grant, new)
        assert not registry.replace_execution_action_grant(grant, new)
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'), old.scope():
            pass
        with registry.bind_execution_action(request(new, 'cancel')).scope() as check:
            check()
        assert registry.revoke_execution_action(new)
        registry.configure_execution_actions(grants=(grant,))
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'), old.scope():
            pass
        with pytest.raises(ValueError, match='key changed'):
            registry.replace_execution_action_grant(grant, replace(grant, account_ref='other'))
    finally:
        registry.close()


def test_local_commit_linearizes_before_waiting_revoke(tmp_path):
    registry, *_ = fixture()
    grant = policy()
    registry.configure_execution_actions(grants=(grant,))
    entered, release, revoke_started, revoked = (threading.Event() for _ in range(4))
    errors = []
    db_path = tmp_path / 'local.db'
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE commits (value INTEGER)')

    def commit():
        try:
            binding = registry.bind_execution_action(request(grant))
            with binding.scope() as check:
                with sqlite3.connect(db_path) as db:
                    db.execute('INSERT INTO commits VALUES (1)')
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError('test release timed out')
                    check()
        except BaseException as error:
            errors.append(error)

    def revoke():
        try:
            revoke_started.set()
            assert registry.revoke_execution_action(grant)
            revoked.set()
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=commit)
    revoker = threading.Thread(target=revoke)
    try:
        worker.start()
        assert entered.wait(3)
        revoker.start()
        assert revoke_started.wait(3)
        assert not revoked.wait(.1)
        release.set()
        worker.join(3)
        revoker.join(3)
        assert not worker.is_alive() and not revoker.is_alive() and not errors
        assert revoked.is_set()
        with sqlite3.connect(db_path) as db:
            assert db.execute('SELECT COUNT(*) FROM commits').fetchone()[0] == 1
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            registry.bind_execution_action(request(grant))
    finally:
        release.set()
        worker.join(3)
        if revoker.ident is not None:
            revoker.join(3)
        registry.close()


def test_binding_thread_restriction_and_atomic_duplicate_install():
    registry, *_ = fixture()
    grant = policy()
    other = replace(grant, account_ref='other')
    try:
        with pytest.raises(ValueError, match='duplicate'):
            registry.configure_execution_actions(grants=(grant, grant))
        registry.configure_execution_actions(grants=(grant,))
        with pytest.raises(ValueError, match='duplicate'):
            registry.configure_execution_actions(grants=(other, grant))
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            registry.bind_execution_action(request(other))
        binding = registry.bind_execution_action(request(grant))
        errors = []
        def cross_thread():
            try:
                with binding.scope():
                    pass
            except ServiceDispatchAdmissionError as error:
                errors.append(str(error))
        worker = threading.Thread(target=cross_thread)
        worker.start()
        worker.join(3)
        assert not worker.is_alive() and errors == ['WRONG_OWNER_THREAD']
    finally:
        registry.close()


def test_read_cancel_and_reconcile_are_separately_installable():
    registry, *_ = fixture()
    grant = replace(policy(), actions=frozenset({'query', 'cancel', 'reconcile'}))
    try:
        registry.configure_execution_actions(grants=(grant,))
        for action in grant.actions:
            with registry.bind_execution_action(request(grant, action)).scope() as check:
                check()
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            registry.bind_execution_action(request(grant, 'accept'))
    finally:
        registry.close()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork unavailable')
def test_inherited_binding_cannot_use_registry_in_child():
    registry, *_ = fixture()
    grant = policy()
    registry.configure_execution_actions(grants=(grant,))
    binding = registry.bind_execution_action(request(grant))
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            with binding.scope():
                os.write(write_fd, b'ALLOWED')
        except ServiceDispatchAdmissionError as error:
            os.write(write_fd, str(error).encode('ascii'))
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        assert os.read(read_fd, 64) == b'WRONG_PROCESS'
        assert os.waitpid(pid, 0)[1] == 0
    finally:
        os.close(read_fd)
        registry.close()
