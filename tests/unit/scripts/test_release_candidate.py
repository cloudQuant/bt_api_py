"""Offline tests for release candidate artifact identity closure."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY_ROOT / "scripts" / "ci" / "release_candidate.py"
SOURCE_SHA = "a" * 40
VERSION = "1.2.3"
WHEEL_NAME = f"bt_api_py-{VERSION}-py3-none-any.whl"
SDIST_NAME = f"bt_api_py-{VERSION}.tar.gz"


def create_artifacts(
    root: Path,
    *,
    wheel_name: str = WHEEL_NAME,
    sdist_name: str = SDIST_NAME,
) -> tuple[Path, Path, Path]:
    """Create filename-valid, content-minimal artifacts in temporary folders."""
    dist_dir = root / "dist"
    meta_dir = root / "dist-meta"
    dist_dir.mkdir(parents=True)
    meta_dir.mkdir()
    (dist_dir / wheel_name).write_bytes(b"fake wheel bytes\n")
    (dist_dir / sdist_name).write_bytes(b"fake source archive bytes\n")
    receipt = meta_dir / "wheel-contract-receipt.json"
    receipt.write_text('{"contract": "offline-test"}\n', encoding="utf-8")
    return dist_dir, meta_dir, receipt


def run_cli(*arguments: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(argument) for argument in arguments)],
        capture_output=True,
        text=True,
        check=False,
    )


def record_candidate(
    dist_dir: Path,
    meta_dir: Path,
    receipt: Path,
    *,
    source_sha: str = SOURCE_SHA,
    version: str = VERSION,
    github_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    arguments: list[str | Path] = [
        "record",
        "--dist-dir",
        dist_dir,
        "--meta-dir",
        meta_dir,
        "--receipt",
        receipt,
        "--source-sha",
        source_sha,
        "--version",
        version,
    ]
    if github_output is not None:
        arguments.extend(["--github-output", github_output])
    return run_cli(*arguments)


def verify_candidate(
    dist_dir: Path,
    meta_dir: Path,
    *,
    source_sha: str = SOURCE_SHA,
    version: str = VERSION,
    wheel_filename: str | None = None,
    wheel_sha256: str | None = None,
    sdist_filename: str | None = None,
    sdist_sha256: str | None = None,
    manifest_sha256: str | None = None,
) -> subprocess.CompletedProcess[str]:
    manifest = read_manifest(meta_dir)
    wheel_record = manifest["wheel"]
    sdist_record = manifest["sdist"]
    manifest_path = meta_dir / "release-candidate.json"
    return run_cli(
        "verify",
        "--dist-dir",
        dist_dir,
        "--meta-dir",
        meta_dir,
        "--expected-source-sha",
        source_sha,
        "--expected-version",
        version,
        "--expected-wheel-filename",
        wheel_record["basename"] if wheel_filename is None else wheel_filename,
        "--expected-wheel-sha256",
        wheel_record["sha256"] if wheel_sha256 is None else wheel_sha256,
        "--expected-sdist-filename",
        sdist_record["basename"] if sdist_filename is None else sdist_filename,
        "--expected-sdist-sha256",
        sdist_record["sha256"] if sdist_sha256 is None else sdist_sha256,
        "--expected-manifest-sha256",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if manifest_sha256 is None
        else manifest_sha256,
    )


def verify_downloaded_wheel(
    meta_dir: Path,
    wheel_dir: Path,
    *,
    wheel_filename: str | None = None,
    wheel_sha256: str | None = None,
    sdist_filename: str | None = None,
    sdist_sha256: str | None = None,
    manifest_sha256: str | None = None,
) -> subprocess.CompletedProcess[str]:
    manifest = read_manifest(meta_dir)
    wheel_record = manifest["wheel"]
    sdist_record = manifest["sdist"]
    manifest_path = meta_dir / "release-candidate.json"
    return run_cli(
        "verify-downloaded-wheel",
        "--manifest",
        meta_dir / "release-candidate.json",
        "--wheel-dir",
        wheel_dir,
        "--expected-source-sha",
        SOURCE_SHA,
        "--expected-version",
        VERSION,
        "--expected-wheel-filename",
        wheel_record["basename"] if wheel_filename is None else wheel_filename,
        "--expected-wheel-sha256",
        wheel_record["sha256"] if wheel_sha256 is None else wheel_sha256,
        "--expected-sdist-filename",
        sdist_record["basename"] if sdist_filename is None else sdist_filename,
        "--expected-sdist-sha256",
        sdist_record["sha256"] if sdist_sha256 is None else sdist_sha256,
        "--expected-manifest-sha256",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if manifest_sha256 is None
        else manifest_sha256,
    )


def write_manifest(meta_dir: Path, payload: dict[str, Any]) -> None:
    (meta_dir / "release-candidate.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_manifest(meta_dir: Path) -> dict[str, Any]:
    return json.loads((meta_dir / "release-candidate.json").read_text(encoding="utf-8"))


def test_record_emits_deterministic_manifest_sums_and_github_outputs(tmp_path: Path) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    github_output = tmp_path / "github-output"
    first = record_candidate(dist_dir, meta_dir, receipt, github_output=github_output)
    assert first.returncode == 0, first.stdout + first.stderr
    manifest_bytes = (meta_dir / "release-candidate.json").read_bytes()
    sums_bytes = (meta_dir / "SHA256SUMS.txt").read_bytes()

    second = record_candidate(dist_dir, meta_dir, receipt, github_output=github_output)
    assert second.returncode == 0, second.stdout + second.stderr
    assert (meta_dir / "release-candidate.json").read_bytes() == manifest_bytes
    assert (meta_dir / "SHA256SUMS.txt").read_bytes() == sums_bytes

    manifest = read_manifest(meta_dir)
    assert manifest["schema"] == 1
    assert manifest["source_sha"] == SOURCE_SHA
    assert manifest["version"] == VERSION
    for key, basename in (
        ("wheel", WHEEL_NAME),
        ("sdist", SDIST_NAME),
        ("receipt", receipt.name),
    ):
        entry = manifest[key]
        artifact = (dist_dir if key != "receipt" else meta_dir) / basename
        assert entry == {
            "basename": basename,
            "size": artifact.stat().st_size,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }

    expected_lines = []
    for basename in sorted((WHEEL_NAME, SDIST_NAME, receipt.name)):
        artifact = (dist_dir if basename != receipt.name else meta_dir) / basename
        expected_lines.append(f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {basename}")
    assert sums_bytes.decode("utf-8").splitlines() == expected_lines
    output_values = dict(
        line.split("=", maxsplit=1)
        for line in github_output.read_text(encoding="utf-8").splitlines()
    )
    assert output_values["source_sha"] == SOURCE_SHA
    assert output_values["version"] == VERSION
    assert output_values["wheel_filename"] == WHEEL_NAME
    assert output_values["wheel_sha256"] == manifest["wheel"]["sha256"]
    assert output_values["sdist_filename"] == SDIST_NAME
    assert output_values["sdist_sha256"] == manifest["sdist"]["sha256"]
    assert output_values["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert output_values["manifest_filename"] == "release-candidate.json"
    verified = verify_candidate(dist_dir, meta_dir)
    assert verified.returncode == 0, verified.stdout + verified.stderr


@pytest.mark.parametrize("source_sha", ["A" * 40, "a" * 39, "g" * 40, "a" * 41])
def test_record_rejects_noncanonical_source_sha(tmp_path: Path, source_sha: str) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)

    result = record_candidate(dist_dir, meta_dir, receipt, source_sha=source_sha)

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("wheel_name", "sdist_name", "version"),
    [
        (WHEEL_NAME, SDIST_NAME, "1.2.4"),
        ("other_pkg-1.2.3-py3-none-any.whl", SDIST_NAME, VERSION),
        ("bt_api_py-1.2.3-py3-none-any.whl", "bt_api_py-1.2.4.tar.gz", VERSION),
    ],
)
def test_record_rejects_distribution_or_version_mismatch(
    tmp_path: Path, wheel_name: str, sdist_name: str, version: str
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(
        tmp_path, wheel_name=wheel_name, sdist_name=sdist_name
    )

    result = record_candidate(dist_dir, meta_dir, receipt, version=version)

    assert result.returncode != 0


@pytest.mark.parametrize(
    "mutation", ["missing-wheel", "missing-sdist", "duplicate-wheel", "duplicate-sdist", "extra"]
)
def test_record_rejects_missing_duplicate_or_extra_dist_files(
    tmp_path: Path, mutation: str
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    if mutation == "missing-wheel":
        (dist_dir / WHEEL_NAME).unlink()
    elif mutation == "missing-sdist":
        (dist_dir / SDIST_NAME).unlink()
    elif mutation == "duplicate-wheel":
        (dist_dir / WHEEL_NAME).rename(dist_dir / f"bt_api_py-{VERSION}-py2-none-any.whl")
        (dist_dir / WHEEL_NAME).write_bytes(b"second wheel bytes\n")
    elif mutation == "duplicate-sdist":
        (dist_dir / f"bt_api_py-{VERSION}.zip").write_bytes(b"second source archive\n")
    else:
        (dist_dir / "unlisted.txt").write_text("extra", encoding="utf-8")

    result = record_candidate(dist_dir, meta_dir, receipt)

    assert result.returncode != 0


def test_record_rejects_symlink_in_dist(tmp_path: Path) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    (dist_dir / "linked-wheel.whl").symlink_to(dist_dir / WHEEL_NAME)

    result = record_candidate(dist_dir, meta_dir, receipt)

    assert result.returncode != 0


def test_record_requires_receipt_as_regular_file_under_meta_dir(tmp_path: Path) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    receipt.unlink()
    outside_receipt = tmp_path / "outside-receipt.json"
    outside_receipt.write_text("{}\n", encoding="utf-8")

    result = record_candidate(dist_dir, meta_dir, outside_receipt)

    assert result.returncode != 0


def test_record_rejects_receipt_symlink_and_extra_meta_file(tmp_path: Path) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    receipt.unlink()
    receipt.symlink_to(tmp_path / "not-a-receipt")
    (tmp_path / "not-a-receipt").write_text("{}\n", encoding="utf-8")

    result = record_candidate(dist_dir, meta_dir, receipt)

    assert result.returncode != 0

    receipt.unlink()
    receipt.write_text("{}\n", encoding="utf-8")
    (meta_dir / "unlisted-meta.txt").write_text("extra", encoding="utf-8")
    result_with_extra = record_candidate(dist_dir, meta_dir, receipt)
    assert result_with_extra.returncode != 0


@pytest.mark.parametrize(
    "manifest_change", ["schema", "path-escape", "invalid-hash", "invalid-size"]
)
def test_verify_rejects_bad_manifest_schema_and_member_metadata(
    tmp_path: Path, manifest_change: str
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    manifest = read_manifest(meta_dir)
    if manifest_change == "schema":
        manifest["schema"] = 2
    elif manifest_change == "path-escape":
        manifest["wheel"]["basename"] = "../outside.whl"
    elif manifest_change == "invalid-hash":
        manifest["wheel"]["sha256"] = "NOT-A-HASH"
    else:
        manifest["wheel"]["size"] = True
    write_manifest(meta_dir, manifest)

    result = verify_candidate(dist_dir, meta_dir)

    assert result.returncode != 0


@pytest.mark.parametrize("tampered_file", ["wheel", "sdist", "receipt", "sums"])
def test_verify_detects_artifact_receipt_and_sum_tampering(
    tmp_path: Path, tampered_file: str
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    if tampered_file == "wheel":
        target = dist_dir / WHEEL_NAME
    elif tampered_file == "sdist":
        target = dist_dir / SDIST_NAME
    elif tampered_file == "receipt":
        target = receipt
    else:
        target = meta_dir / "SHA256SUMS.txt"
    target.write_bytes(target.read_bytes() + b"tampered\n")

    result = verify_candidate(dist_dir, meta_dir)

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("source_sha", "version"),
    [("b" * 40, VERSION), (SOURCE_SHA, "1.2.4")],
)
def test_verify_requires_exact_expected_source_and_version(
    tmp_path: Path, source_sha: str, version: str
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr

    result = verify_candidate(dist_dir, meta_dir, source_sha=source_sha, version=version)

    assert result.returncode != 0


def test_verify_rejects_unmanifested_dist_and_meta_members(tmp_path: Path) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    (dist_dir / "extra.txt").write_text("extra", encoding="utf-8")

    dist_result = verify_candidate(dist_dir, meta_dir)
    assert dist_result.returncode != 0
    (dist_dir / "extra.txt").unlink()
    (meta_dir / "extra.txt").write_text("extra", encoding="utf-8")

    meta_result = verify_candidate(dist_dir, meta_dir)
    assert meta_result.returncode != 0


@pytest.mark.parametrize(
    ("wheel_filename", "wheel_sha256"),
    [
        ("bt_api_py-1.2.3-py2-none-any.whl", None),
        (None, "f" * 64),
    ],
)
def test_verify_requires_independent_expected_wheel_identity(
    tmp_path: Path, wheel_filename: str | None, wheel_sha256: str | None
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr

    result = verify_candidate(
        dist_dir,
        meta_dir,
        wheel_filename=wheel_filename,
        wheel_sha256=wheel_sha256,
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("sdist_filename", "sdist_sha256", "manifest_sha256"),
    [
        ("bt_api_py-1.2.3.zip", None, None),
        (None, "f" * 64, None),
        (None, None, "f" * 64),
    ],
)
def test_verify_requires_independent_expected_sdist_and_manifest_identity(
    tmp_path: Path,
    sdist_filename: str | None,
    sdist_sha256: str | None,
    manifest_sha256: str | None,
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr

    result = verify_candidate(
        dist_dir,
        meta_dir,
        sdist_filename=sdist_filename,
        sdist_sha256=sdist_sha256,
        manifest_sha256=manifest_sha256,
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("sdist_filename", "sdist_sha256", "manifest_sha256"),
    [
        ("../bt_api_py-1.2.3.tar.gz", None, None),
        (None, "F" * 64, None),
        (None, None, "F" * 64),
    ],
)
def test_verify_rejects_malformed_expected_sdist_and_manifest_identity(
    tmp_path: Path,
    sdist_filename: str | None,
    sdist_sha256: str | None,
    manifest_sha256: str | None,
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr

    result = verify_candidate(
        dist_dir,
        meta_dir,
        sdist_filename=sdist_filename,
        sdist_sha256=sdist_sha256,
        manifest_sha256=manifest_sha256,
    )

    assert result.returncode != 0


def test_verify_rejects_coordinated_sdist_manifest_and_sums_replacement(
    tmp_path: Path,
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    original_manifest = read_manifest(meta_dir)
    original_manifest_bytes = (meta_dir / "release-candidate.json").read_bytes()
    original_sdist_sha256 = original_manifest["sdist"]["sha256"]

    sdist_path = dist_dir / SDIST_NAME
    substituted = sdist_path.read_bytes() + b"coordinated replacement\n"
    sdist_path.write_bytes(substituted)
    original_manifest["sdist"]["size"] = len(substituted)
    original_manifest["sdist"]["sha256"] = hashlib.sha256(substituted).hexdigest()
    write_manifest(meta_dir, original_manifest)
    records = [
        original_manifest["wheel"],
        original_manifest["sdist"],
        original_manifest["receipt"],
    ]
    sums_lines = [
        f"{record['sha256']}  {record['basename']}"
        for record in sorted(records, key=lambda record: record["basename"])
    ]
    (meta_dir / "SHA256SUMS.txt").write_text("\n".join(sums_lines) + "\n", encoding="utf-8")

    result = verify_candidate(
        dist_dir,
        meta_dir,
        sdist_filename=SDIST_NAME,
        sdist_sha256=original_sdist_sha256,
        manifest_sha256=hashlib.sha256(original_manifest_bytes).hexdigest(),
    )

    assert result.returncode != 0
    assert "manifest SHA-256 does not match the expected manifest" in result.stderr


@pytest.mark.parametrize(
    ("wheel_filename", "wheel_sha256"),
    [
        ("../bt_api_py-1.2.3-py3-none-any.whl", None),
        (None, "F" * 64),
    ],
)
def test_verify_rejects_malformed_expected_wheel_identity(
    tmp_path: Path, wheel_filename: str | None, wheel_sha256: str | None
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr

    result = verify_candidate(
        dist_dir,
        meta_dir,
        wheel_filename=wheel_filename,
        wheel_sha256=wheel_sha256,
    )

    assert result.returncode != 0


def test_verify_downloaded_wheel_accepts_exact_manifest_artifact(tmp_path: Path) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    downloaded_dir = tmp_path / "downloaded"
    downloaded_dir.mkdir()
    shutil.copyfile(dist_dir / WHEEL_NAME, downloaded_dir / WHEEL_NAME)

    result = verify_downloaded_wheel(meta_dir, downloaded_dir)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "substitution", ["same-name-different-hash", "different-name-same-content"]
)
def test_verify_downloaded_wheel_rejects_name_hash_substitution(
    tmp_path: Path, substitution: str
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    downloaded_dir = tmp_path / "downloaded"
    downloaded_dir.mkdir()
    content = (dist_dir / WHEEL_NAME).read_bytes()
    if substitution == "same-name-different-hash":
        (downloaded_dir / WHEEL_NAME).write_bytes(content + b"changed")
    else:
        (downloaded_dir / f"bt_api_py-{VERSION}-py2-none-any.whl").write_bytes(content)

    result = verify_downloaded_wheel(meta_dir, downloaded_dir)

    assert result.returncode != 0


def test_verify_downloaded_wheel_rejects_extra_or_symlink_files(tmp_path: Path) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    downloaded_dir = tmp_path / "downloaded"
    downloaded_dir.mkdir()
    shutil.copyfile(dist_dir / WHEEL_NAME, downloaded_dir / WHEEL_NAME)
    (downloaded_dir / "extra.whl").write_bytes(b"extra")

    extra_result = verify_downloaded_wheel(meta_dir, downloaded_dir)
    assert extra_result.returncode != 0

    (downloaded_dir / "extra.whl").unlink()
    (downloaded_dir / "linked.whl").symlink_to(downloaded_dir / WHEEL_NAME)
    symlink_result = verify_downloaded_wheel(meta_dir, downloaded_dir)
    assert symlink_result.returncode != 0


@pytest.mark.parametrize(
    ("wheel_filename", "wheel_sha256"),
    [
        ("bt_api_py-1.2.3-py2-none-any.whl", None),
        (None, "f" * 64),
    ],
)
def test_verify_downloaded_wheel_requires_independent_expected_identity(
    tmp_path: Path, wheel_filename: str | None, wheel_sha256: str | None
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    downloaded_dir = tmp_path / "downloaded"
    downloaded_dir.mkdir()
    shutil.copyfile(dist_dir / WHEEL_NAME, downloaded_dir / WHEEL_NAME)

    result = verify_downloaded_wheel(
        meta_dir,
        downloaded_dir,
        wheel_filename=wheel_filename,
        wheel_sha256=wheel_sha256,
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("sdist_filename", "sdist_sha256", "manifest_sha256"),
    [
        ("bt_api_py-1.2.3.zip", None, None),
        (None, "f" * 64, None),
        (None, None, "f" * 64),
    ],
)
def test_verify_downloaded_wheel_requires_sdist_and_manifest_anchors(
    tmp_path: Path,
    sdist_filename: str | None,
    sdist_sha256: str | None,
    manifest_sha256: str | None,
) -> None:
    dist_dir, meta_dir, receipt = create_artifacts(tmp_path)
    recorded = record_candidate(dist_dir, meta_dir, receipt)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    downloaded_dir = tmp_path / "downloaded"
    downloaded_dir.mkdir()
    shutil.copyfile(dist_dir / WHEEL_NAME, downloaded_dir / WHEEL_NAME)

    result = verify_downloaded_wheel(
        meta_dir,
        downloaded_dir,
        sdist_filename=sdist_filename,
        sdist_sha256=sdist_sha256,
        manifest_sha256=manifest_sha256,
    )

    assert result.returncode != 0
