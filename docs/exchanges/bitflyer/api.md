# BitFlyer API 文档

## 交易所信息

- **交易所名称**: bitFlyer
- **官方网站**: https://bitflyer.com
- **API文档**: https://lightning.bitflyer.com/docs
- **实时API文档**: https://bf-lightning-api.readme.io/docs/realtime-api
- **24h交易量排名**: #35
- **24h交易量**: $77M+
- **支持的交易对**: 50+（以官方列表为准）
- **API版本**: HTTP API + Realtime API

## API基础信息

### 基础URL

```text
# HTTP API
https://api.bitflyer.com/v1/

# Realtime API (Socket.IO 2.0)
https://io.lightstream.bitflyer.com

# Realtime API (JSON-RPC)
wss://ws.lightstream.bitflyer.com/json-rpc
```

### 请求头（Private API）

```text
ACCESS-KEY: {api_key}
ACCESS-TIMESTAMP: {timestamp}
ACCESS-SIGN: {signature}
```

## 认证方式

bitFlyer 使用 HMAC SHA256。

**签名字符串**:

`timestamp + HTTP method + request path + request body`

将签名写入 `ACCESS-SIGN`。

## 市场数据API（示例）

- 公共行情、板、成交等：`GET /v1/` 下的 Public API

## 交易API（示例）

- 私有下单：`POST /v1/me/sendchildorder`
- 私有撤单：`POST /v1/me/cancelallchildorders`

## 账户管理API

- 私有资产与订单相关接口见官方文档

## 速率限制

- 同 IP：500 次 / 5 分钟
- Private API：500 次 / 5 分钟
- 小额订单（<=0.1）: 100 次 / 分钟
- 部分私有接口合计 300 次 / 5 分钟

## WebSocket支持

- 支持 Socket.IO 与 JSON-RPC 两种方式
- Public/Private Channels 均可订阅（详见官方 Realtime API 文档）

## 错误代码

- 官方文档提供错误码与限制说明

## 代码示例

```python
# 获取板信息
import requests

url = "https://api.bitflyer.com/v1/board?product_code=BTC_JPY"
print(requests.get(url).json())
```
