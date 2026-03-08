# Interactive Code Examples

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
