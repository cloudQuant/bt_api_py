"""Owned, local-only recovery consumers and alert recording; no network delivery."""

from __future__ import annotations

import copy
import threading


class RecoveryDurationWorker:
    """Process bounded steps on one owner thread and retain unknown cleanup."""

    def __init__(self, outbox, configs, producer_bindings):
        self._outbox = outbox
        self._configs = configs
        self._producer_bindings = producer_bindings
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._failed = False
        self._failure_code = None
        self._views = {}
        self._thread = threading.Thread(target=self._run, name="monitor-recovery-duration", daemon=False)

    def start(self):
        self._thread.start()
        if not self._ready.wait(2.0) or self.failed:
            raise RuntimeError("MONITOR_RECOVERY_START_FAILED")

    def _run(self):
        try:
            from bt_api_monitor.recovery_duration import (
                RecoveryDurationBinding,
                RecoveryDurationConsumer,
            )

            delivery = None
            if any(config.alert_rule is not None for config in self._configs):
                from bt_api_monitor.alert_delivery import AlertDeliveryService

                delivery = AlertDeliveryService(self._outbox, sinks=())
            consumers = []
            for config in self._configs:
                if self._stop.is_set():
                    return
                matches = tuple(
                    item for item in self._producer_bindings
                    if item.binding_ref == config.binding_ref and item.source_ref == config.source_ref
                )
                if len(matches) != 1:
                    raise ValueError("MONITOR_RECOVERY_BINDING_INVALID")
                binding = RecoveryDurationBinding(
                    config.consumer_id, matches[0], config.scope_ref, config.config_version,
                    config.member_key,
                )
                rule = None
                if config.alert_rule is not None:
                    from bt_api_monitor.recovery_duration_alerts import RecoveryDurationAlertRule

                    configured = config.alert_rule
                    if configured.notify is not False:
                        raise ValueError("MONITOR_RECOVERY_ALERT_NOTIFY_INVALID")
                    rule = RecoveryDurationAlertRule(
                        configured.rule_id, configured.rule_version, configured.threshold_ns,
                        configured.max_candidates, configured.severity, configured.notify,
                    )
                options = {"scope_bindings": self._producer_bindings}
                if rule is not None:
                    options["alert_rule"] = rule
                consumer = RecoveryDurationConsumer(self._outbox, binding, **options)
                reconciler = None
                if rule is not None:
                    from bt_api_monitor.recovery_duration_alerts import (
                        RecoveryDurationAlertReconciler,
                    )

                    reconciler = RecoveryDurationAlertReconciler(consumer, delivery)
                if self._stop.is_set():
                    return
                snapshot = consumer.snapshot()
                pending = snapshot["pending_reason"] is not None
                consumers.append((config.consumer_id, consumer, pending, reconciler))
                self._store(config.consumer_id, snapshot, pending)
            self._ready.set()
            while not self._stop.is_set():
                for index, (consumer_id, consumer, pending, reconciler) in enumerate(consumers):
                    if self._stop.is_set():
                        break
                    if not pending:
                        consumer.process_next(facts=())
                        snapshot = consumer.snapshot()
                        pending = snapshot["pending_reason"] is not None
                        consumers[index] = (consumer_id, consumer, pending, reconciler)
                        self._store(consumer_id, snapshot, pending)
                    if reconciler is not None:
                        reconciler.reconcile(limit=1)
                self._stop.wait(0.05)
        except BaseException as error:
            with self._lock:
                self._failed = True
                self._failure_code = (
                    "MONITOR_RECOVERY_ALERT_CAPACITY_EXHAUSTED"
                    if getattr(error, "code", None) == "RECOVERY_ALERT_CAPACITY_EXHAUSTED"
                    else "MONITOR_RECOVERY_FAILED"
                )
            self._ready.set()

    def _store(self, consumer_id, snapshot, pending):
        reason = snapshot["pending_reason"]
        status = "PROCESSING"
        if pending:
            status = "PENDING_FACTS" if reason in {
                "PENDING_MAPPING_OR_CALENDAR", "PENDING_CALENDAR_COVERAGE"
            } else "UNKNOWN"
        with self._lock:
            self._views[consumer_id] = {
                "status": status,
                "current_state": "UNKNOWN",
                "historical_snapshot": snapshot,
                "historical_only": True,
            }

    @property
    def failed(self):
        with self._lock:
            return self._failed

    @property
    def failure_code(self):
        with self._lock:
            return self._failure_code

    def snapshot(self, consumer_id):
        with self._lock:
            if self._failed or consumer_id not in self._views:
                return {"status": "UNKNOWN", "current_state": "UNKNOWN",
                        "historical_snapshot": None, "historical_only": True}
            return copy.deepcopy(self._views[consumer_id])

    def stop(self):
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=1.0)
        return self.stopped

    @property
    def stopped(self):
        return self._thread.ident is None or not self._thread.is_alive()
