"""In-memory context managers only; no host, database, lock, or socket starts."""

import gc
import inspect
import os
import sys
import threading
import weakref
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest

import bt_api_py.risk_service_host as module
import bt_api_risk.pretrade as pretrade
from bt_api_risk.risk_order_journal import RiskOrderJournalError


class _Manager:
    def __init__(self):
        self.events = []
        self.journal, self.fence = object(), object()

    def __enter__(self):
        self.events.append("enter")
        return self.journal, self.fence

    def __exit__(self, *_args):
        self.events.append("exit")


class _BrokenHandoff:
    def enter_context(self, _context):
        raise RuntimeError("synthetic handoff failure")


class _BrokenExitManager(_Manager):
    def __exit__(self, *_args):
        self.events.append("exit")
        raise RuntimeError("synthetic unknown cleanup")


class _PublishDuringHandoff:
    def __init__(self):
        self.stack = ExitStack()

    def enter_context(self, context):
        result = self.stack.enter_context(context)
        with module._PROBE_GUARD:
            module._PROBE_STATE["uncertain"].append((object(), object(), "synthetic other host"))
        return result


def _host(monkeypatch, initializer, *, stack=None):
    from bt_api_risk.risk_order_journal import RiskFenceExitCleanup

    monkeypatch.setattr(module, "_RETAINED_HOSTS", set())
    monkeypatch.setattr(module, "_STORAGE_INIT_ROOTS", {})
    monkeypatch.setattr(module, "_STORAGE_ACTIVE_ROOTS", {})
    monkeypatch.setattr(module, "_PROBE_GUARD", threading.Lock())
    monkeypatch.setattr(module, "_PROBE_STATE", {"active": False, "uncertain": []})
    monkeypatch.setattr(module, "_require_supported_r04", lambda _config: None)
    monkeypatch.setattr(module, "ensure_private_directory", lambda _path: None)
    monkeypatch.setattr(module, "restrict_file", lambda _path: None)
    # A completed storage open holds one exact _FencedJournalManager whose exit
    # delivers a COMPLETE receipt. A duck-typed manager cannot prove a close, so
    # the host would (correctly) classify every cleanup as uncertain; build the
    # real receipt-bearing shape instead of relaxing the production check.
    events = []
    journal = SimpleNamespace(
        prepare_borrowed_fence_close=lambda *_args: None,
        consume_borrowed_fence_close=lambda *_args: RiskFenceExitCleanup.COMPLETE,
    )
    fence = object()

    class _Inner:
        def __enter__(self):
            return journal, fence

        def __exit__(self, *_args):
            events.append("exit")
            return False

    inner = _Inner()
    manager = module._FencedJournalManager(Path("synthetic-risk.sqlite"), object())

    def _open_context(outer, _path, _scope):
        events.append("enter")
        outer._inner_manager = inner
        outer._binding = (inner, journal, fence)
        return journal, fence

    manager.events = events
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_args: manager)
    monkeypatch.setattr(module, "_open_fenced_journal_context", _open_context)
    monkeypatch.setattr(pretrade, "PretradeLedger", initializer)
    monkeypatch.setattr(pretrade, "PolicyStore", lambda _ledger: object())
    monkeypatch.setattr(pretrade, "RuleRegistry", lambda _rules: object())
    import bt_api_risk.gateway_delegate_journal as delegate
    monkeypatch.setattr(delegate, "RiskGatewayDelegateJournal", lambda *_args: object())
    host = object.__new__(module.RiskServiceHost)
    host.config = SimpleNamespace(
        provider="fake", account_scope="account", environment="test",
        storage_directory="unused", risk_database="unused", trusted_rules={},
    )
    host._stack = stack if stack is not None else ExitStack()
    host._creator_pid = os.getpid()
    host._storage_owner = None
    host._resource_owner = None
    host._storage_init_uncertain = False
    host._storage_entry_in_flight = False
    host._storage_init_in_flight = False
    host._delegate_init_in_flight = False
    host._storage_handoff_in_flight = False
    host._storage_cleanup_uncertain = False
    host._cleanup_uncertain = False
    host._cleanup_in_progress = False
    host._lifecycle_in_progress = 0
    host._cleanup_uncertain_resources = []
    host._cleanup_state = "ready"
    host._uncertain_storage = None
    host.ledger = None
    host._delegate_cleanup_unresolved = []
    host.order_port = None
    host.service_host = None
    host._runtimes = []
    host._started = False
    host.stop_event = threading.Event()
    host._check_storage_acquisition_ready = lambda: None
    return host, manager


@pytest.mark.parametrize("error", [
    # The real PretradeLedger.__init__ wraps every unproven commit as this
    # code; an interruption stays UNKNOWN because it may replace that wrap.
    RiskOrderJournalError("PRETRADE_INIT_COMMIT_UNCERTAIN"),
    KeyboardInterrupt(),
])
def test_unknown_initializer_retains_original_entered_stack(monkeypatch, error):
    def initialize(*_args):
        raise error

    host, manager = _host(monkeypatch, initialize)
    reference = weakref.ref(host)
    key = id(host)
    with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN"):
        host._open_storage_owned()
    assert manager.events == ["enter"]
    assert host._storage_init_uncertain
    assert module._STORAGE_INIT_ROOTS[key][1] is not host._stack
    assert module._STORAGE_INIT_ROOTS[key][2] is manager
    del host
    gc.collect()
    assert reference() is not None
    assert manager.events == ["enter"]


def test_unclassified_initializer_error_releases_the_exact_stack(monkeypatch):
    # PretradeLedger.__init__ re-raises an unclassified body error untouched
    # only after the transaction's ROLLBACK and connection close returned
    # normally; an unproven outcome is wrapped as PRETRADE_INIT_COMMIT_UNCERTAIN
    # (covered above). The host relies on that boundary contract: an ordinary
    # escaping error proves no unproven state remains and is a finite schema
    # failure, so the exact stack is closed instead of retained.
    def initialize(*_args):
        raise RuntimeError("unclassified but proven-finite initializer error")

    host, manager = _host(monkeypatch, initialize)
    with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_STORAGE_SCHEMA_INVALID"):
        host._open_storage_owned()
    assert manager.events == ["enter", "exit"]
    assert not module._STORAGE_INIT_ROOTS
    assert host._storage_owner is None
    assert host._storage_init_uncertain is False
    assert host._cleanup_uncertain is False


def test_prewrite_finite_failure_closes_once_and_releases_provisional_root(monkeypatch):
    def initialize(*_args):
        raise RiskOrderJournalError("PRETRADE_LEGACY_QUIESCENCE_REQUIRED")

    host, manager = _host(monkeypatch, initialize)
    with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_STORAGE_SCHEMA_INVALID"):
        host._open_storage_owned()
    assert manager.events == ["enter", "exit"]
    assert not module._STORAGE_INIT_ROOTS
    assert host._storage_owner is None


def test_handoff_failure_never_closes_or_loses_original_stack(monkeypatch):
    host, manager = _host(monkeypatch, lambda *_args: object(), stack=_BrokenHandoff())
    with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN"):
        host._open_storage_owned()
    assert host._storage_init_uncertain
    assert manager.events == ["enter"]
    assert module._STORAGE_INIT_ROOTS[id(host)][2] is manager


def test_other_host_unknown_during_handoff_is_not_reported_as_success(monkeypatch):
    host, manager = _host(monkeypatch, lambda *_args: object(), stack=_PublishDuringHandoff())
    with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN"):
        host._open_storage_owned()
    assert manager.events == ["enter"]
    assert module._STORAGE_INIT_ROOTS[id(host)][2] is manager
    assert host._cleanup_uncertain and host._storage_init_uncertain
    assert not module._STORAGE_ACTIVE_ROOTS


def test_active_owner_remains_rooted_when_other_host_later_becomes_unknown(monkeypatch):
    host, manager = _host(monkeypatch, lambda *_args: object())
    host._open_storage_owned()
    key = id(host)
    reference = weakref.ref(host)
    assert module._STORAGE_ACTIVE_ROOTS[key][2] is manager
    with module._PROBE_GUARD:
        module._PROBE_STATE["uncertain"].append((object(), object(), "synthetic other host"))
    assert host.close() is False
    del host
    gc.collect()
    assert reference() is not None
    assert manager.events == ["enter"]
    assert key in module._STORAGE_ACTIVE_ROOTS


@pytest.mark.parametrize("target", [
    "entered = True",
    "journal, fence = result",
    "_STORAGE_INIT_ROOTS[id(self)] = (self, acquiring, manager, result, journal, fence)",
    "self._storage_entry_in_flight = False",
])
def test_interrupt_after_enter_never_unwinds_entered_manager(monkeypatch, target):
    called = []
    host, manager = _host(monkeypatch, lambda *_args: called.append(True))
    source, start = inspect.getsourcelines(module.RiskServiceHost._open_storage_owned)
    target_line = next(start + i for i, line in enumerate(source)
                       if line.strip() == target and start + i > start + 30)
    old_trace = sys.gettrace()
    fired = []

    def trace(frame, event, _arg):
        if (event == "line" and frame.f_code is module.RiskServiceHost._open_storage_owned.__code__
                and frame.f_lineno == target_line and not fired):
            fired.append(True)
            sys.settrace(None)
            raise KeyboardInterrupt()
        return trace

    try:
        sys.settrace(trace)
        with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN"):
            host._open_storage_owned()
    finally:
        sys.settrace(old_trace)
    assert fired == [True]
    assert called == []
    assert manager.events == ["enter"]
    assert host._storage_cleanup_uncertain
    assert module._STORAGE_INIT_ROOTS[id(host)][2] is manager
    with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN"):
        host._open_storage_owned()
    assert manager.events == ["enter"]
    reference = weakref.ref(host)
    del host
    gc.collect()
    assert reference() is not None
    assert manager.events == ["enter"]


def test_cleanup_failure_is_unknown_and_is_not_retried(monkeypatch):
    def initialize(*_args):
        raise RiskOrderJournalError("PRETRADE_LEGACY_QUIESCENCE_REQUIRED")

    host, _manager = _host(monkeypatch, initialize)
    manager = _BrokenExitManager()
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_args: manager)
    with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN"):
        host._open_storage_owned()
    assert manager.events == ["enter", "exit"]
    assert host._storage_cleanup_uncertain
    assert module._STORAGE_INIT_ROOTS[id(host)][2] is manager
    assert host._cleanup_uncertain


def test_success_transfers_original_stack_and_closes_it_once(monkeypatch):
    host, manager = _host(monkeypatch, lambda *_args: object())
    host._open_storage_owned()
    assert not module._STORAGE_INIT_ROOTS
    assert module._STORAGE_ACTIVE_ROOTS[id(host)][2] is manager
    assert manager.events == ["enter"]
    assert host.close() is True  # Only the manager created in this test is closed.
    assert manager.events == ["enter", "exit"]
    assert not module._STORAGE_ACTIVE_ROOTS
