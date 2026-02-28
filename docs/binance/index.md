# Binance API 文档

Binance（币安）是全球最大的加密货币交易所之一，bt_api_py 提供了对 Binance 现货和合约交易的完整支持。

## 支持的市场

| 市场 | 说明 | 状态 |

|------|------|------|

| **SPOT**| 现货交易 | ✅ 完全支持 |

|**MARGIN**| 杠杆交易 | ✅ 支持 |

|**USDT-M**| USDT 本位永续合约 | ✅ 支持 |

|**COIN-M**| 币本位永续/交割合约 | ✅ 支持 |

## API 类型

| API 类型 | 说明 | 文档 |

|----------|------|------|

|**REST API**| 同步 HTTP 请求 | [查看](spot/rest-api.md) |

|**WebSocket Streams**| 行情数据推送 | [查看](spot/websocket-streams.md) |

|**WebSocket API**| 交易与账户数据 | [查看](spot/websocket-api.md) |

|**User Data Stream** | 账户事件推送 | [查看](spot/user-data-stream.md) |

## 快速开始

```python
from bt_api_py import BtApi

# Binance 现货

api = BtApi(
    exchange='binance',
    market='spot',
    api_key='your_api_key',
    secret='your_secret'
)

# Binance 合约

api_swap = BtApi(
    exchange='binance',
    market='swap',
    api_key='your_api_key',
    secret='your_secret'
)

```bash

## 相关文档

- [API 实现状态](../binance_api_implementation_status.md)
- [待实现 API](../binance_api_missing_apis.md)
- [开发计划](../binance_api_todo.md)
