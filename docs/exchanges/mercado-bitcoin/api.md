# Mercado Bitcoin API 文档

## 交易所信息

- **交易所名称**: Mercado Bitcoin
- **官方网站**: https://www.mercadobitcoin.com.br
- **API文档**: https://www.mercadobitcoin.com.br/api-doc/ （现货 API）
- **API文档（Trade）**: https://www.mercadobitcoin.com.br/api-doc/trade-api/ （私有 API）
- **24h交易量排名**: #48
- **24h交易量**: $40M+
- **支持的交易对**: 200+（以官方列表为准）
- **API版本**: Public / Trade API

## API基础信息

### 基础URL

```text
# Public API
https://www.mercadobitcoin.net/api

# Trade API
https://www.mercadobitcoin.net/tapi
```

### 请求头（Trade API）

```text
TAPI-ID: {api_key}
TAPI-MAC: {signature}
```

## 认证方式

Trade API 使用 HMAC-SHA512。

**签名字符串**:

使用 `tapi_nonce` 与 POST 参数拼接的 query string 进行 HMAC-SHA512。

## 市场数据API（示例）

- Ticker: `GET /api/{coin}/ticker/`
- Orderbook: `GET /api/{coin}/orderbook/`
- Trades: `GET /api/{coin}/trades/`
- Day-summary: `GET /api/{coin}/day-summary/{year}/{month}/{day}/`

## 交易API（示例）

- `POST /tapi/`，`method` 包括 `list_orders`, `place_buy_order`, `place_sell_order`, `cancel_order` 等

## 账户管理API

- 余额、订单、成交等接口通过 Trade API

## 速率限制

- 详见官方文档

## WebSocket支持

- 官方文档提供 WebSocket API 说明（详见官方 API 文档）

## 错误代码

- 详见官方文档

## 代码示例

```python
# 获取 ticker
import requests

url = "https://www.mercadobitcoin.net/api/BTC/ticker/"
print(requests.get(url).json())
```
