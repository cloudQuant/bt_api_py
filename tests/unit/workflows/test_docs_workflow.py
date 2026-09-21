"""Structural contracts for the documentation workflow."""

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "docs.yml"


def load_workflow() -> dict[str, object]:
    """Load GitHub Actions YAML without coercing the ``on`` key to a boolean."""
    with WORKFLOW_PATH.open(encoding="utf-8") as workflow_file:
        return yaml.load(workflow_file, Loader=yaml.BaseLoader)


def test_push_and_pull_request_docs_gates_cover_all_build_inputs() -> None:
    workflow = load_workflow()
    expected_paths = {
        "docs/**",
        "README.md",
        "mkdocs.yml",
        ".readthedocs.yaml",
        "docs/requirements.txt",
        ".github/workflows/docs.yml",
        "pyproject.toml",
        "setup.py",
        "MANIFEST.in",
        "bt_api_py/**",
        "scripts/generate_exchange_support_docs.py",
        "scripts/ci/check_docs_contract.py",
    }

    for event in ("push", "pull_request"):
        paths = set(workflow["on"][event]["paths"])
        assert expected_paths <= paths


def test_documentation_dependencies_use_setup_python_and_resolve_package_dependencies() -> None:
    workflow = load_workflow()
    build = workflow["jobs"]["build"]
    steps = build["steps"]
    setup_python_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("actions/setup-python@")
    )
    install_commands = [
        (index, step["run"])
        for index, step in enumerate(steps)
        if step.get("name")
        in {"Install doc dependencies", "Install package (needed by mkdocstrings)"}
    ]

    assert len(install_commands) == 2
    assert all(index > setup_python_index for index, _ in install_commands)
    commands = [command for _, command in install_commands]
    assert "python -m pip install --upgrade pip -r docs/requirements.txt" in commands
    assert "python -m pip install -e ." in commands
    combined_commands = "\n".join(commands)
    assert "--no-build-isolation" not in combined_commands
    assert "--no-deps" not in combined_commands
    assert "||" not in combined_commands

    assert build.get("continue-on-error", "false").lower() != "true"
    assert not {
        "PIP_NO_DEPS",
        "PIP_NO_BUILD_ISOLATION",
    } & set(workflow.get("env", {}))
    assert not {
        "PIP_NO_DEPS",
        "PIP_NO_BUILD_ISOLATION",
    } & set(build.get("env", {}))

    for index, _ in install_commands:
        step = steps[index]
        assert step.get("continue-on-error", "false").lower() != "true"
        assert not {
            "PIP_NO_DEPS",
            "PIP_NO_BUILD_ISOLATION",
        } & set(step.get("env", {}))


def test_generated_docs_and_contract_checks_block_before_strict_build() -> None:
    workflow = load_workflow()
    steps = workflow["jobs"]["build"]["steps"]
    commands = [step.get("run") for step in steps]
    generated_check = "python scripts/generate_exchange_support_docs.py --check"
    contract_check = "python scripts/ci/check_docs_contract.py"
    strict_build = "mkdocs build --strict"

    assert generated_check in commands
    assert contract_check in commands
    assert strict_build in commands
    assert commands.index(generated_check) < commands.index(contract_check)
    assert commands.index(contract_check) < commands.index(strict_build)

    for step in steps:
        if step.get("run") in {generated_check, contract_check}:
            assert step.get("continue-on-error", "false").lower() != "true"


def test_pages_upload_and_deploy_conditions_remain_unchanged() -> None:
    workflow = load_workflow()
    steps = workflow["jobs"]["build"]["steps"]
    upload = next(step for step in steps if step.get("name") == "Upload artifact")
    deploy = workflow["jobs"]["deploy"]
    deploy_step = next(step for step in deploy["steps"] if step.get("id") == "deployment")

    assert upload["if"] == "github.event_name != 'pull_request'"
    assert upload["uses"] == "actions/upload-pages-artifact@v4"
    assert upload["with"]["path"] == "site/"
    assert deploy["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/master'"
    assert deploy["needs"] == "build"
    assert deploy["permissions"]["pages"] == "write"
    assert deploy["permissions"]["id-token"] == "write"
    assert deploy_step["uses"] == "actions/deploy-pages@v5"
