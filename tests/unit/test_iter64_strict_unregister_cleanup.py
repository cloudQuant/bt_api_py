"""Synthetic Registry replies are not proof of Risk owner cleanup."""

from dataclasses import replace
import time
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireEnvelope, WireMessageKind

from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from bt_api_py.risk_service_client import RiskServiceClient
from bt_api_py.service_runtime_thread import ThreadedRuntimeError
from tests.unit.test_iter64_risk_service_client import lost_runtime_client, risk_route
from tests.unit.test_service_runtime import LocalTransport, ManualClock, PEER, _config, _registry, _runtime


@pytest.mark.parametrize("code", ["STALE_EPOCH", "NOT_REGISTERED", "WRONG_ID"])
def test_rejected_unregister_cannot_authorize_risk_replacement(tmp_path, monkeypatch, code):
    wire, operations = [], []
    clock = ManualClock(1000)

    def factory():
        registry = _registry(tmp_path / "registry.sqlite", [])

        class RejectUnregister(LocalTransport):
            def request(self, request, *, timeout):
                operations.append(request.header.operation)
                if request.header.operation == "service.unregister":
                    header = replace(request.header, kind=WireMessageKind.REPLY,
                        message_id="rejected", correlation_id=request.header.message_id, identity=PEER)
                    payload = ({"ok": True, "code": "OK", "result": {
                        "unregistered": True, "registration_id": "other-registration"}}
                        if code == "WRONG_ID" else {"ok": False, "code": code})
                    return WireEnvelope(header, payload)
                return super().request(request, timeout=timeout)

            def close(self):
                wire.append(self.registry._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
                super().close()
                registry.close()

        transport = RejectUnregister(registry)
        return _runtime(_config(), lambda *_: transport, monotonic_clock=clock,
                        require_unregister_ack=True)

    old = ThreadedServiceClientRuntime(factory, monotonic_clock=clock, require_unregister_ack=True)
    old.start(timeout_seconds=2)
    snapshot = old.close(timeout_seconds=2)
    assert wire == [1] and "service.unregister" in operations
    assert snapshot.unregister_uncertainties == 1
    assert not runtime_cleanup_confirmed(snapshot)
    client, replacements, calls, _ = lost_runtime_client(monkeypatch)
    client._runtime = old
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old and replacements == [] and calls == []
    from bt_api_py.risk_service_host import RiskServiceHost
    observer_replacements, gaps = [], []
    host = SimpleNamespace(_observer_runtime=old, _observer_route=object(),
        pump=SimpleNamespace(_gap=gaps.append), _monotonic=lambda: 1000.0,
        _observer_retry_at=0, _runtimes=[old],
        config=SimpleNamespace(maintain_seconds=1, call_timeout_seconds=1),
        _service_runtime=lambda *_args, **_kwargs: observer_replacements.append(True))
    RiskServiceHost._reconnect_observer(host)
    assert gaps == ["observation_reconnect"] and observer_replacements == []
    assert host._observer_runtime is old and host._runtimes == [old]


@pytest.mark.parametrize("code", ["STALE_EPOCH", "NOT_REGISTERED"])
def test_rejected_renewal_cannot_skip_strict_unregister(tmp_path, code):
    clock = ManualClock(1000)
    operations = []

    def factory():
        registry = _registry(tmp_path / "renew.sqlite", [])
        class RejectBoth(LocalTransport):
            def request(self, request, *, timeout):
                operations.append(request.header.operation)
                if request.header.operation in {"service.renew", "service.unregister"}:
                    header = replace(request.header, kind=WireMessageKind.REPLY,
                        message_id="rejected", correlation_id=request.header.message_id, identity=PEER)
                    return WireEnvelope(header, {"ok": False, "code": code})
                return super().request(request, timeout=timeout)
            def close(self):
                super().close()
                registry.close()
        return _runtime(_config(), lambda *_: RejectBoth(registry), monotonic_clock=clock,
                        require_unregister_ack=True)

    owner = ThreadedServiceClientRuntime(factory, monotonic_clock=clock, require_unregister_ack=True)
    owner.start(timeout_seconds=2)
    clock.advance(10.1)
    deadline = time.monotonic() + 3
    while owner.status().state != "failed" and time.monotonic() < deadline:
        time.sleep(0.01)
    snapshot = owner.close(timeout_seconds=2)
    assert "service.renew" in operations and "service.unregister" in operations
    assert snapshot.unregister_uncertainties == 1 and not runtime_cleanup_confirmed(snapshot)


def test_same_id_acknowledgment_allows_cleanup_without_resending(tmp_path, monkeypatch):
    calls, operations = [], []

    def factory():
        registry = _registry(tmp_path / "ack.sqlite", calls)
        class Acknowledged(LocalTransport):
            def request(self, request, *, timeout):
                operations.append(request.header.operation)
                return super().request(request, timeout=timeout)
            def close(self):
                super().close()
                registry.close()
        return _runtime(_config(), lambda *_: Acknowledged(registry), require_unregister_ack=True)

    owner = ThreadedServiceClientRuntime(factory, require_unregister_ack=True)
    owner.start(timeout_seconds=2)
    assert runtime_cleanup_confirmed(owner.close(timeout_seconds=2))
    assert operations == ["service.discover", "service.register", "service.unregister"] and calls == []
    client, replacements, business, _ = lost_runtime_client(monkeypatch)
    client._runtime = owner
    assert client.reconnect(timeout_seconds=1)
    assert len(replacements) == 1 and business == [] and calls == []


def test_non_risk_callers_retain_legacy_rejection_handling(tmp_path):
    registry = _registry(tmp_path / "legacy.sqlite", [])
    class LegacyReply(LocalTransport):
        def request(self, request, *, timeout):
            if request.header.operation == "service.unregister":
                header = replace(request.header, kind=WireMessageKind.REPLY,
                    message_id="rejected", correlation_id=request.header.message_id, identity=PEER)
                return WireEnvelope(header, {"ok": False, "code": "NOT_REGISTERED"})
            return super().request(request, timeout=timeout)
    transport = LegacyReply(registry)
    runtime = _runtime(_config(), lambda *_: transport)
    try:
        runtime.start()
        runtime.close()
        assert runtime.unregister_uncertainties == 0
    finally:
        runtime.close()
        registry.close()


def test_injected_non_strict_runtime_never_authorizes_risk_calls_or_replacement(tmp_path, monkeypatch):
    operations = []
    def factory():
        registry = _registry(tmp_path / "injected.sqlite", [])
        class LegacyReply(LocalTransport):
            def request(self, request, *, timeout):
                operations.append(request.header.operation)
                if request.header.operation == "service.unregister":
                    header = replace(request.header, kind=WireMessageKind.REPLY,
                        message_id="rejected", correlation_id=request.header.message_id, identity=PEER)
                    return WireEnvelope(header, {"ok": False, "code": "STALE_EPOCH"})
                return super().request(request, timeout=timeout)
            def close(self):
                super().close()
                registry.close()
        return _runtime(_config(), lambda *_: LegacyReply(registry))
    unsafe = ThreadedServiceClientRuntime(factory)
    client, replacements, calls, _ = lost_runtime_client(monkeypatch)
    client._runtime = unsafe
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_POLICY_UNPROVEN"):
        client.start(timeout_seconds=1)
    assert unsafe.status().state == "new" and operations == []
    # An externally started legacy runtime is also rejected by business/reconnect.
    unsafe.start(timeout_seconds=2)
    assert unsafe.status().strict_unregister_ack is False
    assert not client.available
    assert client.status("old", timeout_seconds=1).outcome == "NOT_SENT"
    assert unsafe.close(timeout_seconds=2).unregister_uncertainties == 0  # legacy behavior
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is unsafe and replacements == [] and calls == []
    assert operations == ["service.discover", "service.register", "service.unregister"]


def test_factory_returns_started_non_strict_runtime_it_is_retained(tmp_path, monkeypatch):
    operations, registries = [], []
    def factory():
        registry = _registry(tmp_path / "candidate.sqlite", [])
        registries.append(registry)
        class Trace(LocalTransport):
            def request(self, request, *, timeout):
                operations.append(request.header.operation)
                if request.header.operation == "service.unregister":
                    header = replace(request.header, kind=WireMessageKind.REPLY,
                        message_id="rejected", correlation_id=request.header.message_id, identity=PEER)
                    return WireEnvelope(header, {"ok": False, "code": "STALE_EPOCH"})
                return super().request(request, timeout=timeout)
            def close(self):
                super().close()
                registry.close()
        return _runtime(_config(), lambda *_: Trace(registry))
    candidate = ThreadedServiceClientRuntime(factory)
    candidate.start(timeout_seconds=2)
    client, made, calls, clock = lost_runtime_client(monkeypatch)
    original = client._runtime
    issued = []
    client._factory = lambda: issued.append(candidate) or candidate
    try:
        assert client.reconnect(timeout_seconds=1) is False
        assert client._runtime is original and client._pending_runtime is candidate
        assert candidate.status().thread_alive is False and "service.unregister" in operations
        assert candidate.status().strict_unregister_ack is False
        clock.now += 1
        assert client.reconnect(timeout_seconds=1) is False
        assert client._pending_runtime is candidate and issued == [candidate]
        assert made == [] and calls == [] and "account.snapshot" not in operations
    finally:
        candidate.close(timeout_seconds=2)
        for registry in registries:
            registry.close()


@pytest.mark.parametrize("result", ["raise", "return_old"])
def test_factory_registers_then_hides_candidate_blocks_retries_and_business(tmp_path, monkeypatch, result):
    operations, registries, created = [], [], []
    def raw_factory():
        registry = _registry(tmp_path / "leaky-factory.sqlite", [])
        registries.append(registry)
        class Trace(LocalTransport):
            def request(self, request, *, timeout):
                operations.append(request.header.operation)
                return super().request(request, timeout=timeout)
            def close(self):
                super().close()
                registry.close()
        return _runtime(_config(), lambda *_: Trace(registry), require_unregister_ack=True)

    client, made, calls, clock = lost_runtime_client(monkeypatch)
    original = client._runtime
    attempts = []
    def leaky_factory():
        attempts.append(True)
        candidate = ThreadedServiceClientRuntime(raw_factory, require_unregister_ack=True)
        created.append(candidate)
        candidate.start(timeout_seconds=2)
        if result == "raise":
            raise RuntimeError("failed after registering candidate")
        return original
    client._factory = leaky_factory
    try:
        assert client.reconnect(timeout_seconds=2) is False
        assert created[0].status().state == "running" and "service.register" in operations
        assert client._runtime is original and client._pending_candidate_unknown is True
        for _ in range(3):
            clock.now += 60
            assert client.reconnect(timeout_seconds=1) is False
            assert client.status("old-order", timeout_seconds=1).outcome == "NOT_SENT"
        assert attempts == [True] and made == [] and calls == []
        assert operations.count("service.register") == 1 and "account.snapshot" not in operations
        with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
            client.close(timeout_seconds=1)
    finally:
        for candidate in created:
            candidate.close(timeout_seconds=2)
        for registry in registries:
            registry.close()


def test_public_factory_with_hidden_owner_is_never_invoked(tmp_path, monkeypatch):
    registries, owners, attempts, operations = [], [], [], []
    fixture_client, _made, calls, clock = lost_runtime_client(monkeypatch)
    original = fixture_client._runtime

    def external_factory():
        attempts.append(True)
        for name in ("hidden", "returned"):
            def raw_factory(name=name):
                registry = _registry(tmp_path / (name + ".sqlite"), [])
                registries.append(registry)
                class Trace(LocalTransport):
                    def request(self, request, *, timeout):
                        operations.append(request.header.operation)
                        return super().request(request, timeout=timeout)
                    def close(self):
                        super().close()
                        registry.close()
                return _runtime(_config(), lambda *_: Trace(registry), require_unregister_ack=True)
            runtime = ThreadedServiceClientRuntime(raw_factory, require_unregister_ack=True)
            owners.append(runtime)
            runtime.start(timeout_seconds=2)
        return owners[-1]

    client = RiskServiceClient(original, route=risk_route(), runtime_factory=external_factory,
                               monotonic_clock=clock)
    try:
        for _ in range(3):
            clock.now += 60
            assert client.reconnect(timeout_seconds=1) is False
            assert client.status("prior", timeout_seconds=1).outcome == "NOT_SENT"
        assert client._runtime is original and attempts == [] and owners == []
        assert operations == [] and calls == []
        # Positive control: the very same callable really creates two owners
        # with independently registered synthetic Registry sessions.
        assert external_factory() is owners[-1]
        assert len(owners) == 2 and all(owner.status().state == "running" for owner in owners)
        assert operations.count("service.register") == 2
    finally:
        for runtime in owners:
            runtime.close(timeout_seconds=2)
        for registry in registries:
            registry.close()


def test_strict_wrapper_refuses_non_strict_factory_before_registration(tmp_path):
    operations, registries = [], []
    def factory():
        registry = _registry(tmp_path / "untrusted.sqlite", [])
        registries.append(registry)
        class Trace(LocalTransport):
            def request(self, request, *, timeout):
                operations.append(request.header.operation)
                return super().request(request, timeout=timeout)
            def close(self):
                super().close()
                registry.close()
        return _runtime(_config(), lambda *_: Trace(registry))
    runtime = ThreadedServiceClientRuntime(factory, require_unregister_ack=True)
    client = RiskServiceClient(runtime, route=risk_route())
    try:
        with pytest.raises(ThreadedRuntimeError):
            client.start(timeout_seconds=2)
        result = runtime.close(timeout_seconds=2)
        assert result.strict_unregister_ack is False and operations == []
        assert client._runtime is runtime and not client.available
    finally:
        for registry in registries:
            registry.close()
