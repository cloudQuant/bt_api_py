"""Runtime principal on the Gateway control port (D66-08 / 63.12).

Synthetic Registry, owner and journal; no CTP, account or network. The Runtime
is a limited proxy: strategy-scoped, only apply/status/resume, only its own
causes, and only under an explicit unexpired proof.
"""
from __future__ import annotations

import hashlib
import json
import logging
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import DirectCommandJournalError

from bt_api_py.gateway_direct_service import (
    CONTROL_MODES,
    CONTROL_RUNTIME_OPERATIONS,
    CONTROL_WIRE_SCHEMA_VERSION,
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
    GatewayRuntimeControlProof,
)
from bt_api_py.service_registry import (
    _GATEWAY_CONTROL_OPERATIONS,
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
)
from tests.test_iter63_gateway_direct_owner import Harness
from tests.unit.test_iter63_gateway_cancel_owner import owner_for
from tests.unit.test_service_registry import _next_request, _register

RB = {'symbol': 'rb2701', 'exchange_id': 'SHFE', 'side': 'buy', 'offset': 'open', 'quantity': '1'}
ACCOUNT = WireScope('test-domain', account_ref='account')
STRATEGY = WireScope('test-domain', account_ref='account', strategy_ref='strategy-a')
OTHER_STRATEGY = WireScope('test-domain', account_ref='account', strategy_ref='strategy-b')
OPERATOR_OPERATIONS = frozenset(
    {'direct.control.identity', 'direct.control.apply', 'direct.control.status',
     'direct.control.list', 'direct.control.resume'}
)
RUNTIME_WRITES = frozenset({'direct.control.apply', 'direct.control.resume'})


def origin(kind, principal, actor):
    return kind + ':' + hashlib.sha256(
        json.dumps([principal, actor], separators=(',', ':')).encode()).hexdigest()


def runtime_grant(principal, *, expires_at=200.0, revoked=False, strategies=frozenset({'strategy-a'})):
    return GatewayControlGrant(
        'runtime-actor', False, frozenset(strategies), CONTROL_RUNTIME_OPERATIONS,
        kind='runtime', runtime_proof=GatewayRuntimeControlProof(
            'delegation-' + principal, 'operator-a', expires_at, revoked=revoked))


CALLERS = {
    'ops-acct': (
        WireIdentity(WireRole.APPLICATION_CLIENT, 'ops-acct', 'operator-a'), ACCOUNT,
        GatewayControlGrant('operator-a', True, frozenset(), OPERATOR_OPERATIONS)),
    'runtime-a': (
        WireIdentity(WireRole.APPLICATION_CLIENT, 'runtime-a', 'runtime-actor'), STRATEGY,
        runtime_grant('runtime-a')),
    'runtime-b': (
        WireIdentity(WireRole.APPLICATION_CLIENT, 'runtime-b', 'runtime-actor'), STRATEGY,
        runtime_grant('runtime-b')),
    'runtime-expired': (
        WireIdentity(WireRole.APPLICATION_CLIENT, 'runtime-expired', 'runtime-actor'), STRATEGY,
        runtime_grant('runtime-expired', expires_at=99.0)),
    'runtime-revoked': (
        WireIdentity(WireRole.APPLICATION_CLIENT, 'runtime-revoked', 'runtime-actor'), STRATEGY,
        runtime_grant('runtime-revoked', revoked=True)),
    'runtime-elsewhere': (
        WireIdentity(WireRole.APPLICATION_CLIENT, 'runtime-elsewhere', 'runtime-actor'),
        OTHER_STRATEGY, runtime_grant('runtime-elsewhere')),
}
ALL_OPERATIONS = frozenset().union(*(grant.operations for _, _, grant in CALLERS.values()))
WRITES = ALL_OPERATIONS & RUNTIME_WRITES


@pytest.fixture
def port(tmp_path):
    h = Harness(tmp_path)
    owner = owner_for(h.factory)
    assert owner.start(2)
    registry = None
    try:
        control = owner.submit_service_call(lambda: h.journal.enable_controls(h.guard)).result(timeout_seconds=2)
        binding = GatewayDirectServiceBinding(
            h.journal, {'strategy-p': GatewayDirectGrant('strategy-a', frozenset({'rb2701'}))},
            owner=owner, control=control, control_clock=lambda: 100.0,
            control_grants={name: grant for name, (_, _, grant) in CALLERS.items()})
        unconfigured = GatewayDirectServiceBinding(
            h.journal, {'strategy-p': GatewayDirectGrant('strategy-a', frozenset({'rb2701'}))},
            owner=owner, control_clock=lambda: 100.0)
        capability = ServiceCapability('gateway-a', 'service.direct_write', ALL_OPERATIONS, WRITES)
        registry = ServiceRegistry(
            ':memory:', service_id='gateway-service', capabilities=(capability,),
            grants={name: (PrincipalGrant(identity, (ResourceGrant(
                'gateway-a', 'service.direct_write', 'scope-a', scope, grant.operations),)),)
                for name, (identity, scope, grant) in CALLERS.items()},
            policy_version='policy', requirements_version='requirements', lease_seconds=10,
            clock=lambda: 100.0)
        for operation in ALL_OPERATIONS:
            registry.register_handler('gateway-a', operation,
                                      lambda ctx, args, op=operation: binding.handle(op, context=ctx, arguments=args))
        records = {}
        for name, (identity, scope, grant) in CALLERS.items():
            registered = _register(registry, principal=name, identity=identity, scope=scope,
                                   instance_id='gateway-a', port='service.direct_write', scope_ref='scope-a',
                                   operations=tuple(grant.operations))
            assert registered['ok'], registered
            records[name] = registered['result']

        def call(name, operation, arguments):
            identity, scope, _ = CALLERS[name]
            return registry.handle(name, _next_request(operation, {
                'registration_id': records[name]['registration_id'],
                'service_epoch': records[name]['service_epoch'], 'instance_id': 'gateway-a',
                'arguments': arguments}, identity=identity, scope=scope))

        def on_owner(callback):
            return owner.submit_service_call(callback).result(timeout_seconds=2)

        yield SimpleNamespace(call=call, on_owner=on_owner, h=h, control=control,
                             binding=binding, unconfigured=unconfigured)
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


def cause(port, cause_id):
    return port.on_owner(lambda: port.control.get(cause_id))


# -- D66-08 positive path -----------------------------------------------------------------------

def test_runtime_applies_a_scoped_stop_new_and_reads_status(port, caplog):
    caplog.set_level(logging.WARNING, logger='bt_api_py.gateway_direct_service')
    reply = port.call('runtime-a', 'direct.control.apply',
                      {'cause_id': 'rt-stop', 'mode': 'STOP_NEW'})
    assert reply['result'] == {'status': 'OBSERVED', 'cause_id': 'rt-stop', 'version': 1,
                               'active': True}, reply
    stored = cause(port, 'rt-stop')
    assert (stored['scope_kind'], stored['scope_value'], stored['mode']) == (
        'strategy', 'strategy-a', 'STOP_NEW')
    assert stored['origin'] == origin('runtime', 'runtime-a', 'runtime-actor')
    with pytest.raises(DirectCommandJournalError, match='^CONTROL_STOP_NEW$'):
        port.h.journal.reserve('strategy-a', 'rb-open', 'strategy-p', RB)
    line, = [record.getMessage() for record in caplog.records if 'control apply' in record.getMessage()]
    assert line.startswith(
        'Runtime control apply: cause rt-stop (STOP_NEW, scope strategy strategy-a) version 1 '
        'by principal runtime-a actor runtime-actor registration ')
    assert ' delegation delegation-runtime-a operator operator-a' in line
    # The same cause id is idempotent; status names the same cause.
    assert port.call('runtime-a', 'direct.control.apply',
                     {'cause_id': 'rt-stop', 'mode': 'STOP_NEW'})['result'] == reply['result']
    status = port.call('runtime-a', 'direct.control.status', {'cause_id': 'rt-stop'})['result']
    assert status['status'] == 'OBSERVED' and status['cause']['version'] == 1


def test_runtime_resumes_only_its_own_cause_and_replays_the_same_operation(port):
    assert port.call('runtime-a', 'direct.control.apply',
                     {'cause_id': 'rt-stop', 'mode': 'STOP_NEW'})['result']['active'] is True
    released = port.call('runtime-a', 'direct.control.resume', {
        'cause_id': 'rt-stop', 'expected_version': 1, 'operation_id': 'release-1'})
    assert released['result'] == {'status': 'OBSERVED', 'cause_id': 'rt-stop', 'version': 2,
                                  'active': False}, released
    replay = port.call('runtime-a', 'direct.control.resume', {
        'cause_id': 'rt-stop', 'expected_version': 1, 'operation_id': 'release-1'})
    assert replay['result'] == released['result']
    assert cause(port, 'rt-stop')['version'] == 2


def test_runtime_cannot_resume_another_origin(port):
    port.call('runtime-a', 'direct.control.apply', {'cause_id': 'rt-a', 'mode': 'STOP_NEW'})
    refused = port.call('runtime-b', 'direct.control.resume', {
        'cause_id': 'rt-a', 'expected_version': 1, 'operation_id': 'steal'})
    assert refused['result'] == {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
    assert bool(cause(port, 'rt-a')['active']) is True and cause(port, 'rt-a')['version'] == 1
    # An operator's cause is not a Runtime cause either.
    port.call('ops-acct', 'direct.control.apply', {'cause_id': 'op-cause', 'mode': 'FREEZE'})
    refused = port.call('runtime-a', 'direct.control.resume', {
        'cause_id': 'op-cause', 'expected_version': 1, 'operation_id': 'release-op'})
    assert refused['result'] == {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
    assert cause(port, 'op-cause')['active'] == 1


def test_runtime_is_confined_to_its_own_strategy_scope(port):
    assert port.call('runtime-elsewhere', 'direct.control.apply', {
        'cause_id': 'elsewhere', 'mode': 'STOP_NEW'})['result'] == {
            'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
    # It cannot name a contract at all.
    assert port.call('runtime-a', 'direct.control.apply', {
        'cause_id': 'contract', 'mode': 'STOP_NEW', 'instrument': 'SHFE:rb2701'})['result'] == {
            'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
    assert port.call('ops-acct', 'direct.control.list', {'after': '', 'limit': 10})['result'][
        'causes'] == []


def test_expired_and_revoked_proofs_are_refused_with_finite_codes(port):
    assert port.call('runtime-expired', 'direct.control.apply', {
        'cause_id': 'expired', 'mode': 'STOP_NEW'})['result'] == {
            'status': 'REQUEST_REJECTED', 'code': 'RUNTIME_PROOF_EXPIRED'}
    assert port.call('runtime-revoked', 'direct.control.apply', {
        'cause_id': 'revoked', 'mode': 'STOP_NEW'})['result'] == {
            'status': 'REQUEST_REJECTED', 'code': 'RUNTIME_PROOF_UNAVAILABLE'}
    for name in ('runtime-expired', 'runtime-revoked'):
        assert port.call(name, 'direct.control.status', {'cause_id': 'rt-stop'})['result'] == {
            'status': 'REQUEST_REJECTED',
            'code': 'RUNTIME_PROOF_EXPIRED' if name == 'runtime-expired'
            else 'RUNTIME_PROOF_UNAVAILABLE'}


def test_removing_the_installed_grant_revokes_the_runtime(port):
    installed = port.binding._control_grants
    try:
        port.binding._control_grants = {
            name: grant for name, grant in installed.items() if name != 'runtime-a'}
        assert port.call('runtime-a', 'direct.control.status', {'cause_id': 'x'})['result'] == {
            'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
    finally:
        port.binding._control_grants = installed


def test_an_untrusted_or_non_finite_clock_refuses_the_runtime(port):
    installed = port.binding._control_clock
    try:
        for bad in (lambda: float("nan"), lambda: "now", lambda: True):
            port.binding._control_clock = bad
            assert port.call('runtime-a', 'direct.control.status', {'cause_id': 'x'})['result'] == {
                'status': 'REQUEST_REJECTED', 'code': 'RUNTIME_PROOF_UNAVAILABLE'}
    finally:
        port.binding._control_clock = installed


def test_control_surface_is_unavailable_without_configuration(port):
    identity, scope, _ = CALLERS['runtime-a']
    context = ServiceCallContext(identity, scope, 'registration', 'runtime-a')
    assert port.unconfigured.handle('direct.control.status', context=context,
                                    arguments={'cause_id': 'x'}) == {
        'status': 'REQUEST_REJECTED', 'code': 'CONTROL_UNAVAILABLE'}
    assert not ({'direct.control.apply', 'direct.control.status'}
                & port.unconfigured.supported_operations)


def test_identity_answers_the_frozen_schema_version(port):
    reply = port.call('ops-acct', 'direct.control.identity', {})['result']
    assert reply['schema_version'] == CONTROL_WIRE_SCHEMA_VERSION == 1


# -- grant construction: no operator authority ------------------------------------------------

def proof(**changes):
    values = {'delegation_id': 'delegation-1', 'operator_ref': 'operator-a', 'expires_at': 200.0}
    values.update(changes)
    return GatewayRuntimeControlProof(**values)


@pytest.mark.parametrize('changes', [
    {'runtime_proof': None},                                                # no proof
    {'kind': 'runtime', 'operations': frozenset({'direct.control.list'})},   # operator read
    {'kind': 'runtime', 'operations': frozenset({'direct.control.cancel_scope'})},
    {'kind': 'runtime', 'operations': frozenset({'direct.control.drain_target'})},
    {'kind': 'runtime', 'operations': frozenset({'direct.control.reconcile'})},
    {'kind': 'runtime', 'account_scope_allowed': True},                     # account authority
    {'kind': 'runtime', 'instrument_scope': True},
    {'kind': 'runtime', 'strategies': frozenset()},                         # no strategy
    {'kind': 'operator'},                                                   # proof on operator
    {'kind': 'risk'},
])
def test_a_runtime_grant_cannot_reach_operator_authority(changes):
    values = {
        'actor_ref': 'runtime-actor', 'account_scope_allowed': False,
        'strategies': frozenset({'strategy-a'}),
        'operations': frozenset({'direct.control.apply'}),
        'kind': 'runtime',
    }
    if changes.get('kind') != 'runtime' or 'runtime_proof' not in changes:
        values['runtime_proof'] = proof()
    values.update(changes)
    with pytest.raises(ValueError, match='^INVALID_CONTROL_GRANT$'):
        GatewayControlGrant(**values)


@pytest.mark.parametrize('changes', [
    {'delegation_id': ''},
    {'operator_ref': '   '},
    {'expires_at': float('inf')},
    {'expires_at': float('nan')},
    {'expires_at': '200'},
    {'expires_at': True},
    {'revoked': 'no'},
])
def test_a_runtime_proof_is_explicit_and_finite(changes):
    with pytest.raises(ValueError, match='^INVALID_RUNTIME_CONTROL_PROOF$'):
        proof(**changes)


def test_runtime_operation_subset_is_the_frozen_one():
    assert frozenset(
        {'direct.control.apply', 'direct.control.status', 'direct.control.resume'}) == CONTROL_RUNTIME_OPERATIONS
    assert CONTROL_RUNTIME_OPERATIONS <= _GATEWAY_CONTROL_OPERATIONS
    assert frozenset({'STOP_NEW', 'FREEZE', 'DRAIN'}) == CONTROL_MODES
