# Coincheck API 文档

## 交易所信息

- **交易所名称**: Coincheck
- **官方网站**: https://coincheck.com
- **API文档**: https://coincheck.com/documents/exchange/api
- **24h交易量排名**: #37
- **24h交易量**: $60M+
- **支持的交易对**: 30+（以官方列表为准）
- **API版本**: REST / Public WebSocket

## API基础信息

### 基础URL

```text
# REST API
https://coincheck.com/api

# WebSocket
wss://ws-api.coincheck.com
```

### 请求头（私有接口）

```text
ACCESS-KEY: {api_key}
ACCESS-NONCE: {nonce_ms}
ACCESS-SIGNATURE: {signature}
```

## 认证方式

Coincheck 使用 HMAC SHA256 签名。

**签名字符串**:

`nonce + request_url + request_body`

## 市场数据API（示例）

- Ticker: `GET /api/ticker`
- Orderbook: `GET /api/order_books`
- Trades: `GET /api/trades`

## 交易API（示例）

- 新建订单: `POST /api/exchange/orders`
- 取消订单: `DELETE /api/exchange/orders/{id}`

## 账户管理API

- 余额: `GET /api/accounts/balance`

## 速率限制

- 交易所新下单：最多 4 请求/秒（超过返回 429）

## WebSocket支持

- Public WebSocket 订阅 `*-orderbook` 与 `*-trades` 频道

## 错误代码

- 超限返回 `429: too_many_requests`

## 代码示例

```python
# WebSocket 订阅
import websocket
import json

ws = websocket.WebSocket()
ws.connect("wss://ws-api.coincheck.com")
ws.send(json.dumps({"type": "subscribe", "channel": "btc_jpy-orderbook"}))
```
