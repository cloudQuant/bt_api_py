# bt_api_py Documentation

- *bt_api_py**is a unified multi-exchange trading API framework supporting spot, futures, and derivatives trading with synchronous, asynchronous, and WebSocket interfaces.

[![Python 3.11+](<https://img.shields.io/badge/python-3.11%2B-blue.svg)](<https://www.python.org/downloads/>)>
[![License: MIT](<https://img.shields.io/badge/License-MIT-green.svg)](<https://opensource.org/licenses/MIT>)>
[![PyPI](<https://img.shields.io/pypi/v/bt_api_py.svg)](<https://pypi.org/project/bt_api_py/>)>
[![Documentation](<https://img.shields.io/badge/docs-latest-blue.svg)](<https://cloudquant.github.io/bt_api_py/>)>

## Core Features

| Feature | Description |

|---------|-------------|

|**Unified Multi-Exchange API**| Manage Binance, OKX, CTP, Interactive Brokers through a single `BtApi` class |

|**Three API Modes**| Synchronous REST, Asynchronous REST, WebSocket real-time streaming |

|**Plug-and-Play**| Registry-based architecture, add exchanges without modifying core code |

|**Event-Driven**| Built-in EventBus for callback-based event handling |

|**Standardized Data**| Unified Ticker, OrderBook, Bar, Order, Position data types |

|**Cross-Platform**| Linux, Windows, macOS support |

## Supported Exchanges

| Exchange | Code | Spot (SPOT) | Perpetual (SWAP) | Futures (FUTURE) | Stocks (STK) |

|----------|------|:-----------:|:----------------:|:----------------:|:------------:|

|**Binance**| `BINANCE___SPOT` / `BINANCE___SWAP` | ✅ | ✅ | — | — |

|**OKX**| `OKX___SPOT` / `OKX___SWAP` | ✅ | ✅ | — | — |

|**CTP**(China Futures) | `CTP___FUTURE` | — | — | ✅ | — |

|**Interactive Brokers**| `IB_WEB___STK` / `IB_WEB___FUT` | — | — | — | ✅ |

## Quick Links

- [Installation Guide](installation.en.md) - How to install and configure
- [Quick Start](quickstart.en.md) - Get started in 5 minutes
- [Binance API](binance/) - Binance API documentation
- [OKX API](okx/) - OKX API documentation
- [CTP Futures](ctp_quickstart.md) - CTP Quick Start
- [Interactive Brokers](ib_quickstart.md) - IB Quick Start

## Example Code

```python
from bt_api_py import BtApi

# Configure exchanges

exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    }
}

# Create API instance

api = BtApi(exchange_kwargs=exchange_kwargs)

# Get market data

ticker = api.get_ticker("BINANCE___SPOT", "BTCUSDT")
print(f"BTC Price: {ticker.last_price}")

# Place order

order = api.limit_order(
    exchange="BINANCE___SPOT",
    symbol="BTCUSDT",
    side="buy",
    quantity=0.001,
    price=50000
)

```bash

## Project Information

- **GitHub**: <https://github.com/cloudQuant/bt_api_py>
- **PyPI**: <https://pypi.org/project/bt_api_py/>
- **License**: MIT
- **Documentation**: <https://cloudquant.github.io/bt_api_py/>

- --

!!! tip "Tip"
    Start with the [Quick Start](quickstart.en.md) guide to learn the basics.

!!! info "Note"
    Full English documentation is under development. For complete documentation, please refer to the [Chinese version](index.md).
