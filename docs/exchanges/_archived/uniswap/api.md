# Uniswap API 文档

## 交易所信息

- **交易所名称**: Uniswap
- **官方网站**: <https://uniswap.org>
- **API 文档**: <https://api-docs.uniswap.org/>
- **开发者门户**: <https://developers.uniswap.org>
- **24h 交易量排名**: #1（DEX）
- **区块链**: 多链（详见官方 Supported Chains）
- **API 版本**: Uniswap Labs Trading API

## API 基础信息

### 基础 URL

```text

# Trading API

<https://trade-api.gateway.uniswap.org/v1>

# Beta

<https://beta.trade-api.gateway.uniswap.org/v1>

```bash

### 认证方式

- 所有请求需要 `x-api-key` 头

```text
x-api-key: {api_key}
Content-Type: application/json

```bash

## 交易/报价 API（Swapping）

### 1. Swapping Endpoints

- `POST /quote`  获取报价（Swap/Bridge/Wrap/Unwrap）
- `POST /check_approval`  检查 ERC20 授权
- `POST /limit_order_quote`  获取限价单报价

### 2. UniswapX（Gasless Order）

- `POST /order`  提交 Gasless Order（UniswapX）
- `GET /orders`  查询 Gasless Order 状态/列表

### 3. Protocol Swapping

- `POST /swap`  生成 swap calldata
- `POST /swap_5792`  生成 EIP-5792 批量调用 calldata
- `POST /swap_7702`  生成 EIP-7702 delegated swap calldata
- `GET /swaps`  查询 swap/bridge 状态

## 流动性 API（Liquidity Provisioning）

### 1. 授权与资金检查

- `POST /lp/approve`  检查代币/Permit 授权，并在需要时返回授权交易 calldata

### 2. 创建与管理头寸

- `POST /lp/create`  创建池子与 LP 头寸
- `POST /lp/increase`  增加 LP 头寸
- `POST /lp/decrease`  减少/移除 LP 头寸
- `POST /lp/claim`  领取 LP 手续费
- `POST /lp/claim_rewards`  领取 LP 奖励
- `POST /lp/migrate`  迁移 LP 头寸（例如 V3 -> V4）

## 参考数据 API（Reference Data）

- `GET /swappable_tokens`  查询可跨链桥接的 Token 列表

## 工具 API（Utility）

- `POST /send`  创建转账 calldata
- `POST /wallet/check_delegation`  查询钱包 Delegation 信息
- `POST /wallet/encode_7702`  编码 EIP-7702 交易列表

## 速率限制

- 以官方文档为准

## 错误代码

- 以官方文档为准

## 代码示例

```python

# 获取报价

import requests

url = "<https://trade-api.gateway.uniswap.org/v1/quote">
headers = {"x-api-key": "YOUR_KEY", "Content-Type": "application/json"}
body = {
    "tokenIn": "0x0000000000000000000000000000000000000000",
    "tokenOut": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "tokenInChainId": 1,
    "tokenOutChainId": 1,
    "type": "EXACT_INPUT",
    "amount": "1000000000000000000",
    "swapper": "0x...",
    "slippageTolerance": 0.5
}
print(requests.post(url, json=body, headers=headers).json())

```bash
