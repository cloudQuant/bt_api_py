"""Batch intent IDs must fence public publish, claims and confirmation."""

import fcntl
import json
import subprocess
import sys

import pytest

from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root, scope
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_fresh_observation import confirm
from tests.unit.test_trading_calendar_mock_persistent import operation

_PAUSE_AUTO = """
from bt_api_py import trading_calendar_mock_cli as batch_cli
_original_action = batch_cli._act
def hold_batch(operator, ids, proposal, *, input_binding=None):
    if os.environ.get('HOLD_BATCH') and operator._is_batch_proposal(proposal):
        return {'status':'READY_FOR_TEST', 'proposal':proposal, 'run_id':operator.run_id, **ids}
    return _original_action(operator, ids, proposal, input_binding=input_binding)
batch_cli._act = hold_batch
"""


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_public_publish_refuses_each_swapped_id_and_original_remains_usable(tmp_path, mode):
    root, env, run_id = _initialized_batch_root(tmp_path, mode, _PAUSE_AUTO)
    if mode == "auto":
        env["HOLD_BATCH"] = "1"
    before = (root / "CURRENT.json").read_bytes()
    rc, rows, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-batch",
            "--scope-json",
            json.dumps(scope("SHFE")),
            "--scope-json",
            json.dumps(scope("DCE")),
        ),
    )
    batch = rows[-1]
    assert batch["status"] == ("READY_FOR_TEST" if mode == "auto" else "PENDING_REVIEW"), (
        rows,
        err,
    )
    operator = MockCalendarPublication(root=str(root), run_id=batch["run_id"])
    confirmation = (
        {"reviewer": "MOCK_reviewer", "confirmed_at": "2026-11-13T00:00:00.000000Z"}
        if mode == "manual"
        else {}
    )
    original = {
        "publication_id": batch["publication_id"],
        "version_id": batch["version_id"],
        "review_id": batch["review_id"],
    }
    for key, value in (
        ("publication_id", "MOCK_wrong_pub"),
        ("version_id", "MOCK_wrong_version"),
        ("review_id", "f" * 32),
        ("review_id", None),
    ):
        arguments = {**original, key: value}
        outcome = operator.publish(
            proposal=batch["proposal"], emit=lambda _: None, **arguments, **confirmation
        )
        assert outcome["status"] == "NOT_COMMITTED" and outcome["reason"] == "BATCH_ID_MISMATCH", (
            outcome
        )
        assert (root / "CURRENT.json").read_bytes() == before
        assert not (root / "publications" / "MOCK_wrong_pub.json").exists()
        assert not (root / "versions" / "MOCK_wrong_version.json").exists()
    bad_proposal = {**batch["proposal"], "candidate_id": "e" * 32}
    rejected = operator.publish(
        proposal=bad_proposal, emit=lambda _: None, **original, **confirmation
    )
    assert rejected["status"] != "COMMITTED" and (root / "CURRENT.json").read_bytes() == before
    if mode == "manual":
        outcome = confirm(env, root, batch)
    else:
        outcome = operator.publish(proposal=batch["proposal"], emit=lambda _: None, **original)
    assert outcome["status"] == "COMMITTED", outcome
    assert (
        operator.verify_publication(publication_id=batch["publication_id"])["status"] == "VISIBLE"
    )
    assert outcome["source_authentication"] == "NOT_PROVEN"
    same_before = (root / "CURRENT.json").read_bytes()
    env["HOLD_BATCH"] = "1"
    rc, rows, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-batch",
            "--scope-json",
            json.dumps(scope("SHFE")),
            "--scope-json",
            json.dumps(scope("DCE")),
        ),
    )
    same = rows[-1]
    assert same["status"] == "READY_FOR_TEST" and same["proposal"]["status"] == "SAME", (rows, err)
    same_operator = MockCalendarPublication(root=str(root), run_id=same["run_id"])
    same_ids = {
        "publication_id": same["publication_id"],
        "version_id": same["version_id"],
        "review_id": same["review_id"],
    }
    for key, value in (
        ("publication_id", "MOCK_wrong_same_pub"),
        ("version_id", "MOCK_wrong_same_version"),
        ("review_id", "f" * 32),
    ):
        rejected = same_operator.publish(
            proposal=same["proposal"], emit=lambda _: None, **{**same_ids, key: value}
        )
        assert (
            rejected["status"] == "NOT_COMMITTED" and rejected["reason"] == "BATCH_ID_MISMATCH"
        ), rejected
        assert (root / "CURRENT.json").read_bytes() == same_before
    accepted = same_operator.publish(proposal=same["proposal"], emit=lambda _: None, **same_ids)
    assert accepted["status"] == "SAME" and (root / "CURRENT.json").read_bytes() == same_before


def test_cross_process_wrong_ids_leave_original_manual_confirmation_available(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "manual")
    before = (root / "CURRENT.json").read_bytes()
    rc, rows, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-batch",
            "--scope-json",
            json.dumps(scope("SHFE")),
            "--scope-json",
            json.dumps(scope("DCE")),
        ),
    )
    assert rc == 10 and rows[-1]["status"] == "PENDING_REVIEW", (rows, err)
    batch = rows[-1]
    confirmation_args = operation(
        root,
        batch["run_id"],
        "mock-confirm",
        "--review-id",
        batch["review_id"],
        "--review-sha256",
        batch["review_payload_sha256"],
        "--reviewer",
        "MOCK_reviewer",
        "--confirmed-at",
        "2026-11-13T00:00:00.000000Z",
    )
    with (root / ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc, busy, error = command(env, *confirmation_args)
        fcntl.flock(lock, fcntl.LOCK_UN)
    assert rc != 0 and busy[-1]["reason"] == "SIMULATION_STORE_BUSY", (busy, error)
    assert busy[-1]["run_id"] == batch["run_id"] and busy[-1]["review_id"] == batch["review_id"]
    assert "Traceback" not in error and (root / "CURRENT.json").read_bytes() == before
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(json.dumps(batch["proposal"]))
    script = """
import json, sys
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
op = MockCalendarPublication(root=sys.argv[1], run_id=sys.argv[2])
with open(sys.argv[3]) as f: proposal = json.load(f)
result = op.publish(proposal=proposal, publication_id=sys.argv[4], version_id=sys.argv[5],
                    review_id=sys.argv[6], reviewer='MOCK_reviewer',
                    confirmed_at='2026-11-13T00:00:00.000000Z', emit=lambda _:None)
print(json.dumps(result))
"""
    arguments = [
        ("MOCK_wrong_pub", batch["version_id"], batch["review_id"]),
        (batch["publication_id"], "MOCK_wrong_version", batch["review_id"]),
    ]
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(root), batch["run_id"], str(proposal_path), *args],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for args in arguments
    ]
    for child in children:
        output, error = child.communicate(timeout=30)
        if child.returncode != 0:
            assert "BlockingIOError" in error, error
        else:
            result = json.loads(output)
            assert result["status"] != "COMMITTED", result
    assert (root / "CURRENT.json").read_bytes() == before
    assert confirm(env, root, batch)["status"] == "COMMITTED"
