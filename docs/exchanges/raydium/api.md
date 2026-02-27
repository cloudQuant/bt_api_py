# Raydium API 文档

## 交易所信息

- **交易所名称**: Raydium
- **官方网站**: https://raydium.io
- **API文档**: https://docs.raydium.io/raydium/for-developers/api
- **API 文档（Swagger）**: https://api-v3.raydium.io/docs/
- **24h交易量排名**: #3（DEX）
- **区块链**: Solana

## API基础信息

### 基础URL

```text
https://api-v3.raydium.io
```

## 端点分类

- **MAIN**: Main API
- **MINT**: Mint Info
- **POOLS**: Pools Info And Keys
- **FARMS**: Farms Info And Keys
- **IDO**: IDO Pool Info

## 端点清单

### MAIN

- `GET /main/auto-fee`  transaction auto fee
- `GET /main/chain-time`  Chain Time
- `GET /main/clmm-config`  Clmm Config
- `GET /main/cpmm-config`  Cpmm Config
- `GET /main/info`  TVL and 24 hour volume
- `GET /main/migrate-lp`  Migrate Lp Pool List
- `GET /main/mint-filter-config`  Mint Filter Config
- `GET /main/rpcs`  UI RPCS
- `GET /main/stake-pools`  RAY Stake
- `GET /main/version`  UI V3 current version

### MINT

- `GET /mint/ids`  Mint Info
- `GET /mint/list`  Default Mint List
- `GET /mint/price`  Mint Price

### POOLS

- `GET /pools/info/ids`  Pool Info
- `GET /pools/info/list`  Pool Info List
- `GET /pools/info/list-v2`  Pool Info List V2
- `GET /pools/info/lps`  Pool Info By Lp Mint
- `GET /pools/info/mint`  Pool Info By Token Mint
- `GET /pools/key/ids`  Pool Key
- `GET /pools/line/liquidity`  Pool Liquidity history
- `GET /pools/line/position`  Clmm Position

### FARMS

- `GET /farms/info/ids`  Farm Pool Info
- `GET /farms/info/lp`  Search Farm By Lp Mint
- `GET /farms/key/ids`  Farm Pool Key

### IDO

- `GET /ido/key/ids`  Ido Pool Keys

## 速率限制

- 以官方文档为准

## WebSocket支持

- 官方文档未提供 WebSocket API 说明（更多实时能力建议通过 SDK + gRPC）

## 错误代码

- 以官方文档为准

## 代码示例

```python
# 获取池子列表
import requests

url = "https://api-v3.raydium.io/pools/info/list"
params = {"poolType": "all", "poolSortField": "default", "sortType": "desc", "pageSize": 10, "page": 1}
print(requests.get(url, params=params).json())
```
