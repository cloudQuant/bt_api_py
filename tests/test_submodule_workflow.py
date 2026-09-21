"""Structural checks for report-only submodule quality workflow jobs."""

from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "submodule-tests.yml"
MODULE_MATRIX = "${{ fromJSON(needs.discover-submodules.outputs.modules) }}"


def test_submodule_lint_and_format_are_separate_report_only_jobs() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # BaseLoader keeps all YAML scalars as strings, avoiding YAML 1.1's `on` boolean quirk.
    workflow = yaml.load(workflow_text, Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]

    assert "submodule-quality" not in jobs
    assert "submodule-lint" in jobs
    assert "submodule-format" in jobs
    assert "截至 2026-09-20：lint 存量 10 项、format 涉及 452 个文件" in workflow_text
    assert "分别清零、稳定 CI、独立回滚后，才分别升级为 blocking" in workflow_text

    expected_commands = {
        "submodule-lint": "ruff check bt_api/${{ matrix.module }}",
        "submodule-format": "ruff format --check bt_api/${{ matrix.module }}",
    }
    for job_name, expected_command in expected_commands.items():
        job = jobs[job_name]
        assert job["needs"] == "discover-submodules"
        assert job["continue-on-error"] == "true"
        assert job["strategy"]["matrix"]["module"] == MODULE_MATRIX

        steps = job["steps"]
        checkout = next(
            step for step in steps if step.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout["with"]["submodules"] == "recursive"

        setup_python = next(
            step for step in steps if step.get("uses", "").startswith("actions/setup-python@")
        )
        assert setup_python["with"]["python-version"] == "3.11"

        install_ruff = next(step for step in steps if step.get("name") == "Install ruff")
        assert 'pip install "ruff==0.16.2"' in install_ruff["run"]

        ruff_commands = [
            line.strip()
            for step in steps
            for line in step.get("run", "").splitlines()
            if line.strip().startswith("ruff ")
        ]
        assert ruff_commands == [expected_command]
