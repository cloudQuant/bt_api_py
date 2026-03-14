from __future__ import annotations

import queue
import re
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from bt_api_py.containers.ctp.ctp_order import CtpOrderData
from bt_api_py.containers.ctp.ctp_ticker import CtpTickerData
from bt_api_py.containers.ctp.ctp_trade import CtpTradeData
from bt_api_py.feeds.live_ctp_feed import CtpMarketStream, CtpRequestDataFuture, CtpTradeStream
from bt_api_py.gateway.adapters.base import BaseGatewayAdapter
from bt_api_py.gateway.models import GatewayTick
from bt_api_py.gateway.protocol import CHANNEL_EVENT, CHANNEL_MARKET

_CTP_EXCHANGES = frozenset({"SHFE", "DCE", "CZCE", "CFFEX", "INE", "GFEX"})
_CZCE_PRODUCT_PREFIXES = frozenset(
    {
        "AP",
        "CF",
        "CJ",
        "CY",
        "FG",
        "JR",
        "LR",
        "MA",
        "OI",
        "PF",
        "PK",
        "PM",
        "PX",
        "RI",
        "RM",
        "RS",
        "SA",
        "SF",
        "SM",
        "SR",
        "TA",
        "UR",
        "WH",
        "ZC",
    }
)


class CtpGatewayAdapter(BaseGatewayAdapter):
    def __init__(self, **kwargs: Any) -> None:
        normalized = dict(kwargs)
        normalized["md_front"] = normalized.get("md_front") or normalized.get("md_address") or ""
        normalized["td_front"] = normalized.get("td_front") or normalized.get("td_address") or ""
        normalized["user_id"] = normalized.get("user_id") or normalized.get("investor_id") or ""
        super().__init__(**normalized)
        self.q: queue.Queue[Any] = queue.Queue()
        self.market = CtpMarketStream(self.q, **normalized)
        self.trade = CtpTradeStream(self.q, **normalized)
        self.feed = CtpRequestDataFuture(None, **normalized)
        self.aliases: dict[str, set[str]] = defaultdict(set)
        self.last_volume: dict[str, float] = {}
        self.last_price: dict[str, float] = {}
        self._price_ticks: dict[str, float] = {}
        self.running = False
        self.thread: threading.Thread | None = None
        self.timeout = float(normalized.get("gateway_startup_timeout_sec", 10.0) or 10.0)

    def connect(self) -> None:
        if self.running:
            return
        self.market.start()
        self.trade.start()
        if not self.market.wait_connected(timeout=self.timeout):
            raise RuntimeError("ctp market not ready")
        if not self.trade.wait_connected(timeout=self.timeout):
            raise RuntimeError("ctp trade not ready")
        self.feed._trader = self.trade.trader_client
        self.feed._connected = True
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def disconnect(self) -> None:
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        self.feed._trader = None
        self.feed._connected = False
        self.market.stop()
        self.trade.stop()

    def subscribe_symbols(self, symbols: list[str]) -> dict[str, Any]:
        topics = []
        done = []
        for raw in symbols:
            alias = str(raw or "").strip()
            instrument, _ = _split(alias)
            if not instrument:
                continue
            self.aliases[instrument].update({alias, instrument})
            topics.append({"topic": "tick", "symbol": instrument})
            done.append(alias)
        if topics:
            self.market.subscribe_topics(topics)
        return {"symbols": done}

    def get_balance(self) -> dict[str, Any]:
        rows = self.feed.get_account().get_data()
        if not rows:
            return {"cash": 0.0, "value": 0.0}
        row = rows[0].init_data()
        return {"cash": float(row.get_available_margin() or 0.0), "value": float(row.get_margin() or 0.0)}

    def get_positions(self) -> list[dict[str, Any]]:
        out = []
        for raw in self.feed.get_position().get_data() or []:
            row = raw.init_data()
            out.append({"instrument": row.get_symbol_name(), "direction": row.get_position_direction(), "volume": row.get_position_volume(), "price": row.get_avg_price(), "exchange_id": row.exchange_id})
        return out

    def _get_price_tick(self, instrument: str) -> float:
        """Return the minimum price tick for *instrument*."""
        cached = self._price_ticks.get(instrument)
        if cached is not None:
            return cached
        try:
            trader = self.feed.trader_client
            if trader and hasattr(trader, "query_instrument"):
                info = trader.query_instrument(instrument, timeout=2)
                if info and hasattr(info, "PriceTick"):
                    tick = float(info.PriceTick or 0)
                    if tick > 0:
                        self._price_ticks[instrument] = tick
                        return tick
        except Exception:
            pass
        return 1.0

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("data_name") or payload.get("symbol") or "").strip()
        instrument, exchange_id = _split(name)
        side = str(payload.get("side") or "buy").lower()
        is_market = "market" in str(payload.get("order_type") or "").lower()
        price = payload.get("price")
        needs_price_conversion = is_market or price is None or (price is not None and float(price) <= 0)

        if needs_price_conversion:
            lp = self.last_price.get(instrument or name)
            if not lp or lp <= 0:
                raise RuntimeError(
                    f"CTP market order for {instrument or name} rejected: "
                    f"no recent tick price available to convert to limit order"
                )
            pt = self._get_price_tick(instrument or name)
            slippage = pt * 5
            price = (lp + slippage) if side == "buy" else max(lp - slippage, pt)
            price = round(price, 4)
            self.logger.info(
                "CTP order price converted: %s %s last=%.4f tick=%.4f -> limit=%.4f",
                instrument, side, lp, pt, price,
            )

        kind = "limit"
        rsp = self.feed.make_order(instrument or name, volume=payload.get("size") or 0, price=price, order_type=f"{side}-{kind}", offset=str(payload.get("offset") or "open"), client_order_id=payload.get("client_order_id") or payload.get("bt_order_ref"), exchange_id=exchange_id or payload.get("exchange_id") or "")
        if not rsp.get_status():
            raise RuntimeError("ctp order failed")
        row = rsp.get_data()[0].init_data()
        oid = row.get_order_id() or row.get_client_order_id()
        return {"id": oid, "order_id": oid, "external_order_id": oid, "order_ref": row.get_client_order_id(), "front_id": row.front_id, "session_id": row.session_id, "exchange_id": row.get_order_exchange_id(), "details": {"bt_order_ref": payload.get("bt_order_ref")}}

    def cancel_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("data_name") or payload.get("symbol") or payload.get("instrument") or "").strip()
        instrument, exchange_id = _split(name)
        rsp = self.feed.cancel_order(instrument or name, order_id=payload.get("order_id") or payload.get("external_order_id"), exchange_id=exchange_id or payload.get("exchange_id") or "", front_id=payload.get("front_id"), session_id=payload.get("session_id"), order_ref=payload.get("order_ref"))
        if not rsp.get_status():
            raise RuntimeError("ctp cancel failed")
        data = dict((rsp.get_data() or [{}])[0])
        return {"id": data.get("OrderSysID") or payload.get("order_id") or payload.get("order_ref"), "order_ref": data.get("OrderRef") or payload.get("order_ref"), "order_sys_id": data.get("OrderSysID") or payload.get("order_id"), "front_id": data.get("FrontID") or payload.get("front_id"), "session_id": data.get("SessionID") or payload.get("session_id"), "exchange_id": data.get("ExchangeID") or exchange_id}

    def _run(self) -> None:
        while self.running:
            try:
                item = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if isinstance(item, CtpTickerData):
                self._tick(item.init_data())
            elif isinstance(item, CtpOrderData):
                self.emit(CHANNEL_EVENT, _order(item.init_data(), self.aliases))
            elif isinstance(item, CtpTradeData):
                self.emit(CHANNEL_EVENT, _trade(item.init_data(), self.aliases))

    def _tick(self, row: CtpTickerData) -> None:
        instrument = row.get_symbol_name() or ""
        price = float(row.get_last_price() or 0.0)
        if not instrument or price <= 0:
            return
        self.last_price[instrument] = price
        total = float(row.get_last_volume() or 0.0)
        prev = self.last_volume.get(instrument)
        self.last_volume[instrument] = total
        volume = max(total - prev, 0.0) if prev is not None else 0.0
        day = str(row.trading_day or "")
        stamp = time.time()
        dt = datetime.fromtimestamp(stamp)
        if len(day) == 8 and day.isdigit() and row.update_time_val:
            dt = datetime.strptime(f"{day} {row.update_time_val}", "%Y%m%d %H:%M:%S").replace(microsecond=int(row.update_millisec or 0) * 1000)
            stamp = dt.timestamp()
        for alias in self.aliases.get(instrument) or {instrument}:
            self.emit(CHANNEL_MARKET, GatewayTick(timestamp=stamp, symbol=alias, exchange=row.exchange_id or "", asset_type="futures", local_time=time.time(), price=price, volume=volume, datetime=dt, instrument_id=instrument, exchange_id=row.exchange_id or "", trading_day=row.trading_day or "", update_time=row.update_time_val or "", update_millisec=int(row.update_millisec or 0), bid_price=row.get_bid_price(), ask_price=row.get_ask_price(), bid_volume=float(row.get_bid_volume() or 0.0), ask_volume=float(row.get_ask_volume() or 0.0), openinterest=float(row.get_open_interest() or 0.0), turnover=float(row.turnover or 0.0), trade_id=f"{instrument}-{int(total)}"))


def _split(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if "." in text:
        left, right = text.split(".", 1)
        exchange = right.strip().upper()
        return _normalize_instrument(left.strip(), exchange), exchange
    if "_" in text:
        exchange, instrument = text.split("_", 1)
        exchange = exchange.strip().upper()
        if exchange in _CTP_EXCHANGES:
            return _normalize_instrument(instrument.strip(), exchange), exchange
    return _normalize_instrument(text, ""), ""


def _normalize_instrument(instrument: str, exchange_id: str = "") -> str:
    text = str(instrument or "").strip()
    if not text:
        return ""
    match = re.fullmatch(r"([A-Za-z]+)(\d{4})", text)
    if not match:
        return text
    prefix, digits = match.groups()
    exchange = str(exchange_id or "").strip().upper()
    if exchange == "CZCE" or (not exchange and prefix.upper() in _CZCE_PRODUCT_PREFIXES):
        return f"{prefix}{digits[-3:]}"
    return text


def _alias(aliases: dict[str, set[str]], instrument: str) -> str:
    return next(iter(aliases.get(instrument) or {instrument}), instrument)


def _status(value: Any) -> str:
    raw = str(getattr(value, "value", value) or "submitted").lower()
    return {"new": "accepted", "partially_filled": "partial", "filled": "completed"}.get(raw, raw)


def _order(row: CtpOrderData, aliases: dict[str, set[str]]) -> dict[str, Any]:
    instrument = row.get_symbol_name() or ""
    size = int(row.get_order_size() or 0)
    filled = int(row.get_executed_qty() or 0)
    return {"kind": "order", "order_ref": row.get_client_order_id(), "external_order_id": row.get_order_id() or row.get_client_order_id(), "data_name": _alias(aliases, instrument), "instrument": instrument, "exchange_id": row.get_order_exchange_id(), "front_id": row.front_id, "session_id": row.session_id, "status": _status(row.get_order_status()), "status_msg": row.status_msg or "", "side": row.get_order_side(), "offset": row.get_order_offset(), "price": row.get_order_price(), "size": size, "filled": filled, "remaining": max(size - filled, 0)}


def _trade(row: CtpTradeData, aliases: dict[str, set[str]]) -> dict[str, Any]:
    instrument = row.get_symbol_name() or ""
    return {"kind": "trade", "trade_id": row.get_trade_id(), "order_ref": row.get_client_order_id(), "external_order_id": row.get_order_id() or row.get_client_order_id(), "data_name": _alias(aliases, instrument), "instrument": instrument, "exchange_id": row.exchange_id, "side": row.get_trade_side(), "offset": row.get_trade_offset(), "price": row.get_trade_price(), "size": row.get_trade_volume()}
