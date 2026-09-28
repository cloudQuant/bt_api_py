"""Acceptance checks for the local-only Iteration 41 wheel consumer matrix."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import zipfile
from importlib import metadata
from pathlib import Path

import pytest

from scripts.ci import verify_iteration41_capability_bundle as verifier
from scripts.ci.verify_iteration41_capability_bundle import (
    _PYTHON_SOCKET_GUARD_DESCRIPTION,
    _REDACTION_SENTINEL_ENV,
    FAILED_RESULT,
    LOCAL_ONLY_RESULT,
    BundleVerificationError,
    ProjectSpec,
    _capture_source_snapshot,
    _installed_record_rows,
    _offline_environment,
    _parse_consumer_probe_payload,
    _recheck_source_snapshot,
    _record_contract,
    _repackage_distribution,
    _run_logged,
    _stage_snapshot,
    _validate_consumer_probe_payload,
    _write_receipt_atomically,
    main,
    verify,
)

SDK_ROOT = Path(__file__).resolve().parents[1]


def _backtrader_root() -> Path:
    """Locate the optional sibling checkout without hard-coding a drive path."""

    candidates = []
    configured = os.environ.get("BACKTRADER_ROOT")
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        (
            SDK_ROOT.parent / "source_code" / "backtrader",
            SDK_ROOT.parent / "backtrader",
        )
    )
    for candidate in candidates:
        if (candidate / "setup.py").is_file() and (candidate / "backtrader").is_dir():
            return candidate.resolve()
    pytest.skip(
        "set BACKTRADER_ROOT to run the full local capability-bundle integration test"
    )


def _record_hash(payload: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(
        hashlib.sha256(payload).digest()
    ).decode().rstrip("=")


def _write_controller_distribution(
    site_root: Path,
    *,
    name: str,
    version: str,
    payloads: dict[str, bytes],
) -> metadata.Distribution:
    """Create a minimal controller install with a valid RECORD for unit tests."""

    metadata_name = f"{name.replace('-', '_')}-{version}.dist-info"
    metadata_payload = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    ).encode()
    members = {**payloads, f"{metadata_name}/METADATA": metadata_payload}
    for relative, payload in members.items():
        target = site_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    rows = [
        f"{relative},{_record_hash(payload)},{len(payload)}"
        for relative, payload in sorted(members.items())
    ]
    rows.append(f"{metadata_name}/RECORD,,")
    metadata_root = site_root / metadata_name
    (metadata_root / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return metadata.PathDistribution(metadata_root)


@pytest.mark.integration
def test_local_bundle_builds_reproducible_wheels_and_uses_only_installed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full matrix proves only local mechanics, never release eligibility."""

    monkeypatch.setenv(_REDACTION_SENTINEL_ENV, "controller-secret-must-not-leak")
    receipt = verify(
        artifacts_dir=tmp_path / "iteration41-bundle",
        backtrader_root=_backtrader_root(),
        sdk_root=SDK_ROOT,
    )

    assert receipt["result"] == LOCAL_ONLY_RESULT
    assert receipt["local_validation"] == "PASSED"
    assert receipt["release_status"] == "NOT_RELEASE_ELIGIBLE"
    assert receipt["consumer"]["local_wheelhouse_only"] is True
    assert receipt["consumer"]["probe_payload"]["provider_evidence"] == "fixture_only"
    assert receipt["consumer"]["probe_payload"]["fake_provider_calls"] == 1
    assert receipt["consumer"]["probe_payload"]["network_guard_attempts"] == []
    assert receipt["consumer"]["probe_payload"]["local_backtest_report"]["status"] == (
        verifier._LOCAL_BACKTEST_EXPECTED_STATUS
    )
    assert all(
        receipt["consumer"]["probe_payload"]["local_backtest_report"][field] == 0
        for field in (
            "external_network_requests",
            "external_write_requests",
            "actual_fills",
            "provider_submissions",
        )
    )
    assert {
        runtime_id: report["status"]
        for runtime_id, report in receipt["consumer"]["probe_payload"]["l2_fixture_reports"].items()
    } == verifier._L2_FIXTURE_EXPECTED_STATUSES
    assert all(
        report["external_network_requests"] == 0
        and report["external_write_requests"] == 0
        and report["actual_fills"] == 0
        for report in receipt["consumer"]["probe_payload"]["l2_fixture_reports"].values()
    )
    assert (
        receipt["consumer"]["probe_payload"]["controller_environment_sentinel_absent"]
        is True
    )
    assert "bt_api_base" in receipt["consumer"]["expected_local_projects"]
    assert set(receipt["consumer"]["expected_local_projects"]) == set(
        receipt["local_wheels"]
    )
    assert all(
        "site-packages" in path.replace("\\", "/").lower()
        for path in receipt["consumer"]["probe_payload"]["module_paths"].values()
    )
    assert all(item["record_validated"] for item in receipt["local_wheels"].values())
    assert all(item["reproducible_build"] for item in receipt["local_wheels"].values())
    assert all(
        item["captured_from_bytes"] for item in receipt["source_snapshots"].values()
    )
    assert receipt["wheelhouse_manifest"]["wheel_count"] >= len(receipt["local_wheels"])
    assert len(receipt["wheelhouse_manifest"]["sha256"]) == 64
    assert (tmp_path / "iteration41-bundle" / "receipt.json").is_file()


def test_source_snapshot_freezes_bytes_and_detects_original_mutation(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    package = source_root / "fixture_package"
    package.mkdir(parents=True)
    (source_root / "setup.py").write_text(
        "from setuptools import setup\nsetup(name='fixture')\n"
    )
    tracked = package / "__init__.py"
    tracked.write_text("VALUE = 'before'\n", encoding="utf-8")
    project = ProjectSpec(
        key="fixture",
        distribution="fixture",
        module="fixture_package",
        source_root=source_root,
        includes=("setup.py", "fixture_package"),
    )

    snapshot = _capture_source_snapshot(project, tmp_path / "frozen")
    tracked.write_text("VALUE = 'after'\n", encoding="utf-8")

    build_source = tmp_path / "build-source"
    _stage_snapshot(snapshot, build_source)
    assert (build_source / "fixture_package" / "__init__.py").read_text(
        encoding="utf-8"
    ) == ("VALUE = 'before'\n")
    with pytest.raises(BundleVerificationError, match="source content changed"):
        _recheck_source_snapshot(snapshot)


def test_repackaging_rejects_poisoned_editable_startup_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site_root = tmp_path / "site-packages"
    site_root.mkdir()
    poison = tmp_path / "poisoned.txt"
    distribution = _write_controller_distribution(
        site_root,
        name="evil-editable",
        version="1.0",
        payloads={
            "__editable__.evil_editable-1.0.pth": (
                "import pathlib; pathlib.Path("
                + repr(str(poison))
                + ").write_text('executed')\n"
            ).encode("utf-8"),
        },
    )
    monkeypatch.setattr(verifier, "_site_roots", lambda: (site_root.resolve(),))

    with pytest.raises(BundleVerificationError, match="startup hook"):
        _repackage_distribution(distribution, tmp_path / "wheelhouse")

    assert not poison.exists()
    assert not list((tmp_path / "wheelhouse").glob("*.whl"))


def test_repackaging_rejects_external_startup_hook_before_skipping_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site_root = tmp_path / "site-packages"
    site_root.mkdir()
    distribution = _write_controller_distribution(
        site_root,
        name="external-hook",
        version="1.0",
        payloads={"external_hook.py": b"VALUE = 'safe'\n"},
    )
    metadata_root = site_root / "external_hook-1.0.dist-info"
    with (metadata_root / "RECORD").open("a", encoding="utf-8") as handle:
        handle.write("../../Scripts/external_hook.pth,,\n")
    distribution = metadata.PathDistribution(metadata_root)
    monkeypatch.setattr(verifier, "_site_roots", lambda: (site_root.resolve(),))

    with pytest.raises(BundleVerificationError, match="startup hook"):
        _repackage_distribution(distribution, tmp_path / "wheelhouse")

    assert not list((tmp_path / "wheelhouse").glob("*.whl"))


def test_repackaging_skips_missing_nonruntime_record_member_and_marks_origin_untrusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale controller doc/data entry never becomes an external wheel copy."""

    site_root = tmp_path / "site-packages"
    site_root.mkdir()
    distribution = _write_controller_distribution(
        site_root,
        name="stale-data-record",
        version="1.0",
        payloads={"stale_data_record.py": b"VALUE = 'installed'\n"},
    )
    metadata_root = site_root / "stale_data_record-1.0.dist-info"
    # ``fonttools``-style wheel records can retain a static `.data/data` man
    # page even though that payload was not installed into site-packages.  It
    # must not cause the repackage step to read from any outside controller
    # path.
    with (metadata_root / "RECORD").open("a", encoding="utf-8") as handle:
        handle.write("stale_data_record-1.0.data/data/share/man/man1/ttx.1,,\n")
    distribution = metadata.PathDistribution(metadata_root)
    monkeypatch.setattr(verifier, "_site_roots", lambda: (site_root.resolve(),))

    wheel, origin = _repackage_distribution(distribution, tmp_path / "wheelhouse")

    assert origin["controller_record_status"] == "UNTRUSTED_CONTROLLER_REPACK"
    assert (
        origin["controller_record_reason"] == "UNREPACKAGED_CONTROLLER_RECORD_MEMBERS"
    )
    assert origin["skipped_controller_member_categories"] == {
        "missing_or_non_regular_member": 1
    }
    with zipfile.ZipFile(wheel) as archive:
        assert "stale_data_record.py" in archive.namelist()
        assert not any(name.endswith("ttx.1") for name in archive.namelist())


def test_repackaging_omits_existing_external_runtime_member_and_import_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An external `.py` never enters a repackaged wheel or fake consumer PASS."""

    site_root = tmp_path / "site-packages"
    site_root.mkdir()
    distribution = _write_controller_distribution(
        site_root,
        name="external-runtime",
        version="1.0",
        payloads={"external_runtime.py": b"VALUE = 'safe'\n"},
    )
    metadata_root = site_root / "external_runtime-1.0.dist-info"
    member = "external_runtime.py"
    outside = tmp_path / "outside" / member
    outside.parent.mkdir()
    outside.write_text("outside-controller-bytes", encoding="utf-8")

    class ExternalStaticPathDistribution(metadata.PathDistribution):
        def locate_file(self, path: object) -> Path:
            if str(path) == member:
                return outside
            return super().locate_file(path)

    distribution = ExternalStaticPathDistribution(metadata_root)
    monkeypatch.setattr(verifier, "_site_roots", lambda: (site_root.resolve(),))

    wheel, origin = _repackage_distribution(distribution, tmp_path / "wheelhouse")

    assert origin["controller_record_status"] == "UNTRUSTED_CONTROLLER_REPACK"
    assert origin["skipped_controller_member_categories"] == {"external_member": 1}
    with zipfile.ZipFile(wheel) as archive:
        assert member not in archive.namelist()
        assert "outside-controller-bytes" not in archive.read(
            "external_runtime-1.0.dist-info/METADATA"
        ).decode("utf-8")

    venv_dir = tmp_path / "venv"
    create = _run_logged(
        [sys.executable, "-m", "venv", str(venv_dir)],
        cwd=tmp_path,
        environment=_offline_environment(),
        logs_dir=tmp_path / "logs",
        name="external-member-venv",
    )
    assert create["exit_code"] == 0
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    install = _run_logged(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--force-reinstall",
            str(wheel),
        ],
        cwd=tmp_path,
        environment=_offline_environment(),
        logs_dir=tmp_path / "logs",
        name="external-member-install",
    )
    assert install["exit_code"] == 0
    probe = _run_logged(
        [str(python), "-I", "-c", "import external_runtime"],
        cwd=tmp_path,
        environment=_offline_environment(),
        logs_dir=tmp_path / "logs",
        name="external-member-probe",
    )
    assert probe["exit_code"] != 0
    assert "ModuleNotFoundError" in probe["stderr"]


def test_repackaging_omits_nonconsole_unsafe_record_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path escape is never copied, even though the offline wheel can be built."""

    site_root = tmp_path / "site-packages"
    site_root.mkdir()
    distribution = _write_controller_distribution(
        site_root,
        name="unsafe-record",
        version="1.0",
        payloads={"unsafe_record.py": b"VALUE = 'safe'\n"},
    )
    metadata_root = site_root / "unsafe_record-1.0.dist-info"
    with (metadata_root / "RECORD").open("a", encoding="utf-8") as handle:
        handle.write("../../outside.txt,,\n")
    distribution = metadata.PathDistribution(metadata_root)
    monkeypatch.setattr(verifier, "_site_roots", lambda: (site_root.resolve(),))

    wheel, origin = _repackage_distribution(distribution, tmp_path / "wheelhouse")

    assert origin["controller_record_status"] == "UNTRUSTED_CONTROLLER_REPACK"
    assert origin["skipped_controller_member_categories"] == {"unsafe_relative_path": 1}
    with zipfile.ZipFile(wheel) as archive:
        assert "unsafe_record.py" in archive.namelist()


@pytest.mark.parametrize(
    "member",
    (
        "static_hook-1.0.data/data/share/evil.pth ",
        "static_hook-1.0.data/data/share/sitecustomize.py.",
    ),
)
def test_repackaging_rejects_startup_hook_inside_static_data_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    member: str,
) -> None:
    """Omitting an unsafe member cannot suppress startup-hook rejection."""

    site_root = tmp_path / "site-packages"
    site_root.mkdir()
    distribution = _write_controller_distribution(
        site_root,
        name="static-hook",
        version="1.0",
        payloads={"static_hook.py": b"VALUE = 'safe'\n"},
    )
    metadata_root = site_root / "static_hook-1.0.dist-info"
    with (metadata_root / "RECORD").open("a", encoding="utf-8") as handle:
        handle.write(f"{member},,\n")
    distribution = metadata.PathDistribution(metadata_root)
    monkeypatch.setattr(verifier, "_site_roots", lambda: (site_root.resolve(),))

    with pytest.raises(BundleVerificationError, match="startup hook"):
        _repackage_distribution(distribution, tmp_path / "wheelhouse")

    assert not list((tmp_path / "wheelhouse").glob("*.whl"))


@pytest.mark.parametrize("member", ("evil.pth ", "sitecustomize.py."))
def test_wheel_contract_rejects_windows_normalized_startup_hook(
    tmp_path: Path, member: str
) -> None:
    wheel = tmp_path / "hostile-1.0-py3-none-any.whl"
    metadata_name = "hostile-1.0.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"{metadata_name}/METADATA",
            "Metadata-Version: 2.1\nName: hostile\nVersion: 1.0\n",
        )
        archive.writestr(member, "import os\n")
        archive.writestr(f"{metadata_name}/RECORD", "")

    with pytest.raises(BundleVerificationError, match="startup hook"):
        _record_contract(wheel, "hostile")


def test_wheel_contract_rejects_windows_device_member(tmp_path: Path) -> None:
    wheel = tmp_path / "hostile-device-1.0-py3-none-any.whl"
    metadata_name = "hostile_device-1.0.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"{metadata_name}/METADATA",
            "Metadata-Version: 2.1\nName: hostile-device\nVersion: 1.0\n",
        )
        archive.writestr("NUL.txt", "payload")
        archive.writestr(f"{metadata_name}/RECORD", "")

    with pytest.raises(BundleVerificationError, match="unsafe archive member"):
        _record_contract(wheel, "hostile-device")


def test_repackaging_strips_controller_metadata_and_keeps_record_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site_root = tmp_path / "site-packages"
    site_root.mkdir()
    distribution = _write_controller_distribution(
        site_root,
        name="benign-controller-package",
        version="1.0",
        payloads={
            "benign_controller_package.py": b"VALUE = 'safe'\n",
            "benign_controller_package-1.0.dist-info/direct_url.json": b'{"url":"file:///tmp"}',
            "benign_controller_package-1.0.dist-info/INSTALLER": b"pip\n",
        },
    )
    monkeypatch.setattr(verifier, "_site_roots", lambda: (site_root.resolve(),))

    wheel, origin = _repackage_distribution(distribution, tmp_path / "wheelhouse")

    assert origin["origin"] == "controller_site_packages_repack"
    assert origin["controller_record_status"] == "VALIDATED_CONTROLLER_RECORD"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    assert not any(
        name.casefold().endswith(("direct_url.json", "installer", ".pth", ".egg-link"))
        for name in names
    )


def test_editable_direct_url_is_rejected_even_without_a_pth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site_root = tmp_path / "site-packages"
    site_root.mkdir()
    distribution = _write_controller_distribution(
        site_root,
        name="editable-metadata-only",
        version="1.0",
        payloads={
            "editable_metadata_only.py": b"VALUE = 'not-used'\n",
            "editable_metadata_only-1.0.dist-info/direct_url.json": (
                b'{"url":"file:///tmp/source","dir_info":{"editable":true}}'
            ),
        },
    )
    monkeypatch.setattr(verifier, "_site_roots", lambda: (site_root.resolve(),))

    with pytest.raises(BundleVerificationError, match="refusing editable controller"):
        _repackage_distribution(distribution, tmp_path / "wheelhouse")


def test_installed_record_allows_pip_wheel_url_and_console_script_only(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "venv" / "Lib" / "site-packages"
    metadata_root = site_root / "fixture-1.0.dist-info"
    metadata_root.mkdir(parents=True)
    (metadata_root / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (metadata_root / "direct_url.json").write_text(
        '{"url":"file:///tmp/fixture-1.0.whl"}', encoding="utf-8"
    )
    (metadata_root / "RECORD").write_text(
        "fixture-1.0.dist-info/METADATA,,\n"
        "fixture-1.0.dist-info/direct_url.json,,\n"
        "fixture-1.0.dist-info/RECORD,,\n"
        "../../Scripts/fixture.exe,,\n",
        encoding="utf-8",
    )

    rows = _installed_record_rows(metadata_root, roots=(site_root,))

    assert "fixture-1.0.dist-info/direct_url.json" in rows
    assert "../../Scripts/fixture.exe" not in rows
    with (metadata_root / "RECORD").open("a", encoding="utf-8") as handle:
        handle.write("../../Scripts/fixture.pth , ,\n")
    with pytest.raises(BundleVerificationError, match="startup hook"):
        _installed_record_rows(metadata_root, roots=(site_root,))


def test_minimal_environment_redacts_controller_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_REDACTION_SENTINEL_ENV, "secret")
    monkeypatch.setenv("PYTHONPATH", r"C:\ambient-source")

    environment = _offline_environment()

    assert _REDACTION_SENTINEL_ENV not in environment
    assert "PYTHONPATH" not in environment
    assert environment["PIP_NO_INDEX"] == "1"
    if os.name == "nt":
        platform_probe = _run_logged(
            [
                str(Path(environment["SYSTEMROOT"]) / "System32" / "cmd.exe"),
                "/c",
                "ver",
            ],
            cwd=tmp_path,
            environment=environment,
            logs_dir=tmp_path / "logs",
            name="windows-platform-probe",
        )
        assert platform_probe["exit_code"] == 0


def test_probe_parser_and_log_capture_fail_closed_or_decode_robustly(
    tmp_path: Path,
) -> None:
    with pytest.raises(BundleVerificationError, match="did not emit JSON"):
        _parse_consumer_probe_payload("not json")
    with pytest.raises(BundleVerificationError, match="must be an object"):
        _parse_consumer_probe_payload("[]")

    run = _run_logged(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"],
        cwd=tmp_path,
        environment=_offline_environment(),
        logs_dir=tmp_path / "logs",
        name="non-utf8-output",
    )
    assert run["exit_code"] == 0
    assert "\ufffd" in run["stdout"]


def test_logs_and_failure_receipts_redact_secret_values(tmp_path: Path) -> None:
    secret = "top-secret-value"
    run = _run_logged(
        [
            sys.executable,
            "-c",
            f"print('api_key={secret}'); print('Authorization: Bearer {secret}')",
        ],
        cwd=tmp_path,
        environment=_offline_environment(),
        logs_dir=tmp_path / "logs",
        name="secret-output",
    )
    assert secret not in run["stdout"]
    assert "***REDACTED***" in run["stdout"]
    assert secret not in (tmp_path / "logs" / "secret-output.stdout.log").read_text(
        encoding="utf-8"
    )

    artifacts_dir = tmp_path / "failure-receipt"
    exit_code = main(
        [
            "--artifacts-dir",
            str(artifacts_dir),
            "--backtrader-root",
            str(tmp_path / f"api_key={secret}"),
            "--sdk-root",
            str(SDK_ROOT),
        ]
    )
    receipt_text = (artifacts_dir / "receipt.json").read_text(encoding="utf-8")
    assert exit_code == 1
    assert secret not in receipt_text
    assert "***REDACTED***" in receipt_text


def test_consumer_payload_validation_rejects_empty_and_forged_results(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "venv" / "Lib" / "site-packages"
    site_root.mkdir(parents=True)
    module = site_root / "fixture.py"
    module.write_text("VALUE = 'installed'\n", encoding="utf-8")
    metadata_root = site_root / "fixture-1.0.dist-info"
    metadata_root.mkdir()
    (metadata_root / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n",
        encoding="utf-8",
    )
    expected_projects = {
        "fixture": {
            "distribution": "fixture",
            "version": "1.0",
            "wheel_sha256": "a" * 64,
        }
    }
    expected_modules = {"fixture": ("fixture", "fixture.py")}
    bindings = {
        "fixture": {
            "metadata_path": str(metadata_root),
            "payload_paths": {"fixture.py": str(module)},
        }
    }
    payload = {
        "module_paths": {"fixture": str(module)},
        "package_versions": {"fixture": "1.0"},
        "installed_local_projects": {
            "fixture": {
                "distribution": "fixture",
                "version": "1.0",
                "wheel_sha256": "a" * 64,
                "metadata_path": str(metadata_root),
            }
        },
        "fake_provider_calls": 1,
        "execution_state": "ACKED",
        "local_backtest_report": {
            "status": verifier._LOCAL_BACKTEST_EXPECTED_STATUS,
            "external_network_requests": 0,
            "external_write_requests": 0,
            "actual_fills": 0,
            "provider_submissions": 0,
            "actual_pnl": "NOT_APPLICABLE",
            "pnl_source": "local_backtest_no_orders",
            "data_bars": 4,
            "network_guard_attempts": [],
            "runtime_config": {
                "strategy_id": verifier._LOCAL_BACKTEST_EXPECTED_RUNTIME_ID,
                "mode": "backtest",
                "preset": "local_backtest",
                "environment": "local",
                "allows_network": False,
                "allows_external_writes": False,
                "allows_production_writes": False,
            },
        },
        "l2_fixture_reports": {
            runtime_id: {
                "status": status,
                "external_network_requests": 0,
                "external_write_requests": 0,
                "actual_fills": 0,
                "provider_submissions": 1,
            }
            for runtime_id, status in verifier._L2_FIXTURE_EXPECTED_STATUSES.items()
        },
        "network_guard": _PYTHON_SOCKET_GUARD_DESCRIPTION,
        "network_guard_attempts": [],
        "controller_environment_sentinel_absent": True,
        "provider_evidence": "fixture_only",
    }

    with pytest.raises(BundleVerificationError, match="unexpected result schema"):
        _validate_consumer_probe_payload(
            {},
            expected_projects=expected_projects,
            expected_modules=expected_modules,
            installation_bindings=bindings,
            site_roots=(site_root,),
        )
    forged = json.loads(json.dumps(payload))
    forged["installed_local_projects"]["fixture"]["wheel_sha256"] = "b" * 64
    with pytest.raises(BundleVerificationError, match="wheel binding mismatch"):
        _validate_consumer_probe_payload(
            forged,
            expected_projects=expected_projects,
            expected_modules=expected_modules,
            installation_bindings=bindings,
            site_roots=(site_root,),
        )
    forged_module = json.loads(json.dumps(payload))
    forged_module["module_paths"]["fixture"] = str(metadata_root / "METADATA")
    with pytest.raises(BundleVerificationError, match="module binding mismatch"):
        _validate_consumer_probe_payload(
            forged_module,
            expected_projects=expected_projects,
            expected_modules=expected_modules,
            installation_bindings=bindings,
            site_roots=(site_root,),
        )
    assert (
        _validate_consumer_probe_payload(
            payload,
            expected_projects=expected_projects,
            expected_modules=expected_modules,
            installation_bindings=bindings,
            site_roots=(site_root,),
        )["execution_state"]
        == "ACKED"
    )


def test_missing_backtrader_source_writes_a_non_pass_receipt(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "missing-source-receipt"

    exit_code = main(
        [
            "--artifacts-dir",
            str(artifacts_dir),
            "--backtrader-root",
            str(tmp_path / "not-present"),
            "--sdk-root",
            str(SDK_ROOT),
        ]
    )

    receipt = json.loads((artifacts_dir / "receipt.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert receipt["result"] == FAILED_RESULT
    assert receipt["local_validation"] == "FAILED"
    assert receipt["release_status"] == "NOT_RELEASE_ELIGIBLE"


def test_nonempty_artifacts_are_preserved_and_failure_receipt_is_sidecar(
    tmp_path: Path,
) -> None:
    artifacts_dir = tmp_path / "existing-artifacts"
    artifacts_dir.mkdir()
    preserved = artifacts_dir / "preserved-evidence.txt"
    preserved.write_text("do-not-overwrite", encoding="utf-8")
    original_receipt = artifacts_dir / "receipt.json"
    original_receipt.write_text("old receipt", encoding="utf-8")

    exit_codes = [
        main(
            [
                "--artifacts-dir",
                str(artifacts_dir),
                "--backtrader-root",
                str(tmp_path / "not-present"),
                "--sdk-root",
                str(SDK_ROOT),
            ]
        ),
        main(
            [
                "--artifacts-dir",
                str(artifacts_dir),
                "--backtrader-root",
                str(tmp_path / "not-present"),
                "--sdk-root",
                str(SDK_ROOT),
            ]
        ),
    ]

    sidecars = sorted(tmp_path.glob("existing-artifacts.failure-receipt*.json"))
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in sidecars]
    assert exit_codes == [1, 1]
    assert preserved.read_text(encoding="utf-8") == "do-not-overwrite"
    assert original_receipt.read_text(encoding="utf-8") == "old receipt"
    assert len(sidecars) == 2
    assert all(receipt["result"] == FAILED_RESULT for receipt in receipts)


def test_atomic_receipt_writer_does_not_clobber_existing_file(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    _write_receipt_atomically(receipt_path, {"result": "first"})

    with pytest.raises(FileExistsError):
        _write_receipt_atomically(receipt_path, {"result": "second"})

    assert json.loads(receipt_path.read_text(encoding="utf-8")) == {"result": "first"}
