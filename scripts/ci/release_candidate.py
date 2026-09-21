#!/usr/bin/env python3
"""Record and verify the exact artifacts promoted through the release workflow."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = 1
MANIFEST_FILENAME = "release-candidate.json"
SUMS_FILENAME = "SHA256SUMS.txt"
RECEIPT_FILENAME = "wheel-contract-receipt.json"
SOURCE_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
VERSION_RE = re.compile(
    r"(?:(?:0|[1-9][0-9]*)!)?(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*"
    r"(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?"
    r"(?:\+[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*)?\Z",
    re.IGNORECASE,
)
WHEEL_TAG_RE = re.compile(r"[A-Za-z0-9_.]+\Z")
WHEEL_BUILD_RE = re.compile(r"[0-9][A-Za-z0-9_]*\Z")
SDIST_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".zip")


class CandidateError(ValueError):
    """Raised when a candidate artifact violates the release identity contract."""


def _fail(message: str) -> None:
    raise CandidateError(message)


def _safe_basename(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        _fail(f"{label} must be a non-empty basename")
    if "/" in value or "\\" in value or Path(value).name != value:
        _fail(f"{label} must not contain a path")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail(f"{label} contains a control character")
    return value


def _validate_source_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SOURCE_SHA_RE.fullmatch(value) is None:
        _fail(f"{label} must be a full 40-character lowercase hexadecimal SHA")
    return value


def _validate_version(value: Any, label: str) -> str:
    if not isinstance(value, str) or VERSION_RE.fullmatch(value) is None:
        _fail(f"{label} must be a canonical PEP 440 version")
    return value


def _resolved_directory(path: Path, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        _fail(f"{label} is unavailable: {exc}")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        _fail(f"{label} must be a non-symlink directory")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        _fail(f"{label} cannot be resolved: {exc}")


def _regular_file(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        _fail(f"{label} is unavailable: {exc}")
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        _fail(f"{label} must be a regular non-symlink file")


def _directory_files(directory: Path, label: str) -> dict[str, Path]:
    resolved = _resolved_directory(directory, label)
    files: dict[str, Path] = {}
    try:
        entries = sorted(resolved.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        _fail(f"cannot list {label}: {exc}")
    for entry in entries:
        basename = _safe_basename(entry.name, f"{label} entry name")
        _regular_file(entry, f"{label}/{basename}")
        files[basename] = entry
    return files


def _open_regular(path: Path, label: str, mode: str = "rb") -> Any:
    try:
        path_mode = path.lstat().st_mode
    except FileNotFoundError:
        if "w" not in mode and "a" not in mode:
            _fail(f"{label} is unavailable")
        _resolved_directory(path.parent, f"{label} parent directory")
    except OSError as exc:
        _fail(f"{label} is unavailable: {exc}")
    else:
        if stat.S_ISLNK(path_mode) or not stat.S_ISREG(path_mode):
            _fail(f"{label} must be a regular non-symlink file")
    flags = os.O_RDONLY
    if "w" in mode:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if "a" in mode:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        _fail(f"cannot open {label}: {exc}")
    opened_mode = os.fstat(descriptor).st_mode
    if not stat.S_ISREG(opened_mode):
        os.close(descriptor)
        _fail(f"{label} must be a regular file")
    return os.fdopen(descriptor, mode)


def _file_digest(path: Path, label: str) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with _open_regular(path, label) as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _validate_wheel_filename(filename: str, version: str) -> None:
    basename = _safe_basename(filename, "wheel basename")
    if not basename.endswith(".whl"):
        _fail(f"not a wheel filename: {basename}")
    components = basename[:-4].split("-")
    if len(components) not in {5, 6}:
        _fail(f"invalid wheel filename shape: {basename}")
    if components[0] != "bt_api_py" or components[1] != version:
        _fail(f"wheel distribution/version mismatch: {basename}")
    tags = components[2:]
    if len(tags) == 4:
        if WHEEL_BUILD_RE.fullmatch(tags[0]) is None:
            _fail(f"invalid wheel build tag: {basename}")
        tags = tags[1:]
    if any(WHEEL_TAG_RE.fullmatch(tag) is None for tag in tags):
        _fail(f"invalid wheel compatibility tag: {basename}")


def _validate_sdist_filename(filename: str, version: str) -> None:
    basename = _safe_basename(filename, "sdist basename")
    prefix = f"bt_api_py-{version}"
    if not any(basename == f"{prefix}{suffix}" for suffix in SDIST_SUFFIXES):
        _fail(f"sdist distribution/version mismatch: {basename}")


def _validate_expected_wheel_identity(filename: Any, digest: Any, version: str) -> tuple[str, str]:
    wheel_filename = _safe_basename(filename, "expected wheel filename")
    _validate_wheel_filename(wheel_filename, version)
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        _fail("expected wheel SHA-256 must be 64 lowercase hexadecimal characters")
    return wheel_filename, digest


def _validate_expected_sdist_identity(filename: Any, digest: Any, version: str) -> tuple[str, str]:
    sdist_filename = _safe_basename(filename, "expected sdist filename")
    _validate_sdist_filename(sdist_filename, version)
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        _fail("expected sdist SHA-256 must be 64 lowercase hexadecimal characters")
    return sdist_filename, digest


def _validate_expected_manifest_sha256(value: Any) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail("expected manifest SHA-256 must be 64 lowercase hexadecimal characters")
    return value


def _artifact_record(path: Path, label: str) -> dict[str, Any]:
    size, digest = _file_digest(path, label)
    return {"basename": path.name, "size": size, "sha256": digest}


def _sha_sums(records: list[dict[str, Any]]) -> str:
    lines = [
        f"{record['sha256']}  {record['basename']}"
        for record in sorted(records, key=lambda record: record["basename"])
    ]
    return "\n".join(lines) + "\n"


def _write_atomic(path: Path, content: str) -> None:
    if path.exists() or path.is_symlink():
        _regular_file(path, path.name)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".release-candidate-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temporary_path, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temporary_path.unlink(missing_ok=True)
        _fail(f"cannot write {path.name}: {exc}")


def _append_github_outputs(path: Path, values: dict[str, str]) -> None:
    parent = _resolved_directory(path.parent, "GitHub output parent directory")
    path = parent / _safe_basename(path.name, "GitHub output filename")
    if path.exists() or path.is_symlink():
        _regular_file(path, "GitHub output file")
    with _open_regular(path, "GitHub output file", mode="a") as stream:
        for key, value in values.items():
            if "\n" in key or "\n" in value or "\r" in value:
                _fail("GitHub output values must be single-line strings")
            stream.write(f"{key}={value}\n")


def _record(args: argparse.Namespace) -> None:
    source_sha = _validate_source_sha(args.source_sha, "source SHA")
    version = _validate_version(args.version, "version")
    dist_dir = _resolved_directory(Path(args.dist_dir), "dist directory")
    meta_dir = _resolved_directory(Path(args.meta_dir), "metadata directory")
    dist_files = _directory_files(dist_dir, "dist directory")
    if len(dist_files) != 2:
        _fail("dist directory must contain exactly one wheel and one sdist")

    wheel_names = [name for name in dist_files if name.endswith(".whl")]
    sdist_names = [
        name for name in dist_files if any(name.endswith(suffix) for suffix in SDIST_SUFFIXES)
    ]
    if len(wheel_names) != 1 or len(sdist_names) != 1:
        _fail("dist directory must contain exactly one wheel and one sdist")
    wheel_name = wheel_names[0]
    sdist_name = sdist_names[0]
    _validate_wheel_filename(wheel_name, version)
    _validate_sdist_filename(sdist_name, version)

    receipt_path = Path(args.receipt)
    _regular_file(receipt_path, "wheel-contract receipt")
    resolved_receipt = receipt_path.resolve(strict=True)
    if resolved_receipt.parent != meta_dir:
        _fail("wheel-contract receipt must be a direct child of the metadata directory")
    receipt_name = _safe_basename(resolved_receipt.name, "receipt basename")
    if receipt_name in {MANIFEST_FILENAME, SUMS_FILENAME}:
        _fail("wheel-contract receipt must not collide with generated metadata files")
    if len({wheel_name, sdist_name, receipt_name}) != 3:
        _fail("wheel, sdist, and receipt basenames must be distinct")

    meta_files = _directory_files(meta_dir, "metadata directory")
    allowed_meta_names = {receipt_name, MANIFEST_FILENAME, SUMS_FILENAME}
    if set(meta_files) - allowed_meta_names:
        _fail("metadata directory contains unlisted files")
    for generated_name in (MANIFEST_FILENAME, SUMS_FILENAME):
        generated_path = meta_dir / generated_name
        if generated_path.exists() or generated_path.is_symlink():
            _regular_file(generated_path, generated_name)

    wheel_record = _artifact_record(dist_files[wheel_name], "wheel")
    sdist_record = _artifact_record(dist_files[sdist_name], "sdist")
    receipt_record = _artifact_record(resolved_receipt, "wheel-contract receipt")
    manifest = {
        "schema": SCHEMA,
        "source_sha": source_sha,
        "version": version,
        "wheel": wheel_record,
        "sdist": sdist_record,
        "receipt": receipt_record,
    }
    manifest_content = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    sums_content = _sha_sums([wheel_record, sdist_record, receipt_record])
    _write_atomic(meta_dir / MANIFEST_FILENAME, manifest_content)
    _write_atomic(meta_dir / SUMS_FILENAME, sums_content)
    _, manifest_sha256 = _file_digest(meta_dir / MANIFEST_FILENAME, "release candidate manifest")

    if args.github_output is not None:
        _append_github_outputs(
            Path(args.github_output),
            {
                "source_sha": source_sha,
                "version": version,
                "wheel_filename": wheel_name,
                "wheel_sha256": wheel_record["sha256"],
                "sdist_filename": sdist_name,
                "sdist_sha256": sdist_record["sha256"],
                "manifest_sha256": manifest_sha256,
                "manifest_filename": MANIFEST_FILENAME,
            },
        )
    print(f"recorded release candidate {version} at {source_sha}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_manifest(path: Path, *, expected_sha256: str) -> dict[str, Any]:
    _regular_file(path, "release candidate manifest")
    try:
        with _open_regular(path, "release candidate manifest") as stream:
            raw_manifest = stream.read()
        actual_sha256 = hashlib.sha256(raw_manifest).hexdigest()
        if actual_sha256 != expected_sha256:
            _fail("release candidate manifest SHA-256 does not match the expected manifest")
        payload = json.loads(raw_manifest.decode("utf-8"), object_pairs_hook=_unique_object)
    except CandidateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read release candidate manifest: {exc}")
    if not isinstance(payload, dict):
        _fail("release candidate manifest must be a JSON object")
    return payload


def _validate_record(record: Any, label: str) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != {"basename", "size", "sha256"}:
        _fail(f"{label} record has an invalid schema")
    basename = _safe_basename(record["basename"], f"{label} basename")
    size = record["size"]
    if type(size) is not int or size < 0:
        _fail(f"{label} size must be a non-negative integer")
    digest = record["sha256"]
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        _fail(f"{label} SHA-256 must be lowercase hexadecimal")
    return {"basename": basename, "size": size, "sha256": digest}


def _validate_manifest(
    manifest: dict[str, Any],
    *,
    expected_source_sha: str,
    expected_version: str,
    expected_wheel_filename: str,
    expected_wheel_sha256: str,
    expected_sdist_filename: str,
    expected_sdist_sha256: str,
) -> dict[str, dict[str, Any]]:
    if set(manifest) != {"schema", "source_sha", "version", "wheel", "sdist", "receipt"}:
        _fail("release candidate manifest has an invalid schema")
    if type(manifest["schema"]) is not int or manifest["schema"] != SCHEMA:
        _fail("unsupported release candidate schema")
    source_sha = _validate_source_sha(manifest["source_sha"], "manifest source SHA")
    version = _validate_version(manifest["version"], "manifest version")
    if source_sha != expected_source_sha:
        _fail("manifest source SHA does not match the expected source")
    if version != expected_version:
        _fail("manifest version does not match the expected version")

    records = {
        role: _validate_record(manifest[role], role) for role in ("wheel", "sdist", "receipt")
    }
    basenames = [record["basename"] for record in records.values()]
    if len(set(basenames)) != len(basenames):
        _fail("manifest artifact basenames must be distinct")
    if records["receipt"]["basename"] in {MANIFEST_FILENAME, SUMS_FILENAME}:
        _fail("manifest receipt basename collides with generated metadata files")
    _validate_wheel_filename(records["wheel"]["basename"], version)
    _validate_sdist_filename(records["sdist"]["basename"], version)
    if records["wheel"]["basename"] != expected_wheel_filename:
        _fail("manifest wheel filename does not match the expected wheel filename")
    if records["wheel"]["sha256"] != expected_wheel_sha256:
        _fail("manifest wheel SHA-256 does not match the expected wheel SHA-256")
    if records["sdist"]["basename"] != expected_sdist_filename:
        _fail("manifest sdist filename does not match the expected sdist filename")
    if records["sdist"]["sha256"] != expected_sdist_sha256:
        _fail("manifest sdist SHA-256 does not match the expected sdist SHA-256")
    return records


def _verify_record(path: Path, expected: dict[str, Any], label: str) -> None:
    actual_size, actual_digest = _file_digest(path, label)
    if actual_size != expected["size"]:
        _fail(f"{label} size does not match the manifest")
    if actual_digest != expected["sha256"]:
        _fail(f"{label} SHA-256 does not match the manifest")


def _verify(args: argparse.Namespace) -> None:
    expected_source_sha = _validate_source_sha(args.expected_source_sha, "expected source SHA")
    expected_version = _validate_version(args.expected_version, "expected version")
    expected_wheel_filename, expected_wheel_sha256 = _validate_expected_wheel_identity(
        args.expected_wheel_filename, args.expected_wheel_sha256, expected_version
    )
    expected_sdist_filename, expected_sdist_sha256 = _validate_expected_sdist_identity(
        args.expected_sdist_filename, args.expected_sdist_sha256, expected_version
    )
    expected_manifest_sha256 = _validate_expected_manifest_sha256(args.expected_manifest_sha256)
    dist_dir = _resolved_directory(Path(args.dist_dir), "dist directory")
    meta_dir = _resolved_directory(Path(args.meta_dir), "metadata directory")
    manifest_path = meta_dir / MANIFEST_FILENAME
    manifest = _load_manifest(manifest_path, expected_sha256=expected_manifest_sha256)
    records = _validate_manifest(
        manifest,
        expected_source_sha=expected_source_sha,
        expected_version=expected_version,
        expected_wheel_filename=expected_wheel_filename,
        expected_wheel_sha256=expected_wheel_sha256,
        expected_sdist_filename=expected_sdist_filename,
        expected_sdist_sha256=expected_sdist_sha256,
    )

    dist_files = _directory_files(dist_dir, "dist directory")
    expected_dist_names = {records["wheel"]["basename"], records["sdist"]["basename"]}
    if set(dist_files) != expected_dist_names:
        _fail("dist directory file set does not match the manifest")
    meta_files = _directory_files(meta_dir, "metadata directory")
    expected_meta_names = {
        MANIFEST_FILENAME,
        SUMS_FILENAME,
        records["receipt"]["basename"],
    }
    if set(meta_files) != expected_meta_names:
        _fail("metadata directory file set does not match the manifest")

    artifact_paths = {
        "wheel": dist_files[records["wheel"]["basename"]],
        "sdist": dist_files[records["sdist"]["basename"]],
        "receipt": meta_files[records["receipt"]["basename"]],
    }
    for role, path in artifact_paths.items():
        _verify_record(path, records[role], role)

    expected_sums = _sha_sums(list(records.values()))
    with _open_regular(meta_files[SUMS_FILENAME], SUMS_FILENAME, mode="r") as stream:
        actual_sums = stream.read()
    if actual_sums != expected_sums:
        _fail("SHA256SUMS.txt does not match the candidate files")
    print(f"verified release candidate {expected_version} at {expected_source_sha}")


def _verify_downloaded_wheel(args: argparse.Namespace) -> None:
    expected_source_sha = _validate_source_sha(args.expected_source_sha, "expected source SHA")
    expected_version = _validate_version(args.expected_version, "expected version")
    expected_wheel_filename, expected_wheel_sha256 = _validate_expected_wheel_identity(
        args.expected_wheel_filename, args.expected_wheel_sha256, expected_version
    )
    expected_sdist_filename, expected_sdist_sha256 = _validate_expected_sdist_identity(
        args.expected_sdist_filename, args.expected_sdist_sha256, expected_version
    )
    expected_manifest_sha256 = _validate_expected_manifest_sha256(args.expected_manifest_sha256)
    manifest = _load_manifest(Path(args.manifest), expected_sha256=expected_manifest_sha256)
    records = _validate_manifest(
        manifest,
        expected_source_sha=expected_source_sha,
        expected_version=expected_version,
        expected_wheel_filename=expected_wheel_filename,
        expected_wheel_sha256=expected_wheel_sha256,
        expected_sdist_filename=expected_sdist_filename,
        expected_sdist_sha256=expected_sdist_sha256,
    )
    wheel_dir = _resolved_directory(Path(args.wheel_dir), "downloaded wheel directory")
    downloaded_files = _directory_files(wheel_dir, "downloaded wheel directory")
    expected_name = records["wheel"]["basename"]
    if set(downloaded_files) != {expected_name}:
        _fail("download directory must contain exactly the manifest wheel")
    _verify_record(downloaded_files[expected_name], records["wheel"], "downloaded wheel")
    print(f"verified downloaded wheel {expected_name}")


def _add_expected_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-wheel-filename", required=True)
    parser.add_argument("--expected-wheel-sha256", required=True)
    parser.add_argument("--expected-sdist-filename", required=True)
    parser.add_argument("--expected-sdist-sha256", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record", help="record a fresh release candidate")
    record_parser.add_argument("--dist-dir", required=True)
    record_parser.add_argument("--meta-dir", required=True)
    record_parser.add_argument("--receipt", required=True)
    record_parser.add_argument("--source-sha", required=True)
    record_parser.add_argument("--version", required=True)
    record_parser.add_argument("--github-output")
    record_parser.set_defaults(handler=_record)

    verify_parser = subparsers.add_parser("verify", help="verify a release candidate")
    verify_parser.add_argument("--dist-dir", required=True)
    verify_parser.add_argument("--meta-dir", required=True)
    _add_expected_identity(verify_parser)
    verify_parser.set_defaults(handler=_verify)

    wheel_parser = subparsers.add_parser(
        "verify-downloaded-wheel", help="verify the exact wheel downloaded from TestPyPI"
    )
    wheel_parser.add_argument("--manifest", required=True)
    wheel_parser.add_argument("--wheel-dir", required=True)
    _add_expected_identity(wheel_parser)
    wheel_parser.set_defaults(handler=_verify_downloaded_wheel)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except (CandidateError, OSError) as exc:
        print(f"release candidate error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
