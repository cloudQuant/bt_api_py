# bt_api_py

[![Python 3.11+](<https://img.shields.io/badge/python-3.11%2B-blue.svg)](<https://www.python.org/downloads/>)>
[![License: MIT](<https://img.shields.io/badge/License-MIT-green.svg)](<https://opensource.org/licenses/MIT>)>
[![PyPI](<https://img.shields.io/pypi/v/bt_api_py.svg)](<https://pypi.org/project/bt_api_py/>)>
[![Documentation](<https://img.shields.io/badge/docs-latest-blue.svg)](<https://cloudquant.github.io/bt_api_py/>)>

- *bt_api_py**is a unified multi-exchange trading API framework supporting spot, perpetual, futures and stock trading with synchronous REST, asynchronous REST, and WebSocket interfaces.

:books:**[Documentation](<https://cloudquant.github.io/bt_api_py/)**|> :rocket: [Quick Start](<https://cloudquant.github.io/bt_api_py/quickstart/)> | [中文](README.md) | English

- --

## Features

- **Unified Multi-Exchange Interface**— Manage Binance, OKX, CTP (China Futures), Interactive Brokers through a single `BtApi` class
- **Three API Modes**— Synchronous REST, Asynchronous REST, WebSocket real-time streaming
- **Plug-and-Play Architecture**— Registry pattern allows adding new exchanges without modifying core code
- **Event-Driven**— Built-in EventBus for publish/subscribe callback patterns
- **Rich Data Containers**— 20+ standardized data types: Ticker, OrderBook, Bar, Order, Position, etc.
- **Cross-Platform**— Linux (x86_64), Windows (x64), macOS (arm64/x86_64)

## Supported Exchanges

| Exchange | Code | Spot | Perpetual (SWAP) | Futures | Stocks |

|----------|------|:----:|:----------------:|:-------:|:------:|

|**Binance**| `BINANCE___SPOT` / `BINANCE___SWAP` | ✅ | ✅ | — | — |

|**OKX**| `OKX___SPOT` / `OKX___SWAP` | ✅ | ✅ | — | — |

|**CTP**(China Futures) | `CTP___FUTURE` | — | — | ✅ | — |

|**Interactive Brokers**| `IB_WEB___STK` / `IB_WEB___FUT` | — | — | — | ✅ |

## Installation

```bash

# From PyPI

pip install bt_api_py

# From source

git clone <https://github.com/cloudQuant/bt_api_py>
cd bt_api_py
pip install -r requirements.txt
pip install .

```bash

## Quick Start

### Unified Multi-Exchange API

```python
from bt_api_py import BtApi

# Configure multiple exchanges

exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    },
    "OKX___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "passphrase": "your_passphrase",
    },
    "IB_WEB___STK": {
        "auth_config": {
            "account_id": "your_account_id",
        }
    },
}

# Create unified API instance

api = BtApi(exchange_kwargs=exchange_kwargs)

# Get market data (unified interface)

ticker = api.get_ticker("BINANCE___SPOT", "BTCUSDT")
print(f"BTC Price: {ticker.last_price}")

# Place order (unified interface)

order = api.limit_order(
    exchange="BINANCE___SPOT",
    symbol="BTCUSDT",
    side="buy",
    quantity=0.001,
    price=50000
)

```bash

### CTP Futures Trading

```python
from bt_api_py import BtApi, CtpAuthConfig

exchange_kwargs = {
    "CTP___FUTURE": {
        "auth_config": CtpAuthConfig(
            broker_id="9999",
            user_id="your_user_id",
            password="your_password",
            md_front="tcp://180.168.146.187:10211",
            td_front="tcp://180.168.146.187:10201",
        )
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)
ticker = api.get_ticker("CTP___FUTURE", "IF2506")

```bash

### WebSocket Subscription

```python
def on_ticker(ticker):
    print(f"Price Update: {ticker.last_price}")

# Subscribe to ticker updates

api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT", on_ticker)
api.run()

```bash

## Documentation

For complete documentation, visit:**[<https://cloudquant.github.io/bt_api_py/](<https://cloudquant.github.io/bt_api_py/)*>*>

### Core Documentation

- [Quick Start](<https://cloudquant.github.io/bt_api_py/quickstart/)> - Get started in 5 minutes
- [Installation Guide](<https://cloudquant.github.io/bt_api_py/installation/)> - Installation and configuration
- [Architecture](<https://cloudquant.github.io/bt_api_py/architecture/)> - Core architecture and design patterns
- [Usage Guide](<https://cloudquant.github.io/bt_api_py/usage_guide/)> - Complete tutorials and examples
- [Developer Guide](<https://cloudquant.github.io/bt_api_py/developer_guide/)> - How to extend and contribute

### Exchange Guides

- [Binance API](<https://cloudquant.github.io/bt_api_py/binance/)> - Spot/Perpetual API documentation
- [OKX API](<https://cloudquant.github.io/bt_api_py/okx/)> - Full product API documentation
- [CTP Futures](<https://cloudquant.github.io/bt_api_py/ctp_quickstart/)> - CTP quick start
- [Interactive Brokers](<https://cloudquant.github.io/bt_api_py/ib_quickstart/)> - IB quick start

## Running Tests

```bash

# Run all tests

pytest tests -v

# Run in parallel (recommended)

pytest tests -n 4

# Run specific tests

pytest tests/feeds/test_live_binance_spot_wss_data.py -v

```bash

## Roadmap

- [ ] Add more exchange support
- [ ] Improve backtesting framework
- [ ] Add more technical indicators
- [ ] Optimize performance and stability

## Contributing

Contributions are welcome! Please see the [Developer Guide](<https://cloudquant.github.io/bt_api_py/developer_guide/)> for details.

## License

[MIT License](<https://opensource.org/licenses/MIT)>

## Author

[cloudQuant](<https://github.com/cloudQuant)> — yunjinqi@gmail.com

- --

:star: If you find this project helpful, please give us a star!
