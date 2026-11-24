"""P0-4 integration regression tests for CtpGatewayOrderCutSource:
1. TD reconnect interrupting an in-flight QUERYING cut aborts cleanly without permanent GAP.
2. Reconnect during PROJECTING (after journal writes started) still fails closed with GAP.
3. Periodic cuts on the same session after fact_watermark advances do not hit OBSERVED_CUT_CONFLICT."""
from __future__ import annotations

import hashlib

from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectOrderStatus,
    NativeOrderIdentity,
)
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey

from tests.integration.test_ctp_gateway_order_cut_source import source_for
from tests.integration.test_iter64_ctp_gateway_order_cut import _finish

pytest_plugins = ("tests.integration.test_iter63_ctp_gateway_return_maintenance",)

INSTRUMENT = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb2701")


def test_pause_for_reconnect_during_querying_aborts_cleanly_without_gap_and_rebinds(
    real_owner, monkeypatch
):
    """TD reconnect while ReqQryOrder is in-flight in QUERYING must retain the pin until
    settled, abort without marking observed_order_sources GAP, and allow rebind + next cut."""
    source, maintenance, session, native, _journal = source_for(real_owner)
    sent = []
    original_req = native.ReqQryOrder

    def hold_first(field, rid):
        sent.append(rid)
        if len(sent) == 1:
            return 0
        return original_req(field, rid)

    monkeypatch.setattr(native, "ReqQryOrder", hold_first)
    assert source.begin() and source.advance() and len(sent) == 1
    assert source._coordinator.status().state == "QUERYING"

    # First pause_for_reconnect while native query is still unsealed -> retains pin, NO GAP!
    assert source.pause_for_reconnect() is False
    assert session._order_query_cut is not None
    assert maintenance.observed_order_source_status().status != "GAP"
    assert not maintenance._observed_order_source.blocked
    assert source._failed is False

    # Native query settles -> pause_for_reconnect succeeds cleanly, NO GAP, NOT failed!
    native.client._spi.OnRspQryOrder(None, None, sent[0], True)
    assert source.pause_for_reconnect() is True
    assert session._order_query_cut is None
    assert maintenance.observed_order_source_status().status != "GAP"
    assert not maintenance._observed_order_source.blocked
    assert source._failed is False

    # Rebind after reconnect creates a fresh coordinator and completes the next cut cleanly!
    source.rebind_after_reconnect()
    assert source.due(1_000_000_000.0) and source.begin()
    _finish(source._coordinator)
    cut = source.current()
    assert cut is not None
    assert cut.source_history_has_gap is False
    assert maintenance.observed_order_source_status().status != "GAP"
    assert source.close()


def test_pause_for_reconnect_during_projecting_still_fails_closed_with_gap(
    real_owner, monkeypatch
):
    """Once projection has started (after query rows exist), reconnect abort MUST still fail closed."""
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    source, maintenance, session, native, _journal = source_for(real_owner)
    today = maintenance._observed_order_source._binding.trading_day
    native.rows["orders"] = [
        _order(TradingDay=today, OrderRef="1", OrderSysID="sys-proj-1"),
        _order(TradingDay=today, OrderRef="2", OrderSysID="sys-proj-2"),
    ]
    assert source.begin()
    assert source.advance() is True  # QUERYING -> PROJECTING
    assert source.advance() is True  # ingests row 0 into journal -> still PROJECTING
    assert source._coordinator.status().state == "PROJECTING"
    assert len(source._coordinator._digests) == 1

    assert source.pause_for_reconnect() is True
    assert maintenance.observed_order_source_status().status == "GAP"
    assert source._failed is True
    assert source.close()


def test_periodic_cuts_survive_fact_watermark_advancement_without_gap(real_owner):
    """Cut #1 -> account observation watermark advances -> Cut #1 expires -> Cut #2 on the
    same (0, 0, empty query) must succeed without OBSERVED_CUT_CONFLICT or GAP."""
    source, maintenance, session, native, journal = source_for(real_owner)
    guard = maintenance._ledger._guard

    # Cut #1 succeeds
    assert source.due(1_000_000_000.0) and source.begin()
    _finish(source._coordinator)
    cut1 = source.current()
    assert cut1 is not None

    # Advance account_observation_journal fact_watermark between periodic cuts
    today = maintenance._observed_order_source._binding.trading_day
    for seq in (101, 102):
        maintenance._ledger.ingest(
            GatewayReturnFact(
                account_scope=maintenance._ledger.account_scope,
                route="CTP___FUTURE",
                instrument=INSTRUMENT,
                trading_day=today,
                kind=DirectExecutionEventKind.ORDER,
                source_ref="ctp-return-buffer",
                identity=NativeOrderIdentity(
                    "CTP___FUTURE", f"wm-cid-{seq}", {"order_sys_id": f"wm-sys-{seq}"}
                ),
                identity_keys=(f"wm-venue-{seq}",),
                binding_keys=(),
                venue_key=f"wm-venue-{seq}",
                status=DirectOrderStatus.ACCEPTED,
                filled_quantity="0",
            )
        )

    # Expire Cut #1 and run Cut #2 on the same session with 0 orders
    source._coordinator._deadline = 0.0
    assert source.current() is None
    assert source.due(1_000_000_000.0) and source.begin()
    _finish(source._coordinator)
    cut2 = source.current()
    assert cut2 is not None
    assert cut2.cut_record_digest == cut1.cut_record_digest
    assert cut2.source_history_has_gap is False
    assert maintenance.observed_order_source_status().status != "GAP"
    assert source.close()
