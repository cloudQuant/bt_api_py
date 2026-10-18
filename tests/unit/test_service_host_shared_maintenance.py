from __future__ import annotations

import importlib
import math

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_runtime import RegistrationSummary
from tests.unit import test_service_host


def test_host_schedules_owned_resource_maintenance_before_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(
        test_service_host, "_endpoint", lambda: "tcp://127.0.0.1:17991"
    )
    host, _config, _authority, worker, _provider, _clock, original, *_rest = (
        test_service_host._account_worker_setup(
            tmp_path, expose_instrument_metadata=True
        )
    )
    events: list[tuple[str, frozenset[str] | None]] = []
    due = {"seconds": 0.5}

    def maintain_resource(active_ids: frozenset[str]) -> None:
        events.append(("resource", active_ids))

    resources = ComponentResources.owned(
        original.values,
        ready=original.ready,
        close=original.close,
        internal_ports=original.internal_ports,
        maintain=maintain_resource,
        next_maintenance_delay=lambda: due["seconds"],
    )
    host._resource_provider = lambda *_args: resources

    worker_type = type(worker)
    original_maintain = worker_type.maintain

    def record_adapter_maintenance(instance) -> None:
        if instance is worker:
            events.append(("adapter", None))
        original_maintain(instance)

    monkeypatch.setattr(worker_type, "maintain", record_adapter_maintenance)

    class PollingServer:
        def __init__(self, endpoint, *_args, **_kwargs):
            self.endpoint = endpoint
            self.timeouts: list[int] = []
            self.closed = False

        def serve_once(self, timeout_ms: int) -> bool:
            self.timeouts.append(timeout_ms)
            return False

        def close(self) -> None:
            self.closed = True

    service_v2 = importlib.import_module("bt_api_transport_zmq.service_v2")
    monkeypatch.setattr(service_v2, "ZmqServiceServer", PollingServer)

    class UpstreamSchedule:
        state = "registered"
        healthy = True
        cleanup_failures = 0
        unregister_uncertainties = 0
        unknown_registration_count = 0

        def __init__(self) -> None:
            self.closed = False
            self.maintenance_calls = 0

        def next_maintenance_delay(self) -> float:
            return 0.25

        def maintain(self) -> RegistrationSummary:
            self.maintenance_calls += 1
            return RegistrationSummary((), 0, ())

        def close(self) -> None:
            self.closed = True
            self.state = "closed"

    upstream = UpstreamSchedule()
    server = None
    host.start()
    try:
        assert host._registry is not None
        assert host._registry.ready is True
        events.clear()

        # Startup/recovery reconciliation still renews owned resources while
        # intentionally skipping adapter recovery work.
        host._registry._migration_writes_enabled = False
        host._maintain_components(registrations_only=True)
        assert events == [("resource", frozenset())]

        events.clear()
        host._maintain_components()
        assert events == [("resource", frozenset()), ("adapter", None)]

        host._upstream = upstream
        host._upstream_registration_summary = RegistrationSummary((), 0, ())
        due["seconds"] = 0.5
        assert host.next_maintenance_delay() == 0.25
        due["seconds"] = 0.125
        assert host.next_maintenance_delay() == 0.125

        events.clear()
        assert host.serve_once(4_000) is False
        assert host._server is not None
        server = host._server
        assert host._server.timeouts == [125]
        assert events == [
            ("resource", frozenset()),
            ("adapter", None),
            ("resource", frozenset()),
            ("adapter", None),
        ]
        assert upstream.maintenance_calls == 1

        due["seconds"] = math.nan
        assert host.next_maintenance_delay() == 0.0
        assert host._upstream_faulted is False
        assert host._component_delay_faults == {"gateway-session"}
        assert host.healthy is False
        assert host._registry.ready is False
        assert host.summary["market"]["maintenance_failures"] == ("gateway-session",)

        due["seconds"] = 0.125
        assert host.next_maintenance_delay() == 0.125
        assert host._component_delay_faults == set()
        assert host.healthy is True
        assert host._registry.ready is True
    finally:
        host.close()
    assert host.state == "closed"
    assert host._server is None
    assert server is not None and server.closed is True
    assert upstream.closed is True
