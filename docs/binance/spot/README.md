# Binance Spot API 文档

> 来源: [binance/binance-spot-api-docs](<https://github.com/binance/binance-spot-api-docs)> (master branch)
>
> 最后更新: 2026-02-26

本目录包含 Binance Spot API 的接口文档，方便后续更新和参考。

## 文档索引

| 文件 | 说明 |

|------|------|

| [rest-api.md](./rest-api.md) | Spot REST API (`/api`) - 包含所有 REST 接口 |

| [websocket-api.md](./websocket-api.md) | Spot WebSocket API - WebSocket 方式调用 API |

| [websocket-streams.md](./websocket-streams.md) | Spot 行情 WebSocket 数据流 |

| [user-data-stream.md](./user-data-stream.md) | 用户数据 WebSocket 数据流 |

| [enums.md](./enums.md) | REST 和 WebSocket API 使用的枚举定义 |

| [errors.md](./errors.md) | 错误码和错误信息 |

| [filters.md](./filters.md) | 交易过滤器规则 |

| [sbe-market-data-streams.md](./sbe-market-data-streams.md) | SBE 行情数据流 |

## Base Endpoints

### REST API

- **<https://api.binance.com**>
- **<https://api-gcp.binance.com**>
- **<https://api1.binance.com**~**<https://api4.binance.com*>*>
- 纯行情数据: **<https://data-api.binance.vision**>

### WebSocket Streams

- **wss://stream.binance.com:9443**
- **wss://stream.binance.com:443**
- 纯行情数据: **wss://data-stream.binance.vision**

### WebSocket API

- **wss://ws-api.binance.com:443/ws-api/v3**

## 认证方式

支持 HMAC、RSA 和 Ed25519 密钥。

| 安全类型 | 说明 |

|----------|------|

| `NONE` | 公开行情数据 |

| `TRADE` | 交易相关，下单/撤单 |

| `USER_DATA` | 私有账户信息，如订单状态、交易历史 |

| `USER_STREAM` | 管理用户数据流订阅 |

## 相关资源

- [官方 Python Connector](<https://github.com/binance/binance-connector-python)>
- [Postman Collections](<https://github.com/binance/binance-api-postman)>
- [Swagger/OpenAPI](<https://github.com/binance/binance-api-swagger)>
- [Spot Testnet](<https://testnet.binance.vision/)>
- [API 公告频道](<https://t.me/binance_api_announcements)>
