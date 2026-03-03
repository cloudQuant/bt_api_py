# Ourbit API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04

## 交易所基本信息

- 官方名称: Ourbit
- 官网: <https://www.ourbit.com>
- CMC 衍生品排名: #33
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual Futures)
- 最大杠杆: 200x

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.ourbit.com` | 主端点 |
| WebSocket | `wss://ws.ourbit.com/ws` | 行情流 |

## 认证方式

- HMAC SHA256 签名
- API Key + Secret Key + 时间戳

## 市场数据 API

### 标准接口
- 获取交易对列表 / Ticker / 深度 / K 线 / 成交

## 交易 API

### 标准接口
- 下单 / 撤单 / 查询余额 / 查询持仓

## 特殊说明

- 公开 API 文档有限，建议联系官方获取详细信息

## 相关资源

- [Ourbit 官网](https://www.ourbit.com)
