"""Local Curve/ZAP acceptance of the strict transport-to-gateway bridge."""

from __future__ import annotations

import importlib
import socket
import threading
from pathlib import Path
from typing import Any

import pytest
import zmq

from bt_api_py.runtime_plugins.gateway_transport_server import (
    GatewayTransportCommandHandler,
    GatewayTransportCompositionError,
    create_gateway_zmq_server,
    gateway_principal_from_remote_grant,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CAPABILITY_SOURCES = (
    _REPO_ROOT / "bt_api" / "bt_api_gateway" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_transport_zmq" / "src",
)
_ACCOUNT_SCOPE = "fixture-account-scope"
_STRATEGY_SCOPE = "fixture-strategy-scope"
_NOW = 1_800_000_000.0


class _RouterAuthority:
    """Tiny authority shell that exercises the real gateway router contract."""

    def __init__(self, gateway: Any, transport: Any, router: Any) -> None:
        self.gateway = gateway
        self.transport = transport
        self.router = router
        self.handle_calls = 0
        self.principals: list[Any] = []

    def handle(self, principal: Any, message: Any) -> dict[str, Any]:
        self.handle_calls += 1
        self.principals.append(principal)
        command = self.gateway.gateway_command_from_wire_payload(message.payload)
        result = self.router.dispatch(principal, command)
        return {
            "command_fingerprint": command.fingerprint,
            "command_id": command.command_id,
            "status": result.status.value,
        }


def _modules(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    for source in _CAPABILITY_SOURCES:
        monkeypatch.syspath_prepend(str(source))
    return (
        importlib.import_module("bt_api_gateway"),
        importlib.import_module("bt_api_transport_zmq"),
    )


def _grant(transport: Any, kinds: frozenset[str] = frozenset({"read", "submit"})) -> Any:
    return transport.RemotePrincipalGrant(
        principal_id="fixture-principal",
        account_id="fixture-account",
        strategy_id="fixture-strategy",
        account_scope=_ACCOUNT_SCOPE,
        strategy_scope=_STRATEGY_SCOPE,
        allowed_kinds=kinds,
    )


def _command(gateway: Any, command_id: str, *, kind: str = "read", payload: Any = None) -> Any:
    return gateway.GatewayCommand(
        command_id=command_id,
        account_scope=_ACCOUNT_SCOPE,
        strategy_scope=_STRATEGY_SCOPE,
        kind=gateway.GatewayCommandKind(kind),
        payload={} if payload is None else payload,
        receipt_digest="a" * 64,
        issued_at=_NOW - 5,
        expires_at=_NOW + 30,
    )


def _wire_message(transport: Any, gateway: Any, command: Any, *, sequence: int) -> Any:
    return transport.WireMessage(
        message_id=command.command_id,
        channel=transport.WireChannel.COMMAND,
        scope=_STRATEGY_SCOPE,
        sequence=sequence,
        sent_at=_NOW,
        payload=gateway.gateway_command_to_wire_payload(command),
    )


def _tcp_pair(
    gateway: Any,
    transport: Any,
    authority: _RouterAuthority,
    *,
    context: Any,
) -> tuple[Any, Any, Any]:
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    endpoint = f"tcp://0.0.0.0:{port}"
    server = create_gateway_zmq_server(
        gateway=gateway,
        transport=transport,
        authority=authority,
        endpoint=endpoint,
        authenticator=lambda *_: pytest.fail("remote path must use the ZAP-derived grant"),
        context=context,
        curve_credentials=transport.CurveServerCredentials(server_public, server_secret),
        allow_remote=True,
        remote_principals={client_public: _grant(transport)},
    )
    client = transport.ZmqCommandClient(
        f"tcp://127.0.0.1:{port}",
        context=context,
        curve_credentials=transport.CurveClientCredentials(
            server_public, client_public, client_secret
        ),
        allow_remote=True,
    )
    return server, client, context


def _serve_requests(server: Any, count: int) -> threading.Thread:
    worker = threading.Thread(
        target=lambda: [server.serve_once(timeout_ms=3_000) for _ in range(count)]
    )
    worker.start()
    return worker


def test_remote_curve_zap_request_is_decoded_and_dispatched_by_gateway_router(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gateway, transport = _modules(monkeypatch)
    calls: list[str] = []
    database = tmp_path / "gateway.sqlite"
    writer_authority = gateway.GatewayAccountWriterAuthority(database, clock=lambda: _NOW)
    writer_authority.acquire_writer(_ACCOUNT_SCOPE, "fixture-writer", lease_seconds=40)
    router = gateway.GatewayCommandRouter(
        database,
        lambda command: calls.append(command.command_id) or {"fixture": "accepted"},
        clock=lambda: _NOW,
        admission=lambda _principal, _command: True,
        writer_authority=writer_authority,
    )
    authority = _RouterAuthority(gateway, transport, router)
    context = zmq.Context()
    server, client, _ = _tcp_pair(gateway, transport, authority, context=context)
    commands = [
        _command(gateway, "valid-submit", kind="submit"),
        _command(gateway, "wrong-schema", kind="submit"),
        _command(gateway, "wrong-fingerprint", kind="submit"),
        _command(gateway, "forged-account", kind="submit", payload={"account_id": "other-account"}),
        _command(
            gateway, "forged-strategy", kind="submit", payload={"strategy_id": "other-strategy"}
        ),
    ]
    messages = [
        _wire_message(transport, gateway, command, sequence=index)
        for index, command in enumerate(commands)
    ]
    messages[1].payload["schema"] = "gateway-command-v1"
    messages[2].payload["command_fingerprint"] = "0" * 64
    worker = _serve_requests(server, len(messages))
    responses = []
    try:
        responses.extend(client.request(message, timeout_ms=3_000) for message in messages)
    finally:
        worker.join(timeout=5)
        client.close()
        server.close()
        context.term()

    assert not worker.is_alive()
    assert responses[0].payload["accepted"] is True
    assert responses[0].payload["outcome"]["status"] == "returned_unverified"
    assert [response.payload["accepted"] for response in responses[1:]] == [False] * 4
    assert calls == ["valid-submit"]
    # The two schema/fingerprint failures reach the adapter but not authority;
    # forged identity claims are rejected by the transport ACL before handler.
    assert authority.handle_calls == 1
    assert authority.principals[0] == gateway.GatewayPrincipal(
        principal_id="fixture-principal",
        account_scopes=frozenset({_ACCOUNT_SCOPE}),
        strategy_scopes=frozenset({_STRATEGY_SCOPE}),
        allowed_kinds=frozenset(
            {gateway.GatewayCommandKind.READ, gateway.GatewayCommandKind.SUBMIT}
        ),
    )


@pytest.mark.parametrize(
    ("admission", "writer"),
    [
        (None, False),  # no server permission and no writer authority
        (True, False),  # explicit permission but no writer authority
        (True, True),  # writer authority exists but no active lease
    ],
    ids=("no-permission", "no-writer-authority", "no-writer-lease"),
)
def test_remote_submit_fails_closed_without_permission_or_active_writer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    admission: bool | None,
    writer: bool,
) -> None:
    gateway, transport = _modules(monkeypatch)
    database = tmp_path / "gateway.sqlite"
    provider_calls: list[str] = []
    writer_authority = (
        gateway.GatewayAccountWriterAuthority(database, clock=lambda: _NOW) if writer else None
    )
    router = gateway.GatewayCommandRouter(
        database,
        lambda command: provider_calls.append(command.command_id) or {"provider": "called"},
        clock=lambda: _NOW,
        admission=(lambda _principal, _command: admission is True)
        if admission is not None
        else None,
        writer_authority=writer_authority,
    )
    authority = _RouterAuthority(gateway, transport, router)
    context = zmq.Context()
    server, client, _ = _tcp_pair(gateway, transport, authority, context=context)
    command = _command(gateway, "submit-without-authority", kind="submit")
    worker = _serve_requests(server, 1)
    try:
        response = client.request(
            _wire_message(transport, gateway, command, sequence=1), timeout_ms=3_000
        )
    finally:
        worker.join(timeout=5)
        client.close()
        server.close()
        context.term()

    assert not worker.is_alive()
    assert response.payload["accepted"] is False
    assert provider_calls == []
    assert authority.handle_calls == 1


def test_bridge_rejects_missing_gateway_authority_and_untrusted_local_principal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gateway, transport = _modules(monkeypatch)
    with pytest.raises(GatewayTransportCompositionError, match="gateway and transport"):
        gateway_principal_from_remote_grant(None, transport, object())

    router = gateway.GatewayCommandRouter(tmp_path / "gateway.sqlite", lambda _command: {})
    authority = _RouterAuthority(gateway, transport, router)
    with pytest.raises(GatewayTransportCompositionError, match="gateway"):
        GatewayTransportCommandHandler(gateway=None, transport=transport, authority=authority)
    with pytest.raises(GatewayTransportCompositionError, match="authority"):
        GatewayTransportCommandHandler(gateway=gateway, transport=transport, authority=None)

    handler = GatewayTransportCommandHandler(
        gateway=gateway, transport=transport, authority=authority
    )
    message = _wire_message(transport, gateway, _command(gateway, "local-untrusted"), sequence=0)
    with pytest.raises(GatewayTransportCompositionError, match="ACL grant"):
        handler(object(), message)
    assert authority.handle_calls == 0
