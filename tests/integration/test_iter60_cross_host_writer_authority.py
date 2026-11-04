"""Controlled multi-host fault injection for the writer authority (iteration 60, G1).

Every "host" is its own spawned process with its own host id and no shared
local state; the only shared component is the authority process, reached over
loopback TCP. Host A's link runs through a fault-injecting proxy that can
black-hole traffic (a network partition: requests neither arrive nor fail
fast). Covered: concurrent claims, partition of the holder, crash of the
holder mid-write, and crash/restart of the authority. Provider revocation is a
fake confirmed by the authority's fake verifier; no provider, real account or
remote host is contacted (INTEGRATION_SIM).
"""

from __future__ import annotations

import multiprocessing
import os
import select
import socket
import threading
import time

import pytest

from bt_api_py.account_coordination import AccountCoordinationBinding
from bt_api_py.account_owner_handoff import HandoffError
from bt_api_py.account_writer_authority import (
    RemoteAccountOwnerHandoff,
    WriterAuthorityServer,
    WriterLeasePolicy,
)
from bt_api_py.provider_session_revocation import ProviderSessionRevocationEvidence

SECRET = b"k" * 32
ACCOUNT = ("synthetic-account", "fake:route-1")
TTL, GRACE, MAX_DISPATCH = 1.5, 1.0, 0.5
CTX = multiprocessing.get_context("spawn")


class AcceptingVerifier:
    """Stands in for a deployment verifier of provider-side revocation."""

    def verify_session_revocation(self, evidence) -> bool:
        return evidence.kind == "PROVIDER_LOGOUT_RECEIPT"


def _authority_main(root, port, address_queue, stop_event):
    server = WriterAuthorityServer(
        root,
        clients={"host-a": SECRET, "host-b": SECRET},
        policy=WriterLeasePolicy(
            ttl_seconds=TTL, grace_seconds=GRACE, max_dispatch_seconds=MAX_DISPATCH
        ),
        revocation_verifier=AcceptingVerifier(),
        port=port,
    )
    address_queue.put(server.start())
    stop_event.wait(120)
    server.stop()


def _handle(address, host, timeout=0.3):
    return RemoteAccountOwnerHandoff(
        tuple(address),
        client_id=host,
        secret=SECRET,
        binding=AccountCoordinationBinding(*ACCOUNT),
        initial_owner="owner",
        host_id=host,
        request_timeout=timeout,
    )


def _claim_main(address, host, ready, start, done, results):
    handle = _handle(address, host, timeout=5.0)
    try:
        ready.put(host)
        start.wait(15)
        try:
            handle.hold_owner_lock("owner", provider_session_ref=f"session-{host}")
        except HandoffError as error:
            results.put((host, error.code))
            return
        results.put((host, handle.may_dispatch("owner", 1)))
        done.wait(15)  # keep the lease until both claims were decided
    finally:
        handle.close()


def _partitioned_holder_main(address, ready, partitioned, healed, results):
    handle = _handle(address, "host-a")
    handle.hold_owner_lock("owner", provider_session_ref="session-host-a")
    inflight = handle.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1")
    ready.set()
    partitioned.wait(15)
    attempts = []
    deadline = time.monotonic() + TTL + GRACE + 0.5
    while time.monotonic() < deadline:
        try:
            handle.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="a-new")
            attempts.append("GRANTED")
        except HandoffError as error:
            attempts.append(error.code)
        time.sleep(0.1)
    healed.wait(15)
    try:
        handle.renew_writer_lease()
        renew = "RENEWED"
    except HandoffError as error:
        renew = error.code
    late = inflight.finish("ACKNOWLEDGED")
    results.put({"attempts": attempts, "renew": renew, "late": late})


def _crashing_holder_main(address, ready):
    handle = _handle(address, "host-a", timeout=5.0)
    handle.hold_owner_lock("owner", provider_session_ref="session-host-a")
    handle.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1")
    ready.set()
    time.sleep(0.2)
    os._exit(17)  # dies mid-write: no finish, no release


class BlackholeProxy:
    """TCP proxy that can drop every byte in both directions (a partition)."""

    def __init__(self, target):
        self._target = tuple(target)
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.address = self._listener.getsockname()
        self.blackhole = threading.Event()
        self._closed = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._closed.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._pipe, args=(client,), daemon=True).start()

    def _pipe(self, client):
        try:
            upstream = socket.create_connection(self._target, timeout=5)
        except OSError:
            client.close()
            return
        pair = {client: upstream, upstream: client}
        try:
            while not self._closed.is_set():
                readable, _, _ = select.select(list(pair), [], [], 0.05)
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    if not self.blackhole.is_set():
                        pair[sock].sendall(data)
        except OSError:
            return
        finally:
            client.close()
            upstream.close()

    def close(self):
        self._closed.set()
        self._listener.close()


@pytest.fixture
def authority_process(tmp_path):
    started = []

    def start(port=0):
        address_queue, stop = CTX.Queue(), CTX.Event()
        process = CTX.Process(
            target=_authority_main,
            args=(str(tmp_path / "authority"), port, address_queue, stop),
            daemon=True,
        )
        process.start()
        address = address_queue.get(timeout=30)
        started.append((process, stop))
        return process, stop, address

    yield start
    for process, stop in started:
        # Never notify the Event of a killed child: multiprocessing's
        # Condition.notify_all waits for the dead sleeper and deadlocks.
        if process.is_alive():
            stop.set()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()


def _evidence(session_ref):
    return ProviderSessionRevocationEvidence(
        account_ref=ACCOUNT[0],
        provider_route=ACCOUNT[1],
        revoked_owner_generation=1,
        revoked_session_ref=session_ref,
        kind="PROVIDER_LOGOUT_RECEIPT",
        observed_at_ns=time.time_ns(),
        evidence_ref="logout-receipt",
    )


def test_two_hosts_claiming_at_once_admit_exactly_one_writer(authority_process):
    _process, _stop, address = authority_process()
    ready, start, done, results = CTX.Queue(), CTX.Event(), CTX.Event(), CTX.Queue()
    owners = [
        CTX.Process(
            target=_claim_main, args=(address, host, ready, start, done, results), daemon=True
        )
        for host in ("host-a", "host-b")
    ]
    for process in owners:
        process.start()
    assert {ready.get(timeout=30) for _ in owners} == {"host-a", "host-b"}
    start.set()
    outcomes = dict(results.get(timeout=30) for _ in owners)
    done.set()
    for process in owners:
        process.join(timeout=30)
    assert sorted(outcomes.values(), key=str) == sorted(
        [True, "HANDOFF_WRITER_LEASE_HELD"], key=str
    )


def test_a_partitioned_holder_cannot_write_and_takeover_needs_revocation(authority_process):
    _process, _stop, address = authority_process()
    proxy = BlackholeProxy(address)
    ready, partitioned, healed, results = CTX.Event(), CTX.Event(), CTX.Event(), CTX.Queue()
    holder = CTX.Process(
        target=_partitioned_holder_main,
        args=(proxy.address, ready, partitioned, healed, results),
        daemon=True,
    )
    holder.start()
    b = RemoteAccountOwnerHandoff(
        tuple(address),
        client_id="host-b",
        secret=SECRET,
        binding=AccountCoordinationBinding(*ACCOUNT),
        initial_owner="owner",
        host_id="host-b",
        request_timeout=5.0,
        renew_interval=0.3,
    )
    try:
        assert ready.wait(30)
        proxy.blackhole.set()
        partitioned.set()
        with pytest.raises(HandoffError) as held:
            b.hold_owner_lock("owner", provider_session_ref="session-host-b")
        assert held.value.code == "HANDOFF_WRITER_LEASE_HELD"
        time.sleep(TTL + GRACE + 0.2)
        b.hold_owner_lock("owner", provider_session_ref="session-host-b")
        assert b.writer_lease_state == "PENDING_REVOCATION"
        with pytest.raises(HandoffError) as pending:
            b.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="b-1")
        assert pending.value.code == "HANDOFF_NO_GO_PREVIOUS_SESSION_NOT_REVOKED"
        b.confirm_previous_session_revoked(_evidence("session-host-a"))
        b.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="b-1").finish(
            "ACKNOWLEDGED"
        )
        proxy.blackhole.clear()
        healed.set()
        report = results.get(timeout=30)
    finally:
        healed.set()
        holder.join(timeout=30)
        proxy.close()
    # While partitioned, A was never granted a write.
    assert report["attempts"] and "GRANTED" not in report["attempts"]
    assert set(report["attempts"]) <= {
        "HANDOFF_AUTHORITY_UNAVAILABLE",
        "HANDOFF_WRITER_LEASE_NOT_HELD",
    }
    assert report["renew"] == "HANDOFF_WRITER_LEASE_NOT_HELD"
    assert report["late"] == "UNKNOWN"
    try:
        (orphan,) = b.unresolved_dispatches()
        assert (orphan.intent_id, orphan.reason, orphan.late_outcome) == (
            "i-1",
            "WRITER_LEASE_EXPIRED_ORPHAN",
            "ACKNOWLEDGED",
        )
        with pytest.raises(HandoffError) as unresolved:
            b.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1")
        assert unresolved.value.code == "HANDOFF_DISPATCH_INTENT_UNRESOLVED"
    finally:
        b.close()


def test_a_crashed_holder_mid_write_leaves_an_unknown_write_for_the_next_host(
    authority_process,
):
    _process, _stop, address = authority_process()
    ready = CTX.Event()
    holder = CTX.Process(target=_crashing_holder_main, args=(address, ready), daemon=True)
    holder.start()
    assert ready.wait(30)
    holder.join(timeout=30)
    assert holder.exitcode == 17
    b = _handle(address, "host-b", timeout=5.0)
    try:
        with pytest.raises(HandoffError):
            b.hold_owner_lock("owner", provider_session_ref="session-host-b")
        time.sleep(TTL + GRACE + 0.2)
        b.hold_owner_lock("owner", provider_session_ref="session-host-b")
        assert b.writer_lease_state == "PENDING_REVOCATION"
        (orphan,) = b.unresolved_dispatches()
        assert (orphan.intent_id, orphan.state, orphan.reason) == (
            "i-1",
            "UNKNOWN",
            "WRITER_LEASE_EXPIRED_ORPHAN",
        )
    finally:
        b.close()


def test_an_authority_crash_stops_all_writes_and_restart_keeps_the_holder(authority_process):
    process, _stop, address = authority_process()
    a = _handle(address, "host-a", timeout=0.5)
    a.hold_owner_lock("owner", provider_session_ref="session-host-a")
    a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1").finish(
        "ACKNOWLEDGED"
    )
    process.kill()
    process.join(timeout=10)
    with pytest.raises(HandoffError) as down:
        a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-2")
    assert down.value.code == "HANDOFF_AUTHORITY_UNAVAILABLE"
    _process2, _stop2, address2 = authority_process(port=address[1])
    assert tuple(address2) == tuple(address)
    b = _handle(address, "host-b", timeout=5.0)
    try:
        with pytest.raises(HandoffError) as held:
            b.hold_owner_lock("owner", provider_session_ref="session-host-b")
        assert held.value.code == "HANDOFF_WRITER_LEASE_HELD"
        a.renew_writer_lease()
        a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-2").finish(
            "ACKNOWLEDGED"
        )
        assert b.unresolved_dispatches() == ()
    finally:
        a.close()
        b.close()
