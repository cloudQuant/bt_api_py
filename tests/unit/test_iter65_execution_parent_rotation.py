"""Registry-issued parent grants and trusted local CAS rotation."""
from dataclasses import replace
import os
import threading

import pytest
from bt_api_py.execution_parent_grant import SUBMIT, QUERY
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmissionError
from tests.unit.test_iter65_execution_parent_registry_grant import fixture, call, _other_grant


def issued(*, system_clock=False, two=False):
    data = fixture(system_clock=system_clock, two=two)
    registry, _, identity, scope, grant, received, response = data
    assert response['ok']
    assert call(registry, identity, scope, response['result'])[0]['ok']
    context, arguments = received[-1]
    return data, context, arguments, context.dispatch_admission


def test_issuance_grant_requires_exact_context_arguments_and_operation():
    data, context, args, permit = issued()
    registry, _, _, _, grant = data[:5]
    try:
        assert permit.execution_parent_grant_for(context, SUBMIT, args) is grant
        assert permit.execution_parent_grant_for(replace(context), SUBMIT, args) is None
        assert permit.execution_parent_grant_for(context, SUBMIT, dict(args)) is None
        assert permit.execution_parent_grant_for(context, QUERY, args) is None
        assert permit.execution_parent_grant_for(object(), SUBMIT, args) is None
        assert permit.execution_parent_grant_for(context, object(), args) is None
        fake = ServiceCallContext(context.identity, context.scope, context.registration_id,
                                  context.principal_ref, context.result_fits, permit)
        assert permit.execution_parent_grant_for(fake, SUBMIT, args) is None
        permit.close()
        assert permit.execution_parent_grant_for(context, SUBMIT, args) is None
    finally:
        permit.close()
        registry.close()


def test_gateway_permit_cannot_return_execution_grant():
    from tests.unit.test_iter63_registry_dispatch_admission import _fixture
    registry, _, _, _, _, _, context = _fixture()
    try:
        assert context.dispatch_admission.execution_parent_grant_for(
            context, 'direct.dispatch', context._wire_arguments[1]) is None
    finally:
        context.dispatch_admission.close()
        registry.close()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork unavailable')
def test_fork_does_not_receive_parent_issuance_grant():
    data, context, args, permit = issued()
    registry = data[0]
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            os.close(read_fd)
            result = permit.execution_parent_grant_for(context, SUBMIT, args)
            os.write(write_fd, b'NONE' if result is None else b'GRANT')
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        assert os.read(read_fd, 16) == b'NONE'
        assert os.waitpid(child, 0)[1] == 0
    finally:
        os.close(read_fd)
        permit.close()
        registry.close()


def test_cas_rotation_invalidates_old_permit_and_new_permit_sees_replacement():
    data, context, args, old = issued()
    registry, _, identity, scope, grant, received, response = data
    newer = replace(grant, revision='v2')
    try:
        assert registry.replace_execution_parent_grant(grant, newer) is True
        # The getter exposes issuance, not current policy; scope does the recheck.
        assert old.execution_parent_grant_for(context, SUBMIT, args) is grant
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with old.scope():
                pass
        assert registry.replace_execution_parent_grant(grant, replace(grant, revision='v3')) is False
        assert call(registry, identity, scope, response['result'])[0]['ok']
        new_context, new_args = received[-1]
        current = new_context.dispatch_admission
        assert current.execution_parent_grant_for(new_context, SUBMIT, new_args) is newer
        with current.scope() as check:
            check()
        current.close()
    finally:
        old.close()
        registry.close()


def test_rotation_to_same_object_and_revoke_reinstall_do_not_revive_old_permits():
    data, context, args, old = issued()
    registry, _, identity, scope, grant, received, response = data
    try:
        assert registry.replace_execution_parent_grant(grant, grant)
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with old.scope():
                pass
        assert call(registry, identity, scope, response['result'])[0]['ok']
        next_permit = received[-1][0].dispatch_admission
        assert registry.revoke_execution_parent(grant)
        registry.configure_execution_parents(grants=(grant,))
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with next_permit.scope():
                pass
        next_permit.close()
    finally:
        old.close()
        registry.close()


@pytest.mark.parametrize('field,value', [
    ('instance_id', 'other'), ('scope_ref', 'other'), ('account_ref', 'other'),
    ('strategy_ref', 'other'), ('execution_domain_ref', 'other'),
])
def test_cas_rejects_different_key(field, value):
    data, _, _, permit = issued()
    registry, _, _, _, grant = data[:5]
    try:
        with pytest.raises(ValueError, match='key changed'):
            registry.replace_execution_parent_grant(grant, replace(grant, **{field: value}))
        with permit.scope() as check:
            check()
    finally:
        permit.close()
        registry.close()


def test_cas_rejects_other_actor_even_when_principal_matches():
    data, _, _, permit = issued()
    registry, _, _, _, grant = data[:5]
    try:
        another = replace(grant, identity=replace(grant.identity, actor_ref='different-actor'))
        with pytest.raises(ValueError, match='key changed'):
            registry.replace_execution_parent_grant(grant, another)
    finally:
        permit.close()
        registry.close()


def test_cas_other_strategy_change_does_not_invalidate_first():
    data, _, _, first = issued(two=True)
    registry = data[0]
    _, _, other = _other_grant()
    try:
        registry.configure_execution_parents(grants=(other,))
        assert registry.replace_execution_parent_grant(other, replace(other, revision='v2'))
        with first.scope() as check:
            check()
    finally:
        first.close()
        registry.close()


def test_cas_waits_for_scope_and_invalidates_after_exit():
    data, _, _, permit = issued()
    registry, _, _, _, grant = data[:5]
    attempted, finished = threading.Event(), threading.Event()
    results = []
    def rotate():
        attempted.set()
        results.append(registry.replace_execution_parent_grant(grant, replace(grant, revision='v2')))
        finished.set()
    thread = threading.Thread(target=rotate)
    try:
        with permit.scope() as check:
            thread.start()
            assert attempted.wait(2) and not finished.wait(.05)
            check()
        thread.join(2)
        assert not thread.is_alive() and results == [True]
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with permit.scope():
                pass
    finally:
        thread.join(2)
        permit.close()
        registry.close()


def test_cas_reentry_is_refused_inside_intake_scope():
    data, _, _, permit = issued(system_clock=True)
    registry, _, _, _, grant = data[:5]
    try:
        with permit.intake_scope() as check:
            with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                registry.replace_execution_parent_grant(grant, replace(grant, revision='v2'))
            check()
        assert registry.replace_execution_parent_grant(grant, replace(grant, revision='v2'))
    finally:
        permit.close()
        registry.close()


def test_getter_requires_live_registry_issuance():
    data, context, args, permit = issued()
    registry = data[0]
    try:
        with registry._lock:
            del registry._dispatch_admissions[permit]
        assert permit.execution_parent_grant_for(context, SUBMIT, args) is None
    finally:
        permit.close()
        registry.close()


def test_getter_does_not_treat_expired_registration_as_current_authority():
    data, context, args, permit = issued()
    registry, clock, _, _, grant = data[:5]
    try:
        clock[0] += 10
        assert permit.execution_parent_grant_for(context, SUBMIT, args) is grant
        with pytest.raises(ServiceDispatchAdmissionError, match='NOT_REGISTERED'):
            with permit.scope():
                pass
    finally:
        permit.close()
        registry.close()


def test_cas_rejects_non_grant_values_without_changing_installed_grant():
    data, _, _, permit = issued()
    registry, _, identity, scope, grant = data[:5]
    try:
        with pytest.raises(ValueError):
            registry.replace_execution_parent_grant(None, grant)
        with pytest.raises(ValueError):
            registry.replace_execution_parent_grant(grant, None)
        assert registry.execution_parent_grant(instance_id=grant.instance_id, scope_ref=grant.scope_ref,
            identity=identity, scope=scope, operation=SUBMIT) is grant
    finally:
        permit.close()
        registry.close()
