"""Synthetic tests for the disabled-by-default, in-process claim primitive."""

import threading
import time
import subprocess
import sys

import pytest

from bt_api_py import gateway_dispatch_claim as claim_module
from bt_api_py.gateway_dispatch_claim import (
    DispatchClaimError,
    DispatchCommandBinding,
    GatewayDispatchClaimCoordinator,
)


DOMAINS = ("registry", "market", "parent_facts", "control", "session_source", "risk")


def binding(**overrides):
    values = dict(account="account", owner_generation="g1", journal_id="journal",
                  strategy="strategy", principal="principal", run_id="run",
                  request_key="request", reservation_id="reservation",
                  payload_digest="a" * 64, native_identity_digest="b" * 64,
                  marker_ref="durable-marker")
    values.update(overrides)
    return DispatchCommandBinding(**values)


def ready(*, risk="NOT_SELECTED", **kwargs):
    coordinator = GatewayDispatchClaimCoordinator._for_test("account", "g1", **kwargs)
    for domain in DOMAINS:
        token = coordinator.begin_change(domain)
        coordinator.finish_change(token, risk if domain == "risk" else "READY")
    return coordinator


def prepare(coordinator, command=None, *, duration_ns=500_000_000):
    before = coordinator.capture()
    # The real adapter must validate all facts and persist the marker here.
    after = coordinator.capture()
    return coordinator.prepare(command or binding(), before, after,
                               deadline_ns=time.monotonic_ns() + duration_ns)


def test_production_cannot_enable_by_status_or_configuration():
    coordinator = GatewayDispatchClaimCoordinator("account", "g1")
    token = coordinator.begin_change("registry")
    with pytest.raises(DispatchClaimError, match="COVERAGE_UNAVAILABLE"):
        coordinator.finish_change(token, "READY")
    with pytest.raises(DispatchClaimError, match="COVERAGE_UNAVAILABLE"):
        coordinator.capture()
    with pytest.raises(TypeError):
        GatewayDispatchClaimCoordinator("account", "g1", production_enabled=True)


def test_every_domain_requires_explicit_readiness_and_risk_selection():
    coordinator = GatewayDispatchClaimCoordinator._for_test("account", "g1")
    for domain in DOMAINS[:-1]:
        token = coordinator.begin_change(domain)
        coordinator.finish_change(token, "READY")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    risk = coordinator.begin_change("risk")
    coordinator.finish_change(risk, "NOT_SELECTED")
    assert coordinator.capture()
    market = coordinator.begin_change("market")
    with pytest.raises(DispatchClaimError, match="INVALID_CHANGE"):
        coordinator.finish_change(market, "NOT_SELECTED")
    coordinator.poison(market)


def test_overlapping_mutations_remain_closed_and_disagreement_is_unavailable():
    coordinator = ready()
    first = coordinator.begin_change("session_source")
    second = coordinator.begin_change("session_source")
    coordinator.finish_change(first, "READY")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    coordinator.finish_change(second, "UNAVAILABLE")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    renewal = coordinator.begin_change("session_source")
    coordinator.finish_change(renewal, "READY")
    assert coordinator.capture()


def test_poison_and_failed_or_missing_finish_are_fail_closed():
    coordinator = ready()
    first = coordinator.begin_change("market")
    other = coordinator.begin_change("market")
    coordinator.poison(first)
    with pytest.raises(DispatchClaimError, match="INVALID_CHANGE"):
        coordinator.finish_change(other, "READY")
    with pytest.raises(DispatchClaimError, match="DOMAIN_POISONED"):
        coordinator.begin_change("market")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    next_coordinator = ready()
    next_coordinator.begin_change("control")  # an unfinished writer remains closed
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        next_coordinator.capture()


def test_external_poison_and_change_capacity_are_irreversible():
    coordinator = ready(max_snapshots=2)
    first = coordinator.begin_change("market")
    coordinator.begin_change("market")
    with pytest.raises(DispatchClaimError, match="CHANGE_CAPACITY"):
        coordinator.begin_change("market")
    with pytest.raises(DispatchClaimError, match="INVALID_CHANGE"):
        coordinator.finish_change(first, "READY")
    coordinator.poison_domain("registry")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()


def test_version_change_during_or_after_external_checks_is_never_aba():
    coordinator = ready()
    before = coordinator.capture()
    token = coordinator.begin_change("registry")
    coordinator.finish_change(token, "READY")
    after = coordinator.capture()
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        coordinator.prepare(binding(), before, after,
                            deadline_ns=time.monotonic_ns() + 200_000_000)
    candidate = prepare(coordinator)
    token = coordinator.begin_change("market")
    coordinator.finish_change(token, "READY")
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        coordinator.try_claim(candidate)


def test_before_after_sealed_identity_and_owner_scope_are_required():
    first, second = ready(), ready()
    before, after = first.capture(), first.capture()
    with pytest.raises(DispatchClaimError, match="OWNER_MISMATCH"):
        first.prepare(binding(owner_generation="g2"), before, after,
                      deadline_ns=time.monotonic_ns() + 200_000_000)
    before, after = first.capture(), first.capture()
    with pytest.raises(DispatchClaimError, match="INVALID_CANDIDATE"):
        second.prepare(binding(), before, after, deadline_ns=time.monotonic_ns() + 200_000_000)
    forged = claim_module._Snapshot(before.sequence, before.versions, before.captured_ns, before._seal)
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        first.prepare(binding(), forged, after, deadline_ns=time.monotonic_ns() + 200_000_000)
    # The lookalike must not consume the genuine `before` ticket it copied.
    first.discard_snapshot(before)
    assert first.matches_owner("account", "g1")
    assert not first.matches_owner("account", "g2")


def test_marker_command_identity_binding_and_bounded_expiry():
    coordinator = ready(max_commands=2, max_validity_ns=100_000_000)
    before, after = coordinator.capture(), coordinator.capture()
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        coordinator.prepare(binding(), before, after, deadline_ns=time.monotonic_ns() + 1_000_000_000)
    assert coordinator.capacity_status()["snapshots"] == 0
    with pytest.raises(DispatchClaimError, match="INVALID_SNAPSHOT"):
        coordinator.discard_snapshot(before)
    candidate = prepare(coordinator, duration_ns=10_000_000)
    time.sleep(.02)
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        coordinator.try_claim(candidate)
    with pytest.raises(DispatchClaimError, match="COMMAND_ALREADY_PREPARED"):
        prepare(coordinator, binding(payload_digest="c" * 64), duration_ns=50_000_000)


def test_revoke_before_claim_rejects_and_after_claim_does_not_wait_for_native():
    coordinator = ready()
    rejected = prepare(coordinator)
    change = coordinator.begin_change("registry")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.try_claim(rejected)
    coordinator.finish_change(change, "READY")
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        coordinator.try_claim(rejected)

    accepted = prepare(coordinator, binding(request_key="second"))
    native_entered, native_release, revoked = threading.Event(), threading.Event(), threading.Event()
    results = []

    def native():
        results.append(coordinator.try_claim(accepted))
        native_entered.set()
        assert native_release.wait(3)

    def revoke():
        token = coordinator.begin_change("registry")
        coordinator.finish_change(token, "UNAVAILABLE")
        revoked.set()

    worker = threading.Thread(target=native)
    worker.start()
    try:
        assert native_entered.wait(2)
        writer = threading.Thread(target=revoke)
        writer.start()
        assert revoked.wait(1), "coordinator lock was retained across synthetic native work"
        writer.join(1)
        assert not writer.is_alive()
        assert results[0].binding[6] == "second"
    finally:
        native_release.set()
        worker.join(2)
    assert not worker.is_alive()


def test_multiple_threads_can_claim_exact_command_only_once():
    coordinator = ready()
    candidate = prepare(coordinator)
    barrier = threading.Barrier(12)
    results, lock = [], threading.Lock()

    def run():
        barrier.wait(timeout=2)
        try:
            result = coordinator.try_claim(candidate)
        except DispatchClaimError as exc:
            result = exc.code
        with lock:
            results.append(result)

    workers = [threading.Thread(target=run) for _ in range(12)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(3)
    assert all(not worker.is_alive() for worker in workers)
    assert sum(isinstance(item, claim_module.DispatchClaim) for item in results) == 1
    assert results.count("ALREADY_CLAIMED") == 11


def test_forked_process_identity_is_checked_before_lock(monkeypatch):
    coordinator = ready()
    candidate = prepare(coordinator)
    monkeypatch.setattr(claim_module.os, "getpid", lambda: coordinator._pid + 1)
    with coordinator._lock:
        with pytest.raises(DispatchClaimError, match="COORDINATOR_PROCESS_CHANGED"):
            coordinator.try_claim(candidate)


def test_invalid_values_and_private_tokens_are_not_accepted():
    with pytest.raises(DispatchClaimError, match="INVALID_COMMAND_BINDING"):
        binding(child_id="child")
    with pytest.raises(DispatchClaimError, match="INVALID_COMMAND_BINDING"):
        binding(payload_digest="not-a-sha256")
    coordinator = ready()
    foreign = ready().begin_change("market")
    with pytest.raises(DispatchClaimError, match="INVALID_CHANGE"):
        coordinator.finish_change(foreign, "READY")
    with pytest.raises(DispatchClaimError, match="INVALID_CANDIDATE"):
        coordinator.try_claim(object())


def test_internal_copies_prevent_mutated_tokens_extending_or_rebinding_claim():
    coordinator = ready()
    before = coordinator.capture()
    changed = coordinator.begin_change("market")
    coordinator.finish_change(changed, "READY")
    after = coordinator.capture()
    object.__setattr__(before, "versions", after.versions)
    object.__setattr__(before, "captured_ns", time.monotonic_ns() + 10_000_000_000)
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        coordinator.prepare(binding(), before, after,
                            deadline_ns=time.monotonic_ns() + 100_000_000)

    candidate = prepare(coordinator, duration_ns=10_000_000)
    object.__setattr__(candidate, "binding", ("tampered",))
    object.__setattr__(candidate, "deadline_ns", time.monotonic_ns() + 10_000_000_000)
    time.sleep(.02)
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        coordinator.try_claim(candidate)


def test_tampered_binding_is_revalidated_before_lock_and_never_hashed():
    coordinator = ready()
    before, after = coordinator.capture(), coordinator.capture()
    command = binding()

    class Untrusted:
        def __hash__(self):
            raise AssertionError("untrusted object must not be hashed")

    object.__setattr__(command, "request_key", Untrusted())
    with pytest.raises(DispatchClaimError, match="INVALID_COMMAND_BINDING"):
        coordinator.prepare(command, before, after,
                            deadline_ns=time.monotonic_ns() + 100_000_000)


def test_command_capacity_never_evicts_a_claimed_identity():
    coordinator = ready(max_commands=1)
    coordinator.try_claim(prepare(coordinator))
    assert coordinator.capacity_status()["commands"] == 1
    assert coordinator.capacity_status()["max_commands"] == 1
    with pytest.raises(DispatchClaimError, match="COMMAND_CAPACITY"):
        prepare(coordinator, binding(request_key="another"))
    with pytest.raises(DispatchClaimError, match="COMMAND_ALREADY_PREPARED"):
        prepare(coordinator)


@pytest.mark.parametrize("failure", ["type", "binding", "owner", "deadline", "stale", "changing"])
def test_failed_prepare_consumes_owned_tickets_without_exhausting_capacity(failure):
    coordinator = ready(max_snapshots=2)
    before, after = coordinator.capture(), coordinator.capture()
    command = binding()
    deadline = time.monotonic_ns() + 200_000_000
    expected = {
        "type": "INVALID_CANDIDATE", "binding": "INVALID_COMMAND_BINDING",
        "owner": "OWNER_MISMATCH", "deadline": "CANDIDATE_STALE",
        "stale": "CANDIDATE_STALE", "changing": "DOMAIN_UNAVAILABLE",
    }[failure]
    change = None
    if failure == "type":
        command = object()
    elif failure == "binding":
        object.__setattr__(command, "request_key", object())
    elif failure == "owner":
        command = binding(owner_generation="other")
    elif failure == "deadline":
        deadline = time.monotonic_ns() - 1
    elif failure in {"stale", "changing"}:
        change = coordinator.begin_change("market")
        if failure == "stale":
            coordinator.finish_change(change, "READY")
            change = None

    with pytest.raises(DispatchClaimError, match=expected):
        coordinator.prepare(command, before, after, deadline_ns=deadline)
    assert coordinator.capacity_status()["snapshots"] == 0
    if change is not None:
        coordinator.finish_change(change, "READY")
    # Retrying requires a new pair and fresh verification; no stale evidence is reused.
    candidate = prepare(coordinator)
    assert coordinator.try_claim(candidate).binding[6] == "request"
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        coordinator.prepare(binding(request_key="other"), before, after,
                            deadline_ns=time.monotonic_ns() + 100_000_000)


def test_failed_prepare_cannot_consume_foreign_or_lookalike_tickets():
    first, second = ready(max_snapshots=2), ready(max_snapshots=2)
    owned, foreign = first.capture(), second.capture()
    with pytest.raises(DispatchClaimError, match="INVALID_CANDIDATE"):
        first.prepare(binding(), owned, foreign, deadline_ns=time.monotonic_ns() + 100_000_000)
    assert first.capacity_status()["snapshots"] == 0
    assert second.capacity_status()["snapshots"] == 1
    second.discard_snapshot(foreign)

    original, other = first.capture(), first.capture()
    forged = claim_module._Snapshot(original.sequence, original.versions,
                                    original.captured_ns, original._seal)
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        first.prepare(binding(), forged, other, deadline_ns=time.monotonic_ns() + 100_000_000)
    assert first.capacity_status()["snapshots"] == 1
    first.discard_snapshot(original)


def test_tampered_issued_ticket_is_released_by_identity_on_failure():
    coordinator = ready(max_snapshots=2)
    before, after = coordinator.capture(), coordinator.capture()
    object.__setattr__(before, "sequence", "not-an-integer")
    with pytest.raises(DispatchClaimError, match="INVALID_CANDIDATE"):
        coordinator.prepare(binding(), before, after,
                            deadline_ns=time.monotonic_ns() + 100_000_000)
    assert coordinator.capacity_status()["snapshots"] == 0


def test_discard_pair_only_releases_exact_owned_tickets():
    first, second = ready(max_snapshots=2), ready(max_snapshots=2)
    owned, foreign = first.capture(), second.capture()
    forged = claim_module._Snapshot(owned.sequence, owned.versions,
                                    owned.captured_ns, owned._seal)
    assert first.discard_pair(forged, foreign) == (False, False)
    assert first.capacity_status()["snapshots"] == 1
    assert second.capacity_status()["snapshots"] == 1
    assert first.discard_pair(owned, owned) == (True, False)
    assert first.discard_pair(None, owned) == (False, False)
    assert second.discard_pair(foreign, None) == (True, False)
    assert first.capacity_status()["snapshots"] == 0
    assert second.capacity_status()["snapshots"] == 0


def test_discard_pair_is_atomic_against_concurrent_prepare():
    coordinator = ready(max_snapshots=2)
    before, after = coordinator.capture(), coordinator.capture()
    barrier = threading.Barrier(2)
    results = []
    result_lock = threading.Lock()

    def prepare_in_thread():
        barrier.wait(timeout=2)
        try:
            result = coordinator.prepare(binding(), before, after,
                                         deadline_ns=time.monotonic_ns() + 500_000_000)
        except DispatchClaimError as exc:
            result = exc.code
        with result_lock:
            results.append(("prepare", result))

    def discard_in_thread():
        barrier.wait(timeout=2)
        result = coordinator.discard_pair(before, after)
        with result_lock:
            results.append(("discard", result))

    workers = [threading.Thread(target=prepare_in_thread),
               threading.Thread(target=discard_in_thread)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(3)
    assert all(not worker.is_alive() for worker in workers)
    observed = dict(results)
    if type(observed["prepare"]) is claim_module._Candidate:
        assert observed["discard"] == (False, False)
        coordinator.try_claim(observed["prepare"])
    else:
        assert observed == {"prepare": "CANDIDATE_STALE", "discard": (True, True)}
    assert coordinator.capacity_status()["snapshots"] == 0


def test_discard_pair_preserves_process_and_closed_guards(monkeypatch):
    coordinator = ready()
    before = coordinator.capture()
    actual_pid = coordinator._pid
    with monkeypatch.context() as context:
        context.setattr(claim_module.os, "getpid", lambda: actual_pid + 1)
        with coordinator._lock:
            with pytest.raises(DispatchClaimError, match="COORDINATOR_PROCESS_CHANGED"):
                coordinator.discard_pair(before, None)
    assert coordinator.capacity_status()["snapshots"] == 1
    coordinator.close()
    with pytest.raises(DispatchClaimError, match="COORDINATOR_CLOSED"):
        coordinator.discard_pair(before, None)


def test_duplicate_or_full_command_failure_also_releases_tickets():
    coordinator = ready(max_commands=1, max_snapshots=2)
    coordinator.try_claim(prepare(coordinator))
    for request_key, error in (("request", "COMMAND_ALREADY_PREPARED"),
                               ("another", "COMMAND_CAPACITY")):
        before, after = coordinator.capture(), coordinator.capture()
        with pytest.raises(DispatchClaimError, match=error):
            coordinator.prepare(binding(request_key=request_key), before, after,
                                deadline_ns=time.monotonic_ns() + 100_000_000)
        assert coordinator.capacity_status()["snapshots"] == 0


def test_only_one_concurrent_prepare_can_consume_same_pair():
    coordinator = ready(max_snapshots=2)
    before, after = coordinator.capture(), coordinator.capture()
    barrier = threading.Barrier(8)
    results, lock = [], threading.Lock()

    def run(index):
        barrier.wait(timeout=2)
        try:
            result = coordinator.prepare(binding(request_key=f"request-{index}"), before, after,
                                         deadline_ns=time.monotonic_ns() + 500_000_000)
        except DispatchClaimError as exc:
            result = exc.code
        with lock:
            results.append(result)

    workers = [threading.Thread(target=run, args=(index,)) for index in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(3)
    assert all(not worker.is_alive() for worker in workers)
    candidates = [result for result in results if type(result) is claim_module._Candidate]
    assert len(candidates) == 1
    assert results.count("CANDIDATE_STALE") == 7
    assert coordinator.capacity_status()["snapshots"] == 0
    coordinator.try_claim(candidates[0])


def test_source_change_after_ticket_consumption_before_prepare_is_rejected(monkeypatch):
    coordinator = ready(max_snapshots=2)
    before, after = coordinator.capture(), coordinator.capture()
    command = binding()
    paused, resume = threading.Event(), threading.Event()
    original_values = DispatchCommandBinding._values
    result = []

    def pause_outside_lock(self):
        if self is command:
            paused.set()
            assert resume.wait(2)
        return original_values(self)

    monkeypatch.setattr(DispatchCommandBinding, "_values", pause_outside_lock)

    def run():
        try:
            coordinator.prepare(command, before, after,
                                deadline_ns=time.monotonic_ns() + 500_000_000)
        except DispatchClaimError as exc:
            result.append(exc.code)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert paused.wait(2)
        token = coordinator.begin_change("registry")
        coordinator.finish_change(token, "READY")
    finally:
        resume.set()
        worker.join(2)
    assert not worker.is_alive()
    assert result == ["CANDIDATE_STALE"]
    assert coordinator.capacity_status()["snapshots"] == 0


@pytest.mark.parametrize("claimed_before_exit", [False, True])
def test_process_loss_never_enables_new_owner_from_marker_only(tmp_path, claimed_before_exit):
    # This models an external durable marker; the core does not implement the
    # journal's UNKNOWN projection or prove the marker is authoritative.
    script = r"""
import os, sys, time
from bt_api_py.gateway_dispatch_claim import GatewayDispatchClaimCoordinator, DispatchCommandBinding
c = GatewayDispatchClaimCoordinator._for_test('account', 'g1')
for domain in ('registry', 'market', 'parent_facts', 'control', 'session_source', 'risk'):
    t = c.begin_change(domain)
    c.finish_change(t, 'NOT_SELECTED' if domain == 'risk' else 'READY')
before, after = c.capture(), c.capture()
b = DispatchCommandBinding('account','g1','journal','strategy','principal','run',
    'request','reservation','a'*64,'b'*64,'durable-marker')
candidate = c.prepare(b, before, after, deadline_ns=time.monotonic_ns()+500_000_000)
with open(sys.argv[1], 'w') as stream:
    stream.write('original-marker')
    stream.flush()
    os.fsync(stream.fileno())
if sys.argv[2] == 'yes':
    c.try_claim(candidate)
os._exit(17)
"""
    marker = tmp_path / "synthetic-marker"
    result = subprocess.run([sys.executable, "-c", script, str(marker),
                             "yes" if claimed_before_exit else "no"],
                            timeout=5, check=False, capture_output=True)
    assert result.returncode == 17, result.stderr.decode(errors="replace")
    assert marker.read_text() == "original-marker"
    restarted = GatewayDispatchClaimCoordinator("account", "g2")
    with pytest.raises(DispatchClaimError, match="COVERAGE_UNAVAILABLE"):
        restarted.capture()
