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


def test_maintain_market_runtime_reconnects_on_observer_epoch_change():
    from bt_api_py.service_runtime_thread import BindingSnapshot

    host, old_rt, forgot, closed = _make_host(remaining=2000)
    old_binding = BindingSnapshot(
        "reg-old", "epoch-1", True, "upstream.market", "gw", "gw-a", "scope-a",
        None, frozenset({"market.subscribe"}), True, None, None,
    )
    old_rt.status = lambda: SimpleNamespace(
        state="running",
        healthy=True,
        bindings=(old_binding,),
        min_socket_message_ids_remaining=2000,
    )
    host.pump = SimpleNamespace(verified_epoch="stream-epoch-2")
    host._verified_observer_service_epoch = lambda: "epoch-2"
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
    assert host._market_runtime is new_rt
    assert host.market is new_feed


def test_maintain_control_runtime_reconnects_on_failure_and_rebinds_control():
    from bt_api_py.risk_service_gateway import CONTROL_OPERATIONS
    from bt_api_py.service_runtime_thread import BindingSnapshot

    host, _old_mkt, _forgot, _closed = _make_host(remaining=2000)
    host.config = SimpleNamespace(
        maintain_seconds=1.0,
        call_timeout_seconds=5.0,
        gateway=SimpleNamespace(market=None, account_snapshot=None),
        bound_control_intents_enabled=False,
        control_intent_continuity_enabled=False,
        instruments={},
    )
    forgot_acct = []
    host.facts = SimpleNamespace(forget_account=lambda: forgot_acct.append("forgot"))
    old_ctrl = SimpleNamespace(
        name="old-control-rt",
        status=lambda: SimpleNamespace(
            state="failed", healthy=False, bindings=(), min_socket_message_ids_remaining=0
        ),
    )
    host.control_runtime = old_ctrl
    host._runtimes = [old_ctrl]
    host._control_retry_at = 0.0
    rebound = []
    host.control = SimpleNamespace(replace_runtime=lambda rt: rebound.append(rt))
    closed = []
    host._close_runtime_once = lambda rt, _timeout: closed.append(rt) or True

    new_binding = BindingSnapshot(
        "reg-ctrl-new", "epoch-2", True, "application.direct_write", "gw", "gw-a", "scope-a",
        None, CONTROL_OPERATIONS, True, None, None,
    )
    new_ctrl = SimpleNamespace(
        name="new-control-rt",
        start=lambda timeout_seconds=5.0: SimpleNamespace(
            state="running", healthy=True, bindings=(new_binding,)
        ),
    )
    host._control_runtime = lambda: new_ctrl

    host._maintain_control_runtime(100.0)
    assert closed == [old_ctrl]
    assert old_ctrl not in host._runtimes
    assert host.control_runtime is new_ctrl
    assert new_ctrl in host._runtimes
    assert rebound == [new_ctrl]
    assert host._control_retry_at == 0.0


def test_cleanup_wait_diagnostic_labels_market_runtime():
    from bt_api_py.risk_service_host import _PROBE_GUARD, _PROBE_STATE, _cleanup_wait_diagnostic

    host, old_rt, _forgot, _closed = _make_host(remaining=0)
    with _PROBE_GUARD:
        saved = list(_PROBE_STATE["uncertain"])
        _PROBE_STATE["uncertain"].append((host, old_rt, "RUNTIME_CLOSE_EXCEPTION"))
    try:
        reasons, resources = _cleanup_wait_diagnostic(host)
        assert "RUNTIME_CLOSE_EXCEPTION" in reasons
        assert "MARKET_RUNTIME" in resources
    finally:
        with _PROBE_GUARD:
            _PROBE_STATE["uncertain"][:] = saved


def test_threaded_runtime_wait_pump_runs_during_slow_start_call_owner_and_close():
    import time
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

    class _SlowRaw:
        bindings = ()
        identity = None
        run_id = "run-1"
        healthy = True
        cleanup_failures = 0
        unregister_uncertainties = 0
        unknown_registration_count = 0
        maintenance_code = None
        unresolved_registration_ids = ()
        remote_inactive_registrations = ()
        open_transport_count = 0
        min_socket_message_ids_remaining = 100

        def start(self):
            time.sleep(0.12)

        def maintain(self):
            pass

        def next_maintenance_delay(self):
            return 1.0

        def call(self, mount, op, args, **_kwargs):
            time.sleep(0.12)
            return {"ok": True}

        def close(self):
            time.sleep(0.12)

    class _SlowExt:
        cleanup_uncertainties = 0

        def start(self, _rt):
            pass

        def invoke(self, _op, _args):
            time.sleep(0.12)
            return {"owner": True}

        def close(self):
            pass

    pumps = []
    caller_tid = threading.get_ident()

    def pump():
        assert threading.get_ident() == caller_tid
        pumps.append(time.monotonic())

    rt = ThreadedServiceClientRuntime(
        _SlowRaw,
        owner_extension_factory=_SlowExt,
        wait_pump=pump,
    )
    snap = rt.start(timeout_seconds=3.0)
    assert snap.state == "running"
    start_pumps = len(pumps)
    assert start_pumps >= 2

    res = rt.call("m", "op", {}, timeout_seconds=3.0)
    assert res == {"ok": True}
    call_pumps = len(pumps) - start_pumps
    assert call_pumps >= 2

    handle = rt.submit_owner("op", {})
    assert handle.result(timeout_seconds=3.0) == {"owner": True}
    owner_pumps = len(pumps) - start_pumps - call_pumps
    assert owner_pumps >= 2

    closed = rt.close(timeout_seconds=3.0)
    assert closed.state == "closed"
    close_pumps = len(pumps) - start_pumps - call_pumps - owner_pumps
    assert close_pumps >= 2


def test_service_host_serve_control_burst_allows_control_and_refuses_business():
    from bt_api_py.service_host import ServiceHost

    host = object.__new__(ServiceHost)
    host._owner_thread = threading.get_ident()
    host._execution_submission_active = False
    host._state = "started"
    host._control_only_dispatch = False
    host._atomic_intake_services = ()

    registry_calls = []

    class _FakeRegistry:
        def handle(self, principal, request):
            registry_calls.append((principal, request.header.operation))
            return {"ok": True, "code": "OK", "result": {}}

    host._registry = _FakeRegistry()
    # Any accidental readiness probe during serve_control_burst must explode!
    host._refresh_registry_readiness = lambda: (_ for _ in ()).throw(AssertionError("must not probe"))

    queued = [
        SimpleNamespace(header=SimpleNamespace(operation="service.unregister")),
        SimpleNamespace(header=SimpleNamespace(operation="risk.admin.status")),
        SimpleNamespace(header=SimpleNamespace(operation="service.renew")),
    ]
    replies = []

    class _FakeServer:
        def serve_once(self, _timeout_ms):
            if not queued:
                return False
            req = queued.pop(0)
            replies.append((req.header.operation, host._dispatch_request("client-1", req)))
            return True

    host._server = _FakeServer()
    served = host.serve_control_burst(0, max_requests=16)
    assert served == 3
    assert host._control_only_dispatch is False
    assert registry_calls == [
        ("client-1", "service.unregister"),
        ("client-1", "service.renew"),
    ]
    assert replies[0] == ("service.unregister", {"ok": True, "code": "OK", "result": {}})
    assert replies[1] == (
        "risk.admin.status",
        {"ok": False, "code": "CAPABILITY_UNAVAILABLE", "result": {}},
    )
    assert replies[2] == ("service.renew", {"ok": True, "code": "OK", "result": {}})
