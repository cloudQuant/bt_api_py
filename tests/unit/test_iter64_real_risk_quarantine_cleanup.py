"""Pure in-memory tests for quarantine fixture cleanup decisions."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_py.service_runtime_thread import RuntimeSnapshot
from tests.integration import test_iter64_real_risk_source_quarantine as subject


def _closed(**changes):
    snapshot = RuntimeSnapshot("closed", False, False, None, (), 0, 0,
                               owner_cleanup_uncertainties=0,
                               unknown_registration_count=0, strict_unregister_ack=True)
    return replace(snapshot, **changes)


class _Strategy:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0
        self._pending_candidate_unknown = False
        self._pending_runtime = None
        self._runtime = SimpleNamespace(strict_cleanup_required=True)
        self.snapshot = _closed(state="running", thread_alive=True, healthy=True)

    def runtime_status(self):
        return self.snapshot

    def close(self, **kwargs):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        self.snapshot = self.outcome
        return self.outcome


def _admin(**changes):
    values = {"state": "registered", "healthy": True, "_require_unregister_ack": True,
              "cleanup_failures": 0, "unregister_uncertainties": 0, "unknown_registration_count": 0}
    values.update(changes)
    return SimpleNamespace(**values)


def _cleanup(strategy, *, uncertain=False, admin=None):
    return subject._cleanup_quarantine_fixture(
        object(), object(), _admin() if admin is None else admin, strategy, Path("/synthetic-not-read"),
        gateway_ready=True, risk_ready=True, client_uncertain=uncertain,
    )


@pytest.mark.parametrize("uncertain", [True, None, 0, 1, "", (), []])
def test_known_client_uncertainty_stops_before_all_checks(monkeypatch, uncertain):
    calls = []
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: calls.append("live"))
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(_closed())
    assert not _cleanup(strategy, uncertain=uncertain)
    assert calls == [] and strategy.calls == 0


@pytest.mark.parametrize("checks", [(False,), (True, False)])
def test_reporter_uncertainty_stops_before_client_close(monkeypatch, checks):
    checks = iter(checks)
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: next(checks))
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(_closed())
    assert not _cleanup(strategy)
    assert strategy.calls == 0 and calls == []


@pytest.mark.parametrize("outcome", [
    _closed(strict_unregister_ack=False), _closed(thread_alive=True),
    _closed(cleanup_failures=1), _closed(unregister_uncertainties=1),
    _closed(owner_cleanup_uncertainties=1), _closed(unknown_registration_count=1),
    KeyboardInterrupt("synthetic sensitive value"), SystemExit("synthetic sensitive value"),
])
def test_incomplete_client_close_stops_all_further_cleanup(monkeypatch, outcome):
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(outcome)
    assert not _cleanup(strategy)
    assert strategy.calls == 1 and calls == []


@pytest.mark.parametrize("field", [
    "cleanup_failures", "unregister_uncertainties", "owner_cleanup_uncertainties",
    "unknown_registration_count",
])
@pytest.mark.parametrize("value", [None, False, True, 0.0, (), [], "0"])
def test_invalid_count_cannot_authorize_further_cleanup(monkeypatch, field, value):
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(_closed(**{field: value}))
    assert not _cleanup(strategy)
    assert strategy.calls == 1 and calls == []


@pytest.mark.parametrize("field,value", [
    ("thread_alive", None), ("thread_alive", 0), ("strict_unregister_ack", None),
    ("strict_unregister_ack", 1), ("state", None), ("state", False),
])
def test_invalid_state_evidence_cannot_authorize_cleanup(monkeypatch, field, value):
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(_closed(**{field: value}))
    assert not _cleanup(strategy)
    assert strategy.calls == 1 and calls == []


@pytest.mark.parametrize("field", [
    "state", "thread_alive", "cleanup_failures", "unregister_uncertainties",
    "owner_cleanup_uncertainties", "unknown_registration_count", "strict_unregister_ack",
])
def test_missing_snapshot_field_cannot_authorize_cleanup(monkeypatch, field):
    snapshot = _closed()
    object.__delattr__(snapshot, field)
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(snapshot)
    assert not _cleanup(strategy)
    assert strategy.calls == 1 and calls == []


def test_wrong_snapshot_type_cannot_authorize_cleanup(monkeypatch):
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(SimpleNamespace(**vars(_closed())))
    assert not _cleanup(strategy)
    assert strategy.calls == 1 and calls == []


def test_async_reporter_uncertainty_after_client_close_stops_cleanup(monkeypatch):
    checks = iter((True, True, False))
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: next(checks))
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(_closed())
    assert not _cleanup(strategy)
    assert strategy.calls == 1 and calls == []


def test_complete_client_close_delegates_guarded_remaining_cleanup(monkeypatch):
    calls = []
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: calls.append("live") or True)
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup") or True)
    strategy = _Strategy(_closed())
    assert _cleanup(strategy)
    assert strategy.calls == 1 and calls == ["live", "live", "live", "cleanup"]


@pytest.mark.parametrize("field,value", [
    ("state", "close_failed"), ("state", None), ("healthy", False),
    ("_require_unregister_ack", False), ("_require_unregister_ack", 1),
    ("cleanup_failures", None), ("cleanup_failures", False),
    ("unregister_uncertainties", 1), ("unregister_uncertainties", None),
    ("unknown_registration_count", 1), ("unknown_registration_count", None),
])
def test_admin_uncertainty_blocks_strategy_close_and_all_stops(monkeypatch, field, value):
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(_closed())
    assert not _cleanup(strategy, admin=_admin(**{field: value}))
    assert strategy.calls == 0 and calls == []


@pytest.mark.parametrize("field", [
    "state", "healthy", "_require_unregister_ack", "cleanup_failures", "unregister_uncertainties",
    "unknown_registration_count",
])
def test_missing_admin_evidence_blocks_every_action(monkeypatch, field):
    admin = _admin()
    delattr(admin, field)
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(_closed())
    assert not _cleanup(strategy, admin=admin)
    assert strategy.calls == 0 and calls == []


@pytest.mark.parametrize("field,value", [
    ("_pending_candidate_unknown", True), ("_pending_candidate_unknown", None),
    ("_pending_runtime", object()),
])
def test_pending_strategy_evidence_blocks_every_action(monkeypatch, field, value):
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(_closed())
    setattr(strategy, field, value)
    assert not _cleanup(strategy)
    assert strategy.calls == 0 and calls == []


@pytest.mark.parametrize("changes", [
    {"unknown_registration_count": None}, {"unregister_uncertainties": 1},
    {"cleanup_failures": False}, {"owner_cleanup_uncertainties": None},
    {"healthy": False}, {"thread_alive": False}, {"strict_unregister_ack": None},
    {"error_code": "UNKNOWN"},
])
def test_uncertain_running_strategy_blocks_every_action(monkeypatch, changes):
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(_closed())
    strategy.snapshot = replace(strategy.snapshot, **changes)
    assert not _cleanup(strategy)
    assert strategy.calls == 0 and calls == []


def test_admin_uncertainty_after_strategy_close_blocks_delegation(monkeypatch):
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(subject, "_cleanup_owned_fixture", lambda *a, **k: calls.append("cleanup"))
    strategy = _Strategy(_closed())
    admin = _admin()
    original_close = strategy.close

    def close(**kwargs):
        snapshot = original_close(**kwargs)
        admin.unknown_registration_count = 1
        return snapshot

    strategy.close = close
    assert not _cleanup(strategy, admin=admin)
    assert strategy.calls == 1 and calls == []


def test_delegated_callback_rechecks_both_clients(monkeypatch):
    monkeypatch.setattr(subject, "_fixture_live", lambda *a, **k: True)
    strategy = _Strategy(_closed())
    admin = _admin()
    actions = []

    def fake_helper(*args, **kwargs):
        check = kwargs["pre_action"]
        assert check() is True
        actions.append("admin_closed")
        admin.state = "closed"
        assert check() is True
        strategy.snapshot = _closed(unknown_registration_count=1)
        if check() is True:
            actions.append("risk_stopped")
        return False

    monkeypatch.setattr(subject, "_cleanup_owned_fixture", fake_helper)
    assert not _cleanup(strategy, admin=admin)
    assert actions == ["admin_closed"]


@pytest.mark.parametrize("after_direct_uncertain", [False, True])
def test_shared_cleanup_and_risk_callback_together_are_in_memory(monkeypatch, after_direct_uncertain):
    from tests.integration import _iter64_web_real_host_support as helper

    actions = []
    strategy = _Strategy(_closed())
    admin = _admin()

    def admin_close():
        actions.append("admin_close")
        admin.state = "closed"
        if after_direct_uncertain:
            strategy.snapshot = _closed(unknown_registration_count=1)

    admin.close = admin_close

    class FakeProcess:
        def __init__(self, name):
            self.name = name
            self.exit_code = None

        def poll(self):
            return self.exit_code

        def send_signal(self, signal):
            actions.append(self.name + "_signal")

        def wait(self, timeout):
            self.exit_code = 0
            return 0

    gateway, risk = FakeProcess("gateway"), FakeProcess("risk")
    monkeypatch.setattr(helper, "_gateway_event_check", lambda *a, **k: True)
    monkeypatch.setattr(helper, "_write_gateway_control", lambda *a, **k: actions.append("gateway_stop") or True)
    result = subject._cleanup_quarantine_fixture(
        gateway, risk, admin, strategy, Path("/synthetic-not-read"),
        gateway_ready=True, risk_ready=True, client_uncertain=False,
    )
    if after_direct_uncertain:
        assert not result and actions == ["admin_close"]
    else:
        assert result and actions == ["admin_close", "risk_signal", "gateway_stop"]
    assert strategy.calls == 1


def test_scenario_failure_suppresses_exception_text(monkeypatch, capsys):
    monkeypatch.setattr(subject, "_fixture_root", lambda: Path("/synthetic-not-read"))

    def fail(root):
        raise RuntimeError("synthetic sensitive value")

    monkeypatch.setattr(subject, "_exercise_source_quarantine", fail)
    with pytest.raises(pytest.fail.Exception) as error:
        subject.test_real_risk_process_keeps_source_quarantined_permit()
    assert str(error.value) == "SYNTHETIC_RISK_SCENARIO_FAILED"
    assert "synthetic sensitive value" not in capsys.readouterr().out


def test_identity_write_failure_prints_only_valid_synthetic_pids(monkeypatch, capsys):
    from tests.integration import _iter64_web_real_host_support as helper

    def fail_write(*args):
        raise RuntimeError("synthetic sensitive value")

    monkeypatch.setattr(helper, "_private_write", fail_write)
    monkeypatch.setattr(helper, "_RETAINED_FIXTURES", {})
    root = Path("/synthetic-not-read")
    gateway, risk = SimpleNamespace(pid=123), SimpleNamespace(pid=456)
    assert not subject._record_cleanup(root, True, gateway, risk, None, None, object())
    output = capsys.readouterr().out
    assert 'root="/synthetic-not-read" gateway_pid=123 risk_pid=456' in output
    assert "pid_may_be_reused=true stop_authorized=false" in output
    assert "synthetic sensitive value" not in output
    assert str(root) in helper._RETAINED_FIXTURES


@pytest.mark.parametrize("pid", [None, False, True, 0, -1, "123", 123.0])
def test_invalid_pid_is_never_reported_as_a_pid(pid, capsys):
    subject._emit_cleanup_identity(Path("/synthetic-not-read"), SimpleNamespace(pid=pid), None)
    output = capsys.readouterr().out
    assert "gateway_pid=unknown risk_pid=unknown" in output


def test_identity_record_and_pid_exceptions_suppress_text(monkeypatch, capsys):
    class BrokenProcess:
        @property
        def pid(self):
            raise KeyboardInterrupt("synthetic sensitive value")

    def fail_record(*args):
        raise SystemExit("synthetic sensitive value")

    retained = {}
    monkeypatch.setattr(subject, "_record_fixture_outcome", fail_record)
    monkeypatch.setattr(subject, "_RETAINED_PRESTART", retained)
    root = Path("/synthetic-not-read")
    assert not subject._record_cleanup(root, True, BrokenProcess(), None, None, None, None)
    output = capsys.readouterr().out
    assert "gateway_pid=unknown risk_pid=unknown" in output
    assert "synthetic sensitive value" not in output
    assert str(root) in retained


def test_seed_close_failure_does_not_retry_or_claim_fence(monkeypatch):
    from bt_api_risk.pretrade import ledger as ledger_module

    from bt_api_py import risk_service_host

    calls = []

    class FakeWriter:
        def __enter__(self):
            calls.append("enter")
            return object(), object()

        def __exit__(self, *args):
            calls.append("close")
            raise RuntimeError("synthetic sensitive value")

    monkeypatch.setattr(risk_service_host, "_open_fenced_journal", lambda *a: FakeWriter())
    monkeypatch.setattr(ledger_module, "PretradeLedger", lambda *a: object())
    retained = {}
    monkeypatch.setattr(subject, "_RETAINED_SEED_WRITERS", retained)
    path = Path("/synthetic-not-read")
    with pytest.raises(RuntimeError, match="^SYNTHETIC_SEED_WRITER_UNCERTAIN$"):
        subject._fenced_seed_step(path, object(), lambda ledger: calls.append("seed"))
    assert calls == ["enter", "seed", "close"]
    assert str(path) in retained
