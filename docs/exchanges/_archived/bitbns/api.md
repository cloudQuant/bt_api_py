# Bitbns API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V2
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://github.com/bitbns-official/bitbnspy>
- 官方 Node SDK: <https://github.com/bitbns-official/node-bitbns-api>

## 交易所基本信息

- 官方名称: Bitbns
- 官网: <https://bitbns.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 印度
- 支持的交易对: 300+ (以 INR 和 USDT 计价为主)
- 支持的交易类型: 现货(Spot)、杠杆(Margin)、合约(Futures)
- 法币支持: INR (印度卢比)
- 合规: 符合 FATF 全球 VASP 标准
- 官方 Python SDK: `pip install bitbnspy`

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://api.bitbns.com`> | 主端点 |

| Public API | `<https://api.bitbns.com/api/trade/v1`> | 公共市场数据 |

| WebSocket | `wss://ws.bitbns.com` | 实时数据（有限支持） |

## 认证方式

### API 密钥获取

1. 注册 Bitbns 账户并完成 KYC
2. 进入 API Trading 页面
3. 生成 API Key 和 Secret Key

### 认证机制

Bitbns 使用 API Key + Secret Key 的方式进行认证。推荐使用官方 Python SDK。

### Python SDK 使用

```python

# 安装

# pip install bitbnspy

from bitbnspy import bitbns

# 公共接口（无需 API Key）

public = bitbns.publicEndpoints()
print(public.fetchTickers())

# 私有接口（需要 API Key）

key = "your_api_key"
secret_key = "your_secret_key"
client = bitbns(key, secret_key)
print(client.currentCoinBalance('BTC'))

```bash

## 市场数据 API

### 1. 获取所有 Ticker

- *方法**: `fetchTickers()`

- *描述**: 获取所有交易对的行情数据。无需认证。

```python
public = bitbns.publicEndpoints()
tickers = public.fetchTickers()

# 响应中包含所有交易对的 highest_buy_bid, lowest_sell_bid, last_traded_price, volume 等

```bash

- *响应示例**:

```json
{
  "data": {
    "BTC": {
      "highest_buy_bid": 3804776.47,
      "lowest_sell_bid": 3809634.1,
      "last_traded_price": 3809634.1,
      "yes_price": 3817924.68,
      "volume": {
        "max": "3860000.00",
        "min": "3728401.38",
        "volume": 29.22102567
      }
    },
    "ETH": { ... }
  },
  "status": 1,
  "error": null
}

```bash

### 2. 获取指定币种 Ticker

- *方法**: `getTickerApi(symbol)` (私有接口)

```python
result = client.getTickerApi('BTC')
data = result["data"]["BTC"]
print(f"Best bid: {data['highest_buy_bid']}")
print(f"Best ask: {data['lowest_sell_bid']}")
print(f"Last: {data['last_traded_price']}")

```bash

- *响应字段**:

| 字段 | 描述 |

|------|------|

| highest_buy_bid | 最高买价 |

| lowest_sell_bid | 最低卖价 |

| last_traded_price | 最新成交价 |

### 3. 获取订单簿

- *方法**: `fetchOrderBook(symbol, market, depth)`

```python
public = bitbns.publicEndpoints()
orderbook = public.fetchOrderBook('BTC', 'INR', depth=10)
print(f"Best ask: {orderbook['data']['asks'][0]}")
print(f"Best bid: {orderbook['data']['bids'][0]}")

```bash

- *响应示例**:

```json
{
  "data": {
    "asks": [[3839997.47, 0.14315922], [3840000, 0.00104478]],
    "bids": [[3836673.24, 0.0002062], [3836673.23, 0.23805619]],
    "timestamp": 1630664703000
  },
  "status": 1,
  "error": null
}

```bash

### 4. 获取最近成交

- *方法**: `fetchTrades(symbol, market, limit)`

```python
trades = public.fetchTrades('BTC', 'INR', limit=10)
for trade in trades['data']:
    print(f"{trade['type']} price={trade['price']} vol={trade['base_volume']} time={trade['timestamp']}")

```bash

- *响应示例**:

```json
{
  "data": [
    {
      "base_volume": 0.00106565,
      "price": "3837783.20",
      "quote_volume": 4099.96,
      "timestamp": 1630664966000,
      "tradeId": "2468049",
      "type": "buy"
    }
  ],
  "status": 1,
  "error": null
}

```bash

### 5. 获取 OHLCV (K 线数据)

- *方法**: `fetchOHLCV(symbol, market, page)`

```python
ohlcv = public.fetchOHLCV('BTC', 'INR', page=1)
for candle in ohlcv['data']:
    print(f"{candle['timestamp']}: O={candle['open']} H={candle['high']} L={candle['low']} C={candle['close']} V={candle['vol']}")

```bash

- *响应示例**:

```json
{
  "data": [
    {
      "open": 3727748.31,
      "high": 3727748.31,
      "low": 3724656.82,
      "close": 3727748.31,
      "vol": 1.07505351,
      "timestamp": "2021-09-01T11:25:04.000Z"
    }
  ],
  "status": 1,
  "error": null
}

```bash

## 交易 API

> 以下端点均需 API Key 认证。

### 1. 查询余额

- *方法**: `currentCoinBalance(symbol)`

```python
balance = client.currentCoinBalance('BTC')
print(f"Available: {balance['data']['availableorderBTC']}")
print(f"In order: {balance['data']['inorderBTC']}")

```bash

- *响应示例**:

```json
{
  "data": {
    "inorderBTC": 8.34,
    "availableorderBTC": 15.76
  },
  "status": 1,
  "error": null
}

```bash

### 2. 限价买单

- *方法**: `placeBuyOrder(symbol, quantity, rate)`

```python
result = client.placeBuyOrder('XRP', 200, 25)
print(f"Order ID: {result['id']}, Status: {result['status']}")

```bash

### 3. 限价卖单

- *方法**: `placeSellOrder(symbol, quantity, rate)`

```python
result = client.placeSellOrder('XRP', 200, 25)
print(f"Order ID: {result['id']}")

```bash

### 4. 市价单（按金额）

- *方法**: `placeMarketOrder(symbol, market, side, amount)`

```python

# 用 100 INR 市价买入 BTC

result = client.placeMarketOrder('BTC', 'INR', 'BUY', 100)
print(f"Order ID: {result['id']}")

```bash

### 5. 市价单（按数量）

- *方法**: `placeMarketOrderQuantity(symbol, market, side, quantity)`

```python
result = client.placeMarketOrderQuantity('BTC', 'INR', 'BUY', 0.00001)
print(f"Order ID: {result['id']}")

```bash

### 6. V2 下单接口（推荐）

- *方法**: `placeOrders(params)`

支持简单订单、止损单、括号订单（Bracket Order）。

```python

# 简单限价买单

result = client.placeOrders({
    'symbol': 'XRP',
    'side': 'BUY',
    'quantity': 40,
    'rate': 4
})

# 止损买单

result = client.placeOrders({
    'symbol': 'XRP',
    'side': 'BUY',
    'quantity': 40,
    'rate': 4,
    't_rate': 3.5  # 触发价格

})

# 括号订单（Bracket Order）

result = client.placeOrders({
    'symbol': 'XRP',
    'side': 'BUY',
    'quantity': 40,
    'rate': 4,
    'target_rate': 5,    # 止盈价
    't_rate': 3.5,       # 触发价
    'trail_rate': 0.01   # 追踪价

})

# USDT 市场下单（币名后加 _USDT）

result = client.placeOrders({
    'symbol': 'TRX_USDT',
    'side': 'BUY',
    'quantity': 40,
    'rate': 0.05
})

```bash

### 7. 撤单

- *方法**: `cancelOrders(params)`

```python

# INR 市场撤单

result = client.cancelOrders({
    'symbol': 'XRP',
    'side': 'cancelOrder',
    'entry_id': 462
})

# 撤销止损单

result = client.cancelOrders({
    'symbol': 'XRP',
    'side': 'cancelStopLossOrder',
    'entry_id': 462
})

# USDT 市场撤单

result = client.cancelOrders({
    'symbol': 'TRX_USDT',
    'side': 'usdtcancelOrder',
    'entry_id': 462
})

```bash

- *撤单 side 枚举**:
- `cancelOrder` - 撤销 INR 市场普通订单
- `cancelStopLossOrder` - 撤销 INR 市场止损单
- `usdtcancelOrder` - 撤销 USDT 市场普通订单
- `usdtcancelStopLossOrder` - 撤销 USDT 市场止损单

### 8. 查询未成交订单

- *方法**: `listOpenOrders(symbol)`

```python
orders = client.listOpenOrders('BTC')
for order in orders['data']:
    side = "SELL" if order['type'] == 1 else "BUY"
    print(f"ID:{order['entry_id']} {side} qty={order['btc']} rate={order['rate']}")

```bash

- *订单状态枚举**:
- `-1` - 已撤销
- `0` - 未处理
- `1` - 部分成交
- `2` - 完全成交

### 9. 查询止损订单

- *方法**: `listOpenStopOrders(symbol)`

## 账户管理 API

### 1. 获取充币地址

- *方法**: `getCoinAddress(symbol)`

```python
address = client.getCoinAddress('BTC')
print(f"Address: {address['data']['token']}")

# 对于有 tag 的币种（如 XLM），还会返回 tag 字段

```bash

### 2. 充值历史

- *方法**: `depositHistory(symbol, page)`

```python
deposits = client.depositHistory('BTC', 0)
for d in deposits['data']:
    print(f"{d['type']}: amount={d['amount']} date={d['date']}")

```bash

### 3. 提现历史

- *方法**: `withdrawHistory(symbol, page)`

### 4. 平台状态

- *方法**: `platformStatus()`

```python
status = client.platformStatus()
for coin, info in status['data'].items():
    if info['status'] == 1:
        print(f"{coin}: Active")

```bash

### 5. 成交历史

- *方法**: `fetchTradeHistory(symbol, page, since)`

### 6. INR 充值/提现历史

- `fetchINRDeposits(page)`
- `fetchINRWithrawals(page)`

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| 全局 | 适中 | 具体限制参考官方文档 |

| 公共接口 | 较宽松 | 行情数据限制较高 |

| 交易接口 | 较严格 | 下单/撤单限制较低 |

### 最佳实践

- 使用官方 SDK 自动处理认证和速率限制
- 批量查询使用 `fetchTickers()` 代替逐个查询
- USDT 市场交易对使用 `_USDT` 后缀

## WebSocket 支持

Bitbns 对 WebSocket 支持有限，建议使用 REST API 轮询或官方 SDK。

公共数据可通过 `fetchTickers()` 等轮询接口获取实时数据。

## 错误处理

### 响应格式

- *成功**:

```json
{
  "data": { ... },
  "status": 1,
  "error": null
}

```bash

- *失败**:

```json
{
  "data": null,
  "status": 0,
  "error": "Error message description"
}

```bash

### 常见错误

| status | error | 描述 |

|--------|-------|------|

| 0 | "Not Found" | 请求的资源不存在 |

| 0 | "Insufficient balance" | 余额不足 |

| 0 | "Invalid API key" | API Key 无效 |

| 0 | "Order not found" | 订单不存在 |

| 0 | "Minimum order volume..." | 低于最小下单量 |

### Python 错误处理示例

```python
def safe_api_call(func, *args, **kwargs):
    """带错误处理的 API 调用"""
    try:
        result = func(*args, **kwargs)
        if result.get("status") == 1:
            return result.get("data")
        error = result.get("error", "Unknown error")
        print(f"API Error: {error}")
        return None
    except Exception as e:
        print(f"Exception: {e}")
        return None

# 使用

balance = safe_api_call(client.currentCoinBalance, 'BTC')
if balance:
    print(f"Available: {balance.get('availableorderBTC')}")

```bash

## 代码示例

### Python 完整交易示例

```python
from bitbnspy import bitbns

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"

# 初始化

public = bitbns.publicEndpoints()
client = bitbns(API_KEY, SECRET_KEY)

# ===== 公共接口 =====

# 获取所有行情

tickers = public.fetchTickers()
if tickers['status'] == 1:
    btc = tickers['data'].get('BTC', {})
    print(f"BTC/INR Last: {btc.get('last_traded_price')}")

# 获取订单簿

orderbook = public.fetchOrderBook('BTC', 'INR', depth=5)
if orderbook['status'] == 1:
    print(f"Best ask: {orderbook['data']['asks'][0]}")
    print(f"Best bid: {orderbook['data']['bids'][0]}")

# 获取最近成交

trades = public.fetchTrades('BTC', 'INR', limit=5)
if trades['status'] == 1:
    for t in trades['data']:
        print(f"{t['type']} @ {t['price']}")

# 获取 K 线

ohlcv = public.fetchOHLCV('BTC', 'INR', page=1)
if ohlcv['status'] == 1:
    latest = ohlcv['data'][0]
    print(f"Latest candle: O={latest['open']} C={latest['close']}")

# ===== 私有接口 =====

# 查询余额

balance = client.currentCoinBalance('BTC')
if balance['status'] == 1:
    print(f"BTC available: {balance['data']['availableorderBTC']}")

# V2 下单（推荐）

order = client.placeOrders({
    'symbol': 'BTC',
    'side': 'BUY',
    'quantity': 0.0001,
    'rate': 3500000
})
if order.get('status') == 1:
    print(f"Order placed, ID: {order['id']}")

# 查询未成交订单

open_orders = client.listOpenOrders('BTC')
if open_orders['status'] == 1:
    for o in open_orders['data']:
        print(f"ID:{o['entry_id']} rate={o['rate']} qty={o['btc']}")

# 撤单

cancel = client.cancelOrders({
    'symbol': 'BTC',
    'side': 'cancelOrder',
    'entry_id': 12345
})
print(f"Cancel result: {cancel}")

```bash

### 最小下单量

Bitbns 对每个交易对有最小下单量要求，请查询交易对信息确认。

## 变更历史

### 2026-02-27

- 完善文档，添加公共和私有 API 详细说明
- 添加官方 Python SDK (bitbnspy) 使用示例
- 添加市场数据 API（Ticker、深度、成交、OHLCV）详细说明
- 添加交易 API（V1 和 V2 下单、撤单、查询）完整示例
- 添加 INR 和 USDT 市场下单差异说明
- 添加括号订单（Bracket Order）和止损单示例
- 添加错误处理

- --

## 相关资源

- [Bitbns 官方 Python SDK](<https://github.com/bitbns-official/bitbnspy)>
- [Bitbns 官方 Node SDK](<https://github.com/bitbns-official/node-bitbns-api)>
- [Bitbns 官网](<https://bitbns.com)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 Bitbns 官方 API 文档和 SDK 整理。*
