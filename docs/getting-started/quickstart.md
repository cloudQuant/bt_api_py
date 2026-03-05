# 快速入门

## 5 分钟上手

### BtApi 使用方式

`BtApi` 通过 `exchange_kwargs` 参数配置一个或多个交易所：

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

```

### 支持的交易所

| 交易所 | 代码 | 说明 |
|--------|------|------|
| **Binance 现货**| `BINANCE___SPOT` | 加密货币现货 |
|**Binance 合约**| `BINANCE___SWAP` | USDT 本位合约 |
|**OKX 现货**| `OKX___SPOT` | 加密货币现货 |
|**OKX 合约**| `OKX___SWAP` | 永续合约 |
|**CTP 期货**| `CTP___FUTURE` | 中国期货市场 |
|**IB Web 股票**| `IB_WEB___STK` | 全球股票市场 |
|**IB Web 期货** | `IB_WEB___FUT` | 全球期货市场 |

---

## 加密货币交易 (Binance/OKX)

### 获取行情

```python

# 获取最新价格

ticker = api.get_ticker("BINANCE___SPOT", "BTCUSDT")
print(f"BTC 价格: {ticker.last_price}")

# 获取深度

orderbook = api.get_orderbook("BINANCE___SPOT", "BTCUSDT", limit=20)
print(f"买一: {orderbook.bids[0]}")

```

### 下单交易

```python

# 下限价单

order = api.limit_order(
    exchange="BINANCE___SPOT",
    symbol="BTCUSDT",
    side="buy",
    quantity=0.001,
    price=50000
)
print(f"订单 ID: {order.order_id}")

```

### WebSocket 订阅

```python
def on_ticker(ticker):
    print(f"价格更新: {ticker.last_price}")

# 订阅行情推送

api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT", on_ticker)
api.run()

```

---

## CTP 期货交易

```python
from bt_api_py import CtpAuthConfig

exchange_kwargs = {
    "CTP___FUTURE": {
        "auth_config": CtpAuthConfig(
            broker_id="9999",
            user_id="your_user_id",
            password="your_password",
            md_front="tcp://180.168.146.187:10211",  # 行情前置
            td_front="tcp://180.168.146.187:10201",  # 交易前置
            app_id="your_app_id",
            auth_code="your_auth_code",
        )
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)

# 获取行情

ticker = api.get_ticker("CTP___FUTURE", "IF2506")
print(f"IF2506 价格: {ticker.last_price}")

# 下单

order = api.limit_order(
    exchange="CTP___FUTURE",
    symbol="IF2506",
    side="buy",
    quantity=1,
    price=3500.0
)

```

---

## Interactive Brokers 股票/期货交易

```python
from bt_api_py import IbWebAuthConfig

exchange_kwargs = {
    "IB_WEB___STK": {
        "auth_config": IbWebAuthConfig(
            account_id="your_account_id",
            base_url="https://api.interactivebrokers.com"
        )
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)

# 获取股票行情

ticker = api.get_ticker("IB_WEB___STK", "AAPL")
print(f"AAPL 价格: {ticker.last_price}")

# 查询账户余额

balance = api.get_balance("IB_WEB___STK")
print(f"可用资金: {balance.available_funds}")

# 下单

order = api.limit_order(
    exchange="IB_WEB___STK",
    symbol="AAPL",
    side="buy",
    quantity=100,
    price=150.0
)

```

---

## 多交易所统一操作

```python

# 同时从多个交易所获取价格

exchanges = ["BINANCE___SPOT", "OKX___SPOT"]
for exchange in exchanges:
    ticker = api.get_ticker(exchange, "BTCUSDT")
    print(f"{exchange}: {ticker.last_price}")

# 统一的订单查询

order = api.get_order("BINANCE___SPOT", "BTCUSDT", order_id="123456")
print(f"订单状态: {order.status}")

```

---

## 更多示例

- [使用指南](../guides/usage_guide.md) - 完整使用教程
- [CTP 快速入门](../exchanges/ctp/quickstart.md) - CTP 快速入门
- [IB Web API 文档](../exchanges/ib/index.md) - IB 接口详细说明
