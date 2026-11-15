"""Fault injection for partial claims; no exchange or account operations."""

import pytest

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mock as mock
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_scope_index as scope_index
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from bt_api_py.trading_calendar_store import PublicationResult, TradingCalendarStore


def operator_with_prepared_candidate(tmp_path, kind="CHANGED"):
    operator = object.__new__(MockCalendarPublication)
    operator.root = str(tmp_path.resolve())
    operator.run_id = "a" * 32
    operator._guard = lambda: None
    descriptors, _ = runs._open_runs(operator.root, create=True)
    runs._close(descriptors)
    operator._store = TradingCalendarStore(operator.root)
    with operator._store._writer_lock():
        pass
    proposal = {
        "status": kind,
        "candidate_id": "b" * 32,
        "candidate_payload_sha256": "a" * 64,
        "snapshot_sha256": "b" * 64,
        "difference_sha256": "c" * 64,
        "difference": {},
        "baseline": {"mode": "ABSENT", "publication_id": None, "publication_sha256": None},
        "policy": {"mode": "auto", "revision": "rev", "directory_id": "directory"},
        "data_origin": "MOCK",
    }
    composite = {
        "snapshot": {},
        "manifest": {},
        "manifest_sha256": "d" * 64,
        "binding": {},
        "binding_sha256": "e" * 64,
    }
    operator.prepare = lambda **_: proposal
    operator._candidate = lambda _: (
        {
            "payload_sha256": proposal["candidate_payload_sha256"],
            "bundle": {
                "input": {
                    "metadata": mock.LABEL,
                    "shards": [{"layers": {"sources": [mock.SOURCE]}}],
                }
            },
        },
        composite,
    )
    return operator, proposal


def test_first_difference_failure_retains_original_ids(tmp_path):
    operator, proposal = operator_with_prepared_candidate(tmp_path, "FIRST_SIMULATION_PUBLICATION")
    operator._retain_first_difference = lambda *_: "NOT_COMMITTED"
    outcome = operator.publish(
        proposal=proposal, publication_id="MOCK_pub", version_id="MOCK_ver", emit=lambda _: None
    )
    assert outcome["status"] == "UNKNOWN"
    assert outcome["reason"] == "PARTIAL_RESERVATION_FIRST_DIFFERENCE_NOT_COMMITTED"
    assert outcome["reservation_state"] == "RESERVED"
    assert outcome["publication_visibility"] == "NOT_ATTEMPTED"
    assert outcome["publication_id"] == "MOCK_pub" and outcome["version_id"] == "MOCK_ver"
    assert (
        operator.inspect_publication_claim(publication_id="MOCK_pub")["status"]
        == "VISIBLE_UNAUTHENTICATED"
    )
    assert (
        operator.inspect_version_claim(version_id="MOCK_ver")["status"] == "VISIBLE_UNAUTHENTICATED"
    )


@pytest.mark.parametrize("visibility", ["NOT_COMMITTED", "COMMIT_UNKNOWN", "EXCEPTION"])
def test_store_rejection_retains_reservation_and_original_visibility(tmp_path, visibility, monkeypatch):
    operator, proposal = operator_with_prepared_candidate(tmp_path)

    def publish(*_, **__):
        if visibility == "EXCEPTION":
            raise OSError("injected store failure")
        return PublicationResult(visibility, "UNCONFIRMED", "MOCK_pub", "MOCK_ver")

    monkeypatch.setattr(operator._store, "publish", publish)
    outcome = operator.publish(
        proposal=proposal, publication_id="MOCK_pub", version_id="MOCK_ver", emit=lambda _: None
    )
    assert outcome["status"] == ("UNKNOWN" if visibility == "NOT_COMMITTED" else "COMMIT_UNKNOWN")
    if visibility == "EXCEPTION":
        assert outcome["reservation_state"] == "PARTIAL_OR_UNKNOWN"
    else:
        assert outcome["publication_visibility"] == visibility
        assert outcome["reservation_state"] == "RESERVED"
    assert (
        operator.inspect_version_claim(version_id="MOCK_ver")["status"] == "VISIBLE_UNAUTHENTICATED"
    )


def test_same_rejects_preexisting_store_id_before_reservation(tmp_path):
    operator, proposal = operator_with_prepared_candidate(tmp_path, "SAME")
    (tmp_path / "versions" / "MOCK_ver.json").write_text("existing", encoding="utf-8")
    result = operator.publish(
        proposal=proposal, publication_id="MOCK_pub", version_id="MOCK_ver", emit=lambda _: None
    )
    assert result["status"] == "NOT_COMMITTED" and result["reason"] == "EXISTING_STORE_ID"
    assert not (tmp_path / "observation-runs" / "mock-version-MOCK_ver.json").exists()


def test_pending_scope_migration_blocks_publication_before_reservation(tmp_path):
    operator, proposal = operator_with_prepared_candidate(tmp_path)
    migration_id = "c" * 32
    pending = {
        "schema": "bt_api.calendar-scope-migration-intent/1",
        "environment": "SIMULATION_ONLY",
        "source_authentication": "NOT_PROVEN",
        "migration_id": migration_id,
        "run_id": operator.run_id,
        "declaration": scope_index._DECLARATION,
        "legacy_ids": [],
        "legacy_head": None,
        "policy": None,
        "baseline": None,
    }
    pending_path = tmp_path / "observation-runs" / "scope-check-migration-pending.json"
    pending_path.write_bytes(storage._envelope(pending))
    pending_path.chmod(0o600)
    events = []
    outcome = operator.publish(
        proposal=proposal, publication_id="MOCK_pub", version_id="MOCK_ver", emit=events.append
    )
    assert outcome["status"] == "UNKNOWN"
    assert outcome["reason"] == "SCOPE_MIGRATION_RECONCILE_ORIGINAL_ID"
    assert outcome["migration_id"] == migration_id
    assert outcome["publication"] == "NOT_ATTEMPTED"
    assert not events
    assert not (tmp_path / "observation-runs" / "mock-version-MOCK_ver.json").exists()
    assert pending_path.read_bytes() == storage._envelope(pending)
