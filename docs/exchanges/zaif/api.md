# Zaif API 文档

## 交易所信息

- **交易所名称**: Zaif
- **官方网站**: https://zaif.jp
- **API文档**: https://zaif-api-document.readthedocs.io/ja/latest/
- **24h交易量排名**: #39
- **24h交易量**: $40M+
- **支持的交易对**: 100+（以官方列表为准）
- **API版本**: Public API / Trading API / WebSocket

## API基础信息

### 基础URL

```text
# Public API
https://api.zaif.jp/api/1

# Trading API
https://api.zaif.jp/tapi

# WebSocket
wss://ws.zaif.jp/stream?currency_pair={currency_pair}
```

### 请求头（Trading API）

```text
key: {api_key}
sign: {signature}
```

## 认证方式

Trading API 使用 HMAC-SHA512。

**签名字符串**:

将所有 POST 参数（包含 nonce、method 及其他参数）URL 编码为查询串 `param1=val1&param2=val2`，然后用 Secret Key 进行 HMAC-SHA512 签名。

## 市场数据API（示例）

- 通货信息: `GET /currencies/{currency}`
- 通货对: `GET /currency_pairs/{currency_pair}`
- Ticker: `GET /ticker/{currency_pair}`
- Orderbook: `GET /depth/{currency_pair}`

## 交易API（示例）

- Trading API 方法（如 `get_info`, `trade`, `cancel_order`）通过 `POST /tapi` 调用

## 账户管理API

- 余额与订单信息等通过 Trading API 获取

## 速率限制

- Public API: 10 次/秒
- WebSocket 断线重连：每 IP 约 4 次/秒以内

## WebSocket支持

- 实时板与终值推送

## 错误代码

- 详见官方文档

## 代码示例

```python
# Public: 获取 ticker
import requests

url = "https://api.zaif.jp/api/1/ticker/btc_jpy"
print(requests.get(url).json())
```
