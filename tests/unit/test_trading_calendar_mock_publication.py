"""Isolated local simulated publication using the actual store and binding."""

import json
import shutil
import signal
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from tests.unit.test_trading_calendar_observation_run import env_without_network


def test_simulated_policy_publications_difference_and_day_switch(tmp_path, monkeypatch):
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_observation_run",
        "run",
        "--mock",
        "--mock-year",
        "2026",
        "--budget-seconds",
        "30",
        "--trusted-local-posix",
    ]
    env = env_without_network(tmp_path)
    child = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert child.returncode == 10, (child.stdout, child.stderr)
    result = json.loads(child.stdout.splitlines()[-1])
    root = result["simulation_root"]
    events = []
    try:
        operator = MockCalendarPublication(root=root, run_id=result["run_id"])
        initialized = operator.initialize(mode="manual")
        assert initialized["status"] == "INITIALIZED", initialized
        first = operator.prepare(candidate_id=result["derived_id"])
        assert first["status"] == "FIRST_SIMULATION_PUBLICATION", first
        assert first["baseline"]["mode"] == "ABSENT"
        assert set(first["difference"]["changes"]) == {
            "sources",
            "trading_days",
            "trading_hours",
            "exceptions",
            "coverage",
        }
        assert all(
            rows and all(row["operation"] == "added" for row in rows)
            for rows in first["difference"]["changes"].values()
        )
        pending = operator.publish(
            proposal=first, publication_id="MOCK_pub1", version_id="MOCK_v1", emit=events.append
        )
        assert pending["status"] == "PENDING_REVIEW"
        assert not (Path(root) / "CURRENT.json").exists()
        committed = operator.publish(
            proposal=first,
            publication_id="MOCK_pub1",
            version_id="MOCK_v1",
            emit=events.append,
            reviewer="MOCK_reviewer",
            confirmed_at="2026-11-13T00:00:00.000000Z",
        )
        assert committed["status"] == "COMMITTED", committed
        retained = operator.inspect_first_difference(publication_id="MOCK_pub1")
        assert retained["status"] == "VISIBLE_UNAUTHENTICATED"
        assert retained["difference"] == first["difference"]
        evidence = operator._store.read_publication_policy("MOCK_pub1")
        assert evidence["difference"]["first_difference_sha256"] == first["difference_sha256"]
        assert committed["environment"] == committed["publication"] == "SIMULATION_ONLY"
        assert operator.verify_publication(publication_id="MOCK_pub1")["status"] == "VISIBLE"
        same = operator.prepare(candidate_id=result["derived_id"])
        assert same["status"] == "SAME", same
        no_change = operator.publish(
            proposal=same, publication_id="MOCK_noop", version_id="MOCK_noop_v", emit=events.append
        )
        assert no_change["status"] == "SAME"
        assert not (Path(root) / "publications" / "MOCK_noop.json").exists()
        assert (
            operator.inspect_publication_claim(publication_id="MOCK_noop")["claim"]["state"]
            == "NO_CHANGE"
        )

        bound = operator.binding(trading_day=date(2026, 6, 1))
        scope = {
            "venue": "SHFE",
            "kind": "future",
            "product": "mock_future",
            "contract": "mock_shfe_future_contract",
            "trading_day": date(2026, 6, 2),
        }
        before = bound.query(**scope)["query"]
        assert before.binding.calendar_ref == "MOCK_v1"
        assert (
            next(w for w in before.result.windows if w.window_id == "afternoon").end_local.hour
            == 15
        )
        changed_id = "a" * 32
        built = operator.build_candidate(
            candidate_id=changed_id, year=2026, variant="AFTERNOON_CHANGE", emit=events.append
        )
        assert built["status"] == "COMMITTED", built
        proposal = operator.prepare(candidate_id=changed_id)
        assert proposal["status"] == "CHANGED", proposal
        assert proposal["difference"]["changes"]["trading_hours"]
        waiting = operator.publish(
            proposal=proposal, publication_id="MOCK_pub2", version_id="MOCK_v2", emit=events.append
        )
        assert waiting["status"] == "PENDING_REVIEW"
        changed = operator.publish(
            proposal=proposal,
            publication_id="MOCK_pub2",
            version_id="MOCK_v2",
            emit=events.append,
            reviewer="MOCK_reviewer",
            confirmed_at="2026-11-13T00:00:00.000000Z",
        )
        assert changed["status"] == "COMMITTED", changed
        # Another cooperating publisher committed after the earlier SAME read.
        # Force precisely that stale read at the handoff before SAME's lock.
        original_prepare = operator.prepare
        with monkeypatch.context() as patch:
            patch.setattr(operator, "prepare", lambda **_: same)
            raced = operator.publish(
                proposal=same,
                publication_id="MOCK_race",
                version_id="MOCK_race_v",
                emit=events.append,
            )
        assert (
            raced["status"] == "UNKNOWN"
            and raced["reason"] == "SAME_REVALIDATION_OR_RECORD_UNKNOWN"
        )
        assert (
            operator.inspect_publication_claim(publication_id="MOCK_race")["status"]
            == "NOT_OBSERVED"
        )
        assert operator.prepare == original_prepare
        assert bound.query(**scope)["query"].binding.calendar_ref == "MOCK_v1"
        activation = bound.activate(trading_day=date(2026, 6, 2), sequence=1, event_id="MOCK_event")
        assert activation["activation"].outcome == "ACTIVATED"
        after = bound.query(**scope)["query"]
        assert after.binding.calendar_ref == "MOCK_v2"
        assert (
            next(w for w in after.result.windows if w.window_id == "afternoon").end_local.hour == 14
        )
        assert not after.result.sources_verified

        old_proposal = operator.prepare(candidate_id=result["derived_id"])
        change = operator.change_policy(
            expected_revision=proposal["policy"]["revision"],
            mode="auto",
            operation_id="MOCK_policy1",
            actor="MOCK_operator",
            at_utc="2026-11-13T00:00:00.000000Z",
            emit=events.append,
        )
        assert change["status"] == "COMMITTED", change
        new_revision = change["result"]["policy"]["revision"]
        with pytest.raises(SystemExit) as before_policy:
            operator.change_policy(
                expected_revision=new_revision,
                mode="manual",
                operation_id="MOCK_prepolicy",
                actor="MOCK_operator",
                at_utc="2026-11-13T00:00:00.000000Z",
                emit=lambda _: (_ for _ in ()).throw(SystemExit(8)),
            )
        assert before_policy.value.mock_outcome["status"] == "NOT_COMMITTED"
        assert before_policy.value.mock_outcome["operation_id"] == "MOCK_prepolicy"
        original_policy_write = runs._write_once

        def interrupt_policy(directory, chain, name, data):
            state, reason = original_policy_write(directory, chain, name, data)
            if name == "mock-policy-MOCK_interrupted.json" and state == "COMMITTED":
                signal.raise_signal(signal.SIGINT)
            return state, reason

        with monkeypatch.context() as patch:
            patch.setattr(runs, "_write_once", interrupt_policy)
            with pytest.raises(KeyboardInterrupt) as after_policy:
                operator.change_policy(
                    expected_revision=new_revision,
                    mode="manual",
                    operation_id="MOCK_interrupted",
                    actor="MOCK_operator",
                    at_utc="2026-11-13T00:00:00.000000Z",
                    emit=events.append,
                )
        assert after_policy.value.mock_outcome["status"] == "COMMIT_UNKNOWN"
        assert after_policy.value.mock_outcome["operation_id"] == "MOCK_interrupted"
        assert (
            operator.inspect_policy_claim(operation_id="MOCK_interrupted")["status"]
            == "VISIBLE_UNAUTHENTICATED"
        )
        stale = operator.publish(
            proposal=old_proposal,
            publication_id="MOCK_stale",
            version_id="MOCK_stale_v",
            emit=events.append,
            reviewer="MOCK_reviewer",
            confirmed_at="2026-11-13T00:00:00.000000Z",
        )
        assert (
            stale["status"] == "NOT_COMMITTED"
            and stale["reason"] == "STALE_OR_UNAVAILABLE_PROPOSAL"
        )
        fresh = operator.prepare(candidate_id=result["derived_id"])
        assert fresh["status"] == "CHANGED" and fresh["policy"]["mode"] == "auto"
        auto = operator.publish(
            proposal=fresh, publication_id="MOCK_pub3", version_id="MOCK_v3", emit=events.append
        )
        assert auto["status"] == "COMMITTED", auto
        assert operator.verify_publication(publication_id="MOCK_pub3")["status"] == "VISIBLE"
        assert all(
            e["data_origin"] == "MOCK" and e["environment"] == "SIMULATION_ONLY" for e in events
        )
        uncertain_proposal = operator.prepare(candidate_id=changed_id)
        assert uncertain_proposal["status"] == "CHANGED"
        # Version is reserved first, so an uncertain second claim still fences
        # both IDs through the retained version claim's cross-reference.
        first_write = runs._write_once

        def uncertain_publication_claim(directory, chain, name, data):
            state, reason = first_write(directory, chain, name, data)
            if name == "mock-publication-MOCK_pubclaim.json" and state == "COMMITTED":
                return "COMMIT_UNKNOWN", "INJECTED_AFTER_PUBLICATION_CLAIM"
            return state, reason

        with monkeypatch.context() as patch:
            patch.setattr(runs, "_write_once", uncertain_publication_claim)
            state, reason = operator._reserve(
                uncertain_proposal, "MOCK_pubclaim", "MOCK_claim_v", state="INTENT_ONLY"
            )
        assert state == "UNKNOWN" and reason == "PARTIAL_CLAIM_RESERVATION_COMMIT_UNKNOWN"
        assert (
            operator.inspect_version_claim(version_id="MOCK_claim_v")["status"]
            == "VISIBLE_UNAUTHENTICATED"
        )
        assert (
            operator._reserve(
                uncertain_proposal, "MOCK_pubclaim", "MOCK_another_v", state="INTENT_ONLY"
            )[0]
            == "NOT_COMMITTED"
        )
        assert (
            operator._reserve(
                uncertain_proposal, "MOCK_another", "MOCK_claim_v", state="INTENT_ONLY"
            )[0]
            == "NOT_COMMITTED"
        )
        claim_path = Path(root) / "observation-runs" / "mock-version-MOCK_claim_v.json"
        original_claim = claim_path.read_bytes()
        try:
            for field, value in (
                ("publication_id", "MOCK_conflict"),
                ("proposal_sha256", "0" * 64),
            ):
                body, _ = storage._unwrap(original_claim)
                body[field] = value
                claim_path.write_bytes(storage._envelope(body))
                state, reason = operator._reserve(
                    uncertain_proposal, "MOCK_unrelated", "MOCK_unrelated_v", state="INTENT_ONLY"
                )
                assert state == "UNKNOWN" and reason == "RESERVATION_INDEX_UNAVAILABLE"
        finally:
            claim_path.write_bytes(original_claim)

        def fail_second_claim(directory, chain, name, data):
            if name == "mock-publication-MOCK_partial.json":
                return "NOT_COMMITTED", "INJECTED_BEFORE_PUBLICATION_CLAIM"
            return first_write(directory, chain, name, data)

        with monkeypatch.context() as patch:
            patch.setattr(runs, "_write_once", fail_second_claim)
            partial = operator.publish(
                proposal=uncertain_proposal,
                publication_id="MOCK_partial",
                version_id="MOCK_partial_v",
                emit=events.append,
            )
        assert (
            partial["status"] == "UNKNOWN"
            and partial["reason"] == "PARTIAL_CLAIM_RESERVATION_NOT_COMMITTED"
        )
        assert (
            partial["publication_id"] == "MOCK_partial"
            and partial["version_id"] == "MOCK_partial_v"
        )
        assert (
            operator.inspect_version_claim(version_id="MOCK_partial_v")["status"]
            == "VISIBLE_UNAUTHENTICATED"
        )
        assert (
            operator.inspect_publication_claim(publication_id="MOCK_partial")["status"]
            == "NOT_OBSERVED"
        )
        reserve_data = {
            key: uncertain_proposal[key]
            for key in (
                "candidate_id",
                "candidate_payload_sha256",
                "snapshot_sha256",
                "difference_sha256",
                "baseline",
            )
        }
        script = """import json,sys
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
op=MockCalendarPublication(root=sys.argv[1],run_id=sys.argv[2])
print(json.dumps(op._reserve(json.loads(sys.argv[3]),sys.argv[4],'MOCK_compete_v',state='INTENT_ONLY')))
"""
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    root,
                    result["run_id"],
                    json.dumps(reserve_data),
                    name,
                ],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for name in ("MOCK_compete_a", "MOCK_compete_b")
        ]
        outcomes = []
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=90)
                assert process.returncode == 0, stderr
                outcomes.append(json.loads(stdout)[0])
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        assert outcomes.count("COMMITTED") == 1
        assert all(outcome in {"COMMITTED", "NOT_COMMITTED", "UNKNOWN"} for outcome in outcomes)
        assert (
            operator.inspect_version_claim(version_id="MOCK_compete_v")["status"]
            == "VISIBLE_UNAUTHENTICATED"
        )
        with pytest.raises(KeyboardInterrupt) as before:
            operator.publish(
                proposal=uncertain_proposal,
                publication_id="MOCK_preinterrupt",
                version_id="MOCK_preinterrupt_v",
                emit=lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
            )
        assert before.value.mock_outcome["status"] == "NOT_COMMITTED"
        assert before.value.mock_outcome["publication_id"] == "MOCK_preinterrupt"
        original_write = runs._write_once

        def uncertain_version(directory, chain, name, data):
            state, reason = original_write(directory, chain, name, data)
            if name == "mock-version-MOCK_reserved_v.json" and state == "COMMITTED":
                raise KeyboardInterrupt()
            return state, reason

        with monkeypatch.context() as patch:
            patch.setattr(runs, "_write_once", uncertain_version)
            with pytest.raises(KeyboardInterrupt) as interrupted:
                operator.publish(
                    proposal=uncertain_proposal,
                    publication_id="MOCK_reserved",
                    version_id="MOCK_reserved_v",
                    emit=events.append,
                )
        assert interrupted.value.mock_outcome["status"] == "COMMIT_UNKNOWN"
        assert interrupted.value.mock_outcome["publication_id"] == "MOCK_reserved"
        assert interrupted.value.mock_outcome["version_id"] == "MOCK_reserved_v"
        assert (
            operator.inspect_version_claim(version_id="MOCK_reserved_v")["status"]
            == "VISIBLE_UNAUTHENTICATED"
        )
        other = operator.publish(
            proposal=uncertain_proposal,
            publication_id="MOCK_other",
            version_id="MOCK_reserved_v",
            emit=events.append,
        )
        assert other["status"] == "NOT_COMMITTED" and other["reason"] == "ID_EXISTS"
        original = operator._store.publish
        calls = []

        def uncertain(*args, **kwargs):
            calls.append(True)
            outcome = original(*args, **kwargs)
            assert outcome.visibility == "COMMITTED"
            return replace(outcome, visibility="COMMIT_UNKNOWN")

        monkeypatch.setattr(operator._store, "publish", uncertain)
        uncertain_result = operator.publish(
            proposal=uncertain_proposal,
            publication_id="MOCK_pub4",
            version_id="MOCK_v4",
            emit=events.append,
        )
        assert uncertain_result["status"] == "COMMIT_UNKNOWN"
        assert operator.verify_publication(publication_id="MOCK_pub4")["status"] == "VISIBLE"
        again = operator.publish(
            proposal=uncertain_proposal,
            publication_id="MOCK_pub4",
            version_id="MOCK_v4",
            emit=events.append,
        )
        assert again["status"] == "NOT_COMMITTED" and calls == [True]
    finally:
        shutil.rmtree(root)
