"""Synthetic cancellation queue behavior on the existing single owner."""

import os
import threading
from contextlib import contextmanager
from dataclasses import replace

import pytest
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal

from bt_api_py.gateway_direct_owner import (
    GatewayCancelCommand,
    GatewayDirectOwner,
    GatewayDirectOwnerError,
)
from tests.test_iter63_gateway_direct_owner import (
    Harness,
    command,
    enqueue,
    fence,
    wait_for,
)


def cancel(key="cancel", version=1):
    return GatewayCancelCommand("strategy", key, "principal", version)


def put(owner, harness, cmd, label, *, wait=None, request_fence=fence):
    return owner.enqueue_cancel(
        cmd,
        request_fence=request_fence,
        finalize=harness.finalize(label),
        queue_wait_seconds=wait,
    )


def with_cancel(
    harness, *, journal_dispatcher=None, maintain=None, interval=None, cancel_call=None
):
    def factory():
        bundle = harness.factory()
        cancel_dispatcher = GatewayDirectCancelDispatcher(
            GatewayDirectCancelJournal(bundle.journal),
            bundle.dispatcher,
            harness.guard,
            "SYN",
            lambda *_: None,
            lambda _: True,
        )
        if journal_dispatcher is not None:
            cancel_dispatcher._owner = journal_dispatcher

        def dispatch(strategy, operation_id, principal, *, expected_version, request_fence):
            harness.threads.append(("cancel", threading.get_ident()))
            with request_fence(operation_id):
                harness.dispatched.append("cancel:" + operation_id)
                if cancel_call is not None:
                    cancel_call()

        cancel_dispatcher.dispatch = dispatch
        return replace(
            bundle,
            cancel_dispatcher=cancel_dispatcher,
            maintain=maintain,
            maintenance_interval_seconds=interval,
        )

    return factory


def owner_for(factory, **kwargs):
    config = {
        "queue_size": 1,
        "cancel_queue_size": 1,
        "max_queue_wait_seconds": 1,
        "busy_timeout_seconds": 2,
    }
    config.update(kwargs)
    return GatewayDirectOwner(factory, **config)


def test_reserved_capacity_priority_namespace_duplicates_and_same_thread(tmp_path):
    h = Harness(tmp_path)
    h.block = True
    owner = owner_for(with_cancel(h))
    try:
        assert owner.start(2)
        assert enqueue(owner, command("active"), h, "active") == "QUEUED"
        assert h.started.wait(2)
        assert enqueue(owner, command("same"), h, "place") == "QUEUED"
        assert enqueue(owner, command("full"), h, "full") == "FULL"
        assert put(owner, h, cancel("same"), "cancel") == "QUEUED"
        assert owner.status().queue_depth == 2
        assert not owner.status().accepting  # This flag describes place capacity.
        assert put(owner, h, cancel("same"), "duplicate") == "DUPLICATE"
        assert put(owner, h, cancel("same", 2), "conflict") == "CONFLICT"
        assert put(owner, h, cancel("other"), "cancel-full") == "FULL"
        h.release.set()
        wait_for(lambda: len(h.finalized) == 7)
        assert h.dispatched == ["active", "cancel:same", "same"]
        assert len(set(h.finalized)) == 7
    finally:
        h.release.set()
        assert owner.close(2)
    assert {tid for _, tid in h.threads} == {h.threads[0][1]}
    assert h.threads[0][1] != threading.get_ident()


def test_expiry_and_revoked_fence_finalize_exactly_once(tmp_path):
    h = Harness(tmp_path)
    h.block = True
    owner = owner_for(with_cancel(h), cancel_queue_size=2)
    revoked = []

    @contextmanager
    def denied(_):
        revoked.append(True)
        raise RuntimeError("synthetic revoked permit")
        yield

    try:
        assert owner.start(2)
        assert enqueue(owner, command("active"), h, "active") == "QUEUED"
        assert h.started.wait(2)
        assert put(owner, h, cancel("expired"), "expired", wait=0.02) == "QUEUED"
        assert put(owner, h, cancel("denied"), "denied", request_fence=denied) == "QUEUED"
        threading.Event().wait(0.04)
        h.release.set()
        wait_for(lambda: len(h.finalized) == 3)
        assert h.dispatched == ["active"] and revoked == [True]
        assert sorted(h.finalized) == ["active", "denied", "expired"]
        assert owner.status().thread_alive
    finally:
        h.release.set()
        assert owner.close(2)


def test_close_discards_queues_but_keeps_active_guard(tmp_path):
    h = Harness(tmp_path)
    h.block = True
    owner = owner_for(with_cancel(h))
    try:
        assert owner.start(2)
        assert enqueue(owner, command("active"), h, "active") == "QUEUED"
        assert h.started.wait(2)
        assert enqueue(owner, command("waiting"), h, "place") == "QUEUED"
        assert put(owner, h, cancel(), "cancel") == "QUEUED"
        assert not owner.close(0.03)
        assert h.guard._phase == "active" and owner.status().thread_alive
        assert sorted(h.finalized) == ["cancel", "place"]
        assert put(owner, h, cancel("closed"), "closed") == "CLOSING"
    finally:
        h.release.set()
        assert owner.close(2)
    assert sorted(h.finalized) == ["active", "cancel", "closed", "place"]
    assert h.guard._phase == "idle"


def test_due_maintenance_failure_drains_both_queues(tmp_path):
    h = Harness(tmp_path)
    h.block = True
    first = threading.Event()
    calls = []

    def maintain():
        calls.append(threading.get_ident())
        if len(calls) == 1:
            first.set()
        else:
            raise RuntimeError("synthetic maintenance failure")

    owner = owner_for(with_cancel(h, maintain=maintain, interval=0.02))
    try:
        assert owner.start(2) and first.wait(2)
        assert enqueue(owner, command("active"), h, "active") == "QUEUED"
        assert h.started.wait(2)
        assert enqueue(owner, command("waiting"), h, "place") == "QUEUED"
        assert put(owner, h, cancel(), "cancel") == "QUEUED"
        threading.Event().wait(0.04)
        h.release.set()
        wait_for(lambda: owner.status().failure_category == "maintenance_failed")
        wait_for(lambda: len(h.finalized) == 3)
        assert h.dispatched == ["active"]
        assert sorted(h.finalized) == ["active", "cancel", "place"]
        assert set(calls) == {h.threads[0][1]}
    finally:
        h.release.set()
        assert owner.close(2)


def test_close_does_not_interrupt_active_cancel_or_release_guard(tmp_path):
    h = Harness(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def active():
        entered.set()
        assert release.wait(3)

    owner = owner_for(with_cancel(h, cancel_call=active))
    try:
        assert owner.start(2)
        assert put(owner, h, cancel(), "active") == "QUEUED" and entered.wait(2)
        assert not owner.close(0.03)
        assert owner.status().thread_alive and h.guard._phase == "active"
        assert h.finalized == []
    finally:
        release.set()
        assert owner.close(2)
    assert h.finalized == ["active"] and h.guard._phase == "idle"


def test_unsupported_and_mismatched_resources_fail_closed(tmp_path):
    h = Harness(tmp_path)
    owner = owner_for(h.factory)
    try:
        assert owner.start(2)
        assert put(owner, h, cancel(), "unsupported") == "UNSUPPORTED"
        assert h.finalized == ["unsupported"] and owner.status().queue_depth == 0
    finally:
        assert owner.close(2)

    other_path = tmp_path / "other"
    other_path.mkdir()
    other = Harness(other_path)
    bad = owner_for(with_cancel(other, journal_dispatcher=object()))
    try:
        assert not bad.start(2)
        assert bad.status().failure_category == "factory_failed"
    finally:
        assert bad.close(2) and other.cleanup.is_set()


@pytest.mark.parametrize("value", [0, -1, 10001, True, 1.0])
def test_cancel_capacity_validation(tmp_path, value):
    h = Harness(tmp_path)
    with pytest.raises(GatewayDirectOwnerError, match="INVALID_CONFIGURATION"):
        owner_for(h.factory, cancel_queue_size=value)


def test_process_boundary_precedes_cancel_finalizer_and_lock(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    owner = owner_for(h.factory)
    monkeypatch.setattr(os, "getpid", lambda: owner._pid + 1)
    with pytest.raises(GatewayDirectOwnerError, match="PROCESS_BOUNDARY"):
        put(owner, h, cancel(), "untouched")
    assert not h.finalized
