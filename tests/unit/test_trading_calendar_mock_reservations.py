"""Actual POSIX reservation files under controlled interruption/corruption."""

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication


def fixture_operator(tmp_path):
    operator = object.__new__(MockCalendarPublication)
    operator.root = str(tmp_path.resolve())
    descriptors, _ = runs._open_runs(operator.root, create=True)
    runs._close(descriptors)
    proposal = {
        "candidate_id": "a" * 32,
        "candidate_payload_sha256": "a" * 64,
        "snapshot_sha256": "b" * 64,
        "difference_sha256": "c" * 64,
        "baseline": {"mode": "ABSENT", "publication_id": None, "publication_sha256": None},
    }
    return operator, proposal


def test_partial_second_claim_never_reports_not_committed(tmp_path, monkeypatch):
    operator, proposal = fixture_operator(tmp_path)
    original = runs._write_once

    def fail_second(directory, chain, name, data):
        if name == "mock-publication-MOCK_pub.json":
            return "NOT_COMMITTED", "INJECTED_IO_ERROR"
        return original(directory, chain, name, data)

    monkeypatch.setattr(runs, "_write_once", fail_second)
    state, reason = operator._reserve(proposal, "MOCK_pub", "MOCK_ver", state="INTENT_ONLY")
    assert (state, reason) == ("UNKNOWN", "PARTIAL_CLAIM_RESERVATION_NOT_COMMITTED")
    assert (tmp_path / "observation-runs" / "mock-version-MOCK_ver.json").exists()
    assert not (tmp_path / "observation-runs" / "mock-publication-MOCK_pub.json").exists()
    assert (
        operator._reserve(proposal, "MOCK_other", "MOCK_ver", state="INTENT_ONLY")[0]
        == "NOT_COMMITTED"
    )
    assert (
        operator._reserve(proposal, "MOCK_pub", "MOCK_other_ver", state="INTENT_ONLY")[0]
        == "NOT_COMMITTED"
    )


def test_conflicting_cross_claims_block_unrelated_reservations(tmp_path):
    operator, proposal = fixture_operator(tmp_path)
    assert (
        operator._reserve(proposal, "MOCK_pub", "MOCK_ver", state="INTENT_ONLY")[0] == "COMMITTED"
    )
    path = tmp_path / "observation-runs" / "mock-version-MOCK_ver.json"
    raw = path.read_bytes()
    for field, value in (("publication_id", "MOCK_conflict"), ("proposal_sha256", "0" * 64)):
        body, _ = storage._unwrap(raw)
        body[field] = value
        path.write_bytes(storage._envelope(body))
        assert operator._reserve(proposal, "MOCK_other", "MOCK_other_ver", state="INTENT_ONLY") == (
            "UNKNOWN",
            "RESERVATION_INDEX_UNAVAILABLE",
        )
    path.write_bytes(raw)
    assert (
        operator._reserve(proposal, "MOCK_other", "MOCK_other_ver", state="INTENT_ONLY")[0]
        == "COMMITTED"
    )
