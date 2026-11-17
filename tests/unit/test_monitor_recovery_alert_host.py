"""Standalone host local recovery alert recording, using synthetic facts only."""

import sqlite3
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from bt_api_monitor.alert_delivery import AlertDeliveryService
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.recovery_duration import RecoveryDurationBinding, RecoveryDurationConsumer
from bt_api_monitor.recovery_duration_alerts import (
    RecoveryDurationAlertError,
    RecoveryDurationAlertReconciler,
    RecoveryDurationAlertRule,
)
from bt_api_monitor.service_ingress import MonitorIngressProducerBinding

from bt_api_py.monitor_service_config import MonitorServiceConfigError, parse_monitor_service_config
from bt_api_py.monitor_service_host import MonitorServiceError, MonitorServiceHost
from tests.integration.test_monitor_service_process import client
from tests.unit.test_monitor_recovery_duration_host import (
    call_with_host,
    keypair,
    setup_config,
    wait_for,
)

RULE = {"rule_id": "local-recovery", "rule_version": 1, "threshold_ns": 1,
        "max_candidates": 20, "severity": "warning", "notify": False}


def configured(tmp_path):
    path, doc, _ = setup_config(tmp_path)
    doc["recovery_duration"] = doc["recovery_duration"][:1]
    doc["recovery_duration"][0]["alert_rule"] = deepcopy(RULE)
    return path, doc, parse_monitor_service_config(doc, path=path)


@pytest.mark.parametrize("change", [
    lambda rule: rule.update(notify=True),
    lambda rule: rule.update(notify=0),
    lambda rule: rule.update(rule_id="private rule"),
    lambda rule: rule.update(rule_id=""),
    lambda rule: rule.update(rule_version=True),
    lambda rule: rule.update(rule_version=0),
    lambda rule: rule.update(threshold_ns=0),
    lambda rule: rule.update(threshold_ns=True),
    lambda rule: rule.update(threshold_ns=1 << 63),
    lambda rule: rule.update(max_candidates=1),
    lambda rule: rule.update(max_candidates=1_000_001),
    lambda rule: rule.update(severity="error"),
    lambda rule: rule.update(extra="private"),
    lambda rule: rule.pop("notify"),
])
def test_invalid_rule_is_rejected_offline(tmp_path, change):
    path, doc, _ = configured(tmp_path)
    change(doc["recovery_duration"][0]["alert_rule"])
    with pytest.raises(MonitorServiceConfigError) as error:
        parse_monitor_service_config(doc, path=path)
    assert str(error.value) == "MONITOR_CONFIG_INVALID"
    assert not (tmp_path / "data").exists()


def test_absent_rule_creates_no_alert_tables(tmp_path):
    path, doc, _ = configured(tmp_path)
    del doc["recovery_duration"][0]["alert_rule"]
    host = MonitorServiceHost(parse_monitor_service_config(doc, path=path))
    host.start()
    try:
        with sqlite3.connect(tmp_path / "data/events.sqlite") as db:
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "(name LIKE 'monitor_alert_%' OR name='recovery_duration_alert_candidate')"
            ).fetchall()
        assert tables == []
    finally:
        host.close()


def seed_candidate(config, tmp_path, monkeypatch):
    monitor_tests = Path(__file__).parents[2] / "bt_api/bt_api_monitor/tests"
    monkeypatch.syspath_prepend(str(monitor_tests))
    from test_recovery_duration_calculation import fact
    from test_recovery_duration_consumer import append, epoch_marker, raw_event

    config.storage_directory.mkdir(mode=0o700)
    outbox = DurableOutbox(config.storage_directory / "events.sqlite")
    producer = config.producer_bindings[0]
    ingress = MonitorIngressProducerBinding(producer.binding_ref, producer.source_ref,
        producer.identity, producer.scope, producer.outbox_scope, producer.event_types)
    item = config.recovery_duration[0]
    binding = RecoveryDurationBinding(item.consumer_id, ingress, item.scope_ref,
                                      item.config_version, item.member_key)
    rule = RecoveryDurationAlertRule(**RULE)
    consumer = RecoveryDurationConsumer(outbox, binding, scope_bindings=(ingress,), alert_rule=rule)
    for identifier, raw in (("epoch", epoch_marker()), ("begin", raw_event(2, reason="BEGIN")),
                            ("mark", raw_event(3))):
        append(outbox, binding, raw, identifier)
    assert consumer.process_next() == "SOURCE_CONTINUITY_UNKNOWN"
    assert consumer.process_next() == "BEGIN_CANDIDATE"
    assert consumer.process_next((fact(),)) == "WATERMARK_RECORDED"
    append(outbox, binding, raw_event(4), "pending")
    assert consumer.process_next() == "PENDING_MAPPING_OR_CALENDAR"
    outbox.database_path.chmod(0o600)
    return outbox, consumer


def sql_counts(path):
    with sqlite3.connect(path) as db:
        return (db.execute("SELECT COUNT(*), COALESCE(SUM(delivered),0) FROM "
                           "recovery_duration_alert_candidate").fetchone(),
                db.execute("SELECT COUNT(*) FROM monitor_alert_events").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM monitor_alert_delivery_outbox").fetchone()[0])


def test_pending_source_replays_recorded_candidate_without_duplicate(tmp_path, monkeypatch):
    _, doc, config = configured(tmp_path)
    outbox, consumer = seed_candidate(config, tmp_path, monkeypatch)
    delivery = AlertDeliveryService(outbox, sinks=())
    original = delivery.record

    def crash_after_record(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic crash")

    monkeypatch.setattr(delivery, "record", crash_after_record)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        RecoveryDurationAlertReconciler(consumer, delivery).reconcile(limit=1)
    assert sql_counts(outbox.database_path) == ((1, 0), 1, 0)
    entered, release = Event(), Event()
    original_reconcile = RecoveryDurationAlertReconciler.reconcile

    def paused_reconcile(self, *, limit):
        entered.set()
        release.wait(15)
        return original_reconcile(self, limit=limit)

    monkeypatch.setattr(RecoveryDurationAlertReconciler, "reconcile", paused_reconcile)
    host = MonitorServiceHost(config)
    host.start()
    try:
        assert entered.wait(5)
        assert sql_counts(outbox.database_path) == ((1, 0), 1, 0)
        view = host.recovery_duration_snapshot("consumer-a")
        assert view["status"] == "PENDING_FACTS"
        assert view["current_state"] == "UNKNOWN" and view["historical_only"] is True
        assert view["historical_snapshot"]["checkpoint"] == 3
        def heartbeat():
            remote = client(doc["heartbeat"], keypair(tmp_path, "heartbeat")[0],
                            keypair(tmp_path, "gateway"), "gateway", "monitor.heartbeat")
            remote.start()
            try:
                return remote.call("upstream.telemetry", "monitor.heartbeat", {
                    "schema_version": 1, "relay_epoch": remote.bindings[0].registration_id,
                    "heartbeat_id": "alert-replay", "sent_at_ns": 1,
                })
            finally:
                remote.close()
        assert call_with_host(host, heartbeat)["heartbeat_id"] == "alert-replay"
        release.set()
        wait_for(lambda: sql_counts(outbox.database_path) == ((1, 1), 1, 0))
    finally:
        release.set()
        host.close()
    reopened = MonitorServiceHost(config)
    reopened.start()
    try:
        wait_for(lambda: reopened.recovery_duration_snapshot("consumer-a")["status"] == "PENDING_FACTS")
        assert sql_counts(outbox.database_path) == ((1, 1), 1, 0)
    finally:
        reopened.close()


def test_capacity_failure_is_finite_and_visible(tmp_path, monkeypatch):
    _, _, config = configured(tmp_path)
    release = Event()
    def fail(self, facts=()):
        release.wait(5)
        raise RecoveryDurationAlertError("RECOVERY_ALERT_CAPACITY_EXHAUSTED")
    monkeypatch.setattr(RecoveryDurationConsumer, "process_next", fail)
    host = MonitorServiceHost(config)
    host.start()
    try:
        release.set()
        wait_for(lambda: host._recovery_worker.failed)
        assert host.recovery_duration_snapshot("consumer-a")["current_state"] == "UNKNOWN"
        with pytest.raises(MonitorServiceError, match="MONITOR_RECOVERY_ALERT_CAPACITY_EXHAUSTED"):
            host.serve_once()
    finally:
        release.set()
        host.close()


def test_direct_config_cannot_enable_notification(tmp_path):
    _, _, config = configured(tmp_path)
    item = config.recovery_duration[0]
    unsafe = replace(item, alert_rule=replace(item.alert_rule, notify=True))
    host = MonitorServiceHost(replace(config, recovery_duration=(unsafe,)))
    with pytest.raises(MonitorServiceError, match="MONITOR_START_FAILED"):
        host.start()
    assert host._recovery_worker.stopped
    assert all(item.state == "closed" for item in host._hosts)


def test_reconciler_fault_fails_selected_worker(tmp_path, monkeypatch):
    _, _, config = configured(tmp_path)
    entered, release = Event(), Event()

    def broken(self, *, limit):
        entered.set()
        release.wait(5)
        raise RuntimeError("private alert failure")

    monkeypatch.setattr(RecoveryDurationAlertReconciler, "reconcile", broken)
    host = MonitorServiceHost(config)
    host.start()
    try:
        assert entered.wait(5)
        release.set()
        wait_for(lambda: host._recovery_worker.failed)
        assert host.recovery_duration_snapshot("consumer-a")["current_state"] == "UNKNOWN"
        with pytest.raises(MonitorServiceError, match="MONITOR_RECOVERY_FAILED") as error:
            host.serve_once()
        assert "private" not in str(error.value)
    finally:
        release.set()
        host.close()


def test_stalled_reconciler_retains_owner_until_confirmed_stop(tmp_path, monkeypatch):
    _, _, config = configured(tmp_path)
    entered, release = Event(), Event()
    original = RecoveryDurationAlertReconciler.reconcile
    def slow(self, *, limit):
        assert limit == 1
        entered.set()
        release.wait(15)
        return original(self, limit=limit)
    monkeypatch.setattr(RecoveryDurationAlertReconciler, "reconcile", slow)
    host = MonitorServiceHost(config)
    host.start()
    try:
        assert entered.wait(5)
        owners, worker = tuple(host._hosts), host._recovery_worker
        with pytest.raises(MonitorServiceError, match="MONITOR_CLOSE_FAILED"):
            host.close()
        assert host.state == "close_failed" and host._recovery_worker is worker
        assert tuple(host._hosts) == owners and all(item.state == "started" for item in owners)
    finally:
        release.set()
        host.close()
    assert host.state == "closed"
