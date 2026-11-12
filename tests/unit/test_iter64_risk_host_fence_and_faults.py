"""Risk host: writer fence taken in one non-blocking attempt; test-only fault points (64.09).

UNIT evidence: temp storage, no Gateway contact. The crash behaviour at each
fault point is exercised end to end in
``tests/integration/test_iter64_risk_crash_matrix.py``.
"""

from __future__ import annotations

import multiprocessing
import sqlite3
import threading
import time
from dataclasses import replace

import pytest

from bt_api_py.risk_service_config import load_risk_service_config
from bt_api_py.risk_service_host import (
    FAULT_EXIT_CODE,
    FAULT_POINT_ENVIRONMENT,
    FAULT_POINTS,
    RiskServiceHost,
    RiskServiceStartError,
    _FaultInjectingOrderPort,
    fault_point_from_environment,
    main,
)
from tests.unit.test_iter64_risk_service_host import setup


def test_a_holder_that_wins_the_race_refuses_the_host_at_once(tmp_path, monkeypatch):
    """No probe-then-acquire window: a holder appearing just before acquisition still refuses."""
    from bt_api_risk.risk_order_journal import RiskOrderJournal

    config = load_risk_service_config(setup(tmp_path))
    original_fence = RiskOrderJournal.writer_fence
    original_open = RiskOrderJournal.open_fenced
    holding, release = threading.Event(), threading.Event()

    def hold(scope):
        other = RiskOrderJournal(config.risk_database, writer_id="racer")
        with original_fence(other, scope):
            holding.set()
            release.wait(3)  # bounded: an old waiting host would return after this

    def racing(*args, **kwargs):
        scope = kwargs["scope"]
        threading.Thread(target=hold, args=(scope,), daemon=True).start()
        assert holding.wait(10)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(RiskOrderJournal, "open_fenced", staticmethod(racing))
    host = RiskServiceHost(config)
    started = time.monotonic()
    try:
        with pytest.raises(RiskServiceStartError) as raised:
            host.open_storage()
        assert raised.value.code == "RISK_SERVICE_FENCE_HELD"
        assert time.monotonic() - started < 2.0
    finally:
        release.set()
        host.close()


def _holder_with_sqlite_write(path, ready, release):
    """Synthetic independent owner holds its account fence and a SQLite write lock."""
    from bt_api_risk.core.admission import AccountScope
    from bt_api_risk.risk_order_journal import RiskOrderJournal

    config = load_risk_service_config(path)
    journal = RiskOrderJournal(config.risk_database, writer_id="holder")
    scope = AccountScope(config.provider, config.account_scope, config.environment)
    with journal.writer_fence(scope) as fence:
        with sqlite3.connect(config.risk_database, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            ready.put(("held", fence.fencing_token))
            assert release.wait(30)
            db.execute("ROLLBACK")


def _competing_host(path, different_scope, output):
    config = load_risk_service_config(path)
    if different_scope:
        config = replace(config, account_scope="account-b")
    host = RiskServiceHost(config)
    start = time.monotonic()
    try:
        host.open_storage()
    except RiskServiceStartError as error:
        output.put((error.code, time.monotonic() - start))
    except BaseException as error:
        output.put((type(error).__name__, time.monotonic() - start))
    else:
        output.put(("opened", time.monotonic() - start))
    finally:
        host.close()


@pytest.mark.parametrize("different_scope, expected", [
    (False, "RISK_SERVICE_FENCE_HELD"),
    (True, "RISK_SERVICE_STORAGE_BUSY"),
])
def test_host_competition_before_sqlite_initialization(tmp_path, different_scope, expected):
    """A second process refuses promptly without advancing either scope's token."""
    from bt_api_risk.core.admission import AccountScope

    path = setup(tmp_path)
    config = load_risk_service_config(path)
    context = multiprocessing.get_context("spawn")
    ready, output, release = context.Queue(), context.Queue(), context.Event()
    holder = context.Process(target=_holder_with_sqlite_write, args=(str(path), ready, release))
    competitor = None
    holder.start()
    try:
        assert ready.get(timeout=15) == ("held", 1)
        competitor = context.Process(target=_competing_host,
                                     args=(str(path), different_scope, output))
        competitor.start()
        code, elapsed = output.get(timeout=8)
        assert code == expected and elapsed < 2.0, (code, elapsed)
        competitor.join(5)
        assert competitor.exitcode == 0
        with sqlite3.connect(config.risk_database) as db:
            assert db.execute("SELECT count(*) FROM risk_order_fences").fetchone() == (1,)
            assert db.execute("SELECT fencing_token FROM risk_order_fences").fetchone() == (1,)
    finally:
        release.set()
        holder.join(15)
        if competitor is not None:
            competitor.join(15)
    assert holder.exitcode == 0
    if competitor is not None:
        assert competitor.exitcode == 0

    if different_scope:
        config = replace(config, account_scope="account-b")
    next_host = RiskServiceHost(config)
    try:
        next_host.open_storage()
    finally:
        next_host.close()
    scope = AccountScope(config.provider, config.account_scope, config.environment)
    with sqlite3.connect(config.risk_database) as db:
        token = db.execute("SELECT fencing_token FROM risk_order_fences WHERE scope_key=?",
                           (scope.key,)).fetchone()[0]
    assert token == (1 if different_scope else 2)


def test_host_schema_failure_releases_acquired_account_fence(tmp_path, monkeypatch):
    import bt_api_risk.pretrade as pretrade

    config = load_risk_service_config(setup(tmp_path))
    failed = RiskServiceHost(config)
    with monkeypatch.context() as patch:
        def fail(*_args, **_kwargs):
            raise ValueError("synthetic failure")
        patch.setattr(pretrade, "PretradeLedger", fail)
        with pytest.raises(RiskServiceStartError) as raised:
            failed.open_storage()
        assert raised.value.code == "RISK_SERVICE_STORAGE_SCHEMA_INVALID"
    # No explicit close on the failed host is required to release the fence.
    succeeding = RiskServiceHost(config)
    try:
        succeeding.open_storage()
    finally:
        succeeding.close()
        failed.close()


@pytest.mark.parametrize("value", ["bogus", "", "AFTER_LOCAL_RESERVE", " after_claim_commit"])
def test_an_unknown_fault_point_refuses_to_start_before_storage(tmp_path, monkeypatch, capsys, value):
    path = setup(tmp_path)
    monkeypatch.setenv(FAULT_POINT_ENVIRONMENT, value)
    assert main(["run", "--config", str(path)]) == 2
    assert capsys.readouterr().err.strip() == (
        "RISK_SERVICE_START_REFUSED RISK_SERVICE_FAULT_POINT_INVALID"
    )
    assert not (tmp_path / "state").exists()


def test_without_the_variable_the_engine_gets_the_delegate_port_itself(tmp_path, monkeypatch):
    monkeypatch.delenv(FAULT_POINT_ENVIRONMENT, raising=False)
    assert fault_point_from_environment({}) is None
    config = load_risk_service_config(setup(tmp_path))
    host = RiskServiceHost(config)
    try:
        host._fault_point = fault_point_from_environment({})
        host.open_storage()
        host.build_engine()
        assert host.engine._gateway is host.order_port
    finally:
        host.close()
    host = RiskServiceHost(config)
    try:
        host._fault_point = fault_point_from_environment(
            {FAULT_POINT_ENVIRONMENT: "after_claim_commit"}
        )
        host.open_storage()
        host.build_engine()
        assert type(host.engine._gateway) is _FaultInjectingOrderPort
        assert host.engine._gateway._port is host.order_port
    finally:
        host.close()


class _Exited(BaseException):
    pass


class _Port:
    def __init__(self):
        self.calls = []

    def reserve(self, **kwargs):
        self.calls.append("reserve")
        return "reserved"

    def dispatch(self, **kwargs):
        self.calls.append("dispatch")
        return "queued"

    def status(self, **kwargs):
        self.calls.append("status")
        return "observed"


@pytest.mark.parametrize(
    ("point", "operation", "calls_before_exit"),
    [
        ("after_local_reserve", "reserve", []),
        ("after_gateway_reserve", "reserve", ["reserve"]),
        ("after_claim_commit", "dispatch", []),
        ("after_dispatch_sent", "dispatch", ["dispatch"]),
    ],
)
def test_each_point_exits_exactly_where_it_names(point, operation, calls_before_exit, capfd):
    exits = []

    def exit_now(code):
        exits.append(code)
        raise _Exited

    port = _Port()
    wrapped = _FaultInjectingOrderPort(port, point, exit_now=exit_now)
    other = "dispatch" if operation == "reserve" else "reserve"
    # The other operation and status pass straight through.
    assert getattr(wrapped, other)(request_key="k") in ("reserved", "queued")
    assert wrapped.status(request_key="k") == "observed"
    port.calls.clear()
    with pytest.raises(_Exited):
        getattr(wrapped, operation)(request_key="k")
    assert exits == [FAULT_EXIT_CODE] and port.calls == calls_before_exit
    assert capfd.readouterr().err.strip() == "RISK_SERVICE_FAULT_INJECTED " + point
    assert point in FAULT_POINTS
