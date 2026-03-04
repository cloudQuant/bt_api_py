"""
Gate.io Exchange Registration Module

Registers Gate.io Spot and Swap (Futures) feeds to the global ExchangeRegistry.
Import this module to complete the registration.
"""

from bt_api_py.balance_utils import nested_balance_handler as _gateio_balance_handler
from bt_api_py.containers.exchanges.gateio_exchange_data import (
    GateioExchangeDataSpot,
    GateioExchangeDataSwap,
)
from bt_api_py.feeds.live_gateio.spot import GateioRequestDataSpot
from bt_api_py.feeds.live_gateio.swap import GateioRequestDataSwap
from bt_api_py.registry import ExchangeRegistry


def _gateio_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Gate.io Spot subscription handler (placeholder for future WebSocket implementation)"""
    topic_list = [i["topic"] for i in topics]
    bt_api.log(f"Gate.io Spot topics requested: {topic_list}")


def _gateio_swap_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Gate.io Swap subscription handler (placeholder for future WebSocket implementation)"""
    topic_list = [i["topic"] for i in topics]
    bt_api.log(f"Gate.io Swap topics requested: {topic_list}")


def register_gateio():
    """Register Gate.io Spot and Swap to global ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("GATEIO___SPOT", GateioRequestDataSpot)
    ExchangeRegistry.register_exchange_data("GATEIO___SPOT", GateioExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("GATEIO___SPOT", _gateio_balance_handler)
    ExchangeRegistry.register_stream("GATEIO___SPOT", "subscribe", _gateio_spot_subscribe_handler)

    # Swap (Futures USDT-M)
    ExchangeRegistry.register_feed("GATEIO___SWAP", GateioRequestDataSwap)
    ExchangeRegistry.register_exchange_data("GATEIO___SWAP", GateioExchangeDataSwap)
    ExchangeRegistry.register_balance_handler("GATEIO___SWAP", _gateio_balance_handler)
    ExchangeRegistry.register_stream("GATEIO___SWAP", "subscribe", _gateio_swap_subscribe_handler)


# Auto-register on module import
register_gateio()
