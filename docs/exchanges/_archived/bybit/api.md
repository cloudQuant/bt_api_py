# Bybit API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: v5
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://bybit-exchange.github.io/docs/v5/intro>

## 交易所基本信息

- 官方名称: Bybit
- 官网: <https://www.bybit.com>
- 交易所类型: CEX (中心化交易所)
- 24h 交易量排名: #3 ($2.8B+)
- 支持的交易对类型: 现货、USDT 永续、USDC 永续、反向永续、期权
- 支持的币种数量: 400+

## API 基础 URL

Bybit 提供统一的 API v5 版本：

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://api.bybit.com`> | 主端点 |

| REST API (测试网) | `<https://api-testnet.bybit.com`> | 测试网端点 |

| WebSocket 公共 | `wss://stream.bybit.com/v5/public/spot` | 现货公共数据流 |

| WebSocket 公共 | `wss://stream.bybit.com/v5/public/linear` | 线性合约公共数据流 |

| WebSocket 私有 | `wss://stream.bybit.com/v5/private` | 私有数据流 |

## 认证方式

### API 密钥获取

1. 登录 Bybit 账户
2. 进入 API Management 页面
3. 创建新的 API 密钥
4. 设置以下信息：
   - API Key
   - Secret Key
1. 配置 API 权限（读取、交易、提现等）
2. 可选：绑定 IP 白名单
3. 可选：设置权限范围（现货、合约等）

### 请求签名方法

Bybit 使用 HMAC SHA256 签名算法。

- *签名步骤**:

1. 构建签名字符串: `timestamp + api_key + recv_window + queryString`
2. 使用 Secret Key 进行 HMAC SHA256 签名
3. 将签名转换为十六进制字符串

- *必需的请求头**:

```bash
X-BAPI-API-KEY: API Key
X-BAPI-TIMESTAMP: 时间戳（毫秒）
X-BAPI-SIGN: 签名
X-BAPI-RECV-WINDOW: 接收窗口（默认 5000ms）
Content-Type: application/json

```bash

- *Python 示例**:

```python
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode

API_KEY = '[YOUR_API_KEY]'
SECRET_KEY = '[YOUR_SECRET_KEY]'
BASE_URL = '<https://api.bybit.com'>

def generate_signature(params, timestamp, recv_window='5000'):
    """生成 Bybit API 签名"""
    param_str = urlencode(sorted(params.items()))
    sign_str = str(timestamp) + API_KEY + recv_window + param_str

    signature = hmac.new(
        bytes(SECRET_KEY, 'utf-8'),
        bytes(sign_str, 'utf-8'),
        hashlib.sha256
    ).hexdigest()

    return signature

def get_headers(params=None):
    """生成请求头"""
    if params is None:
        params = {}

    timestamp = str(int(time.time() *1000))
    recv_window = '5000'
    signature = generate_signature(params, timestamp, recv_window)

    return {
        'X-BAPI-API-KEY': API_KEY,
        'X-BAPI-TIMESTAMP': timestamp,
        'X-BAPI-SIGN': signature,
        'X-BAPI-RECV-WINDOW': recv_window,
        'Content-Type': 'application/json'
    }

def signed_request(method, endpoint, params=None):
    """发送签名请求"""
    if params is None:
        params = {}

    headers = get_headers(params)
    url = BASE_URL + endpoint

    try:
        if method == 'GET':
            response = requests.get(url, params=params, headers=headers)
        elif method == 'POST':
            response = requests.post(url, json=params, headers=headers)

        return response.json()
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 市场数据 API

### 1. 获取交易产品信息

- *端点**: `GET /v5/market/instruments-info`

- *描述**: 获取所有可交易产品的信息

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| category | String | 是 | 产品类型：spot, linear, inverse, option |

| symbol | String | 否 | 交易对名称，如 BTCUSDT |

| baseCoin | String | 否 | 基础币种，如 BTC |

| limit | Integer | 否 | 返回数量，最大 1000 |

- *响应示例**:

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "category": "spot",
    "list": [
      {
        "symbol": "BTCUSDT",
        "baseCoin": "BTC",
        "quoteCoin": "USDT",
        "innovation": "0",
        "status": "Trading",
        "lotSizeFilter": {
          "basePrecision": "0.000001",
          "quotePrecision": "0.00000001",
          "minOrderQty": "0.000048",
          "maxOrderQty": "71.73956243"
        }
      }
    ]
  }
}

```bash

- *Python 示例**:

```python
def get_instruments(category='spot', symbol=None):
    """获取交易产品信息"""
    endpoint = '/v5/market/instruments-info'
    params = {'category': category}
    if symbol:
        params['symbol'] = symbol

    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例

instruments = get_instruments('spot')
if instruments['retCode'] == 0:
    print(f"Total instruments: {len(instruments['result']['list'])}")

```bash

### 2. 获取行情数据

- *端点**: `GET /v5/market/tickers`

- *描述**: 获取最新的行情数据

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| category | String | 是 | 产品类型 |

| symbol | String | 否 | 交易对名称 |

- *响应示例**:

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "category": "spot",
    "list": [
      {
        "symbol": "BTCUSDT",
        "lastPrice": "50000",
        "highPrice24h": "51000",
        "lowPrice24h": "49000",
        "prevPrice24h": "49500",
        "volume24h": "12345.67",
        "turnover24h": "617283500",
        "bid1Price": "49999",
        "bid1Size": "1.5",
        "ask1Price": "50001",
        "ask1Size": "2.3"
      }
    ]
  }
}

```bash

### 3. 获取深度数据

- *端点**: `GET /v5/market/orderbook`

- *描述**: 获取订单簿数据

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| category | String | 是 | 产品类型 |

| symbol | String | 是 | 交易对名称 |

| limit | Integer | 否 | 深度档位：1, 50, 200 |

- *响应示例**:

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "s": "BTCUSDT",
    "b": [
      ["49999", "1.5"],
      ["49998", "2.3"]
    ],
    "a": [
      ["50001", "1.8"],
      ["50002", "3.2"]
    ],
    "ts": 1672304484978,
    "u": 5277055
  }
}

```bash

- *Python 示例**:

```python
def get_order_book(category, symbol, limit=50):
    """获取订单簿"""
    endpoint = '/v5/market/orderbook'
    params = {
        'category': category,
        'symbol': symbol,
        'limit': limit
    }

    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例

order_book = get_order_book('spot', 'BTCUSDT', limit=20)
if order_book['retCode'] == 0:
    result = order_book['result']
    print(f"Best bid: {result['b'][0]}")
    print(f"Best ask: {result['a'][0]}")

```bash

### 4. 获取 K 线数据

- *端点**: `GET /v5/market/kline`

- *描述**: 获取 K 线数据

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| category | String | 是 | 产品类型 |

| symbol | String | 是 | 交易对名称 |

| interval | String | 是 | K 线周期：1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M |

| start | Long | 否 | 开始时间戳（毫秒） |

| end | Long | 否 | 结束时间戳（毫秒） |

| limit | Integer | 否 | 返回数量，最大 1000 |

- *响应示例**:

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "symbol": "BTCUSDT",
    "category": "spot",
    "list": [
      [
        "1672304400000",
        "50000",
        "50500",
        "49500",
        "50200",
        "1000",
        "50100000"
      ]
    ]
  }
}

```bash

- *Python 示例**:

```python
def get_klines(category, symbol, interval='60', limit=100):
    """获取 K 线数据"""
    endpoint = '/v5/market/kline'
    params = {
        'category': category,
        'symbol': symbol,
        'interval': interval,
        'limit': limit
    }

    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    return response.json()

# 使用示例

klines = get_klines('spot', 'BTCUSDT', interval='60', limit=24)
if klines['retCode'] == 0:
    for kline in klines['result']['list'][:3]:
        print(f"Time: {kline[0]}, Open: {kline[1]}, Close: {kline[4]}")

```bash

## 交易 API

### 1. 下单

- *端点**: `POST /v5/order/create`

- *描述**: 创建订单

- *权限**: 需要交易权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| category | String | 是 | 产品类型 |

| symbol | String | 是 | 交易对名称 |

| side | String | 是 | 订单方向：Buy, Sell |

| orderType | String | 是 | 订单类型：Market, Limit |

| qty | String | 是 | 订单数量 |

| price | String | 否 | 订单价格（限价单必需） |

| timeInForce | String | 否 | 有效期：GTC, IOC, FOK |

| orderLinkId | String | 否 | 客户自定义订单 ID |

- *响应示例**:

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "orderId": "1321003749386327552",
    "orderLinkId": "spot-test-001"
  }
}

```bash

- *Python 示例**:

```python
def place_order(category, symbol, side, order_type, qty, price=None):
    """下单"""
    endpoint = '/v5/order/create'

    params = {
        'category': category,
        'symbol': symbol,
        'side': side,
        'orderType': order_type,
        'qty': str(qty)
    }

    if order_type == 'Limit' and price:
        params['price'] = str(price)
        params['timeInForce'] = 'GTC'

    try:
        result = signed_request('POST', endpoint, params)
        return result
    except Exception as e:
        print(f"Order failed: {e}")
        return None

# 使用示例 - 限价买单

order = place_order(
    category='spot',
    symbol='BTCUSDT',
    side='Buy',
    order_type='Limit',
    qty='0.001',
    price='50000'
)
print(f"Order placed: {order}")

# 使用示例 - 市价卖单

order = place_order(
    category='spot',
    symbol='BTCUSDT',
    side='Sell',
    order_type='Market',
    qty='0.001'
)
print(f"Order placed: {order}")

```bash

### 2. 撤销订单

- *端点**: `POST /v5/order/cancel`

- *描述**: 撤销未完成的订单

- *权限**: 需要交易权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| category | String | 是 | 产品类型 |

| symbol | String | 是 | 交易对名称 |

| orderId | String | 否 | 订单 ID |

| orderLinkId | String | 否 | 客户自定义订单 ID |

- *响应示例**:

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "orderId": "1321003749386327552",
    "orderLinkId": "spot-test-001"
  }
}

```bash

- *Python 示例**:

```python
def cancel_order(category, symbol, order_id=None, order_link_id=None):
    """撤销订单"""
    endpoint = '/v5/order/cancel'
    params = {
        'category': category,
        'symbol': symbol
    }

    if order_id:
        params['orderId'] = order_id
    elif order_link_id:
        params['orderLinkId'] = order_link_id
    else:
        raise ValueError("Must provide either orderId or orderLinkId")

    try:
        result = signed_request('POST', endpoint, params)
        return result
    except Exception as e:
        print(f"Cancel failed: {e}")
        return None

# 使用示例

result = cancel_order('spot', 'BTCUSDT', order_id='1321003749386327552')
print(f"Order canceled: {result}")

```bash

### 3. 查询订单信息

- *端点**: `GET /v5/order/realtime`

- *描述**: 查询实时订单信息

- *权限**: 需要读取权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| category | String | 是 | 产品类型 |

| symbol | String | 否 | 交易对名称 |

| orderId | String | 否 | 订单 ID |

| orderLinkId | String | 否 | 客户自定义订单 ID |

| openOnly | Integer | 否 | 仅查询未完成订单：0, 1, 2 |

- *响应示例**:

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "list": [
      {
        "orderId": "1321003749386327552",
        "orderLinkId": "spot-test-001",
        "symbol": "BTCUSDT",
        "price": "50000",
        "qty": "0.001",
        "side": "Buy",
        "orderType": "Limit",
        "orderStatus": "New",
        "cumExecQty": "0",
        "cumExecValue": "0",
        "cumExecFee": "0",
        "timeInForce": "GTC",
        "createdTime": "1672304484978",
        "updatedTime": "1672304484978"
      }
    ]
  }
}

```bash

### 4. 查询历史订单

- *端点**: `GET /v5/order/history`

- *描述**: 查询历史订单（已完成、已取消）

- *权限**: 需要读取权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| category | String | 是 | 产品类型 |

| symbol | String | 否 | 交易对名称 |

| orderId | String | 否 | 订单 ID |

| orderLinkId | String | 否 | 客户自定义订单 ID |

| limit | Integer | 否 | 返回数量，最大 50 |

- *Python 示例**:

```python
def get_orders(category, symbol=None, open_only=0):
    """查询订单"""
    endpoint = '/v5/order/realtime'
    params = {
        'category': category,
        'openOnly': open_only
    }
    if symbol:
        params['symbol'] = symbol

    try:
        result = signed_request('GET', endpoint, params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例

orders = get_orders('spot', 'BTCUSDT', open_only=0)
if orders and orders['retCode'] == 0:
    print(f"Total orders: {len(orders['result']['list'])}")
    for order in orders['result']['list'][:5]:
        print(f"Order {order['orderId']}: {order['side']} {order['qty']} @ {order['price']}")

```bash

## 账户管理 API

### 1. 查询账户余额

- *端点**: `GET /v5/account/wallet-balance`

- *描述**: 获取账户钱包余额

- *权限**: 需要读取权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| accountType | String | 是 | 账户类型：UNIFIED, CONTRACT, SPOT |

| coin | String | 否 | 币种，如 BTC |

- *响应示例**:

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "list": [
      {
        "accountType": "SPOT",
        "totalEquity": "10000",
        "totalWalletBalance": "10000",
        "totalAvailableBalance": "9000",
        "coin": [
          {
            "coin": "BTC",
            "equity": "1",
            "walletBalance": "1",
            "availableToWithdraw": "0.8",
            "locked": "0.2"
          },
          {
            "coin": "USDT",
            "equity": "10000",
            "walletBalance": "10000",
            "availableToWithdraw": "9000",
            "locked": "1000"
          }
        ]
      }
    ]
  }
}

```bash

- *Python 示例**:

```python
def get_balance(account_type='SPOT', coin=None):
    """获取账户余额"""
    endpoint = '/v5/account/wallet-balance'
    params = {'accountType': account_type}
    if coin:
        params['coin'] = coin

    try:
        result = signed_request('GET', endpoint, params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例

balance = get_balance('SPOT')
if balance and balance['retCode'] == 0:
    account = balance['result']['list'][0]
    print(f"Total equity: {account['totalEquity']} USDT")
    for coin_info in account['coin']:
        if float(coin_info['equity']) > 0:
            print(f"{coin_info['coin']}: {coin_info['availableToWithdraw']} (locked: {coin_info['locked']})")

```bash

### 2. 查询成交记录

- *端点**: `GET /v5/execution/list`

- *描述**: 获取成交记录

- *权限**: 需要读取权限

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| category | String | 是 | 产品类型 |

| symbol | String | 否 | 交易对名称 |

| orderId | String | 否 | 订单 ID |

| limit | Integer | 否 | 返回数量，最大 100 |

- *响应示例**:

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "list": [
      {
        "symbol": "BTCUSDT",
        "orderId": "1321003749386327552",
        "orderLinkId": "spot-test-001",
        "side": "Buy",
        "orderPrice": "50000",
        "orderQty": "0.001",
        "execFee": "0.00001",
        "feeRate": "0.001",
        "execId": "2100000000007764263",
        "execPrice": "50000",
        "execQty": "0.001",
        "execType": "Trade",
        "execTime": "1672304484978"
      }
    ]
  }
}

```bash

- *Python 示例**:

```python
def get_executions(category, symbol=None, limit=50):
    """获取成交记录"""
    endpoint = '/v5/execution/list'
    params = {
        'category': category,
        'limit': limit
    }
    if symbol:
        params['symbol'] = symbol

    try:
        result = signed_request('GET', endpoint, params)
        return result
    except Exception as e:
        print(f"Query failed: {e}")
        return None

# 使用示例

executions = get_executions('spot', 'BTCUSDT', limit=10)
if executions and executions['retCode'] == 0:
    print(f"Recent executions: {len(executions['result']['list'])}")
    for exec in executions['result']['list'][:5]:
        print(f"{exec['side']} {exec['execQty']} @ {exec['execPrice']}")

```bash

## 速率限制

### 全局速率限制

Bybit 实施基于 IP 和 UID 的速率限制：

| 限制类型 | 限制值 | 时间窗口 | 说明 |

|---------|--------|----------|------|

| REST API | 120 次 | 5 秒 | 每个 IP |

| 交易端点 | 10 次 | 1 秒 | 每个 UID |

| WebSocket 连接 | 500 个 | - | 每个 UID |

| WebSocket 订阅 | 500 个主题 | - | 每个连接 |

### 不同端点的速率限制

| 端点类别 | 限制 |

|---------|------|

| 公共数据 | 120 次/5 秒 |

| 账户信息 | 20 次/5 秒 |

| 交易下单 | 10 次/1 秒 |

| 撤单 | 10 次/1 秒 |

| 批量下单 | 10 次/1 秒，每次最多 10 个订单 |

### 响应头

速率限制信息包含在响应头中：

```bash
X-Bapi-Limit-Status: 剩余请求数
X-Bapi-Limit: 速率限制值
X-Bapi-Limit-Reset-Timestamp: 重置时间戳

```bash

### 触发限制后的行为

- **HTTP 403**: 超过速率限制
- 响应体包含错误代码 10006 和错误消息
- 建议实施指数退避重试策略

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
                if isinstance(response, dict) and response.get('retCode') == 10006:
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
    return signed_request(method, endpoint, **kwargs)

```bash

## WebSocket 支持

### WebSocket 端点

Bybit 提供多个 WebSocket 端点用于不同的数据流：

| 端点类型 | URL | 说明 |

|---------|-----|------|

| 现货公共 | `wss://stream.bybit.com/v5/public/spot` | 现货市场数据 |

| 线性合约公共 | `wss://stream.bybit.com/v5/public/linear` | USDT 永续合约数据 |

| 反向合约公共 | `wss://stream.bybit.com/v5/public/inverse` | 反向合约数据 |

| 私有数据 | `wss://stream.bybit.com/v5/private` | 账户和订单数据 |

### 认证方法

私有 WebSocket 需要认证：

```python
import json
import time
import hmac
import hashlib
import websocket

def generate_ws_signature(api_key, secret_key):
    """生成 WebSocket 认证签名"""
    expires = int((time.time() + 10) *1000)
    signature = hmac.new(
        bytes(secret_key, 'utf-8'),
        bytes(f'GET/realtime{expires}', 'utf-8'),
        hashlib.sha256
    ).hexdigest()

    return {
        "op": "auth",
        "args": [api_key, expires, signature]
    }

```bash

### 可订阅频道

- *公共频道**:
- `orderbook.{depth}.{symbol}` - 订单簿（深度：1, 50, 200, 500）
- `publicTrade.{symbol}` - 公共成交
- `tickers.{symbol}` - 行情 ticker
- `kline.{interval}.{symbol}` - K 线数据

- *私有频道**:
- `order` - 订单更新
- `execution` - 成交更新
- `wallet` - 钱包余额更新
- `position` - 持仓更新（合约）

### 订阅格式

```python

# 订阅消息格式

subscribe_msg = {
    "op": "subscribe",
    "args": [
        "orderbook.50.BTCUSDT",
        "publicTrade.BTCUSDT",
        "tickers.BTCUSDT"
    ]
}

# 取消订阅

unsubscribe_msg = {
    "op": "unsubscribe",
    "args": ["orderbook.50.BTCUSDT"]
}

```bash

### 心跳机制

Bybit WebSocket 使用 ping/pong 心跳：

```python

# 发送 ping

ping_msg = {"op": "ping"}

# 服务器响应 pong

# {"op": "pong", "ret_msg": "pong", "conn_id": "..."}

```bash

### WebSocket 连接示例

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
        "op": "subscribe",
        "args": ["orderbook.50.BTCUSDT", "publicTrade.BTCUSDT"]
    }
    ws.send(json.dumps(subscribe_msg))
    print("Subscribed to channels")

# 创建 WebSocket 连接

ws_url = "wss://stream.bybit.com/v5/public/spot"
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

```bash

## 错误代码

### 常见错误代码

| 错误代码 | 错误消息 | 可能原因 | 处理建议 |

|---------|---------|---------|---------|

| 0 | OK | 成功 | - |

| 10001 | Parameter error | 参数错误 | 检查请求参数 |

| 10003 | Invalid API key | API 密钥无效 | 检查 API 密钥配置 |

| 10004 | Invalid sign | 签名错误 | 检查签名算法和密钥 |

| 10005 | Permission denied | 权限不足 | 检查 API 权限设置 |

| 10006 | Too many requests | 超过速率限制 | 实施退避重试 |

| 10016 | Server error | 服务器错误 | 稍后重试 |

| 110001 | Order does not exist | 订单不存在 | 检查订单 ID |

| 110004 | Insufficient balance | 余额不足 | 检查账户余额 |

| 110007 | Order price is out of range | 价格超出范围 | 调整订单价格 |

| 110043 | Set margin mode first | 需要先设置保证金模式 | 设置保证金模式 |

### 错误处理示例

```python
def handle_api_error(response):
    """处理 API 错误"""
    if not response:
        return "Network error or timeout"

    ret_code = response.get('retCode', -1)
    ret_msg = response.get('retMsg', 'Unknown error')

    error_handlers = {
        10001: "Parameter error - check your request parameters",
        10003: "Invalid API key - verify your API credentials",
        10004: "Invalid signature - check your signing method",
        10005: "Permission denied - check API key permissions",
        10006: "Rate limit exceeded - implement backoff retry",
        110001: "Order not found - verify order ID",
        110004: "Insufficient balance - check account balance",
        110007: "Price out of range - adjust order price"
    }

    if ret_code == 0:
        return None  # Success

    error_msg = error_handlers.get(ret_code, f"Error {ret_code}: {ret_msg}")
    return error_msg

# 使用示例

response = place_order('spot', 'BTCUSDT', 'Buy', 'Limit', '0.001', '50000')
error = handle_api_error(response)
if error:
    print(f"Order failed: {error}")
else:
    print("Order placed successfully")

```bash

## 变更历史

- 2026-02-27: 初始版本创建，基于 Bybit API v5
