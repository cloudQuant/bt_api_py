"""Structural contracts for the optional extended test workflow."""

import json
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "optimized-tests.yml"
MAKEFILE_PATH = REPOSITORY_ROOT / "Makefile"
PERFORMANCE_BASELINE_PATH = (
    REPOSITORY_ROOT / "docs" / "acceptance" / "2026-09-21-performance-baseline.json"
)
PERFORMANCE_BENCHMARK_FULLNAME = (
    "tests/performance/test_event_normalization_performance.py::"
    "test_normalize_event_orderbook_dict_hot_path"
)


def load_workflow() -> dict[str, object]:
    """Load workflow YAML while preserving the string-valued ``on`` key."""
    with WORKFLOW_PATH.open(encoding="utf-8") as workflow_file:
        return yaml.load(workflow_file, Loader=yaml.BaseLoader)


def test_security_job_uses_only_read_only_repository_permissions() -> None:
    workflow = load_workflow()

    assert workflow["jobs"]["security"]["permissions"] == {"contents": "read"}


def test_security_scans_both_run_and_any_scan_failure_fails_the_job() -> None:
    workflow = load_workflow()
    security_job = workflow["jobs"]["security"]
    steps = security_job["steps"]
    scan_step = next(step for step in steps if step.get("name") == "Run security scan")
    scan_script = scan_step["run"]
    scan_lines = scan_script.splitlines()
    bandit_command = "bandit -r bt_api_py -c pyproject.toml -f json -o bandit-report.json"
    audit_command = "pip-audit -f json -o pip-audit-report.json"

    assert security_job.get("continue-on-error", "false").lower() != "true"
    assert scan_step.get("continue-on-error", "false").lower() != "true"
    assert "|| true" not in scan_script
    assert scan_script.index("set +e") < scan_script.index(bandit_command)
    assert scan_lines[scan_lines.index(bandit_command) + 1].strip() == "bandit_status=$?"
    assert scan_lines[scan_lines.index(audit_command) + 1].strip() == "pip_audit_status=$?"
    assert (
        scan_lines.index("pip_audit_status=$?")
        < scan_lines.index("set -e")
        < min(
            scan_lines.index("test -s bandit-report.json || bandit_report_status=1"),
            scan_lines.index("test -s pip-audit-report.json || pip_audit_report_status=1"),
        )
    )
    assert "set -e" in scan_script
    assert "test -s bandit-report.json || bandit_report_status=1" in scan_script
    assert "test -s pip-audit-report.json || pip_audit_report_status=1" in scan_script
    assert (
        'if [ "$bandit_status" -ne 0 ] || [ "$pip_audit_status" -ne 0 ] || '
        '[ "$bandit_report_status" -ne 0 ] || [ "$pip_audit_report_status" -ne 0 ]; then'
    ) in scan_script
    assert "exit 1" in scan_script


def test_security_reports_upload_even_on_failure_and_missing_reports_fail() -> None:
    workflow = load_workflow()
    steps = workflow["jobs"]["security"]["steps"]
    upload_step = next(step for step in steps if step.get("name") == "Upload security reports")

    assert upload_step["if"] == "always()"
    assert upload_step.get("continue-on-error", "false").lower() != "true"
    assert upload_step["uses"].startswith("actions/upload-artifact@")
    assert upload_step["with"]["if-no-files-found"] == "error"
    assert set(upload_step["with"]["path"].splitlines()) == {
        "bandit-report.json",
        "pip-audit-report.json",
    }


def test_build_test_job_uses_only_read_only_repository_permissions() -> None:
    workflow = load_workflow()

    assert workflow["jobs"]["build-test"]["permissions"] == {"contents": "read"}


def test_wheel_smoke_installs_and_imports_the_wheel_outside_the_checkout() -> None:
    workflow = load_workflow()
    build_job = workflow["jobs"]["build-test"]
    steps = build_job["steps"]
    smoke_step = next(step for step in steps if step.get("name") == "Test wheel installation")
    smoke_script = smoke_step["run"]
    import_script_start = smoke_script.index("\"$venv_python\" - <<'PY'")
    import_script_end = smoke_script.index("\nPY", import_script_start)
    import_script = smoke_script[import_script_start:import_script_end]

    assert smoke_step.get("shell") == "bash"
    assert build_job.get("continue-on-error", "false").lower() != "true"
    assert smoke_step.get("continue-on-error", "false").lower() != "true"
    assert smoke_script.splitlines()[0] == "set -euo pipefail"
    assert "|| true" not in smoke_script
    assert "shopt -s nullglob" in smoke_script
    assert 'wheels=( "$GITHUB_WORKSPACE"/dist/*.whl )' in smoke_script
    assert '[ "${#wheels[@]}" -ne 1 ]' in smoke_script
    assert 'wheel_path="$(realpath "${wheels[0]}")"' in smoke_script
    assert 'test -f "$wheel_path"' in smoke_script
    assert 'mktemp -d "$RUNNER_TEMP/' in smoke_script
    assert 'python -m venv "$venv_dir"' in smoke_script
    assert '"$venv_python" -m pip install "$wheel_path"' in smoke_script
    assert 'smoke_dir="$(mktemp -d "$RUNNER_TEMP/' in smoke_script
    assert '"$venv_python" -m pip check' in smoke_script

    ordered_markers = [
        "unset PYTHONPATH",
        '"$venv_python" -m pip install "$wheel_path"',
        'cd "$smoke_dir"',
        "\"$venv_python\" - <<'PY'",
        '"$venv_python" -m pip check',
    ]
    ordered_positions = [smoke_script.index(marker) for marker in ordered_markers]
    assert ordered_positions == sorted(ordered_positions)

    assert "unset PYTHONPATH" in smoke_script
    assert "PYTHONPATH=" not in smoke_script
    assert "sys.path" not in smoke_script
    assert "pip install -e" not in smoke_script
    assert "pip install dist/*.whl" not in smoke_script

    assert "Path(bt_api_py.__file__).resolve()" in import_script
    assert 'Path(os.environ["GITHUB_WORKSPACE"]).resolve()' in import_script
    assert "module_path.relative_to(workspace)" in import_script
    assert 'print(f"Imported bt_api_py from: {module_path}")' in import_script


def test_performance_job_requires_manual_opt_in_and_read_only_permissions() -> None:
    workflow = load_workflow()
    performance_job = workflow["jobs"]["performance"]

    assert performance_job["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.run_performance == true"
    )
    assert performance_job["permissions"] == {"contents": "read"}
    assert performance_job.get("continue-on-error", "false").lower() != "true"


def test_performance_baseline_is_a_versioned_singleton_contract() -> None:
    baseline = json.loads(PERFORMANCE_BASELINE_PATH.read_text(encoding="utf-8"))

    assert baseline["schema_version"] == 1
    assert list(baseline["benchmarks"]) == [PERFORMANCE_BENCHMARK_FULLNAME]


def test_make_performance_target_runs_exact_parallel_benchmark_then_checks_report() -> None:
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    target_start = makefile.index("\ntest-performance:")
    target_end = makefile.index("\ntest-contracts:", target_start)
    performance_target = makefile[target_start:target_end]
    benchmark_command = (
        "python -m pytest "
        "tests/performance/test_event_normalization_performance.py::"
        "test_normalize_event_orderbook_dict_hot_path"
    )
    checker_command = "python scripts/ci/check_performance_baseline.py"

    assert benchmark_command in performance_target
    assert "-n 8" in performance_target
    assert '--benchmark-json="$$benchmark_dir/benchmark.json"' in performance_target
    assert checker_command in performance_target
    assert performance_target.index(benchmark_command) < performance_target.index(checker_command)
    assert 'mktemp -d "$${TMPDIR:-/tmp}/bt-api-py-benchmark.XXXXXX"' in performance_target
    assert "trap 'rm -rf \"$$benchmark_dir\"' EXIT" in performance_target
    assert "/Users/yunjinqi/" not in performance_target


def test_performance_tests_fail_closed_when_benchmark_file_is_missing() -> None:
    workflow = load_workflow()
    performance_job = workflow["jobs"]["performance"]
    steps = performance_job["steps"]
    performance_step = next(step for step in steps if step.get("name") == "Run performance tests")
    performance_script = performance_step["run"]

    assert performance_step.get("shell") == "bash"
    assert performance_step.get("continue-on-error", "false").lower() != "true"
    assert performance_script.splitlines()[0] == "set -euo pipefail"
    assert performance_script.index(
        "test -f tests/performance/test_event_normalization_performance.py"
    ) < performance_script.index(
        "python -m pytest tests/performance/test_event_normalization_performance.py::test_normalize_event_orderbook_dict_hot_path"
    )
    assert (
        "tests/performance/test_event_normalization_performance.py::test_normalize_event_orderbook_dict_hot_path"
        in performance_script
    )
    assert "-n 8" in performance_script
    assert "--benchmark-json=benchmark.json" in performance_script
    assert "skip" not in performance_script.lower()
    assert "|| true" not in performance_script


def test_performance_baseline_is_checked_before_strict_upload() -> None:
    workflow = load_workflow()
    performance_job = workflow["jobs"]["performance"]
    steps = performance_job["steps"]
    validation_step = next(
        step for step in steps if step.get("name") == "Validate performance baseline"
    )
    validation_script = validation_step["run"]
    upload_step = next(
        step for step in steps if step.get("name") == "Upload benchmark result artifact"
    )

    assert validation_step.get("shell") == "bash"
    assert validation_step.get("continue-on-error", "false").lower() != "true"
    assert validation_script.splitlines()[0] == "set -euo pipefail"
    assert "python scripts/ci/check_performance_baseline.py" in validation_script
    assert "--baseline docs/acceptance/2026-09-21-performance-baseline.json" in validation_script
    assert "--report benchmark.json" in validation_script
    assert "json.loads" not in validation_script
    assert "benchmarks recorded" not in validation_script
    assert "|| true" not in validation_script
    assert all(step.get("continue-on-error", "false").lower() != "true" for step in steps)
    assert all("|| true" not in step.get("run", "") for step in steps)
    assert steps.index(validation_step) < steps.index(upload_step)

    assert upload_step.get("if", "success()") == "success()"
    assert upload_step.get("continue-on-error", "false").lower() != "true"
    assert upload_step["uses"].startswith("actions/upload-artifact@")
    assert upload_step["with"]["path"] == "benchmark.json"
    assert upload_step["with"]["if-no-files-found"] == "error"
