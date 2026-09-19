#!/usr/bin/env python3
"""Quality ratchet: lint debt across the main package *and* the submodules may only go down.

The quality gates used to scan only ``bt_api_py/`` and ``tests/``, so the
submodules (``bt_api/bt_api_*/src``) accumulated 1100 ruff findings while the
gated packages stayed clean.  This script closes that hole: it re-scans the
full scope and compares the per-rule counts against a committed snapshot.

Rules:

1. A rule whose count grew since the snapshot fails the gate.
2. A rule that appears from nowhere (count > 0, absent from the snapshot) fails.
3. A rule that dropped passes, but the PR is expected to refresh the snapshot,
   so the new lower level becomes the floor.

Usage:
    python scripts/ci/check_quality_ratchet.py                 # enforce
    python scripts/ci/check_quality_ratchet.py --report        # print current counts only
    python scripts/ci/check_quality_ratchet.py --update        # refresh the snapshot (only when lower)
    python scripts/ci/check_quality_ratchet.py --scope bt_api/bt_api_okx/src
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_BASELINE = ROOT / "docs" / "acceptance" / "2026-09-19-quality-ratchet.json"
#: Main package, its tests, tooling and examples.  Submodule trees are added
#: dynamically below so a new ``bt_api_*`` checkout is gated the moment it lands.
MAIN_SCOPE = ("bt_api_py", "tests", "scripts", "examples")

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Comparison:
    """Outcome of comparing a fresh scan against the snapshot."""

    regressions: list[str]
    improvements: list[str]
    unchanged: list[str]

    @property
    def ok(self) -> bool:
        return not self.regressions


def default_scope() -> list[str]:
    """Main package, tests, tooling, examples and every submodule tree.

    Both ``src`` and ``tests`` of each submodule are gated: shipping code and
    its tests carry debt alike (``bt_api_binance/tests`` alone held 405 findings
    when this ratchet was introduced).
    """
    scope = [path for path in MAIN_SCOPE if (ROOT / path).is_dir()]
    for pattern in ("bt_api/bt_api_*/src", "bt_api/bt_api_*/tests"):
        scope.extend(
            sorted(str(path.relative_to(ROOT)) for path in ROOT.glob(pattern) if path.is_dir())
        )
    return scope


def counts_from_ruff_payload(payload: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Count findings per rule code from `ruff --output-format=json` output."""
    counts: dict[str, int] = {}
    for item in payload:
        code = str(item.get("code") or "unknown")
        counts[code] = counts.get(code, 0) + 1
    return counts


def compare_counts(current: dict[str, int], baseline: dict[str, int]) -> Comparison:
    """Compare per-rule counts; raising a count is a regression."""
    regressions: list[str] = []
    improvements: list[str] = []
    unchanged: list[str] = []
    for rule in sorted(set(current) | set(baseline)):
        now = current.get(rule, 0)
        was = baseline.get(rule, 0)
        if now > was:
            regressions.append(f"{rule}: {now} > baseline {was} (+{now - was})")
        elif now < was:
            improvements.append(f"{rule}: {now} < baseline {was} (-{was - now})")
        elif now:
            unchanged.append(f"{rule}: {now}")
    return Comparison(regressions=regressions, improvements=improvements, unchanged=unchanged)


def missing_scope_paths(resolved: Sequence[str], recorded: Sequence[str]) -> list[str]:
    """Paths the snapshot gated that are absent from the current scan.

    Without this guard a checkout that skipped the submodules would scan a much
    smaller scope, report far fewer findings, and *pass* the ratchet while the
    debt is simply invisible.  A missing path is an error, not a silent pass.
    """
    present = {path.rstrip("/") for path in resolved}
    return [path for path in recorded if path.rstrip("/") not in present]


def _run_ruff(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run one ruff invocation.

    The command is assembled from constants in this module (the interpreter,
    ``-m ruff`` and a fixed argument list); no external input reaches it, so
    S603 does not apply.
    """
    return subprocess.run(  # noqa: S603
        list(command), cwd=ROOT, capture_output=True, text=True, check=False
    )


def _ruff(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ruff, preferring the current interpreter's module over PATH."""
    result = _run_ruff([sys.executable, "-m", "ruff", *args])
    if result.returncode != 0 and "No module named" in (result.stderr or ""):
        result = _run_ruff(["ruff", *args])
    return result


def ruff_version() -> str:
    """The ruff version that produced (or is checked against) the snapshot."""
    completed = _ruff("--version")
    if completed.returncode != 0:
        raise SystemExit(f"could not determine the ruff version: {completed.stderr.strip()}")
    return completed.stdout.strip()


def scan_ruff(scope: Sequence[str], extra_args: Sequence[str] = ()) -> dict[str, int]:
    """Run ruff over ``scope`` and return the per-rule counts."""
    completed = _ruff("check", *scope, "--output-format=json", *extra_args)
    if completed.returncode not in (0, 1):  # 1 == findings, 0 == clean
        raise SystemExit(f"ruff failed (exit {completed.returncode}): {completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as error:  # pragma: no cover - defensive
        raise SystemExit(f"could not parse ruff output: {error}") from error
    return counts_from_ruff_payload(payload)


def load_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"baseline snapshot not found: {path}; run with --update to create it")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit(f"unsupported snapshot schema: {payload.get('schema_version')}")
    return payload


def build_snapshot(scope: Sequence[str], counts: dict[str, int], version: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "ruff_version": version,
        "scope": list(scope),
        "ruff": {
            "total": sum(counts.values()),
            "by_rule": dict(sorted(counts.items())),
        },
        "notes": (
            "Quality ratchet baseline (迭代07). Counts may only decrease; "
            "refresh with scripts/ci/check_quality_ratchet.py --update. "
            "Re-generate after any deliberate ruff version change."
        ),
    }


def _print_report(counts: dict[str, int], comparison: Comparison | None) -> None:
    total = sum(counts.values())
    print(f"ruff findings in scope: {total}")
    for rule, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {rule:8s} {count}")
    if comparison is None:
        return
    if comparison.improvements:
        print("\nimproved (refresh the snapshot in this PR):")
        for line in comparison.improvements:
            print(f"  - {line}")
    if comparison.regressions:
        print("\nREGRESSIONS:")
        for line in comparison.regressions:
            print(f"  - {line}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--scope", nargs="*", default=None, help="override the scan scope")
    parser.add_argument(
        "--update", action="store_true", help="write the snapshot (only when lower)"
    )
    parser.add_argument("--report", action="store_true", help="print counts without comparing")
    parser.add_argument(
        "--print-scope",
        action="store_true",
        help="print the gated paths (single source of truth for the Makefile)",
    )
    parser.add_argument(
        "--force-update", action="store_true", help="write the snapshot unconditionally"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    scope = args.scope or default_scope()
    if args.print_scope:
        print(" ".join(scope))
        return 0
    counts = scan_ruff(scope)

    if args.report:
        _print_report(counts, None)
        return 0

    if args.update or args.force_update:
        if not args.force_update and args.baseline.exists():
            baseline = load_baseline(args.baseline)
            comparison = compare_counts(counts, baseline["ruff"]["by_rule"])
            if comparison.regressions:
                _print_report(counts, comparison)
                print("\nrefusing to raise the ratchet; fix the regressions first", file=sys.stderr)
                return 1
            if not comparison.improvements:
                print("ratchet already at the current level; nothing to update")
                return 0
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(build_snapshot(scope, counts, ruff_version()), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(f"snapshot written: {args.baseline} (total {sum(counts.values())})")
        return 0

    baseline = load_baseline(args.baseline)
    recorded = baseline.get("ruff_version")
    current_version = ruff_version()
    if recorded and recorded != current_version:
        print(
            "WARNING: ruff version differs from the snapshot "
            f"(snapshot: {recorded}; running: {current_version}). "
            "Rule sets change between versions; re-generate the snapshot in a dedicated PR.",
            file=sys.stderr,
        )
    missing = missing_scope_paths(scope, baseline.get("scope", ()))
    if missing:
        print(
            "ERROR: the snapshot gated paths that are not present in this checkout:\n"
            + "\n".join(f"  - {path}" for path in missing)
            + "\nCheck out the submodules (actions/checkout with submodules: recursive) "
            "before trusting the ratchet; a smaller scope would pass silently.",
            file=sys.stderr,
        )
        return 1
    comparison = compare_counts(counts, baseline["ruff"]["by_rule"])
    _print_report(counts, comparison)
    if not comparison.ok:
        print(
            "\nquality ratchet failed: debt may only decrease. "
            "Fix the findings above (do not raise the baseline).",
            file=sys.stderr,
        )
        return 1
    if comparison.improvements:
        print(
            "\nratchet held, and debt decreased. Run with --update to lock in the new floor.",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
