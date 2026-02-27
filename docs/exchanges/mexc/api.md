# MEXC API 文档

## 交易所信息

- **交易所名称**: MEXC (原MXC)
- **官方网站**: https://www.mexc.com
- **API文档**: https://mexcdevelop.github.io/apidocs/spot_v3_en/
- **24h交易量排名**: #10
- **支持的交易对**: 2000+ 交易对
- **API版本**: v3 (当前版本)

## API基础信息

### 基础URL

```
# REST API
https://api.mexc.com

# WebSocket
ws://wbs-api.mexc.com/ws
```

### 请求头

```
X-MEXC-APIKEY: {api_key}
Content-Type: application/json
```

## 认证方式

### 1. 获取API密钥

1. 登录 MEXC 账户
2. 点击右上角个人图标
3. 选择 "API Management"
4. 设置所需权限，添加备注，绑定IP地址
5. 点击创建并完成安全验证
6. 保存 API Key 和 Secret Key

### 2. 请求签名算法

MEXC 使用 HMAC SHA256 签名算法。

**签名步骤**:

1. 生成时间戳 (毫秒)
2. 构建查询字符串和请求体
3. 将查询字符串和请求体拼接为 totalParams
4. 使用 Secret Key 对 totalParams 进行 HMAC SHA256 加密
5. 将签名转换为小写十六进制字符串
6. 将签名作为 signature 参数添加到请求中

**重要规则**:
- 签名必须为小写
- totalParams = queryString + requestBody
- 如果参数同时在查询字符串和请求体中，使用查询字符串的值
- 特殊符号需要URL编码（仅支持大写）

### 3. Python 认证示例

```python
import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode

class MEXCAuth:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.mexc.com"
    
    def _generate_signature(self, params):
        """生成请求签名"""
        # 将参数按字母顺序排序并拼接
        query_string = urlencode(sorted(params.items()))
        
        # 使用HMAC SHA256加密
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return signature.lower()  # 必须小写
    
    def _get_timestamp(self):
        """获取当前时间戳（毫秒）"""
        return int(time.time() * 1000)
    
    def get_request(self, endpoint, params=None):
        """发送GET请求"""
        if params is None:
            params = {}
        
        # 添加时间戳
        params['timestamp'] = self._get_timestamp()
        
        # 生成签名
        params['signature'] = self._generate_signature(params)
        
        # 构建URL
        url = f"{self.base_url}{endpoint}"
        
        # 设置请求头
        headers = {
            'X-MEXC-APIKEY': self.api_key,
            'Content-Type': 'application/json'
        }
        
        response = requests.get(url, params=params, headers=headers)
        return response.json()
    
    def post_request(self, endpoint, params=None):
        """发送POST请求"""
        if params is None:
            params = {}
        
        # 添加时间戳
        params['timestamp'] = self._get_timestamp()
        
        # 生成签名
        params['signature'] = self._generate_signature(params)
        
        # 构建URL
        url = f"{self.base_url}{endpoint}"
        
        # 设置请求头
        headers = {
            'X-MEXC-APIKEY': self.api_key,
            'Content-Type': 'application/json'
        }
        
        response = requests.post(url, params=params, headers=headers)
        return response.json()
    
    def delete_request(self, endpoint, params=None):
        """发送DELETE请求"""
        if params is None:
            params = {}
        
        # 添加时间戳
        params['timestamp'] = self._get_timestamp()
        
        # 生成签名
        params['signature'] = self._generate_signature(params)
        
        # 构建URL
        url = f"{self.base_url}{endpoint}"
        
        # 设置请求头
        headers = {
            'X-MEXC-APIKEY': self.api_key,
            'Content-Type': 'application/json'
        }
        
        response = requests.delete(url, params=params, headers=headers)
        return response.json()

# 使用示例
auth = MEXCAuth('your_api_key', 'your_api_secret')
account_info = auth.get_request('/api/v3/account')
print(account_info)
```

## 市场数据API

### 1. 测试连接

**端点**: `GET /api/v3/ping`

**描述**: 测试与REST API的连接


**Python示例**:

```python
import requests

def test_connectivity():
    """测试连接"""
    url = "https://api.mexc.com/api/v3/ping"
    response = requests.get(url)
    return response.json()

result = test_connectivity()
print(f"连接测试: {result}")
```

### 2. 获取服务器时间

**端点**: `GET /api/v3/time`

**描述**: 获取服务器当前时间

**Python示例**:

```python
def get_server_time():
    """获取服务器时间"""
    url = "https://api.mexc.com/api/v3/time"
    response = requests.get(url)
    data = response.json()
    return data['serverTime']

server_time = get_server_time()
print(f"服务器时间: {server_time}")
```

### 3. 获取交易对信息

**端点**: `GET /api/v3/exchangeInfo`

**描述**: 获取交易规则和交易对信息

**参数**:
- `symbol` (string, 可选): 单个交易对
- `symbols` (string, 可选): 多个交易对，逗号分隔

**Python示例**:

```python
def get_exchange_info(symbol=None):
    """
    获取交易对信息
    
    Args:
        symbol: 交易对符号，例如 'BTCUSDT'
    """
    url = "https://api.mexc.com/api/v3/exchangeInfo"
    params = {}
    if symbol:
        params['symbol'] = symbol
    
    response = requests.get(url, params=params)
    return response.json()

# 获取单个交易对信息
btc_info = get_exchange_info('BTCUSDT')
print(f"BTC/USDT 信息: {btc_info}")

# 获取多个交易对信息
multi_info = get_exchange_info()
print(f"所有交易对数量: {len(multi_info.get('symbols', []))}")
```

### 4. 获取订单簿

**端点**: `GET /api/v3/depth`

**描述**: 获取订单簿深度信息

**参数**:
- `symbol` (string, 必需): 交易对符号
- `limit` (int, 可选): 返回数量，默认100，最大5000

**Python示例**:

```python
def get_order_book(symbol, limit=100):
    """
    获取订单簿
    
    Args:
        symbol: 交易对符号
        limit: 返回数量
    """
    url = "https://api.mexc.com/api/v3/depth"
    params = {
        'symbol': symbol,
        'limit': limit
    }
    response = requests.get(url, params=params)
    return response.json()

book = get_order_book('BTCUSDT', 20)
print("买单 (前5档):")
for price, qty in book['bids'][:5]:
    print(f"  价格: {price}, 数量: {qty}")

print("\n卖单 (前5档):")
for price, qty in book['asks'][:5]:
    print(f"  价格: {price}, 数量: {qty}")
```

### 5. 获取最近成交

**端点**: `GET /api/v3/trades`

**描述**: 获取最近成交记录

**参数**:
- `symbol` (string, 必需): 交易对符号
- `limit` (int, 可选): 返回数量，默认500，最大1000


**Python示例**:

```python
def get_recent_trades(symbol, limit=500):
    """获取最近成交"""
    url = "https://api.mexc.com/api/v3/trades"
    params = {
        'symbol': symbol,
        'limit': limit
    }
    response = requests.get(url, params=params)
    return response.json()

trades = get_recent_trades('BTCUSDT', 10)
print("最近成交:")
for trade in trades:
    side = "买入" if trade['isBuyerMaker'] else "卖出"
    print(f"{side}: 价格={trade['price']}, 数量={trade['qty']}, 时间={trade['time']}")
```

### 6. 获取K线数据

**端点**: `GET /api/v3/klines`

**描述**: 获取K线/蜡烛图数据

**参数**:
- `symbol` (string, 必需): 交易对符号
- `interval` (string, 必需): K线周期
  - `1m`, `5m`, `15m`, `30m`, `60m` (分钟)
  - `4h` (小时)
  - `1d` (天)
  - `1W` (周)
  - `1M` (月)
- `startTime` (long, 可选): 开始时间
- `endTime` (long, 可选): 结束时间
- `limit` (int, 可选): 返回数量，默认500，最大1000

**Python示例**:

```python
def get_klines(symbol, interval='1h', limit=100):
    """
    获取K线数据
    
    Args:
        symbol: 交易对符号
        interval: K线周期
        limit: 返回数量
    """
    url = "https://api.mexc.com/api/v3/klines"
    params = {
        'symbol': symbol,
        'interval': interval,
        'limit': limit
    }
    response = requests.get(url, params=params)
    return response.json()

klines = get_klines('BTCUSDT', '1h', 10)
print("K线数据:")
for kline in klines:
    open_time, open_p, high, low, close, volume, close_time, quote_vol = kline
    print(f"时间={open_time}, 开={open_p}, 高={high}, 低={low}, 收={close}, 量={volume}")
```

### 7. 获取24小时价格变动

**端点**: `GET /api/v3/ticker/24hr`

**描述**: 获取24小时价格变动统计

**参数**:
- `symbol` (string, 可选): 交易对符号，不传则返回所有

**Python示例**:

```python
def get_24hr_ticker(symbol=None):
    """获取24小时ticker"""
    url = "https://api.mexc.com/api/v3/ticker/24hr"
    params = {}
    if symbol:
        params['symbol'] = symbol
    
    response = requests.get(url, params=params)
    return response.json()

ticker = get_24hr_ticker('BTCUSDT')
print(f"24小时统计:")
print(f"  最新价: {ticker['lastPrice']}")
print(f"  涨跌幅: {ticker['priceChangePercent']}%")
print(f"  最高价: {ticker['highPrice']}")
print(f"  最低价: {ticker['lowPrice']}")
print(f"  成交量: {ticker['volume']}")
```

## 交易API

### 1. 查询账户信息

**端点**: `GET /api/v3/account`

**描述**: 获取账户信息和余额

**权限**: SPOT_ACCOUNT_READ

**Python示例**:

```python
def get_account_info(auth):
    """获取账户信息"""
    return auth.get_request('/api/v3/account')

account = get_account_info(auth)
print("账户信息:")
print(f"  可交易: {account['canTrade']}")
print(f"  可提现: {account['canWithdraw']}")
print(f"  可充值: {account['canDeposit']}")

print("\n余额:")
for balance in account['balances']:
    if float(balance['free']) > 0 or float(balance['locked']) > 0:
        print(f"  {balance['asset']}: 可用={balance['free']}, 冻结={balance['locked']}")
```

### 2. 提交订单

**端点**: `POST /api/v3/order`

**描述**: 创建新订单

**权限**: SPOT_DEAL_WRITE

**参数**:
- `symbol` (string, 必需): 交易对符号
- `side` (string, 必需): 订单方向 (`BUY`, `SELL`)
- `type` (string, 必需): 订单类型
  - `LIMIT` - 限价单
  - `MARKET` - 市价单
  - `LIMIT_MAKER` - 只做Maker限价单
  - `IMMEDIATE_OR_CANCEL` - IOC订单
  - `FILL_OR_KILL` - FOK订单
- `quantity` (decimal, 可选): 订单数量
- `quoteOrderQty` (decimal, 可选): 市价单报价数量
- `price` (decimal, 可选): 订单价格
- `newClientOrderId` (string, 可选): 客户端订单ID



**Python示例**:

```python
def place_order(auth, symbol, side, order_type, quantity, price=None):
    """
    提交订单
    
    Args:
        auth: 认证对象
        symbol: 交易对符号
        side: 订单方向 ('BUY' 或 'SELL')
        order_type: 订单类型
        quantity: 订单数量
        price: 订单价格
    """
    params = {
        'symbol': symbol,
        'side': side,
        'type': order_type,
        'quantity': str(quantity)
    }
    
    if price:
        params['price'] = str(price)
    
    return auth.post_request('/api/v3/order', params)

# 限价买单
order = place_order(auth, 'BTCUSDT', 'BUY', 'LIMIT', 0.001, 40000)
print(f"订单已提交: {order}")

# 市价买单
order = place_order(auth, 'BTCUSDT', 'BUY', 'MARKET', 0.001)
print(f"市价订单: {order}")
```

### 3. 取消订单

**端点**: `DELETE /api/v3/order`

**描述**: 取消指定订单

**权限**: SPOT_DEAL_WRITE

**参数**:
- `symbol` (string, 必需): 交易对符号
- `orderId` (long, 可选): 订单ID
- `origClientOrderId` (string, 可选): 客户端订单ID

**Python示例**:

```python
def cancel_order(auth, symbol, order_id=None, client_order_id=None):
    """
    取消订单
    
    Args:
        auth: 认证对象
        symbol: 交易对符号
        order_id: 订单ID
        client_order_id: 客户端订单ID
    """
    params = {'symbol': symbol}
    if order_id:
        params['orderId'] = order_id
    if client_order_id:
        params['origClientOrderId'] = client_order_id
    
    return auth.delete_request('/api/v3/order', params)

# 取消订单
result = cancel_order(auth, 'BTCUSDT', order_id=123456789)
print(f"订单已取消: {result}")
```

### 4. 取消所有订单

**端点**: `DELETE /api/v3/openOrders`

**描述**: 取消指定交易对的所有订单

**权限**: SPOT_DEAL_WRITE

**参数**:
- `symbol` (string, 必需): 交易对符号

**Python示例**:

```python
def cancel_all_orders(auth, symbol):
    """
    取消所有订单
    
    Args:
        auth: 认证对象
        symbol: 交易对符号
    """
    params = {'symbol': symbol}
    return auth.delete_request('/api/v3/openOrders', params)

result = cancel_all_orders(auth, 'BTCUSDT')
print(f"已取消所有订单")
```

### 5. 查询订单

**端点**: `GET /api/v3/order`

**描述**: 查询单个订单详情

**权限**: SPOT_ACCOUNT_READ

**参数**:
- `symbol` (string, 必需): 交易对符号
- `orderId` (long, 可选): 订单ID
- `origClientOrderId` (string, 可选): 客户端订单ID

**Python示例**:

```python
def get_order(auth, symbol, order_id=None, client_order_id=None):
    """
    查询订单
    
    Args:
        auth: 认证对象
        symbol: 交易对符号
        order_id: 订单ID
        client_order_id: 客户端订单ID
    """
    params = {'symbol': symbol}
    if order_id:
        params['orderId'] = order_id
    if client_order_id:
        params['origClientOrderId'] = client_order_id
    
    return auth.get_request('/api/v3/order', params)

order = get_order(auth, 'BTCUSDT', order_id=123456789)
print(f"订单详情: {order}")
```

### 6. 查询未完成订单

**端点**: `GET /api/v3/openOrders`

**描述**: 查询未完成订单列表

**权限**: SPOT_ACCOUNT_READ

**参数**:
- `symbol` (string, 可选): 交易对符号

**Python示例**:

```python
def get_open_orders(auth, symbol=None):
    """
    查询未完成订单
    
    Args:
        auth: 认证对象
        symbol: 交易对符号
    """
    params = {}
    if symbol:
        params['symbol'] = symbol
    
    return auth.get_request('/api/v3/openOrders', params)

orders = get_open_orders(auth, 'BTCUSDT')
print(f"未完成订单数量: {len(orders)}")
for order in orders:
    print(f"  {order['side']} {order['origQty']} @ {order['price']}")
```

### 7. 查询所有订单

**端点**: `GET /api/v3/allOrders`

**描述**: 查询所有订单（包括历史订单）

**权限**: SPOT_ACCOUNT_READ

**参数**:
- `symbol` (string, 必需): 交易对符号
- `orderId` (long, 可选): 起始订单ID
- `startTime` (long, 可选): 开始时间
- `endTime` (long, 可选): 结束时间
- `limit` (int, 可选): 返回数量，默认500，最大1000

**Python示例**:

```python
def get_all_orders(auth, symbol, limit=500):
    """
    查询所有订单
    
    Args:
        auth: 认证对象
        symbol: 交易对符号
        limit: 返回数量
    """
    params = {
        'symbol': symbol,
        'limit': limit
    }
    return auth.get_request('/api/v3/allOrders', params)

orders = get_all_orders(auth, 'BTCUSDT', 10)
print("订单历史:")
for order in orders:
    print(f"  {order['side']} {order['origQty']} @ {order['price']}, 状态: {order['status']}")
```

## 账户管理API

### 1. 查询成交历史

**端点**: `GET /api/v3/myTrades`

**描述**: 查询历史成交记录

**权限**: SPOT_ACCOUNT_READ

**参数**:
- `symbol` (string, 必需): 交易对符号
- `orderId` (long, 可选): 订单ID
- `startTime` (long, 可选): 开始时间
- `endTime` (long, 可选): 结束时间
- `fromId` (long, 可选): 起始成交ID
- `limit` (int, 可选): 返回数量，默认500，最大1000

**Python示例**:

```python
def get_my_trades(auth, symbol, limit=500):
    """
    查询成交历史
    
    Args:
        auth: 认证对象
        symbol: 交易对符号
        limit: 返回数量
    """
    params = {
        'symbol': symbol,
        'limit': limit
    }
    return auth.get_request('/api/v3/myTrades', params)

trades = get_my_trades(auth, 'BTCUSDT', 10)
print("成交历史:")
for trade in trades:
    side = "买入" if trade['isBuyer'] else "卖出"
    print(f"  {side}: {trade['qty']} @ {trade['price']}")
    print(f"    手续费: {trade['commission']} {trade['commissionAsset']}")
```

## 速率限制

### 全局限制

MEXC API有以下速率限制：

**请求权重系统**:
- 每个端点有不同的权重
- 每分钟最多1200权重
- 每秒最多20权重

**订单速率限制**:
- 每10秒最多100个订单
- 每天最多200,000个订单

### 权重说明

| 端点类型 | 权重 |
|---------|------|
| 市场数据 | 1-5 |
| 账户查询 | 5-10 |
| 订单提交 | 1 |
| 订单取消 | 1 |

### 限流响应

当超过速率限制时，API会返回429状态码：

```json
{
  "code": -1003,
  "msg": "Too many requests"
}
```

### Python限流处理示例

```python
import time
from functools import wraps

class RateLimiter:
    def __init__(self, max_weight_per_minute=1200):
        self.max_weight = max_weight_per_minute
        self.weight_used = []
    
    def __call__(self, weight=1):
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                now = time.time()
                # 移除1分钟前的权重记录
                self.weight_used = [(w, t) for w, t in self.weight_used if t > now - 60]
                
                # 计算当前使用的权重
                current_weight = sum(w for w, t in self.weight_used)
                
                if current_weight + weight > self.max_weight:
                    sleep_time = 60 - (now - self.weight_used[0][1])
                    if sleep_time > 0:
                        print(f"速率限制，等待 {sleep_time:.2f} 秒...")
                        time.sleep(sleep_time)
                        self.weight_used = []
                
                self.weight_used.append((weight, time.time()))
                return func(*args, **kwargs)
            return wrapper
        return decorator

# 使用示例
rate_limiter = RateLimiter()

@rate_limiter(weight=1)
def get_ticker_with_limit(symbol):
    return get_24hr_ticker(symbol)
```

## WebSocket API

### 连接信息

**WebSocket URL**: `wss://wbs.mexc.com/ws`

### 订阅格式

```python
import websocket
import json

def on_message(ws, message):
    """处理WebSocket消息"""
    data = json.loads(message)
    print(f"收到消息: {data}")

def on_error(ws, error):
    """处理错误"""
    print(f"错误: {error}")

def on_close(ws, close_status_code, close_msg):
    """连接关闭"""
    print("连接已关闭")

def on_open(ws):
    """连接建立"""
    # 订阅ticker
    subscribe_msg = {
        "method": "SUBSCRIPTION",
        "params": [
            "spot@public.deals.v3.api@BTCUSDT"
        ]
    }
    ws.send(json.dumps(subscribe_msg))

# 创建WebSocket连接
ws = websocket.WebSocketApp(
    "wss://wbs.mexc.com/ws",
    on_message=on_message,
    on_error=on_error,
    on_close=on_close,
    on_open=on_open
)

ws.run_forever()
```

### 可用频道

**1. Ticker (实时价格)**

```python
params = ["spot@public.miniTickers.v3.api"]  # 所有交易对
# 或
params = ["spot@public.miniTicker.v3.api@BTCUSDT"]  # 单个交易对
```

**2. Trade (实时成交)**

```python
params = ["spot@public.deals.v3.api@BTCUSDT"]
```

**3. Orderbook (实时订单簿)**

```python
params = ["spot@public.limit.depth.v3.api@BTCUSDT@20"]  # 20档深度
```

**4. Kline (实时K线)**

```python
params = ["spot@public.kline.v3.api@BTCUSDT@Min1"]  # 1分钟K线
```

### 用户数据流

需要先获取listenKey，然后订阅用户数据流。

**获取listenKey**:

```python
def get_listen_key(auth):
    """获取listenKey"""
    return auth.post_request('/api/v3/userDataStream')

listen_key = get_listen_key(auth)
print(f"ListenKey: {listen_key['listenKey']}")
```

**订阅用户数据**:

```python
def subscribe_user_data(listen_key):
    """订阅用户数据流"""
    def on_open(ws):
        subscribe_msg = {
            "method": "SUBSCRIPTION",
            "params": [
                f"spot@private.account.v3.api",
                f"spot@private.orders.v3.api"
            ]
        }
        ws.send(json.dumps(subscribe_msg))
    
    ws = websocket.WebSocketApp(
        f"wss://wbs.mexc.com/ws?listenKey={listen_key}",
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    
    return ws
```

### 心跳机制

MEXC WebSocket需要定期发送ping消息：

```python
def send_ping(ws):
    """发送ping"""
    ping_msg = {"method": "PING"}
    ws.send(json.dumps(ping_msg))

# 每30秒发送一次ping
import threading

def ping_thread(ws):
    while True:
        time.sleep(30)
        send_ping(ws)

threading.Thread(target=ping_thread, args=(ws,), daemon=True).start()
```

## 错误代码

### 常见错误码

| 错误码 | 描述 | 解决方案 |
|-------|------|---------|
| `-1000` | 未知错误 | 联系技术支持 |
| `-1001` | 断开连接 | 重新连接 |
| `-1002` | 未授权 | 检查API密钥 |
| `-1003` | 请求过多 | 降低请求频率 |
| `-1004` | 重复请求 | 避免重复请求 |
| `-1006` | 异常消息 | 检查请求格式 |
| `-1007` | 超时 | 重试请求 |
| `-1013` | 无效数量 | 检查订单数量 |
| `-1014` | 未知订单类型 | 检查订单类型 |
| `-1015` | 无效订单方向 | 使用BUY或SELL |
| `-1016` | 无效时间戳 | 检查系统时间 |
| `-1020` | 不支持的操作 | 检查API文档 |
| `-1021` | 时间戳超出范围 | 同步系统时间 |
| `-1022` | 无效签名 | 检查签名算法 |
| `-2010` | 新订单被拒绝 | 检查订单参数 |
| `-2011` | 取消订单被拒绝 | 检查订单状态 |
| `-2013` | 订单不存在 | 检查订单ID |
| `-2014` | API Key格式无效 | 检查API Key |
| `-2015` | 无效API Key | 重新生成API Key |

### 错误处理示例

```python
def handle_api_error(response):
    """处理API错误"""
    if 'code' in response and response['code'] != 200:
        code = response['code']
        msg = response.get('msg', 'Unknown error')
        
        if code == -1003:
            print("速率限制，等待后重试...")
            time.sleep(60)
            return 'retry'
        elif code in [-1002, -1022, -2014, -2015]:
            print("认证失败，请检查API密钥")
            return 'auth_error'
        elif code in [-1013, -1014, -1015]:
            print(f"订单参数错误: {msg}")
            return 'param_error'
        elif code == -2013:
            print("订单不存在")
            return 'order_not_found'
        else:
            print(f"API错误 {code}: {msg}")
            return 'error'
    
    return 'success'

# 使用示例
response = place_order(auth, 'BTCUSDT', 'BUY', 'LIMIT', 0.001, 40000)
status = handle_api_error(response)
if status == 'success':
    print("订单提交成功")
```

## 完整示例

### 完整交易流程

```python
import time

# 1. 初始化认证
auth = MEXCAuth('your_api_key', 'your_api_secret')

# 2. 查询账户信息
account = get_account_info(auth)
print("账户信息:")
print(f"  可交易: {account['canTrade']}")

print("\n余额:")
for balance in account['balances']:
    if float(balance['free']) > 0:
        print(f"  {balance['asset']}: {balance['free']}")

# 3. 获取当前价格
ticker = get_24hr_ticker('BTCUSDT')
current_price = float(ticker['lastPrice'])
print(f"\nBTC当前价格: ${current_price:,.2f}")

# 4. 提交限价买单
buy_price = current_price * 0.99  # 低于当前价1%
order = place_order(auth, 'BTCUSDT', 'BUY', 'LIMIT', 0.001, buy_price)
print(f"\n买单已提交: {order['orderId']}")

# 5. 查询订单状态
time.sleep(2)
order_status = get_order(auth, 'BTCUSDT', order_id=order['orderId'])
print(f"订单状态: {order_status['status']}")
print(f"已成交: {order_status['executedQty']}")

# 6. 如果未完全成交，取消订单
if order_status['status'] in ['NEW', 'PARTIALLY_FILLED']:
    cancel_result = cancel_order(auth, 'BTCUSDT', order_id=order['orderId'])
    print(f"订单已取消: {cancel_result['orderId']}")

# 7. 查询成交历史
trades = get_my_trades(auth, 'BTCUSDT', 5)
print(f"\n最近5笔成交:")
for trade in trades:
    side = "买入" if trade['isBuyer'] else "卖出"
    print(f"  {side}: {trade['qty']} @ {trade['price']}")
```

## 注意事项

1. **API密钥安全**: 
   - 不要在代码中硬编码API密钥
   - 使用环境变量或配置文件存储
   - 定期更换API密钥
   - 使用IP白名单限制访问

2. **速率限制**: 
   - 遵守API速率限制
   - 注意权重系统
   - 实现重试机制
   - 使用WebSocket获取实时数据

3. **错误处理**: 
   - 始终检查API响应中的错误
   - 实现适当的错误处理逻辑
   - 记录错误日志便于调试

4. **订单管理**: 
   - 使用客户端订单ID追踪订单
   - 定期查询订单状态
   - 实现订单超时取消机制

5. **签名算法**:
   - 签名必须小写
   - 特殊符号需要URL编码
   - 确保参数顺序正确

6. **时间戳**:
   - 使用毫秒级时间戳
   - 确保系统时间准确
   - 时间戳误差不能超过5秒

7. **WebSocket连接**:
   - 定期发送ping消息
   - 处理断线重连
   - 使用listenKey订阅用户数据

## 相关资源

- [官方API文档](https://mexcdevelop.github.io/apidocs/spot_v3_en/)
- [API状态页面](https://www.mexcstatus.com)
- [开发者社区](https://www.mexc.com/support)
- [Python SDK](https://github.com/mxcdevelop/mexc-api-sdk)
