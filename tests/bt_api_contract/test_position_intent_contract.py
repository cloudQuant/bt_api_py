"""Public SDK preserves position intent and native order identities."""

from decimal import Decimal

import pytest
from bt_api_base.exceptions import InvalidOrderError

from bt_api_py import BtApi, CancelOrderRequest, OrderRequest, OrderType, QueryOrderRequest, Side
from bt_api_py._contracts.errors import CapabilityNotSupportedError


class RecordingFeed:
    def make_order(self, *args, **kwargs):
        return args, kwargs

    query_order = make_order
    cancel_order = make_order


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    api = BtApi(debug=False)
    api.exchange_feeds.update(
        {
            name: RecordingFeed()
            for name in (
                "OKX___SWAP",
                "BINANCE___SWAP",
                "CTP___FUTURE",
            )
        }
    )
    return api


def order(**changes):
    fields = {
        "symbol": "BTCUSDT",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("2"),
        "price": Decimal("60000"),
        "account_id": "demo",
        "client_order_id": "123",
        "time_in_force": "IOC",
    }
    fields.update(changes)
    return OrderRequest(**fields)


@pytest.mark.parametrize("venue,expected", [("OKX___SWAP", "long"), ("BINANCE___SWAP", "LONG")])
@pytest.mark.parametrize(
    "side,position_side,offset",
    [
        (Side.BUY, "long", "open"),
        (Side.SELL, "short", "open"),
        (Side.SELL, "long", "close"),
        (Side.BUY, "short", "close"),
    ],
)
def test_four_hedge_actions_reach_native_feed(api, venue, expected, side, position_side, offset):
    args, options = api.make_order(
        venue,
        order(
            side=side,
            position_side=position_side,
            offset=offset,
            reduce_only=offset == "close",
            position_mode="dual_side",
        ),
    )
    assert args[3] == side.value + "-limit"
    assert options["position_side"] == (
        position_side.upper() if expected == "LONG" else position_side
    )
    assert options["offset"] == offset
    if venue == "BINANCE___SWAP":
        # Binance USD-M Hedge Mode rejects reduceOnly; side + positionSide
        # expresses whether the LONG/SHORT leg is opened or closed.
        assert "reduce_only" not in options
    else:
        assert options["reduce_only"] is (offset == "close")
    assert options["time_in_force"] == "IOC"


@pytest.mark.parametrize("venue,expected", [("OKX___SWAP", "net"), ("BINANCE___SWAP", "BOTH")])
def test_net_account_retains_close_protection(api, venue, expected):
    _, options = api.make_order(
        venue, order(side=Side.SELL, position_side="long", offset="close", position_mode="net")
    )
    assert options["position_side"] == expected
    assert options["reduce_only"] is True


@pytest.mark.parametrize("offset", ["close_today", "close_yesterday"])
def test_ctp_dated_close_and_integer_contracts_are_preserved(api, offset):
    args, options = api.make_order(
        "CTP___FUTURE",
        order(
            symbol="rb2610",
            side=Side.SELL,
            position_side="long",
            position_mode="dual_side",
            offset=offset,
            quantity_unit="contracts",
            exchange_id="SHFE",
        ),
    )
    assert args[1] == 2
    assert options["offset"] == offset
    assert options["exchange_id"] == "SHFE"


def test_ctp_does_not_round_fractional_lots(api):
    with pytest.raises(InvalidOrderError, match="integer"):
        api.make_order("CTP___FUTURE", order(quantity=Decimal("0.5"), quantity_unit="contracts"))


@pytest.mark.parametrize(
    "method,model", [("query_order", QueryOrderRequest), ("cancel_order", CancelOrderRequest)]
)
def test_ctp_local_order_ref_is_not_exchange_order_sys_id(api, method, model):
    args, options = getattr(api, method)(
        "CTP___FUTURE",
        model(
            symbol="rb2610",
            account_id="paper",
            client_order_id="123",
            front_id=5,
            session_id=9,
            exchange_id="SHFE",
        ),
    )
    assert args == ("rb2610", None)
    assert options["order_ref"] == "123"
    assert options["front_id"] == 5 and options["session_id"] == 9
    assert options["exchange_id"] == "SHFE"


def test_ctp_exchange_identity_takes_precedence(api):
    args, options = api.cancel_order(
        "CTP___FUTURE",
        CancelOrderRequest(
            symbol="rb2610",
            account_id="paper",
            order_id="SYS1",
            client_order_id="123",
            exchange_id="SHFE",
        ),
    )
    assert args[1] == "SYS1"
    assert "order_ref" not in options


@pytest.mark.parametrize("venue", ["OKX___SWAP", "BINANCE___SWAP"])
def test_crypto_cannot_silently_ignore_position_ticket(api, venue):
    with pytest.raises(CapabilityNotSupportedError):
        api.make_order(venue, order(position_id="MT5-POSITION-7"))


def test_conflicting_action_fails_before_feed(api):
    with pytest.raises(InvalidOrderError, match="contradict"):
        api.make_order(
            "OKX___SWAP",
            order(side=Side.BUY, position_side="long", offset="close", position_mode="dual_side"),
        )
