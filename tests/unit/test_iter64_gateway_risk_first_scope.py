"""Synthetic candidate only: no production handler, authority, or venue."""

import threading
from dataclasses import replace
from types import MappingProxyType

import pytest
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard

import bt_api_py._gateway_risk_first_scope as scope_module
from bt_api_py._gateway_owner_guard_binding import _GatewayOwnerGuardBinding
from bt_api_py._gateway_risk_first_scope import _plain, _RiskFirstScopeError, assert_in_active_scope
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.service_registry import ServiceDispatchAdmissionError
from tests.unit.test_iter64_registry_gateway_risk_first_v2 import (
    KEY,
    OP,
    register_actor,
    setup_registry,
)
from tests.unit.test_iter64_registry_gateway_risk_first_v2 import (
    request as wire_request,
)
from tests.unit.test_iter64_risk_first_payload_consistency import _payload, _request


@pytest.fixture
def harness(tmp_path):
    state = {}
    closing = threading.Event()
    guard_dir = tmp_path / 'guard'
    guard_dir.mkdir()

    class FakeSession:
        def send(self, *_):
            pytest.fail('synthetic classification must not dispatch')

    session = FakeSession()

    def factory():
        guard = LocalAccountSessionGuard(guard_dir, 'acct', 'owner')
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(tmp_path / 'journal.sqlite', 'acct', generation=generation)
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, session.send,
                                             lambda _: ('anchor',), lambda _: True)
        binding = _GatewayOwnerGuardBinding(owner, journal, guard, dispatcher,
                                             account='acct', journal_id=journal.journal_id,
                                             generation=generation, exchange='CTP', closing=closing)
        state.update(journal=journal, binding=binding, closing=closing)
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

    owner = GatewayDirectOwner(factory, queue_size=2, max_queue_wait_seconds=2, busy_timeout_seconds=2)
    assert owner.start(3)
    registry, clock, actor, scope, grant = setup_registry(path=tmp_path / 'registry.sqlite')
    captures = []
    # This test handler only captures Registry-issued inputs. Its candidate
    # {request,payload} shape is not a frozen public or production contract.
    registry.register_handler('gateway', OP, lambda ctx, args: captures.append((ctx, args)) or {})
    registration = register_actor(registry, actor, scope)['result']
    payload = _payload()
    payload['symbol'] = KEY.native_symbol
    request = _request(payload)
    command = replace(request.command, strategy_scope='strategy-a', actor_principal='risk-p',
                      actor_ref='risk-actor', instrument=KEY, grant_revision=grant.revision)
    request = replace(request, command=command)
    args = {'request': request.to_mapping(), 'payload': payload}
    reply = registry.handle('risk-p', wire_request(actor, scope, registration, args))
    assert reply['ok'], reply
    ctx, args = captures[0]
    state.update(owner=owner, registry=registry, clock=clock, context=ctx,
                 arguments=args, admission=ctx.dispatch_admission, grant=grant,
                 captures=captures, actor=actor, scope=scope, registration=registration)
    try:
        yield state
    finally:
        ctx.dispatch_admission.close()
        registry.close()
        assert owner.close(3)


def invoke(state, recheck, **overrides):
    values = {'registry': state['registry'], 'service_id': 'gateway-service', 'instance_id': 'gateway',
              'context': state['context'], 'arguments': state['arguments'], 'admission': state['admission'],
              'recheck': recheck, 'owner': state['owner'], 'journal': state['journal'],
              'owner_binding': state['binding']}
    values.update(overrides)
    return assert_in_active_scope(**values)


def on_owner(state, callback):
    def wrapped():
        try:
            return callback()
        except BaseException as error:
            return error
    result = state['owner'].submit_service_call(wrapped).result(timeout_seconds=4)
    if isinstance(result, BaseException):
        raise result
    return result


def test_valid_scope_returns_no_result_and_gate_stays_closed(harness):
    state = harness
    def run():
        with state['admission'].scope() as recheck:
            assert invoke(state, recheck) is None
            assert state['journal']._risk_v2_writes_authorized() is False
        with pytest.raises(_RiskFirstScopeError):
            invoke(state, recheck)
        return True
    assert on_owner(state, run)


def test_forged_scope_bindings_and_arguments_are_rejected(harness):
    state = harness
    def run():
        admission = state['admission']
        with admission.scope() as recheck:
            for changed in ({'recheck': lambda: None}, {'service_id': 'other'},
                            {'instance_id': 'other'}, {'registry': object()},
                            {'context': replace(state['context'], principal_ref='wrong')},
                            {'arguments': MappingProxyType(dict(state['arguments']))},
                            {'journal': object()}, {'owner_binding': object()}):
                with pytest.raises(_RiskFirstScopeError):
                    invoke(state, **({'recheck': recheck} | changed))
            def freeze(value):
                if type(value) is dict:
                    return MappingProxyType({key: freeze(item) for key, item in value.items()})
                if type(value) is list:
                    return tuple(map(freeze, value))
                return value
            for path, key, value in [('payload', 'quantity', '2'),
                                     ('request', 'risk_service_epoch', 'altered')]:
                args = _plain(state['arguments'])
                args[path][key] = value
                args = freeze(args)
                ctx = replace(state['context'], _wire_arguments=(state['context']._wire_arguments[0], args))
                with pytest.raises(_RiskFirstScopeError):
                    invoke(state, recheck, arguments=args, context=ctx)
            assert invoke(state, recheck) is None
        return True
    assert on_owner(state, run)


@pytest.mark.parametrize('section,field,value', [
    ('command', 'account_scope', 'different'),
    ('command', 'strategy_scope', 'different'),
    ('command', 'actor_principal', 'different'),
    ('command', 'grant_revision', 'different'),
    ('instrument', 'instrument_id', 'different'),
    ('payload', 'quantity', '2'),
])
def test_admitted_wire_values_still_need_scope_grant_and_payload_consistency(
        harness, section, field, value):
    state = harness
    args = _plain(state['arguments'])
    target = (args['request']['command']['instrument'] if section == 'instrument'
              else args['request']['command'] if section == 'command'
              else args['payload'])
    target[field] = value
    reply = state['registry'].handle(
        'risk-p', wire_request(state['actor'], state['scope'], state['registration'], args))
    assert reply['ok'], reply
    context, arguments = state['captures'][-1]
    admission = context.dispatch_admission
    def run():
        try:
            with admission.scope() as recheck:
                with pytest.raises(_RiskFirstScopeError) as failure:
                    invoke(state, recheck, context=context, arguments=arguments, admission=admission)
                assert failure.value.__context__ is None and failure.value.__cause__ is None
            return True
        finally:
            admission.close()
    assert on_owner(state, run)


def test_cross_thread_closed_scope_exception_and_reentry(harness):
    state = harness
    def run():
        admission = state['admission']
        with pytest.raises(ValueError, match='synthetic exception'), admission.scope() as old:
            outcomes = []
            def foreign():
                try:
                    invoke(state, old)
                except _RiskFirstScopeError:
                    outcomes.append('rejected')
            thread = threading.Thread(target=foreign)
            thread.start()
            thread.join(2)
            assert not thread.is_alive() and outcomes == ['rejected']
            with pytest.raises(ServiceDispatchAdmissionError, match='ADMISSION_BUSY'), admission.scope():
                pass
            raise ValueError('synthetic exception')
        with admission.scope() as current:
            with pytest.raises(_RiskFirstScopeError):
                invoke(state, old)
            assert invoke(state, current) is None
            admission.close()
            with pytest.raises(_RiskFirstScopeError):
                invoke(state, current)
        return True
    assert on_owner(state, run)


def test_revoked_grant_and_owner_closing_fail(harness):
    state = harness
    def run():
        with state['admission'].scope() as recheck:
            state['closing'].set()
            try:
                with pytest.raises(_RiskFirstScopeError):
                    invoke(state, recheck)
            finally:
                state['closing'].clear()
            state['registry']._gateway_delegate_grants.clear()
            with pytest.raises(_RiskFirstScopeError):
                invoke(state, recheck)
        return True
    assert on_owner(state, run)


def test_lease_expiring_during_final_check_is_refused(harness, monkeypatch):
    state = harness
    validate = scope_module.validate_risk_first_payload_consistency
    calls = []

    def slow_validate(request, payload):
        result = validate(request, payload)
        calls.append(None)
        if len(calls) == 2:
            state['clock'][0] = 111.0
        return result

    monkeypatch.setattr(scope_module, 'validate_risk_first_payload_consistency', slow_validate)

    def run():
        with state['admission'].scope() as recheck:
            with pytest.raises(_RiskFirstScopeError):
                invoke(state, recheck)
            assert len(calls) == 2
        return True

    assert on_owner(state, run)


def test_owner_closing_during_final_check_is_refused(harness, monkeypatch):
    state = harness
    validate = scope_module.validate_risk_first_payload_consistency
    calls = []

    def closing_validate(request, payload):
        result = validate(request, payload)
        calls.append(None)
        if len(calls) == 2:
            state['closing'].set()
        return result

    monkeypatch.setattr(scope_module, 'validate_risk_first_payload_consistency', closing_validate)

    def run():
        try:
            with state['admission'].scope() as recheck:
                with pytest.raises(_RiskFirstScopeError):
                    invoke(state, recheck)
                assert len(calls) == 2
            return True
        finally:
            state['closing'].clear()

    assert on_owner(state, run)


def test_owner_condition_contention_fails_without_waiting(harness):
    state = harness
    acquired, release = threading.Event(), threading.Event()
    def occupy():
        with state['owner']._condition:
            acquired.set()
            assert release.wait(3)
    def run():
        with state['admission'].scope() as recheck:
            thread = threading.Thread(target=occupy)
            thread.start()
            try:
                assert acquired.wait(2)
                with pytest.raises(_RiskFirstScopeError):
                    invoke(state, recheck)
            finally:
                release.set()
                thread.join(2)
            assert not thread.is_alive()
        return True
    assert on_owner(state, run)
