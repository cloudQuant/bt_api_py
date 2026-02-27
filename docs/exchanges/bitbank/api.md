# Bitbank API 文档

## 交易所信息

- **交易所名称**: bitbank
- **官方网站**: https://bitbank.cc
- **API文档**: https://github.com/bitbankinc/bitbank-api-docs
- **24h交易量排名**: #38
- **24h交易量**: $50M+
- **支持的交易对**: 30+（以官方列表为准）
- **API版本**: REST v1 / WebSocket

## API基础信息

### 基础URL

```text
# Private REST API
https://api.bitbank.cc/v1

# Public REST API
https://public.bitbank.cc

# WebSocket (Public Stream)
wss://stream.bitbank.cc
```

### 请求头（私有接口）

```text
ACCESS-KEY: {api_key}
ACCESS-NONCE: {nonce}
ACCESS-SIGNATURE: {signature}

# 或使用 ACCESS-TIME-WINDOW 方式:
ACCESS-REQUEST-TIME: {timestamp_ms}
ACCESS-TIME-WINDOW: {window_ms}
ACCESS-SIGNATURE: {signature}
```

## 认证方式

- 公共 API 无需认证
- 私有 API 使用 HMAC-SHA256 签名
- ACCESS-TIME-WINDOW 与 ACCESS-NONCE 两种模式（详见官方说明）

## 市场数据API（示例）

- Ticker: `GET /{pair}/ticker`
- Depth: `GET /{pair}/depth`
- Trades: `GET /{pair}/transactions/{YYYYMMDD}`
- Candlestick: `GET /{pair}/candlestick/{candle-type}/{YYYY}`

## 交易API（示例）

- 下单: `POST /user/spot/order`
- 撤单: `POST /user/spot/cancel_order`
- 查询订单: `GET /user/spot/order`

## 账户管理API

- 资产: `GET /user/assets`

## 速率限制

- 取得系: 10 次/秒
- 更新系（下单、撤单、出金请求等）: 6 次/秒
- 触发限频返回 429

## WebSocket支持

- Socket.IO 4.x 实现的 Public Stream
- 频道：`ticker_{pair}`、`transactions_{pair}`、`depth_diff_{pair}`、`depth_whole_{pair}`、`circuit_break_info_{pair}`

## 错误代码

- 详见官方错误码列表（errors_JP.md）

## 代码示例

```python
# 获取板信息
import requests

url = "https://public.bitbank.cc/btc_jpy/depth"
print(requests.get(url).json())
```
