#!/usr/bin/env python3
"""Verify local Iteration 41 wheel-install and isolated-consumer mechanics.

This verifier is deliberately narrower than a release process.  It creates a
fresh local wheelhouse from the checked-out sources and the controller's
already-installed distributions, installs only from that wheelhouse into a
new virtual environment, and runs a Python-socket-guarded consumer probe. Its receipt
records dirty, untracked, and missing-superproject-pin states verbatim.

The receipt can prove only that a particular local source snapshot built and
that selected local wheel payloads were installed and exercised without an
editable checkout. It cannot prove a reviewed commit, artifact provenance,
publication, signature, provider connectivity, account authority, or live
trading admission. Consequently a successful run is always labelled
``LOCAL_EVIDENCE_ONLY`` rather than a release or production PASS.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.parser import BytesParser
from importlib import metadata
from pathlib import Path
from typing import Any

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name
from packaging.version import Version
from wheel.wheelfile import WheelFile

SDK_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DATE_EPOCH = "315532800"  # 1980-01-01: valid for ZIP timestamps.
LOCAL_ONLY_RESULT = "LOCAL_EVIDENCE_ONLY"
FAILED_RESULT = "FAILED"
_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".benchmarks",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
    }
)
_IGNORED_FILE_SUFFIXES = (".pyc", ".pyo")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)(\b(?:api[_-]?key|authorization|credential|pass(?:word|phrase)?|secret|token)"
    r"\b\s*[:=]\s*)([^\s,;]+)"
)
_AUTHORIZATION_HEADER_PATTERN = re.compile(r"(?im)(\bauthorization\s*:\s*)([^\r\n]+)")
_URL_USERINFO_PATTERN = re.compile(r"(?i)(https?://)([^\s/@:]+):([^\s/@]+)@")


class BundleVerificationError(RuntimeError):
    """A local wheel, dependency, or isolated-consumer contract failed."""


@dataclass(frozen=True)
class ProjectSpec:
    """One source tree included in the local-only consumer matrix."""

    key: str
    distribution: str
    module: str
    source_root: Path
    includes: tuple[str, ...]
    superproject_relative_path: str | None = None


@dataclass(frozen=True)
class SnapshotFile:
    """One byte-for-byte file recorded in a frozen source snapshot."""

    relative_path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class SourceSnapshot:
    """A frozen, symlink-free source view used for a wheel build."""

    project: ProjectSpec
    staged_root: Path
    files: tuple[SnapshotFile, ...]
    digest: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_ignored_directory(path: Path) -> bool:
    return path.name in _IGNORED_DIRECTORY_NAMES or path.name.endswith(".egg-info")


def _is_ignored_file(path: Path) -> bool:
    return path.name.endswith(_IGNORED_FILE_SUFFIXES)


def _selected_files(project: ProjectSpec) -> tuple[Path, ...]:
    """Return exactly the source files that may enter a staged wheel build.

    Symlinks are rejected rather than followed.  A source tree could otherwise
    make a replay receipt look local while packaging bytes outside the reviewed
    directory.
    """

    source_root = project.source_root.resolve(strict=True)
    selected: list[Path] = []
    for include in project.includes:
        candidate = source_root / include
        if not candidate.exists():
            raise BundleVerificationError(
                f"{project.key} required source path is missing: {candidate}"
            )
        if candidate.is_symlink():
            raise BundleVerificationError(
                f"{project.key} required source path is a symlink: {candidate}"
            )
        if candidate.is_file():
            if not _is_ignored_file(candidate):
                selected.append(candidate)
            continue
        for directory, directory_names, file_names in os.walk(
            candidate, followlinks=False
        ):
            current = Path(directory)
            safe_names: list[str] = []
            for directory_name in sorted(directory_names):
                child = current / directory_name
                if child.is_symlink():
                    raise BundleVerificationError(
                        f"{project.key} source tree contains a symlink: {child}"
                    )
                if not _is_ignored_directory(child):
                    safe_names.append(directory_name)
            directory_names[:] = safe_names
            for file_name in sorted(file_names):
                child = current / file_name
                if child.is_symlink():
                    raise BundleVerificationError(
                        f"{project.key} source tree contains a symlink: {child}"
                    )
                if child.is_file() and not _is_ignored_file(child):
                    selected.append(child)
    unique = tuple(sorted(set(selected), key=lambda item: item.as_posix()))
    if not unique:
        raise BundleVerificationError(f"{project.key} source selection is empty")
    return unique


def _snapshot_digest(files: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative, payload in files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _read_regular_file(path: Path, *, project: ProjectSpec, role: str) -> bytes:
    """Read one regular file without silently following a late symlink swap."""

    try:
        before = path.lstat()
    except OSError as error:
        raise BundleVerificationError(
            f"{project.key} cannot stat {role}: {path}"
        ) from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise BundleVerificationError(
            f"{project.key} {role} is not a regular non-symlink file: {path}"
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise BundleVerificationError(
            f"{project.key} cannot open {role}: {path}"
        ) from error
    try:
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise BundleVerificationError(
                    f"{project.key} {role} changed to a non-regular file: {path}"
                )
            payload = handle.read()
    except OSError as error:
        raise BundleVerificationError(
            f"{project.key} cannot read {role}: {path}"
        ) from error
    try:
        after = path.lstat()
    except OSError as error:
        raise BundleVerificationError(
            f"{project.key} {role} disappeared while reading: {path}"
        ) from error
    if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode):
        raise BundleVerificationError(
            f"{project.key} {role} changed while reading: {path}"
        )
    if after.st_size != len(payload):
        raise BundleVerificationError(
            f"{project.key} {role} changed size while reading: {path}"
        )
    return payload


def _snapshot_relative_path(
    path: Path, source_root: Path, *, project: ProjectSpec
) -> str:
    try:
        relative = path.relative_to(source_root)
    except ValueError as error:
        raise BundleVerificationError(
            f"{project.key} source file escaped source root: {path}"
        ) from error
    try:
        return _safe_relative_path(
            relative.as_posix(), context=f"{project.key} snapshot source path"
        ).as_posix()
    except BundleVerificationError as error:
        raise BundleVerificationError(
            f"{project.key} unsafe snapshot path: {relative}"
        ) from error


def _validate_staged_snapshot(snapshot: SourceSnapshot) -> None:
    """Verify that the frozen stage still exactly matches its byte manifest."""

    expected = {item.relative_path: item for item in snapshot.files}
    actual: dict[str, Path] = {}
    for directory, directory_names, file_names in os.walk(
        snapshot.staged_root, followlinks=False
    ):
        current = Path(directory)
        for directory_name in directory_names:
            child = current / directory_name
            if child.is_symlink():
                raise BundleVerificationError(
                    f"{snapshot.project.key} frozen snapshot contains a symlink: {child}"
                )
        for file_name in file_names:
            child = current / file_name
            if child.is_symlink():
                raise BundleVerificationError(
                    f"{snapshot.project.key} frozen snapshot contains a symlink: {child}"
                )
            relative = _snapshot_relative_path(
                child, snapshot.staged_root, project=snapshot.project
            )
            actual[relative] = child
    if set(actual) != set(expected):
        raise BundleVerificationError(
            f"{snapshot.project.key} frozen snapshot file set does not match its manifest"
        )
    material = []
    for relative, manifest in sorted(expected.items()):
        payload = _read_regular_file(
            actual[relative], project=snapshot.project, role="frozen snapshot file"
        )
        if len(payload) != manifest.size or _sha256_bytes(payload) != manifest.sha256:
            raise BundleVerificationError(
                f"{snapshot.project.key} frozen snapshot content changed: {relative}"
            )
        material.append((relative, payload))
    if _snapshot_digest(material) != snapshot.digest:
        raise BundleVerificationError(
            f"{snapshot.project.key} frozen snapshot digest changed"
        )


def _recheck_source_snapshot(snapshot: SourceSnapshot) -> None:
    """Fail if the original source changed after its bytes were frozen."""

    source_root = snapshot.project.source_root.resolve(strict=True)
    expected = {item.relative_path: item for item in snapshot.files}
    current = {
        _snapshot_relative_path(path, source_root, project=snapshot.project): path
        for path in _selected_files(snapshot.project)
    }
    if set(current) != set(expected):
        raise BundleVerificationError(
            f"{snapshot.project.key} source file set changed after frozen snapshot capture"
        )
    for relative, manifest in sorted(expected.items()):
        payload = _read_regular_file(
            current[relative], project=snapshot.project, role="source recheck file"
        )
        if len(payload) != manifest.size or _sha256_bytes(payload) != manifest.sha256:
            raise BundleVerificationError(
                f"{snapshot.project.key} source content changed after frozen snapshot capture: {relative}"
            )


def _capture_source_snapshot(project: ProjectSpec, destination: Path) -> SourceSnapshot:
    """Materialize source bytes once, then build only from that frozen copy.

    The original source tree is never read by later wheel-build stages.  A
    recheck catches a mutation that happened while the snapshot was captured.
    """

    source_root = project.source_root.resolve(strict=True)
    destination.mkdir(parents=True, exist_ok=False)
    manifest: list[SnapshotFile] = []
    material: list[tuple[str, bytes]] = []
    for source in _selected_files(project):
        relative = _snapshot_relative_path(source, source_root, project=project)
        payload = _read_regular_file(source, project=project, role="source file")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(payload)
        manifest.append(SnapshotFile(relative, _sha256_bytes(payload), len(payload)))
        material.append((relative, payload))
    snapshot = SourceSnapshot(
        project=project,
        staged_root=destination,
        files=tuple(sorted(manifest, key=lambda item: item.relative_path)),
        digest=_snapshot_digest(sorted(material)),
    )
    _validate_staged_snapshot(snapshot)
    _recheck_source_snapshot(snapshot)
    return snapshot


def _stage_snapshot(snapshot: SourceSnapshot, destination: Path) -> None:
    """Copy a verified frozen snapshot for one independent wheel build."""

    _validate_staged_snapshot(snapshot)
    destination.mkdir(parents=True, exist_ok=False)
    for manifest in snapshot.files:
        source = snapshot.staged_root / manifest.relative_path
        payload = _read_regular_file(
            source, project=snapshot.project, role="frozen snapshot file"
        )
        if len(payload) != manifest.size or _sha256_bytes(payload) != manifest.sha256:
            raise BundleVerificationError(
                f"{snapshot.project.key} frozen snapshot changed before build: {manifest.relative_path}"
            )
        target = destination / manifest.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(payload)


def _run_git(
    path: Path, arguments: list[str]
) -> subprocess.CompletedProcess[str] | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    try:
        return subprocess.run(  # noqa: S603 - fixed Git subcommands inspect local repository state.
            [executable, "-C", str(path), *arguments],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_offline_environment(),
        )
    except OSError:
        return None


def _git_source_state(project: ProjectSpec, sdk_root: Path) -> dict[str, Any]:
    """Describe local Git facts without treating them as release provenance."""

    root = project.source_root.resolve(strict=True)
    top_level = _run_git(root, ["rev-parse", "--show-toplevel"])
    if top_level is None or top_level.returncode != 0:
        return {
            "source_root": str(root),
            "git_available": False,
            "source_state": "UNTRACKED_OR_NON_GIT_SOURCE",
            "superproject_gitlink": None,
            "checkout_matches_superproject_gitlink": False,
        }

    head = _run_git(root, ["rev-parse", "HEAD"])
    status = _run_git(root, ["status", "--porcelain=v1", "--untracked-files=all"])
    if head is None or head.returncode != 0 or status is None or status.returncode != 0:
        raise BundleVerificationError(f"could not inspect Git state for {project.key}")
    status_lines = tuple(line for line in status.stdout.splitlines() if line)
    state: dict[str, Any] = {
        "source_root": str(root),
        "git_available": True,
        "checkout_head": head.stdout.strip(),
        "dirty": bool(status_lines),
        "status_entry_count": len(status_lines),
        # File names may be locally sensitive.  Preserve an auditable change
        # signal without publishing their names into a receipt.
        "status_sha256": _sha256_bytes(status.stdout.encode("utf-8")),
        "superproject_gitlink": None,
        "checkout_matches_superproject_gitlink": False,
    }
    if project.superproject_relative_path is None:
        state["source_state"] = (
            "DIRTY_LOCAL_SOURCE" if status_lines else "LOCAL_GIT_SOURCE"
        )
        return state

    gitlink = _run_git(
        sdk_root,
        ["ls-tree", "HEAD", "--", project.superproject_relative_path],
    )
    if gitlink is None or gitlink.returncode != 0:
        raise BundleVerificationError(
            f"could not inspect SDK gitlink for {project.key}"
        )
    fields = gitlink.stdout.strip().split()
    if len(fields) >= 3 and fields[0] == "160000" and fields[1] == "commit":
        state["superproject_gitlink"] = fields[2]
        state["checkout_matches_superproject_gitlink"] = (
            fields[2] == state["checkout_head"]
        )
    if status_lines:
        state["source_state"] = "DIRTY_LOCAL_SOURCE"
    elif state["superproject_gitlink"] is None:
        state["source_state"] = "UNPINNED_LOCAL_SOURCE"
    elif not state["checkout_matches_superproject_gitlink"]:
        state["source_state"] = "CHECKOUT_DIFFERS_FROM_SUPERPROJECT_GITLINK"
    else:
        state["source_state"] = "CLEAN_GITLINK_SOURCE"
    return state


_MINIMAL_ENVIRONMENT_KEYS = (
    "ALLUSERSPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "SYSTEMDRIVE",
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
)
_REDACTION_SENTINEL_ENV = "ITERATION41_CONTROLLER_SECRET_SENTINEL"


def _environment_value_case_insensitive(name: str) -> str | None:
    for key, value in os.environ.items():
        if key.casefold() == name.casefold():
            return value
    return None


def _offline_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a minimal child environment with no inherited Python/pip state.

    It is an allow-list, rather than a scrubbed copy of the controller
    environment.  In particular credentials and arbitrary startup variables
    cannot reach build, pip, or consumer processes by inheritance.
    """

    environment = {
        key: value
        for key in _MINIMAL_ENVIRONMENT_KEYS
        if (value := _environment_value_case_insensitive(key)) is not None
    }
    environment.update(
        {
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_NO_INDEX": "1",
            "PIP_NO_CACHE_DIR": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8:replace",
            "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
        }
    )
    if extra:
        environment.update(extra)
    return environment


def _controller_requirement_receipt(
    project: ProjectSpec, *, source_root: Path | None = None
) -> list[dict[str, str]]:
    pyproject = (source_root or project.source_root) / "pyproject.toml"
    if pyproject.is_file():
        with pyproject.open("rb") as handle:
            payload = tomllib.load(handle)
        requirements = list((payload.get("build-system") or {}).get("requires") or [])
    else:
        requirements = ["setuptools"]
    observed: list[dict[str, str]] = []
    for raw in requirements:
        requirement = Requirement(str(raw))
        try:
            version = metadata.version(requirement.name)
        except metadata.PackageNotFoundError as error:
            raise BundleVerificationError(
                f"controller is missing build requirement for {project.key}: {requirement}"
            ) from error
        if requirement.specifier and Version(version) not in requirement.specifier:
            raise BundleVerificationError(
                f"controller build requirement does not satisfy {project.key}: "
                f"{requirement} (observed {version})"
            )
        observed.append({"requirement": str(requirement), "observed_version": version})
    return observed


def _redact_text(value: str) -> str:
    """Remove likely secret values before persisting or printing diagnostics."""

    redacted = value
    # The verifier deliberately does not inherit arbitrary controller values,
    # but a build can still echo a controller-only sentinel or a source-encoded
    # credential.  Do not let either reach artifacts or stderr.
    for key, candidate in os.environ.items():
        normalized = key.casefold()
        if candidate and (
            key == _REDACTION_SENTINEL_ENV
            or any(
                marker in normalized
                for marker in (
                    "api_key",
                    "apikey",
                    "credential",
                    "pass",
                    "secret",
                    "token",
                )
            )
        ):
            redacted = redacted.replace(candidate, "***REDACTED***")
    redacted = _URL_USERINFO_PATTERN.sub(r"\1***REDACTED***:***REDACTED***@", redacted)
    redacted = _AUTHORIZATION_HEADER_PATTERN.sub(r"\1***REDACTED***", redacted)
    return _SECRET_ASSIGNMENT_PATTERN.sub(r"\1***REDACTED***", redacted)


def _run_logged(
    command: list[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    logs_dir: Path,
    name: str,
) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603 - all arguments are locally generated.
        command,
        cwd=cwd,
        env=dict(environment),
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout = _redact_text(completed.stdout)
    stderr = _redact_text(completed.stderr)
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / f"{name}.stdout.log"
    stderr_path = logs_dir / f"{name}.stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout_log": str(stdout_path.relative_to(logs_dir.parent)),
        "stderr_log": str(stderr_path.relative_to(logs_dir.parent)),
        "stdout_sha256": _sha256_bytes(stdout.encode("utf-8")),
        "stderr_sha256": _sha256_bytes(stderr.encode("utf-8")),
        "stdout": stdout,
        "stderr": stderr,
    }


def _wheel_metadata(wheel: Path) -> tuple[str, str, list[str], str]:
    with zipfile.ZipFile(wheel) as archive:
        metadata_members = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA") and not name.endswith("/")
        ]
        if len(metadata_members) != 1:
            raise BundleVerificationError(
                f"{wheel.name} must contain exactly one dist-info/METADATA"
            )
        raw = archive.read(metadata_members[0])
    payload = BytesParser().parsebytes(raw)
    name = payload.get("Name")
    version = payload.get("Version")
    if not name or not version:
        raise BundleVerificationError(
            f"{wheel.name} METADATA is missing Name or Version"
        )
    return (
        name,
        version,
        list(payload.get_all("Requires-Dist") or []),
        _sha256_bytes(raw),
    )


def _record_contract(wheel: Path, expected_distribution: str) -> dict[str, Any]:
    """Validate METADATA and every hashed RECORD member in a built wheel."""

    distribution, version, requirements, metadata_digest = _wheel_metadata(wheel)
    if canonicalize_name(distribution) != canonicalize_name(expected_distribution):
        raise BundleVerificationError(
            f"{wheel.name} declares {distribution}, expected {expected_distribution}"
        )
    with zipfile.ZipFile(wheel) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        names = {item.filename for item in members}
        for name in names:
            if _is_python_startup_hook(name):
                raise BundleVerificationError(
                    f"{wheel.name} contains a Python startup hook: {name}"
                )
            try:
                _safe_relative_path(name, context=f"{wheel.name} archive member")
            except BundleVerificationError as error:
                raise BundleVerificationError(
                    f"{wheel.name} contains an unsafe archive member"
                ) from error
        if any(
            name.endswith((".pyc", ".pyo")) or "__pycache__/" in name for name in names
        ):
            raise BundleVerificationError(f"{wheel.name} contains bytecode")
        record_members = [name for name in names if name.endswith(".dist-info/RECORD")]
        if len(record_members) != 1:
            raise BundleVerificationError(
                f"{wheel.name} must contain exactly one RECORD"
            )
        record_name = record_members[0]
        try:
            rows = list(
                csv.reader(
                    io.TextIOWrapper(archive.open(record_name), encoding="utf-8")
                )
            )
        except (UnicodeDecodeError, csv.Error) as error:
            raise BundleVerificationError(
                f"{wheel.name} has an unreadable RECORD"
            ) from error
        row_by_name = {row[0]: row for row in rows if row}
        if len(row_by_name) != len(rows) or set(row_by_name) != names:
            raise BundleVerificationError(
                f"{wheel.name} RECORD does not cover every archive member"
            )
        for member in members:
            row = row_by_name[member.filename]
            if len(row) != 3:
                raise BundleVerificationError(
                    f"{wheel.name} has a malformed RECORD row"
                )
            if member.filename == record_name:
                if row[1] or row[2]:
                    raise BundleVerificationError(f"{wheel.name} hashes its own RECORD")
                continue
            if not row[1].startswith("sha256="):
                raise BundleVerificationError(
                    f"{wheel.name} RECORD uses a non-SHA256 hash"
                )
            encoded_digest = row[1].split("=", 1)[1]
            padding = "=" * (-len(encoded_digest) % 4)
            try:
                expected_digest = base64.urlsafe_b64decode(encoded_digest + padding)
            except (binascii.Error, ValueError, TypeError) as error:
                raise BundleVerificationError(
                    f"{wheel.name} has an invalid RECORD digest"
                ) from error
            actual = hashlib.sha256(archive.read(member.filename)).digest()
            if actual != expected_digest or row[2] != str(member.file_size):
                raise BundleVerificationError(
                    f"{wheel.name} RECORD integrity check failed for {member.filename}"
                )
    return {
        "distribution": distribution,
        "version": version,
        "metadata_sha256": metadata_digest,
        "requirements": requirements,
        "record_validated": True,
        "wheel_sha256": _sha256_file(wheel),
    }


def _find_project_wheel(directory: Path, distribution: str) -> Path:
    matches = [
        candidate
        for candidate in directory.glob("*.whl")
        if canonicalize_name(_wheel_metadata(candidate)[0])
        == canonicalize_name(distribution)
    ]
    if len(matches) != 1:
        raise BundleVerificationError(
            f"expected exactly one {distribution} wheel in {directory}, found {len(matches)}"
        )
    return matches[0]


def _build_reproducible_wheel(
    snapshot: SourceSnapshot,
    *,
    controller_python: str,
    work_dir: Path,
    wheelhouse: Path,
    logs_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    """Build the same clean snapshot twice and require byte-identical wheels."""

    _recheck_source_snapshot(snapshot)
    build_receipts: list[dict[str, Any]] = []
    built_wheels: list[Path] = []
    for index in (1, 2):
        stage = work_dir / f"{snapshot.project.key}-source-{index}"
        output = work_dir / f"{snapshot.project.key}-wheel-{index}"
        _stage_snapshot(snapshot, stage)
        output.mkdir(parents=True, exist_ok=False)
        command = [
            controller_python,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--no-cache-dir",
            "--wheel-dir",
            str(output),
            ".",
        ]
        run = _run_logged(
            command,
            cwd=stage,
            environment=_offline_environment(),
            logs_dir=logs_dir,
            name=f"build-{snapshot.project.key}-{index}",
        )
        build_receipts.append(
            {
                key: value
                for key, value in run.items()
                if key not in {"stdout", "stderr"}
            }
        )
        if run["exit_code"] != 0:
            detail = run["stderr"].strip() or run["stdout"].strip()
            raise BundleVerificationError(
                f"local wheel build failed for {snapshot.project.key}: {detail}"
            )
        built_wheels.append(_find_project_wheel(output, snapshot.project.distribution))
    first_digest, second_digest = (_sha256_file(path) for path in built_wheels)
    if first_digest != second_digest:
        raise BundleVerificationError(
            f"non-reproducible local wheel for {snapshot.project.key}: "
            f"{first_digest} != {second_digest}"
        )
    _recheck_source_snapshot(snapshot)
    wheelhouse.mkdir(parents=True, exist_ok=True)
    destination = wheelhouse / built_wheels[0].name
    shutil.copy2(built_wheels[0], destination)
    evidence = _record_contract(destination, snapshot.project.distribution)
    evidence.update(
        {
            "filename": destination.name,
            "source_tree_sha256": snapshot.digest,
            "source_file_count": len(snapshot.files),
            "source_snapshot": {
                "captured_from_bytes": True,
                "original_source_recheck": "PASSED_BEFORE_AND_AFTER_BUILD",
            },
            "reproducible_build": True,
            "builds": build_receipts,
        }
    )
    return destination, evidence


def _site_roots() -> tuple[Path, ...]:
    paths = sysconfig.get_paths()
    roots = {
        Path(paths[key]).resolve() for key in ("purelib", "platlib") if paths.get(key)
    }
    return tuple(sorted(roots))


_STRIPPED_CONTROLLER_METADATA = frozenset(
    {"direct_url.json", "installer", "requested", "record"}
)
_PYTHON_STARTUP_FILENAMES = frozenset({"sitecustomize.py", "usercustomize.py"})
_WINDOWS_DEVICE_BASENAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }
)


def _windows_normalized_component(component: str) -> str:
    """Return the filesystem name Windows would use, without accepting it."""

    return component.rstrip(" .")


def _normalized_path_basename(raw: str | Path) -> str:
    raw_text = str(raw).replace("\\", "/")
    return _windows_normalized_component(raw_text.rsplit("/", 1)[-1]).casefold()


def _safe_relative_path(raw: str, *, context: str) -> Path:
    candidate = Path(raw)
    raw_parts = raw.split("/")
    if (
        not raw
        or candidate.is_absolute()
        or "\\" in raw
        or any(not component or component in {".", ".."} for component in raw_parts)
    ):
        raise BundleVerificationError(f"{context} has an unsafe relative path: {raw!r}")
    normalized_parts = []
    for component in raw_parts:
        normalized = _windows_normalized_component(component)
        device_basename = normalized.split(".", 1)[0].casefold()
        if (
            not normalized
            or normalized != component
            or ":" in normalized
            or device_basename in _WINDOWS_DEVICE_BASENAMES
        ):
            raise BundleVerificationError(
                f"{context} has a Windows-unsafe path component: {raw!r}"
            )
        normalized_parts.append(normalized)
    return Path(*normalized_parts)


def _is_python_startup_hook(relative: Path | str) -> bool:
    """Return whether an installed file can change interpreter startup."""

    name = _normalized_path_basename(relative)
    if name.endswith((".pth", ".egg-link")):
        return True
    return name in _PYTHON_STARTUP_FILENAMES


def _is_pip_generated_external_script_member(raw: str) -> bool:
    """Recognize only pip's generated venv console-script RECORD entries."""

    if "\\" in raw:
        return False
    parts = raw.split("/")
    if len(parts) < 3 or not all(part == ".." for part in parts[:-2]):
        return False
    script_directory = _windows_normalized_component(parts[-2])
    if script_directory != parts[-2] or script_directory.casefold() not in {
        "scripts",
        "bin",
    }:
        return False
    # This also rejects Windows device names, ADS, and trailing dot/space in
    # the generated script filename before it can be silently ignored.
    _safe_relative_path(parts[-1], context="pip-generated console script")
    return True


def _reject_python_startup_hook(
    relative: Path | str, *, distribution_name: str
) -> None:
    if _is_python_startup_hook(relative):
        raise BundleVerificationError(
            f"refusing controller startup hook while repackaging {distribution_name}: {relative}"
        )


def _controller_record_evidence(
    distribution: metadata.Distribution,
    *,
    metadata_root: Path,
    roots: tuple[Path, ...],
) -> dict[str, Any]:
    """Validate an installed RECORD when available, without claiming trust.

    Old egg-style installations often have no hashed RECORD.  They can still
    supply an offline compatibility wheel, but the receipt explicitly labels
    their controller origin untrusted rather than treating it as provenance.
    """

    record_path = metadata_root / "RECORD"
    baseline = {
        "origin": "controller_site_packages_repack",
        "metadata_root_sha256": _sha256_bytes(str(metadata_root).encode("utf-8")),
    }
    if not record_path.is_file():
        return {
            **baseline,
            "controller_record_status": "UNTRUSTED_CONTROLLER_REPACK",
            "controller_record_reason": "MISSING_RECORD",
        }
    try:
        raw_record = record_path.read_bytes()
        rows = list(csv.reader(io.StringIO(raw_record.decode("utf-8"))))
        seen: set[str] = set()
        for row in rows:
            if len(row) != 3 or not row[0] or row[0] in seen:
                raise ValueError("malformed or duplicate RECORD row")
            seen.add(row[0])
            relative = _safe_relative_path(row[0], context="controller RECORD")
            _reject_python_startup_hook(
                relative,
                distribution_name=str(distribution.metadata.get("Name") or "unknown"),
            )
            source = Path(str(distribution.locate_file(relative)))
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"missing or symlinked RECORD member: {relative}")
            resolved = source.resolve()
            if not any(resolved.is_relative_to(root) for root in roots):
                raise ValueError(f"RECORD member escapes site-packages: {relative}")
            if row[0].endswith(".dist-info/RECORD"):
                if row[1] or row[2]:
                    raise ValueError("RECORD hashes itself")
                continue
            if not row[1].startswith("sha256="):
                raise ValueError(f"RECORD member is not SHA-256: {relative}")
            encoded = row[1].split("=", 1)[1]
            expected = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            actual = hashlib.sha256(source.read_bytes()).digest()
            if actual != expected or row[2] != str(source.stat().st_size):
                raise ValueError(f"RECORD integrity mismatch: {relative}")
    except BundleVerificationError as error:
        if "startup hook" in str(error):
            raise
        return {
            **baseline,
            "controller_record_status": "UNTRUSTED_CONTROLLER_REPACK",
            "controller_record_reason": type(error).__name__,
            "controller_record_sha256": _sha256_file(record_path),
        }
    except (
        OSError,
        UnicodeDecodeError,
        csv.Error,
        binascii.Error,
        ValueError,
    ) as error:
        return {
            **baseline,
            "controller_record_status": "UNTRUSTED_CONTROLLER_REPACK",
            "controller_record_reason": type(error).__name__,
            "controller_record_sha256": _sha256_file(record_path),
        }
    return {
        **baseline,
        "controller_record_status": "VALIDATED_CONTROLLER_RECORD",
        "controller_record_sha256": _sha256_bytes(raw_record),
    }


def _assert_not_editable_direct_url(direct_url: Path, *, context: str) -> None:
    """Allow a wheel direct URL only when it is not an editable projection."""

    if not direct_url.is_file():
        return
    if direct_url.is_symlink():
        raise BundleVerificationError(f"{context} direct_url metadata is symlinked")
    try:
        payload = json.loads(direct_url.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleVerificationError(
            f"cannot safely interpret {context} direct_url metadata"
        ) from error
    if not isinstance(payload, dict):
        raise BundleVerificationError(f"{context} direct_url metadata is not an object")
    directory_info = payload.get("dir_info")
    if isinstance(directory_info, dict) and directory_info.get("editable") is True:
        raise BundleVerificationError(f"refusing editable {context} distribution")


def _assert_not_editable_controller_distribution(
    metadata_root: Path, name: str
) -> None:
    """Reject editable controller projections even if their .pth is absent from RECORD."""

    _assert_not_editable_direct_url(
        metadata_root / "direct_url.json", context=f"controller {name}"
    )


def _installed_distributions() -> dict[str, metadata.Distribution]:
    roots = _site_roots()
    distributions: dict[str, metadata.Distribution] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        metadata_file = next(
            (
                item
                for item in distribution.files or []
                if str(item).endswith(".dist-info/METADATA")
            ),
            None,
        )
        if metadata_file is None:
            continue
        metadata_path = Path(str(distribution.locate_file(metadata_file))).resolve()
        if not any(metadata_path.is_relative_to(root) for root in roots):
            continue
        key = canonicalize_name(name)
        current = distributions.get(key)
        if current is None or Version(distribution.version) > Version(current.version):
            distributions[key] = distribution
    return distributions


def _repackage_distribution(
    distribution: metadata.Distribution, wheelhouse: Path
) -> tuple[Path, dict[str, Any]]:
    """Create a local wheel from an already-installed runtime distribution."""

    wheelhouse.mkdir(parents=True, exist_ok=True)
    name = distribution.metadata.get("Name")
    if not name:
        raise BundleVerificationError("installed dependency has no distribution name")
    roots = _site_roots()
    metadata_root = Path(getattr(distribution, "_path", "")).resolve()
    if not metadata_root.is_dir() or not any(
        metadata_root.is_relative_to(root) for root in roots
    ):
        raise BundleVerificationError(
            f"installed dependency {name} has no local metadata directory"
        )
    metadata_source = metadata_root / "METADATA"
    if not metadata_source.is_file():
        metadata_source = metadata_root / "PKG-INFO"
    if not metadata_source.is_file():
        raise BundleVerificationError(
            f"installed dependency {name} has no METADATA or PKG-INFO"
        )
    if metadata_source.is_symlink():
        raise BundleVerificationError(
            f"installed dependency {name} has symlinked metadata"
        )
    _assert_not_editable_controller_distribution(metadata_root, name)
    controller_record = _controller_record_evidence(
        distribution,
        metadata_root=metadata_root,
        roots=roots,
    )
    if distribution.files is None:
        raise BundleVerificationError(
            f"installed dependency {name} has no file manifest"
        )
    stage_parent = Path(
        tempfile.mkdtemp(prefix="iteration41-repack-", dir=wheelhouse.parent)
    )
    try:
        stage = stage_parent / "wheel"
        stage.mkdir()
        # A controller RECORD is an input to an offline compatibility build,
        # not an authority to copy arbitrary controller files.  We copy only
        # regular, non-linked files whose resolved location and derived wheel
        # path both remain below a controller site-packages root.  Every other
        # non-hook member is omitted and counted by category; the receipt then
        # calls this controller reconstruction untrusted.  This lets stale
        # headers, data and console wrappers stay out of the wheelhouse
        # without allowing an ambient controller path to enter a wheel.
        skipped_controller_member_categories: dict[str, int] = {}

        def skip_controller_member(category: str) -> None:
            skipped_controller_member_categories[category] = (
                skipped_controller_member_categories.get(category, 0) + 1
            )

        for item in distribution.files:
            relative_text = str(item)
            # Check the original spelling first.  In particular this catches
            # Windows-normalized ``evil.pth `` / ``sitecustomize.py.`` names
            # before an unsafe/external RECORD entry can be skipped below.
            _reject_python_startup_hook(relative_text, distribution_name=name)
            if _is_pip_generated_external_script_member(relative_text):
                # Installed distributions commonly list generated console
                # scripts using ../../Scripts/<name>.  They are not package
                # payload and must never be copied out of controller
                # site-packages.  The origin RECORD evidence is separately
                # labelled untrusted for this case.
                skip_controller_member("pip_generated_console_script")
                continue
            try:
                relative = _safe_relative_path(
                    relative_text, context=f"installed dependency {name}"
                )
            except BundleVerificationError:
                # The raw hook check above is intentionally before this
                # branch, so a trailing-dot/space .pth cannot become a
                # silently skipped "unsafe" member.
                skip_controller_member("unsafe_relative_path")
                continue
            _reject_python_startup_hook(relative, distribution_name=name)
            if "__pycache__" in item.parts or relative_text.endswith((".pyc", ".pyo")):
                continue
            try:
                located = Path(str(distribution.locate_file(item)))
                located_absolute = located.absolute()
                source = located_absolute.resolve()
            except OSError:
                skip_controller_member("unresolvable_location")
                continue
            site_root = next(
                (root for root in roots if source.is_relative_to(root)), None
            )
            if located.is_symlink() or located_absolute != source:
                skip_controller_member("symlinked_member")
                continue
            if site_root is None:
                skip_controller_member("external_member")
                continue
            if not source.is_file():
                skip_controller_member("missing_or_non_regular_member")
                continue
            try:
                relative = _safe_relative_path(
                    source.relative_to(site_root).as_posix(),
                    context=f"installed dependency {name} resolved member",
                )
            except BundleVerificationError:
                skip_controller_member("unsafe_resolved_relative_path")
                continue
            if source.is_relative_to(metadata_root):
                # Recreate metadata below as a valid dist-info directory even
                # when the local install originated from an old egg-info tree.
                continue
            relative = source.relative_to(site_root)
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        dist_info_name = (
            f"{canonicalize_name(name).replace('-', '_')}-"
            f"{distribution.version.replace('-', '_')}.dist-info"
        )
        staged_metadata = stage / dist_info_name
        staged_metadata.mkdir(parents=True, exist_ok=True)
        shutil.copy2(metadata_source, staged_metadata / "METADATA")
        for source in metadata_root.rglob("*"):
            if source.is_symlink():
                raise BundleVerificationError(
                    f"installed dependency {name} has symlinked metadata: {source.name}"
                )
            if not source.is_file() or source == metadata_source:
                continue
            relative = source.relative_to(metadata_root)
            _reject_python_startup_hook(relative, distribution_name=name)
            safe_relative = _safe_relative_path(
                relative.as_posix(), context=f"installed dependency {name} metadata"
            )
            if safe_relative.name.casefold() in _STRIPPED_CONTROLLER_METADATA:
                continue
            target = staged_metadata / safe_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        wheel_file = staged_metadata / "WHEEL"
        if not wheel_file.is_file():
            binary = any(
                path.suffix.lower() in {".pyd", ".so", ".dylib"}
                for path in stage.rglob("*")
                if path.is_file()
            )
            tag = str(next(sys_tags())) if binary else "py3-none-any"
            wheel_file.write_text(
                "Wheel-Version: 1.0\n"
                "Generator: iteration41 local wheelhouse repack\n"
                f"Root-Is-Purelib: {'false' if binary else 'true'}\n"
                f"Tag: {tag}\n",
                encoding="utf-8",
            )
        tags = [
            line.partition(":")[2].strip()
            for line in wheel_file.read_text(encoding="utf-8").splitlines()
            if line.startswith("Tag:")
        ]
        if not tags:
            raise BundleVerificationError(
                f"installed dependency {name} has no wheel tag"
            )
        python_tags, abi_tags, platform_tags = zip(
            *(tag.split("-", 2) for tag in tags), strict=True
        )
        filename = (
            f"{canonicalize_name(name).replace('-', '_')}-"
            f"{distribution.version.replace('-', '_')}-"
            f"{'.'.join(dict.fromkeys(python_tags))}-"
            f"{'.'.join(dict.fromkeys(abi_tags))}-"
            f"{'.'.join(dict.fromkeys(platform_tags))}.whl"
        )
        target_wheel = wheelhouse / filename
        with WheelFile(str(target_wheel), "w") as archive:
            for source in sorted(stage.rglob("*")):
                if source.is_file() and source.name != "RECORD":
                    archive.write(source, source.relative_to(stage).as_posix())
        _record_contract(target_wheel, name)
        if skipped_controller_member_categories:
            controller_record = {
                **controller_record,
                "controller_record_status": "UNTRUSTED_CONTROLLER_REPACK",
                "controller_record_reason": "UNREPACKAGED_CONTROLLER_RECORD_MEMBERS",
                "skipped_controller_member_categories": dict(
                    sorted(skipped_controller_member_categories.items())
                ),
            }
        return target_wheel, controller_record
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)


def _marker_applies(marker: object, extras: Iterable[str] = ()) -> bool:
    if marker is None:
        return True
    selected = tuple(extras) or ("")
    environment = default_environment()
    return any(marker.evaluate({**environment, "extra": extra}) for extra in selected)


def _requirements_for_wheel(
    wheel: Path, *, extras: Iterable[str] = ()
) -> list[Requirement]:
    _, _, raw_requirements, _ = _wheel_metadata(wheel)
    try:
        parsed = [Requirement(item) for item in raw_requirements]
    except (TypeError, ValueError) as error:
        raise BundleVerificationError(
            f"{wheel.name} contains an invalid Requires-Dist value"
        ) from error
    return [item for item in parsed if _marker_applies(item.marker, extras)]


def _repackage_dependency_closure(
    *,
    local_wheels: Mapping[str, Path],
    roots: Iterable[tuple[Path, tuple[str, ...]]],
    wheelhouse: Path,
) -> dict[str, dict[str, Any]]:
    """Fill a wheelhouse from installed distributions without network resolution."""

    installed = _installed_distributions()
    local_versions = {
        canonicalize_name(name): _wheel_metadata(wheel)[1]
        for name, wheel in local_wheels.items()
    }
    copied: dict[str, dict[str, Any]] = {}
    pending: list[Requirement] = []
    for wheel, extras in roots:
        pending.extend(_requirements_for_wheel(wheel, extras=extras))
    while pending:
        requirement = pending.pop()
        if not _marker_applies(requirement.marker):
            continue
        name = canonicalize_name(requirement.name)
        local_version = local_versions.get(name)
        if local_version is not None:
            if (
                requirement.specifier
                and Version(local_version) not in requirement.specifier
            ):
                raise BundleVerificationError(
                    f"local wheel {name} {local_version} does not satisfy {requirement}"
                )
            continue
        distribution = installed.get(name)
        if distribution is None:
            raise BundleVerificationError(
                f"required local dependency is not installed: {requirement}"
            )
        if (
            requirement.specifier
            and Version(distribution.version) not in requirement.specifier
        ):
            raise BundleVerificationError(
                f"installed dependency {distribution.metadata['Name']} {distribution.version} "
                f"does not satisfy {requirement}"
            )
        if name in copied:
            continue
        wheel, controller_record = _repackage_distribution(distribution, wheelhouse)
        copied[name] = {
            "distribution": str(distribution.metadata["Name"]),
            "version": distribution.version,
            "filename": wheel.name,
            "wheel_sha256": _sha256_file(wheel),
            **controller_record,
        }
        for raw in distribution.requires or []:
            try:
                dependency = Requirement(raw)
            except (TypeError, ValueError) as error:
                raise BundleVerificationError(
                    f"installed dependency {distribution.metadata['Name']} has invalid requirement metadata"
                ) from error
            if _marker_applies(dependency.marker):
                pending.append(dependency)
    return dict(sorted(copied.items()))


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


_CONSUMER_MODULE_BINDINGS = {
    "backtrader": ("backtrader", "backtrader/__init__.py"),
    "backtrader_runtime": ("backtrader", "backtrader_runtime/managed_execution.py"),
    "backtrader_runtime.cli": ("backtrader", "backtrader_runtime/cli.py"),
    "backtrader_runtime.inventory": ("backtrader", "backtrader_runtime/inventory.py"),
    "backtrader_runtime.backtest_fixture": (
        "backtrader",
        "backtrader_runtime/_iteration41_backtest_fixture/run.py",
    ),
    "bt_api_base": ("bt_api_base", "bt_api_base/__init__.py"),
    "bt_api_execution": ("bt_api_execution", "bt_api_execution/__init__.py"),
    "bt_api_gateway": ("bt_api_gateway", "bt_api_gateway/__init__.py"),
    "bt_api_monitor": ("bt_api_monitor", "bt_api_monitor/__init__.py"),
    "bt_api_risk": ("bt_api_risk", "bt_api_risk/__init__.py"),
    "bt_api_transport_zmq": (
        "bt_api_transport_zmq",
        "bt_api_transport_zmq/__init__.py",
    ),
    "bt_api_py": ("bt_api_py", "bt_api_py/__init__.py"),
    "bt_api_py.runtime_plugins": ("bt_api_py", "bt_api_py/runtime_plugins/__init__.py"),
}
_L2_FIXTURE_EXPECTED_STATUSES = {
    "example.013_3.sa_midfreq_simnow.managed_replay_l2": "LOCAL_MANAGED_FAKE_PROVIDER_L2_PASS",
    "example.ctp_options_simnow.mechanical_managed_replay_l2": (
        "LOCAL_CTP_MECHANICAL_MANAGED_FAKE_PROVIDER_L2_PASS"
    ),
}
_LOCAL_BACKTEST_EXPECTED_RUNTIME_ID = "backtrader.iteration41.local_backtest_fixture"
_LOCAL_BACKTEST_EXPECTED_STATUS = "LOCAL_BACKTEST_CEREBRO_PASS"
_PYTHON_SOCKET_GUARD_DESCRIPTION = (
    "python_connection_and_dns_entry_points_blocked_before_package_imports; "
    "no OS firewall claim"
)


def _venv_site_roots(venv_dir: Path) -> tuple[Path, ...]:
    """Find actual site-packages directories without trusting a child report."""

    candidates = [venv_dir / "Lib" / "site-packages"]
    candidates.extend((venv_dir / "lib").glob("python*/site-packages"))
    roots = tuple(sorted({path.resolve() for path in candidates if path.is_dir()}))
    if not roots:
        raise BundleVerificationError(
            f"isolated consumer has no site-packages directory: {venv_dir}"
        )
    return roots


def _wheel_payload_manifest(wheel: Path) -> dict[str, tuple[str, int]]:
    """Return every installable wheel member after rejecting startup metadata."""

    payload: dict[str, tuple[str, int]] = {}
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            name = member.filename
            if _is_python_startup_hook(name):
                raise BundleVerificationError(
                    f"{wheel.name} contains a Python startup hook: {name}"
                )
            _safe_relative_path(name, context=f"{wheel.name} payload member")
            if name.endswith(".dist-info/RECORD"):
                continue
            if _normalized_path_basename(name) == "direct_url.json":
                raise BundleVerificationError(
                    f"{wheel.name} contains direct_url metadata"
                )
            body = archive.read(name)
            payload[name] = (_sha256_bytes(body), len(body))
    if not payload:
        raise BundleVerificationError(f"{wheel.name} has no installable payload")
    return payload


def _installed_payload_path(
    relative: str,
    *,
    roots: tuple[Path, ...],
    expected_sha256: str,
    expected_size: int,
) -> Path:
    safe_relative = _safe_relative_path(
        relative, context="selected wheel installation member"
    )
    candidates = [root / safe_relative for root in roots]
    matches: list[Path] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise BundleVerificationError(
                f"selected wheel installation member is not a regular file: {relative}"
            )
        if (
            candidate.stat().st_size != expected_size
            or _sha256_file(candidate) != expected_sha256
        ):
            raise BundleVerificationError(
                f"selected wheel installation payload mismatch: {relative}"
            )
        matches.append(candidate.resolve())
    if len(matches) != 1:
        raise BundleVerificationError(
            f"selected wheel installation member is missing or ambiguous: {relative}"
        )
    return matches[0]


def _installed_record_rows(
    metadata_root: Path, *, roots: tuple[Path, ...]
) -> dict[str, list[str]]:
    record_path = metadata_root / "RECORD"
    if record_path.is_symlink() or not record_path.is_file():
        raise BundleVerificationError(
            "selected wheel installation has no regular RECORD"
        )
    try:
        rows = list(csv.reader(io.StringIO(record_path.read_text(encoding="utf-8"))))
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise BundleVerificationError(
            "selected wheel installation has an unreadable RECORD"
        ) from error
    by_name: dict[str, list[str]] = {}
    for row in rows:
        if len(row) != 3 or not row[0] or row[0] in by_name:
            raise BundleVerificationError(
                "selected wheel installation has an invalid RECORD row"
            )
        if _is_python_startup_hook(row[0]):
            raise BundleVerificationError(
                "selected wheel installation RECORD lists a startup hook"
            )
        if _is_pip_generated_external_script_member(row[0]):
            # Pip creates console wrappers outside site-packages. They are not
            # wheel payload and cannot establish an import origin; accept only
            # this constrained form after the startup-hook/path checks above.
            continue
        safe_relative = _safe_relative_path(
            row[0], context="selected wheel installation RECORD"
        )
        candidate_paths = [root / safe_relative for root in roots]
        existing = [path for path in candidate_paths if path.exists()]
        if len(existing) != 1 or existing[0].is_symlink() or not existing[0].is_file():
            raise BundleVerificationError(
                "selected wheel installation RECORD member is not local and regular"
            )
        if _normalized_path_basename(row[0]) == "direct_url.json":
            _assert_not_editable_direct_url(
                existing[0], context="selected wheel installation"
            )
        by_name[row[0]] = row
    return by_name


def _validate_selected_wheel_installations(
    *,
    projects: Iterable[ProjectSpec],
    local_wheels: Mapping[str, Path],
    site_roots: tuple[Path, ...],
) -> dict[str, dict[str, Any]]:
    """Bind installed payload bytes and RECORD entries to selected local wheels."""

    bindings: dict[str, dict[str, Any]] = {}
    for project in projects:
        wheel = local_wheels[project.key]
        distribution, version, _, _ = _wheel_metadata(wheel)
        payload = _wheel_payload_manifest(wheel)
        payload_paths = {
            member: _installed_payload_path(
                member,
                roots=site_roots,
                expected_sha256=digest,
                expected_size=size,
            )
            for member, (digest, size) in payload.items()
        }
        metadata_members = [
            member for member in payload_paths if member.endswith(".dist-info/METADATA")
        ]
        if len(metadata_members) != 1:
            raise BundleVerificationError(
                f"selected wheel installation has no unique METADATA member: {project.key}"
            )
        metadata_root = payload_paths[metadata_members[0]].parent
        installed_metadata = BytesParser().parsebytes(
            (metadata_root / "METADATA").read_bytes()
        )
        if (
            canonicalize_name(str(installed_metadata.get("Name") or ""))
            != canonicalize_name(distribution)
            or installed_metadata.get("Version") != version
        ):
            raise BundleVerificationError(
                f"selected wheel installation metadata mismatch: {project.key}"
            )
        record_rows = _installed_record_rows(metadata_root, roots=site_roots)
        for member, (digest, size) in payload.items():
            row = record_rows.get(member)
            if row is None or not row[1].startswith("sha256=") or row[2] != str(size):
                raise BundleVerificationError(
                    f"selected wheel installation RECORD does not bind payload: {project.key}/{member}"
                )
            encoded = row[1].split("=", 1)[1]
            try:
                recorded_digest = base64.urlsafe_b64decode(
                    encoded + "=" * (-len(encoded) % 4)
                ).hex()
            except (binascii.Error, ValueError, TypeError) as error:
                raise BundleVerificationError(
                    f"selected wheel installation RECORD digest is invalid: {project.key}/{member}"
                ) from error
            if recorded_digest != digest:
                raise BundleVerificationError(
                    f"selected wheel installation RECORD digest mismatch: {project.key}/{member}"
                )
        manifest_material = [
            {"member": member, "sha256": digest, "size": size}
            for member, (digest, size) in sorted(payload.items())
        ]
        bindings[project.key] = {
            "distribution": distribution,
            "version": version,
            "wheel_sha256": _sha256_file(wheel),
            "metadata_path": str(metadata_root),
            "payload_paths": {
                member: str(path) for member, path in payload_paths.items()
            },
            "payload_member_count": len(payload),
            "payload_manifest_sha256": _sha256_bytes(
                json.dumps(
                    manifest_material, ensure_ascii=True, separators=(",", ":")
                ).encode("utf-8")
            ),
            "installed_record_validated": True,
        }
    return bindings


def _validated_site_path(
    raw_path: object,
    *,
    roots: tuple[Path, ...],
    label: str,
    expect_directory: bool = False,
) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise BundleVerificationError(f"isolated consumer {label} is missing")
    path = Path(raw_path)
    expected_kind = path.is_dir if expect_directory else path.is_file
    if not path.is_absolute() or path.is_symlink() or not expected_kind():
        kind = "directory" if expect_directory else "file"
        raise BundleVerificationError(
            f"isolated consumer {label} is not a regular absolute {kind}"
        )
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root) for root in roots):
        raise BundleVerificationError(
            f"isolated consumer {label} escapes its site-packages roots"
        )
    return resolved


def _validate_consumer_probe_payload(
    payload: Mapping[str, Any],
    *,
    expected_projects: Mapping[str, Mapping[str, str]],
    expected_modules: Mapping[str, tuple[str, str]],
    installation_bindings: Mapping[str, Mapping[str, Any]],
    site_roots: tuple[Path, ...],
) -> dict[str, Any]:
    """Fail closed on the exact isolated-consumer result contract.

    The child probe is useful execution evidence, but its JSON is not trusted
    by itself.  The controller binds every reported local distribution to the
    wheel it selected and independently verifies each reported path below the
    new venv's site-packages directories.
    """

    required_keys = {
        "module_paths",
        "package_versions",
        "installed_local_projects",
        "fake_provider_calls",
        "execution_state",
        "local_backtest_report",
        "l2_fixture_reports",
        "network_guard",
        "network_guard_attempts",
        "controller_environment_sentinel_absent",
        "provider_evidence",
    }
    if set(payload) != required_keys:
        raise BundleVerificationError(
            "isolated consumer probe has an unexpected result schema"
        )
    module_paths = payload["module_paths"]
    if not isinstance(module_paths, Mapping) or set(module_paths) != set(
        expected_modules
    ):
        raise BundleVerificationError(
            "isolated consumer probe module-path set is incomplete"
        )
    for key, (project_key, payload_member) in expected_modules.items():
        resolved_module_path = _validated_site_path(
            module_paths[key], roots=site_roots, label=f"module path {key}"
        )
        expected_module_path = Path(
            installation_bindings[project_key]["payload_paths"][payload_member]
        )
        if resolved_module_path != expected_module_path:
            raise BundleVerificationError(
                f"isolated consumer module binding mismatch: {key}"
            )

    installed_projects = payload["installed_local_projects"]
    if not isinstance(installed_projects, Mapping) or set(installed_projects) != set(
        expected_projects
    ):
        raise BundleVerificationError(
            "isolated consumer project binding set is incomplete"
        )
    expected_versions: dict[str, str] = {}
    for key, expected in expected_projects.items():
        observed = installed_projects[key]
        if not isinstance(observed, Mapping):
            raise BundleVerificationError(
                f"isolated consumer project binding is invalid: {key}"
            )
        required_project_keys = {
            "distribution",
            "version",
            "wheel_sha256",
            "metadata_path",
        }
        if set(observed) != required_project_keys:
            raise BundleVerificationError(
                f"isolated consumer project binding schema is invalid: {key}"
            )
        distribution = expected["distribution"]
        version = expected["version"]
        wheel_sha256 = expected["wheel_sha256"]
        if (
            observed["distribution"] != distribution
            or observed["version"] != version
            or observed["wheel_sha256"] != wheel_sha256
        ):
            raise BundleVerificationError(
                f"isolated consumer wheel binding mismatch: {key}"
            )
        metadata_path = _validated_site_path(
            observed["metadata_path"],
            roots=site_roots,
            label=f"metadata path {key}",
            expect_directory=True,
        )
        expected_metadata_path = Path(installation_bindings[key]["metadata_path"])
        if metadata_path != expected_metadata_path:
            raise BundleVerificationError(
                f"isolated consumer metadata path mismatch: {key}"
            )
        metadata_payload = BytesParser().parsebytes(
            (metadata_path / "METADATA").read_bytes()
        )
        if (
            canonicalize_name(str(metadata_payload.get("Name") or ""))
            != canonicalize_name(distribution)
            or metadata_payload.get("Version") != version
        ):
            raise BundleVerificationError(
                f"isolated consumer metadata binding mismatch: {key}"
            )
        expected_versions[distribution] = version

    package_versions = payload["package_versions"]
    if (
        not isinstance(package_versions, Mapping)
        or dict(package_versions) != expected_versions
    ):
        raise BundleVerificationError(
            "isolated consumer package-version binding mismatch"
        )
    if (
        type(payload["fake_provider_calls"]) is not int
        or payload["fake_provider_calls"] != 1
    ):
        raise BundleVerificationError(
            "isolated consumer fake provider was not called exactly once"
        )
    if payload["execution_state"] != "ACKED":
        raise BundleVerificationError("isolated consumer did not reach ACKED state")
    local_backtest_report = payload["local_backtest_report"]
    required_local_backtest_keys = {
        "status",
        "external_network_requests",
        "external_write_requests",
        "actual_fills",
        "provider_submissions",
        "actual_pnl",
        "pnl_source",
        "data_bars",
        "network_guard_attempts",
        "runtime_config",
    }
    if not isinstance(local_backtest_report, Mapping) or set(local_backtest_report) != (
        required_local_backtest_keys
    ):
        raise BundleVerificationError("isolated consumer local-backtest report schema is invalid")
    if local_backtest_report["status"] != _LOCAL_BACKTEST_EXPECTED_STATUS:
        raise BundleVerificationError("isolated consumer local backtest did not complete")
    for field_name in (
        "external_network_requests",
        "external_write_requests",
        "actual_fills",
        "provider_submissions",
    ):
        if type(local_backtest_report[field_name]) is not int or local_backtest_report[field_name] != 0:
            raise BundleVerificationError(
                f"isolated consumer local backtest has non-local {field_name}"
            )
    if (
        local_backtest_report["actual_pnl"] != "NOT_APPLICABLE"
        or local_backtest_report["pnl_source"] != "local_backtest_no_orders"
        or type(local_backtest_report["data_bars"]) is not int
        or local_backtest_report["data_bars"] != 4
        or local_backtest_report["network_guard_attempts"] != []
    ):
        raise BundleVerificationError("isolated consumer local-backtest result is not the reviewed fixture")
    expected_local_runtime_config = {
        "strategy_id": _LOCAL_BACKTEST_EXPECTED_RUNTIME_ID,
        "mode": "backtest",
        "preset": "local_backtest",
        "environment": "local",
        "allows_network": False,
        "allows_external_writes": False,
        "allows_production_writes": False,
    }
    if (
        not isinstance(local_backtest_report["runtime_config"], Mapping)
        or dict(local_backtest_report["runtime_config"]) != expected_local_runtime_config
    ):
        raise BundleVerificationError(
            "isolated consumer local-backtest configuration is not the reviewed offline shape"
        )
    l2_fixture_reports = payload["l2_fixture_reports"]
    if not isinstance(l2_fixture_reports, Mapping) or set(l2_fixture_reports) != set(
        _L2_FIXTURE_EXPECTED_STATUSES
    ):
        raise BundleVerificationError("isolated consumer L2 fixture report set is incomplete")
    required_l2_report_keys = {
        "status",
        "external_network_requests",
        "external_write_requests",
        "actual_fills",
        "provider_submissions",
    }
    for runtime_id, expected_status in _L2_FIXTURE_EXPECTED_STATUSES.items():
        report = l2_fixture_reports[runtime_id]
        if not isinstance(report, Mapping) or set(report) != required_l2_report_keys:
            raise BundleVerificationError(
                f"isolated consumer L2 fixture report schema is invalid: {runtime_id}"
            )
        if report["status"] != expected_status:
            raise BundleVerificationError(
                f"isolated consumer L2 fixture did not reach its expected status: {runtime_id}"
            )
        for field_name in (
            "external_network_requests",
            "external_write_requests",
            "actual_fills",
        ):
            if type(report[field_name]) is not int or report[field_name] != 0:
                raise BundleVerificationError(
                    f"isolated consumer L2 fixture has non-local {field_name}: {runtime_id}"
                )
        if type(report["provider_submissions"]) is not int or report["provider_submissions"] < 1:
            raise BundleVerificationError(
                f"isolated consumer L2 fixture did not exercise its fake provider: {runtime_id}"
            )
    if payload["provider_evidence"] != "fixture_only":
        raise BundleVerificationError(
            "isolated consumer provider evidence is not fixture-only"
        )
    if payload["controller_environment_sentinel_absent"] is not True:
        raise BundleVerificationError(
            "isolated consumer controller-environment sentinel was not absent"
        )
    if payload["network_guard"] != _PYTHON_SOCKET_GUARD_DESCRIPTION:
        raise BundleVerificationError(
            "isolated consumer Python socket guard is not the expected contract"
        )
    if not isinstance(payload["network_guard_attempts"], list) or payload[
        "network_guard_attempts"
    ]:
        raise BundleVerificationError(
            "isolated consumer attempted a guarded Python connection or DNS entry point"
        )
    return dict(payload)


def _parse_consumer_probe_payload(output: str) -> dict[str, Any]:
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError) as error:
        raise BundleVerificationError(
            "isolated consumer probe did not emit JSON"
        ) from error
    if not isinstance(payload, dict):
        raise BundleVerificationError("isolated consumer probe JSON must be an object")
    return payload


_CONSUMER_PROBE = r"""
import io
import importlib.metadata as metadata
import json
import os
from decimal import Decimal
from pathlib import Path

import socket


network_guard_attempts = []


def _blocked_python_network(label):
    def blocked(*args, **kwargs):
        network_guard_attempts.append(label)
        raise AssertionError(f"Python network entry point is forbidden in the isolated consumer probe: {label}")

    return blocked


_original_socket = socket.socket


class _BlockedSocket(_original_socket):
    def connect(self, *args, **kwargs):
        return _blocked_python_network("socket.connect")(*args, **kwargs)

    def connect_ex(self, *args, **kwargs):
        return _blocked_python_network("socket.connect_ex")(*args, **kwargs)


socket.socket = _BlockedSocket
for _attribute in ("create_connection", "create_server", "fromfd", "socketpair", "getaddrinfo"):
    if hasattr(socket, _attribute):
        setattr(socket, _attribute, _blocked_python_network(f"socket.{_attribute}"))
for _attribute in ("gethostbyaddr", "gethostbyname", "getnameinfo"):
    if hasattr(socket, _attribute):
        setattr(socket, _attribute, _blocked_python_network(f"socket.{_attribute}"))

if os.environ.get("ITERATION41_CONTROLLER_SECRET_SENTINEL") is not None:
    raise AssertionError("controller environment sentinel leaked into the isolated consumer")
try:
    expected_projects = json.loads(os.environ["ITERATION41_EXPECTED_LOCAL_PROJECTS"])
except (KeyError, TypeError, json.JSONDecodeError) as error:
    raise AssertionError("missing or malformed expected local-project manifest") from error
if not isinstance(expected_projects, dict):
    raise AssertionError("expected local-project manifest must be an object")

import backtrader_runtime.cli as runtime_cli
import backtrader_runtime.inventory as runtime_inventory

expected_backtest_runtime_id = "backtrader.iteration41.local_backtest_fixture"
backtest_registry = runtime_inventory.iteration41_backtest_fixture_registry()
if len(backtest_registry.registrations) != 1:
    raise AssertionError("packaged local-backtest registry must contain exactly one runtime")
backtest_registration = backtest_registry.registrations[0]
if backtest_registration.runtime_id != expected_backtest_runtime_id:
    raise AssertionError("packaged local-backtest registry does not match the reviewed runtime ID")
backtest_stdout = io.StringIO()
backtest_stderr = io.StringIO()
backtest_exit_code = runtime_cli.main(
    ["run", "--strategy-dir", str(backtest_registration.runtime_dir), "--full-report"],
    registry=backtest_registry,
    stdout=backtest_stdout,
    stderr=backtest_stderr,
)
if backtest_exit_code != 0:
    raise AssertionError(
        "packaged local-backtest fixture dispatch failed: " + backtest_stderr.getvalue().strip()
    )
try:
    local_backtest_report = json.loads(backtest_stdout.getvalue())["report"]["result"]
except (KeyError, TypeError, json.JSONDecodeError) as error:
    raise AssertionError("packaged local-backtest fixture did not emit a complete report") from error
if local_backtest_report.get("status") != "LOCAL_BACKTEST_CEREBRO_PASS":
    raise AssertionError("packaged local-backtest fixture did not reach its expected status")

expected_l2_statuses = {
    "example.013_3.sa_midfreq_simnow.managed_replay_l2": "LOCAL_MANAGED_FAKE_PROVIDER_L2_PASS",
    "example.ctp_options_simnow.mechanical_managed_replay_l2": "LOCAL_CTP_MECHANICAL_MANAGED_FAKE_PROVIDER_L2_PASS",
}
l2_registry = runtime_inventory.iteration41_l2_fixture_registry()
if {registration.runtime_id for registration in l2_registry.registrations} != set(expected_l2_statuses):
    raise AssertionError("packaged L2 fixture registry does not match the reviewed consumer matrix")
l2_fixture_reports = {}
for registration in l2_registry.registrations:
    l2_stdout = io.StringIO()
    l2_stderr = io.StringIO()
    l2_exit_code = runtime_cli.main(
        ["run", "--strategy-dir", str(registration.runtime_dir), "--full-report"],
        registry=l2_registry,
        stdout=l2_stdout,
        stderr=l2_stderr,
    )
    if l2_exit_code != 0:
        raise AssertionError(
            "packaged L2 fixture dispatch failed: "
            f"{registration.runtime_id}: {l2_stderr.getvalue().strip()}"
        )
    try:
        l2_payload = json.loads(l2_stdout.getvalue())
        l2_report = l2_payload["report"]["result"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise AssertionError(
            f"packaged L2 fixture did not emit a complete report: {registration.runtime_id}"
        ) from error
    if l2_report.get("status") != expected_l2_statuses[registration.runtime_id]:
        raise AssertionError(
            f"packaged L2 fixture did not reach its expected status: {registration.runtime_id}"
        )
    l2_fixture_reports[registration.runtime_id] = {
        "status": l2_report["status"],
        "external_network_requests": l2_report["external_network_requests"],
        "external_write_requests": l2_report["external_write_requests"],
        "actual_fills": l2_report["actual_fills"],
        "provider_submissions": l2_report["provider_submissions"],
    }

import backtrader
import backtrader_runtime._iteration41_backtest_fixture.run as backtest_fixture
import backtrader_runtime.managed_execution as bridge
import bt_api_base
import bt_api_execution
import bt_api_gateway
import bt_api_monitor
import bt_api_risk
import bt_api_transport_zmq
import bt_api_py
import bt_api_py.runtime_plugins as plugins

paths = {
    "backtrader": backtrader.__file__,
    "backtrader_runtime": bridge.__file__,
    "backtrader_runtime.cli": runtime_cli.__file__,
    "backtrader_runtime.inventory": runtime_inventory.__file__,
    "backtrader_runtime.backtest_fixture": backtest_fixture.__file__,
    "bt_api_base": bt_api_base.__file__,
    "bt_api_execution": bt_api_execution.__file__,
    "bt_api_gateway": bt_api_gateway.__file__,
    "bt_api_monitor": bt_api_monitor.__file__,
    "bt_api_risk": bt_api_risk.__file__,
    "bt_api_transport_zmq": bt_api_transport_zmq.__file__,
    "bt_api_py": bt_api_py.__file__,
    "bt_api_py.runtime_plugins": plugins.__file__,
}
for name, value in paths.items():
    resolved = Path(value).resolve()
    if "site-packages" not in {part.lower() for part in resolved.parts}:
        raise AssertionError(f"{name} did not import from site-packages: {resolved}")

observed_projects = {}
for project_key, expected in sorted(expected_projects.items()):
    if not isinstance(expected, dict):
        raise AssertionError(f"expected local-project entry is not an object: {project_key}")
    distribution_name = expected.get("distribution")
    expected_version = expected.get("version")
    expected_wheel_sha256 = expected.get("wheel_sha256")
    if not all(isinstance(value, str) and value for value in (
        distribution_name,
        expected_version,
        expected_wheel_sha256,
    )):
        raise AssertionError(f"expected local-project entry is incomplete: {project_key}")
    observed_version = metadata.version(distribution_name)
    if observed_version != expected_version:
        raise AssertionError(
            f"installed version mismatch for {distribution_name}: {observed_version} != {expected_version}"
        )
    installed_distribution = metadata.distribution(distribution_name)
    installed_metadata = Path(str(getattr(installed_distribution, "_path", ""))).resolve()
    if "site-packages" not in {part.lower() for part in installed_metadata.parts}:
        raise AssertionError(
            f"installed metadata escaped site-packages for {distribution_name}: {installed_metadata}"
        )
    observed_projects[project_key] = {
        "distribution": distribution_name,
        "version": observed_version,
        "wheel_sha256": expected_wheel_sha256,
        "metadata_path": str(installed_metadata),
    }

contract = plugins.RuntimeCapabilityContract(
    strategy_id="iteration41.isolated.consumer",
    mode="live",
    preset="managed_live_direct",
    environment="production",
    order_route="managed_execution",
    required_capabilities=(
        plugins.CAPABILITY_EXECUTION,
        plugins.CAPABILITY_RISK,
        plugins.CAPABILITY_MONITOR,
    ),
    effective_digest="a" * 64,
)
catalog = plugins.CapabilityCatalog(
    (
        plugins.CapabilityPin(
            plugins.CAPABILITY_EXECUTION,
            "bt_api_execution",
            "bt_api_execution",
            metadata.version("bt_api_execution"),
        ),
        plugins.CapabilityPin(
            plugins.CAPABILITY_RISK,
            "bt_api_risk",
            "bt_api_risk",
            metadata.version("bt_api_risk"),
        ),
        plugins.CapabilityPin(
            plugins.CAPABILITY_MONITOR,
            "bt_api_monitor",
            "bt_api_monitor",
            metadata.version("bt_api_monitor"),
        ),
    )
)
snapshot = plugins.SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
    {
        "provider": "fixture_provider",
        "environment": "production",
        "account_ref": "fixture_account",
        "trading_day": "20260922",
        "metadata_version": "fixture-normalized-v1",
        "as_of_ns": 1_000,
        "expires_at_ns": 2_000,
        "account_currency": "USD",
        "instruments": [
            {
                "instrument": "fixture/contract",
                "tick_size": "0.1",
                "lot_size": "1",
                "contract_multiplier": "1",
                "max_gross_notional_account": "1000",
                "quote_currency": "USD",
                "fee_currency": "USD",
                "quote_to_account_fx": "1",
                "fee_to_account_fx": "1",
                "taker_fee_bps": "0",
                "fixed_fee": "0",
                "max_slippage_bps": "0",
                "quantity_unit": "contracts",
            }
        ],
    }
)
runtime = plugins.compose_managed_execution(
    catalog.load(contract),
    state_directory=Path(os.environ["ITERATION41_CONSUMER_STATE"]),
    provider="fixture_provider",
    environment="production",
    account_ref="fixture_account",
    strategy_id=contract.strategy_id,
    writer_id="consumer-writer",
    policy_id="consumer-policy",
    max_increase_notional=Decimal("100"),
    max_increase_count=1,
    trading_day=snapshot.trading_day,
    instrument_metadata_snapshot=snapshot,
    instrument_clock_ns=lambda: 1_500,
)
provider_calls = []
try:
    intent = runtime.execution.OrderIntent.limit(
        intent_id="consumer-intent",
        scope=runtime.scope,
        signal_id="consumer-signal",
        instrument="fixture/contract",
        side=runtime.execution.Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("1"),
        metadata_version=snapshot.metadata_version,
        tags={
            "instrument_metadata_digest": snapshot.instrument_digest("fixture/contract"),
            "quantity_unit": snapshot.instrument_metadata("fixture/contract").quantity_unit,
        },
    )

    def fake_provider(submitted):
        provider_calls.append(submitted.intent_id)
        return runtime.execution.ProviderObservation.accepted(
            submitted.intent_id,
            "fixture-provider-order",
        )

    record = runtime.submit(intent, fake_provider)
    if record.state.value != "ACKED" or provider_calls != ["consumer-intent"]:
        raise AssertionError("local fake-provider composition did not acknowledge exactly once")
finally:
    runtime.close()

print(
    json.dumps(
        {
            "module_paths": {key: str(Path(value).resolve()) for key, value in paths.items()},
            "package_versions": {
                item["distribution"]: item["version"]
                for item in observed_projects.values()
            },
            "installed_local_projects": observed_projects,
            "fake_provider_calls": len(provider_calls),
            "execution_state": "ACKED",
            "local_backtest_report": local_backtest_report,
            "l2_fixture_reports": l2_fixture_reports,
            "network_guard": "python_connection_and_dns_entry_points_blocked_before_package_imports; no OS firewall claim",
            "network_guard_attempts": network_guard_attempts,
            "controller_environment_sentinel_absent": True,
            "provider_evidence": "fixture_only",
        },
        sort_keys=True,
    )
)
"""


def _isolated_consumer(
    *,
    controller_python: str,
    artifacts_dir: Path,
    wheelhouse: Path,
    local_wheels: Mapping[str, Path],
    projects: Iterable[ProjectSpec],
) -> dict[str, Any]:
    """Install from local wheels only and exercise the installed public boundary."""

    logs_dir = artifacts_dir / "logs"
    # Keep the venv outside a potentially long artifact path.  Some valid
    # packages (for example statsmodels fixture data) have deep installed
    # paths that exceed legacy Windows MAX_PATH when nested under a UUID-named
    # receipt directory.  The logs and replayable wheelhouse remain under the
    # requested artifact directory; the disposable venv does not.
    with tempfile.TemporaryDirectory(prefix="i41c-") as temp:
        root = Path(temp)
        venv_dir = root / "venv"
        create = _run_logged(
            [controller_python, "-m", "venv", str(venv_dir)],
            cwd=root,
            environment=_offline_environment(),
            logs_dir=logs_dir,
            name="consumer-venv",
        )
        if create["exit_code"] != 0:
            raise BundleVerificationError(
                "isolated consumer virtualenv creation failed: "
                f"{create['stderr'].strip() or create['stdout'].strip()}"
            )
        python = _venv_python(venv_dir)
        project_list = tuple(projects)
        missing_wheels = [
            project.key for project in project_list if project.key not in local_wheels
        ]
        if missing_wheels:
            raise BundleVerificationError(
                f"isolated consumer is missing local wheels: {', '.join(missing_wheels)}"
            )
        wheels = [local_wheels[project.key] for project in project_list]
        expected_projects = {}
        for project, wheel in zip(project_list, wheels, strict=True):
            distribution, version, _, _ = _wheel_metadata(wheel)
            if canonicalize_name(distribution) != canonicalize_name(
                project.distribution
            ):
                raise BundleVerificationError(
                    f"local wheel binding mismatch for {project.key}: {distribution}"
                )
            expected_projects[project.key] = {
                "distribution": distribution,
                "version": version,
                "wheel_sha256": _sha256_file(wheel),
            }
        install = _run_logged(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--force-reinstall",
                *(str(wheel) for wheel in wheels),
            ],
            cwd=root,
            environment=_offline_environment(),
            logs_dir=logs_dir,
            name="consumer-install",
        )
        if install["exit_code"] != 0:
            raise BundleVerificationError(
                "isolated consumer install failed: "
                f"{install['stderr'].strip() or install['stdout'].strip()}"
            )
        dependency_check = _run_logged(
            [str(python), "-m", "pip", "check"],
            cwd=root,
            environment=_offline_environment(),
            logs_dir=logs_dir,
            name="consumer-pip-check",
        )
        if dependency_check["exit_code"] != 0:
            raise BundleVerificationError(
                "isolated consumer dependency check failed: "
                f"{dependency_check['stderr'].strip() or dependency_check['stdout'].strip()}"
            )
        site_roots = _venv_site_roots(venv_dir)
        installation_bindings_before = _validate_selected_wheel_installations(
            projects=project_list,
            local_wheels=local_wheels,
            site_roots=site_roots,
        )
        state = root / "consumer-state"
        probe = _run_logged(
            [str(python), "-I", "-c", _CONSUMER_PROBE],
            cwd=root,
            environment=_offline_environment(
                {
                    "BT_API_PY_LIGHT_IMPORT": "1",
                    "ITERATION41_CONSUMER_STATE": str(state),
                    "ITERATION41_EXPECTED_LOCAL_PROJECTS": json.dumps(
                        expected_projects,
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                }
            ),
            logs_dir=logs_dir,
            name="consumer-probe",
        )
        if probe["exit_code"] != 0:
            raise BundleVerificationError(
                "isolated consumer probe failed: "
                f"{probe['stderr'].strip() or probe['stdout'].strip()}"
            )
        payload = _parse_consumer_probe_payload(probe["stdout"])
        payload = _validate_consumer_probe_payload(
            payload,
            expected_projects=expected_projects,
            expected_modules=_CONSUMER_MODULE_BINDINGS,
            installation_bindings=installation_bindings_before,
            site_roots=site_roots,
        )
        installation_bindings_after = _validate_selected_wheel_installations(
            projects=project_list,
            local_wheels=local_wheels,
            site_roots=site_roots,
        )
        if installation_bindings_after != installation_bindings_before:
            raise BundleVerificationError(
                "isolated consumer modified a selected local wheel installation"
            )
        return {
            "local_wheelhouse_only": True,
            "isolated_python": str(python),
            "venv": {
                key: value
                for key, value in create.items()
                if key not in {"stdout", "stderr"}
            },
            "install": {
                key: value
                for key, value in install.items()
                if key not in {"stdout", "stderr"}
            },
            "dependency_check": {
                key: value
                for key, value in dependency_check.items()
                if key not in {"stdout", "stderr"}
            },
            "probe": {
                key: value
                for key, value in probe.items()
                if key not in {"stdout", "stderr"}
            },
            "probe_payload": payload,
            "expected_local_projects": expected_projects,
            "local_install_bindings": {
                key: {
                    field: value
                    for field, value in binding.items()
                    if field != "payload_paths"
                }
                for key, binding in installation_bindings_before.items()
            },
        }


def _project_specs(sdk_root: Path, backtrader_root: Path) -> tuple[ProjectSpec, ...]:
    return (
        ProjectSpec(
            "backtrader",
            "backtrader",
            "backtrader",
            backtrader_root,
            ("setup.py", "README.md", "backtrader", "backtrader_runtime"),
        ),
        ProjectSpec(
            "bt_api_py",
            "bt_api_py",
            "bt_api_py",
            sdk_root,
            ("pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "bt_api_py"),
        ),
        ProjectSpec(
            "bt_api_base",
            "bt_api_base",
            "bt_api_base",
            sdk_root / "bt_api" / "bt_api_base",
            ("pyproject.toml", "README.md", "src"),
            "bt_api/bt_api_base",
        ),
        ProjectSpec(
            "bt_api_execution",
            "bt_api_execution",
            "bt_api_execution",
            sdk_root / "bt_api" / "bt_api_execution",
            ("pyproject.toml", "README.md", "src"),
            "bt_api/bt_api_execution",
        ),
        ProjectSpec(
            "bt_api_risk",
            "bt_api_risk",
            "bt_api_risk",
            sdk_root / "bt_api" / "bt_api_risk",
            ("pyproject.toml", "README.md", "src"),
            "bt_api/bt_api_risk",
        ),
        ProjectSpec(
            "bt_api_monitor",
            "bt_api_monitor",
            "bt_api_monitor",
            sdk_root / "bt_api" / "bt_api_monitor",
            ("pyproject.toml", "README.md", "src"),
            "bt_api/bt_api_monitor",
        ),
        ProjectSpec(
            "bt_api_gateway",
            "bt_api_gateway",
            "bt_api_gateway",
            sdk_root / "bt_api" / "bt_api_gateway",
            ("pyproject.toml", "README.md", "src"),
            "bt_api/bt_api_gateway",
        ),
        ProjectSpec(
            "bt_api_transport_zmq",
            "bt_api_transport_zmq",
            "bt_api_transport_zmq",
            sdk_root / "bt_api" / "bt_api_transport_zmq",
            ("pyproject.toml", "README.md", "src"),
            "bt_api/bt_api_transport_zmq",
        ),
    )


def _release_limitations(
    source_states: Mapping[str, Mapping[str, Any]],
    dependency_wheels: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    limitations = [
        "LOCAL_ONLY_VERIFIER_DOES_NOT_ESTABLISH_REVIEWED_RELEASE_OR_PUBLICATION",
        "LOCAL_ONLY_VERIFIER_DOES_NOT_ESTABLISH_SIGNATURE_OR_TRUSTED_ARTIFACT_PROVENANCE",
        "LOCAL_GIT_STATE_IS_NOT_FINAL_COMMIT_OR_RELEASE_PROVENANCE",
        "LOCAL_ONLY_VERIFIER_DOES_NOT_ESTABLISH_PROVIDER_ACCOUNT_OR_LIVE_TRADING_ADMISSION",
    ]
    for key, state in source_states.items():
        source_state = str(state.get("source_state") or "")
        if source_state != "CLEAN_GITLINK_SOURCE":
            limitations.append(f"SOURCE_STATE_{key.upper()}_{source_state}")
    for key, evidence in dependency_wheels.items():
        if evidence.get("controller_record_status") != "VALIDATED_CONTROLLER_RECORD":
            limitations.append(f"DEPENDENCY_{key.upper()}_CONTROLLER_RECORD_UNTRUSTED")
    return limitations


def _wheelhouse_manifest(wheelhouse: Path) -> dict[str, Any]:
    """Return a deterministic digest of every locally usable wheel artifact."""

    files = [
        {"filename": path.name, "sha256": _sha256_file(path)}
        for path in sorted(
            wheelhouse.glob("*.whl"), key=lambda item: item.name.casefold()
        )
    ]
    encoded = json.dumps(
        files, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {"wheel_count": len(files), "sha256": _sha256_bytes(encoded), "files": files}


def _write_receipt_atomically(path: Path, receipt: Mapping[str, Any]) -> None:
    """Create one complete receipt without overwriting a concurrent writer."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # A hard-link creation is atomic and fails if another process has
            # already created the final name.  ``Path.replace`` would silently
            # overwrite a concurrent receipt, which invalidates evidence.
            os.link(temporary_path, path)
        except FileExistsError:
            raise
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _write_unique_failure_receipt(artifacts: Path, receipt: Mapping[str, Any]) -> Path:
    """Create a no-clobber sidecar when the requested artifact path is busy."""

    for attempt in range(1, 10_001):
        suffix = "" if attempt == 1 else f".{attempt}"
        candidate = artifacts.parent / f"{artifacts.name}.failure-receipt{suffix}.json"
        try:
            _write_receipt_atomically(candidate, receipt)
        except FileExistsError:
            continue
        return candidate
    raise BundleVerificationError("could not allocate a unique failure receipt path")


def verify(
    *,
    artifacts_dir: Path,
    backtrader_root: Path,
    sdk_root: Path = SDK_ROOT,
    controller_python: str = sys.executable,
) -> dict[str, Any]:
    """Build and consume the complete local capability matrix.

    Raises :class:`BundleVerificationError` on a missing dependency, failed
    build, non-reproducible wheel, failed installation, or consumer failure.
    """

    artifacts_dir = artifacts_dir.resolve(strict=False)
    if artifacts_dir.exists():
        if any(artifacts_dir.iterdir()):
            raise BundleVerificationError(
                f"artifacts directory must be empty: {artifacts_dir}"
            )
    else:
        artifacts_dir.mkdir(parents=True, exist_ok=False)
    sdk_root = sdk_root.resolve(strict=True)
    backtrader_root = backtrader_root.resolve(strict=True)
    specs = _project_specs(sdk_root, backtrader_root)
    wheelhouse = artifacts_dir / "wheelhouse"
    logs_dir = artifacts_dir / "logs"
    wheels: dict[str, Path] = {}
    wheel_receipts: dict[str, dict[str, Any]] = {}
    # As with the installed-consumer venv, stage builds in the system temp
    # directory rather than below an arbitrarily deep artifact path.  The
    # Backtrader source contains legitimate long indicator filenames that can
    # exceed legacy Windows MAX_PATH when a pytest tmp directory is nested.
    # Wheel/log outputs still remain in ``artifacts_dir``.
    with tempfile.TemporaryDirectory(prefix="i41b-") as temp:
        work_dir = Path(temp)
        frozen_root = work_dir / "frozen-sources"
        frozen_root.mkdir()
        snapshots = {
            project.key: _capture_source_snapshot(project, frozen_root / project.key)
            for project in specs
        }
        controller_requirements = {
            project.key: _controller_requirement_receipt(
                project,
                source_root=snapshots[project.key].staged_root,
            )
            for project in specs
        }
        for project in specs:
            wheel, receipt = _build_reproducible_wheel(
                snapshots[project.key],
                controller_python=controller_python,
                work_dir=work_dir,
                wheelhouse=wheelhouse,
                logs_dir=logs_dir,
            )
            wheels[project.key] = wheel
            wheel_receipts[project.key] = receipt
        dependency_wheels = _repackage_dependency_closure(
            local_wheels={
                project.distribution: wheels[project.key] for project in specs
            },
            roots=[(wheel, ()) for wheel in wheels.values()],
            wheelhouse=wheelhouse,
        )
        # A project captured early must also remain unchanged while later
        # projects and the controller dependency closure are built.
        for snapshot in snapshots.values():
            _recheck_source_snapshot(snapshot)
    consumer = _isolated_consumer(
        controller_python=controller_python,
        artifacts_dir=artifacts_dir,
        wheelhouse=wheelhouse,
        local_wheels=wheels,
        projects=specs,
    )
    for snapshot in snapshots.values():
        _recheck_source_snapshot(snapshot)
    # Capture Git facts only after the final source-byte recheck.  These remain
    # local observations, never a reviewed/published provenance assertion.
    source_states = {
        project.key: _git_source_state(project, sdk_root) for project in specs
    }
    receipt = {
        "schema_version": 2,
        "generated_at": _utc_now(),
        "purpose": "local-only Iteration 41 wheel-install and isolated-consumer mechanics",
        "result": LOCAL_ONLY_RESULT,
        "local_validation": "PASSED",
        "release_status": "NOT_RELEASE_ELIGIBLE",
        "controller_python": controller_python,
        "offline_constraints": {
            "pip_no_index": True,
            "pip_config_file": os.devnull,
            "source_date_epoch": SOURCE_DATE_EPOCH,
            "consumer_python_isolated": True,
            "consumer_inherited_pythonpath": False,
            "minimal_child_environment": True,
            "controller_environment_redaction_sentinel": _REDACTION_SENTINEL_ENV,
            "consumer_python_socket_guard": True,
            "os_network_firewall_verified": False,
        },
        "source_states": source_states,
        "source_snapshots": {
            key: {
                "sha256": snapshot.digest,
                "file_count": len(snapshot.files),
                "captured_from_bytes": True,
                "original_source_recheck": "PASSED_BEFORE_AND_AFTER_BUILD",
            }
            for key, snapshot in snapshots.items()
        },
        "controller_build_requirements": controller_requirements,
        "local_wheels": wheel_receipts,
        "repackaged_dependencies": dependency_wheels,
        "wheelhouse_manifest": _wheelhouse_manifest(wheelhouse),
        "consumer": consumer,
        "limitations": _release_limitations(source_states, dependency_wheels),
    }
    _write_receipt_atomically(artifacts_dir / "receipt.json", receipt)
    return receipt


def _failed_receipt(
    error: BaseException, *, requested_artifacts_dir: Path
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "generated_at": _utc_now(),
        "purpose": "local-only Iteration 41 wheel-install and isolated-consumer mechanics",
        "result": FAILED_RESULT,
        "local_validation": "FAILED",
        "release_status": "NOT_RELEASE_ELIGIBLE",
        "error": _redact_text(f"{type(error).__name__}: {error}"),
        "requested_artifacts_dir": str(requested_artifacts_dir),
        "limitations": [
            "FAILED_LOCAL_VALIDATION_IS_NOT_A_RELEASE_OR_PROVIDER_ADMISSION_DECISION"
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--backtrader-root", type=Path, required=True)
    parser.add_argument("--sdk-root", type=Path, default=SDK_ROOT)
    parser.add_argument("--python", default=sys.executable)
    arguments = parser.parse_args(argv)
    artifacts = arguments.artifacts_dir.resolve(strict=False)
    artifacts_initially_reusable = not artifacts.exists() or (
        artifacts.is_dir() and not any(artifacts.iterdir())
    )
    try:
        receipt = verify(
            artifacts_dir=arguments.artifacts_dir,
            backtrader_root=arguments.backtrader_root,
            sdk_root=arguments.sdk_root,
            controller_python=arguments.python,
        )
    except (
        Exception
    ) as error:  # A parser or unexpected local failure must remain non-PASS.
        receipt = _failed_receipt(error, requested_artifacts_dir=artifacts)
        if artifacts_initially_reusable:
            artifacts.mkdir(parents=True, exist_ok=True)
            receipt_path = artifacts / "receipt.json"
            try:
                _write_receipt_atomically(receipt_path, receipt)
            except FileExistsError:
                _write_unique_failure_receipt(artifacts, receipt)
        else:
            # Never overwrite an operator's nonempty evidence directory.  Put
            # this invocation's atomic failure receipt beside it instead.
            _write_unique_failure_receipt(artifacts, receipt)
        print(receipt["error"], file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"result": receipt["result"], "local_validation": "PASSED"}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
