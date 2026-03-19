"""MT5 Gateway Adapter — bridges sync BaseGatewayAdapter to async pymt5.MT5WebClient."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from bt_api_py.gateway.adapters.base import BaseGatewayAdapter
from bt_api_py.gateway.models import GatewayTick
from bt_api_py.gateway.protocol import CHANNEL_EVENT, CHANNEL_MARKET

logger = logging.getLogger(__name__)

# Timeframe string → period in minutes (used by pymt5.get_rates)
_TIMEFRAME_MAP: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
    "W1": 10080,
    "MN1": 43200,
}

# MT5 order_state → gateway status (from pymt5.constants ORDER_STATE_*)
_MT5_ORDER_STATE_MAP: dict[int, str] = {
    0: "submitted",    # ORDER_STATE_STARTED
    1: "accepted",     # ORDER_STATE_PLACED
    2: "canceled",     # ORDER_STATE_CANCELED
    3: "partial",      # ORDER_STATE_PARTIAL
    4: "completed",    # ORDER_STATE_FILLED
    5: "rejected",     # ORDER_STATE_REJECTED
    6: "canceled",     # ORDER_STATE_EXPIRED
}

# MT5 trade return codes → gateway status
_RETCODE_STATUS: dict[int, str] = {
    10004: "rejected",     # REQUOTE
    10006: "rejected",     # REJECT
    10007: "canceled",     # CANCEL
    10008: "submitted",    # PLACED
    10009: "completed",    # DONE
    10010: "partial",      # DONE_PARTIAL
    10013: "rejected",     # INVALID
    10014: "rejected",     # INVALID_VOLUME
    10015: "rejected",     # INVALID_PRICE
    10016: "rejected",     # INVALID_STOPS
    10017: "rejected",     # TRADE_DISABLED
    10018: "rejected",     # MARKET_CLOSED
    10019: "rejected",     # NO_MONEY
    10030: "rejected",     # INVALID_FILL
    10031: "rejected",     # CONNECTION
}


def _resolve_default_filling() -> int | None:
    try:
        import pymt5

        value = getattr(pymt5, "ORDER_FILLING_FOK", None)
        if value is not None:
            return int(value)
    except Exception:
        return None
    return None


class Mt5GatewayAdapter(BaseGatewayAdapter):
    """Gateway adapter for MT5 via pymt5 WebSocket client.

    Uses an internal asyncio event-loop thread to run the async MT5WebClient,
    while exposing the synchronous BaseGatewayAdapter interface required by
    GatewayRuntime.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any = None  # MT5WebClient instance

        # Config
        self._login = int(kwargs.get("login") or 0)
        self._password = str(kwargs.get("password") or "")
        self._ws_uri = str(kwargs.get("ws_uri") or "")
        self._timeout = float(kwargs.get("timeout") or 60.0)
        self._heartbeat_interval = float(kwargs.get("heartbeat_interval") or 30.0)
        self._auto_reconnect = bool(kwargs.get("auto_reconnect", True))
        self._max_reconnect_attempts = int(kwargs.get("max_reconnect_attempts") or 5)

        # Symbol mapping
        self._symbol_suffix = str(kwargs.get("symbol_suffix") or "")
        self._symbol_map: dict[str, str] = dict(kwargs.get("symbol_map") or {})
        self._resolved_symbols: dict[str, str] = {}
        self._reverse_resolved_symbols: dict[str, str] = {
            str(v): str(k) for k, v in self._symbol_map.items()
        }

        # State
        self._subscribed_symbols: list[str] = []
        self._symbol_specs: dict[str, dict[str, Any]] = {}
        self._running = False

    # ---- BaseGatewayAdapter interface (sync) ----

    def connect(self) -> None:
        if self._running:
            return
        if not self._login or not self._password:
            raise ValueError("MT5 adapter requires 'login' and 'password' parameters")
        self._loop = asyncio.new_event_loop()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        future = asyncio.run_coroutine_threadsafe(self._async_connect(), self._loop)
        # connect+login+load_symbols can take >30s (6000+ symbols); use generous timeout
        connect_timeout = max(self._timeout * 4, 120.0)
        future.result(timeout=connect_timeout)
        self.logger.info(f"Mt5GatewayAdapter connected (login={self._login})")

    def disconnect(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._loop is not None and self._client is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(self._client.close(), self._loop)
                future.result(timeout=5.0)
            except Exception:
                pass
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._loop = None
        self._thread = None
        self._client = None
        self.logger.info("Mt5GatewayAdapter disconnected")

    def subscribe_symbols(self, symbols: list[str]) -> dict[str, Any]:
        resolved = [self._resolve_symbol(s) for s in symbols]
        future = asyncio.run_coroutine_threadsafe(
            self._async_subscribe(symbols, resolved), self._loop
        )
        future.result(timeout=self._timeout)
        self._subscribed_symbols.extend(symbols)
        return {"symbols": symbols}

    def get_balance(self) -> dict[str, Any]:
        getter = getattr(self._client, "get_account_summary", None)
        if getter is None:
            getter = getattr(self._client, "get_account")
        future = asyncio.run_coroutine_threadsafe(getter(), self._loop)
        acct = future.result(timeout=self._timeout)
        if isinstance(acct, dict):
            balance = acct.get("balance", 0.0)
            equity = acct.get("equity", 0.0)
            credit = acct.get("credit", 0.0)
            currency = acct.get("currency", "")
            leverage = acct.get("leverage", 0)
            margin = acct.get("margin", 0.0)
            margin_free = acct.get("margin_free", 0.0)
            profit = acct.get("profit", 0.0)
        else:
            balance = getattr(acct, "balance", 0.0)
            equity = getattr(acct, "equity", 0.0)
            credit = getattr(acct, "credit", 0.0)
            currency = getattr(acct, "currency", "")
            leverage = getattr(acct, "leverage", 0)
            margin = getattr(acct, "margin", 0.0)
            margin_free = getattr(acct, "margin_free", 0.0)
            profit = getattr(acct, "profit", 0.0)
        return {
            "balance": balance,
            "equity": equity,
            "credit": credit,
            "currency": currency,
            "leverage": leverage,
            "cash": balance,
            "value": equity,
            "margin": margin,
            "margin_free": margin_free,
            "profit": profit,
        }

    def get_positions(self) -> list[dict[str, Any]]:
        future = asyncio.run_coroutine_threadsafe(self._client.get_positions(), self._loop)
        positions = future.result(timeout=self._timeout)
        result = []
        for p in (positions or []):
            result.append({
                "instrument": p.get("trade_symbol", ""),
                "position_id": p.get("position_id"),
                "direction": "buy" if p.get("trade_action") == 0 else "sell",
                "volume": p.get("trade_volume", 0),
                "price": p.get("price_open", 0.0),
                "sl": p.get("sl", 0.0),
                "tp": p.get("tp", 0.0),
                "profit": p.get("profit", 0.0),
                "commission": p.get("commission", 0.0),
                "swap": p.get("storage", 0.0),
                "comment": p.get("comment", ""),
            })
        return result

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        future = asyncio.run_coroutine_threadsafe(
            self._async_place_order(payload), self._loop
        )
        return future.result(timeout=self._timeout)

    def cancel_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        order_id = payload.get("order_id") or payload.get("external_order_id")
        if order_id is None:
            return {"status": "error", "error": "missing order_id"}
        future = asyncio.run_coroutine_threadsafe(
            self._client.cancel_pending_order(int(order_id)), self._loop
        )
        result = future.result(timeout=self._timeout)
        return self._trade_result_to_dict(result)

    def get_bars(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        period_minutes = _TIMEFRAME_MAP.get(timeframe)
        if period_minutes is None:
            return []
        to_ts = int(time.time())
        from_ts = to_ts - count * period_minutes * 60
        mt5_symbol = self._resolve_symbol(symbol)
        future = asyncio.run_coroutine_threadsafe(
            self._client.get_rates(mt5_symbol, period_minutes, from_ts, to_ts),
            self._loop,
        )
        rates = future.result(timeout=self._timeout)
        if not rates:
            return []
        return [
            {
                "timestamp": float(r.get("time", 0)),
                "open": float(r.get("open", 0)),
                "high": float(r.get("high", 0)),
                "low": float(r.get("low", 0)),
                "close": float(r.get("close", 0)),
                "volume": float(r.get("tick_volume", 0)),
                "symbol": symbol,
                "timeframe": timeframe,
            }
            for r in rates
        ]

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        cached = self._symbol_specs.get(symbol)
        if cached:
            return dict(cached)
        mt5_symbol = self._resolve_symbol(symbol)
        future = asyncio.run_coroutine_threadsafe(
            self._client.get_full_symbol_info(mt5_symbol), self._loop
        )
        info = future.result(timeout=self._timeout)
        if not info:
            return {}
        spec = {
            "contract_size": info.get("contract_size", 100000),
            "volume_min": info.get("volume_min", 0.01),
            "volume_max": info.get("volume_max", 100.0),
            "volume_step": info.get("volume_step", 0.01),
            "tick_size": info.get("tick_size", 0.00001),
            "digits": info.get("digits", 5),
            "margin_initial": info.get("margin_initial", 0.0),
        }
        self._symbol_specs[symbol] = spec
        return dict(spec)

    def get_open_orders(self) -> list[dict[str, Any]]:
        future = asyncio.run_coroutine_threadsafe(self._client.get_orders(), self._loop)
        orders = future.result(timeout=self._timeout)
        result = []
        for o in (orders or []):
            result.append({
                "order_id": o.get("order_id"),
                "symbol": o.get("trade_symbol", ""),
                "type": o.get("trade_type"),
                "volume": o.get("trade_volume", 0),
                "price": o.get("price_order", 0.0),
                "sl": o.get("sl", 0.0),
                "tp": o.get("tp", 0.0),
                "state": o.get("order_state"),
                "comment": o.get("comment", ""),
            })
        return result

    # ---- Internal async methods ----

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _async_connect(self) -> None:
        from pymt5 import MT5WebClient

        client_kwargs: dict[str, Any] = {
            "auto_reconnect": self._auto_reconnect,
            "max_reconnect_attempts": self._max_reconnect_attempts,
        }
        if self._ws_uri:
            client_kwargs["uri"] = self._ws_uri
        if self._heartbeat_interval:
            client_kwargs["heartbeat_interval"] = self._heartbeat_interval
        if self._timeout:
            client_kwargs["timeout"] = self._timeout

        self._client = MT5WebClient(**client_kwargs)
        await self._client.connect()
        await self._client.login(login=self._login, password=self._password)
        await self._client.load_symbols()

        # Register push callbacks
        self._client.on_tick(self._on_tick_push)
        self._client.on_disconnect(self._on_ws_disconnect)
        try:
            self._client.on_trade_transaction(self._on_transaction_push)
        except Exception:
            pass
        try:
            self._client.on_trade_result(self._on_trade_result_push)
        except Exception:
            pass
        try:
            self._client.on_order_update(self._on_order_update_push)
        except Exception:
            pass
        try:
            self._client.on_position_update(self._on_position_update_push)
        except Exception:
            pass

    async def _async_subscribe(self, standard_symbols: list[str], resolved_symbols: list[str]) -> None:
        await self._client.subscribe_symbols(resolved_symbols)
        for std_sym, mt5_sym in zip(standard_symbols, resolved_symbols, strict=False):
            self._resolved_symbols[std_sym] = mt5_sym
            self._reverse_resolved_symbols[mt5_sym] = std_sym
            try:
                info = await self._client.get_full_symbol_info(mt5_sym)
                if info:
                    self._symbol_specs[std_sym] = {
                        "contract_size": info.get("contract_size", 100000),
                        "volume_min": info.get("volume_min", 0.01),
                        "volume_max": info.get("volume_max", 100.0),
                        "volume_step": info.get("volume_step", 0.01),
                        "tick_size": info.get("tick_size", 0.00001),
                        "digits": info.get("digits", 5),
                        "margin_initial": info.get("margin_initial", 0.0),
                    }
            except Exception as exc:
                logger.warning("failed to cache symbol info for %s: %s", std_sym, exc)

    async def _async_place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("data_name") or payload.get("symbol") or "")
        mt5_symbol = self._resolve_symbol(symbol)
        volume = float(payload.get("volume") or payload.get("size") or 0.01)
        volume = self._normalize_volume(symbol, volume)
        price = payload.get("price")
        if price is not None:
            price = float(price)
        side = str(payload.get("side") or "buy").lower()
        order_type = str(payload.get("order_type") or "market").lower()
        sl = float(payload.get("sl") or payload.get("stop_loss") or 0.0)
        tp = float(payload.get("tp") or payload.get("take_profit") or 0.0)
        deviation = int(payload.get("deviation") or 20)
        comment = str(payload.get("comment") or "")
        magic = int(payload.get("magic") or 0)
        filling_value = payload.get("filling")
        if filling_value is None:
            filling_value = _resolve_default_filling()
        filling = int(filling_value) if filling_value is not None else None
        request_kwargs = {
            "sl": sl,
            "tp": tp,
            "deviation": deviation,
            "comment": comment,
            "magic": magic,
        }
        if filling is not None:
            request_kwargs["filling"] = filling

        if order_type == "market":
            if side == "buy":
                result = await self._client.buy_market(
                    mt5_symbol,
                    volume,
                    **request_kwargs,
                )
            else:
                result = await self._client.sell_market(
                    mt5_symbol,
                    volume,
                    **request_kwargs,
                )
        elif order_type == "limit":
            if price is None:
                return {"status": "error", "error": "limit order requires price"}
            if side == "buy":
                result = await self._client.buy_limit(
                    mt5_symbol,
                    volume,
                    price,
                    **request_kwargs,
                )
            else:
                result = await self._client.sell_limit(
                    mt5_symbol,
                    volume,
                    price,
                    **request_kwargs,
                )
        elif order_type == "stop":
            if price is None:
                return {"status": "error", "error": "stop order requires price"}
            if side == "buy":
                result = await self._client.buy_stop(
                    mt5_symbol,
                    volume,
                    price,
                    **request_kwargs,
                )
            else:
                result = await self._client.sell_stop(
                    mt5_symbol,
                    volume,
                    price,
                    **request_kwargs,
                )
        elif order_type == "close":
            position_id = int(payload.get("position_id") or 0)
            result = await self._client.close_position(
                mt5_symbol,
                position_id,
                volume,
                **request_kwargs,
            )
        else:
            return {"status": "error", "error": f"unsupported order_type: {order_type}"}

        return self._trade_result_to_dict(result, symbol=symbol)

    # ---- Push callbacks (run in asyncio thread) ----

    def _on_tick_push(self, ticks: list[dict]) -> None:
        for tick in ticks:
            symbol_name = tick.get("symbol", "")
            if not symbol_name:
                sym_id = tick.get("symbol_id")
                if sym_id is not None and self._client:
                    info = self._client._symbols_by_id.get(sym_id)
                    if info:
                        symbol_name = info.name
            standard_symbol = self._to_standard_symbol(symbol_name)
            gateway_tick = GatewayTick(
                timestamp=tick.get("tick_time", 0),
                symbol=standard_symbol,
                exchange="MT5",
                asset_type="OTC",
                local_time=time.time(),
                bid_price=tick.get("bid", 0.0),
                ask_price=tick.get("ask", 0.0),
                price=((tick.get("bid") or 0.0) + (tick.get("ask") or 0.0)) / 2.0,
                volume=tick.get("tick_volume", 0.0),
                instrument_id=symbol_name,
            )
            self.emit(CHANNEL_MARKET, gateway_tick)

    def _on_order_update_push(self, orders: list[dict]) -> None:
        for o in orders:
            order_state = o.get("order_state")
            status = _MT5_ORDER_STATE_MAP.get(order_state, "submitted")
            self.emit(CHANNEL_EVENT, {
                "kind": "order",
                "exchange": "MT5",
                "status": status,
                "external_order_id": str(o.get("order_id") or o.get("trade_order") or ""),
                "order_ref": str(o.get("order_id") or o.get("trade_order") or ""),
                "data_name": o.get("trade_symbol", ""),
                "side": "buy" if o.get("order_type", 0) in (0, 2, 4) else "sell",
                "price": float(o.get("price_order") or 0.0),
                "size": float(o.get("volume_initial") or 0.0),
                "filled": float(
                    (o.get("volume_initial") or 0) - (o.get("volume_current") or 0)
                ),
                "remaining": float(o.get("volume_current") or 0.0),
            })

    def _on_position_update_push(self, positions: list[dict]) -> None:
        for p in positions:
            trade_action = p.get("trade_action", -1)
            side = "buy" if trade_action == 0 else "sell"
            self.emit(CHANNEL_EVENT, {
                "kind": "trade",
                "exchange": "MT5",
                "trade_id": str(p.get("position_id") or ""),
                "external_order_id": str(p.get("order_id") or ""),
                "order_ref": str(p.get("order_id") or ""),
                "data_name": p.get("trade_symbol", ""),
                "side": side,
                "size": abs(float(p.get("trade_volume") or 0)),
                "price": float(p.get("price_open") or 0.0),
                "commission": float(p.get("commission") or 0.0),
                "profit": float(p.get("profit") or 0.0),
                "position_id": p.get("position_id"),
            })

    def _on_trade_result_push(self, data: dict[str, Any]) -> None:
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            return
        retcode = int(result.get("retcode", -1))
        status = _RETCODE_STATUS.get(retcode, "unknown")
        self.emit(CHANNEL_EVENT, {
            "kind": "order",
            "exchange": "MT5",
            "status": status,
            "order_id": result.get("order"),
            "external_order_id": str(result.get("order") or ""),
            "deal": result.get("deal"),
            "price": float(result.get("price") or 0.0),
            "size": float(result.get("volume") or 0.0),
        })

    def _on_transaction_push(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        update_type = data.get("update_type")
        if update_type == 2:
            deals = data.get("deals")
            if isinstance(deals, dict):
                deals = [deals]
            elif deals is None and isinstance(data.get("deal"), dict):
                deals = [data["deal"]]
            for deal in deals or []:
                trade_action = deal.get("trade_action", -1)
                side = "buy" if trade_action == 0 else "sell"
                self.emit(CHANNEL_EVENT, {
                    "kind": "trade",
                    "exchange": "MT5",
                    "trade_id": str(deal.get("deal_id") or deal.get("deal") or ""),
                    "external_order_id": str(deal.get("trade_order") or deal.get("order_id") or ""),
                    "order_ref": str(deal.get("trade_order") or deal.get("order_id") or ""),
                    "data_name": deal.get("trade_symbol", ""),
                    "side": side,
                    "size": abs(float(deal.get("trade_volume") or 0.0)),
                    "price": float(deal.get("price_order") or deal.get("price_open") or 0.0),
                    "commission": float(deal.get("commission") or 0.0),
                    "profit": float(deal.get("profit") or 0.0),
                    "position_id": deal.get("position_id"),
                })
            return

        order = data.get("order")
        if order is None and isinstance(data.get("orders"), list) and data["orders"]:
            order = data["orders"][0]
        if not isinstance(order, dict):
            return
        order_state = order.get("order_state")
        status = _MT5_ORDER_STATE_MAP.get(order_state, "submitted")
        self.emit(CHANNEL_EVENT, {
            "kind": "order",
            "exchange": "MT5",
            "status": status,
            "external_order_id": str(order.get("trade_order") or order.get("order_id") or ""),
            "order_ref": str(order.get("trade_order") or order.get("order_id") or ""),
            "data_name": order.get("trade_symbol", ""),
            "side": "buy" if order.get("order_type", 0) in (0, 2, 4) else "sell",
            "price": float(order.get("price_order") or order.get("price_open") or 0.0),
            "size": float(order.get("volume_initial") or order.get("trade_volume") or 0.0),
            "filled": float(
                (order.get("volume_initial") or 0.0) - (order.get("volume_current") or 0.0)
            ),
            "remaining": float(order.get("volume_current") or 0.0),
        })

    def _on_ws_disconnect(self) -> None:
        self.emit(CHANNEL_EVENT, {
            "kind": "health",
            "exchange": "MT5",
            "type": "disconnected",
            "message": "MT5 WebSocket connection lost",
        })

    # ---- Helpers ----

    def _resolve_symbol(self, standard_symbol: str) -> str:
        cached = self._resolved_symbols.get(standard_symbol)
        if cached:
            return cached
        if self._symbol_map and standard_symbol in self._symbol_map:
            resolved = self._symbol_map[standard_symbol]
            self._resolved_symbols[standard_symbol] = resolved
            self._reverse_resolved_symbols[resolved] = standard_symbol
            return resolved
        if self._symbol_suffix:
            resolved = standard_symbol + self._symbol_suffix
            self._resolved_symbols[standard_symbol] = resolved
            self._reverse_resolved_symbols[resolved] = standard_symbol
            return resolved
        discovered = self._discover_symbol(standard_symbol)
        if discovered:
            self._resolved_symbols[standard_symbol] = discovered
            self._reverse_resolved_symbols[discovered] = standard_symbol
            return discovered
        return standard_symbol

    def _discover_symbol(self, standard_symbol: str) -> str | None:
        client = self._client
        if client is None:
            return None
        symbol_names = list(getattr(client, "symbol_names", []) or [])
        target = standard_symbol.upper()
        if standard_symbol in symbol_names:
            return standard_symbol
        exact = next((str(name) for name in symbol_names if str(name).upper() == target), None)
        if exact:
            return exact
        prefix_matches = [str(name) for name in symbol_names if str(name).upper().startswith(target)]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        return None

    def _to_standard_symbol(self, raw_symbol: str) -> str:
        if not raw_symbol:
            return raw_symbol
        mapped = self._reverse_resolved_symbols.get(raw_symbol)
        if mapped:
            return mapped
        raw_upper = raw_symbol.upper()
        prefix_matches = sorted(
            (sym for sym in self._subscribed_symbols if raw_upper.startswith(sym.upper())),
            key=len,
            reverse=True,
        )
        if prefix_matches:
            standard = prefix_matches[0]
            self._resolved_symbols.setdefault(standard, raw_symbol)
            self._reverse_resolved_symbols[raw_symbol] = standard
            return standard
        return raw_symbol

    def _normalize_volume(self, symbol: str, volume: float) -> float:
        spec = self._symbol_specs.get(symbol, {})
        step = spec.get("volume_step", 0.01)
        v_min = spec.get("volume_min", 0.01)
        v_max = spec.get("volume_max", 100.0)
        if step > 0:
            normalized = max(v_min, min(v_max, round(volume / step) * step))
        else:
            normalized = max(v_min, min(v_max, volume))
        return round(normalized, 8)

    @staticmethod
    def _trade_result_to_dict(result: Any, symbol: str = "") -> dict[str, Any]:
        retcode = getattr(result, "retcode", -1)
        return {
            "status": _RETCODE_STATUS.get(retcode, "unknown"),
            "retcode": retcode,
            "description": getattr(result, "description", ""),
            "success": getattr(result, "success", False),
            "order_id": getattr(result, "order", None),
            "external_order_id": getattr(result, "order", None),
            "deal": getattr(result, "deal", None),
            "volume": getattr(result, "volume", None),
            "price": getattr(result, "price", None),
            "bid": getattr(result, "bid", None),
            "ask": getattr(result, "ask", None),
            "comment": getattr(result, "comment", ""),
            "data_name": symbol,
        }
