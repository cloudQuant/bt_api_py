#!/usr/bin/env python3
"""Measure docstring and parameter-annotation coverage for a Python package's public callables."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import TypeAlias

CallableNode: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef

EXCLUDED_DIRECTORY_NAMES = (
    "testing",
    "tests",
    "examples",
    "build",
    "dist",
    "generated",
    "__pycache__",
)

EXCLUSION_RULES = {
    "rules": [
        {
            "id": "private_parent_directory",
            "description": "Exclude Python files when any parent directory starts with '_'.",
        },
        {
            "id": "private_module",
            "description": (
                "Exclude modules whose filename starts with '_' except '__init__.py' "
                "in the source root or a public package."
            ),
        },
        {
            "id": "named_directories",
            "description": "Exclude Python files beneath each fixed directory name listed here.",
        },
        {
            "id": "external_symlink",
            "description": "Do not read Python files whose symlink target is outside the source root.",
        },
    ],
    "directory_names": list(EXCLUDED_DIRECTORY_NAMES),
}


def _metric(numerator: int, denominator: int) -> dict[str, int | float | None]:
    percentage = round(numerator * 100 / denominator, 2) if denominator else None
    return {
        "numerator": numerator,
        "denominator": denominator,
        "percentage": percentage,
    }


def _decorator_name(decorator: ast.expr) -> str | None:
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    return None


def _has_decorator(node: CallableNode, name: str) -> bool:
    return any(_decorator_name(decorator) == name for decorator in node.decorator_list)


def _is_overload(node: CallableNode) -> bool:
    return _has_decorator(node, "overload")


def _parameters(node: CallableNode, *, is_method: bool) -> list[ast.arg]:
    arguments = node.args
    positional_parameters = [*arguments.posonlyargs, *arguments.args]
    parameters = [*positional_parameters, *arguments.kwonlyargs]
    if arguments.vararg is not None:
        parameters.append(arguments.vararg)
    if arguments.kwarg is not None:
        parameters.append(arguments.kwarg)
    if (
        is_method
        and not _has_decorator(node, "staticmethod")
        and positional_parameters
        and positional_parameters[0].arg in {"self", "cls"}
    ):
        parameters.remove(positional_parameters[0])
    return parameters


def _score_callable(node: CallableNode, *, is_method: bool) -> tuple[bool, int, int]:
    parameters = _parameters(node, is_method=is_method)
    annotated_parameters = sum(parameter.annotation is not None for parameter in parameters)
    has_docstring = ast.get_docstring(node) is not None
    return has_docstring, annotated_parameters, len(parameters)


def _excluded_reasons(path: Path, source_root: Path) -> list[str]:
    relative_path = path.relative_to(source_root)
    parent_parts = relative_path.parts[:-1]
    reasons: list[str] = []

    if any(part.startswith("_") for part in parent_parts):
        reasons.append("private_parent_directory")
    if path.name.startswith("_") and path.name != "__init__.py":
        reasons.append("private_module")
    reasons.extend(
        f"directory:{directory_name}"
        for directory_name in EXCLUDED_DIRECTORY_NAMES
        if directory_name in parent_parts
    )
    if not path.resolve().is_relative_to(source_root):
        reasons.append("external_symlink")
    return reasons


def _measure_file(path: Path, relative_path: str) -> dict[str, object]:
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source, filename=relative_path)
    callables: list[tuple[CallableNode, bool]] = []

    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_") and not _is_overload(node):
                callables.append((node, False))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            callables.extend(
                (member, True)
                for member in node.body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not member.name.startswith("_")
                and not _is_overload(member)
            )

    docstring_numerator = 0
    parameter_annotation_numerator = 0
    parameter_denominator = 0
    for node, is_method in callables:
        has_docstring, annotated_parameters, parameter_count = _score_callable(
            node, is_method=is_method
        )
        docstring_numerator += has_docstring
        parameter_annotation_numerator += annotated_parameters
        parameter_denominator += parameter_count

    return {
        "path": relative_path,
        "public_callables": len(callables),
        "docstrings": _metric(docstring_numerator, len(callables)),
        "parameter_annotations": _metric(
            parameter_annotation_numerator,
            parameter_denominator,
        ),
    }


def measure_public_api_quality(source_root: Path) -> dict[str, object]:
    """Build a deterministic report for public callables under ``source_root`` only."""
    source_root = source_root.expanduser().resolve()
    if not source_root.is_dir():
        raise NotADirectoryError(f"source root is not a directory: {source_root}")

    included_files: list[dict[str, object]] = []
    excluded_files: list[dict[str, object]] = []
    totals = {
        "public_callables": 0,
        "documented_callables": 0,
        "annotated_parameters": 0,
        "parameters": 0,
    }

    candidates = sorted(
        (path for path in source_root.rglob("*.py") if path.is_file()),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )
    for path in candidates:
        relative_path = path.relative_to(source_root).as_posix()
        reasons = _excluded_reasons(path, source_root)
        if reasons:
            excluded_files.append({"path": relative_path, "reasons": reasons})
            continue

        file_report = _measure_file(path, relative_path)
        included_files.append(file_report)
        totals["public_callables"] += int(file_report["public_callables"])
        docstrings = file_report["docstrings"]
        parameter_annotations = file_report["parameter_annotations"]
        totals["documented_callables"] += int(docstrings["numerator"])
        totals["annotated_parameters"] += int(parameter_annotations["numerator"])
        totals["parameters"] += int(parameter_annotations["denominator"])

    return {
        "schema_version": 1,
        "source_root": source_root.name,
        "metrics": {
            "docstrings": _metric(
                totals["documented_callables"],
                totals["public_callables"],
            ),
            "parameter_annotations": _metric(
                totals["annotated_parameters"],
                totals["parameters"],
            ),
        },
        "files": included_files,
        "excluded_files": excluded_files,
        "exclusion_rules": EXCLUSION_RULES,
    }


def _format_metric(metric: dict[str, int | float | None]) -> str:
    percentage = metric["percentage"]
    percentage_text = "N/A" if percentage is None else f"{percentage:.2f}%"
    return f"{metric['numerator']}/{metric['denominator']} ({percentage_text})"


def _print_text_report(report: dict[str, object]) -> None:
    metrics = report["metrics"]
    print(f"Public API quality report for {report['source_root']}/")
    print(f"Docstrings: {_format_metric(metrics['docstrings'])}")
    print(f"Parameter annotations: {_format_metric(metrics['parameter_annotations'])}")

    print("\nPer-file details:")
    files = report["files"]
    if not files:
        print("  (no included Python files)")
    for file_report in files:
        print(
            f"  {file_report['path']}: public callables={file_report['public_callables']}; "
            f"docstrings={_format_metric(file_report['docstrings'])}; "
            "parameter annotations="
            f"{_format_metric(file_report['parameter_annotations'])}"
        )

    print("\nExclusion rules:")
    for rule in report["exclusion_rules"]["rules"]:
        print(f"  - {rule['description']}")
    print(f"  - Directory names: {', '.join(report['exclusion_rules']['directory_names'])}")

    print("\nExcluded files:")
    excluded_files = report["excluded_files"]
    if not excluded_files:
        print("  (none)")
    for file_report in excluded_files:
        reasons = ", ".join(file_report["reasons"])
        print(f"  {file_report['path']} [{reasons}]")


def _parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[1] / "bt_api_py"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source_root",
        nargs="?",
        type=Path,
        default=default_root,
        help="package source root to scan (default: repository bt_api_py/)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format (default: text)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_arguments(argv)
    try:
        report = measure_public_api_quality(args.source_root)
    except (OSError, SyntaxError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
