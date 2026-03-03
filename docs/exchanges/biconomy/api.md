# Biconomy.com API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04

## 交易所基本信息

- 官方名称: Biconomy.com
- 官网: <https://www.biconomy.com>
- CMC 衍生品排名: #26
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual Futures)
- 注意: 非 DeFi 项目 Biconomy (BICO Token)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://www.biconomy.com/api` | 主端点 |
| WebSocket | `wss://ws.biconomy.com/ws` | 行情流 |

## 认证方式

### HMAC SHA256 签名

- API Key + Secret Key
- 时间戳签名验证

## 市场数据 API

### 1. 获取交易对列表
- **端点**: `GET /api/v1/exchangeInfo`

### 2. 获取 Ticker
- **端点**: `GET /api/v1/ticker/24hr`
- **参数**: `symbol`

### 3. 获取深度
- **端点**: `GET /api/v1/depth`
- **参数**: `symbol`, `limit`

### 4. 获取 K 线
- **端点**: `GET /api/v1/klines`
- **参数**: `symbol`, `interval`, `limit`

### 5. 获取成交
- **端点**: `GET /api/v1/trades`
- **参数**: `symbol`, `limit`

## 交易 API

### 1. 下单
- **端点**: `POST /api/v1/order`
- **参数**: `symbol`, `side`, `type`, `quantity`, `price`

### 2. 撤单
- **端点**: `DELETE /api/v1/order`

### 3. 查询余额
- **端点**: `GET /api/v1/account`

## 特殊说明

- API 风格类似 Binance
- 注意区分与 DeFi Biconomy (BICO) 项目
- 公开 API 文档有限，建议联系官方

## 相关资源

- [Biconomy.com 官网](https://www.biconomy.com)
