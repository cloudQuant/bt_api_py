"""Iteration 63 E095: audited operator reconciliation of an abandoned session record.

User decision 2026-11-10: an operator command takes the account-session OS lock
(proving the old owner is gone), refuses while the journal has DISPATCHING or
UNKNOWN commands or an in-flight settlement confirmation, and records an
audited entry with operator and reason before the record is closed.
"""

import json
import os
import stat
from types import SimpleNamespace

import pytest
from bt_api_base.account_protocol import AccountKey
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
    inspect_unresolved_dispatch,
)
from bt_api_gateway.session_guard import (
    LocalAccountSessionGuard,
    LocalAccountSessionGuardError,
    reconcile_abandoned_account_session,
)

from bt_api_py import gateway_operator_application as application
from bt_api_py import gateway_operator_cli
from bt_api_py.ctp_gateway_trading import (
    CtpGatewayTradingSettings,
    reconcile_ctp_gateway_account_session,
)
from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplicationError,
    GatewayOperatorLaunchConfig,
    GatewayOperatorProfileFactory,
)
from bt_api_py.gateway_storage_trust import GatewayStorageTrustError

pytestmark = pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX lock and permissions")

_ZERO = {"commands_dispatching": 0, "commands_unknown": 0}


def _abandon(directory, account="account", owner="old-owner"):
    """Leave an active record as a crashed owner would: lock gone, record active."""
    guard = LocalAccountSessionGuard(directory, account, owner)
    generation = guard.acquire()
    os.close(guard._fd)  # the process died: the OS dropped the lock, nobody wrote "closed"
    guard._fd = None
    return guard.lock_path, generation


def _record(path):
    return json.loads(path.read_text())


def _audit(directory):
    files = sorted(directory.glob("account-*.reconciliations.jsonl"))
    return [json.loads(line) for path in files for line in path.read_text().splitlines()]


def _reconcile(directory, evidence=lambda: dict(_ZERO), **kwargs):
    return reconcile_abandoned_account_session(
        directory, "account", operator_ref=kwargs.pop("operator_ref", "ops-alice"),
        reason=kwargs.pop("reason", "gateway crashed; journal checked"),
        blocking_evidence=evidence, clock=lambda: 1234.5, **kwargs,
    )


@pytest.fixture
def guards(tmp_path):
    directory = tmp_path / "guards"
    directory.mkdir(mode=0o700)
    return directory


def test_abandoned_record_blocks_acquire_until_reconciled_with_audit(guards):
    lock_path, generation = _abandon(guards)
    with pytest.raises(LocalAccountSessionGuardError, match="needs reconciliation") as refused:
        LocalAccountSessionGuard(guards, "account", "new-owner").acquire()
    assert refused.value.code == "SESSION_RECONCILIATION_REQUIRED"

    result = _reconcile(guards)

    assert result["status"] == "RECONCILED"
    assert result["abandoned_generation"] == generation
    assert result["evidence"] == _ZERO
    record = _record(lock_path)
    assert record["status"] == "closed" and record["generation"] == generation
    assert record["owner_ref"] == "old-owner"
    (entry,) = _audit(guards)
    assert entry == {
        "abandoned_generation": generation,
        "abandoned_owner_ref": "old-owner",
        "account_digest": result["account_digest"],
        "evidence": _ZERO,
        "operator_ref": "ops-alice",
        "reason": "gateway crashed; journal checked",
        "reconciled_at": 1234.5,
        "version": 1,
    }
    audit_path = next(guards.glob("account-*.reconciliations.jsonl"))
    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600
    successor = LocalAccountSessionGuard(guards, "account", "new-owner")
    assert successor.acquire() != generation
    successor.release()


def test_live_owner_is_refused_before_any_evidence_is_read(guards):
    owner = LocalAccountSessionGuard(guards, "account", "live-owner")
    owner.acquire()
    calls = []
    try:
        with pytest.raises(LocalAccountSessionGuardError) as refused:
            _reconcile(guards, evidence=lambda: calls.append(1) or dict(_ZERO))
        assert refused.value.code == "SESSION_ALREADY_OWNED"
        assert calls == [] and _audit(guards) == []
        owner.assert_owned()
    finally:
        owner.release()


@pytest.mark.parametrize("blocking", ["commands_dispatching", "commands_unknown"])
def test_unresolved_work_keeps_the_record_active_and_unaudited(guards, blocking):
    lock_path, generation = _abandon(guards)
    evidence = dict(_ZERO, **{blocking: 2})

    with pytest.raises(LocalAccountSessionGuardError) as refused:
        _reconcile(guards, evidence=lambda: evidence)

    assert refused.value.code == "SESSION_RECONCILIATION_BLOCKED"
    assert refused.value.evidence == dict(sorted(evidence.items()))
    assert _record(lock_path) == dict(_record(lock_path), status="active", generation=generation)
    assert _audit(guards) == []
    # The lock was released again: a later reconciliation can still run.
    assert _reconcile(guards)["status"] == "RECONCILED"


def test_evidence_failure_leaves_the_record_active_and_unlocked(guards):
    lock_path, _ = _abandon(guards)

    def broken():
        raise DirectCommandJournalError("DATABASE_UNAVAILABLE")

    with pytest.raises(DirectCommandJournalError, match="DATABASE_UNAVAILABLE"):
        _reconcile(guards, evidence=broken)
    assert _record(lock_path)["status"] == "active" and _audit(guards) == []
    assert _reconcile(guards)["status"] == "RECONCILED"


def test_closed_or_missing_record_needs_no_reconciliation(guards):
    assert _reconcile(guards)["status"] == "NOT_REQUIRED"
    guard = LocalAccountSessionGuard(guards, "account", "owner")
    guard.acquire()
    guard.release()
    assert _reconcile(guards)["status"] == "NOT_REQUIRED"
    assert _audit(guards) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("operator_ref", ""), ("operator_ref", "ops\nforged"), ("reason", " "),
     ("reason", "x" * 1025), ("operator_ref", "o" * 257)],
)
def test_operator_and_reason_must_be_bounded_printable_text(guards, field, value):
    lock_path, _ = _abandon(guards)
    with pytest.raises(ValueError):
        _reconcile(guards, **{field: value})
    assert _record(lock_path)["status"] == "active" and _audit(guards) == []


@pytest.mark.parametrize("evidence", [{}, {"commands": -1}, {"commands": True}, {"": 0}])
def test_malformed_evidence_is_refused(guards, evidence):
    lock_path, _ = _abandon(guards)
    with pytest.raises(ValueError):
        _reconcile(guards, evidence=lambda: evidence)
    assert _record(lock_path)["status"] == "active"


def test_symlinked_audit_file_is_refused(guards, tmp_path):
    lock_path, _ = _abandon(guards)
    digest = lock_path.name.removeprefix("account-").removesuffix(".session-lock")
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    (guards / f"account-{digest}.reconciliations.jsonl").symlink_to(target)
    with pytest.raises(LocalAccountSessionGuardError) as refused:
        _reconcile(guards)
    assert refused.value.code == "SESSION_PATH_CHANGED"
    assert _record(lock_path)["status"] == "active" and target.read_text() == ""


# -- journal evidence ---------------------------------------------------------


@pytest.fixture
def journal(tmp_path, guards):
    guard = LocalAccountSessionGuard(guards, "account", "owner")
    generation = guard.acquire()
    path = tmp_path / "journal.sqlite"
    journal = GatewayDirectCommandJournal.create(path, "account", generation=generation,
                                                 clock=lambda: 100.0)
    yield journal, path
    guard.release()


def test_journal_inspection_counts_open_dispatches_only(journal):
    journal, path = journal
    clean = inspect_unresolved_dispatch(path, "account")
    assert set(clean) == {
        "commands_dispatching", "commands_unknown", "cancels_dispatching", "cancels_unknown",
        "settlements_attempting", "settlements_unknown",
    }
    assert not any(clean.values())
    reserved = journal.reserve("one", "reserved-only", "principal", {"symbol": "X"})
    assert reserved.status == "RESERVED"
    assert not any(inspect_unresolved_dispatch(path, "account").values())
    started = journal.reserve("one", "dispatched", "principal", {"symbol": "X"})
    journal.begin_dispatch("one", "dispatched", started.reservation_id, started.digest,
                           "principal", started.version, {"exchange": "TEST"}, ("key-1",))
    assert inspect_unresolved_dispatch(path, "account") == dict(clean, commands_dispatching=1)


def test_journal_inspection_fails_closed_on_wrong_account_or_path(journal, tmp_path):
    _, path = journal
    for account, target, code in (
        ("other-account", path, "ACCOUNT_MISMATCH"),
        ("account", tmp_path / "missing.sqlite", "DATABASE_UNAVAILABLE"),
        ("account", tmp_path, "DATABASE_UNAVAILABLE"),
    ):
        with pytest.raises(DirectCommandJournalError, match=code):
            inspect_unresolved_dispatch(target, account)
    link = tmp_path / "link.sqlite"
    link.symlink_to(path)
    with pytest.raises(DirectCommandJournalError, match="DATABASE_UNAVAILABLE"):
        inspect_unresolved_dispatch(link, "account")


# -- CTP profile, operator application and CLI --------------------------------


def _ctp_settings(root):
    key = AccountKey("ctp", "simnow", "simnow", "00001", "09999")
    return CtpGatewayTradingSettings(
        key, key.account_key_id, root / "guard", "owner", root / "journal.sqlite",
        "journal-1", 0, root / "flow", "source", "entitlement",
    )


@pytest.fixture
def ctp_root(tmp_path):
    root = tmp_path / "gateway"
    root.mkdir(mode=0o700)
    for name in ("guard", "flow"):
        (root / name).mkdir(mode=0o700)
    registry = root / "registry.sqlite"
    registry.write_bytes(b"")
    os.chmod(registry, 0o600)
    return root, registry


def test_ctp_reconciliation_uses_the_profile_journal_and_storage_check(ctp_root):
    root, registry = ctp_root
    settings = _ctp_settings(root)
    owner = LocalAccountSessionGuard(root / "guard", settings.account_scope, "owner")
    generation = owner.acquire()
    journal = GatewayDirectCommandJournal.create(settings.journal_path, settings.account_scope,
                                                 generation=generation, clock=lambda: 100.0)
    started = journal.reserve("one", "k", "principal", {"symbol": "X"})
    journal.begin_dispatch("one", "k", started.reservation_id, started.digest, "principal",
                           started.version, {"exchange": "TEST"}, ("key-1",))
    os.close(owner._fd)
    owner._fd = None
    os.chmod(settings.journal_path, 0o600)

    with pytest.raises(LocalAccountSessionGuardError) as blocked:
        reconcile_ctp_gateway_account_session(settings, registry, operator_ref="ops", reason="r")
    assert blocked.value.code == "SESSION_RECONCILIATION_BLOCKED"
    assert blocked.value.evidence["commands_dispatching"] == 1

    os.chmod(root / "guard", 0o777)  # noqa: S103 - the refusal under test
    try:
        with pytest.raises(GatewayStorageTrustError) as untrusted:
            reconcile_ctp_gateway_account_session(settings, registry, operator_ref="ops",
                                                  reason="r")
        assert untrusted.value.code == "STORAGE_WRITABLE_BY_OTHERS"
    finally:
        os.chmod(root / "guard", 0o700)
    assert _record(owner.lock_path)["status"] == "active" and _audit(root / "guard") == []


def test_ctp_reconciliation_closes_a_clean_abandoned_session(ctp_root):
    root, registry = ctp_root
    settings = _ctp_settings(root)
    owner = LocalAccountSessionGuard(root / "guard", settings.account_scope, "owner")
    generation = owner.acquire()
    GatewayDirectCommandJournal.create(settings.journal_path, settings.account_scope,
                                       generation=generation, clock=lambda: 100.0)
    os.close(owner._fd)
    owner._fd = None
    os.chmod(settings.journal_path, 0o600)

    result = reconcile_ctp_gateway_account_session(
        settings, registry, operator_ref="ops", reason="crash at 01:10, venue checked"
    )

    assert result["status"] == "RECONCILED" and not any(result["evidence"].values())
    successor = LocalAccountSessionGuard(root / "guard", settings.account_scope, "owner")
    successor.acquire()
    successor.release()


def test_factory_reconcile_hook_must_be_callable():
    with pytest.raises(GatewayOperatorApplicationError, match="INVALID_PROFILE"):
        GatewayOperatorProfileFactory("p", "trading_owner", lambda config: None,
                                      reconcile_session="not callable")


def test_application_reconciliation_requires_a_factory_hook(monkeypatch):
    config = GatewayOperatorLaunchConfig("p", object())
    with pytest.raises(GatewayOperatorApplicationError, match="INVALID_LAUNCH_CONFIG"):
        application.reconcile_gateway_operator_account_session(
            config, operator_ref="ops", reason="r")
    monkeypatch.setattr(application, "ServiceConfig", object)
    without = GatewayOperatorProfileFactory("p", "trading_owner", lambda config: None)
    monkeypatch.setattr(application, "_configured_factory", lambda config: without)
    with pytest.raises(GatewayOperatorApplicationError, match="RECONCILIATION_UNSUPPORTED"):
        application.reconcile_gateway_operator_account_session(
            config, operator_ref="ops", reason="r")
    calls = []
    hooked = GatewayOperatorProfileFactory(
        "p", "trading_owner", lambda config: None,
        reconcile_session=lambda cfg, op, why: calls.append((cfg, op, why)) or {"status": "X"},
    )
    monkeypatch.setattr(application, "_configured_factory", lambda config: hooked)
    assert application.reconcile_gateway_operator_account_session(
        config, operator_ref="ops", reason="r") == {"status": "X"}
    assert calls == [(config.service_config, "ops", "r")]


def _cli(monkeypatch, capsys, argv, outcome):
    monkeypatch.setattr(gateway_operator_cli, "load_gateway_operator_launch_config",
                        lambda path: SimpleNamespace(path=path))
    seen = []

    def reconcile(config, *, operator_ref, reason):
        seen.append((operator_ref, reason))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(gateway_operator_cli, "reconcile_gateway_operator_account_session",
                        reconcile)
    code = gateway_operator_cli.main(argv)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    return code, json.loads(lines[-1]), seen


_ARGS = ["--config", "operator.yaml", "--reconcile-session", "--operator", "ops",
         "--reason", "crash"]


def test_cli_reports_reconciliation_without_account_identity(monkeypatch, capsys):
    code, report, seen = _cli(monkeypatch, capsys, _ARGS, {
        "status": "RECONCILED", "account_digest": "a" * 64, "abandoned_generation": "b" * 32,
        "evidence": dict(_ZERO)})
    assert code == 0 and seen == [("ops", "crash")]
    assert report == {"status": "RECONCILED", "mode": "session_reconciliation",
                      "evidence": _ZERO, "account_digest_prefix": "a" * 12}


def test_cli_reports_blocked_evidence_and_typed_codes(monkeypatch, capsys):
    blocked = LocalAccountSessionGuardError("SESSION_RECONCILIATION_BLOCKED", "x")
    blocked.evidence = dict(_ZERO, commands_unknown=1)
    code, report, _ = _cli(monkeypatch, capsys, _ARGS, blocked)
    assert code == 3
    assert report == {"status": "REFUSED", "mode": "session_reconciliation",
                      "error_code": "SESSION_RECONCILIATION_BLOCKED",
                      "evidence": dict(_ZERO, commands_unknown=1)}
    code, report, _ = _cli(monkeypatch, capsys, _ARGS, RuntimeError("path /secret/x"))
    assert code == 3 and report["error_code"] == "RECONCILIATION_FAILED"
    assert "secret" not in json.dumps(report)
    code, report, _ = _cli(monkeypatch, capsys, _ARGS,
                           GatewayOperatorApplicationError("RECONCILIATION_UNSUPPORTED"))
    assert code == 2 and report["error_code"] == "RECONCILIATION_UNSUPPORTED"
    code, report, _ = _cli(monkeypatch, capsys, _ARGS, ValueError("bad"))
    assert code == 2 and report["error_code"] == "INVALID_RECONCILIATION_INPUT"


@pytest.mark.parametrize("argv", [
    ["--config", "c", "--reconcile-session", "--operator", "ops"],
    ["--config", "c", "--reconcile-session", "--reason", "r"],
    ["--config", "c", "--reconcile-session", "--operator", "o", "--reason", "r", "--dry-run"],
    ["--config", "c", "--operator", "o"],
])
def test_cli_rejects_incomplete_or_mixed_reconciliation_arguments(argv):
    with pytest.raises(SystemExit) as exited:
        gateway_operator_cli.main(argv)
    assert exited.value.code == 2


# -- the operator learns which step is next -----------------------------------


@pytest.mark.parametrize("code", ["SESSION_RECONCILIATION_REQUIRED", "SESSION_ALREADY_OWNED"])
def test_construction_error_surfaces_only_allowlisted_codes(code):
    from bt_api_py.service_components import (
        ComponentResources,
        ServiceComponentError,
        ServiceResourceConstructionError,
    )

    partial = ComponentResources.owned({"binding": None}, ready=lambda: False, close=lambda: None)
    assert ServiceResourceConstructionError(partial, code=code).code == code
    assert ServiceResourceConstructionError(partial).code == "RESOURCE_CONSTRUCTION_FAILED"
    assert ServiceResourceConstructionError(
        partial, monitor_incompatible=True, code=code).code == "MONITOR_INCOMPATIBLE"
    for invalid in ("SESSION_IO_ERROR", "anything", 1):
        with pytest.raises(ServiceComponentError, match="INVALID_PARTIAL_COMPONENT_RESOURCES"):
            ServiceResourceConstructionError(partial, code=invalid)
