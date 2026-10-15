from __future__ import annotations

import hashlib
import json
from email.parser import Parser
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(r"D:\temp\iteration10_cfg01_cfg04_mig04_20261015_r2\cfg01-current-head-r1")
REPO = Path(r"D:\bt_api_py")
SOURCE_REVISION = "cdb1b81e39b661afb145ff3d9e94695c97429d7e"
EXPECTED_TREE = "6943f04da92133ab23ace7fc0c3ceefdc676eb50"
EXPECTED_MANIFEST_SHA256 = "fd8fd3b9477b1080bddb681c531b11880a58b586e1a51d7c8de6950a541956af"
EXPECTED_WHEEL_SHA256 = "f529664e95b4b489cf418b4fc78e81d7a77b72f5ba80c37ed3f5c4ce03c9033b"
EXPECTED_CONFIG_SHA256 = "588fccd3c0b5ad7e0d42a296aa8359f7b541faeb39e949f2c897bc740b897592"
CONFIG_RELATIVE = "examples/services/unconfigured-client.yaml"

manifest_path = ROOT / "build-manifest.json"
archive_path = ROOT / "bt_api_py-cdb1b81-packaging.tar"
wheel_path = ROOT / "wheelhouse-current" / "bt_api_py-0.15.5-py3-none-any.whl"
probe_path = ROOT / "cfg01_current_head_probe_r3.py"
original_path = ROOT / "cfg01-current-head-observation-r1.json"
venv_path = ROOT / "venv-client-current-cdb1"
site_packages = venv_path / "Lib" / "site-packages"
reconcile_path = ROOT / "cfg01-current-head-metadata-reconciliation-r1.json"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git_bytes(*args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        check=True,
        capture_output=True,
    ).stdout


def git_text(*args: str) -> str:
    return git_bytes(*args).decode("ascii").strip()


def parse_metadata(data: bytes) -> dict[str, str]:
    parsed = Parser().parsestr(data.decode("utf-8"))
    return {key: parsed.get(key, "") for key in ("Name", "Version", "Requires-Python")}

manifest_raw = manifest_path.read_bytes()
manifest = json.loads(manifest_raw)
original_raw = original_path.read_bytes()
original = json.loads(original_raw)
probe_raw = probe_path.read_bytes()
wheel_raw = wheel_path.read_bytes()
archive_raw = archive_path.read_bytes()
wheel_info = {}
with zipfile.ZipFile(wheel_path) as wheel_zip:
    metadata_names = [
        name for name in wheel_zip.namelist()
        if name.endswith(".dist-info/METADATA") and name.count("/") == 1
    ]
    if len(metadata_names) != 1:
        raise RuntimeError("wheel metadata file is not unique")
    metadata_name = metadata_names[0]
    wheel_info = parse_metadata(wheel_zip.read(metadata_name))

installed_metadata_files = sorted(site_packages.glob("bt_api_py-*.dist-info/METADATA"))
if len(installed_metadata_files) != 1:
    raise RuntimeError("installed SDK dist-info metadata file is not unique")
installed_dist_info = installed_metadata_files[0].parent
installed_metadata = parse_metadata(installed_metadata_files[0].read_bytes())
installed_direct_url_raw = (installed_dist_info / "direct_url.json").read_bytes()
installed_direct_url = json.loads(installed_direct_url_raw)

config_checkout = (REPO / CONFIG_RELATIVE).read_bytes()
config_git_blob = git_bytes("cat-file", "blob", f"{SOURCE_REVISION}:{CONFIG_RELATIVE}")
config_git_oid = git_text("rev-parse", f"{SOURCE_REVISION}:{CONFIG_RELATIVE}")
config_manifest = manifest["config_inputs"]["no_new_block"]
empty_config_path = ROOT / "empty-clients.yaml"
empty_config_raw = empty_config_path.read_bytes()
empty_config_manifest = manifest["config_inputs"]["empty_clients"]

source_tree = git_text("rev-parse", f"{SOURCE_REVISION}^{{tree}}")
source_revision = git_text("rev-parse", SOURCE_REVISION)
manifest_source = manifest["source"]
manifest_build = manifest["build"]
manifest_wheel = manifest["wheel"]
installed_version = installed_metadata["Version"]
wheel_version = wheel_info["Version"]
manifest_wheel_version = manifest_wheel["version"]
build_tool_version = manifest_build["build_tool_version"]
probe_text = probe_raw.decode("utf-8")
original_installed = original["installed_sdk"]
original_checks = original["checks"]
runtime_gate_names = (
    "both_configs_valid_empty_and_unactivated",
    "default_sdk_is_direct_and_closed",
    "cerebro_processed_one_synthetic_bar",
    "four_domain_packages_absent_and_unloaded",
    "optional_transport_and_spdlog_absent_and_unloaded",
    "no_service_modules_or_watched_import_attempts",
    "no_observed_runtime_optional_resource_events",
    "no_servicehost_constructor_in_direct_or_backtest",
    "no_files_created_in_probe_working_directory",
    "observer_restoration_completed",
)
runtime_gates = {name: original_checks.get(name) for name in runtime_gate_names}

result = {
    "record_kind": "CFG01_CURRENT_HEAD_METADATA_ONLY_RECONCILIATION",
    "status": "RECONCILIATION_COMPLETE_WITH_MANIFEST_BYTE_COUNT_DISCREPANCY",
    "scope": "metadata-only; no SDK modules or business probe re-executed",
    "metadata_reader": {
        "python_executable": sys.executable,
        "isolated_flag": bool(sys.flags.isolated),
        "no_site_flag": bool(sys.flags.no_site),
        "sdk_modules_imported": False,
    },
    "inputs": {
        "original_probe": {"path": str(probe_path), "sha256": sha256_bytes(probe_raw), "bytes": len(probe_raw)},
        "original_manifest": {"path": str(manifest_path), "sha256": sha256_bytes(manifest_raw), "bytes": len(manifest_raw)},
        "original_result": {"path": str(original_path), "sha256": sha256_bytes(original_raw), "bytes": len(original_raw), "status": original.get("status")},
        "wheel": {"path": str(wheel_path), "sha256": sha256_bytes(wheel_raw), "bytes": len(wheel_raw)},
        "source_archive": {"path": str(archive_path), "sha256": sha256_bytes(archive_raw), "bytes": len(archive_raw)},
    },
    "sdk_version_and_build_tool": {
        "installed_dist_info": str(installed_dist_info),
        "installed_metadata": installed_metadata,
        "wheel_metadata": wheel_info,
        "manifest_wheel_version": manifest_wheel_version,
        "manifest_build_tool_version_nested_at_build_build_tool_version": build_tool_version,
        "original_probe_installed_version": original_installed.get("version"),
        "original_probe_manifest_wheel_version": original_installed.get("build_manifest_wheel_version"),
        "original_probe_manifest_build_tool_version_recorded": original_installed.get("build_manifest_build_tool_version"),
        "original_probe_manifest_build_tool_lookup_was_top_level": 'build_manifest.get("build_tool_version")' in probe_text,
    },
    "installed_direct_url": {
        "metadata_file": str(installed_dist_info / "direct_url.json"),
        "installed_value": installed_direct_url,
        "original_probe_value": original_installed.get("direct_url"),
    },
    "source_config_identity": {
        "path": str(REPO / CONFIG_RELATIVE),
        "checkout_bytes": len(config_checkout),
        "fixed_git_blob_bytes": len(config_git_blob),
        "checkout_sha256": sha256_bytes(config_checkout),
        "fixed_git_blob_sha256": sha256_bytes(config_git_blob),
        "fixed_git_blob_object_id": config_git_oid,
        "manifest_recorded_bytes": config_manifest.get("bytes"),
        "manifest_recorded_sha256": config_manifest.get("sha256"),
        "manifest_recorded_git_blob_object_id": config_manifest.get("git_blob"),
        "checkout_equals_fixed_git_blob": config_checkout == config_git_blob,
        "empty_clients": {
            "path": str(empty_config_path),
            "bytes": len(empty_config_raw),
            "sha256": sha256_bytes(empty_config_raw),
            "manifest_recorded_bytes": empty_config_manifest.get("bytes"),
            "manifest_recorded_sha256": empty_config_manifest.get("sha256"),
        },
    },
    "original_probe_runtime_gates": {
        "original_status": original.get("status"),
        "original_exit_code": 1,
        "runtime_gate_results": runtime_gates,
        "all_runtime_gates_true": all(value is True for value in runtime_gates.values()),
        "original_failed_checks": original.get("failed_checks"),
        "only_failed_check": original.get("failed_checks") == ["installed_sdk_version_matches_pinned_wheel_metadata"],
        "observed_import_phase_counts": original.get("resource_counts_by_phase", {}).get("imports"),
        "observed_runtime_phase_counts": {
            name: original.get("resource_counts_by_phase", {}).get(name)
            for name in original.get("runtime_phase_names", [])
        },
        "full_run_process_handle_delta_diagnostic_only": original.get("full_run_process_handle_delta"),
        "full_run_active_thread_delta_diagnostic_only": original.get("full_run_active_thread_delta"),
        "service_modules_loaded": original.get("sdk_service_modules_loaded"),
        "watched_import_attempts": original.get("watched_import_attempts"),
        "optional_and_domain_packages": original.get("optional_and_domain_packages"),
        "observer_restoration_errors": original.get("observer_restoration_errors"),
    },
    "checks": {},
}

archive_recorded_hash = manifest_source["archive_sha256"]
wheel_recorded_hash = manifest_wheel["sha256"]
installed_archive_hash = installed_direct_url.get("archive_info", {}).get("hash")
checks = {
    "fixed_source_revision_and_tree_match": source_revision == SOURCE_REVISION and source_tree == EXPECTED_TREE,
    "manifest_hash_matches_original_probe_pin": sha256_bytes(manifest_raw) == EXPECTED_MANIFEST_SHA256,
    "source_archive_hash_matches_pinned_manifest": sha256_bytes(archive_raw) == archive_recorded_hash,
    "wheel_hash_matches_pinned_manifest": sha256_bytes(wheel_raw) == EXPECTED_WHEEL_SHA256 == wheel_recorded_hash,
    "wheel_metadata_version_matches_manifest": wheel_version == manifest_wheel_version == "0.15.5",
    "installed_metadata_matches_wheel_metadata": installed_metadata == wheel_info,
    "installed_version_matches_manifest_wheel_version": installed_version == manifest_wheel_version == "0.15.5",
    "installed_direct_url_matches_original_probe": installed_direct_url == original_installed.get("direct_url"),
    "installed_direct_url_hash_matches_wheel": installed_archive_hash == "sha256=" + EXPECTED_WHEEL_SHA256,
    "build_tool_version_is_pinned_nested_manifest_value": build_tool_version == "1.6.1" and manifest.get("build_tool_version") is None,
    "original_false_version_assertion_is_explained_by_wrong_lookup": (
        original_checks.get("installed_sdk_version_matches_pinned_wheel_metadata") is False
        and original_installed.get("build_manifest_build_tool_version") is None
        and result["sdk_version_and_build_tool"]["original_probe_manifest_build_tool_lookup_was_top_level"] is True
        and build_tool_version == "1.6.1"
    ),
    "config_checkout_matches_fixed_commit_blob_and_sha": (
        config_checkout == config_git_blob
        and sha256_bytes(config_checkout) == EXPECTED_CONFIG_SHA256
        and config_git_oid == config_manifest.get("git_blob")
        and sha256_bytes(config_checkout) == config_manifest.get("sha256")
    ),
    "manifest_config_byte_count_matches_actual": len(config_checkout) == config_manifest.get("bytes"),
    "empty_config_matches_its_manifest_bytes_and_hash": (
        len(empty_config_raw) == empty_config_manifest.get("bytes")
        and sha256_bytes(empty_config_raw) == empty_config_manifest.get("sha256")
    ),
    "all_original_runtime_gates_are_true": all(value is True for value in runtime_gates.values()),
    "original_probe_status_and_exit_remain_failure": original.get("status") == "ASSERTION_FAILED" and original.get("failed_checks") == ["installed_sdk_version_matches_pinned_wheel_metadata"],
}
result["checks"] = checks
result["manifest_config_size_discrepancy"] = {
    "recorded_bytes": config_manifest.get("bytes"),
    "actual_checkout_and_git_blob_bytes": len(config_checkout),
    "delta_bytes": config_manifest.get("bytes") - len(config_checkout),
    "interpretation": "metadata typo preserved; checkout/blob bytes and SHA-256 are identical; original manifest and probe result remain unchanged",
}
result["runtime_interpretation"] = (
    "Every captured CFG01 config, default direct SDK, one-bar Cerebro, optional package/import/resource and observer-restoration gate is true. "
    "The original probe process returned 1 because its build-tool-version lookup used the manifest top level; the actual pinned manifest stores it under build.build_tool_version. "
    "This reconciliation does not rewrite the original status or retroactively make the probe exit code zero."
)
reconcile_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({"status": result["status"], "path": str(reconcile_path), "checks": checks, "original_status": original.get("status"), "original_exit_code": 1}, indent=2, sort_keys=True))
