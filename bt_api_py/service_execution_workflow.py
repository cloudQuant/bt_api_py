"""Typed orchestration for service-backed order execution.

This module borrows an SDK execution client and, optionally, its SDK-owned
recovery flow. It never creates or closes either owner. A submit is durably
prepared through the recovery flow before the client RPC; the RPC is attempted
once. An uncertain result is returned as ``UNKNOWN`` and is never retried here.

Framework objects do not cross this boundary. Hosts map their order objects to
the request DTOs below and map the resulting typed outcomes back to their own
order model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from bt_api_py.service_execution import (
    CancelRecordView,
    OrderRecordView,
    ServiceExecutionClient,
    ServiceExecutionError,
    ServiceExecutionNotDispatchedError,
    ServiceExecutionUnknownOutcomeError,
    is_service_execution_reference as _reference,
)
from bt_api_py.service_recovery import (
    ServiceRecoveryManifest,
    ServiceRecoveryOrderState,
    ServiceRecoveryPreparationV1,
    StrategyPositionRefV1,
    recovery_flow_is_usable,
)


# The execution client proves non-dispatch for this transport result and for
# a host refusal carrying the ``NOT_JOURNALED`` disposition (iteration 60 R3:
# refused by the strategy ledger or the typed command gate before the journal
# saw the request; ``ServiceExecutionNotDispatchedError``). Every other remote
# refusal code and every malformed/missing reply may be observed after the
# owner has entered dispatch, so it must remain UNKNOWN here.
_DEFINITIVE_REJECTION_CODES = frozenset({"REQUEST_NOT_SENT"})


class ServiceExecutionWorkflowError(RuntimeError):
    """A finite local workflow configuration or projection error."""

    def __init__(self, code: str) -> None:
        self.code = code if _safe_code(code) else "SERVICE_EXECUTION_WORKFLOW_FAILED"
        super().__init__(self.code)


@dataclass(frozen=True)
class ServiceExecutionSubmitRequestV1:
    """Framework-neutral submit content plus optional durable recovery facts."""

    intent_id: str
    signal_id: str
    instrument: str
    side: str
    position_effect: str
    order_type: str
    quantity: Decimal | int | str
    price: Decimal | int | str | None = None
    time_in_force: str = "GTC"
    reduce_only: bool = False
    metadata_version: str = "unknown"
    deadline_ns: Optional[int] = None
    tags: Optional[Mapping[str, str]] = None
    position_side: str = "NET"
    position_bucket: str = "ALL"
    recovery_binding: Optional[Mapping[str, Any]] = None
    strategy_position_owner_payload: Optional[Mapping[str, Any]] = None
    intent_context: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class ServiceExecutionCancelRequestV1:
    """Framework-neutral cancellation request."""

    cancel_id: str
    target_intent_id: str
    provider_order_id: str
    metadata_version: str = "unknown"
    tags: Optional[Mapping[str, str]] = None
    # The order's instrument, for a typed cancel under a multi-instrument
    # command binding (iteration 60 R2-B). ``None`` keeps the client default.
    instrument: Optional[str] = None


@dataclass(frozen=True)
class ServiceExecutionOutcomeV1:
    """One typed workflow result; ``UNKNOWN`` never authorizes a retry."""

    operation: str
    disposition: str
    stage: str
    code: Optional[str] = None
    record: OrderRecordView | CancelRecordView | None = None
    recovery_state: Optional[ServiceRecoveryOrderState] = None
    observed_record: OrderRecordView | CancelRecordView | None = None
    strategy_position_ref: Optional[StrategyPositionRefV1] = None

    def __post_init__(self) -> None:
        if self.operation not in {"submit", "cancel"}:
            raise ValueError("invalid service execution operation")
        if self.disposition not in {"RECORD", "RESTORE", "REJECTED", "UNKNOWN"}:
            raise ValueError("invalid service execution disposition")
        if self.stage not in {"VALIDATION", "PREPARE", "REQUEST", "COMPLETED"}:
            raise ValueError("invalid service execution stage")
        if self.code is not None and not _safe_code(self.code):
            raise ValueError("invalid service execution code")
        if self.disposition in {"REJECTED", "UNKNOWN"} and self.code is None:
            raise ValueError("service execution failure requires a code")
        if self.disposition == "RECORD":
            expected = OrderRecordView if self.operation == "submit" else CancelRecordView
            if (
                type(self.record) is not expected
                or self.recovery_state is not None
                or self.observed_record is not None
            ):
                raise ValueError("service execution record type is invalid")
        elif self.record is not None:
            raise ValueError("unexpected service execution record")
        if self.observed_record is not None:
            expected = OrderRecordView if self.operation == "submit" else CancelRecordView
            if self.disposition != "UNKNOWN" or type(self.observed_record) is not expected:
                raise ValueError("observed service execution record type is invalid")
        if self.disposition == "RESTORE":
            if self.operation != "submit" or type(self.recovery_state) is not ServiceRecoveryOrderState:
                raise ValueError("service recovery state type is invalid")
        elif self.recovery_state is not None:
            raise ValueError("unexpected service recovery state")
        if self.strategy_position_ref is not None and (
            self.operation != "submit" or type(self.strategy_position_ref) is not StrategyPositionRefV1
        ):
            raise ValueError("service strategy position reference type is invalid")


class ServiceExecutionWorkflow:
    """Coordinate existing SDK durable preparation and exactly one RPC."""

    def __init__(
        self,
        client: ServiceExecutionClient,
        recovery_flow: object = None,
        *,
        recovery_event_version: int = 1,
    ) -> None:
        if type(client) is not ServiceExecutionClient:
            raise ServiceExecutionWorkflowError("SERVICE_EXECUTION_CLIENT_INVALID")
        if recovery_flow is not None and not recovery_flow_is_usable(recovery_flow):
            raise ServiceExecutionWorkflowError("SERVICE_RECOVERY_FLOW_INVALID")
        if type(recovery_event_version) is not int or recovery_event_version not in {1, 2}:
            raise ServiceExecutionWorkflowError("SERVICE_RECOVERY_EVENT_VERSION_INVALID")
        if recovery_event_version == 2 and recovery_flow is None:
            raise ServiceExecutionWorkflowError("SERVICE_RECOVERY_FLOW_REQUIRED")
        if recovery_flow is not None and not callable(
            getattr(client, "build_submit_payload", None)
        ):
            raise ServiceExecutionWorkflowError("SERVICE_SUBMIT_PAYLOAD_BUILDER_UNAVAILABLE")

        self._client = client
        self._recovery_flow = recovery_flow
        self._recovery_event_version = recovery_event_version

    def requires_intent_context(self, intent_id: str) -> bool:
        """Whether a new V2 manifest needs a BT-produced neutral context map."""

        flow = self._recovery_flow
        if flow is None or self._recovery_event_version != 2:
            return False
        if not _reference(intent_id):
            raise ServiceExecutionWorkflowError("INVALID_INTENT_ID")
        try:
            manifest = flow.manifest(intent_id)
        except Exception as error:
            raise ServiceExecutionWorkflowError(_recovery_error_code(error)) from None
        if manifest is None:
            return True
        if type(manifest) is not ServiceRecoveryManifest or manifest.intent_id != intent_id:
            raise ServiceExecutionWorkflowError("RECOVERY_MANIFEST_PROJECTION_INVALID")
        return False

    def submit(
        self, request: ServiceExecutionSubmitRequestV1
    ) -> ServiceExecutionOutcomeV1:
        """Prepare durably, make at most one service RPC, and classify its result."""

        if type(request) is not ServiceExecutionSubmitRequestV1:
            raise ServiceExecutionWorkflowError("SERVICE_SUBMIT_REQUEST_INVALID")
        flow = self._recovery_flow
        if flow is not None:
            try:
                payload = self._client.build_submit_payload(**_submit_kwargs(request))
                if not isinstance(payload, Mapping):
                    return _outcome(
                        "submit", "REJECTED", "VALIDATION", code="INVALID_SUBMIT_PAYLOAD"
                    )
                payload = dict(payload)
            except Exception as error:
                return _outcome(
                    "submit",
                    "REJECTED",
                    "VALIDATION",
                    code=_recovery_error_code(error),
                )

            if not isinstance(request.recovery_binding, Mapping):
                return _outcome(
                    "submit", "REJECTED", "PREPARE", code="service_execution_recovery_binding_missing"
                )
            if request.strategy_position_owner_payload is not None and not isinstance(
                request.strategy_position_owner_payload, Mapping
            ):
                return _outcome(
                    "submit", "REJECTED", "PREPARE", code="RECOVERY_POSITION_OWNER_PAYLOAD_INVALID"
                )
            if (
                self._recovery_event_version == 2
                and request.intent_context is not None
                and not isinstance(request.intent_context, Mapping)
            ):
                return _outcome(
                    "submit", "REJECTED", "PREPARE", code="RECOVERY_INTENT_CONTEXT_UNAVAILABLE"
                )
            try:
                preparation = flow.prepare_submit(
                    intent_id=request.intent_id,
                    submit_payload=payload,
                    recovery_binding=dict(request.recovery_binding),
                    strategy_position_owner_payload=(
                        dict(request.strategy_position_owner_payload)
                        if isinstance(request.strategy_position_owner_payload, Mapping)
                        else None
                    ),
                    intent_context=(
                        dict(request.intent_context)
                        if isinstance(request.intent_context, Mapping)
                        else None
                    ),
                )
            except Exception as error:
                return _outcome(
                    "submit", "REJECTED", "PREPARE", code=_recovery_error_code(error)
                )
            if type(preparation) is not ServiceRecoveryPreparationV1:
                return _outcome(
                    "submit",
                    "REJECTED",
                    "PREPARE",
                    code="RECOVERY_SUBMIT_PREPARATION_TYPE_INVALID",
                )
            position_ref = preparation.strategy_position_ref
            if position_ref is not None and type(position_ref) is not StrategyPositionRefV1:
                return _outcome(
                    "submit", "REJECTED", "PREPARE", code="RECOVERY_STRATEGY_POSITION_REF_INVALID"
                )
            if not _recovery_binding_matches(
                request.recovery_binding, preparation.recovery_binding
            ):
                return _outcome(
                    "submit", "REJECTED", "PREPARE", code="RECOVERY_SUBMIT_BINDING_MISMATCH"
                )
            prior = preparation.prior_state
            if prior is not None:
                if type(prior) is not ServiceRecoveryOrderState:
                    return _outcome(
                        "submit",
                        "REJECTED",
                        "PREPARE",
                        code="service_execution_recovery_order_projection_type_invalid",
                    )
                if (
                    prior.intent_id != request.intent_id
                    or dict(prior.recovery_binding) != dict(preparation.recovery_binding)
                    or prior.strategy_position_ref != position_ref
                ):
                    return _outcome(
                        "submit",
                        "REJECTED",
                        "PREPARE",
                        code="service_execution_recovery_order_local_binding_mismatch",
                    )
                return ServiceExecutionOutcomeV1(
                    "submit",
                    "RESTORE",
                    "COMPLETED",
                    recovery_state=prior,
                    strategy_position_ref=position_ref,
                )
            submit_kwargs = payload
        else:
            try:
                submit_kwargs = self._client.build_submit_payload(**_submit_kwargs(request))
            except Exception as error:
                return _outcome(
                    "submit",
                    "REJECTED",
                    "VALIDATION",
                    code=_exception_code(error),
                )

        try:
            record = self._client.submit(**submit_kwargs)
        except Exception as error:
            return _exception_outcome(
                "submit",
                "REQUEST",
                error,
                expected_id=request.intent_id,
                strategy_position_ref=(
                    position_ref if flow is not None else None
                ),
            )
        if type(record) is not OrderRecordView:
            return _outcome(
                "submit",
                "UNKNOWN",
                "REQUEST",
                code="SERVICE_EXECUTION_RECORD_TYPE_INVALID",
                strategy_position_ref=(position_ref if flow is not None else None),
            )
        return ServiceExecutionOutcomeV1(
            "submit",
            "RECORD",
            "COMPLETED",
            record=record,
            strategy_position_ref=(position_ref if flow is not None else None),
        )

    def cancel(
        self, request: ServiceExecutionCancelRequestV1
    ) -> ServiceExecutionOutcomeV1:
        """Prepare cancellation facts, then make at most one service RPC."""

        if type(request) is not ServiceExecutionCancelRequestV1:
            raise ServiceExecutionWorkflowError("SERVICE_CANCEL_REQUEST_INVALID")
        try:
            cancel_kwargs = self._client.build_cancel_payload(
                cancel_id=request.cancel_id,
                target_intent_id=request.target_intent_id,
                provider_order_id=request.provider_order_id,
                metadata_version=request.metadata_version,
                tags=request.tags,
            )
        except Exception as error:
            return _outcome(
                "cancel", "REJECTED", "VALIDATION", code=_exception_code(error)
            )
        flow = self._recovery_flow
        if flow is not None:
            try:
                flow.prepare_cancel(
                    cancel_id=request.cancel_id,
                    target_intent_id=request.target_intent_id,
                    provider_order_id=request.provider_order_id,
                    metadata_version=cancel_kwargs["metadata_version"],
                )
            except Exception as error:
                return _outcome(
                    "cancel", "REJECTED", "PREPARE", code=_recovery_error_code(error)
                )
        try:
            if request.instrument is not None:
                record = self._client.cancel(**cancel_kwargs, instrument=request.instrument)
            else:
                record = self._client.cancel(**cancel_kwargs)
        except Exception as error:
            return _exception_outcome(
                "cancel",
                "REQUEST",
                error,
                expected_id=request.cancel_id,
                expected_target_intent_id=request.target_intent_id,
                expected_provider_order_id=request.provider_order_id,
            )
        if type(record) is not CancelRecordView:
            return _outcome(
                "cancel", "UNKNOWN", "REQUEST", code="SERVICE_EXECUTION_RECORD_TYPE_INVALID"
            )
        return ServiceExecutionOutcomeV1(
            "cancel", "RECORD", "COMPLETED", record=record
        )

    def query(self, intent_id: str) -> Optional[OrderRecordView]:
        """Read one durable SDK order record without provider dispatch."""

        return self._client.query(intent_id)


def _submit_kwargs(request: ServiceExecutionSubmitRequestV1) -> dict[str, Any]:
    return {
        "intent_id": request.intent_id,
        "signal_id": request.signal_id,
        "instrument": request.instrument,
        "side": request.side,
        "position_effect": request.position_effect,
        "order_type": request.order_type,
        "quantity": request.quantity,
        "price": request.price,
        "time_in_force": request.time_in_force,
        "reduce_only": request.reduce_only,
        "metadata_version": request.metadata_version,
        "deadline_ns": request.deadline_ns,
        "tags": request.tags,
        "position_side": request.position_side,
        "position_bucket": request.position_bucket,
    }


def _exception_outcome(
    operation: str,
    stage: str,
    error: BaseException,
    *,
    expected_id: str,
    expected_target_intent_id: Optional[str] = None,
    expected_provider_order_id: Optional[str] = None,
    strategy_position_ref: Optional[StrategyPositionRefV1] = None,
) -> ServiceExecutionOutcomeV1:
    code = _exception_code(error)
    observed_record = None
    has_owner_record = type(error) is ServiceExecutionError and error.record is not None
    if has_owner_record:
        candidate = error.record
        if operation == "submit" and type(candidate) is OrderRecordView:
            if candidate.intent_id == expected_id:
                observed_record = candidate
        elif operation == "cancel" and type(candidate) is CancelRecordView:
            if (
                candidate.cancel_id == expected_id
                and candidate.target_intent_id == expected_target_intent_id
                and candidate.provider_order_id == expected_provider_order_id
            ):
                observed_record = candidate

    if isinstance(error, ServiceExecutionUnknownOutcomeError) or has_owner_record:
        disposition = "UNKNOWN"
    elif type(error) is ServiceExecutionNotDispatchedError:
        disposition = "REJECTED"
    else:
        disposition = "REJECTED" if code in _DEFINITIVE_REJECTION_CODES else "UNKNOWN"
    return _outcome(
        operation,
        disposition,
        stage,
        code=code,
        observed_record=observed_record,
        strategy_position_ref=strategy_position_ref,
    )


def _outcome(
    operation: str,
    disposition: str,
    stage: str,
    *,
    code: str,
    observed_record: OrderRecordView | CancelRecordView | None = None,
    strategy_position_ref: Optional[StrategyPositionRefV1] = None,
) -> ServiceExecutionOutcomeV1:
    return ServiceExecutionOutcomeV1(
        operation,
        disposition,
        stage,
        code=code,
        observed_record=observed_record,
        strategy_position_ref=strategy_position_ref,
    )


def _recovery_binding_matches(requested: Mapping[str, Any], issued: Mapping[str, Any]) -> bool:
    if requested.get("schema") != "bt_api.strategy-position-binding.v1":
        return dict(requested) == dict(issued)
    return (
        issued.get("schema") == "bt_api.strategy-position-binding.v1"
        and requested.get("local_position_key_ref") == issued.get("local_position_key_ref")
        and (
            requested.get("strategy_position_ref") is None
            or requested.get("strategy_position_ref") == issued.get("strategy_position_ref")
        )
        and type(issued.get("strategy_position_ref")) is str
    )


def _exception_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if _safe_code(code):
        return code
    return "service_execution_request_failed"


def _recovery_error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if (
        type(code) is str
        and code
        and len(code) <= 96
        and code.isascii()
        and all(character.isalnum() or character in "_.:-" for character in code)
    ):
        return f"service_execution_{code.lower()}"
    return "service_execution_recovery_flow_failed"


def _safe_code(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and len(value) <= 128
        and value.isascii()
        and all(character.isalnum() or character in "_.:-" for character in value)
    )


__all__ = [
    "ServiceExecutionCancelRequestV1",
    "ServiceExecutionOutcomeV1",
    "ServiceExecutionSubmitRequestV1",
    "ServiceExecutionWorkflow",
    "ServiceExecutionWorkflowError",
    "StrategyPositionRefV1",
]
