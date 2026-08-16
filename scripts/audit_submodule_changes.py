#!/usr/bin/env python3
"""盘点所有子模块的未提交改动,按类别输出报告。

用法: python scripts/audit_submodule_changes.py [--json /tmp/report.json]
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BT_API = ROOT / "bt_api"

BUILD_ARTIFACT_GLOBS = ("__pycache__", "*.pyc", "*.egg-info", "build/", "dist/")


def is_build_artifact(path: str) -> bool:
    return any(part in path for part in ("__pycache__", ".egg-info", "/build/", "/dist/")) or path.endswith(".pyc")


def audit(repo: Path) -> dict:
    out = subprocess.run(
        ["git", "status", "--porcelain", "-uall"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    source, artifacts = [], []
    for line in out.splitlines():
        path = line[3:]
        (artifacts if is_build_artifact(path) else source).append(line)
    return {"repo": repo.name, "source_changes": source, "build_artifacts": artifacts}


def main() -> None:
    report = [audit(p) for p in sorted(BT_API.iterdir()) if (p / ".git").exists()]
    for r in report:
        print(f"{r['repo']}: source={len(r['source_changes'])} artifacts={len(r['build_artifacts'])}")
    if "--json" in __import__("sys").argv:
        out = __import__("sys").argv[__import__("sys").argv.index("--json") + 1]
        Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
