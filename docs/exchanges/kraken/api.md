# Kraken API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: v0 (REST), v2 (WebSocket)
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://docs.kraken.com/rest/

## 交易所基本信息
- 官方名称: Kraken
- 官网: https://www.kraken.com
- 交易所类型: CEX (中心化交易所)
- 24h交易量排名: #5 ($1.2B+)
- 支持的交易对类型: 现货、杠杆、期货
- 支持的币种数量: 200+
- 特点: 美国合规交易所，支持法币入金

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API (公共) | `https://api.kraken.com` | 公共数据端点 |
| REST API (私有) | `https://api.kraken.com` | 私有数据端点 |
| WebSocket (公共) | `wss://ws.kraken.com` | 公共数据流 |
| WebSocket (私有) | `wss://ws-auth.kraken.com` | 私有数据流 |

## 认证方式

### API密钥获取

1. 登录Kraken账户
2. 进入 Settings → API 页面
3. 创建新的API密钥
4. 设置以下信息：
   - API Key
   - Private Key
5. 配置API权限：
   - Query Funds
   - Query Open Orders & Trades
   - Query Closed Orders & Trades
   - Create & Modify Orders
   - Cancel/Close Orders
   - Withdraw Funds (可选)
6. 可选：设置nonce窗口
7. 可选：设置IP白名单

### 请求签名方法

Kraken使用HMAC SHA512签名算法。

**签名步骤**:

1. 构建POST数据: `nonce + POST数据`
2. 计算SHA256哈希: `SHA256(nonce + POST数据)`
3. 构建签名消息: `API路径 + SHA256哈希`
4. 使用Private Key进行HMAC SHA512签名
5. 将签名进行Base64编码

**必需的请求头**:

```
API-Key: API Key
API-Sign: Base64编码的签名
Content-Type: application/x-www-form-urlencoded
```

**Python示例**:

```python
import time
import hmac
import hashlib
import base64
import urllib.parse
import requests

API_KEY = '[YOUR_API_KEY]'
PRIVATE_KEY = '[YOUR_PRIVATE_KEY]'
BASE_URL = 'https://api.kraken.com'

def generate_signature(url_path, data, secret):
    """生成Kraken API签名"""
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data['nonce']) + postdata).encode()
    message = url_path.encode() + hashlib.sha256(encoded).digest()
    
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    sigdigest = base64.b64encode(mac.digest())
    return sigdigest.decode()

def kraken_request(uri_path, data=None):
    """发送Kraken API请求"""
    if data is None:
        data = {}
    
    # 添加nonce
    data['nonce'] = str(int(1000 * time.time()))
    
    headers = {
        'API-Key': API_KEY,
        'API-Sign': generate_signature(uri_path, data, PRIVATE_KEY),
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    url = BASE_URL + uri_path
    
    try:
        response = requests.post(url, headers=headers, data=data)
        return response.json()
    except Exception as e:
        print(f"Request failed: {e}")
        return None
```

## 市场数据API

### 1. 获取服务器时间

**端点**: `GET /0/public/Time`

**描述**: 获取服务器时间（用于同步nonce）

**参数**: 无

**响应示例**:
```json
{
  "error": [],
  "result": {
    "unixtime": 1688671955,
    "rfc1123": "Thu,  6 Jul 23 22:52:35 +0000"
  }
}
```

**Python示例**:
```python
def get_server_time():
    """获取服务器时间"""
    url = f"{BASE_URL}/0/public/Time"
    response = requests.get(url)
    return response.json()

# 使用示例
server_time = get_server_time()
print(f"Server time: {server_time['result']['rfc1123']}")
```

### 2. 获取交易对信息

**端点**: `GET /0/public/AssetPairs`

**描述**: 获取可交易的交易对信息

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| pair | String | 否 | 交易对名称，如 XBTUSD |
| info | String | 否 | 信息级别：info, leverage, fees, margin |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "XXBTZUSD": {
      "altname": "XBTUSD",
      "wsname": "XBT/USD",
      "aclass_base": "currency",
      "base": "XXBT",
      "aclass_quote": "currency",
      "quote": "ZUSD",
      "pair_decimals": 1,
      "lot_decimals": 8,
      "lot_multiplier": 1,
      "ordermin": "0.0001",
      "status": "online"
    }
  }
}
```

**Python示例**:
```python
def get_asset_pairs(pair=None):
    """获取交易对信息"""
    url = f"{BASE_URL}/0/public/AssetPairs"
    params = {}
    if pair:
        params['pair'] = pair
    
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
pairs = get_asset_pairs('XBTUSD')
if not pairs['error']:
    for pair_name, pair_info in pairs['result'].items():
        print(f"{pair_info['altname']}: min order {pair_info['ordermin']}")
```

### 3. 获取行情数据

**端点**: `GET /0/public/Ticker`

**描述**: 获取交易对的行情信息

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| pair | String | 是 | 交易对名称（逗号分隔多个） |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "XXBTZUSD": {
      "a": ["50000.00000", "1", "1.000"],
      "b": ["49999.00000", "2", "2.000"],
      "c": ["50000.00000", "0.00100000"],
      "v": ["1234.56789012", "5678.90123456"],
      "p": ["49500.00000", "49800.00000"],
      "t": [1000, 5000],
      "l": ["49000.00000", "48500.00000"],
      "h": ["51000.00000", "51500.00000"],
      "o": "49500.00000"
    }
  }
}
```

**字段说明**:
- a = ask [price, whole lot volume, lot volume]
- b = bid [price, whole lot volume, lot volume]
- c = last trade [price, lot volume]
- v = volume [today, last 24 hours]
- p = volume weighted average price [today, last 24 hours]
- t = number of trades [today, last 24 hours]
- l = low [today, last 24 hours]
- h = high [today, last 24 hours]
- o = today's opening price

### 4. 获取深度数据

**端点**: `GET /0/public/Depth`

**描述**: 获取订单簿数据

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| pair | String | 是 | 交易对名称 |
| count | Integer | 否 | 深度档位数量，最大500 |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "XXBTZUSD": {
      "asks": [
        ["50000.00000", "1.500", 1688671955],
        ["50001.00000", "2.300", 1688671956]
      ],
      "bids": [
        ["49999.00000", "2.100", 1688671955],
        ["49998.00000", "3.200", 1688671954]
      ]
    }
  }
}
```

**Python示例**:
```python
def get_order_book(pair, count=100):
    """获取订单簿"""
    url = f"{BASE_URL}/0/public/Depth"
    params = {
        'pair': pair,
        'count': count
    }
    
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
order_book = get_order_book('XBTUSD', count=20)
if not order_book['error']:
    for pair_name, book in order_book['result'].items():
        print(f"Best bid: {book['bids'][0]}")
        print(f"Best ask: {book['asks'][0]}")
```

### 5. 获取K线数据

**端点**: `GET /0/public/OHLC`

**描述**: 获取OHLC（K线）数据

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| pair | String | 是 | 交易对名称 |
| interval | Integer | 否 | 时间间隔（分钟）：1, 5, 15, 30, 60, 240, 1440, 10080, 21600 |
| since | Integer | 否 | 返回此ID之后的数据 |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "XXBTZUSD": [
      [
        1688671800,
        "50000.0",
        "50500.0",
        "49500.0",
        "50200.0",
        "50100.0",
        "1000.12345678",
        100
      ]
    ],
    "last": 1688671800
  }
}
```

**字段说明**: [time, open, high, low, close, vwap, volume, count]

**Python示例**:
```python
def get_ohlc(pair, interval=60):
    """获取K线数据"""
    url = f"{BASE_URL}/0/public/OHLC"
    params = {
        'pair': pair,
        'interval': interval
    }
    
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
ohlc = get_ohlc('XBTUSD', interval=60)
if not ohlc['error']:
    for pair_name, candles in ohlc['result'].items():
        if pair_name != 'last':
            for candle in candles[:3]:
                print(f"Time: {candle[0]}, Open: {candle[1]}, Close: {candle[4]}")
```


## 交易API

### 1. 下单

**端点**: `POST /0/private/AddOrder`

**描述**: 创建新订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| nonce | Integer | 是 | 递增的整数 |
| ordertype | String | 是 | 订单类型：market, limit, stop-loss, take-profit |
| type | String | 是 | 订单方向：buy, sell |
| volume | String | 是 | 订单数量 |
| pair | String | 是 | 交易对名称 |
| price | String | 否 | 限价单价格 |
| userref | Integer | 否 | 用户参考ID |
| validate | Boolean | 否 | 仅验证订单，不实际下单 |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "descr": {
      "order": "buy 0.001 XBTUSD @ limit 50000.0"
    },
    "txid": ["OUF4EM-FRGI2-MQMWZD"]
  }
}
```

**Python示例**:
```python
def add_order(pair, order_type, side, volume, price=None):
    """下单"""
    uri_path = '/0/private/AddOrder'
    
    data = {
        'pair': pair,
        'type': side,
        'ordertype': order_type,
        'volume': str(volume)
    }
    
    if order_type == 'limit' and price:
        data['price'] = str(price)
    
    try:
        result = kraken_request(uri_path, data)
        return result
    except Exception as e:
        print(f"Order failed: {e}")
        return None

# 使用示例 - 限价买单
order = add_order(
    pair='XBTUSD',
    order_type='limit',
    side='buy',
    volume='0.001',
    price='50000'
)
print(f"Order placed: {order}")

# 使用示例 - 市价卖单
order = add_order(
    pair='XBTUSD',
    order_type='market',
    side='sell',
    volume='0.001'
)
print(f"Order placed: {order}")
```

### 2. 撤销订单

**端点**: `POST /0/private/CancelOrder`

**描述**: 撤销未完成的订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| nonce | Integer | 是 | 递增的整数 |
| txid | String | 是 | 订单ID |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "count": 1
  }
}
```

**Python示例**:
```python
def cancel_order(txid):
    """撤销订单"""
    uri_path = '/0/private/CancelOrder'
    data = {'txid': txid}
    
    try:
        result = kraken_request(uri_path, data)
        return result
    except Exception as e:
        print(f"Cancel failed: {e}")
        return None

# 使用示例
result = cancel_order('OUF4EM-FRGI2-MQMWZD')
print(f"Order canceled: {result}")
```

### 3. 批量撤销订单

**端点**: `POST /0/private/CancelAll`

**描述**: 撤销所有未完成订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| nonce | Integer | 是 | 递增的整数 |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "count": 5
  }
}
```

### 4. 查询未完成订单

**端点**: `POST /0/private/OpenOrders`

**描述**: 查询所有未完成订单

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| nonce | Integer | 是 | 递增的整数 |
| trades | Boolean | 否 | 是否包含成交信息 |
| userref | Integer | 否 | 按用户参考ID过滤 |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "open": {
      "OUF4EM-FRGI2-MQMWZD": {
        "refid": null,
        "userref": 0,
        "status": "open",
        "opentm": 1688671955.1234,
        "starttm": 0,
        "expiretm": 0,
        "descr": {
          "pair": "XBTUSD",
          "type": "buy",
          "ordertype": "limit",
          "price": "50000.0",
          "price2": "0",
          "leverage": "none",
          "order": "buy 0.001 XBTUSD @ limit 50000.0"
        },
        "vol": "0.001",
        "vol_exec": "0.000",
        "cost": "0.000",
        "fee": "0.000",
        "price": "0.000",
        "misc": "",
        "oflags": "fciq"
      }
    }
  }
}
```

**Python示例**:
```python
def get_open_orders():
    """查询未完成订单"""
    uri_path = '/0/private/OpenOrders'
    data = {}
    
    try:
        result = kraken_request(uri_path, data)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
orders = get_open_orders()
if orders and not orders['error']:
    open_orders = orders['result']['open']
    print(f"Open orders: {len(open_orders)}")
    for txid, order in open_orders.items():
        descr = order['descr']
        print(f"Order {txid}: {descr['type']} {order['vol']} {descr['pair']} @ {descr['price']}")
```

### 5. 查询历史订单

**端点**: `POST /0/private/ClosedOrders`

**描述**: 查询已完成或已取消的订单

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| nonce | Integer | 是 | 递增的整数 |
| trades | Boolean | 否 | 是否包含成交信息 |
| userref | Integer | 否 | 按用户参考ID过滤 |
| start | Integer | 否 | 起始时间戳 |
| end | Integer | 否 | 结束时间戳 |
| ofs | Integer | 否 | 结果偏移量 |
| closetime | String | 否 | 时间类型：open, close, both |

## 账户管理API

### 1. 查询账户余额

**端点**: `POST /0/private/Balance`

**描述**: 获取账户余额

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| nonce | Integer | 是 | 递增的整数 |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "XXBT": "1.0000000000",
    "ZUSD": "10000.0000",
    "XETH": "10.5000000000"
  }
}
```

**Python示例**:
```python
def get_balance():
    """获取账户余额"""
    uri_path = '/0/private/Balance'
    data = {}
    
    try:
        result = kraken_request(uri_path, data)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
balance = get_balance()
if balance and not balance['error']:
    print("Account balances:")
    for currency, amount in balance['result'].items():
        if float(amount) > 0:
            print(f"{currency}: {amount}")
```

### 2. 查询交易余额

**端点**: `POST /0/private/TradeBalance`

**描述**: 获取交易账户余额摘要

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| nonce | Integer | 是 | 递增的整数 |
| asset | String | 否 | 基础资产，默认ZUSD |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "eb": "10000.0000",
    "tb": "9000.0000",
    "m": "0.0000",
    "n": "0.0000",
    "c": "0.0000",
    "v": "0.0000",
    "e": "9000.0000",
    "mf": "9000.0000"
  }
}
```

**字段说明**:
- eb = equivalent balance (combined balance of all currencies)
- tb = trade balance (combined balance of all equity currencies)
- m = margin amount of open positions
- n = unrealized net profit/loss of open positions
- c = cost basis of open positions
- v = current floating valuation of open positions
- e = equity = trade balance + unrealized net profit/loss
- mf = free margin = equity - initial margin (maximum margin available to open new positions)

### 3. 查询成交记录

**端点**: `POST /0/private/TradesHistory`

**描述**: 获取成交历史

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| nonce | Integer | 是 | 递增的整数 |
| type | String | 否 | 类型：all, any position, closed position, closing position, no position |
| trades | Boolean | 否 | 是否包含相关交易 |
| start | Integer | 否 | 起始时间戳 |
| end | Integer | 否 | 结束时间戳 |
| ofs | Integer | 否 | 结果偏移量 |

**响应示例**:
```json
{
  "error": [],
  "result": {
    "trades": {
      "THVRQM-33VKH-UCI7BS": {
        "ordertxid": "OUF4EM-FRGI2-MQMWZD",
        "postxid": "TKH2SE-M7IF5-CFI7LT",
        "pair": "XXBTZUSD",
        "time": 1688671955.1234,
        "type": "buy",
        "ordertype": "limit",
        "price": "50000.00000",
        "cost": "50.00000",
        "fee": "0.10000",
        "vol": "0.00100000",
        "margin": "0.00000",
        "misc": ""
      }
    },
    "count": 1
  }
}
```

**Python示例**:
```python
def get_trades_history(start=None, end=None):
    """获取成交记录"""
    uri_path = '/0/private/TradesHistory'
    data = {}
    if start:
        data['start'] = start
    if end:
        data['end'] = end
    
    try:
        result = kraken_request(uri_path, data)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
trades = get_trades_history()
if trades and not trades['error']:
    trade_list = trades['result']['trades']
    print(f"Total trades: {trades['result']['count']}")
    for trade_id, trade in list(trade_list.items())[:5]:
        print(f"{trade['type'].upper()} {trade['vol']} {trade['pair']} @ {trade['price']}")
```

## 速率限制

### 全局速率限制

Kraken使用基于计数器的速率限制系统：

| 限制类型 | 初始值 | 恢复速率 | 最大值 | 说明 |
|---------|--------|----------|--------|------|
| 公共API | - | - | - | 无严格限制，建议1次/秒 |
| 私有API | 15 | +0.33/秒 | 20 | 每个API密钥 |
| 交易API | 15 | +0.33/秒 | 20 | 每个API密钥 |

### 计数器消耗

不同的API端点消耗不同的计数器值：

| 端点类别 | 消耗值 |
|---------|--------|
| 查询余额 | 1 |
| 查询订单 | 1 |
| 下单 | 0 |
| 撤单 | 0 |
| 批量撤单 | 0 |
| 查询成交 | 2 |

### 响应头

Kraken不在响应头中返回速率限制信息，但会在超限时返回错误。

### 触发限制后的行为

- **错误代码**: EAPI:Rate limit exceeded
- 建议等待计数器恢复后重试
- 计数器以每秒0.33的速率恢复

### 最佳实践

```python
import time
from functools import wraps

class RateLimiter:
    """Kraken速率限制器"""
    def __init__(self, max_counter=20, recovery_rate=0.33):
        self.counter = max_counter
        self.max_counter = max_counter
        self.recovery_rate = recovery_rate
        self.last_update = time.time()
    
    def update_counter(self):
        """更新计数器"""
        now = time.time()
        elapsed = now - self.last_update
        recovered = elapsed * self.recovery_rate
        self.counter = min(self.max_counter, self.counter + recovered)
        self.last_update = now
    
    def can_make_request(self, cost=1):
        """检查是否可以发起请求"""
        self.update_counter()
        return self.counter >= cost
    
    def consume(self, cost=1):
        """消耗计数器"""
        self.counter -= cost
    
    def wait_if_needed(self, cost=1):
        """如果需要则等待"""
        self.update_counter()
        if self.counter < cost:
            wait_time = (cost - self.counter) / self.recovery_rate
            print(f"Rate limit: waiting {wait_time:.2f}s...")
            time.sleep(wait_time)
            self.counter = 0
        else:
            self.consume(cost)

# 全局速率限制器
rate_limiter = RateLimiter()

def rate_limited_request(cost=1):
    """速率限制装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            rate_limiter.wait_if_needed(cost)
            return func(*args, **kwargs)
        return wrapper
    return decorator

@rate_limited_request(cost=1)
def safe_kraken_request(uri_path, data=None):
    """带速率限制的Kraken请求"""
    return kraken_request(uri_path, data)
```

## WebSocket支持

### WebSocket端点

| 端点类型 | URL | 说明 |
|---------|-----|------|
| 公共数据 | `wss://ws.kraken.com` | 市场数据 |
| 私有数据 | `wss://ws-auth.kraken.com` | 账户和订单数据 |

### 认证方法

私有WebSocket需要通过REST API获取token：

```python
def get_ws_token():
    """获取WebSocket认证token"""
    uri_path = '/0/private/GetWebSocketsToken'
    data = {}
    
    result = kraken_request(uri_path, data)
    if result and not result['error']:
        return result['result']['token']
    return None

# 使用token连接
token = get_ws_token()
```

### 可订阅频道

**公共频道**:
- `ticker` - 行情ticker
- `ohlc` - K线数据
- `trade` - 公共成交
- `spread` - 买卖价差
- `book` - 订单簿

**私有频道**:
- `openOrders` - 未完成订单
- `ownTrades` - 自己的成交

### 订阅格式

```python
import json
import websocket

# 订阅公共频道
subscribe_msg = {
    "event": "subscribe",
    "pair": ["XBT/USD", "ETH/USD"],
    "subscription": {
        "name": "ticker"
    }
}

# 订阅私有频道（需要token）
subscribe_private = {
    "event": "subscribe",
    "subscription": {
        "name": "openOrders",
        "token": token
    }
}
```

### 心跳机制

Kraken WebSocket使用ping/pong心跳：

```python
# 发送ping
ping_msg = {"event": "ping"}

# 服务器响应
# {"event": "pong"}
```

### WebSocket连接示例

```python
import websocket
import json
import threading

def on_message(ws, message):
    """处理接收到的消息"""
    data = json.loads(message)
    print(f"Received: {data}")

def on_error(ws, error):
    """处理错误"""
    print(f"Error: {error}")

def on_close(ws, close_status_code, close_msg):
    """处理连接关闭"""
    print("Connection closed")

def on_open(ws):
    """连接建立后订阅"""
    subscribe_msg = {
        "event": "subscribe",
        "pair": ["XBT/USD"],
        "subscription": {"name": "ticker"}
    }
    ws.send(json.dumps(subscribe_msg))
    print("Subscribed to ticker")

# 创建WebSocket连接
ws_url = "wss://ws.kraken.com"
ws = websocket.WebSocketApp(
    ws_url,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close,
    on_open=on_open
)

# 启动连接
ws_thread = threading.Thread(target=ws.run_forever)
ws_thread.daemon = True
ws_thread.start()
```

## 错误代码

### 常见错误代码

| 错误代码 | 错误消息 | 可能原因 | 处理建议 |
|---------|---------|---------|---------|
| EAPI:Invalid key | Invalid API key | API密钥无效 | 检查API密钥配置 |
| EAPI:Invalid signature | Invalid signature | 签名错误 | 检查签名算法 |
| EAPI:Invalid nonce | Invalid nonce | Nonce无效或重复 | 使用递增的nonce |
| EAPI:Rate limit exceeded | Rate limit exceeded | 超过速率限制 | 等待计数器恢复 |
| EGeneral:Permission denied | Permission denied | 权限不足 | 检查API权限设置 |
| EOrder:Insufficient funds | Insufficient funds | 余额不足 | 检查账户余额 |
| EOrder:Invalid price | Invalid price | 价格无效 | 调整订单价格 |
| EOrder:Unknown order | Unknown order | 订单不存在 | 检查订单ID |
| EService:Unavailable | Service unavailable | 服务不可用 | 稍后重试 |
| EService:Market in cancel_only mode | Market in cancel only mode | 市场仅允许撤单 | 等待市场恢复 |

### 错误处理示例

```python
def handle_kraken_error(response):
    """处理Kraken API错误"""
    if not response:
        return "Network error or timeout"
    
    errors = response.get('error', [])
    if not errors:
        return None  # Success
    
    error_msg = errors[0] if errors else "Unknown error"
    
    error_handlers = {
        'EAPI:Invalid key': "Invalid API key - verify your credentials",
        'EAPI:Invalid signature': "Invalid signature - check signing method",
        'EAPI:Invalid nonce': "Invalid nonce - ensure nonce is increasing",
        'EAPI:Rate limit exceeded': "Rate limit exceeded - wait and retry",
        'EGeneral:Permission denied': "Permission denied - check API permissions",
        'EOrder:Insufficient funds': "Insufficient funds - check balance",
        'EOrder:Invalid price': "Invalid price - adjust order price",
        'EOrder:Unknown order': "Order not found - verify order ID",
        'EService:Unavailable': "Service unavailable - retry later"
    }
    
    for error_key, handler_msg in error_handlers.items():
        if error_key in error_msg:
            return handler_msg
    
    return f"Error: {error_msg}"

# 使用示例
response = add_order('XBTUSD', 'limit', 'buy', '0.001', '50000')
error = handle_kraken_error(response)
if error:
    print(f"Order failed: {error}")
else:
    print("Order placed successfully")
```

## 变更历史

- 2026-02-27: 初始版本创建，基于Kraken REST API v0和WebSocket API v2

