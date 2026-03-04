# Binance Derivatives API 文档

> 来源: [bigsongeth/binance_derivatives_api](<https://github.com/bigsongeth/binance_derivatives_api)> + [Binance 官方文档](<https://developers.binance.com/docs/derivatives)>
>
> 最后更新: 2026-02-26

本目录包含 Binance 衍生品 (Derivatives) API 的接口文档，方便后续更新和参考。

## 文档索引

| 目录 | 说明 |

|------|------|

| [usds-margined-futures/](./usds-margined-futures/) | USDⓈ-M 合约 (`/fapi`) - U 本位永续/交割合约 |

| [coin-margined-futures/](./coin-margined-futures/) | COIN-M 合约 (`/dapi`) - 币本位永续/交割合约 |

| [option/](./option/) | 欧式期权 (`/eapi`) |

| [portfolio-margin/](./portfolio-margin/) | 投资组合保证金 (`/papi`) |

| [portfolio-margin-pro/](./portfolio-margin-pro/) | 专业投资组合保证金 |

| [futures-data/](./futures-data/) | 合约历史数据接口 |

| [change-log.md](./change-log.md) | API 变更日志 |

| [quick-start.md](./quick-start.md) | 快速开始指南 |

## 各模块子目录结构

每个产品目录下包含以下子目录/文件：

| 子目录/文件 | 说明 |

|-------------|------|

| `general-info.md` | 基本信息、Base URL、签名方式、频率限制 |

| `common-definition.md` | 枚举定义、公共参数 |

| `error-code.md` | 错误码 |

| `market-data/` | 行情数据接口 (REST + WebSocket API) |

| `trade/` | 交易接口 (下单/撤单/改单等) |

| `account/` | 账户信息接口 (余额/持仓/收入历史等) |

| `user-data-streams/` | 用户数据 WebSocket 流 |

| `websocket-market-streams/` | 行情 WebSocket 数据流 |

## Base Endpoints

### USDⓈ-M 合约

- **REST API**: `<https://fapi.binance.com`>
- **WebSocket**: `wss://fstream.binance.com`
- **Testnet REST**: `<https://testnet.binancefuture.com`>
- **Testnet WS**: `wss://fstream.binancefuture.com`

### COIN-M 合约

- **REST API**: `<https://dapi.binance.com`>
- **WebSocket**: `wss://dstream.binance.com`
- **Testnet REST**: `<https://testnet.binancefuture.com`>
- **Testnet WS**: `wss://dstream.binancefuture.com`

### 欧式期权

- **REST API**: `<https://eapi.binance.com`>
- **WebSocket**: `wss://nbstream.binance.com/eoptions`

### 投资组合保证金

- **REST API**: `<https://papi.binance.com`>

## 认证方式

支持 HMAC-SHA256、RSA 和 Ed25519 密钥签名。

| 安全类型 | 说明 |

|----------|------|

| `NONE` | 公开行情数据 |

| `TRADE` | 交易相关，下单/撤单 |

| `USER_DATA` | 私有账户信息 |

| `USER_STREAM` | 管理用户数据流订阅 |

## 相关资源

- [官方 Futures Python Connector](<https://github.com/binance/binance-futures-connector-python)>
- [官方 API 文档](<https://developers.binance.com/docs/derivatives)>
- [Spot API 文档](../spot/)
- [Futures Testnet](<https://testnet.binancefuture.com/)>
- [API 公告频道](<https://t.me/binance_api_announcements)>
