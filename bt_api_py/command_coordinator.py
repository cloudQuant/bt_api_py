"""Coordinate one SDK command lane using caller-owned projection callbacks.

This module does not select provider routes or establish account authority.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any, Callable, Dict


class CommandOutcomeCoordinator:
    """Coordinate SDK command outcomes, drops and one safety-state owner."""

    def __init__(
        self,
        *,
        invoke_command: Callable[[str, Dict[str, Any]], Any],
        invoke_account_risk: Callable[[Mapping[str, Any]], Any],
        set_accept_openings: Callable[[bool], None],
        set_last_error: Callable[[str], None],
        clear_account_risk_refresh: Callable[[Any], None],
        update_correlation_state: Callable[[Mapping[str, Any], str], None],
        force_market_data_only: Callable[[str], None],
        exception_code: Callable[[BaseException, str], str],
        redact_runtime_value: Callable[[Any], Any],
        observe_execution_error: Callable[[BaseException], None],
        project_completion: Callable[[Mapping[str, Any]], Dict[str, Any]],
        project_completion_drop: Callable[[Mapping[str, Any], str], Mapping[str, Any]],
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        callbacks = (
            invoke_command,
            invoke_account_risk,
            set_accept_openings,
            set_last_error,
            clear_account_risk_refresh,
            update_correlation_state,
            force_market_data_only,
            exception_code,
            redact_runtime_value,
            observe_execution_error,
            project_completion,
            project_completion_drop,
            monotonic_ns,
        )
        if any(not callable(callback) for callback in callbacks):
            raise TypeError("all SDK execution callbacks must be callable")
        self._invoke_command = invoke_command
        self._invoke_account_risk = invoke_account_risk
        self._set_last_error = set_last_error
        self._clear_account_risk_refresh = clear_account_risk_refresh
        self._update_correlation_state = update_correlation_state
        self._force_market_data_only = force_market_data_only
        self._exception_code = exception_code
        self._redact_runtime_value = redact_runtime_value
        self._observe_execution_error = observe_execution_error
        self._project_completion = project_completion
        self._project_completion_drop = project_completion_drop
        self._monotonic_ns = monotonic_ns

        from bt_api_py.command_outcome import CommandSafetyState

        self._safety_state = CommandSafetyState(set_accept_openings=set_accept_openings)

    @property
    def risk_state_unknown(self) -> bool:
        return self._safety_state.risk_state_unknown

    def latch_unknown(self, reason: str) -> int:
        """Freeze openings and advance the UNKNOWN incident epoch."""

        return self._safety_state.latch_unknown(reason)

    def risk_incident_epoch(self) -> int:
        return self._safety_state.risk_incident_epoch()

    def health(self) -> Dict[str, Any]:
        return self._safety_state.health()

    def on_command_drop(self, command: Mapping[str, Any], reason: str) -> None:
        """Apply correlation and risk consequences for a dropped command."""

        priority = str(command.get("priority") or "unknown")
        if command.get("operation") == "submit":
            state = (
                "cancelled_before_dispatch"
                if reason == "cancelled_before_send"
                else "rejected_before_dispatch"
            )
            self._update_correlation_state(command, state)
        if priority != "open":
            self.latch_unknown(reason)
        if command.get("operation") == "account_risk":
            self._clear_account_risk_refresh(command.get("account_risk_refresh_token"))

    def on_admission_rejected(self, command: Mapping[str, Any], reason: str) -> None:
        """Latch risk when a risk-reducing command cannot enter the queue."""

        self.latch_unknown(reason)

    def on_execution_unknown(self, reason: str) -> None:
        self.latch_unknown(reason)

    def on_completion_drop(
        self, update: Mapping[str, Any], reason: str
    ) -> Mapping[str, Any]:
        self.latch_unknown(reason)
        return self._redact_runtime_value(self._project_completion_drop(update, reason))

    async def execute_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """Run one command and complete the caller-provided receipt projection."""

        operation = command["operation"]
        completion = self._project_completion(command)
        try:
            from bt_api_py.command_outcome import classify_command_result

            if operation == "account_risk":
                result = await self._invoke_account_risk(command)
            else:
                result = await self._invoke_command(operation, command)
            self._apply_result(completion, operation, result, classifier=classify_command_result)
        except asyncio.CancelledError:
            if operation == "account_risk":
                self._clear_account_risk_refresh(command.get("account_risk_refresh_token"))
            raise
        except Exception as exc:
            self._observe_execution_error(exc)
            self._apply_exception(completion, operation, command, exc)
        completion["completed_monotonic_ns"] = self._monotonic_ns()
        if operation == "account_risk":
            self._clear_account_risk_refresh(command.get("account_risk_refresh_token"))
        return completion

    def _apply_result(
        self, completion: Dict[str, Any], operation: str, result: Any, *, classifier=None
    ) -> None:
        if classifier is None:
            from bt_api_py.command_outcome import classify_command_result as classifier
        fields, health, error_code = classifier(operation, result)
        completion.update(fields)
        for counter in health:
            self._increment_health(counter)
        if error_code is not None:
            self._set_last_error(error_code)

    def _apply_exception(
        self,
        completion: Dict[str, Any],
        operation: str,
        command: Mapping[str, Any],
        exc: Exception,
    ) -> None:
        from bt_api_py.command_outcome import classify_command_exception

        fields, health, state = classify_command_exception(
            operation,
            exc,
            error_code=None,
            remote_write_attempted=bool(command.get("_sdk_dispatch_method_entered")),
        )
        if state is not None:
            self._update_correlation_state(command, state)
        error_code = self._exception_code(exc, type(exc).__name__)
        fields["error_code"] = error_code
        completion.update(fields)
        for counter in health:
            self._increment_health(counter)
        if (not fields["execution_unknown"] and fields["definite_reject"]
                and str(error_code).startswith("execution_arm_")):
            self._force_market_data_only(error_code)
            self.latch_unknown(error_code)
        self._set_last_error(completion["error_code"])

    def _increment_health(self, name: str) -> None:
        self._safety_state.increment_health(name)


__all__ = ("CommandOutcomeCoordinator",)
