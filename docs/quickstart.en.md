# Quick Start

## Get Started in 5 Minutes

### BtApi Usage

`BtApi` uses `exchange_kwargs` parameter to configure one or more exchanges:

```python
from bt_api_py import BtApi

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
        "testnet": True,
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)

```bash

### Supported Exchanges

| Exchange | Code | Description |
|----------|------|-------------|
| **Binance Spot**| `BINANCE___SPOT` | Crypto spot trading |
|**Binance Perpetual**| `BINANCE___SWAP` | USDT-margined futures |
|**OKX Spot**| `OKX___SPOT` | Crypto spot trading |
|**OKX Perpetual**| `OKX___SWAP` | Perpetual swaps |
|**CTP Futures**| `CTP___FUTURE` | China futures market |
|**IB Web Stocks**| `IB_WEB___STK` | Global stock markets |
|**IB Web Futures** | `IB_WEB___FUT` | Global futures markets |

---

## Crypto Trading (Binance/OKX)

### Get Market Data

```python

# Get latest price

ticker = api.get_ticker("BINANCE___SPOT", "BTCUSDT")
print(f"BTC Price: {ticker.last_price}")

# Get order book

orderbook = api.get_orderbook("BINANCE___SPOT", "BTCUSDT", limit=20)
print(f"Best Bid: {orderbook.bids[0]}")

```bash

### Place Order

```python

# Place limit order

order = api.limit_order(
    exchange="BINANCE___SPOT",
    symbol="BTCUSDT",
    side="buy",
    quantity=0.001,
    price=50000
)
print(f"Order ID: {order.order_id}")

```bash

### WebSocket Subscription

```python
def on_ticker(ticker):
    print(f"Price Update: {ticker.last_price}")

# Subscribe to ticker updates

api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT", on_ticker)
api.run()

```bash

---

## CTP Futures Trading

```python
from bt_api_py import CtpAuthConfig

exchange_kwargs = {
    "CTP___FUTURE": {
        "auth_config": CtpAuthConfig(
            broker_id="9999",
            user_id="your_user_id",
            password="your_password",
            md_front="tcp://180.168.146.187:10211",  # Market data front
            td_front="tcp://180.168.146.187:10201",  # Trading front
            app_id="your_app_id",
            auth_code="your_auth_code",
        )
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)

# Get market data

ticker = api.get_ticker("CTP___FUTURE", "IF2506")
print(f"IF2506 Price: {ticker.last_price}")

# Place order

order = api.limit_order(
    exchange="CTP___FUTURE",
    symbol="IF2506",
    side="buy",
    quantity=1,
    price=3500.0
)

```bash

---

## Interactive Brokers Stock/Futures Trading

```python
from bt_api_py import IbWebAuthConfig

exchange_kwargs = {
    "IB_WEB___STK": {
        "auth_config": IbWebAuthConfig(
            account_id="your_account_id",
            base_url="<https://api.interactivebrokers.com">
        )
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)

# Get stock quote

ticker = api.get_ticker("IB_WEB___STK", "AAPL")
print(f"AAPL Price: {ticker.last_price}")

# Check account balance

balance = api.get_balance("IB_WEB___STK")
print(f"Available Funds: {balance.available_funds}")

# Place order

order = api.limit_order(
    exchange="IB_WEB___STK",
    symbol="AAPL",
    side="buy",
    quantity=100,
    price=150.0
)

```bash

---

## Multi-Exchange Unified Operations

```python

# Get prices from multiple exchanges

exchanges = ["BINANCE___SPOT", "OKX___SPOT"]
for exchange in exchanges:
    ticker = api.get_ticker(exchange, "BTCUSDT")
    print(f"{exchange}: {ticker.last_price}")

# Unified order query

order = api.get_order("BINANCE___SPOT", "BTCUSDT", order_id="123456")
print(f"Order Status: {order.status}")

```bash

---

## More Examples

!!! info "Note"

    - For complete documentation, see the [Chinese version](quickstart.md)
    - [CTP Documentation](ctp_quickstart.md) - CTP binding instructions
    - [IB Web API Documentation](ib_web_api/) - IB interface details
