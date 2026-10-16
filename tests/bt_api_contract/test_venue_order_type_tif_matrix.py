"""Venue x order-type/TIF incompatibility matrix over the direct-BtApi adapter seam.

Scope
-----
Closes the DIM06 clause sub-item "each venue: order-type/TIF incompatibility
rejection" (docs/迭代计划/迭代10-可选组件与可配置通信/正式迭代计划.md:778; root
adjudication docs/.../dim06-ctp-contract-qa-20261016/root-adjudication.md) as ONE
unified cross-venue matrix contract.  The exercised seam is the direct-path
pre-dispatch adapter chain::

    OrderRequest -> get_venue_mapper(exchange) -> FeedAdapter -> venue adapter
    (bt_api_py._venue_mappers.* + bt_api_py._feed_adapter + the CTP/OKX/Binance
    request builders the direct backend dispatches into)

Asserted matrix cells (verdicts are the adapter's own production gates)
-----------------------------------------------------------------------
venue          | order_type | time_in_force      | verdict | surface
CTP___FUTURE   | limit      | GFD                | accept  | TimeCondition "3", submitted once
CTP___FUTURE   | limit      | DAY                | accept  | normalized to GFD, submitted once
CTP___FUTURE   | limit      | GTC/IOC/FOK/post_only | reject | ValueError before any submit
CTP___FUTURE   | market     | any                | reject  | mapper CapabilityNotSupportedError
                                                          + feed kind guard (defense in depth)
OKX___SWAP     | limit      | GTC / IOC / FOK    | accept  | ordType limit / ioc / fok, px present
OKX___SWAP     | limit      | post_only          | accept  | ordType post_only, px present
OKX___SWAP     | limit      | DAY / GFD / ZZZ    | reject  | InvalidOrderError before request
OKX___SWAP     | limit, post_only=True | DAY    | reject  | InvalidOrderError pre-request
OKX___SWAP     | market     | GTC                | accept  | ordType market, no px
BINANCE___SWAP | limit      | GTC / IOC / FOK    | accept  | timeInForce passthrough exactly
BINANCE___SWAP | market     | GTC                | accept  | timeInForce+price dropped (render)

Cross-venue contrast nodes drive the SAME time-in-force token / order type through
all three venues and assert the verdicts differ per venue -- this is the matrix
dimension the existing per-venue golden tests (test_ctp_order_mapping.py,
test_okx_order_mapping.py, test_binance_order_mapping.py) do not provide.

Documented boundaries (observed, deliberately NOT asserted here; see the round
report NOT-PROVEN list):
  * Binance has no local time-in-force gate: any string passes through for limit
    orders (provider-domain validation per the clause vehicle; not faked here).
  * OKX market + time_in_force="post_only" builds ordType=post_only without px
    and is not rejected locally (observed boundary).
  * CTP default OrderRequest time_in_force "GTC" is rejected by the CTP adapter;
    callers must request GFD/DAY explicitly.

Closure fence (fixed-source QA)
-------------------------------
This contract imports the direct-path seam with BT_API_PY_LIGHT_IMPORT=1 so that
bt_api_py.__init__ never eagerly imports bt_api_py.bt_api / _direct_backend /
_gateway_wire (an unrelated in-flight workstream held uncommitted edits there at
build time).  Guard nodes assert those modules stay out of sys.modules and that
every adapter module resolves to a file inside this dev tree.

Evidence level: fixed-source, synthetic harnesses; no provider call, no account.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import pytest

from bt_api_base.exceptions import InvalidOrderError
from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py._feed_adapter import FeedAdapter
from bt_api_py._venue_mappers import MAPPERS, get_venue_mapper

import bt_api_binance.feeds.rest_trade as _binance_rest_trade
import bt_api_ctp.feeds.live_ctp_feed as _ctp_feed_module
import bt_api_okx.feeds.live_okx.mixins.trade_mixin as _okx_trade_mixin
from bt_api_binance.feeds.normalize import NormalizeMixin
from bt_api_binance.feeds.rest_trade import RestTradeMixin
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestData
from bt_api_okx.feeds.live_okx.mixins.trade_mixin import TradeMixin

DEV_TREE_ROOT = Path(r"D:\bt_api_py")


# ---------------------------------------------------------------------------
# Harnesses: production adapter classes with access gates stubbed.  Every
# verdict asserted below is produced by production code; only the transport
# gates / network calls are replaced by counting fakes.
# ---------------------------------------------------------------------------


class _NullLogger:
    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _CtpTrader:
    """Counting fake CTP trader: records submit attempts, never talks to CTP."""

    def __init__(self) -> None:
        self.execute_calls = 0
        self.next_request_id_calls = 0
        self.fields: list[Any] = []

    def _next_request_id(self) -> int:
        self.next_request_id_calls += 1
        return 7000 + self.next_request_id_calls

    def submit_order_insert(self, field: Any, request_id: int, **_kwargs: Any) -> int:
        self.execute_calls += 1
        self.fields.append(field)
        return 0


class _CtpHarness(CtpRequestData):
    """CtpRequestData with the execution-access gates stubbed out.

    The gate itself is covered by the dedicated CTP execution contracts; here
    it is stubbed so the order-type/TIF gates below run against the real
    production ``make_order`` body.
    """

    def __init__(self) -> None:
        super().__init__()
        self.broker_id = "9999"
        self.user_id = "SYNTH-USER"
        self.trader = _CtpTrader()
        self._trader = self.trader

    def _ensure_trading_ready(self) -> None:  # harness stub
        return None

    def _ensure_execution_permitted(
        self, capability: Any, symbol: Any, exchange_id: Any = None
    ) -> None:  # harness stub
        return None


class _OkxParams:
    symbol_leverage_dict: dict[str, int] = {}

    def get_symbol(self, symbol: Any) -> Any:
        return symbol

    def get_rest_path(self, request_type: str) -> str:
        return "/api/v5/trade/order"


class _OkxHarness(TradeMixin):
    """TradeMixin with only the network ``request`` call replaced by a counter."""

    def __init__(self, asset_type: str = "SWAP") -> None:
        self.exchange_name = "OKX___SWAP"
        self.asset_type = asset_type
        self._params = _OkxParams()
        self.request_logger = _NullLogger()
        self.requests: list[dict[str, Any]] = []

    def request(self, path: str, body: Any = None, extra_data: Any = None, **_kwargs: Any) -> Any:
        self.requests.append({"path": path, "body": body})
        return {"code": "0", "data": []}


class _BinanceParams:
    def get_symbol(self, symbol: Any) -> Any:
        return symbol

    def get_rest_path(self, request_type: str) -> str:
        return "/fapi/v1/order"


class _BinanceHarness(RestTradeMixin, NormalizeMixin):
    """RestTradeMixin + NormalizeMixin with only the network ``request`` replaced."""

    def __init__(self, asset_type: str = "SWAP") -> None:
        self.exchange_name = "BINANCE___SWAP"
        self.asset_type = asset_type
        self._params = _BinanceParams()
        self.requests: list[dict[str, Any]] = []

    def request(
        self,
        path: str,
        params: Any = None,
        extra_data: Any = None,
        is_sign: bool = False,
        **_kwargs: Any,
    ) -> Any:
        self.requests.append({"path": path, "params": params})
        return {"orderId": "1"}


def _ctp_order(**overrides: Any) -> OrderRequest:
    values: dict[str, Any] = {
        "symbol": "rb2510",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("1"),
        "price": Decimal("3500"),
        "account_id": "paper",
        "client_order_id": "000000000042",
        "time_in_force": "GFD",
    }
    values.update(overrides)
    return OrderRequest(**values)


def _okx_order(**overrides: Any) -> OrderRequest:
    values: dict[str, Any] = {
        "symbol": "BTC-USDT-SWAP",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("1"),
        "price": Decimal("50000"),
        "account_id": "paper",
        "client_order_id": "cid-1",
        "time_in_force": "GTC",
        "quantity_unit": "contracts",
    }
    values.update(overrides)
    return OrderRequest(**values)


def _binance_order(**overrides: Any) -> OrderRequest:
    values: dict[str, Any] = {
        "symbol": "BTCUSDT",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("0.001"),
        "price": Decimal("50000"),
        "account_id": "paper",
        "client_order_id": "cid-1",
        "time_in_force": "GTC",
    }
    values.update(overrides)
    return OrderRequest(**values)


def _direct_adapter(venue: str) -> tuple[FeedAdapter, Callable[[], int]]:
    """Return (FeedAdapter over the venue harness, provider-call counter getter)."""
    if venue == "CTP___FUTURE":
        feed = _CtpHarness()
        return (
            FeedAdapter(feed, get_venue_mapper(venue)),
            lambda: feed.trader.execute_calls,
        )
    if venue == "OKX___SWAP":
        harness = _OkxHarness()
        return FeedAdapter(harness, get_venue_mapper(venue)), lambda: len(harness.requests)
    if venue == "BINANCE___SWAP":
        harness = _BinanceHarness()
        return FeedAdapter(harness, get_venue_mapper(venue)), lambda: len(harness.requests)
    raise AssertionError(f"unmapped matrix venue {venue!r}")


# ---------------------------------------------------------------------------
# Closure fence
# ---------------------------------------------------------------------------

def test_closure_excludes_inflight_wire_workstream_modules() -> None:
    """Fixed-source fence: the in-flight wire workstream must stay unimported."""
    assert "bt_api_py.bt_api" not in sys.modules
    assert "bt_api_py._direct_backend" not in sys.modules
    assert "bt_api_py._gateway_wire" not in sys.modules


@pytest.mark.parametrize(
    "module",
    [
        _ctp_feed_module,
        _okx_trade_mixin,
        _binance_rest_trade,
    ],
    ids=["ctp", "okx", "binance"],
)
def test_venue_adapter_modules_resolve_to_this_dev_tree(module: Any) -> None:
    """The exercised adapter bytes must come from the pinned dev tree."""
    assert Path(module.__file__).is_relative_to(DEV_TREE_ROOT)


def test_direct_path_venue_registry_covers_matrix_venues() -> None:
    assert set(MAPPERS) == {
        "BINANCE___SPOT",
        "BINANCE___SWAP",
        "OKX___SPOT",
        "OKX___SWAP",
        "CTP___FUTURE",
    }
    assert get_venue_mapper("CTP___FUTURE") is MAPPERS["CTP___FUTURE"]
    assert get_venue_mapper("CTP___OPTION") is MAPPERS["CTP___FUTURE"]
    assert get_venue_mapper("BYBIT___SWAP") is None


# ---------------------------------------------------------------------------
# CTP: limit-only orders; the only accepted time conditions are GFD / DAY.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tif", ["GFD", "DAY"], ids=["gfd", "day-normalized"])
def test_ctp_accepts_gfd_and_day_limit_orders(tif: str) -> None:
    feed = _CtpHarness()
    adapter = FeedAdapter(feed, get_venue_mapper("CTP___FUTURE"))

    result = adapter.make_order(_ctp_order(time_in_force=tif))

    assert result.status is True
    assert feed.trader.execute_calls == 1
    field = feed.trader.fields[0]
    assert field.TimeCondition == "3"  # GFD, the single wire time condition CTP uses
    assert field.OrderRef == "000000000042"
    assert result.data[0].get_order_time_in_force() == "GFD"


@pytest.mark.parametrize(
    "tif", ["GTC", "IOC", "FOK", "post_only"], ids=["gtc", "ioc", "fok", "post-only"]
)
def test_ctp_rejects_non_gfd_tif_before_any_submit(tif: str) -> None:
    adapter, calls = _direct_adapter("CTP___FUTURE")

    with pytest.raises(ValueError) as exc_info:
        adapter.make_order(_ctp_order(time_in_force=tif))

    assert f"CTP time_in_force {tif.upper()!r} is unsupported" in str(exc_info.value)
    assert calls() == 0


def test_ctp_rejects_market_orders_at_the_mapper_before_dispatch() -> None:
    adapter, calls = _direct_adapter("CTP___FUTURE")

    with pytest.raises(CapabilityNotSupportedError) as exc_info:
        adapter.make_order(_ctp_order(order_type=OrderType.MARKET, price=None))

    assert exc_info.value.operation == "make_order"
    assert exc_info.value.detail == "CTP supports limit orders only"
    assert exc_info.value.definite_reject is True
    assert calls() == 0


@pytest.mark.parametrize(
    "tif", ["GFD", "GTC"], ids=["gfd", "gtc"]
)
def test_ctp_feed_kind_guard_also_rejects_market_orders(tif: str) -> None:
    """Defense in depth: even a raw feed call cannot build a non-limit CTP order."""
    feed = _CtpHarness()

    with pytest.raises(ValueError) as exc_info:
        feed.make_order(
            "rb2510",
            1.0,
            3500.0,
            "buy-market",
            offset="open",
            post_only=False,
            client_order_id="000000000042",
            time_in_force=tif,
        )

    assert "CTP order kind 'market' is unsupported" in str(exc_info.value)
    assert feed.trader.execute_calls == 0
    assert feed.trader.next_request_id_calls == 0


# ---------------------------------------------------------------------------
# OKX: the adapter's TIF vocabulary is GTC / IOC / FOK plus the post_only flag.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tif", "ord_type"),
    [("GTC", "limit"), ("IOC", "ioc"), ("FOK", "fok")],
    ids=["gtc", "ioc", "fok"],
)
def test_okx_accepts_supported_tif_with_exact_ord_type_render(tif: str, ord_type: str) -> None:
    harness = _OkxHarness()
    adapter = FeedAdapter(harness, get_venue_mapper("OKX___SWAP"))

    result = adapter.make_order(_okx_order(time_in_force=tif))

    assert result == {"code": "0", "data": []}
    assert len(harness.requests) == 1
    body = harness.requests[0]["body"]
    assert body["ordType"] == ord_type
    assert body["px"] == "50000"
    assert body["sz"] == "1"


def test_okx_post_only_limit_renders_ord_type_post_only_with_price() -> None:
    harness = _OkxHarness()
    adapter = FeedAdapter(harness, get_venue_mapper("OKX___SWAP"))

    result = adapter.make_order(_okx_order(time_in_force="post_only"))

    assert result == {"code": "0", "data": []}
    assert len(harness.requests) == 1
    body = harness.requests[0]["body"]
    assert body["ordType"] == "post_only"
    assert body["px"] == "50000"
    assert body["sz"] == "1"


@pytest.mark.parametrize(
    "tif", ["DAY", "GFD", "ZZZ"], ids=["day", "gfd", "unknown-token"]
)
def test_okx_rejects_tif_outside_its_vocabulary_before_any_request(tif: str) -> None:
    adapter, calls = _direct_adapter("OKX___SWAP")

    with pytest.raises(InvalidOrderError) as exc_info:
        adapter.make_order(_okx_order(time_in_force=tif))

    assert "Unsupported OKX time_in_force" in str(exc_info.value)
    assert calls() == 0


def test_okx_rejects_post_only_with_non_gtc_tif_before_any_request() -> None:
    """Feed-level guard: post_only is only compatible with GTC (limit)."""
    harness = _OkxHarness()

    with pytest.raises(InvalidOrderError) as exc_info:
        harness.make_order(
            "BTC-USDT-SWAP",
            "1",
            "50000",
            "buy-limit",
            offset="open",
            post_only=True,
            client_order_id="cid-1",
            time_in_force="DAY",
            size_in_contracts=True,
        )

    assert "post_only is incompatible with IOC/FOK" in str(exc_info.value)
    assert harness.requests == []


def test_okx_market_order_renders_without_price() -> None:
    harness = _OkxHarness()
    adapter = FeedAdapter(harness, get_venue_mapper("OKX___SWAP"))

    result = adapter.make_order(_okx_order(order_type=OrderType.MARKET, price=None))

    assert result == {"code": "0", "data": []}
    body = harness.requests[0]["body"]
    assert body["ordType"] == "market"
    assert "px" not in body


# ---------------------------------------------------------------------------
# Binance: TIF passthrough for limit; market orders carry no TIF and no price.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tif", ["GTC", "IOC", "FOK"], ids=["gtc", "ioc", "fok"])
def test_binance_passes_supported_tif_through_exactly(tif: str) -> None:
    harness = _BinanceHarness()
    adapter = FeedAdapter(harness, get_venue_mapper("BINANCE___SWAP"))

    result = adapter.make_order(_binance_order(time_in_force=tif))

    assert result == {"orderId": "1"}
    assert len(harness.requests) == 1
    params = harness.requests[0]["params"]
    assert params["type"] == "LIMIT"
    assert params["timeInForce"] == tif
    assert params["quantity"] == "0.001"
    assert params["price"] == "50000"


def test_binance_market_order_drops_tif_and_price() -> None:
    harness = _BinanceHarness()
    adapter = FeedAdapter(harness, get_venue_mapper("BINANCE___SWAP"))

    result = adapter.make_order(
        _binance_order(order_type=OrderType.MARKET, price=None, time_in_force="IOC")
    )

    assert result == {"orderId": "1"}
    params = harness.requests[0]["params"]
    assert params["type"] == "MARKET"
    assert "timeInForce" not in params
    assert "price" not in params


# ---------------------------------------------------------------------------
# Cross-venue contrast: the same token has a different verdict per venue.
# ---------------------------------------------------------------------------

# Expected verdict per (time_in_force token, venue): True = accepted by the
# adapter, False = rejected before any provider call.  Binance is deliberately
# absent where it has no local gate at all (documented boundary, not asserted).
TIF_TOKEN_MATRIX: dict[str, dict[str, bool]] = {
    "GTC": {"CTP___FUTURE": False, "OKX___SWAP": True, "BINANCE___SWAP": True},
    "GFD": {"CTP___FUTURE": True, "OKX___SWAP": False},
    "DAY": {"CTP___FUTURE": True, "OKX___SWAP": False},
    "post_only": {"CTP___FUTURE": False, "OKX___SWAP": True},
    "IOC": {"CTP___FUTURE": False, "OKX___SWAP": True, "BINANCE___SWAP": True},
    "FOK": {"CTP___FUTURE": False, "OKX___SWAP": True, "BINANCE___SWAP": True},
}


def _limit_request(venue: str, tif: str) -> OrderRequest:
    if venue == "CTP___FUTURE":
        return _ctp_order(time_in_force=tif)
    if venue == "OKX___SWAP":
        return _okx_order(time_in_force=tif)
    return _binance_order(time_in_force=tif)


@pytest.mark.parametrize(
    ("tif", "venue", "accepted"),
    [
        (tif, venue, accepted)
        for tif, verdicts in TIF_TOKEN_MATRIX.items()
        for venue, accepted in verdicts.items()
    ],
    ids=[
        f"{tif}-{venue}"
        for tif, verdicts in TIF_TOKEN_MATRIX.items()
        for venue in verdicts
    ],
)
def test_cross_venue_tif_token_matrix(tif: str, venue: str, accepted: bool) -> None:
    adapter, calls = _direct_adapter(venue)
    request = _limit_request(venue, tif)

    if accepted:
        result = adapter.make_order(request)
        assert result is not None
        assert calls() == 1
    else:
        with pytest.raises((ValueError, InvalidOrderError)):
            adapter.make_order(request)
        assert calls() == 0


def test_cross_venue_market_order_type_matrix() -> None:
    """The order-type axis: CTP rejects market orders, OKX/Binance accept them."""

    ctp_adapter, ctp_calls = _direct_adapter("CTP___FUTURE")
    with pytest.raises(CapabilityNotSupportedError):
        ctp_adapter.make_order(_ctp_order(order_type=OrderType.MARKET, price=None))
    assert ctp_calls() == 0

    okx_adapter, okx_calls = _direct_adapter("OKX___SWAP")
    okx_adapter.make_order(_okx_order(order_type=OrderType.MARKET, price=None))
    assert okx_calls() == 1

    binance_adapter, binance_calls = _direct_adapter("BINANCE___SWAP")
    binance_adapter.make_order(_binance_order(order_type=OrderType.MARKET, price=None))
    assert binance_calls() == 1
