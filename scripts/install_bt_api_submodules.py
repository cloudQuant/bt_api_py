#!/usr/bin/env python3
"""Install bt_api_py together with bt_api_* submodule packages.

The default strategy is source-first:
1. Skip packages that are already installed, unless --upgrade is used.
2. Initialize missing submodule source trees.
3. Install from local submodule source.
4. Fall back to PyPI if the source tree is missing or source install fails.

Use --strategy none to only report install status without installing anything.
"""

from __future__ import annotations

import argparse
import configparser
import importlib.metadata as metadata
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GITMODULES = ROOT / ".gitmodules"


@dataclass(frozen=True)
class PackageSpec:
    name: str
    dist_name: str
    path: Path
    url: str


@dataclass
class InstallResult:
    name: str
    status: str
    detail: str = ""


def normalize_dist_name(name: str) -> str:
    return name.replace("_", "-").replace(".", "-").lower()


def installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        wanted = normalize_dist_name(name)
        for dist in metadata.distributions():
            dist_name = dist.metadata.get("Name")
            if dist_name and normalize_dist_name(dist_name) == wanted:
                return dist.version
        return None


def run_command(
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd))
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, cwd=cwd, text=True)


def load_submodule_packages() -> list[PackageSpec]:
    if not GITMODULES.exists():
        raise SystemExit(f"Missing {GITMODULES}; cannot discover bt_api submodules")

    parser = configparser.ConfigParser()
    parser.read(GITMODULES)

    specs: list[PackageSpec] = []
    for section in parser.sections():
        path_value = parser.get(section, "path", fallback="")
        url = parser.get(section, "url", fallback="")
        if not path_value.startswith("bt_api/bt_api_"):
            continue
        path = ROOT / path_value
        specs.append(PackageSpec(name=path.name, dist_name=read_project_name(path), path=path, url=url))

    def sort_key(spec: PackageSpec) -> tuple[int, str]:
        return (0 if spec.name == "bt_api_base" else 1, spec.name)

    return sorted(specs, key=sort_key)


def read_project_name(path: Path) -> str:
    pyproject = path / "pyproject.toml"
    if not pyproject.exists():
        return path.name
    try:
        with pyproject.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError):
        return path.name
    return data.get("project", {}).get("name", path.name)


def filter_packages(specs: list[PackageSpec], selected: list[str] | None) -> list[PackageSpec]:
    if not selected:
        return specs

    selected_names = {item.removeprefix("bt_api/").strip() for item in selected}
    selected_names = {item if item.startswith("bt_api_") else f"bt_api_{item}" for item in selected_names}
    found = {spec.name for spec in specs}
    missing = sorted(selected_names - found)
    if missing:
        raise SystemExit(f"Unknown package(s): {', '.join(missing)}")
    return [spec for spec in specs if spec.name in selected_names]


def source_tree_ready(spec: PackageSpec) -> bool:
    return (spec.path / "pyproject.toml").exists() or (spec.path / "setup.py").exists()


def strategy_methods(strategy: str) -> list[str]:
    if strategy == "source-first":
        return ["source", "pypi"]
    if strategy == "pypi-first":
        return ["pypi", "source"]
    if strategy == "source-only":
        return ["source"]
    if strategy == "pypi-only":
        return ["pypi"]
    if strategy == "none":
        return []
    raise ValueError(f"Unsupported strategy: {strategy}")


def ensure_submodules(
    specs: list[PackageSpec],
    *,
    jobs: int,
    skip_update: bool,
    dry_run: bool,
) -> None:
    missing_sources = [spec for spec in specs if not source_tree_ready(spec)]
    if not missing_sources:
        return

    names = ", ".join(spec.name for spec in missing_sources)
    print(f"Missing source trees: {names}")
    if skip_update:
        print("Submodule update skipped by --skip-submodule-update")
        return

    run_command(["git", "submodule", "sync", "--recursive"], dry_run=dry_run)
    paths = [str(spec.path.relative_to(ROOT)) for spec in missing_sources]
    run_command(
        ["git", "submodule", "update", "--init", "--recursive", "--jobs", str(jobs), *paths],
        dry_run=dry_run,
    )


def pip_install_source(
    spec: PackageSpec,
    *,
    python: str,
    editable: bool,
    upgrade: bool,
    dry_run: bool,
) -> bool:
    if not source_tree_ready(spec):
        print(f"[{spec.name}] source unavailable at {spec.path}")
        return False

    cmd = [python, "-m", "pip", "install"]
    if upgrade:
        cmd.append("-U")
    if editable:
        cmd.extend(["-e", str(spec.path)])
    else:
        cmd.append(str(spec.path))
    return run_command(cmd, dry_run=dry_run).returncode == 0


def pip_install_pypi(
    spec: PackageSpec,
    *,
    python: str,
    upgrade: bool,
    dry_run: bool,
) -> bool:
    cmd = [python, "-m", "pip", "install"]
    if upgrade:
        cmd.append("-U")
    cmd.append(spec.dist_name)
    return run_command(cmd, dry_run=dry_run).returncode == 0


def install_one(spec: PackageSpec, args: argparse.Namespace) -> InstallResult:
    current_version = installed_version(spec.dist_name)
    if current_version and not args.upgrade:
        return InstallResult(spec.name, "installed", current_version)

    methods = strategy_methods(args.strategy)
    if not methods:
        detail = current_version or "not installed"
        return InstallResult(spec.name, "checked", detail)

    failures: list[str] = []
    for method in methods:
        if method == "source":
            ok = pip_install_source(
                spec,
                python=args.python,
                editable=args.editable,
                upgrade=args.upgrade,
                dry_run=args.dry_run,
            )
        else:
            ok = pip_install_pypi(
                spec,
                python=args.python,
                upgrade=args.upgrade,
                dry_run=args.dry_run,
            )
        if ok:
            return InstallResult(spec.name, method)
        failures.append(method)

    return InstallResult(spec.name, "failed", "failed methods: " + ", ".join(failures))


def install_root(args: argparse.Namespace) -> bool:
    cmd = [args.python, "-m", "pip", "install"]
    if args.upgrade:
        cmd.append("-U")
    if args.editable_root:
        cmd.extend(["-e", str(ROOT)])
    else:
        cmd.append(str(ROOT))
    return run_command(cmd, dry_run=args.dry_run).returncode == 0


def print_summary(results: list[InstallResult]) -> None:
    print("")
    print("Summary")
    print("=======")
    for result in results:
        suffix = f" ({result.detail})" if result.detail else ""
        print(f"{result.name}: {result.status}{suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install bt_api_py and bt_api_* submodule packages."
    )
    parser.add_argument(
        "packages",
        nargs="*",
        help="Optional package subset, e.g. bt_api_base bt_api_binance or base binance.",
    )
    parser.add_argument(
        "--strategy",
        choices=["source-first", "pypi-first", "source-only", "pypi-only", "none"],
        default="source-first",
        help="Install strategy for missing packages.",
    )
    parser.add_argument(
        "--with-root",
        action="store_true",
        help="Also install this bt_api_py repository.",
    )
    parser.add_argument(
        "--editable",
        action="store_true",
        help="Install submodule packages in editable mode when using source installs.",
    )
    parser.add_argument(
        "--editable-root",
        action="store_true",
        help="Install bt_api_py itself in editable mode when --with-root is used.",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Reinstall or upgrade packages even if they are already installed.",
    )
    parser.add_argument(
        "--skip-submodule-update",
        action="store_true",
        help="Do not run git submodule update for missing source trees.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=8,
        help="Parallel jobs for git submodule update.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any selected package cannot be installed.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without running them.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used for pip commands.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    all_specs = load_submodule_packages()
    specs = filter_packages(all_specs, args.packages)
    if args.with_root and not any(spec.name == "bt_api_base" for spec in specs):
        base = next((spec for spec in all_specs if spec.name == "bt_api_base"), None)
        if base is not None:
            specs = [base, *specs]
    methods = strategy_methods(args.strategy)

    if "source" in methods:
        ensure_submodules(
            specs,
            jobs=args.jobs,
            skip_update=args.skip_submodule_update,
            dry_run=args.dry_run,
        )

    results: list[InstallResult] = []

    # Install bt_api_base before bt_api_py because bt_api_py depends on it.
    base_specs = [spec for spec in specs if spec.name == "bt_api_base"]
    other_specs = [spec for spec in specs if spec.name != "bt_api_base"]
    for spec in base_specs:
        results.append(install_one(spec, args))

    if args.with_root:
        ok = install_root(args)
        results.append(InstallResult("bt_api_py", "source" if ok else "failed", str(ROOT)))

    for spec in other_specs:
        results.append(install_one(spec, args))

    print_summary(results)
    failed = [result for result in results if result.status == "failed"]
    if failed and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
