"""Synthetic account-owner/threaded-proxy tests without provider I/O."""

import copy
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.account_protocol import AccountKey, AccountResourceKey

from bt_api_py.gateway_strategy_account import (
    GatewayAccountClientSettings,
    GatewayAccountOwner,
    GatewayStrategyAccountClient,
    _decode_view,
    _encode_view,
)
from bt_api_py.service_account import AccountStateView
from bt_api_py.service_registry import ServiceRegistry
from bt_api_py.service_runtime_thread import (
    OwnerOperationError,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)
from tests.unit import test_service_account as unit
from tests.unit.test_service_runtime_thread_owner import Raw
from tests.unit.test_service_account_typed_rpc import _typed_reply


def settings():
    return GatewayAccountClientSettings(
        unit._ACCOUNT, unit._COVERAGE, frozenset({"account.snapshot.typed"})
    )


def test_settings_freeze_copy_and_validate():
    account = AccountKey("synthetic", "unit", "paper", "acct")
    resource = AccountResourceKey("spot", "cash")
    value = GatewayAccountClientSettings(
        account, (resource,), frozenset({"account.snapshot.typed"})
    )
    object.__setattr__(account, "account_id", "changed")
    object.__setattr__(resource, "margin_pool", "changed")
    assert (
        value.expected_account.account_id == "acct"
        and value.required_coverage[0].margin_pool == "cash"
    )
    for coverage, operations, flag in (
        ([], frozenset({"account.snapshot.typed"}), False),
        ((), frozenset({"account.snapshot.typed"}), False),
        ((unit._SPOT, unit._SPOT), frozenset({"account.snapshot.typed"}), False),
        (unit._COVERAGE, frozenset(), False),
        (
            unit._COVERAGE,
            frozenset({"account.snapshot.typed", "account.instrument_metadata"}),
            False,
        ),
        (unit._COVERAGE, {"account.snapshot.typed"}, False),
        (unit._COVERAGE, frozenset({"account.snapshot.typed"}), 1),
    ):
        with pytest.raises(ValueError, match="INVALID_ACCOUNT_SETTINGS"):
            GatewayAccountClientSettings(unit._ACCOUNT, coverage, operations, flag)


def test_owner_uses_real_account_client_and_invalidates_on_binding_change(tmp_path, monkeypatch):
    monkeypatch.setattr(unit, "_OPERATIONS", frozenset({"account.snapshot.typed"}))
    original = ServiceRegistry.register_handler

    def register(self, instance, operation, handler, *args, **kwargs):
        return original(
            self,
            instance,
            "account.snapshot.typed" if operation == "account.snapshot" else operation,
            handler,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(ServiceRegistry, "register_handler", register)
    original_client, runtime, registry, source, transport, clock = unit._make_client(tmp_path)
    source.response = _typed_reply()
    owner = GatewayAccountOwner(
        settings(), account_ref=unit._ACCOUNT_REF, scope_ref="account-scope-v1"
    )
    try:
        owner.start(runtime)
        assert owner.invoke("status", {}) == {"healthy": False}
        raw = owner.invoke("refresh", {})
        decoded = _decode_view(raw, check_fresh=True)
        assert decoded.observation.to_dict() == unit._observation().to_dict()
        assert decoded.cursor == 0 and decoded.snapshot_id == "snapshot-1"
        assert owner.invoke("snapshot", {}) == raw
        assert owner.invoke("status", {}) == {"healthy": True}
        assert owner.invoke("validate", {"view": raw}) == {"valid": True}
        changed = dict(raw, cursor=raw["cursor"] + 1)
        with pytest.raises(OwnerOperationError, match="STALE_ACCOUNT_STATE"):
            owner.invoke("validate", {"view": changed})
        monkeypatch.setattr(owner._client, "_monotonic_clock", lambda: raw["fresh_until_monotonic"])
        with pytest.raises(OwnerOperationError, match="STALE_ACCOUNT_STATE"):
            owner.invoke("snapshot", {})
        with pytest.raises(OwnerOperationError, match="STALE_ACCOUNT_STATE"):
            owner.invoke("validate", {"view": raw})
        monkeypatch.setattr(owner._client, "_monotonic_clock", time.monotonic)
        raw = owner.invoke("refresh", {})
        binding = runtime.account_binding_view(
            "store.account",
            unit._ACCOUNT_REF,
            required_operations=frozenset({"account.snapshot.typed"}),
        )

        class ValidateHandle:
            def result(self, *, timeout_seconds):
                return owner.invoke("validate", {"view": raw})

        proxy_runtime, _ = fake_runtime(
            monkeypatch,
            [Handle([ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING"), raw]), ValidateHandle()],
        )
        proxy = GatewayStrategyAccountClient(proxy_runtime)
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            proxy.snapshot()
        monkeypatch.setattr(
            runtime,
            "account_binding_view",
            lambda *a, **kw: replace(binding, service_epoch="changed"),
        )
        with pytest.raises(OwnerOperationError, match="STALE_ACCOUNT_STATE"):
            proxy.snapshot()
        with pytest.raises(OwnerOperationError, match="STALE_ACCOUNT_STATE"):
            owner.invoke("snapshot", {})
        assert owner.invoke("status", {}) == {"healthy": False}
        with pytest.raises(OwnerOperationError, match="INVALID_OWNER_OPERATION"):
            owner.invoke("instrument_metadata", {})
    finally:
        owner.close()
        owner.close()
        with pytest.raises(OwnerOperationError, match="ACCOUNT_CLIENT_UNAVAILABLE"):
            owner.invoke("snapshot", {})
        unit._close_all(original_client, runtime, registry)


def test_view_codec_checks_types_bounds_mutation_and_deadline(monkeypatch):
    now = time.monotonic()
    original = AccountStateView(unit._observation(), 7, "snap", "generation", now, now + 60)
    raw = _encode_view(original)
    decoded = _decode_view(raw, check_fresh=True)
    assert decoded == original and decoded.observation is not original.observation
    mutated = copy.deepcopy(raw)
    mutated["observation"]["data"]["balances"][0]["total"] = "100.00"
    with pytest.raises(OwnerOperationError, match="INVALID_ACCOUNT_VIEW"):
        _decode_view(mutated, check_fresh=True)
    for field, bad in (
        ("cursor", True),
        ("cursor", -1),
        ("snapshot_id", ""),
        ("session_generation", " x "),
        ("fresh_until_monotonic", float("inf")),
        ("received_at_monotonic", True),
        ("schema_version", True),
    ):
        invalid = dict(raw, **{field: bad})
        with pytest.raises(OwnerOperationError, match="INVALID_ACCOUNT_VIEW"):
            _decode_view(invalid, check_fresh=True)
    with pytest.raises(OwnerOperationError, match="STALE_ACCOUNT_STATE"):
        _decode_view(
            dict(raw, fresh_until_monotonic=now - 1, received_at_monotonic=now - 2),
            check_fresh=True,
        )
    exact_deadline = 2**53 + 3
    exact = _decode_view(
        dict(raw, received_at_monotonic=exact_deadline - 2, fresh_until_monotonic=exact_deadline),
        check_fresh=False,
    )
    assert exact.fresh_until_monotonic == exact_deadline
    raw["observation"]["data"]["balances"][0]["total"] = "123"
    assert decoded.observation.balances[0].total == "100"


class Handle:
    def __init__(self, values):
        self.values = list(values)
        self.waits = 0

    def result(self, *, timeout_seconds):
        self.waits += 1
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def fake_runtime(monkeypatch, handles):
    runtime = ThreadedServiceClientRuntime(lambda: None)
    calls = []

    def submit(name, arguments):
        calls.append((name, arguments))
        return handles[len(calls) - 1]

    monkeypatch.setattr(runtime, "submit_owner", submit)
    monkeypatch.setattr(
        runtime, "status", lambda: SimpleNamespace(state="running", thread_alive=True, healthy=True)
    )
    return runtime, calls


def wire():
    now = time.monotonic()
    return _encode_view(AccountStateView(unit._observation(), 7, "snap", "gen", now, now + 60))


@pytest.mark.parametrize("code", ["OWNER_TIMEOUT_QUEUED", "OWNER_TIMEOUT_RUNNING"])
def test_timeout_retains_same_handle_and_rejects_other_operation(monkeypatch, code):
    handle = Handle([ThreadedRuntimeError(code), wire()])
    runtime, calls = fake_runtime(monkeypatch, [handle, Handle([{"valid": True}])])
    client = GatewayStrategyAccountClient(runtime)
    with pytest.raises(ThreadedRuntimeError, match=code):
        client.refresh(timeout_seconds=0.01)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
        client.snapshot(timeout_seconds=0.01)
    assert client.refresh(timeout_seconds=1).cursor == 7
    assert len(calls) == 2
    assert calls[0] == ("account.refresh", {}) and calls[1][0] == "account.validate"
    assert handle.waits == 2


def test_delayed_delivery_does_not_extend_expiry(monkeypatch):
    raw = wire()
    handle = Handle([ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING"), raw])
    runtime, calls = fake_runtime(monkeypatch, [handle])
    client = GatewayStrategyAccountClient(runtime)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.snapshot(timeout_seconds=0.01)
    monkeypatch.setattr(
        "bt_api_py.gateway_strategy_account.time.monotonic", lambda: raw["fresh_until_monotonic"]
    )
    with pytest.raises(OwnerOperationError, match="STALE_ACCOUNT_STATE"):
        client.snapshot(timeout_seconds=1)
    assert len(calls) == 1


def test_retained_validation_timeout_requires_fresh_validation(monkeypatch):
    raw = wire()
    runtime, calls = fake_runtime(
        monkeypatch,
        [
            Handle([ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING"), raw]),
            Handle([ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING"), {"valid": True}]),
            Handle([OwnerOperationError("STALE_ACCOUNT_STATE")]),
        ],
    )
    client = GatewayStrategyAccountClient(runtime)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.refresh()
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.refresh()
    with pytest.raises(OwnerOperationError, match="STALE_ACCOUNT_STATE"):
        client.refresh()
    assert [name for name, _ in calls] == [
        "account.refresh",
        "account.validate",
        "account.validate",
    ]


@pytest.mark.parametrize("state", ["changed", "lease", "closed"])
def test_retained_result_rejects_changed_binding_lost_lease_or_close(monkeypatch, state):
    raw = wire()
    if state == "changed":
        validation = Handle([OwnerOperationError("STALE_ACCOUNT_STATE")])
    else:
        validation = Handle([{"valid": True}])
    runtime, calls = fake_runtime(
        monkeypatch,
        [
            Handle([ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING"), raw]),
            validation,
        ],
    )
    client = GatewayStrategyAccountClient(runtime)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.snapshot()
    if state != "changed":
        monkeypatch.setattr(
            runtime,
            "status",
            lambda: SimpleNamespace(
                state="closed" if state == "closed" else "running", thread_alive=True, healthy=False
            ),
        )
    with pytest.raises((OwnerOperationError, ThreadedRuntimeError)):
        client.snapshot()
    assert [name for name, _ in calls] == ["account.snapshot", "account.validate"]


def test_retained_status_reads_current_status(monkeypatch):
    runtime, calls = fake_runtime(
        monkeypatch,
        [
            Handle([ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING"), {"healthy": True}]),
            Handle([{"healthy": False}]),
        ],
    )
    client = GatewayStrategyAccountClient(runtime)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.status()
    assert client.status() == {"healthy": False}
    assert [name for name, _ in calls] == ["account.status", "account.status"]


def test_real_shared_queue_full_preserves_completed_refresh_for_validation():
    refresh_started, release_refresh = threading.Event(), threading.Event()
    blocker_started, release_blocker = threading.Event(), threading.Event()
    invocations = []
    raw = wire()

    class Extension:
        cleanup_uncertainties = 0

        def start(self, runtime):
            pass

        def invoke(self, operation, arguments):
            invocations.append(operation)
            if operation == "account.refresh":
                refresh_started.set()
                assert release_refresh.wait(3)
                return raw
            if operation == "block":
                blocker_started.set()
                assert release_blocker.wait(3)
            if operation == "account.validate":
                assert arguments == {"view": raw}
                return {"valid": True}
            return {}

        def close(self):
            pass

    runtime = ThreadedServiceClientRuntime(
        lambda: Raw([]), owner_extension_factory=Extension, queue_size=1
    )
    runtime.start()
    client = GatewayStrategyAccountClient(runtime)
    try:
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            client.refresh(timeout_seconds=0.02)
        assert refresh_started.is_set()
        blocker = runtime.submit_owner("block", {})
        release_refresh.set()
        assert blocker_started.wait(1)
        queued = runtime.submit_owner("queued", {})
        with pytest.raises(ThreadedRuntimeError, match="QUEUE_FULL"):
            client.refresh()
        release_blocker.set()
        blocker.result()
        queued.result()
        assert client.refresh().cursor == 7
        assert invocations.count("account.refresh") == 1
        assert invocations.count("account.validate") == 1
    finally:
        release_refresh.set()
        release_blocker.set()
        runtime.close()


def test_status_resubmit_queue_full_retries_without_losing_pending(monkeypatch):
    runtime, calls = fake_runtime(
        monkeypatch,
        [
            Handle([ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING"), {"healthy": True}]),
            Handle([{"healthy": False}]),
        ],
    )
    submit = runtime.submit_owner
    rejected = []

    def fail_once(name, arguments):
        if name == "account.status" and len(calls) == 1 and not rejected:
            rejected.append(True)
            raise ThreadedRuntimeError("QUEUE_FULL")
        return submit(name, arguments)

    monkeypatch.setattr(runtime, "submit_owner", fail_once)
    client = GatewayStrategyAccountClient(runtime)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.status()
    with pytest.raises(ThreadedRuntimeError, match="QUEUE_FULL"):
        client.status()
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
        client.snapshot()
    assert client.status() == {"healthy": False}
    assert [name for name, _ in calls] == ["account.status", "account.status"]


def test_concurrent_call_is_nonblocking_and_owner_call_is_not_duplicated(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class BlockingHandle:
        def result(self, *, timeout_seconds):
            entered.set()
            assert release.wait(2)
            return wire()

    runtime, calls = fake_runtime(monkeypatch, [BlockingHandle()])
    client = GatewayStrategyAccountClient(runtime)
    results = []
    thread = threading.Thread(target=lambda: results.append(client.refresh(timeout_seconds=1)))
    thread.start()
    assert entered.wait(1)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
        client.refresh(timeout_seconds=0.01)
    release.set()
    thread.join(2)
    assert not thread.is_alive() and results[0].cursor == 7 and len(calls) == 1


def test_process_boundary_and_status_result_validation(monkeypatch):
    runtime, calls = fake_runtime(
        monkeypatch, [Handle([{"healthy": True}]), Handle([{"healthy": 1}])]
    )
    client = GatewayStrategyAccountClient(runtime)
    assert client.status() == {"healthy": True}
    with pytest.raises(OwnerOperationError, match="INVALID_ACCOUNT_STATUS"):
        client.status()
    with monkeypatch.context() as patch:
        patch.setattr("bt_api_py.gateway_strategy_account.os.getpid", lambda: client._pid + 1)
        with pytest.raises(ThreadedRuntimeError, match="WRONG_PROCESS"):
            client.refresh()
