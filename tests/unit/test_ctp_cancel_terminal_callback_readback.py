from __future__ import annotations

from types import SimpleNamespace

import pytest
import test_ctp_callback_evidence as callback_fixtures
import test_ctp_execution_owner as owner_fixtures
import test_ctp_managed_cancel_port as cancel_fixtures
import test_ctp_managed_submit_port as submit_fixtures

from bt_api_execution import CancelObservation, ExecutionState


def _emit_action_response(case, *, error_id: int) -> None:
    calls = cancel_fixtures._cancel_calls(case)
    assert len(calls) == 1
    name, field, request_id = calls[0]
    assert name == "ReqOrderAction"
    case.trader._spi.OnRspOrderAction(
        field,
        SimpleNamespace(
            ErrorID=error_id,
            ErrorMsg="synthetic CTP action response",
        ),
        request_id,
        True,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("terminal_first", "error_id", "expected_state", "action_state"),
    [
        (True, 0, ExecutionState.CANCELLED, "ACKNOWLEDGED"),
        (False, 0, ExecutionState.CANCELLED, "ACKNOWLEDGED"),
        (True, 17, ExecutionState.REJECTED, "REJECTED"),
    ],
)
def test_cancel_terminal_readback_uses_exact_order_callback_and_keeps_action_projection(
    tmp_path,
    monkeypatch,
    terminal_first,
    error_id,
    expected_state,
    action_state,
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        callback_fixtures._install_evidence_port(case)
        target, reservation, submit_port, submit_facade = cancel_fixtures._open_target(
            case, f"terminal-readback-{terminal_first}-{error_id}", monkeypatch
        )

        with callback_fixtures._real_cancel_stack(
            case, tmp_path, submit_port, submit_facade
        ) as stack:
            intent = cancel_fixtures._cancel_intent(
                target,
                reservation,
                f"terminal-readback-{terminal_first}-{error_id}",
            )
            initial = stack.facade.cancel(
                intent,
                stack.port,
                before_dispatch=stack.port.prepare_target,
            )
            assert initial.state is ExecutionState.UNKNOWN
            assert initial.dispatch_attempts == 1
            assert len(cancel_fixtures._cancel_calls(case)) == 1
            assert len(cancel_fixtures._query_calls(case)) == 1
            assert intent.cancel_id in stack.port._targets
            risk_snapshot = SimpleNamespace(risk_path=tmp_path / "risk.sqlite3")
            risk_state_before = cancel_fixtures.risk_fixtures._risk_state(risk_snapshot)
            assert len(risk_state_before[1]) == 1

            submit_calls = submit_fixtures._submit_calls(case)
            assert len(submit_calls) == 1
            request_id = submit_calls[0][2]

            def emit_terminal_order() -> None:
                callback_fixtures._emit_order_status(
                    case,
                    target,
                    reservation,
                    request_id,
                    status="5",
                    submit_status="3",
                    traded=0,
                    sequence=2,
                )

            def emit_action_response() -> None:
                _emit_action_response(case, error_id=error_id)

            if terminal_first:
                emit_terminal_order()
                emit_action_response()
            else:
                emit_action_response()
                accepted = stack.port.reconcile_cancel(intent)
                assert type(accepted) is CancelObservation
                assert accepted.state is ExecutionState.ACKED
                assert accepted.cancel_id == intent.cancel_id
                assert accepted.target_intent_id == target.intent_id
                assert accepted.provider_order_id == intent.provider_order_id
                assert intent.cancel_id in stack.port._targets
                assert (
                    cancel_fixtures.risk_fixtures._risk_state(risk_snapshot)
                    == risk_state_before
                )
                emit_terminal_order()

            observation = stack.port.reconcile_cancel(intent)
            assert type(observation) is CancelObservation
            assert observation.state is expected_state
            assert observation.cancel_id == intent.cancel_id
            assert observation.target_intent_id == target.intent_id
            assert observation.provider_order_id == intent.provider_order_id
            assert intent.cancel_id not in stack.port._targets

            command = cancel_fixtures._cancel_command(case, target, intent)
            assert command is not None
            projection = case.store.read_ctp_dispatch_projection(
                case.scope, command.command_id
            )
            assert projection.cancel_action is not None
            assert projection.cancel_action.action_state == action_state
            assert projection.cancel_action.source_kind == "CALLBACK"
            assert projection.cancel_action.terminal is (error_id != 0)
            target_state = projection.cancel_action.target_order.order_state
            assert target_state.provider_state == "CANCELLED"
            assert target_state.terminal is True
            assert target_state.source_kind == "CALLBACK"

            # Port readback is callback/store-only: it neither queries again nor
            # emits another native cancellation request.
            assert len(cancel_fixtures._cancel_calls(case)) == 1
            assert len(cancel_fixtures._query_calls(case)) == 1
            assert (
                cancel_fixtures.risk_fixtures._risk_state(risk_snapshot)
                == risk_state_before
            )
            assert stack.port._queue.unfinished_tasks == 0
