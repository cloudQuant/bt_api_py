"""Iteration 60 (G3): the public, config-driven Gateway position stream.

Spawned Gateway host -> ZMQ account stream -> ``gateway_position_stream_from_config``
(built only from the public client configuration mapping and key material)
-> ``SqliteStrategyLedger`` reconcile. INTEGRATION_SIM: test-owned processes.
"""

from __future__ import annotations

import multiprocessing
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

zmq = pytest.importorskip("zmq")

from bt_api_py.account_position_stream import gateway_position_stream_from_config  # noqa: E402
from bt_api_py.strategy_ledger import (  # noqa: E402
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
)
from tests.unit import test_service_account_integration as account_helpers  # noqa: E402
from tests.unit import test_service_account_views_integration as views  # noqa: E402

INSTRUMENT = "synthetic-perpetual"


def _loaded_host_timeouts(client_config, request_timeout_ms=5_000):
    """T2 root cause: the shared helper's 1 s RPC timeout is shorter than a
    spawned host's reply latency on a heavily loaded machine; registration then
    times out (``RequestOutcomeUnknownError``), which ``ServiceClientRuntime``
    reports as ``REQUIRED_REGISTRATION_FAILED``. Only the test latency budget
    changes; no product check is relaxed."""

    client_config["clients"]["gateway"]["settings"]["options"]["request_timeout_ms"] = (
        request_timeout_ms
    )
    for profile in client_config["communication"]["profiles"].values():
        profile["options"]["request_timeout_ms"] = request_timeout_ms
    return client_config


def start_gateway(tmp_path, records):
    """Spawn the Gateway host; return (host record, service info, client config, keys)."""

    context = multiprocessing.get_context("spawn")
    endpoint = account_helpers._free_endpoints(1)[0]
    server_keys = zmq.curve_keypair()
    service = account_helpers._account_service_info(
        views._ACCOUNT_REF, endpoint, "account-view-curve-v1"
    )
    public_key, secret_key = zmq.curve_keypair()
    guard_directory = tmp_path / "guards"
    guard_directory.mkdir()
    anchor = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    host = views._start_child(
        context,
        views._view_service_process,
        (
            endpoint,
            account_helpers._service_config(service),
            str(tmp_path / "registry.sqlite3"),
            str(tmp_path / "journal.sqlite3"),
            str(guard_directory),
            server_keys,
            ((public_key, views._PRINCIPAL_B, views._STRATEGY_B),),
            1,
            anchor,
        ),
        "gateway-config-host",
    )
    records.append(host)
    ready = account_helpers._receive(host["pipe"], timeout=20)
    assert ready[0] == "ready", ready
    client_config = _loaded_host_timeouts(
        account_helpers._client_config(
            views._STRATEGY_B, {views._ACCOUNT_REF: service}, (views._ACCOUNT_REF,)
        )
    )
    keys = {service["security_ref"]: (server_keys[0], public_key, secret_key)}
    return host, service, client_config, keys


def stream_for(service, client_config, keys, projection_path, **kwargs):
    return gateway_position_stream_from_config(
        client_config,
        account_ref=views._ACCOUNT_REF,
        principal_ref=views._PRINCIPAL_B,
        strategy_ref=views._STRATEGY_B,
        execution_domain_ref=views._DOMAIN_REF,
        service_principal_ref=service["principal_ref"],
        service_id=service["service_id"],
        curve_keys=keys,
        projection_path=str(projection_path),
        expected_account_scope=views._JOURNAL_SCOPE,
        expected_view_scope=views._VIEW_B,
        poll_interval_seconds=0.05,
        wait_seconds=20,
        **kwargs,
    )


def stop_gateway(host, records):
    try:
        account_helpers._host_command(host, "close")
    finally:
        for record in records:
            process = record["process"]
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)


def wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_config_driven_stream_delivers_view_but_does_not_prove_full_account(tmp_path):
    records = []
    host, service, client_config, keys = start_gateway(tmp_path, records)
    stream = None
    ledger = None
    try:
        stream = stream_for(service, client_config, keys, tmp_path / "projection.sqlite3")
        ledger = SqliteStrategyLedger(
            tmp_path / "ledger.sqlite3",
            account_ref=views._ACCOUNT_REF,
            instruments={INSTRUMENT: InstrumentTerms(Decimal("1"), "USD", "futures")},
            budgets={"A": StrategyBudget(Decimal("1000000"), "USD")},
        )
        ledger.reserve(LedgerIntent("a", "A", INSTRUMENT, "BUY", Decimal("2"), Decimal("10")))
        ledger.apply_execution("a", cumulative_quantity=2, average_price=10)
        assert wait_for(lambda: stream.report().positions.get(INSTRUMENT) == Decimal("2")), (
            stream.error, stream.positions.interruption
        )
        report = stream.report()
        assert report.source.startswith("gateway:")
        assert not report.complete
        assert ledger.reconcile(report).reason == "REPORT_INCOMPLETE"
        with pytest.raises(Exception) as error:
            ledger.reader("A", account_positions=stream.report).snapshot()
        assert getattr(error.value, "code", "") == "STRATEGY_BOOK_RECONCILIATION_UNVERIFIED"
    finally:
        if stream is not None:
            stream.close()
        if ledger is not None:
            ledger.close()
        stop_gateway(host, records)


def test_invalid_client_config_fails_before_any_thread(tmp_path):
    with pytest.raises(Exception):
        gateway_position_stream_from_config(
            {"client_config_schema_version": 999},
            account_ref="a",
            principal_ref="p",
            strategy_ref="s",
            execution_domain_ref="d",
            service_principal_ref="sp",
            service_id="sid",
            curve_keys={},
            projection_path=str(tmp_path / "p.sqlite3"),
            expected_account_scope="j",
            expected_view_scope="v",
        )
