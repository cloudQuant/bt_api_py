"""Read-only legacy scope manifest checks in a crowded simulation directory."""

from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation
from tests.unit.test_trading_calendar_scope_checks import batch_args


def test_legacy_manifest_scans_crowded_directory_and_rejects_omissions(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    rc, rows, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (rows, err)
    batch_id = rows[-1]["batch_id"]
    directory = root / "observation-runs"
    head = (directory / "scope-check-head.json").read_bytes()
    current = (root / "CURRENT.json").read_bytes()
    for number in range(10001):
        (directory / f"unrelated-{number:05}.txt").touch(mode=0o600)
    base = ("--batch-id", batch_id)
    rc, missing, err = command(env, *operation(root, run_id, "mock-batch-show", *base))
    assert rc != 0 and missing[-1]["status"] == "UNKNOWN", (missing, err)
    rc, visible, err = command(
        env, *operation(root, run_id, "mock-batch-show", *base, "--legacy-scope-batch-id", batch_id)
    )
    assert (
        rc == 0
        and visible[-1]["scope_checks"]["legacy_manifest_validation"]
        == "MATCHED_AT_CHECK_TIME_UNDER_COOPERATIVE_LOCK"
    ), (visible, err)
    assert visible[-1]["scope_checks"]["directory_scan"] == "TWO_SCANS_UNBOUNDED_ENTRY_COUNT"
    assert (
        visible[-1]["scope_checks"]["scan_limitations"]
        == "NONCOOPERATIVE_ABA_AND_LATER_CHANGES_NOT_EXCLUDED"
    )
    rc, omitted, err = command(
        env, *operation(root, run_id, "mock-batch-show", *base, "--legacy-scope-batch-id", "f" * 32)
    )
    assert rc != 0 and omitted[-1]["status"] == "UNKNOWN", (omitted, err)
    (directory / ("scope-check-batch-" + "f" * 32 + ".json")).write_text("{}")
    rc, orphan, err = command(
        env, *operation(root, run_id, "mock-batch-show", *base, "--legacy-scope-batch-id", batch_id)
    )
    assert rc != 0 and orphan[-1]["status"] == "UNKNOWN", (orphan, err)
    assert (directory / "scope-check-head.json").read_bytes() == head
    assert (root / "CURRENT.json").read_bytes() == current


_INJECT_ORPHAN = """
from bt_api_py import trading_calendar_scope_checks as scope_checks
_scope_original_read = scope_checks._read
_scope_injected = False
def injecting_read(fd, chain, name):
    global _scope_injected
    if name == 'scope-check-head.json' and os.environ.get('ADD_ORPHAN_AFTER_SCAN') and not _scope_injected:
        _scope_injected = True
        new_fd = os.open('scope-check-batch-' + 'f' * 32 + '.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
        os.write(new_fd, b'{}')
        os.close(new_fd)
    return _scope_original_read(fd, chain, name)
scope_checks._read = injecting_read
"""


def test_noncooperating_orphan_after_scan_invalidates_manifest(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto", _INJECT_ORPHAN)
    rc, rows, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (rows, err)
    batch_id = rows[-1]["batch_id"]
    directory = root / "observation-runs"
    head = (directory / "scope-check-head.json").read_bytes()
    current = (root / "CURRENT.json").read_bytes()
    env["ADD_ORPHAN_AFTER_SCAN"] = "1"
    args = operation(
        root, run_id, "mock-batch-show", "--batch-id", batch_id, "--legacy-scope-batch-id", batch_id
    )
    rc, shown, err = command(env, *args)
    assert rc != 0 and shown[-1]["status"] == "UNKNOWN", (shown, err)
    assert shown[-1]["scope_checks"]["status"] == "UNKNOWN"
    assert (directory / ("scope-check-batch-" + "f" * 32 + ".json")).exists()
    assert (directory / "scope-check-head.json").read_bytes() == head
    assert (root / "CURRENT.json").read_bytes() == current
