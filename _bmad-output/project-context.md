- --

project_name: 'bt_api_py'
user_name: 'cloud'
date: '2026-02-27'
sections_completed:

  - technology_stack
  - critical_implementation_rules
  - code_style
  - configuration

status: 'complete'
rule_count: 12
optimized_for_llm: true

- --

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

- --

## Technology Stack & Versions

- **Python**: 3.11+ (supports 3.11, 3.12, 3.13)
- **Package**: bt_api_py v0.15
- **C/C++ Extensions**:
  - Cython for `calculate_numbers` performance module
  - SWIG for CTP (China Futures) API bindings v6.7.7
- **Key Dependencies**:
  - `numpy==1.26.4` (pinned version)
  - `aiohttp` - async HTTP client
  - `websocket-client` - WebSocket connections
  - `spdlog` - logging
  - `pandas` - data manipulation
  - `pytest` with pytest-xdist (parallel tests)

- --

## Critical Implementation Rules

### 1. Exchange Naming Convention (CRITICAL)

- *Use triple-underscore format**: `EXCHANGE___ASSET_TYPE`

- `BINANCE___SPOT` - Binance spot trading
- `BINANCE___SWAP` - Binance perpetual futures
- `OKX___SPOT` - OKX spot trading
- `OKX___SWAP` - OKX perpetual futures
- `CTP___FUTURE` - CTP futures (China)
- `IB___STK` - Interactive Brokers stocks

This naming is used throughout:

- Registry keys
- Configuration sections
- Data queue identifiers

### 2. Container Class Pattern

- *All data containers follow this hierarchy**:

```bash
bt_api_py/containers/
├── {type}/                    # Base type

│   ├── {type}.py             # Abstract base class (e.g., OrderData)

│   ├── binance_{type}.py     # Binance implementation

│   ├── okx_{type}.py         # OKX implementation

│   ├── ctp/                  # CTP subdirectory

│   └── ib/                   # IB subdirectory

```bash

- *Rules**:
- Base class defines abstract `get_*()` methods
- Exchange-specific classes inherit and implement `init_data()`
- Always call `init_data()` before accessing data
- Use `has_been_json_encoded` flag to parse input correctly

### 3. Registry Pattern (DO NOT MODIFY)

- *All exchanges MUST be registered via `ExchangeRegistry`**:

```python

# In feeds/register_{exchange}.py

ExchangeRegistry.register_feed("BINANCE___SWAP", BinanceRequestDataSwap)
ExchangeRegistry.register_stream("BINANCE___SWAP", "market", BinanceMarketStream)
ExchangeRegistry.register_exchange_data("BINANCE___SWAP", BinanceExchangeDataSwap)

```bash

- *NEVER** hardcode exchange class references in core modules. Always use registry.

### 4. Exception Hierarchy

- *Use custom exceptions from `bt_api_py.exceptions`**:

- `BtApiError` - base class
- `ExchangeNotFoundError` - exchange not registered
- `ExchangeConnectionError` - connection failed
- `AuthenticationError` - API key/auth failed
- `RequestTimeoutError` - REST timeout
- `RequestError` - REST request failed
- `OrderError` - order operation failed
- `SubscribeError` - WebSocket subscription failed
- `DataParseError` - data parsing failed

- *NEVER** use generic `Exception` or `assert` for error handling.

### 5. Async Method Convention

- *Prefix async methods with `async_`**:

- `async_get_tick()` - async ticker data
- `async_get_bar()` - async bar data
- `async_get_orderbook()` - async orderbook

Async methods **push results to data queue** instead of returning:

```python
api.async_get_tick("BTC-USDT", extra_data={"key": "value"})

# Result available via: data_queue.get(timeout=10)

```bash

### 6. Data Queue Pattern

- *Each exchange has its own data queue**:

```python
data_queue = bt_api.get_data_queue("BINANCE___SWAP")
data = data_queue.get(timeout=10)

```bash

- Use `queue.Queue` for thread-safe operations
- Push data using `push_data()` method in streams
- Always handle `queue.Empty` exception

### 7. WebSocket Stream Architecture

- *All WebSocket streams inherit from `BaseDataStream`**:

```python
class MyStream(BaseDataStream):
    def connect(self): ...
    def disconnect(self): ...
    def subscribe_topics(self, topics): ...  # topics = [{"topic": "kline", "symbol": "BTC-USDT", "period": "1m"}]
    def _run_loop(self): ...  # runs in daemon thread

```bash

- *Connection states**: `DISCONNECTED` → `CONNECTING` → `CONNECTED` → `AUTHENTICATED`

### 8. Test Organization

- *Tests mirror package structure**:

```bash
tests/
├── containers/
│   ├── orders/test_binance_order.py
│   └── orders/test_okx_order.py
├── feeds/test_binance_*.py
└── test_stage*.py  # Integration tests

```bash

- *Test naming**: `test_{exchange}_{feature}.py`

- *Test markers**: Use `pytest.mark.xdist_group("mixed_exchange_api")` for tests requiring live API access.

### 9. Order Status Mapping

- *Always use `OrderStatus.from_value()`** to normalize exchange-specific statuses:

```python
from bt_api_py.containers.orders.order import OrderStatus
status = OrderStatus.from_value("NEW")  # Returns OrderStatus.ACCEPTED

```bash
The `get_static_dict()` method handles variations like `NEW/new/` mapping.

### 10. Symbol Naming

- *Standardized symbol format**: `{BASE}-{QUOTE}-{TYPE}`

- Crypto: `BTC-USDT`, `ETH-USDT`
- Perpetual: `BTC-USDT` (same, asset_type determines spot/swap)
- CTP: Uses exchange contract codes (e.g., `au2506`)
- IB: Uses SMART combo (e.g., `AAPL-STK-SMART`)

### 11. CTP-Specific Rules

- *CTP requires special handling**:

- CTP uses SWIG bindings - the wrapper is auto-split into modules
- CTP orders MUST include `get_order_offset()` (`open/close/close_today/close_yesterday`)
- CTP orders MUST include `get_order_exchange_id()` (e.g., `CFFEX`, `SHFE`)
- Platform-specific: macOS (.framework), Linux (.so), Windows (.dll)

### 12. Platform Compatibility

- *Code must support**:
- Linux (x86_64)
- Windows (x64)
- macOS (arm64/x86_64)

Use platform detection in `setup.py` for CTP bindings.

- --

## Code Style

- **File naming**: `snake_case` (e.g., `binance_order.py`, `okx_ticker.py`)
- **Class naming**: `PascalCase` with prefix (e.g., `BinanceOrderData`, `OkxTickerData`)
- **Method naming**: `snake_case` (e.g., `get_exchange_name`, `init_data`)
- **Constants**: `UPPER_CASE`
- **Docstrings**: Chinese for internal comments, English for API docs

- --

## Configuration

- Account config: `bt_api_py/configs/account_config.yaml`
- Use `AuthConfig`, `CryptoAuthConfig`, `CtpAuthConfig`, `IbAuthConfig` for authentication
- Proxy support via `proxies` and `async_proxy` parameters

- --

## MVP Scope (Current)

- *Supported exchanges for MVP**:
1. Binance (SPOT + SWAP)
2. OKX (SPOT + SWAP)
3. Interactive Brokers (IB)
4. CTP (China Futures)

- *Future exchanges**: Do NOT add new exchanges until MVP is stable.

- --

## Usage Guidelines

- *For AI Agents:**

- Read this file before implementing any code
- Follow ALL rules exactly as documented
- When in doubt, prefer the more restrictive option
- Update this file if new patterns emerge

- *For Humans:**

- Keep this file lean and focused on agent needs
- Update when technology stack changes
- Review quarterly for outdated rules
- Remove rules that become obvious over time

Last Updated: 2026-02-27
