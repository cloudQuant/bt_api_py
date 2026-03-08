"""
Korbit Spot Feed – three-layer sync / async wrappers + WSS stubs.
"""


from bt_api_py.containers.exchanges.korbit_exchange_data import KorbitExchangeDataSpot
from bt_api_py.feeds.live_korbit.request_base import KorbitRequestData
from bt_api_py.logging_factory import get_logger


class KorbitRequestDataSpot(KorbitRequestData):
    """Korbit Spot REST Feed."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)

    # ── market data ─────────────────────────────────────────────

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_tick(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        return self.get_tick(symbol, extra_data, **kwargs)

    async def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        return await self.async_get_tick(symbol, extra_data, **kwargs)

    def get_depth(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_depth(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_depth(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_depth(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_deals(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_deals(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_deals(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_deals(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_recent_trades(self, symbol, extra_data=None, **kwargs):
        return self.get_deals(symbol, extra_data, **kwargs)

    async def async_get_recent_trades(self, symbol, extra_data=None, **kwargs):
        return await self.async_get_deals(symbol, extra_data, **kwargs)

    def get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, extra = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, extra = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    # ── trading ─────────────────────────────────────────────────

    def make_order(self, symbol, side, order_type, amount, price=None, extra_data=None, **kwargs):
        path, body, extra = self._make_order(
            symbol, side, order_type, amount, price, extra_data, **kwargs
        )
        return self.request(path, body=body, extra_data=extra)

    async def async_make_order(
        self, symbol, side, order_type, amount, price=None, extra_data=None, **kwargs
    ):
        path, body, extra = self._make_order(
            symbol, side, order_type, amount, price, extra_data, **kwargs
        )
        return await self.async_request(path, body=body, extra_data=extra)

    def cancel_order(self, order_id, extra_data=None, **kwargs):
        path, body, extra = self._cancel_order(order_id, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra)

    async def async_cancel_order(self, order_id, extra_data=None, **kwargs):
        path, body, extra = self._cancel_order(order_id, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra = self._get_open_orders(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    # ── account ─────────────────────────────────────────────────

    def get_balance(self, extra_data=None, **kwargs):
        path, params, extra = self._get_balance(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_balance(self, extra_data=None, **kwargs):
        path, params, extra = self._get_balance(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_account(self, extra_data=None, **kwargs):
        path, params, extra = self._get_account(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_account(self, extra_data=None, **kwargs):
        path, params, extra = self._get_account(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)


# ── WebSocket stubs (kept for backward compatibility) ────────


class KorbitMarketWssDataSpot:
    """Korbit Spot Market WebSocket Data Handler (stub)."""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self._params = KorbitExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "korbit_spot_market_wss.log")
        self.request_logger = get_logger("korbit_spot_market_wss")
        self.wss_url = kwargs.get("wss_url", "wss://ws-api.korbit.co.kr/v2/public")
        self.topics = kwargs.get("topics", [])
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class KorbitAccountWssDataSpot:
    """Korbit Spot Account WebSocket Data Handler (stub)."""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self._params = KorbitExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "korbit_spot_account_wss.log")
        self.request_logger = get_logger("korbit_spot_account_wss")
        self.wss_url = kwargs.get("wss_url", "wss://ws-api.korbit.co.kr/v2/private")
        self.topics = kwargs.get("topics", [])
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
