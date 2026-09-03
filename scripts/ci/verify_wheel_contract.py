"""Verify that built artifacts contain the package resources used at runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PACKAGE_RESOURCE = "bt_api_py/configs/exchange-bundles.toml"
PACKAGE_GLOB = "bt_api_py-*.whl"
SDIST_GLOB = "bt_api_py-*.tar.gz"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class WheelContractError(RuntimeError):
    """Raised when a build artifact cannot satisfy the installed-package contract."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _single_artifact(dist_dir: Path, pattern: str) -> Path:
    artifacts = sorted(dist_dir.glob(pattern))
    if len(artifacts) != 1:
        raise WheelContractError(
            f"expected exactly one {pattern!r} artifact in {dist_dir}, found {len(artifacts)}"
        )
    return artifacts[0]


def _read_wheel_resource(wheel: Path) -> bytes:
    with zipfile.ZipFile(wheel) as archive:
        try:
            return archive.read(PACKAGE_RESOURCE)
        except KeyError as exc:
            raise WheelContractError(f"wheel is missing {PACKAGE_RESOURCE}") from exc


def _read_sdist_resource(sdist: Path) -> bytes:
    with tarfile.open(sdist, "r:gz") as archive:
        members = [
            member
            for member in archive.getmembers()
            if member.isfile() and member.name.endswith(PACKAGE_RESOURCE)
        ]
        if len(members) != 1:
            raise WheelContractError(
                f"sdist must contain exactly one {PACKAGE_RESOURCE}, found {len(members)}"
            )
        handle = archive.extractfile(members[0])
        if handle is None:
            raise WheelContractError(f"could not extract {PACKAGE_RESOURCE} from sdist")
        return handle.read()


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - arguments are assembled from local artifacts only.
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )


def _head_sha() -> str:
    result = _run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, env=dict(os.environ))
    if result.returncode != 0:
        raise WheelContractError(
            f"could not resolve repository HEAD: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _isolated_install_probe(wheel: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="bt-api-py-wheel-contract-") as temp_dir:
        temp_root = Path(temp_dir)
        venv_dir = temp_root / "venv"
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(venv_dir)
        python = _venv_python(venv_dir)
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env["PYTHONNOUSERSITE"] = "1"

        install = _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--force-reinstall",
                "--no-deps",
                str(wheel),
            ],
            cwd=temp_root,
            env=env,
        )
        if install.returncode != 0:
            raise WheelContractError(
                "isolated wheel installation failed: "
                f"{install.stderr.strip() or install.stdout.strip()}"
            )

        probe = _run(
            [
                str(python),
                "-c",
                (
                    "import importlib.resources as resources, json, pathlib; "
                    "import bt_api_py; "
                    "from bt_api_py._plugin_catalog import PluginCatalog; "
                    "resource = resources.files('bt_api_py.configs').joinpath("
                    "'exchange-bundles.toml'); "
                    "payload = {'package_file': str(pathlib.Path(bt_api_py.__file__).resolve()), "
                    "'resource': str(resource), 'resource_is_file': resource.is_file(), "
                    "'bundles': PluginCatalog().list_bundles()}; "
                    "assert payload['resource_is_file']; "
                    "print(json.dumps(payload, sort_keys=True))"
                ),
            ],
            cwd=temp_root,
            env=env,
        )
        if probe.returncode != 0:
            raise WheelContractError(
                "installed package resource probe failed: "
                f"{probe.stderr.strip() or probe.stdout.strip()}"
            )
        try:
            probe_payload = json.loads(probe.stdout)
        except json.JSONDecodeError as exc:
            raise WheelContractError(
                f"resource probe did not return JSON: {probe.stdout!r}"
            ) from exc

        package_file = str(probe_payload["package_file"]).replace("\\", "/")
        if "site-packages/bt_api_py" not in package_file:
            raise WheelContractError(
                "installed package probe resolved outside the virtualenv site-packages: "
                f"{package_file}"
            )

        doctor = _run(
            [
                str(python),
                "-m",
                "bt_api_py.doctor",
                "--bundle",
                "core-reference",
                "--format",
                "json",
            ],
            cwd=temp_root,
            env=env,
        )
        if doctor.returncode != 0:
            raise WheelContractError(
                f"installed doctor failed: {doctor.stderr.strip() or doctor.stdout.strip()}"
            )
        try:
            doctor_payload = json.loads(doctor.stdout)
        except json.JSONDecodeError as exc:
            raise WheelContractError(f"doctor did not return JSON: {doctor.stdout!r}") from exc

        return (
            package_file,
            probe_payload,
            {
                "exit_code": doctor.returncode,
                "payload": doctor_payload,
                "stdout_sha256": _sha256(doctor.stdout.encode()),
                "stderr_sha256": _sha256(doctor.stderr.encode()),
            },
        )


def verify(dist_dir: Path) -> dict[str, Any]:
    """Build an evidence receipt for the source, wheel, and sdist resource contract."""

    source_resource = REPOSITORY_ROOT / PACKAGE_RESOURCE
    if not source_resource.is_file():
        raise WheelContractError(f"source tree is missing {source_resource}")

    wheel = _single_artifact(dist_dir, PACKAGE_GLOB)
    sdist = _single_artifact(dist_dir, SDIST_GLOB)
    resource_hashes = {
        "source": _sha256(source_resource.read_bytes()),
        "wheel": _sha256(_read_wheel_resource(wheel)),
        "sdist": _sha256(_read_sdist_resource(sdist)),
    }
    if len(set(resource_hashes.values())) != 1:
        raise WheelContractError(
            f"source, wheel, and sdist exchange-bundles.toml hashes do not match: {resource_hashes}"
        )

    package_file, probe, doctor = _isolated_install_probe(wheel)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "head_sha": _head_sha(),
        "result": "passed",
        "wheel": {
            "path": wheel.name,
            "sha256": _sha256(wheel.read_bytes()),
        },
        "sdist": {
            "path": sdist.name,
            "sha256": _sha256(sdist.read_bytes()),
        },
        "resource_sha256": resource_hashes,
        "package_file": package_file,
        "probe": probe,
        "doctor": doctor,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        receipt = verify(args.dist_dir.resolve())
    except (OSError, WheelContractError, zipfile.BadZipFile, tarfile.TarError) as exc:
        receipt = {
            "schema_version": 1,
            "result": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(receipt["error"], file=sys.stderr)
        return 1

    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps({"result": receipt["result"], "wheel": receipt["wheel"]["path"]}, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
