"""Offline tests for binding the CTP CI wheel to its exact parent gitlink."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

import scripts.ci.ctp_source_pin as ctp_source_pin
from scripts.ci.base_source_pin import SUBPROCESS_ENV_ALLOWLIST
from scripts.ci.ctp_source_pin import (
    CTP_ORIGIN,
    CtpSourcePinError,
    _extract_pinned_archive,
    build_and_install_ctp_wheel,
    verify_ctp_source_pin,
)

BASE_ORIGIN = "https://github.com/cloudQuant/bt_api_base.git"


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repository: Path, message: str) -> None:
    _git(repository, "add", "-A")
    environment = {
        "GIT_AUTHOR_NAME": "CI source pin test",
        "GIT_AUTHOR_EMAIL": "ci-source-pin@example.invalid",
        "GIT_COMMITTER_NAME": "CI source pin test",
        "GIT_COMMITTER_EMAIL": "ci-source-pin@example.invalid",
    }
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", message],  # noqa: S607
        check=True,
        capture_output=True,
        env={**os.environ, **environment},
    )


def _make_source(path: Path, origin: str, pyproject: str, *, extra_file: str = "") -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "CI source pin test")
    _git(path, "config", "user.email", "ci-source-pin@example.invalid")
    _git(path, "remote", "add", "origin", origin)
    (path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    if extra_file:
        (path / "README.md").write_text(extra_file, encoding="utf-8")
    _commit(path, "source")
    return _git(path, "rev-parse", "HEAD")


def _make_repositories(
    root: Path,
    *,
    ctp_version: str = "2.0.3",
    base_version: str = "0.15.5",
    ctp_requirement: str = "bt_api_ctp>=2.0.3,<3.0",
    ctp_origin: str = CTP_ORIGIN,
) -> tuple[Path, Path, Path, str, str]:
    parent = root / "parent"
    base = parent / "bt_api" / "bt_api_base"
    ctp = parent / "bt_api" / "bt_api_ctp"
    base_sha = _make_source(
        base,
        BASE_ORIGIN,
        f'[project]\nname = "bt_api_base"\nversion = "{base_version}"\n',
    )
    ctp_sha = _make_source(
        ctp,
        ctp_origin,
        "[build-system]\nrequires = ['setuptools>=69', 'wheel']\n"
        "build-backend = 'setuptools.build_meta'\n"
        "[project]\nname = 'bt_api_ctp'\n"
        f"version = '{ctp_version}'\n"
        "requires-python = '>=3.9'\n"
        "dependencies = ['bt_api_base>=0.15.5,<1.0']\n",
        extra_file="pinned source snapshot\n",
    )
    (ctp / "setup.py").write_text("# native extension build marker\n", encoding="utf-8")
    _commit(ctp, "add setup marker")
    ctp_sha = _git(ctp, "rev-parse", "HEAD")

    parent.mkdir(exist_ok=True)
    _git(parent, "init", "-q")
    _git(parent, "config", "user.name", "CI source pin test")
    _git(parent, "config", "user.email", "ci-source-pin@example.invalid")
    (parent / ".gitmodules").write_text(
        f'[submodule "bt_api/bt_api_base"]\n\tpath = bt_api/bt_api_base\n\turl = {BASE_ORIGIN}\n'
        f'[submodule "bt_api/bt_api_ctp"]\n\tpath = bt_api/bt_api_ctp\n\turl = {ctp_origin}\n',
        encoding="utf-8",
    )
    (parent / "pyproject.toml").write_text(
        "[project]\nname = 'parent-test'\nversion = '0.0.0'\n"
        f"dependencies = ['bt_api_base>={base_version}']\n"
        "[project.optional-dependencies]\n"
        f"core-reference = ['{ctp_requirement}']\n",
        encoding="utf-8",
    )
    bundle = parent / "bt_api_py" / "configs" / "exchange-bundles.toml"
    bundle.parent.mkdir(parents=True)
    bundle.write_text(
        "[bundles.core-reference]\n"
        "description = 'test'\n"
        "[[bundles.core-reference.venues]]\n"
        "package = 'bt_api_ctp'\n"
        "plugin = 'ctp'\n"
        "exchange = 'CTP___FUTURE'\n"
        "min_version = '2.0.3'\n",
        encoding="utf-8",
    )
    _git(
        parent,
        "add",
        ".gitmodules",
        "pyproject.toml",
        "bt_api_py/configs/exchange-bundles.toml",
    )
    _git(
        parent,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{base_sha},bt_api/bt_api_base",
    )
    _git(
        parent,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{ctp_sha},bt_api/bt_api_ctp",
    )
    _commit(parent, "parent")
    return parent, base, ctp, base_sha, ctp_sha


def test_ctp_pin_reads_gitlink_and_validates_bundle_and_runtime_floors(
    tmp_path: Path,
) -> None:
    parent, _, ctp, _, ctp_sha = _make_repositories(tmp_path)

    pin = verify_ctp_source_pin(parent)

    assert pin.source_commit == ctp_sha
    assert pin.source_tree == _git(ctp, "rev-parse", "HEAD^{tree}")
    assert pin.source_origin == CTP_ORIGIN
    assert pin.package_name == "bt_api_ctp"
    assert pin.package_version == "2.0.3"
    assert pin.minimum_version == "2.0.3"
    assert pin.minimum_base_version == "0.15.5"


def test_ctp_archive_is_built_from_gitlink_and_ignores_worktree_edits(
    tmp_path: Path,
) -> None:
    parent, _, ctp, _, _ = _make_repositories(tmp_path)
    pin = verify_ctp_source_pin(parent)
    (ctp / "README.md").write_text("uncommitted change\n", encoding="utf-8")
    (ctp / "untracked.txt").write_text("not in gitlink\n", encoding="utf-8")

    archive_root = _extract_pinned_archive(pin, tmp_path / "extracted")

    assert (archive_root / "README.md").read_text(encoding="utf-8") == "pinned source snapshot\n"
    assert not (archive_root / "untracked.txt").exists()


@pytest.mark.parametrize(
    ("ctp_version", "ctp_requirement", "expected"),
    [
        ("2.0.2", "bt_api_ctp>=2.0.3,<3.0", "must be >=2.0.3"),
        ("2.0.3", "bt_api_ctp>=2.0.2,<3.0", "lower bound must remain >=2.0.3"),
    ],
)
def test_ctp_pin_rejects_source_or_parent_version_below_floor(
    tmp_path: Path, ctp_version: str, ctp_requirement: str, expected: str
) -> None:
    parent, *_ = _make_repositories(
        tmp_path, ctp_version=ctp_version, ctp_requirement=ctp_requirement
    )

    with pytest.raises(CtpSourcePinError, match=expected):
        verify_ctp_source_pin(parent)


def test_ctp_pin_rejects_a_noncanonical_source_origin(tmp_path: Path) -> None:
    parent, *_ = _make_repositories(tmp_path, ctp_origin="https://example.invalid/ctp.git")

    with pytest.raises(CtpSourcePinError, match="origin mismatch"):
        verify_ctp_source_pin(parent)


def test_build_install_receipt_uses_allowlisted_env_and_pep610_wheel_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, base, _, base_sha, _ = _make_repositories(tmp_path)
    base_tree = _git(base, "rev-parse", "HEAD^{tree}")
    fake_base_wheel = tmp_path / "base-wheelhouse" / "bt_api_base-0.15.5-py3-none-any.whl"
    fake_base_wheel.parent.mkdir()
    fake_base_wheel.write_bytes(b"verified base wheel placeholder")
    base_sha256 = hashlib.sha256(fake_base_wheel.read_bytes()).hexdigest()
    monkeypatch.setenv("BT_API_BASE_SOURCE_SHA", base_sha)
    monkeypatch.setenv("BT_API_BASE_SOURCE_TREE_SHA", base_tree)
    monkeypatch.setenv("BT_API_BASE_SOURCE_ORIGIN", BASE_ORIGIN)
    monkeypatch.setenv("BT_API_BASE_SOURCE_VERSION", "0.15.5")
    monkeypatch.setenv("BT_API_BASE_WHEEL_PATH", str(fake_base_wheel))
    monkeypatch.setenv("BT_API_BASE_WHEEL_SHA256", base_sha256)
    monkeypatch.setenv("CODECOV_TOKEN", "fake-codecov-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-github-secret")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://user:fake-pip-secret@example.invalid/simple")
    monkeypatch.setenv("PYTHONPATH", "fake-private-pythonpath")

    direct_urls: dict[str, dict[str, Any]] = {
        "bt_api_base": {
            "url": fake_base_wheel.resolve().as_uri(),
            "archive_info": {"hashes": {"sha256": base_sha256}},
        }
    }

    class FakeDistribution:
        def __init__(self, package: str) -> None:
            self.version = "0.15.5" if package == "bt_api_base" else "2.0.3"
            self._package = package

        def read_text(self, filename: str) -> str | None:
            assert filename == "direct_url.json"
            return json.dumps(direct_urls.get(self._package, {}))

    from importlib import metadata

    monkeypatch.setattr(metadata, "distribution", lambda package: FakeDistribution(package))

    original_run = subprocess.run
    pip_child_environments: list[dict[str, str]] = []
    built_ctp_wheel: Path | None = None

    def fake_run(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess:
        nonlocal built_ctp_wheel
        if len(command) >= 4 and command[1:3] == ["-m", "pip"]:
            environment = kwargs.get("env")
            assert isinstance(environment, dict)
            pip_child_environments.append(environment)
            if command[3] == "wheel":
                assert "--no-deps" in command
                source_root = Path(command[-1]).resolve()
                assert source_root.parent != (parent / "bt_api" / "bt_api_ctp").resolve()
                output_dir = Path(command[command.index("--wheel-dir") + 1])
                built_ctp_wheel = output_dir / "bt_api_ctp-2.0.3-py3-none-any.whl"
                with zipfile.ZipFile(built_ctp_wheel, "w") as wheel:
                    wheel.writestr(
                        "bt_api_ctp-2.0.3.dist-info/METADATA",
                        "Name: bt_api_ctp\nVersion: 2.0.3\n"
                        "Requires-Dist: bt_api_base<1.0,>=0.15.5\n",
                    )
            elif command[3] == "install":
                assert "--no-deps" in command
                installed_wheel = Path(command[-1]).resolve()
                direct_urls["bt_api_ctp"] = {
                    "url": installed_wheel.as_uri(),
                    "archive_info": {
                        "hashes": {
                            "sha256": hashlib.sha256(installed_wheel.read_bytes()).hexdigest()
                        }
                    },
                }
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(ctp_source_pin.subprocess, "run", fake_run)
    receipt = build_and_install_ctp_wheel(parent, tmp_path / "ctp-wheelhouse")

    assert built_ctp_wheel is not None
    assert receipt.package_name == "bt_api_ctp"
    assert receipt.package_version == "2.0.3"
    assert receipt.source_commit == verify_ctp_source_pin(parent).source_commit
    assert receipt.wheel_sha256 == hashlib.sha256(receipt.wheel_path.read_bytes()).hexdigest()
    for name, value in {
        "BT_API_CTP_SOURCE_SHA": receipt.source_commit,
        "BT_API_CTP_SOURCE_TREE_SHA": receipt.source_tree,
        "BT_API_CTP_SOURCE_ORIGIN": receipt.source_origin,
        "BT_API_CTP_SOURCE_VERSION": receipt.package_version,
        "BT_API_CTP_WHEEL_PATH": str(receipt.wheel_path),
        "BT_API_CTP_WHEEL_SHA256": receipt.wheel_sha256,
    }.items():
        monkeypatch.setenv(name, value)
    assert ctp_source_pin._verify_installed_ctp_receipt(parent) == receipt

    direct_urls["bt_api_ctp"]["url"] = (
        "https://files.pythonhosted.org/packages/bt_api_ctp-2.0.3-py3-none-any.whl"
    )
    with pytest.raises(CtpSourcePinError, match="no local file PEP 610 origin"):
        ctp_source_pin._verify_installed_ctp_receipt(parent)

    direct_urls["bt_api_ctp"]["url"] = receipt.wheel_path.resolve().as_uri()
    original_wheel_bytes = receipt.wheel_path.read_bytes()
    receipt.wheel_path.write_bytes(original_wheel_bytes + b"changed after install")
    with pytest.raises(CtpSourcePinError, match="wheel changed after source-pin installation"):
        ctp_source_pin._verify_installed_ctp_receipt(parent)
    receipt.wheel_path.write_bytes(original_wheel_bytes)
    assert ctp_source_pin._verify_installed_ctp_receipt(parent) == receipt

    assert len(pip_child_environments) == 2
    for environment in pip_child_environments:
        allowed = set(SUBPROCESS_ENV_ALLOWLIST) | {"PIP_CONFIG_FILE", "PIP_INDEX_URL"}
        assert set(environment) <= allowed
        assert "CODECOV_TOKEN" not in environment
        assert "GITHUB_TOKEN" not in environment
        assert "PIP_EXTRA_INDEX_URL" not in environment
        assert "PYTHONPATH" not in environment
        assert "fake-codecov-secret" not in json.dumps(environment)
        assert "fake-github-secret" not in json.dumps(environment)
        assert "fake-pip-secret" not in json.dumps(environment)
        assert environment["PIP_CONFIG_FILE"] == os.devnull
    assert pip_child_environments[0]["PIP_INDEX_URL"] == "https://pypi.org/simple/"
    assert "PIP_INDEX_URL" not in pip_child_environments[1]
