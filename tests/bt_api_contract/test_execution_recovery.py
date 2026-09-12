"""Offline crash-recovery regressions for the managed CTP execution session."""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import threading
from collections import Counter, deque
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bt_api_py import (
    BtApi,
    CancelOrderRequest,
    NormalizedApiError,
    OrderRequest,
    TransportMode,
)
from bt_api_py import bt_api as bt_api_module
from bt_api_py._contracts.models import OrderType, Side
from bt_api_py._direct_backend import DirectBackend
from bt_api_py._execution_session import _ExecutionSession

VENUE = "CTP___FUTURE"
INSTRUMENT = "CZCE.SA609"
ACCOUNT = "acct_0123456789abcdef"
TRADING_DAY = "20260909"
STRATEGY_IDENTITY = "7" * 64
CYCLE = "cycle-iter22"
BUNDLE_SCOPE_VERSION = "ctp-contract-bundle-v1"
BUNDLE_INSTRUMENTS = ["CZCE.SA701", "CZCE.SA701C1080", "CZCE.SA701P1080"]


class _RecoveryNativeAuthorization:
    """Fixture-only opaque counterpart to the native feed grant."""

    def __init__(self, proof):
        self.proof = dict(proof)


class ManagedRecoveryFeed:
    def __init__(self, generation=4):
        self.state = {
            "connected": True,
            "read_only_ready": True,
            "trading_ready": True,
            "auth_state": "authenticated",
            "login_state": "logged_in",
            "settlement_state": "confirmed",
            "settlement_readback_verified": True,
            "auto_settlement_confirm": False,
            "connection_generation": generation,
            "account_fingerprint": ACCOUNT.removeprefix("acct_"),
            "trading_day": TRADING_DAY,
            "environment_profile": "simnow_demo",
        }
        self.trader_client = SimpleNamespace(get_session_state=lambda: dict(self.state))
        self.capability = None
        self.gate = {
            "managed": True,
            "armed": False,
            "scope_version": None,
            "authorized_instruments": None,
        }
        self.arm_calls = []
        self.disarm_calls = []

    def get_session_state(self):
        return dict(self.state)

    def get_environment_info(self):
        return {
            "environment": "demo",
            "verified": True,
            "profile": "simnow_demo",
        }

    def configure_execution_gate(self, capability):
        self.capability = capability
        return dict(self.gate)

    def _issue_execution_authorization_for_core(self, capability, proof, **_kwargs):
        if capability is not self.capability:
            raise RuntimeError("wrong capability")
        return _RecoveryNativeAuthorization(proof)

    def arm_execution_gate(self, capability, authorization):
        if capability is not self.capability:
            raise RuntimeError("wrong capability")
        if type(authorization) is not _RecoveryNativeAuthorization:
            raise RuntimeError("opaque authorization required")
        arm_proof = authorization.proof
        self.arm_calls.append(dict(arm_proof))
        self.gate = {
            "managed": True,
            "armed": True,
            "connection_generation": arm_proof["connection_generation"],
            "trading_day": arm_proof["trading_day"],
            "instrument": arm_proof["instrument"],
            "scope_version": arm_proof.get("scope_version"),
            "authorized_instruments": (
                list(arm_proof["authorized_instruments"])
                if "authorized_instruments" in arm_proof
                else None
            ),
            "environment_profile": arm_proof["environment_profile"],
            "proof_sha256": canonical_sha256(arm_proof),
            "revocation_reason": None,
        }
        return dict(self.gate)

    def disarm_execution_gate(self, capability, reason="execution_arm_revoked"):
        if capability is not self.capability:
            raise RuntimeError("wrong capability")
        self.disarm_calls.append(reason)
        self.gate = {
            "managed": True,
            "armed": False,
            "scope_version": None,
            "authorized_instruments": None,
            "revocation_reason": reason,
        }
        return dict(self.gate)

    def get_execution_gate_state(self):
        return dict(self.gate)


class ManagedRecoveryStream:
    def __init__(self, data_queue, feed):
        self.data_queue = data_queue
        self.stream_name = "ctp_trade_stream"
        self.trader_client = feed.trader_client
        self._running = True
        self.state = SimpleNamespace(value="authenticated")
        self.stopped = False

    def wait_connected(self, *, timeout):
        return timeout == 5.0 and self._running

    def stop(self):
        self.stopped = True
        self._running = False
        self.state = SimpleNamespace(value="disconnected")

    def push(self, event):
        self.data_queue.put(dict(event))


def proof(generation=3, **changes):
    value = {
        "account_fingerprint": ACCOUNT,
        "trading_day": TRADING_DAY,
        "instrument": INSTRUMENT,
        "connection_generation": generation,
        "environment_profile": "simnow_demo",
        "receipt_sha256": "1" * 64,
        "native_sha256": "2" * 64,
        "ctp_package_sha256": "3" * 64,
        "source_hashes_sha256": "4" * 64,
        "dependency_hashes_sha256": "5" * 64,
        "preflight_sha256": "6" * 64,
    }
    value.update(changes)
    return value


def bundle_proof(generation=3, **changes):
    value = proof(
        generation,
        instrument=BUNDLE_INSTRUMENTS[0],
        scope_version=BUNDLE_SCOPE_VERSION,
        authorized_instruments=list(BUNDLE_INSTRUMENTS),
    )
    value.update(changes)
    return value


def context(value):
    return {
        "account_fingerprint": value["account_fingerprint"],
        "trading_day": value["trading_day"],
        "connection_generation": value["connection_generation"],
        "environment_profile": value["environment_profile"],
        "native_sha256": value["native_sha256"],
        "ctp_package_sha256": value["ctp_package_sha256"],
        "account_stream_ready": True,
    }


def make_session(path, *, strategy_identity=STRATEGY_IDENTITY):
    return _ExecutionSession(
        {
            "market_data_only": True,
            "require_order_journal": True,
            "order_journal": str(path),
            "account_ids": {},
            "required_environments": {VENUE: "demo"},
            "strategy_id": "iter22-midfreq",
            "strategy_identity_sha256": strategy_identity,
        },
        exchange_names=(VENUE,),
    )


def _budget_evidence(session, proof_value):
    """Build a minimal write-eligible CTP path-budget evidence package.

    Mirrors what the SDK runtime collector produces for the managed SimNow
    chain; the session still re-validates the evidence against its own arm
    proof, writer lease and journal, so the O2 gate itself is not bypassed.
    """
    raw_scope = list(proof_value.get("authorized_instruments") or [])
    if not raw_scope:
        raw_scope = [proof_value["instrument"]]
    if len(raw_scope) < 2:
        exchange, symbol = raw_scope[0].split(".", 1)
        raw_scope.append(f"{exchange}.{symbol}C1080")
    instruments = [
        dict(zip(("exchange_id", "instrument_id"), item.split(".", 1), strict=True))
        for item in raw_scope
    ]
    budget_context = {
        "account_fingerprint": proof_value["account_fingerprint"],
        "trading_day": proof_value["trading_day"],
        "connection_generation": proof_value["connection_generation"],
        "environment_profile": proof_value["environment_profile"],
        "candidate_id": "iter22-budget-test",
        "strategy_id": session.config.get("strategy_id") or "iter22-midfreq",
        "strategy_identity_sha256": session.config.get("strategy_identity_sha256")
        or STRATEGY_IDENTITY,
        "execution_cycle_id": "budget-cycle",
        "scope_version": "test-budget-scope-v1",
        "authorized_instruments": [dict(item) for item in instruments],
        "primary_instrument": dict(instruments[0]),
    }
    costs = {
        "future_gross_margin": "1",
        "seller_option_gross_margin": "1",
        "paid_long_premium": "1",
        "fees_financing": "1",
        "stress_cash_loss": "1",
        "unresolved_reserve": "1",
    }
    states = [
        {
            "state_id": kind,
            "state_kind": kind,
            "context": dict(budget_context),
            "costs": dict(costs),
        }
        for kind in ("prefix", "partial", "unknown", "cancel", "late_fill")
    ]
    states.append(
        {
            "state_id": "recovery",
            "state_kind": "recovery",
            "non_overlapping": True,
            "recovery_increment_cny": "2",
            "context": dict(budget_context),
            "costs": dict(costs),
        }
    )
    return {
        "schema_version": "ctp-execution-budget-v1",
        "source": "sdk_runtime",
        "source_version": "test-collector-v1",
        "money_unit": "CNY",
        **budget_context,
        "reachable_states": states,
        "fresh_available_cny": "100000",
        "account_available_authoritative": True,
        "seller_margin_source_verified": True,
        "absorption_source_verified": True,
    }


def _reserve_budget(session, proof_value, *, mode="ordinary"):
    return session.reserve_ctp_execution_budget(
        _budget_evidence(session, proof_value), mode=mode
    )


def order_request(
    *,
    side=Side.BUY,
    quantity="2",
    offset="open",
    position_side="long",
    role="entry",
    client_order_id="000000000001",
    cycle=CYCLE,
):
    return OrderRequest(
        symbol="SA609.CZCE",
        side=side,
        order_type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        account_id=ACCOUNT,
        client_order_id=client_order_id,
        price=Decimal("1500"),
        time_in_force="GFD",
        quantity_unit="contracts",
        position_side=position_side,
        offset=offset,
        exchange_id="CZCE",
        execution_cycle_id=cycle,
        execution_role=role,
        strategy_identity_sha256=STRATEGY_IDENTITY,
    )


def bundle_order_request(instrument, *, client_order_id, cycle=CYCLE):
    exchange_id, symbol = instrument.split(".", 1)
    return OrderRequest(
        symbol=symbol,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        account_id=ACCOUNT,
        client_order_id=client_order_id,
        price=Decimal("1500"),
        time_in_force="GFD",
        quantity_unit="contracts",
        position_side="long",
        offset="open",
        exchange_id=exchange_id,
        execution_cycle_id=cycle,
        execution_role="entry",
        strategy_identity_sha256=STRATEGY_IDENTITY,
    )


def bundle_order_update(instrument, *, client_order_id, order_id, terminal=False):
    exchange_id, symbol = instrument.split(".", 1)
    return {
        "kind": "order",
        "symbol": symbol,
        "account_id": ACCOUNT,
        "client_order_id": client_order_id,
        "order_id": order_id,
        "exchange_id": exchange_id,
        "side": "buy",
        "position_side": "long",
        "offset": "open",
        "quantity_unit": "contracts",
        "status": "completed" if terminal else "accepted",
        "filled": "2" if terminal else "0",
        "avg_price": "1500" if terminal else None,
        "terminal_confirmed": terminal,
        "trading_day": TRADING_DAY,
    }


def bundle_trade_update(instrument, *, client_order_id, order_id, trade_id):
    exchange_id, symbol = instrument.split(".", 1)
    return {
        "kind": "trade",
        "symbol": symbol,
        "account_id": ACCOUNT,
        "client_order_id": client_order_id,
        "order_id": order_id,
        "exchange_id": exchange_id,
        "side": "buy",
        "position_side": "long",
        "offset": "open",
        "quantity_unit": "contracts",
        "size": "2",
        "price": "1500",
        "trade_id": trade_id,
        "trading_day": TRADING_DAY,
    }


def bundle_remote_trade(instrument, *, trade_id):
    exchange_id, symbol = instrument.split(".", 1)
    return {
        "AccountID": ACCOUNT,
        "TradingDay": TRADING_DAY,
        "InstrumentID": symbol,
        "ExchangeID": exchange_id,
        "Direction": "0",
        "OffsetFlag": "0",
        "Volume": "2",
        "TradeID": trade_id,
        "connection_generation": 4,
        "evidence_complete": True,
    }


def bundle_position(instrument, *, quantity="2", frozen="0"):
    exchange_id, symbol = instrument.split(".", 1)
    return {
        "AccountID": ACCOUNT,
        "TradingDay": TRADING_DAY,
        "InstrumentID": symbol,
        "ExchangeID": exchange_id,
        "PosiDirection": "2",
        "Position": quantity,
        "TodayPosition": quantity,
        "YdPosition": "0",
        "LongFrozen": frozen,
        "ShortFrozen": quantity,
        "connection_generation": 4,
        "evidence_complete": True,
    }


def order_update(
    *,
    side="buy",
    quantity="2",
    status="accepted",
    terminal=False,
    client_order_id="000000000001",
    order_id="SYS1",
    offset="open",
    position_side=None,
):
    filled = quantity if terminal and status == "completed" else "0"
    return {
        "kind": "order",
        "symbol": "SA609.CZCE",
        "account_id": ACCOUNT,
        "client_order_id": client_order_id,
        "order_id": order_id,
        "exchange_id": "CZCE",
        "side": side,
        "position_side": position_side or ("long" if side == "buy" else "short"),
        "offset": offset,
        "quantity_unit": "contracts",
        "status": status,
        "filled": filled,
        "avg_price": "1500" if Decimal(filled) else None,
        "terminal_confirmed": terminal,
        "trading_day": TRADING_DAY,
    }


def trade_update(
    *,
    side="buy",
    position_side="long",
    quantity="2",
    client_order_id="000000000001",
    order_id="SYS1",
    offset="open",
    trade_id="TRADE1",
):
    return {
        "kind": "trade",
        "symbol": "SA609.CZCE",
        "account_id": ACCOUNT,
        "client_order_id": client_order_id,
        "order_id": order_id,
        "exchange_id": "CZCE",
        "side": side,
        "position_side": position_side,
        "offset": offset,
        "quantity_unit": "contracts",
        "size": quantity,
        "price": "1500",
        "trade_id": trade_id,
        "trading_day": TRADING_DAY,
    }


def remote_trade(*, side="buy", quantity="2"):
    return {
        "AccountID": ACCOUNT,
        "TradingDay": TRADING_DAY,
        "InstrumentID": "SA609",
        "ExchangeID": "CZCE",
        "Direction": "0" if side == "buy" else "1",
        "OffsetFlag": "0",
        "Volume": quantity,
        "TradeID": "TRADE1",
        "connection_generation": 4,
        "evidence_complete": True,
    }


def position(*, side="long", quantity="2", today="1", yesterday="1", frozen="0"):
    return {
        "AccountID": ACCOUNT,
        "TradingDay": TRADING_DAY,
        "InstrumentID": "SA609",
        "ExchangeID": "CZCE",
        "PosiDirection": "2" if side == "long" else "3",
        "Position": quantity,
        "TodayPosition": today,
        "YdPosition": yesterday,
        "LongFrozen": frozen if side == "long" else quantity,
        "ShortFrozen": frozen if side == "short" else quantity,
        "connection_generation": 4,
        "evidence_complete": True,
    }


def active_order(*, side="buy", quantity="2", offset="0"):
    return {
        "AccountID": ACCOUNT,
        "TradingDay": TRADING_DAY,
        "InstrumentID": "SA609",
        "ExchangeID": "CZCE",
        "Direction": "0" if side == "buy" else "1",
        "CombOffsetFlag": offset,
        "CombHedgeFlag": "1",
        "VolumeTotalOriginal": quantity,
        "VolumeTotal": quantity,
        "OrderStatus": "3",
        "OrderRef": "000000000001",
        "OrderSysID": "SYS1",
        "FrontID": 11,
        "SessionID": 22,
        "connection_generation": 4,
        "evidence_complete": True,
    }


def canonical_sha256(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def barrier(session, snapshot, *, first_id=1, account_balance="100"):
    account = (
        {
            "AccountID": ACCOUNT,
            "TradingDay": TRADING_DAY,
            "Balance": account_balance,
        },
    )
    snapshot_sha256 = canonical_sha256(snapshot)
    account_sha256 = canonical_sha256(account)
    full_sha256 = canonical_sha256({"account": account, **snapshot})
    revision = session.recovery_event_revision()
    private_revision = session.recovery_private_event_revision()
    ingress_revision = session.recovery_private_ingress_revision()
    rounds = []
    for offset in (0, 4):
        rounds.append(
            {
                "request_ids": {
                    "account": first_id + offset,
                    "positions": first_id + offset + 1,
                    "orders": first_id + offset + 2,
                    "trades": first_id + offset + 3,
                },
                "account_fingerprint": ACCOUNT,
                "trading_day": TRADING_DAY,
                "connection_generation": 4,
                "snapshot_sha256": snapshot_sha256,
                "account_snapshot_sha256": account_sha256,
                "full_snapshot_sha256": full_sha256,
            }
        )
    material = {
        "schema_version": "bt-api-py.ctp-recovery-query-barrier.v1",
        "stable": True,
        "attempts": 1,
        "event_revisions": {
            "start": revision,
            "middle": revision,
            "end": revision,
        },
        "private_event_revisions": {
            "start": private_revision,
            "end": private_revision,
        },
        "private_ingress_revisions": {
            "start": ingress_revision,
            "end": ingress_revision,
        },
        "private_ingress_epochs": {"start": 0, "end": 0},
        "private_ingress_pending": {"start": 0, "end": 0},
        "rounds": rounds,
    }
    return {**material, "barrier_sha256": canonical_sha256(material)}


def snapshot(*, positions=(), orders=(), trades=()):
    return {
        "positions": tuple(positions),
        "orders": tuple(orders),
        "trades": tuple(trades),
    }


def query_result(query_type, request_id, records=(), *, generation=4):
    return SimpleNamespace(
        request_type=query_type,
        request_id=request_id,
        complete=True,
        evidence_complete=True,
        is_last_seen=True,
        timed_out=False,
        unsupported=False,
        error_code=None,
        late_callback_count=0,
        connection_generation=generation,
        account_fingerprint=ACCOUNT,
        records=tuple(records),
    )


def query_rounds(*account_balances, first_id=1):
    results = []
    request_id = first_id
    for balance in account_balances:
        rows = {
            "account": ({"Balance": balance},),
            "positions": (),
            "orders": (),
            "trades": (),
        }
        for query_type in ("account", "positions", "orders", "trades"):
            results.append(query_result(query_type, request_id, rows[query_type]))
            request_id += 1
    return results


def public_recovery_api(path):
    session = make_session(path)
    feed = ManagedRecoveryFeed()
    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api.exchange_feeds = {VENUE: feed}
    api.exchange_kwargs = {VENUE: {"auto_settlement_confirm": False}}
    api.data_queues = {VENUE: queue.Queue()}
    api._normalized_event_pending = {VENUE: deque()}
    api._event_metrics = Counter()
    api._subscription_streams = []
    api._subscription_flags = {}
    api._execution_session = session
    api._ctp_execution_capability = object()
    feed.configure_execution_gate(api._ctp_execution_capability)
    api._ctp_execution_runtime_identity = lambda: {
        "native_sha256": "2" * 64,
        "ctp_package_sha256": "3" * 64,
    }
    api._prepare_ctp_execution_stream = lambda *_args, **_kwargs: None
    return api, session, feed


def _test_authority():
    return bt_api_module._issue_ctp_controlled_test_authority_for_core()


def _recovery_authorization(api, current_proof, *, cycle=CYCLE):
    return api._issue_ctp_execution_authorization_for_test(
        current_proof,
        _test_authority=_test_authority(),
        execution_cycle_id=cycle,
    )


def public_order_api(session, backend):
    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api._execution_session = session
    api._backend = backend
    api._validate_required_environment = lambda *_args, **_kwargs: None
    return api


def public_recovery_order_api(session, backend):
    api = public_order_api(session, backend)
    feed = ManagedRecoveryFeed()
    api.exchange_feeds = {VENUE: feed}
    api.exchange_kwargs = {VENUE: {"subscribe_account": True}}
    api.data_queues = {VENUE: queue.Queue()}
    api._normalized_event_pending = {VENUE: deque()}
    api._event_metrics = Counter()
    producer_queue = api._ctp_private_stream_queue(VENUE, api.data_queues[VENUE])
    stream = ManagedRecoveryStream(producer_queue, feed)
    api._subscription_streams = [stream]
    api._subscription_flags = {f"{VENUE}_account": True}
    api._ctp_execution_capability = object()
    feed.configure_execution_gate(api._ctp_execution_capability)
    feed.arm_execution_gate(
        api._ctp_execution_capability,
        feed._issue_execution_authorization_for_core(
            api._ctp_execution_capability,
            session._arm_proof,
        ),
    )
    return api, feed, stream


def write_crashed_journal(path, *, exposure=None, active=False, uncertain=False):
    old_proof = proof(3)
    session = make_session(path)
    session.arm_from_preflight(old_proof, lambda: context(old_proof))
    side = "sell" if exposure == "short" else "buy"
    position_side = "short" if exposure == "short" else "long"
    request = order_request(
        side=Side.SELL if side == "sell" else Side.BUY,
        position_side=position_side,
    )

    def submit():
        if uncertain:
            raise TimeoutError("transport outcome unknown")
        return order_update(side=side)

    try:
        session.invoke(
            "make_order",
            VENUE,
            request,
            submit,
            budget_capability=_reserve_budget(session, old_proof),
        )
    except NormalizedApiError:
        if not uncertain:
            raise
    if exposure is not None:
        session.event(
            VENUE,
            trade_update(side=side, position_side=position_side),
        )
        session.event(
            VENUE,
            order_update(side=side, status="completed", terminal=True),
        )
    elif not active and not uncertain:
        session.event(
            VENUE,
            order_update(side=side, status="canceled", terminal=True),
        )
    session.close()


def prepare_from_journal(path, *, new_proof=None, strategy_identity=STRATEGY_IDENTITY):
    current_proof = new_proof or proof(4)
    session = make_session(path, strategy_identity=strategy_identity)
    session.prepare_recovery(
        current_proof,
        lambda: context(current_proof),
        venue=VENUE,
    )
    return session, current_proof


def write_bundle_crashed_journal(path, *, instruments=BUNDLE_INSTRUMENTS[:2]):
    old_proof = bundle_proof(3)
    session = make_session(path)
    session.arm_from_preflight(old_proof, lambda: context(old_proof))
    try:
        for index, instrument in enumerate(instruments, start=1):
            client_order_id = f"0000000000{index:02d}"
            order_id = f"SYS{index}"
            trade_id = f"TRADE{index}"
            request = bundle_order_request(
                instrument,
                client_order_id=client_order_id,
            )
            session.invoke(
                "make_order",
                VENUE,
                request,
                lambda instrument=instrument, client_order_id=client_order_id, order_id=order_id: (
                    bundle_order_update(
                        instrument,
                        client_order_id=client_order_id,
                        order_id=order_id,
                    )
                ),
                budget_capability=_reserve_budget(session, old_proof),
            )
            session.event(
                VENUE,
                bundle_trade_update(
                    instrument,
                    client_order_id=client_order_id,
                    order_id=order_id,
                    trade_id=trade_id,
                ),
            )
            session.event(
                VENUE,
                bundle_order_update(
                    instrument,
                    client_order_id=client_order_id,
                    order_id=order_id,
                    terminal=True,
                ),
            )
    finally:
        session.close()


def breached_recovery_session(path):
    write_crashed_journal(path, exposure="long")
    session, current_proof = prepare_from_journal(path)
    current = snapshot(
        positions=[position(side="long", frozen="0")],
        trades=[remote_trade(side="buy")],
    )
    plan = session.build_recovery_plan(current, barrier=barrier(session, current))
    session.arm_recovery_from_preflight(
        current_proof,
        plan["recovery_token_sha256"],
        lambda: context(current_proof),
        budget_capability=_reserve_budget(session, current_proof, mode="recovery"),
    )
    session.config["account_maximum_loss_bps"] = Decimal("10")
    session.risk_record = {"loss_limit_breached": True}
    return session


def test_v2_bundle_recovery_keeps_future_and_option_exposure_per_leg(tmp_path):
    path = tmp_path / "bundle-orders.jsonl"
    tracked = BUNDLE_INSTRUMENTS[:2]
    write_bundle_crashed_journal(path, instruments=tracked)
    current_proof = bundle_proof(4)
    session, _proof_value = prepare_from_journal(path, new_proof=current_proof)
    current = snapshot(
        positions=[bundle_position(instrument) for instrument in tracked],
        trades=[
            bundle_remote_trade(instrument, trade_id=f"TRADE{index}")
            for index, instrument in enumerate(tracked, start=1)
        ],
    )
    try:
        plan = session.build_recovery_plan(current, barrier=barrier(session, current))

        assert plan["status"] == "RECOVERABLE"
        assert plan["authorized_instruments"] == BUNDLE_INSTRUMENTS
        assert plan["execution_cycle_id"] == CYCLE
        assert plan["remote_positions_by_instrument"] == {
            BUNDLE_INSTRUMENTS[0]: {
                "long_today": "2",
                "long_yesterday": "0",
                "short_today": "0",
                "short_yesterday": "0",
            },
            BUNDLE_INSTRUMENTS[1]: {
                "long_today": "2",
                "long_yesterday": "0",
                "short_today": "0",
                "short_yesterday": "0",
            },
            BUNDLE_INSTRUMENTS[2]: {
                "long_today": "0",
                "long_yesterday": "0",
                "short_today": "0",
                "short_yesterday": "0",
            },
        }
        assert plan["allowed_closes"] == [
            {
                "execution_cycle_id": CYCLE,
                "symbol": "SA701",
                "exchange_id": "CZCE",
                "position_side": "long",
                "side": "sell",
                "offset": "close",
                "quantity": "2",
                "quantity_unit": "contracts",
            },
            {
                "execution_cycle_id": CYCLE,
                "symbol": "SA701C1080",
                "exchange_id": "CZCE",
                "position_side": "long",
                "side": "sell",
                "offset": "close",
                "quantity": "2",
                "quantity_unit": "contracts",
            },
        ]
    finally:
        session.close()


def test_v2_bundle_recovery_requires_manual_intervention_for_unknown_leg(tmp_path):
    path = tmp_path / "bundle-orders.jsonl"
    write_bundle_crashed_journal(path)
    current_proof = bundle_proof(4)
    session, _proof_value = prepare_from_journal(path, new_proof=current_proof)
    current = snapshot(
        positions=[bundle_position("CZCE.SA701P1100")],
    )
    try:
        plan = session.build_recovery_plan(current, barrier=barrier(session, current))

        assert plan["status"] == "MANUAL_INTERVENTION"
        assert plan["allowed_actions"] == []
        assert plan["allowed_closes"] == []
        assert plan["recovery_token_sha256"] is None
        assert "recovery_remote_instrument_mismatch" in plan["evidence_errors"]
    finally:
        session.close()


def test_caller_stable_flag_cannot_bypass_sdk_query_barrier(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path)
    session, _proof_value = prepare_from_journal(path)
    try:
        plan = session.build_recovery_plan(
            {"positions": (), "orders": (), "trades": ()},
            stable=True,
        )
        assert plan["status"] == "MANUAL_INTERVENTION"
        assert plan["can_arm_recovery"] is False
        assert plan["allowed_actions"] == []
        assert "recovery_query_barrier_missing" in plan["evidence_errors"]
    finally:
        session.close()


def test_flat_plan_requires_explicit_completion_and_fresh_preflight(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, uncertain=True)
    session, current_proof = prepare_from_journal(path)
    empty = snapshot()
    try:
        plan = session.build_recovery_plan(empty, barrier=barrier(session, empty))
        token = plan["recovery_token_sha256"]
        assert plan["status"] == "FLAT"
        assert plan["recovery_required"] is False
        assert plan["can_arm_execution"] is True
        assert plan["can_arm_recovery"] is False
        assert plan["execution_cycle_id"] is None
        assert plan["allowed_actions"] == ["complete"]
        assert len(token) == 64

        plan["allowed_actions"].clear()
        assert session._recovery_plan["allowed_actions"] == ["complete"]
        with pytest.raises(NormalizedApiError) as raised:
            session.arm_from_preflight(current_proof, lambda: context(current_proof))
        assert raised.value.code == "execution_recovery_completion_required"

        completed = session.complete_recovery(
            token,
            empty,
            barrier(session, empty, first_id=11),
        )
        assert set(completed) == {
            "completed",
            "armed",
            "market_data_only",
            "recovery_only",
            "requires_new_preflight",
            "recovery_token_sha256",
        }
        assert completed["completed"] is True
        assert session._arm_proof is None
        assert session._arm_state_reader is None
        assert any(
            json.loads(line).get("status") == "reconciled_absent"
            for line in path.read_text().splitlines()
        )

        with pytest.raises(NormalizedApiError) as raised:
            session.arm_from_preflight(current_proof, lambda: context(current_proof))
        assert raised.value.code == "fresh_execution_preflight_required"
        fresh = proof(4, preflight_sha256="8" * 64)
        assert session.arm_from_preflight(fresh, lambda: context(fresh))["armed"] is True
    finally:
        session.close()


@pytest.mark.parametrize(
    ("position_side", "entry_side", "close_side"),
    [("long", "buy", "sell"), ("short", "sell", "buy")],
)
def test_czce_recovery_uses_side_frozen_and_generic_close(
    tmp_path, position_side, entry_side, close_side
):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, exposure=position_side)
    session, current_proof = prepare_from_journal(path)
    current = snapshot(
        positions=[position(side=position_side, frozen="1")],
        trades=[remote_trade(side=entry_side)],
    )
    try:
        plan = session.build_recovery_plan(current, barrier=barrier(session, current))
        assert plan["status"] == "RECOVERABLE"
        assert plan["execution_cycle_id"] == CYCLE
        assert plan["allowed_actions"] == ["close"]
        assert plan["allowed_closes"] == [
            {
                "execution_cycle_id": CYCLE,
                "symbol": "SA609",
                "exchange_id": "CZCE",
                "position_side": position_side,
                "side": close_side,
                "offset": "close",
                "quantity": "1",
                "quantity_unit": "contracts",
            }
        ]
        armed = session.arm_recovery_from_preflight(
            current_proof,
            plan["recovery_token_sha256"],
            lambda: context(current_proof),
            budget_capability=_reserve_budget(session, current_proof, mode="recovery"),
        )
        assert set(armed) == {
            "armed",
            "market_data_only",
            "proof_sha256",
            "recovery_only",
            "recovery_token_sha256",
            "execution_cycle_id",
        }
        assert armed["recovery_only"] is True
    finally:
        session.close()


def test_concurrent_ordinary_arm_cannot_borrow_recovery_capability(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, active=True)
    session, current_proof = prepare_from_journal(path)
    current = snapshot(orders=[active_order()])
    recovery_entered = threading.Event()
    ordinary_entered = threading.Event()
    release_recovery = threading.Event()
    results = queue.Queue()
    try:
        plan = session.build_recovery_plan(current, barrier=barrier(session, current))
        original_arm = session._arm_from_preflight

        def coordinated_arm(*args, **kwargs):
            if kwargs.get("_recovery_capability") is session._recovery_arm_capability:
                recovery_entered.set()
                assert release_recovery.wait(timeout=2)
            else:
                ordinary_entered.set()
            return original_arm(*args, **kwargs)

        session._arm_from_preflight = coordinated_arm

        def recover():
            try:
                    results.put(
                        (
                            "recovery",
                            session.arm_recovery_from_preflight(
                                current_proof,
                                plan["recovery_token_sha256"],
                                lambda: context(current_proof),
                                budget_capability=_reserve_budget(
                                    session, current_proof, mode="recovery"
                                ),
                            ),
                        )
                    )
            except Exception as exc:  # pragma: no cover - assertion reports it
                results.put(("recovery_error", exc))

        def arm_normally():
            try:
                results.put(
                    (
                        "ordinary",
                        session.arm_from_preflight(current_proof, lambda: context(current_proof)),
                    )
                )
            except Exception as exc:
                results.put(("ordinary_error", exc))

        recovery_thread = threading.Thread(target=recover)
        ordinary_thread = threading.Thread(target=arm_normally)
        recovery_thread.start()
        assert recovery_entered.wait(timeout=2)
        ordinary_thread.start()
        assert ordinary_entered.wait(timeout=2)
        release_recovery.set()
        recovery_thread.join(timeout=2)
        ordinary_thread.join(timeout=2)
        assert not recovery_thread.is_alive()
        assert not ordinary_thread.is_alive()

        outcomes = dict(results.get(timeout=1) for _ in range(2))
        assert outcomes["recovery"]["recovery_only"] is True
        assert isinstance(outcomes["ordinary_error"], NormalizedApiError)
        assert outcomes["ordinary_error"].code == "execution_recovery_arm_required"
    finally:
        release_recovery.set()
        session.close()


def test_public_recovery_arm_then_concurrent_disarm_finishes_read_only(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, active=True)
    api, session, feed = public_recovery_api(path)
    current_proof = proof(4)
    session.prepare_recovery(
        current_proof,
        lambda: context(current_proof),
        venue=VENUE,
    )
    current = snapshot(orders=[active_order()])
    plan = session.build_recovery_plan(current, barrier=barrier(session, current))
    api._ctp_execution_arm_context = lambda _venue: context(current_proof)
    authorization = _recovery_authorization(
        api,
        current_proof,
        cycle=plan["execution_cycle_id"],
    )
    original_arm = session.arm_recovery_from_preflight
    session_armed = threading.Event()
    release_arm_return = threading.Event()
    arm_result = queue.Queue()
    disarm_result = queue.Queue()

    def blocked_arm(*args, **kwargs):
        result = original_arm(*args, **kwargs)
        session_armed.set()
        assert release_arm_return.wait(timeout=5)
        return result

    session.arm_recovery_from_preflight = blocked_arm

    def run_arm():
        try:
            arm_result.put(
                api._arm_execution_recovery(
                    authorization=authorization,
                    recovery_token_sha256=plan["recovery_token_sha256"],
                )
            )
        except Exception as exc:  # pragma: no cover - assertion reports it
            arm_result.put(exc)

    def run_disarm():
        try:
            disarm_result.put(api.disarm_execution("concurrent_recovery_disarm"))
        except Exception as exc:  # pragma: no cover - assertion reports it
            disarm_result.put(exc)

    arm_thread = threading.Thread(target=run_arm)
    disarm_thread = threading.Thread(target=run_disarm)
    try:
        arm_thread.start()
        assert session_armed.wait(timeout=5)
        disarm_thread.start()
        assert disarm_result.empty()

        release_arm_return.set()
        arm_thread.join(timeout=5)
        disarm_thread.join(timeout=5)

        assert not arm_thread.is_alive()
        assert not disarm_thread.is_alive()
        assert arm_result.get_nowait()["recovery_only"] is True
        assert disarm_result.get_nowait()["market_data_only"] is True
        assert session.config["market_data_only"] is True
        assert session._recovery_mode is False
        assert feed.get_execution_gate_state()["armed"] is False
    finally:
        release_arm_return.set()
        arm_thread.join(timeout=5)
        disarm_thread.join(timeout=5)
        session.close()


def test_fully_frozen_nonzero_exposure_is_manual_and_cannot_arm(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, exposure="long")
    session, current_proof = prepare_from_journal(path)
    current = snapshot(
        positions=[position(side="long", frozen="2")],
        trades=[remote_trade(side="buy")],
    )
    try:
        plan = session.build_recovery_plan(current, barrier=barrier(session, current))
        assert plan["status"] == "MANUAL_INTERVENTION"
        assert plan["allowed_actions"] == []
        assert plan["allowed_closes"] == []
        assert "recovery_no_safe_action" in plan["evidence_errors"]
        with pytest.raises(NormalizedApiError) as raised:
            session.arm_recovery_from_preflight(
                current_proof,
                plan["recovery_token_sha256"],
                lambda: context(current_proof),
            )
        assert raised.value.code == "invalid_or_consumed_recovery_token"
    finally:
        session.close()


@pytest.mark.parametrize("damage", ["position_identity", "order_semantics"])
def test_remote_identity_or_active_order_semantic_mismatch_is_manual(tmp_path, damage):
    path = tmp_path / "orders.jsonl"
    if damage == "position_identity":
        write_crashed_journal(path, exposure="long")
        row = position(side="long")
        row.pop("evidence_complete")
        current = snapshot(positions=[row], trades=[remote_trade(side="buy")])
    else:
        write_crashed_journal(path, active=True)
        current = snapshot(orders=[active_order(quantity="3")])
    session, _current_proof = prepare_from_journal(path)
    try:
        plan = session.build_recovery_plan(current, barrier=barrier(session, current))
        assert plan["status"] == "MANUAL_INTERVENTION"
        assert plan["allowed_actions"] == []
        assert plan["allowed_cancels"] == []
        assert plan["allowed_closes"] == []
    finally:
        session.close()


def test_recovery_close_blocks_open_reverse_wrong_cycle_and_oversize(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, exposure="long")
    session, current_proof = prepare_from_journal(path)
    current = snapshot(
        positions=[position(side="long", frozen="0")],
        trades=[remote_trade(side="buy")],
    )
    transport = Mock(return_value=order_update())
    try:
        plan = session.build_recovery_plan(current, barrier=barrier(session, current))
        session.arm_recovery_from_preflight(
            current_proof,
            plan["recovery_token_sha256"],
            lambda: context(current_proof),
            budget_capability=_reserve_budget(session, current_proof, mode="recovery"),
        )
        rejected = (
            order_request(client_order_id="000000000002"),
            order_request(
                side=Side.BUY,
                quantity="1",
                offset="close",
                position_side="long",
                role="recovery_exit",
                client_order_id="000000000002",
            ),
            order_request(
                side=Side.SELL,
                quantity="1",
                offset="close",
                position_side="long",
                role="recovery_exit",
                client_order_id="000000000002",
                cycle="other-cycle",
            ),
            order_request(
                side=Side.SELL,
                quantity="3",
                offset="close",
                position_side="long",
                role="recovery_exit",
                client_order_id="000000000002",
            ),
        )
        for request in rejected:
            with pytest.raises(NormalizedApiError):
                session.invoke("make_order", VENUE, request, transport)
        assert session.submit_calls == 0
        transport.assert_not_called()

        allowed = order_request(
            side=Side.SELL,
            quantity="2",
            offset="close",
            position_side="long",
            role="recovery_exit",
            client_order_id="000000000002",
        )
        result = session.invoke(
            "make_order",
            VENUE,
            allowed,
            Mock(
                return_value=order_update(
                    side="sell",
                    quantity="2",
                    client_order_id="000000000002",
                    order_id="SYS2",
                    offset="close",
                    position_side="long",
                )
            ),
        )
        assert result["status"] == "accepted"
        with pytest.raises(NormalizedApiError):
            session.invoke("make_order", VENUE, allowed, transport)
        transport.assert_not_called()
    finally:
        session.close()


def test_public_sync_recovery_exit_bypasses_entry_loss_latch_only(tmp_path):
    session = breached_recovery_session(tmp_path / "sync-orders.jsonl")
    submit = Mock(
        return_value=order_update(
            side="sell",
            quantity="1",
            client_order_id="000000000002",
            order_id="SYS2",
            offset="close",
            position_side="long",
        )
    )
    api = public_order_api(session, SimpleNamespace(make_order=submit))
    entry = order_request(client_order_id="000000000003")
    close = order_request(
        side=Side.SELL,
        quantity="1",
        offset="close",
        position_side="long",
        role="recovery_exit",
        client_order_id="000000000002",
    )
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api.make_order(VENUE, entry, normalized=True)
        assert raised.value.code == "execution_recovery_open_forbidden"
        submit.assert_not_called()

        assert api.make_order(VENUE, close, normalized=True)["status"] == "accepted"
        submit.assert_called_once()
    finally:
        session.close()


def test_public_async_recovery_exit_bypasses_entry_loss_latch_only(tmp_path):
    session = breached_recovery_session(tmp_path / "async-orders.jsonl")
    submit = AsyncMock(
        return_value=order_update(
            side="sell",
            quantity="1",
            client_order_id="000000000002",
            order_id="SYS2",
            offset="close",
            position_side="long",
        )
    )
    api = public_order_api(session, SimpleNamespace(async_make_order=submit))
    entry = order_request(client_order_id="000000000003")
    close = order_request(
        side=Side.SELL,
        quantity="1",
        offset="close",
        position_side="long",
        role="recovery_exit",
        client_order_id="000000000002",
    )

    async def run():
        with pytest.raises(NormalizedApiError) as raised:
            await api.async_make_order(VENUE, entry, normalized=True)
        assert raised.value.code == "execution_recovery_open_forbidden"
        submit.assert_not_awaited()

        result = await api.async_make_order(VENUE, close, normalized=True)
        assert result["status"] == "accepted"
        submit.assert_awaited_once()

    try:
        asyncio.run(run())
    finally:
        session.close()


def test_cancel_allowance_is_atomic_one_shot_and_refresh_rotates_token(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, active=True)
    session, current_proof = prepare_from_journal(path)
    current = snapshot(orders=[active_order()])
    cancel = CancelOrderRequest(
        symbol="SA609.CZCE",
        account_id=ACCOUNT,
        client_order_id="000000000001",
        order_id="SYS1",
        exchange_id="CZCE",
        front_id=11,
        session_id=22,
        order_ref="000000000001",
    )
    transport = Mock(return_value=order_update(status="canceled", terminal=True))
    try:
        plan = session.build_recovery_plan(current, barrier=barrier(session, current))
        old_token = plan["recovery_token_sha256"]
        assert plan["allowed_actions"] == ["cancel"]
        session.arm_recovery_from_preflight(
            current_proof,
            old_token,
            lambda: context(current_proof),
            budget_capability=_reserve_budget(session, current_proof, mode="recovery"),
        )
        session.invoke("cancel_order", VENUE, cancel, transport)
        with pytest.raises(NormalizedApiError) as raised:
            session.invoke("cancel_order", VENUE, cancel, transport)
        assert raised.value.code == "execution_recovery_foreign_cancel"
        transport.assert_called_once_with()

        session.pause_recovery()
        session.prepare_recovery(
            current_proof,
            lambda: context(current_proof),
            venue=VENUE,
        )
        empty = snapshot()
        refreshed = session.build_recovery_plan(empty, barrier=barrier(session, empty, first_id=21))
        assert refreshed["status"] == "FLAT"
        assert refreshed["recovery_token_sha256"] != old_token
    finally:
        session.close()


def test_failed_recovery_transport_consumes_action_and_pauses_lease(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, active=True)
    session, current_proof = prepare_from_journal(path)
    current = snapshot(orders=[active_order()])
    cancel = CancelOrderRequest(
        symbol="SA609.CZCE",
        account_id=ACCOUNT,
        client_order_id="000000000001",
        order_id="SYS1",
        exchange_id="CZCE",
    )
    failed = Mock(side_effect=TimeoutError("unknown cancel result"))
    retry = Mock()
    try:
        plan = session.build_recovery_plan(current, barrier=barrier(session, current))
        session.arm_recovery_from_preflight(
            current_proof,
            plan["recovery_token_sha256"],
            lambda: context(current_proof),
            budget_capability=_reserve_budget(session, current_proof, mode="recovery"),
        )
        result = session.invoke("cancel_order", VENUE, cancel, failed)
        assert result["execution_unknown"] is True
        assert session.config["market_data_only"] is True
        assert session._recovery_mode is False
        with pytest.raises(NormalizedApiError):
            session.invoke("cancel_order", VENUE, cancel, retry)
        failed.assert_called_once_with()
        retry.assert_not_called()
    finally:
        session.close()


def test_sync_recovery_dispatch_blocks_sync_and_async_contenders(tmp_path):
    session = breached_recovery_session(tmp_path / "orders.jsonl")
    entered = threading.Event()
    release = threading.Event()
    owner_result = queue.Queue()
    owner = order_request(
        side=Side.SELL,
        quantity="1",
        offset="close",
        position_side="long",
        role="recovery_exit",
        client_order_id="000000000002",
    )
    contender = order_request(
        side=Side.SELL,
        quantity="1",
        offset="close",
        position_side="long",
        role="recovery_exit",
        client_order_id="000000000003",
    )

    def transport():
        entered.set()
        assert release.wait(timeout=5)
        return order_update(
            side="sell",
            quantity="1",
            client_order_id="000000000002",
            order_id="SYS2",
            offset="close",
            position_side="long",
        )

    def run_owner():
        try:
            owner_result.put(session.invoke("make_order", VENUE, owner, transport))
        except Exception as exc:  # pragma: no cover - assertion reports it
            owner_result.put(exc)

    async_transport = AsyncMock()
    owner_thread = threading.Thread(target=run_owner)
    try:
        owner_thread.start()
        assert entered.wait(timeout=5)
        assert session._recovery_dispatch_in_progress is True

        with pytest.raises(NormalizedApiError) as sync_error:
            session.invoke("make_order", VENUE, contender, Mock())
        assert sync_error.value.code == "execution_recovery_action_in_progress"

        with pytest.raises(NormalizedApiError) as async_error:
            asyncio.run(session.async_invoke("make_order", VENUE, contender, async_transport))
        assert async_error.value.code == "execution_recovery_action_in_progress"
        async_transport.assert_not_awaited()

        release.set()
        owner_thread.join(timeout=5)
        assert not owner_thread.is_alive()
        assert owner_result.get_nowait()["status"] == "accepted"
        assert session._recovery_dispatch_in_progress is False
    finally:
        release.set()
        owner_thread.join(timeout=5)
        session.close()


def test_async_recovery_cancel_releases_claim_and_disarms_native_gate(tmp_path):
    session = breached_recovery_session(tmp_path / "orders.jsonl")
    entered = asyncio.Event()
    owner = order_request(
        side=Side.SELL,
        quantity="1",
        offset="close",
        position_side="long",
        role="recovery_exit",
        client_order_id="000000000002",
    )
    contender = order_request(
        side=Side.SELL,
        quantity="1",
        offset="close",
        position_side="long",
        role="recovery_exit",
        client_order_id="000000000003",
    )
    sync_transport = Mock()

    async def blocked_transport(*_args):
        entered.set()
        await asyncio.Future()

    api, feed, stream = public_recovery_order_api(
        session,
        SimpleNamespace(async_make_order=blocked_transport, make_order=sync_transport),
    )

    async def run():
        task = asyncio.create_task(api.async_make_order(VENUE, owner, normalized=True))
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert session._recovery_dispatch_in_progress is True

        with pytest.raises(NormalizedApiError) as raised:
            api.make_order(VENUE, contender, normalized=True)
        assert raised.value.code == "execution_recovery_action_in_progress"
        sync_transport.assert_not_called()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(run())
        assert session._recovery_dispatch_in_progress is False
        assert session.config["market_data_only"] is True
        assert session._recovery_mode is False
        assert feed.get_execution_gate_state()["armed"] is False
        assert feed.disarm_calls[-1] == "execution_recovery_action_requires_refresh"
        assert stream in api._subscription_streams
        assert stream._running is True
        assert api._subscription_flags[f"{VENUE}_account"] is True
        stream.push(
            order_update(
                side="sell",
                status="canceled",
                terminal=True,
                client_order_id="000000000002",
                order_id="SYS2",
                offset="close",
                position_side="long",
            )
        )
        assert api.poll_event(VENUE)["kind"] == "order"
    finally:
        session.close()


def test_failed_public_recovery_transport_disarms_native_gate_before_return(tmp_path):
    session = breached_recovery_session(tmp_path / "orders.jsonl")
    failed = Mock(side_effect=TimeoutError("unknown recovery result"))
    api, feed, stream = public_recovery_order_api(
        session,
        SimpleNamespace(make_order=failed),
    )
    close = order_request(
        side=Side.SELL,
        quantity="2",
        offset="close",
        position_side="long",
        role="recovery_exit",
        client_order_id="000000000002",
    )
    try:
        result = api.make_order(VENUE, close, normalized=True)
        assert result["execution_unknown"] is True
        assert session._recovery_dispatch_in_progress is False
        assert session.config["market_data_only"] is True
        assert session._recovery_mode is False
        assert feed.get_execution_gate_state()["armed"] is False
        assert feed.disarm_calls[-1] == "execution_recovery_action_requires_refresh"
        failed.assert_called_once_with(VENUE, close)
        assert stream in api._subscription_streams
        assert stream._running is True
        assert api._subscription_flags[f"{VENUE}_account"] is True
        stream.push(
            order_update(
                side="sell",
                status="canceled",
                terminal=True,
                client_order_id="000000000002",
                order_id="SYS2",
                offset="close",
                position_side="long",
            )
        )
        assert api.poll_event(VENUE)["kind"] == "order"
    finally:
        session.close()


def test_pending_private_ingress_blocks_write_before_transport(tmp_path):
    session = breached_recovery_session(tmp_path / "orders.jsonl")
    transport = Mock()
    api, feed, stream = public_recovery_order_api(
        session,
        SimpleNamespace(make_order=transport),
    )
    note_entered = threading.Event()
    release_note = threading.Event()
    original_note = session.note_private_ingress

    def blocked_note(venue, ordered_after_write=False):
        note_entered.set()
        assert release_note.wait(timeout=5)
        return original_note(
            venue,
            ordered_after_write=ordered_after_write,
        )

    session.note_private_ingress = blocked_note
    producer = threading.Thread(
        target=stream.push,
        args=(
            order_update(
                client_order_id="000000000099",
                order_id="SYS99",
            ),
        ),
    )
    producer.start()
    try:
        assert note_entered.wait(timeout=5)
        close = order_request(
            side=Side.SELL,
            quantity="1",
            offset="close",
            position_side="long",
            role="recovery_exit",
            client_order_id="000000000002",
        )
        with pytest.raises(NormalizedApiError) as raised:
            api.make_order(VENUE, close, normalized=True)
        assert raised.value.code == "execution_private_event_pending"
        transport.assert_not_called()
    finally:
        release_note.set()
        producer.join(timeout=5)
        session.close()
    assert not producer.is_alive()
    assert session.config["market_data_only"] is True
    assert feed.get_execution_gate_state()["armed"] is False


@pytest.mark.parametrize(
    "journal_kind",
    ["unknown", "active", "exposure"],
)
def test_ordinary_arm_rejects_restart_journal_before_native_gate(tmp_path, journal_kind):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(
        path,
        exposure="long" if journal_kind == "exposure" else None,
        active=journal_kind == "active",
        uncertain=journal_kind == "unknown",
    )
    session = make_session(path)
    current_proof = proof(4)
    prepare_execution = Mock()
    try:
        with pytest.raises(NormalizedApiError) as raised:
            session.arm_from_preflight(
                current_proof,
                lambda: context(current_proof),
                prepare_execution=prepare_execution,
            )
        assert raised.value.code == "execution_recovery_required"
        prepare_execution.assert_not_called()
        assert session.config["market_data_only"] is True
    finally:
        session.close()


@pytest.mark.parametrize("arm_trading_day", [TRADING_DAY, "20260910"])
def test_ordinary_arm_allows_durable_cycle_only_after_entry_and_exit_net_flat(
    tmp_path, arm_trading_day
):
    path = tmp_path / "orders.jsonl"
    old_proof = proof(3)
    writer = make_session(path)
    writer.arm_from_preflight(old_proof, lambda: context(old_proof))
    writer.invoke(
        "make_order",
        VENUE,
        order_request(),
        lambda: order_update(),
        budget_capability=_reserve_budget(writer, old_proof),
    )
    writer.event(VENUE, trade_update())
    writer.event(
        VENUE,
        order_update(status="completed", terminal=True),
    )
    close_request = order_request(
        side=Side.SELL,
        offset="close",
        position_side="long",
        role="exit",
        client_order_id="000000000002",
    )
    writer.invoke(
        "make_order",
        VENUE,
        close_request,
        lambda: order_update(
            side="sell",
            client_order_id="000000000002",
            order_id="SYS2",
            offset="close",
            position_side="long",
        ),
        budget_capability=_reserve_budget(writer, old_proof),
    )
    writer.event(
        VENUE,
        trade_update(
            side="sell",
            position_side="long",
            client_order_id="000000000002",
            order_id="SYS2",
            offset="close",
            trade_id="TRADE2",
        ),
    )
    writer.event(
        VENUE,
        order_update(
            side="sell",
            status="completed",
            terminal=True,
            client_order_id="000000000002",
            order_id="SYS2",
            offset="close",
            position_side="long",
        ),
    )
    writer.close()

    reader = make_session(path)
    generation = 4 if arm_trading_day == TRADING_DAY else 1
    current_proof = proof(generation, trading_day=arm_trading_day)
    native_gate = Mock()
    try:
        result = reader.arm_from_preflight(
            current_proof,
            lambda: context(current_proof),
            prepare_execution=native_gate,
        )
        assert result["armed"] is True
        native_gate.assert_called_once_with()
    finally:
        reader.close()


def test_old_day_terminal_zero_fill_cycle_does_not_block_new_day(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path)
    reader = make_session(path)
    new_day_proof = proof(1, trading_day="20260910")
    native_gate = Mock()
    try:
        result = reader.arm_from_preflight(
            new_day_proof,
            lambda: context(new_day_proof),
            prepare_execution=native_gate,
        )
        assert result["armed"] is True
        native_gate.assert_called_once_with()
    finally:
        reader.close()


def test_old_day_reconciled_absent_cycle_does_not_block_new_day(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, uncertain=True)
    recovery, _current_proof = prepare_from_journal(path)
    empty = snapshot()
    try:
        plan = recovery.build_recovery_plan(
            empty,
            barrier=barrier(recovery, empty),
        )
        recovery.complete_recovery(
            plan["recovery_token_sha256"],
            empty,
            barrier(recovery, empty, first_id=11),
        )
    finally:
        recovery.close()

    reader = make_session(path)
    new_day_proof = proof(
        1,
        trading_day="20260910",
        preflight_sha256="8" * 64,
    )
    native_gate = Mock()
    try:
        result = reader.arm_from_preflight(
            new_day_proof,
            lambda: context(new_day_proof),
            prepare_execution=native_gate,
        )
        assert result["armed"] is True
        native_gate.assert_called_once_with()
    finally:
        reader.close()


@pytest.mark.parametrize("journal_kind", ["active", "unknown", "exposure"])
def test_old_day_unresolved_unknown_or_exposed_cycle_remains_fail_closed(tmp_path, journal_kind):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(
        path,
        exposure="long" if journal_kind == "exposure" else None,
        active=journal_kind == "active",
        uncertain=journal_kind == "unknown",
    )
    reader = make_session(path)
    new_day_proof = proof(1, trading_day="20260910")
    native_gate = Mock()
    try:
        with pytest.raises(NormalizedApiError) as raised:
            reader.arm_from_preflight(
                new_day_proof,
                lambda: context(new_day_proof),
                prepare_execution=native_gate,
            )
        assert raised.value.code == "execution_recovery_required"
        native_gate.assert_not_called()
        assert reader.config["market_data_only"] is True
    finally:
        reader.close()


def test_ordinary_arm_rejects_terminal_fill_without_durable_trade(tmp_path):
    path = tmp_path / "orders.jsonl"
    old_proof = proof(3)
    writer = make_session(path)
    writer.arm_from_preflight(old_proof, lambda: context(old_proof))
    writer.invoke(
        "make_order",
        VENUE,
        order_request(),
        lambda: order_update(),
        budget_capability=_reserve_budget(writer, old_proof),
    )
    writer.event(
        VENUE,
        order_update(status="completed", terminal=True),
    )
    writer.close()

    reader = make_session(path)
    current_proof = proof(4)
    native_gate = Mock()
    try:
        with pytest.raises(NormalizedApiError) as raised:
            reader.arm_from_preflight(
                current_proof,
                lambda: context(current_proof),
                prepare_execution=native_gate,
            )
        assert raised.value.code == "execution_recovery_required"
        native_gate.assert_not_called()
    finally:
        reader.close()


@pytest.mark.parametrize("damage", ["corrupt", "old_schema", "strategy_identity"])
def test_damaged_or_mismatched_journal_is_manual(tmp_path, damage):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, active=True)
    if damage == "corrupt":
        path.write_text(path.read_text() + "{not-json}\n")
    else:
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            if damage == "old_schema":
                row["schema_version"] = 1
            else:
                row["strategy_identity_sha256"] = "9" * 64
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    session, _current_proof = prepare_from_journal(path)
    current = snapshot(orders=[active_order()])
    try:
        plan = session.build_recovery_plan(current, barrier=barrier(session, current))
        assert plan["status"] == "MANUAL_INTERVENTION"
        assert plan["allowed_actions"] == []
        assert plan["can_arm_execution"] is False
        assert plan["can_arm_recovery"] is False
    finally:
        session.close()


def test_public_recovery_report_is_exact_and_complete_runs_new_barrier(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, uncertain=True)
    api, session, feed = public_recovery_api(path)
    first_results = iter(query_rounds("100", "100", first_id=1))
    api.query_ctp_result = lambda _venue, _query_type: next(first_results)
    try:
        report = api.prepare_execution_recovery(proof=proof(4))
        assert set(report) == {
            "schema_version",
            "status",
            "recovery_required",
            "can_arm_execution",
            "can_arm_recovery",
            "account_fingerprint",
            "trading_day",
            "instrument",
            "connection_generation",
            "strategy_id",
            "execution_cycle_id",
            "remote_position",
            "owned_position",
            "allowed_closes",
            "allowed_cancels",
            "allowed_actions",
            "unknown_ids",
            "evidence_errors",
            "journal_sha256",
            "fencing_epoch",
            "recovery_token_sha256",
        }
        assert report["schema_version"] == "bt_api.execution-recovery.v1"
        assert report["status"] == "FLAT"
        second_results = iter(query_rounds("100", "100", first_id=9))
        api.query_ctp_result = lambda _venue, _query_type: next(second_results)
        completed = api.complete_execution_recovery(
            recovery_token_sha256=report["recovery_token_sha256"]
        )
        assert completed["completed"] is True
        assert feed.get_execution_gate_state()["armed"] is False
    finally:
        session.close()


def test_public_v2_bundle_recovery_report_preserves_scope_and_per_leg_maps(tmp_path):
    """The public report must retain the C/P/F recovery evidence V2 needs."""
    path = tmp_path / "bundle-orders.jsonl"
    api, session, _feed = public_recovery_api(path)
    results = iter(query_rounds("100", "100", first_id=1))
    api.query_ctp_result = lambda _venue, _query_type: next(results)
    current_proof = bundle_proof(4)
    try:
        report = api.prepare_execution_recovery(proof=current_proof)

        assert report["scope_version"] == BUNDLE_SCOPE_VERSION
        assert report["authorized_instruments"] == BUNDLE_INSTRUMENTS
        assert set(report["remote_positions_by_instrument"]) == set(BUNDLE_INSTRUMENTS)
        assert set(report["owned_positions_by_instrument"]) == set(BUNDLE_INSTRUMENTS)
        report["authorized_instruments"].clear()
        assert session._recovery_plan["authorized_instruments"] == BUNDLE_INSTRUMENTS
    finally:
        session.close()


def test_public_recovery_can_reuse_generation_after_ordinary_arm_requires_recovery(
    tmp_path,
):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, uncertain=True)
    api, session, feed = public_recovery_api(path)
    current_proof = proof(4)
    api._ctp_execution_arm_context = lambda _venue: context(current_proof)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_preflight(current_proof)
        assert raised.value.code == "ctp_execution_authorization_required"
        assert session.config["market_data_only"] is True
        assert session._arm_revoked_reason is None
        assert feed.get_execution_gate_state()["armed"] is False

        query_results = iter(query_rounds("100", "100", first_id=1))
        api.query_ctp_result = lambda _venue, _query_type: next(query_results)
        report = api.prepare_execution_recovery(proof=current_proof)
        assert report["status"] == "FLAT"
        assert report["connection_generation"] == 4
        assert report["allowed_actions"] == ["complete"]
    finally:
        session.close()


def test_public_barrier_retries_account_drift_and_event_revision(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, uncertain=True)
    api, session, _feed = public_recovery_api(path)
    results = iter(query_rounds("100", "101", "102", "102", "103", "103", first_id=1))
    calls = 0

    def query(_venue, _query_type):
        nonlocal calls
        calls += 1
        if calls == 13:
            session._recovery_event_revision += 1
        return next(results)

    api.query_ctp_result = query
    try:
        report = api.prepare_execution_recovery(proof=proof(4))
        assert report["status"] == "FLAT"
        assert report["evidence_errors"] == []
        assert session._recovery_plan["query_barrier"]["attempts"] == 3
        assert calls == 24
    finally:
        session.close()


@pytest.mark.parametrize("channel", ["account_stream", "reconcile_pending"])
def test_recovery_arm_rejects_ingress_started_after_final_queue_drain(tmp_path, channel):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, active=True)
    api, session, feed = public_recovery_api(path)
    current_proof = proof(4)
    session.prepare_recovery(
        current_proof,
        lambda: context(current_proof),
        venue=VENUE,
    )
    current = snapshot(orders=[active_order()])
    report = session.build_recovery_plan(
        current,
        barrier=barrier(session, current),
    )
    assert report["status"] == "RECOVERABLE"
    api._ctp_execution_arm_context = lambda _venue: context(current_proof)
    authorization = _recovery_authorization(
        api,
        current_proof,
        cycle=report["execution_cycle_id"],
    )

    producer_queue = api._ctp_private_stream_queue(
        VENUE,
        api.data_queues[VENUE],
    )
    final_drain_complete = threading.Event()
    note_entered = threading.Event()
    release_note = threading.Event()
    producer_done = threading.Event()
    original_ingest = api._ingest_ctp_private_queue
    original_note = session.note_private_ingress
    ingest_calls = 0

    def blocked_note(venue, ordered_after_write=False):
        note_entered.set()
        assert release_note.wait(timeout=5)
        return original_note(
            venue,
            ordered_after_write=ordered_after_write,
        )

    def ingest(*args, **kwargs):
        nonlocal ingest_calls
        value = original_ingest(*args, **kwargs)
        ingest_calls += 1
        if ingest_calls == 2:
            final_drain_complete.set()
            assert note_entered.wait(timeout=5)
        return value

    def produce():
        assert final_drain_complete.wait(timeout=5)
        event = order_update(
            client_order_id="000000000099",
            order_id="SYS99",
        )
        if channel == "account_stream":
            producer_queue.put(event)
        else:
            api._publish_private_event(VENUE, event, normalized=True)
        producer_done.set()

    session.note_private_ingress = blocked_note
    api._ingest_ctp_private_queue = ingest
    producer = threading.Thread(target=produce)
    producer.start()
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api._arm_execution_recovery(
                authorization=authorization,
                recovery_token_sha256=report["recovery_token_sha256"],
            )
        assert raised.value.code == "execution_recovery_required"
        assert session.config["market_data_only"] is True
        assert feed.get_execution_gate_state()["armed"] is False
        queued = api.data_queues[VENUE].qsize()
        pending = len(api._normalized_event_pending[VENUE])
        assert queued + pending == 1
    finally:
        release_note.set()
        producer.join(timeout=5)
        session.close()
    assert producer_done.is_set()
    assert not producer.is_alive()


@pytest.mark.parametrize(
    ("arrival", "private_event"),
    [
        (
            "before_rounds",
            order_update(
                client_order_id="000000000099",
                order_id="SYS99",
            ),
        ),
        (
            "between_rounds",
            trade_update(
                client_order_id="000000000099",
                order_id="SYS99",
                trade_id="TRADE99",
            ),
        ),
        (
            "after_rounds",
            order_update(
                client_order_id="000000000099",
                order_id="SYS99",
            ),
        ),
    ],
)
def test_public_barrier_accounts_for_private_queue_events(tmp_path, arrival, private_event):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, uncertain=True)
    api, session, _feed = public_recovery_api(path)
    results = iter(query_rounds(*(["100"] * 6), first_id=1))
    calls = 0
    inject_after = {"between_rounds": 4, "after_rounds": 8}.get(arrival)
    if arrival == "before_rounds":
        api.data_queues[VENUE].put(dict(private_event))

    def query(_venue, _query_type):
        nonlocal calls
        calls += 1
        result = next(results)
        if calls == inject_after:
            api.data_queues[VENUE].put(dict(private_event))
        return result

    api.query_ctp_result = query
    try:
        report = api.prepare_execution_recovery(proof=proof(4))
        assert report["status"] == "MANUAL_INTERVENTION"
        assert report["can_arm_execution"] is False
        assert report["can_arm_recovery"] is False
        assert "recovery_snapshot_not_stable" in report["evidence_errors"]
        assert "recovery_private_event_fence_invalid" in report["evidence_errors"]
        assert api.poll_event(VENUE)["kind"] == private_event["kind"]
    finally:
        session.close()


def test_private_event_after_flat_plan_revokes_completion_eligibility(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, uncertain=True)
    api, session, feed = public_recovery_api(path)
    prepare_results = iter(query_rounds("100", "100", first_id=1))
    api.query_ctp_result = lambda _venue, _query_type: next(prepare_results)
    try:
        report = api.prepare_execution_recovery(proof=proof(4))
        assert report["status"] == "FLAT"

        api.data_queues[VENUE].put(
            order_update(
                status="canceled",
                terminal=True,
                client_order_id="000000000099",
                order_id="SYS99",
            )
        )
        assert api.poll_event(VENUE)["status"] == "canceled"

        complete_results = iter(query_rounds("100", "100", first_id=9))
        api.query_ctp_result = lambda _venue, _query_type: next(complete_results)
        with pytest.raises(NormalizedApiError) as raised:
            api.complete_execution_recovery(recovery_token_sha256=report["recovery_token_sha256"])
        assert raised.value.code == "invalid_recovery_token"
        assert session.config["market_data_only"] is True
        assert feed.get_execution_gate_state()["armed"] is False
    finally:
        session.close()


def test_private_event_after_flat_completion_blocks_fresh_ordinary_arm(tmp_path):
    path = tmp_path / "orders.jsonl"
    write_crashed_journal(path, uncertain=True)
    api, session, feed = public_recovery_api(path)
    prepare_results = iter(query_rounds("100", "100", first_id=1))
    api.query_ctp_result = lambda _venue, _query_type: next(prepare_results)
    try:
        report = api.prepare_execution_recovery(proof=proof(4))
        assert report["status"] == "FLAT"

        complete_results = iter(query_rounds("100", "100", first_id=9))
        api.query_ctp_result = lambda _venue, _query_type: next(complete_results)
        api.complete_execution_recovery(recovery_token_sha256=report["recovery_token_sha256"])

        api.data_queues[VENUE].put(
            order_update(
                status="canceled",
                terminal=True,
                client_order_id="000000000099",
                order_id="SYS99",
            )
        )
        assert api.poll_event(VENUE)["status"] == "canceled"
        fresh = proof(4, preflight_sha256="8" * 64)
        api._ctp_execution_arm_context = lambda _venue: context(fresh)
        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_preflight(fresh)
        assert raised.value.code == "ctp_execution_authorization_required"
        assert session.config["market_data_only"] is True
        assert feed.get_execution_gate_state()["armed"] is False
    finally:
        session.close()


def test_custom_ctp_instance_injects_capability_for_sync_and_async_writes():
    venue = "ctp___simnow_custom"
    capability = object()

    class Feed:
        def __init__(self):
            self.calls = []

        def make_order(self, *args, **kwargs):
            self.calls.append(("make", args, kwargs))
            return "sync-make"

        async def async_make_order(self, *args, **kwargs):
            self.calls.append(("async-make", args, kwargs))
            return "async-make"

        def cancel_order(self, *args, **kwargs):
            self.calls.append(("cancel", args, kwargs))
            return "sync-cancel"

        async def async_cancel_order(self, *args, **kwargs):
            self.calls.append(("async-cancel", args, kwargs))
            return "async-cancel"

    feed = Feed()
    backend = DirectBackend(lambda _venue: feed, {venue: feed}, capability)
    request = order_request()
    cancel = CancelOrderRequest(
        symbol=request.symbol,
        account_id=request.account_id,
        client_order_id=request.client_order_id,
        order_id="SYS1",
        exchange_id="CZCE",
        order_ref=request.client_order_id,
    )

    assert backend.make_order(venue, request) == "sync-make"
    assert backend.cancel_order(venue, cancel) == "sync-cancel"
    assert asyncio.run(backend.async_make_order(venue, request)) == "async-make"
    assert asyncio.run(backend.async_cancel_order(venue, cancel)) == "async-cancel"
    assert [call[0] for call in feed.calls] == [
        "make",
        "cancel",
        "async-make",
        "async-cancel",
    ]
    assert all(call[2]["_execution_capability"] is capability for call in feed.calls)


def test_prepare_authorization_is_reusable_before_first_arm(tmp_path):
    session = make_session(tmp_path / "orders.jsonl")
    current_proof = proof(3)
    try:
        prepared = session.prepare_execution_authorization()
        assert prepared["prepared"] is True
        assert prepared["reusable"] is True
        assert prepared["minimum_next_generation"] is None
        assert (
            session.arm_from_preflight(current_proof, lambda: context(current_proof))["armed"]
            is True
        )
    finally:
        session.close()


def test_prepare_authorization_fences_old_generation_but_allows_new_proof(tmp_path):
    session = make_session(tmp_path / "orders.jsonl")
    old_proof = proof(3)
    new_proof = proof(4, preflight_sha256="8" * 64)
    try:
        session.arm_from_preflight(old_proof, lambda: context(old_proof))
        prepared = session.prepare_execution_authorization("execution_authorization_reconfigured")
        assert prepared["minimum_next_generation"] == 4
        with pytest.raises(NormalizedApiError) as raised:
            session.arm_from_preflight(old_proof, lambda: context(old_proof))
        assert raised.value.code == "execution_arm_revoked"
        assert session.arm_from_preflight(new_proof, lambda: context(new_proof))["armed"] is True
    finally:
        session.close()
