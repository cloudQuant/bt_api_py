"""A real SDK runtime and SQLite registry share one owner-thread lease."""

import queue
import threading

from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.unit.test_service_runtime_renewal_retry import (
    LocalTransport,
    ManualClock,
    _config,
    _registry,
    _runtime,
)


def test_same_registration_renews_while_caller_does_not_poll(tmp_path):
    clock = ManualClock(1000)
    renewed = queue.Queue()
    observed = []
    threads = []

    class Transport(LocalTransport):
        def request(self, request, *, timeout):
            threads.append(threading.get_ident())
            response = super().request(request, timeout=timeout)
            operation = request.header.operation
            if operation in ("service.renew", "account.snapshot"):
                observed.append((operation, request.payload["registration_id"]))
            if operation == "service.renew":
                renewed.put(request.payload["registration_id"])
            return response

        def close(self):
            threads.append(threading.get_ident())
            super().close()
            self.registry.close()

    def factory():
        threads.append(threading.get_ident())
        registry = _registry(tmp_path / "registry.sqlite", [], clock=clock)
        return _runtime(_config(), Transport(registry), monotonic_clock=clock)

    runtime = ThreadedServiceClientRuntime(factory, monotonic_clock=clock)
    try:
        initial = runtime.start(timeout_seconds=3).bindings[0]
        assert (
            runtime.call(
                "store.account", "account.snapshot", {}, account_ref="account-a", timeout_seconds=3
            )["balance"]
            == 125
        )
        # The caller does no runtime polling or calls across 40.4 simulated
        # seconds. Barriers guarantee each previous renewal completed first.
        for _ in range(4):
            clock.advance(10.1)
            assert renewed.get(timeout=3) == initial.registration_id
        assert (
            runtime.call(
                "store.account", "account.snapshot", {}, account_ref="account-a", timeout_seconds=3
            )["balance"]
            == 125
        )
        assert all(identity == initial.registration_id for _, identity in observed)
        assert [op for op, _ in observed].count("service.renew") == 4
    finally:
        runtime.close(timeout_seconds=3)
    assert len(set(threads)) == 1
    assert threads[0] != threading.get_ident()


def test_unmanaged_runtime_expires_without_poll_at_40_seconds(tmp_path):
    import pytest

    from bt_api_py.service_runtime import ServiceRuntimeError

    clock = ManualClock(1000)
    registry = _registry(tmp_path / "old-baseline.sqlite", [], clock=clock)
    transport = LocalTransport(registry)
    runtime = _runtime(_config(), transport, monotonic_clock=clock)
    try:
        runtime.start()
        clock.advance(40.4)
        with pytest.raises(ServiceRuntimeError, match="REQUIRED_LEASE_EXPIRED"):
            runtime.call("store.account", "account.snapshot", {})
    finally:
        runtime.close()
        registry.close()


def _child_register_and_exit(connection, report):
    """A new synthetic child exits without unregistering its own lease."""
    import os

    from bt_api_py.ctp_gateway_composition import create_gateway_market_runtime

    def factory():
        runtime, _ = create_gateway_market_runtime(connection, start=False)
        return runtime

    runtime = ThreadedServiceClientRuntime(factory)
    snapshot = runtime.start(timeout_seconds=10)
    report.send(snapshot.bindings[0].registration_id)
    report.close()
    os._exit(0)


def test_real_servicehost_curve_maintains_the_same_market_registration(monkeypatch, tmp_path):
    import time

    from bt_api_py.ctp_gateway_composition import (
        create_gateway_market_runtime,
        gateway_market_check_client,
    )
    from tests.integration.test_iter62_gateway_market_runtime_lease import _Clocks, _gateway_hub

    clocks = _Clocks()
    observed = queue.Queue()
    holder = []
    with _gateway_hub(monkeypatch, tmp_path, clocks) as connection:

        def factory():
            runtime, _ = create_gateway_market_runtime(connection, start=False)
            runtime._monotonic_clock = lambda: time.monotonic() + clocks.client_offset
            key = gateway_market_check_client(runtime, connection, "rb2701", "SHFE").key.to_dict()
            holder.append(key)
            original = runtime._rpc

            def spy(service_id, transport_ref, scope, operation, payload, **kwargs):
                result = original(service_id, transport_ref, scope, operation, payload, **kwargs)
                if operation in {"service.renew", "market.subscribe", "market.head"}:
                    observed.put((operation, payload["registration_id"]))
                return result

            runtime._rpc = spy
            return runtime

        runtime = ThreadedServiceClientRuntime(
            factory, monotonic_clock=lambda: time.monotonic() + clocks.client_offset
        )
        try:
            binding = runtime.start(timeout_seconds=10).bindings[0]
            assert runtime.call(
                "store.market", "market.subscribe", {"key": holder[0]}, timeout_seconds=10
            )["stream_id"]
            assert observed.get(timeout=2) == ("market.subscribe", binding.registration_id)
            for _ in range(4):
                clocks.advance(10.1)
                assert observed.get(timeout=4) == ("service.renew", binding.registration_id)
            assert "head" in runtime.call(
                "store.market", "market.head", {"key": holder[0]}, timeout_seconds=10
            )
            assert observed.get(timeout=2) == ("market.head", binding.registration_id)
        finally:
            runtime.close(timeout_seconds=10)


def test_synthetic_child_exit_leaves_registration_to_expire_on_host(monkeypatch, tmp_path):
    import multiprocessing
    import sqlite3
    import time

    from bt_api_py.ctp_gateway_composition import create_gateway_market_runtime
    from tests.integration.test_iter62_gateway_market_runtime_lease import _Clocks, _gateway_hub

    clocks = _Clocks()
    with _gateway_hub(monkeypatch, tmp_path, clocks) as connection:
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_child_register_and_exit, args=(connection, child))
        process.start()
        child.close()
        runtime = None
        try:
            assert parent.poll(15), "synthetic child did not register"
            registration_id = parent.recv()
            process.join(10)
            assert process.exitcode == 0
            db = sqlite3.connect(tmp_path / "gateway-market-registry.sqlite3")
            try:
                row = db.execute(
                    "SELECT expires_at FROM sessions WHERE registration_id = ?", (registration_id,)
                ).fetchone()
                assert row is not None
                expires_at = row[0]
                assert expires_at > time.time() + clocks.host_offset
                # Advance to just after the actual host deadline; a separate
                # registration causes the host to expire stale sessions.
                clocks.host_offset += max(0, expires_at - time.time() + 0.05)
                runtime = ThreadedServiceClientRuntime(
                    lambda: create_gateway_market_runtime(connection, start=False)[0]
                )
                runtime.start(timeout_seconds=10)
                assert (
                    db.execute(
                        "SELECT 1 FROM sessions WHERE registration_id = ?", (registration_id,)
                    ).fetchone()
                    is None
                )
                assert time.time() + clocks.host_offset <= expires_at + 2.0
            finally:
                db.close()
        finally:
            if runtime is not None:
                runtime.close(timeout_seconds=10)
            parent.close()
            if process.is_alive():
                process.terminate()
                process.join(5)
            process.close()
