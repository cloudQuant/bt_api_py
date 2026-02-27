# CoinSpot API 文档

## 交易所信息

- **交易所名称**: CoinSpot
- **官方网站**: https://www.coinspot.com.au
- **API文档**: https://www.coinspot.com.au/v2/api
- **24h交易量排名**: #42
- **24h交易量**: $20M+
- **支持的交易对**: 300+（以官方 markets 列表为准）
- **API版本**: v2（Public/Private/Read Only）

## API基础信息

### 基础URL

```text
# Public API
https://www.coinspot.com.au/pubapi/v2

# Private API
https://www.coinspot.com.au/api/v2

# Read Only API
https://www.coinspot.com.au/api/v2/ro
```

### 请求头（Private API）

```text
Content-Type: application/json
key: {api_key}
signature: {signature}
nonce: {nonce_ms}
```

## 认证方式

CoinSpot 使用 HMAC SHA512。

**签名规则**:

- 使用共享密钥对请求参数进行 HMAC SHA512 签名
- 签名随请求一起发送（详见官方文档 Security 说明）

## 市场数据API（示例）

- 最新买价: `GET /pubapi/v2/buyprice/{cointype}`
- 最新卖价: `GET /pubapi/v2/sellprice/{cointype}`
- 订单簿: `GET /pubapi/v2/orders/open/{cointype}`

## 交易API（示例）

- 现货买入: `POST /api/v2/my/buy`
- 现货卖出: `POST /api/v2/my/sell`
- 取消买单: `POST /api/v2/my/buy/cancel`
- 取消卖单: `POST /api/v2/my/sell/cancel`

## 账户管理API（示例）

- 余额: `POST /api/v2/my/balances`
- 充值地址: `POST /api/v2/my/coin/deposit`
- 提现: `POST /api/v2/my/coin/withdraw/send`

## 速率限制

- 1000 次/分钟

## WebSocket支持

- 官方文档未提供 WebSocket API 说明

## 错误代码

- 返回 `status` 与 `message` 字段

## 代码示例

```python
# Public: 获取最新买价
import requests

url = "https://www.coinspot.com.au/pubapi/v2/buyprice/BTC"
print(requests.get(url).json())
```
