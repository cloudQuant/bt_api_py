"""Prepare and verify an isolated Iteration 10 certlib package subcheck.

This script is intentionally not run during authoring.  When Root schedules it,
it copies only the pinned public certlib sources to a unique temporary project,
builds an sdist and wheel using a local wheelhouse, installs each artifact into
separate fresh virtual environments, and exercises the existing dry case entry
with socket connections denied.  No checkout .env, case config, or evidence
state is copied into either distribution.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    REPO_ROOT
    / "examples"
    / "live"
    / "000_live_certification"
    / "binance_penetration"
    / "certlib"
)
DIST_NAME = "bt-api-binance-certlib"
DIST_VERSION = "0.1.0"
SOURCE_NAMES = (
    "__init__.py",
    "binance_client.py",
    "context.py",
    "envfile.py",
    "monitor.py",
    "runner.py",
    "validation.py",
)

# Filled from the exact public checkout bytes when this script was authored.
SOURCE_SHA256 = {
    "__init__.py": "D00E80E6C9129ECE4D6F10BBE6C582ADDF6576D58E19CE012A8C551602097BAA",
    "binance_client.py": "95FF4186B3642ED9B6C4DF2934E1F1AAC740A0F302B8B882670F61AC78B90DB6",
    "context.py": "CC95002367F0C6241240E431F3B79C155793D260AA0897BB0E124C8A27C58BA9",
    "envfile.py": "DE3613EA601C87D2DCBD88B068375C270DC75C98AEDD29E0F8FCE132A6ECF30D",
    "monitor.py": "7E3BE314E2B24E11247207D62C6E31C9C1449A1A2804E66FDE7F12FE3BEDE5F7",
    "runner.py": "0C7754790655BFB4C0ECE0DFB2787854CC27D8807B3D59B758DD5A1DB2C2811D",
    "validation.py": "8996F4C4E5A1E841D16FF60CC7B68E848D6DA10933793855D709FE389C8FA78F",
    "pyproject.toml": "7A54FFEEFDDB0B53F16F7D74247669F369BCDE1930ADDFF3B8C3A257C2F97E5C",
}

BUILD_WHEELHOUSE_SHA256 = {
    "build-1.2.2.post1-py3-none-any.whl": "1d61c0887fa860c01971625baae8bdd338e517b836a2f70dd1f7aa3a6b2fc5b5",
    "colorama-0.4.6-py2.py3-none-any.whl": "4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6",
    "packaging-26.3-py3-none-any.whl": "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
    "pyproject_hooks-1.2.0-py3-none-any.whl": "9e5c6bfa8dcc30091c74b0cf803c81fdd29d94f01992a7707bc97babb1141913",
    "PyYAML-6.0.1-cp311-cp311-win_amd64.whl": "bf07ee2fef7014951eeb99f56f39c9bb4af143d8aa3c21b1677805985307da34",
    "setuptools-75.8.0-py3-none-any.whl": "e3982f444617239225d675215d51f6ba05f845d4eec313da4418fdbb56fb27e3",
    "wheel-0.43.0-py3-none-any.whl": "55c570405f142630c6b9f72fe09d9b67cf1477fcf543ae5b8dcb1f5b7377da81",
}
BUILD_REQUIREMENTS = (
    "build==1.2.2.post1",
    "colorama==0.4.6",
    "setuptools==75.8.0",
    "wheel==0.43.0",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_name(name: str) -> str:
    return "-".join(name.lower().replace("_", "-").replace(".", "-").split("-"))


def _safe_env(wheelhouse: Path | None = None) -> dict[str, str]:
    allowed = (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "LANG",
        "LC_ALL",
    )
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_NO_INDEX"] = "1"
    if wheelhouse is not None:
        env["PIP_FIND_LINKS"] = str(wheelhouse)
    return env


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    records: list[dict],
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    records.append(
        {
            "argv": argv,
            "cwd": str(cwd),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed with exit {result.returncode}: {argv[0]} {argv[1:]}"
        )
    return result


def _assert_source_pins() -> dict[str, str]:
    actual: dict[str, str] = {}
    for name, expected in SOURCE_SHA256.items():
        path = PACKAGE_ROOT / name
        raw = path.read_bytes()
        actual[name] = _sha256(raw)
        if actual[name] != expected.lower():
            raise RuntimeError(f"pinned source mismatch: {path}")
    return actual


def _assert_wheelhouse_pins(wheelhouse: Path) -> dict[str, str]:
    actual_names = {path.name for path in wheelhouse.iterdir() if path.is_file()}
    expected_names = set(BUILD_WHEELHOUSE_SHA256)
    if actual_names != expected_names:
        raise RuntimeError(
            "build wheelhouse file set mismatch: "
            f"unexpected={sorted(actual_names - expected_names)}, "
            f"missing={sorted(expected_names - actual_names)}"
        )
    actual: dict[str, str] = {}
    for name, expected in BUILD_WHEELHOUSE_SHA256.items():
        digest = _sha256((wheelhouse / name).read_bytes())
        if digest != expected:
            raise RuntimeError(f"build wheelhouse hash mismatch: {name}")
        actual[name] = digest
    return actual


def _stage_allowlisted_project(stage: Path) -> None:
    stage.mkdir(parents=True, exist_ok=False)
    for name in (*SOURCE_NAMES, "pyproject.toml"):
        shutil.copyfile(PACKAGE_ROOT / name, stage / name)
    actual = {path.name for path in stage.iterdir() if path.is_file()}
    expected = {*SOURCE_NAMES, "pyproject.toml"}
    if actual != expected:
        raise RuntimeError(f"staging file set mismatch: {sorted(actual ^ expected)}")


def _relative_sdist_files(archive: Path) -> tuple[str, set[str]]:
    with tarfile.open(archive, "r:gz") as tar:
        regular = [member for member in tar.getmembers() if member.isfile()]
        roots = {PurePosixPath(member.name).parts[0] for member in regular}
        if len(roots) != 1:
            raise RuntimeError(f"sdist has unexpected archive roots: {sorted(roots)}")
        root = next(iter(roots))
        files = {
            PurePosixPath(member.name).relative_to(root).as_posix()
            for member in regular
        }
        return root, files


def _verify_distribution_metadata(raw: bytes, *, artifact: str) -> None:
    metadata = Parser().parsestr(raw.decode("utf-8"))
    if metadata.get("Name") != DIST_NAME:
        raise RuntimeError(f"{artifact} metadata has an unexpected distribution name")
    if metadata.get("Version") != DIST_VERSION:
        raise RuntimeError(f"{artifact} metadata has an unexpected version")
    if metadata.get("Requires-Python") != ">=3.11":
        raise RuntimeError(f"{artifact} metadata has an unexpected Python requirement")
    requirements = [
        value.strip().lower()
        for value in metadata.get_all("Requires-Dist", [])
    ]
    if requirements != ["pyyaml<7,>=6.0"]:
        raise RuntimeError(
            f"{artifact} metadata dependencies are not exactly the expected PyYAML requirement: "
            f"{requirements}"
        )
    extras = metadata.get_all("Provides-Extra", [])
    if extras:
        raise RuntimeError(f"{artifact} metadata unexpectedly declares extras: {extras}")


def _verify_generated_sdist_setup_cfg(raw: bytes) -> None:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string(raw.decode("utf-8"))
    if parser.defaults() or parser.sections() != ["egg_info"]:
        raise RuntimeError("sdist setup.cfg has unexpected generated sections or defaults")
    options = dict(parser.items("egg_info"))
    if options != {"tag_build": "", "tag_date": "0"}:
        raise RuntimeError(f"sdist setup.cfg has unexpected generated egg_info values: {options}")


def _verify_sdist(archive: Path) -> dict[str, str]:
    root, members = _relative_sdist_files(archive)
    root_layout = set(SOURCE_NAMES)
    package_layout = {f"certlib/{name}" for name in SOURCE_NAMES}
    present_sources = {name for name in members if name.endswith(".py")}
    if present_sources not in (root_layout, package_layout):
        raise RuntimeError(f"sdist source file set mismatch: {sorted(present_sources)}")

    with tarfile.open(archive, "r:gz") as tar:
        def read_member(name: str) -> bytes:
            extracted = tar.extractfile(f"{root}/{name}")
            if extracted is None:
                raise RuntimeError(f"sdist is missing expected generated member: {name}")
            return extracted.read()

        pyproject = read_member("pyproject.toml")
        if _sha256(pyproject) != SOURCE_SHA256["pyproject.toml"].lower():
            raise RuntimeError("sdist pyproject.toml hash does not match the pinned source")
        _verify_generated_sdist_setup_cfg(read_member("setup.cfg"))
        _verify_distribution_metadata(read_member("PKG-INFO"), artifact="sdist")

        expected_egg_info_root = "bt_api_binance_certlib.egg-info"
        egg_info_roots = {
            PurePosixPath(member).parts[0]
            for member in members
            if PurePosixPath(member).parts[0].endswith(".egg-info")
        }
        if egg_info_roots != {expected_egg_info_root}:
            raise RuntimeError(f"sdist has unexpected egg-info directories: {sorted(egg_info_roots)}")
        egg_info_members = {
            PurePosixPath(member).relative_to(expected_egg_info_root).as_posix()
            for member in members
            if PurePosixPath(member).parts[0] == expected_egg_info_root
        }
        required_egg_info_members = {"PKG-INFO", "SOURCES.txt", "requires.txt", "top_level.txt"}
        allowed_egg_info_members = required_egg_info_members | {
            "dependency_links.txt",
            "eager_resources.txt",
            "entry_points.txt",
            "namespace_packages.txt",
        }
        if not required_egg_info_members <= egg_info_members:
            raise RuntimeError(
                f"sdist is missing standard egg-info members: "
                f"{sorted(required_egg_info_members - egg_info_members)}"
            )
        unexpected_egg_info_members = egg_info_members - allowed_egg_info_members
        if unexpected_egg_info_members:
            raise RuntimeError(
                f"sdist egg-info contains nonstandard members: {sorted(unexpected_egg_info_members)}"
            )
        if read_member(f"{expected_egg_info_root}/PKG-INFO") != read_member("PKG-INFO"):
            raise RuntimeError("sdist root and egg-info PKG-INFO files differ")
        if read_member(f"{expected_egg_info_root}/top_level.txt") != b"certlib\n":
            raise RuntimeError("sdist egg-info top_level.txt is not the expected package name")
        for empty_member in (
            "dependency_links.txt",
            "eager_resources.txt",
            "entry_points.txt",
            "namespace_packages.txt",
        ):
            relative = f"{expected_egg_info_root}/{empty_member}"
            if empty_member in egg_info_members and read_member(relative).strip():
                raise RuntimeError(f"sdist egg-info {empty_member} unexpectedly contains data")

    allowed = {
        "pyproject.toml",
        "setup.cfg",
        "PKG-INFO",
        *present_sources,
        *(f"bt_api_binance_certlib.egg-info/{name}" for name in egg_info_members),
    }
    unexpected = members - allowed
    if unexpected:
        raise RuntimeError(f"sdist contains files outside the package allowlist: {sorted(unexpected)}")

    hashes: dict[str, str] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for name in SOURCE_NAMES:
            relative = name if name in present_sources else f"certlib/{name}"
            extracted = tar.extractfile(f"{root}/{relative}")
            if extracted is None:
                raise RuntimeError(f"sdist is missing source: {relative}")
            actual = _sha256(extracted.read())
            if actual != SOURCE_SHA256[name].lower():
                raise RuntimeError(f"sdist source hash mismatch: {relative}")
            hashes[name] = actual
    return hashes


def _verify_wheel(archive: Path) -> dict[str, str]:
    with zipfile.ZipFile(archive) as wheel:
        files = {name for name in wheel.namelist() if not name.endswith("/")}
        expected_sources = {f"certlib/{name}" for name in SOURCE_NAMES}
        dist_info_dirs = {
            name.split("/", 1)[0]
            for name in files
            if name.split("/", 1)[0].endswith(".dist-info")
        }
        if files & expected_sources != expected_sources:
            raise RuntimeError("wheel does not contain the complete certlib source set")
        if len(dist_info_dirs) != 1:
            raise RuntimeError(f"wheel has unexpected metadata directories: {sorted(dist_info_dirs)}")
        expected_dist_info = f"bt_api_binance_certlib-{DIST_VERSION}.dist-info"
        if dist_info_dirs != {expected_dist_info}:
            raise RuntimeError(f"wheel has an unexpected dist-info directory: {sorted(dist_info_dirs)}")
        dist_info_members = {
            name.split("/", 1)[1]
            for name in files
            if name.startswith(expected_dist_info + "/")
        }
        required_dist_info_members = {"METADATA", "WHEEL", "RECORD", "top_level.txt"}
        allowed_dist_info_members = required_dist_info_members | {"entry_points.txt"}
        if not required_dist_info_members <= dist_info_members:
            raise RuntimeError(
                f"wheel is missing standard dist-info members: "
                f"{sorted(required_dist_info_members - dist_info_members)}"
            )
        unexpected_dist_info_members = dist_info_members - allowed_dist_info_members
        if unexpected_dist_info_members:
            raise RuntimeError(
                f"wheel dist-info contains nonstandard members: {sorted(unexpected_dist_info_members)}"
            )
        unexpected = files - expected_sources - {
            f"{expected_dist_info}/{name}" for name in dist_info_members
        }
        if unexpected:
            raise RuntimeError(f"wheel contains files outside certlib/ and dist-info/: {sorted(unexpected)}")

        hashes: dict[str, str] = {}
        for name in SOURCE_NAMES:
            raw = wheel.read(f"certlib/{name}")
            actual = _sha256(raw)
            if actual != SOURCE_SHA256[name].lower():
                raise RuntimeError(f"wheel source hash mismatch: certlib/{name}")
            hashes[name] = actual

        _verify_distribution_metadata(
            wheel.read(f"{expected_dist_info}/METADATA"),
            artifact="wheel",
        )
        if wheel.read(f"{expected_dist_info}/top_level.txt") != b"certlib\n":
            raise RuntimeError("wheel dist-info top_level.txt is not the expected package name")
        if "entry_points.txt" in dist_info_members and wheel.read(
            f"{expected_dist_info}/entry_points.txt"
        ).strip():
            raise RuntimeError("wheel dist-info entry_points.txt unexpectedly contains data")
    return hashes


def _venv_python(path: Path) -> Path:
    candidate = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not candidate.is_file():
        raise RuntimeError(f"virtual environment interpreter missing: {candidate}")
    return candidate


def _install_artifact(
    *,
    env_dir: Path,
    artifact: Path,
    wheelhouse: Path,
    records: list[dict],
    cwd: Path,
) -> Path:
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(env_dir)
    python = _venv_python(env_dir)
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            str(artifact),
        ],
        cwd=cwd,
        env=_safe_env(wheelhouse),
        records=records,
    )
    return python


def _consumer_script(
    *,
    run_path: Path,
    config_path: Path,
    expected_env_path: Path,
) -> str:
    expected_source_hashes = {
        name: SOURCE_SHA256[name].lower() for name in SOURCE_NAMES
    }
    return f'''from __future__ import annotations
import importlib.metadata as metadata
import hashlib
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
import socket
import sys

blocked = []
def deny_network(*args, **kwargs):
    blocked.append("socket_operation")
    raise RuntimeError("network access is disabled in this acceptance check")
socket.create_connection = deny_network
socket.getaddrinfo = deny_network
def audit(event, args):
    if event.startswith("socket."):
        blocked.append(event)
        deny_network(event, args)
sys.addaudithook(audit)

import certlib
from certlib import envfile, runner
from certlib import binance_client, context, monitor, validation
import yaml

dist_name = {DIST_NAME!r}
expected_sources = {expected_source_hashes!r}
dist = metadata.distribution(dist_name)
normalize = lambda value: "-".join(re.sub(r"[-_.]+", "-", value).lower().split("-"))
assert normalize(dist.metadata["Name"]) == normalize(dist_name)
assert dist.version == {DIST_VERSION!r}
site_roots = [Path(value).resolve() for value in __import__("site").getsitepackages()]
module_origins = {{
    name: Path(module.__file__).resolve()
    for name, module in {{
        "certlib": certlib,
        "certlib.runner": runner,
        "certlib.binance_client": binance_client,
        "certlib.context": context,
        "certlib.envfile": envfile,
        "certlib.monitor": monitor,
        "certlib.validation": validation,
    }}.items()
}}
for name, origin in module_origins.items():
    assert any(origin.is_relative_to(root) for root in site_roots), (name, str(origin))
    assert origin.name != ""
    source_name = name.split(".", 1)[-1] + ".py" if name != "certlib" else "__init__.py"
    assert hashlib.sha256(origin.read_bytes()).hexdigest() == expected_sources[source_name], name
for source_name, digest in expected_sources.items():
    installed_path = Path(dist.locate_file("certlib/" + source_name)).resolve()
    assert installed_path.is_file(), str(installed_path)
    assert hashlib.sha256(installed_path.read_bytes()).hexdigest() == digest, source_name
packages = metadata.packages_distributions().get("certlib", [])
assert [normalize(value) for value in packages] == [normalize(dist_name)], packages
assert metadata.version("PyYAML") == "6.0.1"
assert any(Path(yaml.__file__).resolve().is_relative_to(root) for root in site_roots)

# Keep the installed envfile.default_env_path implementation unchanged.  The
# venv is deliberately nested so parents[5] is this new, checked-empty root.
expected_env_path = Path({str(expected_env_path)!r}).resolve()
assert envfile.default_env_path().resolve() == expected_env_path
assert module_origins["certlib.envfile"].parents[5] == expected_env_path.parent
assert not expected_env_path.exists()
provider_calls = []
def deny_provider(*args, **kwargs):
    provider_calls.append("make_client")
    raise RuntimeError("provider/client startup is disabled in this acceptance check")
runner.make_client = deny_provider
sys.argv[0] = {str(run_path)!r}
runner_output = StringIO()
with redirect_stdout(runner_output):
    result = runner.main_case(["--config", {str(config_path)!r}])
plan = json.loads(runner_output.getvalue())
assert result == 0, result
assert plan["status"] == "DRY", plan
assert plan["case"] == "MIG04" and plan["venue"] == "binance", plan
assert plan["env_keys_missing"] == [], plan
assert plan["env_keys_optional_missing"] == ["MIG04_DRY_OPTIONAL_KEY"], plan
assert envfile.default_env_path().resolve() == expected_env_path
assert not expected_env_path.exists()
assert not blocked, blocked
assert not provider_calls, provider_calls
print("CERTLIB_INSTALLED_DRY_ENTRY_SUBCHECK_OK")
print(json.dumps({{
    "distribution": dist.metadata["Name"],
    "version": dist.version,
    "module_origins": {{key: str(value) for key, value in module_origins.items()}},
    "site_roots": [str(value) for value in site_roots],
    "package_owners": packages,
    "network_attempts": blocked,
    "provider_startups": provider_calls,
    "env_path": str(expected_env_path),
    "env_path_exists": expected_env_path.exists(),
    "ordinary_entry_exit": result,
}}, sort_keys=True))
'''


def _check_install(
    *,
    kind: str,
    artifact: Path,
    containers_root: Path,
    wheelhouse: Path,
    consumer_dir: Path,
    records: list[dict],
) -> dict:
    env_dir = containers_root / kind / "env"
    env_dir.parent.mkdir(parents=True, exist_ok=False)
    python = _install_artifact(
        env_dir=env_dir,
        artifact=artifact,
        wheelhouse=wheelhouse,
        records=records,
        cwd=consumer_dir,
    )
    case_dir = consumer_dir / "binance_penetration" / "cases" / "MIG04"
    case_dir.mkdir(parents=True, exist_ok=True)
    run_path = case_dir / "run.py"
    run_path.write_text(
        "from certlib.runner import main_case\n"
        "raise SystemExit(main_case())\n",
        encoding="utf-8",
    )
    (case_dir / "MIG04_strategy.py").write_text(
        "from certlib.context import CaseResult\n"
        "class MIG04Strategy:\n"
        "    RISK = 'READ'\n",
        encoding="utf-8",
    )
    config_path = case_dir / "config.yaml"
    config_path.write_text(
        "case_id: MIG04\n"
        "credentials:\n"
        "  restricted_api_key_env: MIG04_DRY_OPTIONAL_KEY\n",
        encoding="utf-8",
    )
    if (case_dir / "MIG04_strategy.py").read_bytes().find(b"CaseResult") < 0:
        raise RuntimeError("synthetic strategy fixture is not import-bound to certlib")

    # Use the temporary run.py as sys.argv[0]. Its runner imports from the
    # installed wheel/sdist because the subprocess uses isolated mode and has
    # no source-tree path or PYTHONPATH.
    expected_env_path = containers_root / ".env"
    if expected_env_path.exists():
        raise RuntimeError(f"controlled installed env path unexpectedly exists: {expected_env_path}")
    entry = _consumer_script(
        run_path=run_path,
        config_path=config_path,
        expected_env_path=expected_env_path,
    )
    entry_result = _run(
        [str(python), "-I", "-c", entry],
        cwd=consumer_dir,
        env=_safe_env(wheelhouse),
        records=records,
    )
    if "CERTLIB_INSTALLED_DRY_ENTRY_SUBCHECK_OK" not in entry_result.stdout:
        raise RuntimeError(f"installed dry-entry subcheck marker missing for {kind}")
    return {
        "artifact": str(artifact),
        "venv": str(env_dir),
        "expected_env_path": str(expected_env_path),
        "entry_stdout": entry_result.stdout,
        "entry_stderr": entry_result.stderr,
        "entry_exit_code": entry_result.returncode,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", required=True, type=Path)
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("this distribution round requires Python 3.11 or newer")
    wheelhouse = args.wheelhouse.resolve(strict=True)
    if not wheelhouse.is_dir():
        raise SystemExit(f"wheelhouse is not a directory: {wheelhouse}")
    wheelhouse_pins = _assert_wheelhouse_pins(wheelhouse)
    source_pins = _assert_source_pins()

    session = Path(tempfile.mkdtemp(prefix="it10-mig04-certlib-r1-20261016-"))
    records: list[dict] = []
    result = {
        "status": "RUNNING",
        "scope": "certlib sdist/wheel install and installed dry entry only; not full MIG04 adjudication or live credential discovery",
        "formal_mig04_acceptance": False,
        "live_env_discovery": "not_assessed; installed default path remains checkout-layout-derived",
        "distribution": DIST_NAME,
        "version": DIST_VERSION,
        "source_pins": source_pins,
        "build_wheelhouse_pins": wheelhouse_pins,
        "source_revision": None,
        "source_branch": None,
        "package_source_status": None,
        "build_interpreter": {"executable": sys.executable, "version": sys.version},
        "check_script": {
            "path": str(Path(__file__).resolve()),
            "sha256": _sha256(Path(__file__).read_bytes()),
        },
        "wheelhouse": str(wheelhouse),
        "session": str(session),
        "commands": records,
    }
    record_path = session / "result.json"
    try:
        git_result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if git_result.returncode != 0:
            raise RuntimeError("could not capture the Backtrader source revision")
        branch_result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "branch", "--show-current"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if branch_result.returncode != 0:
            raise RuntimeError("could not capture the Backtrader source branch")
        result["source_revision"] = git_result.stdout.strip()
        result["source_branch"] = branch_result.stdout.strip()
        package_status_result = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "status",
                "--short",
                "--untracked-files=all",
                "--",
                PACKAGE_ROOT.relative_to(REPO_ROOT).as_posix(),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if package_status_result.returncode != 0:
            raise RuntimeError("could not capture the certlib package source status")
        result["package_source_status"] = package_status_result.stdout.rstrip()

        stage = session / "project"
        _stage_allowlisted_project(stage)

        build_env_dir = session / "venv-build"
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(build_env_dir)
        build_python = _venv_python(build_env_dir)
        _run(
            [
                str(build_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                *BUILD_REQUIREMENTS,
            ],
            cwd=session,
            env=_safe_env(wheelhouse),
            records=records,
        )
        _run(
            [
                str(build_python),
                "-m",
                "build",
                "--no-isolation",
                "--sdist",
                "--wheel",
                "--outdir",
                str(session / "dist"),
                str(stage),
            ],
            cwd=session,
            env=_safe_env(wheelhouse),
            records=records,
        )

        normalized = _canonical_name(DIST_NAME).replace("-", "_")
        wheels = list((session / "dist").glob(f"{normalized}-{DIST_VERSION}-*.whl"))
        sdists = list((session / "dist").glob(f"{normalized}-{DIST_VERSION}.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise RuntimeError(
                f"expected one wheel and one sdist, got wheels={wheels}, sdists={sdists}"
            )
        result["wheel_source_pins"] = _verify_wheel(wheels[0])
        result["sdist_source_pins"] = _verify_sdist(sdists[0])

        containers_root = session / "containers"
        containers_root.mkdir()
        controlled_env_path = containers_root / ".env"
        if controlled_env_path.exists():
            raise RuntimeError(f"new controlled container root unexpectedly contains .env: {controlled_env_path}")

        consumer_dir = session / "consumer"
        consumer_dir.mkdir()
        result["wheel_install"] = _check_install(
            kind="wheel",
            artifact=wheels[0],
            containers_root=containers_root,
            wheelhouse=wheelhouse,
            consumer_dir=consumer_dir,
            records=records,
        )
        # Use a separate clean consumer directory so it cannot pick up the
        # wheel installation or any generated import state.
        sdist_consumer_dir = session / "consumer-sdist"
        sdist_consumer_dir.mkdir()
        result["sdist_install"] = _check_install(
            kind="sdist",
            artifact=sdists[0],
            containers_root=containers_root,
            wheelhouse=wheelhouse,
            consumer_dir=sdist_consumer_dir,
            records=records,
        )
        result["artifacts"] = {
            "wheel": {"path": str(wheels[0]), "sha256": _sha256(wheels[0].read_bytes())},
            "sdist": {"path": str(sdists[0]), "sha256": _sha256(sdists[0].read_bytes())},
        }
        if controlled_env_path.exists():
            raise RuntimeError(f"controlled container root unexpectedly contains .env: {controlled_env_path}")
        result["status"] = "PASS_CERTLIB_ARTIFACT_INSTALL_AND_OFFLINE_DRY_ENTRY_SUBCHECK"
    except Exception as exc:
        result["status"] = "HOLD"
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
        raise
    finally:
        record_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"MIG04_CERTLIB_RESULT={record_path}")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
