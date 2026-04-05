# bt_api_py

[![Python 3.9-3.14](https://img.shields.io/badge/python-3.9--3.14-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/bt_api_py.svg)](https://pypi.org/project/bt_api_py/)
[![Tests](https://github.com/cloudQuant/bt_api_py/actions/workflows/tests.yml/badge.svg)](https://github.com/cloudQuant/bt_api_py/actions/workflows/tests.yml)
[![Docs](https://github.com/cloudQuant/bt_api_py/actions/workflows/docs.yml/badge.svg)](https://github.com/cloudQuant/bt_api_py/actions/workflows/docs.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

**bt_api_py** 是一个统一多交易所交易 API 框架，面向量化交易、套利执行、做市和多账户资产管理场景。它把不同交易所的 REST、异步 REST 和 WebSocket 接口统一到同一套 Python API 上，尽量减少接入层重复工作。

**快速入口**

- [在线文档](https://cloudquant.github.io/bt_api_py/)
- [安装指南](https://cloudquant.github.io/bt_api_py/getting-started/installation/)
- [快速开始](https://cloudquant.github.io/bt_api_py/getting-started/quickstart/)
- [仓库内文档目录](docs/)
- [问题反馈](https://github.com/cloudQuant/bt_api_py/issues)

## 为什么用 bt_api_py

- **统一接口**: 通过 `BtApi` 管理 Binance、OKX、HTX、CTP、Interactive Brokers 等不同交易所。
- **三种调用模式**: 同时支持同步 REST、异步 REST、WebSocket 订阅。
- **标准化数据模型**: `Ticker`、`OrderBook`、`Bar`、`Order`、`Position`、`Balance` 等容器统一字段语义。
- **可扩展架构**: 基于 Registry 和 Adapter 模式，新增交易所时不需要修改核心入口。
- **事件驱动**: 内置 `EventBus`，适合策略执行、行情转发和状态订阅。

## 适用场景

- **量化交易策略开发**: 用统一接口减少多交易所策略重复代码。
- **套利交易系统**: 同时连接多个交易所，统一读取行情、账户和订单状态。
- **做市系统**: 通过 WebSocket 推送处理低延迟行情和订单簿变化。
- **资产管理平台**: 统一管理多交易所账户、持仓和余额。
- **交易机器人**: 结合事件驱动机制构建自动化执行流程。

## 核心特性

### 多交易所统一接口
通过 `BtApi` 类统一管理 Binance、OKX、CTP（中国期货）、Interactive Brokers 等交易所，一套代码适配多个平台。

### 三种 API 模式
- **同步 REST API**: 适合脚本、小型工具和回测场景。
- **异步 REST API**: 适合高并发采集、批量查询和任务编排。
- **WebSocket 实时推送**: 适合低延迟行情订阅和事件驱动交易。

### 即插即用架构
基于 Registry 设计模式，新增交易所只需实现接口并注册，无需修改核心代码。

### 事件驱动机制
内置 `EventBus` 事件总线，可处理行情更新、订单变化、成交通知等异步事件。

### 标准化数据容器
提供 20+ 种标准化数据类型：
- **行情数据**: `Ticker`、`OrderBook`、`Bar`、`MarkPrice`、`FundingRate`
- **交易数据**: `Order`、`Trade`、`Position`、`Balance`、`Account`
- **其他数据**: `Symbol`、`Instrument`、`Liquidation`、`Greek`

### 跨平台支持
项目当前以 `Python 3.9-3.14` 为兼容目标，CI 覆盖 Linux、macOS 和 Windows。

## 支持的交易所

<!-- BEGIN GENERATED:EXCHANGE_SUPPORT_OVERVIEW -->
> 测试状态建议通过 `bash scripts/run_exchange_tests.sh <name>` 复核，当前口径更新于 2026-03-16。

### ✅ 已完整支持（REST + WebSocket + 测试通过）

| 交易所 | 代码 | 现货 | 合约 | 期权 | 股票 | 测试状态 | 说明 |
| -------- | -------- | -------- | -------- | -------- | -------- | -------- | -------- |
| **Binance** | `BINANCE___SPOT` / `BINANCE___SWAP` 等 | ✅ | ✅ | ✅ | — | ✅ 通过 | 现货、合约、杠杆、期权、算法交易、网格、挖矿、质押、钱包、子账户、VIP借币 |
| **HTX (Huobi)** | `HTX___SPOT` / `HTX___USDT_SWAP` 等 | ✅ | ✅ | ✅ | — | ✅ 通过 | 现货、杠杆、U本位永续、币本位永续、期权 |
| **CTP (中国期货)** | `CTP___FUTURE` | — | ✅ | — | — | ✅ 通过 | 中国期货市场（上期所、大商所、郑商所、中金所） |
| **Interactive Brokers** | `IB_WEB___STK` / `IB_WEB___FUT` | — | — | — | ✅ | ✅ 通过 | 美股、期货（通过 Web API） |

### 🔧 已实现 API（仍需继续验证或补齐能力）

| 交易所 | 类型 | 当前状态 | 测试状态 | 备注 |
| -------- | -------- | -------- | -------- | -------- |
| **OKX** | CEX | REST 已实现，WebSocket 部分实现 | ⚠️ 部分失败 | mock 目标路径问题（httpx），主体逻辑正确 |
| **Bybit** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 37 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **Bitget** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 45 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **Kraken** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 46 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **Gate.io** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 56 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **Upbit** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 101 通过 (4 skip) | 建议补 WebSocket 覆盖后再提升状态 |
| **Crypto.com** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 97 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **HitBTC** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 103 通过 (5 skip) | 建议补 WebSocket 覆盖后再提升状态 |
| **Phemex** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 65 通过 (5 skip) | 建议补 WebSocket 覆盖后再提升状态 |
| **Gemini** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 20 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **KuCoin** | CEX | REST 已实现，仍需补稳定性验证 | ⚠️ 16 失败 / 47 通过 | mock 目标路径问题 |
| **MEXC** | CEX | REST 已实现，仍需补稳定性验证 | ⚠️ 11 失败 / 42 通过 | mock 目标路径问题 |
| **Bitfinex** | CEX | REST 已实现，仍需补稳定性验证 | ⚠️ 13 失败 / 43 通过 | mock 目标路径问题 |
| **Coinbase** | CEX | REST 已实现，仍需补稳定性验证 | ⚠️ 19 失败 / 45 通过 | 部分 import 路径变更 |
| **Hyperliquid** | DEX | 实现存在，但当前仓库测试资产不足以提升到完整支持 | ⚠️ 待补验证 | 当前仓库缺少可执行的 Hyperliquid 测试文件 |
| **dYdX** | DEX | 实现存在，但当前仓库测试资产不足以提升到完整支持 | ⚠️ 待补验证 | 当前仓库缺少可执行的 dYdX 测试文件 |
| **BYDFi** | CEX | REST 已实现，仍需补稳定性验证 | ⚠️ 1 失败 / 17 通过 | JSON 解析 bug |

### 📋 已注册（基础框架就绪）

40+ 个交易所已完成注册或基础框架接入，但还需要继续补实现、测试或文档后，再提升对外状态。

> **总计**: 4 个完整支持 + 17 个已实现 API + 40+ 个已注册 = **73+ 个交易所**
>
> **说明**: 该分级采用保守口径；只有 REST、WebSocket 和测试资产同时满足时，才会提升到“完整支持”。
<!-- END GENERATED:EXCHANGE_SUPPORT_OVERVIEW -->

## 安装与兼容性

| 项目 | 当前支持 |
|------|----------|
| Python | `3.9` - `3.14` |
| 操作系统 | Linux, macOS, Windows |
| 安装方式 | PyPI, 源码开发安装 |
| 主要接口 | REST, Async REST, WebSocket |

### 方式一：从 PyPI 安装（推荐）

```bash
pip install bt_api_py
```

### 方式二：从源码安装（开发模式）

从源码安装会编译 Cython 扩展和 CTP SWIG C++ 扩展，请先安装对应平台的编译环境。

#### macOS

```bash
xcode-select --install
brew install swig
```

#### Linux（Debian/Ubuntu）

```bash
sudo apt install swig g++
```

#### Windows 11

```bash
winget install Microsoft.VisualStudio.2022.BuildTools
# 打开 Visual Studio Installer，勾选"使用C++的桌面开发"
winget install miniconda3
conda install -c conda-forge swig libiconv
```

完成前置环境后，再执行源码安装：

```bash
git clone https://github.com/cloudQuant/bt_api_py
cd bt_api_py
python -m pip install --upgrade pip
pip install -e .
```

### 可选依赖

```bash
# 安装所有可选依赖
pip install bt_api_py[all]

# 或按需安装
pip install bt_api_py[dev]          # 开发工具
pip install bt_api_py[ib]           # Interactive Brokers 支持
pip install bt_api_py[ib_web]       # IB Web API 支持
pip install bt_api_py[visualization] # 可视化工具
```

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

```

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

```

### WebSocket 订阅

```python
def on_ticker(ticker):
    print(f"价格更新: {ticker.last_price}")

# 订阅行情推送
api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT", on_ticker)

# 订阅订单簿
def on_orderbook(orderbook):
    print(f"买一: {orderbook.bids[0]}, 卖一: {orderbook.asks[0]}")

api.subscribe_orderbook("BINANCE___SPOT", "BTCUSDT", on_orderbook)

# 启动事件循环
api.run()
```

### 异步 API 使用

```python
import asyncio
from bt_api_py import BtApi

async def main():
    api = BtApi(exchange_kwargs=exchange_kwargs)

    # 异步获取多个交易所行情
    tasks = [
        api.async_get_ticker("BINANCE___SPOT", "BTCUSDT"),
        api.async_get_ticker("OKX___SPOT", "BTC-USDT"),
    ]
    results = await asyncio.gather(*tasks)

    for ticker in results:
        print(f"{ticker.symbol}: {ticker.last_price}")

asyncio.run(main())
```

## 项目结构

```
bt_api_py/
├── bt_api_py/                  # 核心包
│   ├── __init__.py             # 包入口，导出主要类
│   ├── bt_api.py               # 统一 API 入口 (BtApi 类)
│   ├── registry.py             # 交易所注册表 (ExchangeRegistry)
│   ├── event_bus.py            # 事件总线 (EventBus)
│   ├── auth_config.py          # 认证配置类
│   ├── exceptions.py           # 异常体系
│   ├── error.py                # 错误基类和核心错误
│   ├── cache.py                # 缓存管理
│   ├── rate_limiter.py         # 速率限制
│   ├── connection_pool.py      # 连接池管理
│   ├── config_loader.py        # 配置加载器
│   ├── instrument_manager.py   # 合约管理器
│   ├── websocket_manager.py    # WebSocket 管理器
│   │
│   ├── containers/             # 数据容器 (20+ 种类型)
│   │   ├── accounts/           # 账户数据
│   │   ├── balances/           # 余额数据
│   │   ├── bars/               # K 线数据
│   │   ├── tickers/            # 行情数据
│   │   ├── orderbooks/         # 深度数据
│   │   ├── orders/             # 订单数据
│   │   ├── positions/          # 持仓数据
│   │   ├── trades/             # 成交数据
│   │   ├── symbols/            # 标的信息
│   │   ├── fundingrates/       # 资金费率
│   │   ├── markprices/         # 标记价格
│   │   ├── liquidations/       # 强平数据
│   │   └── exchanges/          # 交易所配置容器
│   │
│   ├── feeds/                  # 交易所适配层
│   │   ├── abstract_feed.py    # 统一交易所协议
│   │   ├── feed.py             # Feed 基类
│   │   ├── http_client.py      # HTTP 客户端基类
│   │   ├── base_stream.py      # WebSocket 流基类
│   │   ├── capability.py       # 能力声明
│   │   ├── live_binance/       # Binance 实现
│   │   ├── live_okx/           # OKX 实现
│   │   ├── live_htx/           # HTX (Huobi) 实现
│   │   ├── live_bybit/         # Bybit 实现
│   │   ├── live_gateio/        # Gate.io 实现
│   │   ├── live_kraken/        # Kraken 实现
│   │   ├── live_bitget/        # Bitget 实现
│   │   ├── live_kucoin/        # KuCoin 实现
│   │   ├── live_mexc/          # MEXC 实现
│   │   ├── live_hyperliquid/   # Hyperliquid 实现
│   │   ├── live_ctp_feed.py    # CTP 期货实现
│   │   ├── live_ib_web_feed.py # IB Web API 实现
│   │   └── live_*/              # 其他 70+ 交易所实现
│   │
│   ├── exchange_registers/     # 交易所注册模块
│   │   ├── register_binance.py
│   │   ├── register_okx.py
│   │   ├── register_htx.py
│   │   ├── register_ctp.py
│   │   └── register_*.py       # 70+ 交易所注册
│   │
│   ├── errors/                 # 错误翻译器
│   │   ├── binance_translator.py
│   │   ├── okx_translator.py
│   │   └── *_translator.py    # 各交易所错误翻译
│   │
│   ├── core/                   # 核心模块
│   │   ├── async_context.py    # 异步上下文管理
│   │   └── dependency_injection.py
│   │
│   ├── websocket/              # WebSocket 模块
│   ├── gateway/                # 网关模块
│   ├── monitoring/             # 监控模块
│   ├── risk_management/        # 风险管理
│   ├── security_compliance/    # 安全合规
│   ├── functions/              # 工具函数
│   ├── configs/                # 配置文件
│   └── ctp/                    # CTP C++ 扩展
│
├── tests/                      # 测试套件
│   ├── containers/             # 容器测试
│   ├── feeds/                  # Feed 测试
│   ├── core/                   # 核心模块测试
│   ├── exchange_registers/     # 注册模块测试
│   ├── websocket/              # WebSocket 测试
│   ├── monitoring/             # 监控测试
│   └── test_*.py               # 其他测试
│
├── docs/                       # 文档 (MkDocs)
│   ├── getting-started/        # 入门指南
│   ├── guides/                 # 使用指南
│   ├── reference/              # API 参考
│   ├── explanation/            # 概念解释
│   ├── exchanges/              # 交易所文档
│   └── index.md                # 文档首页
│
├── scripts/                    # 工具脚本
│   ├── run_tests.sh            # 测试运行脚本
│   ├── run_exchange_tests.sh   # 交易所测试脚本
│   ├── generate_docs_*.py      # 文档生成脚本
│   └── check_code_quality.py   # 代码质量检查
│
├── examples/                   # 示例代码
│   ├── network_tests/          # 网络测试示例
│   ├── monitoring_examples.py
│   └── risk_management_*.py
│
├── .github/                    # GitHub 配置
│   └── workflows/              # CI/CD 工作流
│
├── README.md                   # 项目说明
├── CHANGELOG.md                # 变更日志
├── CONTRIBUTING.md             # 贡献指南
├── pyproject.toml              # 项目配置
├── setup.py                    # 安装脚本
└── Makefile                    # 构建脚本
```

## 核心架构

### 设计模式

项目采用以下核心设计模式：

- **Registry 模式** — 交易所注册机制，支持即插即用扩展
- **Factory 模式** — 统一的数据容器创建接口
- **Observer 模式** — 事件总线实现，支持行情和订单事件订阅
- **Adapter 模式** — 各交易所适配器，屏蔽底层差异
- **Strategy 模式** — 灵活的认证策略（API Key、OAuth、Cookie 等）

### 核心模块说明

| 模块 | 说明 |
|------|------|
| `bt_api.py` | 统一 API 入口，提供 `BtApi` 类管理所有交易所 |
| `registry.py` | 交易所注册表，管理交易所实例的创建和获取 |
| `event_bus.py` | 事件总线，支持异步事件订阅和发布 |
| `auth_config.py` | 认证配置类，支持多种认证方式 |
| `containers/` | 标准化数据容器，统一各交易所数据格式 |
| `feeds/` | 交易所适配层，实现各交易所的 REST 和 WebSocket 接口 |
| `exchange_registers/` | 交易所注册模块，声明交易所配置和能力 |
| `errors/` | 错误翻译器，将交易所错误码转换为统一错误类型 |
| `websocket_manager.py` | WebSocket 连接管理，支持自动重连和心跳 |

### 数据容器类型

| 类别 | 容器类型 | 说明 |
|------|----------|------|
| 行情 | `Ticker`, `Bar`, `OrderBook`, `Trade` | 实时行情数据 |
| 账户 | `Account`, `Balance`, `Position` | 账户和持仓信息 |
| 交易 | `Order`, `Trade`, `Income` | 订单和成交记录 |
| 合约 | `Symbol`, `Instrument` | 交易对和合约信息 |
| 衍生品 | `FundingRate`, `MarkPrice`, `Liquidation`, `Greek` | 合约相关数据 |

## 文档

优先访问在线文档：**[https://cloudquant.github.io/bt_api_py/](https://cloudquant.github.io/bt_api_py/)**。

如果 GitHub Pages 站点暂时不可用，也可以直接查看仓库内的 [docs/](docs/) 目录。

### 核心文档

- [快速入门](https://cloudquant.github.io/bt_api_py/getting-started/quickstart/) - 5 分钟上手指南
- [安装指南](https://cloudquant.github.io/bt_api_py/getting-started/installation/) - 安装和环境准备
- [架构设计](https://cloudquant.github.io/bt_api_py/explanation/architecture/) - 核心架构和设计模式
- [使用指南](https://cloudquant.github.io/bt_api_py/guides/usage_guide/) - 常见调用方式和工程集成
- [开发者指南](https://cloudquant.github.io/bt_api_py/explanation/developer_guide/) - 如何扩展和贡献代码
- [更新日志](https://cloudquant.github.io/bt_api_py/getting-started/change_log/) - 最近文档和能力变更
- [仓库文档目录](docs/) - 本地浏览 `docs/` 全量内容

### 交易所指南

- [Binance](https://cloudquant.github.io/bt_api_py/exchanges/binance/) - 现货、合约、杠杆和算法交易文档
- [OKX](https://cloudquant.github.io/bt_api_py/exchanges/okx/) - 交易、资金、公共数据和算法接口
- [CTP 期货](https://cloudquant.github.io/bt_api_py/exchanges/ctp/quickstart/) - 中国期货接入快速入门
- [Interactive Brokers](https://cloudquant.github.io/bt_api_py/exchanges/ib/quickstart/) - IB Web API 使用指南

## 运行测试

### 本地快速验证

```bash
# 与 CI smoke suite 保持一致
pytest tests/test_bt_api_quality.py \
  tests/test_event_bus.py \
  tests/core/test_async_context.py \
  tests/gateway/test_config.py -q

# 代码质量
ruff check bt_api_py tests
mypy bt_api_py --ignore-missing-imports
```

### 完整基线测试

```bash
# 运行非网络、非集成基线测试
pytest tests -m "not network and not integration and not performance and not e2e" -q

# 生成覆盖率报告
pytest tests -m "not network and not integration and not performance and not e2e" \
  --cov=bt_api_py \
  --cov-report=html \
  --cov-report=xml
```

### 需要真实账户或网络的测试

```bash
# 运行前请先配置 API 密钥、测试网账户和 IP 白名单
pytest tests -m binance -v
pytest tests -m okx -v
pytest tests -m ctp -v
```

## 路线图

### 近期计划 (v0.16-v0.20)

- [x] 添加 HTX (Huobi) 交易所完整支持（现货、杠杆、合约、期权）
- [ ] 完善 Bybit、Gate.io 等交易所的 WebSocket 支持
- [ ] 完善回测框架，支持历史数据回放
- [ ] WebSocket 断线重连优化
- [ ] 性能优化和稳定性提升

### 长期计划 (v1.0+)

- [ ] 将更多已注册交易所提升至完整支持
- [ ] 内置风险管理模块
- [ ] 策略回测可视化界面
- [ ] 云端部署支持
- [ ] 机器学习集成

## 常见问题 (FAQ)

### Q: 支持哪些 Python 版本？
当前兼容目标是 Python `3.9` 到 `3.14`。如果你希望和默认 CI 环境保持一致，优先使用 Python `3.11`。

### Q: 如何添加新的交易所？
请参考 [开发者指南](https://cloudquant.github.io/bt_api_py/explanation/developer_guide/)，实现 `AbstractFeed` 接口并注册到 `ExchangeRegistry` 即可。基本步骤：
1. 在 `feeds/` 下创建交易所实现目录
2. 在 `exchange_registers/` 下创建注册模块
3. 在 `errors/` 下添加错误翻译器（可选）

### Q: WebSocket 连接断开怎么办？
框架内置了自动重连机制，会在断开后自动尝试重新连接。您也可以通过事件总线监听连接状态变化。

### Q: 支持模拟交易吗？
支持！Binance 和 OKX 都支持测试网模式，在配置中设置 `testnet=True` 即可。CTP 也支持 SimNow 模拟环境。

### Q: 如何处理交易所 API 限流？
框架内置了速率限制器 (`rate_limiter.py`)，会自动根据各交易所的限制进行请求控制。您也可以自定义限流策略。

### Q: 如何获取技术支持？
可以通过以下方式获取帮助：
- [在线文档](https://cloudquant.github.io/bt_api_py/)
- [仓库内文档目录](docs/)
- [GitHub Issue](https://github.com/cloudQuant/bt_api_py/issues)
- 发送邮件至 yunjinqi@gmail.com

### Q: 项目测试覆盖率如何？
项目包含大规模单元测试和兼容性 smoke suite。当前推荐先跑 README 上面的 smoke suite，再根据需要执行完整基线测试并生成覆盖率报告。

## 贡献

我们欢迎所有形式的贡献！无论是报告 Bug、提出新功能建议、改进文档还是提交代码。

### 如何贡献

1. Fork 本仓库
2. 创建您的特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交您的更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启一个 Pull Request

详细贡献指南请查看 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [开发者指南](https://cloudquant.github.io/bt_api_py/explanation/developer_guide/)。

## 许可证

本项目采用 [MIT License](https://opensource.org/licenses/MIT) 开源许可。您可以自由使用、修改和分发本项目。

## 作者

**cloudQuant** - [GitHub](https://github.com/cloudQuant) - yunjinqi@gmail.com

## 致谢

感谢所有为本项目做出贡献的开发者！

## 联系我们

- Email: yunjinqi@gmail.com
- Issues: [GitHub Issues](https://github.com/cloudQuant/bt_api_py/issues)
- 文档: [在线文档](https://cloudquant.github.io/bt_api_py/)

---

如果这个项目对你有帮助，请给我们一个 Star！

[![Star History Chart](https://api.star-history.com/svg?repos=cloudQuant/bt_api_py&type=Date)](https://star-history.com/#cloudQuant/bt_api_py&Date)
