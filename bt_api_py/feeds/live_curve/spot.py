"""
Curve Spot Feed implementation.

Provides market data for Curve DEX pools.
"""

from bt_api_py.containers.exchanges.curve_exchange_data import CurveExchangeDataSpot, CurveChain
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_curve.request_base import CurveRequestData
from bt_api_py.functions.utils import update_extra_data


class CurveRequestDataSpot(CurveRequestData):
    """Curve DEX Feed for pool data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "CURVE___DEX")

        # Get chain from kwargs or default to Ethereum
        chain = kwargs.get("chain", "ETHEREUM")
        if isinstance(chain, str):
            try:
                self.chain = CurveChain(chain)
            except ValueError:
                self.chain = CurveChain.ETHEREUM
        else:
            self.chain = chain

    def _get_pools(self, extra_data=None, **kwargs):
        """Get all pools on the configured chain. Returns (path, params, extra_data)."""
        path = self._params.get_rest_path("get_pools")
        extra_data = update_extra_data(
            extra_data,
            request_type="get_pools",
            symbol_name="",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_pools_normalize_function,
        )
        return path, {}, extra_data

    @staticmethod
    def _get_pools_normalize_function(input_data, extra_data):
        """Normalize pool data response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        pool_data = data.get("poolData", [])
        return [pool_data], len(pool_data) > 0

    def get_pools(self, extra_data=None, **kwargs):
        """Get pools from Curve."""
        path, params, extra_data = self._get_pools(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_volumes(self, extra_data=None, **kwargs):
        """Get trading volumes. Returns (path, params, extra_data)."""
        path = self._params.get_rest_path("get_volumes")
        extra_data = update_extra_data(
            extra_data,
            request_type="get_volumes",
            symbol_name="",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_volumes_normalize_function,
        )
        return path, {}, extra_data

    @staticmethod
    def _get_volumes_normalize_function(input_data, extra_data):
        """Normalize volume data response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        return [data], data is not None

    def get_volumes(self, extra_data=None, **kwargs):
        """Get volumes from Curve."""
        path, params, extra_data = self._get_volumes(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_tvl(self, extra_data=None, **kwargs):
        """Get TVL. Returns (path, params, extra_data)."""
        path = self._params.get_rest_path("get_tvl")
        extra_data = update_extra_data(
            extra_data,
            request_type="get_tvl",
            symbol_name="",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_tvl_normalize_function,
        )
        return path, {}, extra_data

    @staticmethod
    def _get_tvl_normalize_function(input_data, extra_data):
        """Normalize TVL data response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        return [data], data is not None

    def get_tvl(self, extra_data=None, **kwargs):
        """Get TVL from Curve."""
        path, params, extra_data = self._get_tvl(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_apys(self, extra_data=None, **kwargs):
        """Get APYs. Returns (path, params, extra_data)."""
        path = self._params.get_rest_path("get_apys")
        extra_data = update_extra_data(
            extra_data,
            request_type="get_apys",
            symbol_name="",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_apys_normalize_function,
        )
        return path, {}, extra_data

    @staticmethod
    def _get_apys_normalize_function(input_data, extra_data):
        """Normalize APY data response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        apy_data = data.get("poolApy", {})
        return [apy_data], apy_data is not None

    def get_apys(self, extra_data=None, **kwargs):
        """Get APYs from Curve."""
        path, params, extra_data = self._get_apys(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_pool_summary(self, pool_address, extra_data=None, **kwargs):
        """Get summary data for a specific pool. Returns (path, params, extra_data)."""
        chain_name = self._params.get_chain_name()
        path = f"GET /v1/getPool/{chain_name}/{pool_address}"
        extra_data = update_extra_data(
            extra_data,
            request_type="get_pool_summary",
            symbol_name=pool_address,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            pool_address=pool_address,
            normalize_function=self._get_pool_summary_normalize_function,
        )
        return path, {}, extra_data

    @staticmethod
    def _get_pool_summary_normalize_function(input_data, extra_data):
        """Normalize pool summary response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        pool_data = data.get("poolData", {})
        return [pool_data], bool(pool_data)

    def get_pool_summary(self, pool_address, extra_data=None, **kwargs):
        """Get pool summary from Curve."""
        path, params, extra_data = self._get_pool_summary(pool_address, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Exchange Info ====================

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info. Returns (path, params, extra_data)."""
        path = self._params.get_rest_path("get_pools")
        extra_data = update_extra_data(
            extra_data,
            request_type="get_exchange_info",
            symbol_name="",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_pools_normalize_function,
        )
        return path, {}, extra_data

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info from Curve."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Tick (Pool Price) ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get tick/pool data. Returns (path, params, extra_data)."""
        chain_name = self._params.get_chain_name()
        path = f"GET /v1/getPool/{chain_name}/{symbol}"
        extra_data = update_extra_data(
            extra_data,
            request_type="get_tick",
            symbol_name=symbol,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_tick_normalize_function,
        )
        return path, {}, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize tick response - uses pool summary format."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        pool_data = data.get("poolData", {})
        return [pool_data], bool(pool_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get tick data from Curve pool."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get tick data."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Depth (Pool Liquidity) ====================

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get depth/liquidity data. Returns (path, params, extra_data).

        Curve pools don't have order books. Returns TVL as proxy.
        """
        path = self._params.get_rest_path("get_tvl")
        extra_data = update_extra_data(
            extra_data,
            request_type="get_depth",
            symbol_name=symbol,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_depth_normalize_function,
        )
        return path, {}, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        return [data], data is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get liquidity depth."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Async get liquidity depth."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Kline ====================

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data. Returns (path, params, extra_data).

        Curve doesn't provide kline data directly.
        """
        path = self._params.get_rest_path("get_volumes")
        extra_data = update_extra_data(
            extra_data,
            request_type="get_kline",
            symbol_name=symbol,
            period=period,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_kline_normalize_function,
        )
        return path, {}, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response. Curve doesn't provide klines."""
        return [], True

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Async get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Standard Trading Interfaces ====================

    def _make_order(self, symbol, volume, price, order_type, offset="open",
                    post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Prepare swap order. Returns (path, body, extra_data).

        Curve is a DEX; trading requires on-chain transactions.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "make_order",
            "quantity": volume,
            "price": price,
            "order_type": order_type,
        })
        body = {
            "pool": symbol,
            "amount": str(volume),
        }
        return "POST /v1/swap", body, extra_data

    def make_order(self, symbol, volume, price, order_type, offset="open",
                   post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Place a swap order. Note: Curve requires on-chain tx."""
        path, body, extra_data = self._make_order(
            symbol, volume, price, order_type, offset, post_only,
            client_order_id, extra_data, **kwargs
        )
        return self.request(path, body=body, extra_data=extra_data)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "cancel_order",
            "order_id": order_id,
        })
        return f"DELETE /v1/orders/{order_id}", {}, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "query_order",
            "order_id": order_id,
        })
        return f"GET /v1/orders/{order_id}", {}, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_open_orders",
        })
        return "GET /v1/orders", {}, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Standard Account Interfaces ====================

    def _get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_account",
            "chain": self.chain.value if hasattr(self.chain, 'value') else str(self.chain),
        })
        return self._params.get_rest_path("get_pools"), {}, extra_data

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info."""
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_balance",
            "chain": self.chain.value if hasattr(self.chain, 'value') else str(self.chain),
        })
        return self._params.get_rest_path("get_pools"), {}, extra_data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get token balance. Note: DEX balance requires Web3/on-chain query."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
