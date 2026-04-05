# Troubleshooting Guide

Common issues and solutions when using bt_api_py.

## Installation Issues

### Python Version Compatibility
**Problem**: `bt_api_py requires Python 3.11 or higher`

**Solution**:
```bash
# Check your Python version
python --version

# Install Python 3.11+ (recommended using pyenv)
pyenv install 3.11.0
pyenv local 3.11.0

# Or use conda
conda create -n bt_api_py python=3.11
conda activate bt_api_py
```

### Dependency Conflicts
**Problem**: Package conflicts during installation

**Solution**:
```bash
# Install in clean virtual environment
python -m venv bt_api_env
source bt_api_env/bin/activate  # Linux/Mac
# bt_api_env\Scripts\activate  # Windows

# Install with specific versions
pip install "pandas>=2.0.0" "numpy>=1.26.0" "aiohttp>=3.9.0"
pip install bt_api_py
```

### C/C++ Extensions
**Problem**: CTP or Cython extensions fail to build

**Solution**:
```bash
# Install build dependencies
# Ubuntu/Debian
sudo apt-get install build-essential python3-dev

# CentOS/RHEL
sudo yum groupinstall "Development Tools"
sudo yum install python3-devel

# macOS
xcode-select --install

# Then reinstall
pip install --force-reinstall bt_api_py
```

## Connection Issues

### API Key Errors
**Problem**: `AuthenticationError` or `Invalid API key`

**Solutions**:
1. **Check API Key Format**:
```python
# Correct format
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "your_actual_api_key",  # Not empty string
        "secret": "your_actual_secret",    # Not empty string
    }
})
```

2. **Verify API Permissions**:
- Check exchange API console
- Enable trading permissions
- Enable reading permissions
- Check IP restrictions

3. **Testnet vs Production**:
```python
# For testnet
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "testnet_key",
        "secret": "testnet_secret",
        "testnet": True  # Important for testnet
    }
})
```

### Network Connectivity
**Problem**: `RequestError` or `Connection timeout`

**Solutions**:
1. **Check Network**:
```bash
# Test connectivity
ping api.binance.com
curl -I https://api.binance.com/api/v3/ping
```

2. **Configure Proxy**:
```python
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "key",
        "secret": "secret",
        "proxies": {
            "http": "http://your-proxy:port",
            "https": "https://your-proxy:port",
        }
    }
})
```

3. **Increase Timeout**:
```python
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "key",
        "secret": "secret",
        "timeout": 60  # Increase from default 30s
    }
})
```

## Order Issues

### Invalid Order Parameters
**Problem**: `InvalidOrderError` with LOT_SIZE or PRICE errors

**Solutions**:
1. **Check Symbol Format**:
```python
# Correct: BTCUSDT (not BTC-USDT)
symbol = "BTCUSDT"
ticker = api.get_tick("BINANCE___SPOT", symbol)
```

2. **Check Order Size**:
```python
# Get exchange info for lot size
exchange_info = api.get_exchange_info("BINANCE___SPOT")
symbol_info = exchange_info.get_symbol_info("BTCUSDT")

print(f"Min quantity: {symbol_info.min_quantity}")
print(f"Quantity precision: {symbol_info.quantity_precision}")

# Use valid size
min_qty = symbol_info.min_quantity
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=max(min_qty, 0.001),  # Ensure above minimum
    price=50000,
    order_type="limit"
)
```

3. **Check Price Ticks**:
```python
# Use current market price as reference
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
current_price = ticker.get_last_price()

# Place order near market price
order_price = current_price * 0.99  # 1% below market
```

### Insufficient Balance
**Problem**: `InsufficientBalanceError`

**Solutions**:
1. **Check Available Balance**:
```python
balance = api.get_balance("BINANCE___SPOT", "USDT")
balance.init_data()
available = balance.get_free_balance()
print(f"Available USDT: {available}")
```

2. **Account for Fees**:
```python
# Calculate required amount including fees
fee_rate = 0.001  # 0.1% fee
order_value = volume * price
required_balance = order_value * (1 + fee_rate)

if available >= required_balance:
    # Place order
    pass
```

## WebSocket Issues

### Connection Drops
**Problem**: WebSocket frequently disconnects

**Solutions**:
1. **Implement Reconnection Logic**:
```python
import asyncio
from bt_api_py.exceptions import WebSocketError

async def resilient_stream():
    max_retries = 5
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
                # Process data
                pass

        except WebSocketError as e:
            print(f"WebSocket error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                raise

asyncio.run(resilient_stream())
```

2. **Configure Keep-Alive**:
```python
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "websocket": {
            "ping_interval": 20,  # Send ping every 20s
            "ping_timeout": 10,   # Wait 10s for pong
        }
    }
})
```

### Subscription Limits
**Problem**: `SubscribeError` or rate limiting

**Solutions**:
1. **Limit Concurrent Subscriptions**:
```python
# Subscribe to reasonable number of streams
symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]  # Not too many

# Process sequentially if needed
for symbol in symbols:
    async for ticker in api.stream_ticker("BINANCE___SPOT", symbol):
        # Process one symbol at a time
        break
```

2. **Use Combined Streams**:
```python
# Some exchanges support combined streams
# Check exchange documentation for multi-stream connections
```

## Performance Issues

### Slow Response Times
**Problem**: API calls taking too long

**Solutions**:
1. **Enable Caching**:
```python
api = BtApi(
    enable_cache=True,
    cache_ttl=60  # Cache for 60 seconds
)
```

2. **Use Async for Concurrent Calls**:
```python
import asyncio

async def get_multiple_prices():
    symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]

    # Run calls concurrently
    tasks = [
        api.async_get_tick("BINANCE___SPOT", symbol)
        for symbol in symbols
    ]
    tickers = await asyncio.gather(*tasks)

    for symbol, ticker in zip(symbols, tickers):
        ticker.init_data()
        print(f"{symbol}: ${ticker.get_last_price():.2f}")

asyncio.run(get_multiple_prices())
```

3. **Use Connection Pooling**:
```python
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "pool_connections": 10,
        "pool_maxsize": 20,
    }
})
```

### Memory Usage
**Problem**: High memory usage with streaming

**Solutions**:
1. **Limit Data Retention**:
```python
class EfficientProcessor:
    def __init__(self, max_history=100):
        self.price_history = []
        self.max_history = max_history

    async def process_stream(self):
        async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
            ticker.init_data()
            price = ticker.get_last_price()

            # Add to history
            self.price_history.append(price)

            # Trim old data
            if len(self.price_history) > self.max_history:
                self.price_history.pop(0)
```

## Debugging

### Enable Debug Logging
```python
import logging

# Enable debug logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Or for specific modules
logging.getLogger('bt_api_py').setLevel(logging.DEBUG)
```

### Capture Request/Response
```python
import requests
from bt_api_py import BtApi

# Enable request debugging
import http.client as http_client
http_client.HTTPConnection.debuglevel = 1

# Create API with debug
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "key",
        "secret": "secret",
        "debug": True
    }
})
```

### Test with Known Good Values
```python
def test_basic_functionality():
    try:
        # Test public endpoint (no auth needed)
        ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
        print("✅ Basic API works")

        # Test private endpoint (auth required)
        balance = api.get_balance("BINANCE___SPOT", "USDT")
        print("✅ Authenticated API works")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

test_basic_functionality()
```

## Exchange-Specific Issues

### Binance
- **Symbol format**: Use `BTCUSDT` not `BTC-USDT`
- **Testnet**: Use separate testnet API keys
- **IP restrictions**: Configure in Binance API console

### OKX
- **Passphrase**: Required for API authentication
- **Symbol format**: Use `BTC-USDT` format
- **Demo vs Live**: Different API endpoints

### CTP
- **Login info**: Requires broker ID, account, password
- **Market data**: Separate subscription required
- **Trading hours**: Limited to Chinese market hours

### Interactive Brokers
- **Connection**: Requires IB Gateway or TWS running
- **Paper trading**: Use paper account for testing
- **Market data**: Subscribe to market data feeds

## Getting Help

### Collect Debug Information
```python
def get_debug_info():
    import bt_api_py
    import sys

    return {
        "bt_api_py_version": bt_api_py.__version__,
        "python_version": sys.version,
        "platform": sys.platform,
        "exchange_list": ExchangeRegistry.get_exchange_names(),
    }

print(get_debug_info())
```

### Report Issues

When reporting issues, include:
1. Error message and traceback
2. Minimal code to reproduce
3. Exchange and symbol used
4. API configuration (without secrets)
5. Debug information from above

### Community Resources
- **Documentation**: https://cloudquant.github.io/bt_api_py/
- **GitHub Issues**: https://github.com/cloudQuant/bt_api_py/issues
- **Discussions**: https://github.com/cloudQuant/bt_api_py/discussions
- **Email**: yunjinqi@gmail.com

---

Still having issues? Check our [API patterns guide](../guides/api-patterns.md) or [examples gallery](../examples/gallery.md).
