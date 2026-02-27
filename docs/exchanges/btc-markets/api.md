# BTC Markets API 文档

## 交易所信息

- **交易所名称**: BTC Markets
- **官方网站**: https://www.btcmarkets.net
- **API文档**: https://docs.btcmarkets.net （API v3，JS 文档）
- **API文档（Legacy）**: https://github.com/BTCMarkets/API/wiki
- **24h交易量排名**: #41
- **24h交易量**: $25M+
- **支持的交易对**: 50+（以官方 markets 列表为准）
- **API版本**: v3（推荐），v1/v2（Legacy）

## API基础信息

### 基础URL

```text
# REST API v3
详见官方文档 https://docs.btcmarkets.net

# REST API v2 (Legacy)
https://api.btcmarkets.net/v2

# WebSocket v2
wss://socket.btcmarkets.net/v2
```

### 请求头（Legacy 私有接口）

```text
Accept: application/json
Content-Type: application/json
apikey: {api_key}
timestamp: {timestamp_ms}
signature: {signature}
```

## 认证方式（Legacy v1/v2）

BTC Markets v1/v2 使用 HMAC SHA512。

**签名步骤**:

1. 构造签名字符串：`path + '
' + timestamp + '
' + body`（GET 时 body 为空）
2. 使用 Secret Key（Base64 解码）进行 HMAC SHA512
3. Base64 编码签名，写入 `signature` 头

## 市场数据API（Legacy 示例）

- 活跃市场列表: `GET /v2/market/active`

## 交易API（Legacy 示例）

- v1/v2 订单/成交/资产等请参见官方 Wiki 文档

## 账户管理API（Legacy 示例）

- v1/v2 余额、历史订单与资金接口详见官方 Wiki 文档

## 速率限制

- v3 限频请参见官方文档
- v1/v2 限频请参见官方 Wiki 文档

## WebSocket支持

- WebSocket v2 端点：`wss://socket.btcmarkets.net/v2`
- 频道：`tick`、`trade`、`orderbook`、`orderChange`、`fundChange`、`heartbeat`

## 错误代码

- v3/v1/v2 错误码请参见官方文档

## 代码示例

```python
# Legacy: 获取活跃市场
import requests

url = "https://api.btcmarkets.net/v2/market/active"
print(requests.get(url).json())
```
