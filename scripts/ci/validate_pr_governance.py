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
from pathlib import Path
from typing import Any

VALID_TARGETS = {"dev", "master", "code-optimization"}
RISK_LABELS = {"risk:r0", "risk:r1", "risk:r2", "risk:r3"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_RE = re.compile(r"复现|repro|regression|回归|pytest|test", re.IGNORECASE)

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_INPUT_ERROR = 2


def read_context(raw_path: str) -> dict[str, Any]:
    if raw_path == "-":
        return json.loads(sys.stdin.read())
    try:
        return json.loads(Path(raw_path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"{EXIT_INPUT_ERROR}: file not found: {raw_path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{EXIT_INPUT_ERROR}: invalid JSON: {exc}") from exc


def validate(context: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    target = context.get("target_branch")
    labels = set(context.get("labels") or [])
    body = context.get("body") or ""
    changed = context.get("changed_files") or []

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

    if context.get("submodules_changed"):
        for key in ("old_sha", "new_sha"):
            value = context.get(key)
            if not value or not SHA_RE.match(str(value)):
                violations.append(
                    f"submodule change requires a full 40-hex {key} "
                    "(plugin PR link and rollback SHA belong in the description)"
                )
        if not changed or not any(
            str(path).startswith(("bt_api/", ".gitmodules")) for path in changed
        ):
            violations.append(
                "submodules_changed=true but no bt_api/ or .gitmodules path in changed_files"
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
