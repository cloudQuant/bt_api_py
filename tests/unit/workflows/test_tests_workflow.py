"""Structural contracts for the baseline test workflow."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "tests.yml"
FULL_SUITE_MARKERS = "not network and not integration and not performance and not e2e and not ctp"
BASELINE_STEP_NAME = "Run baseline suite with coverage gate"
WORKFLOW_STEPS_PATH = "workflow.jobs.full-suite.steps"


def load_workflow() -> object:
    """Load Actions YAML while keeping ``on`` as a string key."""
    with WORKFLOW_PATH.open(encoding="utf-8") as workflow_file:
        return yaml.load(workflow_file, Loader=yaml.BaseLoader)


def _mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be a mapping, got {type(value).__name__}")

    mapping: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{path} has a non-string key: {key!r}")
        mapping[key] = item
    return mapping


def _required_field(mapping: dict[str, object], field: str, path: str) -> object:
    if field not in mapping:
        raise KeyError(f"{path} is missing required field {field!r}")
    return mapping[field]


def _string_field(
    mapping: dict[str, object],
    field: str,
    path: str,
    *,
    default: str | None = None,
) -> str:
    if field not in mapping:
        if default is not None:
            return default
        raise KeyError(f"{path} is missing required string field {field!r}")

    value = mapping[field]
    if not isinstance(value, str):
        raise TypeError(f"{path}.{field} must be a string, got {type(value).__name__}")
    return value


def _workflow_steps() -> list[dict[str, object]]:
    workflow = _mapping(load_workflow(), "workflow")
    jobs = _mapping(_required_field(workflow, "jobs", "workflow"), "workflow.jobs")
    full_suite = _mapping(
        _required_field(jobs, "full-suite", "workflow.jobs"), "workflow.jobs.full-suite"
    )
    raw_steps = _required_field(full_suite, "steps", "workflow.jobs.full-suite")
    if not isinstance(raw_steps, list):
        raise TypeError(f"{WORKFLOW_STEPS_PATH} must be a sequence, got {type(raw_steps).__name__}")

    steps: list[dict[str, object]] = []
    for index, raw_step in enumerate(raw_steps):
        steps.append(_mapping(raw_step, f"{WORKFLOW_STEPS_PATH}[{index}]"))
    return steps


def _find_step(steps: list[dict[str, object]], name: str) -> dict[str, object]:
    for index, step in enumerate(steps):
        path = f"{WORKFLOW_STEPS_PATH}[{index}]"
        if "name" in step and _string_field(step, "name", path) == name:
            return step
    raise ValueError(f"{WORKFLOW_STEPS_PATH} is missing step named {name!r}")


def _baseline_step() -> dict[str, object]:
    return _find_step(_workflow_steps(), BASELINE_STEP_NAME)


def _step_path(name: str) -> str:
    return f"{WORKFLOW_STEPS_PATH}[{name!r}]"


def test_workflow_shape_guards_report_invalid_nodes_clearly() -> None:
    with pytest.raises(TypeError, match="workflow must be a mapping, got list"):
        _mapping([], "workflow")

    with pytest.raises(KeyError, match="workflow.jobs is missing required field 'steps'"):
        _required_field({}, "steps", "workflow.jobs")

    field_path = f"{WORKFLOW_STEPS_PATH}[4]"
    with pytest.raises(TypeError) as error:
        _string_field({"run": 5}, "run", field_path)
    assert str(error.value) == f"{field_path}.run must be a string, got int"


def _run_coverage_script(script: str, threshold: str) -> subprocess.CompletedProcess[bytes]:
    """Run the workflow shell with a harmless python stub that captures argv."""
    environment = os.environ.copy()
    environment["COVERAGE_THRESHOLD"] = threshold
    environment["FULL_SUITE_MARKERS"] = FULL_SUITE_MARKERS
    capture_python_argv = "python() { printf '%s\\0' \"$@\"; }\n"
    return subprocess.run(
        ["/bin/bash", "-c", capture_python_argv + script],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        timeout=5,
    )


def test_baseline_uses_parallel_module_pytest_with_branch_coverage() -> None:
    script = _string_field(_baseline_step(), "run", _step_path(BASELINE_STEP_NAME))

    assert "pytest_args=(\n  tests -v -n 8" in script
    assert 'python -m pytest "${pytest_args[@]}"' in script
    assert "--cov-branch" in script
    assert '-m "$FULL_SUITE_MARKERS"' in script
    assert "--cov=bt_api_py" in script
    assert "--cov-report=xml:coverage-full.xml" in script
    assert "--cov-report=html" in script
    assert "--cov-report=term-missing" in script


def test_dispatch_threshold_is_passed_only_through_step_environment() -> None:
    step = _baseline_step()
    step_path = _step_path(BASELINE_STEP_NAME)
    environment = _mapping(_required_field(step, "env", step_path), f"{step_path}.env")

    assert (
        _string_field(environment, "COVERAGE_THRESHOLD", f"{step_path}.env")
        == "${{ github.event.inputs['coverage-threshold'] }}"
    )
    script = _string_field(step, "run", step_path)
    assert "${{ github.event.inputs['coverage-threshold'] }}" not in script


@pytest.mark.parametrize("threshold", ["", "0", "0.5", "99.99", "100", "100.0"])
def test_threshold_accepts_only_supported_decimals_and_passes_one_argument(
    threshold: str,
) -> None:
    step = _baseline_step()
    script = _string_field(step, "run", _step_path(BASELINE_STEP_NAME))
    result = _run_coverage_script(script, threshold)

    assert result.returncode == 0, result.stderr.decode()
    arguments = result.stdout.split(b"\0")[:-1]
    expected = [
        b"-m",
        b"pytest",
        b"tests",
        b"-v",
        b"-n",
        b"8",
        b"-m",
        FULL_SUITE_MARKERS.encode(),
        b"--cov=bt_api_py",
        b"--cov-branch",
        b"--cov-report=xml:coverage-full.xml",
        b"--cov-report=html",
        b"--cov-report=term-missing",
    ]
    if threshold:
        expected.append(f"--cov-fail-under={threshold}".encode())
    assert arguments == expected


@pytest.mark.parametrize(
    "threshold",
    [
        "-0.1",
        "100.01",
        "101",
        " ",
        " 50",
        "50 ",
        "1e2",
        "50 --maxfail=1",
    ],
)
def test_threshold_rejects_out_of_range_or_extra_arguments(threshold: str) -> None:
    step = _baseline_step()
    script = _string_field(step, "run", _step_path(BASELINE_STEP_NAME))
    result = _run_coverage_script(script, threshold)

    assert result.returncode == 2
    assert b"coverage-threshold" in result.stdout
    assert b"\0" not in result.stdout


def test_threshold_rejects_command_substitution_without_executing_it(tmp_path: Path) -> None:
    step = _baseline_step()
    marker = tmp_path / "command-substitution-ran"
    script = _string_field(step, "run", _step_path(BASELINE_STEP_NAME))
    result = _run_coverage_script(script, f"$(touch {marker})")

    assert result.returncode == 2
    assert b"coverage-threshold" in result.stdout
    assert b"\0" not in result.stdout
    assert not marker.exists()


def test_threshold_shell_is_strict_and_does_not_evaluate_extra_arguments() -> None:
    step = _baseline_step()
    step_path = _step_path(BASELINE_STEP_NAME)
    script = _string_field(step, "run", step_path)

    assert _string_field(step, "shell", step_path) == "bash"
    assert script.splitlines()[0] == "set -euo pipefail"
    assert "extra_args=()" in script
    assert "pytest_args=(" in script
    assert 'pytest_args+=("${extra_args[0]}")' in script
    assert '"${pytest_args[@]}"' in script
    assert "eval" not in script
    assert "EXTRA_ARGS" not in script
    assert "|| true" not in script
    assert _string_field(step, "continue-on-error", step_path, default="false").lower() != "true"


def test_coverage_reports_are_validated_before_optional_uploads() -> None:
    steps = _workflow_steps()
    pytest_step = _find_step(steps, BASELINE_STEP_NAME)
    validation_step = _find_step(steps, "Validate coverage reports")
    codecov_step = _find_step(steps, "Upload coverage to Codecov")
    artifact_step = _find_step(steps, "Archive coverage report")
    validation_path = _step_path("Validate coverage reports")
    validation_script = _string_field(validation_step, "run", validation_path)

    assert _string_field(validation_step, "shell", validation_path) == "bash"
    assert _string_field(validation_step, "if", validation_path) == "always()"
    assert validation_script.splitlines()[0] == "set -euo pipefail"
    assert "test -s coverage-full.xml" in validation_script
    assert "test -s htmlcov/index.html" in validation_script
    assert "|| true" not in validation_script
    assert (
        _string_field(
            validation_step, "continue-on-error", validation_path, default="false"
        ).lower()
        != "true"
    )
    assert steps.index(pytest_step) < steps.index(validation_step)
    assert steps.index(validation_step) < steps.index(codecov_step)
    assert steps.index(validation_step) < steps.index(artifact_step)

    codecov_path = _step_path("Upload coverage to Codecov")
    codecov_options = _mapping(
        _required_field(codecov_step, "with", codecov_path), f"{codecov_path}.with"
    )
    assert _string_field(codecov_step, "if", codecov_path) == "always() && env.CODECOV_TOKEN != ''"
    assert _string_field(codecov_options, "fail_ci_if_error", f"{codecov_path}.with") == "false"


def test_coverage_artifact_always_requires_xml_and_html_reports() -> None:
    artifact_step_name = "Archive coverage report"
    artifact_step = _find_step(_workflow_steps(), artifact_step_name)
    artifact_path = _step_path(artifact_step_name)
    artifact_options = _mapping(
        _required_field(artifact_step, "with", artifact_path), f"{artifact_path}.with"
    )

    assert _string_field(artifact_step, "if", artifact_path) == "always()"
    assert _string_field(artifact_step, "uses", artifact_path).startswith(
        "actions/upload-artifact@"
    )
    assert set(_string_field(artifact_options, "path", f"{artifact_path}.with").splitlines()) == {
        "coverage-full.xml",
        "htmlcov/",
    }
    assert _string_field(artifact_options, "if-no-files-found", f"{artifact_path}.with") == "error"
    assert (
        _string_field(artifact_step, "continue-on-error", artifact_path, default="false").lower()
        != "true"
    )
