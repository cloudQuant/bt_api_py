"""Gateway facade cleanup retains its original Risk client (offline only)."""

import threading

import pytest

from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_runtime_thread import ThreadedRuntimeError
from tests.unit.test_iter63_gateway_strategy_client import parts
from tests.unit.test_iter64_risk_service_client import make_client


ROUTE = "CTP___FUTURE"


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("failures", [1, 2])
def test_risk_cleanup_retry_retains_original_and_closes_gateway_once(monkeypatch, owned, failures):
    session = GatewayStrategyClientSession(**parts())
    risk, wire = make_client(monkeypatch)
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=owned, risk_client=risk)
    risk_close, gateway_close = risk.close, session.runtime.close
    risk_calls, gateway_calls = [], []

    def close_risk(**kwargs):
        risk_calls.append(kwargs)
        if len(risk_calls) <= failures:
            raise RuntimeError("private remote detail")
        return risk_close(**kwargs)

    def close_gateway(**kwargs):
        gateway_calls.append(kwargs)
        return gateway_close(**kwargs)

    monkeypatch.setattr(risk, "close", close_risk)
    monkeypatch.setattr(session.runtime, "close", close_gateway)
    monkeypatch.setattr(session, "start", lambda **kw: pytest.fail("started after close"))
    monkeypatch.setattr(risk, "start", lambda **kw: pytest.fail("started Risk after close"))
    for _ in range(failures):
        with pytest.raises(ThreadedRuntimeError, match="^CLEANUP_FAILED$"):
            api.close(timeout_seconds=2)
        assert api.lifecycle_state == "closing"
        assert api.cleanup_confirmed is False
        assert api._risk is risk
        with pytest.raises(RuntimeError, match="GATEWAY_FACADE_CLOSING"):
            api.start()
        with pytest.raises(RuntimeError, match="GATEWAY_FACADE_CLOSING"):
            api.command_status(ROUTE, "key")
    result = api.close(timeout_seconds=2)
    assert result.state == ("closed" if owned else "new")
    assert api.lifecycle_state == "closed" and api.cleanup_confirmed is True
    assert len(risk_calls) == failures + 1
    assert len(gateway_calls) == int(owned)
    assert wire.calls == []
    api.close(timeout_seconds=2)
    assert len(risk_calls) == failures + 1
    assert len(gateway_calls) == int(owned)
    if not owned:
        session.close()


@pytest.mark.parametrize("failure", [False, None, object()])
def test_unconfirmed_risk_return_is_not_success(monkeypatch, failure):
    session = GatewayStrategyClientSession(**parts())
    risk, _ = make_client(monkeypatch)
    api = GatewayBtApi(session, exchange_name=ROUTE, risk_client=risk)
    monkeypatch.setattr(risk, "close", lambda **kw: failure)
    with pytest.raises(ThreadedRuntimeError, match="^CLEANUP_FAILED$"):
        api.close()
    assert api.lifecycle_state == "closing" and api.cleanup_confirmed is False
    session.close()


def test_gateway_failure_does_not_repeat_confirmed_risk_cleanup(monkeypatch):
    session = GatewayStrategyClientSession(**parts())
    risk, _ = make_client(monkeypatch)
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=True, risk_client=risk)
    risk_close, gateway_close = risk.close, session.runtime.close
    risk_calls, gateway_calls = [], []

    def close_risk(**kwargs):
        risk_calls.append(1)
        return risk_close(**kwargs)

    def close_gateway(**kwargs):
        gateway_calls.append(1)
        if len(gateway_calls) == 1:
            raise ThreadedRuntimeError("CLOSE_TIMEOUT_WORKER_ALIVE")
        return gateway_close(**kwargs)

    monkeypatch.setattr(risk, "close", close_risk)
    monkeypatch.setattr(session.runtime, "close", close_gateway)
    with pytest.raises(ThreadedRuntimeError, match="CLOSE_TIMEOUT_WORKER_ALIVE"):
        api.close()
    assert api.lifecycle_state == "closing" and api.cleanup_confirmed is False
    assert api.close().state == "closed"
    assert api.cleanup_confirmed is True
    assert len(risk_calls) == 1 and len(gateway_calls) == 2


def test_uncertain_actual_risk_cleanup_does_not_retry_runtime(monkeypatch):
    import bt_api_py.risk_service_client as risk_module

    session = GatewayStrategyClientSession(**parts())
    risk, wire = make_client(monkeypatch)
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=True, risk_client=risk)
    observations = []
    monkeypatch.setattr(risk_module, "_closed_snapshot", lambda *args: observations.append(1))
    for _ in range(2):
        with pytest.raises(ThreadedRuntimeError, match="^CLEANUP_FAILED$"):
            api.close()
        assert api.lifecycle_state == "closing" and not api.cleanup_confirmed
        assert api._risk is risk
    assert observations == [1]
    assert wire.calls == []


def test_concurrent_close_does_not_duplicate_risk_close(monkeypatch):
    session = GatewayStrategyClientSession(**parts())
    risk, _ = make_client(monkeypatch)
    api = GatewayBtApi(session, exchange_name=ROUTE, risk_client=risk)
    entered, release = threading.Event(), threading.Event()
    original = risk.close
    calls, errors = [], []

    def close_risk(**kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return original(**kwargs)

    def close_api():
        try:
            api.close()
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(risk, "close", close_risk)
    thread = threading.Thread(target=close_api)
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(ThreadedRuntimeError, match="^CLEANUP_FAILED$"):
            api.close()
        assert api.cleanup_confirmed is False
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and errors == []
    assert calls == [1] and api.cleanup_confirmed is True
    session.close()


def test_failed_gateway_snapshot_with_confirmed_cleanup_allows_facade_close(monkeypatch):
    from dataclasses import replace

    session = GatewayStrategyClientSession(**parts())
    risk, _ = make_client(monkeypatch)
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=True, risk_client=risk)
    original = session.runtime.close
    calls = []

    def failed_after_cleanup(**kwargs):
        calls.append(1)
        return replace(original(**kwargs), state="failed", error_code="START_FAILED")

    monkeypatch.setattr(session.runtime, "close", failed_after_cleanup)
    assert api.close().state == "failed"
    assert api.cleanup_confirmed is True and api.lifecycle_state == "closed"
    assert api.close().state == "failed" and calls == [1]


@pytest.mark.parametrize("field", ["unknown_registration_count", "unregister_uncertainties",
                                    "owner_cleanup_uncertainties", "cleanup_failures"])
@pytest.mark.parametrize("value", [None, 1])
def test_non_strict_gateway_retains_its_existing_close_contract(monkeypatch, field, value):
    from dataclasses import replace

    session = GatewayStrategyClientSession(**parts())
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=True)
    original = session.runtime.close
    closed = original()
    uncertain = replace(closed, **{field: value})
    monkeypatch.setattr(session.runtime, "close", lambda **kw: uncertain)
    assert api.close() is uncertain
    assert api.lifecycle_state == "closed" and api.cleanup_confirmed is True
    # The returned snapshot still exposes the unresolved evidence to Store.
    assert getattr(api.close(), field) == value


def test_both_cleanup_failures_are_retained_and_retried(monkeypatch):
    session = GatewayStrategyClientSession(**parts())
    risk, _ = make_client(monkeypatch)
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=True, risk_client=risk)
    risk_close, gateway_close = risk.close, session.runtime.close
    attempts = {"risk": 0, "gateway": 0}

    def close_risk(**kwargs):
        attempts["risk"] += 1
        if attempts["risk"] == 1:
            raise RuntimeError("private Risk text")
        return risk_close(**kwargs)

    def close_gateway(**kwargs):
        attempts["gateway"] += 1
        if attempts["gateway"] == 1:
            raise ThreadedRuntimeError("CLOSE_TIMEOUT_WORKER_ALIVE")
        return gateway_close(**kwargs)

    monkeypatch.setattr(risk, "close", close_risk)
    monkeypatch.setattr(session.runtime, "close", close_gateway)
    with pytest.raises(ThreadedRuntimeError, match="^CLOSE_TIMEOUT_WORKER_ALIVE$"):
        api.close()
    assert attempts == {"risk": 1, "gateway": 1}
    assert api.lifecycle_state == "closing" and api.cleanup_confirmed is False
    assert api.close().state == "closed" and api.cleanup_confirmed is True
    assert attempts == {"risk": 2, "gateway": 2}


def test_close_fences_inflight_gateway_start_before_risk_start(monkeypatch):
    session = GatewayStrategyClientSession(**parts())
    risk, wire = make_client(monkeypatch)
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=True, risk_client=risk)
    entered, release = threading.Event(), threading.Event()
    errors = []

    def start_gateway(**kwargs):
        entered.set()
        assert release.wait(2)
        return session.runtime.status()

    def start_api():
        try:
            api.start()
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(session, "start", start_gateway)
    monkeypatch.setattr(risk, "start", lambda **kw: pytest.fail("started Risk after close"))
    thread = threading.Thread(target=start_api)
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(ThreadedRuntimeError, match="^CLEANUP_FAILED$"):
            api.close()
        assert api.lifecycle_state == "closing" and api.cleanup_confirmed is False
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert len(errors) == 1 and str(errors[0]) == "GATEWAY_FACADE_CLOSING"
    assert wire.calls == []
    assert api.close().state == "closed" and api.cleanup_confirmed is True


def test_strict_gateway_uses_original_session_close(monkeypatch):
    session = GatewayStrategyClientSession(**parts(), strict_cleanup=True)
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=True)
    original = session.close
    calls = []

    def close_session(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(session, "close", close_session)
    result = api.close(timeout_seconds=2)
    # A never-started strict runtime can have no ACK, as no registration exists.
    assert result.strict_unregister_ack is None
    assert session._closed_snapshot is result
    assert calls == [{"timeout_seconds": 2}]
    assert api.cleanup_confirmed is True and api.lifecycle_state == "closed"
    assert api.close() is result and len(calls) == 1


def test_strict_gateway_unknown_cleanup_remains_fenced(monkeypatch):
    session = GatewayStrategyClientSession(**parts(), strict_cleanup=True)
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=True)
    session._cleanup_uncertain = True
    monkeypatch.setattr(session.runtime, "close", lambda **kw: pytest.fail("bypassed strict session"))
    for _ in range(2):
        with pytest.raises(ThreadedRuntimeError, match="^CLEANUP_FAILED$"):
            api.close()
        assert api.lifecycle_state == "closing" and api.cleanup_confirmed is False


@pytest.mark.parametrize("drift", ["runtime", "original_runtime", "policy", "facade_session"])
def test_strict_gateway_identity_or_policy_drift_does_not_close(monkeypatch, drift):
    session = GatewayStrategyClientSession(**parts(), strict_cleanup=True)
    risk, _ = make_client(monkeypatch)
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=True, risk_client=risk)
    risk_close = risk.close
    risk_calls = []

    def close_risk(**kwargs):
        risk_calls.append(1)
        return risk_close(**kwargs)

    monkeypatch.setattr(risk, "close", close_risk)
    monkeypatch.setattr(session, "close", lambda **kw: pytest.fail("closed drifted session"))
    if drift == "runtime":
        session._runtime = object()
    elif drift == "original_runtime":
        session._original_runtime = object()
    elif drift == "policy":
        session._strict_cleanup = False
    else:
        api._session = object()
    with pytest.raises(ThreadedRuntimeError, match="^CLEANUP_FAILED$"):
        api.close()
    assert risk_calls == [1]  # Risk cleanup is still attempted independently.
    assert api.lifecycle_state == "closing" and api.cleanup_confirmed is False


def test_strict_gateway_confirmation_rejects_later_identity_drift():
    session = GatewayStrategyClientSession(**parts(), strict_cleanup=True)
    api = GatewayBtApi(session, exchange_name=ROUTE, owns_session=True)
    api.close()
    assert api.cleanup_confirmed is True
    session._runtime = object()
    assert api.cleanup_confirmed is False
    with pytest.raises(ThreadedRuntimeError, match="^CLEANUP_FAILED$"):
        api.close()
