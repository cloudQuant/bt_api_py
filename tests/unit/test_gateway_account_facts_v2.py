"""Authenticated v2 facts pages are issued only by their original client."""

import threading
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole

import bt_api_py.gateway_account_facts_client as facts_client
from bt_api_py.gateway_account_facts_client import GatewayAccountFactsClient
from bt_api_py.service_runtime_thread import (
    BoundCallObservation,
    BoundCallResult,
    _BoundBindingToken,
)
from tests.unit.test_iter63_account_facts_client import _client

PEER = WireIdentity(WireRole.SERVICE, "gateway", "gateway-service")
FINGERPRINT = "f" * 64
EPOCH = "a" * 64


@pytest.fixture
def v2(monkeypatch):
    legacy, runtime, snap, binding, base, event = _client(monkeypatch)
    binding = replace(binding, operations=binding.operations | {"account.facts.read_after.v2"})
    snap = replace(snap, bindings=(binding,))
    monkeypatch.setattr(runtime, "status", lambda: snap)
    client = GatewayAccountFactsClient(
        runtime, route=legacy._route, peer=PEER, fingerprint=FINGERPRINT
    )
    calls = []
    wire = {
        "status": "OK",
        "schema": "sdk.gateway.account_facts.page/2",
        "journal_id": "journal-1",
        "page": {**base, "events": [event], "has_more": False},
    }

    def call(_mount, operation, arguments, **kwargs):
        calls.append(operation)
        route = client._route
        obs = BoundCallObservation(
            route.identity,
            "run",
            route.scope,
            "registration",
            "epoch",
            route.mount,
            route.service_id,
            route.instance_id,
            route.scope_ref,
            binding.operations,
            PEER,
            FINGERPRINT,
            1000.0,
            True,
            True,
            operation,
        )
        result = (
            wire
            if operation.endswith(".v2")
            else {
                "status": "OK",
                "progress": {**base, "acknowledged_sequence": arguments["sequence"]},
            }
        )
        return BoundCallResult(
            result,
            obs,
            obs,
            _BoundBindingToken(object(), object(), object(), object(), (), (), obs),
        )

    monkeypatch.setattr(runtime, "call_bound", call)
    return client, runtime, calls, wire, snap


def test_v2_copy_and_ack_only_exact_result(v2):
    client, _, calls, _, _ = v2
    result = client.read_after_v2()
    assert result.outcome == "OBSERVED"
    copied = client.validate_copy_issued_page(result)
    assert copied.journal_id == "journal-1" and copied.page == result.page
    assert copied.page is not result.page and copied.page.events[0] is not result.page.events[0]
    with pytest.raises(ValueError, match="ISSUED_PAGE_INVALID"):
        client.validate_copy_issued_page(replace(result))
    assert (
        client.acknowledge(1, expected_epoch=EPOCH, read_token=result._read_token).outcome
        == "OBSERVED"
    )
    assert calls == ["account.facts.read_after.v2", "account.facts.ack"]
    with pytest.raises(ValueError, match="ISSUED_PAGE_INVALID"):
        client.validate_copy_issued_page(result)


def test_copy_uses_one_snapshot_when_original_mutates_concurrently(v2, monkeypatch):
    client, _, calls, _, _ = v2
    result = client.read_after_v2()
    original_reason = result.page.events[0].reason
    snapshot_ready = threading.Event()
    mutation_done = threading.Event()
    original_wire = facts_client._issued_wire
    reads = []

    def paused_wire(value):
        wire = original_wire(value)
        reads.append(None)
        if len(reads) == 1:
            snapshot_ready.set()
            assert mutation_done.wait(5)
        return wire

    def mutate():
        assert snapshot_ready.wait(5)
        object.__setattr__(result.page.events[0], 'reason', 'MUTATED_CONCURRENTLY')
        mutation_done.set()

    monkeypatch.setattr(facts_client, '_issued_wire', paused_wire)
    mutator = threading.Thread(target=mutate)
    mutator.start()
    try:
        copied = client.validate_copy_issued_page(result)
    finally:
        mutator.join(timeout=5)
    assert not mutator.is_alive()
    assert copied.page.events[0].reason == original_reason
    assert len(reads) == 1
    assert client.acknowledge(1, expected_epoch=EPOCH, read_token=result._read_token).code == 'READ_BINDING_REQUIRED'
    assert calls == ['account.facts.read_after.v2']


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: object.__setattr__(r, "journal_id", "forged"),
        lambda r: object.__setattr__(r.page, "has_more", True),
        lambda r: object.__setattr__(r.page, "events", list(r.page.events)),
        lambda r: object.__setattr__(r.page, "progress", replace(r.page.progress)),
        lambda r: object.__setattr__(r.page.events[0], "reason", "FORGED"),
        lambda r: object.__setattr__(r.page.events[0].observation, "trade_quantity", "99"),
    ],
)
def test_mutated_page_cannot_copy_or_ack(v2, mutate):
    client, _, calls, _, _ = v2
    result = client.read_after_v2()
    mutate(result)
    with pytest.raises(ValueError, match="ISSUED_PAGE_INVALID"):
        client.validate_copy_issued_page(result)
    assert (
        client.acknowledge(1, expected_epoch=EPOCH, read_token=result._read_token).code
        == "READ_BINDING_REQUIRED"
    )
    assert calls == ["account.facts.read_after.v2"]


@pytest.mark.parametrize(
    "field,value", [("journal_id", ""), ("journal_id", None), ("schema", "wrong"), ("extra", 1)]
)
def test_invalid_wire_is_unknown(v2, field, value):
    client, _, _, wire, _ = v2
    wire[field] = value
    assert client.read_after_v2().outcome == "UNKNOWN"


def test_new_read_invalidates_old_and_registration_change_fails(v2, monkeypatch):
    client, runtime, _, _, snap = v2
    old = client.read_after_v2()
    current = client.read_after_v2()
    with pytest.raises(ValueError):
        client.validate_copy_issued_page(old)
    assert (
        client.acknowledge(1, expected_epoch=EPOCH, read_token=old._read_token).code
        == "READ_BINDING_REQUIRED"
    )
    monkeypatch.setattr(
        runtime,
        "status",
        lambda: replace(snap, bindings=(replace(snap.bindings[0], registration_id="new"),)),
    )
    with pytest.raises(ValueError):
        client.validate_copy_issued_page(current)
    assert (
        client.acknowledge(1, expected_epoch=EPOCH, read_token=current._read_token).code
        == "READ_BINDING_REQUIRED"
    )


def test_empty_page_is_copyable_but_cannot_ack(v2):
    client, _, calls, wire, _ = v2
    wire["page"].update(events=[], has_more=False, bootstrap_count=0, offered_sequence=0)
    result = client.read_after_v2()
    assert result.outcome == "OBSERVED"
    assert client.validate_copy_issued_page(result).page.events == ()
    assert (
        client.acknowledge(0, expected_epoch=EPOCH, read_token=result._read_token).code
        == "READ_BINDING_REQUIRED"
    )
    assert calls == ["account.facts.read_after.v2"]


def test_another_client_cannot_copy_original_issuance(v2):
    client, runtime, _, _, _ = v2
    result = client.read_after_v2()
    other = GatewayAccountFactsClient(
        runtime, route=client._route, peer=PEER, fingerprint=FINGERPRINT
    )
    with pytest.raises(ValueError, match="ISSUED_PAGE_INVALID"):
        other.validate_copy_issued_page(result)
    assert (
        other.acknowledge(1, expected_epoch=EPOCH, read_token=result._read_token).code
        == "READ_BINDING_REQUIRED"
    )


def test_wrong_thread_and_legacy_no_v2(v2, monkeypatch):
    client, _, _, _, _ = v2
    result = client.read_after_v2()
    errors = []

    def worker():
        try:
            client.validate_copy_issued_page(result)
        except Exception as exc:
            errors.append(str(exc))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert errors == ["ACCOUNT_FACTS_WRONG_OWNER"]
    legacy, *_ = _client(monkeypatch)
    with pytest.raises(ValueError, match="STRICT_ACCOUNT_FACTS_REQUIRED"):
        legacy.read_after_v2()
