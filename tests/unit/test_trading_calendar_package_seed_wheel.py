"""Build the SDK wheel offline and load the packaged seed from the installed copy.

The build reuses the current interpreter's setuptools/wheel (``--no-build-isolation``)
and never resolves dependencies or indexes (``--no-index --no-deps``). It is
slow and marked accordingly. It proves packaging only on this machine; Windows,
a clean environment and a published release remain NOT_RUN.
"""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[2]
SEED_PREFIX = "bt_api_py/trading_calendar_seed/"


def _env(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"}
    }
    env.update(
        PIP_NO_INDEX="1",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        PIP_CONFIG_FILE=os.devnull,
        PIP_NO_INPUT="1",
        TMPDIR=str(tmp_path),
    )
    return env


@pytest.mark.timeout(900)
def test_wheel_contains_and_loads_the_synthetic_seed(tmp_path):
    wheel_dir, target = tmp_path / "wheels", tmp_path / "installed"
    env = _env(tmp_path)

    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(REPO),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert build.returncode == 0, build.stderr[-4000:]

    wheels = sorted(wheel_dir.glob("bt_api_py-*.whl"))
    assert len(wheels) == 1, [path.name for path in wheel_dir.iterdir()]
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    packaged = {name for name in names if name.startswith(SEED_PREFIX)}
    assert f"{SEED_PREFIX}index.json" in packaged
    assert f"{SEED_PREFIX}seed-2026-2027-v1/manifest.json" in packaged
    assert len([name for name in packaged if name.endswith(".json")]) >= 6

    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-input",
            "--target",
            str(target),
            str(wheel),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert install.returncode == 0, install.stdout[-2000:] + install.stderr[-4000:]

    installed_seed = target / SEED_PREFIX
    assert (installed_seed / "index.json").is_file()
    assert (installed_seed / "seed-2026-2027-v1" / "manifest.json").is_file()

    code = (
        "import json, pathlib, bt_api_py\n"
        "from bt_api_py.trading_calendar_package_seed import (\n"
        "  load_package_seed, prepare_package_seed_publication,\n"
        ")\n"
        "seed = load_package_seed()\n"
        "publication = prepare_package_seed_publication(seed)\n"
        "print(json.dumps({\n"
        "  'module': bt_api_py.__file__,\n"
        "  'seed_version': seed.seed_version,\n"
        "  'synthetic': seed.synthetic,\n"
        "  'data_origin': seed.data_origin,\n"
        "  'venues': list(seed.coverage['venues']),\n"
        "  'rows': len(seed.materialize_content()['trading_days']),\n"
        "  'manifest_sha256': publication.coverage_manifest_sha256,\n"
        "  'binding_sha256': publication.binding_sha256,\n"
        "  'manifest': json.loads(publication.coverage_manifest_bytes),\n"
        "}))\n"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**env, "PYTHONPATH": str(target)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert loaded.returncode == 0, loaded.stderr[-4000:]
    payload = json.loads(loaded.stdout.strip().splitlines()[-1])
    assert Path(payload["module"]).resolve().is_relative_to(target.resolve())
    assert payload["seed_version"] == "seed-2026-2027-v1"
    assert payload["synthetic"] is True
    assert payload["data_origin"] == "SYNTHETIC_MOCK"
    assert payload["venues"] == ["CFFEX", "CZCE", "DCE", "GFEX", "INE", "SHFE"]
    assert payload["rows"] == 6 * 2 * 365
    # The installed copy also prepares the declared, queryable coverage bundle.
    assert payload["manifest"]["schema"] == "bt_api.calendar-simulation-coverage/1"
    assert [row["venue"] for row in payload["manifest"]["coverage"]] == payload["venues"]
    assert all(
        (row["coverage_start"], row["coverage_end"]) == ("2026-01-01", "2027-12-31")
        for row in payload["manifest"]["coverage"]
    )
    assert len(payload["manifest_sha256"]) == 64
    assert len(payload["binding_sha256"]) == 64
    assert payload["manifest_sha256"] != payload["binding_sha256"]
