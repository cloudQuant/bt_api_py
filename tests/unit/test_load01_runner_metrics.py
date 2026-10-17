from __future__ import annotations

import hashlib
from pathlib import Path
from types import MappingProxyType

from tests.unit._load01_metrics import OperationLatencyCollector
from tests.unit._load01_profiles import load01_profile
from tests.unit.test_load01_hundred_strategy_30min import (
    _load01_cpu_schedule_summary,
    _load01_expected_order_operation_counts,
    _load01_market_e2e_latency_ms,
    _load01_order_rpc_outcome,
    _load01_performance_measurement_report,
    _load01_read_threshold_registration,
    _load01_timed_order_call,
    _ACCOUNT_REFS,
)
from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError
from bt_api_transport_zmq.service_v2 import RequestNotSentError, RequestOutcomeUnknownError


def test_exact_transport_causes_are_the_only_order_timeouts() -> None:
    assert _load01_order_rpc_outcome(RequestNotSentError("m1", "send_timeout")) == "timeout"
    assert _load01_order_rpc_outcome(RequestOutcomeUnknownError("m2", "response_timeout")) == "timeout"
    assert _load01_order_rpc_outcome(RequestNotSentError("m3", "send_not_queued")) == "error"
    assert _load01_order_rpc_outcome(RequestOutcomeUnknownError("m4", "reply_validation_failed")) == "error"
    assert _load01_order_rpc_outcome(TimeoutError("untyped")) == "error"

    runtime_error = ServiceRuntimeRequestNotSentError()
    runtime_error.__context__ = RequestNotSentError("m5", "send_timeout")
    assert _load01_order_rpc_outcome(runtime_error) == "timeout"
    non_timeout_runtime_error = ServiceRuntimeRequestNotSentError()
    non_timeout_runtime_error.__context__ = RequestNotSentError("m6", "send_not_queued")
    assert _load01_order_rpc_outcome(non_timeout_runtime_error) == "error"


def test_timed_rpc_preserves_response_and_records_non_ok_as_error() -> None:
    class _Runtime:
        def __init__(self, reply):
            self.reply = reply
            self.calls = []

        def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return self.reply

    runtime = _Runtime({"ok": True, "code": "OK", "result": {"id": "keep"}})
    collector = OperationLatencyCollector(required_operations=("orders.submit",))
    reply = _load01_timed_order_call(
        collector,
        runtime,
        "broker.orders",
        "orders.submit",
        {"intent_id": "safe-id"},
        account_ref="account-01",
        scope_ref="scope-01",
    )
    assert reply is runtime.reply
    assert runtime.calls == [
        (("broker.orders", "orders.submit", {"intent_id": "safe-id"}),
         {"account_ref": "account-01", "scope_ref": "scope-01"})
    ]
    assert collector.summary()["operations"]["orders.submit"]["success_count"] == 1

    failed_runtime = _Runtime({"code": "REMOTE_REQUEST_REJECTED"})
    failed_collector = OperationLatencyCollector(required_operations=("orders.submit",))
    _load01_timed_order_call(
        failed_collector,
        failed_runtime,
        "broker.orders",
        "orders.submit",
        {"intent_id": "safe-id"},
    )
    failed = failed_collector.summary()["operations"]["orders.submit"]
    assert failed["error_count"] == 1
    assert failed["timeout_count"] == 0

    frozen_runtime = _Runtime(MappingProxyType({"ok": True, "code": "OK"}))
    frozen_collector = OperationLatencyCollector(required_operations=("orders.submit",))
    frozen_reply = _load01_timed_order_call(
        frozen_collector,
        frozen_runtime,
        "broker.orders",
        "orders.submit",
        {"intent_id": "safe-id"},
    )
    assert frozen_reply is frozen_runtime.reply
    assert frozen_collector.summary()["operations"]["orders.submit"]["success_count"] == 1

    missing_ok_runtime = _Runtime({"code": "OK", "result": {"id": "ambiguous"}})
    missing_ok_collector = OperationLatencyCollector(required_operations=("orders.submit",))
    _load01_timed_order_call(
        missing_ok_collector,
        missing_ok_runtime,
        "broker.orders",
        "orders.submit",
        {"intent_id": "safe-id"},
    )
    missing_ok = missing_ok_collector.summary()["operations"]["orders.submit"]
    assert missing_ok["success_count"] == 0
    assert missing_ok["error_count"] == 1


def test_timed_rpc_records_exception_then_reraises_same_object() -> None:
    failure = RequestOutcomeUnknownError("m7", "response_timeout")

    class _Runtime:
        def call(self, *args, **kwargs):
            raise failure

    collector = OperationLatencyCollector(required_operations=("orders.query",))
    try:
        _load01_timed_order_call(
            collector,
            _Runtime(),
            "broker.order_query",
            "orders.query",
            {"intent_id": "safe-id"},
        )
    except RequestOutcomeUnknownError as caught:
        assert caught is failure
    else:
        raise AssertionError("expected the original transport exception")
    summary = collector.summary()["operations"]["orders.query"]
    assert summary["timeout_count"] == 1
    assert summary["error_count"] == 1
    assert summary["observed_count"] == 1


def test_profile_schedule_derives_expected_per_strategy_rpc_counts() -> None:
    u0 = load01_profile("U0")
    assert _load01_expected_order_operation_counts(u0, 0, 1800) == {
        "orders.submit": 360,
        "orders.query": 0,
        "orders.cancel": 360,
    }
    n4 = load01_profile("N4")
    assert _load01_expected_order_operation_counts(n4, 0, 1800) == {
        "orders.submit": 720,
        "orders.query": 1439,
        "orders.cancel": 719,
    }
    n3 = load01_profile("N3")
    assert _load01_expected_order_operation_counts(n3, 0, 120) == {
        "orders.submit": 24,
        "orders.query": 0,
        "orders.cancel": 24,
    }


def test_cpu_schedule_requires_unique_contiguous_real_samples() -> None:
    baseline = {
        "load01_cpu_sample_tick": 0,
        "load01_cpu_steady_load_sample": False,
        "sample_interval_seconds": 0.1,
        "elapsed_seconds": 0.1,
        "system_cpu_percent": 20.0,
    }
    rows = [
        {
            "load01_cpu_sample_tick": tick,
            "load01_cpu_steady_load_sample": True,
            "sample_interval_seconds": 5.0,
            "elapsed_seconds": tick * 5.0,
            "system_cpu_percent": 40.0 + tick,
        }
        for tick in (1, 2)
    ]
    complete = _load01_cpu_schedule_summary([baseline, *rows], planned_duration_seconds=10)
    assert complete["status"] == "COMPLETE"
    assert complete["sample_count"] == 2
    assert complete["observed_tick_ids"] == [1, 2]
    assert complete["sample_scope"] == "steady_load_only; baseline and shutdown excluded"

    missing = _load01_cpu_schedule_summary([baseline, rows[1]], planned_duration_seconds=10)
    assert missing["status"] == "INCOMPLETE_SAMPLE_SCHEDULE"
    assert missing["acceptance_status"] == "INCOMPLETE"
    duplicate = _load01_cpu_schedule_summary([baseline, rows[0], rows[0]], planned_duration_seconds=10)
    assert duplicate["status"] == "INCOMPLETE_SAMPLE_SCHEDULE"


def test_cpu_summary_does_not_treat_baseline_as_observed_population() -> None:
    summary = _load01_cpu_schedule_summary(
        [{
            "load01_cpu_sample_tick": 0,
            "load01_cpu_steady_load_sample": False,
            "sample_interval_seconds": 0.1,
            "elapsed_seconds": 0.1,
            "system_cpu_percent": 55.0,
        }],
        planned_duration_seconds=10,
    )
    assert summary["sample_count"] == 0
    assert summary["mean_percent"] is None
    assert summary["status"] == "INCOMPLETE_SAMPLE_SCHEDULE"


def test_market_latency_rejects_missing_or_future_publish_time() -> None:
    assert _load01_market_e2e_latency_ms(1_000_000, 3_500_000) == 2.5
    assert _load01_market_e2e_latency_ms(None, 3_500_000) is None
    assert _load01_market_e2e_latency_ms(4_000_000, 3_500_000) is None


def _full_u0_measurement_fixture(
    *,
    slow_strategy: int | None = None,
    missing_cpu_tick: int | None = None,
    market_latency_ms: float = 10.0,
    order_latency_ns: int = 1_000_000,
):
    profile = load01_profile("U0")
    duration = 1800
    root = Path(__file__).resolve().parents[2]
    relative_registration = (
        "docs/evidence/iteration10-load01-engineering-registration-20261018/"
        "threshold-registration.json"
    )
    registration_path = root / relative_registration
    registration_digest = hashlib.sha256(registration_path.read_bytes()).hexdigest()
    registration, registration_pin = _load01_read_threshold_registration(
        root, {relative_registration: registration_digest}
    )
    stream_ids = tuple(f"synthetic-stream-{index:02d}" for index in range(20))
    source_sequences = {
        stream_id: profile.topic_messages_per_second[index] * duration
        for index, stream_id in enumerate(stream_ids)
    }
    strategy_results = {}
    for strategy_index in range(100):
        collector = OperationLatencyCollector(
            sample_capacity=2048,
            required_operations=("orders.submit", "orders.cancel"),
        )
        expected = _load01_expected_order_operation_counts(profile, strategy_index, duration)
        latency = 6_000_000_000 if strategy_index == slow_strategy else order_latency_ns
        for operation, count in expected.items():
            for _ in range(count):
                collector.record(operation, latency, "success")
        market_by_stream = {
            stream_ids[topic_index]: profile.topic_messages_per_second[topic_index] * duration
            for topic_index in profile.subscribed_topic_indices(strategy_index)
        }
        market_events = sum(market_by_stream.values())
        due = market_events // 128
        account_index = profile.account_for_strategy(strategy_index)
        strategy_results[strategy_index] = {
            "order_rpc_metrics": collector.summary(),
            "market_events": market_events,
            "market_by_stream": market_by_stream,
            "market_e2e_due_count": due,
            "market_e2e_valid_count": due,
            "market_e2e_samples_ms": [market_latency_ms] * min(due, 2048),
            "order_cycles_by_account": {
                _ACCOUNT_REFS[account_index]: expected["orders.submit"]
            },
        }
    resource_samples = [
        {
            "load01_cpu_sample_tick": tick,
            "load01_cpu_steady_load_sample": True,
            "sample_interval_seconds": 5.0,
            "elapsed_seconds": tick * 5.0,
            "system_cpu_percent": 50.0,
        }
        for tick in range(1, duration // 5 + 1)
        if tick != missing_cpu_tick
    ]
    return _load01_performance_measurement_report(
        profile=profile,
        mode="full",
        strategy_results=strategy_results,
        resource_samples=resource_samples,
        planned_duration_seconds=duration,
        actual_duration_seconds=float(duration),
        topic_stream_ids_by_index=stream_ids,
        source_sequences_by_stream=source_sequences,
        registration=registration,
        registration_pin=registration_pin,
    )


def test_full_u0_report_checks_every_strategy_without_pooling_tail_latency() -> None:
    normal = _full_u0_measurement_fixture()
    assert normal["p0_evaluation_eligible"] is True
    assert normal["evaluation_status"] == "TARGETS_MET"
    assert normal["violations"] == []
    assert len(normal["input_messages_by_topic"]) == 20
    assert len(normal["delivered_messages_by_strategy_and_topic"]) == 100
    assert len(normal["order_cycles_by_account"]) == 10

    outlier = _full_u0_measurement_fixture(slow_strategy=7)
    assert outlier["evaluation_status"] == "TARGETS_FAILED_OR_INCOMPLETE"
    assert "strategy-007:orders.submit:latency" in outlier["violations"]
    assert outlier["order_rpc_by_strategy"][7]["orders.submit"]["status"] == "TARGETS_FAILED"
    assert outlier["order_rpc_aggregate_counts"]["orders.submit"]["success_count"] == 36_000


def test_market_percentiles_are_reported_without_latency_gate() -> None:
    report = _full_u0_measurement_fixture(
        market_latency_ms=2501.0,
        order_latency_ns=2_501_000_000,
    )

    assert report["p0_evaluation_eligible"] is True
    assert report["evaluation_status"] == "TARGETS_MET"
    assert report["violations"] == []
    assert len(report["market_end_to_end_by_strategy"]) == 100
    for summary in report["market_end_to_end_by_strategy"].values():
        assert summary["status"] == "OBSERVED"
        assert summary["acceptance_scope"] == "OBSERVATION_ONLY_NO_LOW_LATENCY_GATE"
        assert summary["observational_percentiles"] == ["p50", "p95", "p99"]
        assert summary["percentiles_ms"] == {"p50": 2501.0, "p95": 2501.0, "p99": 2501.0}
    order_summary = report["order_rpc_by_strategy"][0]["orders.submit"]
    assert order_summary["status"] == "TARGETS_MET"
    assert order_summary["percentiles_ms_nearest_rank"] == {
        "p50": 2501.0,
        "p95": 2501.0,
        "p99": 2501.0,
    }
    assert order_summary["observational_percentiles"] == ["p50", "p95"]
    assert order_summary["percentile_targets_ms"] == {"p99": 5000}


def test_full_u0_report_does_not_pass_with_missing_cpu_coverage() -> None:
    incomplete = _full_u0_measurement_fixture(missing_cpu_tick=180)
    assert incomplete["system_cpu"]["status"] == "INCOMPLETE_SAMPLE_SCHEDULE"
    assert incomplete["evaluation_status"] == "TARGETS_FAILED_OR_INCOMPLETE"
    assert "system_cpu:incomplete_schedule" in incomplete["violations"]
