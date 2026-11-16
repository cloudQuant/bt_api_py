"""Finite diagnostics for retained synthetic Risk cleanup; no services or accounts."""

import logging

import pytest

from bt_api_py import risk_service_host as module
from bt_api_py.service_runtime import ServiceRuntimeError
from tests.unit.test_iter64_raw_runtime_sticky import populate, setup_runtime
from tests.unit.test_iter64_risk_sticky_cleanup import StopWait, host


@pytest.mark.parametrize("failure", [TimeoutError("private timeout"), ServiceRuntimeError("STALE_EPOCH")])
def test_strict_unregister_failure_reports_host_latch_without_retry(host, caplog, failure):
    calls = []
    runtime = None

    def notify():
        host._runtime_cleanup_sticky(runtime)

    runtime = setup_runtime(guard=host._runtime_cleanup_unknown, notify=notify, ack=True)
    original, _, transport, _ = populate(runtime, calls)
    host._runtimes = [runtime]

    def unregister(binding):
        calls.append(("unregister", binding))
        raise failure

    runtime._unregister_binding = unregister
    with pytest.raises(type(failure)):
        runtime.close()
    assert host._cleanup_uncertain is True
    assert calls == [("unregister", original)]
    assert runtime._transports[("one", "profile")] is transport
    before = list(module._PROBE_STATE["uncertain"])
    with caplog.at_level(logging.ERROR), pytest.raises(StopWait):
        module._wait_for_cleanup(host, _wait=lambda: (_ for _ in ()).throw(StopWait()))
    assert "state=cleanup_uncertain" in caplog.text
    assert "reasons=RISK_RUNTIME_CLEANUP_UNKNOWN" in caplog.text
    assert "resources=RUNTIME" in caplog.text
    assert "private timeout" not in caplog.text and "STALE_EPOCH" not in caplog.text
    assert calls == [("unregister", original)] and "stack" not in host.events
    assert module._PROBE_STATE["uncertain"] == before
    assert host in module._RETAINED_HOSTS


def test_wait_diagnostics_redact_unknown_reasons_and_never_inspect_resources(host, caplog):
    class Malicious:
        def __repr__(self):
            raise AssertionError("resource repr")
        def __eq__(self, other):
            raise AssertionError("resource equality")
        def __str__(self):
            raise AssertionError("reason string")
        def __hash__(self):
            raise AssertionError("reason hash")

    resource = Malicious()
    host._observer_runtime = resource
    host._record_cleanup_uncertain(resource, "RUNTIME_CLOSE_UNCONFIRMED")
    host._record_cleanup_uncertain(resource, Malicious())
    host._record_cleanup_uncertain(resource, "PRIVATE_PATH_AND_ACCOUNT")
    before = tuple(module._PROBE_STATE["uncertain"])
    with caplog.at_level(logging.ERROR), pytest.raises(StopWait):
        module._wait_for_cleanup(host, _wait=lambda: (_ for _ in ()).throw(StopWait()))
    assert "reasons=RUNTIME_CLOSE_UNCONFIRMED,UNKNOWN" in caplog.text
    assert "resources=OBSERVER" in caplog.text
    assert "PRIVATE_PATH_AND_ACCOUNT" not in caplog.text
    assert all(left is right for left, right in zip(before, module._PROBE_STATE["uncertain"]))
    assert len(before) == len(module._PROBE_STATE["uncertain"])
    assert host.events == []


def test_diagnostic_is_bounded_deduplicated_and_keeps_first_reason(host):
    reasons = sorted(module._CLEANUP_DIAGNOSTIC_REASONS)[:10]
    for reason in reasons:
        host._record_cleanup_uncertain(host, reason)
        host._record_cleanup_uncertain(host, reason)
    before = tuple(module._PROBE_STATE["uncertain"])
    first = module._cleanup_wait_diagnostic(host)
    assert first == (tuple(reasons[:8]), ("HOST",))
    assert module._cleanup_wait_diagnostic(host) == first
    assert all(left is right for left, right in zip(before, module._PROBE_STATE["uncertain"]))
    assert len(before) == len(module._PROBE_STATE["uncertain"])


def test_busy_diagnostic_lock_fails_to_fixed_unknown_without_wait(host):
    with module._PROBE_GUARD:
        assert module._cleanup_wait_diagnostic(host) == (("UNKNOWN",), ("UNKNOWN",))


def test_diagnostic_only_reads_own_host_and_classifies_owned_delegate(host):
    foreign_host = object()
    delegate = object()
    host._delegate_cleanup_unresolved = [delegate]
    module._PROBE_STATE["uncertain"].append((foreign_host, object(), "STORAGE_ENTRY_UNKNOWN"))
    host._record_cleanup_uncertain(delegate, "DELEGATE_UNRESOLVED")
    assert module._cleanup_wait_diagnostic(host) == (("DELEGATE_UNRESOLVED",), ("DELEGATE",))


def test_corrupt_diagnostic_storage_cannot_prevent_wait(host, caplog):
    module._PROBE_STATE["uncertain"] = object()
    with caplog.at_level(logging.ERROR), pytest.raises(StopWait):
        # Already-uncertain state prevents _wait_for_cleanup from attempting a close.
        host._cleanup_state = "uncertain"
        module._wait_for_cleanup(host, _wait=lambda: (_ for _ in ()).throw(StopWait()))
    assert "state=cleanup_uncertain reasons=UNKNOWN resources=UNKNOWN" in caplog.text
    assert host.events == []
