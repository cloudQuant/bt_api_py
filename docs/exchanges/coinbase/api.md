# Coinbase API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: v2 (REST), v3 (Advanced Trade)
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://docs.cloud.coinbase.com/

## 交易所基本信息
- 官方名称: Coinbase
- 官网: https://www.coinbase.com
- 交易所类型: CEX (中心化交易所)
- 24h交易量排名: #4 ($1.5B+)
- 支持的交易对类型: 现货
- 支持的币种数量: 250+
- 特点: 美国最大合规交易所，支持法币入金

## API基础URL

Coinbase提供多个API产品：

| API类型 | URL | 说明 |
|---------|-----|------|
| Advanced Trade API | `https://api.coinbase.com/api/v3` | 高级交易API（推荐） |
| Exchange API | `https://api.exchange.coinbase.com` | Coinbase Pro API |
| Wallet API | `https://api.coinbase.com/v2` | 钱包API |
| WebSocket | `wss://advanced-trade-ws.coinbase.com` | 实时数据流 |

## 认证方式

### API密钥获取

1. 登录Coinbase账户
2. 进入 Settings → API 页面
3. 创建新的API密钥
4. 设置以下信息：
   - API Key Name
   - 选择权限范围
5. 保存Private Key（仅显示一次）
6. 可选：设置IP白名单

### 请求签名方法（Advanced Trade API）

Coinbase使用JWT（JSON Web Token）认证。

**签名步骤**:

1. 创建JWT payload
2. 使用Private Key签名
3. 在请求头中包含JWT token

**必需的请求头**:

```
Authorization: Bearer <JWT_TOKEN>
Content-Type: application/json
```

**Python示例**:

```python
import time
import jwt
import requests
from cryptography.hazmat.primitives import serialization

API_KEY = '[YOUR_API_KEY]'
PRIVATE_KEY_PATH = '[PATH_TO_PRIVATE_KEY]'
BASE_URL = 'https://api.coinbase.com/api/v3'

def load_private_key(key_path):
    """加载私钥"""
    with open(key_path, 'r') as f:
        private_key = serialization.load_pem_private_key(
            f.read().encode(),
            password=None
        )
    return private_key

def generate_jwt(request_method, request_path):
    """生成JWT token"""
    private_key = load_private_key(PRIVATE_KEY_PATH)
    
    uri = f"{request_method} {request_path}"
    
    payload = {
        'sub': API_KEY,
        'iss': 'coinbase-cloud',
        'nbf': int(time.time()),
        'exp': int(time.time()) + 120,
        'uri': uri
    }
    
    token = jwt.encode(payload, private_key, algorithm='ES256')
    return token

def coinbase_request(method, endpoint, params=None, data=None):
    """发送Coinbase API请求"""
    request_path = f"/api/v3{endpoint}"
    token = generate_jwt(method, request_path)
    
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    
    url = BASE_URL + endpoint
    
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, params=params)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data)
        
        return response.json()
    except Exception as e:
        print(f"Request failed: {e}")
        return None
```

## 市场数据API

### 1. 获取交易产品信息

**端点**: `GET /brokerage/products`

**描述**: 获取所有可交易产品的信息

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| product_ids | String | 否 | 产品ID列表（逗号分隔） |
| product_type | String | 否 | 产品类型：SPOT, FUTURE |
| limit | Integer | 否 | 返回数量，最大1000 |

**响应示例**:
```json
{
  "products": [
    {
      "product_id": "BTC-USD",
      "price": "50000.00",
      "price_percentage_change_24h": "2.5",
      "volume_24h": "1234567890.00",
      "volume_percentage_change_24h": "5.2",
      "base_increment": "0.00000001",
      "quote_increment": "0.01",
      "quote_min_size": "1.00",
      "quote_max_size": "1000000.00",
      "base_min_size": "0.00001",
      "base_max_size": "280.00",
      "base_name": "Bitcoin",
      "quote_name": "US Dollar",
      "status": "online",
      "cancel_only": false,
      "limit_only": false,
      "post_only": false,
      "trading_disabled": false
    }
  ],
  "num_products": 1
}
```

**Python示例**:
```python
def get_products(product_type='SPOT'):
    """获取交易产品信息"""
    endpoint = '/brokerage/products'
    params = {}
    if product_type:
        params['product_type'] = product_type
    
    response = coinbase_request('GET', endpoint, params=params)
    return response

# 使用示例
products = get_products('SPOT')
if products:
    print(f"Total products: {products.get('num_products', 0)}")
    for product in products.get('products', [])[:5]:
        print(f"{product['product_id']}: ${product['price']}")
```

### 2. 获取单个产品信息

**端点**: `GET /brokerage/products/{product_id}`

**描述**: 获取特定产品的详细信息

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| product_id | String | 是 | 产品ID，如 BTC-USD |

**响应示例**:
```json
{
  "product_id": "BTC-USD",
  "price": "50000.00",
  "price_percentage_change_24h": "2.5",
  "volume_24h": "1234567890.00",
  "base_currency_id": "BTC",
  "quote_currency_id": "USD",
  "base_display_symbol": "BTC",
  "quote_display_symbol": "USD",
  "base_name": "Bitcoin",
  "quote_name": "US Dollar",
  "status": "online"
}
```

### 3. 获取产品行情

**端点**: `GET /brokerage/products/{product_id}/ticker`

**描述**: 获取产品的最新行情

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| product_id | String | 是 | 产品ID |
| limit | Integer | 否 | 返回的交易数量 |

**响应示例**:
```json
{
  "trades": [
    {
      "trade_id": "12345",
      "product_id": "BTC-USD",
      "price": "50000.00",
      "size": "0.001",
      "time": "2023-07-06T12:34:56Z",
      "side": "BUY",
      "bid": "49999.00",
      "ask": "50001.00"
    }
  ],
  "best_bid": "49999.00",
  "best_ask": "50001.00"
}
```

**Python示例**:
```python
def get_ticker(product_id):
    """获取产品行情"""
    endpoint = f'/brokerage/products/{product_id}/ticker'
    response = coinbase_request('GET', endpoint)
    return response

# 使用示例
ticker = get_ticker('BTC-USD')
if ticker:
    print(f"Best bid: {ticker.get('best_bid')}")
    print(f"Best ask: {ticker.get('best_ask')}")
```

### 4. 获取订单簿

**端点**: `GET /brokerage/product_book`

**描述**: 获取产品的订单簿

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| product_id | String | 是 | 产品ID |
| limit | Integer | 否 | 深度档位数量 |

**响应示例**:
```json
{
  "pricebook": {
    "product_id": "BTC-USD",
    "bids": [
      {
        "price": "49999.00",
        "size": "1.5"
      },
      {
        "price": "49998.00",
        "size": "2.3"
      }
    ],
    "asks": [
      {
        "price": "50001.00",
        "size": "1.8"
      },
      {
        "price": "50002.00",
        "size": "3.2"
      }
    ],
    "time": "2023-07-06T12:34:56Z"
  }
}
```

**Python示例**:
```python
def get_order_book(product_id, limit=50):
    """获取订单簿"""
    endpoint = '/brokerage/product_book'
    params = {
        'product_id': product_id,
        'limit': limit
    }
    
    response = coinbase_request('GET', endpoint, params=params)
    return response

# 使用示例
order_book = get_order_book('BTC-USD', limit=20)
if order_book and 'pricebook' in order_book:
    book = order_book['pricebook']
    print(f"Best bid: {book['bids'][0]}")
    print(f"Best ask: {book['asks'][0]}")
```

### 5. 获取K线数据

**端点**: `GET /brokerage/products/{product_id}/candles`

**描述**: 获取历史K线数据

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| product_id | String | 是 | 产品ID |
| start | String | 是 | 开始时间（Unix时间戳） |
| end | String | 是 | 结束时间（Unix时间戳） |
| granularity | String | 是 | 粒度：ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, TWO_HOUR, SIX_HOUR, ONE_DAY |

**响应示例**:
```json
{
  "candles": [
    {
      "start": "1688671800",
      "low": "49500.00",
      "high": "50500.00",
      "open": "50000.00",
      "close": "50200.00",
      "volume": "1000.12345678"
    }
  ]
}
```

**Python示例**:
```python
def get_candles(product_id, start, end, granularity='ONE_HOUR'):
    """获取K线数据"""
    endpoint = f'/brokerage/products/{product_id}/candles'
    params = {
        'start': start,
        'end': end,
        'granularity': granularity
    }
    
    response = coinbase_request('GET', endpoint, params=params)
    return response

# 使用示例
import time
end_time = int(time.time())
start_time = end_time - 86400  # 24小时前

candles = get_candles('BTC-USD', start_time, end_time, 'ONE_HOUR')
if candles and 'candles' in candles:
    for candle in candles['candles'][:3]:
        print(f"Time: {candle['start']}, Open: {candle['open']}, Close: {candle['close']}")
```


## 交易API

### 1. 下单

**端点**: `POST /brokerage/orders`

**描述**: 创建新订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| client_order_id | String | 是 | 客户自定义订单ID（UUID格式） |
| product_id | String | 是 | 产品ID |
| side | String | 是 | 订单方向：BUY, SELL |
| order_configuration | Object | 是 | 订单配置 |

**订单配置类型**:

**限价单**:
```json
{
  "limit_limit_gtc": {
    "base_size": "0.001",
    "limit_price": "50000.00",
    "post_only": false
  }
}
```

**市价单**:
```json
{
  "market_market_ioc": {
    "quote_size": "100.00"
  }
}
```

**响应示例**:
```json
{
  "success": true,
  "order_id": "11111111-1111-1111-1111-111111111111",
  "success_response": {
    "order_id": "11111111-1111-1111-1111-111111111111",
    "product_id": "BTC-USD",
    "side": "BUY",
    "client_order_id": "22222222-2222-2222-2222-222222222222"
  }
}
```

**Python示例**:
```python
import uuid

def create_order(product_id, side, order_type, size, price=None):
    """创建订单"""
    endpoint = '/brokerage/orders'
    
    client_order_id = str(uuid.uuid4())
    
    # 构建订单配置
    if order_type == 'limit':
        order_config = {
            'limit_limit_gtc': {
                'base_size': str(size),
                'limit_price': str(price),
                'post_only': False
            }
        }
    elif order_type == 'market':
        order_config = {
            'market_market_ioc': {
                'quote_size': str(size)  # 市价单使用报价币种数量
            }
        }
    else:
        raise ValueError(f"Unsupported order type: {order_type}")
    
    data = {
        'client_order_id': client_order_id,
        'product_id': product_id,
        'side': side.upper(),
        'order_configuration': order_config
    }
    
    try:
        result = coinbase_request('POST', endpoint, data=data)
        return result
    except Exception as e:
        print(f"Order failed: {e}")
        return None

# 使用示例 - 限价买单
order = create_order(
    product_id='BTC-USD',
    side='BUY',
    order_type='limit',
    size='0.001',
    price='50000'
)
print(f"Order placed: {order}")

# 使用示例 - 市价卖单
order = create_order(
    product_id='BTC-USD',
    side='SELL',
    order_type='market',
    size='100'  # $100 worth
)
print(f"Order placed: {order}")
```

### 2. 撤销订单

**端点**: `POST /brokerage/orders/batch_cancel`

**描述**: 批量撤销订单

**权限**: 需要交易权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| order_ids | Array | 是 | 订单ID列表 |

**响应示例**:
```json
{
  "results": [
    {
      "success": true,
      "order_id": "11111111-1111-1111-1111-111111111111"
    }
  ]
}
```

**Python示例**:
```python
def cancel_orders(order_ids):
    """批量撤销订单"""
    endpoint = '/brokerage/orders/batch_cancel'
    data = {'order_ids': order_ids}
    
    try:
        result = coinbase_request('POST', endpoint, data=data)
        return result
    except Exception as e:
        print(f"Cancel failed: {e}")
        return None

# 使用示例
result = cancel_orders(['11111111-1111-1111-1111-111111111111'])
print(f"Orders canceled: {result}")
```

### 3. 查询订单信息

**端点**: `GET /brokerage/orders/historical/{order_id}`

**描述**: 查询单个订单详情

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| order_id | String | 是 | 订单ID |

**响应示例**:
```json
{
  "order": {
    "order_id": "11111111-1111-1111-1111-111111111111",
    "product_id": "BTC-USD",
    "user_id": "user123",
    "order_configuration": {
      "limit_limit_gtc": {
        "base_size": "0.001",
        "limit_price": "50000.00",
        "post_only": false
      }
    },
    "side": "BUY",
    "client_order_id": "22222222-2222-2222-2222-222222222222",
    "status": "OPEN",
    "time_in_force": "GOOD_UNTIL_CANCELLED",
    "created_time": "2023-07-06T12:34:56Z",
    "completion_percentage": "0",
    "filled_size": "0",
    "average_filled_price": "0",
    "fee": "0",
    "number_of_fills": "0",
    "filled_value": "0",
    "pending_cancel": false,
    "size_in_quote": false,
    "total_fees": "0",
    "size_inclusive_of_fees": false,
    "total_value_after_fees": "0",
    "trigger_status": "UNKNOWN_TRIGGER_STATUS",
    "order_type": "LIMIT",
    "reject_reason": "REJECT_REASON_UNSPECIFIED",
    "settled": false,
    "product_type": "SPOT"
  }
}
```

### 4. 查询所有订单

**端点**: `GET /brokerage/orders/historical/batch`

**描述**: 查询历史订单列表

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| product_id | String | 否 | 产品ID |
| order_status | Array | 否 | 订单状态：OPEN, FILLED, CANCELLED |
| limit | Integer | 否 | 返回数量，最大1000 |
| start_date | String | 否 | 开始日期 |
| end_date | String | 否 | 结束日期 |

**响应示例**:
```json
{
  "orders": [
    {
      "order_id": "11111111-1111-1111-1111-111111111111",
      "product_id": "BTC-USD",
      "side": "BUY",
      "status": "FILLED",
      "created_time": "2023-07-06T12:34:56Z"
    }
  ],
  "has_next": false,
  "cursor": "",
  "sequence": 0
}
```

**Python示例**:
```python
def get_orders(product_id=None, order_status=None, limit=100):
    """查询订单列表"""
    endpoint = '/brokerage/orders/historical/batch'
    params = {'limit': limit}
    
    if product_id:
        params['product_id'] = product_id
    if order_status:
        params['order_status'] = order_status
    
    try:
        result = coinbase_request('GET', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
orders = get_orders(product_id='BTC-USD', order_status=['OPEN'], limit=50)
if orders and 'orders' in orders:
    print(f"Total orders: {len(orders['orders'])}")
    for order in orders['orders'][:5]:
        print(f"Order {order['order_id']}: {order['side']} {order['product_id']} - {order['status']}")
```

## 账户管理API

### 1. 查询账户列表

**端点**: `GET /brokerage/accounts`

**描述**: 获取所有账户信息

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| limit | Integer | 否 | 返回数量 |
| cursor | String | 否 | 分页游标 |

**响应示例**:
```json
{
  "accounts": [
    {
      "uuid": "account-uuid-1",
      "name": "BTC Wallet",
      "currency": "BTC",
      "available_balance": {
        "value": "1.00000000",
        "currency": "BTC"
      },
      "default": true,
      "active": true,
      "created_at": "2023-01-01T00:00:00Z",
      "updated_at": "2023-07-06T12:34:56Z",
      "deleted_at": null,
      "type": "ACCOUNT_TYPE_CRYPTO",
      "ready": true,
      "hold": {
        "value": "0.00000000",
        "currency": "BTC"
      }
    },
    {
      "uuid": "account-uuid-2",
      "name": "USD Wallet",
      "currency": "USD",
      "available_balance": {
        "value": "10000.00",
        "currency": "USD"
      },
      "default": false,
      "active": true,
      "type": "ACCOUNT_TYPE_FIAT"
    }
  ],
  "has_next": false,
  "cursor": "",
  "size": 2
}
```

**Python示例**:
```python
def get_accounts():
    """获取账户列表"""
    endpoint = '/brokerage/accounts'
    
    try:
        result = coinbase_request('GET', endpoint)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
accounts = get_accounts()
if accounts and 'accounts' in accounts:
    print("Account balances:")
    for account in accounts['accounts']:
        balance = account['available_balance']
        if float(balance['value']) > 0:
            print(f"{balance['currency']}: {balance['value']}")
```

### 2. 查询单个账户

**端点**: `GET /brokerage/accounts/{account_uuid}`

**描述**: 获取特定账户的详细信息

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| account_uuid | String | 是 | 账户UUID |

**响应示例**:
```json
{
  "account": {
    "uuid": "account-uuid-1",
    "name": "BTC Wallet",
    "currency": "BTC",
    "available_balance": {
      "value": "1.00000000",
      "currency": "BTC"
    },
    "default": true,
    "active": true,
    "created_at": "2023-01-01T00:00:00Z",
    "updated_at": "2023-07-06T12:34:56Z",
    "type": "ACCOUNT_TYPE_CRYPTO",
    "ready": true,
    "hold": {
      "value": "0.00000000",
      "currency": "BTC"
    }
  }
}
```

### 3. 查询成交记录

**端点**: `GET /brokerage/orders/historical/fills`

**描述**: 获取成交历史

**权限**: 需要读取权限

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| order_id | String | 否 | 订单ID |
| product_id | String | 否 | 产品ID |
| start_sequence_timestamp | String | 否 | 开始时间 |
| end_sequence_timestamp | String | 否 | 结束时间 |
| limit | Integer | 否 | 返回数量 |

**响应示例**:
```json
{
  "fills": [
    {
      "entry_id": "fill-id-1",
      "trade_id": "trade-id-1",
      "order_id": "11111111-1111-1111-1111-111111111111",
      "trade_time": "2023-07-06T12:34:56Z",
      "trade_type": "FILL",
      "price": "50000.00",
      "size": "0.001",
      "commission": "0.50",
      "product_id": "BTC-USD",
      "sequence_timestamp": "2023-07-06T12:34:56.123456Z",
      "liquidity_indicator": "TAKER",
      "size_in_quote": false,
      "user_id": "user123",
      "side": "BUY"
    }
  ],
  "cursor": ""
}
```

**Python示例**:
```python
def get_fills(product_id=None, order_id=None, limit=100):
    """获取成交记录"""
    endpoint = '/brokerage/orders/historical/fills'
    params = {'limit': limit}
    
    if product_id:
        params['product_id'] = product_id
    if order_id:
        params['order_id'] = order_id
    
    try:
        result = coinbase_request('GET', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例
fills = get_fills(product_id='BTC-USD', limit=10)
if fills and 'fills' in fills:
    print(f"Recent fills: {len(fills['fills'])}")
    for fill in fills['fills'][:5]:
        print(f"{fill['side']} {fill['size']} @ {fill['price']}")
```

## 速率限制

### 全局速率限制

Coinbase实施基于端点的速率限制：

| 限制类型 | 限制值 | 时间窗口 | 说明 |
|---------|--------|----------|------|
| 公共端点 | 10次 | 1秒 | 每个IP |
| 私有端点 | 15次 | 1秒 | 每个API密钥 |
| 交易端点 | 10次 | 1秒 | 每个API密钥 |

### 不同端点的速率限制

| 端点类别 | 限制 |
|---------|------|
| 获取产品信息 | 10次/秒 |
| 获取行情数据 | 10次/秒 |
| 查询账户 | 15次/秒 |
| 下单 | 10次/秒 |
| 撤单 | 10次/秒 |
| 查询订单 | 15次/秒 |

### 响应头

速率限制信息包含在响应头中：

```
cb-rate-limit-remaining: 剩余请求数
cb-rate-limit-reset: 重置时间戳
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
                if isinstance(response, dict) and response.get('error') == 'rate_limit_exceeded':
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
    return coinbase_request(method, endpoint, **kwargs)
```

## WebSocket支持

### WebSocket端点

| 端点类型 | URL | 说明 |
|---------|-----|------|
| 市场数据 | `wss://advanced-trade-ws.coinbase.com` | 实时市场数据 |

### 认证方法

WebSocket连接需要JWT认证：

```python
import json
import websocket

def connect_websocket():
    """连接WebSocket"""
    ws_url = "wss://advanced-trade-ws.coinbase.com"
    
    # 生成JWT token
    token = generate_jwt('GET', '/ws')
    
    def on_open(ws):
        # 订阅频道
        subscribe_msg = {
            "type": "subscribe",
            "product_ids": ["BTC-USD", "ETH-USD"],
            "channel": "ticker",
            "jwt": token
        }
        ws.send(json.dumps(subscribe_msg))
    
    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=lambda ws, msg: print(f"Received: {msg}"),
        on_error=lambda ws, err: print(f"Error: {err}"),
        on_close=lambda ws, code, msg: print("Connection closed")
    )
    
    return ws
```

### 可订阅频道

**公共频道**:
- `ticker` - 实时行情
- `ticker_batch` - 批量行情
- `level2` - 订单簿（50档）
- `user` - 用户订单和成交
- `market_trades` - 市场成交

### 订阅格式

```python
# 订阅ticker
subscribe_msg = {
    "type": "subscribe",
    "product_ids": ["BTC-USD"],
    "channel": "ticker",
    "jwt": token
}

# 取消订阅
unsubscribe_msg = {
    "type": "unsubscribe",
    "product_ids": ["BTC-USD"],
    "channel": "ticker"
}
```

### 心跳机制

Coinbase WebSocket使用heartbeat消息：

```python
# 服务器定期发送heartbeat
# {"type": "heartbeat", "current_time": "2023-07-06T12:34:56Z"}
```

## 错误代码

### 常见错误代码

| 错误代码 | 错误消息 | 可能原因 | 处理建议 |
|---------|---------|---------|---------|
| INVALID_ARGUMENT | Invalid argument | 参数错误 | 检查请求参数 |
| UNAUTHENTICATED | Authentication failed | 认证失败 | 检查JWT token |
| PERMISSION_DENIED | Permission denied | 权限不足 | 检查API权限 |
| RESOURCE_EXHAUSTED | Rate limit exceeded | 超过速率限制 | 等待后重试 |
| INSUFFICIENT_FUND | Insufficient funds | 余额不足 | 检查账户余额 |
| INVALID_ORDER_CONFIGURATION | Invalid order config | 订单配置错误 | 检查订单参数 |
| UNKNOWN_ORDER | Order not found | 订单不存在 | 检查订单ID |
| INTERNAL | Internal server error | 服务器错误 | 稍后重试 |

### 错误处理示例

```python
def handle_coinbase_error(response):
    """处理Coinbase API错误"""
    if not response:
        return "Network error or timeout"
    
    if 'error' in response:
        error = response['error']
        error_code = error if isinstance(error, str) else error.get('message', 'Unknown error')
        
        error_handlers = {
            'INVALID_ARGUMENT': "Invalid argument - check request parameters",
            'UNAUTHENTICATED': "Authentication failed - verify JWT token",
            'PERMISSION_DENIED': "Permission denied - check API permissions",
            'RESOURCE_EXHAUSTED': "Rate limit exceeded - wait and retry",
            'INSUFFICIENT_FUND': "Insufficient funds - check balance",
            'INVALID_ORDER_CONFIGURATION': "Invalid order config - check parameters",
            'UNKNOWN_ORDER': "Order not found - verify order ID",
            'INTERNAL': "Internal server error - retry later"
        }
        
        for error_key, handler_msg in error_handlers.items():
            if error_key in error_code:
                return handler_msg
        
        return f"Error: {error_code}"
    
    return None  # Success

# 使用示例
response = create_order('BTC-USD', 'BUY', 'limit', '0.001', '50000')
error = handle_coinbase_error(response)
if error:
    print(f"Order failed: {error}")
else:
    print("Order placed successfully")
```

## 变更历史

- 2026-02-27: 初始版本创建，基于Coinbase Advanced Trade API v3

