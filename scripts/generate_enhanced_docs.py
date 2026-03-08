#!/usr/bin/env python3
"""
Enhanced Documentation Generator for bt_api_py
===============================================

This script creates comprehensive, modern documentation that enhances the existing MkDocs setup
with interactive examples, API references, and developer guides.
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Any
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class EnhancedDocGenerator:
    """Enhanced documentation generator for bt_api_py."""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.docs_dir = project_root / "docs"
        
    def create_interactive_examples(self) -> None:
        """Create interactive code examples directory."""
        examples_dir = self.docs_dir / "examples"
        examples_dir.mkdir(exist_ok=True)
        
        # Create gallery of examples
        gallery_content = """# Interactive Code Examples

Welcome to the bt_api_py interactive code gallery. These examples demonstrate common trading scenarios and best practices.

## 📊 Market Data Examples

### Real-time Price Monitoring
```python
--8<-- "examples/market_data/real_time_prices.py"
```

### Multi-Exchange Arbitrage Detection
```python
--8<-- "examples/market_data/arbitrage_detection.py"
```

### Technical Analysis with Multiple Timeframes
```python
--8<-- "examples/market_data/technical_analysis.py"
```

## 🔄 Trading Examples

### Simple Market Making Bot
```python
--8<-- "examples/trading/market_making.py"
```

### Grid Trading Strategy
```python
--8<-- "examples/trading/grid_trading.py"
```

### Stop-Loss/Take-Profit Manager
```python
--8<-- "examples/trading/risk_management.py"
```

## 📈 Portfolio Management

### Multi-Asset Portfolio Tracker
```python
--8<-- "examples/portfolio/portfolio_tracker.py"
```

### Rebalancing Strategy
```python
--8<-- "examples/portfolio/rebalancing.py"
```

## ⚡ Advanced Examples

### High-Frequency Data Processing
```python
--8<-- "examples/advanced/high_frequency.py"
```

### Multi-Strategy Backtesting
```python
--8<-- "examples/advanced/backtesting.py"
```

### WebSocket Event Chain
```python
--8<-- "examples/advanced/event_chain.py"
```

## 🚀 Deployment Examples

### Docker Configuration
```yaml
--8<-- "examples/deployment/docker-compose.yml"
```

### Kubernetes Deployment
```yaml
--8<-- "examples/deployment/kubernetes.yml"
```

### Cloud Function Integration
```python
--8<-- "examples/deployment/cloud_function.py"
```

---

## Running Examples

### Prerequisites
```bash
pip install bt_api_py[all]  # Install with all optional dependencies
```

### Environment Setup
```bash
cp .env.example .env
# Edit .env with your API credentials
```

### Run Examples
```bash
# Individual examples
python examples/market_data/real_time_prices.py

# All examples with test data
python -m examples.run_all --testnet
```

---

## 🧪 Test Mode

All examples support test mode with paper trading:

```bash
export BT_API_TESTNET=true
python examples/trading/market_making.py
```

## 📚 Learn More

- {doc}`../getting-started/quickstart` - Get started basics
- {doc}`../guides/api-patterns` - Common usage patterns
- {doc}`../reference/api-overview` - Complete API reference
"""
        
        gallery_file = examples_dir / "gallery.md"
        gallery_file.write_text(gallery_content)
        logger.info(f"Created examples gallery: {gallery_file}")

    def create_developer_guides(self) -> None:
        """Create comprehensive developer guides."""
        guides_dir = self.docs_dir / "guides"
        guides_dir.mkdir(exist_ok=True)
        
        # API Patterns Guide
        api_patterns = """# API Usage Patterns & Best Practices

This guide covers common patterns and best practices for using bt_api_py effectively.

## 🔌 Connection Management

### Reuse Connections for Performance

```python
# ❌ Bad: Creates new connection each time
def get_price_inefficient(symbol):
    api = BtApi(exchange_kwargs=config)
    ticker = api.get_tick("BINANCE___SPOT", symbol)
    return ticker.get_last_price()

# ✅ Good: Reuse connection
class TradingBot:
    def __init__(self, config):
        self.api = BtApi(exchange_kwargs=config)
        
    def get_price(self, symbol):
        ticker = self.api.get_tick("BINANCE___SPOT", symbol)
        return ticker.get_last_price()
```

### Connection Pooling

```python
# Configure connection pool for high-frequency operations
api = BtApi(
    exchange_kwargs={
        "BINANCE___SPOT": {
            "api_key": "key",
            "secret": "secret",
            "pool_connections": 10,  # Pool size
            "pool_maxsize": 20,      # Max connections
            "timeout": 30,           # Request timeout
        }
    }
)
```

## 📊 Data Patterns

### Batch Operations

```python
# ✅ Efficient: Batch multiple requests
symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
tickers = api.get_multiple_ticks("BINANCE___SPOT", symbols)

# Process in parallel
def process_ticker(symbol, ticker):
    ticker.init_data()
    return {
        "symbol": symbol,
        "price": ticker.get_last_price(),
        "volume": ticker.get_volume()
    }

with ThreadPoolExecutor(max_workers=5) as executor:
    results = list(executor.map(
        process_ticker, 
        symbols, 
        tickers.values()
    ))
```

### Streaming Data Processing

```python
class DataProcessor:
    def __init__(self):
        self.price_history = {}
        self.sma_window = 20
        
    async def process_tickers(self):
        async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
            ticker.init_data()
            symbol = ticker.get_symbol()
            price = ticker.get_last_price()
            
            # Update price history
            if symbol not in self.price_history:
                self.price_history[symbol] = []
            
            self.price_history[symbol].append(price)
            
            # Keep only recent prices
            if len(self.price_history[symbol]) > self.sma_window:
                self.price_history[symbol].pop(0)
            
            # Calculate SMA
            if len(self.price_history[symbol]) >= self.sma_window:
                sma = sum(self.price_history[symbol]) / self.sma_window
                print(f"{symbol}: Price={price:.2f}, SMA={sma:.2f}")

# Usage
processor = DataProcessor()
asyncio.run(processor.process_tickers())
```

## 🔄 Error Handling Patterns

### Circuit Breaker Pattern

```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        
    async def call(self, func, *args, **kwargs):
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "HALF_OPEN"
            else:
                raise Exception("Circuit breaker is OPEN")
        
        try:
            result = await func(*args, **kwargs)
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
                self.failure_count = 0
            return result
            
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
            
            raise e

# Usage
circuit_breaker = CircuitBreaker()

async def safe_get_ticker(symbol):
    return await circuit_breaker.call(
        api.async_get_tick, "BINANCE___SPOT", symbol
    )
```

### Retry with Exponential Backoff

```python
import random
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((RateLimitError, RequestError))
)
async def get_ticker_with_retry(symbol: str):
    return await api.async_get_tick("BINANCE___SPOT", symbol)
```

## 🚀 Performance Optimization

### Async Processing Pipeline

```python
class AsyncTradingPipeline:
    def __init__(self):
        self.api = BtApi()
        self.price_queue = asyncio.Queue(maxsize=1000)
        self.signal_queue = asyncio.Queue(maxsize=1000)
        
    async def price_feeder(self, symbols):
        """Feed price data to pipeline."""
        tasks = [
            self._feed_symbol(symbol) for symbol in symbols
        ]
        await asyncio.gather(*tasks)
        
    async def _feed_symbol(self, symbol):
        async for ticker in self.api.stream_ticker("BINANCE___SPOT", symbol):
            ticker.init_data()
            await self.price_queue.put(ticker)
            
    async def strategy_processor(self):
        """Process prices and generate signals."""
        while True:
            ticker = await self.price_queue.get()
            signal = self.generate_signal(ticker)
            if signal:
                await self.signal_queue.put(signal)
                
    async def order_executor(self):
        """Execute trading signals."""
        while True:
            signal = await self.signal_queue.get()
            await self.execute_signal(signal)
            
    def generate_signal(self, ticker):
        """Generate trading signal from ticker data."""
        # Your strategy logic here
        price = ticker.get_last_price()
        # Simple example: buy on dip, sell on pump
        if price < 45000:
            return {"action": "buy", "symbol": ticker.get_symbol()}
        elif price > 55000:
            return {"action": "sell", "symbol": ticker.get_symbol()}
        return None
        
    async def execute_signal(self, signal):
        """Execute trading signal."""
        try:
            order = await api.async_make_order(
                exchange_name="BINANCE___SPOT",
                symbol=signal["symbol"],
                volume=0.001,
                order_type="market",
                side=signal["action"]
            )
            print(f"Executed {signal['action']} order: {order.get_order_id()}")
        except Exception as e:
            print(f"Order execution failed: {e}")

# Usage
pipeline = AsyncTradingPipeline()

async def run_pipeline():
    tasks = [
        pipeline.price_feeder(["BTCUSDT", "ETHUSDT"]),
        pipeline.strategy_processor(),
        pipeline.order_executor()
    ]
    await asyncio.gather(*tasks)

asyncio.run(run_pipeline())
```

## 🔒 Security Patterns

### Secure Credential Management

```python
import os
from cryptography.fernet import Fernet

class SecureConfig:
    def __init__(self):
        self.key = os.getenv("BT_API_ENCRYPTION_KEY")
        if not self.key:
            self.key = Fernet.generate_key()
            os.environ["BT_API_ENCRYPTION_KEY"] = self.key.decode()
        
        self.cipher = Fernet(self.key)
        
    def encrypt_credentials(self, api_key: str, secret: str) -> tuple:
        """Encrypt API credentials."""
        encrypted_key = self.cipher.encrypt(api_key.encode())
        encrypted_secret = self.cipher.encrypt(secret.encode())
        return encrypted_key, encrypted_secret
        
    def decrypt_credentials(self, encrypted_key: bytes, encrypted_secret: bytes) -> tuple:
        """Decrypt API credentials."""
        api_key = self.cipher.decrypt(encrypted_key).decode()
        secret = self.cipher.decrypt(encrypted_secret).decode()
        return api_key, secret

# Usage
secure_config = SecureConfig()

# Store encrypted credentials
enc_key, enc_secret = secure_config.encrypt_credentials("your_key", "your_secret")

# Use decrypted credentials
api_key, secret = secure_config.decrypt_credentials(enc_key, enc_secret)
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": api_key,
        "secret": secret
    }
})
```

### IP Whitelisting & Rate Limiting

```python
class RateLimitedAPI:
    def __init__(self, base_api, requests_per_second=10):
        self.api = base_api
        self.rate_limiter = asyncio.Semaphore(requests_per_second)
        
    async def get_tick(self, exchange_name: str, symbol: str):
        async with self.rate_limiter:
            return await self.api.async_get_tick(exchange_name, symbol)

# Wrap API with rate limiting
rate_limited_api = RateLimitedAPI(api, requests_per_second=5)
```

## 📈 Monitoring & Logging

### Performance Metrics

```python
import time
from dataclasses import dataclass
from typing import Dict, List

@dataclass
class PerformanceMetrics:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_response_time: float = 0
    avg_response_time: float = 0
    
    def update(self, response_time: float, success: bool):
        self.total_requests += 1
        self.total_response_time += response_time
        self.avg_response_time = self.total_response_time / self.total_requests
        
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
            
    @property
    def success_rate(self) -> float:
        return self.successful_requests / self.total_requests if self.total_requests > 0 else 0

class MonitoredAPI:
    def __init__(self, base_api):
        self.api = base_api
        self.metrics = PerformanceMetrics()
        
    async def get_tick(self, exchange_name: str, symbol: str):
        start_time = time.time()
        success = False
        
        try:
            ticker = await self.api.async_get_tick(exchange_name, symbol)
            success = True
            return ticker
            
        except Exception as e:
            print(f"API call failed: {e}")
            raise
            
        finally:
            response_time = time.time() - start_time
            self.metrics.update(response_time, success)
            
            # Log metrics every 100 requests
            if self.metrics.total_requests % 100 == 0:
                self.log_metrics()
                
    def log_metrics(self):
        metrics_text = f"""
Performance Metrics:
- Total Requests: {self.metrics.total_requests}
- Success Rate: {self.metrics.success_rate:.2%}
- Avg Response Time: {self.metrics.avg_response_time:.3f}s
- Failed Requests: {self.metrics.failed_requests}
        """
        print(metrics_text)

# Usage
monitored_api = MonitoredAPI(api)
```

## 🧪 Testing Patterns

### Mock Exchange for Testing

```python
class MockExchange:
    def __init__(self):
        self.prices = {
            "BTCUSDT": 45000.0,
            "ETHUSDT": 3000.0,
        }
        
    def get_tick(self, exchange_name: str, symbol: str):
        """Mock ticker data."""
        ticker = MagicMock()
        ticker.get_last_price.return_value = self.prices.get(symbol, 0)
        ticker.get_volume.return_value = 1000000.0
        ticker.get_symbol.return_value = symbol
        return ticker
        
    def make_order(self, **kwargs):
        """Mock order creation."""
        order = MagicMock()
        order.get_order_id.return_value = f"order_{int(time.time())}"
        order.get_status.return_value = "filled"
        return order

# Test with mock
def test_trading_strategy():
    mock_exchange = MockExchange()
    api = BtApi()
    
    # Patch the real API with mock
    api.get_tick = mock_exchange.get_tick
    api.make_order = mock_exchange.make_order
    
    # Test strategy
    strategy = TradingStrategy(api)
    results = strategy.run()
    
    assert results["total_orders"] > 0
    assert results["success_rate"] > 0.5
```

## 📚 Resources

- {doc}`error-handling` - Detailed error handling
- {doc}`performance-optimization` - Performance tuning
- {doc}`security-best-practices` - Security guidelines
- {doc}`testing-strategies` - Testing approaches

## 🤝 Contributing

Have a pattern to share? Please contribute to the documentation!

```bash
# Add your example
git checkout -b add-new-pattern
# Add your code to docs/guides/examples/
# Update documentation
# Submit PR
```
"""
        
        patterns_file = guides_dir / "api-patterns.md"
        patterns_file.write_text(api_patterns)
        logger.info(f"Created API patterns guide: {patterns_file}")

    def create_exchange_guides(self) -> None:
        """Create comprehensive exchange-specific guides."""
        exchanges_dir = self.docs_dir / "exchanges"
        exchanges_dir.mkdir(exist_ok=True)
        
        # Binance comprehensive guide
        binance_guide = """# Binance Integration Guide

Complete guide for integrating with Binance exchange using bt_api_py.

## 🚀 Quick Setup

### 1. Get API Credentials

1. Go to [Binance API Management](https://www.binance.com/en/my/settings/api-management)
2. Create new API key
3. Configure IP restrictions (recommended)
4. Enable required permissions:
   - **Spot & Margin Trading**: Enable reading, spot & margin trading
   - **Futures Trading**: Enable reading, futures trading
   - **Withdrawals**: Enable if needed

### 2. Configure bt_api_py

```python
from bt_api_py import BtApi

# Production configuration
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "your_binance_api_key",
        "secret": "your_binance_secret",
        "testnet": False,  # Set to True for testnet
    }
})

# Testnet configuration
testnet_api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "your_testnet_api_key",
        "secret": "your_testnet_secret",
        "testnet": True,
        "base_url": "https://testnet.binance.vision"
    }
})
```

## 📊 Supported Products

| Product | Exchange Code | WebSocket | Testnet |
|---------|---------------|------------|---------|
| **Spot Trading** | `BINANCE___SPOT` | ✅ | ✅ |
| **USDM Futures** | `BINANCE___USDM_SWAP` | ✅ | ✅ |
| **COINM Futures** | `BINANCE___COINM_SWAP` | ✅ | ✅ |
| **Options** | `BINANCE___OPTION` | ✅ | ❌ |
| **Margin Trading** | `BINANCE___MARGIN` | ✅ | ✅ |

## 🔧 API Endpoints

### REST API Base URLs
- **Production**: `https://api.binance.com`
- **Testnet**: `https://testnet.binance.vision`
- **USDM Futures**: `https://fapi.binance.com`
- **COINM Futures**: `https://dapi.binance.com`

### WebSocket URLs
- **Spot**: `wss://stream.binance.com:9443/ws`
- **USDM Futures**: `wss://fstream.binance.com/ws`
- **COINM Futures**: `wss://dstream.binance.com/ws`

## 📖 Usage Examples

### Spot Trading

```python
# Get ticker data
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
print(f"BTC Price: ${ticker.get_last_price():.2f}")

# Get order book
depth = api.get_depth("BINANCE___SPOT", "BTCUSDT", limit=10)
depth.init_data()
print(f"Best Bid: {depth.get_bids()[0]}")
print(f"Best Ask: {depth.get_asks()[0]}")

# Place market order
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    order_type="market",
    side="buy"
)
order.init_data()
print(f"Order ID: {order.get_order_id()}")
print(f"Executed Price: {order.get_avg_price()}")
```

### Futures Trading

```python
# Get futures ticker
ticker = api.get_tick("BINANCE___USDM_SWAP", "BTCUSDT")
ticker.init_data()
print(f"Futures Price: ${ticker.get_last_price():.2f}")
print(f"Funding Rate: {ticker.get_funding_rate():.4f}")

# Get position information
position = api.get_position("BINANCE___USDM_SWAP", "BTCUSDT")
position.init_data()
print(f"Position Size: {position.get_position_amt()}")
print(f"Unrealized PnL: ${position.get_unrealized_pnl():.2f}")

# Place futures order with leverage
api.set_leverage("BINANCE___USDM_SWAP", "BTCUSDT", 10)  # 10x leverage

order = api.make_order(
    exchange_name="BINANCE___USDM_SWAP",
    symbol="BTCUSDT",
    volume=0.01,
    order_type="limit",
    side="buy",
    price=44000,
    reduce_only=False
)
```

### WebSocket Streaming

```python
import asyncio

async def stream_bitcoin():
    # Stream spot ticker
    async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
        ticker.init_data()
        print(f"Spot BTC: ${ticker.get_last_price():.2f}")

async def stream_futures():
    # Stream futures order book
    async for depth in api.stream_depth("BINANCE___USDM_SWAP", "BTCUSDT"):
        depth.init_data()
        best_bid = depth.get_bids()[0][0] if depth.get_bids() else 0
        best_ask = depth.get_asks()[0][0] if depth.get_asks() else 0
        print(f"Futures BTC: Bid={best_bid}, Ask={best_ask}")

# Run multiple streams
async def main():
    await asyncio.gather(
        stream_bitcoin(),
        stream_futures()
    )

asyncio.run(main())
```

## ⚠️ Rate Limits

### API Weight Limits

| Endpoint | Weight | Limit (per minute) |
|----------|--------|-------------------|
| GET /api/v3/ticker/price | 1 | 1200 |
| GET /api/v3/depth | 1 | 100 |
| POST /api/v3/order | 1 | 100 |
| GET /api/v3/account | 10 | 10 |
| WebSocket connections | 1 | 300 |

### Best Practices for Rate Limiting

```python
import asyncio
from bt_api_py.exceptions import RateLimitError

class BinanceRateLimiter:
    def __init__(self):
        self.request_times = []
        self.weight_used = 0
        self.weight_limit = 1200  # Default limit
        
    async def check_rate_limit(self, weight=1):
        """Check if we can make a request."""
        now = time.time()
        
        # Remove old requests (older than 1 minute)
        self.request_times = [t for t in self.request_times if now - t < 60]
        
        # Check weight limit
        if self.weight_used + weight > self.weight_limit:
            wait_time = 60 - (now - self.request_times[0]) if self.request_times else 1
            print(f"Rate limit reached. Waiting {wait_time:.1f} seconds...")
            await asyncio.sleep(wait_time)
            return await self.check_rate_limit(weight)
        
        self.request_times.append(now)
        self.weight_used += weight
        return True

# Usage
rate_limiter = BinanceRateLimiter()

async def safe_get_ticker(symbol):
    await rate_limiter.check_rate_limit(weight=1)
    return await api.async_get_tick("BINANCE___SPOT", symbol)
```

## 🔍 Error Handling

### Common Binance Errors

```python
from bt_api_py.exceptions import (
    BinanceError,
    InsufficientBalanceError,
    InvalidSymbolError,
    OrderError
)

def handle_binance_error(error):
    """Handle Binance-specific errors."""
    
    if isinstance(error, InsufficientBalanceError):
        print("Insufficient balance. Check available funds.")
        
    elif isinstance(error, InvalidSymbolError):
        print("Invalid trading symbol. Check symbol format.")
        
    elif isinstance(error, OrderError):
        if "LOT_SIZE" in str(error):
            print("Order quantity too small or invalid precision.")
        elif "PRICE" in str(error):
            print("Order price invalid or outside allowed range.")
        else:
            print(f"Order error: {error}")
            
    elif isinstance(error, BinanceError):
        if "IP" in str(error):
            print("IP restriction error. Check API settings.")
        elif "signature" in str(error):
            print("Invalid signature. Check API credentials.")
        else:
            print(f"Binance error: {error}")

# Usage with error handling
try:
    order = api.make_order(
        exchange_name="BINANCE___SPOT",
        symbol="BTCUSDT",
        volume=0.001,
        price=45000,
        order_type="limit",
        side="buy"
    )
    
except Exception as e:
    handle_binance_error(e)
```

## 📈 Advanced Features

### Conditional Orders

```python
# Create OCO (One-Cancels-Other) order
oco_order = api.make_oco_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    side="sell",
    quantity=0.1,
    price=50000,          # Limit order price
    stop_price=48000,      # Stop loss trigger price
    stop_limit_price=47500 # Stop limit price
)

# Query OCO order status
oco_info = api.query_oco_order("BINANCE___SPOT", oco_order.get_oco_order_id())
```

### Futures Settings

```python
# Set leverage and margin type
api.set_leverage("BINANCE___USDM_SWAP", "BTCUSDT", 10)
api.set_margin_type("BINANCE___USDM_SWAP", "BTCUSDT", "ISOLATED")

# Set position mode (hedge vs one-way)
api.set_position_mode("BINANCE___USDM_SWAP", hedge_mode=True)

# Get funding rate history
funding_history = api.get_funding_rate_history(
    "BINANCE___USDM_SWAP",
    "BTCUSDT",
    limit=10
)
```

### Custom Client Order ID

```python
# Place order with custom client ID
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    order_type="limit",
    side="buy",
    price=45000,
    new_client_order_id="my_custom_order_123"
)

# Query by client ID
order = api.query_order(
    "BINANCE___SPOT",
    orig_client_order_id="my_custom_order_123"
)
```

## 🧪 Testing & Development

### Testnet Configuration

```python
# Testnet API setup
testnet_config = {
    "BINANCE___SPOT": {
        "api_key": os.getenv("BINANCE_TESTNET_API_KEY"),
        "secret": os.getenv("BINANCE_TESTNET_SECRET"),
        "testnet": True,
        "base_url": "https://testnet.binance.vision"
    }
}

test_api = BtApi(exchange_kwargs=testnet_config)

# Test with small amounts
def test_trading_flow():
    try:
        # Get account
        account = test_api.get_account("BINANCE___SPOT")
        account.init_data()
        
        # Get test balance
        balance = account.get_balance("USDT")
        print(f"Test USDT balance: {balance}")
        
        # Place small test order
        order = test_api.make_order(
            exchange_name="BINANCE___SPOT",
            symbol="BTCUSDT",
            volume=0.001,
            price=30000,  # Low price for testnet
            order_type="limit",
            side="buy"
        )
        
        print(f"Test order placed: {order.get_order_id()}")
        
        # Cancel the order
        test_api.cancel_order("BINANCE___SPOT", order.get_order_id())
        print("Test order cancelled")
        
    except Exception as e:
        print(f"Test failed: {e}")

# Run test
test_trading_flow()
```

## 📚 Resources

- [Binance API Documentation](https://binance-docs.github.io/apidocs/)
- [Binance Spot API](https://binance-docs.github.io/apidocs/spot/en/)
- [Binance Futures API](https://binance-docs.github.io/apidocs/futures/en/)
- [Testnet](https://testnet.binance.vision/)
- [Rate Limits](https://binance-docs.github.io/apidocs/spot/en/#limits)

## 🆘 Troubleshooting

### Common Issues

1. **Invalid API Key**
   - Check API key and secret are correct
   - Ensure IP restrictions allow your access
   - Verify required permissions are enabled

2. **Signature Error**
   - Check system time is synchronized
   - Verify API secret is correct
   - Ensure request parameters are properly encoded

3. **Order Rejection**
   - Check symbol format (e.g., BTCUSDT, not BTC-USDT)
   - Verify order quantity meets lot size requirements
   - Check price is within allowed range

4. **WebSocket Disconnection**
   - Implement reconnection logic
   - Check network stability
   - Verify WebSocket endpoint URLs

### Debug Mode

```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# API with debug mode
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "key",
        "secret": "secret",
        "debug": True,  # Enable debug mode
    }
})
```

---

Need help? Check our {doc}`../../support/faq` or open an issue on GitHub.
"""
        
        binance_file = exchanges_dir / "binance.md"
        binance_file.write_text(binance_guide)
        logger.info(f"Created Binance guide: {binance_file}")

    def create_api_reference(self) -> None:
        """Create comprehensive API reference."""
        reference_dir = self.docs_dir / "reference"
        reference_dir.mkdir(exist_ok=True)
        
        # Core API reference
        core_api_content = """# Core API Reference

Complete reference for the core bt_api_py API classes and methods.

## BtApi

The main unified API interface for all exchanges.

### Constructor

```python
def __init__(
    self,
    exchange_kwargs: Optional[Dict[str, Any]] = None,
    enable_websocket: bool = True,
    enable_cache: bool = True,
    cache_ttl: int = 300,
    timeout: int = 30,
    retry_count: int = 3,
    retry_delay: float = 1.0,
) -> None:
    """
    Initialize bt_api_py with exchange configurations.
    
    Args:
        exchange_kwargs: Dictionary mapping exchange names to their configurations
        enable_websocket: Enable WebSocket support
        enable_cache: Enable response caching
        cache_ttl: Cache time-to-live in seconds
        timeout: Request timeout in seconds
        retry_count: Number of retries for failed requests
        retry_delay: Delay between retries in seconds
    """
```

### Market Data Methods

#### get_tick()

```python
def get_tick(
    self,
    exchange_name: str,
    symbol: str,
    **kwargs: Any
) -> TickerData:
    """
    Get ticker information for a trading pair.
    
    Args:
        exchange_name: Exchange identifier (e.g., "BINANCE___SPOT")
        symbol: Trading symbol (e.g., "BTCUSDT")
        **kwargs: Additional exchange-specific parameters
        
    Returns:
        TickerData object with market information
        
    Raises:
        ExchangeNotFoundError: Exchange not registered
        InvalidSymbolError: Invalid trading symbol
        RequestError: API request failed
    """
```

**Example:**
```python
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
price = ticker.get_last_price()
volume = ticker.get_volume()
change = ticker.get_price_change()

print(f"BTC: ${price:.2f}, Vol: {volume:,.0f}, Change: {change:.2f}%")
```

#### get_depth()

```python
def get_depth(
    self,
    exchange_name: str,
    symbol: str,
    limit: int = 100,
    **kwargs: Any
) -> OrderBookData:
    """
    Get order book depth for a trading pair.
    
    Args:
        exchange_name: Exchange identifier
        symbol: Trading symbol
        limit: Number of price levels (default: 100)
        **kwargs: Additional exchange-specific parameters
        
    Returns:
        OrderBookData object with bid/ask levels
        
    Raises:
        RequestError: API request failed
        InvalidSymbolError: Invalid trading symbol
    """
```

**Example:**
```python
depth = api.get_depth("BINANCE___SPOT", "BTCUSDT", limit=10)
depth.init_data()

best_bid = depth.get_bids()[0]  # [price, quantity]
best_ask = depth.get_asks()[0]
spread = best_ask[0] - best_bid[0]

print(f"Best Bid: ${best_bid[0]:.2f}")
print(f"Best Ask: ${best_ask[0]:.2f}")
print(f"Spread: ${spread:.2f}")
```

#### get_kline()

```python
def get_kline(
    self,
    exchange_name: str,
    symbol: str,
    interval: str,
    limit: int = 500,
    **kwargs: Any
) -> List[BarData]:
    """
    Get candlestick/kline data.
    
    Args:
        exchange_name: Exchange identifier
        symbol: Trading symbol
        interval: Time interval (1m, 5m, 15m, 1h, 4h, 1d, etc.)
        limit: Number of candles (default: 500)
        **kwargs: Additional exchange-specific parameters
        
    Returns:
        List of BarData objects
        
    Raises:
        RequestError: API request failed
        InvalidSymbolError: Invalid trading symbol
    """
```

**Example:**
```python
bars = api.get_kline("BINANCE___SPOT", "BTCUSDT", "1h", limit=24)
for bar in bars:
    bar.init_data()
    print(f"{bar.get_timestamp()}: O={bar.get_open()}, H={bar.get_high()}, L={bar.get_low()}, C={bar.get_close()}")
```

### Trading Methods

#### make_order()

```python
def make_order(
    self,
    exchange_name: str,
    symbol: str,
    volume: float,
    price: Optional[float] = None,
    order_type: str = "market",
    side: str = "buy",
    **kwargs: Any
) -> OrderData:
    """
    Place a trading order.
    
    Args:
        exchange_name: Exchange identifier
        symbol: Trading symbol
        volume: Order quantity
        price: Order price (required for limit orders)
        order_type: Order type ("market", "limit", "stop", etc.)
        side: Order side ("buy" or "sell")
        **kwargs: Additional exchange-specific parameters
        
    Returns:
        OrderData object with order information
        
    Raises:
        OrderError: Order placement failed
        InsufficientBalanceError: Insufficient balance
        RequestError: API request failed
    """
```

**Example:**
```python
# Market buy order
market_order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    order_type="market",
    side="buy"
)

# Limit sell order
limit_order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=50000,
    order_type="limit",
    side="sell"
)
```

#### cancel_order()

```python
def cancel_order(
    self,
    exchange_name: str,
    order_id: str,
    symbol: Optional[str] = None,
    **kwargs: Any
) -> OrderData:
    """
    Cancel an existing order.
    
    Args:
        exchange_name: Exchange identifier
        order_id: Order ID to cancel
        symbol: Trading symbol (required by some exchanges)
        **kwargs: Additional exchange-specific parameters
        
    Returns:
        OrderData object with cancellation status
        
    Raises:
        OrderNotFoundError: Order not found
        OrderError: Cancellation failed
        RequestError: API request failed
    """
```

#### query_order()

```python
def query_order(
    self,
    exchange_name: str,
    order_id: Optional[str] = None,
    symbol: Optional[str] = None,
    orig_client_order_id: Optional[str] = None,
    **kwargs: Any
) -> OrderData:
    """
    Query order status and details.
    
    Args:
        exchange_name: Exchange identifier
        order_id: Exchange order ID
        symbol: Trading symbol
        orig_client_order_id: Client-side order ID
        **kwargs: Additional exchange-specific parameters
        
    Returns:
        OrderData object with current order status
        
    Raises:
        OrderNotFoundError: Order not found
        RequestError: API request failed
    """
```

### Account Methods

#### get_balance()

```python
def get_balance(
    self,
    exchange_name: str,
    asset: Optional[str] = None,
    **kwargs: Any
) -> Union[BalanceData, Dict[str, BalanceData]]:
    """
    Get account balance information.
    
    Args:
        exchange_name: Exchange identifier
        asset: Specific asset to query (optional)
        **kwargs: Additional exchange-specific parameters
        
    Returns:
        BalanceData object or dictionary of assets
        
    Raises:
        RequestError: API request failed
        AuthenticationError: Authentication failed
    """
```

**Example:**
```python
# Get all balances
all_balances = api.get_balance("BINANCE___SPOT")

# Get specific asset balance
usdt_balance = api.get_balance("BINANCE___SPOT", "USDT")
usdt_balance.init_data()
available = usdt_balance.get_free_balance()
locked = usdt_balance.get_locked_balance()

print(f"USDT Available: {available}")
print(f"USDT Locked: {locked}")
```

#### get_account()

```python
def get_account(
    self,
    exchange_name: str,
    **kwargs: Any
) -> AccountData:
    """
    Get complete account information.
    
    Args:
        exchange_name: Exchange identifier
        **kwargs: Additional exchange-specific parameters
        
    Returns:
        AccountData object with account details
        
    Raises:
        RequestError: API request failed
        AuthenticationError: Authentication failed
    """
```

### WebSocket Methods

#### stream_ticker()

```python
async def stream_ticker(
    self,
    exchange_name: str,
    symbol: str,
    **kwargs: Any
) -> AsyncIterator[TickerData]:
    """
    Stream real-time ticker data.
    
    Args:
        exchange_name: Exchange identifier
        symbol: Trading symbol
        **kwargs: Additional exchange-specific parameters
        
    Yields:
        TickerData objects with real-time data
        
    Raises:
        WebSocketError: WebSocket connection failed
        ExchangeNotFoundError: Exchange not supported
    """
```

**Example:**
```python
import asyncio

async def monitor_prices():
    async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
        ticker.init_data()
        price = ticker.get_last_price()
        volume = ticker.get_volume()
        print(f"BTC: ${price:.2f}, Volume: {volume:,.0f}")

asyncio.run(monitor_prices())
```

#### stream_depth()

```python
async def stream_depth(
    self,
    exchange_name: str,
    symbol: str,
    depth_levels: int = 10,
    **kwargs: Any
) -> AsyncIterator[OrderBookData]:
    """
    Stream real-time order book updates.
    
    Args:
        exchange_name: Exchange identifier
        symbol: Trading symbol
        depth_levels: Number of price levels
        **kwargs: Additional exchange-specific parameters
        
    Yields:
        OrderBookData objects with order book updates
        
    Raises:
        WebSocketError: WebSocket connection failed
    """
```

#### stream_trades()

```python
async def stream_trades(
    self,
    exchange_name: str,
    symbol: str,
    **kwargs: Any
) -> AsyncIterator[TradeData]:
    """
    Stream real-time trade data.
    
    Args:
        exchange_name: Exchange identifier
        symbol: Trading symbol
        **kwargs: Additional exchange-specific parameters
        
    Yields:
        TradeData objects with trade information
        
    Raises:
        WebSocketError: WebSocket connection failed
    """
```

### Multi-Exchange Methods

#### get_all_ticks()

```python
def get_all_ticks(
    self,
    symbol: str,
    exchanges: Optional[List[str]] = None,
    **kwargs: Any
) -> Dict[str, TickerData]:
    """
    Get ticker data from all configured exchanges.
    
    Args:
        symbol: Trading symbol
        exchanges: List of exchanges (default: all configured)
        **kwargs: Additional exchange-specific parameters
        
    Returns:
        Dictionary mapping exchange names to TickerData objects
        
    Raises:
        RequestError: API request failed
    """
```

**Example:**
```python
# Compare BTC price across exchanges
all_btc = api.get_all_ticks("BTCUSDT")

for exchange, ticker in all_btc.items():
    ticker.init_data()
    price = ticker.get_last_price()
    print(f"{exchange}: ${price:.2f}")

# Find arbitrage opportunities
prices = {ex: t.get_last_price() for ex, t in all_btc.items()}
min_price = min(prices.values())
max_price = max(prices.values())

if max_price - min_price > 100:  # $100 spread
    print("Potential arbitrage opportunity!")
```

### Async Methods

All synchronous methods have async counterparts:

```python
# Async versions
async def async_get_tick(...) -> TickerData:
async def async_make_order(...) -> OrderData:
async def async_get_balance(...) -> BalanceData:
async def async_cancel_order(...) -> OrderData:
async def async_query_order(...) -> OrderData:
```

**Example:**
```python
import asyncio

async def async_trading_example():
    # Get ticker asynchronously
    ticker = await api.async_get_tick("BINANCE___SPOT", "BTCUSDT")
    ticker.init_data()
    
    # Place order asynchronously
    order = await api.async_make_order(
        exchange_name="BINANCE___SPOT",
        symbol="BTCUSDT",
        volume=0.001,
        price=45000,
        order_type="limit",
        side="buy"
    )
    
    print(f"Order placed: {order.get_order_id()}")

asyncio.run(async_trading_example())
```

## ExchangeRegistry

Registry for managing exchange feeds and creating instances.

### Class Methods

#### register_feed()

```python
@classmethod
def register_feed(
    cls,
    exchange_name: str,
    feed_class: type
) -> None:
    """
    Register a new exchange feed class.
    
    Args:
        exchange_name: Exchange identifier
        feed_class: Feed class to register
        
    Example:
        ExchangeRegistry.register_feed("MY_EXCHANGE___SPOT", MyExchangeFeed)
    """
```

#### create_feed()

```python
@classmethod
def create_feed(
    cls,
    exchange_name: str,
    data_queue: Optional[Any] = None,
    **kwargs: Any
) -> Any:
    """
    Create an instance of a registered feed.
    
    Args:
        exchange_name: Exchange identifier
        data_queue: Queue for data processing
        **kwargs: Additional parameters for feed constructor
        
    Returns:
        Feed instance
        
    Raises:
        ExchangeNotFoundError: Exchange not registered
    """
```

#### get_exchange_names()

```python
@classmethod
def get_exchange_names(cls) -> List[str]:
    """
    Get list of all registered exchange names.
    
    Returns:
        List of exchange identifiers
    """
```

## EventBus

Event system for pub/sub communication.

### Methods

#### on()

```python
def on(
    self,
    event_type: str,
    callback: Callable[[Any], None]
) -> None:
    """
    Subscribe to an event type.
    
    Args:
        event_type: Event type to subscribe to
        callback: Callback function to handle events
        
    Example:
        bus.on("ticker", lambda data: print(f"Received: {data}"))
    """
```

#### emit()

```python
def emit(
    self,
    event_type: str,
    data: Any
) -> None:
    """
    Emit an event to all subscribers.
    
    Args:
        event_type: Event type
        data: Event data
        
    Example:
        bus.emit("order_filled", order_data)
    """
```

#### off()

```python
def off(
    self,
    event_type: str,
    callback: Optional[Callable[[Any], None]] = None
) -> None:
    """
    Unsubscribe from events.
    
    Args:
        event_type: Event type to unsubscribe from
        callback: Specific callback to remove (None removes all)
    """
```

## Data Containers

Standardized data containers for consistent API across exchanges.

### TickerData

Market ticker information.

```python
class TickerData:
    def get_symbol(self) -> str:
        """Get trading symbol."""
        
    def get_last_price(self) -> float:
        """Get last trade price."""
        
    def get_bid_price(self) -> float:
        """Get current best bid price."""
        
    def get_ask_price(self) -> float:
        """Get current best ask price."""
        
    def get_volume(self) -> float:
        """Get 24h trading volume."""
        
    def get_price_change(self) -> float:
        """Get 24h price change percentage."""
        
    def get_high_price(self) -> float:
        """Get 24h high price."""
        
    def get_low_price(self) -> float:
        """Get 24h low price."""
        
    def get_timestamp(self) -> int:
        """Get timestamp of ticker data."""
```

### OrderData

Order information and status.

```python
class OrderData:
    def get_order_id(self) -> str:
        """Get exchange order ID."""
        
    def get_client_order_id(self) -> str:
        """Get client-side order ID."""
        
    def get_symbol(self) -> str:
        """Get trading symbol."""
        
    def get_order_type(self) -> str:
        """Get order type (market, limit, etc.)."""
        
    def get_side(self) -> str:
        """Get order side (buy/sell)."""
        
    def get_quantity(self) -> float:
        """Get order quantity."""
        
    def get_price(self) -> float:
        """Get order price."""
        
    def get_filled_quantity(self) -> float:
        """Get filled quantity."""
        
    def get_avg_price(self) -> float:
        """Get average fill price."""
        
    def get_status(self) -> str:
        """Get order status."""
        
    def get_create_time(self) -> int:
        """Get order creation time."""
```

### BalanceData

Account balance information.

```python
class BalanceData:
    def get_asset(self) -> str:
        """Get asset name."""
        
    def get_free_balance(self) -> float:
        """Get available balance."""
        
    def get_locked_balance(self) -> float:
        """Get locked/locked balance."""
        
    def get_total_balance(self) -> float:
        """Get total balance."""
```

## Exception Hierarchy

All bt_api_py exceptions inherit from `BtApiError`.

### Base Exception
- `BtApiError` - Base exception for all errors

### Exchange Errors
- `ExchangeNotFoundError` - Exchange not registered
- `ExchangeConnectionError` - Connection failed
- `AuthenticationError` - Authentication failed

### Request Errors
- `RequestError` - REST request failed
- `RequestTimeoutError` - Request timed out
- `RateLimitError` - API rate limit exceeded

### Order Errors
- `OrderError` - Order operation failed
- `InsufficientBalanceError` - Insufficient balance
- `InvalidOrderError` - Invalid order parameters
- `OrderNotFoundError` - Order not found

### Data Errors
- `InvalidSymbolError` - Invalid trading symbol
- `DataParseError` - Data parsing failed

### WebSocket Errors
- `WebSocketError` - WebSocket connection failed
- `SubscribeError` - Subscription failed

### Configuration Errors
- `ConfigurationError` - Configuration error

---

## Next Steps

- {doc}`exchange-api` - Exchange-specific APIs
- {doc}`data-containers` - Complete container reference
- {doc}`websocket-api` - WebSocket streaming details
- {doc}`error-handling` - Error handling patterns
"""
        
        core_api_file = reference_dir / "core-api.md"
        core_api_file.write_text(core_api_content)
        logger.info(f"Created core API reference: {core_api_file}")

    def generate_all(self) -> None:
        """Generate all enhanced documentation."""
        logger.info("Starting enhanced documentation generation...")
        
        # Create enhanced documentation
        self.create_interactive_examples()
        self.create_developer_guides()
        self.create_exchange_guides()
        self.create_api_reference()
        
        logger.info("✅ Enhanced documentation generation complete!")
        logger.info("📖 Run 'make docs' to build the documentation site")


def main():
    """Main entry point."""
    project_root = Path(__file__).parent.parent
    
    generator = EnhancedDocGenerator(project_root)
    generator.generate_all()


if __name__ == "__main__":
    main()