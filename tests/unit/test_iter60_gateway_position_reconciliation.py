"""Iteration 60 (F5): the strategy ledger reconciles against a real Gateway stream.

Spawned Gateway host (account session worker, journal, CURVE ROUTER) ->
ZMQ ``account.read_after`` with cursor + ACK -> ``GatewayAccountPositionStream``
(own thread) -> ``StreamedAccountPositions.report()`` -> ``SqliteStrategyLedger``.
The synthetic provider holds 2.000 of ``synthetic-perpetual``; after a host
refresh it holds 3.000. INTEGRATION_SIM: test-owned processes and keys only.
"""

from __future__ import annotations

import multiprocessing
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

zmq = pytest.importorskip("zmq")

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope  # noqa: E402

from bt_api_py.account_position_stream import (  # noqa: E402
    GatewayAccountPositionStream,
    gateway_position_reducers,
)
from bt_api_py.service_account_consumer import ServiceAccountConsumer  # noqa: E402
from bt_api_py.service_config import parse_client_config  # noqa: E402
from bt_api_py.service_runtime import (  # noqa: E402
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from bt_api_py.strategy_ledger import (  # noqa: E402
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
)
from tests.unit import test_service_account_integration as account_helpers  # noqa: E402
from tests.unit import test_service_account_views_integration as views  # noqa: E402

INSTRUMENT = "synthetic-perpetual"


def _wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_ledger_refuses_unproven_gateway_position_view_while_delivery_continues(tmp_path):
    context = multiprocessing.get_context("spawn")
    endpoint = account_helpers._free_endpoints(1)[0]
    server_keys = zmq.curve_keypair()
    service = account_helpers._account_service_info(
        views._ACCOUNT_REF, endpoint, "account-view-curve-v1"
    )
    public_key, secret_key = zmq.curve_keypair()
    audience = ((public_key, views._PRINCIPAL_B, views._STRATEGY_B),)
    raw_service_config = account_helpers._service_config(service)
    raw_client_config = account_helpers._client_config(
        views._STRATEGY_B, {views._ACCOUNT_REF: service}, (views._ACCOUNT_REF,)
    )
    guard_directory = tmp_path / "guards"
    guard_directory.mkdir()
    anchor = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    records = []
    host = views._start_child(
        context,
        views._view_service_process,
        (
            endpoint,
            raw_service_config,
            str(tmp_path / "registry.sqlite3"),
            str(tmp_path / "journal.sqlite3"),
            str(guard_directory),
            server_keys,
            audience,
            1,
            anchor,
        ),
        "gateway-position-host",
    )
    records.append(host)
    ready = account_helpers._receive(host["pipe"], timeout=20)
    assert ready[0] == "ready", ready

    snapshot_reducer, event_reducer = gateway_position_reducers()

    def consumer_factory():
        runtime = ServiceClientRuntime(
            parse_client_config(raw_client_config),
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, views._PRINCIPAL_B, views._STRATEGY_B),
            scope_resolver=lambda _scope_ref: WireScope(
                views._DOMAIN_REF, account_ref=views._ACCOUNT_REF, strategy_ref=views._STRATEGY_B
            ),
            peer_resolver=lambda _service_id: service["peer"],
            transport_factory=ZmqTransportFactory(
                {service["security_ref"]: ClientCurveKeys(server_keys[0], public_key, secret_key)}
            ),
            run_id=uuid4().hex,
            requested_operations={"store.account": views._OPERATIONS},
        )
        runtime.start()
        consumer = ServiceAccountConsumer(
            runtime,
            "store.account",
            account_ref=views._ACCOUNT_REF,
            path=str(tmp_path / "positions-projection.sqlite3"),
            projection_version="sdk.account.view.v1/iter60-positions-v1",
            initial_state={"view_scope": views._VIEW_B, "positions": {}},
            expected_account_scope=views._JOURNAL_SCOPE,
            expected_strategy_scope=views._VIEW_B,
            snapshot_reducer=snapshot_reducer,
            event_reducer=event_reducer,
        )
        return consumer, runtime

    stream = GatewayAccountPositionStream(
        consumer_factory, account_ref=views._ACCOUNT_REF, poll_interval_seconds=0.05
    )
    ledger = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3",
        account_ref=views._ACCOUNT_REF,
        instruments={INSTRUMENT: InstrumentTerms(Decimal("1"), "USD", "futures")},
        budgets={"A": StrategyBudget(Decimal("1000000"), "USD")},
        reconciliation_max_age_ns=1_000_000_000,  # 1 s settle window for the test
    )
    try:
        ledger.reserve(LedgerIntent("a", "A", INSTRUMENT, "BUY", Decimal("2"), Decimal("10")))
        ledger.apply_execution("a", cumulative_quantity=2, average_price=10)
        stream.start(wait_seconds=20)
        assert _wait_for(lambda: stream.report().positions.get(INSTRUMENT) == Decimal("2.000")), (
            stream.error, stream.positions.interruption
        )
        report = stream.report()
        assert report.positions == {INSTRUMENT: Decimal("2.000")}
        assert report.valid_until_ns is None
        assert ledger.reconcile(report).reason == "REPORT_INCOMPLETE"

        # The provider position grows to 3 outside this ledger: a residual.
        refreshed = account_helpers._host_command(host, "refresh")
        assert refreshed[0] == "refresh-requested", refreshed
        assert _wait_for(lambda: stream.report().positions.get(INSTRUMENT) == Decimal("3.000"))
        # A view update does not establish full-account coverage at any age.
        assert ledger.reconcile(stream.report()).reason == "REPORT_INCOMPLETE"
        time.sleep(1.1)
        assert ledger.reconcile(stream.report()).reason == "REPORT_INCOMPLETE"

        # Matching local values must not turn a partial view into proof.
        ledger.reserve(LedgerIntent("b", "A", INSTRUMENT, "BUY", Decimal("1"), Decimal("10")))
        ledger.apply_execution("b", cumulative_quantity=1, average_price=10)
        assert ledger.reconcile(stream.report()).reason == "REPORT_INCOMPLETE"
        reader = ledger.reader("A", account_positions=stream.report)
        with pytest.raises(Exception) as error:
            reader.snapshot()
        assert getattr(error.value, "code", "") == "STRATEGY_BOOK_RECONCILIATION_UNVERIFIED"
    finally:
        stream.close()
        ledger.close()
        try:
            account_helpers._host_command(host, "close")
        finally:
            for record in records:
                process = record["process"]
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)

