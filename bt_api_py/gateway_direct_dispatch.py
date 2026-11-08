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
