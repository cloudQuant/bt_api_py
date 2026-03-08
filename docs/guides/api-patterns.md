# API Usage Patterns & Best Practices

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
