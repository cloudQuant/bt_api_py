"""Structural CI checks for the parent-pinned CTP native source wheel."""

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "tests.yml"


def _steps() -> list[dict[str, object]]:
    with WORKFLOW_PATH.open(encoding="utf-8") as workflow_file:
        workflow = yaml.load(workflow_file, Loader=yaml.BaseLoader)
    return workflow["jobs"]["full-suite"]["steps"]


def _index(steps: list[dict[str, object]], name: str) -> int:
    return next(index for index, step in enumerate(steps) if step.get("name") == name)


def test_full_suite_installs_and_rechecks_exact_local_ctp_wheel() -> None:
    steps = _steps()
    base = _index(steps, "Build and install parent-pinned base wheel")
    toolchain = _index(steps, "Install build tools")
    ctp = _index(steps, "Build and install parent-pinned CTP source wheel")
    package = _index(steps, "Install package + dev deps")
    verify = _index(steps, "Verify parent-pinned CTP wheel receipt")
    dependency_check = _index(steps, "Check installed dependencies")
    native_check = _index(steps, "Check CTP native extension load")

    assert base < toolchain < ctp < package < verify < dependency_check < native_check
    assert '--github-env "$GITHUB_ENV"' in steps[base]["run"]
    assert "python scripts/ci/ctp_source_pin.py" in steps[ctp]["run"]
    assert '--wheel-dir "${{ runner.temp }}/bt_api_ctp_wheelhouse"' in steps[ctp]["run"]
    assert '--github-env "$GITHUB_ENV"' in steps[ctp]["run"]
    assert steps[verify]["run"] == "python scripts/ci/ctp_source_pin.py --verify-installed"
    assert "python -m pip check" in steps[dependency_check]["run"]
    assert "is_ctp_native_loaded()" in steps[native_check]["run"]
    assert "CODECOV_TOKEN" not in steps[ctp].get("env", {})
    assert "GITHUB_TOKEN" not in steps[ctp].get("env", {})
    assert "env" not in steps[ctp]
    with WORKFLOW_PATH.open(encoding="utf-8") as workflow_file:
        workflow = yaml.load(workflow_file, Loader=yaml.BaseLoader)
    full_suite_job = workflow["jobs"]["full-suite"]
    assert "env" not in full_suite_job
    assert "continue-on-error" not in steps[ctp]


def test_ctp_bootstrap_uses_only_parent_pin_and_shared_strict_environment() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "ci" / "ctp_source_pin.py").read_text(encoding="utf-8")
    assert '"archive"' in script
    assert '"--format=tar"' in script
    assert "pin.source_commit" in script
    assert '"--no-deps"' in script
    assert "_subprocess_environment(public_pip_index=True)" in script
    assert "_subprocess_environment()" in script
    assert "verify_ctp_source_pin(repository_root)" in script
    assert "source_commit" in script
