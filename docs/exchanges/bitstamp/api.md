# Bitstamp API 文档

## 交易所信息

- **交易所名称**: Bitstamp
- **官方网站**: https://www.bitstamp.net
- **API文档**: https://www.bitstamp.net/api
- **24h交易量排名**: #16
- **24h交易量**: $300M+
- **支持的交易对**: 80+ 交易对
- **API版本**: v2
- **特点**: 欧洲最老牌的加密货币交易所之一

## API基础信息

### 基础URL

```text
# REST API
https://www.bitstamp.net/api/v2

# REST API (Sandbox)
https://sandbox.bitstamp.net/api/v2

# WebSocket API v2 文档
https://www.bitstamp.net/websocket/v2/
```

### 请求头（私有接口）

```text
X-Auth: BITSTAMP {api_key}
X-Auth-Signature: {signature}
X-Auth-Nonce: {nonce}
X-Auth-Timestamp: {timestamp_ms}
X-Auth-Version: v2
Content-Type: application/x-www-form-urlencoded (无 body 时不要传)
```

## 认证方式

### 1. 获取API密钥

1. 登录 Bitstamp 账户
2. 进入设置 -> API访问
3. 创建 API Key 并设置权限
4. 可设置 IP 白名单（推荐）
5. 保存 API Key 与 Secret

### 2. 请求签名算法

Bitstamp 使用 HMAC SHA256。

**签名步骤**:

1. 生成 `nonce`（36 位小写字符串，150 秒内不可复用）
2. 生成时间戳（毫秒）
3. 拼接签名字符串：
   `"BITSTAMP" + " " + api_key + HTTP_METHOD + host + path + query + content_type + nonce + timestamp + version + body`
4. 使用 Secret 对签名字符串进行 HMAC SHA256
5. 签名写入 `X-Auth-Signature`

> 若无 body，请移除 `Content-Type` 头并从签名字符串中移除

## 市场数据API（示例）

- Ticker: `GET /api/v2/ticker/` 或 `GET /api/v2/ticker/{currency_pair}/`
- Ticker Hour: `GET /api/v2/ticker_hour/`
- 市场列表: `GET /api/v2/markets/`
- 币种信息: `GET /api/v2/currencies/`

## 交易API（示例）

- 下单: `POST /api/v2/buy/{market_symbol}/`、`POST /api/v2/sell/{market_symbol}/`
- 查询订单: `GET /api/v2/order_status/`
- 撤单: `POST /api/v2/cancel_order/`

## 账户管理API（示例）

- WebSocket 令牌: `POST /api/v2/websockets_token/`

## 速率限制

- 标准限速：400 请求/秒
- 10,000 请求/10 分钟

## WebSocket支持

- 提供 WebSocket API v2（请参考官方 WebSocket 文档）

## 错误代码

- 官方文档提供详细错误码（如 API0001～API0021、400.xxx 等）

## 代码示例

```python
# 获取 ticker
import requests

url = "https://www.bitstamp.net/api/v2/ticker/btcusd/"
print(requests.get(url).json())
```
