"""Structural checks keep docs and test jobs on the exact parent source pin."""

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_ROOT = REPOSITORY_ROOT / ".github" / "workflows"
BOOTSTRAP_COMMAND = "python scripts/ci/base_source_pin.py"


def _workflow_steps(filename: str, job: str) -> list[dict[str, object]]:
    with (WORKFLOW_ROOT / filename).open(encoding="utf-8") as workflow_file:
        workflow = yaml.load(workflow_file, Loader=yaml.BaseLoader)
    return workflow["jobs"][job]["steps"]


def _step_index(steps: list[dict[str, object]], name: str) -> int:
    return next(index for index, step in enumerate(steps) if step.get("name") == name)


def _assert_source_bootstrap_precedes_package_install(
    steps: list[dict[str, object]], install_step: str
) -> None:
    bootstrap_index = _step_index(steps, "Build and install parent-pinned base wheel")
    install_index = _step_index(steps, install_step)
    bootstrap = steps[bootstrap_index]

    assert bootstrap_index < install_index
    assert BOOTSTRAP_COMMAND in bootstrap["run"]
    assert '--wheel-dir "${{ runner.temp }}/bt_api_base_wheelhouse"' in bootstrap["run"]
    assert "$RUNNER_TEMP" not in bootstrap["run"]
    assert "$GITHUB_ENV" not in bootstrap["run"]
    assert (
        "continue-on-error" not in bootstrap
        or str(bootstrap["continue-on-error"]).lower() != "true"
    )
    assert "python -m pip check" in steps[_step_index(steps, "Check installed dependencies")]["run"]


def test_docs_job_checks_out_and_installs_parent_pinned_base_before_root_package() -> None:
    steps = _workflow_steps("docs.yml", "build")
    checkout_index = _step_index(steps, "Checkout pinned base source")
    bootstrap_index = _step_index(steps, "Build and install parent-pinned base wheel")
    install_index = _step_index(steps, "Install package (needed by mkdocstrings)")

    assert checkout_index < bootstrap_index < install_index
    _assert_source_bootstrap_precedes_package_install(
        steps, "Install package (needed by mkdocstrings)"
    )


def test_tests_workflow_bootstraps_every_root_dependency_install_job() -> None:
    quality_steps = _workflow_steps("tests.yml", "quality")
    full_suite_steps = _workflow_steps("tests.yml", "full-suite")
    _assert_source_bootstrap_precedes_package_install(
        quality_steps, "Install package + quality tools"
    )
    _assert_source_bootstrap_precedes_package_install(
        full_suite_steps, "Install package + dev deps"
    )


def test_windows_compatibility_job_uses_cross_platform_temp_and_only_checks_out_base() -> None:
    steps = _workflow_steps("reusable-compat-matrix.yml", "matrix")
    checkout_index = next(
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    source_index = _step_index(steps, "Checkout pinned base source")
    bootstrap_index = _step_index(steps, "Build and install parent-pinned base wheel")
    checkout = steps[checkout_index]
    source_checkout = steps[source_index]

    assert checkout_index < source_index < bootstrap_index
    assert "submodules" not in checkout.get("with", {})
    assert source_checkout["run"] == "git submodule update --init --depth 1 -- bt_api/bt_api_base"
    _assert_source_bootstrap_precedes_package_install(steps, "Install package + dev deps")
    bootstrap_command = steps[bootstrap_index]["run"]
    assert '"${{ runner.temp }}/bt_api_base_wheelhouse"' in bootstrap_command
    assert "$RUNNER_TEMP" not in bootstrap_command
    assert "$GITHUB_ENV" not in bootstrap_command


def test_codecov_secret_is_scoped_to_upload_and_fork_prs_skip_it() -> None:
    with (WORKFLOW_ROOT / "tests.yml").open(encoding="utf-8") as workflow_file:
        workflow = yaml.load(workflow_file, Loader=yaml.BaseLoader)
    full_suite = workflow["jobs"]["full-suite"]
    upload = next(
        step for step in full_suite["steps"] if step.get("name") == "Upload coverage to Codecov"
    )

    assert "CODECOV_TOKEN" not in full_suite.get("env", {})
    assert upload["env"]["CODECOV_TOKEN"] == "${{ secrets.CODECOV_TOKEN }}"
    assert upload["with"]["token"] == "${{ secrets.CODECOV_TOKEN }}"
    assert "github.event_name != 'pull_request'" in upload["if"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in upload["if"]
    assert upload["with"]["fail_ci_if_error"] == "false"
