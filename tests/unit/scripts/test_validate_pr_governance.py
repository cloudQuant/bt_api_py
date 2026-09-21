"""Tests for scripts/ci/validate_pr_governance.py (plan M4 step 1).

Fixture-first: ordinary dev PRs pass; normal PRs targeting master fail;
master hotfixes without risk:r3 / release:hotfix evidence fail; gitlink
changes require per-path schema-v2 evidence. Report-only mode never exits
non-zero but must surface every violation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "validate_pr_governance.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "governance"
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
ZERO_SHA = "0" * 40


def run_validator(context: Any, *, strict: bool) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT),
        "--context",
        "-",
    ]
    if strict:
        args.append("--strict")
    result = subprocess.run(
        args,
        input=json.dumps(context),
        capture_output=True,
        text=True,
        check=False,
    )
    return result


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def gitlink_record(
    path: str,
    *,
    operation: str = "update",
    old_sha: str | None = OLD_SHA,
    new_sha: str | None = NEW_SHA,
) -> dict[str, str | None]:
    return {
        "path": path,
        "operation": operation,
        "old_sha": old_sha,
        "new_sha": new_sha,
    }


def v2_context(
    changed_files: list[str],
    gitlink_changes: list[dict[str, str | None]],
    *,
    collection_errors: list[str] | None = None,
) -> dict:
    context = load_fixture("pr-dev-r1.json")
    context.pop("submodules_changed", None)
    context.pop("old_sha", None)
    context.pop("new_sha", None)
    context.update(
        {
            "schema_version": 2,
            "changed_files": changed_files,
            "gitlink_changes": gitlink_changes,
            "collection_errors": collection_errors or [],
        }
    )
    return context


def assert_strict_and_report_only_violation(context: dict, expected: str) -> None:
    strict_result = run_validator(context, strict=True)
    strict_diagnostic = strict_result.stdout + strict_result.stderr
    assert strict_result.returncode == 1
    assert expected in strict_diagnostic

    report_result = run_validator(context, strict=False)
    assert report_result.returncode == 0
    assert "WARN" in report_result.stdout
    assert expected in report_result.stdout


def test_dev_r1_fixture_passes_strict() -> None:
    result = run_validator(load_fixture("pr-dev-r1.json"), strict=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("context", [[], "not-an-object", 7, None])
def test_non_object_context_is_reported_as_input_error(context: Any) -> None:
    result = run_validator(context, strict=True)

    assert result.returncode == 2
    assert "top-level context must be a JSON object" in result.stderr


def test_master_hotfix_fixture_passes_strict() -> None:
    result = run_validator(load_fixture("pr-master-hotfix.json"), strict=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_submodule_bump_fixture_passes_strict() -> None:
    fixture = load_fixture("pr-submodule-bump.json")
    context = v2_context(
        fixture["changed_files"],
        [
            gitlink_record(
                "bt_api/bt_api_binance",
                old_sha=fixture["old_sha"],
                new_sha=fixture["new_sha"],
            )
        ],
    )

    result = run_validator(context, strict=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_global_sha_fixture_is_rejected_for_gitlink_paths() -> None:
    result = run_validator(load_fixture("pr-submodule-bump.json"), strict=True)

    assert result.returncode == 1
    assert "schema_version" in result.stdout
    assert "gitlink_changes" in result.stdout


def test_normal_pr_targeting_master_fails_strict() -> None:
    context = load_fixture("pr-dev-r1.json")
    context["target_branch"] = "master"
    result = run_validator(context, strict=True)
    assert result.returncode == 1
    assert "release:hotfix" in result.stdout


def test_master_hotfix_without_labels_fails_strict() -> None:
    context = load_fixture("pr-master-hotfix.json")
    context["labels"] = ["risk:r3"]
    result = run_validator(context, strict=True)
    assert result.returncode == 1
    assert "release:hotfix" in result.stdout


def test_master_hotfix_without_repro_evidence_fails_strict() -> None:
    context = load_fixture("pr-master-hotfix.json")
    context["body"] = "fix typo in order router"
    result = run_validator(context, strict=True)
    assert result.returncode == 1


def test_submodule_bump_without_sha_evidence_fails_strict() -> None:
    context = v2_context(
        ["bt_api/bt_api_binance"],
        [gitlink_record("bt_api/bt_api_binance", old_sha=None, new_sha=None)],
    )
    result = run_validator(context, strict=True)
    assert result.returncode == 1
    assert "SHA" in result.stdout


def test_gitmodules_change_requires_gitlink_sha_context() -> None:
    context = load_fixture("pr-dev-r1.json")
    context["changed_files"] = [".gitmodules"]
    context["submodules_changed"] = False
    context["old_sha"] = None
    context["new_sha"] = None

    strict_result = run_validator(context, strict=True)
    diagnostic = strict_result.stdout + strict_result.stderr

    assert strict_result.returncode == 1
    assert ".gitmodules" in diagnostic
    assert "gitlink" in diagnostic.lower()
    assert "schema_version" in diagnostic
    assert "gitlink_changes" in diagnostic
    assert "old_sha/new_sha" in diagnostic

    report_result = run_validator(context, strict=False)
    assert report_result.returncode == 0
    assert "WARN" in report_result.stdout


def test_submodule_gitlink_change_requires_v2_gitlink_record() -> None:
    context = load_fixture("pr-dev-r1.json")
    context["changed_files"] = ["bt_api/bt_api_binance"]

    result = run_validator(context, strict=True)
    diagnostic = result.stdout + result.stderr

    assert result.returncode == 1
    assert "bt_api/bt_api_binance" in diagnostic
    assert "gitlink" in diagnostic.lower()
    assert "schema_version" in diagnostic
    assert "gitlink_changes" in diagnostic


@pytest.mark.parametrize("path", ["bt_api/readme.md", "bt_api/install_and_test_all.py"])
def test_non_gitlink_bt_api_files_do_not_require_submodule_sha_context(path: str) -> None:
    context = load_fixture("pr-dev-r1.json")
    context["changed_files"] = [path]
    context["submodules_changed"] = False
    context["old_sha"] = None
    context["new_sha"] = None

    result = run_validator(context, strict=True)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("with_gitmodules", [False, True])
@pytest.mark.parametrize("multiple", [False, True])
def test_valid_v2_gitlink_updates_pass_with_or_without_gitmodules(
    with_gitmodules: bool, multiple: bool
) -> None:
    paths = ["bt_api/bt_api_binance"]
    if multiple:
        paths.append("bt_api/bt_api_okx")
    changed_files = [*paths, ".gitmodules"] if with_gitmodules else paths.copy()
    records = [gitlink_record(path, old_sha=OLD_SHA, new_sha=NEW_SHA) for path in reversed(paths)]

    result = run_validator(v2_context(changed_files, records), strict=True)

    assert result.returncode == 0, result.stdout + result.stderr


def test_gitmodules_only_v2_context_fails_and_warns() -> None:
    context = v2_context([".gitmodules"], [])

    assert_strict_and_report_only_violation(context, ".gitmodules")


def test_gitlink_shaped_regular_file_without_gitlink_record_fails_closed() -> None:
    context = v2_context(["bt_api/bt_api_binance"], [])

    assert_strict_and_report_only_violation(context, "gitlink_changes")


@pytest.mark.parametrize(
    ("changed_files", "records", "expected"),
    [
        (
            ["bt_api/bt_api_binance"],
            [
                gitlink_record("bt_api/bt_api_binance"),
                gitlink_record("bt_api/bt_api_binance"),
            ],
            "duplicate",
        ),
        (
            ["bt_api/bt_api_binance", "bt_api/bt_api_binance"],
            [gitlink_record("bt_api/bt_api_binance")],
            "duplicate",
        ),
        (
            ["bt_api/bt_api_binance"],
            [gitlink_record("bt_api/bt_api_okx")],
            "changed_files",
        ),
        (
            ["bt_api/bt_api_binance"],
            [gitlink_record("bt_api/readme.md")],
            "path",
        ),
    ],
)
def test_v2_gitlink_records_must_map_one_to_one_to_valid_paths(
    changed_files: list[str],
    records: list[dict[str, str | None]],
    expected: str,
) -> None:
    context = v2_context(changed_files, records)

    assert_strict_and_report_only_violation(context, expected)


@pytest.mark.parametrize(
    ("operation", "old_sha", "new_sha"),
    [
        ("add", None, NEW_SHA),
        ("delete", OLD_SHA, None),
    ],
)
def test_v2_add_and_delete_gitlinks_are_not_accepted_as_bump_updates(
    operation: str, old_sha: str | None, new_sha: str | None
) -> None:
    path = "bt_api/bt_api_binance"
    context = v2_context(
        [path],
        [gitlink_record(path, operation=operation, old_sha=old_sha, new_sha=new_sha)],
    )

    assert_strict_and_report_only_violation(context, operation)


@pytest.mark.parametrize(
    ("old_sha", "new_sha"),
    [
        ("a" * 39, NEW_SHA),
        (ZERO_SHA, NEW_SHA),
        (OLD_SHA, ZERO_SHA),
        (OLD_SHA, OLD_SHA),
    ],
)
def test_v2_update_requires_distinct_nonzero_full_shas(old_sha: str, new_sha: str) -> None:
    path = "bt_api/bt_api_binance"
    context = v2_context([path], [gitlink_record(path, old_sha=old_sha, new_sha=new_sha)])

    assert_strict_and_report_only_violation(context, "SHA")


def test_v2_collection_errors_fail_closed_and_warn_in_report_only() -> None:
    path = "bt_api/bt_api_binance"
    context = v2_context(
        [path],
        [gitlink_record(path)],
        collection_errors=["raw diff parser rejected malformed record"],
    )

    assert_strict_and_report_only_violation(context, "collection_errors")


def test_missing_risk_label_fails_strict() -> None:
    context = load_fixture("pr-dev-r1.json")
    context["labels"] = ["target:dev"]
    result = run_validator(context, strict=True)
    assert result.returncode == 1
    assert "risk:" in result.stdout


def test_report_only_mode_never_blocks_but_warns() -> None:
    context = load_fixture("pr-dev-r1.json")
    context["target_branch"] = "master"
    result = run_validator(context, strict=False)
    assert result.returncode == 0
    assert "WARN" in result.stdout


def test_unknown_target_branch_fails_strict() -> None:
    context = load_fixture("pr-dev-r1.json")
    context["target_branch"] = "feature/rogue"
    result = run_validator(context, strict=True)
    assert result.returncode == 1
