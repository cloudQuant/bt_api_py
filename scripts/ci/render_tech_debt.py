#!/usr/bin/env python3
"""Render a read-only report for the selected gradual Ruff ignores."""

from __future__ import annotations

import json
import re
import subprocess  # fixed Ruff child process only; # nosec B404
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
PYPROJECT_PATH = ROOT / "pyproject.toml"
RULES = ("TC001", "TC003")
RULES_ARGUMENT = ",".join(RULES)
SCAN_SCOPE = ("bt_api_py", "tests", "scripts", "examples")


class TechDebtReportError(RuntimeError):
    """Raised when the Ruff config, child process, or report data is invalid."""


def _comments_from_root_ignore_table(source: str) -> dict[str, str]:
    """Extract inline reasons from the root ``[tool.ruff.lint].ignore`` array."""
    in_lint_table = False
    in_ignore_array = False
    found_lint_table = False
    found_ignore_array = False
    closed_ignore_array = False
    reasons: dict[str, str] = {}

    for line in source.splitlines():
        table_match = re.fullmatch(r"\s*\[([^\]]+)\]\s*(?:#.*)?", line)
        if table_match:
            in_lint_table = table_match.group(1) == "tool.ruff.lint"
            if in_lint_table:
                if found_lint_table:
                    raise TechDebtReportError("pyproject.toml repeats [tool.ruff.lint]")
                found_lint_table = True
            if in_ignore_array:
                raise TechDebtReportError("pyproject.toml has an unterminated Ruff ignore array")
            continue

        if not in_lint_table:
            continue

        if not in_ignore_array:
            if re.fullmatch(r"\s*ignore\s*=\s*\[\s*(?:#.*)?", line):
                if found_ignore_array:
                    raise TechDebtReportError("pyproject.toml repeats the Ruff ignore array")
                found_ignore_array = True
                in_ignore_array = True
            continue

        if re.fullmatch(r"\s*\]\s*(?:#.*)?", line):
            in_ignore_array = False
            closed_ignore_array = True
            break

        entry_match = re.fullmatch(r'\s*"(?P<rule>[A-Z0-9]+)"\s*,\s*#\s*(?P<reason>.*?)\s*', line)
        if entry_match:
            rule = entry_match.group("rule")
            if rule in RULES:
                if rule in reasons:
                    raise TechDebtReportError(f"pyproject.toml repeats ignore reason for {rule}")
                reasons[rule] = entry_match.group("reason").strip()

    if not found_lint_table or not found_ignore_array or not closed_ignore_array:
        raise TechDebtReportError("could not locate a closed [tool.ruff.lint].ignore array")
    return reasons


def load_rule_reasons(pyproject_path: Path = PYPROJECT_PATH) -> dict[str, str]:
    """Validate the required root Ruff ignores and return their inline reasons."""
    try:
        source = pyproject_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise TechDebtReportError(f"could not read {pyproject_path}: {error}") from error

    try:
        config: Any = tomllib.loads(source)
    except tomllib.TOMLDecodeError as error:
        raise TechDebtReportError(f"could not parse {pyproject_path}: {error}") from error

    try:
        ignore_rules = config["tool"]["ruff"]["lint"]["ignore"]
    except (KeyError, TypeError) as error:
        raise TechDebtReportError("missing [tool.ruff.lint].ignore in pyproject.toml") from error
    if not isinstance(ignore_rules, list) or any(
        not isinstance(rule, str) for rule in ignore_rules
    ):
        raise TechDebtReportError("[tool.ruff.lint].ignore must be a list of rule strings")

    missing = [rule for rule in RULES if ignore_rules.count(rule) == 0]
    duplicates = [rule for rule in RULES if ignore_rules.count(rule) > 1]
    if missing:
        raise TechDebtReportError("required Ruff ignore rules are missing: " + ", ".join(missing))
    if duplicates:
        raise TechDebtReportError(
            "required Ruff ignore rules are duplicated: " + ", ".join(duplicates)
        )

    comments = _comments_from_root_ignore_table(source)
    missing_reasons = [rule for rule in RULES if not comments.get(rule)]
    if missing_reasons:
        raise TechDebtReportError(
            "required Ruff ignore comments are missing: " + ", ".join(missing_reasons)
        )
    return {rule: comments[rule] for rule in RULES}


def parse_ruff_json(output: str, returncode: int) -> dict[str, int]:
    """Validate Ruff's JSON diagnostics and count only the fixed rule set."""
    if returncode not in {0, 1}:
        raise TechDebtReportError(f"Ruff exited unexpectedly with status {returncode}")
    try:
        payload: Any = json.loads(output)
    except json.JSONDecodeError as error:
        raise TechDebtReportError(f"could not parse Ruff JSON output: {error}") from error
    if not isinstance(payload, list):
        raise TechDebtReportError("Ruff JSON output must be a list of diagnostics")

    counts = dict.fromkeys(RULES, 0)
    for index, diagnostic in enumerate(payload):
        if not isinstance(diagnostic, Mapping):
            raise TechDebtReportError(f"Ruff diagnostic {index} must be an object")
        code = diagnostic.get("code")
        if not isinstance(code, str):
            raise TechDebtReportError(f"Ruff diagnostic {index} has no string rule code")
        if code not in counts:
            raise TechDebtReportError(f"unexpected Ruff rule in selected output: {code}")
        counts[code] += 1

    has_findings = any(counts.values())
    if (returncode == 0 and has_findings) or (returncode == 1 and not has_findings):
        raise TechDebtReportError("Ruff exit status is inconsistent with its JSON diagnostics")
    return counts


def render_report(counts: Mapping[str, int], reasons: Mapping[str, str]) -> str:
    """Render the fixed-scope counts, including zero-count rules, as Markdown."""
    if set(counts) != set(RULES) or set(reasons) != set(RULES):
        raise TechDebtReportError("report counts and reasons must cover exactly the selected rules")
    if any(
        not isinstance(counts[rule], int) or isinstance(counts[rule], bool) or counts[rule] < 0
        for rule in RULES
    ):
        raise TechDebtReportError("report counts must be non-negative integers")
    if any(not isinstance(reasons[rule], str) or not reasons[rule] for rule in RULES):
        raise TechDebtReportError("report reasons must be non-empty strings")

    total = sum(counts.values())
    lines = [
        "# Ruff gradual-ignore debt report",
        "",
        f"- Scope: {', '.join(f'`{path}`' for path in SCAN_SCOPE)}",
        "- Submodules are not scanned.",
        f"- Selected rules: {', '.join(f'`{rule}`' for rule in RULES)}",
        "",
        "| Rule | Findings | pyproject.toml ignore reason |",
        "|---|---:|---|",
    ]
    for rule in RULES:
        reason = reasons[rule].replace("|", "\\|")
        lines.append(f"| `{rule}` | {counts[rule]} | {reason} |")
    lines.extend((f"| **Total** | **{total}** | |", ""))
    return "\n".join(lines)


def run_ruff() -> subprocess.CompletedProcess[str]:
    """Run the fixed read-only Ruff selection from the repository root."""
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--output-format",
        "json",
        "--select",
        RULES_ARGUMENT,
        *SCAN_SCOPE,
    ]
    try:
        return subprocess.run(  # noqa: S603 - fixed argv/cwd, shell=False; # nosec B603
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
    except OSError as error:
        raise TechDebtReportError(f"could not run Ruff: {error}") from error


def main() -> int:
    """Print the current report, returning non-zero on any invalid state."""
    try:
        reasons = load_rule_reasons()
        completed = run_ruff()
        counts = parse_ruff_json(completed.stdout or "", completed.returncode)
        print(render_report(counts, reasons))
    except TechDebtReportError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
