"""SDK-neutral claim delegation independent of optional execution journals.

Provider authorization, claim issuance and response verification remain with
the selected provider authority; this module does not create those facts.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, Optional, Protocol

from bt_api_base.query_protocol import QueryBatchReceipt, QueryRequest, QuerySnapshotLease
from bt_api_py.query_facade import QueryFacade, QueryFacadeError

ManagedExecutionOperation = Literal["submit", "cancel"]

class ManagedExecutionUnavailable(RuntimeError):
    """The selected SDK composition does not expose a requested capability."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

class ManagedExecutionOutcomeUnknown(RuntimeError):
    """A provider operation did not return a typed outcome that can be trusted."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

class ExecutionManagedProviderPort(Protocol):
    """Provider-owned managed operations injected by unified SDK composition.

    The port validates authorization and claim provenance itself. The facade
    passes each opaque object through unchanged and never derives authority from
    run mode, Store fields, or a truthy flag.
    """

    def claim(
        self,
        operation: ManagedExecutionOperation,
        request: object,
        *,
        authorization: object,
    ) -> object: ...

    def submit(
        self,
        request: object,
        *,
        claim: object,
        authorization: object,
    ) -> object: ...

    def cancel(
        self,
        request: object,
        *,
        claim: object,
        authorization: object,
    ) -> object: ...

    def resolve_unknown(
        self,
        request: object,
        *,
        receipt: QueryBatchReceipt,
        lease: QuerySnapshotLease,
        now: datetime,
        authorization: object,
    ) -> object: ...

class ManagedExecutionFacade:
    """Neutral entry point for explicitly authorized managed execution.

    This facade is a strict delegator. The injected provider capability owns
    durable claim semantics, authorization validation, command dispatch, and
    provider-specific outcome verification. A missing capability is reported as
    unavailable before calling a provider. Values returned by a provider port
    are passed through unchanged; this facade never infers success from a
    truthy result or constructs an order receipt.

    Query refresh is delegated to :class:`bt_api_py.query_facade.QueryFacade`.
    That query lease proves snapshot currency only. ``resolve_unknown`` remains
    unavailable unless the provider authority also verifies the complete
    resolution proof, intent binding, terminal disposition, and authorization.
    """

    def __init__(
        self,
        *,
        provider_authority: Optional[ExecutionManagedProviderPort] = None,
        query_facade: Optional[QueryFacade] = None,
    ) -> None:
        if query_facade is not None and type(query_facade) is not QueryFacade:
            raise ManagedExecutionUnavailable("managed_execution_query_facade_invalid")
        self._provider_authority = provider_authority
        self._query_facade = query_facade

    @staticmethod
    def _require_opaque(value: object, code: str) -> None:
        if value is None or type(value) is bool:
            raise ManagedExecutionUnavailable(code)

    def _require_provider_method(self, method_name: str) -> Any:
        method = getattr(self._provider_authority, method_name, None)
        if not callable(method):
            raise ManagedExecutionUnavailable(
                "managed_execution_{}_unavailable".format(method_name)
            )
        return method

    def _invoke_provider(self, method_name: str, *args: Any, **kwargs: Any) -> object:
        method = self._require_provider_method(method_name)
        try:
            result = method(*args, **kwargs)
        except (ManagedExecutionUnavailable, ManagedExecutionOutcomeUnknown):
            raise
        except Exception as error:  # noqa: BLE001 - uncertain provider effects never imply success
            raise ManagedExecutionOutcomeUnknown(
                "managed_execution_{}_outcome_unknown".format(method_name)
            ) from error
        if result is None or type(result) is bool:
            raise ManagedExecutionOutcomeUnknown(
                "managed_execution_{}_outcome_unknown".format(method_name)
            )
        return result

    def claim(
        self,
        operation: ManagedExecutionOperation,
        request: object,
        *,
        authorization: object,
    ) -> object:
        """Acquire one provider-owned, single-use claim for a typed request."""

        if type(operation) is not str or operation not in ("submit", "cancel"):
            raise ManagedExecutionUnavailable("managed_execution_operation_unsupported")
        self._require_opaque(request, "managed_execution_request_required")
        self._require_opaque(authorization, "managed_execution_authorization_required")
        return self._invoke_provider(
            "claim", operation, request, authorization=authorization
        )

    def submit(
        self,
        request: object,
        *,
        claim: object,
        authorization: object,
    ) -> object:
        """Submit using the exact provider claim and explicit authorization."""

        self._require_opaque(request, "managed_execution_request_required")
        self._require_opaque(claim, "managed_execution_claim_required")
        self._require_opaque(authorization, "managed_execution_authorization_required")
        return self._invoke_provider(
            "submit", request, claim=claim, authorization=authorization
        )

    def cancel(
        self,
        request: object,
        *,
        claim: object,
        authorization: object,
    ) -> object:
        """Cancel using the exact provider claim and explicit authorization."""

        self._require_opaque(request, "managed_execution_request_required")
        self._require_opaque(claim, "managed_execution_claim_required")
        self._require_opaque(authorization, "managed_execution_authorization_required")
        return self._invoke_provider(
            "cancel", request, claim=claim, authorization=authorization
        )

    def query(
        self,
        requests: Sequence[QueryRequest],
        *,
        deadline: datetime,
    ) -> QueryBatchReceipt:
        """Refresh neutral query snapshots through the separately owned facade."""

        facade = self._query_facade
        if facade is None:
            raise ManagedExecutionUnavailable("managed_execution_query_unavailable")
        try:
            receipt = facade.refresh(requests, deadline=deadline)
        except QueryFacadeError:
            raise
        except Exception as error:  # noqa: BLE001 - provider details stay behind SDK boundary
            raise ManagedExecutionUnavailable("managed_execution_query_failed") from error
        if type(receipt) is not QueryBatchReceipt:
            raise ManagedExecutionUnavailable("managed_execution_query_receipt_invalid")
        return receipt

    def resolve_unknown(
        self,
        request: object,
        *,
        receipt: QueryBatchReceipt,
        lease: QuerySnapshotLease,
        now: datetime,
        authorization: object,
    ) -> object:
        """Delegate UNKNOWN resolution only after current source-lease validation.

        A current query lease is not itself a resolution. The provider authority
        must independently bind the complete evidence to this intent, prove a
        permitted terminal outcome, and validate the one-use authorization.
        """

        self._require_opaque(request, "managed_execution_request_required")
        self._require_opaque(authorization, "managed_execution_authorization_required")
        if type(receipt) is not QueryBatchReceipt or type(lease) is not QuerySnapshotLease:
            raise ManagedExecutionUnavailable("managed_execution_unknown_evidence_invalid")
        facade = self._query_facade
        if facade is None:
            raise ManagedExecutionUnavailable("managed_execution_query_unavailable")
        try:
            if facade.validate_lease(lease, receipt, now=now) is not True:
                raise ManagedExecutionUnavailable(
                    "managed_execution_unknown_evidence_stale"
                )
        except QueryFacadeError as error:
            raise ManagedExecutionUnavailable(
                "managed_execution_unknown_evidence_invalid"
            ) from error
        return self._invoke_provider(
            "resolve_unknown",
            request,
            receipt=receipt,
            lease=lease,
            now=now,
            authorization=authorization,
        )

__all__ = (
    "ExecutionManagedProviderPort",
    "ManagedExecutionFacade",
    "ManagedExecutionOperation",
    "ManagedExecutionOutcomeUnknown",
    "ManagedExecutionUnavailable",
)
