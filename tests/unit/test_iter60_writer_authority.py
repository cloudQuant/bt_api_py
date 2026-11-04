"""Cross-host writer authority (iteration 60, G1): lease, fence and takeover rules.

The authority runs in-process here with an injected monotonic clock, so lease
expiry and grace are exercised deterministically; owners reach it over real
loopback TCP with HMAC-authenticated frames. Two handles with different host
ids stand for two hosts: they share nothing but the authority. Provider
revocation evidence is a fake confirmed by a fake verifier; no provider or
real account is contacted.
"""

from __future__ import annotations

import socket
import time

import pytest

from bt_api_py.account_coordination import AccountCoordinationBinding
from bt_api_py.account_owner_handoff import HandoffError
from bt_api_py.account_writer_authority import (
    AuthorityHostedHandoff,
    RemoteAccountOwnerHandoff,
    WriterAuthorityServer,
    WriterLeasePolicy,
    _canonical,
    _HEADER,
    _mac,
)
from bt_api_py.provider_session_revocation import ProviderSessionRevocationEvidence

BINDING = AccountCoordinationBinding("synthetic-account", "fake:route-1")
SECRET = b"s" * 32
POLICY = WriterLeasePolicy(ttl_seconds=10.0, grace_seconds=4.0, max_dispatch_seconds=2.0)
SECOND = 1_000_000_000


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000 * SECOND

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += int(seconds * SECOND)


class Verifier:
    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.calls = []

    def verify_session_revocation(self, evidence) -> bool:
        self.calls.append(evidence)
        return self.answer


@pytest.fixture
def authority(tmp_path):
    clock = FakeClock()
    verifier = Verifier()
    servers = []

    def start(root=tmp_path / "authority"):
        server = WriterAuthorityServer(
            root,
            clients={"host-a": SECRET, "host-b": SECRET, "host-c": b"c" * 32},
            policy=POLICY,
            revocation_verifier=verifier,
            monotonic_ns=clock,
        )
        server.start()
        servers.append(server)
        return server

    server = start()
    yield server, clock, verifier, start
    for item in servers:
        item.stop()


def _owner(server, host, **kwargs):
    return RemoteAccountOwnerHandoff(
        server.address,
        client_id=host,
        secret=kwargs.pop("secret", SECRET),
        binding=BINDING,
        initial_owner="owner",
        host_id=host,
        **kwargs,
    )


def _evidence(**overrides):
    values = dict(
        account_ref=BINDING.account_ref,
        provider_route=BINDING.provider_route,
        revoked_owner_generation=1,
        revoked_session_ref="session-a",
        kind="PROVIDER_LOGOUT_RECEIPT",
        observed_at_ns=time.time_ns(),
        evidence_ref="logout-receipt-1",
    )
    values.update(overrides)
    return ProviderSessionRevocationEvidence(**values)


def test_only_one_of_two_hosts_holds_the_writer_lease(authority):
    server, _clock, _verifier, _start = authority
    a, b = _owner(server, "host-a"), _owner(server, "host-b")
    try:
        a.hold_owner_lock("owner", provider_session_ref="session-a")
        with pytest.raises(HandoffError) as held:
            b.hold_owner_lock("owner", provider_session_ref="session-b")
        assert held.value.code == "HANDOFF_WRITER_LEASE_HELD"
        assert a.holds_owner_lock == "owner" and b.holds_owner_lock is None
        assert a.may_dispatch("owner", 1) is True
        assert b.may_dispatch("owner", 1) is False
        lease = a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1")
        assert lease.finish("ACKNOWLEDGED") == "ACKNOWLEDGED"
        with pytest.raises(HandoffError) as refused:
            b.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-2")
        assert refused.value.code == "HANDOFF_WRITER_LEASE_NOT_HELD"
    finally:
        a.close()
        b.close()


def test_the_authority_refuses_a_dispatch_that_could_outlive_the_lease(authority):
    server, clock, _verifier, _start = authority
    a = _owner(server, "host-a")
    try:
        a.hold_owner_lock("owner", provider_session_ref="session-a")
        # Only the authority clock moves: the owner's local view still says
        # "live", the authority's own check refuses.
        clock.advance(POLICY.ttl_seconds - POLICY.max_dispatch_seconds + 0.001)
        with pytest.raises(HandoffError) as refused:
            a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1")
        assert refused.value.code == "HANDOFF_WRITER_LEASE_EXPIRING"
        clock.advance(POLICY.max_dispatch_seconds)
        with pytest.raises(HandoffError) as expired:
            a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1")
        assert expired.value.code == "HANDOFF_WRITER_LEASE_EXPIRED"
    finally:
        a.close()


def test_takeover_waits_for_grace_and_verified_revocation(authority):
    server, clock, verifier, _start = authority
    a, b = _owner(server, "host-a"), _owner(server, "host-b")
    try:
        a.hold_owner_lock("owner", provider_session_ref="session-a")
        inflight = a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1")
        # A is partitioned or paused from here on; it never renews.
        clock.advance(POLICY.ttl_seconds + POLICY.grace_seconds - 0.001)
        with pytest.raises(HandoffError) as held:
            b.hold_owner_lock("owner", provider_session_ref="session-b")
        assert held.value.code == "HANDOFF_WRITER_LEASE_HELD"
        clock.advance(0.002)
        b.hold_owner_lock("owner", provider_session_ref="session-b")
        assert b.writer_lease_state == "PENDING_REVOCATION"
        with pytest.raises(HandoffError) as pending:
            b.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-2")
        assert pending.value.code == "HANDOFF_NO_GO_PREVIOUS_SESSION_NOT_REVOKED"

        for bad, code in (
            (_evidence(revoked_session_ref="session-x"), "HANDOFF_REVOCATION_NOT_BOUND"),
            (_evidence(revoked_owner_generation=2), "HANDOFF_REVOCATION_NOT_BOUND"),
            (_evidence(provider_route="fake:route-2"), "HANDOFF_REVOCATION_NOT_BOUND"),
            (_evidence(observed_at_ns=1), "HANDOFF_REVOCATION_EVIDENCE_STALE"),
        ):
            with pytest.raises(HandoffError) as refused:
                b.confirm_previous_session_revoked(bad)
            assert refused.value.code == code
        verifier.answer = False
        with pytest.raises(HandoffError) as unconfirmed:
            b.confirm_previous_session_revoked(_evidence())
        assert unconfirmed.value.code == "HANDOFF_NO_GO_REVOCATION_NOT_CONFIRMED"
        verifier.answer = True
        b.confirm_previous_session_revoked(_evidence())
        assert b.writer_lease_state == "ACTIVE"
        b.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-2").finish(
            "ACKNOWLEDGED"
        )

        # The old holder can neither renew nor start a write, and its late
        # outcome does not resolve the orphaned write.
        with pytest.raises(HandoffError) as stale:
            a._call("renew_writer_lease", owner_id="owner", holder_token=a._holder_token)
        assert stale.value.code == "HANDOFF_WRITER_LEASE_NOT_HELD"
        with pytest.raises(HandoffError):
            a._call(
                "acquire_dispatch",
                owner_id="owner",
                generation=1,
                holder_token=a._holder_token,
                request_id="late",
                operation="submit",
                intent_id="i-3",
            )
        assert inflight.finish("ACKNOWLEDGED") == "UNKNOWN"
        (orphan,) = b.unresolved_dispatches()
        assert (orphan.intent_id, orphan.state, orphan.reason, orphan.late_outcome) == (
            "i-1",
            "UNKNOWN",
            "WRITER_LEASE_EXPIRED_ORPHAN",
            "ACKNOWLEDGED",
        )
        with pytest.raises(HandoffError) as unresolved:
            b.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1")
        assert unresolved.value.code == "HANDOFF_DISPATCH_INTENT_UNRESOLVED"
    finally:
        a.close()
        b.close()


def test_clean_release_hands_over_without_revocation(authority):
    server, _clock, verifier, _start = authority
    a, b = _owner(server, "host-a"), _owner(server, "host-b")
    try:
        a.hold_owner_lock("owner", provider_session_ref="session-a")
        a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1").finish(
            "ACKNOWLEDGED"
        )
        a.release_owner_lock()
        b.hold_owner_lock("owner", provider_session_ref="session-b")
        assert b.writer_lease_state == "ACTIVE"
        assert b.may_dispatch("owner", 1) is True
        assert verifier.calls == []
    finally:
        a.close()
        b.close()


def test_release_with_a_write_in_flight_is_unclean(authority):
    server, _clock, _verifier, _start = authority
    a, b = _owner(server, "host-a"), _owner(server, "host-b")
    try:
        a.hold_owner_lock("owner", provider_session_ref="session-a")
        lease = a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1")
        a.release_owner_lock()
        b.hold_owner_lock("owner", provider_session_ref="session-b")
        assert b.writer_lease_state == "PENDING_REVOCATION"
        assert lease.finish("ACKNOWLEDGED") == "UNKNOWN"
        (item,) = b.unresolved_dispatches()
        assert item.reason == "WRITER_RELEASED_INFLIGHT"
    finally:
        a.close()
        b.close()


def test_the_same_holder_resumes_after_its_own_expiry_when_nobody_took_it(authority):
    server, clock, _verifier, _start = authority
    a = _owner(server, "host-a")
    try:
        a.hold_owner_lock("owner", provider_session_ref="session-a")
        clock.advance(POLICY.ttl_seconds + 1)
        a.renew_writer_lease()
        assert a.writer_lease_state == "ACTIVE"
        a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1").finish(
            "ACKNOWLEDGED"
        )
    finally:
        a.close()


def test_an_authority_restart_rearms_a_full_ttl(authority, tmp_path):
    server, clock, _verifier, start = authority
    a = _owner(server, "host-a")
    a.hold_owner_lock("owner", provider_session_ref="session-a")
    clock.advance(POLICY.ttl_seconds - 0.5)
    server.stop()
    restarted = start()
    b = _owner(restarted, "host-b")
    try:
        clock.advance(POLICY.ttl_seconds + POLICY.grace_seconds - 0.5)
        with pytest.raises(HandoffError) as held:
            b.hold_owner_lock("owner", provider_session_ref="session-b")
        assert held.value.code == "HANDOFF_WRITER_LEASE_HELD"
        clock.advance(1.0)
        b.hold_owner_lock("owner", provider_session_ref="session-b")
        assert b.writer_lease_state == "PENDING_REVOCATION"
    finally:
        b.close()


def test_a_lost_dispatch_reply_retry_returns_the_same_lease_or_not_sent(tmp_path):
    clock = FakeClock()
    engine = AuthorityHostedHandoff(
        tmp_path / "store.sqlite", BINDING, initial_owner="owner", policy=POLICY,
        monotonic_ns=clock,
    )
    try:
        engine.acquire_writer_lease(
            "owner", holder_token="t-a", host_id="host-a", provider_session_ref="session-a"
        )
        args = dict(holder_token="t-a", request_id="r-1", operation="submit", intent_id="i-1")
        first = engine.acquire_dispatch_as("owner", 1, **args)
        assert engine.acquire_dispatch_as("owner", 1, **args) == first
        with pytest.raises(HandoffError) as conflict:
            engine.acquire_dispatch_as("owner", 1, **{**args, "intent_id": "i-2"})
        assert conflict.value.code == "HANDOFF_DISPATCH_REQUEST_CONFLICT"
        second = engine.acquire_dispatch_as("owner", 1, **{**args, "request_id": "r-2",
                                                            "intent_id": "i-2"})
        clock.advance(POLICY.ttl_seconds)
        with pytest.raises(HandoffError) as late:
            engine.acquire_dispatch_as("owner", 1, **{**args, "request_id": "r-2",
                                                       "intent_id": "i-2"})
        assert late.value.code == "HANDOFF_WRITER_LEASE_EXPIRED"
        states = {
            lease_id: state
            for lease_id, state in engine._db.execute(
                "SELECT lease_id, state FROM dispatch_lease"
            )
        }
        assert states == {first["lease_id"]: "IN_FLIGHT", second["lease_id"]: "NOT_SENT"}
    finally:
        engine.close()


def test_handoff_stages_run_on_the_authority_and_block_dispatch(authority):
    server, _clock, verifier, _start = authority
    a, b = _owner(server, "host-a"), _owner(server, "host-b")
    try:
        a.hold_owner_lock("owner", provider_session_ref="session-a")
        b.begin(from_owner="owner", to_owner="owner-b")
        assert a.stage == "QUIESCE"
        with pytest.raises(HandoffError) as fenced:
            a.acquire_dispatch_lease("owner", 1, operation="submit", intent_id="i-1")
        assert fenced.value.code == "HANDOFF_DISPATCH_FENCED"
        with pytest.raises(HandoffError) as no_evidence:
            b.fence(to_owner="owner-b", evidence=None, drain_timeout=0)
        assert no_evidence.value.code == "HANDOFF_NO_GO_OLD_SESSION_NOT_REVOKED"
        b.fence(to_owner="owner-b", evidence=_evidence(), drain_timeout=0)
        assert verifier.calls and b.stage == "FENCE"
        b.restore(to_owner="owner-b", ledger_cut="cut-1")
        b.sync(to_owner="owner-b", provider_snapshot_ref="snapshot-1")
        with pytest.raises(HandoffError) as blocked:
            b.reconcile(to_owner="owner-b", reconciliation_state="BALANCED")
        assert blocked.value.code == "HANDOFF_NO_GO_RECOVERY_UNVERIFIED"
    finally:
        a.close()
        b.close()


def test_requests_are_authenticated_and_not_replayable(authority):
    server, _clock, _verifier, _start = authority
    with pytest.raises(HandoffError) as wrong_key:
        _owner(server, "host-a", secret=b"x" * 32)
    assert wrong_key.value.code == "HANDOFF_AUTHORITY_UNAVAILABLE"
    with pytest.raises(HandoffError) as skewed:
        _owner(server, "host-a", wall_ns=lambda: time.time_ns() + 120 * SECOND)
    assert skewed.value.code == "HANDOFF_AUTHORITY_REQUEST_STALE"

    body = {
        "v": 1,
        "client": "host-a",
        "nonce": "n" * 32,
        "ts_ns": time.time_ns(),
        "op": "status",
        "binding": {"account_ref": "synthetic-account", "provider_route": "fake:route-1"},
        "args": {},
    }
    frame = _canonical({**body, "mac": _mac(SECRET, b"req", body)})
    _owner(server, "host-a").close()  # creates the account store
    with socket.create_connection(server.address, timeout=5) as sock:
        replies = []
        for _ in range(2):
            sock.sendall(_HEADER.pack(len(frame)) + frame)
            (size,) = _HEADER.unpack(sock.recv(_HEADER.size))
            data = b""
            while len(data) < size:
                data += sock.recv(size - len(data))
            replies.append(data)
    import json

    first, second = (json.loads(item) for item in replies)
    assert first["ok"] is True
    assert (second["ok"], second["code"]) == (False, "HANDOFF_AUTHORITY_REQUEST_REPLAYED")


def test_a_client_restricted_to_other_bindings_is_denied(tmp_path):
    server = WriterAuthorityServer(
        tmp_path / "authority",
        clients={"host-a": SECRET},
        policy=POLICY,
        allowed_bindings={"host-a": frozenset({"0" * 64})},
    )
    server.start()
    try:
        with pytest.raises(HandoffError) as denied:
            _owner(server, "host-a")
        assert denied.value.code == "HANDOFF_AUTHORITY_BINDING_DENIED"
    finally:
        server.stop()


def test_policy_requires_grace_to_cover_a_dispatch():
    with pytest.raises(HandoffError):
        WriterLeasePolicy(ttl_seconds=10, grace_seconds=1, max_dispatch_seconds=2)
    with pytest.raises(HandoffError):
        WriterLeasePolicy(ttl_seconds=2, grace_seconds=3, max_dispatch_seconds=2)
