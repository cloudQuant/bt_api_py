# bt_api_py

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![PyPI](https://img.shields.io/pypi/v/bt_api_py.svg)](https://pypi.org/project/bt_api_py/)

**bt_api_py** is a unified multi-exchange trading API framework supporting spot, perpetual, futures and stock trading with synchronous REST, asynchronous REST, and WebSocket interfaces.

[中文](README.md) | English

---

## Features

- **Unified Multi-Exchange Interface** — Manage Binance, OKX, CTP (China Futures), Interactive Brokers through a single `BtApi` class
- **Three API Modes** — Synchronous REST, Asynchronous REST, WebSocket real-time streaming
- **Plug-and-Play Architecture** — Registry pattern allows adding new exchanges without modifying core code
- **Event-Driven** — Built-in EventBus for publish/subscribe callback patterns
- **Rich Data Containers** — 20+ standardized data types: Ticker, OrderBook, Bar, Order, Position, etc.
- **Cross-Platform** — Linux (x86_64), Windows (x64), macOS (arm64/x86_64)

## Supported Exchanges

| Exchange | Spot | Perpetual (SWAP) | Futures | Stocks |
|----------|:----:|:----------------:|:-------:|:------:|
| **Binance** | ✅ | ✅ | — | — |
| **OKX** | ✅ | ✅ | — | — |
| **CTP** (China Futures) | — | — | ✅ | — |
| **Interactive Brokers** | — | — | — | ✅ |
| **IB Web API** | — | — | — | ✅ |

## Installation

```bash
# From PyPI
pip install bt_api_py

# From source
git clone https://github.com/cloudQuant/bt_api_py
cd bt_api_py
pip install -r requirements.txt
pip install .
```

## Quick Start

### 1. Configure Account

```bash
cp bt_api_py/configs/account_config_example.yaml bt_api_py/configs/account_config.yaml
```

### 2. Initialize and Connect

```python
from bt_api_py.bt_api import BtApi

exchange_kwargs = {
    "BINANCE___SWAP": {
        "public_key": "YOUR_API_KEY",
        "private_key": "YOUR_SECRET_KEY",
    },
}

bt_api = BtApi(exchange_kwargs, debug=True)
```

### 3. REST Requests

```python
api = bt_api.get_request_api("BINANCE___SWAP")

# Get ticker
tick = api.get_tick("BTC-USDT")
tick.init_data()
print(f"Price: {tick.get_last_price()}")

# Place order
order = api.make_order("BTC-USDT", volume=0.001, price=50000.0, order_type="limit")
```

### 4. WebSocket Subscription

```python
bt_api.subscribe("BINANCE___SWAP___BTC-USDT", [
    {"topic": "kline", "symbol": "BTC-USDT", "period": "1m"},
])

data_queue = bt_api.get_data_queue("BINANCE___SWAP")
data = data_queue.get(timeout=10)
data.init_data()
```

## Documentation

- [Architecture](docs/architecture.md) — Core architecture, design patterns, data flow
- [Usage Guide](docs/usage_guide.md) — Complete tutorials and examples
- [Developer Guide](docs/developer_guide.md) — How to extend and contribute
- [Changelog](docs/change_log.md) — Version history
- [Documentation Index](docs/index.md) — Navigation hub

## Running Tests

```bash
pytest tests -n 4
```

## License

[MIT License](https://opensource.org/licenses/MIT)

## Author

[cloudQuant](https://github.com/cloudQuant) — yunjinqi@gmail.com
