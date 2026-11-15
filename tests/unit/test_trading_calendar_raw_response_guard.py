"""Synthetic retained typed responses are compared, never authenticated."""

import pytest

from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_fresh_observation import confirm, flags
from tests.unit.test_trading_calendar_mock_persistent import operation
from tests.unit.test_trading_calendar_scope_checks import batch_args

RAW_ROW = r"""
_raw_original = collect_akshare_contract
def typed_response(**options):
    original_runner = options.pop('_runner')
    def runner(*args):
        state, packet = original_runner(*args)
        if state == 'CHILD_OK' and packet['status'] == 'OK':
            packet = dict(packet)
            value = os.environ.get('FAKE_EXTRA_VALUE', '2026-12-20')
            if value == 'TYPE_INT':
                value = 1
            elif value == 'TYPE_FLOAT':
                value = 1.0
            packet['rows'] = packet['rows'] + [['最后交易日', value]]
            if os.environ.get('FAKE_REVERSE_ROWS'):
                packet['rows'].reverse()
        return state, packet
    return _raw_original(**options, _runner=runner)
collect_akshare_contract = typed_response
"""


def initialized(tmp_path, mode, extra=""):
    root, env, original = _initialized_batch_root(tmp_path, mode, RAW_ROW + extra)
    return root, env, original


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("variant", ["unmodeled", "both", "reordered"])
def test_unexplained_retained_response_never_same_or_publishes(tmp_path, mode, variant):
    root, env, original = initialized(tmp_path, mode)
    args = operation(root, original, "mock-update", *flags())
    rc, rows, err = command(env, *args)
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    if mode == "manual":
        assert confirm(env, root, rows[-1])["status"] == "COMMITTED"
    before = (root / "CURRENT.json").read_bytes()
    publications = set((root / "publications").iterdir())
    if variant == "reordered":
        env["FAKE_REVERSE_ROWS"] = "1"
    else:
        env["FAKE_EXTRA_VALUE"] = "2026-12-21"
        if variant == "both":
            env["FAKE_AK_END"] = "10:30"
    rc, rows, err = command(env, *args)
    assert rc != 0 and rows[-1]["status"] == "UNKNOWN", (rows, err)
    assert rows[-1]["reason"] == "PROPOSAL_UNAVAILABLE"
    assert (root / "CURRENT.json").read_bytes() == before
    assert set((root / "publications").iterdir()) == publications
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_partial_batch_rejects_unexplained_change_in_updated_scope(tmp_path, mode):
    root, env, original = initialized(tmp_path, mode)
    args = batch_args(root, original)
    rc, rows, err = command(env, *args)
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    if mode == "manual":
        assert confirm(env, root, rows[-1])["status"] == "COMMITTED"
    before = (root / "CURRENT.json").read_bytes()
    env["FAIL_DCE"] = "1"
    env["FAKE_EXTRA_VALUE"] = "2026-12-21"
    rc, rows, err = command(env, *args)
    assert rc != 0 and rows[-1]["status"] == "UNKNOWN", (rows, err)
    assert (root / "CURRENT.json").read_bytes() == before
    assert [item["disposition"] for item in rows[-1]["observations"]] == [
        "UPDATED_AKSHARE",
        "RETAINED_AKSHARE",
    ]


def test_missing_retained_baseline_observation_fails_closed(tmp_path):
    root, env, original = initialized(tmp_path, "auto")
    args = batch_args(root, original)
    rc, rows, err = command(env, *args)
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)
    oid = rows[-1]["observations"][0]["observation_id"]
    (root / "akshare-observations" / f"result-{oid}.json").unlink()
    before = (root / "CURRENT.json").read_bytes()
    rc, rows, err = command(env, *args)
    assert rc != 0 and rows[-1]["status"] == "UNKNOWN", (rows, err)
    assert (root / "CURRENT.json").read_bytes() == before


def test_raw_numeric_type_change_is_not_equal(tmp_path):
    root, env, original = initialized(tmp_path, "auto")
    args = operation(root, original, "mock-update", *flags())
    env["FAKE_EXTRA_VALUE"] = "TYPE_INT"
    rc, rows, err = command(env, *args)
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)
    before = (root / "CURRENT.json").read_bytes()
    env["FAKE_EXTRA_VALUE"] = "TYPE_FLOAT"
    rc, rows, err = command(env, *args)
    assert rc != 0 and rows[-1]["status"] == "UNKNOWN", (rows, err)
    assert (root / "CURRENT.json").read_bytes() == before


LOCK_RACE = r"""
import fcntl
from pathlib import Path
from bt_api_py import trading_calendar_mixed_publication as guarded_mixed
_original_compare = guarded_mixed._check_retained_responses
def break_on_locked_replay(root, filesystem, before, after):
    if os.environ.get('BREAK_ALIGNED_EVIDENCE'):
        fd = os.open(str(Path(root) / '.lock'), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                value = before if os.environ['BREAK_ALIGNED_EVIDENCE'] == 'old' else after
                scope = value['scopes'][0] if 'scopes' in value else value
                oid = scope['evidence_locator']['observation_id']
                (Path(root) / 'akshare-observations' / ('result-' + oid + '.json')).unlink()
                Path(os.environ['BREAK_MARKER']).touch()
                os.environ.pop('BREAK_ALIGNED_EVIDENCE')
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    return _original_compare(root, filesystem, before, after)
guarded_mixed._check_retained_responses = break_on_locked_replay
"""


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("lost", ["old", "new"])
def test_writer_lock_rechecks_original_and_new_observations(tmp_path, mode, lost):
    root, env, original = initialized(tmp_path, mode, LOCK_RACE)
    args = operation(root, original, "mock-update", *flags())
    rc, rows, err = command(env, *args)
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    if mode == "manual":
        assert confirm(env, root, rows[-1])["status"] == "COMMITTED"
    before = (root / "CURRENT.json").read_bytes()
    publications = set((root / "publications").iterdir())
    env["FAKE_AK_END"] = "10:30"
    if mode == "auto":
        env["BREAK_ALIGNED_EVIDENCE"] = lost
        env["BREAK_MARKER"] = str(tmp_path / "race-marker")
    rc, rows, err = command(env, *args)
    if mode == "manual":
        assert rc == 10 and rows[-1]["status"] == "PENDING_REVIEW", (rows, err)
        env["BREAK_ALIGNED_EVIDENCE"] = lost
        env["BREAK_MARKER"] = str(tmp_path / "race-marker")
        pending = rows[-1]
        rc, confirmed, err = command(
            env,
            *operation(
                root,
                pending["run_id"],
                "mock-confirm",
                "--review-id",
                pending["review_id"],
                "--review-sha256",
                pending["review_payload_sha256"],
                "--reviewer",
                "MOCK_reviewer",
                "--confirmed-at",
                "2026-11-13T00:00:00.000000Z",
            ),
        )
        assert rc != 0, (confirmed, err)
        outcome = confirmed[-1]
    else:
        outcome = rows[-1]
    assert (tmp_path / "race-marker").exists(), outcome
    assert outcome["status"] not in {"COMMITTED", "SAME"}, outcome
    assert (root / "CURRENT.json").read_bytes() == before
    assert set((root / "publications").iterdir()) == publications


def test_compare_full_rejects_new_candidate_with_old_provenance(tmp_path):
    from bt_api_py import trading_calendar_full_diff as full
    from bt_api_py.trading_calendar_store import TradingCalendarStore

    root, env, original = initialized(tmp_path, "auto")
    args = operation(root, original, "mock-update", *flags())
    rc, rows, err = command(env, *args)
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)
    baseline = TradingCalendarStore(root).read_current()
    env["FAKE_EXTRA_VALUE"] = "2026-12-21"
    rc, rows, err = command(env, *args)
    assert rc != 0 and rows[-1]["status"] == "UNKNOWN", (rows, err)
    candidate_id = rows[-1]["candidate_id"]
    result = full.compare_full(
        root=str(root),
        candidate_id=candidate_id,
        filesystem="TRUSTED_LOCAL_POSIX",
        simulation_provenance=baseline.simulation_provenance,
    )
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "CANDIDATE_PROVENANCE_UNAVAILABLE_OR_CHANGED"


def test_review_wrapper_and_cli_derive_bound_mixed_provenance(tmp_path):
    import json
    import subprocess
    import sys

    from bt_api_py import trading_calendar_observation_run as runs
    from bt_api_py import trading_calendar_review_bundle as review

    root, env, original = initialized(tmp_path, "auto")
    args = operation(root, original, "mock-update", *flags())
    rc, rows, err = command(env, *args)
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)
    rc, rows, err = command(env, *args)
    assert rc == 10 and rows[-1]["status"] == "SAME", (rows, err)
    candidate_id = rows[-1]["candidate_id"]

    def wrapper():
        return review.compare_full(
            root=str(root), candidate_id=candidate_id, filesystem=runs.TRUSTED_LOCAL_POSIX
        )

    result = wrapper()
    assert result["status"] == "SAME", result
    assert result["difference"]["simulation"]["after"] is not None
    cmd = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_review_bundle",
        "compare-full",
        "--calendar-root",
        str(root),
        "--candidate-id",
        candidate_id,
        "--trusted-local-posix",
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    assert (
        result.returncode == 10 and json.loads(result.stdout.splitlines()[-1])["status"] == "SAME"
    ), result.stderr
    sidecar = root / "review-bundles" / f"mock-mixed-{candidate_id}.json"
    sidecar.unlink()
    result = wrapper()
    assert (
        result["status"] == "UNKNOWN"
        and result["reason"] == "CANDIDATE_PROVENANCE_UNAVAILABLE_OR_CHANGED"
    )
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    assert (
        result.returncode == 25
        and json.loads(result.stdout.splitlines()[-1])["status"] == "UNKNOWN"
    ), result.stderr


def test_compare_full_rechecks_old_observation_after_final_candidate_replay(tmp_path, monkeypatch):
    from bt_api_py import trading_calendar_full_diff as full
    from bt_api_py import trading_calendar_observation_run as runs
    from bt_api_py.trading_calendar_store import TradingCalendarStore

    root, env, original = initialized(tmp_path, "auto")
    args = operation(root, original, "mock-update", *flags())
    rc, rows, err = command(env, *args)
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)
    baseline = TradingCalendarStore(root).read_current()
    old_id = baseline.simulation_provenance["evidence_locator"]["observation_id"]
    old_result = root / "akshare-observations" / f"result-{old_id}.json"
    rc, rows, err = command(env, *args)
    assert rc == 10 and rows[-1]["status"] == "SAME", (rows, err)
    candidate_id = rows[-1]["candidate_id"]
    pointer = (root / "CURRENT.json").read_bytes()
    bound = full._bound_candidate_provenance
    calls = []

    def remove_old_at_last_candidate(*args):
        calls.append(1)
        if len(calls) == 2:
            old_result.unlink()
        return bound(*args)

    monkeypatch.setattr(full, "_bound_candidate_provenance", remove_old_at_last_candidate)
    result = full.compare_full(
        root=str(root), candidate_id=candidate_id, filesystem=runs.TRUSTED_LOCAL_POSIX
    )
    assert len(calls) == 2 and not old_result.exists()
    assert result["status"] == "UNKNOWN" and result["reason"] == "AKSHARE_RESPONSE_REVIEW_REQUIRED"
    assert (root / "CURRENT.json").read_bytes() == pointer


def test_compare_full_declared_mixed_metadata_needs_sidecar(tmp_path):
    from bt_api_py import trading_calendar_observation_run as runs
    from bt_api_py import trading_calendar_review_bundle as review
    from tests.unit.test_trading_calendar_review_bundle import build, published_candidate, sample

    published_candidate(tmp_path, sample())
    value = sample()
    value["metadata"] = {"data_origin": "AKSHARE_MOCK", "environment": "SIMULATION_ONLY"}
    candidate_id = "b" * 32
    assert build(tmp_path, value, candidate_id)["status"] == "COMMITTED"
    result = review.compare_full(
        root=str(tmp_path), candidate_id=candidate_id, filesystem=runs.TRUSTED_LOCAL_POSIX
    )
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "CANDIDATE_PROVENANCE_UNAVAILABLE_OR_CHANGED"

    pure = sample()
    pure["metadata"] = {"data_origin": "MOCK", "environment": "SIMULATION_ONLY"}
    pure_id = "c" * 32
    assert build(tmp_path, pure, pure_id)["status"] == "COMMITTED"
    unchanged = review.compare_full(
        root=str(tmp_path), candidate_id=pure_id, filesystem=runs.TRUSTED_LOCAL_POSIX
    )
    assert unchanged["status"] == "SAME", unchanged
