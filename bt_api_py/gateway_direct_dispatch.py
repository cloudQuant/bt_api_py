"""Explicit trusted-local SDK/Gateway dispatch composition.

The caller supplies the account owner, an already acquired guard, admission,
and a coordinator whose selected route is a *local native provider*. The
caller must not provide a remote Gateway-backed provider: doing so can recurse.
The supplied provider object binds this composition to the selected route; it
does not prove that the object is native or authenticate its source. Future
remote providers must be excluded by provider declarations or composition.
This module does not create an account session or write until invoked.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import gateway_order_contract
from ._contracts.models import OrderRequest

if TYPE_CHECKING:
    from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
    from bt_api_gateway.direct_cancel_journal import CancelRecord
    from bt_api_gateway.direct_command_journal import (
        DirectCommandRecord,
        GatewayDirectCommandJournal,
    )
    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
    from bt_api_gateway.session_guard import LocalAccountSessionGuard

    from .direct_write import DirectWriteCoordinator


def compose_gateway_direct_dispatcher(
    coordinator: DirectWriteCoordinator,
    exchange_name: str,
    journal: GatewayDirectCommandJournal,
    guard: LocalAccountSessionGuard,
    assert_admitted: Callable[[DirectCommandRecord], object],
    *,
    expected_local_provider: object,
) -> GatewayDirectDispatcher:
    """Bind a trusted local native route to the Gateway durable dispatch port.

    ``expected_local_provider`` is injected by the trusted account owner and
    must be the selected route's provider, both during composition and before
    each dispatch. This binding does not prove it is native. The persisted
    payload must be the complete canonical scalar ``OrderRequest.to_dict()``;
    managed authority and nested identities are not accepted. Gateway is
    imported only when selected.
    """
    from .direct_write import DirectWriteCoordinator

    if type(coordinator) is not DirectWriteCoordinator:
        raise ValueError("coordinator must be a DirectWriteCoordinator")
    if (
        expected_local_provider is None
        or coordinator.provider(exchange_name) is not expected_local_provider
    ):
        raise ValueError("local provider does not match coordinator route")
    if not callable(assert_admitted):
        raise ValueError("assert_admitted must be callable")

    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome

    def native_place(
        payload: dict[str, object], port: Any, host_scope: str
    ) -> NativeDispatchOutcome:
        if coordinator.provider(exchange_name) is not expected_local_provider:
            raise ValueError("local provider does not match coordinator route")
        summary = dict(payload)
        request = gateway_order_contract.decode_gateway_order_request(summary, host_scope)
        expected = gateway_order_contract._canonical(summary)

        class CheckedCommitPort:
            def commit(self, record: Any) -> Any:
                if (
                    type(request) is not OrderRequest
                    or gateway_order_contract._canonical(request.to_dict()) != expected
                    or gateway_order_contract._canonical(dict(record.request_summary)) != expected
                ):
                    raise ValueError("order request changed before commit")
                return port.commit(record)

        if coordinator.provider(exchange_name) is not expected_local_provider:
            raise ValueError("local provider does not match coordinator route")
        result = coordinator.place(
            exchange_name,
            request,
            commit_port=CheckedCommitPort(),
            host_scope=host_scope,
            request_summary=summary,
        )
        return NativeDispatchOutcome(
            result.sent, result.identity, result.marker_id, result.error_code
        )

    return GatewayDirectDispatcher(
        journal,
        exchange_name,
        guard,
        native_place,
        lambda identity: coordinator.strong_identity_keys(exchange_name, identity),
        assert_admitted,
    )


def compose_gateway_direct_cancel_dispatcher(
    coordinator: DirectWriteCoordinator,
    exchange_name: str,
    journal: GatewayDirectCommandJournal,
    guard: LocalAccountSessionGuard,
    owner_dispatcher: GatewayDirectDispatcher,
    assert_admitted: Callable[[CancelRecord], object],
    *,
    expected_local_provider: object,
) -> GatewayDirectCancelDispatcher:
    """Bind prepared cancel to the original guarded journal and local route."""
    from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
    from bt_api_base.direct_write_protocol import (
        NativeOrderIdentity,
        PreDispatchRecord,
        WriteOperation,
    )
    from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
    from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
    from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome

    from .direct_write import DirectWriteCoordinator

    if type(coordinator) is not DirectWriteCoordinator:
        raise ValueError("coordinator must be a DirectWriteCoordinator")
    if (
        expected_local_provider is None
        or coordinator.provider(exchange_name) is not expected_local_provider
    ):
        raise ValueError("local provider does not match coordinator route")
    if not callable(assert_admitted):
        raise ValueError("assert_admitted must be callable")
    if type(owner_dispatcher) is not GatewayDirectDispatcher or not owner_dispatcher.is_bound_to(
        journal, guard, exchange_name
    ):
        raise ValueError("owner dispatcher binding mismatch")

    def native_cancel(target: NativeOrderIdentity, symbol: str, port: Any, host_scope: str):
        if coordinator.provider(exchange_name) is not expected_local_provider:
            raise ValueError("local provider does not match coordinator route")
        if type(target) is not NativeOrderIdentity or target.exchange_name != exchange_name:
            raise ValueError("cancel target binding mismatch")
        expected = canonical_gateway_payload(target.to_dict())

        class CheckedCommitPort:
            def commit(self, record: Any) -> Any:
                if (
                    coordinator.provider(exchange_name) is not expected_local_provider
                    or type(record) is not PreDispatchRecord
                    or record.operation is not WriteOperation.CANCEL
                    or record.exchange_name != exchange_name
                    or record.host_scope != host_scope
                    or record.symbol != symbol
                    or type(record.target_identity) is not NativeOrderIdentity
                    or canonical_gateway_payload(target.to_dict()) != expected
                    or canonical_gateway_payload(record.target_identity.to_dict()) != expected
                    or type(record.prepared_cancel) is not PreparedCancelRequest
                ):
                    raise ValueError("cancel request changed before commit")
                record.to_dict()
                return port.commit(record)

        result = coordinator.cancel_prepared(
            exchange_name, target, symbol, commit_port=CheckedCommitPort(), host_scope=host_scope
        )
        return NativeDispatchOutcome(
            result.sent, result.identity, result.marker_id, result.error_code
        )

    cancel_journal = GatewayDirectCancelJournal(journal)
    return GatewayDirectCancelDispatcher(
        cancel_journal, owner_dispatcher, guard, exchange_name, native_cancel, assert_admitted
    )
