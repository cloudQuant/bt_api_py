# Buda API 文档

## 交易所信息

- **交易所名称**: Buda.com
- **官方网站**: https://www.buda.com
- **API文档**: https://api.buda.com
- **24h交易量排名**: #54
- **24h交易量**: $30M+
- **支持的交易对**: 以官方 markets 列表为准
- **API版本**: v2

## API基础信息

### 基础URL

```text
# REST API
https://api.buda.com
```

### 请求头（私有接口）

```text
X-SBTC-APIKEY: {api_key}
X-SBTC-SIGNATURE: {signature}
X-SBTC-TIMESTAMP: {timestamp_ms}
```

## 认证方式

Buda 使用 HMAC-SHA256。

**签名字符串**:

`timestamp + method + request_path + body`

## 市场数据API（示例）

- 市场列表: `GET /markets`
- Ticker: `GET /markets/{market_id}/ticker`

## 交易API（示例）

- 下单: `POST /markets/{market_id}/orders`
- 撤单: `DELETE /markets/{market_id}/orders/{order_id}`

## 账户管理API

- 余额: `GET /balances`

## 速率限制

- 公共：按 IP 30 req/min
- 私有：按账号 30 req/min

## WebSocket支持

- `wss://api.buda.com/websocket`（详见官方文档）

## 错误代码

- 详见官方文档

## 代码示例

```python
# 获取市场列表
import requests

url = "https://api.buda.com/markets"
print(requests.get(url).json())
```
