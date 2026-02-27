# Korbit API 文档

## 交易所信息

- **交易所名称**: Korbit
- **官方网站**: https://www.korbit.co.kr
- **API文档**: https://docs.korbit.co.kr
- **24h交易量排名**: #36
- **24h交易量**: $70M+
- **支持的交易对**: 100+（以官方列表为准）
- **API版本**: REST v2 / WebSocket v2

## API基础信息

### 基础URL

```text
# REST API
https://api.korbit.co.kr

# WebSocket
wss://ws-api.korbit.co.kr/v2/public
wss://ws-api.korbit.co.kr/v2/private
```

### 请求头（私有接口）

- 使用 Korbit Open API Key 进行认证（详见官方认证说明）

## 认证方式

- 私有 REST 与 WebSocket 需要 API Key 认证
- 公共 WebSocket 无需认证

## 市场数据API（示例）

- 行情: `GET /v2/tickers?symbol=btc_krw`
- 深度: `GET /v2/orderbook?symbol=btc_krw`

## 交易API（示例）

- 下单 / 撤单 / 查询订单（详见官方文档）

## 账户管理API

- 余额、订单与成交等私有接口（详见官方文档）

## 速率限制

- 公共 API: 50 次/秒（按 IP）
- 下单: 30 次/秒（按账户）
- 取消订单: 30 次/秒（按账户）
- 出入金: 5 次/秒（按账户）
- 其他私有 API: 50 次/秒（按账户）

## WebSocket支持

- Public: 行情与订单簿（`wss://ws-api.korbit.co.kr/v2/public`）
- Private: 订单/成交/资产（`wss://ws-api.korbit.co.kr/v2/private`）

## 错误代码

- 超限返回 `429 Too Many Requests`

## 代码示例

```python
# REST: 获取行情
import requests

url = "https://api.korbit.co.kr/v2/tickers?symbol=btc_krw"
print(requests.get(url).json())
```
