"""Host fence ownership and close receipts using only synthetic resources."""

import importlib.util
import inspect
import os
import sys
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import bt_api_risk.pretrade as pretrade
import bt_api_risk.risk_order_journal as risk_module
import pytest

import bt_api_py.risk_service_host as host_module

# Keep synthetic unknown owners alive until process exit; no real retained
# state, files or locks are involved.
_SYNTHETIC = []
_OPEN_FENCED = risk_module.RiskOrderJournal.__dict__["open_fenced"]


class FakeGuard:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeEvent:
    def set(self):
        pass


class FakeInner:
    def __init__(self, *, exit_error=None):
        self.events = []
        self.exit_error = exit_error
        self.fence = object()
        self.journal = FakeJournal(self)

    def __enter__(self):
        self.events.append("enter")
        return self.journal, self.fence

    def __exit__(self, *_):
        self.events.append("exit")
        if self.exit_error:
            raise self.exit_error
        return False


class FakeJournal:
    def __init__(self, manager):
        self.manager = manager
        self.receipt = risk_module.RiskFenceExitCleanup.COMPLETE
        self.fail_prepare = None
        self.fail_consume = None

    def prepare_borrowed_fence_close(self, manager, fence):
        assert manager is self.manager and fence is manager.fence
        manager.events.append("prepare")
        if self.fail_prepare:
            raise self.fail_prepare

    def consume_borrowed_fence_close(self, manager, fence):
        assert manager is self.manager and fence is manager.fence
        manager.events.append("consume")
        if self.fail_consume:
            raise self.fail_consume
        return self.receipt


@pytest.fixture
def synthetic(monkeypatch):
    active, initializing, retained = {}, {}, set()
    probe = {"active": False, "uncertain": []}
    _SYNTHETIC.append((active, initializing, retained, probe))
    monkeypatch.setattr(host_module, "_PROBE_GUARD", FakeGuard())
    monkeypatch.setattr(host_module, "_PROBE_STATE", probe)
    monkeypatch.setattr(host_module, "_STORAGE_ACTIVE_ROOTS", active)
    monkeypatch.setattr(host_module, "_STORAGE_INIT_ROOTS", initializing)
    monkeypatch.setattr(host_module, "_RETAINED_HOSTS", retained)
    monkeypatch.setattr(host_module, "_require_supported_r04", lambda _: None)
    monkeypatch.setattr(host_module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(host_module, "restrict_file", lambda _: None)
    inner = FakeInner()
    monkeypatch.setattr(risk_module.RiskOrderJournal, "open_fenced", lambda *a, **kw: inner)
    return SimpleNamespace(inner=inner, active=active, initializing=initializing,
                           retained=retained, probe=probe)


def fake_host():
    host = object.__new__(host_module.RiskServiceHost)
    host.config = SimpleNamespace(provider="fake", account_scope="account", environment="test",
                                  storage_directory="unused", risk_database="unused", trusted_rules={})
    host._creator_pid = os.getpid()
    host._storage_owner = None
    host._resource_owner = None
    host._storage_init_uncertain = False
    host._storage_entry_in_flight = False
    host._storage_init_in_flight = False
    host._delegate_init_in_flight = False
    host._storage_handoff_in_flight = False
    host._storage_cleanup_uncertain = False
    host._uncertain_storage = None
    host._cleanup_state = "ready"
    host._cleanup_uncertain = False
    host._cleanup_in_progress = False
    host._lifecycle_in_progress = 0
    host._cleanup_uncertain_resources = []
    host._stack = ExitStack()
    host._runtimes = []
    host._delegate_cleanup_unresolved = []
    host._observer_runtime = None
    host._observer_route = None
    host.order_port = host.service_host = host.engine = host.ledger = None
    host.control = host.facts = host.pump = host.market = None
    host.account = host.financial = host.component = None
    host._started = False
    host.stop_event = FakeEvent()
    return host


def enter_storage(host, synthetic):
    acquiring = ExitStack()
    manager = host_module._open_fenced_journal("unused", object())
    journal, fence = acquiring.enter_context(manager)
    host._storage_owner = threading.current_thread()
    host._stack.enter_context(acquiring)
    synthetic.active[id(host)] = (host, acquiring, manager, journal, fence)
    return manager, journal, fence


def test_host_accepts_only_receipt_bound_to_original_inner_owner(synthetic):
    host = fake_host()
    manager, journal, fence = enter_storage(host, synthetic)
    assert manager._inner_manager is synthetic.inner
    assert host.close() is True
    assert synthetic.inner.events == ["enter", "prepare", "exit", "consume"]
    assert host_module._fenced_journal_close_confirmed(manager, journal, fence)
    assert not synthetic.active and host.close() is True


@pytest.mark.parametrize("failure", ["no_receipt", "prepare", "exit", "consume", "interrupt",
                                     "prepare_interrupt", "consume_interrupt"])
def test_uncertain_close_retains_original_and_never_exits_twice(synthetic, failure):
    host = fake_host()
    manager, journal, fence = enter_storage(host, synthetic)
    if failure == "no_receipt":
        journal.receipt = None
    elif failure == "prepare":
        journal.fail_prepare = RuntimeError("synthetic")
    elif failure == "exit":
        synthetic.inner.exit_error = RuntimeError("synthetic")
    elif failure == "interrupt":
        synthetic.inner.exit_error = KeyboardInterrupt("synthetic")
    elif failure == "prepare_interrupt":
        journal.fail_prepare = KeyboardInterrupt("synthetic")
    elif failure == "consume_interrupt":
        journal.fail_consume = KeyboardInterrupt("synthetic")
    else:
        journal.fail_consume = RuntimeError("synthetic")
    assert host.close() is False
    assert host in synthetic.retained and synthetic.active[id(host)][2] is manager
    assert not host_module._fenced_journal_close_confirmed(manager, journal, fence)
    events = list(synthetic.inner.events)
    assert host.close() is False
    assert synthetic.inner.events == events and events.count("exit") <= 1


@pytest.mark.parametrize("altered", ["outer", "acquiring"])
def test_altered_exit_stack_does_not_unwind_unknown_callbacks(synthetic, altered):
    host = fake_host()
    enter_storage(host, synthetic)
    acquiring = synthetic.active[id(host)][1]
    callbacks = []
    (host._stack if altered == "outer" else acquiring).callback(lambda: callbacks.append(True))
    assert host.close() is False
    assert callbacks == [] and synthetic.inner.events == ["enter"]
    assert id(host) in synthetic.active and host in synthetic.retained


@pytest.mark.parametrize("inconsistency", ["missing_root", "missing_owner", "missing_both"])
def test_unproven_storage_root_or_owner_never_unwinds(synthetic, inconsistency):
    host = fake_host()
    enter_storage(host, synthetic)
    if inconsistency != "missing_owner":
        del synthetic.active[id(host)]
    if inconsistency != "missing_root":
        host._storage_owner = None
    assert host.close() is False
    assert synthetic.inner.events == ["enter"] and host in synthetic.retained


def test_global_unknown_blocks_empty_and_other_host_cleanup(synthetic):
    active = fake_host()
    enter_storage(active, synthetic)
    empty = fake_host()
    synthetic.probe["uncertain"].append((object(), "other owner"))
    assert empty.close() is False
    assert active.close() is False
    assert synthetic.inner.events == ["enter"]
    assert id(active) in synthetic.active


@pytest.mark.parametrize("pending", ["entry", "initialization", "delegate", "handoff", "root"])
def test_partial_storage_entry_cannot_be_reported_complete(synthetic, pending):
    host = fake_host()
    if pending == "root":
        synthetic.initializing[id(host)] = (host, object())
    else:
        setattr(host, {"entry": "_storage_entry_in_flight",
                       "initialization": "_storage_init_in_flight",
                       "delegate": "_delegate_init_in_flight",
                       "handoff": "_storage_handoff_in_flight"}[pending], True)
    assert host.close() is False
    assert host in synthetic.retained and host._cleanup_state == "uncertain"


@pytest.mark.parametrize("quarantine", [False, True])
def test_original_risk_core_manager_close_with_fake_resources(synthetic, monkeypatch, quarantine):
    helper_path = (Path(__file__).parents[2] / "bt_api/bt_api_risk/tests"
                   / "test_iter64_provisional_fence_entry_fake.py")
    spec = importlib.util.spec_from_file_location("iter64_host_fence_helpers", helper_path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    owners, uncertain, quarantined = {}, [], set()
    _SYNTHETIC.append((owners, uncertain, quarantined, helpers))
    monkeypatch.setattr(risk_module, "_FENCE_PROVISIONAL_OWNERS", owners)
    monkeypatch.setattr(risk_module, "_FENCE_ENTRY_UNCERTAIN", uncertain)
    monkeypatch.setattr(risk_module, "_FENCE_ENTRY_QUARANTINED_KEYS", quarantined)
    monkeypatch.setattr(risk_module, "_FENCE_ENTRY_QUARANTINED_ALL", False)
    monkeypatch.setattr(risk_module, "_PROCESS_LOCK_GUARD", FakeGuard())
    monkeypatch.setattr(risk_module, "_FENCE_PROVISIONAL_GUARD", FakeGuard())
    fake, lock, owner_file, active = helpers.fake_journal(monkeypatch)
    def fake_init(self, *args, **kwargs):
        self.__dict__.update(fake.__dict__)
    monkeypatch.setattr(risk_module.RiskOrderJournal, "__init__", fake_init)
    monkeypatch.setattr(risk_module, "_NATIVE_JOURNAL_INIT", fake_init)
    monkeypatch.setattr(risk_module.RiskOrderJournal, "open_fenced", _OPEN_FENCED)
    host = fake_host()
    acquiring = ExitStack()
    outer = host_module._open_fenced_journal("unused", helpers.SCOPE)
    journal, fence = acquiring.enter_context(outer)
    helpers.fake_borrow_database(monkeypatch)
    host._storage_owner = threading.current_thread()
    host._stack.enter_context(acquiring)
    synthetic.active[id(host)] = (host, acquiring, outer, journal, fence)
    if quarantine:
        risk_module._quarantine_fence_entry(("fake-path", helpers.SCOPE.key))
        assert host.close() is False
        assert (lock.releases, owner_file.closes) == (0, 0)
        assert owners and host in synthetic.retained
        assert not host_module._fenced_journal_close_confirmed(outer, journal, fence)
    else:
        assert host.close() is True
        assert (lock.releases, owner_file.closes) == (1, 1)
        assert not active and not owners
        assert host_module._fenced_journal_close_confirmed(outer, journal, fence)


def test_interrupted_after_inner_entry_retains_owner_without_cleanup(synthetic):
    host = fake_host()
    source, first = inspect.getsourcelines(host_module._open_fenced_journal_context)
    line = next(first + i for i, text in enumerate(source) if text.strip() == "journal, fence = result")
    old_trace = sys.gettrace()
    def trace(frame, event, _arg):
        if (event == "line" and frame.f_code is host_module._open_fenced_journal_context.__code__
                and frame.f_lineno == line):
            sys.settrace(None)
            raise KeyboardInterrupt("synthetic")
        return trace
    try:
        sys.settrace(trace)
        with pytest.raises(host_module.RiskServiceStartError,
                           match="RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN"):
            host._open_storage_owned()
    finally:
        sys.settrace(old_trace)
    assert host._storage_cleanup_uncertain
    assert synthetic.initializing[id(host)][2]._inner_manager is synthetic.inner
    assert host.close() is False and synthetic.inner.events == ["enter"]


@pytest.mark.parametrize("receipt", [True, False])
def test_initialization_failure_releases_only_after_receipt(synthetic, monkeypatch, receipt):
    host = fake_host()
    if not receipt:
        synthetic.inner.journal.receipt = None
    def fail(*_):
        raise risk_module.RiskOrderJournalError("PRETRADE_SCHEMA_MISMATCH")
    monkeypatch.setattr(pretrade, "PretradeLedger", fail)
    with pytest.raises(host_module.RiskServiceStartError) as error:
        host._open_storage_owned()
    assert synthetic.inner.events == ["enter", "prepare", "exit", "consume"]
    if receipt:
        assert error.value.code == "RISK_SERVICE_STORAGE_SCHEMA_INVALID"
        assert not synthetic.initializing and host._storage_owner is None
    else:
        assert error.value.code == "RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN"
        assert host._storage_cleanup_uncertain and id(host) in synthetic.initializing
        assert host.close() is False


@pytest.mark.parametrize("error", [RuntimeError("synthetic"),
                                   risk_module.RiskOrderJournalError("DELEGATE_SCHEMA_MISMATCH"),
                                   KeyboardInterrupt("synthetic")])
def test_delegate_init_exception_after_possible_commit_retains_fence(synthetic, monkeypatch, error):
    import bt_api_risk.gateway_delegate_journal as delegate

    host = fake_host()
    writes = []
    monkeypatch.setattr(pretrade, "PretradeLedger", lambda *_: object())
    def failed_constructor(*_):
        writes.append("commit_or_close_attempted")
        raise error
    monkeypatch.setattr(delegate, "RiskGatewayDelegateJournal", failed_constructor)
    with pytest.raises(host_module.RiskServiceStartError,
                       match="RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN"):
        host._open_storage_owned()
    assert writes == ["commit_or_close_attempted"]
    assert host._delegate_init_in_flight and host._storage_init_uncertain
    assert synthetic.inner.events == ["enter"]
    assert synthetic.initializing[id(host)][2]._inner_manager is synthetic.inner
    assert host.close() is False and synthetic.inner.events == ["enter"]


def test_delegate_result_delivery_interrupt_retains_owner(synthetic, monkeypatch):
    import bt_api_risk.gateway_delegate_journal as delegate

    host = fake_host()
    writes = []
    monkeypatch.setattr(pretrade, "PretradeLedger", lambda *_: object())
    monkeypatch.setattr(delegate, "RiskGatewayDelegateJournal",
                        lambda *_: writes.append("returned") or object())
    source, first = inspect.getsourcelines(host_module.RiskServiceHost._open_storage_owned)
    line = next(first + i for i, text in enumerate(source)
                if text.strip() == "self._delegate_init_in_flight = False")
    old_trace = sys.gettrace()
    def trace(frame, event, _arg):
        if (event == "line" and frame.f_code is host_module.RiskServiceHost._open_storage_owned.__code__
                and frame.f_lineno == line):
            sys.settrace(None)
            raise KeyboardInterrupt("synthetic")
        return trace
    try:
        sys.settrace(trace)
        with pytest.raises(host_module.RiskServiceStartError,
                           match="RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN"):
            host._open_storage_owned()
    finally:
        sys.settrace(old_trace)
    assert writes == ["returned"] and host._delegate_init_in_flight
    assert synthetic.inner.events == ["enter"] and host.close() is False


def test_pre_delegate_schema_failure_with_confirmed_fake_rollback_releases(synthetic, monkeypatch):
    import bt_api_risk.gateway_delegate_journal as delegate

    host = fake_host()
    inner = synthetic.inner
    journal = object.__new__(risk_module.RiskOrderJournal)
    journal._owner_id = "fake-owner"
    journal._assert_current = lambda *_: None
    journal._assert_current_in = lambda *_: None
    scope = risk_module.AccountScope("fake", "account", "test")
    fence = risk_module.RiskOrderWriterFence(scope, "fake-owner", 1, "a" * 64)
    inner.journal, inner.fence = journal, fence
    events = []

    class FakeCursor:
        def fetchone(self):
            return (1,)

        def fetchall(self):
            return [(999,)]

    class FakeConnection:
        def execute(self, sql, *_):
            assert sql.lstrip().startswith("SELECT ")
            events.append("read")
            return FakeCursor()

    @contextmanager
    def fake_transaction():
        events.append("begin")
        try:
            yield FakeConnection()
        except BaseException:
            events.append("rollback")
            raise
        finally:
            events.append("close")

    journal._transaction = fake_transaction
    monkeypatch.setattr(risk_module.RiskOrderJournal, "prepare_borrowed_fence_close",
                        lambda _self, manager, value: inner.events.append("prepare"))
    monkeypatch.setattr(risk_module.RiskOrderJournal, "consume_borrowed_fence_close",
                        lambda _self, manager, value: inner.events.append("consume")
                        or risk_module.RiskFenceExitCleanup.COMPLETE)
    monkeypatch.setattr(delegate, "RiskGatewayDelegateJournal",
                        lambda *_: pytest.fail("delegate initialization must not start"))
    with pytest.raises(host_module.RiskServiceStartError,
                       match="RISK_SERVICE_STORAGE_SCHEMA_INVALID"):
        host._open_storage_owned()
    assert events[0] == "begin" and events[-2:] == ["rollback", "close"]
    assert inner.events == ["enter", "prepare", "exit", "consume"]
    assert host._storage_owner is None and not synthetic.initializing
