# PancakeSwap API 文档

## 文档信息
- 文档版本: 1.0.0
- 创建日期: 2026-02-27
- 开发者文档: https://developer.pancakeswap.finance/
- Subgraph 文档: https://docs.pancakeswap.finance/
- 数据来源: 官方文档

## 交易所基本信息
- 官方名称: PancakeSwap
- 官网: https://pancakeswap.finance
- 交易所类型: DEX (去中心化交易所)
- 交易量排名: DEX #5
- 区块链: BSC, Ethereum, Arbitrum, Base, Polygon zkEVM, zkSync Era, Linea, opBNB
- 特点: AMM DEX, 支持 V2/V3 流动性池, Farm, IFO, NFT
- 治理代币: CAKE

## API 接入方式

PancakeSwap 作为 DEX 提供多种数据接入方式:

| 接入方式 | 用途 |
|---------|------|
| Subgraph (GraphQL) | 链上数据查询 (池子、交易、流动性) |
| Smart Router API | 最优路径报价 |
| 智能合约 | 链上交易执行 |

## Subgraph (GraphQL)

### 主要 Subgraph 端点

| 子图 | 链 | URL |
|------|-----|-----|
| Exchange V2 | BSC | `https://nodereal.io/meganode/api-marketplace/pancakeswap-graphql` |
| Exchange V3 | BSC | 参见官方文档 |
| Blocks | BSC | `https://thegraph.com/legacy-explorer/subgraph/pancakeswap/blocks` |
| Blocks | zkSync | `https://api.studio.thegraph.com/query/45376/blocks-zksync/version/latest` |
| Blocks | Polygon zkEVM | `https://api.studio.thegraph.com/query/45376/polygon-zkevm-block/version/latest` |
| Blocks | opBNB | `https://opbnb-mainnet-graph.nodereal.io/subgraphs/name/pancakeswap/blocks` |

### GraphQL 查询示例

```graphql
# 获取交易对日维度数据
{
  pairDayDatas(first: 5, orderBy: date, orderDirection: desc,
    where: { pairAddress: "0x..." }
  ) {
    date
    dailyVolumeUSD
    reserveUSD
    dailyTxns
  }
}

# 获取 Top 池子
{
  pairs(first: 10, orderBy: reserveUSD, orderDirection: desc) {
    id
    token0 { symbol }
    token1 { symbol }
    reserveUSD
    volumeUSD
  }
}

# 获取 Token 信息
{
  tokens(first: 5, orderBy: tradeVolumeUSD, orderDirection: desc) {
    id
    symbol
    name
    decimals
    tradeVolumeUSD
    totalLiquidity
  }
}
```

### Python GraphQL 查询示例

```python
import requests

SUBGRAPH_URL = "https://proxy-worker-api.pancakeswap.com/bsc-exchange"

query = """
{
  pairs(first: 5, orderBy: reserveUSD, orderDirection: desc) {
    id
    token0 { symbol }
    token1 { symbol }
    reserveUSD
    volumeUSD
  }
}
"""

resp = requests.post(SUBGRAPH_URL, json={"query": query})
data = resp.json()["data"]
for pair in data["pairs"]:
    print(f"{pair['token0']['symbol']}/{pair['token1']['symbol']}: "
          f"TVL=${float(pair['reserveUSD']):,.0f}")
```

## Smart Router API

PancakeSwap 提供 Smart Router 用于获取最优交换路径和报价:

```python
# Smart Router 报价示例
# 具体端点和参数请参考 PancakeSwap SDK 或开发者文档
from pancakeswap_sdk import SmartRouter  # 示例

# 或使用 PancakeSwap SDK
# pip install @pancakeswap/sdk
```

## 链上交易 (智能合约)

### 核心合约

| 合约 | 描述 |
|------|------|
| PancakeRouter V2 | V2 AMM 路由 |
| SmartRouter V3 | V3 智能路由 |
| MasterChef V2/V3 | 流动性挖矿 |
| NonfungiblePositionManager | V3 流动性仓位管理 |

### Python 链上交易示例

```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://bsc-dataseed1.binance.org"))

ROUTER_V2 = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
# Router ABI 从 BscScan 获取

router = w3.eth.contract(address=ROUTER_V2, abi=ROUTER_ABI)

# 获取报价: BNB -> USDT
amounts = router.functions.getAmountsOut(
    w3.to_wei(1, 'ether'),  # 1 BNB
    [WBNB_ADDRESS, USDT_ADDRESS]
).call()
print(f"1 BNB = {amounts[1] / 10**18} USDT")

# 执行交换 (需签名交易)
# tx = router.functions.swapExactTokensForTokens(
#     amountIn, amountOutMin, path, to, deadline
# ).build_transaction({...})
```

## 速率限制

| 类别 | 限制 |
|------|------|
| Subgraph | 取决于 The Graph 节点限制 |
| 链上交易 | 受 BSC 区块 Gas 限制 |

## 变更历史

### 2026-02-27
- 基于官方文档完善

---

## 相关资源

- [PancakeSwap 开发者文档](https://developer.pancakeswap.finance/)
- [PancakeSwap GitHub](https://github.com/pancakeswap)
- [PancakeSwap SDK](https://github.com/pancakeswap/pancake-frontend)

---

*本文档由 bt_api_py 项目维护。*
