"""Synthetic startup and close observations; no remote cleanup claim."""
from dataclasses import replace

import pytest

import bt_api_py.gateway_strategy_client as gateway
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot, ThreadedRuntimeError
from tests.unit.test_iter63_gateway_strategy_client import parts


def setup_rejected(monkeypatch, *, owned=True):
    roots = {}
    monkeypatch.setattr(gateway, '_RETAINED_STRATEGY_SESSIONS', roots)
    session = gateway.GatewayStrategyClientSession(**parts())
    runtime, route = session.runtime, session.route
    binding = BindingSnapshot('registration', 'epoch', True, route.mount, route.service_id,
        route.instance_id, route.scope_ref, replace(route.scope, run_id='old-run'),
        frozenset({'direct.events.progress'}), True)
    running = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0,
                              route.identity, route.scope.run_id, 0, 0, False)
    closed = replace(running, state='closed', thread_alive=False, healthy=False, bindings=())
    api = GatewayBtApi(session, exchange_name='CTP___FUTURE', owns_session=owned)
    monkeypatch.setattr(runtime, 'start', lambda **_: running)
    with pytest.raises(ThreadedRuntimeError, match='REGISTRATION_SCOPE_MISMATCH'):
        api.start()
    assert roots[id(session)] is session
    return api, session, runtime, closed, roots


def test_facade_close_releases_original_rejected_session(monkeypatch):
    api, session, runtime, closed, roots = setup_rejected(monkeypatch)
    calls = []
    monkeypatch.setattr(runtime, 'close', lambda **_: calls.append(1) or closed)
    monkeypatch.setattr(runtime, 'status', lambda: closed)
    assert api.close() is closed
    assert calls == [1] and id(session) not in roots and api.cleanup_confirmed


@pytest.mark.parametrize('changed', ['session_runtime', 'facade_session', 'facade_runtime', 'observation'])
def test_custody_retained_if_original_owner_or_close_observation_unproven(monkeypatch, changed):
    api, session, runtime, closed, roots = setup_rejected(monkeypatch)
    monkeypatch.setattr(runtime, 'close', lambda **_: closed)
    monkeypatch.setattr(runtime, 'status', lambda: replace(closed, thread_alive=True)
                        if changed == 'observation' else closed)
    if changed == 'session_runtime':
        session._runtime = object()
    elif changed == 'facade_session':
        api._session = object()
    elif changed == 'facade_runtime':
        api._runtime = object()
    assert api.close() is closed
    assert roots[id(session)] is session


def test_borrowed_session_is_not_released_by_facade_close(monkeypatch):
    api, session, runtime, _, roots = setup_rejected(monkeypatch, owned=False)
    monkeypatch.setattr(runtime, 'status', lambda: RuntimeSnapshot('running', True, True, None, (), 0, 0))
    assert api.close().state == 'running'
    assert roots[id(session)] is session


@pytest.mark.parametrize('via_api', [False, True])
def test_existing_non_strict_close_contract_releases_local_custody(monkeypatch, via_api):
    api, session, runtime, closed, roots = setup_rejected(monkeypatch)
    # Local owner stopped; this does not confirm remote unregister.
    unconfirmed = replace(closed, unregister_uncertainties=1)
    monkeypatch.setattr(runtime, 'close', lambda **_: unconfirmed)
    monkeypatch.setattr(runtime, 'status', lambda: unconfirmed)
    assert (api.close() if via_api else session.close()) is unconfirmed
    assert id(session) not in roots
