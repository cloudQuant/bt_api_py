"""Authenticated transport-to-gateway server composition.

The ZMQ transport authenticates remote peers and returns a
``RemotePrincipalGrant``.  The gateway router intentionally accepts a
different, typed ``GatewayPrincipal``.  This module is the narrow server-side
bridge between those contracts: it converts only a transport-issued grant,
strictly decodes the canonical gateway command envelope, and delegates to an
already composed gateway authority.

This module does not supply permissions, admission, a writer lease, or a
provider.  Non-read commands remain governed by ``GatewayCommandRouter`` and
are rejected there unless its server-owned admission and writer authority are
present and current.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


class GatewayTransportCompositionError(RuntimeError):
    """The server bridge lacks a required typed gateway or transport contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def gateway_principal_from_remote_grant(gateway: Any, transport: Any, grant: Any) -> Any:
    """Convert one authenticated transport grant to the gateway's exact DTO.

    The transport's identity and scope labels are server-owned ACL values.
    Command kinds are converted by exact enum value; unknown or differently
    typed values fail closed rather than being copied into the gateway DTO.
    """

    grant_type = getattr(transport, "RemotePrincipalGrant", None)
    principal_type = getattr(gateway, "GatewayPrincipal", None)
    command_kind_type = getattr(gateway, "GatewayCommandKind", None)
    if grant_type is None or principal_type is None or command_kind_type is None:
        raise GatewayTransportCompositionError(
            "GATEWAY_TRANSPORT_CONTRACT_MISSING",
            "gateway and transport principal contracts are required",
        )
    if not isinstance(grant, grant_type):
        raise GatewayTransportCompositionError(
            "REMOTE_PRINCIPAL_GRANT_REQUIRED",
            "remote gateway requests require a transport ACL grant",
        )
    try:
        kinds = frozenset(command_kind_type(kind) for kind in grant.allowed_kinds)
    except (TypeError, ValueError) as error:
        raise GatewayTransportCompositionError(
            "REMOTE_PRINCIPAL_KIND_INVALID",
            "transport ACL contains a command kind unsupported by the gateway",
        ) from error
    try:
        return principal_type(
            principal_id=grant.principal_id,
            account_scopes=frozenset({grant.account_scope}),
            strategy_scopes=frozenset({grant.strategy_scope}),
            allowed_kinds=kinds,
        )
    except (TypeError, ValueError) as error:
        raise GatewayTransportCompositionError(
            "GATEWAY_PRINCIPAL_INVALID",
            "authenticated transport grant cannot form a gateway principal",
        ) from error


class GatewayTransportCommandHandler:
    """Strictly adapt authenticated transport requests to a gateway authority.

    ``authority`` is the existing server-side composition (for example
    ``GatewayExecutionAuthority``).  Its ``handle`` method owns the final
    command-specific admission and calls ``GatewayCommandRouter.dispatch``.
    The adapter validates the canonical envelope before handing control to
    that authority, so transport ACL acceptance alone can never dispatch a
    malformed gateway command.
    """

    def __init__(self, *, gateway: Any, transport: Any, authority: Any) -> None:
        required_gateway = (
            "GatewayCommandKind",
            "GatewayCommandRouter",
            "GatewayPrincipal",
            "GatewayWireMappingError",
            "gateway_command_from_wire_payload",
        )
        required_transport = ("RemotePrincipalGrant", "WireChannel", "WireMessage")
        if any(getattr(gateway, name, None) is None for name in required_gateway):
            raise GatewayTransportCompositionError(
                "GATEWAY_CONTRACT_MISSING", "canonical gateway command contracts are required"
            )
        if any(getattr(transport, name, None) is None for name in required_transport):
            raise GatewayTransportCompositionError(
                "TRANSPORT_CONTRACT_MISSING", "authenticated transport contracts are required"
            )
        if getattr(authority, "gateway", None) is not gateway:
            raise GatewayTransportCompositionError(
                "GATEWAY_AUTHORITY_MISMATCH",
                "authority must be composed with the supplied gateway module",
            )
        if getattr(authority, "transport", None) is not transport:
            raise GatewayTransportCompositionError(
                "TRANSPORT_AUTHORITY_MISMATCH",
                "authority must be composed with the supplied transport module",
            )
        if not isinstance(getattr(authority, "router", None), gateway.GatewayCommandRouter):
            raise GatewayTransportCompositionError(
                "GATEWAY_ROUTER_REQUIRED",
                "authority must expose the server-side GatewayCommandRouter",
            )
        if not callable(getattr(authority, "handle", None)):
            raise GatewayTransportCompositionError(
                "GATEWAY_AUTHORITY_REQUIRED", "a server-side gateway authority is required"
            )
        self.gateway = gateway
        self.transport = transport
        self.authority = authority

    def __call__(self, grant: Any, message: Any) -> Mapping[str, Any]:
        """Validate principal and command before delegating to the authority."""

        principal = gateway_principal_from_remote_grant(self.gateway, self.transport, grant)
        if not isinstance(message, self.transport.WireMessage):
            raise GatewayTransportCompositionError(
                "GATEWAY_WIRE_INVALID", "gateway message must be a validated WireMessage"
            )
        if message.channel is not self.transport.WireChannel.COMMAND:
            raise GatewayTransportCompositionError(
                "GATEWAY_COMMAND_CHANNEL_REQUIRED",
                "gateway authority accepts command messages only",
            )
        try:
            command = self.gateway.gateway_command_from_wire_payload(message.payload)
        except Exception as error:
            raise GatewayTransportCompositionError(
                "GATEWAY_COMMAND_WIRE_INVALID",
                "gateway command schema or fingerprint is invalid",
            ) from error
        if message.scope != command.strategy_scope:
            raise GatewayTransportCompositionError(
                "GATEWAY_WIRE_SCOPE_MISMATCH",
                "transport stream scope does not match the canonical command",
            )
        if command.account_scope not in principal.account_scopes:
            raise GatewayTransportCompositionError(
                "GATEWAY_ACCOUNT_SCOPE_DENIED", "authenticated principal lacks account scope"
            )
        if command.strategy_scope not in principal.strategy_scopes:
            raise GatewayTransportCompositionError(
                "GATEWAY_STRATEGY_SCOPE_DENIED", "authenticated principal lacks strategy scope"
            )
        if command.kind not in principal.allowed_kinds:
            raise GatewayTransportCompositionError(
                "GATEWAY_COMMAND_KIND_DENIED", "authenticated principal lacks command permission"
            )
        outcome = self.authority.handle(principal, message)
        if not isinstance(outcome, Mapping):
            raise GatewayTransportCompositionError(
                "GATEWAY_OUTCOME_INVALID", "gateway authority must return an outcome mapping"
            )
        return outcome


def create_gateway_zmq_server(
    *,
    gateway: Any,
    transport: Any,
    authority: Any,
    endpoint: str,
    authenticator: Callable[[bytes, Any], Any],
    **server_options: Any,
) -> Any:
    """Create a ZMQ server wired through the strict gateway command handler.

    All security-sensitive dependencies are explicit. In remote TCP mode the
    transport additionally requires CurveZMQ credentials and a non-empty
    ``remote_principals`` ACL; the injected authenticator is used only by the
    transport's local fake/in-process path.
    """

    handler = GatewayTransportCommandHandler(
        gateway=gateway,
        transport=transport,
        authority=authority,
    )
    server_type = getattr(transport, "ZmqCommandServer", None)
    if server_type is None:
        raise GatewayTransportCompositionError(
            "TRANSPORT_SERVER_MISSING", "transport ZMQ command server is unavailable"
        )
    return server_type(endpoint, authenticator, handler, **server_options)


__all__ = [
    "GatewayTransportCommandHandler",
    "GatewayTransportCompositionError",
    "create_gateway_zmq_server",
    "gateway_principal_from_remote_grant",
]
