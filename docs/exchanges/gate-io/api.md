# Gate.io API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: v4
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://www.gate.io/docs/developers/apiv4/

## 交易所基本信息
- 官方名称: Gate.io
- 官网: https://www.gate.io
- 交易所类型: CEX (中心化交易所)
- 24h交易量排名: #8 ($750M+)
- 支持的交易对类型: 现货、杠杆、合约、期权
- 支持的币种数量: 1400+
- 特点: 支持大量山寨币，老牌交易所

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://api.gateio.ws/api/v4` | 主端点 |
| WebSocket | `wss://api.gateio.ws/ws/v4/` | 实时数据流 |

## 认证方式

### API密钥获取

1. 登录Gate.io账户
2. 进入 API Keys 页面
3. 创建新的API密钥
4. 设置以下信息：
   - API Key
   - Secret Key
5. 配置API权限（读取、交易、提现等）
6. 可选：绑定IP白名单
7. 保存API密钥信息

### 请求签名方法

Gate.io使用HMAC SHA512签名算法。

**签名步骤**:

1. 构建查询字符串（GET请求）或请求体（POST请求）
2. 计算请求体的SHA512哈希
3. 构建签名字符串: `method\n/api/v4/endpoint\nquery_string\nhashed_payload\ntimestamp`
4. 使用Secret Key进行HMAC SHA512签名
5. 将签名转换为十六进制字符串

**必需的请求头**:

```
KEY: API Key
Timestamp: Unix时间戳（秒）
SIGN: 签名
Content-Type: application/json
```

**Python示例**:

```python
import time
import hmac
import hashlib
import requests

API_KEY = '[YOUR_API_KEY]'
SECRET_KEY = '[YOUR_SECRET_KEY]'
BASE_URL = 'https://api.gateio.ws/api/v4'

def generate_signature(method, url_path, query_string='', payload_string=''):
    """生成Gate.io API签名"""
    timestamp = str(int(time.time()))
    
    # 计算payload的SHA512哈希
    hashed_payload = hashlib.sha512(payload_string.encode()).hexdigest()
    
    # 构建签名字符串
    sign_string = f"{method}\n{url_path}\n{query_string}\n{hashed_payload}\n{timestamp}"
    
    # HMAC SHA512签名
    signature = hmac.new(
        SECRET_KEY.encode(),
        sign_string.encode(),
        hashlib.sha512
    ).hexdigest()
    
    return signature, timestamp

def get_headers(method, url_path, query_string='', payload_string=''):
    """生成请求头"""
    signature, timestamp = generate_signature(method, url_path, query_string, payload_string)
    
    return {
        'KEY': API_KEY,
        'Timestamp': timestamp,
        'SIGN': signature,
        'Content-Type': 'application/json'
    }

def gate_request(method, endpoint, params=None, data=None):
    """发送Gate.io API请求"""
    import json
    from urllib.parse import urlencode
    
    url_path = f'/api/v4{endpoint}'
    query_string = ''
    payload_string = ''
    
    if params:
        query_string = urlencode(sorted(params.items()))
    
    if data:
        payload_string = json.dumps(data)
    
    headers = get_headers(method, url_path, query_string, payload_string)
    url = BASE_URL + endpoint
    
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, params=params)
        elif method == 'POST':
            response = requests.post(url, headers=headers, data=payload_string)
        elif method == 'DELETE':
            response = requests.delete(url, headers=headers, params=params)
        
        return response.json()
    except Exception as e:
        print(f"Request failed: {e}")
        return None
```

## 市场数据API

### 1. 获取交易对列表

**端点**: `GET /spot/currency_pairs`

**描述**: 获取所有现货交易对信息

**参数**: 无

**响应示例**:
```json
[
  {
    "id": "BTC_USDT",
    "base": "BTC",
    "quote": "USDT",
    "fee": "0.2",
    "min_base_amount": "0.0001",
    "min_quote_amount": "1",
    "amount_precision": 4,
    "precision": 2,
    "trade_status": "tradable",
    "sell_start": 0,
    "buy_start": 0
  }
]
```

**Python示例**:
```python
def get_currency_pairs():
    """获取交易对列表"""
    endpoint = '/spot/currency_pairs'
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url)
    return response.json()

# 使用示例
pairs = get_currency_pairs()
if pairs:
    print(f"Total pairs: {len(pairs)}")
    for pair in pairs[:5]:
        print(f"{pair['id']}: min amount {pair['min_base_amount']}")
```

### 2. 获取行情数据

**端点**: `GET /spot/tickers`

**描述**: 获取所有交易对的行情

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency_pair | String | 否 | 交易对，如 BTC_USDT |
| timezone | String | 否 | 时区：utc0, utc8, all |

**响应示例**:
```json
[
  {
    "currency_pair": "BTC_USDT",
    "last": "50000",
    "lowest_ask": "50001",
    "highest_bid": "49999",
    "change_percentage": "2.5",
    "base_volume": "1234.5678",
    "quote_volume": "61728350",
    "high_24h": "51000",
    "low_24h": "49000"
  }
]
```

**Python示例**:
```python
def get_tickers(currency_pair=None):
    """获取行情数据"""
    endpoint = '/spot/tickers'
    params = {}
    if currency_pair:
        params['currency_pair'] = currency_pair
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
tickers = get_tickers('BTC_USDT')
if tickers:
    for ticker in tickers:
        print(f"Price: {ticker['last']}")
        print(f"24h change: {ticker['change_percentage']}%")
```

### 3. 获取订单簿

**端点**: `GET /spot/order_book`

**描述**: 获取订单簿数据

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency_pair | String | 是 | 交易对 |
| interval | String | 否 | 深度聚合：0, 0.1, 0.01, 0.001 |
| limit | Integer | 否 | 深度档位数量，最大100 |
| with_id | Boolean | 否 | 是否返回订单ID |

**响应示例**:
```json
{
  "id": 123456789,
  "current": 1688671955000,
  "update": 1688671955000,
  "asks": [
    ["50001", "1.5"],
    ["50002", "2.3"]
  ],
  "bids": [
    ["49999", "2.1"],
    ["49998", "3.2"]
  ]
}
```

**Python示例**:
```python
def get_order_book(currency_pair, limit=50):
    """获取订单簿"""
    endpoint = '/spot/order_book'
    params = {
        'currency_pair': currency_pair,
        'limit': limit
    }
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
order_book = get_order_book('BTC_USDT', limit=20)
if order_book:
    print(f"Best bid: {order_book['bids'][0]}")
    print(f"Best ask: {order_book['asks'][0]}")
```

### 4. 获取成交记录

**端点**: `GET /spot/trades`

**描述**: 获取最近的成交记录

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency_pair | String | 是 | 交易对 |
| limit | Integer | 否 | 返回数量，最大1000 |
| last_id | String | 否 | 从此ID之后开始返回 |
| reverse | Boolean | 否 | 是否倒序 |

**响应示例**:
```json
[
  {
    "id": "123456789",
    "create_time": "1688671955",
    "create_time_ms": "1688671955000",
    "currency_pair": "BTC_USDT",
    "side": "buy",
    "amount": "0.001",
    "price": "50000"
  }
]
```

### 5. 获取K线数据

**端点**: `GET /spot/candlesticks`

**描述**: 获取K线数据

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency_pair | String | 是 | 交易对 |
| interval | String | 否 | 时间间隔：10s, 1m, 5m, 15m, 30m, 1h, 4h, 8h, 1d, 7d, 30d |
| from | Long | 否 | 开始时间（Unix时间戳） |
| to | Long | 否 | 结束时间（Unix时间戳） |
| limit | Integer | 否 | 返回数量，最大1000 |

**响应示例**:
```json
[
  [
    "1688671800",
    "61728350",
    "50500",
    "49500",
    "50000",
    "50200",
    "1000.12345678"
  ]
]
```

**字段说明**: [timestamp, volume(quote), close, high, low, open, volume(base)]

**Python示例**:
```python
def get_candlesticks(currency_pair, interval='1h', limit=100):
    """获取K线数据"""
    endpoint = '/spot/candlesticks'
    params = {
        'currency_pair': currency_pair,
        'interval': interval,
        'limit': limit
    }
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
candles = get_candlesticks('BTC_USDT', '1h', 24)
if candles:
    for candle in candles[:3]:
        print(f"Time: {candle[0]}, Open: {candle[5]}, Close: {candle[2]}")
```


## 交易API

### 1. 下单

**端点**: `POST /spot/orders`

**描述**: 创建新订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| text | String | 否 | 用户自定义订单标识 |
| currency_pair | String | 是 | 交易对 |
| type | String | 否 | 订单类型：limit, market（默认limit） |
| account | String | 否 | 账户类型：spot, margin, cross_margin |
| side | String | 是 | 订单方向：buy, sell |
| amount | String | 是 | 交易数量 |
| price | String | 否 | 交易价格（限价单必需） |
| time_in_force | String | 否 | 有效期：gtc, ioc, poc, fok |
| iceberg | String | 否 | 冰山订单数量 |
| auto_borrow | Boolean | 否 | 是否自动借贷（杠杆） |

**响应示例**:
```json
{
  "id": "123456789",
  "text": "t-my-custom-id",
  "create_time": "1688671955",
  "update_time": "1688671955",
  "create_time_ms": 1688671955000,
  "update_time_ms": 1688671955000,
  "status": "open",
  "currency_pair": "BTC_USDT",
  "type": "limit",
  "account": "spot",
  "side": "buy",
  "amount": "0.001",
  "price": "50000",
  "time_in_force": "gtc",
  "left": "0.001",
  "filled_total": "0",
  "fee": "0",
  "fee_currency": "USDT",
  "point_fee": "0",
  "gt_fee": "0",
  "gt_discount": false,
  "rebated_fee": "0",
  "rebated_fee_currency": "USDT"
}
```

**Python示例**:
```python
def create_order(currency_pair, side, amount, price=None, order_type='limit'):
    """创建订单"""
    endpoint = '/spot/orders'
    
    data = {
        'currency_pair': currency_pair,
        'side': side,
        'amount': str(amount),
        'type': order_type
    }
    
    if order_type == 'limit':
        if not price:
            raise ValueError("Limit order requires price")
        data['price'] = str(price)
    
    try:
        result = gate_request('POST', endpoint, data=data)
        return result
    except Exception as e:
        print(f"Order failed: {e}")
        return None

# 使用示例 - 限价买单
order = create_order(
    currency_pair='BTC_USDT',
    side='buy',
    amount='0.001',
    price='50000',
    order_type='limit'
)
print(f"Order placed: {order}")

# 使用示例 - 市价卖单
order = create_order(
    currency_pair='BTC_USDT',
    side='sell',
    amount='0.001',
    order_type='market'
)
print(f"Order placed: {order}")
```

### 2. 查询订单列表

**端点**: `GET /spot/orders`

**描述**: 查询订单列表

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency_pair | String | 是 | 交易对 |
| status | String | 是 | 订单状态：open, finished |
| page | Integer | 否 | 页码 |
| limit | Integer | 否 | 每页数量，最大100 |
| account | String | 否 | 账户类型 |

**响应示例**: 返回订单数组，格式同创建订单响应

**Python示例**:
```python
def get_orders(currency_pair, status='open', limit=100):
    """查询订单列表"""
    endpoint = '/spot/orders'
    params = {
        'currency_pair': currency_pair,
        'status': status,
        'limit': limit
    }
    
    try:
        result = gate_request('GET', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
orders = get_orders('BTC_USDT', status='open')
if orders:
    print(f"Open orders: {len(orders)}")
    for order in orders[:5]:
        print(f"Order {order['id']}: {order['side']} {order['amount']} @ {order['price']}")
```

### 3. 撤销订单

**端点**: `DELETE /spot/orders/{order_id}`

**描述**: 撤销单个订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| order_id | String | 是 | 订单ID（路径参数） |
| currency_pair | String | 是 | 交易对（查询参数） |

**响应示例**: 返回被撤销的订单信息

**Python示例**:
```python
def cancel_order(order_id, currency_pair):
    """撤销订单"""
    endpoint = f'/spot/orders/{order_id}'
    params = {'currency_pair': currency_pair}
    
    try:
        result = gate_request('DELETE', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Cancel failed: {e}")
        return None

# 使用示例
result = cancel_order('123456789', 'BTC_USDT')
print(f"Order canceled: {result}")
```

### 4. 批量撤销订单

**端点**: `POST /spot/cancel_batch_orders`

**描述**: 批量撤销订单

**权限**: 需要交易权限

**参数**:

请求体为订单数组，每个订单包含：

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency_pair | String | 是 | 交易对 |
| id | String | 是 | 订单ID |

**响应示例**:
```json
[
  {
    "currency_pair": "BTC_USDT",
    "id": "123456789",
    "succeeded": true,
    "label": "ORDER_CANCELLED",
    "message": ""
  }
]
```

**Python示例**:
```python
def cancel_batch_orders(orders):
    """批量撤销订单
    
    Args:
        orders: 订单列表，格式 [{'currency_pair': 'BTC_USDT', 'id': '123'}]
    """
    endpoint = '/spot/cancel_batch_orders'
    
    try:
        result = gate_request('POST', endpoint, data=orders)
        return result
    except Exception as e:
        print(f"Batch cancel failed: {e}")
        return None

# 使用示例
orders_to_cancel = [
    {'currency_pair': 'BTC_USDT', 'id': '123456789'},
    {'currency_pair': 'BTC_USDT', 'id': '123456790'}
]
result = cancel_batch_orders(orders_to_cancel)
print(f"Batch cancel result: {result}")
```

### 5. 查询单个订单

**端点**: `GET /spot/orders/{order_id}`

**描述**: 查询订单详情

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| order_id | String | 是 | 订单ID（路径参数） |
| currency_pair | String | 是 | 交易对（查询参数） |

**响应示例**: 同创建订单响应格式

## 账户管理API

### 1. 查询现货账户

**端点**: `GET /spot/accounts`

**描述**: 获取现货账户余额

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency | String | 否 | 币种 |

**响应示例**:
```json
[
  {
    "currency": "BTC",
    "available": "0.80000000",
    "locked": "0.20000000"
  },
  {
    "currency": "USDT",
    "available": "9000.00",
    "locked": "1000.00"
  }
]
```

**Python示例**:
```python
def get_spot_accounts(currency=None):
    """获取现货账户余额"""
    endpoint = '/spot/accounts'
    params = {}
    if currency:
        params['currency'] = currency
    
    try:
        result = gate_request('GET', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
accounts = get_spot_accounts()
if accounts:
    print("Account balances:")
    for account in accounts:
        if float(account['available']) > 0 or float(account['locked']) > 0:
            print(f"{account['currency']}: {account['available']} (locked: {account['locked']})")
```

### 2. 查询账户变更记录

**端点**: `GET /spot/account_book`

**描述**: 获取账户变更历史

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency | String | 否 | 币种 |
| from | Long | 否 | 开始时间（Unix时间戳） |
| to | Long | 否 | 结束时间（Unix时间戳） |
| page | Integer | 否 | 页码 |
| limit | Integer | 否 | 每页数量，最大100 |
| type | String | 否 | 变更类型 |

**响应示例**:
```json
[
  {
    "id": "123456789",
    "time": "1688671955",
    "time_ms": 1688671955000,
    "currency": "USDT",
    "change": "-50.00",
    "balance": "9950.00",
    "type": "trade"
  }
]
```

### 3. 查询成交记录

**端点**: `GET /spot/my_trades`

**描述**: 获取个人成交历史

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency_pair | String | 是 | 交易对 |
| limit | Integer | 否 | 返回数量，最大1000 |
| page | Integer | 否 | 页码 |
| order_id | String | 否 | 订单ID |
| account | String | 否 | 账户类型 |
| from | Long | 否 | 开始时间 |
| to | Long | 否 | 结束时间 |

**响应示例**:
```json
[
  {
    "id": "987654321",
    "create_time": "1688671955",
    "create_time_ms": "1688671955000",
    "currency_pair": "BTC_USDT",
    "side": "buy",
    "role": "taker",
    "amount": "0.001",
    "price": "50000",
    "order_id": "123456789",
    "fee": "0.05",
    "fee_currency": "USDT",
    "point_fee": "0",
    "gt_fee": "0"
  }
]
```

**Python示例**:
```python
def get_my_trades(currency_pair, limit=100):
    """获取成交记录"""
    endpoint = '/spot/my_trades'
    params = {
        'currency_pair': currency_pair,
        'limit': limit
    }
    
    try:
        result = gate_request('GET', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
trades = get_my_trades('BTC_USDT', limit=10)
if trades:
    print(f"Recent trades: {len(trades)}")
    for trade in trades[:5]:
        print(f"{trade['side'].upper()} {trade['amount']} @ {trade['price']}")
```

## 速率限制

### 全局速率限制

Gate.io实施基于IP和用户的速率限制：

| 限制类型 | 限制值 | 时间窗口 | 说明 |
|---------|--------|----------|------|
| 公共端点 | 900次 | 1秒 | 每个IP |
| 私有端点 | 900次 | 1秒 | 每个用户 |
| 交易端点 | 100次 | 1秒 | 每个用户 |

### 不同端点的速率限制

| 端点类别 | 限制 |
|---------|------|
| 获取交易对 | 无限制 |
| 获取行情 | 无限制 |
| 获取深度 | 无限制 |
| 查询账户 | 900次/秒 |
| 下单 | 100次/秒 |
| 撤单 | 100次/秒 |
| 查询订单 | 900次/秒 |

### 响应头

速率限制信息包含在响应头中：

```
X-Gate-Ratelimit-Limit: 速率限制值
X-Gate-Ratelimit-Remaining: 剩余请求数
X-Gate-Ratelimit-Reset: 重置时间戳
```

### 触发限制后的行为

- **HTTP 429**: Too Many Requests
- 响应体包含错误信息
- 建议等待重置时间后重试

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
                
                # 检查速率限制
                if isinstance(response, dict) and 'label' in response:
                    if response['label'] == 'TOO_MANY_REQUESTS':
                        if attempt < max_retries - 1:
                            wait_time = retry_delay * (2 ** attempt)
                            print(f"Rate limit hit, waiting {wait_time}s...")
                            time.sleep(wait_time)
                            continue
                
                return response
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                raise
        return None
    return wrapper

@rate_limit_handler
def api_call_with_retry(method, endpoint, **kwargs):
    """带重试的API调用"""
    return gate_request(method, endpoint, **kwargs)
```

## WebSocket支持

### WebSocket端点

| 端点类型 | URL | 说明 |
|---------|-----|------|
| 现货 | `wss://api.gateio.ws/ws/v4/` | 现货市场数据 |

### 认证方法

WebSocket私有频道需要认证：

```python
import json
import time
import hmac
import hashlib

def generate_ws_signature(channel, event, timestamp):
    """生成WebSocket认证签名"""
    message = f"channel={channel}&event={event}&time={timestamp}"
    signature = hmac.new(
        SECRET_KEY.encode(),
        message.encode(),
        hashlib.sha512
    ).hexdigest()
    return signature

def authenticate_websocket(ws):
    """WebSocket认证"""
    timestamp = int(time.time())
    channel = "spot.orders"
    event = "subscribe"
    
    signature = generate_ws_signature(channel, event, timestamp)
    
    auth_msg = {
        "time": timestamp,
        "channel": channel,
        "event": event,
        "payload": {
            "api_key": API_KEY,
            "signature": signature,
            "timestamp": str(timestamp)
        }
    }
    
    ws.send(json.dumps(auth_msg))
```

### 可订阅频道

**公共频道**:
- `spot.tickers` - 行情ticker
- `spot.trades` - 公共成交
- `spot.candlesticks` - K线数据
- `spot.order_book` - 订单簿
- `spot.order_book_update` - 订单簿增量更新

**私有频道**:
- `spot.orders` - 订单更新
- `spot.usertrades` - 用户成交
- `spot.balances` - 余额更新

### 订阅格式

```python
# 订阅公共频道
subscribe_msg = {
    "time": int(time.time()),
    "channel": "spot.tickers",
    "event": "subscribe",
    "payload": ["BTC_USDT", "ETH_USDT"]
}

# 取消订阅
unsubscribe_msg = {
    "time": int(time.time()),
    "channel": "spot.tickers",
    "event": "unsubscribe",
    "payload": ["BTC_USDT"]
}
```

### 心跳机制

Gate.io WebSocket使用ping/pong心跳：

```python
# 发送ping
ping_msg = {
    "time": int(time.time()),
    "channel": "spot.ping"
}

# 服务器响应pong
# {"time": ..., "channel": "spot.pong"}
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
        "time": int(time.time()),
        "channel": "spot.tickers",
        "event": "subscribe",
        "payload": ["BTC_USDT"]
    }
    ws.send(json.dumps(subscribe_msg))
    print("Subscribed to ticker")

# 创建WebSocket连接
ws_url = "wss://api.gateio.ws/ws/v4/"
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

| 错误标签 | 错误消息 | 可能原因 | 处理建议 |
|---------|---------|---------|---------|
| INVALID_PARAM_VALUE | Invalid parameter | 参数错误 | 检查请求参数 |
| INVALID_PROTOCOL | Invalid protocol | 协议错误 | 检查请求格式 |
| INVALID_ARGUMENT | Invalid argument | 参数无效 | 检查参数类型 |
| INVALID_REQUEST_BODY | Invalid request body | 请求体错误 | 检查JSON格式 |
| INVALID_SIGNATURE | Invalid signature | 签名错误 | 检查签名算法 |
| INVALID_KEY | Invalid key | API密钥无效 | 检查API密钥 |
| IP_FORBIDDEN | IP forbidden | IP被禁止 | 检查IP白名单 |
| READ_ONLY | Read only | 只读权限 | 检查API权限 |
| INVALID_CREDENTIALS | Invalid credentials | 认证失败 | 检查认证信息 |
| TOO_MANY_REQUESTS | Too many requests | 超过速率限制 | 等待后重试 |
| INSUFFICIENT_BALANCE | Insufficient balance | 余额不足 | 检查账户余额 |
| ORDER_NOT_FOUND | Order not found | 订单不存在 | 检查订单ID |
| ORDER_CLOSED | Order closed | 订单已关闭 | 订单已完成或取消 |
| INTERNAL | Internal error | 服务器错误 | 稍后重试 |

### 错误处理示例

```python
def handle_gate_error(response):
    """处理Gate.io API错误"""
    if not response:
        return "Network error or timeout"
    
    # Gate.io错误响应格式
    if isinstance(response, dict) and 'label' in response:
        label = response['label']
        message = response.get('message', '')
        
        error_handlers = {
            'INVALID_PARAM_VALUE': "Invalid parameter - check request parameters",
            'INVALID_SIGNATURE': "Invalid signature - check signing method",
            'INVALID_KEY': "Invalid API key - verify credentials",
            'IP_FORBIDDEN': "IP forbidden - check IP whitelist",
            'READ_ONLY': "Read only - check API permissions",
            'INVALID_CREDENTIALS': "Invalid credentials - check authentication",
            'TOO_MANY_REQUESTS': "Rate limit exceeded - wait and retry",
            'INSUFFICIENT_BALANCE': "Insufficient balance - check account balance",
            'ORDER_NOT_FOUND': "Order not found - verify order ID",
            'ORDER_CLOSED': "Order closed - order completed or cancelled",
            'INTERNAL': "Internal server error - retry later"
        }
        
        error_msg = error_handlers.get(label, f"Error {label}: {message}")
        return error_msg
    
    return None  # Success

# 使用示例
response = create_order('BTC_USDT', 'buy', '0.001', '50000')
error = handle_gate_error(response)
if error:
    print(f"Order failed: {error}")
else:
    print("Order placed successfully")
```

## 变更历史

- 2026-02-27: 初始版本创建，基于Gate.io API v4

