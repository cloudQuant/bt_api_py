"""Iteration 60 (acceptance report P1-3): service wire v3 contract handshake.

A client refuses to register with a service whose protocol version or wire
contract digest differs from its own, before any other call is made.
"""

from __future__ import annotations

import pytest

import tests.unit.test_service_telemetry as telemetry
from bt_api_base.service_protocol import WIRE_PROTOCOL_VERSION, wire_contract_digest
from bt_api_py.service_runtime import ServiceRuntimeError


def test_matching_contract_registers():
    runtime, leaves = telemetry._runtime(required=True)
    try:
        assert [r.header.operation for r in leaves[0].requests][:1] == ["service.discover"]
        assert leaves[0].requests[0].header.protocol_version == WIRE_PROTOCOL_VERSION
    finally:
        runtime.close()


def test_a_service_on_another_wire_contract_is_refused(monkeypatch):
    monkeypatch.setattr(telemetry, "_wire_contract_digest", lambda: "0" * 64)
    with pytest.raises(ServiceRuntimeError) as raised:
        telemetry._runtime(required=True)
    assert raised.value.code == "PROTOCOL_VERSION_MISMATCH"


def test_a_service_on_another_protocol_version_is_refused(monkeypatch):
    monkeypatch.setattr(telemetry, "_WIRE_PROTOCOL_VERSION", 2)
    with pytest.raises(ServiceRuntimeError) as raised:
        telemetry._runtime(required=True)
    assert raised.value.code == "PROTOCOL_VERSION_MISMATCH"


def test_envelope_digest_covers_the_typed_major_and_negotiates_the_registry(monkeypatch):
    """60.08 (G5): the typed minor/registry moved to discover negotiation.

    A typed registry change no longer makes every envelope undecodable (which
    only produced a silent timeout); it is refused explicitly at connect
    (``TYPED_CONTRACT_MISMATCH``, see test_iter60_typed_contract_matrix) and
    per typed part. A typed major change still fences every envelope.
    """
    import bt_api_base.service_protocol as protocol
    import bt_api_base.wire_codec_v1 as codec

    digest = wire_contract_digest()
    assert len(digest) == 64 and digest == wire_contract_digest()
    monkeypatch.setattr(codec, "schema_digest", lambda: "changed-field-table")
    assert protocol._contract_digest() == digest
    monkeypatch.setattr(codec, "SUPPORTED_MAJOR", codec.SUPPORTED_MAJOR + 1)
    assert protocol._contract_digest() != digest
