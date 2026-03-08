# Binance Integration Guide

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
