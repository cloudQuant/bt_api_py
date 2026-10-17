from __future__ import annotations

import importlib.util
from pathlib import Path
from types import MappingProxyType


_HELPER_PATH = Path(__file__).with_name("_load01_recovery_timing.py")
_SPEC = importlib.util.spec_from_file_location("load01_recovery_timing_under_test", _HELPER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
recovery = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(recovery)


def _head(stream_id: str, epoch: str, sequence: int) -> dict[str, object]:
    return {
        "stream_id": stream_id,
        "epoch": epoch,
        "head": sequence,
        "oldest_sequence": 1,
        "latest_observed_at_utc": None,
    }


def test_market_head_accepts_mappingproxy_and_rejects_wrong_or_extra_fields() -> None:
    parsed = recovery.validate_market_head_reply(
        MappingProxyType(_head("stream-a", "epoch-a", 0)),
        expected_stream_id="stream-a",
    )
    assert parsed["sequence"] == 0
    assert parsed["epoch"] == "epoch-a"

    wrong_stream = _head("other", "epoch-a", 1)
    try:
        recovery.validate_market_head_reply(wrong_stream, expected_stream_id="stream-a")
    except ValueError as error:
        assert str(error) == "invalid_head_reply"
    else:
        raise AssertionError("wrong stream ID was accepted")

    extra_field = {**_head("stream-a", "epoch-a", 1), "secret": "must-not-pass"}
    try:
        recovery.validate_market_head_reply(extra_field, expected_stream_id="stream-a")
    except ValueError as error:
        assert str(error) == "invalid_head_reply"
    else:
        raise AssertionError("noncanonical market.head shape was accepted")


def test_slow_consumer_requires_post_capture_same_epoch_receipt_and_contiguous_delivery() -> None:
    incident = recovery.new_recovery_incident(
        kind="slow_consumer",
        incident_id="slow-1",
        fault_started_monotonic_ns=100,
        prior_cursors={"stream-a": ("epoch-a", 10)},
    )
    incident["fault_ended_monotonic_ns"] = 200
    incident["head_capture_started_monotonic_ns"] = 201
    target = recovery.attach_market_head_target(
        incident,
        stream_id="stream-a",
        reply=_head("stream-a", "epoch-a", 13),
        request_started_monotonic_ns=202,
        reply_received_monotonic_ns=212,
    )
    assert target["sequence"] == 13
    incident["head_capture_completed_monotonic_ns"] = 220

    # A buffered event delivered before its target's head timestamp cannot
    # count as recovery evidence, even if its sequence is already high enough.
    recovery.observe_market_receipt(
        incident,
        stream_id="stream-a",
        epoch="epoch-a",
        sequence=13,
        received_monotonic_ns=211,
    )
    assert incident["streams"]["stream-a"]["target_reached"] is False

    recovery.observe_market_receipt(
        incident,
        stream_id="stream-a",
        epoch="epoch-a",
        sequence=14,
        received_monotonic_ns=260,
    )
    row = incident["streams"]["stream-a"]
    assert row["target_reached"] is True
    assert row["gap_coverage_status"] == "INCOMPLETE"
    assert row["coverage_gap_event_count"] == 2

    clean = recovery.new_recovery_incident(
        kind="slow_consumer",
        incident_id="slow-clean",
        fault_started_monotonic_ns=100,
        prior_cursors={"stream-a": ("epoch-a", 10)},
    )
    clean["fault_ended_monotonic_ns"] = 200
    clean["head_capture_started_monotonic_ns"] = 201
    recovery.attach_market_head_target(
        clean,
        stream_id="stream-a",
        reply=_head("stream-a", "epoch-a", 13),
        request_started_monotonic_ns=202,
        reply_received_monotonic_ns=212,
    )
    clean["head_capture_completed_monotonic_ns"] = 220
    for sequence, received in ((11, 230), (12, 240), (13, 250)):
        recovery.observe_market_receipt(
            clean,
            stream_id="stream-a",
            epoch="epoch-a",
            sequence=sequence,
            received_monotonic_ns=received,
        )
    clean_row = clean["streams"]["stream-a"]
    assert clean_row["target_reached"] is True
    assert row["head_rpc_started_monotonic_ns"] == 202
    assert row["head_rpc_completed_monotonic_ns"] == 212
    assert row["head_rpc_elapsed_ns"] == 10
    assert row["head_target_in_retained_range"] is True
    assert clean_row["first_qualifying_receipt"]["sequence"] == 13
    assert clean_row["first_qualifying_receipt"]["elapsed_from_fault_start_ms"] == 0.00015
    assert clean_row["first_qualifying_receipt"]["elapsed_from_fault_end_ms"] == 0.00005
    assert clean_row["first_qualifying_receipt"]["elapsed_from_head_capture_complete_ms"] == 0.00003
    assert clean_row["gap_coverage_status"] == "COMPLETE_TO_TARGET"


def test_reconnect_snapshot_skip_stays_incomplete_after_target_receipt() -> None:
    incident = recovery.new_recovery_incident(
        kind="reconnect",
        incident_id="reconnect-4",
        fault_started_monotonic_ns=100,
        prior_cursors={"stream-a": ("epoch-a", 10)},
        snapshot_cursors={"stream-a": ("epoch-a", 13)},
    )
    incident["fault_ended_monotonic_ns"] = 180
    incident["head_capture_started_monotonic_ns"] = 181
    recovery.attach_market_head_target(
        incident,
        stream_id="stream-a",
        reply=_head("stream-a", "epoch-a", 15),
        request_started_monotonic_ns=182,
        reply_received_monotonic_ns=192,
    )
    incident["head_capture_completed_monotonic_ns"] = 210

    for sequence, received in ((14, 220), (15, 230)):
        recovery.observe_market_receipt(
            incident,
            stream_id="stream-a",
            epoch="epoch-a",
            sequence=sequence,
            received_monotonic_ns=received,
        )
    row = incident["streams"]["stream-a"]
    assert row["snapshot_skipped_sequence_count"] == 3
    assert row["coverage_cursor_sequence"] == 15
    assert row["target_reached"] is True
    assert row["first_qualifying_receipt"]["sequence"] == 15
    assert row["gap_coverage_status"] == "INCOMPLETE"
    assert row["gap_coverage_reason"] == "snapshot_skipped_sequences"

    summary = recovery.recovery_incident_summary(incident)
    assert summary["snapshot_skipped_sequence_count"] == 3
    assert summary["target_receipt_count"] == 1
    assert summary["gap_coverage_status"] == "INCOMPLETE"


def test_slow_consumer_sequence_jump_counts_missing_events_and_epoch_change() -> None:
    incident = recovery.new_recovery_incident(
        kind="slow_consumer",
        incident_id="slow-gap",
        fault_started_monotonic_ns=100,
        prior_cursors={"stream-a": ("epoch-a", 10)},
    )
    recovery.attach_market_head_target(
        incident,
        stream_id="stream-a",
        reply=_head("stream-a", "epoch-a", 14),
        request_started_monotonic_ns=110,
        reply_received_monotonic_ns=120,
    )
    incident["head_capture_completed_monotonic_ns"] = 125

    recovery.observe_market_receipt(
        incident,
        stream_id="stream-a",
        epoch="epoch-a",
        sequence=12,
        received_monotonic_ns=130,
    )
    row = incident["streams"]["stream-a"]
    assert row["coverage_gap_event_count"] == 1
    assert row["gap_coverage_status"] == "INCOMPLETE"
    assert row["gap_coverage_reason"] == "delivered_sequence_gap_before_target"
    assert row["target_reached"] is False

    recovery.observe_market_receipt(
        incident,
        stream_id="stream-a",
        epoch="epoch-b",
        sequence=14,
        received_monotonic_ns=140,
    )
    assert row["gap_coverage_status"] == "INCOMPLETE"
    assert row["target_reached"] is False


def test_recovery_aggregation_counts_each_profile_fault_and_missing_target_stays_incomplete() -> None:
    strategies = {}
    for strategy_index in range(100):
        local_index = strategy_index % 10
        strategies[strategy_index] = {
            "market_stream_ids": tuple(f"stream-{index:02d}" for index in range(20)),
            "reconnect_records": ([{"window": local_index + 1}] if local_index < 5 else []),
            "slow_pause": (
                {"pause_started_seconds": 30.0, "pause_ended_seconds": 45.0}
                if strategy_index == 0
                else None
            ),
            "unrecovered_gap_count": 0,
            "unrecovered_gap_events": 0,
            "market_recovery_observations": [],
        }
    summary = recovery.aggregate_recovery_observations(strategies)
    assert summary["incident_count_expected_from_observed_faults"] == 51
    assert summary["head_rpc_count_expected_from_observed_faults"] == 1020
    assert summary["head_rpc_count"] == 0
    assert summary["target_timing_status"] == "INCOMPLETE"
    assert summary["gap_coverage_status"] == "INCOMPLETE_NO_OBSERVATIONS"


def test_recovery_aggregation_marks_target_timing_only_after_actual_receipt() -> None:
    incident = recovery.new_recovery_incident(
        kind="reconnect",
        incident_id="one-reconnect",
        fault_started_monotonic_ns=100,
        prior_cursors={"stream-a": ("epoch-a", 4)},
        snapshot_cursors={"stream-a": ("epoch-a", 4)},
    )
    incident["fault_ended_monotonic_ns"] = 150
    incident["window_number"] = 1
    incident["head_capture_started_monotonic_ns"] = 151
    recovery.attach_market_head_target(
        incident,
        stream_id="stream-a",
        reply=_head("stream-a", "epoch-a", 5),
        request_started_monotonic_ns=152,
        reply_received_monotonic_ns=160,
    )
    incident["head_capture_completed_monotonic_ns"] = 161
    strategies = {
        1: {
            "profile_id": "U0",
            "market_stream_ids": ["stream-a"],
            "reconnect_records": [{"window": 1}],
            "slow_pause": None,
            "unrecovered_gap_count": 0,
            "unrecovered_gap_events": 0,
            "market_recovery_observations": [incident],
        }
    }
    before_receipt = recovery.aggregate_recovery_observations(strategies)
    assert before_receipt["target_timing_status"] == "INCOMPLETE"
    assert before_receipt["gap_coverage_status"] == "PENDING"

    recovery.observe_market_receipt(
        incident,
        stream_id="stream-a",
        epoch="epoch-a",
        sequence=5,
        received_monotonic_ns=170,
    )
    after_receipt = recovery.aggregate_recovery_observations(strategies)
    assert after_receipt["target_timing_status"] == "WITHIN_10S_FOR_ALL_TARGETS"
    assert after_receipt["gap_coverage_status"] == "COMPLETE_TO_TARGET"
    assert after_receipt["target_receipt_count"] == 1


def test_recovery_receipt_requires_complete_anchors_and_expires_after_ten_seconds() -> None:
    missing_anchor = recovery.new_recovery_incident(
        kind="slow_consumer",
        incident_id="slow-missing-anchor",
        fault_started_monotonic_ns=100,
        prior_cursors={"stream-a": ("epoch-a", 4)},
    )
    missing_anchor["head_capture_started_monotonic_ns"] = 101
    recovery.attach_market_head_target(
        missing_anchor,
        stream_id="stream-a",
        reply=_head("stream-a", "epoch-a", 5),
        request_started_monotonic_ns=102,
        reply_received_monotonic_ns=103,
    )
    missing_anchor["head_capture_completed_monotonic_ns"] = 104
    recovery.observe_market_receipt(
        missing_anchor,
        stream_id="stream-a",
        epoch="epoch-a",
        sequence=5,
        received_monotonic_ns=105,
    )
    missing = recovery.recovery_incident_summary(missing_anchor)
    assert missing["target_receipt_count"] == 1
    assert missing["target_timing_status"] == "INCOMPLETE"
    assert missing["streams"]["stream-a"]["target_timing_issue"] == (
        "MISSING_OR_INVALID_MONOTONIC_ANCHOR"
    )

    late = recovery.new_recovery_incident(
        kind="reconnect",
        incident_id="reconnect-late",
        fault_started_monotonic_ns=100,
        prior_cursors={"stream-a": ("epoch-a", 4)},
        snapshot_cursors={"stream-a": ("epoch-a", 4)},
    )
    late["fault_ended_monotonic_ns"] = 150
    late["head_capture_started_monotonic_ns"] = 151
    recovery.attach_market_head_target(
        late,
        stream_id="stream-a",
        reply=_head("stream-a", "epoch-a", 5),
        request_started_monotonic_ns=152,
        reply_received_monotonic_ns=160,
    )
    late["head_capture_completed_monotonic_ns"] = 161
    recovery.observe_market_receipt(
        late,
        stream_id="stream-a",
        epoch="epoch-a",
        sequence=5,
        received_monotonic_ns=150 + recovery.RECOVERY_CATCHUP_DEADLINE_NS + 1,
    )
    late_summary = recovery.recovery_incident_summary(late)
    assert late_summary["target_receipt_count"] == 1
    assert late_summary["target_timing_status"] == "EXCEEDED_10S"
    assert late_summary["gap_coverage_status"] == "COMPLETE_TO_TARGET"

    no_receipt = recovery.new_recovery_incident(
        kind="reconnect",
        incident_id="reconnect-no-receipt",
        fault_started_monotonic_ns=100,
        prior_cursors={"stream-a": ("epoch-a", 4)},
        snapshot_cursors={"stream-a": ("epoch-a", 4)},
    )
    no_receipt["fault_ended_monotonic_ns"] = 150
    no_receipt["head_capture_started_monotonic_ns"] = 151
    recovery.attach_market_head_target(
        no_receipt,
        stream_id="stream-a",
        reply=_head("stream-a", "epoch-a", 5),
        request_started_monotonic_ns=152,
        reply_received_monotonic_ns=160,
    )
    no_receipt["head_capture_completed_monotonic_ns"] = 161
    overdue = recovery.recovery_incident_summary(
        no_receipt,
        observed_through_monotonic_ns=150 + recovery.RECOVERY_CATCHUP_DEADLINE_NS,
    )
    assert overdue["target_receipt_count"] == 0
    assert overdue["target_timing_status"] == "EXCEEDED_10S"
    assert overdue["streams"]["stream-a"]["target_timing_issue"] == (
        "TARGET_RECEIPT_MISSING_AT_DEADLINE"
    )


def test_runner_recovery_capture_receipt_and_summary_use_authorized_runtime_path() -> None:
    class _Key:
        def __init__(self, stream_id: str) -> None:
            self.stream_id = stream_id

        def to_dict(self) -> dict[str, str]:
            return {"stream_id": self.stream_id}

    class _Runtime:
        def __init__(self) -> None:
            self.binding_calls = []
            self.calls = []

        def market_binding_view(self, mount, *, required_operations):
            self.binding_calls.append((mount, required_operations))
            return type("Binding", (), {"scope_ref": "market-scope"})()

        def call(self, mount, operation, arguments, *, scope_ref):
            self.calls.append((mount, operation, arguments, scope_ref))
            stream_id = arguments["key"]["stream_id"]
            target = 8 if stream_id == "stream-b" else 5
            return MappingProxyType(_head(stream_id, "epoch-a", target))

    class _Probe:
        def __init__(self):
            self.maintenance_reasons = []

        def _maintain_if_due(self, reason):
            self.maintenance_reasons.append(reason)

    keys = (_Key("stream-a"), _Key("stream-b"))
    incident = recovery.new_recovery_incident(
        kind="reconnect",
        incident_id="reconnect-runtime-wiring",
        fault_started_monotonic_ns=100,
        prior_cursors={
            "stream-a": ("epoch-a", 4),
            "stream-b": ("epoch-a", 7),
        },
        snapshot_cursors={
            "stream-a": ("epoch-a", 4),
            "stream-b": ("epoch-a", 7),
        },
    )
    incident["fault_ended_monotonic_ns"] = 150
    incident["window_number"] = 1
    now = iter(range(200, 240))
    runtime = _Runtime()
    runtime_probe = _Probe()

    recovery.capture_strategy_market_recovery_heads(
        runtime_probe,
        runtime,
        keys,
        incident,
        monotonic_ns=lambda: next(now),
    )

    assert runtime.binding_calls == [("store.market", frozenset({"market.head"}))]
    assert runtime.calls == [
        ("store.market", "market.head", {"key": {"stream_id": stream_id}}, "market-scope")
        for stream_id in ("stream-a", "stream-b")
    ]
    assert runtime_probe.maintenance_reasons == [
        "market-recovery-head",
        "market-recovery-head",
    ]
    assert incident["head_rpc_count"] == 2
    assert incident["head_maintenance_check_count"] == 2

    receipts: dict[str, dict[str, object]] = {}
    for stream_id in ("stream-a", "stream-b"):
        recovery.record_market_delivery_receipt(
            [incident],
            receipts,
            stream_id=stream_id,
            epoch="epoch-a",
            sequence=8 if stream_id == "stream-b" else 5,
            received_monotonic_ns=300,
        )
    strategy_results = {
        1: {
            "profile_id": "U0",
            "market_stream_ids": ("stream-a", "stream-b"),
            "reconnect_records": ({"window": 1},),
            "slow_pause": None,
            "unrecovered_gap_count": 0,
            "unrecovered_gap_events": 0,
            "market_recovery_observations": [incident],
        }
    }
    summary = recovery.aggregate_recovery_observations(
        strategy_results, observed_through_monotonic_ns=301
    )
    assert summary["target_timing_status"] == "WITHIN_10S_FOR_ALL_TARGETS"
    assert summary["target_stream_count"] == 2
    assert summary["target_receipt_count"] == 2
    assert summary["gap_coverage_status"] == "COMPLETE_TO_TARGET"
    assert summary["incidents"][0]["strategy_index"] == 1
    assert summary["incidents"][0]["profile_id"] == "U0"
    assert summary["incidents"][0]["window_number"] == 1
    assert summary["incidents"][0]["head_capture_budget_status"] == "WITHIN_10S"
    assert summary["incidents"][0]["head_capture_timeout_status"] == "NO_TIMEOUT"
    assert summary["profile_counts"]["U0"]["by_kind"]["reconnect"] == {
        "incident_count": 1,
        "incident_count_expected_from_observed_faults": 1,
        "target_stream_count": 2,
        "target_stream_count_expected": 2,
        "target_receipt_count": 2,
        "target_timing_within_10s_count": 2,
        "target_timing_exceeded_10s_count": 0,
        "target_timing_status": "WITHIN_10S_FOR_ALL_TARGETS",
    }
    assert summary["unrecovered_gap_count"] == 0
    assert summary["unrecovered_gap_event_count"] == 0
    assert set(receipts) == {"stream-a", "stream-b"}


def test_capture_budget_exhaustion_stops_remaining_market_head_calls() -> None:
    class _Key:
        def __init__(self, stream_id: str) -> None:
            self.stream_id = stream_id

        def to_dict(self) -> dict[str, str]:
            return {"stream_id": self.stream_id}

    class _Runtime:
        calls = []

        def market_binding_view(self, _mount, *, required_operations):
            return type("Binding", (), {"scope_ref": "market-scope"})()

        def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("the expired capture budget must prevent this RPC")

    keys = (_Key("stream-a"), _Key("stream-b"))
    incident = recovery.new_recovery_incident(
        kind="slow_consumer",
        incident_id="slow-budget-exhausted",
        fault_started_monotonic_ns=100,
        prior_cursors={
            "stream-a": ("epoch-a", 4),
            "stream-b": ("epoch-a", 7),
        },
    )
    incident["fault_ended_monotonic_ns"] = 150
    now_value = [200]
    runtime = _Runtime()

    def advance_to_budget(reason: str) -> None:
        assert reason == "market-recovery-head"
        now_value[0] = 150 + recovery.RECOVERY_CATCHUP_DEADLINE_NS

    recovery.capture_market_recovery_heads(
        runtime,
        keys,
        incident,
        maintenance_if_due=advance_to_budget,
        monotonic_ns=lambda: now_value[0],
    )

    assert runtime.calls == []
    assert incident["head_capture_error_code"] == "CAPTURE_BUDGET_EXHAUSTED"
    assert incident["head_rpc_count"] == 0
    assert all(
        row["head_error_code"] == "CAPTURE_BUDGET_EXHAUSTED"
        for row in incident["streams"].values()
    )
    summary = recovery.recovery_incident_summary(
        incident,
        observed_through_monotonic_ns=now_value[0] + 1,
    )
    assert summary["target_timing_status"] == "INCOMPLETE"
    assert summary["target_stream_count"] == 0


def test_request_start_at_capture_deadline_does_not_dispatch_market_head() -> None:
    class _Key:
        def __init__(self, stream_id: str) -> None:
            self.stream_id = stream_id

        def to_dict(self) -> dict[str, str]:
            return {"stream_id": self.stream_id}

    class _Runtime:
        calls = []

        def market_binding_view(self, _mount, *, required_operations):
            return type("Binding", (), {"scope_ref": "market-scope"})()

        def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("market.head must not start at the capture deadline")

    fault_ended_ns = 100
    deadline_ns = fault_ended_ns + recovery.RECOVERY_CATCHUP_DEADLINE_NS
    incident = recovery.new_recovery_incident(
        kind="slow_consumer",
        incident_id="request-start-at-deadline",
        fault_started_monotonic_ns=90,
        prior_cursors={
            "stream-a": ("epoch-a", 4),
            "stream-b": ("epoch-a", 7),
        },
    )
    incident["fault_ended_monotonic_ns"] = fault_ended_ns
    clock_values = iter(
        [
            95,  # capture start
            deadline_ns - 5,  # before binding/initial budget check
            deadline_ns - 4,  # before first target
            deadline_ns - 3,  # maintenance start
            deadline_ns - 1,  # maintenance completion remains within budget
            deadline_ns,  # request start crosses the boundary
            deadline_ns + 1,  # capture completion
        ]
    )
    runtime = _Runtime()
    recovery.capture_market_recovery_heads(
        runtime,
        (_Key("stream-a"), _Key("stream-b")),
        incident,
        maintenance_if_due=lambda reason: None,
        monotonic_ns=lambda: next(clock_values),
    )

    assert runtime.calls == []
    assert incident["head_capture_error_code"] == "CAPTURE_BUDGET_EXHAUSTED"
    assert incident["head_rpc_count"] == 0
    assert incident["head_maintenance_check_count"] == 1
    assert incident["head_capture_completed_monotonic_ns"] == deadline_ns + 1
    assert all(
        row["head_error_code"] == "CAPTURE_BUDGET_EXHAUSTED"
        for row in incident["streams"].values()
    )


def test_failed_head_capture_and_missing_reconnect_snapshot_remain_incomplete() -> None:
    incident = recovery.new_recovery_incident(
        kind="reconnect",
        incident_id="partial-head-capture",
        fault_started_monotonic_ns=100,
        prior_cursors={
            "stream-a": ("epoch-a", 4),
            "stream-b": ("epoch-b", 7),
        },
        snapshot_cursors={
            "stream-a": ("epoch-a", 4),
            "stream-b": None,
        },
    )
    incident["fault_ended_monotonic_ns"] = 150
    incident["head_capture_started_monotonic_ns"] = 151
    recovery.mark_market_head_failure(
        incident,
        stream_id="stream-a",
        safe_error_code="HEAD_CAPTURE_FAILED",
        elapsed_ns=20,
        request_started_monotonic_ns=152,
        reply_completed_monotonic_ns=172,
        attempted=True,
    )
    recovery.mark_market_head_failure(
        incident,
        stream_id="stream-b",
        safe_error_code="CAPTURE_ABORTED",
        attempted=False,
    )
    incident["head_capture_error_code"] = "HEAD_CAPTURE_FAILED"
    incident["head_capture_completed_monotonic_ns"] = 173
    summary = recovery.aggregate_recovery_observations(
        {
            1: {
                "profile_id": "U0",
                "market_stream_ids": ["stream-a", "stream-b"],
                "reconnect_records": [{"window": 1}],
                "slow_pause": None,
                "unrecovered_gap_count": 0,
                "unrecovered_gap_events": 0,
                "market_recovery_observations": [incident],
            }
        }
    )
    rows = summary["incidents"][0]["streams"]
    assert summary["snapshot_skipped_sequence_count"] is None
    assert summary["snapshot_skipped_sequence_unknown_stream_count"] == 1
    assert summary["incidents"][0]["snapshot_skipped_sequence_count"] is None
    assert summary["head_rpc_count"] == 1
    assert summary["head_rpc_error_count"] == 1
    assert summary["target_timing_status"] == "INCOMPLETE"
    assert summary["gap_coverage_status"] == "INCOMPLETE"
    assert rows["stream-a"]["head_rpc_elapsed_ns"] == 20
    assert rows["stream-b"]["head_error_code"] == "CAPTURE_ABORTED"
    assert rows["stream-b"]["snapshot_skipped_sequence_count"] is None
    assert rows["stream-b"]["gap_coverage_reason"] == "missing_snapshot_cursor"
