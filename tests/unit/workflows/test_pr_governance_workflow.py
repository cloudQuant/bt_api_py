"""Structural contract for the offline PR governance workflow."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pr-governance.yml"


def test_workflow_uses_trusted_scripts_and_untrusted_pr_checkout_separately() -> None:
    with WORKFLOW_PATH.open(encoding="utf-8") as workflow_file:
        workflow = yaml.load(workflow_file, Loader=yaml.BaseLoader)

    job = workflow["jobs"]["governance"]
    steps = job["steps"]
    governance_checkout = next(step for step in steps if step.get("id") == "checkout-governance")
    pr_checkout = next(step for step in steps if step.get("id") == "checkout-pr")
    verify_step = next(step for step in steps if step.get("id") == "verify-objects")
    collect_step = next(step for step in steps if step.get("id") == "collect")
    validate_step = next(step for step in steps if step.get("id") == "validate")
    enforce_step = next(step for step in steps if step.get("name") == "Enforce in strict mode")

    assert governance_checkout["uses"] == "actions/checkout@v6"
    assert governance_checkout["with"]["repository"] == "${{ github.repository }}"
    assert governance_checkout["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert governance_checkout["with"]["path"] == "governance-source"
    assert governance_checkout["with"]["persist-credentials"] == "false"

    assert pr_checkout["uses"] == "actions/checkout@v6"
    assert pr_checkout["with"]["repository"] == "${{ github.repository }}"
    assert pr_checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert pr_checkout["with"]["path"] == "pr-source"
    assert pr_checkout["with"]["fetch-depth"] == "0"
    assert pr_checkout["with"]["persist-credentials"] == "false"
    assert steps.index(governance_checkout) < steps.index(collect_step)
    assert steps.index(pr_checkout) < steps.index(verify_step) < steps.index(collect_step)

    assert verify_step["working-directory"] == "pr-source"
    assert verify_step["env"] == {
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }
    assert verify_step["run"].strip() == "\n".join(
        [
            "set -euo pipefail",
            'git cat-file -e "${BASE_SHA}^{commit}"',
            'git cat-file -e "${HEAD_SHA}^{commit}"',
            'test "$(git rev-parse HEAD)" = "$HEAD_SHA"',
        ]
    )

    assert collect_step["working-directory"] == "pr-source"
    assert collect_step["run"] == (
        "python ../governance-source/scripts/ci/collect_pr_governance_context.py "
        '--output "$RUNNER_TEMP/pr-context.json"'
    )
    assert {
        "BASE_SHA",
        "HEAD_SHA",
        "TARGET_BRANCH",
        "PR_BODY",
        "PR_LABELS",
    } <= set(collect_step["env"])
    assert validate_step["working-directory"] == "governance-source"
    assert "python scripts/ci/validate_pr_governance.py" in validate_step["run"]
    assert '--context "$RUNNER_TEMP/pr-context.json"' in validate_step["run"]
    assert 'tee "$RUNNER_TEMP/pr-result.txt"' in validate_step["run"]
    assert 'echo "exitcode=${PIPESTATUS[0]}" >> "$GITHUB_OUTPUT"' in validate_step["run"]
    assert steps.index(collect_step) < steps.index(validate_step)

    assert "steps.validate.outputs.exitcode != '0'" in enforce_step["if"]
    assert "vars.PR_GOVERNANCE_STRICT == 'true'" in enforce_step["if"]
    assert job.get("continue-on-error") != "true"
    assert all(step.get("continue-on-error") != "true" for step in steps)

    workflow_source = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "two phases" in workflow_source
    assert "dev, master, and" in workflow_source
    assert "code-optimization while PR_GOVERNANCE_STRICT=false" in workflow_source
    assert "PR_GOVERNANCE_STRICT=false" in workflow_source
    assert "observe each branch" in workflow_source
    assert "strict=true after that observation" in workflow_source
    assert "Missing scripts or commit objects must fail closed" in workflow_source
    assert "pr-source/scripts/ci/collect_pr_governance_context.py" not in collect_step["run"]

    legacy_collection = {
        "git diff",
        "old_shas",
        "new_shas",
        "submodules_changed",
        "old_sha",
        "new_sha",
        "[0]",
    }
    assert not any(marker in collect_step["run"] for marker in legacy_collection)
