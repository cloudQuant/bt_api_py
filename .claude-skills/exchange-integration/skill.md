---
name: exchange-integration
description: "Generate standardized cryptocurrency exchange integrations for bt_api_py framework"
version: "2.0.0"
author: "bt_api_py"

requires:
  python: ">=3.8"
  packages:
    - jinja2
    - pyyaml

inputs:
  exchange_name:
    type: string
    required: true
    description: "Exchange name (e.g., 'bybit', 'kucoin', 'coinex')"

  asset_types:
    type: list
    required: false
    description: "Asset types (spot, futures, swap, margin)"
    default: ["spot"]

  exchange_type:
    type: string
    required: false
    description: "Exchange type (CEX or DEX)"
    choices:
      - cex
      - dex
    default: "cex"

  api_type:
    type: string
    required: false
    description: "API type (REST or GraphQL)"
    choices:
      - rest
      - graphql
    default: "rest"

  auth_type:
    type: string
    required: false
    description: "Authentication method"
    choices:
      - none
      - hmac_sha256
      - hmac_sha256_passphrase
      - hmac_sha512
      - api_key
      - oauth2
    default: "none"

  rest_url:
    type: string
    required: false
    description: "REST API base URL"

  wss_url:
    type: string
    required: false
    description: "WebSocket base URL"

  graphql_url:
    type: string
    required: false
    description: "GraphQL endpoint URL (for DEX)"

  interactive:
    type: boolean
    default: false
    description: "Interactive mode for configuration"

---

# Exchange Integration Skill v2.0

Automatically generates standardized cryptocurrency exchange integration code for the bt_api_py framework.

## Overview

This skill creates a complete exchange adapter that follows the established patterns in the codebase. Based on analysis of existing implementations (Binance, Okx, IB, CTP, CoinEx, etc.), the generated code follows these patterns:

**Generated Files:**
- **YAML Configuration** — `configs/{exchange}.yaml` - Exchange endpoints, periods, rate limits
- **ExchangeData Classes** — `containers/exchanges/{exchange}_exchange_data.py` - Config loading with lazy caching
- **Feed Classes** — `feeds/live_{exchange}/request_base.py` + `spot.py` - REST API handling
- **Data Containers** — `containers/tickers/{exchange}_ticker.py` - Data normalization
- **Registration Module** — `feeds/register_{exchange}.py` - Auto-registration on import

## Quick Start

### Interactive Mode (Recommended)

```bash
python .claude-skills/exchange-integration/scripts/generator.py coinex -i
```

### Minimal Usage

```bash
python .claude-skills/exchange-integration/scripts/generator.py phemex
```

### DEX (GraphQL) Mode

```bash
python .claude-skills/exchange-integration/scripts/generator.py uniswap --api-type graphql --graphql-url https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3
```

## Generated File Structure

```
bt_api_py/
├── configs/
│   └── {exchange}.yaml                  # YAML configuration (REQUIRED)
├── feeds/
│   ├── live_{exchange}/
│   │   ├── __init__.py
│   │   ├── request_base.py              # Base REST API class with auth
│   │   └── spot.py                      # Spot market data implementation
│   └── register_{exchange}.py           # Registration module
├── containers/
│   ├── exchanges/
│   │   └── {exchange}_exchange_data.py  # ExchangeData with YAML loading
│   └── tickers/
│       └── {exchange}_ticker.py         # Ticker data container
```

## Core Patterns (CRITICAL)

### 1. YAML Config Pattern (REQUIRED)

Every exchange MUST have a YAML config file:

```yaml
id: {exchange_id}
display_name: {Display Name}
venue_type: cex  # or dex
website: https://www.exchange.com
api_doc: https://docs.exchange.com

base_urls:
  rest: https://api.exchange.com
  wss: wss://stream.exchange.com

authentication:
  type: hmac_sha256  # or none, api_key, hmac_sha512
  header_name: X-API-KEY

kline_periods:
  1m: "1min"
  5m: "5min"
  15m: "15min"
  30m: "30min"
  1h: "1hour"
  4h: "4hour"
  1d: "1day"
  1w: "1week"

legal_currency: [USDT, USD, BTC, ETH]

rate_limits:
  - name: public_api
    type: sliding_window
    interval: 60
    limit: 1200

asset_types:
  spot:
    exchange_name: {exchange}Spot
    rest_paths:
      get_server_time: "GET /api/v1/time"
      get_ticker: "GET /api/v1/ticker"
      get_depth: "GET /api/v1/depth"
      get_kline: "GET /api/v1/kline"
    wss_paths:
      tick: "{symbol}@ticker"
```

### 2. ExchangeData Pattern (Config Loading)

```python
_config = None
_config_loaded = False

def _get_{exchange}_config():
    """延迟加载并缓存 YAML 配置"""
    global _config, _config_loaded
    if _config_loaded:
        return _config
    _config_loaded = True
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "{exchange}.yaml",
        )
        if os.path.exists(config_path):
            _config = load_exchange_config(config_path)
    except Exception as e:
        logger.warning("Failed to load {exchange}.yaml config: %s", e)
    return _config

class {Exchange}ExchangeData(ExchangeData):
    def __init__(self):
        super().__init__()
        self.exchange_name = "{exchange}"
        self.rest_url = "https://api.exchange.com"
        self.wss_url = "wss://stream.exchange.com"
        self.rest_paths = {}
        self.wss_paths = {}

    def _load_from_config(self, asset_type):
        """从 YAML 配置加载"""
        config = _get_{exchange}_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            self.rest_url = config.base_urls.rest
        if config.base_urls and config.base_urls.wss:
            self.wss_url = config.base_urls.wss
        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)
        if asset_cfg.wss_paths:
            self.wss_paths.update(asset_cfg.wss_paths)
        return True

class {Exchange}ExchangeDataSpot({Exchange}ExchangeData):
    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self._load_from_config("spot")
```

### 3. Registry Naming Convention

**Exchange Identifier Format**: `{EXCHANGE}___{ASSET_TYPE}`

```python
# Correct examples
"BINANCE___SPOT"
"BINANCE___SWAP"
"OKX___SPOT"
"COINEX___SPOT"
"UNISWAP___DEX"

# Wrong - don't use
"BinanceSpot"
"binance-spot"
"BINANCE_SPOT"
```

### 4. Registration Pattern

```python
from bt_api_py.registry import ExchangeRegistry

def register_{exchange}():
    """注册 {Exchange} 到全局 ExchangeRegistry"""

    # 注册 Feed 类
    ExchangeRegistry.register_feed(
        "{EXCHANGE}___SPOT",
        {Exchange}RequestDataSpot
    )

    # 注册配置类
    ExchangeRegistry.register_exchange_data(
        "{EXCHANGE}___SPOT",
        {Exchange}ExchangeDataSpot
    )

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler(
        "{EXCHANGE}___SPOT",
        _{exchange}_balance_handler
    )

# 模块导入时自动注册
register_{exchange}()
```

### 5. Feed Class Pattern

```python
from bt_api_py.feeds.capability import Capability

class {Exchange}RequestDataSpot({Exchange}RequestData):
    """{Exchange} Spot Feed"""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "{EXCHANGE}___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """获取 ticker 数据"""
        request_type = "get_tick"
        path = f"GET /api/v1/ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params={"symbol": symbol}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """标准化 ticker 数据"""
        if not input_data:
            return [], False
        ticker = input_data.get("data", {})
        return [ticker], ticker is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """公开方法"""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)
```

### 6. Data Container Pattern

```python
import json
import time
from bt_api_py.containers.tickers.ticker import TickerData

class {Exchange}RequestTickerData(TickerData):
    """{Exchange} ticker 数据容器"""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, symbol_name, asset_type, has_been_json_encoded)
        self.exchange_name = "{EXCHANGE}"
        self.local_update_time = time.time()

    def init_data(self):
        """解析交易所 ticker 响应"""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data.get("data", {})
        if data:
            self.ticker_symbol_name = data.get("symbol")
            self.last_price = self._parse_float(data.get("last"))
            self.bid_price = self._parse_float(data.get("bid"))
            self.ask_price = self._parse_float(data.get("ask"))
            self.volume_24h = self._parse_float(data.get("volume"))

        self.has_been_init_data = True
        return self

    @staticmethod
    def _parse_float(value):
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
```

## Common Issues and Solutions

### Issue 1: Config Loading Fails

**Problem**: `'ExchangeConfig' object has no attribute 'symbol_mappings'`

**Solution**: Access raw config instead of pydantic model:

```python
# Wrong
mappings = config.symbol_mappings

# Correct
raw_mappings = getattr(config, '_raw_config', {}).get('symbol_mappings', {})
```

### Issue 2: GraphQL Query Errors

**Problem**: `Cannot query field "X" on type "Y"`

**Solution**: Verify GraphQL schema and remove invalid fields:

```python
# Wrong - invalid fields
query = """
{
    pools {
        tokens { id }      # Invalid
        liquidity          # Invalid
    }
}
"""

# Correct - use valid fields only
query = """
{
    pools {
        id
        token0 { symbol }
        token1 { symbol }
    }
}
"""
```

### Issue 3: Import Errors

**Problem**: `ModuleNotFoundError: No module named 'xxx'`

**Solution**: Ensure all required files are created:
- `live_{exchange}/__init__.py`
- Data containers with correct imports
- Register module imported in `__init__.py`

### Issue 4: Path Not Found

**Problem**: `raise_path_error` called

**Solution**: Ensure YAML config has all required `rest_paths`:

```yaml
asset_types:
  spot:
    rest_paths:
      get_tick: "GET /api/v1/ticker"  # REQUIRED
      get_depth: "GET /api/v1/depth"   # REQUIRED
```

## Authentication Types

| Type | Description | Headers |
|------|-------------|---------|
| `none` | No auth required | None |
| `api_key` | API Key only | `X-API-KEY: {key}` |
| `hmac_sha256` | HMAC-SHA256 signature | `X-API-KEY`, signature in query params |
| `hmac_sha256_passphrase` | OKX-style | API Key, Signature, Timestamp, Passphrase |
| `hmac_sha512` | Kraken-style | API-Key, API-Sign (base64) |

## Post-Generation Checklist

- [ ] Create `configs/{exchange}.yaml` with correct API endpoints
- [ ] Create `containers/exchanges/{exchange}_exchange_data.py` with `_load_from_config()`
- [ ] Create `feeds/live_{exchange}/` directory with `request_base.py` and `spot.py`
- [ ] Create data containers (at minimum `{exchange}_ticker.py`)
- [ ] Create `feeds/register_{exchange}.py` with auto-registration
- [ ] Import register module in `bt_api_py/feeds/__init__.py` or main startup
- [ ] Test with public endpoints (get_tick, get_depth)
- [ ] Verify YAML config paths match exchange API documentation

## DEX-Specific Patterns

For DEX (Uniswap, PancakeSwap, Balancer, etc.):

```python
# GraphQL query execution
def _execute_graphql(self, query: str, variables: dict = None):
    """Execute GraphQL query"""
    response = requests.post(
        self.graphql_url,
        json={"query": query, "variables": variables},
        headers={"Content-Type": "application/json"}
    )
    return response.json()

# Pool data normalization
def get_pools(self, extra_data=None, **kwargs):
    """Get all pools"""
    query = """
    {
        pools(first: 100) {
            id
            token0 { symbol }
            token1 { symbol }
        }
    }
    """
    extra_data = extra_data or {}
    extra_data.update({
        "normalize_function": self._get_pools_normalize_function,
    })
    return self.request("GRAPHQL", query=query, extra_data=extra_data)
```

## Reference Implementations

- **Binance**: `bt_api_py/feeds/live_binance/` — HMAC-SHA256, complete implementation
- **OKX**: `bt_api_py/feeds/live_okx/` — Passphrase auth, Mixin architecture
- **CoinEx**: `bt_api_py/feeds/live_coinex/` — Simple HMAC-SHA256, minimal implementation
- **Uniswap**: `bt_api_py/feeds/live_uniswap/` — GraphQL DEX implementation
- **PancakeSwap**: `bt_api_py/feeds/live_pancakeswap/` — BSC DEX with GraphQL

## Version History

### v2.0.0
- Updated with lessons from 20+ exchange implementations
- Added DEX/GraphQL support
- Clarified YAML config requirements
- Fixed config loading patterns
- Added common issues and solutions

### v1.1.0
- Initial template-based generation
