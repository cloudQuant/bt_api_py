"""bt_api_py - Unified Multi-Exchange Trading API Framework.

This package provides a unified API for interacting with multiple cryptocurrency exchanges
 and traditional financial markets (CTP, Interactive Brokers).
"""

from bt_api_py.auth_config import (
    AuthConfig,
    CryptoAuthConfig,
    CtpAuthConfig,
    IbAuthConfig,
    IbWebAuthConfig,
)
from bt_api_py.balance_utils import nested_balance_handler, simple_balance_handler
from bt_api_py.bt_api import BtApi
from bt_api_py.event_bus import EventBus
from bt_api_py.exceptions import BtApiError, ExchangeNotFoundError
from bt_api_py.instrument_manager import InstrumentManager, get_instrument_manager
from bt_api_py.registry import ExchangeRegistry
