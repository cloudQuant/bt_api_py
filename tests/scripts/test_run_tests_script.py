"""Contract tests for the repository's shell-based pytest runners."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_SCRIPTS = ("scripts/run_tests.sh",)


def _write_fake_toolchain(
    tmp_path: Path, *, tee_exit: int | None = None
) -> tuple[Path, Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python_capture = tmp_path / "python-argv.bin"
    fake_pytest_capture = tmp_path / "pytest-argv.bin"
    fake_python = fake_bin / "python"
    fake_pytest = fake_bin / "pytest"
    fake_python.write_text(
        r"""#!/bin/sh
printf '%s\0' "$@" > "$FAKE_PYTHON_CAPTURE"
if [ "$1" != "-m" ] || [ "$2" != "pytest" ]; then
    echo "expected python -m pytest" >&2
    exit 97
fi
shift 2
exec "$FAKE_PYTEST" "$@"
""",
        encoding="utf-8",
    )
    fake_pytest.write_text(
        r"""#!/bin/sh
printf '%s\0' "$@" > "$FAKE_PYTEST_CAPTURE"
if [ -n "$FAKE_PYTEST_OUTPUT" ]; then
    printf '%s\n' "$FAKE_PYTEST_OUTPUT"
fi
exit "$FAKE_PYTEST_EXIT"
""",
        encoding="utf-8",
    )
    if tee_exit is not None:
        fake_tee = fake_bin / "tee"
        fake_tee.write_text(
            r"""#!/bin/sh
cat > "$1"
cat "$1"
exit "$FAKE_TEE_EXIT"
""",
            encoding="utf-8",
        )
        fake_tee.chmod(0o755)
    fake_python.chmod(0o755)
    fake_pytest.chmod(0o755)
    return fake_bin, fake_python_capture, fake_pytest_capture, fake_pytest


def _read_argv(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [item.decode("utf-8") for item in path.read_bytes().split(b"\0") if item]


def _run_runner(
    tmp_path: Path,
    runner_relative: str,
    *arguments: str,
    pytest_exit: int = 0,
    pytest_output: str = "",
    tee_exit: int | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    fake_bin, python_capture, pytest_capture, fake_pytest = _write_fake_toolchain(
        tmp_path, tee_exit=tee_exit
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}",
            "FAKE_PYTHON_CAPTURE": str(python_capture),
            "FAKE_PYTEST_CAPTURE": str(pytest_capture),
            "FAKE_PYTEST": str(fake_pytest),
            "FAKE_PYTEST_EXIT": str(pytest_exit),
            "FAKE_PYTEST_OUTPUT": pytest_output,
        }
    )
    if tee_exit is not None:
        environment["FAKE_TEE_EXIT"] = str(tee_exit)
    bash_path = shutil.which("bash")
    assert bash_path is not None
    completed = subprocess.run(
        [bash_path, str(REPO_ROOT / runner_relative), *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )
    return completed, _read_argv(python_capture), _read_argv(pytest_capture)


def _marker_expression(pytest_argv: list[str]) -> str | None:
    marker_indexes = [index for index, argument in enumerate(pytest_argv) if argument == "-m"]
    if not marker_indexes:
        return None
    assert len(marker_indexes) == 1
    return pytest_argv[marker_indexes[0] + 1]


@pytest.mark.parametrize("runner_relative", RUNNER_SCRIPTS)
def test_default_runner_uses_python_module_and_excludes_ctp(
    tmp_path: Path, runner_relative: str
) -> None:
    completed, python_argv, pytest_argv = _run_runner(tmp_path, runner_relative)

    assert completed.returncode == 0
    assert python_argv[:2] == ["-m", "pytest"]
    assert _marker_expression(pytest_argv) == "not ctp"
    assert "--ignore=tests/test_ctp_feed.py" not in pytest_argv


@pytest.mark.parametrize("runner_relative", RUNNER_SCRIPTS)
def test_user_marker_is_grouped_before_default_ctp_exclusion(
    tmp_path: Path, runner_relative: str
) -> None:
    expression = "(unit or integration) and not slow"
    completed, _, pytest_argv = _run_runner(tmp_path, runner_relative, "-m", expression)

    assert completed.returncode == 0
    assert _marker_expression(pytest_argv) == f"({expression}) and not ctp"


@pytest.mark.parametrize("runner_relative", RUNNER_SCRIPTS)
def test_fast_mode_combines_its_filter_with_ctp_exclusion(
    tmp_path: Path, runner_relative: str
) -> None:
    completed, _, pytest_argv = _run_runner(tmp_path, runner_relative, "--fast")

    assert completed.returncode == 0
    assert _marker_expression(pytest_argv) == "(not network and not slow) and not ctp"


@pytest.mark.parametrize("runner_relative", RUNNER_SCRIPTS)
def test_ctp_flag_only_removes_default_exclusion(tmp_path: Path, runner_relative: str) -> None:
    completed, _, pytest_argv = _run_runner(tmp_path, runner_relative, "--ctp")

    assert completed.returncode == 0
    assert _marker_expression(pytest_argv) is None


@pytest.mark.parametrize("runner_relative", RUNNER_SCRIPTS)
@pytest.mark.parametrize("coverage_flag", ("--cov", "--coverage"))
def test_coverage_flags_enable_branch_coverage_and_keep_reports(
    tmp_path: Path, runner_relative: str, coverage_flag: str
) -> None:
    completed, _, pytest_argv = _run_runner(tmp_path, runner_relative, coverage_flag)

    assert completed.returncode == 0
    assert "--cov-branch" in pytest_argv
    assert "--cov=bt_api_py" in pytest_argv
    assert "--cov-report=term-missing" in pytest_argv
    assert "--cov-report=html" in pytest_argv


@pytest.mark.parametrize("runner_relative", RUNNER_SCRIPTS)
def test_parallel_one_omits_xdist_option(tmp_path: Path, runner_relative: str) -> None:
    completed, _, pytest_argv = _run_runner(tmp_path, runner_relative, "--parallel", "1")

    assert completed.returncode == 0
    assert "-n" not in pytest_argv


@pytest.mark.parametrize("runner_relative", RUNNER_SCRIPTS)
def test_pytest_failure_keeps_exit_code_and_prints_failure_summary(
    tmp_path: Path, runner_relative: str
) -> None:
    failure_line = "FAILED tests/unit/test_fake.py - fake failure"
    completed, python_argv, pytest_argv = _run_runner(
        tmp_path,
        runner_relative,
        pytest_exit=23,
        pytest_output=failure_line,
    )

    assert completed.returncode == 23
    assert failure_line in completed.stdout
    assert "FAILURES & ERRORS SUMMARY" in completed.stdout
    assert "Total: 1 failed, 0 errors" in completed.stdout
    assert python_argv[:2] == ["-m", "pytest"]
    assert pytest_argv


@pytest.mark.parametrize("runner_relative", RUNNER_SCRIPTS)
def test_tee_failure_is_propagated_when_pytest_succeeds(
    tmp_path: Path, runner_relative: str
) -> None:
    completed, python_argv, pytest_argv = _run_runner(
        tmp_path, runner_relative, pytest_exit=0, tee_exit=31
    )

    assert python_argv[:2] == ["-m", "pytest"]
    assert pytest_argv
    assert completed.returncode == 31


@pytest.mark.parametrize("runner_relative", RUNNER_SCRIPTS)
def test_pytest_failure_code_takes_priority_over_tee_failure(
    tmp_path: Path, runner_relative: str
) -> None:
    failure_line = "FAILED tests/unit/test_fake.py - fake failure"
    completed, _, _ = _run_runner(
        tmp_path,
        runner_relative,
        pytest_exit=23,
        pytest_output=failure_line,
        tee_exit=31,
    )

    assert completed.returncode == 23
    assert failure_line in completed.stdout
    assert "FAILURES & ERRORS SUMMARY" in completed.stdout
    assert "Total: 1 failed, 0 errors" in completed.stdout


@pytest.mark.parametrize("runner_relative", RUNNER_SCRIPTS)
def test_help_describes_ctp_as_an_include_filter_and_updates_example(
    tmp_path: Path, runner_relative: str
) -> None:
    fake_bin, _, _, _ = _write_fake_toolchain(tmp_path)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}"
    bash_path = shutil.which("bash")
    assert bash_path is not None
    completed = subprocess.run(
        [bash_path, str(REPO_ROOT / runner_relative), "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0
    assert "include tests marked ctp" in completed.stdout.lower()
    assert "--ctp -m ctp" in completed.stdout


def _make_recipe(target: str) -> list[str]:
    lines = (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line == f"{target}:":
            recipe: list[str] = []
            for candidate in lines[index + 1 :]:
                if not candidate.startswith("\t"):
                    break
                recipe.append(candidate.strip())
            return recipe
    raise AssertionError(f"Makefile target not found: {target}")


def test_makefile_coverage_and_ctp_targets_use_runner_contract() -> None:
    assert _make_recipe("test-cov") == ["./scripts/run_tests.sh --cov"]
    assert _make_recipe("test-ctp") == ['./scripts/run_tests.sh --ctp -m "ctp"']
