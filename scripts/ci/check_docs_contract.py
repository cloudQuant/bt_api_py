#!/usr/bin/env python3
"""Fail active documentation claims that exceed current contract evidence."""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
ACTIVE_DOCUMENTS = (
    "README.md",
    "docs/AGENTS.md",
    "docs/index.md",
    "docs/project-overview.md",
    "docs/explanation/architecture.md",
    "docs/reference/core-api.md",
    "docs/reference/bt_api.md",
    "docs/getting-started/installation.md",
    "docs/guides/usage_guide.md",
    "docs/operations/support-status-policy.md",
)
FORBIDDEN_PATHS = ("bt_api_py/feeds", "bt_api_py/containers")
FORBIDDEN_PYTHON_CLAIMS = (
    re.compile(r"(?i)(?:supports?|支持)[^\n。]{0,20}(?:python\s*)?(?:`?3\.9`?|`?3\.10`?)"),
    re.compile(r"(?i)python\s+`?3\.9`?\s*(?:to|-|–)"),
)
CERTIFICATION_TIERS = {"fully_supported", "certified"}
SHA_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def validate_support_matrix(data: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    policy = data.get("policy") or {}
    if policy.get("blocking_python") != ["3.11", "3.12", "3.13"]:
        errors.append("support matrix must declare Python 3.11-3.13 as blocking")
    if policy.get("canary_python") != ["3.14"]:
        errors.append("support matrix must declare Python 3.14 as canary")
    now = datetime.now(UTC)
    for entry in data.get("entries", []):
        tier = str(entry.get("tier") or "")
        if tier not in CERTIFICATION_TIERS:
            continue
        name = str(entry.get("name") or "<unnamed>")
        for field in ("receipt_path", "head_sha", "profile", "validated_at", "expires_at"):
            if not entry.get(field):
                errors.append(f"{name}: {tier} entry is missing {field}")
        receipt = root / str(entry.get("receipt_path") or "")
        if entry.get("receipt_path") and not receipt.is_file():
            errors.append(f"{name}: receipt_path does not exist: {entry['receipt_path']}")
        if entry.get("head_sha") and not SHA_PATTERN.fullmatch(str(entry["head_sha"])):
            errors.append(f"{name}: head_sha is not a commit-like SHA")
        expires_at = _parse_timestamp(str(entry.get("expires_at") or ""))
        if expires_at is None:
            errors.append(f"{name}: expires_at is not an ISO timestamp")
        elif expires_at <= now:
            errors.append(f"{name}: evidence has expired")
    return errors


def check_docs_contract(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for relative_path in ACTIVE_DOCUMENTS:
        path = root / relative_path
        if not path.is_file():
            errors.append(f"active documentation missing: {relative_path}")
            continue
        content = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_PATHS:
            if forbidden in content:
                errors.append(f"{relative_path}: references retired path {forbidden}")
        for claim in FORBIDDEN_PYTHON_CLAIMS:
            if match := claim.search(content):
                errors.append(f"{relative_path}: overstates Python support with {match.group(0)}")
        if re.search(r"order_type\s*=\s*[\"'](?:market|limit)[\"']", content):
            errors.append(
                f"{relative_path}: uses a bare legacy order_type example instead of OrderRequest"
            )
        if relative_path != "docs/operations/support-status-policy.md" and re.search(
            r"(?i)\b(?:fully supported|fully_supported|certified)\b", content
        ):
            errors.append(
                f"{relative_path}: makes a certification-tier claim outside policy metadata"
            )
    matrix_path = root / "docs" / "data" / "exchange_support_matrix.json"
    if not matrix_path.is_file():
        errors.append("support matrix is missing")
    else:
        errors.extend(validate_support_matrix(json.loads(matrix_path.read_text()), root))
    return errors


def main() -> int:
    errors = check_docs_contract()
    if errors:
        print("Documentation contract failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Documentation contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
