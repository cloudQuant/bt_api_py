# bt_api_py 文档

- *bt_api_py**是一个统一的多交易所交易 API 框架，支持现货、合约、期货等多种交易类型，提供同步、异步和 WebSocket 三种接口模式。

[![Python 3.11+](<https://img.shields.io/badge/python-3.11%2B-blue.svg)](<https://www.python.org/downloads/>)>
[![License: MIT](<https://img.shields.io/badge/License-MIT-green.svg)](<https://opensource.org/licenses/MIT>)>
[![PyPI](<https://img.shields.io/pypi/v/bt_api_py.svg)](<https://pypi.org/project/bt_api_py/>)>
[![Documentation](<https://img.shields.io/badge/docs-latest-blue.svg)](<https://cloudquant.github.io/bt_api_py/>)>

## 核心特性

| 特性 | 说明 |

|------|------|

|**多交易所统一接口**| 通过 `BtApi` 类统一管理 Binance、OKX、CTP、Interactive Brokers 等交易所 |

|**三种 API 模式**| 同步 REST、异步 REST、WebSocket 实时推送 |

|**即插即用架构**| 基于 Registry 模式，新增交易所无需修改核心代码 |

|**事件驱动**| 内置 EventBus 事件总线，支持回调模式 |

|**标准化数据**| 统一的 Ticker、OrderBook、Bar、Order、Position 等数据类型 |

|**跨平台支持**| Linux、Windows、macOS |

## 支持的交易所

| 交易所 | 现货 (SPOT) | 合约 (SWAP) | 期货 (FUTURE) | 股票 (STK) |

|--------|:-----------:|:-----------:|:-------------:|:----------:|

|**Binance**| ✅ | ✅ | — | — |

|**OKX**| ✅ | ✅ | — | — |

|**CTP**(中国期货) | — | — | ✅ | — |

|**Interactive Brokers**| — | — | — | ✅ |

## 快速链接

- [安装指南](installation.md) - 如何安装和配置
- [快速入门](quickstart.md) - 5 分钟快速上手
- [架构设计](architecture.md) - 核心架构和设计模式
- [使用指南](usage_guide.md) - 完整使用教程和代码示例
- [开发者指南](developer_guide.md) - 如何扩展和贡献代码
- [API 参考文档](binance/spot/) - 各交易所 API 详细参考

## 示例代码

```python
from bt_api_py import BtApi

# 创建 API 实例

api = BtApi(
    exchange='binance',
    market='spot',
    api_key='your_api_key',
    secret='your_secret',
    testnet=True
)

# 获取行情

ticker = api.get_ticker('BTCUSDT')
print(f"BTC 价格: {ticker.last_price}")

# 下单

order = api.limit_order(
    symbol='BTCUSDT',
    side='buy',
    quantity=0.001,
    price=50000
)

```bash

## 项目信息

- **GitHub**: <https://github.com/cloudQuant/bt_api_py>
- **PyPI**: <https://pypi.org/project/bt_api_py/>
- **License**: MIT

- --

!!! tip "提示"
    建议从 [快速入门](quickstart.md) 开始阅读文档。
