"""Offline contract tests for verified perpetual position-mode mutation."""

from __future__ import annotations

import asyncio
import threading
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bt_api_py import PositionModeUpdate, _normalization
from bt_api_py._contracts.errors import (
    CapabilityNotSupportedError,
    NormalizedApiError,
)
from bt_api_py._contracts.models import OrderRequest, OrderType, Side, TransportMode
from bt_api_py.bt_api import BtApi


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    return BtApi(debug=False)


def order_request(*, position_mode: str | None = "dual_side") -> OrderRequest:
    return OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.01"),
        price=Decimal("50000"),
        account_id="demo",
        client_order_id="123456789012",
        position_side="long",
        offset="open",
        position_mode=position_mode,
    )


@pytest.mark.parametrize(
    "exchange,mode,setter_name,native_value,read_response,ack_response",
    [
        (
            "OKX___SWAP",
            "dual_side",
            "set_position_mode",
            "long_short_mode",
            {"code": "0", "data": [{"posMode": "long_short_mode"}]},
            {"code": "0", "msg": "PRIVATE", "data": []},
        ),
        (
            "OKX___SWAP",
            "net",
            "set_position_mode",
            "net_mode",
            {"code": "0", "data": [{"posMode": "net_mode"}]},
            {"code": "0", "msg": "PRIVATE", "data": []},
        ),
        (
            "BINANCE___SWAP",
            "dual_side",
            "change_position_mode",
            True,
            {"dualSidePosition": True},
            {"code": 200, "msg": "PRIVATE"},
        ),
        (
            "BINANCE___SWAP",
            "net",
            "change_position_mode",
            False,
            {"dualSidePosition": False},
            {"code": 200, "msg": "PRIVATE"},
        ),
    ],
)
def test_verified_mode_change_maps_native_value_and_updates_cache(
    api,
    exchange,
    mode,
    setter_name,
    native_value,
    read_response,
    ack_response,
):
    setter = Mock(return_value=ack_response)
    reader = Mock(return_value=read_response)
    read_name = "get_config" if exchange.startswith("OKX___") else "get_position_mode"
    api.exchange_feeds[exchange] = SimpleNamespace(
        **{setter_name: setter, read_name: reader}
    )

    result = api.set_position_mode(
        exchange,
        mode,
        extra_data={"trace": "offline"},
        recv_window=5000,
    )

    assert isinstance(result, PositionModeUpdate)
    assert result.exchange_name == exchange
    assert result.requested_mode == mode
    assert result.position_mode == mode
    assert result.acknowledged is True
    assert result.verified is True
    assert result.cache_updated is True
    assert result.observed_at.utcoffset() is not None
    assert "PRIVATE" not in repr(result)
    assert not hasattr(result, "raw")
    setter.assert_called_once_with(
        native_value,
        extra_data={"trace": "offline"},
        recv_window=5000,
    )
    reader.assert_called_once_with(extra_data={"trace": "offline"})
    assert api._position_modes[exchange] == mode
    assert exchange not in api._position_mode_reconcile_required


@pytest.mark.parametrize(
    "mode",
    [None, True, "", "NET", "net_mode", "long_short_mode", "hedge"],
)
def test_invalid_canonical_mode_is_rejected_before_feed_access(api, mode):
    setter = Mock()
    reader = Mock()
    api.exchange_feeds["OKX___SWAP"] = SimpleNamespace(
        set_position_mode=setter,
        get_config=reader,
    )

    with pytest.raises(NormalizedApiError) as captured:
        api.set_position_mode("OKX___SWAP", mode)

    assert captured.value.code == "invalid_position_mode"
    assert captured.value.definite_reject is True
    assert captured.value.execution_unknown is False
    setter.assert_not_called()
    reader.assert_not_called()


@pytest.mark.parametrize("exchange", ["CTP___FUTURE", "MT5___FX", "OKX___SPOT"])
def test_other_venues_remain_explicitly_unsupported(api, exchange):
    api.exchange_feeds[exchange] = SimpleNamespace(
        set_position_mode=Mock(side_effect=AssertionError("must not write"))
    )

    with pytest.raises(CapabilityNotSupportedError, match="set_position_mode"):
        api.set_position_mode(exchange, "dual_side")


def test_native_reject_does_not_read_back_or_replace_cached_mode(api):
    setter = Mock(
        return_value={"code": "51000", "msg": "signature=PRIVATE", "data": []}
    )
    reader = Mock(side_effect=AssertionError("rejected write must not read back"))
    api.exchange_feeds["OKX___SWAP"] = SimpleNamespace(
        set_position_mode=setter,
        get_config=reader,
    )
    api._position_modes["OKX___SWAP"] = "net"

    with pytest.raises(NormalizedApiError) as captured:
        api.set_position_mode("OKX___SWAP", "dual_side")

    error = captured.value
    assert error.code == "51000"
    assert error.definite_reject is True
    assert error.execution_unknown is False
    assert "PRIVATE" not in str(error)
    assert error.__context__ is None and error.__cause__ is None
    reader.assert_not_called()
    assert api._position_modes["OKX___SWAP"] == "net"
    assert "OKX___SWAP" not in api._position_mode_reconcile_required


def test_write_timeout_is_unknown_and_does_not_read_back_or_update_cache(api):
    setter = Mock(side_effect=TimeoutError("signature=PRIVATE"))
    reader = Mock(side_effect=AssertionError("unknown write must not read back"))
    api.exchange_feeds["BINANCE___SWAP"] = SimpleNamespace(
        change_position_mode=setter,
        get_position_mode=reader,
    )
    api._position_modes["BINANCE___SWAP"] = "net"

    with pytest.raises(NormalizedApiError) as captured:
        api.set_position_mode("BINANCE___SWAP", "dual_side")

    error = captured.value
    assert error.code == "TimeoutError"
    assert error.execution_unknown is True
    assert error.definite_reject is False
    assert "PRIVATE" not in str(error)
    reader.assert_not_called()
    assert "BINANCE___SWAP" not in api._position_modes
    assert "BINANCE___SWAP" in api._position_mode_reconcile_required


@pytest.mark.parametrize(
    "reader,expected_code",
    [
        (
            Mock(return_value={"dualSidePosition": False}),
            "position_mode_verification_mismatch",
        ),
        (
            Mock(side_effect=TimeoutError("signature=PRIVATE")),
            "position_mode_verification_failed",
        ),
    ],
)
def test_unverified_ack_never_reports_success_or_updates_cache(
    api, reader, expected_code
):
    api.exchange_feeds["BINANCE___SWAP"] = SimpleNamespace(
        change_position_mode=Mock(return_value={"code": 200, "msg": "success"}),
        get_position_mode=reader,
    )
    api._position_modes["BINANCE___SWAP"] = "net"

    with pytest.raises(NormalizedApiError) as captured:
        api.set_position_mode("BINANCE___SWAP", "dual_side")

    error = captured.value
    assert error.code == expected_code
    assert error.execution_unknown is True
    assert error.definite_reject is False
    assert "PRIVATE" not in str(error)
    assert error.__context__ is None and error.__cause__ is None
    assert "BINANCE___SWAP" not in api._position_modes
    assert "BINANCE___SWAP" in api._position_mode_reconcile_required


@pytest.mark.parametrize(
    "placement_path",
    [
        "normalized_sync",
        "normalized_async",
        "typed_raw",
        "legacy_sync",
        "legacy_async",
    ],
)
def test_unknown_mode_blocks_every_public_crypto_placement(api, placement_path):
    exchange = "BINANCE___SWAP"
    legacy_async_order = Mock(
        side_effect=AssertionError("uncertain mode must block async placement")
    )
    api.exchange_feeds[exchange] = SimpleNamespace(
        change_position_mode=Mock(side_effect=TimeoutError("signature=PRIVATE")),
        get_position_mode=Mock(
            side_effect=AssertionError("unknown write must not read back")
        ),
        async_make_order=legacy_async_order,
    )
    api._position_modes[exchange] = "net"
    api._backend.make_order = Mock(
        side_effect=AssertionError("uncertain mode must block placement")
    )

    with pytest.raises(NormalizedApiError):
        api.set_position_mode(exchange, "dual_side")

    with pytest.raises(NormalizedApiError) as captured:
        if placement_path == "normalized_sync":
            api.make_order(exchange, order_request(), normalized=True)
        elif placement_path == "normalized_async":
            asyncio.run(
                api.async_make_order(
                    exchange,
                    order_request(),
                    normalized=True,
                )
            )
        elif placement_path == "typed_raw":
            api.make_order(exchange, order_request())
        elif placement_path == "legacy_sync":
            api.make_order(exchange, "BTCUSDT", 0.01, 50000, "buy-limit")
        else:
            asyncio.run(
                api.async_make_order(
                    exchange,
                    "BTCUSDT",
                    0.01,
                    50000,
                    "buy-limit",
                )
            )

    error = captured.value
    assert error.code == "position_mode_reconcile_required"
    assert error.definite_reject is True
    assert error.execution_unknown is False
    api._backend.make_order.assert_not_called()
    legacy_async_order.assert_not_called()


@pytest.mark.parametrize("read_operation", ["get_position_mode", "get_account_config"])
def test_fresh_verified_mode_read_clears_latch_and_restores_placement(
    api, read_operation
):
    exchange = "BINANCE___SWAP"
    position_reader = Mock(return_value={"dualSidePosition": False})
    account_reader = Mock(return_value={"dualSidePosition": True, "canTrade": True})
    feed = SimpleNamespace(
        change_position_mode=Mock(return_value={"code": 200, "msg": "success"}),
        get_position_mode=position_reader,
        get_account_config=account_reader,
    )
    api.exchange_feeds[exchange] = feed
    api._position_modes[exchange] = "net"

    with pytest.raises(NormalizedApiError, match="verification_mismatch"):
        api.set_position_mode(exchange, "dual_side")

    assert exchange in api._position_mode_reconcile_required
    if read_operation == "get_position_mode":
        position_reader.return_value = {"dualSidePosition": True}
        snapshot = api.get_position_mode(exchange, normalized=True)
    else:
        snapshot = api.get_account_config(exchange, normalized=True)

    assert snapshot["position_mode"] == "dual_side"
    assert api._position_modes[exchange] == "dual_side"
    assert exchange not in api._position_mode_reconcile_required

    api._backend.make_order = Mock(
        return_value={"orderId": 123, "clientOrderId": "123456789012"}
    )
    api._enrich_order_commission = Mock()
    result = api.make_order(exchange, order_request(), normalized=True)

    assert result["status"] == "accepted"
    assert api._backend.make_order.call_count == 1


@pytest.mark.parametrize(
    "placement_path",
    [
        "normalized_sync",
        "normalized_async",
        "typed_raw",
        "legacy_sync",
        "legacy_async",
    ],
)
def test_every_inflight_public_crypto_placement_blocks_mode_mutation(
    api, placement_path
):
    exchange = "BINANCE___SWAP"
    setter = Mock(return_value={"code": 200, "msg": "success"})
    reader = Mock(return_value={"dualSidePosition": False})
    api.exchange_feeds[exchange] = SimpleNamespace(
        change_position_mode=setter,
        get_position_mode=reader,
    )
    placement_entered = threading.Event()
    release_placement = threading.Event()
    results = []
    failures = []

    def place_order(*_args):
        placement_entered.set()
        if not release_placement.wait(5):
            raise TimeoutError("test placement was not released")
        return {"orderId": 123, "clientOrderId": "123456789012"}

    async def place_order_async(*_args, **_kwargs):
        placement_entered.set()
        if not await asyncio.to_thread(release_placement.wait, 5):
            raise TimeoutError("test placement was not released")
        return {"orderId": 123, "clientOrderId": "123456789012"}

    def run_order():
        try:
            if placement_path == "normalized_sync":
                result = api.make_order(exchange, order_request(), normalized=True)
            elif placement_path == "normalized_async":
                result = asyncio.run(
                    api.async_make_order(
                        exchange,
                        order_request(),
                        normalized=True,
                    )
                )
            elif placement_path == "typed_raw":
                result = api.make_order(exchange, order_request())
            elif placement_path == "legacy_sync":
                result = api.make_order(
                    exchange,
                    "BTCUSDT",
                    0.01,
                    50000,
                    "buy-limit",
                )
            else:
                result = asyncio.run(
                    api.async_make_order(
                        exchange,
                        "BTCUSDT",
                        0.01,
                        50000,
                        "buy-limit",
                    )
                )
            results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    api.exchange_feeds[exchange].async_make_order = place_order_async
    api._backend.make_order = Mock(side_effect=place_order)
    api._enrich_order_commission = Mock()
    worker = threading.Thread(target=run_order)
    worker.start()
    assert placement_entered.wait(2)
    try:
        with pytest.raises(NormalizedApiError) as captured:
            api.set_position_mode(exchange, "net")
        assert captured.value.code == "position_mode_placement_inflight"
        assert captured.value.definite_reject is True
        setter.assert_not_called()
        reader.assert_not_called()
    finally:
        release_placement.set()
        worker.join(5)

    assert not worker.is_alive()
    assert failures == []
    assert len(results) == 1
    if placement_path in {"normalized_sync", "normalized_async"}:
        assert results[0]["status"] == "accepted"
    else:
        assert results[0]["orderId"] == 123
    assert exchange not in api._position_mode_active_placements


def test_missing_provider_mutation_fails_before_readback(api):
    reader = Mock()
    api.exchange_feeds["BINANCE___SWAP"] = SimpleNamespace(get_position_mode=reader)

    with pytest.raises(CapabilityNotSupportedError, match="does not implement"):
        api.set_position_mode("BINANCE___SWAP", "dual_side")

    reader.assert_not_called()


def test_zmq_and_raw_result_opt_out_fail_before_feed_access(api):
    api._get_feed = Mock(side_effect=AssertionError("must not access direct feed"))
    api.transport_mode = TransportMode.ZMQ

    with pytest.raises(CapabilityNotSupportedError, match="transport=zmq"):
        api.set_position_mode("BINANCE___SWAP", "dual_side")
    with pytest.raises(NormalizedApiError) as captured:
        api.set_position_mode("BINANCE___SWAP", "dual_side", normalized=False)
    assert captured.value.code == "normalized_result_required"
    api._get_feed.assert_not_called()


def test_snapshot_read_filters_only_known_zero_positions(api):
    api.exchange_feeds["BINANCE___SWAP"] = SimpleNamespace(
        get_position=Mock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0",
                    "positionSide": "BOTH",
                },
                {
                    "symbol": "ETHUSDT",
                    "positionAmt": "-0.25",
                    "positionSide": "BOTH",
                },
            ]
        )
    )

    positions = api.get_position("BINANCE___SWAP", normalized=True)

    assert [item["symbol"] for item in positions] == ["ETHUSDT"]
    assert positions[0]["quantity"] == -0.25
    assert positions[0]["quantity_known"] is True
    assert positions[0]["quantity_exact_zero"] is False


@pytest.mark.parametrize("raw_quantity", ["1E-400", "-1E-400"])
def test_nonzero_subnormal_position_is_never_filtered_as_flat(api, raw_quantity):
    api.exchange_feeds["BINANCE___SWAP"] = SimpleNamespace(
        get_position=Mock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": raw_quantity,
                    "positionSide": "BOTH",
                }
            ]
        )
    )

    positions = api.get_position("BINANCE___SWAP", normalized=True)

    assert len(positions) == 1
    assert positions[0]["quantity"] == Decimal(raw_quantity)
    assert positions[0]["quantity_known"] is True
    assert positions[0]["quantity_exact_zero"] is False


def test_unknown_zero_quantity_snapshot_is_not_filtered(monkeypatch):
    monkeypatch.setattr(
        _normalization,
        "position",
        lambda row, exchange_name, symbol=None: {
            "exchange_name": exchange_name,
            "symbol": symbol or row["symbol"],
            "quantity": 0,
            "quantity_known": False,
        },
    )

    positions = _normalization.normalize_result(
        "get_position",
        [{"symbol": "BTCUSDT"}],
        "BINANCE___SWAP",
    )

    assert positions == [
        {
            "exchange_name": "BINANCE___SWAP",
            "symbol": "BTCUSDT",
            "quantity": 0,
            "quantity_known": False,
        }
    ]


def test_zero_position_event_remains_a_close_tombstone():
    event = _normalization.normalize_event(
        {
            "symbol": "BTCUSDT",
            "positionAmt": "0",
            "positionSide": "BOTH",
        },
        "BINANCE___SWAP",
        kind="position",
    )

    assert event["kind"] == "position"
    assert event["quantity"] == 0
    assert event["quantity_known"] is True
    assert event["quantity_exact_zero"] is True
