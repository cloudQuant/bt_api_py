# Binance API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: v3
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://developers.binance.com/docs/binance-spot-api-docs/

## 交易所基本信息
- 官方名称: Binance
- 官网: https://www.binance.com
- 交易所类型: CEX (中心化交易所)
- 24h交易量排名: #1 (>$21B)
- 支持的交易对类型: 现货、杠杆、期货、期权
- 支持的币种数量: 300+

## API基础URL

Binance提供多个API端点以优化性能：

| 端点类型 | URL | 说明 |
|---------|-----|------|
| 主端点 | `https://api.binance.com` | 推荐使用 |
| GCP端点 | `https://api-gcp.binance.com` | Google Cloud Platform |
| 备用端点1 | `https://api1.binance.com` | 性能更好但稳定性较低 |
| 备用端点2 | `https://api2.binance.com` | 性能更好但稳定性较低 |
| 备用端点3 | `https://api3.binance.com` | 性能更好但稳定性较低 |
| 备用端点4 | `https://api4.binance.com` | 性能更好但稳定性较低 |
| 市场数据专用 | `https://data-api.binance.vision` | 仅公开市场数据 |

## 认证方式

### 支持的认证类型

Binance支持三种API密钥类型：

1. **HMAC (Hash-based Message Authentication Code)**
2. **RSA (Rivest-Shamir-Adleman)**
3. **Ed25519 (Edwards-curve Digital Signature Algorithm)**

### API密钥获取

1. 登录Binance账户
2. 进入 API Management 页面
3. 创建新的API密钥
4. 保存API Key和Secret Key（Secret Key仅显示一次）
5. 配置API权限（读取、交易、提现等）
6. 可选：绑定IP白名单以提高安全性

### 请求签名方法

所有需要认证的请求必须包含以下参数：

- `timestamp`: 请求时间戳（毫秒）
- `signature`: 请求签名

**签名生成步骤**:

1. 构建查询字符串（按参数名排序）
2. 使用HMAC SHA256算法对查询字符串进行签名
3. 将签名添加到请求参数中

**Python示例**:

```python
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode

API_KEY = '[YOUR_API_KEY]'
SECRET_KEY = '[YOUR_SECRET_KEY]'
BASE_URL = 'https://api.binance.com'

def generate_signature(params, secret_key):
    """生成请求签名"""
    query_string = urlencode(params)
    signature = hmac.new(
        secret_key.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

def signed_request(method, endpoint, params=None):
    """发送签名请求"""
    if params is None:
        params = {}
    
    # 添加时间戳
    params['timestamp'] = int(time.time() * 1000)
    
    # 生成签名
    params['signature'] = generate_signature(params, SECRET_KEY)
    
    # 设置请求头
    headers = {
        'X-MBX-APIKEY': API_KEY
    }
    
    # 发送请求
    url = f"{BASE_URL}{endpoint}"
    if method == 'GET':
        response = requests.get(url, params=params, headers=headers)
    elif method == 'POST':
        response = requests.post(url, params=params, headers=headers)
    
    return response.json()
```


## 市场数据API

### 1. 获取服务器时间

**端点**: `GET /api/v3/time`

**描述**: 获取服务器当前时间

**参数**: 无

**响应示例**:
```json
{
  "serverTime": 1709020800000
}
```

**Python示例**:
```python
def get_server_time():
    """获取服务器时间"""
    url = f"{BASE_URL}/api/v3/time"
    response = requests.get(url)
    return response.json()

# 使用示例
server_time = get_server_time()
print(f"Server time: {server_time['serverTime']}")
```

### 2. 获取交易对信息

**端点**: `GET /api/v3/exchangeInfo`

**描述**: 获取交易所交易规则和交易对信息

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 否 | 交易对名称，如 BTCUSDT |
| symbols | ARRAY | 否 | 交易对数组，如 ["BTCUSDT","ETHUSDT"] |

**响应示例**:
```json
{
  "timezone": "UTC",
  "serverTime": 1709020800000,
  "symbols": [
    {
      "symbol": "BTCUSDT",
      "status": "TRADING",
      "baseAsset": "BTC",
      "quoteAsset": "USDT",
      "filters": []
    }
  ]
}
```

### 3. 获取深度信息

**端点**: `GET /api/v3/depth`

**描述**: 获取市场深度数据

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对名称 |
| limit | INT | 否 | 默认100，最大5000 |

**响应示例**:
```json
{
  "lastUpdateId": 1027024,
  "bids": [
    ["4.00000000", "431.00000000"]
  ],
  "asks": [
    ["4.00000200", "12.00000000"]
  ]
}
```

**Python示例**:
```python
def get_order_book(symbol, limit=100):
    """获取订单簿"""
    url = f"{BASE_URL}/api/v3/depth"
    params = {
        'symbol': symbol,
        'limit': limit
    }
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
order_book = get_order_book('BTCUSDT', limit=10)
print(f"Best bid: {order_book['bids'][0]}")
print(f"Best ask: {order_book['asks'][0]}")
```

### 4. 获取最新价格

**端点**: `GET /api/v3/ticker/price`

**描述**: 获取交易对最新价格

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 否 | 交易对名称，不传则返回所有 |

**响应示例**:
```json
{
  "symbol": "BTCUSDT",
  "price": "50000.00"
}
```

### 5. 获取K线数据

**端点**: `GET /api/v3/klines`

**描述**: 获取K线/蜡烛图数据

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对名称 |
| interval | ENUM | 是 | K线间隔：1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M |
| startTime | LONG | 否 | 起始时间戳 |
| endTime | LONG | 否 | 结束时间戳 |
| limit | INT | 否 | 默认500，最大1000 |

**响应示例**:
```json
[
  [
    1499040000000,      // 开盘时间
    "0.01634790",       // 开盘价
    "0.80000000",       // 最高价
    "0.01575800",       // 最低价
    "0.01577100",       // 收盘价
    "148976.11427815",  // 成交量
    1499644799999,      // 收盘时间
    "2434.19055334",    // 成交额
    308,                // 成交笔数
    "1756.87402397",    // 主动买入成交量
    "28.46694368",      // 主动买入成交额
    "17928899.62484339" // 忽略
  ]
]
```

**Python示例**:
```python
def get_klines(symbol, interval='1h', limit=100):
    """获取K线数据"""
    url = f"{BASE_URL}/api/v3/klines"
    params = {
        'symbol': symbol,
        'interval': interval,
        'limit': limit
    }
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
klines = get_klines('BTCUSDT', interval='1h', limit=24)
for kline in klines[:3]:
    print(f"Time: {kline[0]}, Open: {kline[1]}, Close: {kline[4]}")
```

### 6. 获取24小时价格变动

**端点**: `GET /api/v3/ticker/24hr`

**描述**: 获取24小时价格变动统计

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 否 | 交易对名称 |

**响应示例**:
```json
{
  "symbol": "BTCUSDT",
  "priceChange": "-94.99999800",
  "priceChangePercent": "-95.960",
  "weightedAvgPrice": "0.29628482",
  "prevClosePrice": "0.10002000",
  "lastPrice": "4.00000200",
  "lastQty": "200.00000000",
  "bidPrice": "4.00000000",
  "askPrice": "4.00000200",
  "openPrice": "99.00000000",
  "highPrice": "100.00000000",
  "lowPrice": "0.10000000",
  "volume": "8913.30000000",
  "quoteVolume": "15.30000000",
  "openTime": 1499783499040,
  "closeTime": 1499869899040,
  "count": 76
}
```


## 交易API

### 1. 下单

**端点**: `POST /api/v3/order`

**描述**: 创建新订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对名称 |
| side | ENUM | 是 | 订单方向：BUY, SELL |
| type | ENUM | 是 | 订单类型：LIMIT, MARKET, STOP_LOSS, STOP_LOSS_LIMIT, TAKE_PROFIT, TAKE_PROFIT_LIMIT, LIMIT_MAKER |
| timeInForce | ENUM | 否 | 有效方式：GTC, IOC, FOK |
| quantity | DECIMAL | 否 | 订单数量 |
| quoteOrderQty | DECIMAL | 否 | 报价资产数量（市价单） |
| price | DECIMAL | 否 | 订单价格（限价单必需） |
| newClientOrderId | STRING | 否 | 客户自定义订单ID |
| stopPrice | DECIMAL | 否 | 止损价格 |
| timestamp | LONG | 是 | 时间戳 |
| signature | STRING | 是 | 签名 |

**响应示例**:
```json
{
  "symbol": "BTCUSDT",
  "orderId": 28,
  "orderListId": -1,
  "clientOrderId": "6gCrw2kRUAF9CvJDGP16IP",
  "transactTime": 1507725176595,
  "price": "0.00000000",
  "origQty": "10.00000000",
  "executedQty": "10.00000000",
  "cummulativeQuoteQty": "10.00000000",
  "status": "FILLED",
  "timeInForce": "GTC",
  "type": "MARKET",
  "side": "SELL"
}
```

**Python示例**:
```python
def place_order(symbol, side, order_type, quantity, price=None):
    """下单"""
    endpoint = '/api/v3/order'
    params = {
        'symbol': symbol,
        'side': side,
        'type': order_type,
        'quantity': quantity
    }
    
    # 限价单需要价格和有效期
    if order_type == 'LIMIT':
        if price is None:
            raise ValueError("Price is required for LIMIT orders")
        params['price'] = price
        params['timeInForce'] = 'GTC'
    
    try:
        result = signed_request('POST', endpoint, params)
        return result
    except Exception as e:
        print(f"Order failed: {e}")
        return None

# 使用示例 - 限价买单
order = place_order(
    symbol='BTCUSDT',
    side='BUY',
    order_type='LIMIT',
    quantity='0.001',
    price='50000.00'
)
print(f"Order placed: {order}")

# 使用示例 - 市价卖单
order = place_order(
    symbol='BTCUSDT',
    side='SELL',
    order_type='MARKET',
    quantity='0.001'
)
print(f"Order placed: {order}")
```

### 2. 撤销订单

**端点**: `DELETE /api/v3/order`

**描述**: 撤销活动订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对名称 |
| orderId | LONG | 否 | 订单ID |
| origClientOrderId | STRING | 否 | 客户自定义订单ID |
| timestamp | LONG | 是 | 时间戳 |
| signature | STRING | 是 | 签名 |

**响应示例**:
```json
{
  "symbol": "BTCUSDT",
  "origClientOrderId": "myOrder1",
  "orderId": 4,
  "orderListId": -1,
  "clientOrderId": "cancelMyOrder1",
  "price": "2.00000000",
  "origQty": "1.00000000",
  "executedQty": "0.00000000",
  "cummulativeQuoteQty": "0.00000000",
  "status": "CANCELED",
  "timeInForce": "GTC",
  "type": "LIMIT",
  "side": "BUY"
}
```

**Python示例**:
```python
def cancel_order(symbol, order_id):
    """撤销订单"""
    endpoint = '/api/v3/order'
    params = {
        'symbol': symbol,
        'orderId': order_id
    }
    
    try:
        result = signed_request('DELETE', endpoint, params)
        return result
    except Exception as e:
        print(f"Cancel failed: {e}")
        return None

# 使用示例
result = cancel_order('BTCUSDT', order_id=12345)
print(f"Order canceled: {result}")
```

### 3. 查询订单

**端点**: `GET /api/v3/order`

**描述**: 查询订单状态

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对名称 |
| orderId | LONG | 否 | 订单ID |
| origClientOrderId | STRING | 否 | 客户自定义订单ID |
| timestamp | LONG | 是 | 时间戳 |
| signature | STRING | 是 | 签名 |

**响应示例**:
```json
{
  "symbol": "BTCUSDT",
  "orderId": 1,
  "orderListId": -1,
  "clientOrderId": "myOrder1",
  "price": "0.1",
  "origQty": "1.0",
  "executedQty": "0.0",
  "cummulativeQuoteQty": "0.0",
  "status": "NEW",
  "timeInForce": "GTC",
  "type": "LIMIT",
  "side": "BUY",
  "stopPrice": "0.0",
  "time": 1499827319559,
  "updateTime": 1499827319559,
  "isWorking": true
}
```

### 4. 查询当前挂单

**端点**: `GET /api/v3/openOrders`

**描述**: 查询所有当前挂单

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 否 | 交易对名称，不传则返回所有 |
| timestamp | LONG | 是 | 时间戳 |
| signature | STRING | 是 | 签名 |

**响应示例**:
```json
[
  {
    "symbol": "BTCUSDT",
    "orderId": 1,
    "clientOrderId": "myOrder1",
    "price": "0.1",
    "origQty": "1.0",
    "executedQty": "0.0",
    "status": "NEW",
    "timeInForce": "GTC",
    "type": "LIMIT",
    "side": "BUY",
    "time": 1499827319559
  }
]
```

**Python示例**:
```python
def get_open_orders(symbol=None):
    """查询当前挂单"""
    endpoint = '/api/v3/openOrders'
    params = {}
    if symbol:
        params['symbol'] = symbol
    
    try:
        result = signed_request('GET', endpoint, params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
open_orders = get_open_orders('BTCUSDT')
print(f"Open orders: {len(open_orders)}")
for order in open_orders:
    print(f"Order {order['orderId']}: {order['side']} {order['origQty']} @ {order['price']}")
```

### 5. 查询历史订单

**端点**: `GET /api/v3/allOrders`

**描述**: 查询所有订单（包括历史订单）

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对名称 |
| orderId | LONG | 否 | 起始订单ID |
| startTime | LONG | 否 | 起始时间 |
| endTime | LONG | 否 | 结束时间 |
| limit | INT | 否 | 默认500，最大1000 |
| timestamp | LONG | 是 | 时间戳 |
| signature | STRING | 是 | 签名 |

**响应示例**: 同查询订单


## 账户管理API

### 1. 查询账户信息

**端点**: `GET /api/v3/account`

**描述**: 获取账户信息

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| timestamp | LONG | 是 | 时间戳 |
| signature | STRING | 是 | 签名 |

**响应示例**:
```json
{
  "makerCommission": 15,
  "takerCommission": 15,
  "buyerCommission": 0,
  "sellerCommission": 0,
  "canTrade": true,
  "canWithdraw": true,
  "canDeposit": true,
  "updateTime": 123456789,
  "accountType": "SPOT",
  "balances": [
    {
      "asset": "BTC",
      "free": "4723846.89208129",
      "locked": "0.00000000"
    },
    {
      "asset": "USDT",
      "free": "10000.00000000",
      "locked": "0.00000000"
    }
  ],
  "permissions": ["SPOT"]
}
```

**Python示例**:
```python
def get_account_info():
    """获取账户信息"""
    endpoint = '/api/v3/account'
    params = {}
    
    try:
        result = signed_request('GET', endpoint, params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
account = get_account_info()
if account:
    print(f"Can trade: {account['canTrade']}")
    print(f"Balances:")
    for balance in account['balances']:
        if float(balance['free']) > 0 or float(balance['locked']) > 0:
            print(f"  {balance['asset']}: {balance['free']} (locked: {balance['locked']})")
```

### 2. 查询账户余额

**端点**: `GET /api/v3/account`

**描述**: 获取账户余额（同账户信息接口）

**Python示例**:
```python
def get_balance(asset=None):
    """获取账户余额"""
    account = get_account_info()
    if not account:
        return None
    
    if asset:
        # 查询特定资产余额
        for balance in account['balances']:
            if balance['asset'] == asset:
                return {
                    'asset': asset,
                    'free': float(balance['free']),
                    'locked': float(balance['locked']),
                    'total': float(balance['free']) + float(balance['locked'])
                }
        return None
    else:
        # 返回所有非零余额
        balances = []
        for balance in account['balances']:
            free = float(balance['free'])
            locked = float(balance['locked'])
            if free > 0 or locked > 0:
                balances.append({
                    'asset': balance['asset'],
                    'free': free,
                    'locked': locked,
                    'total': free + locked
                })
        return balances

# 使用示例
btc_balance = get_balance('BTC')
print(f"BTC Balance: {btc_balance}")

all_balances = get_balance()
print(f"All non-zero balances: {len(all_balances)}")
```

### 3. 查询成交历史

**端点**: `GET /api/v3/myTrades`

**描述**: 获取账户成交历史

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | STRING | 是 | 交易对名称 |
| orderId | LONG | 否 | 订单ID |
| startTime | LONG | 否 | 起始时间 |
| endTime | LONG | 否 | 结束时间 |
| fromId | LONG | 否 | 起始成交ID |
| limit | INT | 否 | 默认500，最大1000 |
| timestamp | LONG | 是 | 时间戳 |
| signature | STRING | 是 | 签名 |

**响应示例**:
```json
[
  {
    "symbol": "BTCUSDT",
    "id": 28457,
    "orderId": 100234,
    "orderListId": -1,
    "price": "4.00000100",
    "qty": "12.00000000",
    "quoteQty": "48.000012",
    "commission": "10.10000000",
    "commissionAsset": "BNB",
    "time": 1499865549590,
    "isBuyer": true,
    "isMaker": false,
    "isBestMatch": true
  }
]
```

**Python示例**:
```python
def get_my_trades(symbol, limit=100):
    """获取成交历史"""
    endpoint = '/api/v3/myTrades'
    params = {
        'symbol': symbol,
        'limit': limit
    }
    
    try:
        result = signed_request('GET', endpoint, params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
trades = get_my_trades('BTCUSDT', limit=10)
if trades:
    print(f"Recent trades: {len(trades)}")
    for trade in trades[:5]:
        side = 'BUY' if trade['isBuyer'] else 'SELL'
        print(f"{side} {trade['qty']} @ {trade['price']}")
```

## 速率限制

### 全局速率限制

Binance实施多层速率限制以保护系统稳定性：

| 限制类型 | 限制值 | 时间窗口 | 说明 |
|---------|--------|----------|------|
| 请求权重 | 6000 | 1分钟 | 每个请求有不同权重 |
| 原始请求数 | 61000 | 5分钟 | 总请求数限制 |
| 订单数 | 100 | 10秒 | 下单频率限制 |
| 订单数 | 200000 | 1天 | 每日订单总数 |

### 权重系统

每个API端点都有对应的权重值：

| 端点 | 权重 |
|------|------|
| GET /api/v3/ticker/price | 2 |
| GET /api/v3/depth | 根据limit：5-50 |
| GET /api/v3/klines | 2 |
| POST /api/v3/order | 1 |
| GET /api/v3/account | 20 |
| GET /api/v3/myTrades | 20 |

### 响应头

API响应包含速率限制信息：

```
X-MBX-USED-WEIGHT-1M: 当前1分钟内使用的权重
X-MBX-ORDER-COUNT-10S: 当前10秒内的订单数
X-MBX-ORDER-COUNT-1D: 当天的订单数
```

### 触发限制后的行为

- **HTTP 429**: 超过速率限制
- **HTTP 418**: IP被临时封禁（持续2分钟到3天）
- **Retry-After**: 响应头指示重试等待时间

### 最佳实践

```python
import time
from functools import wraps

def rate_limit_handler(func):
    """速率限制处理装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                response = func(*args, **kwargs)
                return response
            except Exception as e:
                if '429' in str(e):  # 速率限制
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        print(f"Rate limit hit, waiting {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                raise
        return None
    return wrapper

@rate_limit_handler
def api_call_with_retry(endpoint, params):
    """带重试的API调用"""
    return signed_request('GET', endpoint, params)
```


## WebSocket支持

Binance提供WebSocket API用于实时数据推送。

### WebSocket端点

| 类型 | URL |
|------|-----|
| 现货 | `wss://stream.binance.com:9443/ws` |
| 现货（备用） | `wss://stream.binance.com:443/ws` |
| 组合流 | `wss://stream.binance.com:9443/stream` |

### 订阅方式

WebSocket连接不需要认证即可订阅公开数据流。

### 可订阅的数据频道

| 频道 | 订阅格式 | 描述 |
|------|---------|------|
| 实时成交 | `<symbol>@trade` | 实时成交信息 |
| K线 | `<symbol>@kline_<interval>` | K线数据 |
| 深度 | `<symbol>@depth` | 订单簿深度 |
| 24小时统计 | `<symbol>@ticker` | 24小时价格统计 |
| 最优挂单 | `<symbol>@bookTicker` | 最优买卖挂单 |

### 订阅消息格式

```json
{
  "method": "SUBSCRIBE",
  "params": [
    "btcusdt@trade",
    "btcusdt@kline_1m"
  ],
  "id": 1
}
```

### 取消订阅消息格式

```json
{
  "method": "UNSUBSCRIBE",
  "params": [
    "btcusdt@trade"
  ],
  "id": 312
}
```

### 心跳机制

- 服务器每3分钟发送一次ping帧
- 客户端必须在10分钟内响应pong帧
- 否则连接将被断开

### Python WebSocket示例

```python
import websocket
import json
import threading

class BinanceWebSocket:
    """Binance WebSocket客户端"""
    
    def __init__(self):
        self.ws = None
        self.url = "wss://stream.binance.com:9443/ws"
        
    def on_message(self, ws, message):
        """处理接收到的消息"""
        data = json.loads(message)
        print(f"Received: {data}")
        
    def on_error(self, ws, error):
        """处理错误"""
        print(f"Error: {error}")
        
    def on_close(self, ws, close_status_code, close_msg):
        """处理连接关闭"""
        print("Connection closed")
        
    def on_open(self, ws):
        """连接建立后订阅数据流"""
        print("Connection opened")
        
        # 订阅BTCUSDT实时成交和K线
        subscribe_message = {
            "method": "SUBSCRIBE",
            "params": [
                "btcusdt@trade",
                "btcusdt@kline_1m"
            ],
            "id": 1
        }
        ws.send(json.dumps(subscribe_message))
        
    def connect(self):
        """建立WebSocket连接"""
        self.ws = websocket.WebSocketApp(
            self.url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open
        )
        
        # 在新线程中运行
        wst = threading.Thread(target=self.ws.run_forever)
        wst.daemon = True
        wst.start()
        
    def close(self):
        """关闭连接"""
        if self.ws:
            self.ws.close()

# 使用示例
ws_client = BinanceWebSocket()
ws_client.connect()

# 保持运行
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    ws_client.close()
```

### 用户数据流（需要认证）

用户数据流用于接收账户更新、订单更新等私有数据。

**步骤**:

1. 创建listenKey: `POST /api/v3/userDataStream`
2. 连接WebSocket: `wss://stream.binance.com:9443/ws/<listenKey>`
3. 定期延长listenKey有效期: `PUT /api/v3/userDataStream`

```python
def create_listen_key():
    """创建listenKey"""
    endpoint = '/api/v3/userDataStream'
    headers = {'X-MBX-APIKEY': API_KEY}
    url = f"{BASE_URL}{endpoint}"
    response = requests.post(url, headers=headers)
    return response.json()['listenKey']

def keep_alive_listen_key(listen_key):
    """延长listenKey有效期"""
    endpoint = '/api/v3/userDataStream'
    headers = {'X-MBX-APIKEY': API_KEY}
    params = {'listenKey': listen_key}
    url = f"{BASE_URL}{endpoint}"
    response = requests.put(url, params=params, headers=headers)
    return response.status_code == 200

# 使用示例
listen_key = create_listen_key()
user_ws_url = f"wss://stream.binance.com:9443/ws/{listen_key}"
print(f"User data stream URL: {user_ws_url}")
```

## 错误代码

### 常见HTTP错误码

| 错误码 | 描述 | 可能原因 | 处理建议 |
|--------|------|----------|----------|
| 400 | Bad Request | 请求参数错误 | 检查参数格式和必需参数 |
| 401 | Unauthorized | 认证失败 | 检查API密钥是否正确 |
| 403 | Forbidden | 权限不足 | 检查API密钥权限设置 |
| 404 | Not Found | 端点不存在 | 检查API端点路径 |
| 429 | Too Many Requests | 超过速率限制 | 降低请求频率，实施退避策略 |
| 418 | IP被封禁 | 违反速率限制 | 等待解封（2分钟-3天） |
| 5xx | Server Error | 服务器错误 | 稍后重试 |

### 业务错误码

| 错误码 | 消息 | 描述 |
|--------|------|------|
| -1000 | UNKNOWN | 未知错误 |
| -1001 | DISCONNECTED | 内部错误，无法处理请求 |
| -1002 | UNAUTHORIZED | 无权限执行此操作 |
| -1003 | TOO_MANY_REQUESTS | 请求过多 |
| -1006 | UNEXPECTED_RESP | 从消息总线收到意外响应 |
| -1007 | TIMEOUT | 等待后端服务器响应超时 |
| -1010 | ERROR_MSG_RECEIVED | 从消息总线收到错误消息 |
| -1013 | INVALID_MESSAGE | 无效的消息格式 |
| -1014 | UNKNOWN_ORDER_COMPOSITION | 不支持的订单组合 |
| -1015 | TOO_MANY_ORDERS | 订单过多 |
| -1016 | SERVICE_SHUTTING_DOWN | 服务关闭中 |
| -1020 | UNSUPPORTED_OPERATION | 不支持的操作 |
| -1021 | INVALID_TIMESTAMP | 时间戳不在允许范围内 |
| -1022 | INVALID_SIGNATURE | 签名无效 |
| -1100 | ILLEGAL_CHARS | 参数包含非法字符 |
| -1101 | TOO_MANY_PARAMETERS | 参数过多 |
| -1102 | MANDATORY_PARAM_EMPTY_OR_MALFORMED | 必需参数为空或格式错误 |
| -1103 | UNKNOWN_PARAM | 未知参数 |
| -1104 | UNREAD_PARAMETERS | 未读取的参数 |
| -1105 | PARAM_EMPTY | 参数为空 |
| -1106 | PARAM_NOT_REQUIRED | 不需要的参数 |
| -1111 | BAD_PRECISION | 精度超出定义范围 |
| -1112 | NO_DEPTH | 没有深度数据 |
| -1114 | TIF_NOT_REQUIRED | 不需要timeInForce参数 |
| -1115 | INVALID_TIF | 无效的timeInForce |
| -1116 | INVALID_ORDER_TYPE | 无效的订单类型 |
| -1117 | INVALID_SIDE | 无效的订单方向 |
| -1118 | EMPTY_NEW_CL_ORD_ID | 新的客户订单ID为空 |
| -1119 | EMPTY_ORG_CL_ORD_ID | 原始客户订单ID为空 |
| -1120 | BAD_INTERVAL | 无效的时间间隔 |
| -1121 | BAD_SYMBOL | 无效的交易对 |
| -1125 | INVALID_LISTEN_KEY | 无效的listenKey |
| -1127 | MORE_THAN_XX_HOURS | 查询时间范围超过限制 |
| -1128 | OPTIONAL_PARAMS_BAD_COMBO | 可选参数组合错误 |
| -1130 | INVALID_PARAMETER | 无效的参数 |
| -2010 | NEW_ORDER_REJECTED | 订单被拒绝 |
| -2011 | CANCEL_REJECTED | 撤单被拒绝 |
| -2013 | NO_SUCH_ORDER | 订单不存在 |
| -2014 | BAD_API_KEY_FMT | API密钥格式错误 |
| -2015 | REJECTED_MBX_KEY | 无效的API密钥 |

### 错误处理示例

```python
def handle_api_error(error_code, error_msg):
    """处理API错误"""
    error_handlers = {
        -1021: "时间戳错误，请同步系统时间",
        -1022: "签名错误，请检查Secret Key",
        -2010: "订单被拒绝，请检查订单参数",
        -2013: "订单不存在",
        -1003: "请求过多，请降低频率"
    }
    
    if error_code in error_handlers:
        return error_handlers[error_code]
    else:
        return f"错误 {error_code}: {error_msg}"

# 在API调用中使用
try:
    result = place_order('BTCUSDT', 'BUY', 'LIMIT', '0.001', '50000')
except Exception as e:
    error_data = json.loads(str(e))
    error_msg = handle_api_error(error_data['code'], error_data['msg'])
    print(f"Error: {error_msg}")
```

## 变更历史

### 2026-02-27
- 初始版本创建
- 添加现货API文档
- 包含REST API和WebSocket文档
- 添加Python代码示例

---

## 相关资源

- [Binance官方API文档](https://developers.binance.com/docs/binance-spot-api-docs/)
- [Binance API GitHub](https://github.com/binance/binance-spot-api-docs)
- [Binance API公告频道](https://t.me/binance_api_announcements)
- [Binance API状态页面](https://binance.statuspage.io/)

---

*本文档由 bt_api_py 项目维护，内容基于Binance官方API文档整理。*
