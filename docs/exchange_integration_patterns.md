# Exchange Integration Patterns and Specifications

## Overview

This document outlines the standardized patterns and specifications used for exchange integrations in bt_api_py. It is based on the analysis of four implemented exchanges: **Binance**, **OKX**, **Interactive Brokers (IB)**, and **CTP**.

## Architecture Overview

The exchange integration follows a modular, registry-based architecture:

```
bt_api_py/
├── containers/           # Data containers for exchange responses
│   ├── exchanges/        # Exchange configuration classes
│   ├── tickers/          # Ticker data containers
│   ├── orderbooks/       # Order book data containers
│   ├── orders/           # Order data containers
│   ├── balances/         # Balance data containers
│   └── ...
├── feeds/                # Feed classes for REST/WebSocket APIs
│   ├── live_<exchange>/  # Exchange-specific feed implementations
│   │   ├── spot.py       # Spot trading feed
│   │   ├── swap.py       # Futures/perpetual feed
│   │   ├── request_base.py  # Base request class
│   │   └── ...
│   └── register_<exchange>.py  # Registration module
├── configs/              # YAML configuration files
│   └── <exchange>.yaml   # Exchange-specific configuration
├── registry.py           # Global exchange registry
└── config_loader.py      # YAML configuration loader
```

## 1. Naming Conventions

### Exchange Name Format
- ***Registry Key**: `<EXCHANGE>___<ASSET_TYPE>` (e.g., `BINANCE___SPOT`, `OKX___SWAP`)
- ***Directory**: `live_<exchange_lowercase>` (e.g., `live_binance`, `live_okx`)
- ***Class Names**: `<Exchange><Request/Wss><DataType>` (e.g., `BinanceRequestTickerData`, `OkxWssOrderData`)
- ***Config File**: `<exchange>.yaml` (e.g., `binance.yaml`, `okx.yaml`)

### Asset Types
Common asset types:
- `spot` - Spot trading
- `swap` - USDT-M perpetual futures
- `futures` - Delivery futures
- `coin_m` - Coin-margined futures
- `option` - Options trading
- `margin` - Margin trading

## 2. Exchange Configuration Class Pattern

### Location
`bt_api_py/containers/exchanges/<exchange>_exchange_data.py`

### Structure

```python
import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="<exchange>_exchange_data.log",
    logger_name="<exchange>_data",
    print_info=False
).create_logger()

# Module-level config cache (for lazy loading)
_<exchange>_config = None
_<exchange>_config_loaded = False


def _get_<exchange>_config():
    """Load and cache YAML configuration."""
    global _<exchange>_config, _<exchange>_config_loaded
    if _<exchange>_config_loaded:
        return _<exchange>_config
    _<exchange>_config_loaded = True
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "<exchange>.yaml",
        )
        if os.path.exists(config_path):
            _<exchange>_config = load_exchange_config(config_path)
    except Exception as e:
        logger.warn(f"Failed to load <exchange>.yaml config: {e}")
    return _<exchange>_config


class <Exchange>ExchangeData(ExchangeData):
    """Base class for <Exchange> exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "<exchange>"
        self.rest_url = "https://api.example.com"
        self.wss_url = "wss://api.example.com/ws"
        self.acct_wss_url = ""  # Account WebSocket URL

        self.kline_periods = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = ["USDT", "USD", "BTC", "ETH"]
        self.rest_paths = {}
        self.wss_paths = {}

    def _load_from_config(self, asset_type):
        """Load configuration from YAML.

        Args:
            asset_type: Asset type key (e.g., 'spot', 'swap')
        Returns:
            bool: Success status
        """
        config = _get_<exchange>_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        # Load exchange_name
        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # Load URLs (handle both dict and string formats)
        if config.base_urls:
            if isinstance(config.base_urls.rest, dict):
                self.rest_url = config.base_urls.rest.get(
                    asset_type,
                    config.base_urls.rest.get('default', self.rest_url)
                )
            else:
                self.rest_url = config.base_urls.rest

            if isinstance(config.base_urls.wss, dict):
                self.wss_url = config.base_urls.wss.get(
                    asset_type,
                    config.base_urls.wss.get('default', self.wss_url)
                )
            else:
                self.wss_url = config.base_urls.wss

        # Load rest_paths
        if asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        # Load wss_paths (convert templates to proper format)
        if asset_cfg.wss_paths:
            converted = {}
            for key, value in asset_cfg.wss_paths.items():
                if isinstance(value, str):
                    if value:
                        converted[key] = {"params": [value], "method": "SUBSCRIBE", "id": 1}
                    else:
                        converted[key] = ""
                else:
                    converted[key] = value
            self.wss_paths = converted

        # Load kline_periods (asset-level priority)
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # Load legal_currency (asset-level priority)
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_symbol(self, symbol):
        """Convert standard symbol format to exchange format.

        Override in subclasses for exchange-specific formatting.
        """
        return symbol

    def get_period(self, key):
        """Get exchange-specific kline period format."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key):
        """Get REST API path for given endpoint.

        Args:
            key: Endpoint key (e.g., 'get_tick', 'make_order')
        Returns:
            str: API path (e.g., 'GET /api/v1/ticker')
        """
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    def get_wss_path(self, **kwargs):
        """Get WebSocket subscription path.

        Args:
            **kwargs: Including 'topic', 'symbol', 'period', etc.
        Returns:
            str: JSON-formatted subscription message
        """
        key = kwargs["topic"]
        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)

        # Substitute template parameters
        req = self.wss_paths[key].copy()
        # Implementation varies by exchange...
        return json.dumps(req)

    def account_wss_symbol(self, symbol):
        """Convert symbol for account WebSocket."""
        return symbol


class <Exchange>ExchangeDataSpot(<Exchange>ExchangeData):
    """Spot trading configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self._load_from_config("spot")

    # Override methods if needed for spot-specific behavior
    def get_symbol(self, symbol):
        return symbol.replace("-", "").upper()


class <Exchange>ExchangeDataSwap(<Exchange>ExchangeData):
    """Perpetual futures configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "swap"
        self._load_from_config("swap")
```

### Key Points

1. ***Lazy Config Loading**: Use module-level cache and only load when needed
2. ***Asset Type Priority**: Asset-level config overrides exchange-level config
3. ***Flexible URL Handling**: Support both dict and string formats for base_urls
4. ***Template Substitution**: wss_paths use `<symbol>`, `<period>` templates
5. ***Override Hooks**: Subclasses override `get_symbol()`, `get_wss_path()` etc.

## 3. YAML Configuration Pattern

### Location
`bt_api_py/configs/<exchange>.yaml`

### Structure

```yaml
# <Exchange> Exchange Configuration
id: <exchange>
display_name: <Exchange Name>
venue_type: cex  # cex, dex, broker
website: https://example.com
api_doc: https://docs.example.com

base_urls:
  rest:
    spot: https://api.example.com
    swap: https://fapi.example.com
    default: https://api.example.com
  wss:
    spot: wss://api.example.com/ws
    swap: wss://fapi.example.com/ws
    public: wss://api.example.com/ws/public
    private: wss://api.example.com/ws/private
    default: wss://api.example.com/ws
  acct_wss:
    spot: wss://api.example.com/ws/account
    swap: wss://fapi.example.com/ws/account
    default: wss://api.example.com/ws/account

connection:
  type: http  # http, websocket, spi, tws
  timeout: 10
  max_retries: 3

authentication:
  type: hmac_sha256  # none, api_key, hmac_sha256, hmac_sha512
  header_name: X-API-KEY
  timestamp_key: timestamp
  signature_key: signature

# Exchange-level kline periods (shared default)
kline_periods:
  1m: "1m"
  5m: "5m"
  15m: "15m"
  30m: "30m"
  1h: "1h"
  4h: "4h"
  1d: "1d"

# Exchange-level legal currencies
legal_currency: [USDT, USD, BTC, ETH]

# Rate limit rules
rate_limits:
  - name: request_weight
    type: sliding_window  # sliding_window, fixed_window, token_bucket
    interval: 60
    limit: 2400
    scope: global  # global, endpoint, ip
    weight: 1
    weight_map:
      "/api/v1/depth": 5

# Order status mapping (optional)
status_dict:
  live: new
  partially_filled: partially_filled
  filled: filled
  canceled: canceled

# Asset Types
asset_types:
  spot:
    exchange_name: <EXCHANGE>_SPOT
    symbol_format: "{base}{quote}"
    legal_currency: [USDT, USD, BTC, ETH]
    rest_paths:
      ping: "GET /api/v1/ping"
      get_server_time: "GET /api/v1/time"
      get_tick: "GET /api/v1/ticker"
      get_depth: "GET /api/v1/depth"
      get_kline: "GET /api/v1/klines"
      make_order: "POST /api/v1/order"
      cancel_order: "DELETE /api/v1/order"
      query_order: "GET /api/v1/order"
      get_open_orders: "GET /api/v1/openOrders"
      get_balance: "GET /api/v1/account"
      get_account: "GET /api/v1/account"
    wss_paths:
      tick: "<symbol>@ticker"
      depth: "<symbol>@depth20"
      kline: "<symbol>@kline_<period>"
      orders: ""
      account: ""

  swap:
    exchange_name: <EXCHANGE>_SWAP
    symbol_format: "{base}{quote}"
    rest_paths:
      # ... same structure as spot
    wss_paths:
      # ... WebSocket subscription templates
```

### Important Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique exchange identifier |
| `venue_type` | Yes | `cex`, `dex`, or `broker` |
| `base_urls` | Yes (CEX) | REST and WebSocket URLs |
| `connection.type` | Yes | Connection method |
| `rate_limits[].type` | Yes | Must be `sliding_window`, `fixed_window`, or `token_bucket` |

## 4. Feed Class Pattern

### Location
`bt_api_py/feeds/live_<exchange>/request_base.py` (base class)
`bt_api_py/feeds/live_<exchange>/spot.py` (spot implementation)

### Base Request Class Structure

```python
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.capability import Capability
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class <Exchange>RequestData(Feed):
    """Base REST API request handler for <Exchange>."""

    @classmethod
    def _capabilities(cls):
        """Declare supported capabilities."""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key")
        self.private_key = kwargs.get("private_key")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "<exchange>_feed.log")

        # Exchange configuration
        self._params = kwargs.get("exchange_data", <Exchange>ExchangeDataSpot())

        # Loggers
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        # Rate limiter
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())

    @staticmethod
    def _create_default_rate_limiter():
        """Create default rate limiter from config."""
        rules = [
            RateLimitRule(
                name="<exchange>_request_weight",
                limit=2400,
                interval=60,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.GLOBAL,
            ),
        ]
        return RateLimiter(rules)

    # ============== Private API Methods (underscore prefix) ==============
    # These methods return (path, params, extra_data) tuples

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data endpoint.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_tick"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": <Exchange>RequestData._get_tick_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker response.

        Returns:
            tuple: (data_list, status)
        """
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [<Exchange>RequestTickerData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [<Exchange>RequestTickerData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, input_data is not None

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book endpoint.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol, "limit": count}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": <Exchange>RequestData._get_depth_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize order book response."""
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        data = [<Exchange>RequestOrderBookData(input_data, symbol_name, asset_type, True)]
        return data, input_data is not None

    def _get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        """Get kline data endpoint.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_period = self._params.get_period(period)
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": request_symbol,
            "interval": request_period,
            "limit": count,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": <Exchange>RequestData._get_kline_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response."""
        # Implementation...
        pass

    def _make_order(self, symbol, vol, price=None, order_type="buy-limit",
                    offset="open", extra_data=None, **kwargs):
        """Create order endpoint.

        Args:
            symbol: Trading pair symbol
            vol: Order volume
            price: Order price (None for market orders)
            order_type: Order type (buy-limit, sell-limit, buy-market, etc.)
            offset: Order side (open, close)
        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        # Parse order_type
        side, order_type = order_type.split("-")
        params = {
            "symbol": request_symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": vol,
        }
        if price and order_type == "limit":
            params["price"] = price

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": <Exchange>RequestData._make_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Normalize order response."""
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [<Exchange>RequestOrderData(i, symbol_name, asset_type, True)
                    for i in input_data]
        else:
            data = [<Exchange>RequestOrderData(input_data, symbol_name, asset_type, True)]
        return data, input_data is not None

    def _cancel_order(self, symbol, order_id=None, client_order_id=None,
                      extra_data=None, **kwargs):
        """Cancel order endpoint.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["clientOrderId"] = client_order_id

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": <Exchange>RequestData._cancel_order_normalize_function,
            },
        )
        return path, params, extra_data

    def _get_balance(self, extra_data=None, **kwargs):
        """Get account balance endpoint.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": <Exchange>RequestData._get_balance_normalize_function,
            },
        )
        return path, params, extra_data

    # ============== Public API Methods ==============

    def get_tick(self, symbol):
        """Get ticker data.

        Returns:
            RequestData: Ticker data container
        """
        path, params, extra_data = self._get_tick(symbol)
        return self.request(path, params=params, extra_data=extra_data)

    def get_depth(self, symbol, count=20):
        """Get order book.

        Returns:
            RequestData: Order book data container
        """
        path, params, extra_data = self._get_depth(symbol, count)
        return self.request(path, params=params, extra_data=extra_data)

    def get_kline(self, symbol, period="1h", count=100):
        """Get kline data.

        Returns:
            RequestData: Kline data container
        """
        path, params, extra_data = self._get_kline(symbol, period, count)
        return self.request(path, params=params, extra_data=extra_data)

    def make_order(self, symbol, vol, price=None, order_type="buy-limit",
                   offset="open", client_order_id=None):
        """Place an order.

        Returns:
            RequestData: Order data container
        """
        path, params, extra_data = self._make_order(
            symbol, vol, price, order_type, offset
        )
        if client_order_id:
            params["clientOrderId"] = client_order_id
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def cancel_order(self, symbol, order_id=None, client_order_id=None):
        """Cancel an order.

        Returns:
            RequestData: Order data container
        """
        path, params, extra_data = self._cancel_order(
            symbol, order_id, client_order_id
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)
```

### Key Points

1. ***Private Methods**: Use `_` prefix for internal methods that return `(path, params, extra_data)`
2. ***Public Methods**: Call private methods and invoke `self.request()`
3. ***Normalization**: Each endpoint has a normalize function to convert response to data containers
4. ***Rate Limiting**: Use `_rate_limiter` decorator or check before requests
5. ***Authentication**: Set `is_sign=True` for endpoints requiring signatures

## 5. Data Container Pattern

### Base Container Classes

Location: `bt_api_py/containers/<datatype>/<datatype>.py`

```python
# Ticker base
from bt_api_py.containers.tickers.ticker import TickerData

# OrderBook base
from bt_api_py.containers.orderbooks.orderbook import OrderBookData

# Order base
from bt_api_py.containers.orders.order import OrderData

# Balance base
from bt_api_py.containers.balances.balance import BalanceData
```

### Ticker Container Example

```python
import json
import time
from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class <Exchange>RequestTickerData(TickerData):
    """Ticker data from REST API."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "<EXCHANGE>"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.server_time = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.last_price = None
        self.last_volume = None
        self.high_24h = None
        self.low_24h = None
        self.volume_24h = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse ticker data from exchange response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse fields according to exchange API format
        self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "symbol")
        self.server_time = from_dict_get_float(self.ticker_data, "time")
        self.bid_price = from_dict_get_float(self.ticker_data, "bidPrice")
        self.ask_price = from_dict_get_float(self.ticker_data, "askPrice")
        self.last_price = from_dict_get_float(self.ticker_data, "lastPrice")
        self.high_24h = from_dict_get_float(self.ticker_data, "high24h")
        self.low_24h = from_dict_get_float(self.ticker_data, "low24h")

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        """Get all data as dictionary."""
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "ticker_symbol_name": self.ticker_symbol_name,
                "server_time": self.server_time,
                "bid_price": self.bid_price,
                "ask_price": self.ask_price,
                "last_price": self.last_price,
                "high_24h": self.high_24h,
                "low_24h": self.low_24h,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    # Standard getter methods
    def get_exchange_name(self):
        return self.exchange_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_last_price(self):
        self.init_data()
        return self.last_price

    def get_bid_price(self):
        self.init_data()
        return self.bid_price

    def get_ask_price(self):
        self.init_data()
        return self.ask_price

    def get_high_24h(self):
        self.init_data()
        return self.high_24h

    def get_low_24h(self):
        self.init_data()
        return self.low_24h
```

### Constructor Parameters

All data containers follow this signature:

```python
def __init__(self, data, symbol_name, asset_type, has_been_json_encoded=False):
    """
    Args:
        data: Raw response data (dict or JSON string)
        symbol_name: Standard symbol format (e.g., "BTC-USDT")
        asset_type: Asset type (e.g., "SPOT", "SWAP")
        has_been_json_encoded: True if data is already a dict
    """
```

## 6. Registration Pattern

### Location
`bt_api_py/feeds/register_<exchange>.py`

### Structure

```python
"""
<Exchange> Registration Module

Registers <Exchange> feeds to the global ExchangeRegistry.
Import this module to complete the registration.
"""

from bt_api_py.balance_utils import simple_balance_handler as _<exchange>_balance_handler
from bt_api_py.containers.exchanges.<exchange>_exchange_data import (
    <Exchange>ExchangeDataSpot,
    <Exchange>ExchangeDataSwap,
)
from bt_api_py.feeds.live_<exchange>.spot import <Exchange>RequestDataSpot
from bt_api_py.feeds.live_<exchange>.swap import <Exchange>RequestDataSwap
from bt_api_py.registry import ExchangeRegistry


def _<exchange>_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """<Exchange> subscription handler for WebSocket streams.

    Args:
        data_queue: queue.Queue
        exchange_params: dict with connection parameters
        topics: list of topic dicts
        bt_api: BtApi instance for shared state
    """
    exchange_data = <Exchange>ExchangeDataSpot()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "<exchange>_market_data"
    kwargs["wss_url"] = exchange_data.wss_url
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics

    # Start market WebSocket
    <Exchange>MarketWssDataSpot(data_queue, **kwargs).start()

    # Start account WebSocket (if not already started)
    if not bt_api._subscription_flags.get("<EXCHANGE>___SPOT_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
        ]
        <Exchange>AccountWssDataSpot(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["<EXCHANGE>___SPOT_account"] = True


def register_<exchange>():
    """Register <Exchange> to global ExchangeRegistry."""
    # Spot
    ExchangeRegistry.register_feed("<EXCHANGE>___SPOT", <Exchange>RequestDataSpot)
    ExchangeRegistry.register_exchange_data("<EXCHANGE>___SPOT", <Exchange>ExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("<EXCHANGE>___SPOT", _<exchange>_balance_handler)
    ExchangeRegistry.register_stream("<EXCHANGE>___SPOT", "subscribe", _<exchange>_subscribe_handler)

    # Swap (if applicable)
    ExchangeRegistry.register_feed("<EXCHANGE>___SWAP", <Exchange>RequestDataSwap)
    ExchangeRegistry.register_exchange_data("<EXCHANGE>___SWAP", <Exchange>ExchangeDataSwap)
    ExchangeRegistry.register_balance_handler("<EXCHANGE>___SWAP", _<exchange>_balance_handler)
    ExchangeRegistry.register_stream("<EXCHANGE>___SWAP", "subscribe", _<exchange>_subscribe_handler)


# Auto-register on module import
register_<exchange>()
```

## 7. Testing Pattern

### Location
`tests/feeds/test_<exchange>.py`

### Structure

```python
"""
Test <Exchange> exchange integration.

Run tests:
    pytest tests/feeds/test_<exchange>.py -v

Run with coverage:
    pytest tests/feeds/test_<exchange>.py --cov=bt_api_py.feeds.live_<exchange> --cov-report=term-missing
"""

import queue
import pytest

# Import registration to auto-register <Exchange>
import bt_api_py.feeds.register_<exchange>  # noqa: F401

from bt_api_py.containers.exchanges.<exchange>_exchange_data import (
    <Exchange>ExchangeDataSpot,
)
from bt_api_py.feeds.live_<exchange>.spot import <Exchange>RequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def init_req_feed():
    """Initialize request feed for testing."""
    data_queue = queue.Queue()
    kwargs = {
        "public_key": "test_key",
        "private_key": "test_secret",
    }
    return <Exchange>RequestDataSpot(data_queue, **kwargs)


class Test<Exchange>ExchangeData:
    """Test <Exchange> exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating <Exchange> spot exchange data."""
        exchange_data = <Exchange>ExchangeDataSpot()
        assert exchange_data.exchange_name in ["<exchange>", "<exchange>_spot"]
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url.startswith("https://")
        assert len(exchange_data.rest_paths) > 0

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = <Exchange>ExchangeDataSpot()
        assert exchange_data.get_symbol("BTC-USDT") == "BTCUSDT"
        assert exchange_data.get_symbol("ETH-USDT") == "ETHUSDT"

    def test_kline_periods(self):
        """Test kline periods are defined."""
        exchange_data = <Exchange>ExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        """Test legal currencies are defined."""
        exchange_data = <Exchange>ExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency


class Test<Exchange>Registration:
    """Test <Exchange> registration."""

    def test_<exchange>_registered(self):
        """Test that <Exchange> is properly registered."""
        assert "<EXCHANGE>___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["<EXCHANGE>___SPOT"] == <Exchange>RequestDataSpot
        assert "<EXCHANGE>___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["<EXCHANGE>___SPOT"] == <Exchange>ExchangeDataSpot

    def test_<exchange>_create_feed(self):
        """Test creating <Exchange> feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("<EXCHANGE>___SPOT", data_queue)
        assert isinstance(feed, <Exchange>RequestDataSpot)

    def test_<exchange>_create_exchange_data(self):
        """Test creating <Exchange> exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("<EXCHANGE>___SPOT")
        assert isinstance(exchange_data, <Exchange>ExchangeDataSpot)


class Test<Exchange>ServerTime:
    """Test server time endpoint."""

    @pytest.mark.skip(reason="Requires network access")
    def test_get_server_time(self):
        """Test getting server time."""
        feed = init_req_feed()
        result = feed.get_server_time()
        assert result is not None
        assert result.status is True


class Test<Exchange>Ticker:
    """Test ticker data retrieval."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_data = {
            "symbol": "BTCUSDT",
            "bidPrice": "49990",
            "askPrice": "50010",
            "lastPrice": "50000",
            "high24h": "51000",
            "low24h": "49000",
        }

        from bt_api_py.containers.tickers.<exchange>_ticker import <Exchange>RequestTickerData

        ticker = <Exchange>RequestTickerData(
            ticker_data, "BTC-USDT", "SPOT", True
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "<EXCHANGE>"
        assert ticker.get_symbol_name() == "BTC-USDT"
        assert ticker.get_last_price() == 50000.0
        assert ticker.get_bid_price() == 49990.0
        assert ticker.get_ask_price() == 50010.0

    @pytest.mark.skip(reason="Requires network access")
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        feed = init_req_feed()
        result = feed.get_tick("BTC-USDT")
        assert result.status is True


class Test<Exchange>OrderBook:
    """Test order book data retrieval."""

    def test_orderbook_container(self):
        """Test orderbook data container."""
        orderbook_data = {
            "lastUpdateId": 123456789,
            "bids": [
                ["49990", "1.5"],
                ["49980", "2.0"]
            ],
            "asks": [
                ["50010", "1.0"],
                ["50020", "2.5"]
            ],
        }

        from bt_api_py.containers.orderbooks.<exchange>_orderbook import <Exchange>RequestOrderBookData

        orderbook = <Exchange>RequestOrderBookData(
            orderbook_data, "BTC-USDT", "SPOT"
        )
        orderbook.init_data()

        assert orderbook.symbol_name == "BTC-USDT"
        assert orderbook.exchange_name == "<EXCHANGE>"

    @pytest.mark.skip(reason="Requires network access")
    def test_get_depth_live(self):
        """Test getting order book from live API."""
        feed = init_req_feed()
        result = feed.get_depth("BTC-USDT", count=20)
        assert result.status is True


class Test<Exchange>Order:
    """Test order placement and management."""

    def test_order_container(self):
        """Test order data container."""
        order_data = {
            "orderId": "123456",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "LIMIT",
            "price": "50000",
            "origQty": "0.001",
            "status": "FILLED",
        }

        from bt_api_py.containers.orders.<exchange>_order import <Exchange>RequestOrderData

        order = <Exchange>RequestOrderData(
            order_data, "BTC-USDT", "SPOT", True
        )
        order.init_data()

        assert order.symbol_name == "BTC-USDT"
        assert order.exchange_name == "<EXCHANGE>"


class Test<Exchange>Balance:
    """Test balance data retrieval."""

    def test_balance_container(self):
        """Test balance data container."""
        balance_data = {
            "asset": "BTC",
            "free": "0.5",
            "locked": "0.1",
        }

        from bt_api_py.containers.balances.<exchange>_balance import <Exchange>RequestBalanceData

        balance = <Exchange>RequestBalanceData(
            balance_data, "SPOT", False
        )
        balance.init_data()

        assert balance.get_exchange_name() == "<EXCHANGE>"
        assert balance.get_currency() == "BTC"
        assert balance.get_available() == 0.5


class Test<Exchange>Capabilities:
    """Test <Exchange> feed capabilities."""

    def test_feed_capabilities(self):
        """Test that feed has expected capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = <Exchange>RequestDataSpot._capabilities()

        assert Capability.GET_TICK in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities


class Test<Exchange>Integration:
    """Integration tests for <Exchange>."""

    @pytest.mark.skip(reason="Requires network access")
    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = <Exchange>RequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("BTC-USDT")
        assert ticker.status is True

        # Test depth
        depth = feed.get_depth("BTC-USDT", count=20)
        assert depth.status is True

        # Test kline
        kline = feed.get_kline("BTC-USDT", "1h", count=10)
        assert kline.status is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

### Test Categories

1. ***Configuration Tests**: Verify exchange data class loads correctly
2. ***Registration Tests**: Verify registry registration works
3. ***Container Tests**: Verify data containers parse responses correctly
4. ***Capability Tests**: Verify declared capabilities
5. ***Integration Tests**: Live API tests (marked with `@pytest.mark.skip`)

## 8. Special Exchange Types

### CTP (Futures Market) Pattern

CTP uses SPI callback instead of REST/WebSocket:

```python
class CtpExchangeData(ExchangeData):
    def __init__(self):
        super().__init__()
        self.exchange_name = "CTP"
        self.rest_url = ""  # No REST API
        self.wss_url = ""  # No WebSocket
        self.md_front = ""  # Market data front address
        self.td_front = ""  # Trading front address

    def get_rest_path(self, key):
        raise NotImplementedError("CTP does not use REST API")

    def get_wss_path(self, **kwargs):
        raise NotImplementedError("CTP does not use WebSocket")
```

### IB (Broker) Pattern

IB uses different APIs for different data types:

```python
class IbWebExchangeData(ExchangeData):
    PROD_REST_URL = "https://api.interactivebrokers.com"
    TEST_REST_URL = "https://api.test.interactivebrokers.com"
    GATEWAY_REST_URL = "https://localhost:5000"

    def __init__(self):
        # ... loads entirely from YAML config
        self.sec_type_map = {}       # Security type mapping
        self.market_data_fields = {}  # Market data field definitions
        self.order_type_map = {}     # Order type mapping
        self.tif_map = {}            # Time in force mapping
```

### DEX Pattern

For DEX (Uniswap, PancakeSwap, etc.):

```yaml
id: uniswap
display_name: Uniswap
venue_type: dex
website: https://uniswap.org

# DEX uses RPC endpoints instead of REST
connection:
  type: rpc
  timeout: 30

# DEX-specific fields
chains: [ethereum, polygon, arbitrum]
router_address: "0xE592427A0AEce92De3Edee1F18E0157C05861564"
factory_address: "0x1F98431c8aD98523631AE4a59f267346ea31F984"
```

## 9. Error Handling Pattern

### Error Translator

Location: `bt_api_py/errors/<exchange>_error_translator.py`

```python
from bt_api_py.error_framework import ErrorTranslator, UnifiedError


class <Exchange>ErrorTranslator(ErrorTranslator):
    """Translate <Exchange> API errors to UnifiedError."""

    def translate(self, response, exchange_name="<EXCHANGE>") -> UnifiedError | None:
        """Translate error response to UnifiedError.

        Args:
            response: Raw error response from exchange
            exchange_name: Exchange name
        Returns:
            UnifiedError or None if no error
        """
        if not isinstance(response, dict):
            return None

        # Check for error code
        code = response.get("code")
        if code is not None and int(code) < 0:
            error_code = int(code)
            message = response.get("msg", "Unknown error")

            # Map to standard error types
            if error_code == -1001:
                return UnifiedError(
                    exchange_name=exchange_name,
                    error_type=UnifiedError.DISCONNECTED,
                    message=message,
                    raw_code=error_code,
                )
            elif error_code == -1021:
                return UnifiedError(
                    exchange_name=exchange_name,
                    error_type=UnifiedError.INVALID_TIMESTAMP,
                    message=message,
                    raw_code=error_code,
                )
            # ... more mappings

        return None
```

### Usage in Feed Class

```python
def __init__(self, data_queue, **kwargs):
    # ...
    self._error_translator = <Exchange>ErrorTranslator()

def translate_error(self, raw_response):
    """Translate raw response to UnifiedError."""
    return self._error_translator.translate(raw_response, self.exchange_name)
```

## 10. Rate Limiting Pattern

### Default Rate Limiter

```python
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType

@staticmethod
def _create_default_rate_limiter():
    """Create default rate limiter from config."""
    rules = [
        RateLimitRule(
            name="request_weight",
            limit=2400,
            interval=60,
            type=RateLimitType.SLIDING_WINDOW,
            scope=RateLimitScope.GLOBAL,
        ),
        RateLimitRule(
            name="order_rate",
            limit=100,
            interval=10,
            type=RateLimitType.SLIDING_WINDOW,
            scope=RateLimitScope.ENDPOINT,
            endpoint="/api/v1/order*",
        ),
    ]
    return RateLimiter(rules)
```

### Using the Rate Limiter

```python
from bt_api_py.rate_limiter import rate_limit

@rate_limit
def request(self, path, params=None, extra_data=None, is_sign=False):
    """Send HTTP request with rate limiting."""
    # Implementation...
```

## 11. Common Pitfalls to Avoid

1. ***Missing `asset_type` attribute**: Always set `self.asset_type` in subclass `__init__`
2. ***Incorrect logger format**: spdlog doesn't support `%s` formatting, use f-strings
3. ***Missing `type` field in rate_limits**: All rate_limits entries must have a `type` field
4. ***Hardcoded URLs**: Always load from YAML config
5. ***Incorrect constructor signature**: Data containers must use `(data, symbol_name, asset_type, has_been_json_encoded)`
6. ***Forgetting registration**: Always create `register_<exchange>.py` module
7. ***Mocking config in tests**: Avoid module-level mocking that affects other tests

## 12. Checklist for New Exchange Integration

### Configuration
- [ ] Create `<exchange>.yaml` in `bt_api_py/configs/`
- [ ] Include all required fields (id, venue_type, base_urls, connection)
- [ ] Add `type` field to all rate_limits entries
- [ ] Define rest_paths for all endpoints
- [ ] Define wss_paths for WebSocket subscriptions

### Exchange Data Class
- [ ] Create `<exchange>_exchange_data.py`
- [ ] Implement `_load_from_config()` method
- [ ] Implement `get_symbol()` for symbol conversion
- [ ] Implement `get_period()` for kline period conversion
- [ ] Create subclasses for each asset type (Spot, Swap, etc.)
- [ ] Set `self.asset_type` in each subclass

### Feed Classes
- [ ] Create `live_<exchange>` directory
- [ ] Create `request_base.py` with base request class
- [ ] Create `spot.py` with spot-specific implementation
- [ ] Implement `_` methods for all endpoints
- [ ] Implement normalize functions for each endpoint
- [ ] Implement public API methods

### Data Containers
- [ ] Create ticker container in `containers/tickers/`
- [ ] Create orderbook container in `containers/orderbooks/`
- [ ] Create order container in `containers/orders/`
- [ ] Create balance container in `containers/balances/`
- [ ] Ensure `init_data()` parses all fields
- [ ] Implement getter methods

### Registration
- [ ] Create `register_<exchange>.py`
- [ ] Import and register all asset types
- [ ] Register balance handler
- [ ] Register subscribe handler (if WebSocket)

### Tests
- [ ] Create `test_<exchange>.py`
- [ ] Test exchange data creation
- [ ] Test registration
- [ ] Test data containers
- [ ] Test capabilities
- [ ] Add integration tests (skipped by default)

### Error Handling
- [ ] Create error translator
- [ ] Map common error codes to UnifiedError types
- [ ] Use translator in feed class

## Summary

This pattern document provides a comprehensive guide for implementing new exchanges in bt_api_py. By following these patterns:

1. ***Consistency**: All exchanges follow the same structure
2. ***Pluggability**: New exchanges can be added without modifying core code
3. ***Configurability**: All settings are externalized to YAML
4. ***Testability**: Standardized test structure for all exchanges
5. ***Maintainability**: Clear separation of concerns between components

For reference implementations, study:
- ***Binance**: Most complete implementation with multiple asset types
- ***OKX**: Good example of mixin-based organization
- ***IB**: Example of broker-style integration
- ***CTP**: Example of SPI/callback-based integration
