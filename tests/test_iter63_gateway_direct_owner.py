"""Single native owner queue; no provider or account calls."""

import os
import threading
import time
from contextlib import contextmanager

import pytest
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_direct_owner import (
    GatewayDirectOwner,
    GatewayDirectOwnerError,
    GatewayDirectOwnerResources,
    GatewayDispatchCommand,
    GatewayOwnerStartupCleanupError,
)


def command(key="key", digest="a" * 64):
    return GatewayDispatchCommand("strategy", key, "principal", "reservation", digest, 1)


@contextmanager
def fence(_row):
    yield


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            pytest.fail("timed out waiting for state")
        threading.Event().wait(0.005)


class Harness:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.root.joinpath("guards").mkdir()
        self.started, self.release, self.finished, self.cleanup = (
            threading.Event() for _ in range(4)
        )
        self.factory_ready, self.factory_release = threading.Event(), threading.Event()
        self.factory_release.set()
        self.block = False
        self.fail_cleanup = 0
        self.threads = []
        self.dispatched = []
        self.finalized = []
        self.guard = self.journal = self.dispatcher = None
        self.lock = threading.Lock()

    def factory(self):
        self.threads.append(("factory", threading.get_ident()))
        self.guard = LocalAccountSessionGuard(self.root / "guards", "account", "owner")
        generation = self.guard.acquire()
        self.journal = GatewayDirectCommandJournal.create(
            self.root / "journal", "account", generation=generation, clock=lambda: 100.0
        )
        self.dispatcher = GatewayDirectDispatcher(
            self.journal, "SYN", self.guard, lambda *_: None, lambda _: ("key",), lambda _: True
        )
        self.dispatcher.dispatch = self.dispatch
        self.factory_ready.set()
        assert self.factory_release.wait(5)
        return GatewayDirectOwnerResources(self.dispatcher, self.journal, self.close)

    def dispatch(self, strategy, key, principal, reservation_id, digest, version, *, request_fence):
        self.threads.append(("dispatch", threading.get_ident()))
        with request_fence(key):
            self.dispatched.append(key)
        self.started.set()
        if self.block:
            assert self.release.wait(5)
        self.finished.set()

    def close(self):
        self.threads.append(("close", threading.get_ident()))
        if self.fail_cleanup:
            self.fail_cleanup -= 1
            raise OSError("do not expose details")
        self.guard.release()
        self.cleanup.set()

    def finalize(self, name):
        def run():
            with self.lock:
                self.finalized.append(name)

        return run


def create(h, **overrides):
    config = {"queue_size": 3, "max_queue_wait_seconds": 2, "busy_timeout_seconds": 1}
    config.update(overrides)
    return GatewayDirectOwner(h.factory, **config)


def enqueue(owner, cmd, h, label, wait=None):
    return owner.enqueue(
        cmd, request_fence=fence, finalize=h.finalize(label), queue_wait_seconds=wait
    )


def test_factory_dispatch_cleanup_same_thread_and_binding(tmp_path):
    h = Harness(tmp_path)
    owner = create(h)
    assert owner.start(2)
    binding = owner.binding()
    assert (binding.account_scope, binding.journal_id, binding.generation) == (
        h.journal.account_scope,
        h.journal.journal_id,
        h.journal.generation,
    )
    assert owner.is_bound_to(h.journal) and not owner.is_bound_to(object())
    assert enqueue(owner, command(), h, "task") == "QUEUED"
    assert h.finished.wait(2)
    wait_for(lambda: h.finalized == ["task"])
    assert owner.close(2) and h.cleanup.is_set()
    assert {ident for _, ident in h.threads} == {h.threads[0][1]}
    assert h.threads[0][1] != threading.get_ident()
    assert not owner.start(1) and owner.binding() is None


def test_duplicate_conflict_capacity_and_nonblocking_close_during_active(tmp_path):
    h = Harness(tmp_path)
    h.block = True
    owner = create(h, queue_size=1, busy_timeout_seconds=2)
    assert owner.start(2)
    assert enqueue(owner, command(), h, "active") == "QUEUED" and h.started.wait(2)
    assert enqueue(owner, command(), h, "duplicate") == "DUPLICATE"
    assert enqueue(owner, command(digest="b" * 64), h, "conflict") == "CONFLICT"
    assert enqueue(owner, command("queued"), h, "queued") == "QUEUED"
    assert enqueue(owner, command("full"), h, "full") == "FULL"
    assert not owner.close(0.05)
    assert h.guard._phase == "active" and owner.status().thread_alive
    assert set(h.finalized) == {"duplicate", "conflict", "queued", "full"}
    h.release.set()
    assert owner.close(2) and h.guard._phase == "idle"
    assert sorted(h.finalized) == sorted(["active", "duplicate", "conflict", "queued", "full"])
    assert h.dispatched == ["key"]


def test_queue_expiry_drops_pending_and_finalizes(tmp_path):
    h = Harness(tmp_path)
    h.block = True
    owner = create(h, busy_timeout_seconds=2)
    assert owner.start(2)
    row = h.journal.reserve("strategy", "expires", "principal", {"symbol": "X"})
    expiring = GatewayDispatchCommand(
        row.strategy_scope,
        row.request_key,
        row.principal,
        row.reservation_id,
        row.digest,
        row.version,
    )
    assert enqueue(owner, command(), h, "active") == "QUEUED" and h.started.wait(2)
    assert enqueue(owner, expiring, h, "expired", wait=0.03) == "QUEUED"
    threading.Event().wait(0.06)
    h.release.set()
    wait_for(lambda: len(h.finalized) == 2)
    assert h.dispatched == ["key"] and "expired" in h.finalized
    assert h.journal.get("strategy", "expires", "principal").status == "RESERVED"
    assert owner.close(2)


def test_real_dispatcher_uses_request_fence_and_durable_journal(tmp_path):
    h = Harness(tmp_path)
    entries, sent = [], []

    def factory():
        bundle = h.factory()
        del bundle.dispatcher.dispatch

        def native(payload, port, scope):
            identity = NativeOrderIdentity("SYN", "ref", {"trading_day": "20300101"}, 1)
            marker = port.commit(
                PreDispatchRecord(
                    WriteOperation.PLACE,
                    scope,
                    "SYN",
                    "X",
                    identity,
                    None,
                    payload,
                    "20300101",
                    1,
                    "synthetic",
                )
            )
            sent.append(marker)
            return NativeDispatchOutcome(True, identity, marker)

        from iter63_claim_support import SyntheticClaimPort, claiming

        bundle.dispatcher._native_place = claiming(native)
        bundle.dispatcher._claim_port = SyntheticClaimPort()
        return bundle

    owner = GatewayDirectOwner(
        factory, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert owner.start(2)
    row = h.journal.reserve("strategy", "real", "principal", {"symbol": "X"})
    cmd = GatewayDispatchCommand(
        row.strategy_scope,
        row.request_key,
        row.principal,
        row.reservation_id,
        row.digest,
        row.version,
    )

    @contextmanager
    def counted(row):
        entries.append(("enter", row.request_key))
        yield lambda: entries.append(("recheck", row.request_key))
        entries.append(("exit", row.request_key))

    assert owner.enqueue(cmd, request_fence=counted, finalize=h.finalize("real")) == "QUEUED"
    wait_for(lambda: h.finalized == ["real"])
    assert h.journal.get("strategy", "real", "principal").status == "SENT"
    assert sent == [row.reservation_id]
    # Decision B: initial check, claim validation (before the marker), commit.
    kinds = [kind for kind, _ in entries]
    assert kinds.count("enter") == 3 == kinds.count("exit"), kinds
    assert kinds.count("recheck") >= 2, kinds
    assert owner.close(2)


def test_busy_rejects_new_without_interrupting_active(tmp_path):
    h = Harness(tmp_path)
    h.block = True
    owner = create(h, busy_timeout_seconds=0.03)
    assert owner.start(2)
    assert enqueue(owner, command(), h, "active") == "QUEUED" and h.started.wait(2)
    threading.Event().wait(0.06)
    assert not owner.status().accepting and owner.status().running
    assert enqueue(owner, command("new"), h, "busy") == "BUSY"
    assert h.guard._phase == "active" and h.finalized == ["busy"]
    h.release.set()
    assert owner.close(2)


def test_concurrent_enqueue_respects_capacity_and_finalizes_every_permit(tmp_path):
    h = Harness(tmp_path)
    h.block = True
    owner = create(h, queue_size=3, busy_timeout_seconds=2)
    assert owner.start(2)
    assert enqueue(owner, command(), h, "active") == "QUEUED" and h.started.wait(2)
    barrier = threading.Barrier(11)
    results = []

    def put(index):
        barrier.wait()
        result = enqueue(owner, command(f"key-{index}"), h, f"p-{index}")
        with h.lock:
            results.append(result)

    threads = [threading.Thread(target=put, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(2)
    assert results.count("QUEUED") == 3 and results.count("FULL") == 7
    assert owner.status().queue_depth == 3
    assert not owner.status().accepting
    assert not owner.close(0.03)
    h.release.set()
    assert owner.close(2)
    assert len(h.finalized) == 11 and len(set(h.finalized)) == 11


def test_cleanup_failure_keeps_guard_until_explicit_retry(tmp_path):
    h = Harness(tmp_path)
    h.fail_cleanup = 1
    owner = create(h)
    assert owner.start(2)
    assert not owner.close(0.05)
    assert owner.status().failure_category == "cleanup_failed"
    assert owner.status().thread_alive and h.guard._phase == "active"
    assert owner.close(2) and h.guard._phase == "idle"
    assert [name for name, _ in h.threads].count("close") == 2


def test_factory_timeout_late_resources_cleaned_on_owner_thread(tmp_path):
    h = Harness(tmp_path)
    h.factory_release.clear()
    owner = create(h)
    assert not owner.start(0.05) and h.factory_ready.is_set()
    assert h.guard._phase == "active" and owner.status().thread_alive
    h.factory_release.set()
    assert owner.close(2) and h.cleanup.is_set()
    assert {tid for _, tid in h.threads} == {h.threads[0][1]}
    assert not owner.start(1)


def test_mismatched_dispatcher_journal_fails_closed_and_cleans(tmp_path):
    h = Harness(tmp_path)

    def bad_factory():
        bundle = h.factory()
        return GatewayDirectOwnerResources(bundle.dispatcher, object(), bundle.close)

    owner = GatewayDirectOwner(
        bad_factory, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert not owner.start(2)
    assert owner.close(2) and h.cleanup.is_set() and owner.binding() is None
    assert owner.status().failure_category == "factory_failed"


def test_precreated_other_thread_resources_are_rejected_and_cleaned(tmp_path):
    h = Harness(tmp_path)
    bundle = h.factory()  # This guard and dispatcher belong to this test thread.
    cleanup_requested, cleanup_done = threading.Event(), threading.Event()

    def cleanup_on_original_owner():
        cleanup_requested.set()
        assert cleanup_done.wait(3)

    def factory():
        return GatewayDirectOwnerResources(
            bundle.dispatcher, bundle.journal, cleanup_on_original_owner
        )

    owner = GatewayDirectOwner(
        factory, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert not owner.start(2)
    assert cleanup_requested.wait(2) and owner.binding() is None
    assert not owner.close(0.03) and h.guard._phase == "active"
    h.close()
    cleanup_done.set()
    assert owner.close(2) and h.guard._phase == "idle"
    assert owner.status().failure_category == "factory_failed"


def test_partial_factory_failure_cleanup_is_owned_and_retried(tmp_path):
    h = Harness(tmp_path)
    h.fail_cleanup = 1

    def failing_factory():
        bundle = h.factory()
        raise GatewayOwnerStartupCleanupError(bundle.close)

    owner = GatewayDirectOwner(
        failing_factory, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert not owner.start(2)
    wait_for(lambda: owner.status().failure_category == "cleanup_failed")
    assert owner.status().thread_alive and h.guard._phase == "active"
    assert owner.close(2) and h.guard._phase == "idle"
    assert {ident for _, ident in h.threads} == {h.threads[0][1]}
    assert not owner.start(1)


def test_unknown_factory_failure_requires_trusted_cleanup_transfer(tmp_path):
    h = Harness(tmp_path)

    def failing_factory():
        h.factory()
        raise RuntimeError("private details")

    owner = GatewayDirectOwner(
        failing_factory, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert not owner.start(2)
    wait_for(lambda: owner.status().failure_category == "cleanup_unknown")
    assert not owner.close(0.03) and owner.status().thread_alive and h.guard._phase == "active"
    owner.provide_startup_cleanup(h.close)
    assert owner.close(2) and h.guard._phase == "idle"
    assert {ident for _, ident in h.threads} == {h.threads[0][1]}


def test_factory_system_exit_keeps_unknown_custody(tmp_path):
    h = Harness(tmp_path)

    def failing_factory():
        h.factory()
        raise SystemExit("hidden")

    owner = GatewayDirectOwner(
        failing_factory, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert not owner.start(2)
    wait_for(lambda: owner.status().failure_category == "cleanup_unknown")
    assert not owner.close(0.03) and owner.status().thread_alive and h.guard._phase == "active"
    owner.provide_startup_cleanup(h.close)
    assert owner.close(2) and h.guard._phase == "idle"


@pytest.mark.parametrize("interrupt", [SystemExit, KeyboardInterrupt])
def test_dispatch_and_finalizer_base_exception_do_not_kill_owner(tmp_path, interrupt):
    h = Harness(tmp_path)

    def factory():
        bundle = h.factory()

        def broken(*args, **kwargs):
            raise interrupt("hidden")

        bundle.dispatcher.dispatch = broken
        return bundle

    def broken_finalizer():
        raise interrupt("hidden")

    owner = GatewayDirectOwner(
        factory, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert owner.start(2)
    assert owner.enqueue(command(), request_fence=fence, finalize=broken_finalizer) == "QUEUED"
    wait_for(lambda: owner.status().failure_category == "finalizer_failed")
    assert owner.status().thread_alive and owner.close(2) and h.guard._phase == "idle"


@pytest.mark.parametrize("interrupt", [SystemExit, KeyboardInterrupt])
def test_cleanup_base_exception_stays_retryable(tmp_path, interrupt):
    h = Harness(tmp_path)
    attempts = []

    def factory():
        bundle = h.factory()

        def cleanup():
            attempts.append(1)
            if len(attempts) == 1:
                raise interrupt("hidden")
            bundle.close()

        return GatewayDirectOwnerResources(bundle.dispatcher, bundle.journal, cleanup)

    owner = GatewayDirectOwner(
        factory, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert owner.start(2)
    assert not owner.close(0.03)
    assert owner.status().failure_category == "cleanup_failed" and h.guard._phase == "active"
    assert owner.close(2) and len(attempts) == 2 and h.guard._phase == "idle"


def test_process_boundary_rejected_before_touching_lock(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    owner = create(h)

    class LockBomb:
        def __enter__(self):
            pytest.fail("lock touched")

        def __exit__(self, *_):
            pass

    owner._condition = LockBomb()
    monkeypatch.setattr(os, "getpid", lambda: owner._pid + 1)
    actions = [
        lambda: owner.start(1),
        lambda: owner.close(1),
        owner.status,
        owner.binding,
        lambda: owner.is_bound_to(None),
        lambda: enqueue(owner, command(), h, "x"),
    ]
    for action in actions:
        with pytest.raises(GatewayDirectOwnerError, match="PROCESS_BOUNDARY"):
            action()
    assert not h.finalized


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_forked_process_cannot_use_inherited_owner(tmp_path):
    h = Harness(tmp_path)
    owner = create(h)
    reader, writer = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(reader)
        try:
            owner.status()
        except GatewayDirectOwnerError as exc:
            os.write(writer, exc.code.encode("ascii"))
        else:
            os.write(writer, b"unexpected-success")
        finally:
            os.close(writer)
            os._exit(0)
    os.close(writer)
    try:
        assert os.read(reader, 128) == b"PROCESS_BOUNDARY"
    finally:
        os.close(reader)
        _, status = os.waitpid(child, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def test_busy_claim_is_requeued_after_service_work_then_dispatched(tmp_path):
    """Decision B: a covered writer waiting for the owner must not deadlock it."""
    from bt_api_gateway.direct_dispatch import DirectDispatchError

    h = Harness(tmp_path)
    state = {"writer_done": False, "attempts": 0}

    def dispatch(strategy, key, principal, reservation_id, digest, version, *, request_fence):
        state["attempts"] += 1
        if not state["writer_done"]:
            # The writer's change only completes on this owner thread.
            raise DirectDispatchError("DISPATCH_CLAIM_BUSY")
        h.dispatched.append(key)
        h.finished.set()

    owner = create(h)
    assert owner.start(2)
    h.dispatcher.dispatch = dispatch
    assert enqueue(owner, command(), h, "task") == "QUEUED"
    handle = owner.submit_service_call(lambda: state.__setitem__("writer_done", True))
    assert handle.result(timeout_seconds=2) is None
    assert h.finished.wait(2)
    wait_for(lambda: h.finalized == ["task"])
    assert h.dispatched == ["key"] and state["attempts"] >= 2
    assert owner.status().thread_alive
    assert owner.close(2)


def test_busy_claim_past_deadline_is_finalized_without_dispatch(tmp_path):
    from bt_api_gateway.direct_dispatch import DirectDispatchError

    h = Harness(tmp_path)
    attempts = []

    def dispatch(*_args, **_kwargs):
        attempts.append(1)
        raise DirectDispatchError("DISPATCH_CLAIM_BUSY")

    owner = create(h)
    assert owner.start(2)
    h.dispatcher.dispatch = dispatch
    assert enqueue(owner, command(), h, "task", wait=0.2) == "QUEUED"
    wait_for(lambda: h.finalized == ["task"])
    assert len(attempts) >= 2 and h.dispatched == []
    assert owner.close(2)


def test_owner_side_dispatch_refusal_is_logged_with_its_code(tmp_path, caplog):
    # SimNow 63.09 (I63-E103): a dispatch refused on the owner after QUEUED
    # (e.g. MARKET_GATE_*) left no trace while the command stayed RESERVED.
    import logging

    h = Harness(tmp_path)

    class RefusedError(RuntimeError):
        code = "MARKET_GATE_STALE"

    def factory():
        bundle = h.factory()

        def refused(*args, **kwargs):
            raise RefusedError("account 12345678")

        bundle.dispatcher.dispatch = refused
        return bundle

    owner = GatewayDirectOwner(
        factory, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert owner.start(2)
    with caplog.at_level(logging.WARNING, logger="bt_api_py.gateway_direct_owner"):
        assert owner.enqueue(command(), request_fence=fence, finalize=h.finalize("one")) == "QUEUED"
        wait_for(lambda: owner.status().failure_category == "dispatch_failed")
    assert "Gateway owner dispatch not completed: RefusedError code=MARKET_GATE_STALE" in caplog.text
    assert "12345678" not in caplog.text
    assert owner.status().thread_alive and owner.close(2)

