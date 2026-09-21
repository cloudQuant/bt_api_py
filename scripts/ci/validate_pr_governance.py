#!/usr/bin/env python3
"""Validate PR governance metadata against the routing table in docs/governance/branch-model.md.

Usage:
    python scripts/ci/validate_pr_governance.py --context <context.json|- > [--strict]

Exit codes: 0 = valid (or report-only), 1 = strict violation, 2 = input error.
Default is report-only (always exit 0, violations prefixed WARN); --strict is
enabled by maintainers after the observation period and turns FAIL into a
blocking check.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, NoReturn

VALID_TARGETS = {"dev", "master", "code-optimization"}
RISK_LABELS = {"risk:r0", "risk:r1", "risk:r2", "risk:r3"}
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
ZERO_SHA = "0" * 40
EVIDENCE_RE = re.compile(r"复现|repro|regression|回归|pytest|test", re.IGNORECASE)

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_INPUT_ERROR = 2


def _raise_input_error(message: str) -> NoReturn:
    print(f"{EXIT_INPUT_ERROR}: {message}", file=sys.stderr)
    raise SystemExit(EXIT_INPUT_ERROR)


def read_context(raw_path: str) -> dict[str, Any]:
    try:
        if raw_path == "-":
            context = json.loads(sys.stdin.read())
        else:
            context = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _raise_input_error(f"file not found: {raw_path}")
    except json.JSONDecodeError as exc:
        _raise_input_error(f"invalid JSON: {exc}")
    if not isinstance(context, dict):
        _raise_input_error("top-level context must be a JSON object")
    return context


def _is_submodule_path(path: Any) -> bool:
    normalized = str(path).rstrip("/")
    if normalized == ".gitmodules":
        return True
    parts = normalized.split("/")
    return (
        len(parts) == 2
        and parts[0] == "bt_api"
        and parts[1].startswith("bt_api_")
        and len(parts[1]) > len("bt_api_")
    )


def _is_gitlink_path(path: str) -> bool:
    return path != ".gitmodules" and _is_submodule_path(path)


def _validate_v2_gitlinks(
    context: dict[str, Any], changed_files: list[str], violations: list[str]
) -> None:
    raw_errors = context.get("collection_errors")
    if not isinstance(raw_errors, list):
        violations.append("schema_version 2 requires a collection_errors list")
    elif raw_errors:
        details = "; ".join(str(error) for error in raw_errors)
        violations.append(f"collection_errors is non-empty: {details}")

    raw_records = context.get("gitlink_changes")
    if not isinstance(raw_records, list):
        violations.append("schema_version 2 requires a gitlink_changes list")
        records: list[Any] = []
    else:
        records = raw_records

    changed_gitlinks = [path for path in changed_files if _is_gitlink_path(path)]
    changed_counts = Counter(changed_gitlinks)
    for path, count in changed_counts.items():
        if count > 1:
            violations.append(f"duplicate gitlink path in changed_files: {path}")

    record_paths: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            violations.append(f"gitlink_changes[{index}] must be an object")
            continue

        path = record.get("path")
        if not isinstance(path, str) or not _is_gitlink_path(path):
            violations.append(
                f"gitlink_changes[{index}].path must be a bt_api/bt_api_<name> gitlink path"
            )
            continue
        record_paths.append(path)

        operation = record.get("operation")
        if operation != "update":
            violations.append(
                f"gitlink_changes[{index}] operation {operation!r} is not an accepted update"
            )
            continue

        old_sha = record.get("old_sha")
        new_sha = record.get("new_sha")
        if (
            not isinstance(old_sha, str)
            or not SHA_RE.fullmatch(old_sha)
            or old_sha == ZERO_SHA
            or not isinstance(new_sha, str)
            or not SHA_RE.fullmatch(new_sha)
            or new_sha == ZERO_SHA
            or old_sha.lower() == new_sha.lower()
        ):
            violations.append(
                f"gitlink update {path!r} requires distinct, non-zero full 40-hex old/new SHA values"
            )

    record_counts = Counter(record_paths)
    for path, count in record_counts.items():
        if count > 1:
            violations.append(f"duplicate gitlink record path: {path}")
        if changed_counts.get(path, 0) != 1:
            violations.append(
                f"gitlink record path {path!r} must map to exactly one changed_files entry"
            )

    violations.extend(
        f"gitlink path {path!r} requires exactly one matching gitlink_changes record"
        for path in changed_gitlinks
        if record_counts.get(path, 0) != 1
    )

    if ".gitmodules" in changed_files and not record_paths:
        violations.append(".gitmodules changed without a corresponding gitlink_changes record")


def validate(context: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    target = context.get("target_branch")
    labels = set(context.get("labels") or [])
    body = context.get("body") or ""
    raw_changed = context.get("changed_files", [])
    if not isinstance(raw_changed, list) or any(not isinstance(path, str) for path in raw_changed):
        violations.append("changed_files must be a list of path strings")
        changed: list[str] = []
    else:
        changed = raw_changed
    submodule_paths = [path for path in changed if _is_submodule_path(path)]

    if target not in VALID_TARGETS:
        violations.append(
            f"target_branch '{target}' is not routable; expected one of {sorted(VALID_TARGETS)}"
        )

    risk_labels = labels & RISK_LABELS
    if len(risk_labels) != 1:
        violations.append(
            f"exactly one risk: label is required, found {sorted(risk_labels) or 'none'}"
        )

    if target == "master":
        missing = {"release:hotfix", "risk:r3"} - labels
        if missing:
            violations.append(
                f"PRs targeting master are restricted to hotfix/promotion with evidence; "
                f"missing labels: {sorted(missing)}"
            )
        if not EVIDENCE_RE.search(body):
            violations.append(
                "master PR lacks reproduction/regression/test evidence in the description"
            )

    schema_version = context.get("schema_version")
    if type(schema_version) is int and schema_version == 2:
        if "changed_files" not in context:
            violations.append("schema_version 2 requires a changed_files list")
        _validate_v2_gitlinks(context, changed, violations)
    elif submodule_paths or context.get("submodules_changed") is True:
        violations.append(
            "submodule metadata/gitlink path(s) "
            f"{submodule_paths} require schema_version 2 and per-path gitlink_changes "
            "records; legacy global old_sha/new_sha fields are not authoritative"
        )

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--context", required=True, help="PR context JSON path, or '-' for stdin")
    parser.add_argument("--strict", action="store_true", help="exit non-zero on any violation")
    args = parser.parse_args()

    context = read_context(args.context)
    violations = validate(context)

    if not violations:
        print("OK: PR metadata satisfies the governance routing table.")
        return EXIT_OK

    prefix = "FAIL" if args.strict else "WARN"
    for violation in violations:
        print(f"{prefix}: {violation}")
    if args.strict:
        print(f"\n{len(violations)} governance violation(s); blocking.")
        return EXIT_VIOLATION

    print("\nreport-only mode: fix the items above before merge review.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
