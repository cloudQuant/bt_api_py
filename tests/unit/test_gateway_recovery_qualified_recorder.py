"""Local qualified recorder: real owner and SQLite, synthetic clock and source."""

import os
import threading
import time

import pytest
from bt_api_base.market_recovery_observation import RecoveryObservationPage
from bt_api_gateway.recovery_observation_journal import RecoveryObservationJournal

import bt_api_py.gateway_recovery_observation as module
from bt_api_py.gateway_recovery_clock_qualification import GatewayRecoveryClockQualifier
from bt_api_py.gateway_recovery_qualified_capture import GatewayQualifiedRecoveryCapture
from tests.test_iter63_gateway_direct_owner import wait_for
from tests.unit.test_gateway_recovery_clock_qualification import fake_history, point
from tests.unit.test_gateway_recovery_observation import owner_for, page, settings
from tests.unit.test_gateway_recovery_qualified_capture import observation
from tests.unit.test_iter63_gateway_server_clock_refresh import make_refresher


def qualified_settings(tmp_path, **changes):
    return settings(
        tmp_path,
        qualification_mode="local_clock_v1",
        clock_max_interval_ns=1_000_000_000,
        **changes,
    )


def journal(cfg, mode="local_clock_v1"):
    return RecoveryObservationJournal(
        cfg.journal_path,
        scope_ref=cfg.scope_ref,
        max_events=cfg.max_events,
        max_bytes=cfg.max_bytes,
        timeout_seconds=cfg.sqlite_timeout_seconds,
        qualification_mode=mode,
    )


@pytest.mark.parametrize(
    "mode,threshold",
    [
        ("disabled", 1),
        ("disabled", True),
        ("local_clock_v1", None),
        ("local_clock_v1", True),
        ("local_clock_v1", 0),
        ("local_clock_v1", 1 << 63),
        ("OTHER", None),
        (None, None),
    ],
)
def test_configuration_requires_explicit_bounded_threshold(tmp_path, mode, threshold):
    with pytest.raises(ValueError, match="INVALID_RECOVERY_OBSERVATION_SETTINGS"):
        settings(tmp_path, qualification_mode=mode, clock_max_interval_ns=threshold)


def test_qualified_pair_is_committed_once_and_retried_without_requalification(
    tmp_path, monkeypatch
):
    owner, harness = owner_for(tmp_path)
    cfg = qualified_settings(tmp_path)
    base = time.monotonic_ns() - 10_000_000_000
    items = (
        observation(1, base),
        observation(2, base, state="UNKNOWN"),
        observation(3, base, start=1100, through=1200),
    )
    entered, release = threading.Event(), threading.Event()
    helper = [None]
    captures, writes = [], []

    def capture(after, limit):
        if helper[0] is None:
            source, _ = make_refresher(monkeypatch, lambda *_: (0, 100), history_capacity=8)
            fake_history(monkeypatch, source, [point(base, 1, 100)])
            qualifier = GatewayRecoveryClockQualifier(
                source, max_interval_ns=cfg.clock_max_interval_ns
            )

            def raw(cursor, _limit):
                captures.append((threading.get_ident(), cursor))
                return RecoveryObservationPage(
                    "b" * 32, os.getpid(), cursor, 1, 3, items if cursor == 0 else (), False
                )

            helper[0] = GatewayQualifiedRecoveryCapture(raw, qualifier=qualifier)
        return helper[0].capture(after, limit)

    class LostReply(RecoveryObservationJournal):
        def append_page(self, source, raw, *, qualifications=None):
            writes.append((threading.get_ident(), raw, qualifications))
            result = super().append_page(source, raw, qualifications=qualifications)
            if len(writes) == 1:
                entered.set()
                assert release.wait(3)
                raise OSError("synthetic lost commit acknowledgement")
            return result

    monkeypatch.setattr(module, "RecoveryObservationJournal", LostReply)
    recorder = module.GatewayRecoveryObservationRecorder(owner, capture, cfg)
    try:
        recorder.start()
        assert entered.wait(2)
        assert recorder.status()["pending_pages"] == 1
        owner._fence_dispatch_shutdown()
        release.set()
        wait_for(lambda: len(writes) >= 2 and recorder.status()["pending_pages"] == 0)
        assert writes[0][1] is writes[1][1] and writes[0][2] is writes[1][2]
        assert captures == [(harness.threads[0][1], 0)]
        assert writes[0][0] != captures[0][0]
        assert recorder.close()
        records = journal(cfg).read_after(0, limit=20).records
        rows = [row for row in records if row.kind == "observation"]
        assert [row.observation for row in rows] == list(items)
        assert [row.qualification.reason for row in rows] == [
            "OK",
            "SOURCE_UNKNOWN",
            "SAMPLE_EXPIRED",
        ]
        first = rows[0].qualification
        assert first.start_utc_lower_ns == 1_700_000_000_000_000_000 + 150 - 100
        assert first.end_utc_upper_ns == 1_700_000_000_000_000_000 + 250 + 100
        assert records[-1].kind == "capture_stopped" and records[-1].through_sequence == 3
    finally:
        release.set()
        assert recorder.close()
        assert owner.close(2)


def test_no_clock_and_source_unknown_remain_unknown(tmp_path):
    owner, _ = owner_for(tmp_path)
    cfg = qualified_settings(tmp_path)
    base = time.monotonic_ns() - 10_000_000_000
    items = (observation(1, base), observation(2, base, state="UNKNOWN"))
    helper = [None]

    def capture(after, limit):
        if helper[0] is None:
            helper[0] = GatewayQualifiedRecoveryCapture(
                lambda cursor, _: RecoveryObservationPage(
                    "b" * 32, os.getpid(), cursor, 1, 2, items if cursor == 0 else (), False
                )
            )
        return helper[0].capture(after, limit)

    recorder = module.GatewayRecoveryObservationRecorder(owner, capture, cfg)
    try:
        recorder.start()
        wait_for(lambda: recorder.status()["state"] == "ready")
        owner._fence_dispatch_shutdown()
        assert recorder.close()
        rows = [
            row for row in journal(cfg).read_after(0, limit=10).records if row.kind == "observation"
        ]
        assert [row.qualification.reason for row in rows] == ["CLOCK_UNAVAILABLE", "SOURCE_UNKNOWN"]
        assert all(row.qualification.start_utc_lower_ns is None for row in rows)
    finally:
        assert recorder.close()
        assert owner.close(2)


def test_v2_rejects_v1_without_changing_existing_records(tmp_path):
    cfg = qualified_settings(tmp_path)
    existing = journal(cfg, mode="disabled")
    existing.append_page("source", page(0))
    before = existing.read_after(0, limit=10)
    owner, _ = owner_for(tmp_path)
    recorder = module.GatewayRecoveryObservationRecorder(owner, lambda *_: None, cfg)
    try:
        with pytest.raises(RuntimeError, match="RECORDER_START_FAILED"):
            recorder.start()
        assert recorder.status()["last_failure_code"] == "JOURNAL_START_FAILED"
        assert journal(cfg, mode="disabled").read_after(0, limit=10) == before
        assert recorder.close()
    finally:
        assert recorder.close()
        assert owner.close(2)


@pytest.mark.parametrize("kind", ["raw", "tampered"])
def test_v2_rejects_raw_or_tampered_pairs_without_journaling(tmp_path, kind):
    owner, _ = owner_for(tmp_path)
    cfg = qualified_settings(tmp_path)

    def capture(after, limit):
        if kind == "raw":
            return page(after)
        paired = GatewayQualifiedRecoveryCapture(lambda cursor, _: page(cursor)).capture(
            after, limit
        )
        object.__setattr__(paired.qualifications[0], "reason", "FORGED")
        return paired

    recorder = module.GatewayRecoveryObservationRecorder(owner, capture, cfg)
    try:
        recorder.start()
        wait_for(lambda: recorder.status()["last_failure_code"] == "INVALID_SOURCE_PAGE")
        assert recorder.status()["pending_pages"] == 0
        assert journal(cfg).read_after(0, limit=10).records == ()
    finally:
        assert recorder.close()
        assert owner.close(2)


@pytest.mark.parametrize("pending", ["queued", "running"])
def test_v2_close_retains_running_or_cancels_queued(tmp_path, pending):
    owner, _ = owner_for(tmp_path)
    cfg = qualified_settings(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(3)

    if pending == "queued":
        blocker = owner.submit_service_call(block)
        assert entered.wait(2)
    calls = []

    def capture(after, limit):
        calls.append(after)
        if pending == "running":
            block()
        return GatewayQualifiedRecoveryCapture(lambda cursor, _: page(cursor)).capture(after, limit)

    recorder = module.GatewayRecoveryObservationRecorder(owner, capture, cfg)
    try:
        recorder.start()
        if pending == "running":
            assert entered.wait(2)
        wait_for(lambda: recorder.status()["pending_calls"] == 1)
        if pending == "queued":
            assert recorder.close()
            assert calls == []
        else:
            assert not recorder.close()
            assert recorder.status()["pending_calls"] == 1 and calls == [0]
        release.set()
        if pending == "queued":
            blocker.result(timeout_seconds=2)
        assert recorder.close()
    finally:
        release.set()
        assert recorder.close()
        assert owner.close(2)
