# Hyperliquid API 文档

## 交易所信息

- **交易所名称**: Hyperliquid
- **官方网站**: https://hyperliquid.xyz
- **API文档**: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
- **24h交易量排名**: #2（DEX）
- **支持的市场**: Perps / Spot（详见官方文档）

## API基础信息

### 基础URL

```text
# Mainnet
https://api.hyperliquid.xyz

# Testnet
https://api.hyperliquid-testnet.xyz
```

### 主要 REST 端点

- `POST /info`  市场与账户查询（根据请求体 `type` 不同返回不同信息）
- `POST /exchange`  交易/账户操作（需签名）

### WebSocket

```text
wss://api.hyperliquid.xyz/ws
wss://api.hyperliquid-testnet.xyz/ws
```

## 认证方式

- 交易相关接口需要签名
- 官方文档提供 Python SDK 示例

## Info Endpoint 说明（官方）

- 时间范围查询最多返回 500 条；可用最后时间戳作为下一次请求的 `startTime` 分页
- Perps 与 Spot 的 `coin` 规则不同：
  - Perps：使用 `meta` 返回的 `coin`
  - Spot：`PURR/USDC` 用对名，其他 Spot token 用 `@{index}`（`spotMeta.universe` 中对应索引）

## 常用 Info 请求类型（示例）

- 市场数据：`allMids`、`l2Book`、`candleSnapshot`、`exchangeStatus`
- 账户与订单：`clearinghouseState`、`spotClearinghouseState`、`orderStatus`
- 其他：`userRole`

### candleSnapshot 说明

- 仅提供最近 5000 根 K 线
- 支持周期：`1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 12h, 1d, 3d, 1w, 1M`

## 常用 Exchange 操作（示例）

- `order` / `cancel` / `modify`
- `transfer` / `withdraw`
- 其他类型详见官方文档

## Exchange Endpoint 说明（官方）

- `asset`（Perps）：`meta.universe` 中的索引
- `asset`（Spot）：`10000 + spotMeta.universe` 中的索引
- 子账户与 Vault 需由主账户签名，并设置 `vaultAddress`
- 部分操作支持 `expiresAfter`（毫秒时间戳）

## 速率限制（官方）

- REST 请求总权重：1200/分钟/IP
- `info` 请求权重示例：`l2Book, allMids, clearinghouseState, orderStatus, spotClearinghouseState, exchangeStatus` 为 2；`userRole` 为 60；其他多数为 20
- `candleSnapshot` 会按返回条数增加权重
- WebSocket：最多 10 个连接、30 次/分钟新连接、1000 订阅、2000 消息/分钟
- 地址级限额：默认 10000 初始 buffer；开放订单数默认 1000，随成交增加上限（上限 5000）

## 错误代码

- 详见官方文档

## 代码示例

```python
# 获取市场信息（allMids）
import requests

url = "https://api.hyperliquid.xyz/info"
body = {"type": "allMids"}
print(requests.post(url, json=body).json())
```
