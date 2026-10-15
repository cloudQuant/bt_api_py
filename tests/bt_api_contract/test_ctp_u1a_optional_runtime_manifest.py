"""Source-fake tests for the protected direct CTP dependency identity."""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import pytest

from bt_api_py import _ctp_execution_authorization as authorization_module
from bt_api_py import bt_api as bt_api_module

_OPTIONAL_DIRECT_EXTRAS = frozenset({"pyzmq", "spdlog"})
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _SyntheticDistribution:
    def __init__(self, name: str, root: Path) -> None:
        self.metadata = {"Name": name}
        self.version = "1.0.0.synthetic"
        self.files = (PurePosixPath("synthetic_module.py"),)
        self._root = root

    def locate_file(self, relative: PurePosixPath) -> Path:
        return self._root / str(relative)


class _SyntheticSession:
    config = {
        "strategy_id": "synthetic-strategy",
        "strategy_identity_sha256": "a" * 64,
    }
    _ctp_approval_revoked_ids = set()
    _ctp_approval_revoked_nonces = set()
    _ctp_approval_revocation_snapshot_version = 1


def _install_metadata_fixture(monkeypatch, tmp_path, *, missing=frozenset()):
    calls: list[str] = []

    def distribution(name: str) -> _SyntheticDistribution:
        calls.append(name)
        if name in missing:
            raise importlib.metadata.PackageNotFoundError(name)
        root = tmp_path / name.replace("-", "_")
        root.mkdir(parents=True, exist_ok=True)
        (root / "synthetic_module.py").write_text(
            f"# source fixture for {name}\n", encoding="utf-8"
        )
        return _SyntheticDistribution(name, root)

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)
    return calls


def test_direct_ctp_manifest_ignores_optional_transport_and_logger_distributions(
    monkeypatch, tmp_path
):
    assert _OPTIONAL_DIRECT_EXTRAS.isdisjoint(bt_api_module._U1A_RUNTIME_DISTRIBUTIONS)

    calls = _install_metadata_fixture(monkeypatch, tmp_path, missing=_OPTIONAL_DIRECT_EXTRAS)
    without_extras = bt_api_module._dependency_source_manifest_digest()
    assert re.fullmatch(r"[0-9a-f]{64}", without_extras)
    assert set(calls) == set(bt_api_module._U1A_RUNTIME_DISTRIBUTIONS)
    assert _OPTIONAL_DIRECT_EXTRAS.isdisjoint(calls)

    calls_with_extras = _install_metadata_fixture(monkeypatch, tmp_path)
    with_extras_available = bt_api_module._dependency_source_manifest_digest()
    assert with_extras_available == without_extras
    assert _OPTIONAL_DIRECT_EXTRAS.isdisjoint(calls_with_extras)


def test_removing_optional_extras_changes_the_prior_approval_identity(monkeypatch, tmp_path):
    current_names = bt_api_module._U1A_RUNTIME_DISTRIBUTIONS
    previous_names = (
        *current_names[:8],
        "spdlog",
        *current_names[8:11],
        "pyzmq",
        *current_names[11:],
    )
    _install_metadata_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        bt_api_module,
        "_U1A_RUNTIME_DISTRIBUTIONS",
        previous_names,
    )
    prior_manifest_digest = bt_api_module._dependency_source_manifest_digest()

    monkeypatch.setattr(bt_api_module, "_U1A_RUNTIME_DISTRIBUTIONS", current_names)
    current_manifest_digest = bt_api_module._dependency_source_manifest_digest()

    # The arm path binds and compares this digest; old approvals must be reissued.
    assert current_manifest_digest != prior_manifest_digest


def test_direct_ctp_manifest_still_fails_closed_for_a_required_distribution(monkeypatch, tmp_path):
    required = "numpy"
    assert required in bt_api_module._U1A_RUNTIME_DISTRIBUTIONS
    calls = _install_metadata_fixture(monkeypatch, tmp_path, missing={required})

    with pytest.raises(ValueError, match="^dependency_identity_unavailable$"):
        bt_api_module._dependency_source_manifest_digest()

    assert required in calls
    assert _OPTIONAL_DIRECT_EXTRAS.isdisjoint(calls)


@pytest.mark.parametrize("invalid_field", ["name", "version", "files_none", "files_empty"])
def test_required_distribution_with_incomplete_metadata_fails_closed(
    monkeypatch, tmp_path, invalid_field
):
    distribution = _SyntheticDistribution("numpy", tmp_path)
    if invalid_field == "name":
        distribution.metadata = {}
    elif invalid_field == "version":
        distribution.version = ""
    elif invalid_field == "files_none":
        distribution.files = None
    else:
        distribution.files = ()
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: distribution)

    with pytest.raises(ValueError, match="^dependency_identity_unavailable$"):
        bt_api_module._dependency_source_manifest_digest()


def test_previous_approval_digest_is_rejected_by_the_existing_entry_material_gate(
    monkeypatch, tmp_path
):
    current_names = bt_api_module._U1A_RUNTIME_DISTRIBUTIONS
    previous_names = (
        *current_names[:8],
        "spdlog",
        *current_names[8:11],
        "pyzmq",
        *current_names[11:],
    )
    _install_metadata_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bt_api_module, "_U1A_RUNTIME_DISTRIBUTIONS", previous_names)
    previous_digest = bt_api_module._dependency_source_manifest_digest()
    monkeypatch.setattr(bt_api_module, "_U1A_RUNTIME_DISTRIBUTIONS", current_names)
    current_digest = bt_api_module._dependency_source_manifest_digest()
    assert current_digest != previous_digest

    api = object.__new__(bt_api_module.BtApi)
    session = _SyntheticSession()
    now = datetime.now(UTC)
    payload = {
        "schema_version": "ctp-execution-entry-approval-v1",
        "purpose": bt_api_module.APPROVAL_PURPOSE,
        "approval_id": "synthetic-approval",
        "nonce": "synthetic-nonce",
        "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "strategy_id": session.config["strategy_id"],
        "strategy_identity_sha256": session.config["strategy_identity_sha256"],
        "native_sha256": "b" * 64,
        "bt_api_ctp_sha256": "c" * 64,
        "backtrader_sha256": "d" * 64,
        "bt_api_py_sha256": "e" * 64,
        "bt_api_base_sha256": "f" * 64,
        "dependency_hashes_sha256": previous_digest,
        "revocation_snapshot_version": 1,
    }
    approval = authorization_module.CtpExecutionApproval(
        payload=payload,
        payload_sha256="1" * 64,
        trust_root_sha256="2" * 64,
        signature=b"synthetic-signature",
        revocation_snapshot={},
        _seal=authorization_module._CAPABILITY_SEAL,
    )
    capability = bt_api_module.CtpExecutionApprovalCapability(
        seal=authorization_module._CAPABILITY_SEAL,
        owner=api,
        approval=approval,
    )
    api._reject_simnow_native_execution = lambda *_args, **_kwargs: None
    api._sole_ctp_execution_venue = lambda _operation: (session, "CTP___FUTURE")
    api._ctp_execution_runtime_identity = lambda: {
        "native_sha256": payload["native_sha256"],
        "ctp_package_sha256": payload["bt_api_ctp_sha256"],
    }
    api._ctp_execution_runtime_python_identity = lambda: {
        "backtrader_sha256": payload["backtrader_sha256"],
        "bt_api_py_sha256": payload["bt_api_py_sha256"],
        "bt_api_base_sha256": payload["bt_api_base_sha256"],
        "dependency_hashes_sha256": current_digest,
    }

    with pytest.raises(bt_api_module.NormalizedApiError) as raised:
        api._prepare_ctp_entry_arm_authorization(capability)

    assert raised.value.code == "ctp_execution_authorization_material_mismatch"
    assert capability._entry_used is True


def test_zmq_remains_an_explicit_transport_extra():
    root_project = tomllib.loads((_REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    base_project = tomllib.loads(
        (_REPOSITORY_ROOT / "bt_api" / "bt_api_base" / "pyproject.toml").read_text(encoding="utf-8")
    )
    root_defaults = root_project["project"]["dependencies"]
    base_defaults = base_project["project"]["dependencies"]

    assert not any("pyzmq" in requirement.lower() for requirement in root_defaults)
    assert not any("pyzmq" in requirement.lower() for requirement in base_defaults)
    assert base_project["project"]["optional-dependencies"]["transport-zmq"] == ["pyzmq>=26.0.0"]
    assert root_project["project"]["optional-dependencies"]["transport-zmq"] == [
        "bt_api_base[transport-zmq]>=0.15.8",
        "bt_api_transport_zmq>=0.1.0",
    ]
