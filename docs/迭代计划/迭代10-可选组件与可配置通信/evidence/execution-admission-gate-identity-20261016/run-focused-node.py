from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

package = Path(r"D:\bt_api_py\bt_api\bt_api_execution")
base = Path(r"D:\bt_api_py\bt_api\bt_api_base")
artifact = Path(sys.argv[1])
node = "tests/test_group_admission_identity.py::test_account_scoped_adapter_requires_exact_gate_identity"
tracked_inputs = {
    "admission": package / "src" / "bt_api_execution" / "admission.py",
    "package_api": package / "src" / "bt_api_execution" / "__init__.py",
    "test": package / "tests" / "test_group_admission_identity.py",
    "base_wire_protocol": base / "src" / "bt_api_base" / "service_protocol.py",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True
    )
    return completed.stdout.decode("utf-8", errors="replace").strip()


def snapshot() -> dict[str, object]:
    return {
        "execution_head": git(package, "rev-parse", "HEAD"),
        "execution_status": git(package, "status", "--short"),
        "base_head": git(base, "rev-parse", "HEAD"),
        "base_status": git(base, "status", "--short"),
        "files": {name: {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}
                  for name, path in tracked_inputs.items()},
    }


before = snapshot()
env = os.environ.copy()
env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
env["PYTHONDONTWRITEBYTECODE"] = "1"
env["PYTHONNOUSERSITE"] = "1"
env["PYTHONPATH"] = os.pathsep.join((str(package / "src"), str(base / "src")))
command = [
    sys.executable,
    "-m",
    "pytest",
    "--noconftest",
    "--import-mode=importlib",
    "-p",
    "no:cacheprovider",
    "-q",
    "--junitxml",
    str(artifact / "junit.xml"),
    node,
]
(artifact / "command.json").write_text(
    json.dumps(
        {
            "cwd": str(package),
            "command": command,
            "node": node,
            "python_executable": sys.executable,
            "python_version": sys.version,
            "environment_overrides": {
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": env["PYTHONPATH"],
            },
            "package_head": before["execution_head"],
            "base_head": before["base_head"],
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
result = subprocess.run(
    command,
    cwd=package,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    check=False,
)
(artifact / "pytest-output.raw.log").write_bytes(result.stdout)
(artifact / "pytest-exit.txt").write_text(f"{result.returncode}\n", encoding="ascii")
after = snapshot()
report = {
    "pytest_exit_code": result.returncode,
    "before": before,
    "after": after,
    "source_pins_unchanged": before["files"] == after["files"],
    "execution_head_unchanged": before["execution_head"] == after["execution_head"],
    "base_head_unchanged": before["base_head"] == after["base_head"],
    "raw_log": {
        "path": str(artifact / "pytest-output.raw.log"),
        "bytes": (artifact / "pytest-output.raw.log").stat().st_size,
        "sha256": sha256(artifact / "pytest-output.raw.log"),
    },
    "junit": {
        "path": str(artifact / "junit.xml"),
        "bytes": (artifact / "junit.xml").stat().st_size,
        "sha256": sha256(artifact / "junit.xml"),
    } if (artifact / "junit.xml").exists() else None,
}
(artifact / "source-pins.json").write_text(
    json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
sys.stdout.buffer.write(result.stdout)
sys.stdout.buffer.write(
    ("\nartifact=" + str(artifact) + "\npytest_exit=" + str(result.returncode) + "\n").encode()
)
sys.exit(result.returncode)