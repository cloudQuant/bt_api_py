"""Iteration 60.08 (G5): typed contract negotiation and mixed-version matrix.

Every request and reply of these tests crosses a real strict v3 MessagePack
envelope encode/decode. "Old" peers are simulated in process: an old server
declares another typed contract in ``service.discover`` (or none, with another
envelope contract), an old client compares the reply with another local
declaration. Mismatches are refused with explicit codes before registration;
typed parts are also refused per message by their registry digest. There is
never a fallback to a generic map.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

import bt_api_base.wire_codec_v1 as codec
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireMessageKind,
    decode_envelope,
    encode_envelope,
)
from bt_api_py.service_account import ServiceAccountClient, ServiceAccountError
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError
from tests.unit import test_service_account as unit
from tests.unit.test_service_account_typed_rpc import _typed_reply

_TYPED = "account.snapshot.typed"
_OLD_DECLARATION = {
    "schema_id": codec.SCHEMA_ID,
    "major": codec.SUPPORTED_MAJOR,
    "minor": codec.SUPPORTED_MINOR - 1,
    "registry_digest": "0" * 64,
}


class _WireTransport:
    """In-process service transport that crosses real v3 envelopes.

    ``old_server`` rewrites the server's discover declaration, ``old_client``
    swaps the client's local declaration while it reads the reply.
    """

    def __init__(self, registry, monkeypatch, *, old_server=None, old_client=False):
        self.registry = registry
        self.monkeypatch = monkeypatch
        self.old_server = old_server
        self.old_client = old_client
        self.operations: list[str] = []
        self.closed = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        del timeout
        request = decode_envelope(encode_envelope(request))
        self.operations.append(request.header.operation)
        result = self.registry.handle(unit._CLIENT.principal_ref, request)
        if request.header.operation == "service.discover":
            result = dict(result)
            body = dict(result["result"])
            if self.old_server == "older_typed":
                body["typed_contract"] = dict(_OLD_DECLARATION)
            elif self.old_server == "malformed_typed":
                body["typed_contract"] = {"minor": "5"}
            elif self.old_server == "pre_negotiation":
                body.pop("typed_contract")
                body["wire_contract"] = "f" * 64
            elif self.old_server == "undeclared":
                body.pop("typed_contract")
            result["result"] = body
            if self.old_client:
                self.monkeypatch.setattr(
                    codec, "typed_contract_declaration", lambda: dict(_OLD_DECLARATION)
                )
        reply = WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=unit._PEER,
            ),
            result,
        )
        return decode_envelope(encode_envelope(reply))

    def close(self) -> None:
        self.closed = True


def _start(tmp_path, monkeypatch, *, reply=None, snapshot_format=None, **transport_options):
    operations = frozenset({_TYPED})
    capability = ServiceCapability("account-instance", "service.account_read", operations)
    grant = ResourceGrant(
        "account-instance", "service.account_read", "account-scope-v1", unit._SCOPE, operations
    )
    registry = ServiceRegistry(
        tmp_path / f"account-{uuid4().hex}.sqlite3",
        service_id="account-service",
        capabilities=(capability,),
        grants={unit._CLIENT.principal_ref: (PrincipalGrant(unit._CLIENT, (grant,)),)},
        policy_version="account-policy-v1",
        requirements_version="account-requirements-v1",
        lease_seconds=30,
    )
    source = unit.AccountService(reply if reply is not None else _typed_reply())
    registry.register_handler("account-instance", _TYPED, source.snapshot)
    registry.set_ready(True)
    clock = unit.ManualClock()
    transport = _WireTransport(registry, monkeypatch, **transport_options)
    config = parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": unit._CLIENT.actor_ref},
            "clients": {
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "store.account",
                                "service_id": "account-service",
                                "instance_id": "account-instance",
                                "scope_ref": "account-scope-v1",
                                "account_ref": unit._ACCOUNT_REF,
                                "transport_ref": "account-rpc",
                            }
                        ],
                        "options": {"request_timeout_ms": 1000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "account-rpc": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7811"},
                        "security_profile_ref": "account-keys",
                    }
                },
            },
        }
    )
    runtime = ServiceClientRuntime(
        config,
        identity=unit._CLIENT,
        scope_resolver=lambda _scope_ref: unit._SCOPE,
        peer_resolver=lambda _service_id: unit._PEER,
        transport_factory=lambda _profile, _peer: transport,
        run_id="account-run-1",
        requested_operations={"store.account": operations},
        monotonic_clock=clock,
    )
    return runtime, registry, source, transport, clock


def _client(runtime, clock, **kwargs):
    return ServiceAccountClient(
        runtime,
        "store.account",
        account_ref=unit._ACCOUNT_REF,
        expected_account=unit._ACCOUNT,
        required_coverage=unit._COVERAGE,
        monotonic_clock=clock,
        **kwargs,
    )


def test_discover_declares_the_typed_contract():
    declaration = codec.typed_contract_declaration()
    assert set(declaration) == {"schema_id", "major", "minor", "registry_digest"}
    assert declaration["minor"] == codec.SUPPORTED_MINOR
    assert declaration["registry_digest"] == codec.schema_digest()
    codec.check_typed_contract_declaration(declaration)


def test_equal_peers_register_and_read_typed_by_default(tmp_path, monkeypatch):
    runtime, registry, source, transport, clock = _start(tmp_path, monkeypatch)
    runtime.start()
    try:
        client = _client(runtime, clock)  # no snapshot_format: typed is the default
        view = client.refresh()
        assert view.observation.to_dict() == unit._observation().to_dict()
        assert transport.operations[0] == "service.discover"
        assert transport.operations[-1] == _TYPED
        assert "account.snapshot" not in transport.operations
        client.close()
    finally:
        runtime.close()
        registry.close()


@pytest.mark.parametrize(
    "old_server, code",
    [
        ("older_typed", "TYPED_CONTRACT_MISMATCH"),
        ("malformed_typed", "TYPED_CONTRACT_INVALID"),
        ("pre_negotiation", "PROTOCOL_VERSION_MISMATCH"),
    ],
)
def test_new_client_refuses_an_old_server_before_registration(
    tmp_path, monkeypatch, old_server, code
):
    runtime, registry, _source, transport, _clock = _start(
        tmp_path, monkeypatch, old_server=old_server
    )
    try:
        with pytest.raises(ServiceRuntimeError) as raised:
            runtime.start()
        assert raised.value.code == code
        # Nothing but the handshake was sent: no registration, no typed read.
        assert transport.operations == ["service.discover"]
    finally:
        runtime.close()
        registry.close()


def test_old_client_refuses_a_new_server_before_registration(tmp_path, monkeypatch):
    runtime, registry, _source, transport, _clock = _start(
        tmp_path, monkeypatch, old_client=True
    )
    try:
        with pytest.raises(ServiceRuntimeError) as raised:
            runtime.start()
        assert raised.value.code == "TYPED_CONTRACT_MISMATCH"
        assert transport.operations == ["service.discover"]
    finally:
        runtime.close()
        registry.close()


def test_typed_parts_from_another_registry_are_refused_per_message(tmp_path, monkeypatch):
    # A server that does not negotiate (no declaration) is admitted for its
    # generic surface, but each typed part is still fenced by its own digest.
    stale = _typed_reply()
    stale["snapshot"]["payload"]["observation"]["registry_digest"] = "0" * 64
    runtime, registry, _source, transport, clock = _start(
        tmp_path, monkeypatch, old_server="undeclared", reply=stale
    )
    runtime.start()
    try:
        client = _client(runtime, clock)
        with pytest.raises(ServiceAccountError, match="TYPED_CONTRACT_MISMATCH"):
            client.refresh()
        older = _typed_reply()
        older["snapshot"]["payload"]["observation"]["minor"] = codec.SUPPORTED_MINOR - 1
        _source.response = older
        with pytest.raises(ServiceAccountError, match="TYPED_CONTRACT_MISMATCH"):
            client.refresh()
        assert "account.snapshot" not in transport.operations  # no generic fallback
        client.close()
    finally:
        runtime.close()
        registry.close()


def test_rogue_old_reader_cannot_decode_a_new_typed_part(monkeypatch):
    part = codec.encode_typed_part(
        "strategy_result",
        codec._KINDS["strategy_result"][0](
            **{
                **{
                    name: None
                    for name in (
                        "provider_order_ref",
                        "reason_code",
                        "observed_at_utc_ns",
                        "source_precision_ns",
                    )
                },
                "account_key": unit._ACCOUNT,
                "route": "route-a",
                "strategy_ref": "strategy-a",
                "run_id": "run-a",
                "request_id": "request-a",
                "idempotency_key": "intent-a",
                "operation": "submit",
                "outcome": "unknown",
                "command_fingerprint": "a" * 64,
            }
        ),
    )
    assert codec.decode_typed_part(part, kind="strategy_result").outcome == "unknown"
    monkeypatch.setattr(codec, "SUPPORTED_MINOR", codec.SUPPORTED_MINOR - 1)
    with pytest.raises(codec.WireCodecError, match="WIRE_TYPED_CONTRACT_MISMATCH"):
        codec.decode_typed_part(part, kind="strategy_result")


def test_every_sdk_service_operation_has_a_registered_wire_contract():
    from bt_api_py.service_runtime import PORT_OPERATIONS

    operations = sorted(set().union(*PORT_OPERATIONS.values()))
    assert operations
    contracts = {name: codec.service_operation_contract(name)[0] for name in operations}
    assert contracts["account.snapshot.typed"] == "typed"
    assert contracts["execution.account_books.query"] == "typed"
    assert set(contracts.values()) <= {"typed", "generic"}
