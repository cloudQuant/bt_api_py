"""Local TTL and binding fences; synthetic proof is not CURVE evidence."""

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

import bt_api_py.gateway_instrument_product_client as module
from bt_api_py.gateway_instrument_product_client import (
    GatewayInstrumentProductClient,
    InstrumentProductClientError,
)
from bt_api_py.gateway_instrument_product_codec import (
    BEGIN,
    OPERATIONS,
    SCHEMA,
    validate_response,
)
from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime

KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701")
IDENTITY = WireIdentity(WireRole.SERVICE, "monitor", "reader")
PEER = WireIdentity(WireRole.SERVICE, "gateway", "gateway-service")
SCOPE = WireScope("domain", account_ref="account")
OPID = "a" * 32


def fake(monkeypatch):
    runtime = object.__new__(ServiceClientRuntime)
    binding = RegisteredBinding(
        "upstream.instrument_products",
        "gateway-service",
        "instance",
        "scope",
        SCOPE,
        "reg",
        "epoch",
        OPERATIONS,
        300,
        "policy",
        "requirements",
        "transport",
        1,
        lease_deadline=250,
        renewal_due_at=200,
    )
    runtime._state, runtime._bindings, runtime.identity, runtime.run_id = (
        "registered",
        [binding],
        IDENTITY,
        "run",
    )
    runtime._cleanup_unknown = None
    runtime._on_cleanup_sticky = None
    runtime._monotonic_now = lambda: 100
    objects = tuple(object() for _ in range(7))
    runtime._registration_lineage = {id(binding): (binding, None, *objects)}
    runtime._has_current_registration_observation = lambda b: (
        id(b) in runtime._registration_lineage and runtime._registration_lineage[id(b)][0] is b
    )
    runtime.authenticated_peer_for_binding = lambda _: (PEER, "f" * 64)
    runtime._transports = {("gateway-service", "transport"): object()}
    clock = SimpleNamespace(now=10.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    snapshot = {
        "schema": SCHEMA,
        "instrument": KEY.to_dict(),
        "product_id": "rb",
        "session_generation": "generation",
        "connection_generation": 1,
        "trading_day": "2026-11-06",
        "completed_at_utc": "2026-11-06T00:00:00Z",
        "remaining_seconds": 5.0,
    }

    def call(_mount, op, _args, **_kwargs):
        if op == BEGIN:
            return {"operation_id": OPID, "status": "pending", "code": None, "snapshot": None}
        return {"operation_id": OPID, "status": "ready", "code": None, "snapshot": snapshot}

    runtime.call = call
    client = GatewayInstrumentProductClient(
        runtime,
        identity=IDENTITY,
        scope=SCOPE,
        peer=PEER,
        service_id="gateway-service",
        instance_id="instance",
        scope_ref="scope",
    )
    return client, runtime, clock, snapshot, objects


def test_status_ttl_counts_from_send_not_arrival(monkeypatch):
    client, runtime, clock, snapshot, _ = fake(monkeypatch)
    assert client.begin(OPID, KEY).status == "pending"
    original = runtime.call

    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        clock.now += 3
        return result

    runtime.call = slow
    result = client.status(OPID)
    assert result.valid_until_monotonic == 15 and clock.now == 13
    clock.now = 20
    snapshot["remaining_seconds"] = 1
    with pytest.raises(InstrumentProductClientError, match="OBSERVATION_EXPIRED"):
        client.status(OPID)
    client.close()
    assert runtime.state == "registered"


def test_authenticated_renewal_and_replacement_or_scope_rejected(monkeypatch):
    client, runtime, _, _, lineage = fake(monkeypatch)
    assert client.begin(OPID, KEY).status == "pending"
    original = runtime._bindings[0]
    renewed = replace(original, lease_deadline=275, expires_at=325, renewal_due_at=220)
    runtime._bindings = [renewed]
    runtime._registration_lineage[id(renewed)] = (renewed, None, *lineage, object())
    assert client.status(OPID).status == "ready"
    fake_copy = replace(renewed)
    runtime._bindings = [fake_copy]
    with pytest.raises(InstrumentProductClientError, match="PRODUCT_UNAVAILABLE"):
        client.status(OPID)
    runtime._bindings = [replace(renewed, registration_id="new-reg")]
    runtime._registration_lineage[id(runtime._bindings[0])] = (runtime._bindings[0], None, *lineage)
    with pytest.raises(InstrumentProductClientError, match="PRODUCT_UNAVAILABLE"):
        client.status(OPID)
    assert runtime.state == "registered"
    client.close()


def test_terminal_retirement_bounds_capacity_and_never_drops_pending(monkeypatch):
    client, runtime, _, snapshot, _ = fake(monkeypatch)

    def call(_mount, op, args, **_kwargs):
        if op == BEGIN:
            return {
                "operation_id": args["operation_id"],
                "status": "pending",
                "code": None,
                "snapshot": None,
            }
        return {
            "operation_id": args["operation_id"],
            "status": "ready",
            "code": None,
            "snapshot": snapshot,
        }

    runtime.call = call
    for index in range(130):
        operation_id = f"{index:032x}"
        client.begin(operation_id, KEY)
        with pytest.raises(InstrumentProductClientError, match="OPERATION_NOT_TERMINAL"):
            client.retire(operation_id)
        assert client.status(operation_id).status == "ready"
        client.retire(operation_id)
    assert not client._jobs


def test_peer_and_owner_thread_fail_without_closing_borrowed_runtime(monkeypatch):
    client, runtime, _, _, _ = fake(monkeypatch)
    client.begin(OPID, KEY)
    failures = []

    def wrong_thread():
        try:
            client.status(OPID)
        except InstrumentProductClientError as error:
            failures.append(error.code)

    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join()
    assert failures == ["PRODUCT_UNAVAILABLE"]
    runtime.authenticated_peer_for_binding = lambda _: (replace(PEER, actor_ref="other"), "f" * 64)
    with pytest.raises(InstrumentProductClientError, match="PRODUCT_UNAVAILABLE"):
        client.status(OPID)
    assert runtime.state == "registered"
    client.close()


def test_uncertain_begin_retains_operation_until_terminal_observed(monkeypatch):
    client, runtime, _, _, _ = fake(monkeypatch)
    original_call = runtime.call

    def interrupted(*args, **kwargs):
        if args[1] == BEGIN:
            raise RuntimeError("synthetic lost reply")
        return original_call(*args, **kwargs)

    runtime.call = interrupted
    with pytest.raises(InstrumentProductClientError, match="PRODUCT_UNAVAILABLE"):
        client.begin(OPID, KEY)
    with pytest.raises(InstrumentProductClientError, match="OPERATION_NOT_TERMINAL"):
        client.retire(OPID)
    assert client.status(OPID).status == "ready"
    client.retire(OPID)
    assert runtime.state == "registered"
    client.close()


@pytest.mark.parametrize("operation", ["begin", "status", "cancel"])
@pytest.mark.parametrize("lost_reply", [False, True])
def test_new_call_clears_old_terminal_before_sending(monkeypatch, lost_reply, operation):
    client, runtime, _, _, _ = fake(monkeypatch)
    client.begin(OPID, KEY)
    assert client.status(OPID).status == "ready"

    def invoke():
        return client.begin(OPID, KEY) if operation == "begin" else getattr(client, operation)(OPID)

    if lost_reply:

        def interrupted(*_args, **_kwargs):
            raise RuntimeError("synthetic lost reply")

        runtime.call = interrupted
        with pytest.raises(InstrumentProductClientError, match="PRODUCT_UNAVAILABLE"):
            invoke()
    else:
        runtime.call = lambda *_args, **_kwargs: {
            "operation_id": OPID,
            "status": "pending",
            "code": None,
            "snapshot": None,
        }
        assert invoke().status == "pending"
    with pytest.raises(InstrumentProductClientError, match="OPERATION_NOT_TERMINAL"):
        client.retire(OPID)
    client.close()


@pytest.mark.parametrize(
    "change",
    [
        {"status": []},
        {"status": {}},
        {"code": []},
        {"status": "failed", "code": "unknown"},
        {"status": "ready", "snapshot": None},
        {"operation_id": "bad"},
    ],
)
def test_response_rejects_malformed_envelopes(change):
    value = {"operation_id": OPID, "status": "pending", "code": None, "snapshot": None}
    value.update(change)
    with pytest.raises(ValueError, match="INVALID_INSTRUMENT_PRODUCT_RESPONSE"):
        validate_response(value, expected_operation_id=OPID)


@pytest.mark.parametrize(
    "field,value",
    [
        ("remaining_seconds", -1),
        ("remaining_seconds", float("nan")),
        ("remaining_seconds", True),
        ("connection_generation", True),
        ("trading_day", "20261106"),
        ("completed_at_utc", "2026-11-06T00:00:00+08:00"),
        ("product_id", ""),
        ("instrument", {"foo": "bar"}),
    ],
)
def test_snapshot_rejects_malformed_values(monkeypatch, field, value):
    _, _, _, snapshot, _ = fake(monkeypatch)
    snapshot[field] = value
    with pytest.raises(ValueError, match="INVALID_INSTRUMENT_PRODUCT_RESPONSE"):
        validate_response(
            {"operation_id": OPID, "status": "ready", "code": None, "snapshot": snapshot},
            expected_operation_id=OPID,
            expected_instrument=KEY,
        )
