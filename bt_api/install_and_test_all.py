#!/usr/bin/env python3
"""Compatibility wrapper for the isolated submodule validation system.

The old implementation installed many editable submodules concurrently in the
same interpreter.  Keep this entry point for community scripts, but delegate
to ``scripts/ci/submodule_validation.py`` so every result has JSON, JUnit and
per-phase logs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="all")
    parser.add_argument("--artifacts-dir", type=Path, default=Path("submodule-artifacts"))
    parser.add_argument("--diagnostic", action="store_true")
    # Accepted only to avoid breaking existing automation; parallel shared
    # environments are intentionally no longer implemented.
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--report", choices=["markdown"], default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repository_root = Path(__file__).resolve().parent.parent
    command = [
        sys.executable,
        str(repository_root / "scripts" / "ci" / "submodule_validation.py"),
        "--profile",
        args.profile,
        "--artifacts-dir",
        str(args.artifacts_dir),
        "--repository-root",
        str(repository_root),
    ]
    if args.diagnostic:
        command.append("--diagnostic")
    if args.parallel != 1:
        print(
            "--parallel is ignored: validation uses one isolated venv per package", file=sys.stderr
        )
    return subprocess.call(command)  # noqa: S603 - command is built from fixed local paths/args


if __name__ == "__main__":
    raise SystemExit(main())
