#!/usr/bin/env python3
"""Build and install the exact parent-pinned CTP source wheel for CI only."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import asdict, dataclass
from email.parser import BytesParser
from importlib import metadata
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

if __package__:
    from .base_source_pin import (
        BaseSourcePinError,
        _run_git,
        _safe_env_value,
        _sha256,
        _stable_version,
        _subprocess_environment,
        verify_base_source_pin,
    )
else:
    from base_source_pin import (
        BaseSourcePinError,
        _run_git,
        _safe_env_value,
        _sha256,
        _stable_version,
        _subprocess_environment,
        verify_base_source_pin,
    )

CTP_PATH = "bt_api/bt_api_ctp"
CTP_ORIGIN = "https://github.com/cloudQuant/bt_api_ctp.git"
CTP_MINIMUM_VERSION = (2, 0, 3)
BASE_MINIMUM_VERSION = (0, 15, 5)
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
CTP_REQUIREMENT_RE = re.compile(
    r"^bt_api_ctp\s*>=\s*(\d+\.\d+\.\d+)\s*,\s*<\s*(\d+\.\d+)$",
    re.IGNORECASE,
)
BASE_REQUIREMENT_RE = re.compile(
    r"^bt_api_base\s*>=\s*(\d+\.\d+\.\d+)\s*,\s*<\s*(\d+\.\d+)$",
    re.IGNORECASE,
)


class CtpSourcePinError(RuntimeError):
    """Raised when the CTP wheel cannot be bound to the parent source pin."""


@dataclass(frozen=True)
class CtpSourcePin:
    parent_commit: str
    source_commit: str
    source_tree: str
    source_origin: str
    package_name: str
    package_version: str
    minimum_version: str
    minimum_base_version: str
    source_path: Path


@dataclass(frozen=True)
class CtpWheelReceipt:
    parent_commit: str
    source_commit: str
    source_tree: str
    source_origin: str
    package_name: str
    package_version: str
    minimum_version: str
    minimum_base_version: str
    wheel_filename: str
    wheel_sha256: str
    wheel_path: Path


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise CtpSourcePinError("git executable is unavailable in the allowlisted PATH")
    return executable


def _source_metadata(source_path: Path, source_commit: str) -> tuple[str, str, str]:
    result = subprocess.run(  # noqa: S603
        [
            _git_executable(),
            "-C",
            str(source_path),
            "show",
            f"{source_commit}:pyproject.toml",
        ],
        capture_output=True,
        check=False,
        env=_subprocess_environment(),
    )
    if result.returncode:
        raise CtpSourcePinError("pinned bt_api_ctp source has no readable pyproject.toml")
    try:
        data = tomllib.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CtpSourcePinError(f"pinned bt_api_ctp pyproject.toml is invalid: {exc}") from exc

    project = data.get("project")
    if not isinstance(project, dict):
        raise CtpSourcePinError("pinned bt_api_ctp pyproject.toml has no [project] table")
    package_name = str(project.get("name") or "")
    package_version = str(project.get("version") or "")
    if package_name != "bt_api_ctp":
        raise CtpSourcePinError(
            f"pinned source package name must be bt_api_ctp, got {package_name!r}"
        )
    _stable_version(package_version, label="pinned bt_api_ctp version")

    dependencies = project.get("dependencies", [])
    matches = [
        match
        for dependency in dependencies
        if (match := BASE_REQUIREMENT_RE.fullmatch(str(dependency).strip())) is not None
    ]
    if len(matches) != 1:
        raise CtpSourcePinError(
            "pinned bt_api_ctp must declare exactly one simple bt_api_base>=X.Y.Z,<X.Y "
            "runtime requirement"
        )
    base_minimum = matches[0].group(1)
    if _stable_version(base_minimum, label="pinned CTP base minimum") < BASE_MINIMUM_VERSION:
        raise CtpSourcePinError(
            f"pinned bt_api_ctp base floor must remain >=0.15.5, got {base_minimum}"
        )
    return package_name, package_version, base_minimum


def _parent_ctp_requirement(repository_root: Path) -> str:
    try:
        root_data = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CtpSourcePinError(f"cannot read parent pyproject.toml: {exc}") from exc
    optional = root_data.get("project", {}).get("optional-dependencies", {})
    core_reference = optional.get("core-reference", []) if isinstance(optional, dict) else []
    matches = [
        match
        for requirement in core_reference
        if (match := CTP_REQUIREMENT_RE.fullmatch(str(requirement).strip())) is not None
    ]
    if len(matches) != 1:
        raise CtpSourcePinError(
            "parent core-reference extra must contain exactly one simple "
            "bt_api_ctp>=X.Y.Z,<X.Y requirement"
        )
    minimum_version = matches[0].group(1)
    if _stable_version(minimum_version, label="parent CTP minimum") < CTP_MINIMUM_VERSION:
        raise CtpSourcePinError(
            f"parent CTP lower bound must remain >=2.0.3, got {minimum_version}"
        )
    return minimum_version


def verify_ctp_source_pin(repository_root: Path) -> CtpSourcePin:
    """Bind CTP metadata and wheel input to the exact parent gitlink."""
    repository_root = repository_root.resolve(strict=True)
    parent_commit = _run_git(repository_root, "rev-parse", "HEAD")
    tree_entry = _run_git(repository_root, "ls-tree", parent_commit, "--", CTP_PATH)
    fields = tree_entry.split()
    if len(fields) != 4 or fields[0] != "160000" or fields[1] != "commit" or fields[3] != CTP_PATH:
        raise CtpSourcePinError(f"{CTP_PATH} is not an exact gitlink in parent HEAD")
    source_commit = fields[2]
    if not GIT_SHA_RE.fullmatch(source_commit):
        raise CtpSourcePinError(
            f"parent CTP gitlink is not a full 40-character SHA: {source_commit!r}"
        )

    source_path = repository_root / CTP_PATH
    if not source_path.is_dir():
        raise CtpSourcePinError(f"pinned source checkout is unavailable: {source_path}")
    configured_origin = _run_git(
        repository_root,
        "config",
        "-f",
        ".gitmodules",
        "--get",
        "submodule.bt_api/bt_api_ctp.url",
    )
    actual_origin = _run_git(source_path, "remote", "get-url", "origin")
    if configured_origin != CTP_ORIGIN or actual_origin != CTP_ORIGIN:
        raise CtpSourcePinError(
            "bt_api_ctp origin mismatch: .gitmodules URL and source origin must both match "
            f"canonical upstream {CTP_ORIGIN}"
        )
    checked_out_commit = _run_git(source_path, "rev-parse", "HEAD")
    if checked_out_commit != source_commit:
        raise CtpSourcePinError(
            f"bt_api_ctp checkout {checked_out_commit} does not match parent gitlink {source_commit}"
        )
    source_tree = _run_git(source_path, "rev-parse", f"{source_commit}^{{tree}}")

    package_name, package_version, minimum_base_version = _source_metadata(
        source_path, source_commit
    )
    package_version_tuple = _stable_version(package_version, label="pinned bt_api_ctp version")
    if package_version_tuple < CTP_MINIMUM_VERSION:
        raise CtpSourcePinError(f"pinned bt_api_ctp version must be >=2.0.3, got {package_version}")
    minimum_version = _parent_ctp_requirement(repository_root)
    if package_version_tuple < _stable_version(minimum_version, label="parent CTP minimum"):
        raise CtpSourcePinError(
            f"pinned bt_api_ctp {package_version} does not satisfy parent >= {minimum_version}"
        )

    bundle_path = repository_root / "bt_api_py" / "configs" / "exchange-bundles.toml"
    try:
        bundle_data = tomllib.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CtpSourcePinError(f"cannot read core-reference bundle metadata: {exc}") from exc
    venues = bundle_data.get("bundles", {}).get("core-reference", {}).get("venues", [])
    ctp_venues = [venue for venue in venues if venue.get("package") == "bt_api_ctp"]
    if len(ctp_venues) != 1:
        raise CtpSourcePinError("core-reference bundle must contain exactly one bt_api_ctp entry")
    bundle_minimum = str(ctp_venues[0].get("min_version") or "")
    if not STABLE_VERSION_RE.fullmatch(bundle_minimum):
        raise CtpSourcePinError("core-reference bt_api_ctp min_version must be a stable X.Y.Z")
    if package_version_tuple < _stable_version(bundle_minimum, label="bundle CTP minimum"):
        raise CtpSourcePinError(
            f"pinned bt_api_ctp {package_version} does not satisfy bundle >= {bundle_minimum}"
        )
    if not (source_path / "setup.py").is_file():
        raise CtpSourcePinError(
            "pinned bt_api_ctp source is missing setup.py native extension build"
        )

    return CtpSourcePin(
        parent_commit=parent_commit,
        source_commit=source_commit,
        source_tree=source_tree,
        source_origin=actual_origin,
        package_name=package_name,
        package_version=package_version,
        minimum_version=minimum_version,
        minimum_base_version=minimum_base_version,
        source_path=source_path,
    )


def _extract_pinned_archive(pin: CtpSourcePin, destination: Path) -> Path:
    completed = subprocess.run(  # noqa: S603
        [
            _git_executable(),
            "-C",
            str(pin.source_path),
            "archive",
            "--format=tar",
            pin.source_commit,
        ],
        capture_output=True,
        check=False,
        env=_subprocess_environment(),
    )
    if completed.returncode:
        raise CtpSourcePinError(
            f"cannot archive pinned bt_api_ctp source (git exit code {completed.returncode})"
        )
    source_root = destination / "source"
    source_root.mkdir(parents=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in member.name
                    or ":" in member.name
                ):
                    raise CtpSourcePinError("pinned CTP source archive contains an unsafe path")
                target = source_root.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fileobj = archive.extractfile(member)
                    if fileobj is None:
                        raise CtpSourcePinError("pinned CTP archive contains an unreadable file")
                    with fileobj, target.open("wb") as output:
                        output.write(fileobj.read())
                    if member.mode & 0o111:
                        target.chmod(target.stat().st_mode | 0o111)
                else:
                    raise CtpSourcePinError(
                        "pinned CTP archive contains a non-regular entry; refusing extraction"
                    )
    except tarfile.TarError as exc:
        raise CtpSourcePinError(f"pinned CTP source archive is invalid: {exc}") from exc
    return source_root


def _read_wheel_identity(wheel_path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            metadata_paths = [
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_paths) != 1:
                raise CtpSourcePinError(
                    f"expected one wheel METADATA file, found {len(metadata_paths)}"
                )
            metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
    except (OSError, zipfile.BadZipFile) as exc:
        raise CtpSourcePinError(f"pinned CTP build did not produce a valid wheel: {exc}") from exc
    return str(metadata.get("Name") or ""), str(metadata.get("Version") or "")


def _verified_archive_hash(direct_url: dict[str, object]) -> str:
    archive_info = direct_url.get("archive_info")
    if not isinstance(archive_info, dict):
        return ""
    recorded_hash = archive_info.get("hash")
    if recorded_hash is None:
        hashes = archive_info.get("hashes")
        recorded_hash = hashes.get("sha256") if isinstance(hashes, dict) else None
        if recorded_hash:
            recorded_hash = f"sha256={recorded_hash}"
    return str(recorded_hash or "")


def _verify_local_wheel_origin(
    package_name: str,
    package_version: str,
    wheel_path: Path,
    wheel_sha256: str,
) -> None:
    try:
        distribution = metadata.distribution(package_name)
        direct_url_text = distribution.read_text("direct_url.json")
        direct_url = json.loads(direct_url_text) if direct_url_text else {}
    except (ImportError, json.JSONDecodeError, metadata.PackageNotFoundError) as exc:
        raise CtpSourcePinError(
            f"cannot verify installed {package_name} wheel origin: {exc}"
        ) from exc
    if distribution.version != package_version:
        raise CtpSourcePinError(
            f"installed {package_name} {distribution.version} does not match local wheel "
            f"{package_version}"
        )
    parsed_url = urlparse(str(direct_url.get("url") or ""))
    if parsed_url.scheme != "file":
        raise CtpSourcePinError(f"installed {package_name} has no local file PEP 610 origin")
    url_path = unquote(parsed_url.path)
    if os.name == "nt" and re.match(r"^/[A-Za-z]:/", url_path):
        url_path = url_path[1:]
    recorded_path = Path(url_path).resolve()
    if recorded_path != wheel_path.resolve():
        raise CtpSourcePinError(
            f"installed {package_name} PEP 610 URL is not the pinned local wheel"
        )
    if _verified_archive_hash(direct_url) != f"sha256={wheel_sha256}":
        raise CtpSourcePinError(
            f"installed {package_name} direct_url.json does not match local wheel SHA-256"
        )


def _verify_base_receipt(repository_root: Path) -> None:
    base_pin = verify_base_source_pin(repository_root)
    expected = {
        "BT_API_BASE_SOURCE_SHA": base_pin.source_commit,
        "BT_API_BASE_SOURCE_TREE_SHA": base_pin.source_tree,
        "BT_API_BASE_SOURCE_ORIGIN": base_pin.source_origin,
        "BT_API_BASE_SOURCE_VERSION": base_pin.package_version,
    }
    for name, value in expected.items():
        if os.environ.get(name) != value:
            raise CtpSourcePinError(f"installed base receipt {name} does not match parent gitlink")
    wheel_path_text = os.environ.get("BT_API_BASE_WHEEL_PATH", "")
    wheel_sha256 = os.environ.get("BT_API_BASE_WHEEL_SHA256", "")
    if not wheel_path_text or not wheel_sha256:
        raise CtpSourcePinError(
            "pinned base wheel path/SHA receipt is missing from the environment"
        )
    wheel_path = Path(wheel_path_text).resolve(strict=True)
    if _sha256(wheel_path) != wheel_sha256:
        raise CtpSourcePinError("pinned base wheel changed after source-pin installation")
    _verify_local_wheel_origin("bt_api_base", base_pin.package_version, wheel_path, wheel_sha256)


def _verify_installed_ctp_receipt(repository_root: Path) -> CtpWheelReceipt:
    pin = verify_ctp_source_pin(repository_root)
    expected = {
        "BT_API_CTP_SOURCE_SHA": pin.source_commit,
        "BT_API_CTP_SOURCE_TREE_SHA": pin.source_tree,
        "BT_API_CTP_SOURCE_ORIGIN": pin.source_origin,
        "BT_API_CTP_SOURCE_VERSION": pin.package_version,
    }
    for name, value in expected.items():
        if os.environ.get(name) != value:
            raise CtpSourcePinError(f"installed CTP receipt {name} does not match parent gitlink")
    wheel_path_text = os.environ.get("BT_API_CTP_WHEEL_PATH", "")
    wheel_sha256 = os.environ.get("BT_API_CTP_WHEEL_SHA256", "")
    if not wheel_path_text or not wheel_sha256:
        raise CtpSourcePinError("pinned CTP wheel path/SHA receipt is missing from the environment")
    wheel_path = Path(wheel_path_text).resolve(strict=True)
    if _sha256(wheel_path) != wheel_sha256:
        raise CtpSourcePinError("pinned CTP wheel changed after source-pin installation")
    wheel_name, wheel_version = _read_wheel_identity(wheel_path)
    if wheel_name != pin.package_name or wheel_version != pin.package_version:
        raise CtpSourcePinError("pinned CTP wheel METADATA no longer matches exact source metadata")
    _verify_local_wheel_origin(pin.package_name, pin.package_version, wheel_path, wheel_sha256)
    return CtpWheelReceipt(
        parent_commit=pin.parent_commit,
        source_commit=pin.source_commit,
        source_tree=pin.source_tree,
        source_origin=pin.source_origin,
        package_name=pin.package_name,
        package_version=pin.package_version,
        minimum_version=pin.minimum_version,
        minimum_base_version=pin.minimum_base_version,
        wheel_filename=wheel_path.name,
        wheel_sha256=wheel_sha256,
        wheel_path=wheel_path,
    )


def build_and_install_ctp_wheel(
    repository_root: Path,
    wheel_dir: Path,
    *,
    python: str = sys.executable,
) -> CtpWheelReceipt:
    """Build from a safe archive of the exact gitlink, install, then verify PEP 610."""
    repository_root = repository_root.resolve(strict=True)
    pin = verify_ctp_source_pin(repository_root)
    _verify_base_receipt(repository_root)
    wheel_dir = wheel_dir.resolve()
    wheel_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bt-api-ctp-pinned-source-") as temporary:
        temporary_root = Path(temporary)
        source_root = _extract_pinned_archive(pin, temporary_root)
        output_dir = temporary_root / "wheel-output"
        output_dir.mkdir()
        command = [
            python,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(output_dir),
            str(source_root),
        ]
        completed = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            check=False,
            text=True,
            env=_subprocess_environment(public_pip_index=True),
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise CtpSourcePinError(f"pinned bt_api_ctp wheel build failed: {detail}")
        wheels = sorted(output_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise CtpSourcePinError(f"expected one pinned bt_api_ctp wheel, found {len(wheels)}")
        built_wheel = wheels[0]
        wheel_name, wheel_version = _read_wheel_identity(built_wheel)
        if wheel_name != pin.package_name or wheel_version != pin.package_version:
            raise CtpSourcePinError(
                f"pinned CTP wheel metadata mismatch: Name={wheel_name!r}, Version={wheel_version!r}"
            )
        final_wheel = wheel_dir / built_wheel.name
        final_wheel.write_bytes(built_wheel.read_bytes())

    wheel_name, wheel_version = _read_wheel_identity(final_wheel)
    if wheel_name != pin.package_name or wheel_version != pin.package_version:
        raise CtpSourcePinError("copied local CTP wheel metadata changed after the source build")
    wheel_sha256 = _sha256(final_wheel)
    install_env = _subprocess_environment()
    install = subprocess.run(  # noqa: S603
        [
            python,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--force-reinstall",
            str(final_wheel),
        ],
        capture_output=True,
        check=False,
        text=True,
        env=install_env,
    )
    if install.returncode:
        detail = install.stderr.strip() or install.stdout.strip()
        raise CtpSourcePinError(f"installing pinned bt_api_ctp wheel failed: {detail}")

    receipt = CtpWheelReceipt(
        parent_commit=pin.parent_commit,
        source_commit=pin.source_commit,
        source_tree=pin.source_tree,
        source_origin=pin.source_origin,
        package_name=pin.package_name,
        package_version=pin.package_version,
        minimum_version=pin.minimum_version,
        minimum_base_version=pin.minimum_base_version,
        wheel_filename=final_wheel.name,
        wheel_sha256=wheel_sha256,
        wheel_path=final_wheel,
    )
    if _sha256(final_wheel) != receipt.wheel_sha256:
        raise CtpSourcePinError("local bt_api_ctp wheel hash changed before installation")
    _verify_local_wheel_origin(
        receipt.package_name,
        receipt.package_version,
        receipt.wheel_path,
        receipt.wheel_sha256,
    )
    return receipt


def _write_github_environment(path: Path, receipt: CtpWheelReceipt) -> None:
    values = {
        "BT_API_CTP_SOURCE_SHA": receipt.source_commit,
        "BT_API_CTP_SOURCE_TREE_SHA": receipt.source_tree,
        "BT_API_CTP_SOURCE_ORIGIN": receipt.source_origin,
        "BT_API_CTP_SOURCE_VERSION": receipt.package_version,
        "BT_API_CTP_WHEEL_SHA256": receipt.wheel_sha256,
        "BT_API_CTP_WHEEL_PATH": str(receipt.wheel_path),
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for name, value in values.items():
            handle.write(f"{name}={_safe_env_value(value)}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--wheel-dir", type=Path)
    parser.add_argument("--github-env", type=Path)
    parser.add_argument("--verify-installed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        repository_root = args.repository_root.resolve()
        if args.verify_installed:
            receipt = _verify_installed_ctp_receipt(repository_root)
            _verify_base_receipt(repository_root)
        else:
            if args.wheel_dir is None:
                raise CtpSourcePinError(
                    "--wheel-dir is required when building the pinned CTP wheel"
                )
            receipt = build_and_install_ctp_wheel(repository_root, args.wheel_dir)
            if args.github_env:
                _write_github_environment(args.github_env, receipt)
    except (BaseSourcePinError, CtpSourcePinError, OSError) as exc:
        print(f"CTP source pin bootstrap failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(receipt), default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
