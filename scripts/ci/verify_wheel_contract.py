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
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__:
    from .base_source_pin import BaseSourcePinError, BaseWheelReceipt, build_pinned_base_wheel
    from .offline_pip import (
        WheelhousePathError,
        pip_source_args,
        pip_source_environment,
        resolve_wheelhouse_path,
    )
else:
    from base_source_pin import BaseSourcePinError, BaseWheelReceipt, build_pinned_base_wheel
    from offline_pip import (
        WheelhousePathError,
        pip_source_args,
        pip_source_environment,
        resolve_wheelhouse_path,
    )

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


def _require_venv_package_path(package_file: str, venv_dir: Path, package_name: str) -> str:
    package_path = Path(package_file).resolve()
    venv_path = venv_dir.resolve()
    if package_path.is_relative_to(venv_path):
        relative_parts = tuple(
            part.casefold() for part in package_path.relative_to(venv_path).parts
        )
        expected_package = package_name.casefold()
        has_expected_package_path = any(
            part == "site-packages"
            and index + 1 < len(relative_parts)
            and relative_parts[index + 1] == expected_package
            for index, part in enumerate(relative_parts)
        )
    else:
        has_expected_package_path = False
    if not has_expected_package_path:
        raise WheelContractError(
            f"installed {package_name} package probe resolved outside the virtualenv site-packages: "
            f"{package_path}"
        )
    return package_path.as_posix()


def _isolated_subprocess_env(wheelhouse: Path | None = None) -> dict[str, str]:
    """Return an environment that cannot join a parent pytest-cov session.

    The installed-wheel probe deliberately starts a second interpreter outside
    this source checkout.  pytest-cov exports ``COV_CORE_*`` variables to
    subprocesses; allowing them into that interpreter creates statement-only
    coverage shards because the wheel has no project coverage configuration.
    Those shards then make the parent branch-coverage report impossible to
    combine.  The probe is an installation test, not a coverage child.
    """

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    for key in tuple(env):
        if key.startswith("COV_CORE_") or key in {"COVERAGE_FILE", "COVERAGE_PROCESS_START"}:
            env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    return pip_source_environment(env, resolve_wheelhouse_path(wheelhouse))


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


def _isolated_install_probe(
    wheel: Path, wheelhouse: Path | None = None, *, base_wheel_dir: Path
) -> tuple[str, dict[str, Any], dict[str, Any], BaseWheelReceipt]:
    with tempfile.TemporaryDirectory(prefix="bt-api-py-wheel-contract-") as temp_dir:
        temp_root = Path(temp_dir)
        venv_dir = temp_root / "venv"
        env = _isolated_subprocess_env(wheelhouse)
        create = _run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            cwd=temp_root,
            env=env,
        )
        if create.returncode != 0:
            raise WheelContractError(
                "isolated virtualenv creation failed: "
                f"{create.stderr.strip() or create.stdout.strip()}"
            )
        python = _venv_python(venv_dir)

        base_wheel_dir = base_wheel_dir.resolve()
        try:
            base_receipt = build_pinned_base_wheel(
                REPOSITORY_ROOT,
                base_wheel_dir,
                build_wheelhouse=wheelhouse,
            )
        except (BaseSourcePinError, WheelhousePathError, OSError) as exc:
            raise WheelContractError(
                f"could not build the pinned bt_api_base wheel: {exc}"
            ) from exc

        base_env = _isolated_subprocess_env(base_wheel_dir)
        install_base = _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                *pip_source_args(base_wheel_dir),
                "--disable-pip-version-check",
                "--no-deps",
                "--force-reinstall",
                str(base_receipt.wheel_path),
            ],
            cwd=temp_root,
            env=base_env,
        )
        if install_base.returncode != 0:
            raise WheelContractError(
                "isolated pinned base wheel installation failed: "
                f"{install_base.stderr.strip() or install_base.stdout.strip()}"
            )

        install = _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                *pip_source_args(wheelhouse),
                "--disable-pip-version-check",
                # This is an installed-package probe.  Resolve the wheel's
                # declared runtime dependencies instead of relying on whatever
                # happens to be present in the runner's system site-packages.
                # The pinned base wheel is already installed into this fresh
                # venv and satisfies the candidate wheel's base requirement.
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

        check = _run([str(python), "-m", "pip", "check"], cwd=temp_root, env=env)
        if check.returncode != 0:
            raise WheelContractError(
                "isolated installed dependency check failed: "
                f"{check.stderr.strip() or check.stdout.strip()}"
            )

        probe = _run(
            [
                str(python),
                "-c",
                (
                    "import importlib.resources as resources, json, pathlib; "
                    "from importlib import metadata; "
                    "import bt_api_base, bt_api_py; "
                    "from bt_api_py._plugin_catalog import PluginCatalog; "
                    "resource = resources.files('bt_api_py.configs').joinpath("
                    "'exchange-bundles.toml'); "
                    "base_distribution = metadata.distribution('bt_api_base'); "
                    "base_direct_url = json.loads(base_distribution.read_text('direct_url.json') "
                    "or '{}'); "
                    "payload = {'package_file': str(pathlib.Path(bt_api_py.__file__).resolve()), "
                    "'base_package_file': str(pathlib.Path(bt_api_base.__file__).resolve()), "
                    "'base_version': base_distribution.version, "
                    "'base_direct_url': base_direct_url, "
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

        package_file = _require_venv_package_path(
            str(probe_payload["package_file"]), venv_dir, "bt_api_py"
        )
        base_package_file = _require_venv_package_path(
            str(probe_payload["base_package_file"]), venv_dir, "bt_api_base"
        )
        probe_payload["base_package_file"] = base_package_file
        if probe_payload["base_version"] != base_receipt.package_version:
            raise WheelContractError(
                "installed base package version does not match the parent-pinned source wheel: "
                f"{probe_payload['base_version']} != {base_receipt.package_version}"
            )
        expected_wheel_url = base_receipt.wheel_path.resolve().as_uri()
        recorded_wheel_url = probe_payload["base_direct_url"].get("url")
        if recorded_wheel_url != expected_wheel_url:
            raise WheelContractError(
                "installed base package PEP 610 URL does not identify the exact pinned local wheel: "
                f"{recorded_wheel_url!r} != {expected_wheel_url!r}"
            )
        archive_info = probe_payload["base_direct_url"].get("archive_info") or {}
        recorded_hash = archive_info.get("hash")
        if recorded_hash is None:
            recorded_hash = (archive_info.get("hashes") or {}).get("sha256")
            if recorded_hash:
                recorded_hash = f"sha256={recorded_hash}"
        if recorded_hash != f"sha256={base_receipt.wheel_sha256}":
            raise WheelContractError(
                "installed base package PEP 610 wheel hash does not match the pinned source wheel"
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
            base_receipt,
        )


def verify(dist_dir: Path, wheelhouse: Path | None = None) -> dict[str, Any]:
    """Build an evidence receipt for the source, wheel, and sdist resource contract."""
    dist_dir = dist_dir.resolve()
    wheelhouse = resolve_wheelhouse_path(wheelhouse)

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

    package_file, probe, doctor, base_receipt = _isolated_install_probe(
        wheel,
        wheelhouse,
        base_wheel_dir=dist_dir / "bt_api_base_source",
    )
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
        "base_package_file": probe["base_package_file"],
        "base_source": {
            "parent_commit": base_receipt.parent_commit,
            "source_commit": base_receipt.source_commit,
            "source_tree": base_receipt.source_tree,
            "source_origin": base_receipt.source_origin,
            "package_name": base_receipt.package_name,
            "package_version": base_receipt.package_version,
            "minimum_version": base_receipt.minimum_version,
            "wheel_filename": base_receipt.wheel_filename,
            "wheel_path": base_receipt.wheel_path.relative_to(dist_dir).as_posix(),
            "wheel_path_url": base_receipt.wheel_path.resolve().as_uri(),
            "wheel_sha256": base_receipt.wheel_sha256,
        },
        "probe": probe,
        "doctor": doctor,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--wheelhouse",
        type=Path,
        help="Use only wheels from this existing absolute local directory.",
    )
    args = parser.parse_args(argv)

    try:
        receipt = verify(args.dist_dir.resolve(), wheelhouse=args.wheelhouse)
    except (
        OSError,
        WheelContractError,
        WheelhousePathError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ) as exc:
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
