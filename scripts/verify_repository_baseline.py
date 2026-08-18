#!/usr/bin/env python3
"""Generate a reproducible repository + plugin baseline inventory (read-only).

Part of the unified-api-zmq-gateway acceptance iteration plan (Task 0.1).

The manifest explains the relationship between:

* the parent repository commit,
* every submodule declared in ``.gitmodules`` (the single source of truth),
* each submodule's pinned gitlink vs. actual checkout and internal dirtiness,
* installed ``bt_api.plugins`` entry points and their certification status.

The script is strictly read-only: it runs git subcommands and reads
``importlib.metadata`` entry-point metadata. It never performs
``git submodule update/reset`` and never auto-resolves a pin divergence —
divergences (e.g. ``bt_api/bt_api_ctp``) are reported for a maintainer
decision.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

GIT = shutil.which("git") or "git"

SCHEMA_VERSION = 1
PLUGIN_GROUP = "bt_api.plugins"
# Certified venue cards land here as part of Task 6.2. Their presence upgrades
# a plugin from ``installed`` to ``certified``.
CERTIFIED_CARDS_DIR = "docs/acceptance/exchange-cards"


def _run_git(repo_root: Path, args: list[str]) -> str:
    """Run a read-only git command and return trimmed stdout."""
    proc = subprocess.run(  # noqa: S603 — fixed git subcommands, trusted .gitmodules paths
        [GIT, *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _gitmodules_paths(repo_root: Path) -> list[str]:
    """Return every submodule path declared in ``.gitmodules``, in order."""
    gitmodules = repo_root / ".gitmodules"
    if not gitmodules.exists():
        return []
    paths: list[str] = []
    for line in gitmodules.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("path = "):
            paths.append(stripped.split("=", 1)[1].strip())
    return paths


def _collect_submodule(repo_root: Path, path: str) -> dict[str, object]:
    """Collect one submodule's pinned gitlink, checkout, dirtiness and mismatch."""
    submodule_dir = repo_root / path
    pinned = _run_git(repo_root, ["ls-tree", "HEAD", path]).split()[2]
    if submodule_dir.is_dir() and (submodule_dir / ".git").exists():
        checked_out = _run_git(submodule_dir, ["rev-parse", "HEAD"])
        dirty = bool(_run_git(submodule_dir, ["status", "--porcelain", "--untracked-files=no"]))
    else:
        # Uninitialized or missing checkout: report honestly rather than guessing.
        checked_out = ""
        dirty = False
    return {
        "path": path,
        "pinned_commit": pinned,
        "checked_out_commit": checked_out,
        "dirty": dirty,
        "pin_mismatch": bool(checked_out) and pinned != checked_out,
    }


def _entry_points(group: str) -> list[metadata.EntryPoint]:
    """Return entry points for ``group`` across all distributions (no loading)."""
    return list(metadata.entry_points().select(group=group))


def _collect_plugins(repo_root: Path) -> list[dict[str, object]]:
    """Collect installed ``bt_api.plugins`` entry points and certification state."""
    cards_dir = repo_root / CERTIFIED_CARDS_DIR
    plugins: list[dict[str, object]] = []
    for ep in sorted(_entry_points(PLUGIN_GROUP), key=lambda e: e.name):
        certified = (cards_dir / f"{ep.name}.md").exists()
        plugins.append(
            {
                "name": ep.name,
                "package": ep.dist.name if ep.dist else None,
                "entry_point": f"{ep.module}:{ep.attr}",
                "installed": True,
                "certified": certified,
                "status": "certified" if certified else "installed",
            }
        )
    return plugins


def _package_by_basename(plugins: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Index plugins by their distribution package name (submodule basename)."""
    index: dict[str, dict[str, object]] = {}
    for plugin in plugins:
        package = plugin.get("package")
        if package:
            index[package] = plugin
    return index


def collect_baseline(repo_root: Path) -> dict[str, object]:
    """Collect the full baseline manifest as a dict (read-only)."""
    submodules = [_collect_submodule(repo_root, path) for path in _gitmodules_paths(repo_root)]
    plugins = _collect_plugins(repo_root)
    package_index = _package_by_basename(plugins)

    # Attach plugin status to the matching submodule so the 60-vs-30 gap is
    # explainable, and mark submodules with no installed plugin.
    for submodule in submodules:
        basename = str(submodule["path"]).rsplit("/", 1)[-1]
        plugin = package_index.get(basename)
        submodule["plugin_status"] = plugin["status"] if plugin else None

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parent": {
            "commit": _run_git(repo_root, ["rev-parse", "HEAD"]),
            "branch": _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"]),
        },
        "submodules": submodules,
        "plugins": plugins,
        "summary": {
            "submodule_count": len(submodules),
            "plugin_count": len(plugins),
            "dirty_submodules": [s["path"] for s in submodules if s["dirty"]],
            "pin_mismatch_submodules": [s["path"] for s in submodules if s["pin_mismatch"]],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a read-only repository + plugin baseline manifest."
    )
    parser.add_argument("--json", help="write the manifest JSON to this path")
    parser.add_argument("--repo-root", help="repository root (default: script's parent)")
    args = parser.parse_args(argv)

    repo_root = (
        Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parent.parent
    )
    manifest = collect_baseline(repo_root)
    payload = json.dumps(manifest, indent=2, sort_keys=True)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n")
        print(f"wrote baseline manifest to {out}")
    else:
        print(payload)

    summary = manifest["summary"]
    print(
        f"submodules={summary['submodule_count']} "
        f"plugins={summary['plugin_count']} "
        f"dirty={summary['dirty_submodules']} "
        f"pin_mismatch={summary['pin_mismatch_submodules']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
