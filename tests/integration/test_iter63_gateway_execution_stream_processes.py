"""Synthetic execution facts over a spawned Gateway, CURVE and Registry.

All facts and commands are created by the test in the Gateway process; this
neither connects a provider nor establishes account or production evidence.
"""

from __future__ import annotations

import multiprocessing
import os
import queue
from pathlib import Path

import pytest
import zmq
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    NativeOrderIdentity,
)
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_direct_owner import (
    GatewayDirectOwner,
    GatewayDirectOwnerResources,
)
from bt_api_py.gateway_direct_service import (
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import (
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
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

EVENTS = frozenset(
    {"direct.events.read_after", "direct.events.progress", "direct.events.ack"}
)
CLIENT_C = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-c", "strategy-c")
INSTRUMENT = MarketInstrumentKey("synthetic", "venue", "paper", "future", "X", "X")


def _seed(journal, ledger):
    for identity, key in ((CLIENT_A, "a"), (CLIENT_B, "b")):
        row = journal.reserve(
            identity.actor_ref,
            key,
            identity.principal_ref,
            {"symbol": "X", "side": "buy", "offset": "open", "quantity": "2"},
        )
        native = {
            "exchange_name": "TEST",
            "client_order_id": key,
            "session_generation": 1,
            "fields_json": "{}",
        }
        journal.begin_dispatch(
            identity.actor_ref,
            key,
            row.reservation_id,
            row.digest,
            identity.principal_ref,
            1,
            native,
            ("anchor-" + key,),
        )
    for key, trade in (("a", "one"), ("b", "two"), ("a", "three")):
        anchor = "anchor-" + key
        fact = GatewayReturnFact(
            ACCOUNT,
            "TEST",
            INSTRUMENT,
            "20300101",
            DirectExecutionEventKind.TRADE,
            "synthetic-" + trade,
            identity=NativeOrderIdentity("TEST", key, {"trading_day": "20300101"}, 1),
            identity_keys=(anchor,),
            binding_keys=(anchor,),
            venue_key=anchor,
            trade_key=("venue", trade),
            side="buy",
            offset="open",
            trade_quantity="0.5",
            trade_price="10",
        )
        assert ledger.ingest(fact).disposition == "ATTRIBUTED"


def _gateway(root, endpoint, server_keys, keys, messages, controls, enabled):
    from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService

    host = owner = stream = None
    try:
        root = Path(root)
        directory = root / "guard"
        directory.mkdir()
        resources = []

        def owner_factory():
            guard = LocalAccountSessionGuard(directory, ACCOUNT, "synthetic-owner")
            journal = GatewayDirectCommandJournal.create(
                root / "journal.sqlite", ACCOUNT, generation=guard.acquire()
            )
            ledger = GatewayDirectExecutionLedger(journal, guard)
            _seed(journal, ledger)
            dispatcher = GatewayDirectDispatcher(
                journal, "TEST", guard, lambda *_: None, lambda _: (), lambda _: True
            )
            resources.append((journal, ledger))
            return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

        owner = GatewayDirectOwner(
            owner_factory,
            queue_size=4,
            max_queue_wait_seconds=20,
            busy_timeout_seconds=20,
        )
        assert owner.start(10)
        journal, ledger = resources[0]
        grants = {
            i.principal_ref: GatewayDirectGrant(i.actor_ref, frozenset({"X"}))
            for i in (CLIENT_A, CLIENT_B, CLIENT_C)
        }
        if enabled:
            stream = GatewayExecutionStreamService(
                ledger,
                journal,
                {i.principal_ref: i.actor_ref for i in (CLIENT_A, CLIENT_B, CLIENT_C)},
                execution_domain_ref=DOMAIN,
            )
        binding = GatewayDirectServiceBinding(
            journal,
            grants,
            owner=owner,
            execution_stream=stream,
            execution_domain_ref=DOMAIN,
        )

        def grant(identity):
            operations = (
                EVENTS
                if enabled and identity != CLIENT_C
                else frozenset({"direct.command_status"})
            )
            return PrincipalGrant(
                identity,
                (
                    ResourceGrant(
                        "gateway-a",
                        "service.direct_write",
                        "scope-a",
                        WireScope(
                            DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref
                        ),
                        operations,
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
                key[0]: identity.principal_ref
                for key, identity in zip(
                    keys, (CLIENT_A, CLIENT_B, CLIENT_C), strict=True
                )
            },
            grants={
                identity.principal_ref: (grant(identity),)
                for identity in (CLIENT_A, CLIENT_B, CLIENT_C)
            },
        )
        host = ServiceHost(
            _service_config(endpoint),
            authority,
            root / "registry.sqlite",
            lambda *_: ComponentResources.borrowed(
                {"binding": binding}, ready=lambda: True
            ),
        )
        host.start()
        operations = frozenset(
            op
            for component in host._components
            for capability in component.capabilities
            for op in capability.operations
        )
        messages.put(("ready", os.getpid(), journal.journal_id, operations))
        while True:
            try:
                command = controls.get_nowait()
            except queue.Empty:
                command = None
            if command == "stop":
                break
            host.serve_once(10)
    except BaseException as error:
        import traceback

        messages.put(
            ("error", type(error).__name__, str(error), traceback.format_exc())
        )
    finally:
        try:
            if host is not None and host.state == "started":
                host.close()
            if stream is not None:
                stream.close()
            if owner is not None and not owner.close(10):
                raise RuntimeError("OWNER_CLOSE_FAILED")
            messages.put(("closed", os.getpid()))
        except BaseException as error:
            messages.put(("cleanup_error", type(error).__name__, str(error)))


@pytest.fixture
def service(tmp_path, request):
    enabled = getattr(request, "param", True)
    ctx = multiprocessing.get_context("spawn")
    messages, controls = ctx.Queue(), ctx.Queue()
    server_keys, keys = zmq.curve_keypair(), tuple(
        zmq.curve_keypair() for _ in range(3)
    )
    endpoint = _free_endpoint()
    process = ctx.Process(
        target=_gateway,
        args=(str(tmp_path), endpoint, server_keys, keys, messages, controls, enabled),
    )
    process.start()
    try:
        ready = messages.get(timeout=20)
        assert ready[0] == "ready", ready
        assert ready[1] == process.pid != os.getpid()
        assert (ready[3] >= EVENTS) is enabled
        yield endpoint, server_keys[0], keys, ready[2]
    finally:
        controls.put("stop")
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail("test Gateway required termination")
        assert process.exitcode == 0
        assert messages.get(timeout=3) == ("closed", process.pid)
        controls.close()
        messages.close()


def _client(service, identity, run_id, *, drop=None, budget=None):
    from bt_api_py.gateway_execution_stream_client import GatewayExecutionStreamClient
    from bt_api_py.service_config import parse_client_config

    endpoint, server_public, keys, journal_id = service
    index = (CLIENT_A, CLIENT_B, CLIENT_C).index(identity)
    scope = WireScope(
        DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref, run_id=run_id
    )
    route = GatewayClientRoute(
        "broker.direct_write",
        SERVICE.actor_ref,
        "gateway-a",
        "scope-a",
        scope,
        identity,
    )
    binding = GatewayCommandBinding(
        journal_id, ACCOUNT, identity.actor_ref, identity.principal_ref
    )
    config = _client_config(endpoint, identity)
    if budget is not None:
        document = config.to_public_dict()
        document["communication"]["profiles"]["client-transport"].setdefault(
            "options", {}
        )["max_message_bytes"] = budget
        config = parse_client_config(document)

    errors = queue.Queue()
    requests = queue.Queue()

    def factory():
        base = ZmqTransportFactory(
            {"security-a": ClientCurveKeys(server_public, *keys[index])}
        )
        if drop:

            class DropOnce:
                def __init__(self, wrapped):
                    self.wrapped, self.dropped = wrapped, False

                def request(self, request, *, timeout):
                    requests.put(request.header.operation)
                    reply = self.wrapped.request(request, timeout=timeout)
                    if request.header.operation == drop and not self.dropped:
                        self.dropped = True
                        raise TimeoutError("synthetic lost reply after commit")
                    return reply

                def close(self):
                    self.wrapped.close()

            def transport(*args):
                return DropOnce(base(*args))

        else:
            transport = base
        raw = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
            peer_resolver=lambda _: SERVICE,
            transport_factory=transport,
            run_id=run_id,
            requested_operations={"broker.direct_write": EVENTS},
        )
        start = raw.start

        def capture_start():
            try:
                return start()
            except ServiceRuntimeError as error:
                errors.put(error.code)
                raise

        raw.start = capture_start
        return raw

    runtime = ThreadedServiceClientRuntime(factory)
    runtime._test_start_errors = errors
    runtime._test_requests = requests
    return runtime, GatewayExecutionStreamClient(runtime, binding=binding, route=route)


def _observed(result, field):
    assert result.outcome == "OBSERVED", result
    return getattr(result, field)


def test_real_curve_registry_stream_isolation_ack_and_new_run(service):
    runtime_a, client_a = _client(service, CLIENT_A, "run-a")
    runtime_b, client_b = _client(service, CLIENT_B, "run-b")
    denied, _ = _client(service, CLIENT_C, "run-c")
    replacement = None
    try:
        runtime_a.start(timeout_seconds=10)
        runtime_b.start(timeout_seconds=10)
        with pytest.raises(ThreadedRuntimeError, match="START_FAILED"):
            denied.start(timeout_seconds=10)
        assert denied._test_start_errors.get(timeout=2) == "SCOPE_DENIED"
        page_a = _observed(client_a.read_after(), "page")
        page_b = _observed(client_b.read_after(), "page")
        assert [e.sequence for e in page_a.events] == [1, 3]
        assert [e.sequence for e in page_b.events] == [2]
        assert [e.request_key for e in page_a.events] == ["a", "a"]
        assert [e.request_key for e in page_b.events] == ["b"]
        assert page_a.consumer_ref != page_b.consumer_ref
        assert (page_a.offered_sequence, page_a.acknowledged_sequence) == (3, 0)
        replay = _observed(client_a.read_after(), "page")
        assert replay.events == page_a.events
        assert (
            _observed(client_a.ack(1, page_a.epoch), "progress").acknowledged_sequence
            == 1
        )
        assert (
            _observed(client_a.ack(3, page_a.epoch), "progress").acknowledged_sequence
            == 3
        )
        assert (
            _observed(client_a.ack(1, page_a.epoch), "progress").acknowledged_sequence
            == 3
        )
        assert _observed(client_b.progress(), "progress").acknowledged_sequence == 0
        assert client_a.ack(2, page_a.epoch).outcome == "REQUEST_DENIED"
        assert client_a.ack(3, "0" * 64).outcome == "REQUEST_DENIED"
        runtime_a.close(timeout_seconds=10)
        replacement, client_new = _client(service, CLIENT_A, "run-a-replacement")
        replacement.start(timeout_seconds=10)
        progress = _observed(client_new.progress(), "progress")
        assert (
            progress.consumer_ref,
            progress.epoch,
            progress.offered_sequence,
            progress.acknowledged_sequence,
        ) == (page_a.consumer_ref, page_a.epoch, 3, 3)
        assert _observed(client_new.read_after(), "page").events == page_a.events
    finally:
        for runtime in (replacement, denied, runtime_b, runtime_a):
            if runtime is not None:
                runtime.close(timeout_seconds=10)


def test_lost_read_reply_leaves_offered_but_never_auto_acks(service):
    runtime, client = _client(
        service, CLIENT_A, "run-lost", drop="direct.events.read_after"
    )
    try:
        runtime.start(timeout_seconds=10)
        assert client.read_after().outcome == "UNKNOWN"
        progress = _observed(client.progress(), "progress")
        assert (progress.offered_sequence, progress.acknowledged_sequence) == (3, 0)
        page = _observed(client.read_after(), "page")
        assert [e.sequence for e in page.events] == [1, 3]
        assert page.acknowledged_sequence == 0
    finally:
        runtime.close(timeout_seconds=10)


def test_lost_ack_reply_commits_once_and_new_run_observes_progress(service):
    runtime, client = _client(
        service, CLIENT_A, "run-ack-lost", drop="direct.events.ack"
    )
    replacement = None
    try:
        runtime.start(timeout_seconds=10)
        page = _observed(client.read_after(), "page")
        assert client.ack(3, page.epoch).outcome == "UNKNOWN"
        progress = _observed(client.progress(), "progress")
        assert (progress.offered_sequence, progress.acknowledged_sequence) == (3, 3)
        requests = []
        while not runtime._test_requests.empty():
            requests.append(runtime._test_requests.get_nowait())
        assert requests.count("direct.events.ack") == 1
        runtime.close(timeout_seconds=10)
        replacement, resumed = _client(service, CLIENT_A, "run-ack-recovered")
        replacement.start(timeout_seconds=10)
        recovered = _observed(resumed.progress(), "progress")
        assert (
            recovered.consumer_ref,
            recovered.epoch,
            recovered.acknowledged_sequence,
        ) == (page.consumer_ref, page.epoch, 3)
        assert [e.sequence for e in _observed(resumed.read_after(), "page").events] == [
            1,
            3,
        ]
    finally:
        for item in (replacement, runtime):
            if item is not None:
                item.close(timeout_seconds=10)


def test_receive_budget_shrinks_actual_wire_page(service):
    # Search the client's real transport cap, using replies from the actual codec;
    # no estimate or mock of the envelope size determines the threshold.
    lower, upper = 1400, 8192
    for attempt in range(15):
        budget = (lower + upper) // 2
        runtime, client = _client(
            service, CLIENT_A, f"run-budget-{attempt}", budget=budget
        )
        try:
            runtime.start(timeout_seconds=10)
            result = client.read_after()
            if result.outcome == "REQUEST_DENIED":
                assert result.code == "REPLY_TOO_LARGE"
                lower = budget + 1
            else:
                page = _observed(result, "page")
                if len(page.events) == 1:
                    assert [e.sequence for e in page.events] == [1]
                    assert page.has_more is True and page.offered_sequence >= 1
                    tail = _observed(client.read_after(1, page.epoch), "page")
                    assert [e.sequence for e in tail.events] == [3]
                    assert tail.has_more is False
                    return
                assert [e.sequence for e in page.events] == [1, 3]
                upper = budget - 1
        finally:
            runtime.close(timeout_seconds=10)
        assert lower <= upper, "no actual transport cap produced a one-event page"
    pytest.fail("no actual transport cap produced a one-event page")


@pytest.mark.parametrize("service", [False], indirect=True)
def test_without_execution_stream_no_event_operations_advertised(service):
    runtime, _ = _client(service, CLIENT_A, "run-no-stream")
    try:
        with pytest.raises(ThreadedRuntimeError, match="START_FAILED"):
            runtime.start(timeout_seconds=10)
        assert runtime._test_start_errors.get(timeout=2) == "CAPABILITY_UNAVAILABLE"
    finally:
        runtime.close(timeout_seconds=10)


def test_client_receive_budget_does_not_advance_first_event(service, tmp_path):
    runtime, client = _client(service, CLIENT_A, "run-small", budget=1400)
    try:
        runtime.start(timeout_seconds=10)
        rejected = client.read_after()
        assert (rejected.outcome, rejected.code) == (
            "REQUEST_DENIED",
            "REPLY_TOO_LARGE",
        )
        import sqlite3

        with sqlite3.connect(tmp_path / "journal.sqlite") as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM direct_execution_consumers"
                ).fetchone()[0]
                == 0
            )
        progress = _observed(client.progress(), "progress")
        assert (progress.offered_sequence, progress.acknowledged_sequence) == (0, 0)
    finally:
        runtime.close(timeout_seconds=10)
