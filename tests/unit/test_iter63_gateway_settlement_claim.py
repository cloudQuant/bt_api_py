"""Synthetic claim model tests; no actual provider, account or I/O."""

import copy
import os
import select
import signal
import threading
import time
from dataclasses import replace

import pytest

from bt_api_base.claim_fence import ClaimFence

from bt_api_py.gateway_dispatch_claim import DispatchClaimError
from bt_api_py.gateway_settlement_claim import (
    GatewaySettlementClaimCoordinator,
    SettlementClaimBinding,
)

DOMAINS = ("write_admission", "sdk_source", "provider_session", "owner", "observation",
           "journal_marker")


def binding(**changes):
    base = SettlementClaimBinding("CTP", "account", "20261109", "observation",
                                  1, "owner", "journal", "marker")
    return replace(base, **changes)


def ready():
    c = GatewaySettlementClaimCoordinator._for_test("account", "owner")
    for domain in DOMAINS:
        token = c.begin_change(domain)
        c.finish_change(token, "READY")
    return c


def prepare(c, b=None):
    # In a real owner the original marker must already be durable before
    # capturing; this fixture models only the later in-memory version fence.
    before = c.capture()
    # Validate the existing marker, observation and grant between captures.
    after = c.capture()
    return c.prepare(b or binding(), before, after,
                     deadline_ns=time.monotonic_ns() + 500_000_000)


def test_production_has_no_configurable_authority():
    c = GatewaySettlementClaimCoordinator("account", "owner")
    token = c.begin_change("write_admission")
    with pytest.raises(DispatchClaimError, match="COVERAGE_UNAVAILABLE"):
        c.finish_change(token, "READY")
    with pytest.raises(DispatchClaimError, match="COVERAGE_UNAVAILABLE"):
        c.capture()
    with pytest.raises(TypeError):
        GatewaySettlementClaimCoordinator("account", "owner", production_enabled=True)


@pytest.mark.parametrize("domain", DOMAINS)
def test_invalidation_before_claim_rejects_even_if_source_recovers(domain):
    c = ready()
    candidate = prepare(c)
    token = c.begin_change(domain)
    with pytest.raises(DispatchClaimError):
        c.try_claim(candidate)
    c.finish_change(token, "READY")
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        c.try_claim(candidate)


def test_claim_then_revoke_finishes_while_native_is_blocked():
    c = ready()
    candidate = prepare(c)
    claimed, release = threading.Event(), threading.Event()
    outcome = []

    def native():
        outcome.append(c.try_claim(candidate))
        claimed.set()
        assert release.wait(3)

    thread = threading.Thread(target=native)
    thread.start()
    assert claimed.wait(1)
    token = c.begin_change("write_admission")
    c.finish_change(token, "UNAVAILABLE")
    marker_change = c.begin_change("journal_marker")
    c.finish_change(marker_change, "UNAVAILABLE")
    assert thread.is_alive()
    release.set()
    thread.join(2)
    assert not thread.is_alive() and len(outcome) == 1


def test_unique_day_and_original_identity_cannot_be_reprepared():
    c = ready()
    candidate = prepare(c)
    claim = c.try_claim(candidate)
    assert claim.binding[0] == "account"
    assert claim.binding[1] == "owner"
    assert claim.binding[2] == "journal"
    assert claim.binding[4] == "CTP"
    assert claim.binding[5] == "observation"
    assert claim.binding[6] == "20261109"
    assert claim.binding[7] == "1"
    assert claim.binding[10] == "marker"
    with pytest.raises(DispatchClaimError, match="ALREADY_CLAIMED"):
        c.try_claim(candidate)
    with pytest.raises(DispatchClaimError, match="COMMAND_ALREADY_PREPARED"):
        prepare(c, binding(observation_id="new-obs", session_generation=2,
                           marker_id="other-marker"))


def test_owner_and_binding_validation():
    c = ready()
    with pytest.raises(DispatchClaimError, match="OWNER_MISMATCH"):
        prepare(c, binding(owner_generation="other"))
    with pytest.raises(DispatchClaimError, match="OWNER_MISMATCH"):
        prepare(c, binding(account_binding="other"))
    for change in ({"trading_day": "20260230"}, {"session_generation": True},
                   {"marker_id": ""}):
        with pytest.raises(DispatchClaimError, match="INVALID_SETTLEMENT_BINDING"):
            binding(**change)


def test_missing_or_overlapping_source_publication_fails_closed():
    c = ready()
    a = c.begin_change("sdk_source")
    b = c.begin_change("sdk_source")
    c.finish_change(a, "READY")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        c.capture()
    c.finish_change(b, "UNAVAILABLE")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        c.capture()


def test_journal_marker_is_required_and_cannot_be_not_selected():
    c = GatewaySettlementClaimCoordinator._for_test("account", "owner")
    for domain in DOMAINS[:-1]:
        token = c.begin_change(domain)
        c.finish_change(token, "READY")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        c.capture()
    marker = c.begin_change("journal_marker")
    with pytest.raises(DispatchClaimError, match="INVALID_CHANGE"):
        c.finish_change(marker, "NOT_SELECTED")
    c.finish_change(marker, "READY")
    assert c.capture()


def test_observation_change_during_validation_cannot_be_prepared():
    c = ready()
    before = c.capture()
    changed = c.begin_change("observation")
    c.finish_change(changed, "READY")
    after = c.capture()
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        c.prepare(binding(), before, after,
                  deadline_ns=time.monotonic_ns() + 500_000_000)


def test_marker_must_precede_captures_and_later_change_invalidates_claim():
    c = ready()
    # This dict is a synthetic source; it does not establish SQLite durability,
    # journal identity, or coverage of actual marker writers.
    journal = {}
    write = c.begin_change("journal_marker")
    journal["20261109"] = "marker"
    c.finish_change(write, "READY")

    before = c.capture()
    assert journal.get("20261109") == binding().marker_id
    after = c.capture()
    candidate = c.prepare(binding(), before, after,
                          deadline_ns=time.monotonic_ns() + 500_000_000)
    write = c.begin_change("journal_marker")
    journal["20261109"] = "replaced"
    c.finish_change(write, "READY")
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        c.try_claim(candidate)


def test_marker_write_between_captures_cannot_prepare():
    c = ready()
    journal = {}
    before = c.capture()
    write = c.begin_change("journal_marker")
    journal["20261109"] = "marker"
    c.finish_change(write, "READY")
    after = c.capture()
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        c.prepare(binding(), before, after,
                  deadline_ns=time.monotonic_ns() + 500_000_000)


def test_expired_or_tampered_binding_cannot_claim():
    c = ready()
    before, after = c.capture(), c.capture()
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        c.prepare(binding(), before, after, deadline_ns=time.monotonic_ns() - 1)
    broken = binding()
    object.__setattr__(broken, "marker_id", "")
    with pytest.raises(DispatchClaimError, match="INVALID_SETTLEMENT_BINDING"):
        c.prepare(broken, before, after, deadline_ns=time.monotonic_ns() + 500_000_000)


def test_prevalidation_failure_releases_only_exact_owned_snapshots():
    c, foreign = ready(), ready()
    local = c.capture()
    unrelated = c.capture()
    outside = foreign.capture()
    with pytest.raises(DispatchClaimError, match="INVALID_SETTLEMENT_BINDING"):
        c.prepare(None, local, outside, deadline_ns=0)
    with pytest.raises(DispatchClaimError, match="INVALID_SNAPSHOT"):
        c.discard_snapshot(local)
    foreign.discard_snapshot(outside)
    c.discard_snapshot(unrelated)
    assert c.capacity_status()["snapshots"] == 0


def test_prevalidation_failure_keeps_original_error_and_does_not_consume_forgery():
    c = ready()
    real, other = c.capture(), c.capture()
    forged = copy.copy(real)
    with pytest.raises(DispatchClaimError, match="INVALID_SETTLEMENT_BINDING"):
        c.prepare(None, forged, other, deadline_ns=0)
    c.discard_snapshot(real)
    with pytest.raises(DispatchClaimError, match="INVALID_SNAPSHOT"):
        c.discard_snapshot(other)
    assert c.capacity_status()["snapshots"] == 0


def test_unexpected_external_failure_faults_raw_core(monkeypatch):
    c = ready()
    candidate = prepare(c)
    before, after = c.capture(), c.capture()
    broken = SettlementClaimBinding.__new__(SettlementClaimBinding)

    def fail_close():
        raise RuntimeError("injected close failure")

    monkeypatch.setattr(c._inner, "close", fail_close)
    with pytest.raises(AttributeError):
        c.prepare(broken, before, after, deadline_ns=0)
    with pytest.raises(DispatchClaimError, match="COORDINATOR_FAILED"):
        ClaimFence.try_claim(c._core, candidate)


def test_external_fact_change_must_be_active_before_operation():
    c = ready()
    candidate = prepare(c)
    active = c.begin_change("observation")
    # The changing source is fenced before the external action or its error.
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        ClaimFence.try_claim(c._core, candidate)
    c.poison(active)
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        ClaimFence.try_claim(c._core, candidate)


def test_failed_preparation_does_not_release_prepared_command():
    c = ready()
    candidate = prepare(c)
    before, after = c.capture(), c.capture()
    with pytest.raises(DispatchClaimError, match="INVALID_SETTLEMENT_BINDING"):
        c.prepare(None, before, after, deadline_ns=0)
    assert c.capacity_status()["snapshots"] == 0
    assert c.try_claim(candidate)


def test_external_validation_failure_can_release_captured_pair():
    c = ready()
    before, after = c.capture(), c.capture()
    with pytest.raises(RuntimeError, match="validation failed"):
        try:
            raise RuntimeError("validation failed")
        finally:
            assert c.discard_snapshots(before, after) == (True, True)
    assert c.capacity_status()["snapshots"] == 0


def inject_pair_failure(c, after, entered=None, release=None):
    class BrokenLookup(dict):
        def get(self, key, default=None):
            if key == id(after):
                if entered is not None:
                    entered.set()
                    assert release.wait(3)
                raise RuntimeError("injected cleanup failure")
            return super().get(key, default)

    c._core._snapshot_ids = BrokenLookup(c._core._snapshot_ids)


def test_cleanup_failure_does_not_mask_binding_failure():
    c = ready()
    before, after = c.capture(), c.capture()
    inject_pair_failure(c, after)
    with pytest.raises(DispatchClaimError, match="INVALID_SETTLEMENT_BINDING") as result:
        c.prepare(None, before, after, deadline_ns=0)
    assert result.value.settlement_cleanup_failed is True
    assert result.value.settlement_coordinator_closed is True
    with pytest.raises(DispatchClaimError, match="COORDINATOR_FAILED"):
        c.capture()


def test_cleanup_and_close_failure_is_reported_on_original_exception(monkeypatch):
    c = ready()
    candidate = prepare(c)
    before, after = c.capture(), c.capture()

    def fail(*args):
        raise RuntimeError("injected failure")

    inject_pair_failure(c, after)
    monkeypatch.setattr(c._inner, "close", fail)
    with pytest.raises(DispatchClaimError, match="INVALID_SETTLEMENT_BINDING") as result:
        c.prepare(None, before, after, deadline_ns=0)
    assert result.value.settlement_cleanup_failed is True
    assert result.value.settlement_coordinator_closed is False
    with pytest.raises(DispatchClaimError, match="COORDINATOR_FAILED"):
        c.try_claim(candidate)
    with pytest.raises(DispatchClaimError, match="COORDINATOR_FAILED"):
        ClaimFence.try_claim(c._core, candidate)
    with pytest.raises(DispatchClaimError, match="COORDINATOR_FAILED"):
        c.capture()


def test_cleanup_failure_linearizes_with_concurrent_claim(monkeypatch):
    c = ready()
    candidate = prepare(c)
    before, after = c.capture(), c.capture()
    entered, release = threading.Event(), threading.Event()
    errors = []

    def fail_close():
        raise RuntimeError("injected close failure")

    def cleanup():
        try:
            c.prepare(None, before, after, deadline_ns=0)
        except DispatchClaimError as exc:
            errors.append(("prepare", exc.code))

    def claim():
        try:
            ClaimFence.try_claim(c._core, candidate)
        except DispatchClaimError as exc:
            errors.append(("claim", exc.code))

    inject_pair_failure(c, after, entered, release)
    monkeypatch.setattr(c._inner, "close", fail_close)
    cleanup_thread = threading.Thread(target=cleanup)
    cleanup_thread.start()
    assert entered.wait(1)
    claim_thread = threading.Thread(target=claim)
    claim_thread.start()
    release.set()
    cleanup_thread.join(2)
    claim_thread.join(2)
    assert not cleanup_thread.is_alive() and not claim_thread.is_alive()
    assert sorted(errors) == [("claim", "COORDINATOR_FAILED"),
                              ("prepare", "INVALID_SETTLEMENT_BINDING")]


def test_claim_before_cleanup_failure_remains_in_flight(monkeypatch):
    c = ready()
    candidate = prepare(c)
    claimed = c.try_claim(candidate)
    before, after = c.capture(), c.capture()

    def fail(*args):
        raise RuntimeError("injected failure")

    inject_pair_failure(c, after)
    monkeypatch.setattr(c._inner, "close", fail)
    with pytest.raises(DispatchClaimError, match="INVALID_SETTLEMENT_BINDING"):
        c.prepare(None, before, after, deadline_ns=0)
    assert claimed.binding[10] == "marker"
    with pytest.raises(DispatchClaimError, match="COORDINATOR_FAILED"):
        c.try_claim(candidate)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork required")
def test_child_rejects_every_entry_before_inherited_locked_wrapper():
    c = ready()
    read_fd, write_fd = os.pipe()
    c._state_lock.acquire()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        operations = (
            lambda: c.begin_change("owner"),
            lambda: c.finish_change(None, "READY"),
            lambda: c.poison(None),
            c.capture,
            lambda: c.discard_snapshot(None),
            lambda: c.discard_snapshots(None, None),
            lambda: c.prepare(None, None, None, deadline_ns=0),
            lambda: c.try_claim(None),
            c.capacity_status,
            c.close,
        )
        try:
            for operation in operations:
                try:
                    operation()
                except DispatchClaimError as exc:
                    if exc.code != "COORDINATOR_PROCESS_CHANGED":
                        os.write(write_fd, b"wrong error")
                        os._exit(1)
                else:
                    os.write(write_fd, b"unexpected success")
                    os._exit(1)
            os.write(write_fd, b"ok")
            os._exit(0)
        except BaseException:
            os.write(write_fd, b"unexpected exception")
            os._exit(1)

    os.close(write_fd)
    try:
        readable, _, _ = select.select([read_fd], [], [], 3)
        if not readable:
            os.kill(child, signal.SIGKILL)
        _, status = os.waitpid(child, 0)
        assert readable, "child blocked on inherited wrapper lock"
        assert os.read(read_fd, 128) == b"ok"
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        os.close(read_fd)
        c._state_lock.release()


def test_crash_recovery_requires_external_marker_to_prevent_new_attempt():
    c = ready()
    candidate = prepare(c)
    c.try_claim(candidate)
    # Simulated process restart loses claim memory; the durable owner must check
    # the previously persisted marker and perform readback, never call prepare.
    restarted = ready()
    assert restarted.capacity_status()["commands"] == 0
