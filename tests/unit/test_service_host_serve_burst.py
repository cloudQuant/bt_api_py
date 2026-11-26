"""Unit tests for bounded burst serving on ServiceHost, GatewayOperatorDeployment, and RiskServiceHost."""
from __future__ import annotations

import importlib
import threading
from types import SimpleNamespace

import pytest

from bt_api_py.gateway_operator import GatewayOperatorDeployment
from bt_api_py.risk_service_host import RiskServiceHost
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, ServiceHostError
from tests.unit import test_service_host


def test_service_host_serve_burst_drains_queued_requests_with_single_maintenance_pair(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(test_service_host, "_endpoint", lambda: "tcp://127.0.0.1:17993")
    host, _config, _authority, worker, _provider, _clock, original, *_rest = (
        test_service_host._account_worker_setup(tmp_path, expose_instrument_metadata=True)
    )
    maintain_calls = 0

    def maintain_resource(_active_ids: frozenset[str]) -> None:
        nonlocal maintain_calls
        maintain_calls += 1

    resources = ComponentResources.owned(
        original.values,
        ready=original.ready,
        close=original.close,
        internal_ports=original.internal_ports,
        maintain=maintain_resource,
        next_maintenance_delay=lambda: 0.5,
    )
    host._resource_provider = lambda *_args: resources

    class BurstServer:
        def __init__(self, endpoint, *_args, **_kwargs):
            self.endpoint = endpoint
            self.queued = 6
            self.timeouts: list[int] = []

        def serve_once(self, timeout_ms: int) -> bool:
            self.timeouts.append(timeout_ms)
            if self.queued > 0:
                self.queued -= 1
                return True
            return False

        def close(self) -> None:
            pass

    service_v2 = importlib.import_module("bt_api_transport_zmq.service_v2")
    monkeypatch.setattr(service_v2, "ZmqServiceServer", BurstServer)

    host.start()
    try:
        maintain_calls = 0
        # 6 queued requests drained in one burst (capped at max_requests=4 first, then remaining 2).
        served_first = host.serve_burst(50, max_requests=4)
        assert served_first == 4
        assert maintain_calls == 2  # exactly one pre-poll + one post-poll sweep for 4 requests
        assert host._server.timeouts == [50, 0, 0, 0]

        maintain_calls = 0
        served_second = host.serve_burst(50, max_requests=16)
        assert served_second == 2
        assert maintain_calls == 2  # pre-poll + post-poll sweep; stops cleanly once queue empty
        assert host._server.timeouts[-3:] == [50, 0, 0]

        # Idle poll returns 0.
        assert host.serve_burst(10, max_requests=8) == 0

        # Invalid arguments fail closed.
        with pytest.raises(ServiceHostError, match="INVALID_TIMEOUT"):
            host.serve_burst(-1)
        with pytest.raises(ServiceHostError, match="INVALID_TIMEOUT"):
            host.serve_burst(10, max_requests=0)
        with pytest.raises(ServiceHostError, match="INVALID_TIMEOUT"):
            host.serve_burst(10, max_requests=257)
        with pytest.raises(ServiceHostError, match="INVALID_TIMEOUT"):
            host.serve_burst(10, max_burst_seconds=0.0)
    finally:
        host.close()


def test_service_host_serve_burst_respects_maintenance_delay_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(test_service_host, "_endpoint", lambda: "tcp://127.0.0.1:17994")
    host, _config, _authority, _worker, _provider, _clock, original, *_rest = (
        test_service_host._account_worker_setup(tmp_path, expose_instrument_metadata=True)
    )
    resources = ComponentResources.owned(
        original.values,
        ready=original.ready,
        close=original.close,
        internal_ports=original.internal_ports,
        maintain=lambda _ids: None,
        next_maintenance_delay=lambda: 0.0,  # maintenance immediately due -> no extra burst beyond 1st
    )
    host._resource_provider = lambda *_args: resources

    class InfiniteServer:
        def __init__(self, endpoint, *_args, **_kwargs):
            self.endpoint = endpoint
            self.calls = 0

        def serve_once(self, _timeout_ms: int) -> bool:
            self.calls += 1
            return True

        def close(self) -> None:
            pass

    service_v2 = importlib.import_module("bt_api_transport_zmq.service_v2")
    monkeypatch.setattr(service_v2, "ZmqServiceServer", InfiniteServer)

    host.start()
    try:
        served = host.serve_burst(50, max_requests=16)
        assert served == 1
        assert host._server.calls == 1
    finally:
        host.close()


def test_risk_service_host_run_once_interleaves_inbound_drains_between_due_phases():
    """When multiple upstream poll phases are due in the same run_once tick, inbound RPCs
    must be drained between phases instead of waiting behind all upstream Gateway RPCs."""
    events: list[str] = []

    class FakeServiceHost:
        def serve_once(self, timeout_ms: int = 0) -> bool:
            events.append(f"serve:{timeout_ms}")
            return False

    class FakePoll:
        def __init__(self, name: str):
            self.name = name

        def poll(self):
            events.append(self.name)

        def refresh(self):
            events.append(self.name)

        def maintain(self):
            events.append(self.name)

    host = object.__new__(RiskServiceHost)
    host.config = SimpleNamespace(
        loop_timeout_ms=20,
        observation_poll_seconds=0.5,
        market_poll_seconds=1.0,
        account_snapshot_seconds=15.0,
        strategy_financial_seconds=5.0,
        maintain_seconds=1.0,
        account_facts_observation=None,
        r04_enforcement_mode=None,
    )
    host._creator_pid = __import__("os").getpid()
    host._storage_owner = threading.current_thread()
    host._resource_owner = threading.current_thread()
    host._storage_init_uncertain = False
    host._storage_cleanup_uncertain = False
    host._cleanup_uncertain = False
    host._cleanup_in_progress = False
    host._cleanup_state = "ready"
    host._lifecycle_in_progress = 0
    host.stop_event = threading.Event()
    host.service_host = FakeServiceHost()
    host.ledger = SimpleNamespace(fence="ok")
    host.pump = FakePoll("observation")
    host.market = FakePoll("market")
    host.account = FakePoll("account")
    host.financial = None
    host._account_facts_coordinator = None
    host.account_facts_status = "DISABLED"
    host.engine = FakePoll("maintain")
    host._monotonic = lambda: 100.0
    host._next = {
        "observation": 100.0,
        "market": 100.0,
        "account": 100.0,
        "financial": 100.0,
        "maintain": 100.0,
    }
    host._reconnect_observer = lambda: None
    host._maintain_account_facts_runtime = lambda *_a, **_kw: None

    # Patch _require_supported_r04 check by using a valid minimal config attribute set
    import bt_api_py.risk_service_host as rsh_mod

    orig_req = rsh_mod._require_supported_r04
    rsh_mod._require_supported_r04 = lambda _cfg: None
    try:
        host.run_once()
    finally:
        rsh_mod._require_supported_r04 = orig_req

    # Initial serve(20), then interleaved serve(0) between observation->market->account->maintain!
    assert events == [
        "serve:20",
        "observation",
        "serve:0",
        "market",
        "serve:0",
        "account",
        "serve:0",
        "maintain",
    ]


def test_gateway_operator_deployment_forwards_serve_burst():
    deployment = object.__new__(GatewayOperatorDeployment)
    deployment._owner_thread_id = threading.get_ident()
    recorded = []
    deployment._host = SimpleNamespace(
        serve_burst=lambda timeout_ms, *, max_requests=16: recorded.append(
            (timeout_ms, max_requests)
        )
        or 3
    )
    assert deployment.serve_burst(25, max_requests=8) == 3
    assert recorded == [(25, 8)]
