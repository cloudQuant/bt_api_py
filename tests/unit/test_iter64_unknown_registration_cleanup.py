"""Unknown remote registration prevents owner replacement (local fake transport)."""

from dataclasses import replace
import subprocess
import sys
from types import SimpleNamespace

import pytest

from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed
from bt_api_py.service_runtime_thread import RuntimeSnapshot, ThreadedRuntimeError, ThreadedServiceClientRuntime
from tests.unit.test_iter64_risk_service_client import lost_runtime_client
from tests.unit.test_service_runtime import LocalTransport, _config, _registry, _runtime


@pytest.mark.parametrize("count", [1, None, True, -1])
def test_cleanup_requires_known_zero_unknown_registrations(count):
    snapshot = SimpleNamespace(state="closed", thread_alive=False, error_code=None,
        unregister_uncertainties=0, owner_cleanup_uncertainties=0, unknown_registration_count=count)
    assert not runtime_cleanup_confirmed(snapshot)
    del snapshot.unknown_registration_count
    assert not runtime_cleanup_confirmed(snapshot)


def test_legacy_snapshot_construction_does_not_invent_clean_registration_state():
    legacy = RuntimeSnapshot("closed", False, False, None, (), 0, 0)
    assert legacy.unknown_registration_count is None
    assert not runtime_cleanup_confirmed(legacy)


def test_neutral_runtime_cleanup_does_not_import_risk():
    code = ("import sys; import bt_api_py.service_runtime_thread, "
            "bt_api_py.service_runtime_cleanup, bt_api_py.risk_service_client; "
            "assert not any(m == 'bt_api_risk' or m.startswith('bt_api_risk.') "
            "for m in sys.modules)")
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr


def test_raw_runtime_without_registration_evidence_stays_unknown():
    class LegacyRaw:
        bindings = ()
        healthy = True
        cleanup_failures = unregister_uncertainties = 0
        def start(self): pass
        def close(self): pass
        def maintain(self): pass
        def next_maintenance_delay(self): return 1

    owner = ThreadedServiceClientRuntime(LegacyRaw)
    owner.start(timeout_seconds=1)
    snapshot = owner.close(timeout_seconds=1)
    assert snapshot.unknown_registration_count is None
    assert not runtime_cleanup_confirmed(snapshot)


def test_committed_but_lost_registration_is_visible_and_blocks_reconnect(tmp_path, monkeypatch):
    committed, business_calls, raw_runtimes = [], [], []

    def factory():
        registry = _registry(tmp_path / "registry.sqlite", business_calls)

        class LostReply(LocalTransport):
            def request(self, request, *, timeout):
                reply = super().request(request, timeout=timeout)
                if request.header.operation == "service.register":
                    assert reply.payload["ok"] is True
                    committed.append(True)
                    raise TimeoutError("synthetic lost reply after commit")
                return reply

            def close(self):
                super().close()
                registry.close()

        transport = LostReply(registry)
        raw = _runtime(_config(), lambda *_: transport)
        raw_runtimes.append(raw)
        return raw

    old = ThreadedServiceClientRuntime(factory)
    with pytest.raises(ThreadedRuntimeError):
        old.start(timeout_seconds=2)
    old.close(timeout_seconds=2)
    assert committed == [True] and raw_runtimes[0].unknown_registration_count == 1
    assert old.status().unknown_registration_count == 1

    client, replacements, calls, clock = lost_runtime_client(monkeypatch)
    client._runtime = old
    assert client.reconnect(timeout_seconds=2) is False
    assert client._runtime is old and replacements == [] and calls == [] and business_calls == []
    assert client.status("existing-request", timeout_seconds=1).outcome == "NOT_SENT"
    # Synthetic independent proof of cleanup, not a claim that this actual
    # unknown registration can currently be resolved by the SDK.
    clean = replace(old.status(), unknown_registration_count=0)
    monkeypatch.setattr(old, "close", lambda **_kwargs: clean)
    clock.now += 1.0
    assert client.reconnect(timeout_seconds=1) is True
    assert len(replacements) == 1 and calls == [] and business_calls == []
