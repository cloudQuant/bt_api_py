"""Singleton migration barrier blocks selected simulation operator effects."""

from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_fresh_observation import flags
from tests.unit.test_trading_calendar_mock_persistent import operation
from tests.unit.test_trading_calendar_mock_policy_transition import begin as begin_policy
from tests.unit.test_trading_calendar_scope_checks import batch_args

_FAIL_PER_ID_INTENT = """
from bt_api_py import trading_calendar_observation_run as runs
_real_migration_write = runs._write_once
def fail_id_intent(fd, chain, name, data):
    if os.environ.get('FAIL_MIGRATION_PER_ID') and name.startswith('scope-check-migration-') and name != 'scope-check-migration-pending.json':
        return ('NOT_COMMITTED', 'IO_ERROR')
    return _real_migration_write(fd, chain, name, data)
runs._write_once = fail_id_intent
"""


def test_pending_before_per_id_intent_blocks_selected_operator_and_recovers(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto", _FAIL_PER_ID_INTENT)
    operator = MockCalendarPublication(root=str(root), run_id=run_id)
    proposal = operator.prepare(candidate_id=operator._audit["derived_id"])
    assert proposal["status"] == "SAME"
    original = (root / "CURRENT.json").read_bytes()
    migration_id = "e" * 32
    env["FAIL_MIGRATION_PER_ID"] = "1"
    rc, failed, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-scope-migrate-begin",
            "--migration-id",
            migration_id,
            "--declare-old-writers-stopped",
        ),
    )
    assert rc != 0 and failed[-1]["status"] == "UNKNOWN", (failed, err)
    folder = root / "observation-runs"
    assert (folder / "scope-check-migration-pending.json").exists()
    assert not (folder / f"scope-check-migration-{migration_id}.json").exists()
    rc, competing, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-scope-migrate-begin",
            "--migration-id",
            "d" * 32,
            "--declare-old-writers-stopped",
        ),
    )
    assert rc != 0 and competing[-1]["migration_id"] == migration_id, (competing, err)
    calls = (tmp_path / "calls").read_bytes() if (tmp_path / "calls").exists() else b""
    rc, denied, err = command(env, *batch_args(root, run_id))
    assert rc != 0 and denied[-1]["reason"] == "SCOPE_MIGRATION_RECONCILE_ORIGINAL_ID", (
        denied,
        err,
    )
    assert denied[-1]["migration_id"] == migration_id
    rc, update, err = command(env, *operation(root, run_id, "mock-update", *flags()))
    assert rc != 0 and update[-1]["reason"] == "SCOPE_MIGRATION_RECONCILE_ORIGINAL_ID", (
        update,
        err,
    )
    rc, policy, err = begin_policy(env, root, run_id, "manual", "MOCK_barrier_policy")
    assert rc != 0 and policy[-1]["status"] == "UNKNOWN", (policy, err)
    for candidate in (
        operator,
        MockCalendarPublication(root=str(root), run_id=run_id, scope_migration_id=migration_id),
    ):
        denied_publish = candidate.publish(
            proposal=proposal,
            publication_id="MOCK_blocked_pub",
            version_id="MOCK_blocked_version",
            emit=lambda _: None,
        )
        assert (
            denied_publish["status"] == "UNKNOWN" and denied_publish["migration_id"] == migration_id
        )
    assert (
        (tmp_path / "calls").read_bytes() == calls
        if (tmp_path / "calls").exists()
        else calls == b""
    )
    assert (root / "CURRENT.json").read_bytes() == original
    env.pop("FAIL_MIGRATION_PER_ID")
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-scope-migrate-show", "--migration-id", migration_id)
    )
    assert rc != 0 and shown[-1]["reason"] == "MIGRATION_GATE_NOT_OBSERVED", (shown, err)
    rc, ready, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-scope-migrate-resume",
            "--migration-id",
            migration_id,
            "--declare-old-writers-stopped",
        ),
    )
    assert rc == 0 and ready[-1]["reason"] == "MIGRATION_READY", (ready, err)
    assert not (folder / "scope-check-migration-pending.json").exists()
    assert (root / "CURRENT.json").read_bytes() == original
