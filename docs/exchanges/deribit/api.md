# Deribit API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04
- 官方文档: <https://docs.deribit.com>

## 交易所基本信息

- 官方名称: Deribit
- 官网: <https://www.deribit.com>
- CMC 衍生品排名: #11
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 期权(Options)、永续合约(Perpetual)、期货(Futures)
- 支持的币种: BTC, ETH, SOL, USDC 等
- 手续费: Maker -0.01% ~ 0.02%, Taker 0.03% ~ 0.05% (合约); 期权另计
- 协议: JSON-RPC 2.0 (REST + WebSocket)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST (生产) | `https://www.deribit.com/api/v2` | 生产环境 |
| REST (测试) | `https://test.deribit.com/api/v2` | 测试环境 |
| WebSocket (生产) | `wss://www.deribit.com/ws/api/v2` | 生产 WebSocket |
| WebSocket (测试) | `wss://test.deribit.com/ws/api/v2` | 测试 WebSocket |
| FIX (生产) | `tcp://fix.deribit.com:9881` | FIX 协议(机构) |

## 认证方式

### API Key 认证

Deribit 使用 JSON-RPC 格式，认证通过 `public/auth` 方法完成。

**认证方式**:
- `client_credentials`: 使用 API Key + Secret
- `client_signature`: HMAC SHA256 签名
- `refresh_token`: 刷新令牌

```python
import requests
import json

BASE_URL = "https://www.deribit.com/api/v2"
API_KEY = "your_client_id"
API_SECRET = "your_client_secret"

def auth():
    """使用 client_credentials 认证"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "public/auth",
        "params": {
            "grant_type": "client_credentials",
            "client_id": API_KEY,
            "client_secret": API_SECRET
        }
    }
    resp = requests.post(BASE_URL, json=payload)
    result = resp.json()
    return result["result"]["access_token"]

def private_request(method, params, access_token):
    """发送私有请求"""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": method,
        "params": params
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(BASE_URL + f"/{method.replace('/', '/')}", 
                        params=params, headers=headers)
    return resp.json()

def public_request(method, params=None):
    """发送公共请求"""
    url = f"{BASE_URL}/public/{method}"
    resp = requests.get(url, params=params or {})
    return resp.json()
```

## 市场数据 API

### 1. 获取服务器时间

- **方法**: `public/get_time`
- **端点**: `GET /api/v2/public/get_time`

### 2. 获取交易品种列表

- **方法**: `public/get_instruments`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| currency | STRING | 是 | BTC / ETH / SOL / USDC |
| kind | STRING | 否 | future / option / spot / future_combo / option_combo |
| expired | BOOL | 否 | 是否包含已过期合约 |

### 3. 获取 Ticker

- **方法**: `public/ticker`
- **参数**: `instrument_name` (必需，如 `BTC-PERPETUAL`)

**响应字段**:
```json
{
  "result": {
    "instrument_name": "BTC-PERPETUAL",
    "last_price": 96000.0,
    "best_bid_price": 95999.5,
    "best_ask_price": 96000.5,
    "mark_price": 96000.2,
    "index_price": 95998.0,
    "open_interest": 500000000,
    "funding_8h": 0.0001,
    "volume_24h": 12000.0
  }
}
```

### 4. 获取深度

- **方法**: `public/get_order_book`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| instrument_name | STRING | 是 | 合约名称 |
| depth | INT | 否 | 深度档位: 1,5,10,20(默认) |

### 5. 获取 K 线

- **方法**: `public/get_tradingview_chart_data`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| instrument_name | STRING | 是 | 合约名称 |
| start_timestamp | INT | 是 | 起始时间(毫秒) |
| end_timestamp | INT | 是 | 结束时间(毫秒) |
| resolution | STRING | 是 | 1,3,5,10,15,30,60,120,180,360,720,1D |

### 6. 获取最近成交

- **方法**: `public/get_last_trades_by_instrument`
- **参数**: `instrument_name`, `count` (默认10, 最大1000)

### 7. 获取资金费率

- **方法**: `public/get_funding_rate_value`
- **参数**: `instrument_name`, `start_timestamp`, `end_timestamp`

## 交易 API

### 1. 下单

- **方法**: `private/buy` 或 `private/sell`
- **参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| instrument_name | STRING | 是 | 合约名称 |
| amount | DECIMAL | 是 | 数量(USD for futures, contracts for options) |
| type | ENUM | 否 | limit / market / stop_limit / stop_market |
| price | DECIMAL | 条件 | 价格(limit订单) |
| post_only | BOOL | 否 | 仅做 Maker |
| reduce_only | BOOL | 否 | 仅减仓 |
| label | STRING | 否 | 用户自定义标签 |

### 2. 撤单

- **方法**: `private/cancel`
- **参数**: `order_id` (必需)

### 3. 批量撤单

- **方法**: `private/cancel_all_by_instrument`
- **参数**: `instrument_name`

### 4. 查询持仓

- **方法**: `private/get_positions`
- **参数**: `currency` (必需), `kind` (可选)

### 5. 查询账户

- **方法**: `private/get_account_summary`
- **参数**: `currency` (必需)

**响应字段**:
```json
{
  "result": {
    "equity": 10.5,
    "balance": 10.0,
    "margin_balance": 10.2,
    "available_funds": 8.5,
    "maintenance_margin": 0.5,
    "initial_margin": 1.5,
    "total_pl": 0.5
  }
}
```

## WebSocket

### 连接与认证

```python
import websocket
import json

def on_open(ws):
    # 认证
    auth_msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "public/auth",
        "params": {
            "grant_type": "client_credentials",
            "client_id": API_KEY,
            "client_secret": API_SECRET
        }
    }
    ws.send(json.dumps(auth_msg))
    
    # 订阅
    sub_msg = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "public/subscribe",
        "params": {
            "channels": [
                "book.BTC-PERPETUAL.100ms",
                "trades.BTC-PERPETUAL.100ms",
                "ticker.BTC-PERPETUAL.100ms"
            ]
        }
    }
    ws.send(json.dumps(sub_msg))

ws = websocket.WebSocketApp(
    "wss://www.deribit.com/ws/api/v2",
    on_open=on_open,
    on_message=lambda ws, msg: print(json.loads(msg))
)
ws.run_forever()
```

### 订阅频道

| 频道 | 格式 | 说明 |
|------|------|------|
| 深度 | `book.{instrument}.{interval}` | interval: 100ms, agg2 |
| 成交 | `trades.{instrument}.{interval}` | 实时成交 |
| Ticker | `ticker.{instrument}.{interval}` | 行情快照 |
| K线 | `chart.trades.{instrument}.{resolution}` | K线数据 |
| 用户订单 | `user.orders.{instrument}.raw` | 需认证 |
| 用户成交 | `user.trades.{instrument}.{interval}` | 需认证 |

### 心跳

- 使用 `public/set_heartbeat` 方法设置心跳间隔
- 服务器发送 `heartbeat` 类型的 `test_request`
- 客户端需回复 `public/test` 方法

## 速率限制

| 类别 | 限制 |
|------|------|
| 非匹配引擎请求 | 20次/秒 |
| 匹配引擎请求 | 取决于等级 |
| WebSocket 订阅 | 每连接 100 个频道 |

## 合约命名规则

| 类型 | 格式 | 示例 |
|------|------|------|
| 永续 | `{COIN}-PERPETUAL` | `BTC-PERPETUAL` |
| 期货 | `{COIN}-{DDMMMYY}` | `BTC-28MAR25` |
| 期权 | `{COIN}-{DDMMMYY}-{STRIKE}-{C/P}` | `BTC-28MAR25-100000-C` |

## 特殊说明

- Deribit 是全球最大的加密期权交易所
- 使用 JSON-RPC 2.0 协议（非传统 REST）
- 合约金额以 USD 计价（反向合约）
- 支持组合保证金(Portfolio Margin)
- 测试网可免费获取测试资金

## 相关资源

- [Deribit API 文档](https://docs.deribit.com)
- [Deribit 快速入门](https://docs.deribit.com/articles/deribit-quickstart)
- [Deribit Python SDK](https://github.com/deribit/deribit-api-clients)
- [CCXT Deribit 实现](https://github.com/ccxt/ccxt)
