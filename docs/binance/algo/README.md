# Binance Algo Trading API 文档

> 来源: [Binance Algo Trading API](<https://developers.binance.com/docs/algo)>
>
> 最后更新: 2026-02-26

本目录包含 Binance Algo Trading API 的接口文档，方便后续更新和参考。

## 文档索引

| 文件 | 说明 |

|------|------|

| `change-log.md` | 变更日志 |

| [quick-start.md](./quick-start.md) | 快速开始指南 |

| `introduction.md` | Algo Trading 介绍 |

| `general-info.md` | 通用信息（Base URL、限流、签名等） |

| `error-code.md` | 错误码和错误信息 |

| `contact-us.md` | 联系方式 |

## Future Algo 接口

仅支持 USDⓈ-M 合约。

| 文件 | 接口 | 方法 | 说明 |

|------|------|------|------|

| `vp-new-order.md` | `/sapi/v1/algo/futures/newOrderVp` | POST | VP(Volume Participation) 下单 |

| [twap-new-order.md](./future-algo/twap-new-order.md) | `/sapi/v1/algo/futures/newOrderTwap` | POST | TWAP(时间加权均价) 下单 |

| `cancel-algo-order.md` | `/sapi/v1/algo/futures/order` | DELETE | 取消 Algo 订单 |

| `query-sub-orders.md` | `/sapi/v1/algo/futures/subOrders` | GET | 查询子订单 |

| `query-current-open-orders.md` | `/sapi/v1/algo/futures/openOrders` | GET | 查询当前活跃 Algo 订单 |

| `query-historical-orders.md` | `/sapi/v1/algo/futures/historicalOrders` | GET | 查询历史 Algo 订单 |

## Spot Algo 接口

| 文件 | 接口 | 方法 | 说明 |

|------|------|------|------|

| [fee-structure.md](./spot-algo/fee-structure.md) | - | - | Algo 手续费结构 |

| `twap-new-order.md` | `/sapi/v1/algo/spot/newOrderTwap` | POST | TWAP(时间加权均价) 下单 |

| `cancel-algo-order.md` | `/sapi/v1/algo/spot/order` | DELETE | 取消 Algo 订单 |

| `query-sub-orders.md` | `/sapi/v1/algo/spot/subOrders` | GET | 查询子订单 |

| `query-current-open-orders.md` | `/sapi/v1/algo/spot/openOrders` | GET | 查询当前活跃 Algo 订单 |

| `query-historical-orders.md` | `/sapi/v1/algo/spot/historicalOrders` | GET | 查询历史 Algo 订单 |

## Base URL

- **<https://api.binance.com**>

## 认证方式

所有交易和查询接口均需要 API Key 和签名（SIGNED），支持 HMAC、RSA 和 Ed25519。

| 安全类型 | 说明 |

|----------|------|

| `TRADE` | 交易相关，下单/撤单 |

| `USER_DATA` | 私有账户信息，如订单状态、交易历史 |
