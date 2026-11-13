"""Local observations using generated CURVE credentials and loopback only."""
from __future__ import annotations

import gc
import inspect
import multiprocessing
import os
import threading
import weakref
from dataclasses import replace

import pytest
import zmq
from bt_api_transport_zmq.service_v2 import RequestOutcomeUnknownError

from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from tests.unit.test_service_runtime import (
    PEER,
    LocalTransport,
    ManualClock,
    _config,
    _curve_server_process,
    _registry,
    _runtime,
)


@pytest.fixture
def live_runtime(tmp_path):
    server_keys = zmq.curve_keypair()
    public, secret = zmq.curve_keypair()
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(
        target=_curve_server_process,
        args=(child, str(tmp_path / "lineage.sqlite"), server_keys, public),
    )
    process.start()
    child.close()
    runtime = None
    try:
        assert parent.poll(15)
        endpoint = parent.recv()
        factory = ZmqTransportFactory({"client-keys": ClientCurveKeys(server_keys[0], public, secret)})
        runtime = _runtime(_config(endpoint, timeout_ms=1500), factory)
        yield runtime, factory, endpoint
    finally:
        if runtime is not None:
            runtime.close()
        if process.is_alive():
            parent.send("stop")
            assert parent.poll(10)
            parent.recv()
        process.join(10)
        assert not process.is_alive()
        assert process.exitcode == 0
        parent.close()


def test_registration_survives_later_rpc_but_not_renew_or_close(live_runtime):
    runtime, _, _ = live_runtime
    binding = runtime.start().bindings[0]
    assert runtime._has_current_registration_observation(binding)
    assert not runtime._has_current_registration_observation(replace(binding))
    assert runtime.call("store.account", "account.snapshot", {})["source"] == "synthetic"
    assert runtime._has_current_registration_observation(binding)
    renewed = runtime.renew().bindings[0]
    assert renewed is not binding
    assert not runtime._has_current_registration_observation(binding)
    assert not runtime._has_current_registration_observation(renewed)
    runtime.close()
    assert not runtime._has_current_registration_observation(binding)


def test_owner_binding_client_and_socket_identity(live_runtime):
    runtime, factory, endpoint = live_runtime
    binding = runtime.start().bindings[0]
    key = (binding.service_id, binding.transport_ref)
    adapter = runtime._transports[key]
    client = adapter._transport
    assert runtime._has_current_registration_observation(binding)
    observed = []
    thread = threading.Thread(target=lambda: observed.append(runtime._has_current_registration_observation(binding)))
    thread.start()
    thread.join(5)
    assert observed == [False]
    if hasattr(os, "fork"):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                os.write(write_fd, b"1" if runtime._has_current_registration_observation(binding) else b"0")
            finally:
                os._exit(0)
        os.close(write_fd)
        assert os.read(read_fd, 1) == b"0"
        os.close(read_fd)
        os.waitpid(pid, 0)
    assert runtime._has_current_registration_observation(binding)
    replacement = factory(_config(endpoint).communication["profiles"][binding.transport_ref], PEER)
    try:
        runtime._transports[key] = replacement
        assert not runtime._has_current_registration_observation(binding)
    finally:
        runtime._transports[key] = adapter
        replacement.close()
    assert not runtime._has_current_registration_observation(binding)
    client._retire_socket()
    assert not runtime._has_current_registration_observation(binding)


def test_socket_retirement_invalidates_observation(live_runtime):
    runtime, _, _ = live_runtime
    binding = runtime.start().bindings[0]
    client = runtime._transports[(binding.service_id, binding.transport_ref)]._transport
    assert runtime._has_current_registration_observation(binding)
    client._retire_socket()
    assert not runtime._has_current_registration_observation(binding)


def test_wrong_thread_rpc_preserves_owner_lineage(live_runtime):
    runtime, _, _ = live_runtime
    binding = runtime.start().bindings[0]
    assert runtime._has_current_registration_observation(binding)
    prior_lineage = dict(runtime._registration_lineage)
    prior_failures = dict(runtime._registration_failure_generation)
    errors = []

    def invoke():
        try:
            runtime._rpc(binding.service_id, binding.transport_ref, binding.scope,
                         "service.discover", {})
        except ServiceRuntimeError as error:
            errors.append(error.code)

    thread = threading.Thread(target=invoke)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert errors == ["WRONG_TRANSPORT_THREAD"]
    assert runtime._registration_lineage == prior_lineage
    assert runtime._registration_failure_generation == prior_failures
    assert runtime._has_current_registration_observation(binding)


def test_lease_expiry_is_sticky_and_fake_transport_never_proves(tmp_path):
    clock = ManualClock(100)
    registry = _registry(tmp_path / "fake.sqlite", [])
    transport = LocalTransport(registry)
    runtime = _runtime(_config(), lambda *_: transport, monotonic_clock=clock)
    try:
        binding = runtime.start().bindings[0]
        assert not runtime._has_current_registration_observation(binding)
    finally:
        runtime.close()
        registry.close()


def test_expiry_with_real_transport(live_runtime):
    runtime, _, _ = live_runtime
    clock = ManualClock(100)
    runtime._monotonic_clock = clock
    binding = runtime.start().bindings[0]
    assert runtime._has_current_registration_observation(binding)
    clock.value = binding.lease_deadline
    assert not runtime._has_current_registration_observation(binding)
    clock.value = 100
    assert not runtime._has_current_registration_observation(binding)


def test_multiple_registrations_and_reentrant_install(live_runtime, monkeypatch):
    runtime, _, _ = live_runtime
    original = runtime.start().bindings[0]
    binding = dict(runtime._config["clients"]["gateway"]["settings"]["bindings"][0])
    binding["mount"] = "application.account"
    capabilities = [{"instance_id": binding["instance_id"], "port": "service.account_read",
                     "operations": ["account.snapshot"]}]
    real_check = runtime._registration_transport_is_current
    nested = []

    def reenter(proof):
        if not nested:
            nested.append(True)
            other = dict(binding, mount="upstream.account")
            runtime._register_port(other, original.scope, "service.account_read", capabilities,
                                   timeout=1.5, required=True, heartbeat_interval_seconds=None)
        return real_check(proof)

    monkeypatch.setattr(runtime, "_registration_transport_is_current", reenter)
    runtime._register_port(binding, original.scope, "service.account_read", capabilities,
                           timeout=1.5, required=True, heartbeat_interval_seconds=None)
    monkeypatch.setattr(runtime, "_registration_transport_is_current", real_check)
    assert len(runtime.bindings) == 3
    assert all(runtime._has_current_registration_observation(item) for item in runtime.bindings)
    assert len({runtime._registration_lineage[id(item)][8] for item in runtime.bindings}) == 3


def _synthetic_server(connection, server_keys, client_public, mode):
    """Own only this test's generated socket; never contacts external services."""
    import time

    from bt_api_base.service_protocol import WIRE_PROTOCOL_VERSION, wire_contract_digest
    from bt_api_transport_zmq.server import CurveServerCredentials
    from bt_api_transport_zmq.service_v2 import ZmqServiceServer

    from tests.unit.test_service_runtime import CLIENT

    registrations = []
    unregistrations = []
    attempts = 0

    def handle(_principal, request):
        nonlocal attempts
        op = request.header.operation
        payload = request.payload
        if op == "service.discover":
            result = {"protocol_version": WIRE_PROTOCOL_VERSION, "wire_contract": wire_contract_digest(),
                      "service_id": "account-service", "ready": True, "capabilities": [
                          {"instance_id": "gateway-a", "port": "service.account_read", "operations": ["account.snapshot"]},
                          {"instance_id": "gateway-a", "port": "service.risk_query", "operations": ["risk.snapshot"]},
                      ]}
        elif op == "service.register":
            attempts += 1
            if mode == "adopt" and attempts == 1:
                return {"ok": False, "code": "DUPLICATE_ACTIVE_RUN", "result": {"strategy_write_run_id": "released-run"}}
            registration_id = f"reg-{attempts}"
            registrations.append((payload["port"], payload["registration_key"]))
            result = {"registration_id": registration_id, "service_epoch": "epoch-1", "policy_version": "v1",
                      "requirements_version": "v1", "operations": [] if mode == "invalid" else payload["operations"],
                      "expires_at": 10000, "lease_remaining_seconds": 30}
        elif op == "service.unregister":
            unregistrations.append(payload["registration_id"])
            result = {"unregistered": True, "registration_id": payload["registration_id"]}
        elif op == "account.snapshot" and mode == "timeout":
            time.sleep(0.5)
            result = {}
        else:
            result = {}
        return {"ok": True, "code": "OK", "result": result}

    server = ZmqServiceServer("tcp://127.0.0.1:*", PEER, {client_public: CLIENT.principal_ref}, handle,
                              credentials=CurveServerCredentials(*server_keys))
    try:
        connection.send(server.endpoint)
        while not connection.poll():
            server.serve_once(30)
        assert connection.recv() == "stop"
        connection.send((registrations, unregistrations))
    finally:
        server.close()
        connection.close()


@pytest.fixture
def synthetic_runtime(request):
    mode = getattr(request, "param", "normal")
    server_keys = zmq.curve_keypair()
    public, secret = zmq.curve_keypair()
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_synthetic_server, args=(child, server_keys, public, mode))
    process.start()
    child.close()
    runtime = None
    try:
        assert parent.poll(15)
        endpoint = parent.recv()
        factory = ZmqTransportFactory({"client-keys": ClientCurveKeys(server_keys[0], public, secret)})
        runtime = _runtime(_config(endpoint, timeout_ms=1200), factory)
        yield runtime
    finally:
        if runtime is not None:
            runtime.close()
        parent.send("stop")
        assert parent.poll(10)
        registrations, unregistrations = parent.recv()
        if mode == "invalid":
            assert registrations and unregistrations == ["reg-1"]
        process.join(10)
        assert not process.is_alive() and process.exitcode == 0
        parent.close()


def test_distinct_ports_on_same_transport(synthetic_runtime):
    runtime = synthetic_runtime
    raw = runtime._config["clients"]["gateway"]["settings"]["bindings"]
    raw.append(dict(raw[0], mount="risk.read_model"))
    bindings = runtime.start().bindings
    assert len(bindings) == 2
    assert {next(iter(binding.operations)) for binding in bindings} == {"account.snapshot", "risk.snapshot"}
    assert all(runtime._has_current_registration_observation(binding) for binding in bindings)
    assert len({runtime._registration_lineage[id(binding)][8] for binding in bindings}) == 2


def test_partial_release_revokes_slice_before_io_and_retains_cleanup_handle(synthetic_runtime, monkeypatch):
    runtime = synthetic_runtime
    raw = runtime._config["clients"]["gateway"]["settings"]["bindings"]
    raw.append(dict(raw[0], mount="risk.read_model"))
    first, second = runtime.start().bindings
    key = (first.service_id, first.transport_ref)
    adapter_ref = weakref.ref(runtime._transports[key])
    client_ref = weakref.ref(runtime._transports[key]._transport)

    def unacknowledged(binding):
        assert binding is second
        assert set(runtime._registration_lineage) == {id(first)}
        runtime._unregister_uncertainties[binding.registration_id] = None
        return False

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_unregister_binding", unacknowledged)
        runtime._release_bindings(1)
    assert runtime._has_current_registration_observation(first)
    assert runtime.bindings[1] is not second and not runtime.bindings[1].active
    assert runtime.unregister_uncertainties == 1
    assert adapter_ref() is runtime._transports[key] and client_ref() is not None
    runtime._release()
    assert not runtime._registration_lineage and not runtime._transports
    gc.collect()
    assert adapter_ref() is None and client_ref() is None


def test_failed_release_retains_retry_handles_without_lineage(synthetic_runtime, monkeypatch):
    runtime = synthetic_runtime
    binding = runtime.start().bindings[0]
    key = (binding.service_id, binding.transport_ref)
    adapter = runtime._transports[key]

    def unacknowledged(item):
        assert not runtime._registration_lineage
        runtime._unregister_uncertainties[item.registration_id] = None
        return False

    def close_failed():
        raise TimeoutError("synthetic close failure")

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_unregister_binding", unacknowledged)
        patch.setattr(adapter, "close", close_failed)
        runtime._release(capture_base_exceptions=True)
    assert not runtime._registration_lineage
    assert runtime._transports[key] is adapter
    assert len(runtime.bindings) == 1 and not runtime.bindings[0].active
    assert runtime.unregister_uncertainties == 1
    runtime.close()
    assert not runtime._transports and not runtime.bindings
    assert runtime.unregister_uncertainties == 0


def test_optional_expiry_revokes_lineage_before_ack(synthetic_runtime, monkeypatch):
    runtime = synthetic_runtime
    raw = runtime._config["clients"]["gateway"]["settings"]["bindings"]
    raw.append(dict(raw[0], mount="risk.read_model", required=False))
    first, second = runtime.start().bindings

    def acknowledged(binding):
        assert binding is second
        assert id(second) not in runtime._registration_lineage
        return True

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_unregister_binding", acknowledged)
        runtime._expire_binding(second)
    assert runtime.bindings == (first,)
    assert runtime._has_current_registration_observation(first)


def test_degrade_clears_stale_argument_and_matching_current(synthetic_runtime, monkeypatch):
    runtime = synthetic_runtime
    stale = runtime.start().bindings[0]
    proof = runtime._registration_lineage[id(stale)]
    current = replace(stale)
    runtime._bindings[0] = current
    runtime._registration_lineage[id(current)] = (current, *proof[1:])

    def acknowledged(binding):
        assert binding is stale
        assert not runtime._registration_lineage
        return True

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_unregister_binding", acknowledged)
        runtime._degrade_binding(stale, "TEST")
    assert not runtime.bindings and not runtime._registration_lineage
    runtime._registration_lineage[id(stale)] = proof
    runtime._degrade_binding(stale, "TEST")
    assert not runtime._registration_lineage


def test_failed_start_releases_lineage_and_transport(synthetic_runtime):
    runtime = synthetic_runtime
    raw = runtime._config["clients"]["gateway"]["settings"]["bindings"]
    raw.append(dict(raw[0], mount="risk.read_model", instance_id="missing"))
    with pytest.raises(ServiceRuntimeError, match="^CAPABILITY_UNAVAILABLE$"):
        runtime.start()
    assert runtime.state == "failed"
    assert not runtime._registration_lineage and not runtime._transports and not runtime.bindings


def test_required_lease_failure_clears_all_lineages(synthetic_runtime):
    runtime = synthetic_runtime
    clock = ManualClock(100)
    runtime._monotonic_clock = clock
    raw = runtime._config["clients"]["gateway"]["settings"]["bindings"]
    raw.append(dict(raw[0], mount="risk.read_model"))
    first, second = runtime.start().bindings
    assert len(runtime._registration_lineage) == 2
    clock.value = min(first.lease_deadline, second.lease_deadline)
    with pytest.raises(ServiceRuntimeError, match="^REQUIRED_LEASE_EXPIRED$"):
        runtime._require_healthy()
    assert runtime.state == "failed" and not runtime._registration_lineage
    clock.value = 100


def test_invalid_renewal_result_clears_old_binding_lineage(synthetic_runtime):
    runtime = synthetic_runtime
    binding = runtime.start().bindings[0]
    assert runtime._has_current_registration_observation(binding)
    with pytest.raises(ServiceRuntimeError, match="^RENEWAL_BINDING_MISMATCH$"):
        runtime.renew()
    assert runtime.state == "failed" and not runtime._registration_lineage
    assert runtime.bindings[0] is not binding


@pytest.mark.parametrize("synthetic_runtime", ["adopt"], indirect=True)
def test_adoption_binds_successful_attempt(synthetic_runtime):
    runtime = synthetic_runtime
    binding = runtime.start().bindings[0]
    assert binding.scope.run_id == "released-run"
    assert runtime._has_current_registration_observation(binding)


@pytest.mark.parametrize("synthetic_runtime", ["invalid"], indirect=True)
def test_invalid_registration_result_never_installs_proof(synthetic_runtime):
    runtime = synthetic_runtime
    with pytest.raises(ServiceRuntimeError, match="^INVALID_SERVICE_REPLY$"):
        runtime.start()
    assert not runtime.bindings
    assert not runtime._registration_lineage


@pytest.mark.parametrize("synthetic_runtime", ["timeout"], indirect=True)
def test_timed_out_rpc_retires_observation(synthetic_runtime):
    runtime = synthetic_runtime
    binding = runtime.start().bindings[0]
    assert runtime._has_current_registration_observation(binding)
    with pytest.raises(RequestOutcomeUnknownError):
        runtime._rpc(binding.service_id, binding.transport_ref, binding.scope,
                     "account.snapshot", {}, control=False, timeout=0.1)
    assert not runtime._has_current_registration_observation(binding)


def test_rpc_signature_and_legacy_argument_forwarding(monkeypatch):
    signature = inspect.signature(ServiceClientRuntime._rpc)
    assert str(signature) == (
        "(self, service_id, transport_ref, scope, operation, payload, *, control=True, "
        "timeout=None, include_started_at=False, started_at=None, timing=None, "
        "include_receive_receipt=False)"
    )
    runtime = _runtime(_config(), lambda *_: None)
    calls = []
    marker = object()
    monkeypatch.setattr(runtime, "_rpc_impl", lambda *args, **kwargs: calls.append((args, kwargs)) or marker)
    scope, payload, started, timing = object(), {}, [], {}
    assert runtime._rpc("service", "transport", scope, "operation", payload) is marker
    assert calls[-1] == (("service", "transport", scope, "operation", payload), {
        "control": True, "timeout": None, "include_started_at": False,
        "started_at": None, "timing": None, "include_receive_receipt": False,
    })
    assert runtime._rpc(
        service_id="service", transport_ref="transport", scope=scope,
        operation="operation", payload=payload, control=False, timeout=2,
        include_started_at=True, started_at=started, timing=timing,
        include_receive_receipt=False,
    ) is marker
    assert calls[-1] == (("service", "transport", scope, "operation", payload), {
        "control": False, "timeout": 2, "include_started_at": True,
        "started_at": started, "timing": timing, "include_receive_receipt": False,
    })
    assert calls[-1][1]["started_at"] is started
    assert calls[-1][1]["timing"] is timing
    with pytest.raises(TypeError):
        runtime._rpc("service", "transport", scope, "operation", payload, False)
