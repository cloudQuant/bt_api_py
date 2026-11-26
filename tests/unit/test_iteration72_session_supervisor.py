from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "deploy" / "iteration72" / "session_supervisor.py"
SPEC = importlib.util.spec_from_file_location(
    "iteration72_session_supervisor", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
supervisor_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = supervisor_module
SPEC.loader.exec_module(supervisor_module)

COMPONENT_START_ORDER = supervisor_module.COMPONENT_START_ORDER
COMPONENT_STOP_ORDER = supervisor_module.COMPONENT_STOP_ORDER
GroupResult = supervisor_module.GroupResult
JsonlReceipt = supervisor_module.JsonlReceipt
ReceiptEvent = supervisor_module.ReceiptEvent
RECEIPT_SCHEMA = supervisor_module.RECEIPT_SCHEMA
RiskGateObservation = supervisor_module.RiskGateObservation
SessionOutcome = supervisor_module.SessionOutcome
SessionPlan = supervisor_module.SessionPlan
SessionSupervisor = supervisor_module.SessionSupervisor
StepResult = supervisor_module.StepResult


SESSION_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
UNITS = ("strategy_alpha", "strategy_beta", "strategy_gamma")


def digest(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


class MemoryReceipt:
    def __init__(self) -> None:
        self.events = []

    @property
    def next_sequence(self) -> int:
        return len(self.events) + 1

    def append(self, event) -> None:
        assert event.sequence == self.next_sequence
        self.events.append(event)


class FailingReceipt(MemoryReceipt):
    def append(self, event) -> None:
        if (
            event.action == "component_start"
            and event.subject == "monitor"
            and event.status == "APPLIED"
        ):
            raise OSError("simulated append failure")
        super().append(event)


class FailingIntentReceipt(MemoryReceipt):
    def append(self, event) -> None:
        if (
            event.action == "component_start"
            and event.subject == "monitor"
            and event.status == "INTENT"
        ):
            raise OSError("simulated intent append failure")
        super().append(event)


class FakeComponents:
    def __init__(self) -> None:
        self.calls = []
        self.preflight_by = {}
        self.start_by = {}
        self.stop_by = {}

    def preflight(self, component: str):
        self.calls.append(("preflight", component))
        return self.preflight_by.get(component, StepResult("APPLIED", "ready"))

    def start(self, component: str):
        self.calls.append(("start", component))
        default = StepResult(
            "APPLIED",
            "started",
            reference=digest("a") if component == "gateway" else None,
            exit_code=0,
        )
        return self.start_by.get(component, default)

    def stop_cooperatively(self, component: str):
        self.calls.append(("stop_cooperatively", component))
        return self.stop_by.get(
            component, StepResult("APPLIED", "stopped", exit_code=0)
        )


class FakeRuntime:
    def __init__(self) -> None:
        self.calls = []
        self.preflight_result = StepResult("APPLIED", "ready")
        self.start_result = GroupResult(
            "APPLIED",
            "started",
            started_units=UNITS,
            reference=digest("b"),
        )
        self.status_result = GroupResult(
            "APPLIED",
            "running",
            started_units=UNITS,
            reference=digest("c"),
        )
        self.stop_by = {}

    def preflight_target_set(self, units):
        self.calls.append(("preflight_target_set", units))
        return self.preflight_result

    def start_all_confirmed(self, session_id, units):
        self.calls.append(("start_all_confirmed", session_id, units))
        return self.start_result

    def status_group(self, session_id, units):
        self.calls.append(("status_group", session_id, units))
        return self.status_result

    def stop_cooperatively_confirmed(self, session_id, unit):
        self.calls.append(("stop_cooperatively_confirmed", session_id, unit))
        return self.stop_by.get(unit, StepResult("APPLIED", "stopped", exit_code=0))


class FakeRisk:
    def __init__(self, observations=None) -> None:
        self.calls = []
        self.observations = list(
            observations
            or [
                RiskGateObservation(True, True, digest("a"), digest("c")),
                RiskGateObservation(True, True, digest("a"), digest("d")),
            ]
        )

    def observe_r05(self, expected_gateway_reference):
        self.calls.append(expected_gateway_reference)
        return self.observations.pop(0)


def build_supervisor(*, components=None, runtime=None, risk=None, receipt=None):
    components = components or FakeComponents()
    runtime = runtime or FakeRuntime()
    risk = risk or FakeRisk()
    receipt = receipt or MemoryReceipt()
    session = SessionSupervisor(
        SessionPlan(SESSION_ID, UNITS),
        components=components,
        runtime=runtime,
        risk=risk,
        receipt=receipt,
    )
    return session, components, runtime, risk, receipt


def test_preflight_blocker_prevents_every_start() -> None:
    components = FakeComponents()
    components.preflight_by["gateway"] = StepResult("BLOCKED", "not_ready")
    session, _, runtime, risk, receipt = build_supervisor(components=components)

    outcome = session.start_session()

    assert (outcome.status, outcome.exit_code, outcome.phase) == (
        "BLOCKED",
        2,
        "BLOCKED",
    )
    assert not [call for call in components.calls if call[0] == "start"]
    assert not [call for call in runtime.calls if call[0] == "start_all_confirmed"]
    assert risk.calls == []
    assert any(event.code == "component_preflight_blocked" for event in receipt.events)


def test_fixed_start_order_two_r05_checks_and_one_group_start() -> None:
    session, components, runtime, risk, receipt = build_supervisor()

    outcome = session.start_session()

    assert (outcome.status, outcome.code, outcome.exit_code) == (
        "APPLIED",
        "session_ready",
        0,
    )
    assert [value for action, value in components.calls if action == "start"] == list(
        COMPONENT_START_ORDER
    )
    assert risk.calls == [digest("a"), digest("a")]
    assert [call[0] for call in runtime.calls].count("start_all_confirmed") == 1
    applied_r05 = [
        event
        for event in receipt.events
        if event.action == "risk_r05" and event.status == "APPLIED"
    ]
    assert [event.subject for event in applied_r05] == [
        "risk_r05_before_execution",
        "risk_r05_before_strategy_group",
    ]
    assert session.start_session() is outcome
    assert [call[0] for call in runtime.calls].count("start_all_confirmed") == 1


@pytest.mark.parametrize(
    "observation,expected_code",
    [
        (
            RiskGateObservation(True, False, digest("a"), digest("c")),
            "risk_r05_unready",
        ),
        (
            RiskGateObservation(True, True, digest("b"), digest("c")),
            "risk_gateway_mismatch",
        ),
    ],
)
def test_r05_fails_closed_before_execution(observation, expected_code) -> None:
    components = FakeComponents()
    risk = FakeRisk([observation])
    session, _, runtime, _, receipt = build_supervisor(components=components, risk=risk)

    outcome = session.start_session()

    assert outcome.status in {"BLOCKED", "UNKNOWN"}
    started = [value for action, value in components.calls if action == "start"]
    assert started == list(COMPONENT_START_ORDER[:6])
    assert "execution" not in started
    assert not [call for call in runtime.calls if call[0] == "start_all_confirmed"]
    assert any(event.code == expected_code for event in receipt.events)


def test_second_r05_failure_blocks_group_without_rollback() -> None:
    risk = FakeRisk(
        [
            RiskGateObservation(True, True, digest("a"), digest("c")),
            RiskGateObservation(True, False, digest("a"), digest("d")),
        ]
    )
    session, components, runtime, _, receipt = build_supervisor(risk=risk)

    outcome = session.start_session()

    assert (outcome.status, outcome.exit_code) == ("BLOCKED", 2)
    assert [value for action, value in components.calls if action == "start"] == list(
        COMPONENT_START_ORDER
    )
    assert not [call for call in runtime.calls if call[0] == "start_all_confirmed"]
    assert not [call for call in components.calls if call[0] == "stop_cooperatively"]
    assert [event.code for event in receipt.events].count("risk_r05_ready") == 1
    assert [event.code for event in receipt.events].count("risk_r05_unready") == 1


def test_partial_group_is_unknown_without_rollback_or_retry() -> None:
    runtime = FakeRuntime()
    runtime.start_result = GroupResult(
        "UNKNOWN",
        "start_set_partial",
        started_units=("strategy_alpha",),
        not_started_units=("strategy_beta",),
        uncertain_units=("strategy_gamma",),
        reference=digest("b"),
    )
    session, components, _, _, receipt = build_supervisor(runtime=runtime)

    outcome = session.start_session()
    stop_outcome = session.stop_session()

    assert (outcome.status, outcome.exit_code) == ("UNKNOWN", 3)
    assert (stop_outcome.status, stop_outcome.code) == (
        "UNKNOWN",
        "manual_observation_required",
    )
    assert not [call for call in components.calls if call[0] == "stop_cooperatively"]
    assert [call[0] for call in runtime.calls].count("start_all_confirmed") == 1
    assert not [
        call for call in runtime.calls if call[0] == "stop_cooperatively_confirmed"
    ]
    assert any(event.code == "strategy_group_start_partial" for event in receipt.events)


def test_receipt_failure_after_side_effect_seals_unknown_without_retry() -> None:
    receipt = FailingReceipt()
    session, components, runtime, _, _ = build_supervisor(receipt=receipt)

    first = session.start_session()
    second = session.start_session()

    assert first is second
    assert (first.status, first.code, first.exit_code) == (
        "UNKNOWN",
        "manual_observation_required",
        3,
    )
    assert [value for action, value in components.calls if action == "start"] == [
        "monitor"
    ]
    assert not [call for call in runtime.calls if call[0] == "start_all_confirmed"]


def test_receipt_intent_failure_prevents_side_effect() -> None:
    receipt = FailingIntentReceipt()
    session, components, runtime, _, _ = build_supervisor(receipt=receipt)

    outcome = session.start_session()

    assert (outcome.status, outcome.code, outcome.exit_code) == (
        "UNKNOWN",
        "manual_observation_required",
        3,
    )
    assert not [call for call in components.calls if call[0] == "start"]
    assert not [call for call in runtime.calls if call[0] == "start_all_confirmed"]


def test_stop_uses_only_reverse_cooperative_order() -> None:
    session, components, runtime, _, receipt = build_supervisor()
    assert session.start_session().status == "APPLIED"
    components.calls.clear()
    runtime.calls.clear()

    outcome = session.stop_session()

    assert (outcome.status, outcome.code, outcome.exit_code) == (
        "APPLIED",
        "session_stopped",
        0,
    )
    assert [call[2] for call in runtime.calls] == list(reversed(UNITS))
    assert [
        value for action, value in components.calls if action == "stop_cooperatively"
    ] == list(COMPONENT_STOP_ORDER)
    strategy_events = [
        event for event in receipt.events if event.action == "strategy_stop"
    ]
    assert {event.subject for event in strategy_events} == {"strategy_unit"}
    assert all(
        event.reference and event.reference.startswith("sha256:")
        for event in strategy_events
    )
    assert all(unit not in repr(strategy_events) for unit in UNITS)


def test_invalid_early_stop_does_not_consume_later_stop_attempt() -> None:
    session, components, _, _, _ = build_supervisor()

    with pytest.raises(RuntimeError, match="cannot be stopped"):
        session.stop_session()
    assert session.start_session().code == "session_ready"
    components.calls.clear()

    assert session.stop_session().code == "session_stopped"
    assert [
        value for action, value in components.calls if action == "stop_cooperatively"
    ] == list(COMPONENT_STOP_ORDER)


def test_unknown_cooperative_stop_halts_without_component_or_force_fallback() -> None:
    session, components, runtime, _, receipt = build_supervisor()
    assert session.start_session().status == "APPLIED"
    components.calls.clear()
    runtime.calls.clear()
    runtime.stop_by["strategy_gamma"] = StepResult(
        "UNKNOWN", "cooperative_stop_timeout"
    )

    outcome = session.stop_session()

    assert (outcome.status, outcome.exit_code) == ("UNKNOWN", 3)
    assert [call[2] for call in runtime.calls] == ["strategy_gamma"]
    assert not [call for call in components.calls if call[0] == "stop_cooperatively"]
    assert any(event.code == "strategy_stop_unknown" for event in receipt.events)


def test_web_runtime_write_control_is_forbidden() -> None:
    with pytest.raises(ValueError, match="must remain disabled"):
        SessionPlan(SESSION_ID, UNITS, web_runtime_control_enabled=True)


def test_jsonl_receipt_is_strict_append_only_and_finite(tmp_path: Path) -> None:
    path = (tmp_path / "session.jsonl").resolve()
    receipt = JsonlReceipt(path, SESSION_ID)
    first = ReceiptEvent(
        RECEIPT_SCHEMA,
        SESSION_ID,
        1,
        "NEW",
        "session_result",
        "session",
        "BLOCKED",
        "session_blocked",
        None,
        2,
    )
    receipt.append(first)
    receipt.close()
    reopened = JsonlReceipt(path, SESSION_ID)
    assert reopened.next_sequence == 2
    reopened.append(
        ReceiptEvent(
            RECEIPT_SCHEMA,
            SESSION_ID,
            2,
            "STOPPED",
            "session_result",
            "session",
            "APPLIED",
            "session_stopped",
            digest("d"),
            0,
        )
    )
    reopened.close()

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2]
    public_text = path.read_text(encoding="utf-8").lower()
    assert all(
        term not in public_text for term in ("password", "bearer", "token", "account")
    )
    with pytest.raises(ValueError, match="invalid receipt code"):
        ReceiptEvent(
            RECEIPT_SCHEMA,
            SESSION_ID,
            3,
            "STOPPED",
            "session_result",
            "session",
            "UNKNOWN",
            "password_admin123",
        )
    with pytest.raises(ValueError, match="invalid receipt subject"):
        ReceiptEvent(
            RECEIPT_SCHEMA,
            SESSION_ID,
            3,
            "STOPPED",
            "session_result",
            "password_secret",
            "UNKNOWN",
            "manual_observation_required",
        )


def test_session_outcome_rejects_boolean_exit_code() -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        SessionOutcome(False, "APPLIED", "session_ready", "RUNNING")


def test_jsonl_receipt_supports_complete_supervisor_sequence(tmp_path: Path) -> None:
    path = (tmp_path / "complete-session.jsonl").resolve()
    receipt = JsonlReceipt(path, SESSION_ID)
    session, _, _, _, _ = build_supervisor(receipt=receipt)

    assert session.start_session().code == "session_ready"
    assert session.stop_session().code == "session_stopped"
    receipt.close()

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["sequence"] for row in rows] == list(range(1, len(rows) + 1))
    assert rows[-1]["code"] == "session_stopped"
    reopened = JsonlReceipt(path, SESSION_ID)
    assert reopened.next_sequence == len(rows) + 1
    reopened.close()


def test_jsonl_receipt_enforces_unique_owner_claim(tmp_path: Path) -> None:
    path = (tmp_path / "owned.jsonl").resolve()
    owner = JsonlReceipt(path, SESSION_ID)

    with pytest.raises(RuntimeError, match="owner claim already exists"):
        JsonlReceipt(path, SESSION_ID)

    owner.close()
    replacement = JsonlReceipt(path, SESSION_ID)
    replacement.close()


def test_jsonl_receipt_rejects_forked_owner_use(tmp_path: Path, monkeypatch) -> None:
    path = (tmp_path / "forked.jsonl").resolve()
    owner = JsonlReceipt(path, SESSION_ID)
    event = ReceiptEvent(
        RECEIPT_SCHEMA,
        SESSION_ID,
        1,
        "NEW",
        "session_result",
        "session",
        "BLOCKED",
        "session_blocked",
        None,
        2,
    )
    original_pid = supervisor_module.os.getpid()
    child_view = object.__new__(JsonlReceipt)
    child_view._lock = supervisor_module.Lock()
    child_view._owner_path = owner._owner_path
    child_view._owner_fd = supervisor_module.os.dup(owner._owner_fd)
    child_view._owner_identity = owner._owner_identity
    child_view._owner_pid = original_pid
    child_view._closed = False

    with monkeypatch.context() as patch:
        patch.setattr(supervisor_module.os, "getpid", lambda: original_pid + 1)
        with pytest.raises(RuntimeError, match="forked process"):
            owner.append(event)
        child_view.close()

    assert child_view._closed is True
    assert owner._owner_path.exists()
    owner.append(event)
    owner.close()


def test_jsonl_receipt_rejects_duplicate_fields(tmp_path: Path) -> None:
    path = (tmp_path / "duplicate.jsonl").resolve()
    valid = ReceiptEvent(
        RECEIPT_SCHEMA,
        SESSION_ID,
        1,
        "NEW",
        "session_result",
        "session",
        "BLOCKED",
        "session_blocked",
        None,
        2,
    )
    line = json.dumps(valid.to_mapping(), separators=(",", ":"), sort_keys=True)
    line = line.replace(
        '"code":"session_blocked"',
        '"code":"password_admin123","code":"session_blocked"',
    )
    path.write_text(line + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON"):
        JsonlReceipt(path, SESSION_ID)


def test_existing_receipt_cannot_implicitly_resume(tmp_path: Path) -> None:
    path = (tmp_path / "session.jsonl").resolve()
    receipt = JsonlReceipt(path, SESSION_ID)
    receipt.append(
        ReceiptEvent(
            RECEIPT_SCHEMA,
            SESSION_ID,
            1,
            "NEW",
            "session_result",
            "session",
            "UNKNOWN",
            "manual_observation_required",
            None,
            3,
        )
    )
    receipt.close()
    reopened = JsonlReceipt(path, SESSION_ID)
    try:
        with pytest.raises(ValueError, match="manual recovery"):
            SessionSupervisor(
                SessionPlan(SESSION_ID, UNITS),
                components=FakeComponents(),
                runtime=FakeRuntime(),
                risk=FakeRisk(),
                receipt=reopened,
            )
    finally:
        reopened.close()
