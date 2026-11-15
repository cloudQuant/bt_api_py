"""Small offline CLI orchestration tests; no fixture is used by product code."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mock_cli as cli
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review


class FakeSimulation:
    def __init__(self, root, run_id):
        self.root, self.run_id = root, run_id
        self._audit = {
            "year": 2026,
            "capture_id": "1" * 32,
            "parent_id": "2" * 32,
            "derived_id": "3" * 32,
        }
        self._store = SimpleNamespace(read_current=self.current)
        self.mode = None
        self.status = "FIRST_SIMULATION_PUBLICATION"
        self.baseline = "before"
        self.changed = "original"
        self.published = []
        self.record = None
        self.variant = None

    def _guard(self):
        pass

    def current(self):
        if not self.published:
            raise ValueError("no current")
        return SimpleNamespace(
            publication_id=self.published[-1]["publication_id"],
            publication_sha256="a" * 64,
            version_id=self.published[-1]["version_id"],
            version_sha256="b" * 64,
            coverage_status="DECLARED_UNVERIFIED",
        )

    def initialize(self, *, mode):
        self.mode = mode
        return {"status": "INITIALIZED"}

    def prepare(self, *, candidate_id):
        return {
            "data_origin": "MOCK",
            "environment": "SIMULATION_ONLY",
            "status": self.status,
            "candidate_id": candidate_id,
            "policy": {"mode": self.mode, "revision": "r1"},
            "baseline": {"value": self.baseline},
            "difference": {
                "changes": {"trading_days": [{"operation": "added", "after": self.changed}]}
            },
        }

    def build_candidate(self, *, candidate_id, year, variant, emit):
        assert year == 2026
        self.variant = variant
        emit({"event": "FAKE_CANDIDATE", "candidate_id": candidate_id})
        self.status = "SAME" if variant == "BASE" else "CHANGED"
        return {"status": "COMMITTED", "candidate_id": candidate_id}

    def publish(
        self, *, proposal, publication_id, version_id, emit, reviewer=None, confirmed_at=None
    ):
        emit({"event": "FAKE_PUBLISH", "publication_id": publication_id, "version_id": version_id})
        if proposal["status"] == "SAME":
            return {"status": "SAME"}
        self.published.append(
            {
                "publication_id": publication_id,
                "version_id": version_id,
                "reviewer": reviewer,
                "confirmed_at": confirmed_at,
            }
        )
        self.record = (publication_id, version_id)
        return {
            "status": "COMMITTED",
            "publication": "SIMULATION_ONLY",
            "result": {"visibility": "COMMITTED", "fence": "CLEAR", "post_commit": "COMPLETE"},
        }

    def verify_publication(self, *, publication_id):
        return {
            "status": "VISIBLE" if self.record and self.record[0] == publication_id else "UNKNOWN"
        }

    def inspect_publication_claim(self, *, publication_id):
        return {
            "status": "VISIBLE_UNAUTHENTICATED"
            if self.record and self.record[0] == publication_id
            else "NOT_OBSERVED"
        }

    def inspect_version_claim(self, *, version_id):
        return {
            "status": "VISIBLE_UNAUTHENTICATED"
            if self.record and self.record[1] == version_id
            else "NOT_OBSERVED"
        }

    def inspect_first_difference(self, *, publication_id):
        return {"status": "NOT_OBSERVED"}


@pytest.fixture
def setup_cli(tmp_path, monkeypatch):
    root = str(tmp_path.resolve())
    fds, _ = review._open(root, True)
    runs._close(fds)
    run_id = "a" * 32
    operator = FakeSimulation(root, run_id)
    monkeypatch.setattr(cli, "MockCalendarPublication", lambda **_: operator)

    def run_once(**kwargs):
        operator.run_id = kwargs["run_id"]
        return {
            "simulation_root": root,
            "run_id": kwargs["run_id"],
            "candidate_state": "COMMITTED",
            "state": "INCOMPLETE",
            "overall": "INCOMPLETE",
            "simulation_publication": "NOT_ATTEMPTED",
        }

    monkeypatch.setattr(runs, "run_once", run_once)
    return root, operator


def outputs(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def initial(root, operator, capsys, mode="manual"):
    rc = runs.main(
        [
            "run",
            "--mock",
            "--mock-year",
            "2026",
            "--mock-review-mode",
            mode,
            "--budget-seconds",
            "30",
            "--trusted-local-posix",
        ]
    )
    lines = outputs(capsys)
    return rc, lines[-1], lines


def test_cli_manual_pending_show_confirm_and_no_replay(setup_cli, capsys):
    root, operator = setup_cli
    rc, pending, lines = initial(root, operator, capsys)
    assert rc == 10 and pending["status"] == "PENDING_REVIEW"
    assert pending["environment"] == "SIMULATION_ONLY"
    assert pending["proposal"]["difference"]["changes"]["trading_days"][0]["after"] == "original"
    assert lines[0]["event"] == "MOCK_PUBLICATION_IDS_ALLOCATED"
    assert not operator.published
    run_id = operator.run_id
    rc = runs.main(
        [
            "mock-show",
            "--calendar-root",
            root,
            "--run-id",
            run_id,
            "--review-id",
            pending["review_id"],
            "--trusted-local-posix",
        ]
    )
    shown = outputs(capsys)[-1]
    assert rc == 10 and shown["current"]["status"] == "UNKNOWN"
    assert shown["pending_review"]["status"] == "VISIBLE_UNAUTHENTICATED"
    assert shown["pending_review"]["body"]["publication_id"] == pending["publication_id"]
    command = [
        "mock-confirm",
        "--calendar-root",
        root,
        "--run-id",
        run_id,
        "--review-id",
        pending["review_id"],
        "--review-sha256",
        pending["review_payload_sha256"],
        "--reviewer",
        "MOCK_reviewer",
        "--confirmed-at",
        "2026-11-13T00:00:00.000000Z",
        "--trusted-local-posix",
    ]
    assert runs.main(command) == 0
    confirmed = outputs(capsys)[-1]
    assert confirmed["status"] == "COMMITTED" and confirmed["current_visibility"] == "VISIBLE"
    assert operator.published[0]["reviewer"] == "MOCK_reviewer"
    assert runs.main(command) == 25
    assert outputs(capsys)[-1]["reason"] == "ORIGINAL_IDS_RECONCILE_FIRST"
    assert len(operator.published) == 1


def test_pending_review_rejects_stale_or_changed_payload(setup_cli, capsys):
    root, operator = setup_cli
    _, pending, _ = initial(root, operator, capsys)
    command = [
        "mock-confirm",
        "--calendar-root",
        root,
        "--run-id",
        operator.run_id,
        "--review-id",
        pending["review_id"],
        "--review-sha256",
        pending["review_payload_sha256"],
        "--reviewer",
        "MOCK_reviewer",
        "--confirmed-at",
        "2026-11-13T00:00:00.000000Z",
        "--trusted-local-posix",
    ]
    operator.baseline = "changed"
    assert runs.main(command) == 24
    assert outputs(capsys)[-1]["reason"] == "REVIEW_STALE_OR_CHANGED"
    assert not operator.published
    operator.baseline = "before"
    path = Path(root) / "review-bundles" / f"mock-review-{pending['review_id']}.json"
    body, _ = storage._unwrap(path.read_bytes())
    body["proposal"]["difference"]["changes"]["trading_days"][0]["after"] = "tampered"
    path.write_bytes(storage._envelope(body))
    assert runs.main(command) == 25
    assert outputs(capsys)[-1]["reason"] == "PENDING_REVIEW_UNAVAILABLE_OR_CHANGED"
    assert not operator.published


def test_auto_run_and_later_unchanged_and_changed_updates(setup_cli, capsys):
    root, operator = setup_cli
    rc, result, _ = initial(root, operator, capsys, "auto")
    assert rc == 0 and result["status"] == "COMMITTED"
    run_id = operator.run_id
    rc = runs.main(
        [
            "mock-update",
            "--calendar-root",
            root,
            "--run-id",
            run_id,
            "--variant",
            "BASE",
            "--trusted-local-posix",
        ]
    )
    lines = outputs(capsys)
    assert (
        rc == 10
        and lines[0]["event"] == "MOCK_UPDATE_IDS_ALLOCATED"
        and lines[-1]["status"] == "SAME"
    )
    rc = runs.main(
        [
            "mock-update",
            "--calendar-root",
            root,
            "--run-id",
            run_id,
            "--variant",
            "AFTERNOON_CHANGE",
            "--trusted-local-posix",
        ]
    )
    lines = outputs(capsys)
    assert rc == 0 and lines[-1]["status"] == "COMMITTED"
    assert operator.variant == "AFTERNOON_CHANGE" and len(operator.published) == 2


def test_cli_blocks_real_configuration_and_network_flags(setup_cli, capsys):
    root, operator = setup_cli
    for extra in (["--config", root], ["--enable-shfe"], ["--enable-ine"]):
        rc = runs.main(
            [
                "run",
                "--mock",
                "--mock-year",
                "2026",
                "--mock-review-mode",
                "auto",
                "--budget-seconds",
                "30",
                "--trusted-local-posix",
                *extra,
            ]
        )
        assert rc == 2
        assert outputs(capsys)[-1]["status"] == "ERROR"
    assert operator.mode is None and not operator.published


def test_unknown_pending_write_is_not_promoted(setup_cli, monkeypatch):
    root, operator = setup_cli
    operator.mode = "manual"
    ids = cli.allocate(candidate_id="4" * 32)
    proposal = operator.prepare(candidate_id=ids["candidate_id"])
    original = storage._write

    def uncertain(*args):
        assert original(*args) == "COMMITTED"
        return "COMMIT_UNKNOWN"

    monkeypatch.setattr(storage, "_write", uncertain)
    result = cli._act(operator, ids, deepcopy(proposal))
    assert result["status"] == "COMMIT_UNKNOWN"
    observed = cli._read_pending(operator, ids["review_id"])
    assert observed["status"] == "VISIBLE_UNAUTHENTICATED"
    assert not operator.published


def test_visible_pointer_with_incomplete_post_commit_never_exits_success(setup_cli, monkeypatch):
    _, operator = setup_cli
    operator.mode = "auto"
    ids = cli.allocate(candidate_id="4" * 32)
    proposal = operator.prepare(candidate_id=ids["candidate_id"])
    monkeypatch.setattr(
        operator,
        "publish",
        lambda **_: {
            "status": "COMMITTED",
            "publication": "SIMULATION_ONLY",
            "result": {"visibility": "COMMITTED", "fence": "CLEAR", "post_commit": "INCOMPLETE"},
        },
    )
    result = cli._act(operator, ids, proposal)
    assert result["status"] == "COMMIT_UNKNOWN"
    assert result["reason"] == "PUBLICATION_PROOF_INCOMPLETE"
    assert cli._result_code(result) == 25
