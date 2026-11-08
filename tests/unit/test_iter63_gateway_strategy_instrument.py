"""Offline instrument client checks using synthetic owner-thread replies."""

import copy
import math
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import Freshness, InstrumentSpec
from bt_api_py.gateway_instrument_spec_codec import encode_gateway_instrument_spec
from bt_api_py.gateway_strategy_instrument import (
    INSTRUMENT_OPERATIONS,
    GatewayInstrumentClientSettings,
    GatewayInstrumentOwner,
    GatewayStrategyInstrumentClient,
)
from bt_api_py.service_runtime_thread import (
    OwnerOperationContinuation,
    OwnerOperationError,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)

KEY = MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", "IF2612", "IF2612")
SETTINGS = GatewayInstrumentClientSettings((KEY,), INSTRUMENT_OPERATIONS)


def spec():
    return InstrumentSpec(
        "CTP___FUTURE",
        "IF2612",
        "future",
        "CNY",
        "CNY",
        "future",
        True,
        Decimal("1"),
        Decimal("300"),
        Decimal(".2"),
        Decimal("1"),
        Decimal("1"),
        None,
        None,
        "contracts",
        "trading",
        Freshness("synthetic", datetime(2026, 11, 9, tzinfo=UTC)),
        "a" * 64,
        source="synthetic",
        raw={},
    )


def ready(op, remaining=10):
    return {
        "operation_id": op,
        "status": "ready",
        "code": None,
        "snapshot": {
            "instrument": KEY.to_dict(),
            "spec": encode_gateway_instrument_spec(spec()),
            "session_generation": "a" * 32,
            "connection_generation": 2,
            "trading_day": "20261109",
            "remaining_seconds": remaining,
        },
    }


class Raw:
    def __init__(self):
        self.epoch = "epoch"
        self.registration = "registration"
        self.ops = INSTRUMENT_OPERATIONS
        self.calls = []
        self.response = lambda op, args: {
            "operation_id": args["operation_id"],
            "status": "pending",
            "code": None,
            "snapshot": None,
        }
        self.identity = WireIdentity(WireRole.STRATEGY_CLIENT, "principal", "strategy")
        self.run_id = "run"
        self.healthy = True
        self.cleanup_failures = self.unregister_uncertainties = 0
        self.maintains = 0

    @property
    def bindings(self):
        return (
            SimpleNamespace(
                registration_id=self.registration,
                service_epoch=self.epoch,
                active=True,
                mount="store.account",
                service_id="gateway",
                instance_id="instance",
                scope_ref="scope",
                scope=WireScope("domain", account_ref="account"),
                operations=self.ops,
                required=True,
                lease_deadline=time.monotonic() + 60,
            ),
        )

    def account_binding_view(self, mount, account_ref, *, required_operations):
        if not required_operations <= self.ops:
            raise ValueError("denied")
        return SimpleNamespace(
            mount=mount,
            service_id="gateway",
            instance_id="instance",
            scope_ref="scope",
            account_ref=account_ref,
            registration_id=self.registration,
            service_epoch=self.epoch,
            rpc_scope=WireScope("domain", account_ref=account_ref),
            transport_ref="transport",
            operations=self.ops,
        )

    def call(self, mount, operation, arguments, **kwargs):
        self.calls.append((operation, copy.deepcopy(arguments)))
        return self.response(operation, arguments)

    def start(self):
        pass

    def maintain(self):
        self.maintains += 1

    def next_maintenance_delay(self):
        return 0.01

    def close(self):
        pass


def owner(raw):
    o = GatewayInstrumentOwner(SETTINGS, account_ref="account", scope_ref="scope")
    o.start(raw)
    return o


def run_ready(o):
    result = o.invoke("lookup", {"instrument": KEY.to_dict()})
    while isinstance(result, OwnerOperationContinuation):
        result = result.resume()
    return result


def test_pending_then_ready_and_exact_request_shape():
    raw = Raw()
    raw.response = lambda op, args: (
        ready(args["operation_id"])
        if op.endswith(".status")
        else {
            "operation_id": args["operation_id"],
            "status": "pending",
            "code": None,
            "snapshot": None,
        }
    )
    o = owner(raw)
    view = run_ready(o)
    assert raw.calls[0][1] == {
        "operation_id": view["operation_id"],
        "instrument": KEY.to_dict(),
    }
    assert raw.calls[1][1] == {"operation_id": view["operation_id"]}
    assert o.invoke("validate", {"view": view}) == {"valid": True}
    assert view["spec"]["spec"]["freshness"]["observed_at"] == "2026-11-09T00:00:00Z"
    raw.epoch = "new"
    with pytest.raises(OwnerOperationError, match="STALE_INSTRUMENT_SPEC"):
        o.invoke("validate", {"view": view})
    o.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(extra=1),
        lambda r: r.update(operation_id="b" * 32),
        lambda r: r["snapshot"].update(remaining_seconds=True),
        lambda r: r["snapshot"].update(remaining_seconds=math.nan),
        lambda r: r["snapshot"].update(remaining_seconds=36),
        lambda r: r["snapshot"].update(connection_generation=True),
        lambda r: r["snapshot"].update(session_generation=1),
        lambda r: r["snapshot"]["instrument"].update(native_symbol="OTHER"),
        lambda r: r["snapshot"]["spec"]["spec"].update(symbol="OTHER"),
        lambda r: r["snapshot"].update(extra=1),
    ],
)
def test_invalid_responses_fail_closed(mutate):
    raw = Raw()

    def response(op, args):
        result = ready(args["operation_id"])
        mutate(result)
        return result

    raw.response = response
    o = owner(raw)
    with pytest.raises(OwnerOperationError, match="INVALID_INSTRUMENT_REPLY"):
        run_ready(o)
    o.close()


def test_elapsed_source_ttl_and_binding_changes():
    raw = Raw()

    def response(op, args):
        time.sleep(0.02)
        return ready(args["operation_id"], 0.005)

    raw.response = response
    o = owner(raw)
    with pytest.raises(OwnerOperationError, match="STALE_INSTRUMENT_SPEC"):
        run_ready(o)
    o.close()
    raw = Raw()
    o = owner(raw)
    continuation = o.invoke("lookup", {"instrument": KEY.to_dict()})
    raw.registration = "replacement"
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_BINDING_CHANGED"):
        continuation.resume()
    o.close()
    assert o.cleanup_uncertainties == 1


def test_failures_close_cancel_and_exact_input():
    raw = Raw()
    raw.response = lambda op, args: {
        "operation_id": args["operation_id"],
        "status": "failed",
        "code": "SOURCE_UNAVAILABLE",
        "snapshot": None,
    }
    o = owner(raw)
    with pytest.raises(OwnerOperationError, match="SOURCE_UNAVAILABLE"):
        run_ready(o)
    with pytest.raises(OwnerOperationError, match="INVALID_OWNER_ARGUMENTS"):
        o.invoke("lookup", {"instrument": KEY.to_dict(), "extra": 1})
    raw.response = lambda op, args: {
        "operation_id": args["operation_id"],
        "status": "cancelled" if op.endswith("cancel") else "pending",
        "code": None,
        "snapshot": None,
    }
    o.invoke("lookup", {"instrument": KEY.to_dict()})
    o.close()
    assert raw.calls[-1][0].endswith(".cancel")
    assert o.cleanup_uncertainties == 0


@pytest.mark.parametrize(
    "ops",
    [
        frozenset(),
        frozenset({"account.instrument_spec.begin"}),
        set(INSTRUMENT_OPERATIONS),
    ],
)
def test_settings_require_complete_permission_triple(ops):
    with pytest.raises(ValueError, match="INVALID_INSTRUMENT_SETTINGS"):
        GatewayInstrumentClientSettings((KEY,), ops)


def test_actual_owner_thread_retains_call_after_timeout_and_maintains():
    raw = Raw()
    ready_flag = threading.Event()
    raw.response = lambda op, args: (
        ready(args["operation_id"])
        if ready_flag.is_set() and op.endswith("status")
        else {
            "operation_id": args["operation_id"],
            "status": "cancelled" if op.endswith("cancel") else "pending",
            "code": None,
            "snapshot": None,
        }
    )
    runtime = ThreadedServiceClientRuntime(
        lambda: raw, owner_extension_factory=lambda: Extension()
    )

    # Factory runs only after Extension is defined, on the owner thread.
    class Extension:
        def start(self, r):
            self.owner = owner(r)

        def invoke(self, operation, args):
            return self.owner.invoke(operation.removeprefix("instrument."), args)

        def close(self):
            self.owner.close()

        @property
        def cleanup_uncertainties(self):
            return self.owner.cleanup_uncertainties

    runtime.start()
    client = GatewayStrategyInstrumentClient(runtime, SETTINGS)
    try:
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            client.lookup("CTP___FUTURE", "IF2612", timeout_seconds=0.03)
        assert runtime.status().healthy
        assert raw.maintains > 0
        with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
            client._invoke("lookup", {"instrument": {}}, 0.1)
        ready_flag.set()
        view = client.lookup("CTP___FUTURE", "IF2612", timeout_seconds=2)
        client.validate(view)
        assert view.spec.price_tick == Decimal(".2")
        assert len([c for c in raw.calls if c[0].endswith(".begin")]) == 1
    finally:
        runtime.close()


def test_import_does_not_load_provider():
    code = """import builtins\norig = builtins.__import__\ndef blocked(name, *a, **k):\n    if name.startswith('bt_api_ctp'): raise AssertionError(name)\n    return orig(name, *a, **k)\nbuiltins.__import__ = blocked\nimport bt_api_py.gateway_strategy_instrument\n"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_frozen_wire_reply_is_detached_and_validated():
    from types import MappingProxyType

    def freeze(value):
        if isinstance(value, dict):
            return MappingProxyType({k: freeze(v) for k, v in value.items()})
        return value

    raw = Raw()
    raw.response = lambda op, args: freeze(ready(args["operation_id"]))
    o = owner(raw)
    result = run_ready(o)
    assert type(result["spec"]) is dict
    assert o.invoke("validate", {"view": result}) == {"valid": True}
    o.close()


def test_session_operation_union_and_optional_settings(monkeypatch):
    import bt_api_py.gateway_strategy_client as module
    from bt_api_py.service_config import parse_client_config
    from tests.unit.test_gateway_strategy_account import settings as account_settings
    from tests.unit.test_iter63_gateway_strategy_client import config_data, parts

    captured = []

    def runtime_factory(config, **kwargs):
        captured.append(kwargs)
        return object()

    monkeypatch.setattr(module, "ServiceClientRuntime", runtime_factory)
    monkeypatch.setattr(module, "ZmqTransportFactory", lambda profiles: object())
    data = parts()
    session = module.GatewayStrategyClientSession(**data)
    session.runtime._factory()
    assert session.instruments is None
    assert captured[-1]["requested_operations"] == {
        "broker.direct_write": data["requested_operations"]
    }
    session.close()
    cfg = config_data()
    binding = dict(cfg["clients"]["gateway"]["settings"]["bindings"][0])
    binding["mount"] = "store.account"
    cfg["clients"]["gateway"]["settings"]["bindings"].append(binding)
    data["config"] = parse_client_config(cfg)
    session = module.GatewayStrategyClientSession(
        **data, instrument_settings=SETTINGS, account_settings=account_settings()
    )
    session.runtime._factory()
    assert captured[-1]["requested_operations"][
        "store.account"
    ] == INSTRUMENT_OPERATIONS | {"account.snapshot.typed"}
    assert session.instruments is not None and session.account_client is not None
    assert (
        session.instruments._runtime
        is session.account_client._runtime
        is session.runtime
    )
    session.close()


def test_unknown_query_requires_explicit_cleanup_by_same_id():
    raw = Raw()

    def response(op, args):
        if op.endswith("begin"):
            raise TimeoutError("reply lost")
        assert op.endswith("cancel")
        return {
            "operation_id": args["operation_id"],
            "status": "cancelled",
            "code": None,
            "snapshot": None,
        }

    raw.response = response

    class Extension:
        def start(self, r):
            self.owner = owner(r)

        def invoke(self, operation, args):
            return self.owner.invoke(operation.removeprefix("instrument."), args)

        def close(self):
            self.owner.close()

        @property
        def cleanup_uncertainties(self):
            return self.owner.cleanup_uncertainties

    runtime = ThreadedServiceClientRuntime(
        lambda: raw, owner_extension_factory=Extension
    )
    runtime.start()
    client = GatewayStrategyInstrumentClient(runtime, SETTINGS)
    try:
        with pytest.raises(OwnerOperationError, match="INSTRUMENT_OUTCOME_UNKNOWN"):
            client.lookup("CTP___FUTURE", "IF2612")
        with pytest.raises(ThreadedRuntimeError, match="INSTRUMENT_CLEANUP_REQUIRED"):
            client.lookup("CTP___FUTURE", "IF2612")
        assert client.cleanup_pending() == {"remaining": 0, "resolved": True}
        assert raw.calls[0][1]["operation_id"] == raw.calls[1][1]["operation_id"]
    finally:
        runtime.close()


def test_view_capacity_is_bounded_and_expired_entries_are_reclaimed():
    raw = Raw()
    o = owner(raw)
    o._views = {
        str(i): ({"fresh_until_monotonic": time.monotonic() + 30}, ())
        for i in range(256)
    }
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_CAPACITY_EXCEEDED"):
        o.invoke("lookup", {"instrument": KEY.to_dict()})
    o._views["0"][0]["fresh_until_monotonic"] = 0
    result = o.invoke("lookup", {"instrument": KEY.to_dict()})
    assert isinstance(result, OwnerOperationContinuation)
    o.close()


def test_get_spec_retains_validation_handle_after_wait_timeout():
    o = owner(Raw())
    wire = ready("a" * 32)["snapshot"]
    wire = dict(wire)
    wire["fresh_until_monotonic"] = time.monotonic() + 10
    wire["operation_id"] = "a" * 32
    del wire["remaining_seconds"]

    class Handle:
        def __init__(self, outcomes):
            self.outcomes = iter(outcomes)

        def result(self, *, timeout_seconds):
            value = next(self.outcomes)
            if isinstance(value, Exception):
                raise value
            return value

    class Runtime:
        def __init__(self):
            self.calls = []

        def submit_owner(self, operation, args):
            self.calls.append(operation)
            if operation == "instrument.lookup":
                return Handle([wire])
            return Handle(
                [ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING"), {"valid": True}]
            )

    runtime = Runtime()
    client = GatewayStrategyInstrumentClient(runtime, SETTINGS)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.get_instrument_spec("CTP___FUTURE", "IF2612")
    assert client.get_instrument_spec("CTP___FUTURE", "IF2612").price_tick == Decimal(
        ".2"
    )
    assert runtime.calls == ["instrument.lookup", "instrument.validate"]
    o.close()


def test_real_runtime_account_instrument_permissions_are_explicit(
    tmp_path, monkeypatch
):
    from tests.unit import test_service_runtime as unit

    advertised = unit._ACK_ACCOUNT_READ_OPERATIONS | INSTRUMENT_OPERATIONS
    monkeypatch.setattr(unit, "_ACK_ACCOUNT_READ_OPERATIONS", advertised)
    registry = unit._account_operation_registry(
        tmp_path / "instrument-runtime.sqlite", [], granted_operations=advertised
    )
    default_transport = unit.LocalTransport(registry)
    default = unit._runtime(unit._config(), lambda *_: default_transport)
    try:
        default.start()
        assert default.bindings[0].operations == unit._LEGACY_ACCOUNT_READ_OPERATIONS
        with pytest.raises(
            unit.ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"
        ):
            default.call("store.account", "account.instrument_spec.begin", {})
    finally:
        default.close()
    transport = unit.LocalTransport(registry)
    requested = INSTRUMENT_OPERATIONS | {"account.snapshot"}
    runtime = unit._runtime(
        unit._config(),
        lambda *_: transport,
        requested_operations={"store.account": requested},
    )
    try:
        runtime.start()
        assert runtime.bindings[0].operations == requested
        for operation in INSTRUMENT_OPERATIONS:
            assert (
                runtime.call("store.account", operation, {})["operation"] == operation
            )
    finally:
        runtime.close()
        registry.close()


@pytest.mark.parametrize("count", [1, 2])
def test_real_runtime_rejects_all_partial_instrument_triples(count):
    from itertools import combinations

    from tests.unit import test_service_runtime as unit

    for subset in combinations(sorted(INSTRUMENT_OPERATIONS), count):
        for extra in (frozenset(), frozenset({"account.snapshot"})):
            with pytest.raises(
                unit.ServiceRuntimeError, match="INVALID_ACTION_REQUEST"
            ):
                unit._runtime(
                    unit._config(),
                    lambda *_: pytest.fail("unexpected transport"),
                    requested_operations={"store.account": frozenset(subset) | extra},
                )


@pytest.mark.parametrize(
    "timeout", [True, False, float("nan"), float("inf"), 0, -1, 1e100]
)
def test_invalid_timeout_never_submits_an_owner_call(timeout):
    class Runtime:
        def submit_owner(self, *_args):
            pytest.fail("invalid timeout must be rejected before submission")

    client = GatewayStrategyInstrumentClient(Runtime(), SETTINGS)
    with pytest.raises(ValueError):
        client.lookup("CTP___FUTURE", "IF2612", timeout_seconds=timeout)
    assert client._pending is None


@pytest.mark.parametrize(
    "failure",
    [
        ValueError("unexpected wait failure"),
        ThreadedRuntimeError("OWNER_THREAD_REENTRY"),
    ],
)
def test_unclassified_wait_failure_retains_original_handle(failure):
    wire = dict(ready("a" * 32)["snapshot"])
    wire["fresh_until_monotonic"] = time.monotonic() + 10
    wire["operation_id"] = "a" * 32
    del wire["remaining_seconds"]

    class Handle:
        def __init__(self):
            self.outcomes = iter((failure, wire))

        def result(self, *, timeout_seconds):
            value = next(self.outcomes)
            if isinstance(value, Exception):
                raise value
            return value

    class Runtime:
        def __init__(self):
            self.submissions = 0

        def submit_owner(self, *_args):
            self.submissions += 1
            return Handle()

    runtime = Runtime()
    client = GatewayStrategyInstrumentClient(runtime, SETTINGS)
    with pytest.raises(type(failure)):
        client.lookup("CTP___FUTURE", "IF2612")
    assert client.lookup("CTP___FUTURE", "IF2612").instrument == KEY
    assert runtime.submissions == 1
