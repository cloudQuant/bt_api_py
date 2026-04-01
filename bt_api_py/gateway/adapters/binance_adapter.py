"""Binance gateway adapter for SWAP and SPOT."""

from __future__ import annotations

import queue
import threading
import time
from collections import defaultdict
from typing import Any

from bt_api_py.containers.exchanges.binance_exchange_data import (
    BinanceExchangeDataSpot,
    BinanceExchangeDataSwap,
)
from bt_api_py.containers.orders.binance_order import BinanceSwapWssOrderData
from bt_api_py.containers.tickers.binance_ticker import BinanceWssTickerData
from bt_api_py.containers.trades.binance_trade import BinanceSwapWssTradeData
from bt_api_py.feeds.live_binance.swap import (
    BinanceAccountWssDataSwap,
    BinanceMarketWssDataSwap,
    BinanceRequestDataSwap,
)
from bt_api_py.gateway.adapters.base import BaseGatewayAdapter
from bt_api_py.gateway.models import GatewayTick
from bt_api_py.gateway.protocol import CHANNEL_EVENT, CHANNEL_MARKET


def _normalize_asset_type(raw: Any) -> str:
    value = str(raw or "SWAP").strip().upper()
    mapping = {"SWAP": "SWAP", "SPOT": "SPOT", "FUTURE": "SWAP", "FUT": "SWAP"}
    return mapping.get(value, value)


def _create_feed(q: queue.Queue, kwargs: dict[str, Any]):
    asset_type = kwargs.get("asset_type", "SWAP")
    if asset_type == "SPOT":
        from bt_api_py.feeds.live_binance.spot import BinanceRequestDataSpot

        return BinanceRequestDataSpot(q, **kwargs)
    return BinanceRequestDataSwap(q, **kwargs)


def _create_exchange_data(asset_type: str):
    if asset_type == "SPOT":
        return BinanceExchangeDataSpot()
    return BinanceExchangeDataSwap()


class BinanceGatewayAdapter(BaseGatewayAdapter):
    """Gateway adapter wrapping Binance REST + WSS feeds."""

    def __init__(self, **kwargs: Any) -> None:
        normalized = dict(kwargs)
        self.asset_type = _normalize_asset_type(normalized.get("asset_type"))
        normalized["asset_type"] = self.asset_type
        normalized.setdefault("public_key", normalized.get("api_key", ""))
        normalized.setdefault("private_key", normalized.get("secret_key", ""))
        normalized.setdefault("exchange_name", "BINANCE")
        exchange_data = _create_exchange_data(self.asset_type)
        normalized["exchange_data"] = exchange_data
        super().__init__(**normalized)
        self.kwargs = normalized
        self.q: queue.Queue[Any] = queue.Queue()
        self.feed = _create_feed(self.q, normalized)
        self.market_stream = None
        self.account_stream = None
        self.aliases: dict[str, set[str]] = defaultdict(set)
        self.running = False
        self.thread: threading.Thread | None = None
        self.account_stream_thread: threading.Thread | None = None
        self.timeout = float(normalized.get("gateway_startup_timeout_sec", 10.0) or 10.0)

    def connect(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.logger.info("BinanceGatewayAdapter connected")

    def disconnect(self) -> None:
        self.running = False
        thread = self.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self.thread = None
        self.market_stream = None
        self.account_stream = None
        self.account_stream_thread = None
        self.aliases = defaultdict(set)
        self.logger.info("BinanceGatewayAdapter disconnected")

    def _start_account_stream_async(self, account_kwargs: dict[str, Any]) -> None:
        try:
            if self.asset_type == "SPOT":
                from bt_api_py.feeds.live_binance.spot import BinanceAccountWssDataSpot

                stream = BinanceAccountWssDataSpot(self.q, **account_kwargs)
            else:
                stream = BinanceAccountWssDataSwap(self.q, **account_kwargs)
            stream.start()
            self.account_stream = stream
            self.logger.info("Binance account stream started")
        except Exception as exc:
            self.account_stream = None
            self.logger.warning(f"Binance account stream unavailable: {exc}")
        finally:
            self.account_stream_thread = None

    def subscribe_symbols(self, symbols: list[str]) -> dict[str, Any]:
        topics = [{"topic": "ticker", "symbol": s} for s in symbols]
        wss_kwargs = dict(self.kwargs)
        if self.asset_type == "SPOT":
            wss_url = "wss://stream.binance.com:9443/ws"
        else:
            wss_url = "wss://fstream.binance.com/ws"
        wss_kwargs["wss_url"] = wss_url
        wss_kwargs["wss_name"] = "binance_market_data"
        wss_kwargs["topics"] = topics

        if self.market_stream is None:
            if self.asset_type == "SPOT":
                from bt_api_py.feeds.live_binance.spot import BinanceMarketWssDataSpot

                self.market_stream = BinanceMarketWssDataSpot(self.q, **wss_kwargs)
            else:
                self.market_stream = BinanceMarketWssDataSwap(self.q, **wss_kwargs)
            self.market_stream.start()
            self.logger.info(f"Binance market stream started for {symbols}")

        if self.account_stream is None:
            account_kwargs = dict(wss_kwargs)
            account_kwargs["topics"] = [
                {"topic": "account"},
                {"topic": "order"},
                {"topic": "trade"},
            ]
            if self.account_stream_thread is None or not self.account_stream_thread.is_alive():
                self.account_stream_thread = threading.Thread(
                    target=self._start_account_stream_async,
                    args=(account_kwargs,),
                    daemon=True,
                )
                self.account_stream_thread.start()

        for symbol in symbols:
            self.aliases[symbol].add(symbol)
        return {"symbols": symbols}

    def get_balance(self) -> dict[str, Any]:
        try:
            result = self.feed.get_balance()
            data = result.get_data() if hasattr(result, "get_data") else result
            if isinstance(data, list) and len(data) > 0:
                item = data[0]
                if hasattr(item, "get_all_data"):
                    return item.get_all_data()
                return dict(item) if isinstance(item, dict) else {"raw": str(item)}
            if isinstance(data, dict):
                return data
            return {"raw": str(data)}
        except Exception as exc:
            self.logger.warning(f"get_balance error: {exc}")
            return {"error": str(exc)}

    def get_positions(self) -> list[dict[str, Any]]:
        try:
            result = self.feed.get_position()
            data = result.get_data() if hasattr(result, "get_data") else result
            if isinstance(data, list):
                positions = []
                for item in data:
                    if hasattr(item, "get_all_data"):
                        positions.append(item.get_all_data())
                    elif isinstance(item, dict):
                        positions.append(item)
                return positions
            return []
        except Exception as exc:
            self.logger.warning(f"get_positions error: {exc}")
            return []

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = payload.get("data_name") or payload.get("symbol") or ""
        volume = float(payload.get("volume") or payload.get("size") or 0)
        price = payload.get("price")
        if price is not None:
            price = float(price)
        side = str(payload.get("side") or "buy").lower()
        order_type = str(payload.get("order_type") or "limit").lower()
        offset = str(payload.get("offset") or "open").lower()
        order_type_str = f"{side}-{order_type}"
        client_order_id = payload.get("client_order_id")

        result = self.feed.make_order(
            symbol=symbol,
            vol=volume,
            price=price,
            order_type=order_type_str,
            offset=offset,
            client_order_id=client_order_id,
        )
        data = result.get_data() if hasattr(result, "get_data") else result
        if isinstance(data, list) and len(data) > 0:
            item = data[0]
            if hasattr(item, "get_all_data"):
                return item.get_all_data()
            return dict(item) if isinstance(item, dict) else {"raw": str(item)}
        if isinstance(data, dict):
            return data
        return {"raw": str(data)}

    def cancel_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = payload.get("data_name") or payload.get("symbol") or ""
        order_id = payload.get("order_id") or payload.get("external_order_id")
        client_order_id = payload.get("client_order_id")

        cancel_kwargs: dict[str, Any] = {}
        if client_order_id:
            cancel_kwargs["client_order_id"] = client_order_id

        result = self.feed.cancel_order(
            symbol=symbol,
            order_id=order_id,
            **cancel_kwargs,
        )
        data = result.get_data() if hasattr(result, "get_data") else result
        if isinstance(data, dict):
            return data
        return {"raw": str(data)}

    def _run(self) -> None:
        while self.running:
            try:
                item = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._dispatch_item(item)
            except Exception as exc:
                self.logger.warning(f"Binance adapter dispatch error: {exc}")

    def _dispatch_item(self, item: Any) -> None:
        if isinstance(item, BinanceWssTickerData):
            self._emit_ticker(item)
        elif isinstance(item, BinanceSwapWssOrderData):
            self._emit_order(item)
        elif isinstance(item, BinanceSwapWssTradeData):
            self._emit_trade(item)
        else:
            event_name = getattr(item, "event", None) or type(item).__name__
            self.emit(CHANNEL_EVENT, {"kind": "raw", "type": event_name})

    def _emit_ticker(self, ticker: BinanceWssTickerData) -> None:
        ticker.init_data()
        symbol = ticker.get_symbol_name() or ""
        server_time = ticker.get_server_time() or 0.0
        ts = server_time / 1000.0 if server_time > 1e12 else server_time
        bid = ticker.get_bid_price() or 0.0
        ask = ticker.get_ask_price() or 0.0
        tick = GatewayTick(
            timestamp=ts,
            symbol=symbol,
            exchange="BINANCE",
            asset_type=self.asset_type,
            local_time=time.time(),
            bid_price=bid,
            ask_price=ask,
            bid_volume=ticker.get_bid_volume() or 0.0,
            ask_volume=ticker.get_ask_volume() or 0.0,
            price=(bid + ask) / 2.0 if bid and ask else 0.0,
        )
        self.emit(CHANNEL_MARKET, tick)

    def _emit_order(self, order: BinanceSwapWssOrderData) -> None:
        try:
            order.init_data()
            self.emit(
                CHANNEL_EVENT,
                {
                    "kind": "order",
                    "exchange": "BINANCE",
                    "symbol": order.get_symbol_name(),
                    "order_id": order.get_order_id(),
                    "client_order_id": order.get_client_order_id(),
                    "status": order.get_order_status(),
                    "side": order.get_order_side(),
                    "price": order.get_order_price(),
                    "volume": order.get_order_size(),
                    "filled": order.get_executed_qty(),
                },
            )
        except Exception as exc:
            self.logger.warning(f"_emit_order error: {exc}")

    def _emit_trade(self, trade: BinanceSwapWssTradeData) -> None:
        try:
            trade.init_data()
            self.emit(
                CHANNEL_EVENT,
                {
                    "kind": "trade",
                    "exchange": "BINANCE",
                    "symbol": trade.get_symbol_name(),
                    "trade_id": trade.get_trade_id(),
                    "order_id": trade.get_order_id(),
                    "price": trade.get_trade_price(),
                    "volume": trade.get_trade_volume(),
                    "side": trade.get_trade_side(),
                },
            )
        except Exception as exc:
            self.logger.warning(f"_emit_trade error: {exc}")
