from __future__ import annotations

import importlib.util
import json
import sys
from collections import deque
from pathlib import Path
from uuid import UUID, uuid5

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


supervisor_module = load_module(
    "iteration72_session_supervisor_for_adapters",
    ROOT / "deploy" / "iteration72" / "session_supervisor.py",
)
adapter_module = load_module(
    "iteration72_session_supervisor_adapters",
    ROOT / "deploy" / "iteration72" / "session_supervisor_adapters.py",
)

GroupResult = supervisor_module.GroupResult
RiskGateObservation = supervisor_module.RiskGateObservation
SessionPlan = supervisor_module.SessionPlan
SessionSupervisor = supervisor_module.SessionSupervisor
StepResult = supervisor_module.StepResult
RuntimeHttpPort = adapter_module.RuntimeHttpPort
RiskWebHttpPort = adapter_module.RiskWebHttpPort

SESSION_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER_SESSION_ID = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
UNITS = ("strategy_alpha", "strategy_beta", "strategy_gamma")
GATEWAY_REFERENCE = "sha256:" + "a" * 64
RISK_SESSION = "c" * 64
CONFIRMATION = "confirmation-token-1234567890"


class FakeResponse:
    def __init__(self, mapping, *, http_status=200):
        self.mapping = mapping
        self.http_status = http_status

    def to_mapping(self):
        return dict(self.mapping)


def control_response(status: str, code: str, *, http_status=200, **data):
    return FakeResponse(
        {
            "schema": adapter_module.CONTROL_RESPONSE_SCHEMA,
            "status": status,
            "code": code,
            "identity_mode": "token",
            **data,
        },
        http_status=http_status,
    )


def unit_row(unit: str, *, state="INITIALIZED_NO_RUN", code="identity_present"):
    row = {
        "unit": unit,
        "access": "gateway",
        "state": state,
        "code": code,
        "identity_state": "present",
        "phase": None,
        "heartbeat_age_s": None,
        "heartbeat_fresh": False,
        "process_alive": False,
        "run_id": None,
        "stop_step": None,
        "clean": False,
        "pending_fields": [],
        "exit_code_observed": None,
        "detail": {},
    }
    if state == "STOPPED":
        row.update(
            {
                "code": "stopped",
                "run_id": "11111111-2222-4333-8444-555555555555",
                "stop_step": "DONE",
                "clean": True,
                "exit_code_observed": 0,
                "detail": {"summary_present": True},
            }
        )
    elif state == "RUNNING":
        row.update(
            {
                "code": "running",
                "phase": "LIVE",
                "heartbeat_age_s": 0.5,
                "heartbeat_fresh": True,
                "process_alive": True,
                "run_id": "11111111-2222-4333-8444-555555555555",
                "detail": {"last_error_code": None, "write_block_reasons": []},
            }
        )
    return row


def list_response(rows, *, status="OK", code="ok"):
    return control_response(status, code, units=list(rows), count=len(rows))


def confirmation_response(command_id: str, action: str, *, unit=None, all_units=False):
    data = {
        "command_id": command_id,
        "action": action,
        "confirmation_token": CONFIRMATION,
        "confirmation_expires_at_utc": "2030-01-01T00:00:00Z",
    }
    if unit is not None:
        data["unit"] = unit
    if all_units:
        data["all_units"] = True
    return control_response("CONFIRMATION_REQUIRED", "confirmation_required", **data)


class FakeRuntimeClient:
    def __init__(self, *, lists=(), actions=()):
        self.list_responses = deque(lists)
        self.action_responses = deque(actions)
        self.calls = []

    @staticmethod
    def _take(queue):
        item = queue.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def list_units(self, *, credential=None):
        self.calls.append(("list", credential))
        return self._take(self.list_responses)

    def unit_action(
        self,
        action,
        unit_name=None,
        *,
        credential=None,
        parameters=None,
        command_id=None,
        reason="",
        confirm=None,
        all_units=False,
        force=False,
    ):
        self.calls.append(
            (
                "action",
                {
                    "action": action,
                    "unit_name": unit_name,
                    "credential": credential,
                    "parameters": parameters,
                    "command_id": command_id,
                    "reason": reason,
                    "confirm": confirm,
                    "all_units": all_units,
                    "force": force,
                },
            )
        )
        return self._take(self.action_responses)


def runtime_port(client):
    return RuntimeHttpPort(
        client,
        step_result_factory=StepResult,
        group_result_factory=GroupResult,
    )


def test_runtime_http_factory_rejects_non_loopback_and_invalid_timeout() -> None:
    from bt_api_runtime.control_http import ControlTransportError

    with pytest.raises(ControlTransportError, match="non_loopback"):
        RuntimeHttpPort.from_http(
            host="0.0.0.0",
            port=18442,
            token="runtime-token-placeholder",
            step_result_factory=StepResult,
            group_result_factory=GroupResult,
        )
    for timeout in (True, 0, 31, float("nan")):
        with pytest.raises(ValueError, match="timeout"):
            RuntimeHttpPort.from_http(
                host="127.0.0.1",
                port=18442,
                token="runtime-token-placeholder",
                step_result_factory=StepResult,
                group_result_factory=GroupResult,
                timeout=timeout,
            )


def configured_rows(*, state="INITIALIZED_NO_RUN"):
    return [unit_row(unit, state=state) for unit in UNITS]


def start_command_id() -> str:
    return RuntimeHttpPort._command_id(
        SESSION_ID,
        "iteration72:runtime:start-all:" + RuntimeHttpPort._unit_set_token(UNITS),
    )


def successful_start_final(command_id: str):
    return control_response(
        "APPLIED",
        "started",
        command_id=command_id,
        action="start",
        all_units=True,
        units=[
            {
                "unit": unit,
                "status": "APPLIED",
                "code": "started",
                "state": "STARTING",
                "command_id": str(uuid5(UUID(command_id), unit)),
            }
            for unit in UNITS
        ],
        started_units=list(UNITS),
        not_started_units=[],
        uncertain_units=[],
        detail={},
    )


def prepare_started_port(*, extra_lists=(), extra_actions=()):
    command_id = start_command_id()
    client = FakeRuntimeClient(
        lists=[
            list_response(configured_rows()),
            list_response(configured_rows()),
            *extra_lists,
        ],
        actions=[
            confirmation_response(command_id, "start", all_units=True),
            successful_start_final(command_id),
            *extra_actions,
        ],
    )
    port = runtime_port(client)
    assert port.preflight_target_set(UNITS).status == "APPLIED"
    assert port.start_all_confirmed(SESSION_ID, UNITS).status == "APPLIED"
    return port, client


def test_runtime_start_all_uses_one_stable_two_step_confirmation() -> None:
    port, client = prepare_started_port()

    action_calls = [payload for kind, payload in client.calls if kind == "action"]
    assert len(action_calls) == 2
    first, second = action_calls
    assert first["confirm"] is None and second["confirm"] == CONFIRMATION
    assert {**first, "confirm": None} == {**second, "confirm": None}
    assert first["parameters"] == {}
    assert first["all_units"] is True and first["force"] is False
    assert first["unit_name"] is None
    assert str(UUID(first["command_id"])) == first["command_id"]
    assert first["command_id"] == start_command_id()
    assert CONFIRMATION not in repr(port)
    assert not hasattr(port, "__dict__")

    repeated = port.start_all_confirmed(SESSION_ID, UNITS)
    assert (repeated.status, repeated.code) == (
        "UNKNOWN",
        "runtime_start_already_attempted",
    )
    assert len([call for call in client.calls if call[0] == "action"]) == 2


def test_runtime_start_command_id_binds_session_and_exact_target_set() -> None:
    first = RuntimeHttpPort._command_id(
        SESSION_ID,
        "iteration72:runtime:start-all:" + RuntimeHttpPort._unit_set_token(UNITS),
    )
    changed = RuntimeHttpPort._command_id(
        SESSION_ID,
        "iteration72:runtime:start-all:"
        + RuntimeHttpPort._unit_set_token((*UNITS, "strategy_extra")),
    )
    other_session = RuntimeHttpPort._command_id(
        OTHER_SESSION_ID,
        "iteration72:runtime:start-all:" + RuntimeHttpPort._unit_set_token(UNITS),
    )

    assert len({first, changed, other_session}) == 3


@pytest.mark.parametrize(
    "rows",
    [
        configured_rows()[:-1],
        configured_rows() + [unit_row("strategy_extra")],
        configured_rows() + [unit_row(UNITS[0])],
        [{**unit_row(UNITS[0]), "state": True}, *configured_rows()[1:]],
        [{**unit_row(UNITS[0]), "phase": []}, *configured_rows()[1:]],
        [{**unit_row(UNITS[0]), "stop_step": {}}, *configured_rows()[1:]],
        [
            {**unit_row(UNITS[0]), "heartbeat_age_s": 10**10_000},
            *configured_rows()[1:],
        ],
    ],
)
def test_runtime_preflight_rejects_non_exact_or_malformed_target_set(rows) -> None:
    client = FakeRuntimeClient(lists=[list_response(rows)])
    port = runtime_port(client)

    result = port.preflight_target_set(UNITS)

    assert result.status in {"BLOCKED", "UNKNOWN"}
    assert not [call for call in client.calls if call[0] == "action"]


@pytest.mark.parametrize("http_status", [200.0, True])
def test_runtime_preflight_rejects_non_integer_http_status(http_status) -> None:
    client = FakeRuntimeClient(lists=[list_response(configured_rows())])
    client.list_responses[0].http_status = http_status

    result = runtime_port(client).preflight_target_set(UNITS)

    assert (result.status, result.code) == ("UNKNOWN", "runtime_status_unknown")
    assert not [call for call in client.calls if call[0] == "action"]


@pytest.mark.parametrize(
    "row",
    [
        unit_row(UNITS[0], state="UNKNOWN", code="runtime_record_unknown"),
        unit_row(UNITS[0], state="STOPPED_WITH_WORKING_ORDERS"),
        {**unit_row(UNITS[0]), "process_alive": True},
    ],
)
def test_runtime_preflight_blocks_unsafe_history_without_start(row) -> None:
    rows = configured_rows()
    rows[0] = row
    client = FakeRuntimeClient(lists=[list_response(rows)])
    result = runtime_port(client).preflight_target_set(UNITS)

    assert (result.status, result.code) == (
        "BLOCKED",
        "runtime_unit_not_startable",
    )
    assert not [call for call in client.calls if call[0] == "action"]


def test_runtime_first_write_transport_loss_is_unknown_and_never_retried() -> None:
    client = FakeRuntimeClient(
        lists=[list_response(configured_rows()), list_response(configured_rows())],
        actions=[TimeoutError("password=must-not-escape")],
    )
    port = runtime_port(client)
    assert port.preflight_target_set(UNITS).status == "APPLIED"

    result = port.start_all_confirmed(SESSION_ID, UNITS)

    assert (result.status, result.code) == ("UNKNOWN", "runtime_start_unknown")
    assert len([call for call in client.calls if call[0] == "action"]) == 1
    assert "must-not-escape" not in repr(result) + repr(port)


def test_runtime_second_write_transport_loss_is_unknown_and_token_is_not_retained() -> (
    None
):
    command_id = start_command_id()
    client = FakeRuntimeClient(
        lists=[list_response(configured_rows()), list_response(configured_rows())],
        actions=[
            confirmation_response(command_id, "start", all_units=True),
            ConnectionResetError("token=must-not-escape"),
        ],
    )
    port = runtime_port(client)
    assert port.preflight_target_set(UNITS).status == "APPLIED"

    result = port.start_all_confirmed(SESSION_ID, UNITS)

    assert result.status == "UNKNOWN"
    assert len([call for call in client.calls if call[0] == "action"]) == 2
    assert CONFIRMATION not in repr(result) + repr(port)
    assert "must-not-escape" not in repr(result) + repr(port)


def test_runtime_partial_start_is_preserved_as_unknown_without_rollback() -> None:
    command_id = start_command_id()
    client = FakeRuntimeClient(
        lists=[list_response(configured_rows()), list_response(configured_rows())],
        actions=[
            confirmation_response(command_id, "start", all_units=True),
            control_response(
                "UNKNOWN",
                "start_set_partial",
                command_id=command_id,
                action="start",
                all_units=True,
                units=[
                    {
                        "unit": UNITS[0],
                        "status": "APPLIED",
                        "code": "started",
                        "state": "STARTING",
                        "command_id": str(uuid5(UUID(command_id), UNITS[0])),
                    },
                    {
                        "unit": UNITS[1],
                        "status": "UNKNOWN",
                        "code": "spawn_outcome_unknown",
                        "state": "UNKNOWN",
                        "command_id": str(uuid5(UUID(command_id), UNITS[1])),
                    },
                ],
                started_units=[UNITS[0]],
                not_started_units=[UNITS[2]],
                uncertain_units=[UNITS[1]],
                detail={},
            ),
        ],
    )
    port = runtime_port(client)
    assert port.preflight_target_set(UNITS).status == "APPLIED"

    result = port.start_all_confirmed(SESSION_ID, UNITS)

    assert result.status == "UNKNOWN" and result.code == "start_set_partial"
    assert result.started_units == (UNITS[0],)
    assert result.not_started_units == (UNITS[2],)
    assert result.uncertain_units == (UNITS[1],)
    assert len([call for call in client.calls if call[0] == "action"]) == 2


def test_runtime_server_preflight_block_maps_to_blocked_group() -> None:
    command_id = start_command_id()
    client = FakeRuntimeClient(
        lists=[list_response(configured_rows()), list_response(configured_rows())],
        actions=[
            confirmation_response(command_id, "start", all_units=True),
            control_response(
                "REJECTED",
                "start_preflight_blocked",
                command_id=command_id,
                action="start",
                all_units=True,
                units=[],
                started_units=[],
                not_started_units=list(UNITS),
                uncertain_units=[],
                detail={"blockers": []},
            ),
        ],
    )
    port = runtime_port(client)
    assert port.preflight_target_set(UNITS).status == "APPLIED"

    result = port.start_all_confirmed(SESSION_ID, UNITS)

    assert result.status == "BLOCKED"
    assert result.not_started_units == UNITS


def test_runtime_success_requires_each_child_result_and_bound_child_id() -> None:
    command_id = start_command_id()
    malformed_success = control_response(
        "APPLIED",
        "started",
        command_id=command_id,
        action="start",
        all_units=True,
        units=[],
        started_units=list(UNITS),
        not_started_units=[],
        uncertain_units=[],
        detail={},
    )
    client = FakeRuntimeClient(
        lists=[list_response(configured_rows()), list_response(configured_rows())],
        actions=[
            confirmation_response(command_id, "start", all_units=True),
            malformed_success,
        ],
    )
    port = runtime_port(client)
    assert port.preflight_target_set(UNITS).status == "APPLIED"

    result = port.start_all_confirmed(SESSION_ID, UNITS)

    assert (result.status, result.code) == ("UNKNOWN", "runtime_start_unknown")
    assert result.uncertain_units == UNITS


def test_runtime_success_rejects_stopped_child_state() -> None:
    command_id = start_command_id()
    malformed_success = successful_start_final(command_id)
    malformed_success.mapping["units"][0]["state"] = "STOPPED"
    client = FakeRuntimeClient(
        lists=[list_response(configured_rows()), list_response(configured_rows())],
        actions=[
            confirmation_response(command_id, "start", all_units=True),
            malformed_success,
        ],
    )
    port = runtime_port(client)
    assert port.preflight_target_set(UNITS).status == "APPLIED"

    result = port.start_all_confirmed(SESSION_ID, UNITS)

    assert (result.status, result.code) == ("UNKNOWN", "runtime_start_unknown")
    assert result.uncertain_units == UNITS


def test_runtime_partial_start_rejects_inconsistent_aggregate_classification() -> None:
    command_id = start_command_id()
    client = FakeRuntimeClient(
        lists=[list_response(configured_rows()), list_response(configured_rows())],
        actions=[
            confirmation_response(command_id, "start", all_units=True),
            control_response(
                "UNKNOWN",
                "start_set_partial",
                command_id=command_id,
                action="start",
                all_units=True,
                units=[
                    {
                        "unit": UNITS[0],
                        "status": "APPLIED",
                        "code": "started",
                        "state": "STARTING",
                        "command_id": str(uuid5(UUID(command_id), UNITS[0])),
                    },
                    {
                        "unit": UNITS[1],
                        "status": "UNKNOWN",
                        "code": "spawn_outcome_unknown",
                        "state": "UNKNOWN",
                        "command_id": str(uuid5(UUID(command_id), UNITS[1])),
                    },
                ],
                started_units=[UNITS[0]],
                not_started_units=[UNITS[1], UNITS[2]],
                uncertain_units=[],
                detail={},
            ),
        ],
    )
    port = runtime_port(client)
    assert port.preflight_target_set(UNITS).status == "APPLIED"

    result = port.start_all_confirmed(SESSION_ID, UNITS)

    assert (result.status, result.code) == ("UNKNOWN", "runtime_start_unknown")
    assert result.uncertain_units == UNITS


def test_runtime_running_status_requires_live_fresh_exact_set() -> None:
    port, _ = prepare_started_port(
        extra_lists=[list_response(configured_rows(state="RUNNING"))]
    )

    result = port.status_group(SESSION_ID, UNITS)

    assert (result.status, result.code, result.started_units) == (
        "APPLIED",
        "running",
        UNITS,
    )


def test_runtime_warmup_status_is_unknown_not_running() -> None:
    rows = configured_rows(state="RUNNING")
    rows[0]["phase"] = "WARMUP"
    port, _ = prepare_started_port(extra_lists=[list_response(rows)])

    result = port.status_group(SESSION_ID, UNITS)

    assert result.status == "UNKNOWN"
    assert UNITS[0] in result.uncertain_units


def test_runtime_cooperative_stop_uses_exact_single_unit_two_step_request() -> None:
    target = UNITS[-1]
    command_id = RuntimeHttpPort._command_id(
        SESSION_ID, "iteration72:runtime:cooperative-stop:" + target
    )
    stopped_rows = configured_rows(state="RUNNING")
    stopped_rows[-1] = unit_row(target, state="STOPPED")
    port, client = prepare_started_port(
        extra_lists=[list_response(stopped_rows)],
        extra_actions=[
            confirmation_response(command_id, "stop", unit=target),
            control_response(
                "APPLIED",
                "stopped",
                command_id=command_id,
                action="stop",
                unit=target,
                state="STOPPED",
                detail={},
                steps=[],
            ),
        ],
    )

    result = port.stop_cooperatively_confirmed(SESSION_ID, target)

    assert (result.status, result.code, result.exit_code) == (
        "APPLIED",
        "stopped",
        0,
    )
    calls = [payload for kind, payload in client.calls if kind == "action"][-2:]
    assert calls[0]["confirm"] is None and calls[1]["confirm"] == CONFIRMATION
    assert {**calls[0], "confirm": None} == {**calls[1], "confirm": None}
    assert calls[0]["parameters"] == {"cooperative_only": True}
    assert calls[0]["all_units"] is False and calls[0]["force"] is False
    assert calls[0]["unit_name"] == target


def test_runtime_cooperative_stop_requires_clean_zero_exit_status_readback() -> None:
    target = UNITS[-1]
    command_id = RuntimeHttpPort._command_id(
        SESSION_ID, "iteration72:runtime:cooperative-stop:" + target
    )
    stopped_rows = configured_rows(state="RUNNING")
    stopped_rows[-1] = unit_row(target, state="STOPPED")
    stopped_rows[-1]["exit_code_observed"] = 9
    port, _ = prepare_started_port(
        extra_lists=[list_response(stopped_rows)],
        extra_actions=[
            confirmation_response(command_id, "stop", unit=target),
            control_response(
                "APPLIED",
                "stopped",
                command_id=command_id,
                action="stop",
                unit=target,
                state="STOPPED",
                detail={},
                steps=[],
            ),
        ],
    )

    result = port.stop_cooperatively_confirmed(SESSION_ID, target)

    assert (result.status, result.code, result.exit_code) == (
        "UNKNOWN",
        "runtime_stop_unknown",
        None,
    )


def test_runtime_cooperative_stop_loss_is_unknown_without_fallback_or_retry() -> None:
    target = UNITS[-1]
    command_id = RuntimeHttpPort._command_id(
        SESSION_ID, "iteration72:runtime:cooperative-stop:" + target
    )
    port, client = prepare_started_port(
        extra_actions=[
            confirmation_response(command_id, "stop", unit=target),
            TimeoutError("private response"),
        ]
    )

    result = port.stop_cooperatively_confirmed(SESSION_ID, target)
    repeated = port.stop_cooperatively_confirmed(SESSION_ID, target)

    assert result.status == "UNKNOWN" and repeated.status == "UNKNOWN"
    calls = [payload for kind, payload in client.calls if kind == "action"]
    assert len(calls) == 4
    assert all(call["force"] is False for call in calls)
    assert not any(call["all_units"] for call in calls[-2:])


def test_runtime_rejects_wrong_session_or_unit_without_control_write() -> None:
    port, client = prepare_started_port()
    before = len([call for call in client.calls if call[0] == "action"])

    wrong_session = port.stop_cooperatively_confirmed(OTHER_SESSION_ID, UNITS[0])
    wrong_unit = port.stop_cooperatively_confirmed(SESSION_ID, "strategy_extra")

    assert wrong_session.status == "UNKNOWN" and wrong_unit.status == "UNKNOWN"
    assert len([call for call in client.calls if call[0] == "action"]) == before


def test_runtime_sensitive_reply_is_unknown_and_never_reflected() -> None:
    command_id = start_command_id()
    private_value = "do-not-reflect-private-value"
    client = FakeRuntimeClient(
        lists=[list_response(configured_rows()), list_response(configured_rows())],
        actions=[
            confirmation_response(command_id, "start", all_units=True),
            control_response(
                "APPLIED",
                "started",
                command_id=command_id,
                action="start",
                all_units=True,
                units=[],
                started_units=list(UNITS),
                not_started_units=[],
                uncertain_units=[],
                detail={"password": private_value},
            ),
        ],
    )
    port = runtime_port(client)
    assert port.preflight_target_set(UNITS).status == "APPLIED"

    result = port.start_all_confirmed(SESSION_ID, UNITS)

    assert result.status == "UNKNOWN"
    assert private_value not in repr(result) + repr(port)


class FakeRiskResponse:
    def __init__(self, body: bytes, *, status=200, content_type="application/json"):
        self._body = body
        self.status = status
        self._content_type = content_type

    def read(self, limit):
        return self._body[:limit]

    def getheader(self, name):
        if name.lower() == "content-type":
            return self._content_type
        return None


class FakeRiskConnection:
    def __init__(self, host, port, *, timeout, script, calls):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.script = script
        self.calls = calls
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "headers": dict(headers or {}),
                "host": self.host,
                "port": self.port,
                "timeout": self.timeout,
            }
        )

    def getresponse(self):
        if isinstance(self.script, BaseException):
            raise self.script
        return self.script

    def close(self):
        self.closed = True


def install_risk_transport(monkeypatch, script):
    calls = []
    connections = []

    def factory(host, port, timeout):
        connection = FakeRiskConnection(
            host, port, timeout=timeout, script=script, calls=calls
        )
        connections.append(connection)
        return connection

    monkeypatch.setattr(adapter_module.http.client, "HTTPConnection", factory)
    return calls, connections


def risk_body(*, checked=True, ready=True, extra_summary=None):
    summary = {
        "available": True,
        "order_cut_admission": {"checked": checked, "ready": ready},
        **(extra_summary or {}),
    }
    return json.dumps({"status": "OBSERVED", "summary": summary}).encode()


def risk_port():
    return RiskWebHttpPort(
        origin="http://127.0.0.1:18443",
        session_token=RISK_SESSION,
        gateway_reference=GATEWAY_REFERENCE,
        observation_factory=RiskGateObservation,
        timeout=1.0,
    )


@pytest.mark.parametrize(
    "origin",
    [
        "https://127.0.0.1:18443",
        "http://localhost:18443",
        "http://0.0.0.0:18443",
        "http://user@127.0.0.1:18443",
        "http://127.0.0.1:18443/api",
        "http://127.0.0.1:18443?x=1",
    ],
)
def test_risk_port_rejects_non_exact_loopback_origin(origin) -> None:
    with pytest.raises(ValueError, match="loopback"):
        RiskWebHttpPort(
            origin=origin,
            session_token=RISK_SESSION,
            gateway_reference=GATEWAY_REFERENCE,
            observation_factory=RiskGateObservation,
        )


@pytest.mark.parametrize("timeout", [True, 0, -1, 31, float("nan")])
def test_risk_port_rejects_invalid_timeout_without_exposing_session(timeout) -> None:
    with pytest.raises(ValueError) as raised:
        RiskWebHttpPort(
            origin="http://127.0.0.1:18443",
            session_token=RISK_SESSION,
            gateway_reference=GATEWAY_REFERENCE,
            observation_factory=RiskGateObservation,
            timeout=timeout,
        )
    assert RISK_SESSION not in str(raised.value)


def test_risk_get_uses_fixed_path_once_but_keeps_missing_gateway_proof_unknown(
    monkeypatch,
) -> None:
    calls, connections = install_risk_transport(
        monkeypatch, FakeRiskResponse(risk_body())
    )
    port = risk_port()

    observation = port.observe_r05(GATEWAY_REFERENCE)

    assert (observation.checked, observation.ready) == (True, True)
    assert observation.gateway_reference is None
    assert observation.observation_reference is None
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == adapter_module.RISK_STATUS_PATH
    assert calls[0]["body"] is None
    assert calls[0]["headers"]["x-risk-session"] == RISK_SESSION
    assert calls[0]["host"] == "127.0.0.1"
    assert connections[0].closed is True
    assert RISK_SESSION not in repr(port) + repr(observation)
    assert not hasattr(port, "__dict__")


def test_real_risk_adapter_keeps_supervisor_before_execution_unknown(
    monkeypatch,
) -> None:
    calls, _ = install_risk_transport(monkeypatch, FakeRiskResponse(risk_body()))

    class Components:
        def __init__(self):
            self.started = []

        def preflight(self, _component):
            return StepResult("APPLIED", "ready")

        def start(self, component):
            self.started.append(component)
            return StepResult(
                "APPLIED",
                "started",
                reference=GATEWAY_REFERENCE if component == "gateway" else None,
                exit_code=0,
            )

        def stop_cooperatively(self, _component):
            return StepResult("APPLIED", "stopped", exit_code=0)

    class Runtime:
        def __init__(self):
            self.start_calls = 0

        def preflight_target_set(self, _units):
            return StepResult("APPLIED", "ready")

        def start_all_confirmed(self, _session_id, _units):
            self.start_calls += 1
            return GroupResult("UNKNOWN", "unreachable")

        def status_group(self, _session_id, _units):
            return GroupResult("UNKNOWN", "unreachable")

        def stop_cooperatively_confirmed(self, _session_id, _unit):
            return StepResult("UNKNOWN", "unreachable")

    class Receipt:
        def __init__(self):
            self.events = []

        @property
        def next_sequence(self):
            return len(self.events) + 1

        def append(self, event):
            self.events.append(event)

    components = Components()
    runtime = Runtime()
    receipt = Receipt()
    supervisor = SessionSupervisor(
        SessionPlan(SESSION_ID, UNITS),
        components=components,
        runtime=runtime,
        risk=risk_port(),
        receipt=receipt,
    )

    outcome = supervisor.start_session()

    assert (outcome.status, outcome.code, outcome.exit_code) == (
        "UNKNOWN",
        "session_unknown",
        3,
    )
    assert components.started == list(supervisor_module.COMPONENT_START_ORDER[:6])
    assert runtime.start_calls == 0
    assert [event.code for event in receipt.events].count("risk_gateway_mismatch") == 1
    assert len(calls) == 1


def test_risk_gateway_mismatch_avoids_http_and_fails_closed(monkeypatch) -> None:
    calls, _ = install_risk_transport(monkeypatch, FakeRiskResponse(risk_body()))
    port = risk_port()

    observation = port.observe_r05("sha256:" + "f" * 64)

    assert (observation.checked, observation.ready) == (False, False)
    assert observation.gateway_reference is None
    assert calls == []


@pytest.mark.parametrize(
    "script",
    [
        FakeRiskResponse(b"not-json"),
        FakeRiskResponse(b'{"status":"OBSERVED","status":"OBSERVED","summary":{}}'),
        FakeRiskResponse(risk_body(), status=502),
        FakeRiskResponse(risk_body(), content_type="text/plain"),
        FakeRiskResponse(
            json.dumps(
                {
                    "status": "OBSERVED",
                    "summary": {
                        "order_cut_admission": {"checked": False, "ready": True}
                    },
                }
            ).encode()
        ),
        FakeRiskResponse(risk_body(extra_summary={"password": "private-value"})),
        FakeRiskResponse(
            b'{"status":"OBSERVED","summary":'
            + b"[" * 2_000
            + b"0"
            + b"]" * 2_000
            + b"}"
        ),
        FakeRiskResponse(b"{" + b"x" * adapter_module.MAX_RISK_RESPONSE_BYTES),
        TimeoutError("token=private-value"),
    ],
)
def test_risk_malformed_non_200_oversized_or_transport_failure_is_unknown_once(
    monkeypatch, script
) -> None:
    calls, connections = install_risk_transport(monkeypatch, script)
    port = risk_port()

    observation = port.observe_r05(GATEWAY_REFERENCE)

    assert (observation.checked, observation.ready) == (False, False)
    assert observation.gateway_reference is None
    assert observation.observation_reference is None
    assert len(calls) == 1
    assert connections[0].closed is True
    assert "private-value" not in repr(observation) + repr(port)


def test_risk_valid_not_ready_is_distinct_but_still_has_no_source_bound_reference(
    monkeypatch,
) -> None:
    calls, _ = install_risk_transport(
        monkeypatch, FakeRiskResponse(risk_body(checked=True, ready=False))
    )
    port = risk_port()

    first = port.observe_r05(GATEWAY_REFERENCE)
    second = port.observe_r05(GATEWAY_REFERENCE)

    assert (first.checked, first.ready) == (True, False)
    assert first.gateway_reference is None and second.gateway_reference is None
    assert first.observation_reference is None and second.observation_reference is None
    assert len(calls) == 2
