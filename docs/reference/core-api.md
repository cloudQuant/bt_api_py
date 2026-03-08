# Core API Reference

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
