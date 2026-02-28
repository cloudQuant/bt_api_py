# bt_api_py

[![Python 3.11+](<https://img.shields.io/badge/python-3.11%2B-blue.svg)](<https://www.python.org/downloads/>)>
[![License: MIT](<https://img.shields.io/badge/License-MIT-green.svg)](<https://opensource.org/licenses/MIT>)>
[![PyPI](<https://img.shields.io/pypi/v/bt_api_py.svg)](<https://pypi.org/project/bt_api_py/>)>
[![Documentation](<https://img.shields.io/badge/docs-latest-blue.svg)](<https://cloudquant.github.io/bt_api_py/>)>

- *bt_api_py**是一个统一的多交易所交易 API 框架，支持现货、合约、期货、股票等多种交易类型，提供同步、异步和 WebSocket 三种接口模式。

:books:**[在线文档](<https://cloudquant.github.io/bt_api_py/)**|> :rocket: [快速开始](<https://cloudquant.github.io/bt_api_py/quickstart/)> | [English](README.en.md) | 中文

- --

## 特性

- **多交易所统一接口**— 通过 `BtApi` 类统一管理 Binance、OKX、CTP(中国期货)、Interactive Brokers 等交易所
- **三种 API 模式**— 同步 REST、异步 REST、WebSocket 实时推送
- **即插即用架构**— 基于 Registry 模式，新增交易所无需修改核心代码
- **事件驱动**— 内置 EventBus 事件总线，支持回调模式
- **丰富的数据容器**— 标准化的 Ticker、OrderBook、Bar、Order、Position 等 20+ 种数据类型
- **跨平台**— 支持 Linux (x86_64)、Windows (x64)、macOS (arm64/x86_64)

## 支持的交易所

| 交易所 | 代码 | 现货 (SPOT) | 合约 (SWAP/FUTURE) | 股票 (STK) | 状态 |

|--------|------|:-----------:|:------------------:|:----------:|:----:|

|**Binance**| `BINANCE___SPOT` / `BINANCE___SWAP` | ✅ | ✅ | — | ✅ |

|**OKX**| `OKX___SPOT` / `OKX___SWAP` | ✅ | ✅ | — | ✅ |

|**CTP**(中国期货) | `CTP___FUTURE` | — | ✅ | — | ✅ |

|**Interactive Brokers** | `IB_WEB___STK` / `IB_WEB___FUT` | — | — | ✅ | ✅ |

## 安装

### 从 PyPI 安装

```bash
pip install bt_api_py

```bash

### 从源码安装

```bash
git clone <https://github.com/cloudQuant/bt_api_py>
cd bt_api_py
pip install -r requirements.txt
pip install .

```bash

## 快速开始

### 统一多交易所 API

```python
from bt_api_py import BtApi

# 配置多个交易所

exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    },
    "OKX___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "passphrase": "your_passphrase",
    },
    "IB_WEB___STK": {
        "auth_config": {
            "account_id": "your_account_id",
        }
    },
}

# 创建统一 API 实例

api = BtApi(exchange_kwargs=exchange_kwargs)

# 获取行情（统一接口）

ticker = api.get_ticker("BINANCE___SPOT", "BTCUSDT")
print(f"BTC 价格: {ticker.last_price}")

# 下单交易（统一接口）

order = api.limit_order(
    exchange="BINANCE___SPOT",
    symbol="BTCUSDT",
    side="buy",
    quantity=0.001,
    price=50000
)

```bash

### CTP 期货交易

```python
from bt_api_py import BtApi, CtpAuthConfig

exchange_kwargs = {
    "CTP___FUTURE": {
        "auth_config": CtpAuthConfig(
            broker_id="9999",
            user_id="your_user_id",
            password="your_password",
            md_front="tcp://180.168.146.187:10211",
            td_front="tcp://180.168.146.187:10201",
        )
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)
ticker = api.get_ticker("CTP___FUTURE", "IF2506")

```bash

### WebSocket 订阅

```python
def on_ticker(ticker):
    print(f"价格更新: {ticker.last_price}")

# 订阅行情推送

api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT", on_ticker)
api.run()

```bash

## 项目结构

```bash
bt_api_py/
├── bt_api_py/                  # 核心包

│   ├── bt_api.py               # 统一 API 入口

│   ├── registry.py             # 交易所注册表

│   ├── event_bus.py            # 事件总线

│   ├── auth_config.py          # 认证配置

│   ├── exceptions.py           # 异常体系

│   ├── containers/             # 数据容器 (20+ 种类型)

│   │   ├── orders/             # 订单数据

│   │   ├── tickers/            # 行情数据

│   │   ├── bars/               # K 线数据

│   │   ├── orderbooks/         # 深度数据

│   │   ├── positions/          # 持仓数据

│   │   └── exchanges/          # 交易所配置

│   ├── feeds/                  # 交易所适配层

│   │   ├── abstract_feed.py    # 统一场所协议

│   │   ├── live_binance/       # Binance 实现

│   │   ├── live_okx/           # OKX 实现

│   │   ├── live_ctp_feed.py    # CTP 实现

│   │   ├── live_ib_web_feed.py # IB Web API 实现

│   │   └── register_*.py       # 交易所注册模块

│   └── functions/              # 工具函数

├── docs/                       # 文档 (MkDocs)

├── tests/                      # 测试

└── examples/                   # 示例代码

```bash

## 文档

详细文档请访问: **[<https://cloudquant.github.io/bt_api_py/](<https://cloudquant.github.io/bt_api_py/)*>*>

### 核心文档

- [快速入门](<https://cloudquant.github.io/bt_api_py/quickstart/)> - 5 分钟上手指南
- [安装指南](<https://cloudquant.github.io/bt_api_py/installation/)> - 安装和配置
- [架构设计](<https://cloudquant.github.io/bt_api_py/architecture/)> - 核心架构和设计模式
- [使用指南](<https://cloudquant.github.io/bt_api_py/usage_guide/)> - 完整的使用教程
- [开发者指南](<https://cloudquant.github.io/bt_api_py/developer_guide/)> - 如何扩展和贡献代码

### 交易所指南

- [Binance API](<https://cloudquant.github.io/bt_api_py/binance/)> - 现货/合约 API 文档
- [OKX API](<https://cloudquant.github.io/bt_api_py/okx/)> - 全品类 API 文档
- [CTP 期货](<https://cloudquant.github.io/bt_api_py/ctp_quickstart/)> - CTP 快速入门
- [Interactive Brokers](<https://cloudquant.github.io/bt_api_py/ib_quickstart/)> - IB 快速入门

## 运行测试

1. 配置 API 密钥（参考测试文件中的配置）
2. 确保账户有足够资金
3. 添加 IP 到交易所白名单

```bash

# 运行所有测试

pytest tests -v

# 并行运行 (推荐)

pytest tests -n 4

# 仅运行指定测试

pytest tests/feeds/test_live_binance_spot_wss_data.py -v

```bash

## 路线图

- [ ] 添加更多交易所支持
- [ ] 完善回测框架
- [ ] 添加更多技术指标
- [ ] 优化性能和稳定性

## 贡献

欢迎贡献代码！请查看 [开发者指南](<https://cloudquant.github.io/bt_api_py/developer_guide/)> 了解详情。

## 许可证

本项目采用 [MIT License](<https://opensource.org/licenses/MIT)> 开源许可。

## 作者

[cloudQuant](<https://github.com/cloudQuant)> — yunjinqi@gmail.com

- --

:star: 如果这个项目对你有帮助，请给我们一个 star！
