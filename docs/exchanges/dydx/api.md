# dYdX API 文档

## 交易所信息

- **交易所名称**: dYdX
- **官方网站**: https://dydx.xyz
- **API文档**: https://docs.dydx.xyz/
- **24h交易量排名**: #4（DEX）
- **区块链**: dYdX Chain

## API基础信息

### Indexer HTTP API (Swagger)

```text
```

### WebSocket API

```text
wss://indexer.dydx.trade/v4/ws
wss://indexer.v4testnet.dydx.exchange/v4/ws
```

## 端点清单（来自官方 Swagger）

### Indexer

- `GET /addresses/{address}`
- `GET /addresses/{address}/parentSubaccountNumber/{parentSubaccountNumber}`
- `POST /addresses/{address}/registerToken`
- `GET /addresses/{address}/subaccountNumber/{subaccountNumber}`
- `POST /addresses/{address}/testNotification`
- `GET /affiliates/address`
- `GET /affiliates/metadata`
- `POST /affiliates/referralCode`
- `GET /affiliates/snapshot`
- `GET /affiliates/total_volume`
- `GET /assetPositions`
- `GET /assetPositions/parentSubaccountNumber`
- `GET /candles/perpetualMarkets/{ticker}`
- `GET /compliance/screen/{address}`
- `GET /fills`
- `GET /fills/parentSubaccount`
- `GET /fundingPayments`
- `GET /fundingPayments/parentSubaccount`
- `GET /height`
- `GET /historical-pnl`
- `GET /historical-pnl/parentSubaccountNumber`
- `GET /historicalBlockTradingRewards/{address}`
- `GET /historicalFunding/{ticker}`
- `GET /historicalTradingRewardAggregations/{address}`
- `GET /orderbooks/perpetualMarket/{ticker}`
- `GET /orders`
- `GET /orders/parentSubaccountNumber`
- `GET /orders/{orderId}`
- `GET /perpetualMarkets`
- `GET /perpetualPositions`
- `GET /perpetualPositions/parentSubaccountNumber`
- `GET /pnl`
- `GET /pnl/parentSubaccountNumber`
- `GET /screen`
- `GET /sparklines`
- `GET /time`
- `GET /trader/search`
- `GET /trades/perpetualMarket/{ticker}`
- `GET /transfers`
- `GET /transfers/between`
- `GET /transfers/parentSubaccountNumber`
- `POST /turnkey/appleLoginRedirect`
- `POST /turnkey/signin`
- `POST /turnkey/uploadAddress`
- `GET /vault/v1/megavault/historicalPnl`
- `GET /vault/v1/megavault/positions`
- `GET /vault/v1/vaults/historicalPnl`

## 速率限制

- 以官方文档为准

## 错误代码

- 以官方文档为准

## 代码示例

```python
# 获取永续市场列表
import requests

url = "https://indexer.dydx.trade/v4/perpetualMarkets"
print(requests.get(url).json())
```
