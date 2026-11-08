"""Synthetic CTP callback through SDK ledger and authenticated CURVE stream."""

import queue
import sqlite3
import threading

import pytest
import zmq
from bt_api_base.direct_write_protocol import DirectSubmitResult, WriteOperation
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.ctp_gateway_return_ingestion import CtpGatewayReturnIngestor
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_direct_dispatch import (
    compose_gateway_direct_cancel_dispatcher,
    compose_gateway_direct_dispatcher,
)
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.gateway_execution_stream_client import GatewayExecutionStreamClient
from bt_api_py.gateway_execution_stream_service import (
    STREAM_OPERATIONS,
    GatewayExecutionStreamService,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from bt_api_py.write_admission import WriteAdmission
from tests.integration.test_iter63_ctp_gateway_return_ingestion import (
    ACCOUNT,
    ACCOUNT_KEY,
    INSTRUMENT,
    _order,
    _reader,
    _trade,
)
from tests.integration.test_iter63_gateway_direct_service import (
    CLIENT_A,
    CLIENT_B,
    DOMAIN,
    SERVICE,
    _client_config,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_cancel_return_ingestion import (
    append_error,
    assert_offered,
    create_cancel,
    create_original,
    make_buffer,
)

MOUNT = "broker.direct_write"


@pytest.fixture
def cancel_stream(tmp_path):
    endpoint = _free_endpoint()
    server_pub, server_secret = zmq.curve_keypair()
    a_pub, a_secret = zmq.curve_keypair()
    b_pub, b_secret = zmq.curve_keypair()
    (tmp_path / "guard").mkdir()
    shared = {}
    errors = queue.Queue()

    def owner_factory():
        guard = LocalAccountSessionGuard(tmp_path / "guard", ACCOUNT, "owner")
        try:
            journal = GatewayDirectCommandJournal.create(
                tmp_path / "orders.sqlite", ACCOUNT, generation=guard.acquire()
            )
            ledger = GatewayDirectExecutionLedger(journal, guard)
            reader, _, native = _reader()
            ingestor = CtpGatewayReturnIngestor(
                reader, ledger, account_key=ACCOUNT_KEY, instruments=(INSTRUMENT,)
            )
            original = create_original(journal)
            native.rows["orders"], native.rows["trades"] = [_order()], [_trade()]
            ingestor.read_and_ingest("orders", timeout=1)
            ingestor.read_and_ingest("trades", timeout=1)

            class SyntheticProvider:
                def __init__(self):
                    self.prepared = None
                    self.sends = 0

                def session_generation(self):
                    return 1

                def trading_day(self):
                    return self.prepared.trading_day

                def prepare_cancel_order(self, target, symbol):
                    assert target.to_dict() == self.prepared.target_identity.to_dict()
                    assert symbol == self.prepared.symbol
                    return self.prepared

                def release_cancel_reservation(self, prepared):
                    pass

                def submit_prepared_cancel(self, prepared, grant, verifier):
                    verifier.consume(
                        grant,
                        exchange_name="CTP___FUTURE",
                        operation=WriteOperation.CANCEL,
                        session_generation=1,
                    )
                    self.sends += 1
                    return DirectSubmitResult(True, prepared.target_identity)

            provider = SyntheticProvider()
            coordinator = DirectWriteCoordinator(
                WriteAdmission({"CTP___FUTURE": True}), lambda _: provider
            )
            dispatcher = compose_gateway_direct_dispatcher(
                coordinator,
                "CTP___FUTURE",
                journal,
                guard,
                lambda _: True,
                expected_local_provider=provider,
            )
            cancel_dispatcher = compose_gateway_direct_cancel_dispatcher(
                coordinator,
                "CTP___FUTURE",
                journal,
                guard,
                dispatcher,
                lambda _: True,
                expected_local_provider=provider,
            )

            def dispatch(row, prepared):
                provider.prepared = prepared
                attempt = cancel_dispatcher.dispatch(
                    "strategy-a", row.operation_id, "principal-a", expected_version=row.version
                )
                assert attempt.status == "SENT"
                return attempt

            first, f1 = create_cancel(journal, original, "cancel-one", 11, dispatch=dispatch)
            second, f2 = create_cancel(journal, original, "cancel-two", 12, dispatch=dispatch)
            assert provider.sends == 2
            buffer = make_buffer()
            append_error(buffer, f1)
            append_error(buffer, f1, kind="action_error")
            append_error(buffer, f2, kind="action_error")
            batch = buffer.offer()

            def current(record):
                assert_offered(buffer, record)

            assert ingestor.ingest_callbacks(batch, assert_current=current) == 3
            assert ingestor.ingest_callbacks(batch, assert_current=current) == 3
            buffer.ack(batch)
            assert ledger.position("strategy-a", INSTRUMENT) == "1"
            shared.update(
                journal=journal,
                ledger=ledger,
                first=first,
                second=second,
                db_path=tmp_path / "orders.sqlite",
            )
            return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
        except BaseException:
            import traceback

            errors.put(traceback.format_exc())
            guard.release()
            raise

    owner = GatewayDirectOwner(
        owner_factory, queue_size=4, max_queue_wait_seconds=10, busy_timeout_seconds=10
    )
    if not owner.start(10):
        owner.provide_startup_cleanup(lambda: None)
        owner.close(10)
        pytest.fail(f"synthetic owner failed: {errors.get(timeout=2)}")
    journal, ledger = shared["journal"], shared["ledger"]
    grants = {CLIENT_A.principal_ref: "strategy-a", CLIENT_B.principal_ref: "strategy-b"}
    stream = GatewayExecutionStreamService(ledger, journal, grants, execution_domain_ref=DOMAIN)
    binding = GatewayDirectServiceBinding(
        journal,
        {k: GatewayDirectGrant(v, frozenset({"rb2701"})) for k, v in grants.items()},
        owner=owner,
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
                    STREAM_OPERATIONS,
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
        curve_public_key=server_pub,
        curve_secret_key=server_secret,
        principals={a_pub: CLIENT_A.principal_ref, b_pub: CLIENT_B.principal_ref},
        grants={
            CLIENT_A.principal_ref: (grant(CLIENT_A),),
            CLIENT_B.principal_ref: (grant(CLIENT_B),),
        },
    )
    ready, stop = queue.Queue(), threading.Event()

    def serve():
        host = None
        try:
            host = ServiceHost(
                _service_config(endpoint),
                authority,
                tmp_path / "registry.sqlite",
                lambda *_: ComponentResources.borrowed({"binding": binding}, ready=lambda: True),
            )
            host.start()
            ready.put(("ready", None))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as error:
            ready.put(("error", getattr(error, "code", type(error).__name__)))
        finally:
            if host is not None and host.state == "started":
                host.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    runtimes = []
    try:
        assert ready.get(timeout=10) == ("ready", None)

        def client(identity, run):
            public, secret = (a_pub, a_secret) if identity == CLIENT_A else (b_pub, b_secret)

            def factory():
                raw = _client_config(endpoint, identity).to_public_dict()
                raw["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = ACCOUNT
                return ServiceClientRuntime(
                    parse_client_config(raw),
                    identity=identity,
                    scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
                    peer_resolver=lambda _: SERVICE,
                    run_id=run,
                    transport_factory=ZmqTransportFactory(
                        {"security-a": ClientCurveKeys(server_pub, public, secret)}
                    ),
                    requested_operations={MOUNT: STREAM_OPERATIONS},
                )

            runtime = ThreadedServiceClientRuntime(factory)
            runtime.start(timeout_seconds=10)
            runtimes.append(runtime)
            scope = WireScope(
                DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref, run_id=run
            )
            route = GatewayClientRoute(
                MOUNT, SERVICE.actor_ref, "gateway-a", "scope-a", scope, identity
            )
            cmd_binding = GatewayCommandBinding(
                journal.journal_id, ACCOUNT, identity.actor_ref, identity.principal_ref
            )
            return runtime, GatewayExecutionStreamClient(runtime, binding=cmd_binding, route=route)

        yield shared, client
    finally:
        for runtime in reversed(runtimes):
            runtime.close(timeout_seconds=10)
        stop.set()
        thread.join(10)
        assert not thread.is_alive()
        assert stream.close()
        assert owner.close(10)


def test_errors_are_separate_authenticated_events_replay_and_ack_restart(cancel_stream):
    shared, client = cancel_stream
    runtime, stream = client(CLIENT_A, "run-a")
    assert stream.progress().progress.offered_sequence == 0
    with pytest.raises(ServiceRuntimeError, match="REPLY_TOO_LARGE"):
        runtime.call(
            MOUNT,
            "direct.events.read_after",
            {"after_sequence": 0, "expected_epoch": None, "limit": 256},
            max_reply_message_bytes=512,
            timeout_seconds=5,
        )
    assert stream.progress().progress.offered_sequence == 0
    raw = runtime.call(
        MOUNT,
        "direct.events.read_after",
        {"after_sequence": 0, "expected_epoch": None, "limit": 256},
        timeout_seconds=5,
    )
    assert raw["page"]["schema_version"] == 3
    assert "cancel_request_key" not in str(raw) and "source_binding" not in str(raw)
    assert all(key not in str(raw) for key in shared["first"].prepared_cancel.request_keys)
    page = stream.read_after().page
    events = [event for event in page.events if event.cancel_attribution is not None]
    assert len(events) == 2
    assert [
        (e.cancel_attribution.operation_id, e.cancel_attribution.attempt_id) for e in events
    ] == [
        (shared["first"].operation_id, shared["first"].attempt_id),
        (shared["second"].operation_id, shared["second"].attempt_id),
    ]
    assert events[0].fact_digest != events[1].fact_digest
    assert shared["ledger"].position("strategy-a", INSTRUMENT) == "1"
    assert (
        shared["journal"].get("strategy-a", "key-a", CLIENT_A.principal_ref).status == "DISPATCHING"
    )
    _, other = client(CLIENT_B, "run-b")
    assert other.read_after().page.events == ()
    assert stream.ack(page.events[-1].sequence, page.epoch).outcome == "OBSERVED"
    runtime.close(timeout_seconds=10)
    _, resumed = client(CLIENT_A, "next-run")
    progress = resumed.progress().progress
    assert progress.acknowledged_sequence == page.events[-1].sequence
    assert resumed.read_after(progress.acknowledged_sequence, progress.epoch).page.events == ()


def test_malformed_cancel_association_does_not_offer_page(cancel_stream):
    shared, client = cancel_stream
    _, stream = client(CLIENT_A, "run-a")
    assert stream.progress().progress.offered_sequence == 0
    with sqlite3.connect(shared["db_path"]) as db:
        db.execute(
            "UPDATE direct_cancel_error_attribution SET attempt_id='invalid' WHERE attempt_id=?",
            (shared["first"].attempt_id,),
        )
    assert stream.read_after().outcome == "UNKNOWN"
    assert stream.progress().progress.offered_sequence == 0
