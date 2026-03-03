# WHXEX API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04

## 交易所基本信息

- 官方名称: WHXEX
- 官网: <https://www.whxex.com>
- CMC 衍生品排名: #99
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual Futures)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.whxex.com` | 主端点(待确认) |
| WebSocket | `wss://ws.whxex.com` | 行情流(待确认) |

## 认证方式

- HMAC SHA256 签名(推测)

## 市场数据 API / 交易 API

### 标准接口(待确认)
- 获取交易对列表 / Ticker / 深度 / K 线 / 成交
- 下单 / 撤单 / 查询余额 / 查询持仓

## 特殊说明

- 小型交易所，公开 API 文档有限，建议优先级: 低

## 相关资源

- [WHXEX 官网](https://www.whxex.com)
