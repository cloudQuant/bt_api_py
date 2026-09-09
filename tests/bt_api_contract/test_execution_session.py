"""Public BtApi execution session regressions, with no network or credentials."""

import asyncio
import hashlib
import json
import multiprocessing
import os
import stat
import threading
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bt_api_base.exceptions import InvalidOrderError

import bt_api_py._execution_session as execution_session_module
from bt_api_py import (
    BtApi,
    CancelOrderRequest,
    NormalizedApiError,
    OrderRequest,
    QueryOrderRequest,
    migrate_execution_journal,
)
from bt_api_py._contracts import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderType, Side

VENUE = "OKX___SWAP"
BINANCE_VENUE = "BINANCE___SWAP"
SYMBOL = "BTC-USDT-SWAP"
MIGRATION_FINGERPRINT = hashlib.sha256(
    b"bt-api-py\0OKX\0fixture-okx-public"
).hexdigest()


def credential_settings(venue):
    if venue.startswith("OKX___"):
        return {
            "environment": "demo",
            "api_key": "fixture-okx-public",
            "api_secret": "fixture-okx-secret",
            "passphrase": "fixture-okx-passphrase",
        }
    return {
        "environment": "demo",
        "api_key": "fixture-binance-public",
        "api_secret": "fixture-binance-secret",
    }


def request(client_id="123456789012", **changes):
    values = {
        "symbol": SYMBOL,
        "account_id": VENUE,
        "client_order_id": client_id,
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("2"),
        "price": Decimal("60000"),
        "quantity_unit": "native",
        "position_mode": "net",
        "time_in_force": "IOC",
    }
    values.update(changes)
    return OrderRequest(**values)


def response(**changes):
    return {
        "order_id": "123",
        "client_order_id": "123456789012",
        "symbol": SYMBOL,
        "status": "accepted",
        "filled": 0,
        "side": "buy",
        **changes,
    }


class Backend:
    def __init__(self):
        self.placed = []
        self.queried = []
        self.canceled = []
        self.place_result = response()
        self.query_result = response()
        self.cancel_result = {"order_id": "123"}
        self.account_results = {
            VENUE: {"details": [{"ccy": "USDT", "availEq": "100", "eq": "100"}]},
            BINANCE_VENUE: {
                "assets": [
                    {
                        "asset": "USDT",
                        "availableBalance": "200",
                        "walletBalance": "200",
                        "unrealizedProfit": "0",
                    }
                ]
            },
        }
        self.position_results = {VENUE: [], BINANCE_VENUE: []}
        self.open_order_results = {VENUE: [], BINANCE_VENUE: []}
        self.open_orders_queried = []

    @staticmethod
    def result(value):
        if isinstance(value, Exception):
            raise value
        return value() if callable(value) else value

    def make_order(self, venue, req):
        self.placed.append((venue, req))
        return self.result(self.place_result)

    def query_order(self, venue, req, **kwargs):
        self.queried.append((venue, req))
        return self.result(self.query_result)

    def cancel_order(self, venue, req, **kwargs):
        self.canceled.append((venue, req))
        return self.result(self.cancel_result)

    def get_account(self, venue, **kwargs):
        del kwargs
        return self.result(self.account_results[venue])

    def get_position(self, venue, **kwargs):
        del kwargs
        return self.result(self.position_results[venue])

    def get_open_orders(self, venue, **kwargs):
        del kwargs
        self.open_orders_queried.append(venue)
        return self.result(self.open_order_results[venue])


@pytest.fixture
def factory(monkeypatch, tmp_path):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    monkeypatch.setattr(
        "bt_api_py._execution_session._ledger_registry_root",
        lambda: tmp_path / "execution-ledgers",
    )
    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.create_feed",
        lambda exchange_name, data_queue, **kwargs: SimpleNamespace(
            disconnect=lambda: None,
            get_environment_info=lambda: {
                "environment": kwargs.get("environment", "demo"),
                "api_region": "global",
                "simulated": kwargs.get("environment", "demo") != "production",
                "verified": True,
            },
        ),
    )
    clients = []

    def make(path=None, *, config=None, legacy=False):
        settings = {
            "order_journal": path or tmp_path / f"orders-{len(clients)}.jsonl",
            "account_currency": "USDT",
            "account_ids": {VENUE: VENUE, BINANCE_VENUE: BINANCE_VENUE},
            "required_environments": {VENUE: "demo", BINANCE_VENUE: "demo"},
            **(config or {}),
        }
        exchange_settings = {
            VENUE: credential_settings(VENUE),
            BINANCE_VENUE: credential_settings(BINANCE_VENUE),
        }
        api = BtApi(
            exchange_settings,
            debug=False,
            execution_config=None if legacy else settings,
        )
        api.exchange_feeds[VENUE] = SimpleNamespace(
            disconnect=lambda: None,
            get_environment_info=lambda: {
                "environment": "demo",
                "api_region": "global",
                "simulated": True,
                "verified": True,
            },
        )
        api.data_queues[VENUE] = Queue()
        api._backend = Backend()
        clients.append(api)
        return api

    yield make
    for api in clients:
        with suppress(Exception):
            api.close()


def test_execution_identity_is_sdk_owned_and_account_scoped(factory, tmp_path):
    account_id = f"identity-{tmp_path.parent.name}-{tmp_path.name}"
    api = factory(
        config={
            "strategy_id": "strategy-a",
            "account_ids": {VENUE: account_id, BINANCE_VENUE: f"{account_id}-binance"},
        }
    )

    identity = api.get_execution_identity(VENUE)

    assert identity["fencing_epoch"] >= 1
    assert identity["provider"] == "OKX"
    assert identity["environment"] == "demo"
    assert identity["account_id"].startswith("okx-credential-")
    assert identity["credential_fingerprint"]
    assert identity["exchange_name"] == VENUE
    assert identity["strategy_id"] == "strategy-a"
    assert identity["journal_path"] == str(api._execution_session.path)
    assert account_id not in identity["account_id"]


def test_account_risk_baseline_is_provider_queried_durable_and_reopens(
    factory, tmp_path
):
    path = tmp_path / "risk-orders.jsonl"
    first = factory(path)

    snapshot = first.get_account_risk_snapshot(initialize_baseline=True)

    assert snapshot["evidence_complete"] is True
    assert snapshot["durable"] is True
    assert snapshot["trading_blocked"] is False
    assert snapshot["baseline_equity"] == snapshot["current_equity"] == "300"
    assert snapshot["currency"] == "USDT"
    assert snapshot["generation"] == snapshot["fencing_epoch"]
    assert snapshot["fencing_epoch"] >= 1
    assert first._backend.open_orders_queried == [
        BINANCE_VENUE,
        VENUE,
        BINANCE_VENUE,
        VENUE,
    ]
    risk_path = first._execution_session.risk_path
    assert risk_path.is_file()
    first.close()

    second = factory(path)
    reopened = second.get_account_risk_snapshot()
    assert reopened["durable"] is True
    assert reopened["baseline_equity"] == "300"
    assert reopened["session_generation"] != snapshot["session_generation"]
    assert reopened["fencing_epoch"] > snapshot["fencing_epoch"]


def test_first_account_risk_baseline_uses_normalized_public_open_order_queries(factory):
    api = factory()
    original = api.get_open_orders
    calls = []

    def get_open_orders(venue, *args, **kwargs):
        calls.append((venue, args, dict(kwargs)))
        return original(venue, *args, **kwargs)

    api.get_open_orders = get_open_orders

    snapshot = api.get_account_risk_snapshot(initialize_baseline=True)

    assert snapshot["durable"] is True
    assert [call for call in calls if call[2].get("normalized")] == [
        (BINANCE_VENUE, (None,), {"normalized": True}),
        (VENUE, (None,), {"normalized": True}),
        (BINANCE_VENUE, (None,), {"normalized": True}),
        (VENUE, (None,), {"normalized": True}),
    ]


def test_first_account_risk_baseline_rechecks_open_orders_at_commit(factory):
    api = factory()
    venue_results = iter([[], [response()]])
    api._backend.open_order_results[VENUE] = lambda: next(venue_results)

    snapshot = api.get_account_risk_snapshot(initialize_baseline=True)

    assert snapshot["baseline_equity_by_venue"] is None
    assert snapshot["evidence_complete"] is False
    assert snapshot["durable"] is False
    assert snapshot["trading_blocked"] is True
    assert (
        snapshot["evidence_errors"][f"{VENUE}:baseline_commit_open_orders"]
        == "open_orders_present"
    )
    assert not api._execution_session.risk_path.exists()
    assert api._backend.open_orders_queried == [
        BINANCE_VENUE,
        VENUE,
        BINANCE_VENUE,
        VENUE,
    ]


@pytest.mark.parametrize(
    "open_orders,error_code",
    [
        ([response()], "open_orders_present"),
        (RuntimeError("offline"), "RuntimeError"),
        (None, "ValueError"),
    ],
)
def test_first_account_risk_baseline_fails_closed_without_proven_empty_remote_orders(
    factory, open_orders, error_code
):
    api = factory()
    api._backend.open_order_results[VENUE] = open_orders

    snapshot = api.get_account_risk_snapshot(initialize_baseline=True)

    assert snapshot["baseline_equity"] is None
    assert snapshot["durable"] is False
    assert snapshot["trading_blocked"] is True
    assert snapshot["evidence_errors"][f"{VENUE}:open_orders"] == error_code
    assert api._backend.open_orders_queried == [BINANCE_VENUE, VENUE]


def test_first_account_risk_baseline_fails_closed_when_open_order_api_is_missing(
    factory,
):
    api = factory()
    api._backend.get_open_orders = None

    snapshot = api.get_account_risk_snapshot(initialize_baseline=True)

    assert snapshot["baseline_equity"] is None
    assert snapshot["durable"] is False
    assert snapshot["trading_blocked"] is True
    assert {
        f"{BINANCE_VENUE}:open_orders",
        f"{VENUE}:open_orders",
    } < set(snapshot["evidence_errors"])


@pytest.mark.parametrize("local_state", ["active", "unknown"])
def test_first_account_risk_baseline_requires_no_local_active_or_unknown_orders(
    factory, local_state
):
    api = factory()
    if local_state == "active":
        result = api.make_order(VENUE, request(), normalized=True)
        assert result["terminal_confirmed"] is False
    else:
        api._backend.place_result = RuntimeError("timeout")
        result = api.make_order(VENUE, request(), normalized=True)
        assert result["execution_unknown"] is True

    snapshot = api.get_account_risk_snapshot(initialize_baseline=True)

    assert snapshot["baseline_equity"] is None
    assert snapshot["durable"] is False
    assert snapshot["trading_blocked"] is True
    assert snapshot["evidence_errors"]["baseline"] == "unsafe_baseline_initialization"
    assert api._backend.open_orders_queried == [BINANCE_VENUE, VENUE]


def test_account_risk_never_initializes_from_nonflat_or_incomplete_evidence(factory):
    api = factory()
    api._backend.position_results[VENUE] = [
        {"instId": SYMBOL, "pos": "1", "posSide": "long"}
    ]

    nonflat = api.get_account_risk_snapshot(initialize_baseline=True)
    assert nonflat["evidence_complete"] is False
    assert nonflat["durable"] is False
    assert nonflat["trading_blocked"] is True
    assert "unsafe_baseline_initialization" in nonflat["evidence_errors"].values()

    api._backend.position_results[VENUE] = []
    api._backend.account_results[BINANCE_VENUE] = RuntimeError("offline")
    incomplete = api.get_account_risk_snapshot(initialize_baseline=True)
    assert incomplete["evidence_complete"] is False
    assert incomplete["durable"] is False
    assert incomplete["baseline_equity"] is None


def test_account_risk_equity_never_round_trips_through_binary_float(factory):
    api = factory()
    api._backend.account_results[VENUE] = {
        "details": [
            {
                "ccy": "USDT",
                "availEq": "100.0000000000000000000000000000000000000001",
                "eq": "100.0000000000000000000000000000000000000001",
            }
        ]
    }
    api._backend.account_results[BINANCE_VENUE] = {
        "assets": [
            {
                "asset": "USDT",
                "availableBalance": "200",
                "walletBalance": "200",
                "unrealizedProfit": "0",
            }
        ]
    }
    snapshot = api.get_account_risk_snapshot(initialize_baseline=True)
    expected = "300.0000000000000000000000000000000000000001"
    assert snapshot["current_equity"] == expected
    assert snapshot["baseline_equity"] == expected


def test_account_maximum_loss_boundary_uses_exact_decimal_comparison(factory):
    api = factory(config={"account_maximum_loss_bps": "1"})
    assert (
        api.get_account_risk_snapshot(initialize_baseline=True)["loss_limit_breached"]
        is False
    )

    just_inside = "99.9700000000000000000000000000000000000001"
    api._backend.account_results[VENUE]["details"][0].update(
        availEq=just_inside, eq=just_inside
    )
    safe = api.get_account_risk_snapshot()
    assert safe["loss_limit_breached"] is False
    assert Decimal(safe["loss_amount"]) < Decimal(safe["loss_limit_amount"])

    just_outside = "99.9699999999999999999999999999999999999999"
    api._backend.account_results[VENUE]["details"][0].update(
        availEq=just_outside, eq=just_outside
    )
    breached = api.get_account_risk_snapshot()
    assert breached["loss_limit_breached"] is True
    assert Decimal(breached["loss_amount"]) > Decimal(breached["loss_limit_amount"])


def test_account_maximum_loss_config_preserves_all_decimal_digits(factory):
    limit = "0.123456789012345678901234567890123456789"

    api = factory(config={"account_maximum_loss_bps": limit})

    assert api._execution_session.config["account_maximum_loss_bps"] == limit


def test_account_maximum_loss_requires_durable_transition_journal(factory):
    with pytest.raises(NormalizedApiError, match="invalid_execution_config"):
        factory(
            config={
                "account_maximum_loss_bps": "100",
                "require_order_journal": False,
            }
        )


def test_account_maximum_loss_latches_across_recovery_restart_and_requires_safe_reset(
    factory, tmp_path
):
    path = tmp_path / "loss-latch-orders.jsonl"
    config = {"account_maximum_loss_bps": "100"}
    first = factory(path, config=config)

    assert first.get_execution_summary()["trading_blocked"] is True
    assert (
        "account_risk_baseline_required"
        in first.get_execution_summary()["evidence_errors"]
    )
    with pytest.raises(NormalizedApiError, match="account_risk_baseline_required"):
        first.make_order(VENUE, request(), normalized=True)

    baseline = first.get_account_risk_snapshot(initialize_baseline=True)
    assert baseline["loss_limit_bps"] == "100"
    assert baseline["loss_limit_breached"] is False
    assert baseline["loss_amount"] == "0"
    assert baseline["loss_limit_amount"] == "3"

    first._backend.account_results[VENUE]["details"][0].update(availEq="90", eq="90")
    breached = first.get_account_risk_snapshot()
    assert breached["durable"] is True
    assert breached["trading_blocked"] is True
    assert breached["loss_limit_breached"] is True
    assert Decimal(breached["loss_bps_observed"]) > Decimal("300")
    assert breached["loss_breached_at"] > 0
    with pytest.raises(NormalizedApiError, match="account_maximum_loss_breached"):
        first.make_order(VENUE, request(), normalized=True)

    first._backend.account_results[VENUE]["details"][0].update(availEq="100", eq="100")
    recovered_equity = first.get_account_risk_snapshot()
    assert recovered_equity["loss_amount"] == "0"
    assert recovered_equity["loss_limit_breached"] is True
    assert recovered_equity["loss_breached_at"] == breached["loss_breached_at"]
    first.close()

    second = factory(path, config=config)
    reopened = second.get_account_risk_snapshot()
    assert reopened["loss_limit_breached"] is True
    assert reopened["trading_blocked"] is True

    reset = second.reset_account_maximum_loss_latch()
    assert reset["durable"] is True
    assert reset["trading_blocked"] is False
    assert reset["loss_limit_breached"] is False
    assert reset["baseline_equity"] == reset["current_equity"] == "300"
    assert reset["peak_loss_bps"] == "0"
    assert second.make_order(VENUE, request(), normalized=True)["status"] == "accepted"


def test_incomplete_refresh_never_reports_or_clears_a_prior_loss_latch(factory):
    api = factory(config={"account_maximum_loss_bps": "100"})
    assert api.get_account_risk_snapshot(initialize_baseline=True)["durable"] is True
    api._backend.account_results[VENUE]["details"][0].update(availEq="90", eq="90")
    breached = api.get_account_risk_snapshot()
    api._backend.account_results[VENUE] = RuntimeError("offline")

    incomplete = api.get_account_risk_snapshot()

    assert incomplete["loss_limit_breached"] is True
    assert incomplete["loss_breached_at"] == breached["loss_breached_at"]
    assert incomplete["peak_loss_bps"] == breached["peak_loss_bps"]
    assert api.get_execution_summary()["loss_limit_breached"] is True


def test_account_maximum_loss_reset_fails_closed_until_remote_state_is_flat_and_empty(
    factory,
):
    api = factory(config={"account_maximum_loss_bps": "50"})
    assert (
        api.get_account_risk_snapshot(initialize_baseline=True)["loss_limit_breached"]
        is False
    )
    api._backend.account_results[VENUE]["details"][0].update(availEq="90", eq="90")
    breached = api.get_account_risk_snapshot()
    assert breached["loss_limit_breached"] is True
    persisted = api._execution_session.risk_path.read_text()

    api._backend.position_results[VENUE] = [
        {"instId": SYMBOL, "pos": "1", "posSide": "long"}
    ]
    nonflat = api.reset_account_maximum_loss_latch()
    assert nonflat["loss_limit_breached"] is True
    assert nonflat["trading_blocked"] is True
    assert nonflat["evidence_errors"]["loss_reset"] == "unsafe_account_loss_reset"
    assert api._execution_session.risk_path.read_text() == persisted

    api._backend.position_results[VENUE] = []
    api._backend.open_order_results[VENUE] = [response()]
    open_order = api.reset_account_maximum_loss_latch()
    assert open_order["loss_limit_breached"] is True
    assert open_order["trading_blocked"] is True
    assert (
        open_order["evidence_errors"][f"{VENUE}:open_orders"] == "open_orders_present"
    )
    assert api._execution_session.risk_path.read_text() == persisted


def test_breach_during_active_order_is_persisted_and_remains_blocked_after_restart(
    factory, tmp_path
):
    path = tmp_path / "active-loss-latch.jsonl"
    config = {"account_maximum_loss_bps": "100"}
    first = factory(path, config=config)
    assert (
        first.get_account_risk_snapshot(initialize_baseline=True)["loss_limit_breached"]
        is False
    )
    assert first.make_order(VENUE, request(), normalized=True)["status"] == "accepted"
    first._backend.account_results[VENUE]["details"][0].update(availEq="90", eq="90")

    breached = first.get_account_risk_snapshot()

    assert breached["loss_limit_breached"] is True
    assert breached["durable"] is False
    persisted = json.loads(first._execution_session.risk_path.read_text())
    assert persisted["loss_limit_breached"] is True
    first.data_queues[VENUE].put(
        {
            "kind": "order",
            **response(status="filled", filled=2, avg_price=60000),
        }
    )
    assert first.poll_event(VENUE)["terminal_confirmed"] is True
    with pytest.raises(NormalizedApiError, match="account_maximum_loss_breached"):
        first.make_order(VENUE, request("123456789013"), normalized=True)
    first.close()

    second = factory(path, config=config)
    assert second.get_execution_summary()["loss_limit_breached"] is True
    with pytest.raises(NormalizedApiError, match="account_maximum_loss_breached"):
        second.make_order(VENUE, request("123456789014"), normalized=True)


def test_failed_loss_refresh_blocks_summary_and_placement_until_recovered(factory):
    api = factory(config={"account_maximum_loss_bps": "100"})
    assert api.get_account_risk_snapshot(initialize_baseline=True)["durable"] is True
    healthy = api._backend.account_results[VENUE]
    api._backend.account_results[VENUE] = RuntimeError("offline")

    incomplete = api.get_account_risk_snapshot()

    assert incomplete["trading_blocked"] is True
    summary = api.get_execution_summary()
    assert summary["trading_blocked"] is True
    assert "account_risk_evidence_incomplete" in summary["evidence_errors"]
    with pytest.raises(NormalizedApiError, match="account_risk_evidence_incomplete"):
        api.make_order(VENUE, request(), normalized=True)

    api._backend.account_results[VENUE] = healthy
    assert api.get_account_risk_snapshot()["evidence_complete"] is True
    assert api.make_order(VENUE, request(), normalized=True)["status"] == "accepted"


def test_loss_snapshot_blocks_placement_while_refresh_is_in_progress(factory):
    api = factory(config={"account_maximum_loss_bps": "100"})
    assert api.get_account_risk_snapshot(initialize_baseline=True)["durable"] is True
    api._backend.account_results[VENUE]["details"][0].update(availEq="90", eq="90")
    entered = threading.Event()
    release = threading.Event()
    original_get_account = api._backend.get_account

    def blocking_get_account(venue, **kwargs):
        if venue == BINANCE_VENUE and not entered.is_set():
            entered.set()
            assert release.wait(2)
        return original_get_account(venue, **kwargs)

    api._backend.get_account = blocking_get_account
    risk_thread, risk_done, risk_outcome = start_thread(api.get_account_risk_snapshot)
    assert entered.wait(2)

    summary = api.get_execution_summary()
    assert "account_risk_refresh_in_progress" in summary["evidence_errors"]
    with pytest.raises(NormalizedApiError, match="account_risk_refresh_in_progress"):
        api.make_order(VENUE, request(), normalized=True)
    assert api._backend.placed == []

    release.set()
    assert risk_done.wait(2)
    risk_thread.join(timeout=2)

    assert risk_outcome["result"]["loss_limit_breached"] is True
    with pytest.raises(NormalizedApiError, match="account_maximum_loss_breached"):
        api.make_order(VENUE, request("123456789017"), normalized=True)
    assert api._backend.placed == []


def test_account_risk_snapshot_ttl_blocks_until_a_successful_refresh(factory):
    api = factory(
        config={
            "account_maximum_loss_bps": "100",
            "account_risk_max_age_seconds": "2",
        }
    )
    assert api.get_account_risk_snapshot(initialize_baseline=True)["durable"] is True
    api._execution_session.risk_last_verified_monotonic_ns -= 2_000_000_001

    summary = api.get_execution_summary()
    assert summary["trading_blocked"] is True
    assert "account_risk_snapshot_stale" in summary["evidence_errors"]
    with pytest.raises(NormalizedApiError, match="account_risk_snapshot_stale"):
        api.make_order(VENUE, request(), normalized=True)

    refreshed = api.get_account_risk_snapshot()
    assert refreshed["durable"] is True
    assert (
        "account_risk_snapshot_stale"
        not in api.get_execution_summary()["evidence_errors"]
    )
    assert api.make_order(VENUE, request(), normalized=True)["status"] == "accepted"


def test_reopened_loss_guard_requires_a_current_process_refresh(factory, tmp_path):
    path = tmp_path / "loss-snapshot-restart.jsonl"
    config = {"account_maximum_loss_bps": "100"}
    first = factory(path, config=config)
    assert first.get_account_risk_snapshot(initialize_baseline=True)["durable"] is True
    first.close()

    second = factory(path, config=config)
    summary = second.get_execution_summary()
    assert summary["trading_blocked"] is True
    assert "account_risk_snapshot_refresh_required" in summary["evidence_errors"]
    with pytest.raises(
        NormalizedApiError, match="account_risk_snapshot_refresh_required"
    ):
        second.make_order(VENUE, request(), normalized=True)

    assert second.get_account_risk_snapshot()["durable"] is True
    assert second.make_order(VENUE, request(), normalized=True)["status"] == "accepted"


def test_unexpected_risk_collection_failure_remains_fail_closed(factory, monkeypatch):
    api = factory(config={"account_maximum_loss_bps": "100"})
    assert api.get_account_risk_snapshot(initialize_baseline=True)["durable"] is True
    original_snapshot = api._execution_session.account_risk_snapshot

    def fail_snapshot(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("snapshot assembly failed")

    monkeypatch.setattr(api._execution_session, "account_risk_snapshot", fail_snapshot)
    with pytest.raises(RuntimeError, match="snapshot assembly failed"):
        api.get_account_risk_snapshot()

    summary = api.get_execution_summary()
    assert "account_risk_evidence_incomplete" in summary["evidence_errors"]
    with pytest.raises(NormalizedApiError, match="account_risk_evidence_incomplete"):
        api.make_order(VENUE, request(), normalized=True)

    monkeypatch.setattr(
        api._execution_session, "account_risk_snapshot", original_snapshot
    )
    assert api.get_account_risk_snapshot()["durable"] is True
    assert api.make_order(VENUE, request(), normalized=True)["status"] == "accepted"


def test_uncertain_reset_replace_remains_latched_across_restart(
    factory, monkeypatch, tmp_path
):
    path = tmp_path / "uncertain-loss-reset.jsonl"
    config = {"account_maximum_loss_bps": "100"}
    first = factory(path, config=config)
    assert first.get_account_risk_snapshot(initialize_baseline=True)["durable"] is True
    first._backend.account_results[VENUE]["details"][0].update(availEq="90", eq="90")
    assert first.get_account_risk_snapshot()["loss_limit_breached"] is True
    original_fsync_directory = execution_session_module._fsync_directory

    def fail_directory_fsync(_path):
        raise OSError("directory fsync failed")

    monkeypatch.setattr(
        execution_session_module, "_fsync_directory", fail_directory_fsync
    )
    failed = first.reset_account_maximum_loss_latch()
    assert failed["loss_limit_breached"] is True
    assert failed["trading_blocked"] is True
    monkeypatch.setattr(
        execution_session_module, "_fsync_directory", original_fsync_directory
    )
    first.close()

    second = factory(path, config=config)
    summary = second.get_execution_summary()
    assert summary["loss_limit_breached"] is True
    assert "account_loss_reset_incomplete" in summary["evidence_errors"]
    with pytest.raises(NormalizedApiError, match="account_maximum_loss_breached"):
        second.make_order(VENUE, request("123456789015"), normalized=True)
    reset = second.reset_account_maximum_loss_latch()
    assert reset["loss_limit_breached"] is False
    assert reset["trading_blocked"] is False


def test_risk_json_cannot_clear_a_journaled_breach_without_explicit_reset(
    factory, tmp_path
):
    path = tmp_path / "tampered-loss-latch.jsonl"
    config = {"account_maximum_loss_bps": "100"}
    first = factory(path, config=config)
    assert first.get_account_risk_snapshot(initialize_baseline=True)["durable"] is True
    first._backend.account_results[VENUE]["details"][0].update(availEq="90", eq="90")
    assert first.get_account_risk_snapshot()["loss_limit_breached"] is True
    risk_path = first._execution_session.risk_path
    first.close()
    record = json.loads(risk_path.read_text())
    record.update(
        loss_limit_breached=False,
        loss_breached_at=None,
        loss_amount="0",
        loss_bps_observed="0",
        peak_loss_bps="0",
    )
    risk_path.write_text(json.dumps(record))

    second = factory(path, config=config)
    summary = second.get_execution_summary()
    assert summary["loss_limit_breached"] is True
    assert "account_loss_transition_mismatch" in summary["evidence_errors"]
    with pytest.raises(NormalizedApiError, match="account_maximum_loss_breached"):
        second.make_order(VENUE, request("123456789016"), normalized=True)
    reset = second.reset_account_maximum_loss_latch()
    assert reset["loss_limit_breached"] is False


@pytest.mark.parametrize(
    "maximum_loss_bps",
    [False, True, 0, -1, "NaN", "Infinity", "", object()],
)
def test_execution_config_rejects_invalid_account_maximum_loss(
    factory, maximum_loss_bps
):
    with pytest.raises(NormalizedApiError, match="invalid_execution_config"):
        factory(config={"account_maximum_loss_bps": maximum_loss_bps})


@pytest.mark.parametrize(
    "max_age_seconds",
    [False, True, 0, -1, "0.0000000001", "NaN", "Infinity", "", object()],
)
def test_execution_config_rejects_invalid_account_risk_max_age(
    factory, max_age_seconds
):
    with pytest.raises(NormalizedApiError, match="invalid_execution_config"):
        factory(config={"account_risk_max_age_seconds": max_age_seconds})


def test_persisted_account_loss_limit_cannot_be_silently_changed(factory, tmp_path):
    path = tmp_path / "loss-limit-contract.jsonl"
    first = factory(path, config={"account_maximum_loss_bps": "50"})
    assert first.get_account_risk_snapshot(initialize_baseline=True)["durable"]
    first.close()

    second = factory(path, config={"account_maximum_loss_bps": "100"})
    snapshot = second.get_account_risk_snapshot()
    assert snapshot["durable"] is False
    assert snapshot["trading_blocked"] is True
    assert "unreadable_or_mismatched_account_risk_state" in snapshot["blocked_reasons"]


def test_account_risk_identity_tamper_blocks_execution_after_restart(factory, tmp_path):
    path = tmp_path / "risk-tamper.jsonl"
    first = factory(path)
    assert first.get_account_risk_snapshot(initialize_baseline=True)["durable"]
    risk_path = first._execution_session.risk_path
    first.close()
    record = json.loads(risk_path.read_text())
    record["ledger_identities"][0]["credential_fingerprint"] = "f" * 64
    risk_path.write_text(json.dumps(record))

    second = factory(path)
    result = second.get_account_risk_snapshot()
    assert result["durable"] is False
    assert result["trading_blocked"] is True
    assert "unreadable_or_mismatched_account_risk_state" in result["blocked_reasons"]
    with pytest.raises(NormalizedApiError, match="unresolved_or_undurable_journal"):
        second.make_order(VENUE, request(), normalized=True)


@pytest.mark.parametrize(
    "tamper", ["venue", "currency", "equity", "noncanonical_equity"]
)
def test_account_risk_baseline_contract_tamper_never_loads_or_rewrites(
    factory, tmp_path, tamper
):
    path = tmp_path / f"risk-{tamper}.jsonl"
    first = factory(path)
    assert first.get_account_risk_snapshot(initialize_baseline=True)["durable"]
    risk_path = first._execution_session.risk_path
    first.close()
    record = json.loads(risk_path.read_text())
    if tamper == "venue":
        record["baseline_equity_by_venue"]["OTHER___SWAP"] = record[
            "baseline_equity_by_venue"
        ].pop(VENUE)
    elif tamper == "currency":
        record["baseline_equity_by_venue"][VENUE]["currency"] = "BTC"
    elif tamper == "equity":
        record["baseline_equity_by_venue"][VENUE]["equity"] = "NaN"
    else:
        record["baseline_equity_by_venue"][VENUE]["equity"] = "100.0"
    tampered = json.dumps(record, sort_keys=True)
    risk_path.write_text(tampered)

    second = factory(path)
    snapshot = second.get_account_risk_snapshot()

    assert snapshot["baseline_equity_by_venue"] is None
    assert snapshot["evidence_complete"] is False
    assert snapshot["durable"] is False
    assert snapshot["trading_blocked"] is True
    assert "unreadable_or_mismatched_account_risk_state" in snapshot["blocked_reasons"]
    assert risk_path.read_text() == tampered


def test_malformed_position_row_cannot_prove_account_is_flat(factory):
    api = factory()
    api._backend.position_results[VENUE] = [{}]

    snapshot = api.get_account_risk_snapshot(initialize_baseline=True)

    assert snapshot["baseline_equity_by_venue"] is None
    assert snapshot["evidence_complete"] is False
    assert snapshot["durable"] is False
    assert snapshot["trading_blocked"] is True
    assert VENUE in snapshot["evidence_errors"]


def test_boolean_position_quantity_cannot_prove_account_is_flat(factory):
    api = factory()
    api._backend.position_results[VENUE] = [
        {"instId": SYMBOL, "pos": False, "posSide": "long"}
    ]

    snapshot = api.get_account_risk_snapshot(initialize_baseline=True)

    assert snapshot["baseline_equity_by_venue"] is None
    assert snapshot["evidence_complete"] is False
    assert snapshot["durable"] is False
    assert snapshot["trading_blocked"] is True
    assert VENUE in snapshot["evidence_errors"]


def test_persisted_active_order_blocks_existing_baseline_after_restart(
    factory, tmp_path
):
    path = tmp_path / "risk-active-restart.jsonl"
    first = factory(path)
    assert first.get_account_risk_snapshot(initialize_baseline=True)["durable"]
    assert first.make_order(VENUE, request(), normalized=True)["status"] == "accepted"
    risk_path = first._execution_session.risk_path
    persisted_risk = risk_path.read_text()
    first.close()

    second = factory(path)
    assert second.get_open_orders(BINANCE_VENUE, normalized=True) == []
    assert second.get_open_orders(VENUE, normalized=True) == []
    snapshot = second.get_account_risk_snapshot()

    assert snapshot["baseline_equity"] == "300"
    assert snapshot["current_equity"] == "300"
    assert snapshot["evidence_complete"] is False
    assert snapshot["durable"] is False
    assert snapshot["trading_blocked"] is True
    assert "active_execution" in snapshot["blocked_reasons"]
    assert "execution_unknown" in snapshot["blocked_reasons"]
    assert risk_path.read_text() == persisted_risk


def due(api):
    for state in api._execution_session.orders.values():
        state["next_poll"] = 0


def start_thread(call):
    outcome = {}
    done = threading.Event()

    def run():
        try:
            outcome["result"] = call()
        except Exception as exc:  # pragma: no cover - asserted by the caller
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, done, outcome


@pytest.mark.parametrize(
    "config",
    [
        {"market_data_only": 1},
        {"require_order_journal": "true"},
        {"account_currency": ""},
        {"account_currency": 1},
        {"account_currencies": {VENUE: ""}},
        {"account_currencies": {VENUE: None}},
    ],
)
def test_execution_config_rejects_invalid_flags_and_currencies(factory, config):
    with pytest.raises(NormalizedApiError, match="invalid_execution_config"):
        factory(config=config)


def test_optional_session_keeps_legacy_raw_default(factory):
    api = factory(legacy=True)
    assert api.make_order(VENUE, request()) is api._backend.place_result
    summary = api.get_execution_summary()
    assert summary["session_enabled"] is False
    assert summary["funding_unresolved_orders"] == []
    assert summary["funding_evidence_status"] == "unavailable"
    assert summary["signed_funding_cashflow"] is None
    assert summary["evidence_errors"] == []


@pytest.mark.parametrize("coroutine", [False, True])
def test_legacy_async_none_is_an_explicit_capability_failure(factory, coroutine):
    api = factory(legacy=True)
    if coroutine:

        async def no_result(*_args, **_kwargs):
            return None

    else:

        def no_result(*_args, **_kwargs):
            return None

    api.exchange_feeds[VENUE] = SimpleNamespace(async_get_tick=no_result)
    with pytest.raises(CapabilityNotSupportedError, match="returned no result"):
        asyncio.run(api.async_get_tick(VENUE, SYMBOL))


def test_typed_order_intent_is_fsynced_before_dispatch_and_preserves_units(factory):
    api = factory()
    backend = api._backend
    original = backend.make_order

    def dispatch(venue, req):
        row = json.loads(api._execution_session.path.read_text().splitlines()[0])
        assert (
            row["event"] == "intent" and row["client_order_id"] == req.client_order_id
        )
        assert row["quantity"] == "2" and row["quantity_unit"] == "native"
        assert row["time_in_force"] == "IOC" and row["reduce_only"] is True
        assert "bt_order_ref" not in row and "data_name" not in row
        return original(venue, req)

    backend.make_order = dispatch
    result = api.make_order(VENUE, request(reduce_only=True), normalized=True)
    assert result["status"] == "accepted"
    assert api.get_execution_summary()["submit_calls"] == 1


def test_allocated_ids_are_reserved_not_prematurely_used(factory, monkeypatch):
    api = factory()
    monkeypatch.setattr("bt_api_py._execution_session.time.time_ns", lambda: 123)
    first = api.new_client_order_id(VENUE)
    second = api.new_client_order_id(VENUE)
    assert first != second and len(first) == 12 and first.isdecimal()
    api.make_order(VENUE, request(first), normalized=True)
    assert len(api._backend.placed) == 1


def test_allocated_id_is_durable_before_return_and_survives_restart(
    factory, monkeypatch, tmp_path
):
    path = tmp_path / "reserved.jsonl"
    first = factory(path, config={"strategy_id": "alpha-a"})
    monkeypatch.setattr("bt_api_py._execution_session.time.time_ns", lambda: 123)
    client_id = first.new_client_order_id(VENUE)
    row = json.loads(path.read_text().splitlines()[0])
    assert row["event"] == "client_id_reservation"
    assert row["client_order_id"] == client_id
    assert row["strategy_id"] == "alpha-a"
    identity = first.get_execution_identity(VENUE)
    assert row["ledger_identity"] == {
        key: identity[key]
        for key in ("provider", "environment", "account_id", "credential_fingerprint")
    }
    first_epoch = row["fencing_epoch"]
    first.close()

    second = factory(path, config={"strategy_id": "alpha-a"})
    assert not second.get_execution_summary()["unknown_ids"]
    assert second._execution_session.fencing_epoch == first_epoch + 1
    assert second.new_client_order_id(VENUE) != client_id


def test_request_account_cannot_forge_a_second_ledger(factory, monkeypatch):
    api = factory(config={"strategy_id": "spread-a"})
    monkeypatch.setattr("bt_api_py._execution_session.time.time_ns", lambda: 456)

    api.new_client_order_id(VENUE)
    with pytest.raises(NormalizedApiError, match="authenticated_account_id_mismatch"):
        api.new_client_order_id(VENUE, account_id="forged-account")
    assert len(api._execution_session.path.read_text().splitlines()) == 1


def test_same_account_cannot_open_a_second_journal(factory, tmp_path):
    first = factory(
        tmp_path / "account-primary.jsonl",
        config={"account_ids": {VENUE: "first-label", BINANCE_VENUE: "first-binance"}},
    )
    with pytest.raises(NormalizedApiError, match="journal_conflict|session_locked"):
        factory(
            tmp_path / "account-shadow.jsonl",
            config={
                "account_ids": {
                    VENUE: "spoofed-label",
                    BINANCE_VENUE: "spoofed-binance",
                }
            },
        )
    first.close()


def test_crypto_account_alias_is_nfkc_casefolded_but_never_authoritative(factory):
    api = factory(
        config={"account_ids": {VENUE: "  ＤｅＭｏ－Ａ  ", BINANCE_VENUE: "binance"}}
    )
    result = api.make_order(
        VENUE,
        request(account_id="demo-a"),
        normalized=True,
    )
    assert result["status"] == "accepted"
    identity = api.get_execution_identity(VENUE)
    assert identity["account_id"].startswith("okx-credential-")
    assert "demo" not in identity["account_id"]


def test_sdk_default_journal_is_shared_across_strategy_partitions(factory):
    first = factory(config={"order_journal": None, "strategy_id": "midfreq"})
    first_path = first.get_execution_identity(VENUE)["journal_path"]
    first.close()
    second = factory(config={"order_journal": None, "strategy_id": "event-driven"})
    assert second.get_execution_identity(VENUE)["journal_path"] == first_path


def test_forked_process_identity_cannot_write_parent_lease(factory, monkeypatch):
    api = factory()
    before = (
        api._execution_session.path.read_bytes()
        if api._execution_session.path.exists()
        else b""
    )
    monkeypatch.setattr(
        "bt_api_py._execution_session.os.getpid",
        lambda: api._execution_session.owner_pid + 1,
    )
    with pytest.raises(NormalizedApiError, match="execution_session_forked_process"):
        api.new_client_order_id(VENUE)
    after = (
        api._execution_session.path.read_bytes()
        if api._execution_session.path.exists()
        else b""
    )
    assert after == before


def test_legacy_journal_migration_claims_and_quarantines_before_atomic_cutover(
    tmp_path,
):
    source = tmp_path / "legacy.jsonl"
    destination = tmp_path / "claimed.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "intent",
                        "exchange_name": VENUE,
                        "symbol": SYMBOL,
                        "client_order_id": "claimed",
                        "account_id": "legacy",
                    }
                ),
                json.dumps(
                    {
                        "event": "intent",
                        "exchange_name": VENUE,
                        "symbol": SYMBOL,
                        "client_order_id": "ambiguous",
                    }
                ),
            ]
        )
        + "\n"
    )

    report = migrate_execution_journal(
        source,
        destination,
        {
            "claimed": {
                "provider": "OKX",
                "environment": "demo",
                "account_id": "demo-a",
                "strategy_id": "spread-a",
                "credential_fingerprint": MIGRATION_FINGERPRINT,
            }
        },
    )

    assert report["status"] == "BLOCKED"
    assert report["reason"] == "quarantined_records"
    assert report["migrated_records"] == 1
    assert report["quarantined_records"] == 1
    assert report["lock_copied"] is False
    assert not destination.exists()
    assert not destination.with_suffix(destination.suffix + ".lock").exists()
    quarantine = destination.with_suffix(destination.suffix + ".quarantine")
    assert "unclaimed_identity" in quarantine.read_text()
    assert (
        json.loads((tmp_path / "legacy.jsonl.freeze").read_text())["status"]
        == "BLOCKED"
    )


def test_journal_migration_requires_remote_reconcile_and_atomically_cuts_over(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        execution_session_module,
        "_ledger_registry_root",
        lambda: tmp_path / "execution-ledgers",
    )
    source = tmp_path / "legacy-clean.jsonl"
    blocked_destination = tmp_path / "blocked.jsonl"
    destination = tmp_path / "claimed-clean.jsonl"
    source.write_text(
        json.dumps(
            {
                "event": "intent",
                "exchange_name": VENUE,
                "symbol": SYMBOL,
                "client_order_id": "claimed",
                "account_id": "legacy",
            }
        )
        + "\n"
    )
    claims = {
        "claimed": {
            "provider": "OKX",
            "environment": "demo",
            "account_id": "demo-a",
            "strategy_id": "spread-a",
            "credential_fingerprint": MIGRATION_FINGERPRINT,
        }
    }

    blocked = migrate_execution_journal(source, blocked_destination, claims)
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason"] == "remote_reconcile_required"
    assert not blocked_destination.exists()

    reconciled = []

    def reconcile(**manifest):
        reconciled.append(manifest)
        return {"verified": True, "unknown_ids": [], **manifest}

    report = migrate_execution_journal(
        source,
        destination,
        claims,
        remote_reconcile=reconcile,
    )
    assert report["status"] == "COMPLETE"
    assert report["destination_epoch"] > report["source_epoch"]
    assert Path(report["sealed_source"]).exists()
    assert json.loads(source.read_text())["event"] == "cutover_tombstone"
    migrated = json.loads(destination.read_text().strip())
    assert migrated["ledger_identity"] == {
        "provider": "OKX",
        "environment": "demo",
        "account_id": f"okx-credential-{MIGRATION_FINGERPRINT}",
        "credential_fingerprint": MIGRATION_FINGERPRINT,
    }
    assert migrated["strategy_id"] == "spread-a"
    assert migrated["source_hash"] == report["source_hash"]
    assert migrated["fencing_epoch"] == report["destination_epoch"]
    assert reconciled[0]["source_hash"] == report["source_hash"]
    reopened = BtApi(debug=False)
    reopened.configure_execution(
        {
            "order_journal": destination,
            "account_ids": {VENUE: "any-local-alias"},
            "required_environments": {VENUE: "demo"},
        },
        _exchange_names={VENUE: credential_settings(VENUE)},
    )
    try:
        assert (
            reopened.get_execution_identity(VENUE)["account_id"]
            == migrated["account_id"]
        )
    finally:
        reopened.close()
    with pytest.raises(NormalizedApiError, match="journal_frozen_for_cutover"):
        BtApi(
            {VENUE: credential_settings(VENUE)},
            debug=False,
            execution_config={
                "order_journal": source,
                "account_ids": {VENUE: "demo-a"},
                "required_environments": {VENUE: "demo"},
            },
        )


def test_migration_detects_raw_concurrent_append_after_reconcile(monkeypatch, tmp_path):
    monkeypatch.setattr(
        execution_session_module,
        "_ledger_registry_root",
        lambda: tmp_path / "execution-ledgers",
    )
    source = tmp_path / "legacy-race.jsonl"
    destination = tmp_path / "migrated-race.jsonl"
    source.write_text(
        json.dumps(
            {
                "event": "intent",
                "exchange_name": VENUE,
                "symbol": SYMBOL,
                "client_order_id": "race-order",
            }
        )
        + "\n"
    )
    claims = {
        "race-order": {
            "provider": "OKX",
            "environment": "demo",
            "account_id": "ignored-alias",
            "strategy_id": "spread-a",
            "credential_fingerprint": MIGRATION_FINGERPRINT,
        }
    }

    def reconcile(**manifest):
        with source.open("a") as stream:
            stream.write(
                json.dumps({"event": "intent", "client_order_id": "late"}) + "\n"
            )
        return {"verified": True, "unknown_ids": [], **manifest}

    report = migrate_execution_journal(
        source, destination, claims, remote_reconcile=reconcile
    )
    assert report["status"] == "BLOCKED"
    assert report["reason"] == "source_changed_while_frozen"
    assert not destination.exists()


def test_prepared_migration_recovers_after_crash_and_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(
        execution_session_module,
        "_ledger_registry_root",
        lambda: tmp_path / "execution-ledgers",
    )
    source = tmp_path / "legacy-crash.jsonl"
    destination = tmp_path / "migrated-crash.jsonl"
    source.write_text(
        json.dumps(
            {
                "event": "intent",
                "exchange_name": VENUE,
                "symbol": SYMBOL,
                "client_order_id": "crash-order",
            }
        )
        + "\n"
    )
    claims = {
        "crash-order": {
            "provider": "OKX",
            "environment": "demo",
            "account_id": "ignored-alias",
            "strategy_id": "spread-a",
            "credential_fingerprint": MIGRATION_FINGERPRINT,
        }
    }

    def reconcile(**manifest):
        return {"verified": True, "unknown_ids": [], **manifest}

    class SimulatedCrash(BaseException):
        pass

    original_publish = execution_session_module._publish_no_replace
    monkeypatch.setattr(
        execution_session_module,
        "_publish_no_replace",
        lambda *_args: (_ for _ in ()).throw(SimulatedCrash()),
    )
    with pytest.raises(SimulatedCrash):
        migrate_execution_journal(
            source, destination, claims, remote_reconcile=reconcile
        )
    transaction = destination.with_suffix(
        destination.suffix + ".cutover.transaction.json"
    )
    assert json.loads(transaction.read_text())["status"] == "PREPARED"

    monkeypatch.setattr(
        execution_session_module, "_publish_no_replace", original_publish
    )
    report = migrate_execution_journal(
        source, destination, claims, remote_reconcile=reconcile
    )
    assert report["status"] == "COMPLETE"
    assert destination.is_file()
    assert json.loads(transaction.read_text())["status"] == "COMMITTED"
    recovered = migrate_execution_journal(
        source, destination, claims, remote_reconcile=reconcile
    )
    assert recovered["status"] == "COMPLETE"


@pytest.mark.parametrize(
    "operation", ["make_order", "cancel_order", "cancel_all", "get_request_api"]
)
def test_configured_session_has_no_unmanaged_public_write_escape(factory, operation):
    api = factory()
    args = (
        (VENUE,)
        if operation in {"cancel_all", "get_request_api"}
        else (VENUE, request())
    )
    with pytest.raises((NormalizedApiError, CapabilityNotSupportedError)):
        getattr(api, operation)(*args)
    assert not api._backend.placed and not api._backend.canceled


@pytest.mark.parametrize(
    "operation", ["async_make_order", "async_cancel_order", "async_cancel_all"]
)
def test_configured_session_rejects_unmanaged_async_writes(factory, operation):
    with pytest.raises((CapabilityNotSupportedError, NormalizedApiError)):
        asyncio.run(getattr(factory(), operation)(VENUE, request()))


def test_normalized_async_execution_reuses_sync_session_and_is_nonblocking(factory):
    api = factory()
    entered = threading.Event()
    released = threading.Event()

    def slow_place(venue, req):
        api._backend.placed.append((venue, req))
        entered.set()
        if not released.wait(5):
            raise TimeoutError("test dispatch was not released")
        return response(client_order_id=req.client_order_id)

    api._backend.make_order = slow_place

    async def scenario():
        task = asyncio.create_task(
            api.async_make_order(VENUE, request(), normalized=True)
        )
        while not entered.is_set():
            await asyncio.sleep(0)
        # The event loop continues while the synchronous venue adapter owns
        # the request in its worker thread.
        await asyncio.sleep(0)
        assert not task.done()
        released.set()
        return await task

    result = asyncio.run(scenario())
    assert result["status"] == "accepted"
    rows = [
        json.loads(line)
        for line in api._execution_session.path.read_text().splitlines()
    ]
    assert rows[0]["event"] == "intent"
    assert rows[0]["schema_version"] == 2
    assert rows[0]["owner_token"]
    assert rows[0]["fencing_epoch"] >= 1
    identity = api.get_execution_identity(VENUE)
    assert rows[0]["ledger_identity"] == {
        key: identity[key]
        for key in ("provider", "environment", "account_id", "credential_fingerprint")
    }


def test_async_order_cancellation_persists_unknown_before_reraising(factory):
    api = factory()

    async def scenario():
        entered = asyncio.Event()
        never = asyncio.Event()

        async def place(_venue, _request):
            entered.set()
            await never.wait()

        api._backend.async_make_order = place
        task = asyncio.create_task(
            api.async_make_order(VENUE, request(), normalized=True)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    rows = [
        json.loads(line)
        for line in api._execution_session.path.read_text().splitlines()
    ]
    assert [row["event"] for row in rows] == ["intent", "order_update"]
    assert rows[-1]["execution_unknown"] is True
    assert rows[-1]["error_code"] == "CancelledError"
    assert api.get_execution_summary()["trading_blocked"] is True


def test_async_cancel_cancellation_persists_unknown_before_reraising(factory):
    api = factory()
    assert api.make_order(VENUE, request(), normalized=True)["status"] == "accepted"

    async def scenario():
        entered = asyncio.Event()
        never = asyncio.Event()

        async def cancel(_venue, _request):
            entered.set()
            await never.wait()

        api._backend.async_cancel_order = cancel
        cancel_request = CancelOrderRequest(
            symbol=SYMBOL,
            account_id=VENUE,
            client_order_id=request().client_order_id,
        )
        task = asyncio.create_task(
            api.async_cancel_order(VENUE, cancel_request, normalized=True)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    rows = [
        json.loads(line)
        for line in api._execution_session.path.read_text().splitlines()
    ]
    assert [row["event"] for row in rows[-2:]] == ["cancel_intent", "order_update"]
    assert rows[-1]["execution_unknown"] is True
    assert rows[-1]["terminal_confirmed"] is False
    assert rows[-1]["error_code"] == "CancelledError"
    assert api.get_execution_summary()["trading_blocked"] is True


def test_normalized_async_execution_awaits_real_backend_coroutines(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)

    class CoroutineFeed:
        def __init__(self):
            self.calls = []

        async def async_make_order(self, *args, **kwargs):
            await asyncio.sleep(0)
            self.calls.append(("make", args, kwargs))
            return {
                "orderId": "native-1",
                "clientOrderId": kwargs["client_order_id"],
                "symbol": args[0],
                "status": "NEW",
            }

        async def async_query_order(self, *args, **kwargs):
            await asyncio.sleep(0)
            self.calls.append(("query", args, kwargs))
            return {
                "orderId": "native-1",
                "clientOrderId": kwargs["client_order_id"],
                "symbol": args[0],
                "status": "NEW",
            }

        async def async_cancel_order(self, *args, **kwargs):
            await asyncio.sleep(0)
            self.calls.append(("cancel", args, kwargs))
            return {
                "orderId": "native-1",
                "clientOrderId": kwargs["client_order_id"],
                "symbol": args[0],
                "status": "CANCELED",
            }

        def make_order(self, *_args, **_kwargs):
            raise AssertionError("sync order path must not run")

        def query_order(self, *_args, **_kwargs):
            raise AssertionError("sync query path must not run")

        def cancel_order(self, *_args, **_kwargs):
            raise AssertionError("sync cancel path must not run")

        def disconnect(self):
            return None

        @staticmethod
        def get_environment_info():
            return {"environment": "demo", "simulated": True, "verified": True}

    venue = "BINANCE___SWAP"
    symbol = "BTCUSDT"
    client_id = "987654321012"
    feed = CoroutineFeed()
    api = BtApi(debug=False)
    api.configure_execution(
        {
            "order_journal": tmp_path / "async-native.jsonl",
            "account_currency": "USDT",
            "account_ids": {venue: "demo-binance"},
            "required_environments": {venue: "demo"},
        },
        _exchange_names={venue: credential_settings(venue)},
    )
    api.exchange_feeds[venue] = feed
    api.exchange_kwargs[venue] = {}
    order_request = OrderRequest(
        symbol=symbol,
        account_id="demo-binance",
        client_order_id=client_id,
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.002"),
        price=Decimal("60000"),
        quantity_unit="base",
        position_mode="dual_side",
        position_side="long",
        offset="close",
        reduce_only=True,
    )
    query = QueryOrderRequest(
        symbol=symbol,
        account_id="demo-binance",
        client_order_id=client_id,
    )
    cancel = CancelOrderRequest(
        symbol=symbol,
        account_id="demo-binance",
        client_order_id=client_id,
    )
    try:
        assert (
            asyncio.run(api.async_make_order(venue, order_request, normalized=True))[
                "status"
            ]
            == "accepted"
        )
        assert (
            asyncio.run(api.async_query_order(venue, query, normalized=True))["status"]
            == "accepted"
        )
        assert (
            asyncio.run(api.async_cancel_order(venue, cancel, normalized=True))[
                "status"
            ]
            == "canceled"
        )
    finally:
        api.close()

    assert [call[0] for call in feed.calls] == ["make", "query", "cancel"]
    make_options = feed.calls[0][2]
    assert make_options["position_side"] == "LONG"
    assert "reduce_only" not in make_options
    assert api.get_execution_summary()["session_enabled"] is True


def test_async_query_and_cancel_remain_available_while_new_placements_are_blocked(
    factory,
):
    api = factory()
    api._backend.place_result = TimeoutError()
    first = asyncio.run(api.async_make_order(VENUE, request(), normalized=True))
    assert first["execution_unknown"]
    with pytest.raises(NormalizedApiError, match="unresolved_or_undurable_journal"):
        asyncio.run(api.async_make_order(VENUE, request("another"), normalized=True))

    query = QueryOrderRequest(
        symbol=SYMBOL,
        account_id=VENUE,
        client_order_id=request().client_order_id,
    )
    assert (
        asyncio.run(api.async_query_order(VENUE, query, normalized=True))["status"]
        == "accepted"
    )
    cancel = CancelOrderRequest(
        symbol=SYMBOL,
        account_id=VENUE,
        client_order_id=request().client_order_id,
    )
    result = asyncio.run(api.async_cancel_order(VENUE, cancel, normalized=True))
    assert result["execution_unknown"]


def test_missing_journal_uses_sdk_canonical_identity_path(factory):
    api = factory(config={"order_journal": None})
    assert api._execution_session.path is not None
    assert api._execution_session.path.parent.name == "execution-journals"
    api.make_order(VENUE, request(), normalized=True)
    assert api._backend.placed


def test_wal_failure_before_send_never_dispatches(factory, monkeypatch):
    api = factory()
    monkeypatch.setattr(
        "bt_api_py._execution_session.os.fsync",
        Mock(side_effect=OSError("private URL")),
    )
    with pytest.raises(NormalizedApiError, match="persistence_failed") as error:
        api.make_order(VENUE, request(), normalized=True)
    assert "private URL" not in str(error.value)
    assert not api._backend.placed and api.get_execution_summary()["trading_blocked"]


def test_wal_failure_after_send_keeps_unknown_and_blocks_followup(factory, monkeypatch):
    api = factory()
    file_calls = 0
    original = os.fsync

    def fsync(fd):
        nonlocal file_calls
        if stat.S_ISREG(os.fstat(fd).st_mode):
            file_calls += 1
            if file_calls >= 2:
                raise OSError("disk full")
        original(fd)

    monkeypatch.setattr("bt_api_py._execution_session.os.fsync", fsync)
    result = api.make_order(VENUE, request(), normalized=True)
    assert result["execution_unknown"] and result["journal_error"]
    with pytest.raises(NormalizedApiError, match="unresolved_or_undurable_journal"):
        api.make_order(VENUE, request("different"), normalized=True)
    assert len(api._backend.placed) == 1


def test_persistence_failure_still_dispatches_degraded_emergency_cancel(
    factory, monkeypatch
):
    api = factory()
    file_calls = 0
    original = os.fsync

    def fsync(fd):
        nonlocal file_calls
        if stat.S_ISREG(os.fstat(fd).st_mode):
            file_calls += 1
            if file_calls >= 2:
                raise OSError("disk full")
        original(fd)

    monkeypatch.setattr("bt_api_py._execution_session.os.fsync", fsync)
    placed = api.make_order(VENUE, request(), normalized=True)
    assert placed["execution_unknown"] and placed["journal_error"]
    api._backend.cancel_result = response(
        status="canceled",
        filled=0,
        terminal_confirmed=True,
    )

    canceled = api.cancel_order(
        VENUE,
        CancelOrderRequest(
            symbol=SYMBOL,
            account_id=VENUE,
            client_order_id=request().client_order_id,
        ),
        normalized=True,
    )

    assert len(api._backend.canceled) == 1
    assert canceled["emergency_cancel"] and canceled["journal_degraded"]
    assert canceled["execution_unknown"] and canceled["journal_error"]
    summary = api.get_execution_summary()
    assert summary["cancel_calls"] == 1 and summary["trading_blocked"]
    with pytest.raises(NormalizedApiError, match="unresolved_or_undurable_journal"):
        api.make_order(VENUE, request("different"), normalized=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync contract")
def test_first_journal_creation_fsyncs_file_before_parent_directory(
    factory, monkeypatch
):
    api = factory()
    calls = []
    original = os.fsync

    def fsync(fd):
        calls.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        original(fd)

    monkeypatch.setattr("bt_api_py._execution_session.os.fsync", fsync)
    api.make_order(VENUE, request(), normalized=True)

    assert calls[:2] == ["file", "directory"]
    assert calls.count("directory") == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync contract")
def test_parent_directory_fsync_failure_prevents_dispatch(factory, monkeypatch):
    api = factory()
    original = os.fsync

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory flush failed")
        original(fd)

    monkeypatch.setattr("bt_api_py._execution_session.os.fsync", fsync)
    with pytest.raises(NormalizedApiError, match="persistence_failed"):
        api.make_order(VENUE, request(), normalized=True)

    assert not api._backend.placed
    assert api.get_execution_summary()["trading_blocked"]


@pytest.mark.parametrize(
    "error", [TimeoutError("signed URL secret"), RuntimeError("signed URL secret")]
)
def test_unknown_never_resubmits_and_queries_by_client_reference(factory, error):
    api = factory()
    api._backend.place_result = error
    result = api.make_order(VENUE, request(), normalized=True)
    assert result["execution_unknown"] and not result["terminal_confirmed"]
    assert "signed URL" not in str(result)
    with pytest.raises(NormalizedApiError, match="unresolved_or_undurable_journal"):
        api.make_order(VENUE, request("new"), normalized=True)
    api._backend.query_result = response(status="filled", filled=2, avg_price=60001)
    due(api)
    update = api.poll_event(VENUE)
    assert update["terminal_confirmed"] and update["avg_price"] == 60001
    assert len(api._backend.placed) == 1
    assert api._backend.queried[0][1].client_order_id == request().client_order_id
    assert api._backend.queried[0][1].order_id is None
    assert not api.get_execution_summary()["unknown_ids"]


@pytest.mark.parametrize(
    "result",
    [
        InvalidOrderError(VENUE, SYMBOL, "invalid lots"),
        response(status="rejected"),
    ],
)
def test_only_explicit_rejection_is_terminal(factory, result):
    api = factory()
    api._backend.place_result = result
    update = api.make_order(VENUE, request(), normalized=True)
    assert update["status"] == "rejected" and update["terminal_confirmed"]
    assert not api.get_execution_summary()["unknown_ids"]


def test_unrecognized_numeric_venue_error_remains_unknown(factory):
    api = factory()
    api._backend.place_result = {"code": "50120", "msg": "private native message"}

    update = api.make_order(VENUE, request(), normalized=True)

    assert update["execution_unknown"] and not update["terminal_confirmed"]
    assert api.get_execution_summary()["unknown_ids"] == [request().client_order_id]


def test_cancel_ack_without_status_remains_unknown_until_query(factory):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    result = api.cancel_order(
        VENUE,
        CancelOrderRequest(
            symbol=SYMBOL,
            account_id=VENUE,
            client_order_id=request().client_order_id,
        ),
        normalized=True,
    )
    assert result["execution_unknown"] and not result["terminal_confirmed"]
    assert [
        json.loads(x)["event"]
        for x in api._execution_session.path.read_text().splitlines()
    ] == ["intent", "order_update", "cancel_intent", "order_update"]


@pytest.mark.parametrize("average", [0, -1, None, float("nan")])
def test_filled_without_real_average_cannot_be_terminal(factory, average):
    api = factory()
    api._backend.place_result = response(status="canceled", filled=1, avg_price=average)
    result = api.make_order(VENUE, request(), normalized=True)
    assert result["execution_unknown"] and not result["terminal_confirmed"]


def test_confirmed_fill_and_fee_survive_older_market_order_reports(factory):
    api = factory()
    api._backend.place_result = response(
        status="filled",
        filled=2,
        avg_price=60001,
        cumulative_commission=0.8,
        commission_currency="USDT",
    )
    first = api.make_order(VENUE, request(), normalized=True)
    for row in [
        response(status="accepted"),
        response(status="filled", filled=2, avg_price=60001),
    ]:
        api.data_queues[VENUE].put({"kind": "order", **row})
        update = api.poll_event(VENUE)
        assert update["terminal_confirmed"] and update["filled"] == 2
        assert update["cumulative_commission"] == first["cumulative_commission"] == 0.8


def test_blocked_submit_allows_other_venue_event_and_reduce_only_order(factory):
    api = factory()
    other_venue = "BINANCE___SWAP"
    other_symbol = "BTCUSDT"
    api.exchange_feeds[other_venue] = SimpleNamespace(
        disconnect=lambda: None,
        get_environment_info=lambda: {
            "environment": "demo",
            "simulated": True,
            "verified": True,
        },
    )
    api.exchange_kwargs[other_venue] = {}
    api.data_queues[other_venue] = Queue()
    started = threading.Event()
    release = threading.Event()

    def place_order(venue, req):
        api._backend.placed.append((venue, req))
        if venue == VENUE:
            started.set()
            if not release.wait(5):
                raise TimeoutError("test dispatch was not released")
        return response(
            order_id="123" if venue == VENUE else f"order-{req.client_order_id}",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side.value,
        )

    api._backend.make_order = place_order
    submit_thread, submit_done, submit = start_thread(
        lambda: api.make_order(VENUE, request(), normalized=True)
    )
    event_thread = close_thread = None
    try:
        assert started.wait(2)
        event_thread, event_done, stream = start_thread(
            lambda: api._execution_session.event(
                other_venue,
                {
                    "kind": "order",
                    "symbol": other_symbol,
                    "client_order_id": "stream-order",
                    "order_id": "stream-native",
                    "status": "accepted",
                    "filled": 0,
                    "side": "buy",
                },
            )
        )
        close_request = request(
            "other-close",
            symbol=other_symbol,
            account_id=other_venue,
            side=Side.SELL,
            reduce_only=True,
        )
        close_thread, close_done, close_order = start_thread(
            lambda: api.make_order(other_venue, close_request, normalized=True)
        )

        assert event_done.wait(2), "venue B stream merge waited for venue A REST"
        assert close_done.wait(2), "venue B close order waited for venue A REST"
        assert stream["result"]["status"] == "accepted"
        assert "error" not in close_order, repr(close_order.get("error"))
        assert close_order["result"]["status"] == "accepted"

        in_flight_update = api._execution_session.event(
            VENUE,
            {
                "kind": "order",
                "symbol": SYMBOL,
                "client_order_id": request().client_order_id,
                "order_id": "123",
                "status": "partial",
                "filled": 1,
                "avg_price": 60001,
                "side": "buy",
            },
        )
        assert in_flight_update["filled"] == 1
    finally:
        release.set()
        submit_thread.join(5)
        if event_thread is not None:
            event_thread.join(5)
        if close_thread is not None:
            close_thread.join(5)

    assert submit_done.is_set() and "error" not in submit
    assert submit["result"]["status"] == "partial"
    assert submit["result"]["filled"] == 1


def test_slow_reconciliation_query_does_not_block_event_or_new_placement(factory):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    other_venue = "BINANCE___SWAP"
    other_symbol = "BTCUSDT"
    api.exchange_feeds[other_venue] = SimpleNamespace(
        disconnect=lambda: None,
        get_environment_info=lambda: {
            "environment": "demo",
            "simulated": True,
            "verified": True,
        },
    )
    api.exchange_kwargs[other_venue] = {}
    api.data_queues[other_venue] = Queue()
    started = threading.Event()
    release = threading.Event()

    def query_order(venue, req, **kwargs):
        del kwargs
        api._backend.queried.append((venue, req))
        started.set()
        if not release.wait(5):
            raise TimeoutError("test query was not released")
        return response(client_order_id=req.client_order_id, symbol=req.symbol)

    def place_order(venue, req):
        api._backend.placed.append((venue, req))
        return response(
            order_id=f"order-{req.client_order_id}",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side.value,
        )

    api._backend.query_order = query_order
    api._backend.make_order = place_order
    due(api)
    poll_thread, poll_done, poll = start_thread(lambda: api.poll_event(VENUE))
    event_thread = placement_thread = None
    try:
        assert started.wait(2)
        event_thread, event_done, stream = start_thread(
            lambda: api._execution_session.event(
                VENUE,
                {
                    "kind": "order",
                    "symbol": SYMBOL,
                    "client_order_id": request().client_order_id,
                    "order_id": "123",
                    "status": "partial",
                    "filled": 1,
                    "avg_price": 60001,
                    "side": "buy",
                },
            )
        )
        placement_request = request(
            "other-open",
            symbol=other_symbol,
            account_id=other_venue,
        )
        placement_thread, placement_done, placement = start_thread(
            lambda: api.make_order(other_venue, placement_request, normalized=True)
        )

        assert event_done.wait(2), "stream update waited for the slow REST query"
        assert placement_done.wait(2), "new placement waited for the slow REST query"
        assert stream["result"]["filled"] == 1
        assert "error" not in placement, repr(placement.get("error"))
        assert placement["result"]["status"] == "accepted"
    finally:
        release.set()
        poll_thread.join(5)
        if event_thread is not None:
            event_thread.join(5)
        if placement_thread is not None:
            placement_thread.join(5)

    assert poll_done.is_set() and "error" not in poll
    state = api._execution_session.orders[(VENUE, request().client_order_id)]
    assert state["last_update"]["status"] == "partial"
    assert state["last_update"]["filled"] == 1
    assert len(api._backend.queried) == 1


@pytest.mark.parametrize("currency", ["USDT", "BTC", None])
def test_commission_is_bookable_only_in_account_currency(factory, currency):
    api = factory()
    api._backend.place_result = response(
        status="filled",
        filled=2,
        avg_price=60001,
        cumulative_commission=0.8,
        commission_currency=currency,
    )
    update = api.make_order(VENUE, request(), normalized=True)
    if currency == "USDT":
        assert (
            update["commission_normalized"] and update["cumulative_commission"] == 0.8
        )
    else:
        assert "cumulative_commission" not in update
        assert update["unbooked_cumulative_commission"] == 0.8
        assert api.get_execution_summary()["fee_unresolved_orders"]


@pytest.mark.parametrize("fee", [0.25, -0.25])
def test_trade_fee_cost_or_rebate_has_one_canonical_sign_and_is_deduplicated(
    factory, fee
):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    event = {
        "kind": "trade",
        "symbol": SYMBOL,
        "order_id": "123",
        "trade_id": "T1",
        "side": "buy",
        "size": 1,
        "price": 60001,
        "fee": fee,
        "fee_currency": "USDT",
    }
    api.data_queues[VENUE].put(event)
    api.data_queues[VENUE].put(event)
    result = api.poll_event(VENUE)
    assert result["commission"] == fee and result["commission_normalized"]
    assert "fee" not in result
    assert api.poll_event(VENUE) is None


def test_trade_journal_failure_does_not_hide_real_fill(factory, monkeypatch):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    api.data_queues[VENUE].put(
        {
            "kind": "trade",
            "symbol": SYMBOL,
            "order_id": "123",
            "trade_id": "T1",
            "side": "buy",
            "size": 1,
            "price": 60001,
        }
    )
    monkeypatch.setattr(
        "bt_api_py._execution_session.os.fsync", Mock(side_effect=OSError())
    )
    assert api.poll_event(VENUE)["journal_error"]
    assert api.poll_event(VENUE)["kind"] == "trade"
    assert api.get_execution_summary()["trading_blocked"]


def test_poll_events_delivers_existing_session_pending_before_raw_market(factory):
    api = factory()
    api._execution_session.pending[VENUE].append(
        {
            "kind": "order",
            "symbol": SYMBOL,
            "order_id": "pending-order",
            "client_order_id": "pending-client",
            "status": "accepted",
            "terminal_confirmed": False,
            "execution_unknown": False,
        }
    )
    api.data_queues[VENUE].put(
        {
            "kind": "orderbook",
            "symbol": SYMBOL,
            "bids": [[60000, 1]],
            "asks": [[60001, 1]],
            "sequence": 10,
        }
    )

    events = api.poll_events(
        VENUE,
        max_raw_items=1,
        coalesce_market_snapshots=("orderbook",),
    )

    assert [event["kind"] for event in events] == ["order", "orderbook"]
    assert events[0]["order_id"] == "pending-order"
    assert events[1]["sequence"] == 10


def test_poll_events_keeps_journal_uncertainty_immediately_before_real_fill(
    factory, monkeypatch
):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    api.data_queues[VENUE].put(
        {
            "kind": "trade",
            "symbol": SYMBOL,
            "order_id": "123",
            "trade_id": "T1",
            "side": "buy",
            "size": 1,
            "price": 60001,
        }
    )
    monkeypatch.setattr(
        "bt_api_py._execution_session.os.fsync", Mock(side_effect=OSError())
    )

    events = api.poll_events(VENUE, max_raw_items=1)

    assert [event["kind"] for event in events] == ["order", "trade"]
    assert events[0]["journal_error"] and events[0]["execution_unknown"]
    assert events[1]["trade_id"] == "T1"
    assert api.get_execution_summary()["trading_blocked"]


def test_same_journal_lock_then_restart_reconciles_unknown_and_keeps_used_ids(
    factory, tmp_path
):
    path = tmp_path / "shared.jsonl"
    first = factory(path)
    with pytest.raises(
        NormalizedApiError, match="session_locked|locked_or_unavailable"
    ):
        factory(path)
    first._backend.place_result = TimeoutError()
    first.make_order(VENUE, request(), normalized=True)
    first.close()
    second = factory(path)
    assert second.get_execution_summary()["unknown_ids"] == [request().client_order_id]
    assert second.get_execution_summary()["submit_calls"] == 0
    second._backend.query_result = response(status="canceled", filled=0)
    assert second.poll_event(VENUE)["terminal_confirmed"]
    assert not second.get_execution_summary()["trading_blocked"]
    with pytest.raises(NormalizedApiError, match="duplicate_client_order_id"):
        second.make_order(VENUE, request(), normalized=True)


def test_old_journal_terminal_history_preserves_ids_and_discards_engine_fields(
    factory, tmp_path
):
    path = tmp_path / "old.jsonl"
    identity = {
        "symbol": SYMBOL,
        "exchange_name": VENUE,
        "client_order_id": "old",
        "bt_order_ref": 77,
        "data_name": SYMBOL,
        "size": 2,
    }
    path.write_text(
        json.dumps({"event": "intent", **identity})
        + "\n"
        + json.dumps(
            {
                "event": "order_update",
                **identity,
                "status": "canceled",
                "terminal_confirmed": True,
                "filled": 0,
                "execution_unknown": False,
            }
        )
        + "\n"
    )
    api = factory(path)
    assert not api.get_execution_summary()["trading_blocked"]
    assert "bt_order_ref" not in str(api._execution_session.orders)
    with pytest.raises(NormalizedApiError, match="duplicate_client_order_id"):
        api.make_order(VENUE, request("old"), normalized=True)


def test_order_id_only_cancel_survives_restart_without_inventing_client_id(
    factory, tmp_path
):
    path = tmp_path / "cancel-recovery.jsonl"
    first = factory(path)
    first._backend.cancel_result = TimeoutError()
    first.cancel_order(
        VENUE,
        CancelOrderRequest(symbol=SYMBOL, account_id=VENUE, order_id="native-id"),
        normalized=True,
    )
    assert first.get_execution_summary()["unknown_ids"] == [
        f"{VENUE}:{SYMBOL}::order:native-id"
    ]
    first.close()
    second = factory(path)
    assert second.get_execution_summary()["unknown_ids"] == [
        f"{VENUE}:{SYMBOL}::order:native-id"
    ]
    second._backend.query_result = {
        "symbol": SYMBOL,
        "order_id": "native-id",
        "client_order_id": "later-reported-client",
        "status": "canceled",
        "filled": 0,
    }
    assert second.poll_event(VENUE)["terminal_confirmed"]
    locator = second._backend.queried[-1][1]
    assert locator.order_id == "native-id" and locator.client_order_id is None
    assert not second.get_execution_summary()["trading_blocked"]


def test_failed_journal_load_releases_lock_and_public_only_does_not_lock(
    factory, tmp_path
):
    path = tmp_path / "corrupt.jsonl"
    path.write_text("not json")
    with pytest.raises(NormalizedApiError, match="unreadable_journal"):
        factory(path)
    path.write_text("")
    first = factory(path)
    public = factory(path, config={"market_data_only": True})
    assert public.get_all_balances(normalized=True)[VENUE]["cash"] == 0
    assert public.get_all_positions(normalized=True)[VENUE] == []
    with pytest.raises(NormalizedApiError, match="market_data_only"):
        public.make_order(VENUE, request(), normalized=True)
    first.close()


def test_close_error_releases_lock_and_old_session_cannot_write(factory, tmp_path):
    path = tmp_path / "close.jsonl"
    first = factory(path)
    first.make_order(VENUE, request(), normalized=True)
    first.exchange_feeds[VENUE].disconnect = Mock(
        side_effect=RuntimeError("close failed")
    )
    with pytest.raises(NormalizedApiError):
        first.close()
    second = factory(path)
    before = path.read_text()
    with pytest.raises(NormalizedApiError, match="execution_session_closed"):
        first.query_order(
            VENUE,
            QueryOrderRequest(
                symbol=SYMBOL,
                account_id=VENUE,
                client_order_id=request().client_order_id,
            ),
            normalized=True,
        )
    assert path.read_text() == before
    assert second.get_execution_summary()["unknown_ids"]


def test_pending_query_progresses_despite_busy_market_and_slow_failure_has_backoff(
    factory, monkeypatch
):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    clock = [100.0]
    monkeypatch.setattr("bt_api_py._execution_session.time.monotonic", lambda: clock[0])

    def failure():
        clock[0] += 30
        raise TimeoutError()

    api._backend.query_result = failure
    due(api)
    for _ in range(10):
        api.data_queues[VENUE].put({"kind": "tick", "symbol": SYMBOL, "price": 60000})
    for _ in range(5):
        assert api.poll_event(VENUE)["kind"] == "tick"
    assert len(api._backend.queried) == 1


def test_ctp_trade_authority_and_native_cancel_locator_are_preserved(factory):
    api = factory()
    venue = "CTP___FUTURE"
    api.data_queues[venue] = Queue()
    req = request(
        symbol="IF2609",
        account_id=venue,
        position_mode=None,
        position_side="long",
        offset="close_today",
        exchange_id="CFFEX",
    )
    api._backend.place_result = response(
        symbol="IF2609",
        status="filled",
        filled=2,
        avg_price=60000,
        front_id=1,
        session_id=2,
        exchange_id="CFFEX",
        order_ref=req.client_order_id,
    )
    result = api.make_order(venue, req, normalized=True)
    assert result["execution_source"] == "trades" and result["avg_price"] is None
    assert not result["execution_unknown"] and result["terminal_confirmed"]
    assert "price" not in result
    cancel = CancelOrderRequest(
        symbol="IF2609",
        account_id=venue,
        client_order_id=req.client_order_id,
        order_id="123",
        order_ref=req.client_order_id,
        front_id=1,
        session_id=2,
        exchange_id="CFFEX",
    )
    api.cancel_order(venue, cancel, normalized=True)
    assert api._backend.canceled[-1][1] == cancel


def test_unsupported_query_keeps_unknown_visible_without_retrying_placement(factory):
    api = factory()
    api._backend.place_result = TimeoutError()
    api.make_order(VENUE, request(), normalized=True)
    api._backend.query_result = CapabilityNotSupportedError("query_order")
    due(api)
    assert api.poll_event(VENUE) is None
    summary = api.get_execution_summary()
    assert summary["unknown_ids"] and summary["trading_blocked"]
    assert (
        summary["reconciliation_errors"][request().client_order_id]
        == "CapabilityNotSupportedError"
    )
    assert summary["evidence_errors"] == [
        f"{request().client_order_id}:CapabilityNotSupportedError"
    ]
    assert len(api._backend.placed) == 1


@pytest.mark.parametrize("unknown", [None, "USD"])
def test_portfolio_rejects_unknown_or_different_nonzero_currency(factory, unknown):
    api = factory()
    with pytest.raises(NormalizedApiError, match="mixed_or_unknown_account_currencies"):
        api.get_portfolio_balance(
            venue_balances={
                "a": {"cash": 1, "value": 2, "currency": "USDT"},
                "b": {"cash": 3, "value": 4, "currency": unknown},
            }
        )


def test_portfolio_reuses_supplied_snapshot_without_another_request(factory):
    api = factory()
    api.get_all_balances = Mock(side_effect=AssertionError("unexpected HTTP request"))
    assert api.get_portfolio_balance(
        venue_balances={
            "a": {"cash": 1, "value": 2, "currency": "USDT"},
            "b": {"cash": 3, "value": 4, "currency": "USDT"},
        }
    ) == {"cash": 4, "value": 6, "currency": "USDT"}


def test_configure_execution_is_idempotent_but_cannot_swap_journal(factory):
    api = factory()
    api.configure_execution(dict(api._execution_session.config))
    with pytest.raises(NormalizedApiError, match="execution_already_configured"):
        api.configure_execution({"order_journal": "different.jsonl"})


def _child_hold_session(path, connection):
    api = BtApi(debug=False)
    api.configure_execution(
        {
            "order_journal": path,
            "account_ids": {VENUE: VENUE},
            "required_environments": {VENUE: "demo"},
        },
        _exchange_names={VENUE: credential_settings(VENUE)},
    )
    connection.send(True)
    connection.recv()
    api.close()


def test_process_lock_blocks_another_process_and_crash_releases_it(factory, tmp_path):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    path = str(tmp_path / "process.jsonl")
    process = context.Process(target=_child_hold_session, args=(path, child))
    process.start()
    try:
        assert parent.poll(20) and parent.recv()
        with pytest.raises(NormalizedApiError, match="locked_or_unavailable"):
            factory(path)
    finally:
        process.terminate()
        process.join(20)
        parent.close()
        child.close()
    api = factory(path)
    assert api.get_execution_summary()["submit_calls"] == 0


def test_restart_restores_later_native_order_references_and_trade_ids(
    factory, tmp_path
):
    path = tmp_path / "ctp-recovery.jsonl"
    venue = "CTP___FUTURE"
    base = {
        "exchange_name": venue,
        "symbol": "IF2609",
        "client_order_id": "100000000001",
    }
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "event": "intent",
                    **base,
                    "quantity_unit": "contracts",
                    "position_side": "long",
                    "offset": "close_today",
                    "size": 2,
                },
                {
                    "event": "order_update",
                    **base,
                    "order_id": "EXCHANGE-ID",
                    "order_ref": base["client_order_id"],
                    "exchange_id": "CFFEX",
                    "front_id": 5,
                    "session_id": 7,
                    "terminal_confirmed": False,
                    "execution_unknown": False,
                    "status": "partial",
                    "filled": 1,
                    "execution_source": "trades",
                },
                {
                    "event": "trade_update",
                    **base,
                    "trade_id": "native-trade-1",
                    "price": 3456,
                    "size": 1,
                    "offset": "close_today",
                },
            ]
        )
        + "\n"
    )
    api = factory(path)
    api.data_queues[venue] = Queue()
    api._backend.query_result = CapabilityNotSupportedError("query_order")
    assert api.poll_event(venue) is None
    query = api._backend.queried[0][1]
    assert (
        query.order_id,
        query.order_ref,
        query.exchange_id,
        query.front_id,
        query.session_id,
    ) == ("EXCHANGE-ID", "100000000001", "CFFEX", 5, 7)
    api.data_queues[venue].put(
        {
            "kind": "trade",
            **base,
            "trade_id": "native-trade-1",
            "price": 3456,
            "size": 1,
        }
    )
    assert api.poll_event(venue) is None
    assert api.get_execution_summary()["unknown_ids"] == [base["client_order_id"]]


def test_identical_exchange_order_ids_never_merge_across_venues(factory):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    second = "BINANCE___SWAP"
    api.data_queues[second] = Queue()
    api.data_queues[second].put(
        {
            "kind": "order",
            "symbol": "BTCUSDT",
            "order_id": "123",
            "client_order_id": "elsewhere",
            "side": "sell",
            "status": "filled",
            "filled": 0.002,
            "avg_price": 60000,
        }
    )
    result = api.poll_event(second)
    assert (
        result["exchange_name"] == second and result["client_order_id"] == "elsewhere"
    )
    assert (
        api._execution_session.orders[(VENUE, request().client_order_id)]["terminal"]
        is False
    )


def test_identical_native_ids_are_scoped_by_symbol_in_query_and_journal_recovery(
    factory, tmp_path
):
    path = tmp_path / "symbol-scoped.jsonl"
    api = factory(path)
    for symbol, price in [(SYMBOL, 60000), ("ETH-USDT-SWAP", 3000)]:
        api._backend.query_result = {
            "symbol": symbol,
            "order_id": "123",
            "status": "filled",
            "filled": 1,
            "avg_price": price,
        }
        result = api.query_order(
            VENUE,
            QueryOrderRequest(symbol=symbol, account_id=VENUE, order_id="123"),
            normalized=True,
        )
        assert result["symbol"] == symbol and result["avg_price"] == price
    assert len(api._execution_session.orders) == 2
    api.close()
    recovered = factory(path)
    assert len(recovered._execution_session.orders) == 2
    assert {row["symbol"] for row in recovered._execution_session.orders.values()} == {
        SYMBOL,
        "ETH-USDT-SWAP",
    }
    assert not recovered.get_execution_summary()["trading_blocked"]


def test_failed_query_of_untracked_order_does_not_create_or_poll_ghost_state(factory):
    api = factory()
    api._backend.query_result = TimeoutError("private query failure")
    query = QueryOrderRequest(
        symbol=SYMBOL,
        account_id=VENUE,
        client_order_id=request().client_order_id,
    )

    with pytest.raises(NormalizedApiError):
        api.query_order(VENUE, query, normalized=True)

    assert api._execution_session.orders == {}
    assert api.get_execution_summary() == {
        "session_enabled": True,
        "armed": False,
        "market_data_only": False,
        "arm_managed": False,
        "arm_revoked": False,
        "revocation_reason": None,
        "arm_proof_sha256": None,
        "proof_sha256": None,
        "last_arm_proof_sha256": None,
        "generation": None,
        "session_generation": None,
        "fencing_epoch": 1,
        "submit_calls": 0,
        "cancel_calls": 0,
        "unknown_ids": [],
        "active_orders": 0,
        "fee_unresolved_orders": [],
        "estimated_fee_orders": [],
        "funding_unresolved_orders": [],
        "funding_evidence_status": "unavailable",
        "signed_funding_cashflow": None,
        "evidence_errors": [],
        "loss_limit_bps": None,
        "loss_limit_breached": False,
        "trading_blocked": False,
        "evidence_complete": True,
        "reconciliation_errors": {},
    }
    assert api.poll_event(VENUE) is None
    assert len(api._backend.queried) == 1


@pytest.mark.parametrize(
    "query_result,expected_active",
    [
        (response(), 1),
        (response(status="filled", filled=2, avg_price=60000), 0),
    ],
)
def test_successful_query_of_untracked_order_creates_real_state(
    factory, query_result, expected_active
):
    api = factory()
    api._backend.query_result = query_result

    result = api.query_order(
        VENUE,
        QueryOrderRequest(
            symbol=SYMBOL,
            account_id=VENUE,
            client_order_id=request().client_order_id,
        ),
        normalized=True,
    )

    assert len(api._execution_session.orders) == 1
    assert api.get_execution_summary()["active_orders"] == expected_active
    assert result["terminal_confirmed"] is (expected_active == 0)


def test_failed_query_of_known_unknown_order_preserves_blocking_state(factory):
    api = factory()
    api._backend.place_result = TimeoutError("placement timeout")
    api.make_order(VENUE, request(), normalized=True)
    before = dict(api.get_execution_summary())
    api._backend.query_result = TimeoutError("query timeout")

    with pytest.raises(NormalizedApiError):
        api.query_order(
            VENUE,
            QueryOrderRequest(
                symbol=SYMBOL,
                account_id=VENUE,
                client_order_id=request().client_order_id,
            ),
            normalized=True,
        )

    after = api.get_execution_summary()
    assert len(api._execution_session.orders) == 1
    assert after["active_orders"] == before["active_orders"] == 1
    assert after["unknown_ids"] == before["unknown_ids"] == [request().client_order_id]
    assert after["trading_blocked"] is True


def test_successful_query_updates_existing_state_without_duplicating_it(factory):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    state = next(iter(api._execution_session.orders.values()))
    api._backend.query_result = response(status="filled", filled=2, avg_price=60000)

    result = api.query_order(
        VENUE,
        QueryOrderRequest(
            symbol=SYMBOL,
            account_id=VENUE,
            client_order_id=request().client_order_id,
        ),
        normalized=True,
    )

    assert len(api._execution_session.orders) == 1
    assert next(iter(api._execution_session.orders.values())) is state
    assert (
        result["terminal_confirmed"]
        and api.get_execution_summary()["active_orders"] == 0
    )


def test_native_ids_include_known_exchange_and_unscoped_collision_is_rejected(factory):
    api = factory()
    for exchange_id in ("A", "B"):
        api._backend.query_result = {
            "symbol": SYMBOL,
            "exchange_id": exchange_id,
            "order_id": "123",
            "status": "accepted",
            "filled": 0,
        }
        result = api.query_order(
            VENUE,
            QueryOrderRequest(
                symbol=SYMBOL, account_id=VENUE, order_id="123", exchange_id=exchange_id
            ),
            normalized=True,
        )
        assert result["exchange_id"] == exchange_id
    assert len(api._execution_session.orders) == 2
    before = len(api._backend.queried)
    with pytest.raises(NormalizedApiError, match="ambiguous_order_identity"):
        api.query_order(
            VENUE,
            QueryOrderRequest(symbol=SYMBOL, account_id=VENUE, order_id="123"),
            normalized=True,
        )
    assert len(api._backend.queried) == before


@pytest.mark.parametrize(
    "changed",
    [
        {"symbol": "ETH-USDT-SWAP"},
        {"order_id": "different"},
        {"client_order_id": "different"},
    ],
)
def test_bound_order_identity_cannot_be_overwritten_by_query_reply(factory, changed):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    api._backend.query_result = response(
        status="filled", filled=2, avg_price=60000, **changed
    )
    result = api.query_order(
        VENUE,
        QueryOrderRequest(
            symbol=SYMBOL, account_id=VENUE, client_order_id=request().client_order_id
        ),
        normalized=True,
    )
    assert (
        result["execution_unknown"]
        and result["error_code"] == "order_identity_mismatch"
    )
    assert result["symbol"] == SYMBOL and result["order_id"] == "123"
    assert result["client_order_id"] == request().client_order_id
    assert api.get_execution_summary()["trading_blocked"]


@pytest.mark.parametrize(
    "changed",
    [
        {"position_side": "short"},
        {"offset": "close"},
        {"position_mode": "net"},
        {"quantity_unit": "base"},
    ],
)
def test_bound_position_intent_cannot_be_overwritten_by_query_reply(factory, changed):
    api = factory()
    bound = {
        "position_side": "long",
        "offset": "open",
        "position_mode": "dual_side",
        "quantity_unit": "contracts",
    }
    api.make_order(VENUE, request(**bound), normalized=True)
    state = next(iter(api._execution_session.orders.values()))
    api._backend.query_result = response(
        status="filled", filled=2, avg_price=60000, **changed
    )

    result = api.query_order(
        VENUE,
        QueryOrderRequest(
            symbol=SYMBOL, account_id=VENUE, client_order_id=request().client_order_id
        ),
        normalized=True,
    )

    assert result["execution_unknown"] is True
    assert result["error_code"] == "ledger_mismatch"
    for key, value in bound.items():
        assert result[key] == state[key] == value
    assert api.get_execution_summary()["trading_blocked"] is True


def test_inferred_venue_unit_is_not_independent_identity_evidence(factory):
    api = factory()
    bound = request(account_id=BINANCE_VENUE, quantity_unit="contracts")

    result = api.make_order(BINANCE_VENUE, bound, normalized=True)

    assert result["status"] == "accepted"
    assert result["execution_unknown"] is False
    assert result["quantity_unit"] == "contracts"
    state = next(iter(api._execution_session.orders.values()))
    assert state["quantity_unit"] == "contracts"
    assert "_explicit_identity_fields" not in result


def test_order_update_with_opposite_intent_side_becomes_ledger_mismatch(factory):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    state = next(iter(api._execution_session.orders.values()))
    api.data_queues[VENUE].put(
        {
            "kind": "order",
            **response(side="sell", status="partial", filled=1, avg_price=60000),
        }
    )

    result = api.poll_event(VENUE)

    assert result["execution_unknown"] is True
    assert result["error_code"] == "ledger_mismatch"
    assert result["side"] == state["side"] == "buy"
    assert state["last_update"]["side"] == "buy"
    assert api.get_execution_summary()["trading_blocked"] is True
    due(api)
    api.poll_event(VENUE)
    assert api._backend.queried


def test_trade_with_opposite_intent_side_is_not_booked_and_reconciles(factory):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    state = next(iter(api._execution_session.orders.values()))
    api.data_queues[VENUE].put(
        {
            "kind": "trade",
            "symbol": SYMBOL,
            "order_id": "123",
            "client_order_id": request().client_order_id,
            "trade_id": "wrong-side-trade",
            "side": "sell",
            "price": 60001,
            "size": 1,
            "fee": 0.1,
            "fee_currency": "USDT",
        }
    )

    result = api.poll_event(VENUE)

    assert result["execution_unknown"] is True
    assert result["error_code"] == "ledger_mismatch"
    assert result["side"] == state["side"] == "buy"
    assert (VENUE, SYMBOL, "wrong-side-trade") not in api._execution_session.trade_ids
    journal_events = [
        json.loads(line)["event"]
        for line in api._execution_session.path.read_text().splitlines()
    ]
    assert "trade" not in journal_events and "trade_update" not in journal_events
    due(api)
    api.poll_event(VENUE)
    assert api._backend.queried


@pytest.mark.parametrize(
    "changed",
    [
        {"position_side": "short"},
        {"offset": "close"},
        {"position_mode": "net"},
        {"quantity_unit": "base"},
    ],
)
def test_order_update_cannot_overwrite_bound_position_intent(factory, changed):
    api = factory()
    bound = {
        "position_side": "long",
        "offset": "open",
        "position_mode": "dual_side",
        "quantity_unit": "contracts",
    }
    api.make_order(VENUE, request(**bound), normalized=True)
    state = next(iter(api._execution_session.orders.values()))
    api.data_queues[VENUE].put(
        {
            "kind": "order",
            **response(status="partial", filled=1, avg_price=60000),
            **changed,
        }
    )

    result = api.poll_event(VENUE)

    assert result["execution_unknown"] is True
    assert result["error_code"] == "ledger_mismatch"
    for key, value in bound.items():
        assert result[key] == state[key] == value
    assert api.get_execution_summary()["trading_blocked"] is True


@pytest.mark.parametrize(
    "changed",
    [
        {"position_side": "short"},
        {"offset": "close"},
        {"position_mode": "net"},
        {"quantity_unit": "base"},
    ],
)
def test_trade_cannot_overwrite_bound_position_intent_or_enter_journal(
    factory, changed
):
    api = factory()
    bound = {
        "position_side": "long",
        "offset": "open",
        "position_mode": "dual_side",
        "quantity_unit": "contracts",
    }
    api.make_order(VENUE, request(**bound), normalized=True)
    state = next(iter(api._execution_session.orders.values()))
    api.data_queues[VENUE].put(
        {
            "kind": "trade",
            "symbol": SYMBOL,
            "order_id": "123",
            "client_order_id": request().client_order_id,
            "trade_id": "wrong-position-intent",
            "side": "buy",
            "price": 60001,
            "size": 1,
            "fee": 0.1,
            "fee_currency": "USDT",
            **changed,
        }
    )

    result = api.poll_event(VENUE)

    assert result["execution_unknown"] is True
    assert result["error_code"] == "ledger_mismatch"
    for key, value in bound.items():
        assert result[key] == state[key] == value
    assert (
        VENUE,
        SYMBOL,
        "wrong-position-intent",
    ) not in api._execution_session.trade_ids
    journal_events = [
        json.loads(line)["event"]
        for line in api._execution_session.path.read_text().splitlines()
    ]
    assert "trade" not in journal_events and "trade_update" not in journal_events


def test_trade_with_bound_client_id_but_wrong_symbol_is_not_booked(factory):
    api = factory()
    api.make_order(VENUE, request(), normalized=True)
    api.data_queues[VENUE].put(
        {
            "kind": "trade",
            "symbol": "ETH-USDT-SWAP",
            "order_id": "123",
            "client_order_id": request().client_order_id,
            "trade_id": "other",
            "price": 10,
            "size": 1,
        }
    )
    result = api.poll_event(VENUE)
    assert result["kind"] == "order" and result["execution_unknown"]
    assert result["symbol"] == SYMBOL and "price" not in result
    assert api.get_execution_summary()["trading_blocked"]


def test_real_request_data_is_normalized_before_session_merge_and_fee_enrichment(
    factory,
):
    from bt_api_base.containers.requestdatas.request_data import RequestData

    api = factory()
    api._backend.place_result = RequestData(
        {
            "code": "0",
            "data": [
                {
                    "instId": SYMBOL,
                    "ordId": "123",
                    "clOrdId": request().client_order_id,
                    "state": "filled",
                    "accFillSz": "2",
                    "avgPx": "60001",
                    "sz": "2",
                    "fee": "-0.8",
                    "feeCcy": "USDT",
                    "side": "buy",
                }
            ],
        },
        {},
    )
    result = api.make_order(VENUE, request(), normalized=True)
    assert result["terminal_confirmed"] and result["filled"] == 2
    assert result["cumulative_commission"] == 0.8 and result["commission_normalized"]


def test_terminal_fee_enrichment_uses_reconciled_binance_deals_in_session(factory):
    api = factory()
    venue = "BINANCE___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        disconnect=lambda: None,
        get_environment_info=lambda: {
            "environment": "demo",
            "simulated": True,
            "verified": True,
        },
    )
    api.exchange_kwargs[venue] = {}
    api.data_queues[venue] = Queue()
    api._backend.place_result = {
        "orderId": 123,
        "clientOrderId": "binance1",
        "symbol": "BTCUSDT",
        "status": "FILLED",
        "executedQty": ".002",
        "avgPrice": "60000",
    }
    api._backend.get_deals = Mock(
        return_value=[
            {
                "id": 1,
                "orderId": 123,
                "symbol": "BTCUSDT",
                "price": "60000",
                "qty": ".002",
                "commission": ".048",
                "commissionAsset": "USDT",
                "side": "BUY",
            }
        ]
    )
    result = api.make_order(
        venue,
        request(
            "binance1",
            symbol="BTCUSDT",
            account_id=venue,
            quantity=Decimal(".002"),
        ),
        normalized=True,
    )
    assert result["cumulative_commission"] == 0.048 and result["commission_normalized"]


def test_pre_dispatch_unsupported_is_explicit_but_later_same_class_is_unknown(factory):
    for declared in (True, False):
        api = factory()
        api._backend.place_result = CapabilityNotSupportedError(
            "make_order", definite_reject=declared
        )
        result = api.make_order(VENUE, request(), normalized=True)
        assert result["terminal_confirmed"] is declared
        assert result["execution_unknown"] is not declared
        journal = api._execution_session.path
        api.close()
        journal.unlink()


@pytest.mark.parametrize(
    "result,unknown",
    [
        (TimeoutError("signed URL should never escape"), True),
        ({"status": "rejected"}, False),
        ({"status": "accepted", "order_id": "MT5-ticket-7"}, False),
    ],
)
def test_session_uses_real_gateway_command_roundtrip_with_native_mt5_intent(
    monkeypatch, tmp_path, result, unknown
):
    from bt_api_base.gateway.config import GatewayConfig
    from bt_api_base.gateway.runtime import GatewayRuntime

    from bt_api_py import ForwardingConfig
    from bt_api_py.forwarding.schema import CommandAck, OrderCommand

    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    runtime = GatewayRuntime(
        GatewayConfig(exchange_type="MT5", asset_type="FX", account_id="demo")
    )
    method = (
        Mock(side_effect=result)
        if isinstance(result, Exception)
        else Mock(return_value=result)
    )
    runtime.adapter = SimpleNamespace(place_order=method)

    class Client:
        def _send_command_sync(self, command):
            restored = OrderCommand.from_dict(command.to_dict())
            assert restored.extra["quantity_unit"] == "lots"
            assert restored.extra["position_id"] == "987"
            ack = runtime._handle_command(restored)
            return CommandAck.from_dict(ack.to_dict())

        def disconnect(self):
            pass

        def poll_broker_update(self):
            return None

    api = BtApi(
        {"MT5___FX": {}},
        debug=False,
        transport_mode="zmq",
        forwarding_config=ForwardingConfig(
            command_endpoint="inproc://c",
            market_endpoint="inproc://m",
            private_endpoint="inproc://p",
            account_id="demo",
            strategy_id="test",
        ),
        execution_config={
            "order_journal": tmp_path / "mt5.jsonl",
            "account_currency": "USD",
        },
    )
    api._backend._client = Client()
    try:
        req = request(
            symbol="EURUSD",
            account_id="demo",
            order_type=OrderType.MARKET,
            price=None,
            quantity=Decimal(".01"),
            quantity_unit="native",
            position_id="987",
            position_mode=None,
            position_side="long",
            offset="close",
            side=Side.SELL,
        )
        update = api.make_order("MT5___FX", req, normalized=True)
        assert update["execution_unknown"] is unknown
        assert method.call_count == 1 and api.exchange_feeds == {}
        assert method.call_args.args[0]["position_id"] == "987"
        if unknown:
            due(api)
            assert api.poll_event("MT5___FX") is None
            assert api.get_execution_summary()["trading_blocked"]
    finally:
        api.close()
