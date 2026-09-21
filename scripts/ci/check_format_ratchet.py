#!/usr/bin/env python3
"""Keep per-submodule Ruff format debt from increasing."""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # fixed Ruff child process only; # nosec B404
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_BASELINE = ROOT / "docs" / "acceptance" / "2026-09-20-format-ratchet.json"
SCOPE_PATTERN = "bt_api/bt_api_*"
SCOPE_COMMAND = "ruff format --check bt_api/<module>"
SCHEMA_VERSION = 1


class FormatRatchetError(ValueError):
    """Raised when the snapshot or module scope is invalid."""


class FormatScanError(RuntimeError):
    """Raised when Ruff fails or its output cannot be counted safely."""


@dataclass(frozen=True)
class Comparison:
    """Outcome of comparing per-module format counts with the snapshot."""

    regressions: list[str]
    improvements: list[str]
    unchanged: list[str]

    @property
    def ok(self) -> bool:
        return not self.regressions


def default_scope(root: Path = ROOT) -> list[str]:
    """Return every checked-out ``bt_api/bt_api_*`` directory, sorted by name."""
    submodule_root = root / "bt_api"
    if not submodule_root.is_dir():
        return []
    return sorted(path.name for path in submodule_root.glob("bt_api_*") if path.is_dir())


def scope_paths(modules: Sequence[str]) -> list[str]:
    """Return repository-relative paths passed to individual Ruff invocations."""
    return [f"bt_api/{module}" for module in modules]


def parse_format_output(output: str, returncode: int) -> int:
    """Count files Ruff would reformat from its final summary line.

    Ruff's diff can include Markdown code blocks, so file extensions and diff
    hunks are not a safe counter. The summary emitted by ``format --check``
    counts all supported files, including Markdown, and is required here.
    """
    if returncode not in {0, 1}:
        raise FormatScanError(f"unexpected ruff format exit code: {returncode}")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise FormatScanError("ruff format produced no summary output")

    reformatted: int | None = None
    recognized_parts = 0
    for part in (value.strip() for value in lines[-1].split(",")):
        match = re.fullmatch(r"(\d+) files? would be reformatted", part)
        if match:
            if reformatted is not None:
                raise FormatScanError("ruff format summary repeats the reformatted count")
            reformatted = int(match.group(1))
            recognized_parts += 1
            continue
        if re.fullmatch(r"\d+ files? already formatted", part):
            recognized_parts += 1
            continue
        raise FormatScanError(f"could not parse ruff format summary: {lines[-1]!r}")

    if recognized_parts == 0:
        raise FormatScanError(f"could not parse ruff format summary: {lines[-1]!r}")
    count = reformatted or 0
    if returncode == 0 and count != 0:
        raise FormatScanError("ruff reported files to reformat with a successful exit code")
    if returncode == 1 and count == 0:
        raise FormatScanError("ruff reported a failure without files to reformat")
    return count


def _run_ruff(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a fixed Ruff argument vector without invoking a shell."""
    try:
        return subprocess.run(  # noqa: S603 - fixed argv/repo cwd, shell=False; # nosec B603
            list(command), cwd=ROOT, capture_output=True, text=True, check=False, shell=False
        )
    except OSError as error:
        raise FormatScanError(f"could not run ruff: {error}") from error


def _ruff(*args: str) -> subprocess.CompletedProcess[str]:
    """Prefer the current interpreter's Ruff module, falling back only if absent."""
    result = _run_ruff([sys.executable, "-m", "ruff", *args])
    missing_module = (result.stderr or "").lower().replace("'", "").replace('"', "")
    if result.returncode != 0 and "no module named ruff" in missing_module:
        result = _run_ruff(["ruff", *args])
    return result


def ruff_version() -> str:
    """Return the version string from the same executable selection as scans."""
    result = _ruff("--version")
    if result.returncode != 0:
        raise FormatScanError(
            f"could not determine the ruff version: {(result.stderr or '').strip()}"
        )
    version = (result.stdout or "").strip()
    if not re.fullmatch(r"ruff\s+\S+", version):
        raise FormatScanError(f"could not parse the ruff version: {version!r}")
    return version


def scan_module(module: str) -> int:
    """Run the workflow-equivalent format check for one submodule directory."""
    if not module.startswith("bt_api_") or Path(module).name != module:
        raise FormatScanError(f"invalid submodule name: {module!r}")
    path = f"bt_api/{module}"
    result = _ruff("format", "--check", path)
    try:
        return parse_format_output(result.stdout or "", result.returncode)
    except FormatScanError as error:
        stderr = (result.stderr or "").strip()
        detail = f"; stderr: {stderr}" if stderr else ""
        raise FormatScanError(f"{path}: {error}{detail}") from error


def scan_modules(modules: Sequence[str]) -> dict[str, int]:
    """Scan every module independently; any unparseable invocation fails closed."""
    if len(set(modules)) != len(modules):
        raise FormatScanError("duplicate submodule in scan scope")
    return {module: scan_module(module) for module in sorted(modules)}


def compare_format_counts(current: dict[str, int], baseline: dict[str, int]) -> Comparison:
    """Compare per-module debt; new or missing modules are always regressions."""
    regressions: list[str] = []
    improvements: list[str] = []
    unchanged: list[str] = []
    for module in sorted(set(current) | set(baseline)):
        if module not in baseline:
            regressions.append(f"{module}: unrecorded module")
            continue
        if module not in current:
            regressions.append(f"{module}: missing from current scope")
            continue
        now = current[module]
        was = baseline[module]
        if now > was:
            regressions.append(f"{module}: {now} > baseline {was} (+{now - was})")
        elif now < was:
            improvements.append(f"{module}: {now} < baseline {was} (-{was - now})")
        elif now:
            unchanged.append(f"{module}: {now}")
    return Comparison(regressions, improvements, unchanged)


def module_scope_differences(current: Sequence[str], recorded: Sequence[str]) -> list[str]:
    """Describe module additions/removals independently of their counts."""
    current_set = set(current)
    recorded_set = set(recorded)
    differences = [f"unrecorded module: {module}" for module in sorted(current_set - recorded_set)]
    differences.extend(
        f"missing recorded module: {module}" for module in sorted(recorded_set - current_set)
    )
    return differences


def _valid_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_baseline(payload: Any) -> None:
    """Validate snapshot shape, module coverage, and its derived total."""
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        version = payload.get("schema_version") if isinstance(payload, dict) else None
        raise FormatRatchetError(f"unsupported snapshot schema: {version!r}")
    if not isinstance(payload.get("generated_at"), str) or not payload["generated_at"]:
        raise FormatRatchetError("snapshot generated_at must be a non-empty string")
    if not isinstance(payload.get("ruff_version"), str) or not payload["ruff_version"].startswith(
        "ruff "
    ):
        raise FormatRatchetError("snapshot ruff_version is missing or invalid")
    if payload.get("scope") != {"pattern": SCOPE_PATTERN, "command": SCOPE_COMMAND}:
        raise FormatRatchetError("snapshot scope does not match the submodule-format workflow")
    modules = payload.get("modules")
    if (
        not isinstance(modules, list)
        or any(
            not isinstance(module, str) or not module.startswith("bt_api_") for module in modules
        )
        or modules != sorted(set(modules))
    ):
        raise FormatRatchetError("snapshot modules must be unique, sorted bt_api_* names")
    format_data = payload.get("format")
    if not isinstance(format_data, dict):
        raise FormatRatchetError("snapshot format section is missing")
    by_module = format_data.get("by_module")
    if not isinstance(by_module, dict) or set(by_module) != set(modules):
        raise FormatRatchetError("snapshot format.by_module keys must match modules exactly")
    if any(not _valid_count(value) for value in by_module.values()):
        raise FormatRatchetError("snapshot module counts must be non-negative integers")
    total = format_data.get("total")
    if not _valid_count(total) or total != sum(by_module.values()):
        raise FormatRatchetError("snapshot format.total does not match by_module counts")
    if not isinstance(payload.get("notes"), str) or not payload["notes"]:
        raise FormatRatchetError("snapshot notes must be a non-empty string")


def load_baseline(path: Path) -> dict[str, Any]:
    """Read and validate a committed format ratchet snapshot."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FormatRatchetError(f"baseline snapshot not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise FormatRatchetError(f"could not read baseline snapshot {path}: {error}") from error
    validate_baseline(payload)
    return payload


def build_snapshot(modules: Sequence[str], counts: dict[str, int], version: str) -> dict[str, Any]:
    """Build a deterministic per-module snapshot with a fresh UTC timestamp."""
    ordered_modules = sorted(modules)
    if len(set(ordered_modules)) != len(ordered_modules) or set(counts) != set(ordered_modules):
        raise FormatRatchetError("snapshot module list and count keys must match exactly")
    if any(not _valid_count(value) for value in counts.values()):
        raise FormatRatchetError("format counts must be non-negative integers")
    if not version.startswith("ruff "):
        raise FormatRatchetError(f"invalid ruff version: {version!r}")
    by_module = {module: counts[module] for module in ordered_modules}
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "ruff_version": version,
        "scope": {"pattern": SCOPE_PATTERN, "command": SCOPE_COMMAND},
        "modules": ordered_modules,
        "format": {"total": sum(by_module.values()), "by_module": by_module},
        "notes": (
            "Submodule format ratchet baseline. Per-module counts may only decrease; "
            "use --update after improvements or --force-update for an intentional rebaseline."
        ),
    }
    validate_baseline(snapshot)
    return snapshot


def _print_report(counts: dict[str, int], comparison: Comparison | None = None) -> None:
    print(f"files needing format in submodules: {sum(counts.values())}")
    for module, count in sorted(counts.items()):
        print(f"  {module:24s} {count}")
    if comparison is None:
        return
    if comparison.improvements:
        print("\nimproved (refresh the snapshot with --update):")
        for line in comparison.improvements:
            print(f"  - {line}")
    if comparison.regressions:
        print("\nREGRESSIONS:")
        for line in comparison.regressions:
            print(f"  - {line}")


def _write_snapshot(
    path: Path, modules: Sequence[str], counts: dict[str, int], version: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_snapshot(modules, counts, version)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _warn_version_mismatch(recorded: str, current: str) -> None:
    if recorded != current:
        print(
            "WARNING: ruff version differs from the snapshot "
            f"(snapshot: {recorded}; running: {current}). "
            "Rule output can change between versions; rebaseline deliberately.",
            file=sys.stderr,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--report", action="store_true", help="print module counts only")
    modes.add_argument(
        "--update",
        action="store_true",
        help="update when no count grows and at least one count falls",
    )
    modes.add_argument("--force-update", action="store_true", help="rebaseline unconditionally")
    modes.add_argument(
        "--print-scope", action="store_true", help="print module paths without scanning"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    modules = default_scope()
    if not modules:
        print("ERROR: no bt_api/bt_api_* module directories were discovered", file=sys.stderr)
        return 1
    if args.print_scope:
        print(" ".join(scope_paths(modules)))
        return 0

    baseline = None
    if args.baseline.exists() and not args.force_update:
        try:
            baseline = load_baseline(args.baseline)
        except FormatRatchetError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
    elif not args.baseline.exists() and not (args.report or args.update or args.force_update):
        print(
            f"ERROR: baseline snapshot not found: {args.baseline}; run with --update to create it",
            file=sys.stderr,
        )
        return 1

    scope_errors = (
        module_scope_differences(modules, baseline["modules"]) if baseline is not None else []
    )
    if scope_errors and not (args.report or args.force_update):
        print(
            "ERROR: discovered module scope differs from the baseline:\n"
            + "\n".join(f"  - {line}" for line in scope_errors)
            + "\nCheck out all submodules; use --force-update only for an intentional scope change.",
            file=sys.stderr,
        )
        return 1

    try:
        counts = scan_modules(modules)
        version = ruff_version()
    except FormatScanError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if baseline is not None:
        _warn_version_mismatch(baseline["ruff_version"], version)

    if args.report:
        _print_report(counts)
        if scope_errors:
            print("ERROR: " + "; ".join(scope_errors), file=sys.stderr)
            return 1
        return 0

    if args.force_update:
        try:
            _write_snapshot(args.baseline, modules, counts, version)
        except (OSError, FormatRatchetError) as error:
            print(f"ERROR: could not write snapshot: {error}", file=sys.stderr)
            return 1
        print(f"snapshot written: {args.baseline} (total {sum(counts.values())})")
        return 0

    if args.update:
        if baseline is not None:
            comparison = compare_format_counts(counts, baseline["format"]["by_module"])
            _print_report(counts, comparison)
            if comparison.regressions:
                print(
                    "\nrefusing to raise the format ratchet; fix regressions first", file=sys.stderr
                )
                return 1
            if not comparison.improvements:
                print("ratchet already at the current level; nothing to update")
                return 0
        try:
            _write_snapshot(args.baseline, modules, counts, version)
        except (OSError, FormatRatchetError) as error:
            print(f"ERROR: could not write snapshot: {error}", file=sys.stderr)
            return 1
        print(f"snapshot written: {args.baseline} (total {sum(counts.values())})")
        return 0

    if baseline is None:
        print(f"ERROR: baseline snapshot not found: {args.baseline}", file=sys.stderr)
        return 1
    comparison = compare_format_counts(counts, baseline["format"]["by_module"])
    _print_report(counts, comparison)
    if not comparison.ok:
        print(
            "\nformat ratchet failed: each module's debt may only decrease. "
            "New and missing modules require an explicit scope review.",
            file=sys.stderr,
        )
        return 1
    if comparison.improvements:
        print("\nratchet held with lower debt; run with --update to lock in the new floor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
