# Balancer API 文档

## 文档信息
- 文档版本: 1.0.0
- API类型: GraphQL
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://docs.balancer.fi/data-and-analytics/data-and-analytics/balancer-api/balancer-api.html

## 交易所基本信息
- 官方名称: Balancer
- 官网: https://balancer.fi
- 交易所类型: DEX (去中心化交易所)
- 协议版本: v2 / v3
- 24h交易量排名: #8 (DEX)
- 区块链: 多链 (Ethereum, Polygon, Arbitrum, Optimism, Gnosis, Avalanche, Base, BSC, Sonic)
- 池类型: Weighted Pool, Stable Pool, Composable Stable Pool, Boosted Pool, Managed Pool 等
- 治理代币: BAL
- GitHub: https://github.com/balancer

## API基础URL

Balancer 采用 GraphQL API，无传统 REST 端点。

| 端点类型 | URL | 说明 |
|---------|-----|------|
| GraphQL API | `https://api-v3.balancer.fi` | 主端点（自带 Playground） |
| Subgraph (Ethereum) | `https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-v2` | The Graph 子图 |
| Subgraph (Polygon) | `https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-polygon-v2` | Polygon 子图 |
| Subgraph (Arbitrum) | `https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-arbitrum-v2` | Arbitrum 子图 |

> **提示**: 可在浏览器直接访问 `https://api-v3.balancer.fi` 打开 GraphQL Playground 进行交互式查询。

## 认证方式

Balancer API 为**公开只读**接口，无需 API Key 或签名认证。

- 查询池子、Token、价格、SOR 路径等：**无需认证**
- 链上交易（Swap、Add/Remove Liquidity）：需通过**钱包签名**与智能合约交互，不经过 API

## 支持的链（Chain 枚举值）

| Chain 枚举 | 网络 |
|-----------|------|
| MAINNET | Ethereum 主网 |
| POLYGON | Polygon |
| ARBITRUM | Arbitrum |
| OPTIMISM | Optimism |
| GNOSIS | Gnosis Chain |
| AVALANCHE | Avalanche |
| BASE | Base |
| SEPOLIA | Sepolia 测试网 |

> 多数查询需要 `chain` 参数指定网络。

## GraphQL 查询域

API 围绕以下主要域组织：

| 域 | 描述 | 主要查询 |
|----|------|---------|
| Pools | 池子信息、TVL、APR | `poolGetPool`, `poolGetPools` |
| Gauges | veBAL 投票、Gauge 信息 | `veBalGetUser`, `veBalGetUserBalance`, `veBalGetVotingList` |
| Events | 池子事件（Swap/Add/Remove） | `poolGetEvents` |
| Users | 用户余额、质押 | `userGetPoolBalances`, `userGetStaking` |
| Tokens | Token 元数据与动态数据 | `tokenGetTokens`, `tokenGetTokenDynamicData`, `tokenGetTokensData` |
| Prices | 当前/历史价格 | `tokenGetCurrentPrices`, `tokenGetHistoricalPrices` |
| SOR | 智能订单路由（最优交换路径） | `sorGetSwapPaths` |

## 市场数据API

### 1. 查询单个池子详情（含 APR）

**Query**: `poolGetPool`

**描述**: 获取单个池子的详细信息，包括 Token 组成、TVL、APR 等。

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| id | String | 是 | 池子 ID（合约地址+nonce） |
| chain | GqlChain | 是 | 链枚举，如 MAINNET |

**GraphQL 示例**:
```graphql
{
  poolGetPool(
    id: "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda00020000000000000000067d"
    chain: MAINNET
  ) {
    id
    name
    type
    version
    allTokens {
      address
      name
    }
    poolTokens {
      address
      symbol
      balance
      hasNestedPool
    }
    dynamicData {
      totalLiquidity
      aprItems {
        title
        type
        apr
      }
    }
  }
}
```

**响应示例**:
```json
{
  "data": {
    "poolGetPool": {
      "id": "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda00020000000000000000067d",
      "name": "50WETH-50USDC",
      "type": "WEIGHTED",
      "version": 2,
      "allTokens": [
        {"address": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "name": "Wrapped Ether"},
        {"address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "name": "USD Coin"}
      ],
      "dynamicData": {
        "totalLiquidity": "12345678.90",
        "aprItems": [
          {"title": "Swap fees APR", "type": "SWAP_FEE", "apr": "0.0234"},
          {"title": "BAL reward APR", "type": "STAKING", "apr": "0.0512"}
        ]
      }
    }
  }
}
```

### 2. 查询多个池子（按条件筛选）

**Query**: `poolGetPools`

**描述**: 按链、TVL 等条件批量查询池子。

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| where.chainIn | [GqlChain] | 否 | 链列表，如 [MAINNET, ARBITRUM] |
| where.minTvl | Float | 否 | 最低 TVL 过滤 |
| where.userAddress | String | 否 | 用户地址（查询用户持仓池） |
| first | Int | 否 | 返回数量限制 |
| skip | Int | 否 | 分页偏移 |
| orderBy | GqlPoolOrderBy | 否 | 排序字段，如 totalLiquidity |
| orderDirection | GqlPoolOrderDirection | 否 | asc / desc |

**GraphQL 示例 - Top 10 按 TVL 排序**:
```graphql
{
  poolGetPools(
    first: 10
    orderBy: totalLiquidity
    orderDirection: desc
    where: { chainIn: [MAINNET] }
  ) {
    id
    name
    type
    dynamicData {
      totalLiquidity
      volume24h
      fees24h
    }
  }
}
```

**GraphQL 示例 - TVL > $10k 的池子**:
```graphql
{
  poolGetPools(
    where: { chainIn: [AVALANCHE, ARBITRUM], minTvl: 10000 }
  ) {
    id
    address
    name
  }
}
```

### 3. 查询池子 Swap 事件

**Query**: `poolGetEvents`

**描述**: 获取指定池子的 Swap/Add/Remove 事件历史。

**GraphQL 示例**:
```graphql
{
  poolGetEvents(
    range: { start: 1700000000, end: 1700086400 }
    poolId: "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda00020000000000000000067d"
    chain: MAINNET
    typeIn: [SWAP]
  ) {
    id
    type
    timestamp
    tx
  }
}
```

### 4. 查询 Token 列表与价格

**Query**: `tokenGetTokens`, `tokenGetCurrentPrices`

**描述**: 获取 Token 元数据及当前/历史价格。

**GraphQL 示例**:
```graphql
# 获取 Token 列表
{
  tokenGetTokens(chains: [MAINNET]) {
    address
    name
    symbol
    decimals
  }
}

# 获取 Token 动态数据（价格、市值等）
{
  tokenGetTokenDynamicData(
    address: "0xba100000625a3754423978a60c9317c58a424e3d"
    chain: MAINNET
  ) {
    price
    priceChange24h
    marketCap
    volume24h
  }
}
```

## 交易API（SOR - 智能订单路由）

### 查询最优 Swap 路径

**Query**: `sorGetSwapPaths`

**描述**: 使用智能订单路由器（SOR）查询最优交换路径。注意此接口仅返回最优路径信息，实际交换需通过链上合约完成。

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| chain | GqlChain | 是 | 链枚举 |
| swapAmount | String | 是 | 交换数量（human-readable，如 "1" 表示 1 ETH） |
| swapType | GqlSorSwapType | 是 | EXACT_IN（精确输入）或 EXACT_OUT（精确输出） |
| tokenIn | String | 是 | 输入 Token 地址 |
| tokenOut | String | 是 | 输出 Token 地址 |

**GraphQL 示例 - 1 WETH → USDC**:
```graphql
{
  sorGetSwapPaths(
    chain: MAINNET
    swapAmount: "1"
    swapType: EXACT_IN
    tokenIn: "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    tokenOut: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
  ) {
    swapAmountRaw
    returnAmountRaw
    priceImpact {
      priceImpact
      error
    }
  }
}
```

**响应示例**:
```json
{
  "data": {
    "sorGetSwapPaths": {
      "swapAmountRaw": "1000000000000000000",
      "returnAmountRaw": "3250000000",
      "priceImpact": {
        "priceImpact": "0.0012",
        "error": null
      }
    }
  }
}
```

> 链上执行 Swap 需要调用 Balancer Vault 合约的 `swap()` 或通过 Balancer Router 合约。

## 用户数据API

### 1. 查询用户池子余额

**Query**: `poolGetPools` (带 userAddress)

**描述**: 查询用户在各池子中的余额（包括钱包持有的 BPT 和质押在 Gauge/Aura 中的 BPT）。

**GraphQL 示例**:
```graphql
{
  poolGetPools(
    where: {
      chainIn: [MAINNET]
      userAddress: "0xYourWalletAddress"
    }
  ) {
    address
    name
    userBalance {
      stakedBalances {
        balance
        balanceUsd
        stakingType
      }
      walletBalance
      walletBalanceUsd
      totalBalance
      totalBalanceUsd
    }
  }
}
```

### 2. 查询用户 veBAL 信息

**Query**: `veBalGetUser`

**描述**: 查询用户的 veBAL 锁仓和投票信息。

**GraphQL 示例**:
```graphql
{
  veBalGetUser(address: "0xYourWalletAddress") {
    balance
    rank
  }
}
```

## 速率限制

| 限制类型 | 限制值 | 说明 |
|---------|--------|------|
| GraphQL 请求 | 无严格公开限制 | 建议合理控制请求频率 |
| 复杂查询 | 可能被限流 | 避免一次查询过多数据 |
| Subgraph 查询 | The Graph 免费层有限制 | 高频查询建议使用付费计划 |

### 最佳实践

- 使用 `first` / `skip` 参数分页，避免一次拉取全量数据
- 缓存不常变动的数据（如 Token 列表）
- SOR 查询结果有约 5 分钟的缓存周期，动态数据不实时
- 生产环境建议使用自部署后端或 Subgraph

## WebSocket支持

Balancer API **不提供**原生 WebSocket 实时推送。

替代方案：
- **轮询 GraphQL API**: 定时查询行情和池子状态
- **监听链上事件**: 通过 Web3 Provider (如 ethers.js、web3.py) 订阅 Vault 合约的 Swap/PoolBalanceChanged 等事件
- **The Graph Subgraph**: 使用 Subgraph 的 subscription 功能（部分支持）

```python
# 监听 Balancer Vault 合约 Swap 事件示例（使用 web3.py）
from web3 import Web3

w3 = Web3(Web3.WebsocketProvider("wss://mainnet.infura.io/ws/v3/YOUR_KEY"))

# Balancer Vault 地址 (v2)
VAULT_ADDRESS = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

# Swap 事件 ABI
SWAP_EVENT_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "poolId", "type": "bytes32"},
        {"indexed": True, "name": "tokenIn", "type": "address"},
        {"indexed": True, "name": "tokenOut", "type": "address"},
        {"indexed": False, "name": "amountIn", "type": "uint256"},
        {"indexed": False, "name": "amountOut", "type": "uint256"}
    ],
    "name": "Swap",
    "type": "event"
}
```

## 错误处理

### GraphQL 错误格式

```json
{
  "errors": [
    {
      "message": "Pool not found",
      "locations": [{"line": 2, "column": 3}],
      "path": ["poolGetPool"],
      "extensions": {
        "code": "POOL_NOT_FOUND"
      }
    }
  ],
  "data": null
}
```

### 常见错误

| 错误 | 可能原因 | 处理建议 |
|------|---------|---------|
| Pool not found | 池子 ID 错误或不存在 | 检查 poolId 和 chain 参数 |
| Invalid chain | chain 枚举值错误 | 使用正确的枚举值（如 MAINNET） |
| Query too complex | 查询字段过多或嵌套过深 | 减少查询字段，分多次查询 |
| Rate limited | 请求频率过高 | 降低请求频率，添加缓存 |

## 代码示例

### Python 完整示例

```python
import requests
import json

API_URL = "https://api-v3.balancer.fi"

def graphql_query(query, variables=None):
    """执行 GraphQL 查询"""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    response = requests.post(
        API_URL,
        json=payload,
        headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    result = response.json()

    if "errors" in result:
        raise Exception(f"GraphQL Error: {result['errors']}")
    return result["data"]


def get_top_pools(chain="MAINNET", limit=10):
    """获取 TVL 排名前 N 的池子"""
    query = """
    query($chain: [GqlChain!], $limit: Int) {
      poolGetPools(
        first: $limit
        orderBy: totalLiquidity
        orderDirection: desc
        where: { chainIn: $chain }
      ) {
        id
        name
        type
        dynamicData {
          totalLiquidity
          volume24h
          fees24h
        }
      }
    }
    """
    variables = {"chain": [chain], "limit": limit}
    data = graphql_query(query, variables)
    return data["poolGetPools"]


def get_swap_path(token_in, token_out, amount, chain="MAINNET"):
    """查询最优交换路径"""
    query = """
    query($chain: GqlChain!, $amount: String!, $tokenIn: String!, $tokenOut: String!) {
      sorGetSwapPaths(
        chain: $chain
        swapAmount: $amount
        swapType: EXACT_IN
        tokenIn: $tokenIn
        tokenOut: $tokenOut
      ) {
        swapAmountRaw
        returnAmountRaw
        priceImpact {
          priceImpact
          error
        }
      }
    }
    """
    variables = {
        "chain": chain,
        "amount": str(amount),
        "tokenIn": token_in,
        "tokenOut": token_out
    }
    data = graphql_query(query, variables)
    return data["sorGetSwapPaths"]


def get_token_price(address, chain="MAINNET"):
    """获取 Token 价格"""
    query = """
    query($address: String!, $chain: GqlChain!) {
      tokenGetTokenDynamicData(address: $address, chain: $chain) {
        price
        priceChange24h
        marketCap
        volume24h
      }
    }
    """
    variables = {"address": address, "chain": chain}
    data = graphql_query(query, variables)
    return data["tokenGetTokenDynamicData"]


# ========== 使用示例 ==========

# 获取 Top 10 池子
pools = get_top_pools("MAINNET", 10)
for pool in pools:
    tvl = pool["dynamicData"]["totalLiquidity"]
    print(f"{pool['name']} ({pool['type']}): TVL ${float(tvl):,.2f}")

# 查询 1 WETH -> USDC 的最优路径
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
swap = get_swap_path(WETH, USDC, "1", "MAINNET")
print(f"Swap 1 WETH -> USDC: {swap['returnAmountRaw']} raw units")
print(f"Price impact: {swap['priceImpact']['priceImpact']}")

# 获取 BAL Token 价格
BAL = "0xba100000625a3754423978a60c9317c58a424e3d"
price = get_token_price(BAL, "MAINNET")
print(f"BAL price: ${price['price']}")
```

## 智能合约交互

对于链上操作（Swap、Add/Remove Liquidity），需要直接调用 Balancer 智能合约：

### 核心合约地址 (Ethereum Mainnet)

| 合约 | 地址 | 说明 |
|------|------|------|
| Vault (v2) | `0xBA12222222228d8Ba445958a75a0704d566BF2C8` | 核心 Vault，管理所有池子资产 |
| BalancerQueries | `0xE39B5e3B6D74016b2F6A9673D7d7493B6DF549d5` | 只读查询合约 |
| BAL Token | `0xba100000625a3754423978a60c9317c58a424e3d` | 治理代币 |

### Python Web3 Swap 示例

```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://mainnet.infura.io/v3/YOUR_KEY"))

VAULT_ADDRESS = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

# Vault ABI（仅 swap 函数）
VAULT_ABI_SWAP = [{
    "inputs": [
        {"name": "singleSwap", "type": "tuple", "components": [
            {"name": "poolId", "type": "bytes32"},
            {"name": "kind", "type": "uint8"},
            {"name": "assetIn", "type": "address"},
            {"name": "assetOut", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "userData", "type": "bytes"}
        ]},
        {"name": "funds", "type": "tuple", "components": [
            {"name": "sender", "type": "address"},
            {"name": "fromInternalBalance", "type": "bool"},
            {"name": "recipient", "type": "address"},
            {"name": "toInternalBalance", "type": "bool"}
        ]},
        {"name": "limit", "type": "uint256"},
        {"name": "deadline", "type": "uint256"}
    ],
    "name": "swap",
    "outputs": [{"name": "amountCalculated", "type": "uint256"}],
    "stateMutability": "payable",
    "type": "function"
}]

vault = w3.eth.contract(address=VAULT_ADDRESS, abi=VAULT_ABI_SWAP)
# 构建交易需要：poolId, tokenIn, tokenOut, amount, 钱包私钥签名
```

## 变更历史

### 2026-02-27
- 完善文档，添加详细 GraphQL 查询示例
- 添加 SOR 智能订单路由文档
- 添加用户数据查询文档
- 添加智能合约交互示例
- 添加 Python 完整代码示例

---

## 相关资源

- [Balancer 官方文档](https://docs.balancer.fi/)
- [Balancer API GraphQL Playground](https://api-v3.balancer.fi)
- [Balancer GitHub](https://github.com/balancer)
- [Balancer SDK](https://github.com/balancer/balancer-sdk)
- [Balancer Subgraph](https://docs.balancer.fi/data-and-analytics/data-and-analytics/subgraph.html)
- [Balancer Telegram (API Updates)](https://t.me/BalancerAPI)

---

*本文档由 bt_api_py 项目维护，内容基于 Balancer 官方 API 文档整理。*
