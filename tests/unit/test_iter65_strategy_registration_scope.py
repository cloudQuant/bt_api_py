"""Registration scope checks use synthetic owner snapshots, not account facts."""
from dataclasses import replace

import pytest

from bt_api_py.execution_parent_grant import OPERATIONS
from bt_api_py.execution_parent_strategy_session import ExecutionParentStrategySession
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot, ThreadedRuntimeError
from tests.unit.test_iter63_gateway_strategy_client import parts
from tests.unit.test_iter65_execution_parent_strategy_session import KEYS, TARGET, config


def session_for(kind):
    if kind == 'gateway':
        session = GatewayStrategyClientSession(**parts(), strict_cleanup=True)
        return session, session.route
    session = ExecutionParentStrategySession(config(), target=TARGET,
        security_profiles={'test-security': KEYS})
    return session, session.target


def setup_owner(monkeypatch, kind, *, scope_change=None):
    session, route = session_for(kind)
    runtime = session.runtime
    scope = replace(route.scope, **(scope_change or {}))
    binding = BindingSnapshot('registration', 'epoch', True, route.mount, route.service_id,
        route.instance_id, route.scope_ref, scope, OPERATIONS, True)
    running = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0,
                              route.identity, route.scope.run_id, 0, 0, True)
    closed = replace(running, state='closed', thread_alive=False, healthy=False, bindings=())
    state, calls = {'value': running}, []
    monkeypatch.setattr(runtime, 'start', lambda **_: running)
    monkeypatch.setattr(runtime, 'cleanup_observation', lambda **_: (state['value'], True))
    def close(**_):
        calls.append('close')
        state['value'] = closed
        return closed
    monkeypatch.setattr(runtime, 'close_once_if_state', close)
    monkeypatch.setattr(runtime, 'status', lambda: state['value'])
    return session, runtime, running, closed, calls


@pytest.mark.parametrize('kind', ['gateway', 'execution'])
@pytest.mark.parametrize('change', [{'run_id': 'old-run'}, {'execution_domain_ref': 'other'},
                                    {'account_ref': 'other'}])
def test_mismatched_active_scope_rejects_start_and_closes_original(monkeypatch, kind, change):
    session, runtime, _, closed, calls = setup_owner(monkeypatch, kind, scope_change=change)
    with pytest.raises(ThreadedRuntimeError, match='REGISTRATION_SCOPE_MISMATCH'):
        session.start()
    assert session.runtime is runtime
    with pytest.raises(ThreadedRuntimeError):
        session.start()
    assert session.close() == closed
    assert calls == ['close']
    if kind == 'execution':
        assert session.cleanup_confirmed


@pytest.mark.parametrize('kind', ['gateway', 'execution'])
def test_matching_active_scope_starts_and_closes(monkeypatch, kind):
    session, _, running, closed, calls = setup_owner(monkeypatch, kind)
    assert session.start() == running
    assert session.close() == closed
    assert calls == ['close']


def test_non_strict_gateway_mismatch_rejects_and_closes_same_owner(monkeypatch):
    session = GatewayStrategyClientSession(**parts())
    runtime, route = session.runtime, session.route
    binding = BindingSnapshot('registration', 'epoch', True, route.mount, route.service_id,
        route.instance_id, route.scope_ref, replace(route.scope, run_id='old-run'),
        frozenset({'direct.events.progress'}), True)
    running = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0,
                              route.identity, route.scope.run_id, 0, 0, False)
    closed = replace(running, state='closed', thread_alive=False, healthy=False, bindings=())
    monkeypatch.setattr(runtime, 'start', lambda **_: running)
    monkeypatch.setattr(runtime, 'close', lambda **_: closed)
    with pytest.raises(ThreadedRuntimeError, match='REGISTRATION_SCOPE_MISMATCH'):
        session.start()
    assert session.runtime is runtime
    assert session.close() == closed
