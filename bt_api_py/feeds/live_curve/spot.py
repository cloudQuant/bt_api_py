"""
Curve Spot Feed implementation.

Provides market data for Curve DEX pools.
"""

from bt_api_py.containers.exchanges.curve_exchange_data import CurveExchangeDataSpot, CurveChain
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_curve.request_base import CurveRequestData


class CurveRequestDataSpot(CurveRequestData):
    """Curve DEX Feed for pool data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_EXCHANGE_INFO,
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
        """Get all pools on the configured chain."""
        request_type = "get_pools"
        path = self._params.get_rest_path("get_pools")
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_pools_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

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
        return self._get_pools(extra_data, **kwargs)

    def _get_volumes(self, extra_data=None, **kwargs):
        """Get trading volumes for the configured chain."""
        request_type = "get_volumes"
        path = self._params.get_rest_path("get_volumes")
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_volumes_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_volumes_normalize_function(input_data, extra_data):
        """Normalize volume data response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        return [data], data is not None

    def get_volumes(self, extra_data=None, **kwargs):
        """Get volumes from Curve."""
        return self._get_volumes(extra_data, **kwargs)

    def _get_tvl(self, extra_data=None, **kwargs):
        """Get Total Value Locked (TVL) for the configured chain."""
        request_type = "get_tvl"
        path = self._params.get_rest_path("get_tvl")
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_tvl_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_tvl_normalize_function(input_data, extra_data):
        """Normalize TVL data response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        return [data], data is not None

    def get_tvl(self, extra_data=None, **kwargs):
        """Get TVL from Curve."""
        return self._get_tvl(extra_data, **kwargs)

    def _get_apys(self, extra_data=None, **kwargs):
        """Get APYs for pools on the configured chain."""
        request_type = "get_apys"
        path = self._params.get_rest_path("get_apys")
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_apys_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

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
        return self._get_apys(extra_data, **kwargs)

    def _get_pool_summary(self, pool_address, extra_data=None, **kwargs):
        """Get summary data for a specific pool."""
        request_type = "get_pool_summary"
        # Construct the pool summary path
        chain_name = self._params.get_chain_name()
        path = f"GET /v1/getPool/{chain_name}/{pool_address}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "pool_address": pool_address,
            "normalize_function": self._get_pool_summary_normalize_function,
        })
        return path, None, extra_data

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

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info - uses get_pools for Curve."""
        return self._get_pools(extra_data, **kwargs)

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info from Curve."""
        return self._get_exchange_info(extra_data, **kwargs)

    def _get_tick(self, pool_address, extra_data=None, **kwargs):
        """Get tick data for a pool - uses pool summary."""
        return self._get_pool_summary(pool_address, extra_data, **kwargs)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize tick response - uses pool summary format."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        pool_data = data.get("poolData", {})
        return [pool_data], bool(pool_data)

    def get_tick(self, pool_address, extra_data=None, **kwargs):
        """Get tick data from Curve pool."""
        path, params, extra_data = self._get_tick(pool_address, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
