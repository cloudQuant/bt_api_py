"""Unit tests for RiskServiceHost proactive upstream.market runtime rotation (Iter 72 GAP 3)."""
from __future__ import annotations

import os
import threading
from types import SimpleNamespace

from bt_api_py.risk_service_host import RiskServiceHost, RiskServiceStartError


def _make_host(remaining: int, *, state: str = "running", healthy: bool = True):
    host = object.__new__(RiskServiceHost)
    host._creator_pid = os.getpid()
    host._storage_owner = threading.current_thread()
    host._resource_owner = threading.current_thread()
    host._storage_init_uncertain = False
    host._storage_cleanup_uncertain = False
    host._cleanup_uncertain = False
    host._cleanup_in_progress = False
    host._cleanup_state = "ready"
    host._lifecycle_in_progress = 0
    host._cleanup_uncertain_resources = []
    host.stop_event = threading.Event()
    host.config = SimpleNamespace(
        maintain_seconds=1.0,
        call_timeout_seconds=5.0,
        gateway=SimpleNamespace(market=object()),
    )
    forgot = []
    host.facts = SimpleNamespace(forget_market=lambda: forgot.append("forgot"))
    old_rt = SimpleNamespace(
        name="old-market-rt",
        status=lambda: SimpleNamespace(
            state=state,
            healthy=healthy,
            bindings=(),
            min_socket_message_ids_remaining=remaining,
        ),
    )
    host._market_runtime = old_rt
    host._runtimes = [old_rt]
    host.market = SimpleNamespace(name="old-market-feed")
    host._market_retry_at = 0.0
    closed = []
    host._close_runtime_once = lambda rt, _timeout: closed.append(rt) or True
    return host, old_rt, forgot, closed


def test_maintain_market_runtime_skips_when_socket_budget_above_headroom():
    host, old_rt, forgot, closed = _make_host(remaining=200)
    started = []
    host._start_market = lambda: started.append("started")
    host._maintain_market_runtime(100.0)
    assert started == []
    assert forgot == []
    assert closed == []
    assert host._market_runtime is old_rt


def test_maintain_market_runtime_proactively_rotates_near_4096_cap_and_forgets_facts():
    host, old_rt, forgot, closed = _make_host(remaining=RiskServiceHost._MARKET_ROTATION_HEADROOM)
    new_rt = SimpleNamespace(name="new-market-rt")
    new_feed = SimpleNamespace(name="new-market-feed")

    def fake_start_market():
        host._market_runtime = new_rt
        host._runtimes.append(new_rt)
        host.market = new_feed

    host._start_market = fake_start_market
    host._maintain_market_runtime(100.0)
    assert forgot == ["forgot"]
    assert closed == [old_rt]
    assert old_rt not in host._runtimes
    assert host._market_runtime is new_rt
    assert host.market is new_feed
    assert host._market_retry_at == 0.0


def test_maintain_market_runtime_fails_closed_when_replacement_start_fails():
    host, old_rt, forgot, closed = _make_host(remaining=0, state="failed", healthy=False)

    def failing_start():
        raise RiskServiceStartError("RISK_SERVICE_GATEWAY_MARKET_UNAVAILABLE")

    host._start_market = failing_start
    host._maintain_market_runtime(100.0)
    assert forgot == ["forgot"]
    assert closed == [old_rt]
    assert host._market_runtime is None
    assert host.market is None
    assert host._market_retry_at == 101.0

    # Retrying before backoff does nothing; retrying at/after backoff succeeds.
    calls = []
    host._start_market = lambda: calls.append("retried")
    host._maintain_market_runtime(100.5)
    assert calls == []
    host._maintain_market_runtime(101.0)
    assert calls == ["retried"]
