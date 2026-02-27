# KuCoin API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: v2 (REST), v3 (部分端点)
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://docs.kucoin.com/

## 交易所基本信息
- 官方名称: KuCoin
- 官网: https://www.kucoin.com
- 交易所类型: CEX (中心化交易所)
- 24h交易量排名: #7 ($840M+)
- 支持的交易对类型: 现货、杠杆、合约
- 支持的币种数量: 700+
- 特点: "人民的交易所"，支持大量小币种

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://api.kucoin.com` | 主端点 |
| REST API (期货) | `https://api-futures.kucoin.com` | 期货端点 |
| WebSocket (公共) | 动态获取 | 通过REST API获取 |
| WebSocket (私有) | 动态获取 | 通过REST API获取 |

## 认证方式

### API密钥获取

1. 登录KuCoin账户
2. 进入 API Management 页面
3. 创建新的API密钥
4. 设置以下信息：
   - API Key
   - Secret Key
   - Passphrase（自定义密码短语）
5. 配置API权限（通用、交易、提现等）
6. 可选：绑定IP白名单
7. 保存API密钥信息

### 请求签名方法

KuCoin使用HMAC SHA256签名算法。

**签名步骤**:

1. 构建签名字符串: `timestamp + method + requestEndpoint + body`
2. 使用Secret Key进行HMAC SHA256签名
3. 将签名进行Base64编码
4. 使用Passphrase进行HMAC SHA256签名并Base64编码

**必需的请求头**:

```
KC-API-KEY: API Key
KC-API-SIGN: 签名
KC-API-TIMESTAMP: 时间戳（毫秒）
KC-API-PASSPHRASE: 加密后的Passphrase
KC-API-KEY-VERSION: 2
Content-Type: application/json
```

**Python示例**:

```python
import time
import hmac
import hashlib
import base64
import requests

API_KEY = '[YOUR_API_KEY]'
SECRET_KEY = '[YOUR_SECRET_KEY]'
PASSPHRASE = '[YOUR_PASSPHRASE]'
BASE_URL = 'https://api.kucoin.com'

def generate_signature(timestamp, method, endpoint, body=''):
    """生成KuCoin API签名"""
    str_to_sign = str(timestamp) + method + endpoint + body
    signature = base64.b64encode(
        hmac.new(
            SECRET_KEY.encode('utf-8'),
            str_to_sign.encode('utf-8'),
            hashlib.sha256
        ).digest()
    )
    return signature.decode('utf-8')

def generate_passphrase():
    """生成加密的Passphrase"""
    passphrase = base64.b64encode(
        hmac.new(
            SECRET_KEY.encode('utf-8'),
            PASSPHRASE.encode('utf-8'),
            hashlib.sha256
        ).digest()
    )
    return passphrase.decode('utf-8')

def get_headers(method, endpoint, body=''):
    """生成请求头"""
    timestamp = int(time.time() * 1000)
    signature = generate_signature(timestamp, method, endpoint, body)
    passphrase = generate_passphrase()
    
    return {
        'KC-API-KEY': API_KEY,
        'KC-API-SIGN': signature,
        'KC-API-TIMESTAMP': str(timestamp),
        'KC-API-PASSPHRASE': passphrase,
        'KC-API-KEY-VERSION': '2',
        'Content-Type': 'application/json'
    }

def kucoin_request(method, endpoint, params=None, data=None):
    """发送KuCoin API请求"""
    import json
    
    body = ''
    if data:
        body = json.dumps(data)
    
    headers = get_headers(method, endpoint, body)
    url = BASE_URL + endpoint
    
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, params=params)
        elif method == 'POST':
            response = requests.post(url, headers=headers, data=body)
        elif method == 'DELETE':
            response = requests.delete(url, headers=headers)
        
        return response.json()
    except Exception as e:
        print(f"Request failed: {e}")
        return None
```

## 市场数据API

### 1. 获取交易对列表

**端点**: `GET /api/v2/symbols`

**描述**: 获取所有可交易的交易对信息

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| market | String | 否 | 市场类型：USDS, BTC, KCS, ALTS |

**响应示例**:
```json
{
  "code": "200000",
  "data": [
    {
      "symbol": "BTC-USDT",
      "name": "BTC-USDT",
      "baseCurrency": "BTC",
      "quoteCurrency": "USDT",
      "feeCurrency": "USDT",
      "market": "USDS",
      "baseMinSize": "0.00001",
      "quoteMinSize": "0.1",
      "baseMaxSize": "10000",
      "quoteMaxSize": "99999999",
      "baseIncrement": "0.00000001",
      "quoteIncrement": "0.01",
      "priceIncrement": "0.1",
      "priceLimitRate": "0.1",
      "minFunds": "0.1",
      "isMarginEnabled": true,
      "enableTrading": true
    }
  ]
}
```

**Python示例**:
```python
def get_symbols(market=None):
    """获取交易对列表"""
    endpoint = '/api/v2/symbols'
    params = {}
    if market:
        params['market'] = market
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
symbols = get_symbols('USDS')
if symbols['code'] == '200000':
    print(f"Total symbols: {len(symbols['data'])}")
    for symbol in symbols['data'][:5]:
        print(f"{symbol['symbol']}: min size {symbol['baseMinSize']}")
```

### 2. 获取行情数据

**端点**: `GET /api/v1/market/orderbook/level1`

**描述**: 获取交易对的最新行情

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | String | 是 | 交易对，如 BTC-USDT |

**响应示例**:
```json
{
  "code": "200000",
  "data": {
    "time": 1688671955000,
    "sequence": "1234567890",
    "price": "50000",
    "size": "0.001",
    "bestBid": "49999",
    "bestBidSize": "1.5",
    "bestAsk": "50001",
    "bestAskSize": "2.3"
  }
}
```

**Python示例**:
```python
def get_ticker(symbol):
    """获取行情数据"""
    endpoint = '/api/v1/market/orderbook/level1'
    params = {'symbol': symbol}
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
ticker = get_ticker('BTC-USDT')
if ticker['code'] == '200000':
    data = ticker['data']
    print(f"Price: {data['price']}")
    print(f"Best bid: {data['bestBid']}")
    print(f"Best ask: {data['bestAsk']}")
```

### 3. 获取24小时统计

**端点**: `GET /api/v1/market/stats`

**描述**: 获取24小时交易统计

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | String | 是 | 交易对 |

**响应示例**:
```json
{
  "code": "200000",
  "data": {
    "time": 1688671955000,
    "symbol": "BTC-USDT",
    "buy": "50000",
    "sell": "50001",
    "changeRate": "0.025",
    "changePrice": "1225",
    "high": "51000",
    "low": "49000",
    "vol": "1234.56789",
    "volValue": "61728350",
    "last": "50000",
    "averagePrice": "50100",
    "takerFeeRate": "0.001",
    "makerFeeRate": "0.001",
    "takerCoefficient": "1",
    "makerCoefficient": "1"
  }
}
```

### 4. 获取深度数据

**端点**: `GET /api/v1/market/orderbook/level2_100`

**描述**: 获取订单簿数据（100档）

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | String | 是 | 交易对 |

**响应示例**:
```json
{
  "code": "200000",
  "data": {
    "time": 1688671955000,
    "sequence": "1234567890",
    "bids": [
      ["49999", "1.5"],
      ["49998", "2.3"]
    ],
    "asks": [
      ["50001", "1.8"],
      ["50002", "3.2"]
    ]
  }
}
```

**Python示例**:
```python
def get_order_book(symbol):
    """获取订单簿"""
    endpoint = '/api/v1/market/orderbook/level2_100'
    params = {'symbol': symbol}
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
order_book = get_order_book('BTC-USDT')
if order_book['code'] == '200000':
    data = order_book['data']
    print(f"Best bid: {data['bids'][0]}")
    print(f"Best ask: {data['asks'][0]}")
```

### 5. 获取K线数据

**端点**: `GET /api/v1/market/candles`

**描述**: 获取K线数据

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | String | 是 | 交易对 |
| type | String | 否 | K线类型：1min, 3min, 5min, 15min, 30min, 1hour, 2hour, 4hour, 6hour, 8hour, 12hour, 1day, 1week |
| startAt | Long | 否 | 开始时间（秒） |
| endAt | Long | 否 | 结束时间（秒） |

**响应示例**:
```json
{
  "code": "200000",
  "data": [
    [
      "1688671800",
      "50000",
      "50500",
      "49500",
      "50200",
      "1000.12345678",
      "50100000"
    ]
  ]
}
```

**字段说明**: [time, open, close, high, low, volume, turnover]

**Python示例**:
```python
def get_klines(symbol, kline_type='1hour'):
    """获取K线数据"""
    endpoint = '/api/v1/market/candles'
    params = {
        'symbol': symbol,
        'type': kline_type
    }
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例
klines = get_klines('BTC-USDT', '1hour')
if klines['code'] == '200000':
    for kline in klines['data'][:3]:
        print(f"Time: {kline[0]}, Open: {kline[1]}, Close: {kline[2]}")
```


## 交易API

### 1. 下单

**端点**: `POST /api/v1/orders`

**描述**: 创建新订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| clientOid | String | 是 | 客户自定义订单ID（唯一） |
| side | String | 是 | 订单方向：buy, sell |
| symbol | String | 是 | 交易对 |
| type | String | 否 | 订单类型：limit, market（默认limit） |
| price | String | 否 | 限价单价格 |
| size | String | 否 | 订单数量（限价单必需） |
| funds | String | 否 | 订单金额（市价买单必需） |
| timeInForce | String | 否 | 有效期：GTC, GTT, IOC, FOK |
| postOnly | Boolean | 否 | 仅做Maker |
| hidden | Boolean | 否 | 隐藏订单 |
| iceberg | Boolean | 否 | 冰山订单 |

**响应示例**:
```json
{
  "code": "200000",
  "data": {
    "orderId": "5bd6e9286d99522a52e458de"
  }
}
```

**Python示例**:
```python
import uuid

def place_order(symbol, side, order_type, size=None, price=None, funds=None):
    """下单"""
    endpoint = '/api/v1/orders'
    
    client_oid = str(uuid.uuid4())
    
    data = {
        'clientOid': client_oid,
        'side': side,
        'symbol': symbol,
        'type': order_type
    }
    
    if order_type == 'limit':
        if not size or not price:
            raise ValueError("Limit order requires size and price")
        data['size'] = str(size)
        data['price'] = str(price)
    elif order_type == 'market':
        if side == 'buy':
            if not funds:
                raise ValueError("Market buy order requires funds")
            data['funds'] = str(funds)
        else:
            if not size:
                raise ValueError("Market sell order requires size")
            data['size'] = str(size)
    
    try:
        result = kucoin_request('POST', endpoint, data=data)
        return result
    except Exception as e:
        print(f"Order failed: {e}")
        return None

# 使用示例 - 限价买单
order = place_order(
    symbol='BTC-USDT',
    side='buy',
    order_type='limit',
    size='0.001',
    price='50000'
)
print(f"Order placed: {order}")

# 使用示例 - 市价卖单
order = place_order(
    symbol='BTC-USDT',
    side='sell',
    order_type='market',
    size='0.001'
)
print(f"Order placed: {order}")
```

### 2. 撤销订单

**端点**: `DELETE /api/v1/orders/{orderId}`

**描述**: 撤销单个订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| orderId | String | 是 | 订单ID（路径参数） |

**响应示例**:
```json
{
  "code": "200000",
  "data": {
    "cancelledOrderIds": [
      "5bd6e9286d99522a52e458de"
    ]
  }
}
```

**Python示例**:
```python
def cancel_order(order_id):
    """撤销订单"""
    endpoint = f'/api/v1/orders/{order_id}'
    
    try:
        result = kucoin_request('DELETE', endpoint)
        return result
    except Exception as e:
        print(f"Cancel failed: {e}")
        return None

# 使用示例
result = cancel_order('5bd6e9286d99522a52e458de')
print(f"Order canceled: {result}")
```

### 3. 批量撤销订单

**端点**: `DELETE /api/v1/orders`

**描述**: 撤销所有订单或指定交易对的订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| symbol | String | 否 | 交易对（不传则撤销所有） |
| tradeType | String | 否 | 交易类型：TRADE, MARGIN_TRADE |

**响应示例**:
```json
{
  "code": "200000",
  "data": {
    "cancelledOrderIds": [
      "5bd6e9286d99522a52e458de",
      "5bd6e9286d99522a52e458df"
    ]
  }
}
```

### 4. 查询订单列表

**端点**: `GET /api/v1/orders`

**描述**: 查询订单列表

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| status | String | 否 | 订单状态：active, done |
| symbol | String | 否 | 交易对 |
| side | String | 否 | 订单方向：buy, sell |
| type | String | 否 | 订单类型：limit, market |
| startAt | Long | 否 | 开始时间（毫秒） |
| endAt | Long | 否 | 结束时间（毫秒） |

**响应示例**:
```json
{
  "code": "200000",
  "data": {
    "currentPage": 1,
    "pageSize": 50,
    "totalNum": 1,
    "totalPage": 1,
    "items": [
      {
        "id": "5bd6e9286d99522a52e458de",
        "symbol": "BTC-USDT",
        "opType": "DEAL",
        "type": "limit",
        "side": "buy",
        "price": "50000",
        "size": "0.001",
        "funds": "0",
        "dealFunds": "50",
        "dealSize": "0.001",
        "fee": "0.05",
        "feeCurrency": "USDT",
        "stp": "",
        "stop": "",
        "stopTriggered": false,
        "stopPrice": "0",
        "timeInForce": "GTC",
        "postOnly": false,
        "hidden": false,
        "iceberg": false,
        "visibleSize": "0",
        "cancelAfter": 0,
        "channel": "API",
        "clientOid": "client-order-id",
        "remark": null,
        "tags": null,
        "isActive": false,
        "cancelExist": false,
        "createdAt": 1688671955000,
        "tradeType": "TRADE"
      }
    ]
  }
}
```

**Python示例**:
```python
def get_orders(symbol=None, status='active'):
    """查询订单列表"""
    endpoint = '/api/v1/orders'
    params = {'status': status}
    if symbol:
        params['symbol'] = symbol
    
    try:
        result = kucoin_request('GET', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
orders = get_orders(symbol='BTC-USDT', status='active')
if orders and orders['code'] == '200000':
    items = orders['data']['items']
    print(f"Active orders: {len(items)}")
    for order in items[:5]:
        print(f"Order {order['id']}: {order['side']} {order['size']} @ {order['price']}")
```

### 5. 查询单个订单

**端点**: `GET /api/v1/orders/{orderId}`

**描述**: 查询订单详情

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| orderId | String | 是 | 订单ID（路径参数） |

**响应示例**: 同订单列表中的单个订单格式

## 账户管理API

### 1. 查询账户列表

**端点**: `GET /api/v1/accounts`

**描述**: 获取账户列表

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency | String | 否 | 币种 |
| type | String | 否 | 账户类型：main, trade, margin |

**响应示例**:
```json
{
  "code": "200000",
  "data": [
    {
      "id": "5bd6e9286d99522a52e458de",
      "currency": "BTC",
      "type": "trade",
      "balance": "1.00000000",
      "available": "0.80000000",
      "holds": "0.20000000"
    },
    {
      "id": "5bd6e9286d99522a52e458df",
      "currency": "USDT",
      "type": "trade",
      "balance": "10000.00",
      "available": "9000.00",
      "holds": "1000.00"
    }
  ]
}
```

**Python示例**:
```python
def get_accounts(currency=None, account_type='trade'):
    """获取账户列表"""
    endpoint = '/api/v1/accounts'
    params = {}
    if currency:
        params['currency'] = currency
    if account_type:
        params['type'] = account_type
    
    try:
        result = kucoin_request('GET', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
accounts = get_accounts(account_type='trade')
if accounts and accounts['code'] == '200000':
    print("Account balances:")
    for account in accounts['data']:
        if float(account['balance']) > 0:
            print(f"{account['currency']}: {account['available']} (holds: {account['holds']})")
```

### 2. 查询单个账户

**端点**: `GET /api/v1/accounts/{accountId}`

**描述**: 获取单个账户详情

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| accountId | String | 是 | 账户ID（路径参数） |

**响应示例**: 同账户列表中的单个账户格式

### 3. 查询成交记录

**端点**: `GET /api/v1/fills`

**描述**: 获取成交历史

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| orderId | String | 否 | 订单ID |
| symbol | String | 否 | 交易对 |
| side | String | 否 | 订单方向：buy, sell |
| type | String | 否 | 订单类型：limit, market |
| startAt | Long | 否 | 开始时间（毫秒） |
| endAt | Long | 否 | 结束时间（毫秒） |

**响应示例**:
```json
{
  "code": "200000",
  "data": {
    "currentPage": 1,
    "pageSize": 50,
    "totalNum": 1,
    "totalPage": 1,
    "items": [
      {
        "symbol": "BTC-USDT",
        "tradeId": "5c35c02709e4f67d5266954e",
        "orderId": "5c35c02703aa673ceec2a168",
        "counterOrderId": "5c1ab46003aa676e487fa8e3",
        "side": "buy",
        "liquidity": "taker",
        "forceTaker": true,
        "price": "50000",
        "size": "0.001",
        "funds": "50",
        "fee": "0.05",
        "feeRate": "0.001",
        "feeCurrency": "USDT",
        "stop": "",
        "type": "limit",
        "createdAt": 1688671955000,
        "tradeType": "TRADE"
      }
    ]
  }
}
```

**Python示例**:
```python
def get_fills(symbol=None, order_id=None):
    """获取成交记录"""
    endpoint = '/api/v1/fills'
    params = {}
    if symbol:
        params['symbol'] = symbol
    if order_id:
        params['orderId'] = order_id
    
    try:
        result = kucoin_request('GET', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
fills = get_fills(symbol='BTC-USDT')
if fills and fills['code'] == '200000':
    items = fills['data']['items']
    print(f"Recent fills: {len(items)}")
    for fill in items[:5]:
        print(f"{fill['side'].upper()} {fill['size']} @ {fill['price']}")
```

## 速率限制

### 全局速率限制

KuCoin使用基于权重的速率限制系统：

| 限制类型 | 限制值 | 时间窗口 | 说明 |
|---------|--------|----------|------|
| 公共端点 | 无限制 | - | 建议不超过100次/10秒 |
| 私有端点 | 200次 | 10秒 | 每个API密钥 |
| 交易端点 | 45次 | 3秒 | 每个API密钥 |

### 不同端点的权重

| 端点类别 | 权重 |
|---------|------|
| 获取交易对 | 1 |
| 获取行情 | 1 |
| 获取深度 | 2 |
| 查询账户 | 5 |
| 下单 | 2 |
| 撤单 | 2 |
| 查询订单 | 2 |

### 响应头

速率限制信息包含在响应头中：

```
X-RateLimit-Limit: 速率限制值
X-RateLimit-Remaining: 剩余请求数
X-RateLimit-Reset: 重置时间戳（毫秒）
```

### 触发限制后的行为

- **HTTP 429**: Too Many Requests
- 响应体包含错误代码429000
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
                if isinstance(response, dict) and response.get('code') == '429000':
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
    return kucoin_request(method, endpoint, **kwargs)
```

## WebSocket支持

### 获取WebSocket连接信息

KuCoin的WebSocket端点是动态的，需要先通过REST API获取：

**端点**: `POST /api/v1/bullet-public` (公共) 或 `POST /api/v1/bullet-private` (私有)

**响应示例**:
```json
{
  "code": "200000",
  "data": {
    "token": "2neAiuYvAU61ZDXANAGAsiL4-iAExhsBXZxftpOeh_55i3Ysy2q2LEsEWU64mdzUOPusi34M_wGoSf7iNyEWJ1UQy47YbpY4zVdzilNP-Bj3iXzrjjGlWtiYB9J6i9GjsxUuhPw3BlrzazF6ghq4Lzf7scStOz3KkxjwpsOBCH4=.WNQmhZQeUKIkh97KYgU0Lg==",
    "instanceServers": [
      {
        "endpoint": "wss://ws-api-spot.kucoin.com/",
        "encrypt": true,
        "protocol": "websocket",
        "pingInterval": 18000,
        "pingTimeout": 10000
      }
    ]
  }
}
```

### 认证方法

使用获取的token连接WebSocket：

```python
import json
import websocket

def get_ws_token(is_private=False):
    """获取WebSocket token"""
    endpoint = '/api/v1/bullet-private' if is_private else '/api/v1/bullet-public'
    
    if is_private:
        result = kucoin_request('POST', endpoint)
    else:
        url = f"{BASE_URL}{endpoint}"
        response = requests.post(url)
        result = response.json()
    
    if result['code'] == '200000':
        return result['data']
    return None

def connect_websocket(is_private=False):
    """连接WebSocket"""
    ws_data = get_ws_token(is_private)
    if not ws_data:
        return None
    
    token = ws_data['token']
    endpoint = ws_data['instanceServers'][0]['endpoint']
    ws_url = f"{endpoint}?token={token}&[connectId={int(time.time() * 1000)}]"
    
    return ws_url
```

### 可订阅频道

**公共频道**:
- `/market/ticker:{symbol}` - 行情ticker
- `/market/level2:{symbol}` - 订单簿
- `/market/match:{symbol}` - 成交数据
- `/market/candles:{symbol}_{type}` - K线数据

**私有频道**:
- `/spotMarket/tradeOrders` - 订单更新
- `/account/balance` - 账户余额更新

### 订阅格式

```python
# 订阅消息格式
subscribe_msg = {
    "id": int(time.time() * 1000),
    "type": "subscribe",
    "topic": "/market/ticker:BTC-USDT",
    "privateChannel": False,
    "response": True
}

# 取消订阅
unsubscribe_msg = {
    "id": int(time.time() * 1000),
    "type": "unsubscribe",
    "topic": "/market/ticker:BTC-USDT",
    "privateChannel": False,
    "response": True
}
```

### 心跳机制

KuCoin WebSocket使用ping/pong心跳：

```python
# 发送ping
ping_msg = {
    "id": int(time.time() * 1000),
    "type": "ping"
}

# 服务器响应pong
# {"id": "...", "type": "pong"}
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
        "id": int(time.time() * 1000),
        "type": "subscribe",
        "topic": "/market/ticker:BTC-USDT",
        "privateChannel": False,
        "response": True
    }
    ws.send(json.dumps(subscribe_msg))
    print("Subscribed to ticker")

# 获取WebSocket URL
ws_url = connect_websocket(is_private=False)

if ws_url:
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
| 200000 | Success | 成功 | - |
| 400001 | Any of KC-API-KEY, KC-API-SIGN, KC-API-TIMESTAMP, KC-API-PASSPHRASE is missing | 缺少认证头 | 检查请求头 |
| 400002 | Invalid KC-API-TIMESTAMP | 时间戳无效 | 同步系统时间 |
| 400003 | Invalid KC-API-KEY | API密钥无效 | 检查API密钥 |
| 400004 | Invalid KC-API-PASSPHRASE | Passphrase错误 | 检查Passphrase |
| 400005 | Invalid KC-API-SIGN | 签名错误 | 检查签名算法 |
| 400006 | The requested ip is not in the whitelist | IP不在白名单 | 添加IP到白名单 |
| 400007 | Access Denied | 权限不足 | 检查API权限 |
| 400100 | Parameter Error | 参数错误 | 检查请求参数 |
| 411100 | User is frozen | 用户被冻结 | 联系客服 |
| 429000 | Too Many Requests | 超过速率限制 | 等待后重试 |
| 500000 | Internal Server Error | 服务器错误 | 稍后重试 |
| 900001 | symbol not exists | 交易对不存在 | 检查交易对名称 |

### 错误处理示例

```python
def handle_kucoin_error(response):
    """处理KuCoin API错误"""
    if not response:
        return "Network error or timeout"
    
    code = response.get('code', '500000')
    msg = response.get('msg', 'Unknown error')
    
    if code == '200000':
        return None  # Success
    
    error_handlers = {
        '400001': "Missing authentication headers - check request headers",
        '400002': "Invalid timestamp - sync system time",
        '400003': "Invalid API key - verify credentials",
        '400004': "Invalid passphrase - check passphrase",
        '400005': "Invalid signature - check signing method",
        '400006': "IP not whitelisted - add IP to whitelist",
        '400007': "Access denied - check API permissions",
        '400100': "Parameter error - check request parameters",
        '411100': "User frozen - contact support",
        '429000': "Rate limit exceeded - wait and retry",
        '500000': "Internal server error - retry later",
        '900001': "Symbol not exists - check symbol name"
    }
    
    error_msg = error_handlers.get(code, f"Error {code}: {msg}")
    return error_msg

# 使用示例
response = place_order('BTC-USDT', 'buy', 'limit', '0.001', '50000')
error = handle_kucoin_error(response)
if error:
    print(f"Order failed: {error}")
else:
    print("Order placed successfully")
```

## 变更历史

- 2026-02-27: 初始版本创建，基于KuCoin API v2

