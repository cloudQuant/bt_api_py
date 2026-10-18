"""Ordinary DAY close requests retain their typed terms through Risk proof.

Only the provider feed is synthetic. Admission, order journaling, close-position
reservation, fill reconciliation, and terminal close proof use the real local
SQLite Risk and service components.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py._venue_mappers import MAPPERS
from bt_api_py._venue_mappers.ctp import map_order_request as map_ctp_order
from bt_api_py.bt_api import BtApi
from bt_api_py.service_risk_order_entry import RiskOrderEntryService, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal, canonical_json, sha256_text
from bt_api_risk import (
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)
from tests.unit import test_service_risk_order_exposure_integration as fixtures


class _FilledCloseFeed(fixtures._V2SyntheticBtApiFeed):
    """The provider leaf records a full close with explicit source dimensions."""

    def __init__(self, path, *, position_side, position_bucket, offset):
        super().__init__(str(path), ("filled",))
        self.sent = []
        self.sent_args = []
        self._source_dimensions = {
            "position_side": position_side.lower(),
            "position_bucket": position_bucket,
            "offset": offset,
        }

    def make_order(self, *args, **kwargs):
        self.sent.append(dict(kwargs))
        self.sent_args.append(tuple(args))
        response = super().make_order(*args, **kwargs)
        client_order_id = kwargs["client_order_id"]
        with sqlite3.connect(self._ledger_path) as connection:
            row_text, fills_text = connection.execute(
                "SELECT order_json,fills_json FROM provider_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            row = json.loads(row_text)
            row.update(self._source_dimensions)
            fills = json.loads(fills_text)
            for fill in fills:
                fill.update(self._source_dimensions)
            connection.execute(
                "UPDATE provider_orders SET order_json=?,fills_json=? WHERE client_order_id=?",
                (
                    json.dumps(row, sort_keys=True, separators=(",", ":")),
                    json.dumps(fills, sort_keys=True, separators=(",", ":")),
                    client_order_id,
                ),
            )
        assert response["data"][0]["client_order_id"] == client_order_id
        return self._order_envelope(row)


@contextmanager
def _close_stack(tmp_path, monkeypatch, *, position_side, position_bucket, offset):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    monkeypatch.setitem(MAPPERS, fixtures._EXCHANGE, map_ctp_order)
    account_scope = fixtures._ACCOUNT_SCOPE
    wire_scope = fixtures._SCOPE
    now_ns = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument=fixtures._V2_INSTRUMENT,
        metadata_version=fixtures._V2_METADATA_VERSION,
        as_of_ns=now_ns - 1_000_000_000,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("1000"),
        taker_fee_bps=Decimal("0"),
        fixed_fee=Decimal("0"),
        max_slippage_bps=Decimal("0"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )
    registry = InstrumentRiskRegistry((metadata,))
    target = RiskOrderTarget(
        account_scope=account_scope,
        strategy_id=wire_scope.strategy_ref,
        exchange_name=fixtures._EXCHANGE,
        provider_account_id=account_scope.account_id,
        registry=registry,
    )
    journal = RiskOrderJournal(tmp_path / "journal.sqlite3", writer_id="risk-day-close-owner")
    gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        RiskPolicy(
            "risk-day-close-policy-v1",
            Decimal("1000"),
            max_increase_count=5,
            permit_ttl_seconds=60,
            require_strategy_allocation=True,
            notional_unit="USD",
        ),
        execution_journal_authority=journal,
    )
    api = BtApi(debug=False)
    try:
        gate.set_strategy_allocation(
            account_scope,
            wire_scope.strategy_ref,
            "risk-day-close-allocation-v1",
            max_notional=Decimal("1000"),
            notional_unit="USD",
        )
        gate.set_closable_position(
            account_scope,
            fixtures._V2_INSTRUMENT,
            wire_scope.strategy_ref,
            f"risk-day-close-{position_side.lower()}-{position_bucket.lower()}-v1",
            closable_quantity=Decimal("1"),
            quantity_unit="BTC",
            position_side=position_side,
            position_bucket=position_bucket,
        )
        feed = _FilledCloseFeed(
            tmp_path / "provider.sqlite3",
            position_side=position_side,
            position_bucket=position_bucket,
            offset=offset,
        )
        api.exchange_feeds[fixtures._EXCHANGE] = feed
        mapper = InstrumentRiskAdmissionMapper(
            account_scope,
            registry,
            clock_ns=time.time_ns,
            allocation_reader=lambda execution_scope: gate.get_strategy_allocation(
                account_scope, execution_scope.strategy_id
            ),
        )
        admission = DurableRiskAdmissionAdapter(gate, mapper)
        contract = replace(
            fixtures._contract(("DAY",)),
            supported_position_effects=("OPEN", "CLOSE"),
            supported_position_dimensions=(
                ("NET", "ALL"),
                ("LONG", "TODAY"),
                ("SHORT", "YESTERDAY"),
            ),
        )
        service = RiskOrderEntryService(
            api,
            {wire_scope: target},
            {wire_scope: admission},
            journal,
            gate,
            {wire_scope: contract},
        )
        identity = WireIdentity(
            WireRole.STRATEGY_CLIENT, fixtures.base._PRINCIPAL, wire_scope.strategy_ref
        )

        def call(arguments):
            frozen = dict(arguments)
            frozen["tags"] = MappingProxyType(dict(frozen.get("tags", {})))
            return service.handle(
                "orders.submit",
                principal_ref=identity.principal_ref,
                identity=identity,
                scope=wire_scope,
                arguments=MappingProxyType(frozen),
            )

        yield SimpleNamespace(
            account_scope=account_scope,
            call=call,
            contract=contract,
            feed=feed,
            gate=gate,
            journal=journal,
            principal=identity.principal_ref,
            service=service,
            wire_scope=wire_scope,
        )
    finally:
        try:
            api.close()
        finally:
            gate.close()


@pytest.mark.parametrize(
    "intent_id,raw_effect,side,position_side,position_bucket,offset",
    [
        ("day-close-net", "CLOSE", "SELL", "NET", "ALL", "close"),
        ("day-close-today", "CLOSE_TODAY", "SELL", "LONG", "TODAY", "close_today"),
        (
            "day-close-yesterday",
            "CLOSE_YESTERDAY",
            "BUY",
            "SHORT",
            "YESTERDAY",
            "close_yesterday",
        ),
    ],
)
def test_normal_day_close_preserves_position_terms_and_terminal_proof(
    tmp_path,
    monkeypatch,
    intent_id,
    raw_effect,
    side,
    position_side,
    position_bucket,
    offset,
):
    with _close_stack(
        tmp_path,
        monkeypatch,
        position_side=position_side,
        position_bucket=position_bucket,
        offset=offset,
    ) as env:
        assert env.service.capability_state(env.wire_scope)["orders.submit"] is True
        arguments = fixtures.base._order_arguments(intent_id)
        arguments.update(
            instrument=fixtures._V2_INSTRUMENT,
            metadata_version=fixtures._V2_METADATA_VERSION,
            side=side,
            position_effect=raw_effect,
            order_type="LIMIT",
            quantity="1",
            price="20",
            time_in_force="DAY",
            reduce_only=True,
        )
        if position_side != "NET":
            arguments["position_side"] = position_side
            arguments["position_bucket"] = position_bucket

        reply = env.call(arguments)
        assert reply["ok"] is True, reply
        record = reply["result"]["record"]
        assert record["state"] == "FILLED", record
        assert record["filled_quantity"] == "1", record
        assert record["dispatch_attempts"] == 1, record

        command = env.journal.get_command(env.account_scope, env.principal, intent_id)
        assert command is not None
        canonical = command.canonical_payload
        request = command.order_request
        assert canonical["position_effect"] == "CLOSE"
        assert canonical["reduce_only"] is True
        assert canonical["time_in_force"] == "DAY"
        assert canonical.get("position_side", "NET") == position_side
        assert canonical.get("position_bucket", "ALL") == position_bucket
        assert request["time_in_force"] == "DAY"
        assert request["reduce_only"] is True
        assert request.get("position_side") == (None if position_side == "NET" else position_side.lower())
        assert request.get("offset") == (None if position_side == "NET" else offset)
        assert command.payload_sha256 == sha256_text(canonical_json(dict(canonical)))
        assert command.order_request_sha256 == sha256_text(
            canonical_json(
                {
                    "exchange_name": fixtures._EXCHANGE,
                    "operation": "BtApi.make_order(normalized=True)",
                    "request": dict(request),
                }
            )
        )
        assert len(env.feed.sent) == 1
        assert env.feed.sent[0]["time_in_force"] == "DAY"
        # CTP expresses reduce-only semantics as an explicit closing offset.
        assert env.feed.sent[0]["offset"] == offset
        assert env.feed.sent_args[0][3] == f"{side.lower()}-limit"
        writes, counters = env.feed.snapshot()
        assert writes == (command.client_order_id,)
        assert counters == {"fill_queries": 1, "order_queries": 1}

        close_receipt = env.gate.dispatch_close_resolution_receipt(command.permit_id)
        assert close_receipt.status.value == "TERMINAL_CLOSE_RESOLVED", close_receipt
        assert close_receipt.position_effect == "CLOSE", close_receipt
        assert close_receipt.dispatch_latch_active is False, close_receipt
        assert close_receipt.requested_quantity == close_receipt.filled_quantity == Decimal("1")

