from __future__ import annotations

import json
from typing import TYPE_CHECKING

from scripts.ci import check_quality_ratchet as ratchet

if TYPE_CHECKING:
    from pathlib import Path


def _write_snapshot(path: Path, scope: list[str], counts: dict[str, int] | None = None) -> bytes:
    payload = ratchet.build_snapshot(scope, counts or {"F401": 2}, "ruff test")
    content = (json.dumps(payload) + "\n").encode()
    path.write_bytes(content)
    return content


def test_scope_comparison_finds_both_directions_and_normalizes_trailing_slashes() -> None:
    resolved = [
        "bt_api_py/",
        "bt_api/bt_api_new/src/",
        "bt_api/bt_api_new/tests",
    ]
    recorded = [
        "bt_api_py",
        "bt_api/bt_api_removed/src/",
        "bt_api/bt_api_removed/tests",
    ]

    assert ratchet.missing_scope_paths(resolved, recorded) == [
        "bt_api/bt_api_removed/src/",
        "bt_api/bt_api_removed/tests",
    ]
    assert ratchet.unrecorded_scope_paths(resolved, recorded) == [
        "bt_api/bt_api_new/src/",
        "bt_api/bt_api_new/tests",
    ]


def test_committed_snapshot_and_default_scope_match_in_both_directions() -> None:
    baseline = ratchet.load_baseline(ratchet.DEFAULT_BASELINE)
    resolved = ratchet.default_scope()
    recorded = baseline["scope"]

    assert ratchet.missing_scope_paths(resolved, recorded) == []
    assert ratchet.unrecorded_scope_paths(resolved, recorded) == []


def test_normal_gate_fails_when_counts_match_but_scope_has_unrecorded_path(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    baseline_path = tmp_path / "baseline.json"
    _write_snapshot(baseline_path, ["bt_api_py/"])
    current_scope = ["bt_api_py", "bt_api/bt_api_new/tests/"]
    monkeypatch.setattr(ratchet, "default_scope", lambda: current_scope)
    monkeypatch.setattr(ratchet, "scan_ruff", lambda scope: {"F401": 2})
    monkeypatch.setattr(ratchet, "ruff_version", lambda: "ruff test")

    exit_code = ratchet.main(["--baseline", str(baseline_path)])

    assert exit_code == 1
    assert "bt_api/bt_api_new/tests/" in capsys.readouterr().err


def test_update_refuses_scope_mismatch_without_writing_snapshot(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    baseline_path = tmp_path / "baseline.json"
    original = _write_snapshot(baseline_path, ["bt_api_py/"])
    current_scope = ["bt_api_py", "bt_api/bt_api_new/src/tests/"]
    monkeypatch.setattr(ratchet, "default_scope", lambda: current_scope)
    monkeypatch.setattr(ratchet, "scan_ruff", lambda scope: {"F401": 2})
    monkeypatch.setattr(ratchet, "ruff_version", lambda: "ruff test")

    exit_code = ratchet.main(["--baseline", str(baseline_path), "--update"])

    assert exit_code == 1
    assert baseline_path.read_bytes() == original
    assert "scope" in capsys.readouterr().err.lower()


def test_force_update_records_reviewed_scope_expansion(tmp_path: Path, monkeypatch) -> None:
    baseline_path = tmp_path / "baseline.json"
    _write_snapshot(baseline_path, ["bt_api_py/"])
    current_scope = ["bt_api_py", "bt_api/bt_api_new/src/", "bt_api/bt_api_new/tests/"]
    scanned_scopes: list[tuple[str, ...]] = []

    def scan(scope: list[str]) -> dict[str, int]:
        scanned_scopes.append(tuple(scope))
        if tuple(scope) == tuple(current_scope):
            return {"F401": 5}
        return {"F401": 2}

    monkeypatch.setattr(ratchet, "default_scope", lambda: current_scope)
    monkeypatch.setattr(ratchet, "scan_ruff", scan)
    monkeypatch.setattr(ratchet, "ruff_version", lambda: "ruff test")

    exit_code = ratchet.main(["--baseline", str(baseline_path), "--force-update"])

    assert exit_code == 0
    updated = ratchet.load_baseline(baseline_path)
    assert updated["scope"] == current_scope
    assert updated["ruff"]["by_rule"] == {"F401": 5}
    assert scanned_scopes == [tuple(current_scope), ("bt_api_py/",)]


def test_force_update_refuses_scope_shrink_without_writing_snapshot(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    baseline_path = tmp_path / "baseline.json"
    original = _write_snapshot(baseline_path, ["bt_api_py/", "tests/"])
    current_scope = ["bt_api_py"]
    scanned_scopes: list[tuple[str, ...]] = []

    def scan(scope: list[str]) -> dict[str, int]:
        scanned_scopes.append(tuple(scope))
        return {"F401": 2}

    monkeypatch.setattr(ratchet, "default_scope", lambda: current_scope)
    monkeypatch.setattr(ratchet, "scan_ruff", scan)

    exit_code = ratchet.main(["--baseline", str(baseline_path), "--force-update"])

    assert exit_code == 1
    assert baseline_path.read_bytes() == original
    assert scanned_scopes == []
    error = capsys.readouterr().err
    assert "--force-update cannot remove" in error
    assert "tests/" in error


def test_force_update_refuses_expansion_when_recorded_scope_regresses(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    baseline_path = tmp_path / "baseline.json"
    original = _write_snapshot(baseline_path, ["bt_api_py/"], {"F401": 2})
    current_scope = ["bt_api_py", "tests"]
    scanned_scopes: list[tuple[str, ...]] = []

    def scan(scope: list[str]) -> dict[str, int]:
        scanned_scopes.append(tuple(scope))
        return {"F401": 5 if tuple(scope) == tuple(current_scope) else 3}

    monkeypatch.setattr(ratchet, "default_scope", lambda: current_scope)
    monkeypatch.setattr(ratchet, "scan_ruff", scan)

    exit_code = ratchet.main(["--baseline", str(baseline_path), "--force-update"])

    assert exit_code == 1
    assert baseline_path.read_bytes() == original
    assert scanned_scopes == [tuple(current_scope), ("bt_api_py/",)]
    captured = capsys.readouterr()
    assert "F401: 3 > baseline 2" in captured.out
    assert "previously recorded scope" in captured.err


def test_force_update_refuses_same_scope_regression_without_writing_snapshot(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    baseline_path = tmp_path / "baseline.json"
    original = _write_snapshot(baseline_path, ["bt_api_py/"], {"F401": 2})
    current_scope = ["bt_api_py"]
    scanned_scopes: list[tuple[str, ...]] = []

    def scan(scope: list[str]) -> dict[str, int]:
        scanned_scopes.append(tuple(scope))
        return {"F401": 3 if len(scanned_scopes) == 1 else 2}

    monkeypatch.setattr(ratchet, "default_scope", lambda: current_scope)
    monkeypatch.setattr(ratchet, "scan_ruff", scan)

    exit_code = ratchet.main(["--baseline", str(baseline_path), "--force-update"])

    assert exit_code == 1
    assert baseline_path.read_bytes() == original
    assert scanned_scopes == [("bt_api_py",)]
    captured = capsys.readouterr()
    assert "F401: 3 > baseline 2" in captured.out
    assert "same-scope counts may not increase" in captured.err
