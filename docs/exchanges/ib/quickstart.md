# Interactive Brokers 交易指南

Interactive Brokers (IB) 是全球领先的电子交易经纪商，支持股票、期货、外汇、期权等多种市场。

## 前置条件

### IB 账户

1. 开设 IB 账户: https://www.interactivebrokers.com/
2. 启用交易权限
3. 获取账户 ID

### Web API 门户

1. 登录 IB 客户门户
2. 进入 **设置 → API 设置**
3. 启用 **Web API**选项

## 支持的市场

| 市场 | 代码 | 说明 |
|------|------|------|
| 股票 | `IB_WEB___STK` | 美股、港股、A 股等全球股票 |
| 期货 | `IB_WEB___FUT` | 全球期货市场 |

## 快速开始

### 1. 创建 IB API 实例

```python
from bt_api_py import BtApi, IbWebAuthConfig

exchange_kwargs = {
    "IB_WEB___STK": {
        "auth_config": IbWebAuthConfig(
            account_id="your_account_id",           # IB 账户 ID
            base_url="<https://api.interactivebrokers.com">
        )
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)

```

### 2. 获取股票行情

```python

# 获取美股行情

ticker = api.get_ticker("IB_WEB___STK", "AAPL")
print(f"AAPL 最新价: {ticker.last_price}")
print(f"买一: {ticker.bid_price1}, 卖一: {ticker.ask_price1}")

# 获取港股行情 (港股代码需要添加交易所后缀)

ticker_hk = api.get_ticker("IB_WEB___STK", "700.HK")
print(f"腾讯控股: {ticker_hk.last_price}")

```

### 3. 查询账户

```python

# 获取账户摘要

balance = api.get_balance("IB_WEB___STK")
print(f"账户净值: {balance.net_value}")
print(f"可用资金: {balance.available_funds}")
print(f"购买力: {balance.buying_power}")

```

### 4. 下单交易

```python

# 下限价单

order = api.limit_order(
    exchange="IB_WEB___STK",
    symbol="AAPL",
    side="buy",
    quantity=100,
    price=150.0
)
print(f"订单 ID: {order.order_id}")

```

### 5. 查询持仓

```python
positions = api.get_positions("IB_WEB___STK")
for pos in positions:
    print(f"{pos.symbol}: {pos.position} 股")

```

## 期货交易

```python
exchange_kwargs = {
    "IB_WEB___FUT": {
        "auth_config": IbWebAuthConfig(
            account_id="your_account_id",
        )
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)

# 获取期货行情

ticker = api.get_ticker("IB_WEB___FUT", "ES2025")  # E-mini S&P 500

print(f"ES 价格: {ticker.last_price}")

```

## 常用股票代码

| 市场 | 代码 | 说明 |
|------|------|------|
| 美股 | AAPL | 苹果 |
| 美股 | TSLA | 特斯拉 |
| 美股 | NVDA | 英伟达 |
| 美股 | MSFT | 微软 |
| 港股 | 700.HK | 腾讯控股 |
| 港股 | 9988.HK | 阿里巴巴 |
| A 股 | 600519.STK | 贵州茅台 |

## WebSocket 订阅

```python
def on_ticker(ticker):
    print(f"{ticker.symbol} 价格更新: {ticker.last_price}")

# 订阅股票行情推送

api.subscribe_ticker("IB_WEB___STK", "AAPL", on_ticker)
api.run()

```

## IB 特有功能

### 查询订单状态

```python
order = api.get_order("IB_WEB___STK", "AAPL", order_id="123456")
print(f"订单状态: {order.status}")
print(f"成交数量: {order.filled_quantity}")

```

### 获取账户历史

```python

# 获取交易历史

trades = api.get_trades("IB_WEB___STK")
for trade in trades:
    print(f"{trade.symbol}: {trade.side} {trade.quantity} @ {trade.price}")

```

## 注意事项

1.***市场时间**: 不同市场有不同交易时间

1. ***货币转换**: 跨币种交易需要注意汇率
2. ***数据延迟**: 部分数据可能有延迟
3. ***API 限制**: 注意请求频率限制

## 相关文档

- [IB Web API 文档](index.md)
- [IB API 快速参考](api_reference_quick.md)
- [IB 实现指南](implementation_guide.md)
