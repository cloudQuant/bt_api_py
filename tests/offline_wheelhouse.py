"""Build temporary, fully local wheelhouses for installed-artifact tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
from wheel.wheelfile import WheelFile

from scripts.ci.offline_pip import pip_source_args, pip_source_environment

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASE_SOURCE = REPOSITORY_ROOT / "bt_api" / "bt_api_base"
PYTEST_SOCKET_VERSION = "0.7.0"


def _project_requirements(path: Path, section: str) -> list[str]:
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    return [str(item) for item in config.get("project", {}).get(section, [])]


def _build_requirements(path: Path) -> list[str]:
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    return [str(item) for item in config.get("build-system", {}).get("requires", [])]


def _installed_distributions() -> dict[str, metadata.Distribution]:
    distributions: dict[str, metadata.Distribution] = {}
    site_roots = {
        Path(sysconfig.get_paths()[key]).resolve()
        for key in ("purelib", "platlib")
        if sysconfig.get_paths().get(key)
    }
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
        if not any(metadata_path.parent.parent == root for root in site_roots):
            continue
        key = canonicalize_name(name)
        current = distributions.get(key)
        if current is None or Version(distribution.version) > Version(current.version):
            distributions[key] = distribution
    return distributions


def _copy_distribution_wheel(distribution: metadata.Distribution, wheelhouse: Path) -> Path:
    files = distribution.files or []
    metadata_file = next(
        (item for item in files if str(item).endswith(".dist-info/METADATA")), None
    )
    if metadata_file is None:
        raise RuntimeError(
            f"installed distribution {distribution.metadata['Name']} has no METADATA"
        )
    metadata_source = Path(str(distribution.locate_file(metadata_file))).resolve()

    site_roots = {
        Path(sysconfig.get_paths()[key]).resolve()
        for key in ("purelib", "platlib")
        if sysconfig.get_paths().get(key)
    }
    stage_parent = Path(tempfile.mkdtemp(prefix=".wheel-repack-", dir=wheelhouse))
    try:
        stage = stage_parent / "wheel"
        stage.mkdir()
        copied_metadata: Path | None = None
        for item in files:
            relative_text = str(item)
            if "__pycache__" in item.parts or relative_text.endswith(".pyc"):
                continue
            source = Path(str(distribution.locate_file(item))).resolve()
            site_root = next((root for root in site_roots if source.is_relative_to(root)), None)
            if site_root is None or not source.is_file():
                continue
            relative = source.relative_to(site_root)
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if source == metadata_source:
                copied_metadata = target

        if copied_metadata is None:
            raise RuntimeError(
                f"could not copy METADATA for installed distribution "
                f"{distribution.metadata['Name']}"
            )

        wheel_file = copied_metadata.parent / "WHEEL"
        if not wheel_file.is_file():
            wheel_contents = distribution.read_text("WHEEL")
            if wheel_contents is None:
                raise RuntimeError(
                    f"installed distribution {distribution.metadata['Name']} has no WHEEL metadata"
                )
            wheel_file.write_text(wheel_contents, encoding="utf-8")

        tags = [
            line.partition(":")[2].strip()
            for line in wheel_file.read_text(encoding="utf-8").splitlines()
            if line.startswith("Tag:")
        ]
        if not tags:
            raise RuntimeError(
                f"installed distribution {distribution.metadata['Name']} has no wheel tag"
            )
        python_tags, abi_tags, platform_tags = zip(
            *(tag.split("-", 2) for tag in tags), strict=True
        )
        distribution_name = canonicalize_name(distribution.metadata["Name"]).replace("-", "_")
        version = distribution.version.replace("-", "_")
        python_tag = ".".join(dict.fromkeys(python_tags))
        abi_tag = ".".join(dict.fromkeys(abi_tags))
        platform_tag = ".".join(dict.fromkeys(platform_tags))
        wheel_name = f"{distribution_name}-{version}-{python_tag}-{abi_tag}-{platform_tag}.whl"
        wheel_path = wheelhouse / wheel_name
        with WheelFile(wheel_path, "w") as archive:
            for source in sorted(stage.rglob("*")):
                if source.is_file() and source.name != "RECORD":
                    archive.write(source, source.relative_to(stage).as_posix())
        return wheel_path
    finally:
        shutil.rmtree(stage_parent)


def _repackage_dependency_closure(
    wheelhouse: Path, requirements: list[str], *, excluded: set[str] | None = None
) -> None:
    installed = _installed_distributions()
    excluded_names = {canonicalize_name(name) for name in (excluded or set())}
    copied: dict[str, metadata.Distribution] = {}
    pending = [Requirement(item) for item in requirements]

    while pending:
        requirement = pending.pop()
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        name = canonicalize_name(requirement.name)
        if name in excluded_names:
            continue
        distribution = installed.get(name)
        if distribution is None:
            raise RuntimeError(f"required local distribution is not installed: {requirement}")
        version = Version(distribution.version)
        if requirement.specifier and version not in requirement.specifier:
            raise RuntimeError(
                f"installed {distribution.metadata['Name']} {version} does not satisfy {requirement}"
            )
        if name in copied:
            continue

        copied[name] = distribution
        _copy_distribution_wheel(distribution, wheelhouse)
        pending.extend(Requirement(dependency) for dependency in distribution.requires or [])


def _write_pytest_socket_wheel(wheelhouse: Path) -> Path:
    """Write the small pytest-socket contract used by the isolated validator test."""
    filename = f"pytest_socket-{PYTEST_SOCKET_VERSION}-py3-none-any.whl"
    wheel_path = wheelhouse / filename
    metadata_path = "pytest_socket-0.7.0.dist-info"
    contents = {
        "pytest_socket.py": '''from __future__ import annotations

import socket


class SocketBlockedError(OSError):
    """Raised when a test opens a socket while socket access is disabled."""


_original_socket = socket.socket


def _blocked_socket(family=socket.AF_INET, *args, **kwargs):
    if _allow_unix_socket and family == socket.AF_UNIX:
        return _original_socket(family, *args, **kwargs)
    raise SocketBlockedError("socket access disabled by --disable-socket")


_allow_unix_socket = False


def pytest_addoption(parser):
    group = parser.getgroup("socket")
    group.addoption("--disable-socket", action="store_true", default=False)
    group.addoption("--allow-unix-socket", action="store_true", default=False)


def pytest_configure(config):
    global _allow_unix_socket
    if config.getoption("--disable-socket"):
        _allow_unix_socket = config.getoption("--allow-unix-socket")
        socket.socket = _blocked_socket
''',
        f"{metadata_path}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: pytest-socket\n"
            f"Version: {PYTEST_SOCKET_VERSION}\n"
            "Requires-Dist: pytest>=7.0\n\n"
        ),
        f"{metadata_path}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: bt_api_py tests.offline_wheelhouse\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
        f"{metadata_path}/entry_points.txt": "[pytest11]\npytest_socket = pytest_socket\n",
    }
    with WheelFile(str(wheel_path), "w") as archive:
        for path, value in contents.items():
            archive.writestr(path, value)
    return wheel_path


def build_validator_wheelhouse(destination: Path) -> Path:
    """Create a local wheelhouse for the temporary submodule validator fixtures."""
    wheelhouse = destination.resolve()
    wheelhouse.mkdir(parents=True, exist_ok=True)
    requirements = [
        "pytest>=7.0",
        "pytest-asyncio>=0.21.0",
        "pytz>=2023.3",
        "setuptools>=64",
        "wheel",
    ]
    _repackage_dependency_closure(wheelhouse, requirements)
    _write_pytest_socket_wheel(wheelhouse)
    return wheelhouse


def _build_local_base_wheel(wheelhouse: Path) -> None:
    if not (BASE_SOURCE / "pyproject.toml").is_file():
        raise RuntimeError(f"local bt_api_base source is unavailable: {BASE_SOURCE}")

    with tempfile.TemporaryDirectory(prefix="bt-api-base-wheel-source-") as temp_dir:
        source = Path(temp_dir) / "bt_api_base"
        shutil.copytree(
            BASE_SOURCE,
            source,
            ignore=shutil.ignore_patterns(
                ".git", "build", "dist", "*.egg-info", "__pycache__", ".pytest_cache"
            ),
        )
        environment = pip_source_environment(os.environ, wheelhouse)
        command = [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            *pip_source_args(wheelhouse),
            "--wheel-dir",
            str(wheelhouse),
            str(source),
        ]
        result = subprocess.run(
            command,
            cwd=temp_dir,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "local bt_api_base wheel build failed: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if not list(wheelhouse.glob("bt_api_base-0.15.4-*.whl")):
            raise RuntimeError("local bt_api_base build did not produce version 0.15.4")


def build_project_wheelhouse(destination: Path) -> Path:
    """Build a temporary root/base runtime dependency wheelhouse from local installs."""
    wheelhouse = destination.resolve()
    wheelhouse.mkdir(parents=True, exist_ok=True)
    root_pyproject = REPOSITORY_ROOT / "pyproject.toml"
    base_pyproject = BASE_SOURCE / "pyproject.toml"
    root_requirements = _project_requirements(root_pyproject, "dependencies")
    base_requirements = _project_requirements(base_pyproject, "dependencies")
    build_requirements = [
        *_build_requirements(root_pyproject),
        *_build_requirements(base_pyproject),
    ]

    root_requirements = [
        item
        for item in root_requirements
        if canonicalize_name(Requirement(item).name) != "bt-api-base"
    ]
    requirements = [*root_requirements, *base_requirements, *build_requirements]
    _repackage_dependency_closure(wheelhouse, requirements, excluded={"bt_api_base"})
    _build_local_base_wheel(wheelhouse)
    return wheelhouse
