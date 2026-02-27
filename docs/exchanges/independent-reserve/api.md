# Independent Reserve API 文档

## 交易所信息

- **交易所名称**: Independent Reserve
- **官方网站**: https://www.independentreserve.com
- **API文档**: https://www.independentreserve.com/au/features/api
- **24h交易量排名**: #40
- **24h交易量**: $30M+
- **支持的交易对**: 50+（以官方列表为准）
- **API版本**: REST / WebSocket

## API基础信息

### 基础URL

```text
# REST API
https://api.independentreserve.com
```

### 请求方式

- Public 方法: HTTP GET
- Private 方法: HTTP POST（JSON, `Content-Type: application/json`）

## 认证方式

- 私有方法使用 `apiKey`、`nonce` 与 `signature`（HMAC-SHA256）

## 市场数据API（示例）

- 市场摘要: `GET /Public/GetMarketSummary`
- 订单簿: `GET /Public/GetOrderBook`
- 成交: `GET /Public/GetRecentTrades`
- 货币/网络信息: `GET /Public/GetValidPrimaryCurrencyCodes` 等

## 交易API（示例）

- 下单: `POST /Private/PlaceLimitOrder`
- 取消订单: `POST /Private/CancelOrder`

## 账户管理API

- 充值地址: `POST /Private/GetDigitalCurrencyDepositAddress2`
- 余额与资金相关接口详见官方文档

## 速率限制

- 官方文档未给出统一数值（部分接口有缓存提示）

## WebSocket支持

- 提供 WebSocket（详见官方 GitHub 文档链接）

## 错误代码

- 官方文档提供错误码与处理方式

## 代码示例

```python
# 获取市场摘要
import requests

url = "https://api.independentreserve.com/Public/GetMarketSummary?primaryCurrencyCode=xbt&secondaryCurrencyCode=usd"
print(requests.get(url).json())
```
