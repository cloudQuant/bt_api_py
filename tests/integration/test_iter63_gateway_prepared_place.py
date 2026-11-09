"""Synthetic CTP Python prepared PLACE through Gateway ledger and CURVE stream.

No CTP connection or account access occurs. The sole native API is a test leaf;
prepared request creation, original SPI callback capture, SDK ingestion,
SQLite attribution, Registry, codec and client projection are real code.
"""

from __future__ import annotations

import multiprocessing
import queue
import threading
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey
from bt_api_base.direct_write_protocol import DirectExecutionEventKind
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireScope
from bt_api_ctp.ctp import client as ctp_client
from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
from bt_api_ctp.gateway_return_buffer import CtpGatewayReturnBuffer
from bt_api_ctp.gateway_return_query import CtpGatewayReturnQueryReader
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from iter63_prepared_support import compose_claimed_dispatcher

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.ctp_gateway_return_ingestion import (
    CtpGatewayReturnIngestionError,
    CtpGatewayReturnIngestor,
)
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_direct_client import GatewayClientRoute
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
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from bt_api_py.write_admission import WriteAdmission
from tests.integration.test_iter63_gateway_direct_service import (
    CLIENT_A,
    CLIENT_B,
    DOMAIN,
    SERVICE,
    _client_config,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint

ROUTE = "CTP___FUTURE"
BROKER, INVESTOR, DAY = "3070", "synth001", "20261009"
ACCOUNT_KEY = AccountKey("ctp", "simnow", "simnow", INVESTOR, BROKER)
ACCOUNT = ACCOUNT_KEY.account_key_id
INSTRUMENT = MarketInstrumentKey("ctp", "CZCE", "simnow", "future", "SA2701", "SA2701")


def _fact_count(journal):
    with journal._transaction() as db:
        return db.execute("SELECT count(*) FROM direct_execution_facts").fetchone()[0]


def _fake_reader_and_provider(monkeypatch):
    # These are fixed labels used by the existing registered-SimNow source
    # policy. The synthetic Trader is never connected and has no network API.
    td, md = "tcp://182.254.243.31:30002", "tcp://182.254.243.31:30012"
    buffer = CtpGatewayReturnBuffer(broker_id=BROKER, investor_id=INVESTOR)
    feed = CtpRequestDataFuture(
        broker_id=BROKER,
        user_id=INVESTOR,
        password="unused",
        td_front=td,
        md_front=md,
        auto_settlement_confirm=False,
    )
    native = SimpleNamespace()
    trader = ctp_client.TraderClient(td, BROKER, INVESTOR, "unused")
    trader._install_gateway_return_buffer(buffer, environment="simnow")
    trader._api = native
    trader._session_native_api = native
    trader._session_native_front = td
    trader._connected = True
    trader._authentication_state = "authenticated"
    trader._login_state = "logging_in"
    trader._connection_generation = 7
    trader._login_request_id = 1
    trader._login_connection_generation = 7
    spi = ctp_client._TraderSpi(trader, native_api=native)
    trader._spi = spi
    spi.OnRspUserLogin(
        SimpleNamespace(
            BrokerID=BROKER,
            UserID=INVESTOR,
            FrontID=1,
            SessionID=2,
            TradingDay=DAY,
            MaxOrderRef="7",
        ),
        SimpleNamespace(ErrorID=0),
        1,
        True,
    )
    trader._settlement_state = "confirmed"
    trader._settlement_readback_verified = True
    trader._settlement_account_fingerprint = trader._account_fingerprint
    trader._settlement_trading_day = trader._trading_day
    trader._settlement_connection_generation = 7
    trader._ready = True
    trader._has_current_settlement_readback_locked = lambda: True
    capability, _ = trader.arm_execution_for_registered_sim(
        instrument_id="SA2701",
        exchange_id="CZCE",
        md_front=md,
        strategy_identity_sha256="b" * 64,
        execution_cycle_id="test-cycle",
        preflight_sha256="c" * 64,
    )
    feed._trader = trader
    provider = CtpDirectWriteProvider(
        feed,
        background_settlement_readback=False,
        allow_implicit_connect=False,
        owner_thread_only=True,
    )
    monkeypatch.setattr(provider, "_admitted_capability", lambda _: capability)
    monkeypatch.setattr(provider, "_ensure_armed", lambda *_: None)
    reader = CtpGatewayReturnQueryReader(
        trader, environment="simnow", broker_id=BROKER, investor_id=INVESTOR
    )
    return reader, provider, trader, spi, native, buffer


def _wrong_ticket(record, *, wrong_id=False, wrong_source=False):
    """Create only a negative test ticket from an actual captured callback."""
    sink = CtpGatewayReturnBuffer(broker_id=BROKER, investor_id=INVESTOR)
    assert sink._append(
        kind=record.kind,
        row=dict(record.row),
        source_ref=("wrong-source" if wrong_source else record.source_ref),
        source_tags=record.source_tags,
        connection_generation=record.connection_generation,
        reception_trading_day=record.reception_trading_day,
        front_id=record.front_id,
        session_id=record.session_id,
        request_id=(record.request_id + 1 if wrong_id else record.request_id),
        is_last=record.is_last,
        error_code=record.error_code,
        login_request_id=record.login_request_id,
    )
    return sink


def _scope_check(sink, reader):
    def check(record):
        if not sink._contains_record(record):
            raise RuntimeError("ticket no longer offered")
        reader.assert_session_current()

    return check


def _seed(owner_path, monkeypatch):
    guard = LocalAccountSessionGuard(owner_path / "guard", ACCOUNT, "synthetic-owner")
    generation = guard.acquire()
    try:
        journal = GatewayDirectCommandJournal.create(
            owner_path / "orders.sqlite", ACCOUNT, generation=generation
        )
        ledger = GatewayDirectExecutionLedger(journal, guard)
        reader, provider, trader, spi, native, buffer = _fake_reader_and_provider(monkeypatch)
        ingestor = CtpGatewayReturnIngestor(
            reader, ledger, account_key=ACCOUNT_KEY, instruments=(INSTRUMENT,)
        )
        admission = WriteAdmission({ROUTE: True})
        coordinator = DirectWriteCoordinator(
            admission, lambda route: provider if route == ROUTE else None
        )
        dispatcher = compose_claimed_dispatcher(
            coordinator,
            ROUTE,
            journal,
            guard,
            lambda _: True,
            expected_local_provider=provider,
        )
        request = OrderRequest(
            symbol="SA2701",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("2"),
            account_id=ACCOUNT,
            client_order_id="synthetic-client",
            price=Decimal("100"),
            time_in_force="GFD",
            idempotency_key="place-a",
            offset="open",
            exchange_id="CZCE",
            quantity_unit="contracts",
        )
        original = journal.reserve("strategy-a", "place-a", "principal-a", request.to_dict())
        sent = []

        def send(field, request_id):
            current = journal.get("strategy-a", "place-a", "principal-a")
            prepared = journal.get_prepared_place(original.reservation_id)
            assert current.status == "DISPATCHING" and current.native_identity_json is not None
            assert prepared is not None and prepared.request_fields["RequestID"] == request_id
            assert (
                field.RequestID == request_id
                and field.OrderRef == prepared.identity.client_order_id
            )
            assert prepared.source_binding["schema"] == "ctp-place-source-v1"
            assert (
                journal.find_place_by_request_key(prepared.request_keys[0]).reservation_id
                == original.reservation_id
            )
            sent.append((field, request_id, prepared))
            return 0

        native.ReqOrderInsert = send
        dispatched = dispatcher.dispatch(
            "strategy-a",
            "place-a",
            "principal-a",
            original.reservation_id,
            original.digest,
            original.version,
        )
        assert dispatched.status == "SENT" and len(sent) == 1
        assert (
            dispatcher.dispatch(
                "strategy-a",
                "place-a",
                "principal-a",
                original.reservation_id,
                original.digest,
                original.version,
            )
            == dispatched
        )
        assert len(sent) == 1
        field, request_id, prepared = sent[0]
        # Error flows through the real native SPI callback and volatile sink.
        spi.OnRspOrderInsert(
            field, SimpleNamespace(ErrorID=7, ErrorMsg="synthetic"), request_id, True
        )
        spi.OnErrRtnOrderInsert(field, SimpleNamespace(ErrorID=7, ErrorMsg="synthetic"))
        offered = buffer.offer()
        assert len(offered.records) == 2 and buffer.status().gap is False
        for kwargs in ({"wrong_id": True}, {"wrong_source": True}):
            bad_sink = _wrong_ticket(offered.records[0], **kwargs)
            bad = bad_sink.offer()
            with pytest.raises(CtpGatewayReturnIngestionError):
                ingestor.ingest_callbacks(bad, assert_current=_scope_check(bad_sink, reader))
            assert bad_sink.offer() is bad and _fact_count(journal) == 0
        assert ingestor.ingest_callbacks(offered, assert_current=_scope_check(buffer, reader)) == 2
        assert ingestor.ingest_callbacks(offered, assert_current=_scope_check(buffer, reader)) == 2
        assert len(ledger.read_events("strategy-a")) == 1
        assert journal.get("strategy-a", "place-a", "principal-a").status == "SENT"
        assert ledger.position("strategy-a", INSTRUMENT) is None
        buffer.ack(offered)
        # Native ORDER and TRADE arrive after the insert rejection; they still
        # travel through the same SPI, buffer and SDK ingestion path.
        identity = prepared.identity
        order = SimpleNamespace(
            BrokerID=BROKER,
            InvestorID=INVESTOR,
            TradingDay=DAY,
            ExchangeID="CZCE",
            InstrumentID="SA2701",
            OrderRef=identity.client_order_id,
            FrontID=1,
            SessionID=2,
            OrderSysID="sys-1",
            Direction="0",
            CombOffsetFlag="0",
            OrderStatus="1",
            OrderSubmitStatus="3",
            OrderPriceType="2",
            LimitPrice=100.0,
            VolumeTotalOriginal=2,
            VolumeTraded=1,
            VolumeTotal=1,
        )
        trade = SimpleNamespace(
            BrokerID=BROKER,
            InvestorID=INVESTOR,
            TradingDay=DAY,
            ExchangeID="CZCE",
            InstrumentID="SA2701",
            OrderRef=identity.client_order_id,
            OrderSysID="sys-1",
            TradeID="trade-1",
            Direction="0",
            OffsetFlag="0",
            Volume=1,
            Price=100.0,
            TradeTime="09:30:00",
        )
        spi.OnRtnOrder(order)
        spi.OnRtnTrade(trade)
        late = buffer.offer()
        assert len(late.records) == 2 and not buffer.status().gap
        assert ingestor.ingest_callbacks(late, assert_current=_scope_check(buffer, reader)) == 2
        buffer.ack(late)
        assert ledger.position("strategy-a", INSTRUMENT) == "1"
        assert journal.get("strategy-a", "place-a", "principal-a").status == "SENT"
        with journal._transaction() as db:
            revision = journal._assert_anchor(db)
        reopened = GatewayDirectCommandJournal(
            owner_path / "orders.sqlite",
            ACCOUNT,
            generation=generation,
            expected_journal_id=journal.journal_id,
            minimum_revision=revision,
        )
        assert (
            reopened.get_prepared_place(original.reservation_id).to_mapping()
            == prepared.to_mapping()
        )
        assert len(GatewayDirectExecutionLedger(reopened, guard).read_events("strategy-a")) == 3
        return guard, journal, ledger, dispatcher, prepared
    except BaseException:
        guard.release()
        raise


@pytest.fixture
def prepared_stream(tmp_path, monkeypatch):
    from bt_api_py.gateway_direct_owner import GatewayOwnerStartupCleanupError

    (tmp_path / "guard").mkdir()
    shared = {}
    errors = queue.Queue()

    def owner_factory():
        try:
            guard, journal, ledger, dispatcher, prepared = _seed(tmp_path, monkeypatch)
            shared.update(journal=journal, ledger=ledger, prepared=prepared)
            return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
        except BaseException:
            import traceback

            errors.put(traceback.format_exc())
            raise GatewayOwnerStartupCleanupError(lambda: None) from None

    owner = GatewayDirectOwner(
        owner_factory, queue_size=4, max_queue_wait_seconds=10, busy_timeout_seconds=10
    )
    if not owner.start(10):
        owner.close(10)
        pytest.fail(f"synthetic owner setup failed: {errors.get(timeout=2)}")
    stream = None
    try:
        journal, ledger = shared["journal"], shared["ledger"]
        grants = {
            CLIENT_A.principal_ref: CLIENT_A.actor_ref,
            CLIENT_B.principal_ref: CLIENT_B.actor_ref,
        }
        stream = GatewayExecutionStreamService(ledger, journal, grants, execution_domain_ref=DOMAIN)
        binding = GatewayDirectServiceBinding(
            journal,
            {
                key: GatewayDirectGrant(value, frozenset({"SA2701"}))
                for key, value in grants.items()
            },
            owner=owner,
            execution_stream=stream,
            execution_domain_ref=DOMAIN,
        )
        endpoint = _free_endpoint()
        server_keys = zmq.curve_keypair()
        client_keys = {CLIENT_A: zmq.curve_keypair(), CLIENT_B: zmq.curve_keypair()}

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
            curve_public_key=server_keys[0],
            curve_secret_key=server_keys[1],
            principals={keys[0]: identity.principal_ref for identity, keys in client_keys.items()},
            grants={identity.principal_ref: (grant(identity),) for identity in client_keys},
        )
        messages, stop = queue.Queue(), threading.Event()

        def serve():
            service = None
            try:
                service = ServiceHost(
                    _service_config(endpoint),
                    authority,
                    tmp_path / "registry.sqlite",
                    lambda *_: ComponentResources.borrowed(
                        {"binding": binding}, ready=lambda: True
                    ),
                )
                service.start()
                messages.put(("ready",))
                while not stop.is_set():
                    service.serve_once(10)
            except BaseException as error:
                messages.put(("error", type(error).__name__, getattr(error, "code", "")))
            finally:
                if service is not None and service.state == "started":
                    service.close()

        thread = threading.Thread(target=serve, name="synthetic-r19-gateway", daemon=True)
        thread.start()
    except BaseException:
        if stream is not None:
            stream.close()
        owner.close(10)
        raise
    runtimes = []
    try:
        ready = messages.get(timeout=10)
        assert ready == ("ready",), ready

        def client(identity, run):
            def factory():
                config = _client_config(endpoint, identity).to_public_dict()
                config["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = ACCOUNT
                return ServiceClientRuntime(
                    parse_client_config(config),
                    identity=identity,
                    scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
                    peer_resolver=lambda _: SERVICE,
                    run_id=run,
                    transport_factory=ZmqTransportFactory(
                        {"security-a": ClientCurveKeys(server_keys[0], *client_keys[identity])}
                    ),
                    requested_operations={"broker.direct_write": STREAM_OPERATIONS},
                )

            runtime = ThreadedServiceClientRuntime(factory)
            runtime.start(timeout_seconds=10)
            runtimes.append(runtime)
            scope = WireScope(
                DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref, run_id=run
            )
            route = GatewayClientRoute(
                "broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", scope, identity
            )
            client_binding = GatewayCommandBinding(
                journal.journal_id, ACCOUNT, identity.actor_ref, identity.principal_ref
            )
            return runtime, GatewayExecutionStreamClient(
                runtime, binding=client_binding, route=route
            )

        yield shared, client
    finally:
        cleanup_errors = []
        for runtime in reversed(runtimes):
            try:
                runtime.close(timeout_seconds=10)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        stop.set()
        thread.join(10)
        if thread.is_alive():
            cleanup_errors.append("Gateway host thread did not stop")
        try:
            if not stream.close():
                cleanup_errors.append("stream close failed")
        except Exception as error:
            cleanup_errors.append(type(error).__name__)
        try:
            if not owner.close(10):
                cleanup_errors.append("owner close failed")
        except Exception as error:
            cleanup_errors.append(type(error).__name__)
        assert not cleanup_errors, cleanup_errors


def test_prepared_ctp_place_reject_and_late_facts_reach_authenticated_client(prepared_stream):
    shared, connect = prepared_stream
    runtime, client = connect(CLIENT_A, "run-a")
    raw = runtime.call(
        "broker.direct_write",
        "direct.events.read_after",
        {"after_sequence": 0, "expected_epoch": None, "limit": 256},
        timeout_seconds=5,
    )
    assert raw["page"]["schema_version"] == 3
    raw_text = repr(raw)
    prepared = shared["prepared"]
    for secret in ("RequestID", "source_binding", "source_ref", *prepared.request_keys):
        assert secret not in raw_text
    page_result = client.read_after()
    assert page_result.outcome == "OBSERVED", page_result
    page = page_result.page
    assert [event.sequence for event in page.events] == [1, 2, 3]
    error = page.events[0]
    assert error.observation.kind is DirectExecutionEventKind.ORDER_ERROR
    assert error.observation.status is None
    assert error.place_attribution.prepared_digest == prepared.digest()
    assert error.cancel_attribution is None
    assert shared["ledger"].position("strategy-a", INSTRUMENT) == "1"
    _, other = connect(CLIENT_B, "run-b")
    assert other.read_after().page.events == ()
    assert client.ack(3, page.epoch).outcome == "OBSERVED"
    runtime.close(timeout_seconds=10)
    _, resumed = connect(CLIENT_A, "run-next")
    assert resumed.progress().progress.acknowledged_sequence == 3


def _reopen_prepared_place(root, journal_id, revision, reservation_id, replies):
    guard = LocalAccountSessionGuard(Path(root) / "guard", ACCOUNT, "restarted-synthetic-owner")
    try:
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal(
            Path(root) / "orders.sqlite",
            ACCOUNT,
            generation=generation,
            expected_journal_id=journal_id,
            minimum_revision=revision,
        )
        prepared = journal.get_prepared_place(reservation_id)
        command = journal.find_place_by_request_key(prepared.request_keys[0])
        ledger = GatewayDirectExecutionLedger(journal, guard)
        replies.put(
            (
                "ok",
                generation,
                prepared.digest(),
                command.status,
                len(ledger.read_events("strategy-a")),
                ledger.position("strategy-a", INSTRUMENT),
            )
        )
    except BaseException as error:
        replies.put(("error", type(error).__name__, str(getattr(error, "code", ""))))
    finally:
        guard.release()


def test_new_process_reopens_persisted_prepared_identity_and_events(tmp_path, monkeypatch):
    (tmp_path / "guard").mkdir()
    guard, journal, ledger, _dispatcher, prepared = _seed(tmp_path, monkeypatch)
    try:
        with journal._transaction() as db:
            revision = journal._assert_anchor(db)
        journal_id, old_generation = journal.journal_id, journal.generation
        reservation_id = journal.find_place_by_request_key(prepared.request_keys[0]).reservation_id
        expected = prepared.digest()
        assert ledger.position("strategy-a", INSTRUMENT) == "1"
    finally:
        guard.release()
    context = multiprocessing.get_context("spawn")
    replies = context.Queue()
    process = context.Process(
        target=_reopen_prepared_place,
        args=(str(tmp_path), journal_id, revision, reservation_id, replies),
    )
    process.start()
    try:
        result = replies.get(timeout=15)
        assert result[0] == "ok", result
        assert result[1] != old_generation
        assert result[2:] == (expected, "SENT", 3, "1")
    finally:
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail("synthetic restart process did not stop")
        assert process.exitcode == 0
        replies.close()
