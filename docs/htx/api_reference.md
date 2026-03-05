# HTX (Huobi) API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: v1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://www.htx.com/en-us/opend/newApiPages/>

## 交易所基本信息

- 官方名称: HTX (原 Huobi)
- 官网: <https://www.htx.com>
- 交易所类型: CEX (中心化交易所)
- 24h 交易量排名: #6 ($2.4B+)
- 支持的交易对类型: 现货、杠杆、合约、期权
- 支持的币种数量: 600+
- 特点: 老牌交易所，全球化运营

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://api.huobi.pro`> | 主端点 |

| REST API (AWS) | `<https://api-aws.huobi.pro`> | AWS 区域 |

| WebSocket (市场) | `wss://api.huobi.pro/ws` | 市场数据 |

| WebSocket (账户) | `wss://api.huobi.pro/ws/v2` | 账户数据 |

## 认证方式

### API 密钥获取

1. 登录 HTX 账户
2. 进入 API Management 页面
3. 创建新的 API 密钥
4. 设置以下信息：
   - Access Key
   - Secret Key
1. 配置 API 权限（读取、交易、提现等）
2. 可选：绑定 IP 白名单
3. 保存 API 密钥信息

### 请求签名方法

HTX 使用 HMAC SHA256 签名算法。

- *签名步骤**:

1. 构建规范化请求字符串
2. 按字母顺序排序参数
3. 使用 Secret Key 进行 HMAC SHA256 签名
4. 将签名进行 Base64 编码

- *必需的参数**:

```
AccessKeyId: Access Key
SignatureMethod: HmacSHA256
SignatureVersion: 2
Timestamp: UTC 时间（ISO 8601 格式）
Signature: 签名

```

- *Python 示例**:

```python
import time
import hmac
import hashlib
import base64
import urllib.parse
from datetime import datetime
import requests

ACCESS_KEY = '[YOUR_ACCESS_KEY]'
SECRET_KEY = '[YOUR_SECRET_KEY]'
BASE_URL = '<https://api.huobi.pro'>

def create_signature(method, host, path, params):
    """生成 HTX API 签名"""

# 按字母顺序排序参数
    sorted_params = sorted(params.items())
    encoded_params = urllib.parse.urlencode(sorted_params)

# 构建签名字符串
    payload = f"{method}\n{host}\n{path}\n{encoded_params}"

# HMAC SHA256 签名
    signature = hmac.new(
        SECRET_KEY.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).digest()

# Base64 编码
    signature = base64.b64encode(signature).decode()

    return signature

def get_signed_params(method, path, params=None):
    """生成签名参数"""
    if params is None:
        params = {}

# 添加必需参数
    timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    params.update({
        'AccessKeyId': ACCESS_KEY,
        'SignatureMethod': 'HmacSHA256',
        'SignatureVersion': '2',
        'Timestamp': timestamp
    })

# 生成签名
    host = 'api.huobi.pro'
    signature = create_signature(method, host, path, params)
    params['Signature'] = signature

    return params

def htx_request(method, endpoint, params=None, data=None):
    """发送 HTX API 请求"""
    import json

    url = BASE_URL + endpoint

    if method == 'GET':
        signed_params = get_signed_params(method, endpoint, params)
        response = requests.get(url, params=signed_params)
    elif method == 'POST':
        signed_params = get_signed_params(method, endpoint, params)
        response = requests.post(url, params=signed_params, json=data)

    try:
        return response.json()
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```

## 市场数据 API

### 1. 获取交易对信息

- *端点**: `GET /v1/common/symbols`

- *描述**: 获取所有交易对信息

- *参数**: 无

- *响应示例**:

```json
{
  "status": "ok",
  "data": [
    {
      "base-currency": "btc",
      "quote-currency": "usdt",
      "price-precision": 2,
      "amount-precision": 6,
      "symbol-partition": "main",
      "symbol": "btcusdt",
      "state": "online",
      "value-precision": 8,
      "min-order-amt": 0.0001,
      "max-order-amt": 1000,
      "min-order-value": 5,
      "limit-order-min-order-amt": 0.0001,
      "limit-order-max-order-amt": 1000,
      "sell-market-min-order-amt": 0.0001,
      "sell-market-max-order-amt": 100,
      "buy-market-max-order-value": 1000000,
      "leverage-ratio": 5
    }
  ]
}

```

- *Python 示例**:

```python
def get_symbols():
    """获取交易对信息"""
    endpoint = '/v1/common/symbols'

    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url)
    return response.json()

# 使用示例

symbols = get_symbols()
if symbols['status'] == 'ok':
    print(f"Total symbols: {len(symbols['data'])}")
    for symbol in symbols['data'][:5]:
        print(f"{symbol['symbol']}: min amount {symbol['min-order-amt']}")

```

### 2. 获取行情数据

- *端点**: `GET /market/detail/merged`

- *描述**: 获取聚合行情（Ticker）

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | String | 是 | 交易对，如 btcusdt |

- *响应示例**:

```json
{
  "status": "ok",
  "ch": "market.btcusdt.detail.merged",
  "ts": 1688671955000,
  "tick": {
    "id": 123456789,
    "ts": 1688671955000,
    "close": 50000,
    "open": 49500,
    "high": 51000,
    "low": 49000,
    "amount": 1234.5678,
    "count": 10000,
    "vol": 61728350,
    "ask": [50001, 1.5],
    "bid": [49999, 2.3]
  }
}

```

- *Python 示例**:

```python
def get_ticker(symbol):
    """获取行情数据"""
    endpoint = '/market/detail/merged'
    params = {'symbol': symbol}

    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例

ticker = get_ticker('btcusdt')
if ticker['status'] == 'ok':
    tick = ticker['tick']
    print(f"Price: {tick['close']}")
    print(f"24h high: {tick['high']}, low: {tick['low']}")

```

### 3. 获取深度数据

- *端点**: `GET /market/depth`

- *描述**: 获取市场深度数据

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | String | 是 | 交易对 |

| depth | Integer | 否 | 深度档位：5, 10, 20 |

| type | String | 是 | 深度类型：step0, step1, step2, step3, step4, step5 |

- *响应示例**:

```json
{
  "status": "ok",
  "ch": "market.btcusdt.depth.step0",
  "ts": 1688671955000,
  "tick": {
    "bids": [
      [49999, 1.5],
      [49998, 2.3]
    ],
    "asks": [
      [50001, 1.8],
      [50002, 3.2]
    ],
    "ts": 1688671955000,
    "version": 123456789
  }
}

```

- *Python 示例**:

```python
def get_depth(symbol, depth_type='step0'):
    """获取深度数据"""
    endpoint = '/market/depth'
    params = {
        'symbol': symbol,
        'type': depth_type
    }

    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例

depth = get_depth('btcusdt', 'step0')
if depth['status'] == 'ok':
    tick = depth['tick']
    print(f"Best bid: {tick['bids'][0]}")
    print(f"Best ask: {tick['asks'][0]}")

```

### 4. 获取 K 线数据

- *端点**: `GET /market/history/kline`

- *描述**: 获取 K 线数据

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | String | 是 | 交易对 |

| period | String | 是 | K 线周期：1min, 5min, 15min, 30min, 60min, 4hour, 1day, 1mon, 1week, 1year |

| size | Integer | 否 | 返回数量，最大 2000 |

- *响应示例**:

```json
{
  "status": "ok",
  "ch": "market.btcusdt.kline.1min",
  "ts": 1688671955000,
  "data": [
    {
      "id": 1688671800,
      "open": 50000,
      "close": 50200,
      "low": 49500,
      "high": 50500,
      "amount": 1000.12345678,
      "vol": 50100000,
      "count": 100
    }
  ]
}

```

- *Python 示例**:

```python
def get_klines(symbol, period='60min', size=100):
    """获取 K 线数据"""
    endpoint = '/market/history/kline'
    params = {
        'symbol': symbol,
        'period': period,
        'size': size
    }

    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例

klines = get_klines('btcusdt', '60min', 24)
if klines['status'] == 'ok':
    for kline in klines['data'][:3]:
        print(f"Time: {kline['id']}, Open: {kline['open']}, Close: {kline['close']}")

```

### 5. 获取最近成交

- *端点**: `GET /market/history/trade`

- *描述**: 获取最近的成交记录

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | String | 是 | 交易对 |

| size | Integer | 否 | 返回数量，最大 2000 |

- *响应示例**:

```json
{
  "status": "ok",
  "ch": "market.btcusdt.trade.detail",
  "ts": 1688671955000,
  "data": [
    {
      "id": 123456789,
      "ts": 1688671955000,
      "data": [
        {
          "id": 123456789,
          "ts": 1688671955000,
          "trade-id": 987654321,
          "amount": 0.001,
          "price": 50000,
          "direction": "buy"
        }
      ]
    }
  ]
}

```

## 交易 API

### 1. 获取账户 ID

- *端点**: `GET /v1/account/accounts`

- *描述**: 获取账户列表（下单前需要先获取账户 ID）

- *权限**: 需要读取权限

- *参数**: 无

- *响应示例**:

```json
{
  "status": "ok",
  "data": [
    {
      "id": 123456,
      "type": "spot",
      "subtype": "",
      "state": "working"
    }
  ]
}

```

- *Python 示例**:

```python
def get_accounts():
    """获取账户列表"""
    endpoint = '/v1/account/accounts'

    try:
        result = htx_request('GET', endpoint)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例

accounts = get_accounts()
if accounts and accounts['status'] == 'ok':
    spot_account = next((acc for acc in accounts['data'] if acc['type'] == 'spot'), None)
    if spot_account:
        account_id = spot_account['id']
        print(f"Spot account ID: {account_id}")

```

### 2. 下单

- *端点**: `POST /v1/order/orders/place`

- *描述**: 创建新订单

- *权限**: 需要交易权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| account-id | String | 是 | 账户 ID |

| symbol | String | 是 | 交易对 |

| type | String | 是 | 订单类型：buy-market, sell-market, buy-limit, sell-limit |

| amount | String | 是 | 订单数量 |

| price | String | 否 | 订单价格（限价单必需） |

| client-order-id | String | 否 | 客户自定义订单 ID |

| source | String | 否 | 订单来源：spot-api, margin-api |

- *响应示例**:

```json
{
  "status": "ok",
  "data": "123456789"
}

```

- *Python 示例**:

```python
def place_order(account_id, symbol, order_type, amount, price=None):
    """下单"""
    endpoint = '/v1/order/orders/place'

    data = {
        'account-id': str(account_id),
        'symbol': symbol,
        'type': order_type,
        'amount': str(amount)
    }

    if 'limit' in order_type and price:
        data['price'] = str(price)

    try:
        result = htx_request('POST', endpoint, data=data)
        return result
    except Exception as e:
        print(f"Order failed: {e}")
        return None

# 使用示例 - 限价买单

order = place_order(
    account_id=123456,
    symbol='btcusdt',
    order_type='buy-limit',
    amount='0.001',
    price='50000'
)
print(f"Order placed: {order}")

# 使用示例 - 市价卖单

order = place_order(
    account_id=123456,
    symbol='btcusdt',
    order_type='sell-market',
    amount='0.001'
)
print(f"Order placed: {order}")

```

### 3. 撤销订单

- *端点**: `POST /v1/order/orders/{order-id}/submitcancel`

- *描述**: 撤销单个订单

- *权限**: 需要交易权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| order-id | String | 是 | 订单 ID（路径参数） |

- *响应示例**:

```json
{
  "status": "ok",
  "data": "123456789"
}

```

- *Python 示例**:

```python
def cancel_order(order_id):
    """撤销订单"""
    endpoint = f'/v1/order/orders/{order_id}/submitcancel'

    try:
        result = htx_request('POST', endpoint)
        return result
    except Exception as e:
        print(f"Cancel failed: {e}")
        return None

# 使用示例

result = cancel_order('123456789')
print(f"Order canceled: {result}")

```

### 4. 批量撤销订单

- *端点**: `POST /v1/order/orders/batchcancel`

- *描述**: 批量撤销订单

- *权限**: 需要交易权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| order-ids | Array | 是 | 订单 ID 列表（最多 50 个） |

- *响应示例**:

```json
{
  "status": "ok",
  "data": {
    "success": ["123456789", "123456790"],
    "failed": []
  }
}

```

- *Python 示例**:

```python
def batch_cancel_orders(order_ids):
    """批量撤销订单"""
    endpoint = '/v1/order/orders/batchcancel'
    data = {'order-ids': order_ids}

    try:
        result = htx_request('POST', endpoint, data=data)
        return result
    except Exception as e:
        print(f"Batch cancel failed: {e}")
        return None

# 使用示例

result = batch_cancel_orders(['123456789', '123456790'])
print(f"Batch cancel result: {result}")

```

### 5. 查询订单详情

- *端点**: `GET /v1/order/orders/{order-id}`

- *描述**: 查询订单详情

- *权限**: 需要读取权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| order-id | String | 是 | 订单 ID（路径参数） |

- *响应示例**:

```json
{
  "status": "ok",
  "data": {
    "id": 123456789,
    "symbol": "btcusdt",
    "account-id": 123456,
    "amount": "0.001",
    "price": "50000",
    "created-at": 1688671955000,
    "type": "buy-limit",
    "field-amount": "0.001",
    "field-cash-amount": "50",
    "field-fees": "0.00001",
    "finished-at": 1688671960000,
    "source": "spot-api",
    "state": "filled",
    "canceled-at": 0
  }
}

```

### 6. 查询订单列表

- *端点**: `GET /v1/order/orders`

- *描述**: 查询订单列表

- *权限**: 需要读取权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | String | 是 | 交易对 |

| states | String | 是 | 订单状态：submitted, partial-filled, filled, canceled, partial-canceled |

| types | String | 否 | 订单类型 |

| start-date | String | 否 | 开始日期（yyyy-mm-dd） |

| end-date | String | 否 | 结束日期 |

| from | String | 否 | 起始订单 ID |

| direct | String | 否 | 查询方向：prev, next |

| size | Integer | 否 | 返回数量 |

- *响应示例**: 返回订单数组，格式同订单详情

- *Python 示例**:

```python
def get_orders(symbol, states='submitted,partial-filled'):
    """查询订单列表"""
    endpoint = '/v1/order/orders'
    params = {
        'symbol': symbol,
        'states': states
    }

    try:
        result = htx_request('GET', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例

orders = get_orders('btcusdt', 'submitted,partial-filled')
if orders and orders['status'] == 'ok':
    print(f"Open orders: {len(orders['data'])}")
    for order in orders['data'][:5]:
        print(f"Order {order['id']}: {order['type']} {order['amount']} @ {order['price']}")

```

## 账户管理 API

### 1. 查询账户余额

- *端点**: `GET /v1/account/accounts/{account-id}/balance`

- *描述**: 获取账户余额

- *权限**: 需要读取权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| account-id | String | 是 | 账户 ID（路径参数） |

- *响应示例**:

```json
{
  "status": "ok",
  "data": {
    "id": 123456,
    "type": "spot",
    "state": "working",
    "list": [
      {
        "currency": "btc",
        "type": "trade",
        "balance": "0.80000000"
      },
      {
        "currency": "btc",
        "type": "frozen",
        "balance": "0.20000000"
      },
      {
        "currency": "usdt",
        "type": "trade",
        "balance": "9000.00"
      },
      {
        "currency": "usdt",
        "type": "frozen",
        "balance": "1000.00"
      }
    ]
  }
}

```

- *Python 示例**:

```python
def get_balance(account_id):
    """获取账户余额"""
    endpoint = f'/v1/account/accounts/{account_id}/balance'

    try:
        result = htx_request('GET', endpoint)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例

balance = get_balance(123456)
if balance and balance['status'] == 'ok':
    balances = {}
    for item in balance['data']['list']:
        currency = item['currency']
        if currency not in balances:
            balances[currency] = {'trade': '0', 'frozen': '0'}
        balances[currency][item['type']] = item['balance']

    print("Account balances:")
    for currency, amounts in balances.items():
        if float(amounts['trade']) > 0 or float(amounts['frozen']) > 0:
            print(f"{currency.upper()}: {amounts['trade']} (frozen: {amounts['frozen']})")

```

### 2. 查询成交记录

- *端点**: `GET /v1/order/orders/{order-id}/matchresults`

- *描述**: 查询订单的成交记录

- *权限**: 需要读取权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| order-id | String | 是 | 订单 ID（路径参数） |

- *响应示例**:

```json
{
  "status": "ok",
  "data": [
    {
      "id": 987654321,
      "order-id": 123456789,
      "match-id": 111222333,
      "symbol": "btcusdt",
      "type": "buy-limit",
      "source": "spot-api",
      "price": "50000",
      "filled-amount": "0.001",
      "filled-fees": "0.00001",
      "created-at": 1688671955000
    }
  ]
}

```

### 3. 查询历史成交

- *端点**: `GET /v1/order/matchresults`

- *描述**: 查询历史成交记录

- *权限**: 需要读取权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | String | 是 | 交易对 |

| types | String | 否 | 订单类型 |

| start-date | String | 否 | 开始日期 |

| end-date | String | 否 | 结束日期 |

| from | String | 否 | 起始 ID |

| direct | String | 否 | 查询方向 |

| size | Integer | 否 | 返回数量 |

- *响应示例**: 同查询订单成交记录

- *Python 示例**:

```python
def get_match_results(symbol, size=100):
    """获取成交记录"""
    endpoint = '/v1/order/matchresults'
    params = {
        'symbol': symbol,
        'size': size
    }

    try:
        result = htx_request('GET', endpoint, params=params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例

matches = get_match_results('btcusdt', size=10)
if matches and matches['status'] == 'ok':
    print(f"Recent matches: {len(matches['data'])}")
    for match in matches['data'][:5]:
        print(f"{match['type']} {match['filled-amount']} @ {match['price']}")

```

## 速率限制

### 全局速率限制

HTX 实施基于 IP 和 UID 的速率限制：

| 限制类型 | 限制值 | 时间窗口 | 说明 |

|---------|--------|----------|------|

| REST API (公共) | 100 次 | 10 秒 | 每个 IP |

| REST API (私有) | 100 次 | 10 秒 | 每个 UID |

| 交易端点 | 100 次 | 2 秒 | 每个 UID |

| WebSocket 连接 | 50 个 | - | 每个 UID |

### 不同端点的速率限制

| 端点类别 | 限制 |

|---------|------|

| 获取交易对 | 10 次/秒 |

| 获取行情 | 10 次/秒 |

| 获取深度 | 10 次/秒 |

| 查询账户 | 10 次/秒 |

| 下单 | 100 次/2 秒 |

| 撤单 | 100 次/2 秒 |

| 查询订单 | 10 次/秒 |

### 响应头

速率限制信息包含在响应头中：

```
ratelimit-limit: 速率限制值
ratelimit-remaining: 剩余请求数
ratelimit-reset: 重置时间戳

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
                if isinstance(response, dict) and response.get('err-code') == 'api-signature-not-valid':
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 **attempt)
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
def api_call_with_retry(method, endpoint,**kwargs):
    """带重试的 API 调用"""
    return htx_request(method, endpoint, **kwargs)

```

## WebSocket 支持

### WebSocket 端点

| 端点类型 | URL | 说明 |

|---------|-----|------|

| 市场数据 | `wss://api.huobi.pro/ws` | 市场行情 |

| 账户数据 | `wss://api.huobi.pro/ws/v2` | 账户和订单 |

### 认证方法

WebSocket 私有频道需要认证：

```python
import json
import gzip

def authenticate_websocket(ws):
    """WebSocket 认证"""
    timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')

    params = {
        'accessKey': ACCESS_KEY,
        'signatureMethod': 'HmacSHA256',
        'signatureVersion': '2.1',
        'timestamp': timestamp
    }

# 生成签名
    host = 'api.huobi.pro'
    path = '/ws/v2'
    signature = create_signature('GET', host, path, params)

    auth_msg = {
        'action': 'req',
        'ch': 'auth',
        'params': {
            'authType': 'api',
            'accessKey': ACCESS_KEY,
            'signatureMethod': 'HmacSHA256',
            'signatureVersion': '2.1',
            'timestamp': timestamp,
            'signature': signature
        }
    }

    ws.send(json.dumps(auth_msg))

```

### 可订阅频道

- *公共频道**:
- `market.{symbol}.ticker` - 行情 ticker
- `market.{symbol}.depth.{type}` - 市场深度
- `market.{symbol}.trade.detail` - 成交明细
- `market.{symbol}.kline.{period}` - K 线数据

- *私有频道**:
- `orders#{symbol}` - 订单更新
- `accounts.update#2` - 账户变动

### 订阅格式

```python

# 订阅公共频道

subscribe_msg = {
    "sub": "market.btcusdt.ticker",
    "id": "id1"
}

# 取消订阅

unsubscribe_msg = {
    "unsub": "market.btcusdt.ticker",
    "id": "id2"
}

```

### 心跳机制

HTX WebSocket 使用 ping/pong 心跳：

```python

# 服务器发送 ping

# {"ping": 1688671955000}

# 客户端响应 pong

pong_msg = {"pong": 1688671955000}

```

### WebSocket 连接示例

```python
import websocket
import json
import gzip
import threading

def on_message(ws, message):
    """处理接收到的消息"""

# HTX WebSocket 消息是 gzip 压缩的
    data = json.loads(gzip.decompress(message).decode('utf-8'))

# 处理 ping
    if 'ping' in data:
        pong_msg = json.dumps({'pong': data['ping']})
        ws.send(pong_msg)
    else:
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
        "sub": "market.btcusdt.ticker",
        "id": "id1"
    }
    ws.send(json.dumps(subscribe_msg))
    print("Subscribed to ticker")

# 创建 WebSocket 连接

ws_url = "wss://api.huobi.pro/ws"
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

| base-msg | Success | 成功 | - |

| api-signature-not-valid | Signature not valid | 签名错误 | 检查签名算法 |

| api-signature-check-failed | Signature check failed | 签名验证失败 | 检查密钥和参数 |

| api-key-invalid | API key invalid | API 密钥无效 | 检查 API 密钥 |

| api-key-expired | API key expired | API 密钥过期 | 更新 API 密钥 |

| api-key-ip-invalid | IP invalid | IP 不在白名单 | 添加 IP 到白名单 |

| api-key-permission-invalid | Permission invalid | 权限不足 | 检查 API 权限 |

| gateway-internal-error | Internal error | 服务器错误 | 稍后重试 |

| account-frozen-balance-insufficient-error | Insufficient balance | 余额不足 | 检查账户余额 |

| order-orderstate-error | Order state error | 订单状态错误 | 检查订单状态 |

| order-queryorder-invalid | Order not found | 订单不存在 | 检查订单 ID |

| order-update-error | Order update error | 订单更新失败 | 重试或联系客服 |

### 错误处理示例

```python
def handle_htx_error(response):
    """处理 HTX API 错误"""
    if not response:
        return "Network error or timeout"

    status = response.get('status', 'error')

    if status == 'ok':
        return None  # Success

    err_code = response.get('err-code', '')
    err_msg = response.get('err-msg', 'Unknown error')

    error_handlers = {
        'api-signature-not-valid': "Invalid signature - check signing method",
        'api-signature-check-failed': "Signature check failed - verify key and params",
        'api-key-invalid': "Invalid API key - verify credentials",
        'api-key-expired': "API key expired - update API key",
        'api-key-ip-invalid': "IP invalid - add IP to whitelist",
        'api-key-permission-invalid': "Permission invalid - check API permissions",
        'gateway-internal-error': "Internal server error - retry later",
        'account-frozen-balance-insufficient-error': "Insufficient balance - check account balance",
        'order-orderstate-error': "Order state error - check order status",
        'order-queryorder-invalid': "Order not found - verify order ID",
        'order-update-error': "Order update error - retry or contact support"
    }

    error_msg = error_handlers.get(err_code, f"Error {err_code}: {err_msg}")
    return error_msg

# 使用示例

response = place_order(123456, 'btcusdt', 'buy-limit', '0.001', '50000')
error = handle_htx_error(response)
if error:
    print(f"Order failed: {error}")
else:
    print("Order placed successfully")

```

## 变更历史

- 2026-02-27: 初始版本创建，基于 HTX (Huobi) API v1
