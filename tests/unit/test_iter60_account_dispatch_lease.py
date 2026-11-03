"""Iteration 60, step 60.10 (re-check G1): dispatch leases and the takeover exit.

A provider write holds a shared ``IN_FLIGHT`` lease bound to the account,
owner and generation for the whole provider call. QUIESCE is the exclusive
side: after it commits no lease of the old generation is granted, and FENCE
waits (bounded) for granted leases or records them ``UNKNOWN``. The provider
here is an in-memory fake driven by barriers and events; processes share only
a temporary SQLite file and a temporary host registry.
"""

from __future__ import annotations

import multiprocessing
import threading
import time

import pytest

from bt_api_py.account_coordination import (
    AccountCoordinationBinding,
    HostAccountRegistry,
    canonical_coordination_path,
)
from bt_api_py.account_owner_handoff import AccountOwnerHandoff, HandoffError
from bt_api_py.provider_session_revocation import ProviderSessionRevocationEvidence

ACCOUNT = "account:lease"
ROUTE = "ctp:fake-front-lease"
BINDING = AccountCoordinationBinding(ACCOUNT, ROUTE)
OLD, NEW = "owner-old", "owner-new"
SESSION = "fake-session-old"


class _Revoked:
    def verify_session_revocation(self, evidence):
        return True


def _evidence():
    return ProviderSessionRevocationEvidence(
        ACCOUNT, ROUTE, 1, SESSION, "PROVIDER_LOGOUT_RECEIPT", time.time_ns(), "logout-1"
    )


def _open(path, registry_root, *, owner=OLD):
    return AccountOwnerHandoff(
        path,
        ACCOUNT,
        initial_owner=owner,
        binding=BINDING,
        revocation_verifier=_Revoked(),
        host_registry=HostAccountRegistry(registry_root),
    )


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "owner.sqlite3", tmp_path / "host"


@pytest.fixture
def owner(paths):
    handoff = _open(*paths)
    handoff.hold_owner_lock(OLD, provider_session_ref=SESSION)
    yield handoff
    handoff.close()


@pytest.fixture
def controller(paths, owner):
    handoff = _open(*paths)
    yield handoff
    handoff.close()


class _FakeProvider:
    """Records the order in which writes start and end."""

    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def write(self, intent, *, entered=None, release=None):
        with self.lock:
            self.events.append(("start", intent))
        if entered is not None:
            entered.set()
        if release is not None:
            assert release.wait(10)
        with self.lock:
            self.events.append(("end", intent))


def _leased_write(handoff, provider, intent, **kwargs):
    lease = handoff.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id=intent)
    with lease:
        provider.write(intent, **kwargs)
        lease.finish("ACKNOWLEDGED")
    return lease


# -- in-process interleavings ------------------------------------------------------


def test_quiesce_blocks_new_writes_and_fence_waits_for_the_inflight_one(owner, controller):
    provider = _FakeProvider()
    entered, release = threading.Event(), threading.Event()
    first = threading.Thread(
        target=_leased_write,
        args=(owner, provider, "intent-1"),
        kwargs={"entered": entered, "release": release},
    )
    first.start()
    assert entered.wait(10)  # the provider call is running under its lease

    controller.begin(from_owner=OLD, to_owner=NEW)
    provider.events.append(("quiesce", None))
    with pytest.raises(HandoffError, match="HANDOFF_DISPATCH_FENCED"):
        _leased_write(owner, provider, "intent-2")

    fenced = threading.Event()

    def take_fence():
        controller.fence(to_owner=NEW, evidence=_evidence(), drain_timeout=10)
        fenced.set()

    fencer = threading.Thread(target=take_fence)
    fencer.start()
    assert not fenced.wait(0.2)  # the exclusive side waits for the shared holder
    release.set()
    first.join(10)
    fencer.join(10)
    assert fenced.is_set()
    assert provider.events == [
        ("start", "intent-1"),
        ("quiesce", None),
        ("end", "intent-1"),
    ]
    detail = dict(controller.audit())["FENCE"]
    assert detail["unknown_inflight"] == []
    assert dict(controller.audit())["QUIESCE"]["inflight_at_quiesce"] == 1
    assert controller.unresolved_dispatches() == ()


def test_fence_drain_timeout_records_the_inflight_write_unknown(owner, controller):
    provider = _FakeProvider()
    entered, release = threading.Event(), threading.Event()
    writer = threading.Thread(
        target=_leased_write,
        args=(owner, provider, "intent-slow"),
        kwargs={"entered": entered, "release": release},
    )
    writer.start()
    assert entered.wait(10)
    controller.begin(from_owner=OLD, to_owner=NEW)
    controller.fence(to_owner=NEW, evidence=_evidence(), drain_timeout=0.05)
    assert dict(controller.audit())["FENCE"]["unknown_inflight"] == ["intent-slow"]
    release.set()
    writer.join(10)
    # The old owner's late report does not resolve the write.
    (unresolved,) = controller.unresolved_dispatches()
    assert (unresolved.intent_id, unresolved.state, unresolved.reason) == (
        "intent-slow",
        "UNKNOWN",
        "FENCE_DRAIN_TIMEOUT",
    )
    assert unresolved.late_outcome == "ACKNOWLEDGED"
    # UNKNOWN is carried into the new owner's recovery and blocks promotion.
    controller.restore(to_owner=NEW, ledger_cut="cut-1")
    assert dict(controller.audit())["RESTORE"]["carried_unknown_intents"] == ["intent-slow"]
    controller.sync(to_owner=NEW, provider_snapshot_ref="snap-1")
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_UNKNOWN_INFLIGHT"):
        controller.reconcile(to_owner=NEW, reconciliation_state="BALANCED")
    assert controller.stage == "SYNC"


def test_no_lease_is_granted_after_quiesce_commits(owner, controller):
    """Race a lease loop against QUIESCE; every grant precedes the high-water mark."""

    start = threading.Barrier(2)
    granted, refused = [], []

    def lease_loop():
        start.wait()
        for index in range(10_000):
            try:
                lease = owner.acquire_dispatch_lease(
                    OLD, 1, operation="submit", intent_id=f"race-{index}"
                )
            except HandoffError as error:
                refused.append(error.code)
                return
            granted.append(lease.lease_id)
            lease.finish("ACKNOWLEDGED")
            time.sleep(0.0005)  # a provider round trip; SQLite locks are not fair

    loop = threading.Thread(target=lease_loop)
    loop.start()
    start.wait()
    time.sleep(0.02)
    controller.begin(from_owner=OLD, to_owner=NEW)
    loop.join(30)
    high_water = dict(controller.audit())["QUIESCE"]["lease_high_water"]
    assert refused == ["HANDOFF_DISPATCH_FENCED"]
    assert granted and max(granted) <= high_water


def test_same_submit_intent_is_never_dispatched_twice(owner):
    lease = owner.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="i-unknown")
    lease.finish("UNKNOWN")
    with pytest.raises(HandoffError, match="HANDOFF_DISPATCH_INTENT_UNRESOLVED"):
        owner.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="i-unknown")
    owner.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="i-ack").finish(
        "ACKNOWLEDGED"
    )
    with pytest.raises(HandoffError, match="HANDOFF_DISPATCH_INTENT_ALREADY_SENT"):
        owner.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="i-ack")
    owner.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="i-local").finish("NOT_SENT")
    owner.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="i-local").finish(
        "ACKNOWLEDGED"
    )
    # A cancel may be retried after an UNKNOWN, but not run twice at once.
    owner.acquire_dispatch_lease(OLD, 1, operation="cancel", intent_id="c-1").finish("UNKNOWN")
    running = owner.acquire_dispatch_lease(OLD, 1, operation="cancel", intent_id="c-1")
    with pytest.raises(HandoffError, match="HANDOFF_DISPATCH_INTENT_IN_FLIGHT"):
        owner.acquire_dispatch_lease(OLD, 1, operation="cancel", intent_id="c-1")
    running.finish("ACKNOWLEDGED")
    assert [item.intent_id for item in owner.unresolved_dispatches()] == ["i-unknown", "c-1"]


def test_leaving_a_lease_unfinished_records_unknown(owner):
    with pytest.raises(RuntimeError):
        with owner.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="boom"):
            raise RuntimeError("provider transport failed mid-call")
    (unresolved,) = owner.unresolved_dispatches()
    assert (unresolved.intent_id, unresolved.state) == ("boom", "UNKNOWN")


def test_lease_requires_the_host_owner_lock(paths, owner):
    other = _open(*paths)
    try:
        assert not other.may_dispatch(OLD, 1)
        with pytest.raises(HandoffError, match="HANDOFF_HOST_OWNER_LOCK_NOT_HELD"):
            other.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="x")
        with pytest.raises(HandoffError, match="HANDOFF_HOST_OWNER_LOCK_HELD"):
            other.hold_owner_lock(OLD, provider_session_ref="another")
        owner.release_owner_lock()
        other.hold_owner_lock(OLD, provider_session_ref="restarted")
        assert other.may_dispatch(OLD, 1) and not owner.may_dispatch(OLD, 1)
    finally:
        other.close()


def test_crashed_owner_leases_become_unknown_when_the_lock_is_retaken(paths, owner):
    owner.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="orphan")
    owner.close()  # process dies without finishing the provider call
    restarted = _open(*paths)
    try:
        restarted.hold_owner_lock(OLD, provider_session_ref="session-2")
        (unresolved,) = restarted.unresolved_dispatches()
        assert (unresolved.intent_id, unresolved.reason) == ("orphan", "OWNER_LOCK_ORPHAN")
        with pytest.raises(HandoffError, match="HANDOFF_DISPATCH_INTENT_UNRESOLVED"):
            restarted.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="orphan")
    finally:
        restarted.close()


# -- coordinator identity binding ----------------------------------------------------


def test_second_coordination_database_for_the_same_account_is_refused(tmp_path):
    registry = tmp_path / "host"
    first = _open(tmp_path / "a.sqlite3", registry)
    try:
        with pytest.raises(HandoffError, match="HANDOFF_COORDINATION_STORE_CONFLICT"):
            _open(tmp_path / "b.sqlite3", registry)
        _open(tmp_path / "a.sqlite3", registry).close()  # the registered one reopens
    finally:
        first.close()


def test_database_bound_to_another_route_is_refused(tmp_path):
    path = tmp_path / "owner.sqlite3"
    _open(path, tmp_path / "host-1").close()
    with pytest.raises(HandoffError, match="HANDOFF_COORDINATOR_BINDING_MISMATCH"):
        AccountOwnerHandoff(
            path,
            ACCOUNT,
            initial_owner=OLD,
            binding=AccountCoordinationBinding(ACCOUNT, "ctp:another-front"),
            host_registry=HostAccountRegistry(tmp_path / "host-2"),
        )


def test_binding_must_name_the_handoff_account(tmp_path):
    with pytest.raises(HandoffError, match="HANDOFF_BINDING_ACCOUNT_MISMATCH"):
        AccountOwnerHandoff(
            tmp_path / "owner.sqlite3",
            "account:other",
            initial_owner=OLD,
            binding=BINDING,
            host_registry=HostAccountRegistry(tmp_path / "host"),
        )


def test_canonical_location_is_derived_from_the_binding(tmp_path):
    root = tmp_path / "coordination"
    path = canonical_coordination_path(root, BINDING)
    assert path == canonical_coordination_path(root, AccountCoordinationBinding(ACCOUNT, ROUTE))
    assert path != canonical_coordination_path(root, AccountCoordinationBinding(ACCOUNT, "x"))
    handoff = AccountOwnerHandoff.open_canonical(
        root, BINDING, initial_owner=OLD, host_registry=HostAccountRegistry(tmp_path / "host")
    )
    try:
        assert path.exists() and handoff.binding == BINDING
    finally:
        handoff.close()


# -- two processes sharing the coordination database -------------------------------


def _owner_process(path, registry, commands, replies):
    handoff = _open(path, registry)
    try:
        handoff.hold_owner_lock(OLD, provider_session_ref=SESSION)
        provider_calls = []
        lease = handoff.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="p-1")
        provider_calls.append("p-1")  # provider write starts under the lease
        replies.put(("in_flight", provider_calls[:]))
        while True:
            command = commands.get(timeout=30)
            if command == "second":
                try:
                    second = handoff.acquire_dispatch_lease(
                        OLD, 1, operation="submit", intent_id="p-2"
                    )
                except HandoffError as error:
                    replies.put(("second", error.code, provider_calls[:]))
                else:
                    provider_calls.append("p-2")
                    second.finish("ACKNOWLEDGED")
                    replies.put(("second", "granted", provider_calls[:]))
            elif command == "finish":
                replies.put(("finish", lease.finish("ACKNOWLEDGED")))
            elif command == "stop":
                return
    finally:
        handoff.close()


def _spawn_owner(path, registry):
    context = multiprocessing.get_context("spawn")
    commands, replies = context.Queue(), context.Queue()
    process = context.Process(
        target=_owner_process, args=(str(path), str(registry), commands, replies)
    )
    process.start()
    return process, commands, replies


def test_two_processes_quiesce_blocks_the_old_owner_and_fence_drains(paths):
    path, registry = paths
    process, commands, replies = _spawn_owner(path, registry)
    controller = None
    try:
        assert replies.get(timeout=60) == ("in_flight", ["p-1"])
        controller = _open(path, registry)
        with pytest.raises(HandoffError, match="HANDOFF_HOST_OWNER_LOCK_HELD"):
            controller.hold_owner_lock(OLD, provider_session_ref="intruder")
        controller.begin(from_owner=OLD, to_owner=NEW)
        commands.put("second")
        assert replies.get(timeout=30) == ("second", "HANDOFF_DISPATCH_FENCED", ["p-1"])
        commands.put("finish")
        assert replies.get(timeout=30) == ("finish", "ACKNOWLEDGED")
        controller.fence(to_owner=NEW, evidence=_evidence(), drain_timeout=10)
        assert dict(controller.audit())["FENCE"]["unknown_inflight"] == []
        commands.put("stop")
        process.join(30)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        if controller is not None:
            controller.close()


def test_two_processes_killed_owner_leaves_unknown_and_frees_the_host_lock(paths):
    path, registry = paths
    process, _commands, replies = _spawn_owner(path, registry)
    controller = None
    try:
        assert replies.get(timeout=60) == ("in_flight", ["p-1"])
        controller = _open(path, registry)
        controller.begin(from_owner=OLD, to_owner=NEW)
        process.terminate()  # the old owner dies with its provider call in flight
        process.join(30)
        controller.fence(to_owner=NEW, evidence=_evidence(), drain_timeout=0.05)
        assert dict(controller.audit())["FENCE"]["unknown_inflight"] == ["p-1"]
        assert [item.intent_id for item in controller.unresolved_dispatches()] == ["p-1"]
        # The OS released the dead owner's lock; still nobody may dispatch.
        controller.hold_owner_lock(OLD, provider_session_ref="restarted")
        assert not controller.may_dispatch(OLD, 1)
        with pytest.raises(HandoffError, match="HANDOFF_DISPATCH_FENCED"):
            controller.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="p-1")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        if controller is not None:
            controller.close()
