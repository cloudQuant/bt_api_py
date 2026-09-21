"""Structural contracts for the release candidate and publishing workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "publish.yml"


def load_workflow() -> dict[str, Any]:
    """Load the workflow while preserving expression strings and the on key."""
    with WORKFLOW_PATH.open(encoding="utf-8") as workflow_file:
        return yaml.load(workflow_file, Loader=yaml.BaseLoader)


def step_by_name(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def run_steps(workflow: dict[str, Any]) -> list[str]:
    return [
        step.get("run", "") for job in workflow["jobs"].values() for step in job.get("steps", [])
    ]


def test_release_flow_has_one_build_and_a_strict_publication_dependency_chain() -> None:
    workflow = load_workflow()
    jobs = workflow["jobs"]

    assert list(jobs).count("build") == 1
    assert jobs["publish-testpypi"]["needs"] == ["build"]
    assert jobs["smoke-install-testpypi"]["needs"] == ["build", "publish-testpypi"]
    assert jobs["publish-pypi"]["needs"] == [
        "build",
        "publish-testpypi",
        "smoke-install-testpypi",
    ]
    release_gate = "github.event_name == 'release' && vars.ENABLE_PYPI_RELEASE == 'true'"
    for job_name in ("publish-testpypi", "smoke-install-testpypi", "publish-pypi"):
        assert jobs[job_name]["if"] == release_gate
    assert jobs["publish-pypi"]["environment"]["name"] == "pypi"


def test_expected_sha_enters_shell_only_through_env_and_release_tag_is_bound_to_version() -> None:
    workflow = load_workflow()
    build = workflow["jobs"]["build"]
    guard = step_by_name(build, "Guard candidate source")
    guard_script = guard["run"]

    assert guard["env"]["EXPECTED_SHA"] == "${{ inputs.expected_sha }}"
    assert "${{ inputs.expected_sha }}" not in "\n".join(run_steps(workflow))
    assert 'if [[ ! "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]' in guard_script
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in guard_script
    assert '[[ "$ACTUAL_SHA" != "$SOURCE_SHA" ]]' in guard_script
    release_guard = step_by_name(build, "Verify release tag version")
    assert release_guard["env"]["RELEASE_TAG"] == "${{ github.ref_name }}"
    assert 'if [[ "$RELEASE_TAG" != "v$VERSION" ]]; then' in release_guard["run"]


def test_build_records_candidate_outputs_and_uploads_strict_single_artifact() -> None:
    workflow = load_workflow()
    build = workflow["jobs"]["build"]
    candidate_step = step_by_name(build, "Record release candidate")
    outputs = build["outputs"]
    assert outputs["source_sha"] == "${{ steps.source.outputs.source_sha }}"
    assert outputs["version"] == "${{ steps.version.outputs.version }}"
    for key in ("wheel_filename", "wheel_sha256", "manifest_filename"):
        assert outputs[key] == f"${{{{ steps.candidate.outputs.{key} }}}}"
    for key in ("sdist_filename", "sdist_sha256", "manifest_sha256"):
        assert outputs[key] == f"${{{{ steps.candidate.outputs.{key} }}}}"

    assert candidate_step["id"] == "candidate"
    assert "release_candidate.py" in candidate_step["run"]
    assert "record" in candidate_step["run"]
    upload = step_by_name(build, "Upload release candidate")
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"]["if-no-files-found"] == "error"
    assert set(upload["with"]["path"].splitlines()) == {"dist/", "dist-meta/"}
    assert upload["with"]["name"] == "release-candidate"


def test_both_publish_jobs_download_the_same_candidate_and_verify_before_publish() -> None:
    workflow = load_workflow()
    for job_name, publish_step_name in (
        ("publish-testpypi", "Publish to TestPyPI"),
        ("publish-pypi", "Publish to PyPI"),
    ):
        job = workflow["jobs"][job_name]
        steps = job["steps"]
        checkout = next(
            step for step in steps if step.get("uses", "").startswith("actions/checkout@")
        )
        download = next(
            step for step in steps if step.get("uses", "").startswith("actions/download-artifact@")
        )
        verify = step_by_name(job, "Verify release candidate")
        publish = step_by_name(job, publish_step_name)
        assert checkout["with"]["ref"] == "${{ needs.build.outputs.source_sha }}"
        assert download["with"]["name"] == "release-candidate"
        assert download["with"]["path"] == "dist-artifact/"
        assert "release_candidate.py" in verify["run"]
        assert "verify" in verify["run"]
        assert verify["env"]["WHEEL_FILENAME"] == "${{ needs.build.outputs.wheel_filename }}"
        assert verify["env"]["WHEEL_SHA256"] == "${{ needs.build.outputs.wheel_sha256 }}"
        assert verify["env"]["SDIST_FILENAME"] == "${{ needs.build.outputs.sdist_filename }}"
        assert verify["env"]["SDIST_SHA256"] == "${{ needs.build.outputs.sdist_sha256 }}"
        assert verify["env"]["MANIFEST_SHA256"] == "${{ needs.build.outputs.manifest_sha256 }}"
        assert '--expected-wheel-filename "$WHEEL_FILENAME"' in verify["run"]
        assert '--expected-wheel-sha256 "$WHEEL_SHA256"' in verify["run"]
        assert '--expected-sdist-filename "$SDIST_FILENAME"' in verify["run"]
        assert '--expected-sdist-sha256 "$SDIST_SHA256"' in verify["run"]
        assert '--expected-manifest-sha256 "$MANIFEST_SHA256"' in verify["run"]
        assert steps.index(download) < steps.index(verify) < steps.index(publish)
        assert "dist-artifact/dist/" in publish["with"]["packages-dir"]
        assert job.get("continue-on-error", "false").lower() != "true"


def test_smoke_uses_testpypi_exact_wheel_bounded_retry_then_verified_local_install() -> None:
    workflow = load_workflow()
    smoke = workflow["jobs"]["smoke-install-testpypi"]
    checkouts = [
        step for step in smoke["steps"] if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["ref"] == "${{ needs.build.outputs.source_sha }}"
    download = next(
        step
        for step in smoke["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    smoke_step = step_by_name(smoke, "Install candidate in a fresh virtualenv and smoke test")
    script = smoke_step["run"]
    candidate_verify = step_by_name(smoke, "Verify release candidate")
    assert candidate_verify["env"]["WHEEL_FILENAME"] == "${{ needs.build.outputs.wheel_filename }}"
    assert candidate_verify["env"]["WHEEL_SHA256"] == "${{ needs.build.outputs.wheel_sha256 }}"
    assert candidate_verify["env"]["SDIST_FILENAME"] == "${{ needs.build.outputs.sdist_filename }}"
    assert candidate_verify["env"]["SDIST_SHA256"] == "${{ needs.build.outputs.sdist_sha256 }}"
    assert (
        candidate_verify["env"]["MANIFEST_SHA256"] == "${{ needs.build.outputs.manifest_sha256 }}"
    )
    assert '--expected-wheel-filename "$WHEEL_FILENAME"' in candidate_verify["run"]
    assert '--expected-wheel-sha256 "$WHEEL_SHA256"' in candidate_verify["run"]
    assert '--expected-sdist-filename "$SDIST_FILENAME"' in candidate_verify["run"]
    assert '--expected-sdist-sha256 "$SDIST_SHA256"' in candidate_verify["run"]
    assert '--expected-manifest-sha256 "$MANIFEST_SHA256"' in candidate_verify["run"]
    assert smoke_step["env"]["WHEEL_FILENAME"] == "${{ needs.build.outputs.wheel_filename }}"
    assert smoke_step["env"]["WHEEL_SHA256"] == "${{ needs.build.outputs.wheel_sha256 }}"
    assert smoke_step["env"]["SDIST_FILENAME"] == "${{ needs.build.outputs.sdist_filename }}"
    assert smoke_step["env"]["SDIST_SHA256"] == "${{ needs.build.outputs.sdist_sha256 }}"
    assert smoke_step["env"]["MANIFEST_SHA256"] == "${{ needs.build.outputs.manifest_sha256 }}"

    assert smoke["permissions"] == {"contents": "read"}
    assert download["with"]["name"] == "release-candidate"
    assert 'work_dir="$(mktemp -d "$RUNNER_TEMP/' in script
    assert 'cd "$attempt_dir"' in script
    assert "for attempt in 1 2 3 4 5" in script
    assert "--timeout 15" in script
    assert "--retries 0" in script
    assert "--no-cache-dir" in script
    assert "--no-deps" in script
    assert "--only-binary=:all:" in script
    assert '"bt_api_py==$VERSION"' in script
    download_segment = script.split("python3 -m pip download", maxsplit=1)[1].split(
        'if [[ "$downloaded" -ne 1 ]]', maxsplit=1
    )[0]
    install_segment = script.split('"$venv_python" -m pip install', maxsplit=1)[1].split(
        "printf 'SMOKE_VENV", maxsplit=1
    )[0]
    assert "--index-url https://test.pypi.org/simple/" in download_segment
    assert "--extra-index-url" not in download_segment
    assert "https://pypi.org/simple/" not in download_segment
    assert "--index-url https://pypi.org/simple/" in install_segment
    assert "https://test.pypi.org/simple/" not in install_segment
    assert "--extra-index-url" not in install_segment
    assert "verify-downloaded-wheel" in script
    assert '--expected-wheel-filename "$WHEEL_FILENAME"' in script
    assert '--expected-wheel-sha256 "$WHEEL_SHA256"' in script
    assert '--expected-sdist-filename "$SDIST_FILENAME"' in script
    assert '--expected-sdist-sha256 "$SDIST_SHA256"' in script
    assert '--expected-manifest-sha256 "$MANIFEST_SHA256"' in script
    assert script.index("verify-downloaded-wheel") < script.index('"$wheel_path"')
    assert '"$venv_python" -m pip install' in script
    assert "--no-deps" not in script.split('"$venv_python" -m pip install', maxsplit=1)[1]
    assert 'wheel_uri="$(python3 -c' in script
    assert 'local_wheel_requirement="bt_api_py[core-reference] @ $wheel_uri"' in script
    assert '"$local_wheel_requirement"' in script
    assert 'printf \'SMOKE_VENV=%s\\n\' "$venv_dir" >> "$GITHUB_ENV"' in script
    assert 'assert bt_api_py.__version__ == os.environ["VERSION"]' in script
    assert "package_path.is_relative_to(workspace)" in script
    assert "set -euo pipefail" in script.splitlines()[0]


def test_publish_workflow_has_no_fail_open_or_manual_pypi_path() -> None:
    workflow = load_workflow()
    all_scripts = "\n".join(run_steps(workflow))
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["publish-testpypi"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert jobs["publish-pypi"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert "skip-existing" not in all_scripts
    assert "|| true" not in all_scripts
    for job in jobs.values():
        assert job.get("continue-on-error", "false").lower() != "true"
        for step in job.get("steps", []):
            assert step.get("continue-on-error", "false").lower() != "true"
