# Curve API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-02-27
- 官方文档: <https://api.curve.finance/v1/documentation/>
- 技术文档: <https://docs.curve.finance/>
- 数据来源: 官方 Swagger API + 文档

## 交易所基本信息

- 官方名称: Curve Finance
- 官网: <https://curve.fi>
- 交易所类型: DEX (去中心化交易所)
- 交易量排名: DEX #6
- 区块链: Ethereum, Arbitrum, Optimism, Polygon, Avalanche, Base, BSC, 等多链
- 特点: 专注于稳定币和同类资产的低滑点交换, AMM (Automated Market Maker)
- 治理代币: CRV / veCRV

## API 基础 URL

| 端点类型 | URL |

|---------|-----|

| REST API | `<https://api.curve.finance`> |

| 前端应用 | `<https://curve.fi`> |

> **注意**: Curve 是 DEX，交易通过链上智能合约执行。REST API 仅提供只读数据查询，不执行交易。

## REST API (只读, 无需认证)

Curve 提供 Swagger 文档化的 REST API，主要用于查询链上数据。

### 常用端点

| 端点 | 描述 |

|------|------|

| /v1/getPools/{chain}/{factory} | 获取池子列表 |

| /v1/getVolumes/{chain} | 获取交易量 |

| /v1/getTVL/{chain} | 获取 TVL |

| /v1/getAPYs/{chain} | 获取 APY |

| /v1/getGauges/{chain} | 获取 Gauge 信息 |

| /v1/getCrvCircSupply | CRV 流通供应量 |

### 支持的链

`ethereum`, `arbitrum`, `optimism`, `polygon`, `avalanche`, `base`, `bsc`, `fantom`, `celo`, `gnosis`, `moonbeam`, `kava`, `aurora`, `fraxtal`, `xlayer`

```python
import requests

BASE = "<https://api.curve.finance">

# 获取 Ethereum 上的池子

resp = requests.get(f"{BASE}/v1/getPools/ethereum/main")
data = resp.json()
for pool in data.get("data", {}).get("poolData", [])[:5]:
    print(f"Pool: {pool.get('name')}, TVL: {pool.get('usdTotal')}")

# 获取 TVL

resp = requests.get(f"{BASE}/v1/getTVL/ethereum")
print(f"Ethereum TVL: {resp.json()}")

# 获取交易量

resp = requests.get(f"{BASE}/v1/getVolumes/ethereum")

```bash

## 链上交易 (通过智能合约)

Curve 的交易通过直接与智能合约交互完成:

### 核心合约方法

| 方法 | 描述 |

|------|------|

| `exchange(i, j, dx, min_dy)` | 池内交换 |

| `get_dy(i, j, dx)` | 估算兑换数量 |

| `add_liquidity(amounts, min_mint_amount)` | 添加流动性 |

| `remove_liquidity(amount, min_amounts)` | 移除流动性 |

| `get_virtual_price()` | 获取虚拟价格 |

### Python 链上交易示例

```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("<https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY"))>

# 3pool (DAI/USDC/USDT) 合约

POOL_ADDRESS = "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7"
POOL_ABI = [...]  # 从 Etherscan 获取

pool = w3.eth.contract(address=POOL_ADDRESS, abi=POOL_ABI)

# 估算: 1000 USDC -> USDT

dy = pool.functions.get_dy(1, 2, 1000 * 10**6).call()
print(f"1000 USDC -> {dy / 10**6} USDT")

# 执行交换 (需签名交易)

# tx = pool.functions.exchange(1, 2, amount, min_dy).build_transaction({...})

```bash

## Curve Router

对于跨池交换，使用 Curve Router 合约:

```python

# Curve Router 地址 (Ethereum)

ROUTER = "0xF0d4c12A5768D806021F80a262B4d39d26C58b8D"

```bash

## 速率限制

| 类别 | 限制 |

|------|------|

| REST API | 无明确公开限制，建议合理调用 |

| 链上交易 | 受区块链 Gas 限制 |

## 变更历史

### 2026-02-27

- 基于官方 API 文档和技术文档完善

- --

## 相关资源

- [Curve API Swagger](<https://api.curve.finance/v1/documentation/)>
- [Curve 技术文档](<https://docs.curve.finance/)>
- [Curve GitHub](<https://github.com/curvefi)>

- --

- 本文档由 bt_api_py 项目维护。*
