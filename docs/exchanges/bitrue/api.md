# Bitrue API 文档

## 交易所信息

- **交易所名称**: Bitrue
- **官方网站**: https://www.bitrue.com
- **API文档**: https://github.com/Bitrue-exchange/Spot-official-api-docs
- **24h交易量排名**: #17
- **24h交易量**: $200M+
- **支持的交易对**: 500+ 交易对
- **API版本**: v1 (Spot)

## API基础信息

### 基础URL

```text
# REST API (Spot)
https://openapi.bitrue.com

# WebSocket (市场数据)
wss://ws.bitrue.com/market/ws

# WebSocket (用户数据流)
wss://wsapi.bitrue.com
```

### 请求头（私有接口）

```text
X-MBX-APIKEY: {api_key}
Content-Type: application/x-www-form-urlencoded
```

## 认证方式

### 1. 获取API密钥

1. 登录 Bitrue 账户
2. 进入账户 -> API管理
3. 创建 API Key 并设置权限
4. 保存 API Key 与 Secret

### 2. 请求签名算法

Bitrue 使用 HMAC SHA256。

**签名步骤**:

1. 组装 `totalParams` = query string + request body
2. 使用 Secret 对 `totalParams` 做 HMAC SHA256
3. `signature` 放入 query string
4. `timestamp` 必填，`recvWindow` 可选（默认 5000ms）

## 市场数据API（示例）

- 最新成交: `GET /api/v1/trades`
- 历史成交: `GET /api/v1/historicalTrades`
- 最新价格: `GET /api/v1/ticker/price`
- 最优买卖: `GET /api/v1/ticker/bookTicker`
- ETF 净值: `GET /api/v1/etf/net-value/{symbol}`

## 交易API（示例）

- 下单: `POST /api/v1/order`
- 撤单: `DELETE /api/v1/order`
- 查询订单: `GET /api/v1/order`
- 当前挂单: `GET /api/v1/openOrders`
- 全部订单: `GET /api/v1/allOrders`

## 账户管理API（示例）

- 账户信息: `GET /api/v1/account`
- 成交记录: `GET /api/v2/myTrades`

## 速率限制

- 各接口返回 `X-MBX-USED-WEIGHT` 等权重信息（以接口说明为准）

## WebSocket支持

- 市场数据 WS：`wss://ws.bitrue.com/market/ws`
- 用户数据流 WS：`wss://wsapi.bitrue.com/stream?listenKey={listenKey}`

## 错误代码

- 官方文档提供错误码表（如 `-1022`、`-1102` 等）

## 代码示例

```python
# 获取最新价格
import requests

url = "https://openapi.bitrue.com/api/v1/ticker/price?symbol=BTCUSDT"
print(requests.get(url).json())
```
