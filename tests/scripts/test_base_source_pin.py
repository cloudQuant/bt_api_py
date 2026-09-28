"""Offline tests for binding a base wheel to the parent repository source pin."""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

import scripts.ci.base_source_pin as base_source_pin
from scripts.ci.base_source_pin import (
    BASE_ORIGIN,
    BaseSourcePinError,
    verify_base_source_pin,
)


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


def _make_repositories(
    root: Path,
    *,
    source_name: str = "bt_api_base",
    source_version: str = "0.15.5",
    minimum_version: str = "0.15.5",
) -> tuple[Path, Path, str]:
    parent = root / "parent"
    source = parent / "bt_api" / "bt_api_base"
    source.mkdir(parents=True)
    _git(parent, "init", "-q")
    _git(source, "init", "-q")
    _git(source, "config", "user.name", "CI source pin test")
    _git(source, "config", "user.email", "ci-source-pin@example.invalid")
    _git(source, "remote", "add", "origin", BASE_ORIGIN)

    (source / "pyproject.toml").write_text(
        f'[project]\nname = "{source_name}"\nversion = "{source_version}"\n',
        encoding="utf-8",
    )
    _commit(source, "source")
    source_sha = _git(source, "rev-parse", "HEAD")

    (parent / ".gitmodules").write_text(
        f'[submodule "bt_api/bt_api_base"]\n\tpath = bt_api/bt_api_base\n\turl = {BASE_ORIGIN}\n',
        encoding="utf-8",
    )
    (parent / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'parent-test'\n"
        "version = '0.0.0'\n"
        f"dependencies = ['bt_api_base>={minimum_version}']\n",
        encoding="utf-8",
    )
    _git(parent, "add", ".gitmodules", "pyproject.toml")
    _git(
        parent,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{source_sha},bt_api/bt_api_base",
    )
    _commit(parent, "parent")
    return parent, source, source_sha


def test_source_pin_uses_parent_gitlink_and_checks_origin_and_package_metadata(
    tmp_path: Path,
) -> None:
    parent, source, source_sha = _make_repositories(tmp_path)

    pin = verify_base_source_pin(parent)

    assert pin.source_commit == source_sha
    assert pin.source_tree == _git(source, "rev-parse", "HEAD^{tree}")
    assert pin.source_origin == BASE_ORIGIN
    assert pin.package_name == "bt_api_base"
    assert pin.package_version == "0.15.5"
    assert pin.minimum_version == "0.15.5"


def test_source_pin_rejects_a_noncanonical_gitmodule_origin(tmp_path: Path) -> None:
    parent, _, _ = _make_repositories(tmp_path)
    gitmodules = parent / ".gitmodules"
    gitmodules.write_text(
        gitmodules.read_text(encoding="utf-8").replace(
            BASE_ORIGIN, "https://example.invalid/base.git"
        ),
        encoding="utf-8",
    )
    _git(parent, "add", ".gitmodules")
    _commit(parent, "alter origin")

    with pytest.raises(BaseSourcePinError, match="origin mismatch"):
        verify_base_source_pin(parent)


def test_source_pin_does_not_log_credentials_from_a_remote_url(tmp_path: Path) -> None:
    parent, source, _ = _make_repositories(tmp_path)
    _git(source, "remote", "set-url", "origin", "https://user:fake-secret@example.invalid/base.git")

    with pytest.raises(BaseSourcePinError, match="origin mismatch") as error:
        verify_base_source_pin(parent)

    assert "fake-secret" not in str(error.value)


def test_pip_child_processes_receive_only_allowlisted_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, _, _ = _make_repositories(tmp_path)
    monkeypatch.setenv("CODECOV_TOKEN", "fake-codecov-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-github-secret")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://user:fake-pip-secret@example.invalid/simple")
    monkeypatch.setenv("PYTHONPATH", "fake-private-pythonpath")

    original_run = subprocess.run
    pip_child_environments: list[dict[str, str]] = []

    def capture_run(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess:
        if len(command) >= 3 and command[1:3] == ["-m", "pip"]:
            environment = kwargs["env"]
            assert isinstance(environment, dict)
            pip_child_environments.append(environment)
            if "wheel" in command:
                output_dir = Path(command[command.index("--wheel-dir") + 1])
                wheel_path = output_dir / "bt_api_base-0.15.5-py3-none-any.whl"
                with zipfile.ZipFile(wheel_path, "w") as wheel:
                    wheel.writestr(
                        "bt_api_base-0.15.5.dist-info/METADATA",
                        "Name: bt_api_base\nVersion: 0.15.5\n",
                    )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(base_source_pin.subprocess, "run", capture_run)
    monkeypatch.setattr(base_source_pin, "_verify_installed_wheel", lambda _receipt: None)

    base_source_pin.build_and_install_base_wheel(parent, tmp_path / "wheelhouse")

    assert len(pip_child_environments) == 2
    for environment in pip_child_environments:
        allowed_keys = set(base_source_pin.SUBPROCESS_ENV_ALLOWLIST) | {
            "PIP_CONFIG_FILE",
            "PIP_INDEX_URL",
        }
        assert set(environment) <= allowed_keys
        assert "CODECOV_TOKEN" not in environment
        assert "GITHUB_TOKEN" not in environment
        assert "PIP_EXTRA_INDEX_URL" not in environment
        assert "PYTHONPATH" not in environment
        for name in base_source_pin.SUBPROCESS_ENV_ALLOWLIST:
            if name in os.environ:
                assert environment[name] == os.environ[name]
        assert environment["PIP_CONFIG_FILE"] == os.devnull
        assert "fake-codecov-secret" not in json.dumps(environment)
        assert "fake-github-secret" not in json.dumps(environment)
        assert "fake-pip-secret" not in json.dumps(environment)
    assert pip_child_environments[0]["PIP_INDEX_URL"] == "https://pypi.org/simple/"
    assert "PIP_INDEX_URL" not in pip_child_environments[1]


def test_source_pin_rejects_a_checkout_that_does_not_match_the_gitlink(
    tmp_path: Path,
) -> None:
    parent, source, source_sha = _make_repositories(tmp_path)
    (source / "README.md").write_text("different checkout\n", encoding="utf-8")
    _commit(source, "advance source")

    with pytest.raises(BaseSourcePinError, match=f"does not match parent gitlink {source_sha}"):
        verify_base_source_pin(parent)


@pytest.mark.parametrize(
    ("source_name", "source_version", "minimum_version", "expected"),
    [
        ("unrelated_package", "0.15.5", "0.15.5", "package name"),
        ("bt_api_base", "0.15.4", "0.15.5", "does not satisfy parent"),
        ("bt_api_base", "0.15.5", "0.15.4", "lower bound must remain >=0.15.5"),
    ],
)
def test_source_pin_rejects_wrong_package_identity_or_version_floor(
    tmp_path: Path,
    source_name: str,
    source_version: str,
    minimum_version: str,
    expected: str,
) -> None:
    parent, _, _ = _make_repositories(
        tmp_path,
        source_name=source_name,
        source_version=source_version,
        minimum_version=minimum_version,
    )

    with pytest.raises(BaseSourcePinError, match=expected):
        verify_base_source_pin(parent)
