# Balancer API 文档

## 交易所信息

- **交易所名称**: Balancer
- **官方网站**: https://balancer.fi
- **API文档**: https://docs-v3.balancer.fi/data-and-analytics/data-and-analytics/balancer-api.html
- **24h交易量排名**: #8（DEX）
- **区块链**: 多链

## API基础信息

### GraphQL API

```text
https://api-v3.balancer.fi
```

### 主要 Query 域（官方 Backend 文档）

- Pools: `poolGetPool`, `poolGetPools`
- Gauges: `veBalGetUser`, `veBalGetUserBalance`, `veBalGetVotingList`
- Events: `poolGetEvents`
- Users: `userGetPoolBalances`, `userGetStaking`
- Tokens: `tokenGetTokens`, `tokenGetTokenDynamicData`, `tokenGetTokensDynamicData`, `tokenGetTokenData`, `tokenGetTokensData`
- Prices: `tokenGetCurrentPrices`, `tokenGetHistoricalPrices`
- SOR: `sorGetSwapPaths`

> 多数查询需要 `chain` 参数。

## 代码示例

```graphql
# 查询池子信息
{
  poolGetPool(id: "POOL_ID", chain: MAINNET) {
    id
    name
  }
}
```
