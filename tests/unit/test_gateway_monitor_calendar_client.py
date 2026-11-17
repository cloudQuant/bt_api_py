"""Monitor calendar wire and local identity fences; synthetic registration proof."""

import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Thread

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_monitor_calendar_client import (
    OPERATIONS,
    GatewayMonitorCalendarClient,
    MonitorCalendarClientError,
    MonitorCalendarWindowsResult,
)
from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime
from tests.unit.test_gateway_calendar_ranges import POLICY, REQUEST
from tests.unit.test_gateway_calendar_service import CONTEXT, setup_service  # noqa: F401

IDENTITY = WireIdentity(WireRole.SERVICE, "monitor", "reader")
PEER = WireIdentity(WireRole.SERVICE, "gateway", "gateway-service")
SCOPE = WireScope("domain", account_ref="account")


def system(monkeypatch, fixture):
    service, _, _ = fixture
    service._range_policies[POLICY.scope_key] = POLICY
    value = service.handle("calendar.windows.v1", context=CONTEXT, arguments=REQUEST)
    runtime = object.__new__(ServiceClientRuntime)
    binding = RegisteredBinding(
        "upstream.calendar",
        "gateway-service",
        "instance",
        "scope",
        SCOPE,
        value["registration_id"],
        value["service_epoch"],
        OPERATIONS,
        300,
        "policy",
        "requirements",
        "transport",
        1,
        lease_deadline=250,
        renewal_due_at=200,
    )
    runtime._state, runtime._bindings, runtime.identity, runtime.run_id = (
        "registered",
        [binding],
        IDENTITY,
        "run",
    )
    runtime._cleanup_unknown = runtime._on_cleanup_sticky = None
    runtime._monotonic_now = lambda: 100
    lineage = tuple(object() for _ in range(7))
    runtime._registration_lineage = {id(binding): (binding, None, *lineage)}
    runtime._has_current_registration_observation = lambda b: (
        id(b) in runtime._registration_lineage and runtime._registration_lineage[id(b)][0] is b
    )
    runtime.authenticated_peer_for_binding = lambda _: (PEER, "f" * 64)
    runtime._transports = {("gateway-service", "transport"): object()}
    runtime.call = lambda *_a, **_kw: deepcopy(value)
    client = GatewayMonitorCalendarClient(
        runtime,
        identity=IDENTITY,
        scope=SCOPE,
        peer=PEER,
        service_id="gateway-service",
        instance_id="instance",
        scope_ref="scope",
    )
    return client, runtime, binding, value, lineage


def read(client):
    return client.windows(
        **{
            name: REQUEST[name]
            for name in ("route", "venue", "kind", "product", "contract", "start_utc", "end_utc")
        }
    )


def test_typed_result_preserves_simulation_and_is_detached(monkeypatch, setup_service):  # noqa: F811
    client, runtime, _, value, _ = system(monkeypatch, setup_service)
    result = read(client)
    assert type(result) is MonitorCalendarWindowsResult and result.status == "COMPLETE"
    assert (result.execution_domain_ref, result.account_ref) == ("domain", "account")
    assert result.source_authentication == "NOT_PROVEN" and result.trade_authorization == "NONE"
    assert result.policy["declaration"] == "DEPLOYMENT_DECLARED_UNVERIFIED"
    assert result.binding["coverage_status"] == "DECLARED_UNVERIFIED"
    value["windows"][0]["phase"] = "auction"
    assert result.windows[0]["phase"] == "continuous"
    with pytest.raises(TypeError):
        result.windows[0]["phase"] = "auction"
    client.close()
    assert runtime.state == "registered"


def test_unknown_and_empty_complete_do_not_imply_closed(monkeypatch, setup_service):  # noqa: F811
    client, _, _, value, _ = system(monkeypatch, setup_service)
    value["windows"] = []
    empty = read(client)
    assert empty.status == "COMPLETE" and empty.windows == ()
    value.update(status="UNKNOWN", reason="SOURCE_UNAVAILABLE", binding=None, policy=None, gaps=[])
    unknown = read(client)
    assert unknown.status == "UNKNOWN" and unknown.binding is None and unknown.windows == ()


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.update(schema="wrong"),
        lambda v: v.update(registration_id="other"),
        lambda v: v.update(service_epoch="other"),
        lambda v: v["scope"].update(contract="other"),
        lambda v: v["scope"].update(route="other"),
        lambda v: v["binding"].update(route="other"),
        lambda v: v["scope"].update(venue="OTHER"),
        lambda v: v["scope"].update(kind="option"),
        lambda v: v["scope"].update(product="other"),
        lambda v: v["scope"].update(start_utc="2026-01-01T01:31:00Z"),
        lambda v: v["binding"].update(version_sha256="broken"),
        lambda v: v["binding"].update(data_origin="REAL"),
        lambda v: v["windows"][0].update(version="another"),
        lambda v: v["windows"][0].update(start_utc="2026-01-01T01:00:00+08:00"),
        lambda v: v["windows"][0].update(end_utc=v["windows"][0]["start_utc"]),
        lambda v: v.update(windows=v["windows"] * 129),
        lambda v: v.update(reason=["invalid"]),
        lambda v: v.update(source_authentication="PROVEN"),
    ],
)
def test_malformed_or_cross_binding_wire_is_finite_failure(monkeypatch, setup_service, change):  # noqa: F811
    client, runtime, _, value, _ = system(monkeypatch, setup_service)
    change(value)
    with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
        read(client)
    assert runtime.state == "registered"


def test_current_renewal_lineage_and_forged_binding(monkeypatch, setup_service):  # noqa: F811
    client, runtime, original, _, lineage = system(monkeypatch, setup_service)
    renewed = replace(original, lease_deadline=275, expires_at=325, renewal_due_at=220)
    runtime._bindings = [renewed]
    runtime._registration_lineage[id(renewed)] = (renewed, None, *lineage, object())
    assert read(client).status == "COMPLETE"
    fake = replace(renewed)
    runtime._bindings = [fake]
    with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
        read(client)
    changed = replace(renewed, registration_id="other")
    runtime._bindings = [changed]
    runtime._registration_lineage[id(changed)] = (changed, None, *lineage)
    with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
        read(client)


def test_binding_scope_peer_thread_and_closed_runtime_refused(monkeypatch, setup_service):  # noqa: F811
    client, runtime, original, _, lineage = system(monkeypatch, setup_service)
    outcomes = []

    def in_thread():
        try:
            read(client)
        except MonitorCalendarClientError as error:
            outcomes.append(error.code)

    thread = Thread(target=in_thread)
    thread.start()
    thread.join()
    assert outcomes == ["CALENDAR_UNAVAILABLE"]
    runtime.authenticated_peer_for_binding = lambda _: (replace(PEER, actor_ref="other"), "f" * 64)
    with pytest.raises(MonitorCalendarClientError):
        read(client)
    runtime.authenticated_peer_for_binding = lambda _: (PEER, "f" * 64)
    changed = replace(original, scope=replace(SCOPE, account_ref="other"))
    runtime._bindings = [changed]
    runtime._registration_lineage[id(changed)] = (changed, None, *lineage)
    with pytest.raises(MonitorCalendarClientError):
        read(client)
    runtime._bindings = [original]
    runtime._state = "closed"
    with pytest.raises(MonitorCalendarClientError):
        read(client)
    client.close()


def test_invalid_request_is_local_and_resource_never_borrowed_when_closed(
    monkeypatch,
    setup_service,  # noqa: F811
):
    client, runtime, _, _, _ = system(monkeypatch, setup_service)
    with pytest.raises(MonitorCalendarClientError, match="INVALID_REQUEST"):
        client.windows(
            route="route-a",
            venue="SIM",
            kind="future",
            product="x",
            contract="x2601",
            start_utc="bad",
            end_utc="bad",
        )
    client.close()
    with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
        read(client)
    assert runtime.state == "registered"
    runtime._state = "closed"
    with pytest.raises(MonitorCalendarClientError, match="BINDING_UNAVAILABLE"):
        GatewayMonitorCalendarClient(
            runtime,
            identity=IDENTITY,
            scope=SCOPE,
            peer=PEER,
            service_id="gateway-service",
            instance_id="instance",
            scope_ref="scope",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("instance_id", "other"),
        ("service_id", "other"),
        ("operations", frozenset({"calendar.windows.v1"})),
        ("scope_ref", "other"),
        ("remote_lease_inactive", True),
    ],
)
def test_wrong_resource_or_registration_is_unavailable(monkeypatch, setup_service, field, value):  # noqa: F811
    client, runtime, binding, _, lineage = system(monkeypatch, setup_service)
    changed = replace(binding, **{field: value})
    runtime._bindings = [changed]
    runtime._registration_lineage[id(changed)] = (changed, None, *lineage)
    with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
        read(client)


def test_registration_change_during_call_cannot_publish_result(monkeypatch, setup_service):  # noqa: F811
    client, runtime, original, value, lineage = system(monkeypatch, setup_service)

    def replaced(*_args, **_kwargs):
        changed = replace(original, registration_id="other")
        runtime._bindings = [changed]
        runtime._registration_lineage[id(changed)] = (changed, None, *lineage)
        return deepcopy(value)

    runtime.call = replaced
    with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
        read(client)


def test_renewal_during_projection_cannot_publish_result(monkeypatch, setup_service):  # noqa: F811
    from bt_api_py.gateway_strategy_calendar import GatewayCalendarWindowsResult

    client, runtime, original, _, lineage = system(monkeypatch, setup_service)
    project = GatewayCalendarWindowsResult.from_wire

    def renew_during_projection(value):
        result = project(value)
        renewed = replace(original, lease_deadline=275, expires_at=325, renewal_due_at=220)
        runtime._bindings = [renewed]
        runtime._registration_lineage[id(renewed)] = (renewed, None, *lineage, object())
        return result

    monkeypatch.setattr(GatewayCalendarWindowsResult, "from_wire", renew_during_projection)
    with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
        read(client)


def test_response_exceeding_calendar_wire_budget_is_rejected(monkeypatch, setup_service):  # noqa: F811
    client, _, _, value, _ = system(monkeypatch, setup_service)
    base = datetime(2026, 1, 1, 1, 30, tzinfo=UTC)
    value["windows"] = []
    for index in range(128):
        start = base + timedelta(microseconds=2 * index + 1)
        end = start + timedelta(microseconds=1)
        value["windows"].append(
            {
                "window_id": f"w{index:03d}" + "x" * 124,
                "phase": "continuous",
                "trading_day": "2026-01-01",
                "start_utc": start.isoformat().replace("+00:00", "Z"),
                "end_utc": end.isoformat().replace("+00:00", "Z"),
            }
        )
    assert len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()) > 32768
    with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
        read(client)


def test_monitor_reader_import_has_no_ctp_dependency():
    script = """
import builtins, sys
original = builtins.__import__
def deny(name, *args, **kwargs):
    if name == "bt_api_ctp" or name.startswith("bt_api_ctp."):
        raise ImportError("CTP intentionally unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = deny
from bt_api_py.gateway_monitor_calendar_client import GatewayMonitorCalendarClient
assert GatewayMonitorCalendarClient
assert not any(name == "bt_api_ctp" or name.startswith("bt_api_ctp.") for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
