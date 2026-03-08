#!/usr/bin/env python3
"""
Enhanced Documentation Generator for bt_api_py - Simplified Version
"""

import os
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def create_interactive_examples(docs_dir):
    """Create interactive code examples directory."""
    examples_dir = docs_dir / "examples"
    examples_dir.mkdir(exist_ok=True)

    gallery_content = """# Interactive Code Examples

Welcome to the bt_api_py interactive code gallery. These examples demonstrate common trading scenarios.

## Quick Examples

### Real-time Price Monitoring
```python
import asyncio
from bt_api_py import BtApi

async def monitor_price():
    api = BtApi()
    async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
        ticker.init_data()
        print(f"BTC Price: ${ticker.get_last_price():.2f}")

asyncio.run(monitor_price())
```

### Multi-Exchange Price Comparison
```python
from bt_api_py import BtApi

api = BtApi()

# Get prices from multiple exchanges
exchanges = ["BINANCE___SPOT", "OKX___SPOT", "HTX___SPOT"]
symbol = "BTCUSDT"

for exchange in exchanges:
    try:
        ticker = api.get_tick(exchange, symbol)
        ticker.init_data()
        print(f"{exchange}: ${ticker.get_last_price():.2f}")
    except Exception as e:
        print(f"{exchange}: Error - {e}")
```

### Simple Trading Bot
```python
import asyncio
from bt_api_py import BtApi

class SimpleBot:
    def __init__(self):
        self.api = BtApi(exchange_kwargs={
            "BINANCE___SPOT": {
                "api_key": "your_key",
                "secret": "your_secret",
                "testnet": True
            }
        })
        
    async def run(self):
        # Monitor price and place orders
        async for ticker in self.api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
            ticker.init_data()
            price = ticker.get_last_price()
            
            # Simple strategy: buy on dip
            if price < 45000:
                order = await self.api.async_make_order(
                    exchange_name="BINANCE___SPOT",
                    symbol="BTCUSDT",
                    volume=0.001,
                    price=price * 0.999,  # Slightly below market
                    order_type="limit",
                    side="buy"
                )
                print(f"Placed buy order: {order.get_order_id()}")

bot = SimpleBot()
asyncio.run(bot.run())
```

## More Examples Coming Soon!

- Advanced trading strategies
- Risk management systems
- Portfolio rebalancing
- High-frequency trading patterns

---

Need help? Check our [complete documentation](../index.md).
"""

    gallery_file = examples_dir / "gallery.md"
    gallery_file.write_text(gallery_content)
    logger.info(f"Created examples gallery: {gallery_file}")


def create_api_patterns_guide(docs_dir):
    """Create API usage patterns guide."""
    guides_dir = docs_dir / "guides"
    guides_dir.mkdir(exist_ok=True)

    patterns_content = """# API Usage Patterns & Best Practices

This guide covers common patterns for using bt_api_py effectively.

## Connection Management

### Reuse Connections
```python
# Good: Reuse API instance
class TradingApp:
    def __init__(self):
        self.api = BtApi(exchange_kwargs=config)
        
    def get_price(self, symbol):
        ticker = self.api.get_tick("BINANCE___SPOT", symbol)
        return ticker.get_last_price()
```

### Batch Operations
```python
# Efficient: Get multiple symbols at once
symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
tickers = api.get_multiple_ticks("BINANCE___SPOT", symbols)

for symbol, ticker in tickers.items():
    ticker.init_data()
    print(f"{symbol}: ${ticker.get_last_price():.2f}")
```

## Error Handling

### Robust Error Handling
```python
from bt_api_py.exceptions import (
    RateLimitError,
    OrderError,
    InsufficientBalanceError
)

def safe_place_order(symbol, volume, price):
    try:
        order = api.make_order(
            exchange_name="BINANCE___SPOT",
            symbol=symbol,
            volume=volume,
            price=price,
            order_type="limit",
            side="buy"
        )
        print(f"Order placed: {order.get_order_id()}")
        return order
        
    except InsufficientBalanceError:
        print("Insufficient balance")
    except OrderError as e:
        print(f"Order failed: {e}")
    except RateLimitError:
        print("Rate limit exceeded")
        time.sleep(60)  # Wait and retry
        return safe_place_order(symbol, volume, price)
```

## Async Patterns

### Async Processing
```python
import asyncio
from bt_api_py import BtApi

async def process_multiple_symbols():
    api = BtApi()
    symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
    
    # Process symbols concurrently
    tasks = [
        api.async_get_tick("BINANCE___SPOT", symbol) 
        for symbol in symbols
    ]
    tickers = await asyncio.gather(*tasks)
    
    for symbol, ticker in zip(symbols, tickers):
        ticker.init_data()
        print(f"{symbol}: ${ticker.get_last_price():.2f}")

asyncio.run(process_multiple_symbols())
```

## WebSocket Patterns

### Multi-Stream Processing
```python
async def stream_data():
    api = BtApi()
    
    async def process_ticker():
        async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
            ticker.init_data()
            print(f"Price: ${ticker.get_last_price():.2f}")
    
    async def process_depth():
        async for depth in api.stream_depth("BINANCE___SPOT", "BTCUSDT"):
            depth.init_data()
            if depth.get_bids():
                print(f"Bid: ${depth.get_bids()[0][0]:.2f}")
    
    # Run multiple streams
    await asyncio.gather(
        process_ticker(),
        process_depth()
    )

asyncio.run(stream_data())
```

## Performance Tips

1. **Reuse API instances** - Don't create new instances for each request
2. **Use async methods** - For concurrent operations
3. **Batch requests** - When getting multiple symbols
4. **Implement caching** - For frequently accessed data
5. **Handle rate limits** - Implement backoff strategies

---

Need more patterns? Check our [API reference](../reference/core-api.md).
"""

    patterns_file = guides_dir / "api-patterns.md"
    patterns_file.write_text(patterns_content)
    logger.info(f"Created API patterns guide: {patterns_file}")


def create_exchange_guide(docs_dir):
    """Create exchange-specific guide."""
    exchanges_dir = docs_dir / "exchanges"
    exchanges_dir.mkdir(exist_ok=True)

    binance_guide = """# Binance Integration Guide

Complete guide for integrating with Binance exchange.

## Quick Setup

### 1. Get API Credentials
1. Go to [Binance API Management](https://www.binance.com/en/my/settings/api-management)
2. Create new API key
3. Configure IP restrictions
4. Enable required permissions

### 2. Configure API
```python
from bt_api_py import BtApi

# Production
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": False
    }
})

# Testnet
testnet_api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "testnet_key",
        "secret": "testnet_secret",
        "testnet": True
    }
})
```

## Supported Products

| Product | Code | WebSocket | Testnet |
|---------|-------|-----------|---------|
| Spot Trading | `BINANCE___SPOT` | ✅ | ✅ |
| USDM Futures | `BINANCE___USDM_SWAP` | ✅ | ✅ |
| COINM Futures | `BINANCE___COINM_SWAP` | ✅ | ✅ |

## Usage Examples

### Spot Trading
```python
# Get ticker
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
print(f"BTC Price: ${ticker.get_last_price():.2f}")

# Place order
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    order_type="market",
    side="buy"
)
print(f"Order ID: {order.get_order_id()}")
```

### WebSocket Streaming
```python
import asyncio

async def stream_prices():
    async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
        ticker.init_data()
        print(f"Live Price: ${ticker.get_last_price():.2f}")

asyncio.run(stream_prices())
```

### Multi-Exchange Comparison
```python
# Compare prices across exchanges
exchanges = ["BINANCE___SPOT", "OKX___SPOT"]
symbol = "BTCUSDT"

for exchange in exchanges:
    ticker = api.get_tick(exchange, symbol)
    ticker.init_data()
    print(f"{exchange}: ${ticker.get_last_price():.2f}")
```

## Rate Limits

- **Spot API**: 1200 requests/minute
- **Futures API**: 2400 requests/minute
- **WebSocket**: 300 connections/minute

Implement backoff for rate limits:
```python
import time
from bt_api_py.exceptions import RateLimitError

def get_ticker_with_retry(symbol, max_retries=3):
    for attempt in range(max_retries):
        try:
            return api.get_tick("BINANCE___SPOT", symbol)
        except RateLimitError:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            else:
                raise
```

## Error Handling

```python
from bt_api_py.exceptions import (
    BinanceError,
    InsufficientBalanceError,
    OrderError
)

def handle_errors():
    try:
        order = api.make_order(...)
    except InsufficientBalanceError:
        print("Insufficient balance")
    except OrderError as e:
        if "LOT_SIZE" in str(e):
            print("Invalid order size")
        else:
            print(f"Order error: {e}")
    except BinanceError as e:
        print(f"Binance error: {e}")
```

## Resources

- [Binance API Docs](https://binance-docs.github.io/apidocs/)
- [Testnet](https://testnet.binance.vision/)
- [Rate Limits](https://binance-docs.github.io/apidocs/spot/en/#limits)

---

Need help? Check our [troubleshooting guide](../support/faq.md).
"""

    binance_file = exchanges_dir / "binance.md"
    binance_file.write_text(binance_guide)
    logger.info(f"Created Binance guide: {binance_file}")


def create_core_api_reference(docs_dir):
    """Create core API reference."""
    reference_dir = docs_dir / "reference"
    reference_dir.mkdir(exist_ok=True)

    api_content = """# Core API Reference

## BtApi - Main Interface

The unified API for all exchanges.

### Initialization
```python
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "key",
        "secret": "secret",
        "testnet": True
    }
})
```

### Market Data Methods

#### Get Ticker
```python
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
price = ticker.get_last_price()
volume = ticker.get_volume()
```

#### Get Order Book
```python
depth = api.get_depth("BINANCE___SPOT", "BTCUSDT", limit=10)
depth.init_data()
best_bid = depth.get_bids()[0]
best_ask = depth.get_asks()[0]
```

#### Get Kline Data
```python
bars = api.get_kline("BINANCE___SPOT", "BTCUSDT", "1h", limit=24)
for bar in bars:
    bar.init_data()
    print(f"{bar.get_timestamp()}: O={bar.get_open()}, C={bar.get_close()}")
```

### Trading Methods

#### Place Order
```python
# Market order
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    order_type="market",
    side="buy"
)

# Limit order
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=50000,
    order_type="limit",
    side="sell"
)
```

#### Cancel Order
```python
api.cancel_order("BINANCE___SPOT", order.get_order_id())
```

#### Query Order
```python
order = api.query_order("BINANCE___SPOT", order_id="123456")
print(f"Status: {order.get_status()}")
```

### Account Methods

#### Get Balance
```python
balance = api.get_balance("BINANCE___SPOT", "USDT")
balance.init_data()
print(f"Available: {balance.get_free_balance()}")
```

#### Get Account Info
```python
account = api.get_account("BINANCE___SPOT")
account.init_data()
# Access account details
```

### WebSocket Methods

#### Stream Ticker
```python
async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
    ticker.init_data()
    print(f"Price: {ticker.get_last_price()}")
```

#### Stream Order Book
```python
async for depth in api.stream_depth("BINANCE___SPOT", "BTCUSDT"):
    depth.init_data()
    print(f"Spread: {depth.get_asks()[0][0] - depth.get_bids()[0][0]}")
```

#### Stream Trades
```python
async for trade in api.stream_trades("BINANCE___SPOT", "BTCUSDT"):
    trade.init_data()
    print(f"Trade: {trade.get_price()} @ {trade.get_quantity()}")
```

### Multi-Exchange Methods

#### Get All Tickers
```python
all_prices = api.get_all_ticks("BTCUSDT")
for exchange, ticker in all_prices.items():
    ticker.init_data()
    print(f"{exchange}: ${ticker.get_last_price()}")
```

### Async Methods

All methods have async versions:
```python
ticker = await api.async_get_tick("BINANCE___SPOT", "BTCUSDT")
order = await api.async_make_order(...)
balance = await api.async_get_balance("BINANCE___SPOT", "USDT")
```

## Data Containers

### TickerData
- `get_symbol()` - Trading symbol
- `get_last_price()` - Last price
- `get_bid_price()` - Best bid
- `get_ask_price()` - Best ask
- `get_volume()` - 24h volume
- `get_price_change()` - 24h change %

### OrderData
- `get_order_id()` - Order ID
- `get_symbol()` - Symbol
- `get_order_type()` - Order type
- `get_side()` - Buy/Sell
- `get_quantity()` - Quantity
- `get_price()` - Price
- `get_filled_quantity()` - Filled amount
- `get_status()` - Order status

### BalanceData
- `get_asset()` - Asset name
- `get_free_balance()` - Available balance
- `get_locked_balance()` - Locked balance
- `get_total_balance()` - Total balance

## Error Handling

### Common Exceptions
- `BtApiError` - Base exception
- `ExchangeNotFoundError` - Exchange not found
- `OrderError` - Order operation failed
- `InsufficientBalanceError` - Insufficient funds
- `RateLimitError` - Rate limit exceeded
- `WebSocketError` - WebSocket connection failed

### Error Handling Pattern
```python
try:
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
except ExchangeNotFoundError:
    print("Exchange not supported")
except RateLimitError:
    print("Rate limit exceeded")
except Exception as e:
    print(f"Error: {e}")
```

## Exchange Names

Follow the pattern: `EXCHANGE___TYPE`

Examples:
- `BINANCE___SPOT` - Binance Spot
- `BINANCE___USDM_SWAP` - Binance USDT Futures
- `OKX___SPOT` - OKX Spot
- `CTP___FUTURE` - CTP Futures
- `IB_WEB___STK` - Interactive Brokers Stocks

---

Need more details? Check our [complete examples](../examples/gallery.md).
"""

    core_api_file = reference_dir / "core-api.md"
    core_api_file.write_text(api_content)
    logger.info(f"Created core API reference: {core_api_file}")


def update_mkdocs_config():
    """Update mkdocs.yml to include new documentation sections."""
    config_path = Path("mkdocs.yml")

    if not config_path.exists():
        logger.warning("mkdocs.yml not found")
        return

    # Read existing config
    with open(config_path, "r") as f:
        content = f.read()

    # Add new sections to navigation if not already present
    if "examples/gallery.md" not in content:
        # Add examples section after getting-started
        old_nav = "  - 常见问题: getting-started/faq.md"
        new_nav = """  - 常见问题: getting-started/faq.md
      - 交互式示例: examples/gallery.md"""
        content = content.replace(old_nav, new_nav)

    # Save updated config
    with open(config_path, "w") as f:
        f.write(content)

    logger.info("Updated mkdocs.yml navigation")


def main():
    """Generate all enhanced documentation."""
    project_root = Path(".")
    docs_dir = project_root / "docs"

    logger.info("Generating enhanced documentation...")

    # Create enhanced sections
    create_interactive_examples(docs_dir)
    create_api_patterns_guide(docs_dir)
    create_exchange_guide(docs_dir)
    create_core_api_reference(docs_dir)

    # Update configuration
    update_mkdocs_config()

    logger.info("✅ Enhanced documentation generation complete!")
    logger.info("📖 Run 'mkdocs serve' to preview documentation")
    logger.info("🌐 Run 'mkdocs build' to build documentation")


if __name__ == "__main__":
    main()
