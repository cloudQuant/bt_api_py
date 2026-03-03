# Bitfinex API 文档

## 交易所信息

- **交易所名称**: Bitfinex
- **官方网站**: <https://www.bitfinex.com>
- **API 文档**: <https://docs.bitfinex.com>
- **24h 交易量排名**: #9
- **24h 交易量**: $233M+
- **支持的交易对**: 400+ 交易对（包括现货、保证金和衍生品）
- **API 版本**: v2 (推荐), v1 (已弃用)

## API 基础信息

### 基础 URL

```bash

# 公共端点

<https://api-pub.bitfinex.com/v2>

# 认证端点

<https://api.bitfinex.com/v2>

# WebSocket

wss://api-pub.bitfinex.com/ws/2

```bash

### 交易对格式

- **现货交易对**: 以 `t` 开头 (例如: `tBTCUSD`, `tETHUSD`)
- **保证金货币**: 以 `f` 开头 (例如: `fUSD`, `fBTC`)
- **所有交易对**: 大写字母 (例如: `BTCUSD` 无效, `BTCUSD` 有效)

### 获取可用交易对列表

```python

# 所有货币

<https://api-pub.bitfinex.com/v2/conf/pub:list:currency>

# 所有现货交易对

<https://api-pub.bitfinex.com/v2/conf/pub:list:pair:exchange>

# 所有保证金交易对

<https://api-pub.bitfinex.com/v2/conf/pub:list:pair:margin>

```bash

## 认证方式

### 1. 获取 API 密钥

1. 登录 Bitfinex 账户
2. 访问 API 密钥管理页面
3. 创建新的 API 密钥
4. 保存 API Key 和 API Secret

### 2. 请求签名算法

Bitfinex 使用 HMAC-SHA384 签名算法。


- *签名步骤**:

1. 生成 nonce (当前时间戳的毫秒数)
2. 构建请求路径 (例如: `/v2/auth/r/wallets`)
3. 构建请求体 JSON 字符串
4. 创建签名字符串: `/api{path}{nonce}{body}`
5. 使用 API Secret 对签名字符串进行 HMAC-SHA384 加密
6. 将签名转换为十六进制字符串

- *请求头**:

```bash
bfx-nonce: {nonce}
bfx-apikey: {api_key}
bfx-signature: {signature}
Content-Type: application/json

```bash

### 3. Python 认证示例

```python
import hmac
import hashlib
import json
import time
import requests

class BitfinexAuth:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "<https://api.bitfinex.com">

    def _generate_signature(self, path, nonce, body=""):
        """生成请求签名"""
        signature_payload = f'/api{path}{nonce}{body}'
        signature = hmac.new(
            self.api_secret.encode(),
            signature_payload.encode(),
            hashlib.sha384
        ).hexdigest()
        return signature

    def _get_headers(self, path, body=""):
        """生成请求头"""
        nonce = str(int(time.time() *1000000))
        signature = self._generate_signature(path, nonce, body)

        return {
            'bfx-nonce': nonce,
            'bfx-apikey': self.api_key,
            'bfx-signature': signature,
            'content-type': 'application/json'
        }

    def post_request(self, path, params=None):
        """发送认证 POST 请求"""
        body = json.dumps(params) if params else ""
        headers = self._get_headers(path, body)
        url = f"{self.base_url}{path}"

        response = requests.post(url, headers=headers, data=body)
        return response.json()

# 使用示例

auth = BitfinexAuth('your_api_key', 'your_api_secret')
wallets = auth.post_request('/v2/auth/r/wallets')
print(wallets)

```bash

## 市场数据 API

### 1. 获取平台状态

- *端点**: `GET /v2/platform/status`

- *描述**: 获取平台当前状态（运行中或维护中）


- *Python 示例**:

```python
import requests

def get_platform_status():
    """获取平台状态"""
    url = "<https://api-pub.bitfinex.com/v2/platform/status">
    response = requests.get(url)
    status = response.json()

# 返回: 1 = 运行中, 0 = 维护中
    return status

status = get_platform_status()
print(f"平台状态: {'运行中' if status == 1 else '维护中'}")

```bash

### 2. 获取 Ticker 信息

- *端点**: `GET /v2/tickers`

- *描述**: 获取一个或多个交易对的 ticker 信息

- *参数**:
- `symbols` (string): 交易对列表，逗号分隔，或使用 `ALL` 获取所有

- *响应格式** (现货交易对):

```bash
[SYMBOL, BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_PERC,
 LAST_PRICE, VOLUME, HIGH, LOW]

```bash

- *Python 示例**:

```python
def get_tickers(symbols):
    """
    获取 ticker 信息

    Args:
        symbols: 交易对列表，例如 ['tBTCUSD', 'tETHUSD'] 或 'ALL'
    """
    if isinstance(symbols, list):
        symbols_str = ','.join(symbols)
    else:
        symbols_str = symbols

    url = f"<https://api-pub.bitfinex.com/v2/tickers?symbols={symbols_str}">
    response = requests.get(url)
    return response.json()

# 获取单个交易对

ticker = get_tickers(['tBTCUSD'])
print(f"BTC/USD Ticker: {ticker}")

# 获取多个交易对

tickers = get_tickers(['tBTCUSD', 'tETHUSD'])
for t in tickers:
    symbol, bid, bid_size, ask, ask_size, change, change_perc, last, volume, high, low = t
    print(f"{symbol}: Last={last}, Bid={bid}, Ask={ask}, Volume={volume}")

```bash

### 3. 获取单个 Ticker

- *端点**: `GET /v2/ticker/{symbol}`

- *描述**: 获取单个交易对的 ticker 信息

- *Python 示例**:

```python
def get_ticker(symbol):
    """获取单个交易对 ticker"""
    url = f"<https://api-pub.bitfinex.com/v2/ticker/{symbol}">
    response = requests.get(url)
    return response.json()

ticker = get_ticker('tBTCUSD')
bid, bid_size, ask, ask_size, change, change_perc, last, volume, high, low = ticker
print(f"BTC/USD: 最新价={last}, 买一={bid}, 卖一={ask}, 24h 成交量={volume}")

```bash

### 4. 获取订单簿

- *端点**: `GET /v2/book/{symbol}/{precision}`

- *描述**: 获取订单簿数据

- *参数**:
- `symbol` (string): 交易对符号
- `precision` (string): 价格聚合精度 (`P0`, `P1`, `P2`, `P3`, `R0`)
  - `P0` - `P3`: 不同级别的价格聚合
  - `R0`: 原始订单簿（不聚合）
- `len` (string, 可选): 价格点数量 (`1`, `25`, `100`)


- *响应格式**:

```bash

# 聚合订单簿 (P0-P3)

[[PRICE, COUNT, AMOUNT], ...]

# 原始订单簿 (R0)

[[ORDER_ID, PRICE, AMOUNT], ...]

```bash

- *Python 示例**:

```python
def get_order_book(symbol, precision='P0', length='25'):
    """
    获取订单簿

    Args:
        symbol: 交易对符号
        precision: 精度级别 (P0, P1, P2, P3, R0)
        length: 返回的价格点数量
    """
    url = f"<https://api-pub.bitfinex.com/v2/book/{symbol}/{precision}">
    params = {'len': length}
    response = requests.get(url, params=params)
    return response.json()

# 获取聚合订单簿

book = get_order_book('tBTCUSD', 'P0', '25')
print("订单簿 (前 5 档):")
for i, (price, count, amount) in enumerate(book[:5]):
    side = "买" if amount > 0 else "卖"
    print(f"{side}: 价格={price}, 数量={abs(amount)}, 订单数={count}")

# 获取原始订单簿

raw_book = get_order_book('tBTCUSD', 'R0', '10')
print("\n 原始订单簿:")
for order_id, price, amount in raw_book[:5]:
    print(f"订单 ID={order_id}, 价格={price}, 数量={amount}")

```bash

### 5. 获取最近成交

- *端点**: `GET /v2/trades/{symbol}/hist`

- *描述**: 获取历史成交记录

- *参数**:
- `limit` (int): 返回记录数，最大 10000
- `start` (int): 开始时间戳（毫秒）
- `end` (int): 结束时间戳（毫秒）
- `sort` (int): 排序方式，1=升序，-1=降序

- *响应格式**:

```bash
[[ID, MTS, AMOUNT, PRICE], ...]

```bash

- *Python 示例**:

```python
def get_trades(symbol, limit=100, sort=-1):
    """
    获取最近成交记录

    Args:
        symbol: 交易对符号
        limit: 返回记录数
        sort: 排序方式 (1=升序, -1=降序)
    """
    url = f"<https://api-pub.bitfinex.com/v2/trades/{symbol}/hist">
    params = {
        'limit': limit,
        'sort': sort
    }
    response = requests.get(url, params=params)
    return response.json()

trades = get_trades('tBTCUSD', limit=10)
print("最近成交:")
for trade_id, timestamp, amount, price in trades:
    side = "买入" if amount > 0 else "卖出"
    print(f"时间={timestamp}, {side}, 价格={price}, 数量={abs(amount)}")

```bash

### 6. 获取 K 线数据

- *端点**: `GET /v2/candles/trade:{timeframe}:{symbol}/hist`

- *描述**: 获取 K 线/蜡烛图数据

- *参数**:
- `timeframe` (string): 时间周期 (`1m`, `5m`, `15m`, `30m`, `1h`, `3h`, `6h`, `12h`, `1D`, `7D`, `14D`, `1M`)
- `limit` (int): 返回 K 线数量，最大 10000
- `start` (int): 开始时间戳（毫秒）
- `end` (int): 结束时间戳（毫秒）
- `sort` (int): 排序方式，1=升序，-1=降序

- *响应格式**:

```bash
[[MTS, OPEN, CLOSE, HIGH, LOW, VOLUME], ...]

```bash

- *Python 示例**:

```python
def get_candles(symbol, timeframe='1h', limit=100):
    """
    获取 K 线数据

    Args:
        symbol: 交易对符号
        timeframe: 时间周期
        limit: 返回 K 线数量
    """
    url = f"<https://api-pub.bitfinex.com/v2/candles/trade:{timeframe}:{symbol}/hist">
    params = {'limit': limit}
    response = requests.get(url, params=params)
    return response.json()

# 获取 1 小时 K 线

candles = get_candles('tBTCUSD', '1h', 10)
print("K 线数据:")
for mts, open_price, close, high, low, volume in candles:
    print(f"时间={mts}, 开={open_price}, 高={high}, 低={low}, 收={close}, 量={volume}")

# 获取 1 分钟 K 线

candles_1m = get_candles('tBTCUSD', '1m', 5)
print("\n1 分钟 K 线:")
for candle in candles_1m:
    print(candle)

```bash

## 交易 API

### 1. 查询账户余额

- *端点**: `POST /v2/auth/r/wallets`

- *描述**: 获取账户钱包余额

- *响应格式**:

```bash
[[WALLET_TYPE, CURRENCY, BALANCE, UNSETTLED_INTEREST, BALANCE_AVAILABLE], ...]

```bash

- *Python 示例**:

```python
def get_wallets(auth):
    """获取账户钱包余额"""
    return auth.post_request('/v2/auth/r/wallets')

wallets = get_wallets(auth)
print("账户余额:")
for wallet_type, currency, balance, unsettled, available in wallets:
    print(f"{wallet_type} - {currency}: 余额={balance}, 可用={available}")

```bash

### 2. 查询活跃订单

- *端点**: `POST /v2/auth/r/orders`

- *描述**: 获取所有活跃订单

- *可选参数**:
- 在 URL 中添加交易对: `/v2/auth/r/orders/{symbol}`

- *响应格式**:

```bash
[[ID, GID, CID, SYMBOL, MTS_CREATE, MTS_UPDATE, AMOUNT, AMOUNT_ORIG, TYPE,
  TYPE_PREV, ..., FLAGS, STATUS, ..., PRICE, PRICE_AVG, ...], ...]

```bash

- *Python 示例**:

```python
def get_active_orders(auth, symbol=None):
    """
    获取活跃订单

    Args:
        auth: 认证对象
        symbol: 可选，指定交易对
    """
    path = f'/v2/auth/r/orders/{symbol}' if symbol else '/v2/auth/r/orders'
    return auth.post_request(path)

# 获取所有活跃订单

orders = get_active_orders(auth)
print("活跃订单:")
for order in orders:
    order_id, gid, cid, symbol = order[0], order[1], order[2], order[3]
    amount, amount_orig, order_type = order[6], order[7], order[8]
    status, price = order[13], order[16]
    print(f"订单 ID={order_id}, {symbol}, 类型={order_type}, 价格={price}, 数量={amount}, 状态={status}")

# 获取指定交易对的活跃订单

btc_orders = get_active_orders(auth, 'tBTCUSD')
print(f"\nBTC/USD 活跃订单数: {len(btc_orders)}")

```bash

### 3. 提交订单

- *端点**: `POST /v2/auth/w/order/submit`

- *描述**: 提交新订单


- *参数**:
- `type` (string): 订单类型
  - `LIMIT` - 限价单
  - `MARKET` - 市价单
  - `STOP` - 止损单
  - `STOP LIMIT` - 止损限价单
  - `TRAILING STOP` - 追踪止损单
  - `EXCHANGE LIMIT` - 交易所限价单
  - `EXCHANGE MARKET` - 交易所市价单
  - `FOK` - Fill or Kill
  - `IOC` - Immediate or Cancel
- `symbol` (string): 交易对符号
- `amount` (string): 订单数量（正数=买入，负数=卖出）
- `price` (string): 订单价格
- `flags` (int, 可选): 订单标志（例如: 64=隐藏订单）
- `cid` (int, 可选): 客户端订单 ID
- `gid` (int, 可选): 订单组 ID
- `lev` (int, 可选): 杠杆倍数（衍生品订单，1-100）

- *Python 示例**:

```python
def submit_order(auth, order_type, symbol, amount, price=None, flags=0):
    """
    提交订单

    Args:
        auth: 认证对象
        order_type: 订单类型
        symbol: 交易对符号
        amount: 订单数量 (正数买入, 负数卖出)
        price: 订单价格 (市价单可为 None)
        flags: 订单标志
    """
    params = {
        'type': order_type,
        'symbol': symbol,
        'amount': str(amount),
        'flags': flags
    }

    if price is not None:
        params['price'] = str(price)

    return auth.post_request('/v2/auth/w/order/submit', params)

# 提交限价买单

buy_order = submit_order(
    auth,
    order_type='EXCHANGE LIMIT',
    symbol='tBTCUSD',
    amount='0.001',  # 买入 0.001 BTC
    price='40000'
)
print(f"买单已提交: {buy_order}")

# 提交限价卖单

sell_order = submit_order(
    auth,
    order_type='EXCHANGE LIMIT',
    symbol='tBTCUSD',
    amount='-0.001',  # 卖出 0.001 BTC
    price='45000'
)
print(f"卖单已提交: {sell_order}")

# 提交市价买单

market_order = submit_order(
    auth,
    order_type='EXCHANGE MARKET',
    symbol='tBTCUSD',
    amount='0.001'
)
print(f"市价单已提交: {market_order}")

# 提交隐藏订单 (flags=64)

hidden_order = submit_order(
    auth,
    order_type='EXCHANGE LIMIT',
    symbol='tBTCUSD',
    amount='0.001',
    price='40000',
    flags=64
)
print(f"隐藏订单已提交: {hidden_order}")

```bash

### 4. 更新订单

- *端点**: `POST /v2/auth/w/order/update`

- *描述**: 更新现有订单

- *参数**:
- `id` (int): 订单 ID
- `price` (string, 可选): 新价格
- `amount` (string, 可选): 新数量
- `delta` (string, 可选): 数量变化
- `flags` (int, 可选): 订单标志

- *Python 示例**:

```python
def update_order(auth, order_id, price=None, amount=None, flags=None):
    """
    更新订单

    Args:
        auth: 认证对象
        order_id: 订单 ID
        price: 新价格
        amount: 新数量
        flags: 订单标志
    """
    params = {'id': order_id}

    if price is not None:
        params['price'] = str(price)
    if amount is not None:
        params['amount'] = str(amount)
    if flags is not None:
        params['flags'] = flags

    return auth.post_request('/v2/auth/w/order/update', params)

# 更新订单价格

updated = update_order(auth, order_id=12345678, price='41000')
print(f"订单已更新: {updated}")

# 更新订单数量

updated = update_order(auth, order_id=12345678, amount='0.002')
print(f"订单数量已更新: {updated}")

```bash

### 5. 取消订单

- *端点**: `POST /v2/auth/w/order/cancel`

- *描述**: 取消单个订单

- *参数**:
- `id` (int): 订单 ID
- 或 `cid` (int) + `cid_date` (string): 客户端订单 ID + 日期 (YYYY-MM-DD)


- *Python 示例**:

```python
def cancel_order(auth, order_id=None, cid=None, cid_date=None):
    """
    取消订单

    Args:
        auth: 认证对象
        order_id: 订单 ID
        cid: 客户端订单 ID
        cid_date: 客户端订单日期 (YYYY-MM-DD)
    """
    if order_id:
        params = {'id': order_id}
    elif cid and cid_date:
        params = {'cid': cid, 'cid_date': cid_date}
    else:
        raise ValueError("必须提供 order_id 或 (cid + cid_date)")

    return auth.post_request('/v2/auth/w/order/cancel', params)

# 通过订单 ID 取消

result = cancel_order(auth, order_id=12345678)
print(f"订单已取消: {result}")

# 通过客户端订单 ID 取消

result = cancel_order(auth, cid=1001, cid_date='2026-02-27')
print(f"订单已取消: {result}")

```bash

### 6. 批量取消订单

- *端点**: `POST /v2/auth/w/order/cancel/multi`

- *描述**: 批量取消多个订单

- *参数**:
- `id` (list): 订单 ID 列表
- `gid` (int): 订单组 ID
- `all` (int): 设置为 1 取消所有订单

- *Python 示例**:

```python
def cancel_orders_multi(auth, order_ids=None, gid=None, cancel_all=False):
    """
    批量取消订单

    Args:
        auth: 认证对象
        order_ids: 订单 ID 列表
        gid: 订单组 ID
        cancel_all: 是否取消所有订单
    """
    params = {}

    if cancel_all:
        params['all'] = 1
    elif order_ids:
        params['id'] = order_ids
    elif gid:
        params['gid'] = gid
    else:
        raise ValueError("必须提供 order_ids, gid 或 cancel_all=True")

    return auth.post_request('/v2/auth/w/order/cancel/multi', params)

# 取消多个订单

result = cancel_orders_multi(auth, order_ids=[12345678, 12345679, 12345680])
print(f"批量取消结果: {result}")

# 取消所有订单

result = cancel_orders_multi(auth, cancel_all=True)
print(f"所有订单已取消: {result}")

```bash

### 7. 查询订单历史

- *端点**: `POST /v2/auth/r/orders/{symbol}/hist`

- *描述**: 获取历史订单（最近约 2 周）

- *参数**:
- `symbol` (string, 可选): 交易对符号
- `start` (int): 开始时间戳（毫秒）
- `end` (int): 结束时间戳（毫秒）
- `limit` (int): 返回记录数
- `id` (list): 订单 ID 列表

- *Python 示例**:

```python
def get_orders_history(auth, symbol=None, limit=100):
    """
    获取订单历史

    Args:
        auth: 认证对象
        symbol: 交易对符号
        limit: 返回记录数
    """
    path = f'/v2/auth/r/orders/{symbol}/hist' if symbol else '/v2/auth/r/orders/hist'
    params = {'limit': limit}
    return auth.post_request(path, params)

# 获取所有历史订单

history = get_orders_history(auth, limit=50)
print(f"历史订单数: {len(history)}")

# 获取指定交易对的历史订单

btc_history = get_orders_history(auth, symbol='tBTCUSD', limit=20)
print(f"BTC/USD 历史订单:")
for order in btc_history[:5]:
    order_id, symbol, amount, price, status = order[0], order[3], order[6], order[16], order[13]
    print(f"ID={order_id}, {symbol}, 数量={amount}, 价格={price}, 状态={status}")

```bash

### 8. 查询成交记录

- *端点**: `POST /v2/auth/r/trades/{symbol}/hist`

- *描述**: 获取账户成交历史

- *参数**:
- `symbol` (string, 可选): 交易对符号
- `start` (int): 开始时间戳（毫秒）
- `end` (int): 结束时间戳（毫秒）
- `limit` (int): 返回记录数
- `sort` (int): 排序方式


- *Python 示例**:

```python
def get_trades_history(auth, symbol=None, limit=100):
    """
    获取成交历史

    Args:
        auth: 认证对象
        symbol: 交易对符号
        limit: 返回记录数
    """
    path = f'/v2/auth/r/trades/{symbol}/hist' if symbol else '/v2/auth/r/trades/hist'
    params = {'limit': limit}
    return auth.post_request(path, params)

# 获取所有成交记录

trades = get_trades_history(auth, limit=50)
print(f"成交记录数: {len(trades)}")

# 获取指定交易对的成交记录

btc_trades = get_trades_history(auth, symbol='tBTCUSD', limit=10)
print("BTC/USD 成交记录:")
for trade in btc_trades:
    trade_id, symbol, timestamp, order_id = trade[0], trade[1], trade[2], trade[3]
    amount, price, fee = trade[4], trade[5], trade[9]
    print(f"ID={trade_id}, 时间={timestamp}, 价格={price}, 数量={amount}, 手续费={fee}")

```bash

## 账户管理 API

### 1. 查询保证金信息

- *端点**: `POST /v2/auth/r/info/margin/{key}`

- *描述**: 获取账户保证金信息

- *参数**:
- `key` (string): `base` 或交易对符号

- *Python 示例**:

```python
def get_margin_info(auth, key='base'):
    """
    获取保证金信息

    Args:
        auth: 认证对象
        key: 'base' 或交易对符号
    """
    path = f'/v2/auth/r/info/margin/{key}'
    return auth.post_request(path)

# 获取基础保证金信息

margin_base = get_margin_info(auth, 'base')
print(f"保证金信息: {margin_base}")

# 获取指定交易对的保证金信息

margin_btc = get_margin_info(auth, 'tBTCUSD')
print(f"BTC/USD 保证金: {margin_btc}")

```bash

### 2. 查询持仓

- *端点**: `POST /v2/auth/r/positions`

- *描述**: 获取活跃持仓

- *Python 示例**:

```python
def get_positions(auth):
    """获取活跃持仓"""
    return auth.post_request('/v2/auth/r/positions')

positions = get_positions(auth)
print("活跃持仓:")
for pos in positions:
    symbol, status, amount, base_price = pos[0], pos[1], pos[2], pos[3]
    pl, pl_perc, leverage = pos[6], pos[7], pos[9]
    print(f"{symbol}: 数量={amount}, 开仓价={base_price}, 盈亏={pl} ({pl_perc}%), 杠杆={leverage}x")

```bash

### 3. 账户间转账

- *端点**: `POST /v2/auth/w/transfer`

- *描述**: 在不同钱包之间转账

- *参数**:
- `from` (string): 源钱包 (`exchange`, `margin`, `funding`)
- `to` (string): 目标钱包 (`exchange`, `margin`, `funding`)
- `currency` (string): 货币代码
- `currency_to` (string, 可选): 目标货币（用于转换）
- `amount` (string): 转账金额

- *Python 示例**:

```python
def transfer_between_wallets(auth, from_wallet, to_wallet, currency, amount, currency_to=None):
    """
    钱包间转账

    Args:
        auth: 认证对象
        from_wallet: 源钱包
        to_wallet: 目标钱包
        currency: 货币代码
        amount: 转账金额
        currency_to: 目标货币（可选）
    """
    params = {
        'from': from_wallet,
        'to': to_wallet,
        'currency': currency,
        'amount': str(amount)
    }

    if currency_to:
        params['currency_to'] = currency_to

    return auth.post_request('/v2/auth/w/transfer', params)

# 从交易账户转到保证金账户

result = transfer_between_wallets(
    auth,
    from_wallet='exchange',
    to_wallet='margin',
    currency='USD',
    amount='100'
)
print(f"转账结果: {result}")

# 转账并转换货币 (USDT -> USTF0 用于衍生品)

result = transfer_between_wallets(
    auth,
    from_wallet='exchange',
    to_wallet='margin',
    currency='UST',
    amount='200',
    currency_to='USTF0'
)
print(f"转账转换结果: {result}")

```bash

### 4. 查询资金流水

- *端点**: `POST /v2/auth/r/ledgers/{currency}/hist`

- *描述**: 查看历史账本记录


- *参数**:
- `currency` (string, 可选): 货币代码
- `start` (int): 开始时间戳（毫秒）
- `end` (int): 结束时间戳（毫秒）
- `limit` (int): 返回记录数

- *Python 示例**:

```python
def get_ledgers(auth, currency='', limit=100):
    """
    获取资金流水

    Args:
        auth: 认证对象
        currency: 货币代码
        limit: 返回记录数
    """
    path = f'/v2/auth/r/ledgers/{currency}/hist' if currency else '/v2/auth/r/ledgers/hist'
    params = {'limit': limit}
    return auth.post_request(path, params)

# 获取所有货币的流水

ledgers = get_ledgers(auth, limit=50)
print(f"资金流水记录数: {len(ledgers)}")

# 获取 BTC 流水

btc_ledgers = get_ledgers(auth, currency='BTC', limit=20)
print("BTC 资金流水:")
for ledger in btc_ledgers[:5]:
    ledger_id, currency, timestamp, amount, balance, description = ledger[0], ledger[1], ledger[2], ledger[3], ledger[4], ledger[5]
    print(f"时间={timestamp}, 金额={amount}, 余额={balance}, 描述={description}")

```bash

### 5. 查询充值提现记录

- *端点**: `POST /v2/auth/r/movements/{currency}/hist`

- *描述**: 查看充值和提现历史

- *参数**:
- `currency` (string, 可选): 货币代码
- `start` (int): 开始时间戳（毫秒）
- `end` (int): 结束时间戳（毫秒）
- `limit` (int): 返回记录数，默认和最大 25

- *Python 示例**:

```python
def get_movements(auth, currency='', limit=25):
    """
    获取充值提现记录

    Args:
        auth: 认证对象
        currency: 货币代码
        limit: 返回记录数
    """
    path = f'/v2/auth/r/movements/{currency}/hist' if currency else '/v2/auth/r/movements/hist'
    params = {'limit': limit}
    return auth.post_request(path, params)

# 获取所有充值提现记录

movements = get_movements(auth)
print("充值提现记录:")
for mov in movements:
    mov_id, currency, started, updated, status = mov[0], mov[1], mov[5], mov[6], mov[9]
    amount, fees, address, tx_id = mov[12], mov[13], mov[16], mov[20]
    print(f"ID={mov_id}, {currency}, 金额={amount}, 手续费={fees}, 状态={status}")

# 获取 BTC 充值提现记录

btc_movements = get_movements(auth, currency='BTC')
print(f"\nBTC 充值提现记录数: {len(btc_movements)}")

```bash

## 速率限制

### 限制规则

Bitfinex 对 REST API 实施速率限制，以确保平台稳定性。

- *限制范围**: 每分钟 10-90 次请求，具体取决于端点

- *限制类型**:
- 公共端点: 较宽松的限制
- 认证端点: 根据端点类型有不同限制
- 订单相关: 基础限制为每 5 分钟 1000 个订单，根据交易量比例增加

- *建议**:
- 实现请求队列和速率控制
- 使用 WebSocket 获取实时数据，减少 REST 请求
- 缓存不经常变化的数据
- 监控响应头中的速率限制信息

- *Python 速率限制示例**:

```python
import time
from collections import deque

class RateLimiter:
    def __init__(self, max_requests, time_window):
        """
        速率限制器

        Args:
            max_requests: 时间窗口内最大请求数
            time_window: 时间窗口（秒）
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = deque()

    def wait_if_needed(self):
        """如果需要，等待直到可以发送请求"""
        now = time.time()

# 移除时间窗口外的请求
        while self.requests and self.requests[0] < now - self.time_window:
            self.requests.popleft()

# 如果达到限制，等待
        if len(self.requests) >= self.max_requests:
            sleep_time = self.requests[0] + self.time_window - now
            if sleep_time > 0:
                print(f"速率限制: 等待 {sleep_time:.2f} 秒")
                time.sleep(sleep_time)
                return self.wait_if_needed()

# 记录此次请求
        self.requests.append(now)

# 创建速率限制器 (每分钟 60 次请求)

limiter = RateLimiter(max_requests=60, time_window=60)

def rate_limited_request(url):
    """带速率限制的请求"""
    limiter.wait_if_needed()
    response = requests.get(url)
    return response.json()

# 使用示例

for i in range(100):
    data = rate_limited_request('<https://api-pub.bitfinex.com/v2/ticker/tBTCUSD')>
    print(f"请求 {i+1}: {data}")

```bash

## WebSocket 支持

### 连接信息

- *WebSocket URL**: `wss://api-pub.bitfinex.com/ws/2`

- *特点**:
- 实时市场数据推送
- 支持多频道订阅
- 支持认证频道（账户更新）
- 自动心跳机制


### 公共频道

- *可用频道**:
- `ticker` - Ticker 数据
- `trades` - 实时成交
- `book` - 订单簿
- `candles` - K 线数据
- `status` - 衍生品状态

### 认证频道

- *可用频道**:
- 账户余额更新
- 订单更新
- 持仓更新
- 成交通知

### WebSocket 认证

认证需要发送包含签名的消息：

```python
import json
import hmac
import hashlib
import time

def generate_auth_payload(api_key, api_secret):
    """生成 WebSocket 认证载荷"""
    nonce = str(int(time.time() *1000000))
    auth_payload = f'AUTH{nonce}'
    signature = hmac.new(
        api_secret.encode(),
        auth_payload.encode(),
        hashlib.sha384
    ).hexdigest()

    return {
        'event': 'auth',
        'apiKey': api_key,
        'authSig': signature,
        'authPayload': auth_payload,
        'authNonce': nonce
    }

```bash

### Python WebSocket 示例

```python
import websocket
import json
import threading

class BitfinexWebSocket:
    def __init__(self):
        self.ws = None
        self.url = "wss://api-pub.bitfinex.com/ws/2"

    def on_message(self, ws, message):
        """处理接收到的消息"""
        data = json.loads(message)
        print(f"收到消息: {data}")

    def on_error(self, ws, error):
        """处理错误"""
        print(f"错误: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        """连接关闭"""
        print("WebSocket 连接已关闭")

    def on_open(self, ws):
        """连接建立"""
        print("WebSocket 连接已建立")

# 订阅 ticker
        subscribe_msg = {
            'event': 'subscribe',
            'channel': 'ticker',
            'symbol': 'tBTCUSD'
        }
        ws.send(json.dumps(subscribe_msg))

    def connect(self):
        """建立 WebSocket 连接"""
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )

# 在新线程中运行
        wst = threading.Thread(target=self.ws.run_forever)
        wst.daemon = True
        wst.start()

    def subscribe_ticker(self, symbol):
        """订阅 ticker"""
        msg = {
            'event': 'subscribe',
            'channel': 'ticker',
            'symbol': symbol
        }
        self.ws.send(json.dumps(msg))

    def subscribe_trades(self, symbol):
        """订阅实时成交"""
        msg = {
            'event': 'subscribe',
            'channel': 'trades',
            'symbol': symbol
        }
        self.ws.send(json.dumps(msg))

    def subscribe_book(self, symbol, precision='P0', length='25'):
        """订阅订单簿"""
        msg = {
            'event': 'subscribe',
            'channel': 'book',
            'symbol': symbol,
            'prec': precision,
            'len': length
        }
        self.ws.send(json.dumps(msg))

    def subscribe_candles(self, symbol, timeframe='1m'):
        """订阅 K 线"""
        msg = {
            'event': 'subscribe',
            'channel': 'candles',
            'key': f'trade:{timeframe}:{symbol}'
        }
        self.ws.send(json.dumps(msg))

# 使用示例

ws_client = BitfinexWebSocket()
ws_client.connect()

# 等待连接建立

time.sleep(2)

# 订阅多个频道

ws_client.subscribe_ticker('tBTCUSD')
ws_client.subscribe_trades('tETHUSD')
ws_client.subscribe_book('tBTCUSD', 'P0', '25')
ws_client.subscribe_candles('tBTCUSD', '1m')

# 保持运行

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("程序退出")

```bash

### 认证 WebSocket 示例

```python
class BitfinexAuthWebSocket(BitfinexWebSocket):
    def __init__(self, api_key, api_secret):
        super().__init__()
        self.api_key = api_key
        self.api_secret = api_secret

    def on_open(self, ws):
        """连接建立后进行认证"""
        print("WebSocket 连接已建立，正在认证...")

# 发送认证消息
        auth_payload = generate_auth_payload(self.api_key, self.api_secret)
        ws.send(json.dumps(auth_payload))

# 使用认证 WebSocket

auth_ws = BitfinexAuthWebSocket('your_api_key', 'your_api_secret')
auth_ws.connect()

# 认证后会自动接收账户更新

time.sleep(60)

```bash

## 错误代码

### 常见错误代码

| 错误代码 | 描述 | 解决方案 |

|---------|------|---------|

| 10000 | 未知错误 | 检查请求格式和参数 |

| 10001 | 通用错误 | 查看错误消息详情 |

| 10020 | 维护中 | 等待维护结束 |

| 10050 | 无效符号 | 检查交易对格式 |

| 10100 | 认证失败 | 检查 API 密钥和签名 |

| 10114 | 无效 API 密钥 | 验证 API 密钥是否正确 |

| 11000 | 速率限制 | 降低请求频率 |

| 12020 | 余额不足 | 充值或减少订单金额 |

| 12100 | 订单不存在 | 检查订单 ID |


### 错误处理示例

```python
def handle_api_error(response):
    """处理 API 错误响应"""
    if isinstance(response, list) and len(response) > 0:
        if response[0] == 'error':
            error_code = response[1]
            error_msg = response[2]

            error_handlers = {
                10020: lambda: print("交易所维护中，请稍后再试"),
                10050: lambda: print("无效的交易对符号"),
                10100: lambda: print("认证失败，请检查 API 密钥"),
                11000: lambda: print("触发速率限制，请降低请求频率"),
                12020: lambda: print("余额不足"),
                12100: lambda: print("订单不存在")
            }

            handler = error_handlers.get(error_code, lambda: print(f"错误 {error_code}: {error_msg}"))
            handler()
            return None

    return response

# 使用示例

try:
    response = auth.post_request('/v2/auth/w/order/submit', {
        'type': 'EXCHANGE LIMIT',
        'symbol': 'tBTCUSD',
        'amount': '0.001',
        'price': '40000'
    })

    result = handle_api_error(response)
    if result:
        print(f"订单提交成功: {result}")

except Exception as e:
    print(f"请求异常: {e}")

```bash

## 完整交易示例

### 完整的交易流程

```python
import requests
import hmac
import hashlib
import json
import time

class BitfinexTrader:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "<https://api.bitfinex.com">
        self.public_url = "<https://api-pub.bitfinex.com">

    def _generate_signature(self, path, nonce, body=""):
        """生成请求签名"""
        signature_payload = f'/api{path}{nonce}{body}'
        signature = hmac.new(
            self.api_secret.encode(),
            signature_payload.encode(),
            hashlib.sha384
        ).hexdigest()
        return signature

    def _get_headers(self, path, body=""):
        """生成请求头"""
        nonce = str(int(time.time()*1000000))
        signature = self._generate_signature(path, nonce, body)

        return {
            'bfx-nonce': nonce,
            'bfx-apikey': self.api_key,
            'bfx-signature': signature,
            'content-type': 'application/json'
        }

    def post_request(self, path, params=None):
        """发送认证 POST 请求"""
        body = json.dumps(params) if params else ""
        headers = self._get_headers(path, body)
        url = f"{self.base_url}{path}"

        response = requests.post(url, headers=headers, data=body)
        return response.json()

    def get_public(self, path, params=None):
        """发送公共 GET 请求"""
        url = f"{self.public_url}{path}"
        response = requests.get(url, params=params)
        return response.json()

# 市场数据方法
    def get_ticker(self, symbol):
        """获取 ticker"""
        return self.get_public(f'/v2/ticker/{symbol}')

    def get_order_book(self, symbol, precision='P0'):
        """获取订单簿"""
        return self.get_public(f'/v2/book/{symbol}/{precision}')

# 账户方法
    def get_wallets(self):
        """获取钱包余额"""
        return self.post_request('/v2/auth/r/wallets')

    def get_active_orders(self, symbol=None):
        """获取活跃订单"""
        path = f'/v2/auth/r/orders/{symbol}' if symbol else '/v2/auth/r/orders'
        return self.post_request(path)

# 交易方法
    def submit_order(self, order_type, symbol, amount, price=None):
        """提交订单"""
        params = {
            'type': order_type,
            'symbol': symbol,
            'amount': str(amount)
        }
        if price:
            params['price'] = str(price)

        return self.post_request('/v2/auth/w/order/submit', params)

    def cancel_order(self, order_id):
        """取消订单"""
        params = {'id': order_id}
        return self.post_request('/v2/auth/w/order/cancel', params)

    def cancel_all_orders(self):
        """取消所有订单"""
        params = {'all': 1}
        return self.post_request('/v2/auth/w/order/cancel/multi', params)

# 使用示例

def main():

# 初始化交易器
    trader = BitfinexTrader('your_api_key', 'your_api_secret')

# 1. 获取市场数据
    print("=== 市场数据 ===")
    ticker = trader.get_ticker('tBTCUSD')
    bid, bid_size, ask, ask_size, change, change_perc, last, volume, high, low = ticker
    print(f"BTC/USD: 最新价={last}, 买一={bid}, 卖一={ask}")

# 2. 查询账户余额
    print("\n=== 账户余额 ===")
    wallets = trader.get_wallets()
    for wallet_type, currency, balance, unsettled, available in wallets:
        if balance != 0:
            print(f"{wallet_type} - {currency}: {balance} (可用: {available})")

# 3. 提交限价买单
    print("\n=== 提交订单 ===")
    buy_price = bid* 0.99  # 在买一价下方 1%挂单
    order_result = trader.submit_order(
        order_type='EXCHANGE LIMIT',
        symbol='tBTCUSD',
        amount='0.001',
        price=buy_price
    )
    print(f"买单已提交: {order_result}")

# 4. 查询活跃订单
    print("\n=== 活跃订单 ===")
    orders = trader.get_active_orders('tBTCUSD')
    for order in orders:
        order_id = order[0]
        amount = order[6]
        price = order[16]
        status = order[13]
        print(f"订单 ID={order_id}, 数量={amount}, 价格={price}, 状态={status}")

# 5. 取消所有订单
    print("\n=== 取消订单 ===")
    cancel_result = trader.cancel_all_orders()
    print(f"取消结果: {cancel_result}")

if __name__ == '__main__':
    main()

```bash

## 最佳实践

### 1. 安全性

```python

# 使用环境变量存储 API 密钥

import os

api_key = os.getenv('BITFINEX_API_KEY')
api_secret = os.getenv('BITFINEX_API_SECRET')

# 不要在代码中硬编码密钥

# 不要将密钥提交到版本控制系统

```bash

### 2. 错误重试

```python
import time
from functools import wraps

def retry_on_error(max_retries=3, delay=1):
    """错误重试装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    print(f"请求失败，{delay}秒后重试... (尝试 {attempt + 1}/{max_retries})")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

@retry_on_error(max_retries=3, delay=2)
def get_ticker_with_retry(symbol):
    url = f"<https://api-pub.bitfinex.com/v2/ticker/{symbol}">
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()

```bash

### 3. 日志记录

```python
import logging

# 配置日志

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bitfinex_trading.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger('BitfinexTrader')

# 在交易方法中使用日志

def submit_order_with_logging(trader, order_type, symbol, amount, price):
    logger.info(f"提交订单: {order_type} {symbol} 数量={amount} 价格={price}")
    try:
        result = trader.submit_order(order_type, symbol, amount, price)
        logger.info(f"订单提交成功: {result}")
        return result
    except Exception as e:
        logger.error(f"订单提交失败: {e}")
        raise

```bash

## 参考资源

- **官方文档**: <https://docs.bitfinex.com>
- **API 状态页**: <https://status.bitfinex.com>
- **Python SDK**: <https://github.com/bitfinexcom/bitfinex-api-py>
- **WebSocket 文档**: <https://docs.bitfinex.com/docs/ws-general>
- **费率说明**: <https://www.bitfinex.com/fees>

- --

- 最后更新: 2026-02-27*
