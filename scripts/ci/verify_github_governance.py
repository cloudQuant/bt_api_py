#!/usr/bin/env python3
"""Verify a sanitized GitHub Rulesets API summary against in-repo governance manifests.

Usage:
    python scripts/ci/verify_github_governance.py \
        --actual <sanitized-api-summary.json> \
        --manifest-dir <dir-with-*.json-manifests>

Exit codes: 0 = no drift, 1 = drift detected, 2 = input error.

The --actual file is produced by an administrator from read-only API responses
(GET /repos/{owner}/{repo}/rulesets plus per-ruleset details, and optionally
GET /repos/{owner}/{repo}/codeowners/errors). It must never contain tokens or
raw private payloads.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_INPUT_ERROR = 2


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"{EXIT_INPUT_ERROR}: file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{EXIT_INPUT_ERROR}: invalid JSON in {path}: {exc}") from exc


def find_ruleset(rulesets: list[dict[str, Any]], ref_pattern: str) -> dict[str, Any] | None:
    for ruleset in rulesets:
        refs = ruleset.get("includes_refs") or []
        if any(
            ref == ref_pattern or ref_pattern.endswith("*") and ref.startswith(ref_pattern[:-1])
            for ref in refs
        ):
            return ruleset
    return None


def rule_of_type(ruleset: dict[str, Any], rule_type: str) -> dict[str, Any] | None:
    for rule in ruleset.get("rules") or []:
        if rule.get("type") == rule_type:
            return rule
    return None


def branch_ref(target: str) -> str:
    return target if "/" in target else f"refs/heads/{target}"


def check_manifest(
    manifest: dict[str, Any],
    ruleset: dict[str, Any] | None,
    label: str,
    drifts: list[str],
) -> None:
    expected_enforcement = manifest.get("enforcement")

    if expected_enforcement == "disabled":
        if ruleset is not None and ruleset.get("enforcement") == "active":
            drifts.append(
                f"{label}: ruleset is active but manifest requires disabled "
                f"(pending gate: {manifest.get('pending_decision_gate', 'n/a')})"
            )
        return

    if ruleset is None:
        drifts.append(f"{label}: no ruleset found for {branch_ref(str(manifest['target']))}")
        return

    if ruleset.get("enforcement") != expected_enforcement:
        drifts.append(
            f"{label}: enforcement is '{ruleset.get('enforcement')}', "
            f"manifest requires '{expected_enforcement}'"
        )

    pr_rule = rule_of_type(ruleset, "pull_request")
    if pr_rule is None:
        drifts.append(f"{label}: pull_request rule missing")
        params: dict[str, Any] = {}
    else:
        params = pr_rule.get("parameters") or {}

    expected_approvals = manifest.get("approvals_required")
    actual_approvals = params.get("required_approving_review_count")
    if expected_approvals is not None and actual_approvals != expected_approvals:
        drifts.append(
            f"{label}: approvals required is {actual_approvals}, manifest requires {expected_approvals}"
        )

    if manifest.get("dismiss_stale_reviews") and not params.get("dismiss_stale_reviews_on_push"):
        drifts.append(f"{label}: dismiss_stale_reviews_on_push is not enabled")

    if manifest.get("require_code_owner_review") and not params.get("require_code_owner_review"):
        drifts.append(f"{label}: require_code_owner_review is not enabled")

    if manifest.get("block_force_pushes") and rule_of_type(ruleset, "non_fast_forward") is None:
        drifts.append(f"{label}: force pushes are not blocked (missing non_fast_forward rule)")

    if manifest.get("block_deletions") and rule_of_type(ruleset, "deletion") is None:
        drifts.append(f"{label}: deletions are not blocked (missing deletion rule)")

    required_checks = manifest.get("required_checks") or []
    status_rule = rule_of_type(ruleset, "required_status_checks")
    contexts: set[str] = set()
    if status_rule is not None:
        checks = (status_rule.get("parameters") or {}).get("required_status_checks") or []
        contexts = {check.get("context", "") for check in checks}
    for context in required_checks:
        if context not in contexts:
            drifts.append(
                f"{label}: required_status_checks is missing required check '{context}' "
                f"(has: {sorted(contexts)})"
            )

    expected_bypass = manifest.get("bypass_actors") or []
    actual_bypass = ruleset.get("bypass_actors") or []
    if sorted(map(json.dumps, expected_bypass)) != sorted(map(json.dumps, actual_bypass)):
        drifts.append(
            f"{label}: bypass actors differ from manifest (expected {len(expected_bypass)}, "
            f"found {len(actual_bypass)}); every bypass actor must be D4/D3-approved"
        )


def verify(actual_path: Path, manifest_dir: Path) -> list[str]:
    actual = load_json(actual_path)
    rulesets = actual.get("rulesets") or []
    drifts: list[str] = []

    owners_errors = actual.get("codeowners_errors") or []
    if owners_errors:
        first = owners_errors[0]
        drifts.append(
            f"CODEOWNERS has {len(owners_errors)} unresolved error(s), e.g. "
            f"{first.get('path', '?')}:{first.get('line', '?')}: {first.get('message', '?')}"
        )

    for manifest_path in sorted(manifest_dir.glob("*.json")):
        manifest = load_json(manifest_path)
        if "target" not in manifest or "enforcement" not in manifest:
            drifts.append(f"{manifest_path.name}: manifest lacks 'target' or 'enforcement'")
            continue
        ruleset = find_ruleset(rulesets, branch_ref(str(manifest["target"])))
        check_manifest(manifest, ruleset, manifest_path.name, drifts)

    return drifts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--actual", type=Path, required=True, help=load_json.__doc__)
    parser.add_argument("--manifest-dir", type=Path, required=True)
    args = parser.parse_args()

    if not args.actual.is_file():
        print(f"input error: {args.actual} is not a file", file=sys.stderr)
        return EXIT_INPUT_ERROR
    if not args.manifest_dir.is_dir():
        print(f"input error: {args.manifest_dir} is not a directory", file=sys.stderr)
        return EXIT_INPUT_ERROR

    drifts = verify(args.actual, args.manifest_dir)
    if drifts:
        for drift in drifts:
            print(f"DRIFT: {drift}")
        print(f"\n{len(drifts)} drift item(s); see docs/governance/ for remediation.")
        return EXIT_DRIFT

    print("OK: GitHub state matches all governance manifests.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
