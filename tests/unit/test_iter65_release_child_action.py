"""Local release policy only: it does not claim an order or authorize a send."""

from dataclasses import replace
import sqlite3
import threading

import pytest

from bt_api_py.service_registry import ServiceDispatchAdmissionError
from tests.unit.test_iter65_execution_action_grant import policy, request
from tests.unit.test_iter65_execution_parent_registry_grant import fixture, OTHER


def _release_policy():
    return replace(policy(), actions=frozenset({'release_child'}))


def _release_request(grant):
    return request(grant, 'release_child')


def test_release_child_is_separate_from_cancel_and_release_proof():
    registry, *_ = fixture()
    grant = replace(policy(), actions=frozenset({'cancel', 'release_proof'}))
    try:
        # A missing or differently scoped policy is never a release permit.
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            registry.bind_execution_action(_release_request(grant))
        registry.configure_execution_actions(grants=(grant,))
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            registry.bind_execution_action(_release_request(grant))
        replacement = _release_policy()
        assert registry.replace_execution_action_grant(grant, replacement)
        grant = replacement
        with registry.bind_execution_action(_release_request(grant)).scope() as check:
            check()
        for other in ('cancel', 'release_proof', 'dispatch_child'):
            with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
                registry.bind_execution_action(request(grant, other))
    finally:
        registry.close()


def test_release_policy_checks_identity_scope_and_survives_strategy_disconnect():
    registry, clock, _, _, _, _, registration = fixture()
    grant = _release_policy()
    try:
        assert registration['ok']
        registry.configure_execution_actions(grants=(grant,))
        # This local service policy does not require an online strategy session.
        # It also does not establish the service's authenticated registration.
        clock[0] += 11
        assert not registry.active_registration_ids(instance_id='exec-a')
        req = _release_request(grant)
        with registry.bind_execution_action(req).scope() as check:
            check()
        for variant in (
            replace(req, instance_id='other'),
            replace(req, identity=replace(req.identity, actor_ref='other')),
            replace(req, account_ref='other'),
            replace(req, strategy_ref='other'),
            replace(req, execution_domain_ref='other'),
            replace(req, grant_revision='other'),
            replace(req, instrument=OTHER),
            replace(req, plan_version='other'),
        ):
            with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
                registry.bind_execution_action(variant)
    finally:
        registry.close()


def test_release_binding_revoke_reinstall_is_not_a_new_authorization():
    registry, *_ = fixture()
    grant = _release_policy()
    try:
        registry.configure_execution_actions(grants=(grant,))
        binding = registry.bind_execution_action(_release_request(grant))
        assert registry.revoke_execution_action(grant)
        registry.configure_execution_actions(grants=(grant,))
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with binding.scope():
                pass
        with registry.bind_execution_action(_release_request(grant)).scope() as check:
            check()
    finally:
        registry.close()


def test_local_policy_scope_serializes_revoke_and_preserves_authorizer(tmp_path):
    registry, *_ = fixture()
    grant = _release_policy()
    registry.configure_execution_actions(grants=(grant,))
    entered, resume, revoke_started, revoked = (threading.Event() for _ in range(4))
    errors = []
    path = tmp_path / 'local-release-policy-test.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE entries (value INTEGER)')

    def guarded_transaction():
        try:
            binding = registry.bind_execution_action(_release_request(grant))
            with sqlite3.connect(path) as connection:
                connection.set_authorizer(lambda action, *_: (
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DELETE else sqlite3.SQLITE_OK
                ))
                with binding.scope() as check:
                    connection.execute('INSERT INTO entries VALUES (1)')
                    entered.set()
                    if not resume.wait(3):
                        raise AssertionError('revoke test timed out')
                    check()
                    connection.commit()
                with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
                    connection.execute('DELETE FROM entries')
        except BaseException as exc:
            errors.append(exc)

    def revoke():
        try:
            revoke_started.set()
            assert registry.revoke_execution_action(grant)
            revoked.set()
        except BaseException as exc:
            errors.append(exc)

    transaction = threading.Thread(target=guarded_transaction)
    revoker = threading.Thread(target=revoke)
    try:
        transaction.start()
        assert entered.wait(3)
        revoker.start()
        assert revoke_started.wait(3)
        assert not revoked.wait(.1)
        resume.set()
        transaction.join(3)
        revoker.join(3)
        assert not transaction.is_alive() and not revoker.is_alive() and not errors
        assert revoked.is_set()
        with sqlite3.connect(path) as connection:
            assert connection.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 1
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            registry.bind_execution_action(_release_request(grant))
    finally:
        resume.set()
        transaction.join(3)
        if revoker.ident is not None:
            revoker.join(3)
        registry.close()
