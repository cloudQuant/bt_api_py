# dYdX API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V4 (dYdX Chain)
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://docs.dydx.xyz/>

## 交易所基本信息

- 官方名称: dYdX
- 官网: <https://dydx.xyz>
- 交易所类型: DEX (去中心化交易所)
- 底层区块链: dYdX Chain (基于 Cosmos SDK 的独立应用链)
- 支持的交易对: 100+ 永续合约
- 支持的交易类型: 永续合约(Perpetuals)
- 手续费: Maker -0.011%, Taker 0.05% (阶梯费率)
- 特点: 完全去中心化的订单簿，链上撮合，无 KYC

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| Indexer REST (主网) | `<https://indexer.dydx.trade/v4`> | 主网数据查询 |

| Indexer REST (测试网) | `<https://indexer.v4testnet.dydx.exchange/v4`> | 测试网 |

| WebSocket (主网) | `wss://indexer.dydx.trade/v4/ws` | 实时数据 |

| WebSocket (测试网) | `wss://indexer.v4testnet.dydx.exchange/v4/ws` | 测试网 |

| Node gRPC | 各验证者节点 | 链上交易提交 |

## 认证方式

dYdX V4 是完全去中心化的。交易通过钱包私钥签名直接提交到链上，不需要传统的 API Key 认证。

### 身份验证

- **读取数据**: Indexer API 公开免费，无需认证
- **提交交易**: 需要 dYdX 链钱包私钥签名
- **Python SDK**: 推荐使用 `dydx-v4-client` 或 `v4-client-py`

### Python 客户端示例

```python

# pip install v4-client-py

from v4_client_py import CompositeClient, NodeClient
from v4_client_py.chain.aerial.wallet import LocalWallet
from v4_client_py.clients.constants import Network

# 创建钱包

mnemonic = "your mnemonic phrase ..."
wallet = LocalWallet.from_mnemonic(mnemonic, "dydx")

# 创建客户端

client = CompositeClient(
    Network.mainnet(),
)

```bash

## 市场数据 API (Indexer)

> 所有 Indexer API 公开免费，无需认证。

### 1. 获取永续市场列表

- *端点**: `GET /v4/perpetualMarkets`

```python
import requests

BASE_URL = "<https://indexer.dydx.trade/v4">

resp = requests.get(f"{BASE_URL}/perpetualMarkets")
data = resp.json()
for ticker, info in list(data["markets"].items())[:5]:
    print(f"{ticker}: price={info['oraclePrice']}, "
          f"volume24h={info['volume24H']}, trades24h={info['trades24H']}")

```bash

### 2. 获取订单簿

- *端点**: `GET /v4/orderbooks/perpetualMarket/{ticker}`

```python
resp = requests.get(f"{BASE_URL}/orderbooks/perpetualMarket/BTC-USD")
book = resp.json()
for ask in book["asks"][:5]:
    print(f"ASK: price={ask['price']}, size={ask['size']}")
for bid in book["bids"][:5]:
    print(f"BID: price={bid['price']}, size={bid['size']}")

```bash

### 3. 获取最近成交

- *端点**: `GET /v4/trades/perpetualMarket/{ticker}`

- *参数**: `limit` (可选, 默认 100)

```python
resp = requests.get(f"{BASE_URL}/trades/perpetualMarket/BTC-USD", params={"limit": 10})
for trade in resp.json()["trades"]:
    print(f"Price={trade['price']}, Size={trade['size']}, Side={trade['side']}, "
          f"Time={trade['createdAt']}")

```bash

### 4. 获取 K 线数据

- *端点**: `GET /v4/candles/perpetualMarkets/{ticker}`

- *参数**: `resolution` (1MIN/5MINS/15MINS/30MINS/1HOUR/4HOURS/1DAY), `limit`

```python
resp = requests.get(f"{BASE_URL}/candles/perpetualMarkets/BTC-USD", params={
    "resolution": "1HOUR",
    "limit": 24
})
for c in resp.json()["candles"]:
    print(f"T={c['startedAt']} O={c['open']} H={c['high']} L={c['low']} "
          f"C={c['close']} V={c['baseTokenVolume']}")

```bash

### 5. 获取历史资金费率

- *端点**: `GET /v4/historicalFunding/{ticker}`

```python
resp = requests.get(f"{BASE_URL}/historicalFunding/BTC-USD", params={"limit": 10})
for f in resp.json()["historicalFunding"]:
    print(f"Rate={f['rate']}, Price={f['price']}, Time={f['effectiveAt']}")

```bash

### 6. 获取迷你 K 线 (Sparklines)

- *端点**: `GET /v4/sparklines`

- *参数**: `timePeriod` (ONE_DAY/SEVEN_DAYS)

```python
resp = requests.get(f"{BASE_URL}/sparklines", params={"timePeriod": "ONE_DAY"})

```bash

## 账户数据 API

### 1. 查询子账户信息

- *端点**: `GET /v4/addresses/{address}/subaccountNumber/{subaccountNumber}`

```python
address = "dydx1..."
resp = requests.get(f"{BASE_URL}/addresses/{address}/subaccountNumber/0")
data = resp.json()
sub = data["subaccount"]
print(f"Equity: {sub['equity']}, Free collateral: {sub['freeCollateral']}")
for pos in sub.get("openPerpetualPositions", {}).values():
    print(f"  {pos['market']}: size={pos['size']}, entry={pos['entryPrice']}, "
          f"unrealizedPnl={pos['unrealizedPnl']}")

```bash

### 2. 查询订单

- *端点**: `GET /v4/orders`

- *参数**: `address`, `subaccountNumber`, `limit`, `status`, `side`, `type`, `ticker`

```python
resp = requests.get(f"{BASE_URL}/orders", params={
    "address": "dydx1...",
    "subaccountNumber": 0,
    "status": "OPEN",
    "limit": 50
})
for o in resp.json():
    print(f"ID:{o['id']} {o['side']} {o['ticker']} price={o['price']} "
          f"size={o['size']} status={o['status']}")

```bash

### 3. 查询成交记录

- *端点**: `GET /v4/fills`

- *参数**: `address`, `subaccountNumber`, `market`, `limit`

### 4. 查询资金费用

- *端点**: `GET /v4/fundingPayments`

### 5. 查询转账记录

- *端点**: `GET /v4/transfers`

### 6. 查询历史 PnL

- *端点**: `GET /v4/historical-pnl`

## 交易 (链上操作)

> 交易通过 dYdX Chain 提交，需要钱包签名。推荐使用官方 Python SDK。

### 使用 Python SDK 下单

```python
from v4_client_py import CompositeClient
from v4_client_py.chain.aerial.wallet import LocalWallet
from v4_client_py.clients.constants import Network
from v4_client_py.clients.helpers.chain_helpers import OrderSide, OrderType, OrderTimeInForce

# 初始化

wallet = LocalWallet.from_mnemonic("your mnemonic...", "dydx")
client = CompositeClient(Network.mainnet())

# 限价买单

order = client.place_order(
    wallet,
    subaccount_number=0,
    market="BTC-USD",
    side=OrderSide.BUY,
    type=OrderType.LIMIT,
    price=40000,
    size=0.01,
    time_in_force=OrderTimeInForce.GTT,
    good_til_block=0,
    good_til_time_in_seconds=600,  # 10 分钟有效

)
print(f"Order tx: {order}")

# 市价单

order = client.place_order(
    wallet,
    subaccount_number=0,
    market="ETH-USD",
    side=OrderSide.SELL,
    type=OrderType.MARKET,
    price=0,  # 市价单
    size=1.0,
    time_in_force=OrderTimeInForce.IOC,
)

# 撤单

cancel = client.cancel_order(
    wallet,
    subaccount_number=0,
    order_id="order_id_here",
    good_til_block=0,
    good_til_time_in_seconds=600,
)

```bash

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| Indexer GET | 100 次/10 秒 | 按 IP |

| Indexer Batch | 按端点不同 | 参考官方文档 |

| 链上交易 | 受区块大小限制 | 每区块有限订单数 |

## WebSocket 支持

### 连接信息

- *URL**: `wss://indexer.dydx.trade/v4/ws`

### 支持频道

| 频道 | 描述 |

|------|------|

| `v4_orderbook` | 订单簿增量 |

| `v4_trades` | 实时成交 |

| `v4_markets` | 市场状态 |

| `v4_candles` | K 线数据 |

| `v4_subaccounts` | 子账户更新（需地址） |

| `v4_parent_subaccounts` | 父子账户更新 |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    msg_type = data.get("type", "")
    channel = data.get("channel", "")

    if msg_type == "subscribed":
        print(f"Subscribed to {channel}")
    elif msg_type == "channel_data":
        contents = data.get("contents", {})
        if channel == "v4_trades":
            for trade in contents.get("trades", []):
                print(f"Trade: price={trade['price']}, size={trade['size']}, side={trade['side']}")
        elif channel == "v4_orderbook":
            asks = contents.get("asks", [])
            bids = contents.get("bids", [])
            if asks:
                print(f"Book asks update: {len(asks)} levels")
            if bids:
                print(f"Book bids update: {len(bids)} levels")
    elif msg_type == "channel_batch_data":
        for item in data.get("contents", []):
            print(f"Batch: {item}")

def on_open(ws):

# 订阅成交
    ws.send(json.dumps({
        "type": "subscribe",
        "channel": "v4_trades",
        "id": "BTC-USD"
    }))

# 订阅订单簿
    ws.send(json.dumps({
        "type": "subscribe",
        "channel": "v4_orderbook",
        "id": "BTC-USD"
    }))

# 订阅子账户（需地址）
    ws.send(json.dumps({
        "type": "subscribe",
        "channel": "v4_subaccounts",
        "id": "dydx1.../0"
    }))

ws = websocket.WebSocketApp(
    "wss://indexer.dydx.trade/v4/ws",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)

```bash

## 错误处理

### HTTP 错误码

| 状态码 | 描述 |

|--------|------|

| 200 | 成功 |

| 400 | 请求参数错误 |

| 404 | 资源不存在 |

| 429 | 速率限制 |

| 500 | 服务器错误 |

### Python 错误处理

```python
def safe_dydx_get(path, params=None):
    try:
        resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            print("Rate limited, waiting...")
            import time; time.sleep(2)
            return safe_dydx_get(path, params)
        print(f"HTTP Error: {e}")
        return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细 V4 Indexer REST API 端点说明
- 添加市场数据 API（市场列表、订单簿、成交、K 线、资金费率）
- 添加账户数据查询示例
- 添加 Python SDK 交易（下单/撤单）示例
- 添加 WebSocket 频道订阅示例
- 说明去中心化认证机制（钱包签名）

- --

## 相关资源

- [dYdX 官方文档](<https://docs.dydx.xyz/)>
- [dYdX Python SDK](<https://github.com/dydxprotocol/v4-clients)>
- [dYdX 官网](<https://dydx.xyz)>
- [dYdX Chain 浏览器](<https://www.mintscan.io/dydx)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 dYdX V4 官方文档整理。*
