"""CURVE/Registry event registration and cursor recovery, using synthetic facts."""

import multiprocessing
import os
import queue
from pathlib import Path

import pytest
import zmq
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    EVENT_OPERATIONS,
    STORAGE_OPERATIONS,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
    execution_events_requested_operations,
    recovery_requested_operations,
)
from bt_api_py.gateway_execution_stream_client import GatewayExecutionStreamClient
from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    DOMAIN,
    SERVICE,
    _client_config,
    _service_config,
)
from tests.integration.test_iter63_gateway_execution_stream_processes import _seed
from tests.integration.test_service_account_instrument_metadata import _free_endpoint


def _server(root, endpoint, server_keys, client_public, messages, controls):
    root = Path(root)
    host = owner = stream = None
    try:
        (root / "guard").mkdir()
        owned = []

        def owner_factory():
            guard = LocalAccountSessionGuard(root / "guard", ACCOUNT, "owner")
            journal = GatewayDirectCommandJournal.create(
                root / "journal.sqlite", ACCOUNT, generation=guard.acquire()
            )
            ledger = GatewayDirectExecutionLedger(journal, guard)
            _seed(journal, ledger)
            dispatcher = GatewayDirectDispatcher(
                journal, "TEST", guard, lambda *_: None, lambda _: (), lambda _: True
            )
            owned.append((journal, ledger))
            return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

        owner = GatewayDirectOwner(
            owner_factory, queue_size=4, max_queue_wait_seconds=10, busy_timeout_seconds=10
        )
        assert owner.start(10)
        journal, ledger = owned[0]
        stream = GatewayExecutionStreamService(
            ledger,
            journal,
            {CLIENT_A.principal_ref: CLIENT_A.actor_ref},
            execution_domain_ref=DOMAIN,
        )
        binding = GatewayDirectServiceBinding(
            journal,
            {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({"X"}))},
            owner=owner,
            execution_stream=stream,
            execution_domain_ref=DOMAIN,
        )
        scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref)
        grant = PrincipalGrant(
            CLIENT_A,
            (
                ResourceGrant(
                    "gateway-a",
                    "service.direct_write",
                    "scope-a",
                    scope,
                    STORAGE_OPERATIONS | EVENT_OPERATIONS,
                ),
            ),
        )
        authority = TrustedServiceAuthority(
            identity=SERVICE,
            scope=WireScope(DOMAIN),
            scope_ref="scope-a",
            scope_version="scope-v1",
            security_ref="security-a",
            security_version="security-v1",
            registration_policy_ref="direct-policy-v1",
            registration_policy_version="direct-policy-v1",
            service_requirements_ref="direct-requirements-v1",
            service_requirements_version="direct-requirements-v1",
            curve_public_key=server_keys[0],
            curve_secret_key=server_keys[1],
            principals={client_public: CLIENT_A.principal_ref},
            grants={CLIENT_A.principal_ref: (grant,)},
        )
        host = ServiceHost(
            _service_config(endpoint),
            authority,
            root / "registry.sqlite",
            lambda *_: ComponentResources.borrowed({"binding": binding}, ready=lambda: True),
        )
        host.start()
        advertised = frozenset(
            op
            for component in host._components
            for cap in component.capabilities
            for op in cap.operations
        )
        messages.put(("ready", os.getpid(), journal.journal_id, advertised))
        while True:
            try:
                command = controls.get_nowait()
            except queue.Empty:
                command = None
            if command == "stop":
                break
            if isinstance(command, tuple) and command[0] == "stop_run":
                result = host._registry.stop_strategy_new(
                    WireScope(
                        DOMAIN,
                        account_ref=ACCOUNT,
                        strategy_ref=CLIENT_A.actor_ref,
                        run_id=command[1],
                    ),
                    "synthetic-stop-command",
                )
                messages.put(("stopped", result["status"]))
            host.serve_once(10)
    except BaseException as error:
        import traceback

        messages.put(("error", type(error).__name__, str(error), traceback.format_exc()))
    finally:
        try:
            if host is not None and host.state == "started":
                host.close()
            if stream is not None:
                assert stream.close()
            if owner is not None:
                assert owner.close(10)
            messages.put(("closed", os.getpid()))
        except BaseException as error:
            messages.put(("cleanup_error", type(error).__name__, str(error)))


@pytest.fixture
def recovery_service(tmp_path):
    context = multiprocessing.get_context("spawn")
    messages, controls = context.Queue(), context.Queue()
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    endpoint = _free_endpoint()
    process = context.Process(
        target=_server,
        args=(str(tmp_path), endpoint, server_keys, client_keys[0], messages, controls),
    )
    process.start()
    try:
        ready = messages.get(timeout=20)
        assert ready[0] == "ready", ready
        assert ready[1] == process.pid != os.getpid()
        assert ready[3] >= STORAGE_OPERATIONS | EVENT_OPERATIONS
        yield endpoint, server_keys[0], client_keys, ready[2], messages, controls
    finally:
        controls.put("stop")
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail("synthetic Gateway required termination")
        assert process.exitcode == 0
        assert messages.get(timeout=3) == ("closed", process.pid)
        controls.close()
        messages.close()


def _runtime(service, run, requested):
    endpoint, server_public, client_keys, journal_id, _, _ = service

    def factory():
        return ServiceClientRuntime(
            _client_config(endpoint, CLIENT_A),
            identity=CLIENT_A,
            scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
            peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory(
                {"security-a": ClientCurveKeys(server_public, *client_keys)}
            ),
            run_id=run,
            requested_operations=requested,
        )

    runtime = ThreadedServiceClientRuntime(factory)
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id=run)
    route = GatewayClientRoute(
        "broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", scope, CLIENT_A
    )
    binding = GatewayCommandBinding(journal_id, ACCOUNT, CLIENT_A.actor_ref, CLIENT_A.principal_ref)
    return runtime, GatewayExecutionStreamClient(runtime, binding=binding, route=route)


def test_default_registration_does_not_include_advertised_events(recovery_service):
    runtime, _ = _runtime(recovery_service, "default-run", None)
    try:
        state = runtime.start(timeout_seconds=10)
        assert len(state.bindings) == 1
        assert state.bindings[0].operations == STORAGE_OPERATIONS
        assert state.bindings[0].operations.isdisjoint(EVENT_OPERATIONS)
        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            runtime.call("broker.direct_write", "direct.events.progress", {}, timeout_seconds=5)
    finally:
        runtime.close(timeout_seconds=10)


def test_events_only_recovery_for_stopped_run_can_read_and_ack(recovery_service):
    messages, controls = recovery_service[4:]
    original, _ = _runtime(recovery_service, "stopped-run", recovery_requested_operations())
    recovery = None
    try:
        original.start(timeout_seconds=10)
        controls.put(("stop_run", "stopped-run"))
        assert messages.get(timeout=5) == ("stopped", "applied")
        original.close(timeout_seconds=10)
        recovery, client = _runtime(
            recovery_service, "stopped-run", execution_events_requested_operations()
        )
        snapshot = recovery.start(timeout_seconds=10)
        assert snapshot.bindings[0].operations == EVENT_OPERATIONS
        page_result = client.read_after()
        assert page_result.outcome == "OBSERVED"
        assert [event.sequence for event in page_result.page.events] == [1, 3]
        acknowledged = client.ack(3, page_result.page.epoch)
        assert (
            acknowledged.outcome == "OBSERVED" and acknowledged.progress.acknowledged_sequence == 3
        )
        assert client.progress().progress.acknowledged_sequence == 3
        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            recovery.call("broker.direct_write", "direct.release", {}, timeout_seconds=5)
    finally:
        if recovery is not None:
            recovery.close(timeout_seconds=10)
        original.close(timeout_seconds=10)


def test_network_ack_reply_budget_denial_does_not_advance_ack(recovery_service):
    runtime, client = _runtime(
        recovery_service, "budget-run", execution_events_requested_operations()
    )
    try:
        runtime.start(timeout_seconds=10)
        page = client.read_after().page
        assert [event.sequence for event in page.events] == [1, 3]
        with pytest.raises(ServiceRuntimeError, match="REPLY_TOO_LARGE"):
            runtime.call(
                "broker.direct_write",
                "direct.events.ack",
                {"sequence": 3, "expected_epoch": page.epoch},
                max_reply_message_bytes=512,
                timeout_seconds=5,
            )
        progress = client.progress()
        assert progress.outcome == "OBSERVED"
        assert (progress.progress.offered_sequence, progress.progress.acknowledged_sequence) == (
            3,
            0,
        )
        assert client.ack(3, page.epoch).progress.acknowledged_sequence == 3
    finally:
        runtime.close(timeout_seconds=10)
