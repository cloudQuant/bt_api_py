"""R18 public YAML to strategy session with CURVE and real BT pending host.

The Gateway runs in a spawned local process with a synthetic provider; none of
these observations prove venue execution or Backtrader Broker/Store wiring.
"""

import multiprocessing
import os
import queue
import time
from pathlib import Path

import pytest
import yaml
import zmq
from backtrader.live_state import StrategyStateDir
from backtrader.live_state.gateway_pending import GatewayPendingHost
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from iter63_prepared_support import compose_claimed_dispatcher

from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_direct_dispatch import compose_gateway_direct_cancel_dispatcher
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.gateway_execution_stream_service import (
    STREAM_OPERATIONS,
    GatewayExecutionStreamService,
)
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_config_io import load_client_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ZmqTransportFactory
from bt_api_py.write_admission import WriteAdmission
from tests.integration.test_iter63_gateway_cancel_rpc import (
    MOUNT,
    OPS,
    ROUTE,
    SyntheticProvider,
    eventually,
    order,
    seed_position,
)
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    CLIENT_B,
    DOMAIN,
    SERVICE,
    _client_config,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint

ALL_OPS = OPS | STREAM_OPERATIONS


def _gateway_process(root, endpoint, server_keys, public_keys, messages, controls):
    root = Path(root)
    host = owner = stream = None
    try:
        (root / "gateway-guard").mkdir()
        owned = []

        def owner_factory():
            guard = LocalAccountSessionGuard(root / "gateway-guard", ACCOUNT, "synthetic-owner")
            try:
                journal = GatewayDirectCommandJournal.create(
                    root / "gateway.sqlite", ACCOUNT, generation=guard.acquire()
                )
                ledger = GatewayDirectExecutionLedger(journal, guard)
                seed_position(journal, ledger)
                provider = SyntheticProvider(root / "gateway.sqlite")
                coordinator = DirectWriteCoordinator(
                    WriteAdmission({ROUTE: True}), lambda _: provider
                )
                dispatcher = compose_claimed_dispatcher(
                    coordinator,
                    ROUTE,
                    journal,
                    guard,
                    lambda _: True,
                    expected_local_provider=provider,
                )
                cancel_dispatcher = compose_gateway_direct_cancel_dispatcher(
                    coordinator,
                    ROUTE,
                    journal,
                    guard,
                    dispatcher,
                    lambda _: True,
                    expected_local_provider=provider,
                )
                owned.append((journal, ledger, provider))
                return GatewayDirectOwnerResources(
                    dispatcher, journal, guard.release, cancel_dispatcher=cancel_dispatcher
                )
            except BaseException:
                guard.release()
                raise

        owner = GatewayDirectOwner(
            owner_factory, queue_size=8, max_queue_wait_seconds=15, busy_timeout_seconds=15
        )
        if not owner.start(10):
            owner.provide_startup_cleanup(lambda: None)
            raise RuntimeError("synthetic owner failed")
        journal, ledger, provider = owned[0]
        grants = {
            CLIENT_A.principal_ref: CLIENT_A.actor_ref,
            CLIENT_B.principal_ref: CLIENT_B.actor_ref,
        }
        stream = GatewayExecutionStreamService(ledger, journal, grants, execution_domain_ref=DOMAIN)
        binding = GatewayDirectServiceBinding(
            journal,
            {
                CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({"rb"})),
                CLIENT_B.principal_ref: GatewayDirectGrant(CLIENT_B.actor_ref, frozenset({"sa"})),
            },
            owner=owner,
            cancel_enabled=True,
            execution_stream=stream,
            execution_domain_ref=DOMAIN,
        )

        def grant(identity):
            return PrincipalGrant(
                identity,
                (
                    ResourceGrant(
                        "gateway-a",
                        "service.direct_write",
                        "scope-a",
                        WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref),
                        ALL_OPS,
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
            principals={
                public_keys[0]: CLIENT_A.principal_ref,
                public_keys[1]: CLIENT_B.principal_ref,
            },
            grants={
                CLIENT_A.principal_ref: (grant(CLIENT_A),),
                CLIENT_B.principal_ref: (grant(CLIENT_B),),
            },
        )
        raw = _service_config(endpoint).to_public_dict()
        raw["components"]["gateway"]["settings"]["options"].update(
            dispatch_enabled=True, cancel_enabled=True
        )
        host = ServiceHost(
            parse_service_config(raw),
            authority,
            root / "registry.sqlite",
            lambda *_: ComponentResources.borrowed({"binding": binding}, ready=lambda: True),
        )
        host.start()
        host._registry.lease_seconds = 2.0
        messages.put(("ready", os.getpid(), journal.journal_id))
        while True:
            try:
                command = controls.get_nowait()
            except queue.Empty:
                command = None
            if command == "stop":
                break
            if command == "count":
                messages.put(("count", len(provider.sent)))
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
            import traceback

            messages.put(
                ("cleanup_error", type(error).__name__, str(error), traceback.format_exc())
            )


def _client_yaml(tmp_path, endpoint, identity):
    raw = _client_config(endpoint, identity).to_public_dict()
    raw["clients"]["gateway"]["settings"]["options"]["heartbeat_interval_seconds"] = 1
    path = tmp_path / (identity.actor_ref + "-public-client.yaml")
    path.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")
    return load_client_config(path)


def _bt_host(tmp_path, name, binding):
    directory = tmp_path / name
    directory.mkdir()
    state = StrategyStateDir.initialize(directory)
    GatewayPendingHost.upgrade_for_gateway(state.strategy_dir, state.strategy_uuid, binding)
    host = GatewayPendingHost.upgrade_for_cancellation(
        state.strategy_dir, state.strategy_uuid, binding
    )
    return state, host


def _session(config, identity, run_id, binding, host, keys, operations=ALL_OPS):
    route = GatewayClientRoute(
        MOUNT,
        SERVICE.actor_ref,
        "gateway-a",
        "scope-a",
        WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref, run_id=run_id),
        identity,
    )
    return GatewayStrategyClientSession(
        config,
        binding=binding,
        route=route,
        peer=SERVICE,
        security_profiles={"security-a": keys},
        host=host,
        cancel_host=host,
        requested_operations=operations,
    )


def test_public_yaml_bt_host_cancel_recovery_stream_and_lease(tmp_path, monkeypatch):
    context = multiprocessing.get_context("spawn")
    messages, controls = context.Queue(), context.Queue()
    server_keys, a_keys, b_keys = zmq.curve_keypair(), zmq.curve_keypair(), zmq.curve_keypair()
    endpoint = _free_endpoint()
    process = context.Process(
        target=_gateway_process,
        args=(str(tmp_path), endpoint, server_keys, (a_keys[0], b_keys[0]), messages, controls),
    )
    sessions = []
    process.start()
    try:
        ready = messages.get(timeout=20)
        assert ready[0] == "ready", ready
        assert ready[1] == process.pid != os.getpid()
        binding_a = GatewayCommandBinding(
            ready[2], ACCOUNT, CLIENT_A.actor_ref, CLIENT_A.principal_ref
        )
        binding_b = GatewayCommandBinding(
            ready[2], ACCOUNT, CLIENT_B.actor_ref, CLIENT_B.principal_ref
        )
        state_a, host_a = _bt_host(tmp_path, "strategy-a", binding_a)
        _, host_b = _bt_host(tmp_path, "strategy-b", binding_b)
        config_a = _client_yaml(tmp_path, endpoint, CLIENT_A)
        config_b = _client_yaml(tmp_path, endpoint, CLIENT_B)

        # Drop only the first real response after its request has reached the server.
        dropped = []
        renewals = []
        transport_starts = []
        factory_call = ZmqTransportFactory.__call__

        class DropResponse:
            def __init__(self, wrapped, is_a):
                self.wrapped, self.is_a = wrapped, is_a

            def request(self, request, *, timeout):
                response = self.wrapped.request(request, timeout=timeout)
                if self.is_a and request.header.operation == "service.renew":
                    renewals.append(True)
                if (
                    request.header.operation == "direct.cancel"
                    and not dropped
                    and request.payload["arguments"]["operation_id"] == "cancel-two"
                ):
                    dropped.append(True)
                    raise TimeoutError("synthetic dropped response")
                return response

            def close(self):
                self.wrapped.close()

        def make_transport(self, *args):
            transport_starts.append(True)
            return DropResponse(
                factory_call(self, *args), self._profiles["security-a"].public_key == a_keys[0]
            )

        monkeypatch.setattr(ZmqTransportFactory, "__call__", make_transport)

        session = _session(
            config_a, CLIENT_A, "run-a", binding_a, host_a, ClientCurveKeys(server_keys[0], *a_keys)
        )
        sessions.append(session)
        assert session.status().state == "new" and transport_starts == []
        started = session.start(timeout_seconds=10)
        assert started.healthy and started.bindings[0].operations == ALL_OPS
        assert transport_starts
        direct, events = session.command_client, session.execution_stream_client
        assert direct.reserve(order("strategy-order")).outcome == "OBSERVED"
        assert direct.dispatch("strategy-order").outcome == "QUEUED"
        eventually(lambda: direct.status("strategy-order").record.state == "SENT")
        first = direct.cancel("strategy-order", operation_id="cancel-one")
        assert first.outcome == "QUEUED"
        eventually(lambda: direct.cancel_status("cancel-one").record.state == "SENT")
        assert direct.cancel("strategy-order", operation_id="cancel-one").record.state == "SENT"
        assert direct.cancel("strategy-order", operation_id="cancel-two").outcome == "UNKNOWN"
        assert dropped == [True]
        eventually(lambda: direct.cancel_status("cancel-two").record.state == "SENT")
        assert direct.cancel("strategy-order", operation_id="cancel-two").record.state == "SENT"
        controls.put("count")
        assert messages.get(timeout=5) == ("count", 2)

        assert events.progress().progress.acknowledged_sequence == 0
        page = events.read_after().page
        # This projection is test-local memory, not a durable BT Order/Position projection.
        projected = [(event.sequence, event.observation.kind) for event in page.events]
        assert len(projected) == 1 and projected[0][1] == "trade"
        assert events.progress().progress.acknowledged_sequence == 0
        assert events.ack(projected[-1][0], page.epoch).outcome == "OBSERVED"

        other = _session(
            config_b, CLIENT_B, "run-b", binding_b, host_b, ClientCurveKeys(server_keys[0], *b_keys)
        )
        sessions.append(other)
        other.start(timeout_seconds=10)
        assert other.runtime.call(
            MOUNT, "direct.command_status", {"request_key": "strategy-order"}, timeout_seconds=5
        ) == {"status": "REQUEST_REJECTED", "code": "NOT_FOUND"}
        assert other.execution_stream_client.read_after().page.events == ()

        initial_registration = session.status().bindings[0].registration_id
        renewal_count = len(renewals)
        idle_started = time.monotonic()
        time.sleep(3.2)  # No client polling; original server lease is only two seconds.
        assert time.monotonic() - idle_started > 2.0
        current = session.status()
        assert current.healthy and current.bindings[0].registration_id == initial_registration
        assert len(renewals) > renewal_count
        assert direct.status("strategy-order").record.state == "SENT"
        assert session.close(timeout_seconds=10).state == "closed"
        reopened = GatewayPendingHost(state_a.strategy_dir, state_a.strategy_uuid, binding_a)
        plans = reopened.list_cancel_plans()
        assert [plan.operation_id for plan in plans] == ["cancel-one", "cancel-two"]
        assert all(reopened.load_cancel_record(plan.operation_id).state == "SENT" for plan in plans)
        recovery_ops = (
            frozenset({"direct.command_status", "direct.cancel_status"}) | STREAM_OPERATIONS
        )
        recovery = _session(
            config_a,
            CLIENT_A,
            "run-recovery",
            binding_a,
            reopened,
            ClientCurveKeys(server_keys[0], *a_keys),
            operations=recovery_ops,
        )
        sessions.append(recovery)
        recovered = recovery.start(timeout_seconds=10)
        assert recovered.bindings[0].operations == recovery_ops
        assert recovery.command_client.cancel_status("cancel-two").record.state == "SENT"
        assert (
            recovery.execution_stream_client.progress().progress.acknowledged_sequence
            == projected[-1][0]
        )
        controls.put("count")
        assert messages.get(timeout=5) == ("count", 2)
    finally:
        client_closes = []
        close_errors = []
        for session in reversed(sessions):
            try:
                snapshot = session.close(timeout_seconds=10)
                client_closes.append((snapshot.state, snapshot.thread_alive))
            except Exception as error:
                close_errors.append(repr(error))
        controls.put("stop")
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail("synthetic Gateway required termination")
        assert process.exitcode == 0
        closed = messages.get(timeout=3)
        assert closed == ("closed", process.pid), closed
        controls.close()
        messages.close()
        assert not close_errors, close_errors
        assert len(client_closes) == len(sessions)
        assert all(state == "closed" and alive is False for state, alive in client_closes), (
            client_closes
        )
