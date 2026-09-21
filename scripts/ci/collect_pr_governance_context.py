#!/usr/bin/env python3
"""Collect a schema-v2 PR governance context using offline Git diffs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
ZERO_SHA = "0" * 40


def _is_commit_sha(value: str) -> bool:
    return bool(SHA_RE.fullmatch(value)) and value != ZERO_SHA


def _run_git(args: list[str], label: str, errors: list[str]) -> str | None:
    argv = ["git", *args]
    try:
        # PR metadata never enters argv; these are fixed Git subcommands and
        # flags from this module, with shell execution explicitly disabled.
        result = subprocess.run(  # noqa: S603 - module-controlled argv; shell=False
            argv,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="strict",
            shell=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        errors.append(f"git {label} failed: {type(exc).__name__}: {exc}")
        return None

    if result.returncode != 0:
        detail = result.stderr.strip()
        errors.append(
            f"git {label} failed with exit code {result.returncode}"
            + (f": {detail}" if detail else "")
        )
        return None
    return result.stdout


def _parse_name_only(output: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    if not output:
        return [], errors
    if not output.endswith("\0"):
        errors.append("git name-only output is not NUL-terminated")
    paths = output.split("\0")
    if paths[-1] == "":
        paths.pop()
    if any(not path for path in paths):
        errors.append("git name-only output contains an empty path")
        paths = [path for path in paths if path]
    return paths, errors


def _is_sha(value: str) -> bool:
    return bool(SHA_RE.fullmatch(value)) and value != ZERO_SHA


def _parse_raw_diff(output: str) -> tuple[list[dict[str, str | None]], list[str]]:
    records: list[dict[str, str | None]] = []
    errors: list[str] = []
    if not output:
        return records, errors
    if not output.endswith("\0"):
        errors.append("git raw diff output is not NUL-terminated")

    fields = output.split("\0")
    if fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        errors.append("git raw diff output is truncated before a path")
        fields.pop()

    for index in range(0, len(fields), 2):
        header, path = fields[index], fields[index + 1]
        if not header.startswith(":"):
            errors.append("git raw diff contains a malformed record header")
            continue
        columns = header[1:].split()
        if len(columns) != 5:
            errors.append("git raw diff contains a malformed record header")
            continue

        old_mode, new_mode, old_oid, new_oid, status = columns
        if old_mode != "160000" and new_mode != "160000":
            continue

        mode_pair = (old_mode, new_mode)
        if mode_pair == ("160000", "160000"):
            operation = "update"
        elif mode_pair == ("000000", "160000"):
            operation = "add"
        elif mode_pair == ("160000", "000000"):
            operation = "delete"
        else:
            errors.append(
                f"gitlink path {path!r} has unsupported mode transition {old_mode}->{new_mode}"
            )
            continue

        expected_status = {"update": "M", "add": "A", "delete": "D"}[operation]
        if status != expected_status:
            errors.append(f"gitlink path {path!r} has unexpected status {status!r} for {operation}")
            continue
        if not path:
            errors.append("git raw diff contains a gitlink record with an empty path")
            continue

        if operation == "update":
            if not _is_sha(old_oid) or not _is_sha(new_oid):
                errors.append(f"gitlink update {path!r} requires full non-zero 40-hex SHAs")
                continue
            if old_oid == new_oid:
                errors.append(f"gitlink update {path!r} must change to a different SHA")
                continue
            old_sha: str | None = old_oid
            new_sha: str | None = new_oid
        elif operation == "add":
            if old_oid != ZERO_SHA or not _is_sha(new_oid):
                errors.append(f"gitlink add {path!r} has invalid zero/full SHA values")
                continue
            old_sha = None
            new_sha = new_oid
        else:
            if not _is_sha(old_oid) or new_oid != ZERO_SHA:
                errors.append(f"gitlink delete {path!r} has invalid full/zero SHA values")
                continue
            old_sha = old_oid
            new_sha = None

        records.append(
            {
                "path": path,
                "operation": operation,
                "old_sha": old_sha,
                "new_sha": new_sha,
            }
        )

    return records, errors


def collect_context(
    base_sha: str,
    head_sha: str,
    *,
    target_branch: str = "",
    labels: list[str] | None = None,
    body: str = "",
) -> dict[str, Any]:
    """Collect full changed paths and one record per raw Git link change."""
    context: dict[str, Any] = {
        "schema_version": 2,
        "target_branch": target_branch,
        "labels": [label.strip() for label in labels or [] if label.strip()],
        "body": body,
        "changed_files": [],
        "gitlink_changes": [],
        "collection_errors": [],
    }
    errors: list[str] = context["collection_errors"]

    if not _is_commit_sha(base_sha) or not _is_commit_sha(head_sha):
        errors.append("BASE_SHA and HEAD_SHA must be full non-zero 40-hex commit IDs")
        return context

    revision_range = f"{base_sha}..{head_sha}"
    names_output = _run_git(
        ["diff", "--name-only", "-z", "--no-renames", revision_range],
        "name-only diff",
        errors,
    )
    if names_output is not None:
        paths, parse_errors = _parse_name_only(names_output)
        context["changed_files"] = paths
        errors.extend(parse_errors)

    raw_output = _run_git(
        [
            "diff",
            "--raw",
            "-z",
            "--no-renames",
            "--no-ext-diff",
            "--abbrev=40",
            revision_range,
        ],
        "raw diff",
        errors,
    )
    if raw_output is not None:
        records, parse_errors = _parse_raw_diff(raw_output)
        context["gitlink_changes"] = records
        errors.extend(parse_errors)

    return context


def _parse_labels(raw_labels: str) -> list[str]:
    return [label.strip() for label in raw_labels.split(",") if label.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Output path for context JSON")
    args = parser.parse_args()

    context = collect_context(
        os.environ.get("BASE_SHA", ""),
        os.environ.get("HEAD_SHA", ""),
        target_branch=os.environ.get("TARGET_BRANCH", ""),
        labels=_parse_labels(os.environ.get("PR_LABELS", "")),
        body=os.environ.get("PR_BODY", ""),
    )
    Path(args.output).write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
