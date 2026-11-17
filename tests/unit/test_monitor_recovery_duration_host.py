"""Synthetic standalone host recovery consumer composition and ownership."""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_monitor.recovery_duration import RecoveryDurationConsumer

from bt_api_py.monitor_service_config import MonitorServiceConfigError, parse_monitor_service_config
from bt_api_py.monitor_service_host import MonitorServiceError, MonitorServiceHost
from tests.integration.test_monitor_service_process import client
from tests.unit.test_monitor_service_v2_config import v2_document

TYPE = "gateway.recovery.journal.v1"
KEY_A = MarketSubscriptionKey(
    "source", "grant", MarketInstrumentKey("ctp", "venue", "demo", "future", "id", "symbol"),
    "trade",
)
KEY_B = MarketSubscriptionKey(
    "source", "grant", MarketInstrumentKey("ctp", "venue", "demo", "future", "id2", "symbol2"),
    "trade",
)


def setup_config(tmp_path):
    path, doc = v2_document(tmp_path)
    doc["storage"]["event_types"] = [TYPE]
    doc["producer_bindings"][0].update(binding_ref="binding", source_ref="source", event_types=[TYPE])
    doc["recovery_duration"] = [
        {"consumer_id": consumer, "binding_ref": "binding", "source_ref": "source",
         "scope_ref": "scope", "config_version": "v1", "member_key": key.to_dict()}
        for consumer, key in (("consumer-a", KEY_A), ("consumer-b", KEY_B))
    ]
    return path, doc, parse_monitor_service_config(doc, path=path)


def wait_for(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition timed out")


def call_with_host(host, function):
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(function)
        deadline = time.monotonic() + 15
        while not future.done() and time.monotonic() < deadline:
            host.serve_once(timeout_ms=5)
        return future.result(timeout=1)


def keypair(tmp_path, name):
    return ((tmp_path / f"{name}.public").read_bytes(), (tmp_path / f"{name}.secret").read_bytes())


def test_real_ingress_separate_pending_and_restart(tmp_path, monkeypatch):
    path, doc, config = setup_config(tmp_path)
    doc["storage"]["event_types"].append("synthetic.observation")
    doc["ingress"]["principals"].append(deepcopy(doc["heartbeat"]["principals"][0]))
    extra = deepcopy(doc["producer_bindings"][0])
    extra.update(binding_ref="other", source_ref="other", event_types=["synthetic.observation"],
                 identity={"principal_ref": "gateway", "actor_ref": "gateway-service"})
    doc["producer_bindings"].append(extra)
    config = parse_monitor_service_config(doc, path=path)
    constructed = []
    attempts = {}
    original_init = RecoveryDurationConsumer.__init__
    original_process = RecoveryDurationConsumer.process_next

    def capture(self, outbox, binding, *, scope_bindings):
        constructed.append(scope_bindings)
        original_init(self, outbox, binding, scope_bindings=scope_bindings)

    def counted(self, facts=()):
        attempts[self._consumer] = attempts.get(self._consumer, 0) + 1
        return original_process(self, facts)

    monkeypatch.setattr(RecoveryDurationConsumer, "__init__", capture)
    monkeypatch.setattr(RecoveryDurationConsumer, "process_next", counted)
    # Fixtures produce legitimate synthetic journal codec payloads.
    monitor_tests = Path(__file__).parents[2] / "bt_api/bt_api_monitor/tests"
    monkeypatch.syspath_prepend(str(monitor_tests))
    from test_recovery_duration_consumer import epoch_marker, raw_event

    host = MonitorServiceHost(config)
    host.start()
    try:
        def send():
            producer = client(doc["ingress"], keypair(tmp_path, "events")[0],
                              keypair(tmp_path, "producer"), "producer", "events.append")
            producer.start()
            try:
                for cursor, raw in enumerate((epoch_marker(), raw_event(2, reason="BEGIN"), raw_event(3)), 1):
                    event = {"schema_version": 2, "event_id": f"event-{cursor}", "event_type": TYPE,
                             "occurred_at": 1800000000.0, "binding_ref": "binding", "source_ref": "source",
                             "data": {"schema": "sdk.gateway_recovery_relay/v1",
                                      "envelope_time_basis": "FIRST_RELAY_ENQUEUE", "event": raw}}
                    producer.call("upstream.telemetry", "events.append", event)
            finally:
                producer.close()
        call_with_host(host, send)
        wait_for(lambda: host.recovery_duration_snapshot("consumer-a")["status"] == "PENDING_FACTS")
        wait_for(lambda: host.recovery_duration_snapshot("consumer-b")["historical_snapshot"]["checkpoint"] == 3)
        a = host.recovery_duration_snapshot("consumer-a")
        b = host.recovery_duration_snapshot("consumer-b")
        assert a["historical_snapshot"]["checkpoint"] == 2
        assert a["historical_snapshot"]["pending_reason"] == "PENDING_MAPPING_OR_CALENDAR"
        assert a["historical_snapshot"]["business_eligibility"] == "UNKNOWN"
        before = attempts["consumer-a"]
        time.sleep(0.2)
        assert attempts["consumer-a"] == before
        assert b["historical_snapshot"]["checkpoint"] == 3
        assert b["historical_snapshot"]["episodes"][0]["state"] == "NOT_APPLICABLE"
        assert len(host._recovery_worker._producer_bindings) == len(config.producer_bindings)
        assert len(constructed) == 2 and all(len(bindings) == 2 for bindings in constructed)
    finally:
        host.close()
    reopened = MonitorServiceHost(config)
    reopened.start()
    try:
        wait_for(lambda: reopened.recovery_duration_snapshot("consumer-a")["status"] == "PENDING_FACTS")
        assert reopened.recovery_duration_snapshot("consumer-a")["historical_snapshot"]["checkpoint"] == 2
        assert reopened.recovery_duration_snapshot("consumer-b")["historical_snapshot"]["checkpoint"] == 3
    finally:
        reopened.close()


@pytest.mark.parametrize("mutation", [
    lambda d: d["recovery_duration"][0].update(member_key=None),
    lambda d: d["recovery_duration"][0].update(member_key={}),
    lambda d: d["recovery_duration"][0].update(binding_ref="other"),
    lambda d: d["recovery_duration"][0].update(source_ref="other"),
    lambda d: d["recovery_duration"][1].update(consumer_id="consumer-a"),
    lambda d: d["recovery_duration"][1].update(member_key=d["recovery_duration"][0]["member_key"]),
    lambda d: d["recovery_duration"][0]["member_key"].update(depth=True),
    lambda d: d["recovery_duration"].clear(),
    lambda d: d["producer_bindings"].append({**deepcopy(d["producer_bindings"][0]),
                                              "binding_ref": "other", "source_ref": "other",
                                              "identity": {"principal_ref": "gateway", "actor_ref": "gateway-service"}}),
])
def test_invalid_recovery_configuration_refused(tmp_path, mutation):
    path, doc, _ = setup_config(tmp_path)
    doc["ingress"]["principals"].append(deepcopy(doc["heartbeat"]["principals"][0]))
    mutation(doc)
    with pytest.raises(MonitorServiceConfigError, match="MONITOR_CONFIG_INVALID"):
        parse_monitor_service_config(doc, path=path)
    assert not (tmp_path / "data").exists()


def test_stalled_worker_preserves_listeners_and_retry_cleanup(tmp_path, monkeypatch):
    _, doc, config = setup_config(tmp_path)
    entered, release = Event(), Event()
    original = RecoveryDurationConsumer.process_next

    def slow(self, facts=()):
        entered.set()
        release.wait(15)
        return original(self, facts)

    monkeypatch.setattr(RecoveryDurationConsumer, "process_next", slow)
    host = MonitorServiceHost(config)
    host.start()
    try:
        assert entered.wait(5)
        def heartbeat():
            remote = client(doc["heartbeat"], keypair(tmp_path, "heartbeat")[0],
                            keypair(tmp_path, "gateway"), "gateway", "monitor.heartbeat")
            remote.start()
            try:
                request = {"schema_version": 1, "relay_epoch": remote.bindings[0].registration_id,
                           "heartbeat_id": "synthetic", "sent_at_ns": 1}
                return remote.call("upstream.telemetry", "monitor.heartbeat", request)
            finally:
                remote.close()
        assert call_with_host(host, heartbeat)["heartbeat_id"] == "synthetic"

        def ingress():
            remote = client(doc["ingress"], keypair(tmp_path, "events")[0],
                            keypair(tmp_path, "producer"), "producer", "events.append")
            remote.start()
            try:
                return remote.call("upstream.telemetry", "events.append", {
                    "schema_version": 2, "event_id": "slow-worker-event", "event_type": TYPE,
                    "occurred_at": 1800000000.0, "binding_ref": "binding", "source_ref": "source",
                    "data": {"synthetic": True},
                })
            finally:
                remote.close()

        assert call_with_host(host, ingress)["source_event_id"] == "slow-worker-event"
        owners = tuple(host._hosts)
        worker = host._recovery_worker
        with pytest.raises(MonitorServiceError, match="MONITOR_CLOSE_FAILED"):
            host.close()
        assert host.state == "close_failed"
        assert tuple(host._hosts) == owners and host._recovery_worker is worker
        assert all(owner.state == "started" for owner in owners)
    finally:
        release.set()
        host.close()
    assert host.state == "closed"


def test_failed_consumer_construction_closes_listeners(tmp_path, monkeypatch):
    _, _, config = setup_config(tmp_path)
    def broken(*args, **kwargs):
        raise RuntimeError("private diagnostic")
    monkeypatch.setattr(RecoveryDurationConsumer, "__init__", broken)
    host = MonitorServiceHost(config)
    with pytest.raises(MonitorServiceError, match="MONITOR_START_FAILED") as error:
        host.start()
    assert "private" not in str(error.value)
    assert host.state == "failed"
    assert all(owner.state == "closed" for owner in host._hosts)
    assert host._recovery_worker.stopped


def test_uncertain_constructor_cleanup_retains_original_host(tmp_path, monkeypatch):
    _, _, config = setup_config(tmp_path)
    entered, release = Event(), Event()
    original = RecoveryDurationConsumer.__init__

    def blocked(self, *args, **kwargs):
        entered.set()
        release.wait(15)
        original(self, *args, **kwargs)

    monkeypatch.setattr(RecoveryDurationConsumer, "__init__", blocked)
    host = MonitorServiceHost(config)
    try:
        with pytest.raises(MonitorServiceError, match="MONITOR_START_CLEANUP_FAILED"):
            host.start()
        assert entered.is_set()
        assert host.state == "close_failed"
        worker, owners = host._recovery_worker, tuple(host._hosts)
        assert not worker.stopped and all(owner.state == "started" for owner in owners)
        release.set()
        wait_for(lambda: worker.stopped)
        host.close()
        assert host._recovery_worker is worker and tuple(host._hosts) == owners
        assert host.state == "closed"
    finally:
        release.set()
        if host.state == "close_failed":
            wait_for(lambda: host._recovery_worker.stopped)
            host.close()


def test_processing_sql_fault_is_unknown_and_fails_host(tmp_path, monkeypatch):
    _, _, config = setup_config(tmp_path)
    failed, release = Event(), Event()

    def broken(self, facts=()):
        release.wait(5)
        failed.set()
        raise sqlite3.OperationalError("private diagnostic")

    monkeypatch.setattr(RecoveryDurationConsumer, "process_next", broken)
    host = MonitorServiceHost(config)
    host.start()
    try:
        release.set()
        assert failed.wait(5)
        wait_for(lambda: host._recovery_worker.failed)
        assert host.recovery_duration_snapshot("consumer-a") == {"status": "UNKNOWN", "current_state": "UNKNOWN", "historical_snapshot": None, "historical_only": True}
        with pytest.raises(MonitorServiceError, match="MONITOR_RECOVERY_FAILED") as error:
            host.serve_once()
        assert "private" not in str(error.value)
    finally:
        host.close()


def _recovery_event(event_id, raw):
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_type": TYPE,
        "occurred_at": 1800000000.0,
        "binding_ref": "binding",
        "source_ref": "source",
        "data": {"schema": "sdk.gateway_recovery_relay/v1",
                 "envelope_time_basis": "FIRST_RELAY_ENQUEUE", "event": raw},
    }


def _publish(tmp_path, doc, events):
    producer = client(doc["ingress"], keypair(tmp_path, "events")[0],
                      keypair(tmp_path, "producer"), "producer", "events.append")
    producer.start()
    try:
        return [producer.call("upstream.telemetry", "events.append", event) for event in events]
    finally:
        producer.close()


def test_acknowledged_stop_never_turns_cached_history_into_current_state(tmp_path, monkeypatch):
    path, doc, _ = setup_config(tmp_path)
    doc["recovery_duration"] = doc["recovery_duration"][:1]
    config = parse_monitor_service_config(doc, path=path)
    monitor_tests = Path(__file__).parents[2] / "bt_api/bt_api_monitor/tests"
    monkeypatch.syspath_prepend(str(monitor_tests))
    from test_recovery_duration_consumer import epoch_marker, raw_event

    blocked, release = Event(), Event()
    original = RecoveryDurationConsumer.process_next
    selected = []

    def pause_after_begin(self, facts=()):
        if self.snapshot()["source_cursor"] == 2:
            selected.append(self)
            blocked.set()
            release.wait(15)
        return original(self, facts)

    monkeypatch.setattr(RecoveryDurationConsumer, "process_next", pause_after_begin)
    host = MonitorServiceHost(config)
    host.start()
    try:
        events = [_recovery_event("epoch", epoch_marker()),
                  _recovery_event("begin", raw_event(2, reason="BEGIN"))]
        call_with_host(host, lambda: _publish(tmp_path, doc, events))
        assert blocked.wait(5)
        historical = host.recovery_duration_snapshot("consumer-a")
        assert historical["historical_snapshot"]["episodes"][0]["state"] == "ACTIVE"
        stopped = epoch_marker(3)
        stopped.update(kind="capture_stopped", through_sequence=1, previous_epoch=None)
        receipt = call_with_host(host, lambda: _publish(tmp_path, doc, [_recovery_event("stop", stopped)]))
        assert receipt[0]["source_event_id"] == "stop"
        current = selected[0].snapshot()
        assert current["unprocessed_tail"] and current["episodes"][0]["state"] == "UNKNOWN"
        view = host.recovery_duration_snapshot("consumer-a")
        assert view["current_state"] == "UNKNOWN" and view["historical_only"] is True
        assert "snapshot" not in view
        assert view["historical_snapshot"]["episodes"][0]["state"] == "ACTIVE"
    finally:
        release.set()
        host.close()


def test_real_sqlite_write_lock_bounds_ingress_and_allows_heartbeat(tmp_path, monkeypatch):
    from bt_api_py.service_runtime import ServiceRuntimeError

    _, doc, config = setup_config(tmp_path)
    locked, release, unlocked = Event(), Event(), Event()
    original = RecoveryDurationConsumer.process_next
    first = True

    def lock_database(self, facts=()):
        nonlocal first
        if first:
            first = False
            with self._outbox._transaction():
                locked.set()
                release.wait(15)
            unlocked.set()
        return original(self, facts)

    monkeypatch.setattr(RecoveryDurationConsumer, "process_next", lock_database)
    host = MonitorServiceHost(config)
    host.start()
    try:
        assert locked.wait(5)
        assert host._resources[0]._timeout_seconds == 0.05
        event = {"schema_version": 2, "event_id": "lock-contended", "event_type": TYPE,
                 "occurred_at": 1800000000.0, "binding_ref": "binding", "source_ref": "source",
                 "data": {"synthetic": True}}

        def heartbeat():
            remote = client(doc["heartbeat"], keypair(tmp_path, "heartbeat")[0],
                            keypair(tmp_path, "gateway"), "gateway", "monitor.heartbeat")
            remote.start()
            try:
                return remote.call("upstream.telemetry", "monitor.heartbeat", {
                    "schema_version": 1, "relay_epoch": remote.bindings[0].registration_id,
                    "heartbeat_id": "concurrent", "sent_at_ns": 1,
                })
            finally:
                remote.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            ingress = executor.submit(_publish, tmp_path, doc, [event])
            heartbeat_result = executor.submit(heartbeat)
            deadline = time.monotonic() + 10
            while (not ingress.done() or not heartbeat_result.done()) and time.monotonic() < deadline:
                host.serve_once(timeout_ms=5)
            with pytest.raises(ServiceRuntimeError) as rejected:
                ingress.result(timeout=1)
            assert rejected.value.code == "BUSINESS_OUTCOME_UNKNOWN"
            assert heartbeat_result.result(timeout=1)["heartbeat_id"] == "concurrent"
        assert not release.is_set()
        release.set()
        assert unlocked.wait(5)
        outbox = host._resources[0]
        rows, more = outbox.read_page_types("events", (TYPE,), 0, 10)
        assert not rows and not more
        receipt = call_with_host(host, lambda: _publish(tmp_path, doc, [event]))
        assert receipt[0]["source_event_id"] == "lock-contended"
        rows, more = outbox.read_page_types("events", (TYPE,), 0, 10)
        assert len(rows) == 1 and not more
    finally:
        release.set()
        host.close()


def test_unselected_recovery_preserves_default_outbox_timeout(tmp_path):
    path, doc, _ = setup_config(tmp_path)
    del doc["recovery_duration"]
    host = MonitorServiceHost(parse_monitor_service_config(doc, path=path))
    host.start()
    try:
        assert host._recovery_worker is None
        assert host._resources[0]._timeout_seconds == 5.0
    finally:
        host.close()
