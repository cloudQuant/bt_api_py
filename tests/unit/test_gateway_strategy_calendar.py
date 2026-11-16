"""Strategy calendar configuration, retained calls and component selection."""
import copy
from pathlib import Path

import pytest
import yaml

from bt_api_py.gateway_strategy_calendar import GatewayCalendarClientSettings, GatewayStrategyCalendarClient
from bt_api_py.gateway_strategy_config import GatewayStrategyConfigError, parse_gateway_strategy_config
from bt_api_py.service_components import ServiceComponentError
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime_thread import ThreadedRuntimeError
from tests.integration.test_iter63_gateway_direct_service import _service_config
from tests.integration.test_iter63_gateway_dual_ports import _resources
from tests.unit.test_iter63_gateway_dual_ports import _build, _prepare
from tests.unit.test_iter63_gateway_strategy_client import Host, parts


def raw_config():
    path = Path(__file__).resolve().parents[2] / "examples/services/gateway-strategy-client.example.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def scope(route="CTP___FUTURE"):
    return dict(route=route, venue="SIM", kind="future", product="x", contract="x2601")


def test_explicit_calendar_composes_and_old_config_stays_compatible():
    raw = raw_config()
    assert parse_gateway_strategy_config(raw).calendar_settings is None
    mounts = raw["client_config"]["clients"]["gateway"]["settings"]["bindings"]
    mounts.append(dict(mounts[0], mount="store.calendar"))
    raw["calendar_settings"] = {"mode": "SIMULATION_ONLY", "scopes": [scope()]}
    config = parse_gateway_strategy_config(raw)
    session = config.create_session(host=Host(), cancel_host=Host(), security_profiles=parts()["security_profiles"])
    assert session.calendar is not None
    raw["calendar_settings"]["scopes"][0]["contract"] = "changed"
    assert config.calendar_settings.permits(scope())
    session.close()


@pytest.mark.parametrize("edit", [
    lambda x: x.update(calendar_settings=None),
    lambda x: x["calendar_settings"].update(mode="LIVE"),
    lambda x: x["calendar_settings"]["scopes"][0].update(route="other"),
    lambda x: x["calendar_settings"]["scopes"].append(scope()),
    lambda x: x["client_config"]["clients"]["gateway"]["settings"]["bindings"][-1].update(instance_id="other"),
])
def test_invalid_calendar_settings_or_mismatched_binding_rejected(edit):
    raw = raw_config()
    mounts = raw["client_config"]["clients"]["gateway"]["settings"]["bindings"]
    mounts.append(dict(mounts[0], mount="store.calendar"))
    raw["calendar_settings"] = {"mode": "SIMULATION_ONLY", "scopes": [scope()]}
    edit(raw)
    with pytest.raises(GatewayStrategyConfigError):
        parse_gateway_strategy_config(raw)


def test_calendar_port_requires_explicit_exact_service_resource(tmp_path):
    raw = _service_config("tcp://127.0.0.1:12345").to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.calendar_read")
    prepared = _prepare(parse_service_config(raw))
    assert prepared.operations_by_port["service.calendar_read"] == {"calendar.query.v1"}
    direct, _, _ = _resources(tmp_path)
    with pytest.raises(ServiceComponentError, match="INVALID_CALENDAR_SERVICE_BINDING"):
        _build(prepared, {"binding": direct})
    with pytest.raises(ServiceComponentError, match="INVALID_CALENDAR_SERVICE_BINDING"):
        _build(prepared, {"binding": direct, "calendar_service": object()})


def test_timeout_retains_original_handle_and_rejects_other_query():
    class Handle:
        def result(self, *, timeout_seconds):
            raise ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")
    class Runtime:
        def __init__(self): self.submitted = 0
        def submit_owner(self, *_):
            self.submitted += 1
            return Handle()
    runtime = Runtime()
    settings = GatewayCalendarClientSettings("SIMULATION_ONLY", (scope(),))
    client = GatewayStrategyCalendarClient(runtime, settings, route=parts()["route"], peer=parts()["peer"])
    args = dict(scope(), trading_day="2026-01-01")
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.query(**args)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
        client.query(**dict(args, trading_day="2026-01-02"))
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.query(**args)
    assert runtime.submitted == 1


def test_resource_failure_closes_calendar_service(tmp_path):
    from bt_api_py.gateway_calendar_service import CalendarGrant, GatewayCalendarService
    raw = _service_config("tcp://127.0.0.1:12345").to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.calendar_read")
    direct, _, _ = _resources(tmp_path)
    service = GatewayCalendarService(grants=frozenset({CalendarGrant("p", "d", "wrong-account", "s", "run", "r", "SIM", "future", "x", "x1")}))
    with pytest.raises(ServiceComponentError, match="INVALID_CALENDAR_SERVICE_BINDING"):
        _build(_prepare(parse_service_config(raw)), {"binding": direct, "calendar_service": service})
    with pytest.raises(ValueError):
        service.rebind(None)


def test_owner_rejects_old_epoch_and_registration_change():
    from dataclasses import replace
    from bt_api_py.gateway_strategy_calendar import GatewayCalendarOwner
    from bt_api_py.service_runtime import RegisteredBinding
    from bt_api_py.service_runtime_thread import OwnerOperationError
    values = parts()
    route, peer = values["route"], values["peer"]
    selected = RegisteredBinding("store.calendar", "gateway", "instance", "scope", route.scope,
        "registration-new", "epoch-new", frozenset({"calendar.query.v1"}), 1000, "policy", "requirements",
        "transport", 5, lease_deadline=1000)
    args = dict(mode="SIMULATION_ONLY", **scope(), trading_day="2026-01-01")
    response = dict(schema="sdk.gateway.calendar.query/1", scope=args, registration_id="registration-new",
        service_epoch="epoch-old", status="UNKNOWN", reason="SOURCE_UNAVAILABLE", binding=None,
        venue_day={"status": "UNKNOWN", "day_status": None}, windows=[],
        source_authentication="NOT_PROVEN", trade_authorization="NONE")
    class Runtime:
        bindings = (selected,)
        identity = route.identity
        run_id = route.scope.run_id
        def authenticated_peer_for_binding(self, b): return (peer, "fingerprint")
        def _monotonic_now(self): return 1
        def call(self, *_args, **_kwargs): return copy.deepcopy(response)
    runtime = Runtime()
    owner = GatewayCalendarOwner(GatewayCalendarClientSettings("SIMULATION_ONLY", (scope(),)),
        account_ref="account", scope_ref="scope", route=route, peer=peer)
    owner.start(runtime)
    with pytest.raises(OwnerOperationError, match="CALENDAR_OUTCOME_UNKNOWN"):
        owner.invoke("query", args)
    response["service_epoch"] = "epoch-new"
    def changed(*_args, **_kwargs):
        runtime.bindings = (replace(selected, registration_id="registration-other"),)
        return copy.deepcopy(response)
    runtime.call = changed
    with pytest.raises(OwnerOperationError, match="CALENDAR_OUTCOME_UNKNOWN"):
        owner.invoke("query", args)
    owner.close()


@pytest.mark.parametrize("change", ["registration", "closed"])
def test_timed_out_completed_query_cannot_be_collected_after_session_change(change):
    from bt_api_py.service_runtime_thread import OwnerOperationError
    args = dict(scope(), trading_day="2026-01-01")
    wire = dict(schema="sdk.gateway.calendar.query/1", scope=dict(mode="SIMULATION_ONLY", **args),
        registration_id="old-registration", service_epoch="old-epoch", status="UNKNOWN",
        reason="SOURCE_UNAVAILABLE", binding=None, venue_day={"status": "UNKNOWN", "day_status": None},
        windows=[], source_authentication="NOT_PROVEN", trade_authorization="NONE")
    class Query:
        def __init__(self): self.first = True
        def result(self, *, timeout_seconds):
            if self.first:
                self.first = False
                raise ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")
            return copy.deepcopy(wire)
    class Validation:
        def result(self, *, timeout_seconds): raise OwnerOperationError("CALENDAR_OUTCOME_UNKNOWN")
    class Runtime:
        def __init__(self): self.queries = 0
        def submit_owner(self, operation, arguments):
            if operation == "calendar.query":
                self.queries += 1
                return Query()
            assert operation == "calendar.validate" and arguments == {"registration_id": "old-registration", "service_epoch": "old-epoch"}
            if change == "closed":
                raise ThreadedRuntimeError("NOT_READY")
            return Validation()
    runtime = Runtime()
    client = GatewayStrategyCalendarClient(runtime, GatewayCalendarClientSettings("SIMULATION_ONLY", (scope(),)), route=parts()["route"], peer=parts()["peer"])
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.query(**args)
    with pytest.raises(OwnerOperationError, match="CALENDAR_OUTCOME_UNKNOWN"):
        client.query(**args)
    assert runtime.queries == 1


def test_one_deadline_covers_query_validation_and_retry_drain(monkeypatch):
    from types import SimpleNamespace
    import bt_api_py.gateway_strategy_calendar as calendar_module

    now = [0.0]
    monkeypatch.setattr(calendar_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    values = parts()
    route, peer = values["route"], values["peer"]
    args = dict(scope(), trading_day="2026-01-01")
    wire = dict(schema="sdk.gateway.calendar.query/1", scope=dict(mode="SIMULATION_ONLY", **args),
        registration_id="registration", service_epoch="epoch", status="UNKNOWN",
        reason="SOURCE_UNAVAILABLE", binding=None, venue_day={"status": "UNKNOWN", "day_status": None},
        windows=[], source_authentication="NOT_PROVEN", trade_authorization="NONE")
    waits = []
    class Query:
        def result(self, *, timeout_seconds):
            waits.append(("query", timeout_seconds))
            now[0] += 2
            return copy.deepcopy(wire)
    class Validation:
        def __init__(self, first): self.first = first
        def result(self, *, timeout_seconds):
            waits.append(("validate", timeout_seconds))
            if self.first:
                self.first = False
                now[0] += 3
            else:
                now[0] += 1
            return {"valid": True}
    class Runtime:
        def __init__(self): self.operations = []
        def submit_owner(self, operation, arguments):
            self.operations.append(operation)
            return Query() if operation == "calendar.query" else Validation(len(self.operations) == 2)
        def status(self):
            item = SimpleNamespace(mount="store.calendar", active=True, registration_id="registration",
                service_epoch="epoch", service_id=route.service_id, instance_id=route.instance_id,
                scope_ref=route.scope_ref, scope=route.scope, authenticated_peer=peer,
                server_key_fingerprint="fingerprint")
            return SimpleNamespace(state="running", healthy=True, bindings=(item,),
                identity=route.identity, run_id=route.scope.run_id)
    runtime = Runtime()
    client = GatewayStrategyCalendarClient(runtime, GatewayCalendarClientSettings("SIMULATION_ONLY", (scope(),)),
        route=route, peer=peer)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.query(**args, timeout_seconds=5)
    assert waits == [("query", 5), ("validate", 3)]
    assert runtime.operations == ["calendar.query", "calendar.validate"]
    result = client.query(**args, timeout_seconds=5)
    assert result.registration_id == "registration"
    assert waits == [("query", 5), ("validate", 3), ("validate", 5), ("validate", 4)]
    assert runtime.operations == ["calendar.query", "calendar.validate", "calendar.validate"]


def test_exhausted_query_budget_defers_validation_and_rechecks_old_generation(monkeypatch):
    from types import SimpleNamespace
    import bt_api_py.gateway_strategy_calendar as calendar_module
    from bt_api_py.service_runtime_thread import OwnerOperationError

    now = [0.0]
    monkeypatch.setattr(calendar_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    args = dict(scope(), trading_day="2026-01-01")
    wire = dict(schema="sdk.gateway.calendar.query/1", scope=dict(mode="SIMULATION_ONLY", **args),
        registration_id="old-registration", service_epoch="old-epoch", status="UNKNOWN",
        reason="SOURCE_UNAVAILABLE", binding=None, venue_day={"status": "UNKNOWN", "day_status": None},
        windows=[], source_authentication="NOT_PROVEN", trade_authorization="NONE")
    class Query:
        def result(self, *, timeout_seconds):
            assert timeout_seconds == 5
            now[0] += 5
            return copy.deepcopy(wire)
    class Validation:
        def result(self, *, timeout_seconds):
            raise OwnerOperationError("CALENDAR_OUTCOME_UNKNOWN")
    class Runtime:
        def __init__(self): self.operations = []
        def submit_owner(self, operation, arguments):
            self.operations.append((operation, arguments))
            return Query() if operation == "calendar.query" else Validation()
    runtime = Runtime()
    values = parts()
    client = GatewayStrategyCalendarClient(runtime, GatewayCalendarClientSettings("SIMULATION_ONLY", (scope(),)),
        route=values["route"], peer=values["peer"])
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.query(**args, timeout_seconds=5)
    assert [operation for operation, _ in runtime.operations] == ["calendar.query"]
    with pytest.raises(OwnerOperationError, match="CALENDAR_OUTCOME_UNKNOWN"):
        client.query(**args, timeout_seconds=5)
    assert runtime.operations[1] == ("calendar.validate", {"registration_id": "old-registration", "service_epoch": "old-epoch"})
    assert len(runtime.operations) == 2
