# PancakeSwap API 文档

## 交易所信息

- **交易所名称**: PancakeSwap
- **官方网站**: https://pancakeswap.finance
- **开发者文档**: https://developer.pancakeswap.finance/
- **Subgraph 文档**: https://docs.pancakeswap.finance/to-delete/api/subgraph
- **24h交易量排名**: #5（DEX）
- **区块链**: BSC / 多链

## API基础信息

### Subgraph（GraphQL）

官方子图入口（示例）：

- Blocks (BSC): `https://thegraph.com/legacy-explorer/subgraph/pancakeswap/blocks`
- Blocks (zkSync): `https://api.studio.thegraph.com/query/45376/blocks-zksync/version/latest`
- Blocks (zkSync testnet): `https://api.studio.thegraph.com/query/45376/blocks-zksync-testnet/version/latest`
- Blocks (Polygon zkEVM): `https://api.studio.thegraph.com/query/45376/polygon-zkevm-block/version/latest`
- Blocks (opBNB): `https://opbnb-mainnet-graph.nodereal.io/subgraphs/name/pancakeswap/blocks`

- Exchange V2 (BSC): `https://nodereal.io/meganode/api-marketplace/pancakeswap-graphql`

> 其他子图（Prediction / Profile / Farm / NFT 等）详见官方 Subgraph 文档与 pancake-subgraph 仓库。

## 代码示例

```graphql
# 获取 Pair 日维度数据（示例）
{
  pairDayDatas(first: 1, orderBy: date, orderDirection: desc) {
    date
    dailyVolumeUSD
    reserveUSD
  }
}
```
