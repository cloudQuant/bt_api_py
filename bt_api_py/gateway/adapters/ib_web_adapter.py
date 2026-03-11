from __future__ import annotations

import queue
import threading
import time
from collections import defaultdict
from typing import Any

from bt_api_py.feeds.live_ib_web_feed import IbWebRequestDataFuture, IbWebRequestDataStock
from bt_api_py.feeds.live_ib_web_stream import IbWebAccountStream, IbWebDataStream
from bt_api_py.gateway.adapters.base import BaseGatewayAdapter
from bt_api_py.gateway.models import GatewayTick
from bt_api_py.gateway.protocol import CHANNEL_EVENT, CHANNEL_MARKET


class IbWebGatewayAdapter(BaseGatewayAdapter):
    def __init__(self, **kwargs: Any) -> None:
        normalized = dict(kwargs)
        self.asset_type = _normalize_asset_type(normalized.get("asset_type"))
        normalized["asset_type"] = self.asset_type
        normalized["base_url"] = normalized.get("base_url") or normalized.get("rest_url") or "https://localhost:5000"
        super().__init__(**normalized)
        self.kwargs = normalized
        self.q: queue.Queue[Any] = queue.Queue()
        self.feed = _create_feed(self.q, normalized)
        self.market_stream: IbWebDataStream | None = None
        self.account_stream: IbWebAccountStream | None = None
        self.aliases: dict[int, set[str]] = defaultdict(set)
        self.running = False
        self.thread: threading.Thread | None = None
        self.timeout = float(normalized.get("gateway_startup_timeout_sec", 10.0) or 10.0)

    def connect(self) -> None:
        if self.running:
            return
        self.feed.connect()
        if not self.feed.is_connected():
            raise RuntimeError("ib_web feed not ready")
        self._ensure_account_stream()
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def disconnect(self) -> None:
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        if self.market_stream is not None:
            self.market_stream.stop()
        if self.account_stream is not None:
            self.account_stream.stop()
        self.feed.disconnect()
        self.market_stream = None
        self.account_stream = None

    def subscribe_symbols(self, symbols: list[str]) -> dict[str, Any]:
        done: list[str] = []
        topics: list[dict[str, Any]] = []
        for raw in symbols:
            alias = str(raw or "").strip()
            if not alias:
                continue
            conid = int(self.feed._resolve_conid_param(alias))
            self.aliases[conid].update({alias, str(conid)})
            topics.append({"topic": "market_data", "conid": conid})
            done.append(alias)
        if topics:
            self._ensure_market_stream(topics)
        return {"symbols": done}

    def get_balance(self) -> dict[str, Any]:
        response = self.feed.get_balance()
        if isinstance(response, dict):
            cash = _coerce_float(
                response.get("cash")
                or response.get("CashBalance")
                or response.get("availablefunds")
                or response.get("AvailableFunds"),
                0.0,
            )
            value = _coerce_float(
                response.get("value")
                or response.get("NetLiquidation")
                or response.get("EquityWithLoanValue")
                or response.get("equity"),
                cash,
            )
            payload = dict(response)
            payload.setdefault("cash", cash)
            payload.setdefault("value", value)
            return payload
        return {"cash": 0.0, "value": 0.0, "raw": response}

    def get_positions(self) -> list[dict[str, Any]]:
        try:
            response = self.feed.get_position()
        except NotImplementedError:
            return []
        if isinstance(response, list):
            return [item for item in response if isinstance(item, dict)]
        if isinstance(response, dict):
            rows = response.get("positions")
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
            return [response]
        return []

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("data_name") or payload.get("symbol") or "").strip()
        side = str(payload.get("side") or "buy").lower()
        order_type = str(payload.get("order_type") or "").strip().lower()
        if not order_type:
            order_type = f"{side}-{'market' if payload.get('price') in (None, '') else 'limit'}"
        extra_data = dict(payload.get("extra_data") or {})
        response = self.feed.make_order(
            symbol,
            volume=payload.get("size") or payload.get("volume") or 0,
            price=payload.get("price"),
            order_type=order_type,
            client_order_id=payload.get("client_order_id") or payload.get("bt_order_ref"),
            extra_data=extra_data,
        )
        row = _first_row(response)
        order_id = row.get("order_id") or row.get("orderId") or row.get("id") or payload.get("client_order_id") or payload.get("bt_order_ref") or ""
        return {
            "id": order_id,
            "order_id": order_id,
            "external_order_id": row.get("orderId") or row.get("id") or order_id,
            "details": row or response,
        }

    def cancel_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("data_name") or payload.get("symbol") or payload.get("instrument") or "").strip()
        order_id = payload.get("order_id") or payload.get("external_order_id") or payload.get("id")
        extra_data = dict(payload.get("extra_data") or {})
        response = self.feed.cancel_order(symbol, order_id, extra_data=extra_data)
        row = _first_row(response)
        return {
            "id": row.get("orderId") or order_id,
            "order_id": row.get("orderId") or order_id,
            "status": row.get("status") or row.get("order_status") or "canceled",
            "details": row or response,
        }

    def _ensure_market_stream(self, topics: list[dict[str, Any]]) -> None:
        if self.market_stream is None or not self.market_stream.is_running():
            self.market_stream = IbWebDataStream(self.q, **self.kwargs, topics=topics)
            self.market_stream.start()
            if not self.market_stream.wait_connected(timeout=self.timeout):
                raise RuntimeError("ib_web market stream not ready")
            return
        self.market_stream.subscribe_topics(topics)

    def _ensure_account_stream(self) -> None:
        if self.account_stream is not None and self.account_stream.is_running():
            return
        topics = [{"topic": "account"}, {"topic": "order"}, {"topic": "trade"}]
        self.account_stream = IbWebAccountStream(self.q, **self.kwargs, topics=topics)
        self.account_stream.start()
        if not self.account_stream.wait_connected(timeout=self.timeout):
            raise RuntimeError("ib_web account stream not ready")

    def _run(self) -> None:
        while self.running:
            try:
                item = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if isinstance(item, dict):
                self._handle_item(item)

    def _handle_item(self, item: dict[str, Any]) -> None:
        item_type = str(item.get("type") or "").strip().lower()
        data = dict(item.get("data") or {})
        if item_type == "market_data":
            self._emit_market(data)
            return
        kind = {
            "order_update": "order",
            "trade_update": "trade",
            "account_update": "account",
            "pnl_update": "pnl",
        }.get(item_type, item_type or "system")
        self.emit(CHANNEL_EVENT, {"kind": kind, "exchange": "IB_WEB", "data": data})

    def _emit_market(self, data: dict[str, Any]) -> None:
        conid_raw = data.get("conidEx") or data.get("conid") or data.get("contract_id") or ""
        alias_candidates = self.aliases.get(_safe_int(conid_raw), set())
        if not alias_candidates:
            alias_candidates = {str(data.get("symbol") or conid_raw or "")}
        price = _coerce_float(data.get("31") or data.get("last") or data.get("lastPrice"), 0.0)
        bid_price = _coerce_float(data.get("84") or data.get("bid") or data.get("bidPrice"), None)
        ask_price = _coerce_float(data.get("86") or data.get("ask") or data.get("askPrice"), None)
        bid_volume = _coerce_float(data.get("85") or data.get("bidSize") or data.get("bid_volume"), None)
        ask_volume = _coerce_float(data.get("88") or data.get("askSize") or data.get("ask_volume"), None)
        volume = _coerce_float(data.get("volume") or data.get("lastSize") or data.get("87"), 0.0)
        now = time.time()
        instrument_id = str(conid_raw or data.get("symbol") or "")
        exchange_id = str(data.get("exchange") or data.get("listingExchange") or "")
        for alias in alias_candidates:
            if not alias:
                continue
            self.emit(
                CHANNEL_MARKET,
                GatewayTick(
                    timestamp=now,
                    local_time=now,
                    symbol=alias,
                    exchange="IB_WEB",
                    asset_type=self.asset_type.lower(),
                    price=price,
                    volume=volume,
                    bid_price=bid_price,
                    ask_price=ask_price,
                    bid_volume=bid_volume,
                    ask_volume=ask_volume,
                    instrument_id=instrument_id,
                    exchange_id=exchange_id,
                ),
            )


def _normalize_asset_type(value: Any) -> str:
    text = str(value or "STK").strip().upper()
    if text in {"STOCK", "STK", "EQUITY"}:
        return "STK"
    if text in {"FUTURE", "FUT"}:
        return "FUT"
    return text or "STK"


def _create_feed(data_queue: Any, kwargs: dict[str, Any]) -> Any:
    if _normalize_asset_type(kwargs.get("asset_type")) == "FUT":
        return IbWebRequestDataFuture(data_queue, **kwargs)
    return IbWebRequestDataStock(data_queue, **kwargs)


def _first_row(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if isinstance(response, list):
        for item in response:
            if isinstance(item, dict):
                return item
    return {}


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: Any, default: float | None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
