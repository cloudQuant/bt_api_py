# Hyperliquid API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api>
- Python SDK: <https://github.com/hyperliquid-dex/hyperliquid-python-sdk>

## 交易所基本信息

- 官方名称: Hyperliquid
- 官网: <https://hyperliquid.xyz>
- 交易所类型: DEX (去中心化交易所)
- 底层区块链: Hyperliquid L1 (自有链)
- 支持的市场: 永续合约(Perps) 150+, 现货(Spot)
- 手续费: Maker 0.01%, Taker 0.035% (基础费率，可降)
- 特点: 链上订单簿 DEX，亚毫秒级延迟，无 Gas 费交易，HIP-1/HIP-2 代币标准

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST (主网) | `<https://api.hyperliquid.xyz`> | 主端点 |

| REST (测试网) | `<https://api.hyperliquid-testnet.xyz`> | 测试网 |

| WebSocket (主网) | `wss://api.hyperliquid.xyz/ws` | 实时数据 |

| WebSocket (测试网) | `wss://api.hyperliquid-testnet.xyz/ws` | 测试网 |

## 认证方式

### API 架构

Hyperliquid 只有两个主要 REST 端点:

- `POST /info` — 查询数据（无需签名）
- `POST /exchange` — 交易操作（需 EIP-712 签名）

### 交易签名

交易通过 EVM 钱包私钥使用 EIP-712 类型签名。推荐使用官方 Python SDK。

### Python SDK 安装

```bash
pip install hyperliquid-python-sdk

```bash

### Python SDK 示例

```python
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
import eth_account

# 信息查询（无需私钥）

info = Info(constants.MAINNET_API_URL, skip_ws=True)

# 交易操作（需要私钥）

secret_key = "0x..."  # EVM 私钥

account = eth_account.Account.from_key(secret_key)
address = account.address
exchange = Exchange(account, constants.MAINNET_API_URL)

```bash

## 市场数据 API (POST /info)

> 所有 `/info` 请求无需签名，使用 POST + JSON body，`type` 字段区分请求类型。

### 1. 获取所有中间价

- *请求**: `{"type": "allMids"}`

```python
import requests

BASE_URL = "<https://api.hyperliquid.xyz">

resp = requests.post(f"{BASE_URL}/info", json={"type": "allMids"})
mids = resp.json()
for coin, price in list(mids.items())[:10]:
    print(f"{coin}: mid={price}")

# SDK

all_mids = info.all_mids()

```bash

### 2. 获取市场元数据

- *请求**: `{"type": "meta"}`

```python
resp = requests.post(f"{BASE_URL}/info", json={"type": "meta"})
meta = resp.json()
for asset in meta["universe"][:5]:
    print(f"{asset['name']}: szDecimals={asset['szDecimals']}, maxLeverage={asset['maxLeverage']}")

# SDK

meta = info.meta()

```bash

### 3. 获取现货元数据

- *请求**: `{"type": "spotMeta"}`

```python
resp = requests.post(f"{BASE_URL}/info", json={"type": "spotMeta"})
spot_meta = resp.json()
for token in spot_meta["universe"][:5]:
    print(f"{token['name']}: tokens={token['tokens']}")

```bash

### 4. 获取 L2 订单簿

- *请求**: `{"type": "l2Book", "coin": "BTC"}`

```python
resp = requests.post(f"{BASE_URL}/info", json={"type": "l2Book", "coin": "BTC"})
book = resp.json()
for level in book["levels"][0][:5]:  # asks
    print(f"ASK: px={level['px']}, sz={level['sz']}, n={level['n']}")
for level in book["levels"][1][:5]:  # bids
    print(f"BID: px={level['px']}, sz={level['sz']}, n={level['n']}")

# SDK

book = info.l2_snapshot("BTC")

```bash

### 5. 获取 K 线数据

- *请求**: `{"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "1h", "startTime": ..., "endTime": ...}}`

- *支持周期**: `1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 12h, 1d, 3d, 1w, 1M`

> 最多返回最近 5000 根 K 线

```python
import time

end = int(time.time() *1000)
start = end - 24*3600*1000  # 24h 前

resp = requests.post(f"{BASE_URL}/info", json={
    "type": "candleSnapshot",
    "req": {"coin": "BTC", "interval": "1h", "startTime": start, "endTime": end}
})
for c in resp.json():
    print(f"T={c['t']} O={c['o']} H={c['h']} L={c['l']} C={c['c']} V={c['v']}")

# SDK

candles = info.candles_snapshot("BTC", "1h", start, end)

```bash

### 6. 获取最近成交

- *请求**: `{"type": "recentTrades", "coin": "BTC"}`

```python
resp = requests.post(f"{BASE_URL}/info", json={"type": "recentTrades", "coin": "BTC"})
for t in resp.json()[:10]:
    print(f"Price={t['px']}, Size={t['sz']}, Side={t['side']}, Time={t['time']}")

```bash

### 7. 获取交易所状态

- *请求**: `{"type": "exchangeStatus"}`

## 账户数据 API

### 1. 查询永续账户状态

- *请求**: `{"type": "clearinghouseState", "user": "0x..."}`

```python
resp = requests.post(f"{BASE_URL}/info", json={
    "type": "clearinghouseState",
    "user": "0x1234..."
})
state = resp.json()
print(f"Account value: {state['marginSummary']['accountValue']}")
print(f"Total margin used: {state['marginSummary']['totalMarginUsed']}")
for pos in state.get("assetPositions", []):
    p = pos["position"]
    if float(p["szi"]) != 0:
        print(f"  {p['coin']}: size={p['szi']}, entry={p['entryPx']}, "
              f"unrealizedPnl={p['unrealizedPnl']}, leverage={p['leverage']['value']}")

# SDK

state = info.user_state(address)

```bash

### 2. 查询现货账户状态

- *请求**: `{"type": "spotClearinghouseState", "user": "0x..."}`

```python
resp = requests.post(f"{BASE_URL}/info", json={
    "type": "spotClearinghouseState",
    "user": "0x1234..."
})
balances = resp.json()["balances"]
for b in balances:
    if float(b.get("total", 0)) > 0:
        print(f"{b['coin']}: total={b['total']}, hold={b['hold']}")

```bash

### 3. 查询订单状态

- *请求**: `{"type": "orderStatus", "user": "0x...", "oid": 12345}`

### 4. 查询用户成交

- *请求**: `{"type": "userFills", "user": "0x..."}`

### 5. 查询资金费用

- *请求**: `{"type": "userFunding", "user": "0x..."}`

## 交易 (POST /exchange)

> 交易请求需要 EIP-712 签名。推荐使用 Python SDK。

### 1. 下单

```python
from hyperliquid.utils.types import LIMIT_ORDER

# 限价买单

order_result = exchange.order(
    coin="BTC",
    is_buy=True,
    sz=0.01,
    limit_px=40000,
    order_type={"limit": {"tif": "Gtc"}},
)
print(f"Order status: {order_result['response']['data']['statuses']}")

# 市价买单 (使用 IOC + 滑点)

order_result = exchange.market_open(
    coin="ETH",
    is_buy=True,
    sz=1.0,
    slippage=0.01,  # 1% 滑点

)

# 止损单

order_result = exchange.order(
    coin="BTC",
    is_buy=False,
    sz=0.01,
    limit_px=38000,
    order_type={"trigger": {
        "triggerPx": "39000",
        "isMarket": True,
        "tpsl": "sl"
    }},
)

```bash

### 2. 撤单

```python

# 撤销特定订单

cancel_result = exchange.cancel(coin="BTC", oid=12345)

# 批量撤单

cancel_result = exchange.bulk_cancel([
    {"coin": "BTC", "oid": 12345},
    {"coin": "ETH", "oid": 67890},
])

```bash

### 3. 修改订单

```python
modify_result = exchange.modify_order(
    oid=12345,
    coin="BTC",
    is_buy=True,
    sz=0.02,
    limit_px=41000,
    order_type={"limit": {"tif": "Gtc"}},
)

```bash

### 4. 设置杠杆

```python
exchange.update_leverage(leverage=10, coin="BTC", is_cross=True)

```bash

### 5. 资金划转

```python

# USDC 划转

exchange.usd_class_transfer(amount=100, toPerp=True)  # 现货→合约

# 提现到 Arbitrum

exchange.withdraw(amount=100, destination="0x...")

```bash

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| REST 总权重 | 1200/分钟 | 按 IP |

| `l2Book, allMids, clearinghouseState` | 权重 2 | 轻量查询 |

| `candleSnapshot` | 按返回条数增加 | 数据量相关 |

| `userRole` | 权重 60 | 高权重 |

| 其他 info | 权重 20 | 默认 |

| WebSocket 连接 | 最多 10 个/IP | |

| WebSocket 新连接 | 30 次/分钟 | |

| WebSocket 订阅 | 1000 个 | 单连接 |

| WebSocket 消息 | 2000/分钟 | |

| 开放订单数 | 默认 1000，上限 5000 | 按地址，随成交增加 |

### Coin 命名规则

| 类型 | 规则 | 示例 |

|------|------|------|

| 永续合约 | `meta` 返回的 `name` | `BTC`, `ETH` |

| 现货 (PURR) | 使用交易对名 | `PURR/USDC` |

| 其他现货 | `@{index}` | `@1`, `@2`（`spotMeta.universe` 索引） |

## WebSocket 支持

### 连接信息

- *URL**: `wss://api.hyperliquid.xyz/ws`

### 订阅频道

| 频道 | 描述 |

|------|------|

| `allMids` | 所有中间价 |

| `l2Book` | L2 订单簿 |

| `trades` | 实时成交 |

| `candle` | K 线数据 |

| `notification` | 通知（需用户地址） |

| `orderUpdates` | 订单更新（需用户地址） |

| `userEvents` | 用户事件 |

| `userFills` | 用户成交 |

| `userFundings` | 用户资金费用 |

| `userNonFundingLedgerUpdates` | 非资金费用账本 |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    channel = data.get("channel", "")

    if channel == "allMids":
        mids = data.get("data", {}).get("mids", {})
        btc = mids.get("BTC", "N/A")
        eth = mids.get("ETH", "N/A")
        print(f"BTC={btc}, ETH={eth}")
    elif channel == "trades":
        for t in data.get("data", []):
            print(f"Trade {t['coin']}: px={t['px']}, sz={t['sz']}, side={t['side']}")
    elif channel == "l2Book":
        book = data.get("data", {})
        coin = book.get("coin", "")
        levels = book.get("levels", [[], []])
        print(f"Book {coin}: asks={len(levels[0])}, bids={len(levels[1])}")
    elif channel == "orderUpdates":
        for order in data.get("data", []):
            print(f"Order update: {order['coin']} status={order['status']}")

def on_open(ws):

# 订阅中间价
    ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "allMids"}}))

# 订阅成交
    ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": "BTC"}}))

# 订阅订单簿
    ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": "BTC"}}))

# 订阅用户订单更新
    ws.send(json.dumps({
        "method": "subscribe",
        "subscription": {"type": "orderUpdates", "user": "0x1234..."}
    }))

ws = websocket.WebSocketApp(
    "wss://api.hyperliquid.xyz/ws",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)

```bash

### SDK WebSocket

```python
from hyperliquid.info import Info
from hyperliquid.utils import constants

info = Info(constants.MAINNET_API_URL)

def on_trade(data):
    for t in data["data"]:
        print(f"Trade: {t['coin']} px={t['px']} sz={t['sz']}")

# 订阅成交

info.subscribe({"type": "trades", "coin": "BTC"}, on_trade)

```bash

## 错误处理

### 响应格式

成功的 `/exchange` 响应:

```json
{
  "status": "ok",
  "response": {
    "type": "order",
    "data": {
      "statuses": [{"resting": {"oid": 12345}}]
    }
  }
}

```bash

### 常见错误

| 错误 | 描述 |

|------|------|

| `INVALID_SIGNATURE` | 签名无效 |

| `INSUFFICIENT_MARGIN` | 保证金不足 |

| `ORDER_NOT_FOUND` | 订单不存在 |

| `RATE_LIMIT` | 速率限制 |

| `PRICE_SLIPPAGE` | 价格滑点过大 |

| `MIN_TRADE_SIZE` | 低于最小交易数量 |

### Python 错误处理

```python
def safe_hl_info(request_type, **kwargs):
    try:
        body = {"type": request_type, **kwargs}
        resp = requests.post(f"{BASE_URL}/info", json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            print("Rate limited, waiting...")
            import time; time.sleep(2)
            return safe_hl_info(request_type, **kwargs)
        print(f"HTTP Error: {e}")
        return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细 /info 和 /exchange 端点说明
- 添加 EIP-712 签名方式和 Python SDK 使用示例
- 添加市场数据（中间价、订单簿、K 线、成交）详细说明
- 添加账户查询（永续/现货状态、订单、成交）示例
- 添加交易操作（下单、撤单、改单、杠杆、划转）SDK 示例
- 添加 WebSocket（原生 + SDK）订阅示例
- 添加 Coin 命名规则说明
- 添加速率限制详细权重信息

- --

## 相关资源

- [Hyperliquid 官方文档](<https://hyperliquid.gitbook.io/hyperliquid-docs/)>
- [Hyperliquid Python SDK](<https://github.com/hyperliquid-dex/hyperliquid-python-sdk)>
- [Hyperliquid 官网](<https://hyperliquid.xyz)>
- [CCXT Hyperliquid 实现](<https://github.com/ccxt/ccxt)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 Hyperliquid 官方 API 文档整理。*
