# BitMart API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V2/V3
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://developer.bitmart.com>
- Python SDK: <https://github.com/bitmartexchange/bitmart-python-sdk-api>

## 交易所基本信息

- 官方名称: BitMart
- 官网: <https://www.bitmart.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 开曼群岛
- 支持的交易对: 1000+ (USDT, BTC, ETH 计价)
- 支持的交易类型: 现货(Spot)、永续合约(Futures)
- 手续费: Maker 0.25%, Taker 0.25% (现货基础费率)
- Python SDK: `pip install bitmart-python-sdk-api`

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API (Spot) | `<https://api-cloud.bitmart.com`> | 现货主端点 |

| REST API (Futures) | `<https://api-cloud-v2.bitmart.com`> | 合约主端点 |

| WebSocket (Spot Public) | `wss://ws-manager-compress.bitmart.com/api?protocol=1.1` | 现货公共频道 |

| WebSocket (Spot Private) | `wss://ws-manager-compress.bitmart.com/user?protocol=1.1` | 现货私有频道 |

| WebSocket (Futures Public) | `wss://openapi-ws-v2.bitmart.com/api?protocol=1.1` | 合约公共频道 |

| WebSocket (Futures Private) | `wss://openapi-ws-v2.bitmart.com/user?protocol=1.1` | 合约私有频道 |

## 认证方式

### API 密钥获取

1. 注册 BitMart 账户并完成 KYC
2. 进入 API 管理页面
3. 创建 API Key，获取 **API Key**、**Secret Key**和**Memo**
4. 设置权限和 IP 白名单

### HMAC SHA256 签名

- *签名步骤**:
1. 构建签名字符串: `timestamp + "#" + memo + "#" + queryString`（GET）或 `timestamp + "#" + memo + "#" + requestBody`（POST）
2. 使用 Secret Key 进行 HMAC SHA256 签名
3. 将签名转为十六进制字符串

- *请求头**:

| Header | 描述 |

|--------|------|

| X-BM-KEY | API Key |

| X-BM-TIMESTAMP | 毫秒级时间戳 |

| X-BM-SIGN | HMAC SHA256 签名 |

| Content-Type | application/json |

### Python 签名示例

```python
import hmac
import hashlib
import time
import json
import requests

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
MEMO = "your_memo"
BASE_URL = "<https://api-cloud.bitmart.com">

def generate_signature(timestamp, memo, body_str, secret_key):
    """生成 HMAC SHA256 签名"""
    message = f"{timestamp}#{memo}#{body_str}"
    signature = hmac.new(
        secret_key.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

def signed_post(path, params=None):
    """发送签名 POST 请求"""
    timestamp = str(int(time.time() *1000))
    body_str = json.dumps(params) if params else ""
    signature = generate_signature(timestamp, MEMO, body_str, SECRET_KEY)

    headers = {
        "X-BM-KEY": API_KEY,
        "X-BM-TIMESTAMP": timestamp,
        "X-BM-SIGN": signature,
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}{path}"
    resp = requests.post(url, headers=headers, data=body_str)
    return resp.json()

def signed_get(path, params=None):
    """发送签名 GET 请求"""
    timestamp = str(int(time.time()*1000))
    query_str = "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else ""
    signature = generate_signature(timestamp, MEMO, query_str, SECRET_KEY)

    headers = {
        "X-BM-KEY": API_KEY,
        "X-BM-TIMESTAMP": timestamp,
        "X-BM-SIGN": signature,
    }
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=headers, params=params)
    return resp.json()

```bash

## 市场数据 API

> 公共 API 无需认证。

### 使用官方 SDK

```python
from bitmart.api_spot import APISpot

spotAPI = APISpot(timeout=(2, 10))

```bash

### 1. 获取所有币种

- *端点**: `GET /spot/v1/currencies`

```python
response = spotAPI.get_currencies()
print(response[0])  # 响应数据

```bash

### 2. 获取单个 Ticker

- *端点**: `GET /spot/quotation/v3/ticker`

- *参数**: `symbol` (必需, 如 `BTC_USDT`)

```python
response = spotAPI.get_v3_ticker(symbol='BTC_USDT')
data = response[0]
print(data)

# 或直接使用 REST

resp = requests.get(f"{BASE_URL}/spot/quotation/v3/ticker", params={"symbol": "BTC_USDT"})
print(resp.json())

```bash

### 3. 获取所有 Ticker

- *端点**: `GET /spot/quotation/v3/tickers`

```python
response = spotAPI.get_v3_tickers()
print(f"Tickers count: {len(response[0].get('data', []))}")

```bash

### 4. 获取订单簿

- *端点**: `GET /spot/quotation/v3/books`

- *参数**: `symbol` (必需), `limit` (可选, 默认 35, 最大 50)

```python
response = spotAPI.get_v3_depth(symbol='BTC_USDT')
data = response[0]
print(data)

```bash

### 5. 获取最近成交

- *端点**: `GET /spot/quotation/v3/trades`

- *参数**: `symbol` (必需), `limit` (可选, 默认 50, 最大 50)

```python
response = spotAPI.get_v3_trades(symbol='BTC_USDT', limit=10)
print(response[0])

```bash

### 6. 获取 K 线数据

- *端点**: `GET /spot/quotation/v3/klines`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对 |

| before | LONG | 否 | 开始时间（秒） |

| after | LONG | 否 | 结束时间（秒） |

| step | INT | 否 | K 线周期(分钟): 1,3,5,15,30,45,60,120,180,240,1440,10080,43200 |

| limit | INT | 否 | 数量限制 |

```python
response = spotAPI.get_v3_klines(symbol='BTC_USDT', step=60, limit=100)
print(response[0])

```bash

## 交易 API

> 以下端点需要 API Key + Secret + Memo 认证。

### 使用官方 SDK

```python
from bitmart.api_spot import APISpot

spotAPI = APISpot(
    api_key="your_api_key",
    secret_key="your_secret_key",
    memo="your_memo",
    timeout=(3, 10)
)

```bash

### 1. 查询现货余额

- *端点**: `GET /spot/v1/wallet`

```python
response = spotAPI.get_wallet()
if response[0]['code'] == 1000:
    for asset in response[0]['data']['wallet']:
        if float(asset['available']) > 0:
            print(f"{asset['id']}: available={asset['available']}, frozen={asset['frozen']}")

```bash

### 2. 下单

- *端点**: `POST /spot/v2/submit_order`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对，如 BTC_USDT |

| side | STRING | 是 | buy / sell |

| type | STRING | 是 | limit / market / limit_maker / ioc |

| size | STRING | 条件 | 数量（limit 必需；market sell 必需） |

| price | STRING | 条件 | 价格（limit 必需） |

| notional | STRING | 条件 | 金额（market buy 时必需） |

| client_order_id | STRING | 否 | 客户端订单 ID |

```python

# 限价买单

response = spotAPI.post_submit_order(
    symbol='BTC_USDT',
    side='buy',
    type='limit',
    size='0.001',
    price='40000'
)
if response[0]['code'] == 1000:
    print(f"Order ID: {response[0]['data']['order_id']}")

# 市价买单（按金额）

response = spotAPI.post_submit_order(
    symbol='BTC_USDT',
    side='buy',
    type='market',
    notional='100'  # 100 USDT

)

# 限价卖单

response = spotAPI.post_submit_order(
    symbol='BTC_USDT',
    side='sell',
    type='limit',
    size='0.001',
    price='50000'
)

```bash

### 3. 撤单

- *端点**: `POST /spot/v3/cancel_order`

```python
response = spotAPI.post_cancel_order(
    symbol='BTC_USDT',
    order_id='12345678'
)
print(f"Cancel result: {response[0]}")

# 按客户端订单 ID 撤单

response = spotAPI.post_cancel_order(
    symbol='BTC_USDT',
    client_order_id='my_order_001'
)

```bash

### 4. 批量撤单

- *端点**: `POST /spot/v4/cancel_all`

```python

# 撤销某交易对的所有订单

response = spotAPI.post_cancel_all_order(symbol='BTC_USDT')

```bash

### 5. 查询订单详情

- *端点**: `POST /spot/v4/query/order`

```python
response = spotAPI.post_order_detail(
    symbol='BTC_USDT',
    order_id='12345678'
)
if response[0]['code'] == 1000:
    order = response[0]['data']
    print(f"Status: {order['state']}, Filled: {order['filled_size']}/{order['size']}")

```bash

### 6. 查询当前挂单

- *端点**: `POST /spot/v4/query/open-orders`

```python
response = spotAPI.post_open_orders(
    symbol='BTC_USDT',
    limit=50
)

```bash

### 7. 查询历史订单

- *端点**: `POST /spot/v4/query/history-orders`

## 合约交易 API

### 使用官方 SDK

```python
from bitmart.api_contract import APIContract

contractAPI = APIContract(
    api_key="your_api_key",
    secret_key="your_secret_key",
    memo="your_memo",
    timeout=(3, 10)
)

```bash

### 1. 查询合约详情

```python
response = contractAPI.get_details(contract_symbol='BTCUSDT')
print(response[0])

```bash

### 2. 查询合约深度

```python
response = contractAPI.get_depth(contract_symbol='BTCUSDT')

```bash

### 3. 查询资金费率

```python
response = contractAPI.get_funding_rate(contract_symbol='BTCUSDT')

```bash

### 4. 合约 K 线

```python
import time
end_time = int(time.time())
start_time = end_time - 3600

response = contractAPI.get_kline(
    contract_symbol='BTCUSDT',
    step=5,
    start_time=start_time,
    end_time=end_time
)

```bash

### 5. 合约下单

```python
response = contractAPI.post_submit_order(
    contract_symbol='BTCUSDT',
    client_order_id="BM1234",
    side=4,          # 1=买入开多, 2=买入平空, 3=卖出平多, 4=卖出开空
    mode=1,          # 1=下单, 2=只减仓
    type='limit',    # limit / market
    leverage='10',
    open_type='isolated',  # isolated / cross
    size=10,
    price='40000'
)
if response[0]['code'] == 1000:
    print(f"Futures order: {response[0]}")

```bash

## 账户管理 API

### 1. 充值地址

- *端点**: `GET /account/v1/deposit/address`

### 2. 提现

- *端点**: `POST /account/v1/withdraw/apply`

### 3. 充值历史

- *端点**: `GET /account/v2/deposit-withdraw/history`

### 4. 资产划转

在现货和合约账户间划转资产。

## 速率限制

BitMart 通过响应头提供速率限制信息：

| 响应头 | 描述 |

|--------|------|

| x-bm-ratelimit-remaining | 当前窗口已用次数 |

| x-bm-ratelimit-limit | 当前窗口最大次数 |

| x-bm-ratelimit-reset | 窗口时间（秒） |

| x-bm-ratelimit-mode | 限制模式（IP/UID） |

### 具体限制

| 类别 | 限制 | 说明 |

|------|------|------|

| 公共端点 | 600 次/分钟 | 按 IP |

| 下单/撤单 | 200,000 次/分钟 | 下单+撤单合计 |

| 查询 | 不同端点不同 | 参考响应头 |

### Python 速率限制监控

```python
from bitmart.api_spot import APISpot

spotAPI = APISpot()
response = spotAPI.get_currencies()
data = response[0]
limit_info = response[1]  # 速率限制信息

print(f"Remaining: {limit_info['Remaining']}")
print(f"Limit: {limit_info['Limit']}")
print(f"Reset: {limit_info['Reset']}s")
print(f"Mode: {limit_info['Mode']}")

```bash

## WebSocket 支持

### 现货公共频道

- *支持的频道**:
- `spot/ticker:{symbol}` - Ticker
- `spot/kline1m:{symbol}` - K 线
- `spot/depth5:{symbol}` - 深度（5 档）
- `spot/depth20:{symbol}` - 深度（20 档）
- `spot/depth50:{symbol}` - 深度（50 档）
- `spot/trade:{symbol}` - 成交

### 现货私有频道

- `spot/user/order:{symbol}` - 订单更新
- `spot/user/balance:BALANCE_UPDATE` - 余额更新

### Python WebSocket 示例 (SDK)

```python
import logging
import time
from bitmart.lib.cloud_consts import SPOT_PUBLIC_WS_URL
from bitmart.lib.cloud_utils import config_logging
from bitmart.websocket.spot_socket_client import SpotSocketClient

def message_handler(message):
    logging.info(f"Received: {message}")

config_logging(logging, logging.INFO)

# 公共频道

client = SpotSocketClient(
    stream_url=SPOT_PUBLIC_WS_URL,
    on_message=message_handler
)

# 订阅 Ticker

client.subscribe(args="spot/ticker:BTC_USDT")

# 订阅多个频道

client.subscribe(args=["spot/ticker:BTC_USDT", "spot/trade:ETH_USDT"])

# 取消订阅

time.sleep(10)
client.unsubscribe(args="spot/ticker:BTC_USDT")

```bash

### 私有频道 (需认证)

```python
from bitmart.lib.cloud_consts import SPOT_PRIVATE_WS_URL
from bitmart.websocket.spot_socket_client import SpotSocketClient

client = SpotSocketClient(
    stream_url=SPOT_PRIVATE_WS_URL,
    on_message=message_handler,
    api_key="your_api_key",
    api_secret_key="your_secret_key",
    api_memo="your_memo"
)
client.login()
client.subscribe(args="spot/user/balance:BALANCE_UPDATE")

```bash

### 合约 WebSocket

```python
from bitmart.lib.cloud_consts import FUTURES_PUBLIC_WS_URL
from bitmart.websocket.futures_socket_client import FuturesSocketClient

client = FuturesSocketClient(
    stream_url=FUTURES_PUBLIC_WS_URL,
    on_message=message_handler
)
client.subscribe(args="futures/ticker:BTCUSDT")

```bash

## 错误代码

### 响应格式

- *成功**:

```json
{
  "code": 1000,
  "message": "OK",
  "data": { ... }
}

```bash

### 常见错误码

| 错误码 | 描述 |

|--------|------|

| 1000 | 成功 |

| 40001 | 参数无效 |

| 40002 | 参数缺失 |

| 40004 | API Key 无效 |

| 40005 | 签名无效 |

| 40006 | 时间戳过期 |

| 40007 | 权限不足 |

| 40008 | IP 不在白名单 |

| 50000 | 内部错误 |

| 50001 | 系统繁忙 |

| 51000 | 余额不足 |

| 51003 | 订单不存在 |

| 51004 | 订单已撤销 |

| 53000 | 交易对不存在 |

| 53001 | 交易对未开放 |

### Python 错误处理示例

```python
from bitmart.api_spot import APISpot
from bitmart.lib import cloud_exceptions

spotAPI = APISpot(api_key=API_KEY, secret_key=SECRET_KEY, memo=MEMO, timeout=(3, 10))

try:
    response = spotAPI.post_submit_order(
        symbol='BTC_USDT', side='buy', type='limit',
        size='0.001', price='40000'
    )
    if response[0]['code'] == 1000:
        print(f"Order placed: {response[0]['data']}")
    else:
        print(f"API Error: {response[0]['message']}")
except cloud_exceptions.APIException as e:
    print(f"HTTP Error: {e.response}")
except Exception as e:
    print(f"Exception: {e}")

```bash

## 代码示例

### Python 完整交易示例

```python
from bitmart.api_spot import APISpot
from bitmart.api_contract import APIContract
from bitmart.lib import cloud_exceptions

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
MEMO = "your_memo"

# ===== 现货公共接口 =====

public = APISpot(timeout=(2, 10))

# 获取所有币种

currencies = public.get_currencies()
print(f"Currencies: {len(currencies[0].get('data', {}).get('currencies', []))}")

# 获取 Ticker

ticker = public.get_v3_ticker(symbol='BTC_USDT')
print(f"BTC/USDT ticker: {ticker[0]}")

# 获取订单簿

depth = public.get_v3_depth(symbol='BTC_USDT')
print(f"Depth: {depth[0]}")

# 获取最近成交

trades = public.get_v3_trades(symbol='BTC_USDT', limit=5)
print(f"Recent trades: {trades[0]}")

# ===== 现货私有接口 =====

spot = APISpot(api_key=API_KEY, secret_key=SECRET_KEY, memo=MEMO, timeout=(3, 10))

try:

# 查询余额
    wallet = spot.get_wallet()
    if wallet[0]['code'] == 1000:
        for asset in wallet[0]['data']['wallet']:
            if float(asset.get('available', 0)) > 0:
                print(f"{asset['id']}: {asset['available']}")

# 限价买单
    order = spot.post_submit_order(
        symbol='BTC_USDT', side='buy', type='limit',
        size='0.001', price='40000'
    )
    if order[0]['code'] == 1000:
        order_id = order[0]['data']['order_id']
        print(f"Order placed: {order_id}")

# 查询订单
        detail = spot.post_order_detail(symbol='BTC_USDT', order_id=order_id)
        print(f"Order detail: {detail[0]}")

# 撤单
        cancel = spot.post_cancel_order(symbol='BTC_USDT', order_id=order_id)
        print(f"Cancel: {cancel[0]}")

except cloud_exceptions.APIException as e:
    print(f"API Error: {e.response}")

# ===== 合约接口 =====

futures = APIContract(api_key=API_KEY, secret_key=SECRET_KEY, memo=MEMO, timeout=(3, 10))

# 查询合约详情

details = APIContract(timeout=(2, 10)).get_details(contract_symbol='BTCUSDT')
print(f"BTCUSDT details: {details[0]}")

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细 REST API 端点说明
- 添加 HMAC SHA256 签名认证完整 Python 示例
- 添加官方 SDK (bitmart-python-sdk-api) 使用示例
- 添加现货和合约市场数据 API 详细说明
- 添加现货和合约交易 API 完整示例
- 添加 WebSocket (公共/私有频道) 订阅示例
- 添加速率限制监控和错误处理

- --

## 相关资源

- [BitMart API 文档](<https://developer.bitmart.com)>
- [BitMart Python SDK](<https://github.com/bitmartexchange/bitmart-python-sdk-api)>
- [BitMart 官网](<https://www.bitmart.com)>
- [API 指南](<https://www.bitmart.com/open-api-guide/en-US)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 BitMart 官方 API 文档和 Python SDK 整理。*
