"""Synthetic same-owner service queue with retained call handles."""

import gc
import os
import threading
import time
import weakref
from dataclasses import replace

import pytest

from bt_api_py.gateway_direct_owner import (
    GatewayDirectOwner,
    GatewayDirectOwnerError,
    GatewayOwnerServiceCallError,
)
from tests.test_iter63_gateway_direct_owner import Harness, command, enqueue, wait_for
from tests.unit.test_iter63_gateway_cancel_owner import cancel, put, with_cancel


def owner_for(factory, **kwargs):
    return GatewayDirectOwner(
        factory,
        queue_size=kwargs.pop("queue_size", 3),
        cancel_queue_size=kwargs.pop("cancel_queue_size", 1),
        service_queue_size=kwargs.pop("service_queue_size", 3),
        max_queue_wait_seconds=kwargs.pop("max_queue_wait_seconds", 2),
        busy_timeout_seconds=kwargs.pop("busy_timeout_seconds", 2),
        **kwargs,
    )


def test_same_handle_running_and_queued_timeouts_no_duplicate(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def block():
        calls.append(("running", threading.get_ident()))
        entered.set()
        assert release.wait(3)
        return {"one": 1}

    try:
        assert owner.start(2)
        running = owner.submit_service_call(block)
        assert entered.wait(2)
        queued = owner.submit_service_call(lambda: calls.append(("queued", threading.get_ident())))
        assert owner.status().service_queue_depth == 1
        assert owner.status().queue_depth == 0
        assert not running.done and not queued.done
        for _ in range(2):
            with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_TIMEOUT_RUNNING"):
                running.result(timeout_seconds=0)
            with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_TIMEOUT_QUEUED"):
                queued.result(timeout_seconds=0)
        assert not running.cancel()
        release.set()
        assert running.result(timeout_seconds=2) == {"one": 1}
        assert running.result(timeout_seconds=0) == {"one": 1}
        assert queued.result(timeout_seconds=2) is None
        assert running.done and queued.done
        for bad_timeout in (True, -1, float("nan"), float("inf"), 3601, "1"):
            with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_INVALID_TIMEOUT"):
                running.result(timeout_seconds=bad_timeout)
        assert [name for name, _ in calls] == ["running", "queued"]
        assert {tid for _, tid in calls} == {harness.threads[0][1]}
    finally:
        release.set()
        assert owner.close(2)
    assert running.result(timeout_seconds=0) == {"one": 1}


def test_capacity_cancel_priority_close_and_callback_release(tmp_path):
    harness = Harness(tmp_path)
    harness.block = True
    owner = owner_for(with_cancel(harness), service_queue_size=1)
    called = []

    class Callback:
        def __call__(self):
            called.append(True)

    try:
        assert owner.start(2)
        assert enqueue(owner, command("active"), harness, "active") == "QUEUED"
        assert harness.started.wait(2)
        callback = Callback()
        reference = weakref.ref(callback)
        first = owner.submit_service_call(callback)
        del callback
        with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_QUEUE_FULL"):
            owner.submit_service_call(lambda: None)
        assert put(owner, harness, cancel("c"), "cancel") == "QUEUED"
        assert first.cancel() and first.done and not first.cancel()
        with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_CANCELLED"):
            first.result(timeout_seconds=0)
        gc.collect()
        assert reference() is None
        pending = owner.submit_service_call(lambda: called.append(True))
        assert not owner.close(0.02)
        assert pending.done
        with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_CLOSED"):
            pending.result(timeout_seconds=0)
        with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_NOT_READY"):
            owner.submit_service_call(lambda: None)
        assert called == [] and not owner.closed
    finally:
        harness.release.set()
        assert owner.close(2)
    assert owner.closed and called == []


def test_service_expiry_and_exception_codes_are_finite(tmp_path):
    harness = Harness(tmp_path)
    harness.block = True
    owner = owner_for(harness.factory, max_queue_wait_seconds=0.03)
    try:
        assert owner.start(2)
        assert enqueue(owner, command("active"), harness, "active") == "QUEUED"
        assert harness.started.wait(2)
        pending = owner.submit_service_call(lambda: pytest.fail("expired callback executed"))
        time.sleep(0.06)
        harness.release.set()
        with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_EXPIRED"):
            pending.result(timeout_seconds=2)

        def known():
            raise GatewayOwnerServiceCallError("SAFE_FAILURE")

        def private():
            raise RuntimeError("secret private detail")

        with pytest.raises(GatewayOwnerServiceCallError, match="SAFE_FAILURE"):
            owner.submit_service_call(known).result(timeout_seconds=2)
        with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_FAILED") as failure:
            owner.submit_service_call(private).result(timeout_seconds=2)
        assert "secret" not in str(failure.value)
        with pytest.raises(ValueError):
            GatewayOwnerServiceCallError("bad value")
    finally:
        harness.release.set()
        assert owner.close(2)


def test_completed_service_releases_callback_without_a_result_registry(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory)

    class Callback:
        def __call__(self):
            return "done"

    try:
        assert owner.start(2)
        callback = Callback()
        reference = weakref.ref(callback)
        handle = owner.submit_service_call(callback)
        del callback
        assert handle.result(timeout_seconds=2) == "done"
        gc.collect()
        assert reference() is None
        assert handle.result(timeout_seconds=0) == "done"
    finally:
        assert owner.close(2)


def test_pid_and_owner_reentry_checked_before_lock(tmp_path, monkeypatch):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory)
    entered, release = threading.Event(), threading.Event()
    retained = []

    def reentry():
        entered.set()
        assert release.wait(2)
        observed = []
        for call in (
            lambda: owner.submit_service_call(lambda: None),
            retained[0].result,
            retained[0].cancel,
            lambda: retained[0].done,
        ):
            try:
                call()
            except GatewayDirectOwnerError as error:
                observed.append(error.code)
        return observed

    try:
        assert owner.start(2)
        retained.append(owner.submit_service_call(reentry))
        assert entered.wait(2)
        real_pid = os.getpid()
        with monkeypatch.context() as patch:
            patch.setattr("bt_api_py.gateway_direct_owner.os.getpid", lambda: real_pid + 1)
            for call in (
                lambda: owner.submit_service_call(lambda: None),
                retained[0].result,
                retained[0].cancel,
                lambda: retained[0].done,
                lambda: owner.closed,
            ):
                with pytest.raises(GatewayDirectOwnerError, match="PROCESS_BOUNDARY"):
                    call()
        release.set()
        assert retained[0].result(timeout_seconds=2) == ["SERVICE_CALL_OWNER_REENTRY"] * 4
    finally:
        release.set()
        assert owner.close(2)


def test_closed_requires_completed_cleanup_and_supports_never_started(tmp_path):
    harness = Harness(tmp_path)
    unopened = owner_for(harness.factory)
    assert not unopened.closed
    assert unopened.close(1) and unopened.closed
    assert not unopened.start(1)

    (tmp_path / "other").mkdir()
    harness2 = Harness(tmp_path / "other")
    harness2.fail_cleanup = 1
    owner = owner_for(harness2.factory)
    assert owner.start(2) and not owner.closed
    assert not owner.close(0.03) and not owner.closed
    assert owner.close(2) and owner.closed


@pytest.mark.parametrize("size", [0, True, 10001, 1.0])
def test_service_capacity_is_bounded(tmp_path, size):
    harness = Harness(tmp_path)
    with pytest.raises(GatewayDirectOwnerError, match="INVALID_CONFIGURATION"):
        owner_for(harness.factory, service_queue_size=size)


def test_cancel_priority_place_service_fairness_and_due_maintenance(tmp_path):
    harness = Harness(tmp_path)
    entered, release = threading.Event(), threading.Event()
    events = []
    dispatch = harness.dispatch

    def place(strategy, key, principal, reservation_id, digest, version, *, request_fence):
        events.append("place:" + key)
        return dispatch(
            strategy, key, principal, reservation_id, digest, version, request_fence=request_fence
        )

    harness.dispatch = place

    def maintenance():
        events.append("maintain")

    def block():
        entered.set()
        assert release.wait(2)

    owner = owner_for(
        with_cancel(
            harness,
            maintain=maintenance,
            interval=0.01,
            cancel_call=lambda: events.append("cancel"),
        )
    )
    try:
        assert owner.start(2)
        active = owner.submit_service_call(block)
        assert entered.wait(2)
        assert enqueue(owner, command("one"), harness, "one") == "QUEUED"
        assert enqueue(owner, command("two"), harness, "two") == "QUEUED"
        first = owner.submit_service_call(lambda: events.append("service:one"))
        second = owner.submit_service_call(lambda: events.append("service:two"))
        assert put(owner, harness, cancel("c"), "cancel") == "QUEUED"
        time.sleep(0.03)
        marker = len(events)
        release.set()
        active.result(timeout_seconds=2)
        first.result(timeout_seconds=2)
        second.result(timeout_seconds=2)
        wait_for(lambda: "place:two" in events)
        post = events[marker:]
        assert post[0] == "maintain"
        assert [item for item in post if item != "maintain"] == [
            "cancel",
            "place:one",
            "service:one",
            "place:two",
            "service:two",
        ]
    finally:
        release.set()
        assert owner.close(2)


def test_maintenance_failure_discards_queued_service(tmp_path):
    harness = Harness(tmp_path)
    entered, release = threading.Event(), threading.Event()
    count = []

    def maintain():
        count.append(True)
        if len(count) > 1:
            raise RuntimeError("private")

    def block():
        entered.set()
        assert release.wait(2)

    def factory():
        return replace(harness.factory(), maintain=maintain, maintenance_interval_seconds=0.01)

    owner = owner_for(factory)
    try:
        assert owner.start(2)
        running = owner.submit_service_call(block)
        assert entered.wait(2)
        pending = owner.submit_service_call(lambda: pytest.fail("discarded callback executed"))
        time.sleep(0.03)
        release.set()
        running.result(timeout_seconds=2)
        with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_CLOSED"):
            pending.result(timeout_seconds=2)
        wait_for(lambda: owner.closed)
        assert owner.status().failure_category == "maintenance_failed"
    finally:
        release.set()
        assert owner.close(2)


def test_expired_pop_and_cancel_are_atomic_under_controlled_interleaving(tmp_path):
    from collections import deque

    harness = Harness(tmp_path)
    owner = owner_for(harness.factory, max_queue_wait_seconds=0.03)
    entered, release = threading.Event(), threading.Event()
    popped, resume_owner = threading.Event(), threading.Event()
    marked = threading.Event()
    original_condition = owner._condition

    class InstrumentedCondition:
        def __enter__(self):
            return original_condition.__enter__()

        def __exit__(self, *args):
            result = original_condition.__exit__(*args)
            if threading.current_thread() is owner._thread and marked.is_set():
                marked.clear()
                popped.set()
                assert resume_owner.wait(2)
            return result

        def __getattr__(self, key):
            return getattr(original_condition, key)

    class InstrumentedQueue(deque):
        def popleft(self):
            task = super().popleft()
            if time.monotonic() >= task.deadline:
                marked.set()
            return task

    owner._condition = InstrumentedCondition()
    owner._service_queue = InstrumentedQueue()

    def block():
        entered.set()
        assert release.wait(2)

    try:
        assert owner.start(2)
        running = owner.submit_service_call(block)
        assert entered.wait(2)
        expired = owner.submit_service_call(lambda: pytest.fail("expired callback executed"))
        time.sleep(0.06)
        release.set()
        assert popped.wait(2)
        assert expired.cancel() is False
        assert expired.done
        with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_EXPIRED"):
            expired.result(timeout_seconds=0)
        resume_owner.set()
        running.result(timeout_seconds=2)
    finally:
        release.set()
        resume_owner.set()
        assert owner.close(2)


def test_callback_cannot_start_or_close_owner_and_owner_continues(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory)

    def reentry():
        results = []
        for action in (lambda: owner.start(3600), lambda: owner.close(3600)):
            try:
                action()
            except GatewayDirectOwnerError as error:
                results.append(error.code)
        return results

    try:
        assert owner.start(2)
        assert owner.submit_service_call(reentry).result(timeout_seconds=2) == [
            "SERVICE_CALL_OWNER_REENTRY",
            "SERVICE_CALL_OWNER_REENTRY",
        ]
        assert (
            owner.submit_service_call(lambda: "continued").result(timeout_seconds=2) == "continued"
        )
        assert not owner.closed
    finally:
        assert owner.close(2)


def test_service_port_claims_are_bounded_identity_scoped_and_persistent(tmp_path, monkeypatch):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory)
    claimant, rival = object(), object()
    owner.claim_service_port("market", claimant)
    owner.claim_service_port("market", claimant)
    with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_PORT_ALREADY_CLAIMED"):
        owner.claim_service_port("market", rival)
    for i in range(15):
        owner.claim_service_port(f"other-{i}", object())
    with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_PORT_LIMIT"):
        owner.claim_service_port("excess", object())
    for name in ("", "x" * 65, "bad space", "市场", ".bad"):
        with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_PORT_INVALID"):
            owner.claim_service_port(name, claimant)
    with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_PORT_INVALID"):
        owner.claim_service_port("invalid", None)
    original_pid = os.getpid()
    with monkeypatch.context() as patch:
        patch.setattr("bt_api_py.gateway_direct_owner.os.getpid", lambda: original_pid + 1)
        with pytest.raises(GatewayDirectOwnerError, match="PROCESS_BOUNDARY"):
            owner.claim_service_port("market", claimant)
    try:
        assert owner.start(2)

        def reentry():
            try:
                owner.claim_service_port("market", claimant)
            except GatewayDirectOwnerError as error:
                return error.code

        assert (
            owner.submit_service_call(reentry).result(timeout_seconds=2)
            == "SERVICE_CALL_OWNER_REENTRY"
        )
    finally:
        assert owner.close(2)
    with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_NOT_READY"):
        owner.claim_service_port("market", claimant)
