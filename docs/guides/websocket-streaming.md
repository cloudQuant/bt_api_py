# WebSocket Streaming Guide

Real-time data streaming with bt_api_py WebSocket connections.

## Overview

bt_api_py provides WebSocket streaming for:
- Real-time market data (tickers, order books, trades)
- User data streams (orders, balances, positions)
- Custom data subscriptions

## WebSocket Connection Management

### Basic Connection
```python
import asyncio
from bt_api_py import BtApi

async def basic_streaming():
    api = BtApi()
    
    # Stream ticker data
    async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
        ticker.init_data()
        print(f"BTC Price: ${ticker.get_last_price():.2f}")

asyncio.run(basic_streaming())
```

### Multiple Streams
```python
async def multi_streaming():
    api = BtApi()
    
    async def stream_ticker():
        async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
            ticker.init_data()
            print(f"Ticker: ${ticker.get_last_price():.2f}")
    
    async def stream_depth():
        async for depth in api.stream_depth("BINANCE___SPOT", "BTCUSDT"):
            depth.init_data()
            if depth.get_bids() and depth.get_asks():
                bid = depth.get_bids()[0][0]
                ask = depth.get_asks()[0][0]
                print(f"Spread: ${ask - bid:.2f}")
    
    # Run multiple streams concurrently
    await asyncio.gather(
        stream_ticker(),
        stream_depth()
    )

asyncio.run(multi_streaming())
```

## Market Data Streams

### Ticker Stream
```python
async def ticker_stream():
    api = BtApi()
    
    async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
        ticker.init_data()
        
        # Access ticker data
        symbol = ticker.get_symbol()
        price = ticker.get_last_price()
        volume = ticker.get_volume()
        change = ticker.get_price_change()
        
        print(f"{symbol}: ${price:.2f} ({change:+.2f}%) Vol: {volume:,.0f}")
```

### Order Book Stream
```python
async def depth_stream():
    api = BtApi()
    
    async for depth in api.stream_depth("BINANCE___SPOT", "BTCUSDT"):
        depth.init_data()
        
        # Get best bid/ask
        if depth.get_bids() and depth.get_asks():
            best_bid = depth.get_bids()[0]  # [price, quantity]
            best_ask = depth.get_asks()[0]
            
            print(f"Bid: ${best_bid[0]:.2f} ({best_bid[1]:.4f})")
            print(f"Ask: ${best_ask[0]:.2f} ({best_ask[1]:.4f})")
            
            # Calculate spread
            spread = best_ask[0] - best_bid[0]
            spread_pct = (spread / best_bid[0]) * 100
            print(f"Spread: ${spread:.2f} ({spread_pct:.3f}%)")
```

### Trade Stream
```python
async def trade_stream():
    api = BtApi()
    
    async for trade in api.stream_trades("BINANCE___SPOT", "BTCUSDT"):
        trade.init_data()
        
        # Access trade data
        price = trade.get_price()
        quantity = trade.get_quantity()
        timestamp = trade.get_timestamp()
        side = trade.get_side()  # buy/sell
        
        print(f"Trade: {side} {quantity:.4f} @ ${price:.2f} - {timestamp}")
```

### Kline Stream
```python
async def kline_stream():
    api = BtApi()
    
    async for kline in api.stream_klines("BINANCE___SPOT", "BTCUSDT", "1m"):
        kline.init_data()
        
        # Access kline data
        timestamp = kline.get_timestamp()
        open_price = kline.get_open()
        high_price = kline.get_high()
        low_price = kline.get_low()
        close_price = kline.get_close()
        volume = kline.get_volume()
        
        print(f"Candle {timestamp}: O={open_price} H={high_price} L={low_price} C={close_price} V={volume}")
```

## User Data Streams

### Order Updates
```python
async def order_stream():
    api = BtApi(exchange_kwargs={
        "BINANCE___SPOT": {
            "api_key": "your_key",
            "secret": "your_secret"
        }
    })
    
    async for order in api.stream_orders("BINANCE___SPOT"):
        order.init_data()
        
        status = order.get_status()
        symbol = order.get_symbol()
        side = order.get_side()
        
        if status == "filled":
            filled_qty = order.get_filled_quantity()
            avg_price = order.get_avg_price()
            print(f"✅ Filled: {side} {filled_qty} {symbol} @ ${avg_price}")
            
        elif status == "canceled":
            print(f"❌ Canceled: {symbol} {order.get_order_id()}")
            
        elif status == "partial":
            print(f"⏳ Partial: {symbol} {order.get_filled_quantity()}/{order.get_quantity()}")

asyncio.run(order_stream())
```

### Balance Updates
```python
async def balance_stream():
    api = BtApi(exchange_kwargs={
        "BINANCE___SPOT": {
            "api_key": "your_key",
            "secret": "your_secret"
        }
    })
    
    async for balance in api.stream_balances("BINANCE___SPOT"):
        balance.init_data()
        
        asset = balance.get_asset()
        free = balance.get_free_balance()
        locked = balance.get_locked_balance()
        total = balance.get_total_balance()
        
        print(f"Balance {asset}: Free={free:.6f} Locked={locked:.6f} Total={total:.6f}")
```

## Advanced WebSocket Usage

### Custom Data Processing
```python
class DataProcessor:
    def __init__(self):
        self.price_history = []
        self.sma_window = 20
        
    async def process_stream(self):
        api = BtApi()
        
        async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
            ticker.init_data()
            price = ticker.get_last_price()
            
            # Add to history
            self.price_history.append(price)
            
            # Keep only recent data
            if len(self.price_history) > self.sma_window:
                self.price_history.pop(0)
            
            # Calculate indicators
            if len(self.price_history) >= self.sma_window:
                sma = sum(self.price_history) / self.sma_window
                
                # Generate signals
                if price > sma * 1.02:  # 2% above SMA
                    print(f"🔥 HOT: BTC ${price:.2f} (SMA: ${sma:.2f})")
                elif price < sma * 0.98:  # 2% below SMA
                    print(f"❄️ COLD: BTC ${price:.2f} (SMA: ${sma:.2f})")

processor = DataProcessor()
asyncio.run(processor.process_stream())
```

### Multi-Exchange Streaming
```python
async def multi_exchange_stream():
    api = BtApi()
    exchanges = ["BINANCE___SPOT", "OKX___SPOT", "HTX___SPOT"]
    symbol = "BTCUSDT"
    
    async def stream_exchange(exchange_name):
        try:
            async for ticker in api.stream_ticker(exchange_name, symbol):
                ticker.init_data()
                price = ticker.get_last_price()
                print(f"{exchange_name}: ${price:.2f}")
        except Exception as e:
            print(f"{exchange_name} error: {e}")
    
    # Stream from all exchanges
    tasks = [stream_exchange(ex) for ex in exchanges]
    await asyncio.gather(*tasks, return_exceptions=True)

asyncio.run(multi_exchange_stream())
```

## Error Handling

### Connection Resilience
```python
import asyncio
from bt_api_py.exceptions import WebSocketError

async def resilient_stream():
    api = BtApi()
    max_retries = 5
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
                ticker.init_data()
                print(f"Price: ${ticker.get_last_price():.2f}")
                
        except WebSocketError as e:
            print(f"WebSocket error (attempt {attempt + 1}): {e}")
            
            if attempt < max_retries - 1:
                print(f"Reconnecting in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                print("Max retries reached")
                break
        except Exception as e:
            print(f"Unexpected error: {e}")
            break

asyncio.run(resilient_stream())
```

## Performance Optimization

### Stream Filtering
```python
async def filtered_stream():
    api = BtApi()
    
    async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
        ticker.init_data()
        price = ticker.get_last_price()
        volume = ticker.get_volume()
        
        # Only process significant price changes
        if volume > 1000:  # High volume threshold
            print(f"High volume trade: ${price:.2f} Vol: {volume:,.0f}")

asyncio.run(filtered_stream())
```

### Batch Processing
```python
async def batch_processing():
    api = BtApi()
    batch_size = 10
    ticker_batch = []
    
    async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
        ticker.init_data()
        ticker_batch.append(ticker.get_last_price())
        
        # Process in batches
        if len(ticker_batch) >= batch_size:
            # Calculate batch statistics
            avg_price = sum(ticker_batch) / len(ticker_batch)
            min_price = min(ticker_batch)
            max_price = max(ticker_batch)
            
            print(f"Batch - Avg: ${avg_price:.2f}, Min: ${min_price:.2f}, Max: ${max_price:.2f}")
            
            # Reset batch
            ticker_batch = []

asyncio.run(batch_processing())
```

## Configuration Options

### WebSocket Settings
```python
# Configure WebSocket connection
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "key",
        "secret": "secret",
        "websocket": {
            "ping_interval": 20,      # Ping interval in seconds
            "ping_timeout": 10,       # Ping timeout
            "max_reconnect": 5,       # Max reconnection attempts
            "reconnect_delay": 1,      # Delay between reconnections
            "compression": True,       # Enable compression
        }
    }
})
```

## Best Practices

1. **Handle Disconnections** - Implement reconnection logic
2. **Rate Limit Updates** - Don't overwhelm your application
3. **Filter Data** - Process only relevant updates
4. **Monitor Health** - Track connection status
5. **Use Async** - Leverage asyncio for performance
6. **Buffer Data** - Handle temporary network issues

---

Need more help? Check our [API patterns guide](../guides/api-patterns.md).