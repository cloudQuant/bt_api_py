"""Bounded local discovery; fixtures contain no real configuration or network."""

import json
import subprocess
import sys

import pytest

from bt_api_py import trading_calendar_observation_run as runs
from tests.unit.test_trading_calendar_observation_run import (
    ID,
    env_without_network,
    run_args,
)
from tests.unit.test_trading_calendar_preview_catalog import setup_preview
from tests.unit.test_trading_calendar_preview_reports import report


def read_list(root):
    return runs.list_runs(root=str(root), filesystem=runs.TRUSTED_LOCAL_POSIX)


def setup_runs(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: report())
    assert runs.run_once(**run_args(root, config, supplement))["record_installation"] == "COMMITTED"
    return root


def test_list_validates_without_claiming_snapshot_or_success(tmp_path, monkeypatch):
    root = setup_runs(tmp_path, monkeypatch)
    state = read_list(root)
    assert state["status"] == "OBSERVED_UNAUTHENTICATED"
    assert state["max_entries"] == 200
    assert state["runs"] == [
        {"run_id": ID, "intent": "VISIBLE_UNAUTHENTICATED", "record": "VISIBLE_UNAUTHENTICATED"}
    ]
    assert state["snapshot_completeness"] == state["time_ordering"] == "NOT_PROVEN"
    assert state["writer_identity"] == state["source_authentication"] == "NOT_PROVEN"
    assert state["last_success"] == "NOT_UPDATED" and state["publication"] == "NOT_AUTHORIZED"


def test_empty_and_missing_directory_are_distinct_and_no_directory_created(tmp_path):
    root, _, _, _ = setup_preview(tmp_path)
    missing = read_list(root)
    assert missing["status"] == "UNKNOWN" and missing["reason"] == "DIRECTORY_UNAVAILABLE"
    folder = root / "observation-runs"
    assert not folder.exists()
    folder.mkdir(mode=0o700)
    empty = read_list(root)
    assert empty["status"] == "OBSERVED_UNAUTHENTICATED" and empty["runs"] == []
    assert empty["snapshot_completeness"] == "NOT_PROVEN"


@pytest.mark.parametrize(
    "broken", ["invalid_name", "bad_intent", "bad_result", "symlink", "orphan"]
)
def test_list_reports_invalid_and_incomplete_entries(tmp_path, monkeypatch, broken):
    root = setup_runs(tmp_path, monkeypatch)
    folder = root / "observation-runs"
    intent, result = folder / f"run-{ID}.intent", folder / f"run-{ID}.json"
    if broken == "invalid_name":
        (folder / "private-path-or-secret").write_text("anything")
    elif broken == "bad_intent":
        intent.write_bytes(b"{}")
    elif broken == "bad_result":
        result.write_bytes(b"{}")
    elif broken == "symlink":
        result.unlink()
        result.symlink_to(tmp_path / "private-target")
    else:
        intent.unlink()
    state = read_list(root)
    assert state["status"] == "UNKNOWN" and state["reason"] == "ENTRY_UNKNOWN"
    assert len(state["runs"]) == 1
    if broken == "orphan":
        assert state["runs"][0]["intent"] == "NOT_OBSERVED"
        assert state["runs"][0]["record"] == "UNKNOWN"
    assert "private-path-or-secret" not in json.dumps(state)
    assert "private-target" not in json.dumps(state)


def test_list_rejects_over_limit_without_partial_output(tmp_path):
    root, _, _, _ = setup_preview(tmp_path)
    folder = root / "observation-runs"
    folder.mkdir(mode=0o700)
    for i in range(runs._LIST_ENTRY_LIMIT + 1):
        (folder / f"entry-{i}").touch()
    state = read_list(root)
    assert state["status"] == "UNKNOWN" and state["reason"] == "ENTRY_LIMIT_EXCEEDED"
    assert state["runs"] == []


@pytest.mark.parametrize("limit", [1, 200, 2000])
def test_repeated_scan_names_are_counted_as_visits(tmp_path, monkeypatch, limit):
    root, _, _, _ = setup_preview(tmp_path)
    (root / "observation-runs").mkdir(mode=0o700)
    visits = []

    class RepeatingEntries:
        def __init__(self, directory):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            class Entry:
                name = "repeated"

            while True:
                visits.append(1)
                yield Entry()

    monkeypatch.setattr(runs.os, "scandir", RepeatingEntries)
    state = runs.list_runs(root=str(root), filesystem=runs.TRUSTED_LOCAL_POSIX, max_entries=limit)
    assert state["status"] == "UNKNOWN" and state["reason"] == "ENTRY_LIMIT_EXCEEDED"
    assert state["runs"] == [] and len(visits) == limit + 1


@pytest.mark.parametrize("limit", [1, 7, 200, 2000])
def test_requested_scan_limit_applies_to_each_pass(tmp_path, monkeypatch, limit):
    root, _, _, _ = setup_preview(tmp_path)
    (root / "observation-runs").mkdir(mode=0o700)
    counts = []

    class Entries:
        def __init__(self, directory):
            counts.append(0)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            class Entry:
                name = "invalid"

            for _ in range(limit):
                counts[-1] += 1
                yield Entry()

    monkeypatch.setattr(runs.os, "scandir", Entries)
    state = runs.list_runs(root=str(root), filesystem=runs.TRUSTED_LOCAL_POSIX, max_entries=limit)
    assert state["max_entries"] == limit and state["reason"] == "ENTRY_UNKNOWN"
    assert counts == [limit, limit]


def test_explicit_small_limit_rejects_entries_without_partial_output(tmp_path):
    root, _, _, _ = setup_preview(tmp_path)
    folder = root / "observation-runs"
    folder.mkdir(mode=0o700)
    for index in range(3):
        (folder / f"run-{index:032x}.intent").write_bytes(runs._intent(f"{index:032x}", False))
    state = runs.list_runs(root=str(root), filesystem=runs.TRUSTED_LOCAL_POSIX, max_entries=2)
    assert state["max_entries"] == 2 and state["reason"] == "ENTRY_LIMIT_EXCEEDED"
    assert state["runs"] == []


def test_second_scan_independently_enforces_limit(tmp_path, monkeypatch):
    root, _, _, _ = setup_preview(tmp_path)
    (root / "observation-runs").mkdir(mode=0o700)
    scan_counts = []

    class Entries:
        def __init__(self, directory):
            scan_counts.append(0)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            class Entry:
                name = "repeated"

            while True:
                scan_counts[-1] += 1
                if len(scan_counts) == 1 and scan_counts[-1] > 1:
                    return
                yield Entry()

    monkeypatch.setattr(runs.os, "scandir", Entries)
    state = runs.list_runs(root=str(root), filesystem=runs.TRUSTED_LOCAL_POSIX, max_entries=1)
    assert state["reason"] == "ENTRY_LIMIT_EXCEEDED" and state["runs"] == []
    assert scan_counts == [2, 2]


@pytest.mark.parametrize(
    "bad", [0, -1, 2001, True, False, 1.0, "2", None, pytest.param(10**10000, id="huge-int")]
)
def test_invalid_api_limit_is_rejected_before_open(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(
        runs, "_open_runs", lambda *a, **kw: pytest.fail("unexpected directory read")
    )
    state = runs.list_runs(root=str(tmp_path), filesystem=runs.TRUSTED_LOCAL_POSIX, max_entries=bad)
    assert state["status"] == "UNKNOWN" and state["reason"] == "INVALID_ARGUMENTS"
    assert state["max_entries"] is None
    assert len(json.dumps(state)) < 1000


def test_oversized_record_and_invalid_uuid_fail_closed(tmp_path, monkeypatch):
    root = setup_runs(tmp_path, monkeypatch)
    folder = root / "observation-runs"
    (folder / f"run-{ID}.json").write_bytes(b"x" * (runs._LIMIT + 1))
    (folder / f"run-{'A' * 32}.intent").touch(mode=0o600)
    state = read_list(root)
    assert state["status"] == "UNKNOWN" and state["reason"] == "ENTRY_UNKNOWN"
    assert state["runs"] == [
        {"run_id": ID, "intent": "VISIBLE_UNAUTHENTICATED", "record": "UNKNOWN"}
    ]


def test_mutation_inside_scan_returns_unknown(tmp_path, monkeypatch):
    root = setup_runs(tmp_path, monkeypatch)
    original = runs.os.scandir

    class ChangingIterator:
        def __init__(self, path):
            self.inner = original(path)

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def __iter__(self):
            for index, entry in enumerate(self.inner):
                if index == 0:
                    (root / "observation-runs" / "concurrent-entry").touch()
                yield entry

    monkeypatch.setattr(runs.os, "scandir", ChangingIterator)
    state = read_list(root)
    assert state["status"] == "UNKNOWN" and state["runs"] == []
    assert state["reason"] == "DIRECTORY_CHANGED_OR_UNREADABLE"


def test_changed_scan_does_not_return_a_partial_snapshot(tmp_path, monkeypatch):
    root = setup_runs(tmp_path, monkeypatch)
    scan = runs._scan_run_names
    calls = 0

    def changing(directory, chain, max_entries):
        nonlocal calls
        output = scan(directory, chain, max_entries)
        calls += 1
        if calls == 1:
            path = root / "observation-runs" / f"run-{'b' * 32}.intent"
            path.write_bytes(runs._intent("b" * 32, False))
            path.chmod(0o600)
        return output

    monkeypatch.setattr(runs, "_scan_run_names", changing)
    state = read_list(root)
    assert state["status"] == "UNKNOWN" and state["runs"] == []
    assert state["reason"] == "DIRECTORY_CHANGED"


def test_replaced_root_does_not_return_stale_observations(tmp_path, monkeypatch):
    root = setup_runs(tmp_path, monkeypatch)
    old = tmp_path / "old-root"
    scan = runs._scan_run_names
    calls = 0

    def replacing(directory, chain, max_entries):
        nonlocal calls
        output = scan(directory, chain, max_entries)
        calls += 1
        if calls == 1:
            root.rename(old)
            root.mkdir(mode=0o700)
        return output

    monkeypatch.setattr(runs, "_scan_run_names", replacing)
    state = read_list(root)
    assert state["status"] == "UNKNOWN" and state["runs"] == []
    assert state["reason"] == "DIRECTORY_CHANGED_OR_UNREADABLE"
    assert not (root / "observation-runs").exists()


@pytest.mark.parametrize("limit", ["50", "2000"])
def test_real_list_cli_does_not_write_start_preview_or_connect(tmp_path, monkeypatch, limit):
    root = setup_runs(tmp_path, monkeypatch)
    folder = root / "observation-runs"
    before = {entry.name: entry.read_bytes() for entry in folder.iterdir()}
    env = env_without_network(
        tmp_path,
        """
from bt_api_py import trading_calendar_preview as p
def forbidden(*args, **kwargs): raise AssertionError("preview invoked")
p._supervise = forbidden
""",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_observation_run",
            "list",
            "--calendar-root",
            str(root),
            "--trusted-local-posix",
            "--max-entries",
            limit,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["runs"][0]["run_id"] == ID and payload["max_entries"] == int(limit)
    assert str(root) not in result.stdout + result.stderr
    assert {entry.name: entry.read_bytes() for entry in folder.iterdir()} == before
    assert not (root / "CURRENT.json").exists()


@pytest.mark.parametrize("bad", ["0", "-1", "2001", "+1", "1.0", "01", "x"])
def test_invalid_cli_limit_never_opens_directory(tmp_path, bad):
    marker = tmp_path / "opened"
    env = env_without_network(
        tmp_path,
        f"""
import os
_open = os.open
def monitored(path, flags, *args, **kwargs):
    if flags & os.O_DIRECTORY:
        open({str(marker)!r}, "w").close()
    return _open(path, flags, *args, **kwargs)
os.open = monitored
""",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_observation_run",
            "list",
            "--calendar-root",
            str(tmp_path),
            "--trusted-local-posix",
            "--max-entries",
            bad,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert result.returncode == 2 and not marker.exists()
    assert str(tmp_path) not in result.stdout + result.stderr


def test_scan_enforces_directory_chain_on_missing_and_bad_files(tmp_path, monkeypatch):
    root = setup_runs(tmp_path, monkeypatch)
    folder = root / "observation-runs"
    (folder / f"run-{ID}.json").unlink()
    result = read_list(root)
    assert result["status"] == "UNKNOWN"
    assert result["runs"][0]["record"] == "NOT_OBSERVED"
    assert result["runs"][0]["intent"] == "VISIBLE_UNAUTHENTICATED"
