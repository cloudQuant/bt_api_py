# Gemini API 文档

## 交易所信息

- **交易所名称**: Gemini
- **官方网站**: https://www.gemini.com
- **API文档**: https://docs.gemini.com
- **24h交易量排名**: #20-25
- **24h交易量**: $100M+
- **支持的交易对**: 100+ 交易对
- **API版本**: v1/v2

## API基础信息

### 基础URL

```text
# REST API (生产环境)
https://api.gemini.com

# REST API (沙盒环境)
https://api.sandbox.gemini.com
```

### 请求头（私有接口）

```text
X-GEMINI-APIKEY: {api_key}
X-GEMINI-PAYLOAD: {base64_payload}
X-GEMINI-SIGNATURE: {signature}
Content-Type: text/plain
Content-Length: 0
Cache-Control: no-cache
```

## 认证方式

### 1. 获取API密钥

1. 登录 Gemini 账户
2. 进入账户设置 -> API
3. 创建 API Key 并选择权限
4. 保存 API Key 和 Secret

### 2. 请求签名算法

Gemini 使用 HMAC SHA384。

**签名步骤**:

1. 构建 payload JSON，必须包含 `request` 和 `nonce`
2. 将 payload JSON 进行 Base64 编码
3. 使用 Secret 对 Base64 结果进行 HMAC SHA384
4. 签名结果转为十六进制字符串
5. 写入 `X-GEMINI-SIGNATURE`

## 市场数据API

- 交易对列表: `GET /v1/symbols`
- 交易对详情: `GET /v1/symbols/details/{symbol}`
- 行情: `GET /v1/pubticker/{symbol}`
- 24h Ticker: `GET /v2/ticker/{symbol}`
- 深度: `GET /v1/book/{symbol}`
- 成交: `GET /v1/trades/{symbol}`
- K线: `GET /v2/candles/{symbol}/{time_frame}`

## 交易API

- 下单: `POST /v1/order/new`
- 撤单: `POST /v1/order/cancel`
- 撤销全部订单: `POST /v1/order/cancel/all`
- 撤销会话订单: `POST /v1/order/cancel/session`
- 查询订单: `POST /v1/order/status`
- 当前挂单: `POST /v1/orders`
- 历史订单: `POST /v1/orders/history`
- 历史成交: `POST /v1/mytrades`

## 账户管理API

- 余额: `POST /v1/balances`
- 充值地址: `POST /v1/addresses/{network}`
- 新建充值地址: `POST /v1/deposit/{network}/newAddress`
- 提现: `POST /v1/withdraw/{currency}`
- 内部转账: `POST /v1/transfers`
- 交易历史: `POST /v1/transfers` / `POST /v1/transactions`

## 速率限制

- 公共接口: **120 次/分钟**（建议不超过 1 次/秒）
- 私有接口: **600 次/分钟**（建议不超过 5 次/秒）

## WebSocket支持

- 官方文档提供市场数据与订单事件 WebSocket API（详见官方 WebSocket 文档）

## 错误代码

- HTTP 错误码与 `reason`/`message` 详见官方错误码列表

## 代码示例

```python
# REST: 获取交易对列表
import requests

url = "https://api.gemini.com/v1/symbols"
print(requests.get(url).json())
```
