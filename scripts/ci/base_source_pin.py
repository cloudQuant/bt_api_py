#!/usr/bin/env python3
"""Build/install the pinned base source for CI (Python 3.11 through 3.14)."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import asdict, dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

BASE_PATH = "bt_api/bt_api_base"
BASE_ORIGIN = "https://github.com/cloudQuant/bt_api_base.git"
MINIMUM_BASE_VERSION = (0, 15, 5)
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
BASE_REQUIREMENT_RE = re.compile(r"^bt_api_base\s*>=\s*(\d+\.\d+\.\d+)$", re.IGNORECASE)
SUBPROCESS_ENV_ALLOWLIST = (
    "PATH",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)


class BaseSourcePinError(RuntimeError):
    """Raised when the checked-out source cannot be bound to the parent gitlink."""


@dataclass(frozen=True)
class BaseSourcePin:
    parent_commit: str
    source_commit: str
    source_tree: str
    source_origin: str
    package_name: str
    package_version: str
    minimum_version: str
    source_path: Path


@dataclass(frozen=True)
class BaseWheelReceipt:
    parent_commit: str
    source_commit: str
    source_tree: str
    source_origin: str
    package_name: str
    package_version: str
    minimum_version: str
    wheel_filename: str
    wheel_sha256: str
    wheel_path: Path


def _subprocess_environment(*, public_pip_index: bool = False) -> dict[str, str]:
    """Pass only OS runtime settings and the explicit public pip configuration."""
    environment = {
        name: os.environ[name] for name in SUBPROCESS_ENV_ALLOWLIST if name in os.environ
    }
    environment["PIP_CONFIG_FILE"] = os.devnull
    if public_pip_index:
        environment["PIP_INDEX_URL"] = "https://pypi.org/simple/"
    return environment


def _run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", "-C", str(repository), *arguments],  # noqa: S607
        capture_output=True,
        check=False,
        text=True,
        env=_subprocess_environment(),
    )
    if completed.returncode:
        command = arguments[0] if arguments else "unknown"
        raise BaseSourcePinError(f"git {command!r} failed with exit code {completed.returncode}")
    return completed.stdout.strip()


def _stable_version(version: str, *, label: str) -> tuple[int, int, int]:
    match = STABLE_VERSION_RE.fullmatch(version)
    if not match:
        raise BaseSourcePinError(f"{label} must be a stable three-part version: {version!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _source_metadata(source_path: Path, source_commit: str) -> tuple[str, str]:
    result = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
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
        raise BaseSourcePinError("pinned bt_api_base source has no readable pyproject.toml")
    try:
        data = tomllib.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise BaseSourcePinError(f"pinned bt_api_base pyproject.toml is invalid: {exc}") from exc
    project = data.get("project")
    if not isinstance(project, dict):
        raise BaseSourcePinError("pinned bt_api_base pyproject.toml has no [project] table")
    name = str(project.get("name") or "")
    version = str(project.get("version") or "")
    if name != "bt_api_base":
        raise BaseSourcePinError(f"pinned source package name must be bt_api_base, got {name!r}")
    _stable_version(version, label="pinned bt_api_base version")
    return name, version


def verify_base_source_pin(repository_root: Path) -> BaseSourcePin:
    """Bind the source checkout to the full gitlink SHA and canonical upstream URL."""
    repository_root = repository_root.resolve(strict=True)
    parent_commit = _run_git(repository_root, "rev-parse", "HEAD")
    tree_entry = _run_git(repository_root, "ls-tree", parent_commit, "--", BASE_PATH)
    fields = tree_entry.split()
    if len(fields) != 4 or fields[0] != "160000" or fields[1] != "commit" or fields[3] != BASE_PATH:
        raise BaseSourcePinError(f"{BASE_PATH} is not an exact gitlink in parent HEAD")
    source_commit = fields[2]
    if not GIT_SHA_RE.fullmatch(source_commit):
        raise BaseSourcePinError(
            f"parent gitlink is not a full 40-character SHA: {source_commit!r}"
        )

    source_path = repository_root / BASE_PATH
    if not source_path.is_dir():
        raise BaseSourcePinError(f"pinned source checkout is unavailable: {source_path}")

    configured_origin = _run_git(
        repository_root,
        "config",
        "-f",
        ".gitmodules",
        "--get",
        "submodule.bt_api/bt_api_base.url",
    )
    actual_origin = _run_git(source_path, "remote", "get-url", "origin")
    if configured_origin != BASE_ORIGIN or actual_origin != BASE_ORIGIN:
        raise BaseSourcePinError(
            "bt_api_base origin mismatch: .gitmodules URL and source origin must both match "
            f"canonical upstream {BASE_ORIGIN}"
        )

    checked_out_commit = _run_git(source_path, "rev-parse", "HEAD")
    if checked_out_commit != source_commit:
        raise BaseSourcePinError(
            f"bt_api_base checkout {checked_out_commit} does not match parent gitlink {source_commit}"
        )
    source_tree = _run_git(source_path, "rev-parse", f"{source_commit}^{{tree}}")

    root_pyproject = repository_root / "pyproject.toml"
    try:
        root_data = tomllib.loads(root_pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BaseSourcePinError(f"cannot read parent pyproject.toml: {exc}") from exc
    dependencies = root_data.get("project", {}).get("dependencies", [])
    matches = [
        match
        for requirement in dependencies
        if (match := BASE_REQUIREMENT_RE.fullmatch(str(requirement).strip())) is not None
    ]
    if len(matches) != 1:
        raise BaseSourcePinError(
            "parent dependencies must contain exactly one simple bt_api_base>=X.Y.Z requirement"
        )
    minimum_version = matches[0].group(1)
    minimum = _stable_version(minimum_version, label="parent bt_api_base minimum version")
    if minimum < MINIMUM_BASE_VERSION:
        raise BaseSourcePinError(
            f"parent bt_api_base lower bound must remain >=0.15.5, got {minimum_version}"
        )

    package_name, package_version = _source_metadata(source_path, source_commit)
    version_tuple = _stable_version(package_version, label="pinned bt_api_base version")
    if version_tuple < minimum:
        raise BaseSourcePinError(
            f"pinned bt_api_base {package_version} does not satisfy parent >= {minimum_version}"
        )

    return BaseSourcePin(
        parent_commit=parent_commit,
        source_commit=source_commit,
        source_tree=source_tree,
        source_origin=actual_origin,
        package_name=package_name,
        package_version=package_version,
        minimum_version=minimum_version,
        source_path=source_path,
    )


def _extract_pinned_archive(pin: BaseSourcePin, destination: Path) -> Path:
    completed = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
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
        raise BaseSourcePinError(
            f"cannot archive pinned bt_api_base source (git exit code {completed.returncode})"
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
                    raise BaseSourcePinError("pinned source archive contains an unsafe path")
                target = source_root.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fileobj = archive.extractfile(member)
                    if fileobj is None:
                        raise BaseSourcePinError(
                            "pinned source archive contains an unreadable file"
                        )
                    with fileobj, target.open("wb") as output:
                        output.write(fileobj.read())
                    if member.mode & 0o111:
                        target.chmod(target.stat().st_mode | 0o111)
                else:
                    raise BaseSourcePinError(
                        "pinned source archive contains a non-regular entry; refusing extraction"
                    )
    except tarfile.TarError as exc:
        raise BaseSourcePinError(f"pinned source archive is invalid: {exc}") from exc
    return source_root


def _read_wheel_identity(wheel_path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            metadata_paths = [
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_paths) != 1:
                raise BaseSourcePinError(
                    f"expected one wheel METADATA file, found {len(metadata_paths)}"
                )
            metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
    except (OSError, zipfile.BadZipFile) as exc:
        raise BaseSourcePinError(
            f"pinned source build did not produce a valid wheel: {exc}"
        ) from exc
    return str(metadata.get("Name") or ""), str(metadata.get("Version") or "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_env_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise BaseSourcePinError("refusing multiline GitHub environment value")
    return value


def _verify_installed_wheel(receipt: BaseWheelReceipt) -> None:
    try:
        from importlib import metadata
    except ImportError as exc:  # pragma: no cover - Python 3.11+ provides this module
        raise BaseSourcePinError(f"cannot import installed distribution metadata: {exc}") from exc

    try:
        distribution = metadata.distribution(receipt.package_name)
        direct_url_text = distribution.read_text("direct_url.json")
        direct_url = json.loads(direct_url_text) if direct_url_text else {}
    except (ImportError, json.JSONDecodeError, metadata.PackageNotFoundError) as exc:
        raise BaseSourcePinError(f"cannot verify installed source wheel receipt: {exc}") from exc

    if distribution.version != receipt.package_version:
        raise BaseSourcePinError(
            f"installed bt_api_base {distribution.version} does not match pinned wheel "
            f"{receipt.package_version}"
        )
    archive_info = direct_url.get("archive_info") or {}
    recorded_hash = archive_info.get("hash")
    if recorded_hash is None:
        recorded_hash = (archive_info.get("hashes") or {}).get("sha256")
        if recorded_hash:
            recorded_hash = f"sha256={recorded_hash}"
    if recorded_hash != f"sha256={receipt.wheel_sha256}":
        raise BaseSourcePinError(
            "installed bt_api_base direct_url.json does not match the local wheel SHA-256"
        )


def build_and_install_base_wheel(
    repository_root: Path,
    wheel_dir: Path,
    *,
    python: str = sys.executable,
) -> BaseWheelReceipt:
    """Build the parent-pinned source archive, hash it, install it, and verify PEP 610."""
    pin = verify_base_source_pin(repository_root)
    wheel_dir = wheel_dir.resolve()
    wheel_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bt-api-base-pinned-source-") as temporary:
        temporary_root = Path(temporary)
        source_root = _extract_pinned_archive(pin, temporary_root)
        output_dir = temporary_root / "wheel-output"
        output_dir.mkdir()
        environment = _subprocess_environment(public_pip_index=True)
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
            env=environment,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise BaseSourcePinError(f"pinned bt_api_base wheel build failed: {detail}")

        wheels = sorted(output_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise BaseSourcePinError(f"expected one pinned bt_api_base wheel, found {len(wheels)}")
        built_wheel = wheels[0]
        wheel_name, wheel_version = _read_wheel_identity(built_wheel)
        if wheel_name != pin.package_name or wheel_version != pin.package_version:
            raise BaseSourcePinError(
                f"pinned wheel metadata mismatch: Name={wheel_name!r}, Version={wheel_version!r}"
            )
        final_wheel = wheel_dir / built_wheel.name
        final_wheel.write_bytes(built_wheel.read_bytes())

    wheel_name, wheel_version = _read_wheel_identity(final_wheel)
    if wheel_name != pin.package_name or wheel_version != pin.package_version:
        raise BaseSourcePinError("copied local wheel metadata changed after the source build")
    wheel_sha256 = _sha256(final_wheel)
    receipt = BaseWheelReceipt(
        parent_commit=pin.parent_commit,
        source_commit=pin.source_commit,
        source_tree=pin.source_tree,
        source_origin=pin.source_origin,
        package_name=pin.package_name,
        package_version=pin.package_version,
        minimum_version=pin.minimum_version,
        wheel_filename=final_wheel.name,
        wheel_sha256=wheel_sha256,
        wheel_path=final_wheel,
    )

    if _sha256(final_wheel) != receipt.wheel_sha256:
        raise BaseSourcePinError("local bt_api_base wheel hash changed before installation")
    install_environment = _subprocess_environment()
    completed = subprocess.run(  # noqa: S603
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
        env=install_environment,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise BaseSourcePinError(f"installing pinned bt_api_base wheel failed: {detail}")
    _verify_installed_wheel(receipt)
    return receipt


def _write_github_environment(path: Path, receipt: BaseWheelReceipt) -> None:
    values = {
        "BT_API_BASE_SOURCE_SHA": receipt.source_commit,
        "BT_API_BASE_SOURCE_TREE_SHA": receipt.source_tree,
        "BT_API_BASE_SOURCE_ORIGIN": receipt.source_origin,
        "BT_API_BASE_SOURCE_VERSION": receipt.package_version,
        "BT_API_BASE_WHEEL_SHA256": receipt.wheel_sha256,
        "BT_API_BASE_WHEEL_PATH": str(receipt.wheel_path),
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            handle.write(f"{key}={_safe_env_value(value)}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--github-env", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        receipt = build_and_install_base_wheel(args.repository_root, args.wheel_dir)
        if args.github_env:
            _write_github_environment(args.github_env, receipt)
    except (BaseSourcePinError, OSError) as exc:
        print(f"base source pin bootstrap failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(receipt), default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
