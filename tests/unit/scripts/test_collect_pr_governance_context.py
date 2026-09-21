"""Pure fake-Git tests for PR governance context collection."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]
COLLECTOR_SCRIPT = REPO_ROOT / "scripts" / "ci" / "collect_pr_governance_context.py"
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
ZERO_SHA = "0" * 40


@pytest.fixture
def collector() -> ModuleType:
    spec = importlib.util.spec_from_file_location("collect_pr_governance_context", COLLECTOR_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_git(
    monkeypatch: pytest.MonkeyPatch,
    collector: ModuleType,
    results: list[tuple[int, str, str]],
) -> list[tuple[list[str], dict[str, Any]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        returncode, stdout, stderr = results[len(calls) - 1]
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(collector.subprocess, "run", run)
    return calls


def _collect(
    monkeypatch: pytest.MonkeyPatch,
    collector: ModuleType,
    *,
    names: str,
    raw: str,
    name_returncode: int = 0,
    raw_returncode: int = 0,
    name_stderr: str = "",
    raw_stderr: str = "",
) -> tuple[dict[str, Any], list[tuple[list[str], dict[str, Any]]]]:
    calls = _fake_git(
        monkeypatch,
        collector,
        [
            (name_returncode, names, name_stderr),
            (raw_returncode, raw, raw_stderr),
        ],
    )
    context = collector.collect_context(
        "c" * 40,
        "d" * 40,
        target_branch="dev",
        labels=["risk:r1", "target:dev"],
        body="pytest passed",
    )
    return context, calls


def test_collects_full_paths_and_multiple_gitlink_updates_with_safe_argv(
    monkeypatch: pytest.MonkeyPatch, collector: ModuleType
) -> None:
    first_path = "bt_api/bt_api_binance"
    second_path = "bt_api/bt_api_okx"
    names = f"README.md\0.gitmodules\0{first_path}\0{second_path}\0"
    raw = (
        f":100644 100644 {OLD_SHA} {NEW_SHA} M\0.gitmodules\0"
        f":160000 160000 {OLD_SHA} {NEW_SHA} M\0{first_path}\0"
        f":160000 160000 {'c' * 40} {'d' * 40} M\0{second_path}\0"
    )

    context, calls = _collect(monkeypatch, collector, names=names, raw=raw)

    assert context == {
        "schema_version": 2,
        "target_branch": "dev",
        "labels": ["risk:r1", "target:dev"],
        "body": "pytest passed",
        "changed_files": ["README.md", ".gitmodules", first_path, second_path],
        "gitlink_changes": [
            {
                "path": first_path,
                "operation": "update",
                "old_sha": OLD_SHA,
                "new_sha": NEW_SHA,
            },
            {
                "path": second_path,
                "operation": "update",
                "old_sha": "c" * 40,
                "new_sha": "d" * 40,
            },
        ],
        "collection_errors": [],
    }

    base_head = f"{'c' * 40}..{'d' * 40}"
    assert calls[0][0] == ["git", "diff", "--name-only", "-z", "--no-renames", base_head]
    assert calls[1][0] == [
        "git",
        "diff",
        "--raw",
        "-z",
        "--no-renames",
        "--no-ext-diff",
        "--abbrev=40",
        base_head,
    ]
    assert all(kwargs["shell"] is False for _, kwargs in calls)
    assert all(kwargs["check"] is False for _, kwargs in calls)


def test_normalizes_add_and_delete_zero_oids_to_null(
    monkeypatch: pytest.MonkeyPatch, collector: ModuleType
) -> None:
    added_path = "bt_api/bt_api_bybit"
    deleted_path = "bt_api/bt_api_gateio"
    names = f"{added_path}\0{deleted_path}\0"
    raw = (
        f":000000 160000 {ZERO_SHA} {NEW_SHA} A\0{added_path}\0"
        f":160000 000000 {OLD_SHA} {ZERO_SHA} D\0{deleted_path}\0"
    )

    context, _ = _collect(monkeypatch, collector, names=names, raw=raw)

    assert context["collection_errors"] == []
    assert context["gitlink_changes"] == [
        {
            "path": added_path,
            "operation": "add",
            "old_sha": None,
            "new_sha": NEW_SHA,
        },
        {
            "path": deleted_path,
            "operation": "delete",
            "old_sha": OLD_SHA,
            "new_sha": None,
        },
    ]


@pytest.mark.parametrize(
    ("raw", "expected_error"),
    [
        (f":160000 160000 {OLD_SHA} {OLD_SHA} M\0bt_api/bt_api_binance\0", "different"),
        (":160000 160000 short " + NEW_SHA + " M\0bt_api/bt_api_binance\0", "40-hex"),
        (
            f":100644 160000 {OLD_SHA} {NEW_SHA} T\0bt_api/bt_api_binance\0",
            "unsupported",
        ),
        (f":160000 160000 {OLD_SHA} {NEW_SHA} M\0", "truncated"),
    ],
)
def test_records_parse_and_gitlink_errors_instead_of_authorizing_changes(
    monkeypatch: pytest.MonkeyPatch,
    collector: ModuleType,
    raw: str,
    expected_error: str,
) -> None:
    context, _ = _collect(
        monkeypatch,
        collector,
        names="bt_api/bt_api_binance\0",
        raw=raw,
    )

    assert context["gitlink_changes"] == []
    assert any(expected_error in error.lower() for error in context["collection_errors"])


def test_git_errors_are_saved_in_v2_context(
    monkeypatch: pytest.MonkeyPatch, collector: ModuleType
) -> None:
    context, _ = _collect(
        monkeypatch,
        collector,
        names="",
        raw="",
        raw_returncode=128,
        raw_stderr="fatal: bad object",
    )

    assert context["schema_version"] == 2
    assert context["collection_errors"]
    assert any("fatal: bad object" in error for error in context["collection_errors"])


def test_regular_file_with_gitlink_shaped_name_is_not_collected_as_gitlink(
    monkeypatch: pytest.MonkeyPatch, collector: ModuleType
) -> None:
    path = "bt_api/bt_api_binance"
    raw = f":100644 100644 {OLD_SHA} {NEW_SHA} M\0{path}\0"

    context, _ = _collect(monkeypatch, collector, names=f"{path}\0", raw=raw)

    assert context["changed_files"] == [path]
    assert context["gitlink_changes"] == []
    assert context["collection_errors"] == []


def test_collects_real_gitlink_update_add_delete_and_special_path(
    monkeypatch: pytest.MonkeyPatch,
    collector: ModuleType,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_executable = shutil.which("git")
    assert git_executable is not None

    def git(*args: str) -> str:
        result = subprocess.run(
            [git_executable, *args],
            cwd=repo,
            capture_output=True,
            check=True,
            text=True,
        )
        return result.stdout.strip()

    git("init")
    git("config", "user.name", "Governance Test")
    git("config", "user.email", "governance-test@example.invalid")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    git("add", "seed.txt")
    git("commit", "-m", "seed commit")
    seed_sha = git("rev-parse", "HEAD")

    update_path = "bt_api/bt_api_update"
    delete_path = "bt_api/bt_api_delete"
    special_path = "bt_api/bt_api special\tname"
    git("update-index", "--add", "--cacheinfo", f"160000,{seed_sha},{update_path}")
    git("update-index", "--add", "--cacheinfo", f"160000,{seed_sha},{delete_path}")
    git("commit", "-m", "base gitlinks")
    base_sha = git("rev-parse", "HEAD")

    git("update-index", "--cacheinfo", f"160000,{base_sha},{update_path}")
    git("update-index", "--add", "--cacheinfo", f"160000,{seed_sha},{special_path}")
    git("update-index", "--force-remove", delete_path)
    git("commit", "-m", "update gitlinks")
    head_sha = git("rev-parse", "HEAD")

    real_run = subprocess.run

    def run_in_repo(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return real_run(argv, cwd=repo, **kwargs)

    monkeypatch.setattr(collector.subprocess, "run", run_in_repo)
    context = collector.collect_context(base_sha, head_sha)

    assert context["collection_errors"] == []
    assert set(context["changed_files"]) == {update_path, delete_path, special_path}
    assert len(context["gitlink_changes"]) == 3
    assert {record["path"]: record for record in context["gitlink_changes"]} == {
        update_path: {
            "path": update_path,
            "operation": "update",
            "old_sha": seed_sha,
            "new_sha": base_sha,
        },
        special_path: {
            "path": special_path,
            "operation": "add",
            "old_sha": None,
            "new_sha": seed_sha,
        },
        delete_path: {
            "path": delete_path,
            "operation": "delete",
            "old_sha": seed_sha,
            "new_sha": None,
        },
    }
