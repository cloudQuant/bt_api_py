# Luno API 文档

## 交易所信息

- **交易所名称**: Luno
- **官方网站**: https://www.luno.com
- **API文档**: https://www.luno.com/en/developers/api
- **24h交易量排名**: #44
- **24h交易量**: $50M+
- **支持的交易对**: 50+（以官方 markets 列表为准）
- **API版本**: v1（REST + Streaming）

## API基础信息

### 基础URL

```text
# REST API
https://api.luno.com/api/1

# Exchange REST API
https://api.luno.com/api/exchange/1

# WebSocket (市场)
wss://ws.luno.com/api/1/stream/:pair

# WebSocket (用户)
wss://ws.luno.com/api/1/userstream
```

### 认证方式

Luno 使用 HTTP Basic Auth。

- 用户名: API key id
- 密码: API key secret

## 市场数据API（示例）

- 行情: `GET /api/1/ticker`
- 全部行情: `GET /api/1/tickers`
- 订单簿: `GET /api/1/orderbook`
- Top 订单簿: `GET /api/1/orderbook_top`
- 成交: `GET /api/1/trades`
- K线: `GET /api/exchange/1/candles`（需认证）
- 市场信息: `GET /api/exchange/1/markets`

## 交易API（示例）

- 下单、撤单、订单查询等详见官方文档

## 账户管理API（示例）

- 余额: `GET /api/1/balance`
- 账户与资金接口详见官方文档

## 速率限制

- REST API: 300 次/分钟
- Streaming: 50 个并发会话

## WebSocket支持

- 市场 WebSocket: `wss://ws.luno.com/api/1/stream/:pair`
- 用户 WebSocket: `wss://ws.luno.com/api/1/userstream`

## 错误代码

- 超限返回 HTTP 429

## 代码示例

```python
# 获取行情
import requests

url = "https://api.luno.com/api/1/ticker?pair=XBTZAR"
print(requests.get(url).json())
```
