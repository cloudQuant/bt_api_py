# CoW Swap API 文档

## 交易所信息

- **交易所名称**: CoW Swap (CoW Protocol)
- **官方网站**: https://cow.fi
- **API文档**: https://docs.cow.fi/
- **24h交易量排名**: #10（DEX）
- **区块链**: 多链

## API基础信息

### Orderbook API (OpenAPI)

OpenAPI 定义位于 CoW Protocol Services 仓库：

```text
https://raw.githubusercontent.com/cowprotocol/services/main/crates/orderbook/openapi.yml
```

### Base URLs（按链）

- Mainnet: `https://api.cow.fi/mainnet`
- Mainnet (Staging): `https://barn.api.cow.fi/mainnet`
- Gnosis Chain: `https://api.cow.fi/xdai`
- Gnosis Chain (Staging): `https://barn.api.cow.fi/xdai`
- Arbitrum One: `https://api.cow.fi/arbitrum_one`
- Arbitrum One (Staging): `https://barn.api.cow.fi/arbitrum_one`
- Base: `https://api.cow.fi/base`
- Base (Staging): `https://barn.api.cow.fi/base`
- Avalanche: `https://api.cow.fi/avalanche`
- Avalanche (Staging): `https://barn.api.cow.fi/avalanche`
- Polygon: `https://api.cow.fi/polygon`
- Polygon (Staging): `https://barn.api.cow.fi/polygon`
- Lens: `https://api.cow.fi/lens`
- Lens (Staging): `https://barn.api.cow.fi/lens`
- BNB: `https://api.cow.fi/bnb`
- BNB (Staging): `https://barn.api.cow.fi/bnb`
- Sepolia: `https://api.cow.fi/sepolia`
- Sepolia (Staging): `https://barn.api.cow.fi/sepolia`

## 核心端点（OpenAPI 摘要）

### 订单

- `POST /api/v1/orders`  创建订单（支持 replacement order UID）
- `DELETE /api/v1/orders`  批量撤单（EIP-712 签名）
- `GET /api/v1/orders/{UID}`  查询订单
- `DELETE /api/v1/orders/{UID}`  撤单（Deprecated）
- `GET /api/v1/orders/{UID}/status`  订单状态
- `GET /api/v1/account/{owner}/orders`  用户订单列表（分页）

### 交易与撮合

- `GET /api/v1/transactions/{txHash}/orders`  根据成交交易哈希查询订单
- `GET /api/v1/trades`  交易记录（Deprecated）
- `GET /api/v2/trades`  交易记录（分页）
- `GET /api/v1/auction`  当前批次竞价（权限接口）

### 报价

- `POST /api/v1/quote`  报价与费用估算

### Solver 竞赛

- `GET /api/v1/solver_competition/{auction_id}`  竞赛信息（Deprecated）
- `GET /api/v1/solver_competition/by_tx_hash/{tx_hash}`  竞赛信息（Deprecated）
- `GET /api/v1/solver_competition/latest`  最近竞赛（Deprecated）
- `GET /api/v2/solver_competition/{auction_id}`  竞赛信息
- `GET /api/v2/solver_competition/by_tx_hash/{tx_hash}`  竞赛信息
- `GET /api/v2/solver_competition/latest`  最近竞赛

### Token 与 AppData

- `GET /api/v1/token/{token}/native_price`  token 对原生币报价
- `GET /api/v1/app_data/{app_data_hash}`  获取完整 appData
- `PUT /api/v1/app_data/{app_data_hash}`  注册完整 appData
- `PUT /api/v1/app_data`  注册 appData 并返回 hash

### 版本

- `GET /api/v1/version`  获取 API 版本信息

## 代码示例

```python
# 获取订单状态
import requests

base = "https://api.cow.fi/mainnet"
uid = "0x..."
url = f"{base}/api/v1/orders/{uid}/status"
print(requests.get(url).json())
```
