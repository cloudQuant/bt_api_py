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
> 测试状态建议通过 `bash scripts/run_exchange_tests.sh <name>` 复核，当前口径更新于 2026-04-06。

### ✅ 已完整支持（REST + WebSocket + 测试通过）

| 交易所 | 代码 | 现货 | 合约 | 期权 | 股票 | 测试状态 | 说明 |
| -------- | -------- | -------- | -------- | -------- | -------- | -------- | -------- |
| **Binance** | `BINANCE___SPOT` / `BINANCE___SWAP` 等 | ✅ | ✅ | ✅ | — | ✅ 通过 | 现货、合约、杠杆、期权、算法交易、网格、挖矿、质押、钱包、子账户、VIP借币 |
| **HTX (Huobi)** | `HTX___SPOT` / `HTX___USDT_SWAP` 等 | ✅ | ✅ | ✅ | — | ✅ 通过 | 现货、杠杆、U本位永续、币本位永续、期权 |
| **CTP (中国期货)** | `CTP___FUTURE` | — | ✅ | — | — | ✅ 通过 | 中国期货市场（上期所、大商所、郑商所、中金所） |
| **Interactive Brokers** | `IB_WEB___STK` / `IB_WEB___FUT` | — | — | — | ✅ | ✅ 通过 | 美股、期货（通过 Web API） |

### 🔧 已实现 API（按当前主要缺口分组）

首页只展示分组摘要；逐交易所测试状态见 [详细状态页](docs/exchanges/EXCHANGE_STATUS.md)。

- `OKX`: REST 已实现，WebSocket 部分实现。当前主要工作是修正现有 mock 路径问题，并补齐 WebSocket 覆盖。
- `Bybit`、`Bitget`、`Kraken`、`Gate.io`、`Upbit`、`Crypto.com`、`HitBTC`、`Phemex`、`Gemini`: REST 已实现。当前主要缺口是 WebSocket 能力、实时订阅适配和对应测试覆盖。
- `KuCoin`、`MEXC`、`Bitfinex`、`Coinbase`、`BYDFi`: REST 已实现。当前主要工作是修复已知失败项、兼容性问题，并补稳定性回归测试。
- `Hyperliquid`、`dYdX`: 实现存在，但仓库内测试资产仍不足。补齐可执行测试文件和验证资产后，再提升到更高支持等级。

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

# 如果你要参与开发
pip install -e ".[dev]"
```

### 可选依赖

| Extra | 用途 |
|------|------|
| `bt_api_py[all]` | 安装所有可选依赖 |
| `bt_api_py[dev]` | `pytest`、`ruff`、`mypy` 等开发工具 |
| `bt_api_py[security]` | `security_compliance`、OAuth/JWT、加密、密码哈希 |
| `bt_api_py[ib]` | Interactive Brokers 原生支持 |
| `bt_api_py[ib_web]` | IB Web API 和浏览器自动化相关依赖 |
| `bt_api_py[visualization]` | 图表与可视化工具 |

```bash
pip install bt_api_py[all]
pip install bt_api_py[dev]
pip install bt_api_py[security]
```

## 快速开始

### 同步行情查询

```python
from bt_api_py import BtApi

exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
print(ticker)
```

### 统一下单

```python
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=50000,
    order_type="limit",
)
print(order)
```

### 异步请求

```python
import asyncio
from bt_api_py import BtApi

async def main():
    api = BtApi(
        exchange_kwargs={
            "BINANCE___SPOT": {
                "api_key": "your_api_key",
                "secret": "your_secret",
                "testnet": True,
            }
        }
    )

    ticker = await api.async_get_tick("BINANCE___SPOT", "BTCUSDT")
    print(ticker)

asyncio.run(main())
```

### WebSocket 订阅

```python
api.subscribe(
    "BINANCE___SPOT___BTCUSDT",
    [
        {"topic": "ticker", "symbol": "BTCUSDT"},
        {"topic": "depth", "symbol": "BTCUSDT"},
    ],
)

data_queue = api.get_data_queue("BINANCE___SPOT")
message = data_queue.get(timeout=10)
print(type(message).__name__, message)
```

## 核心 API 一览

| 方法 | 说明 |
|------|------|
| `get_tick(exchange, symbol)` | 查询最新行情 |
| `get_depth(exchange, symbol, count=20)` | 查询订单簿深度 |
| `get_kline(exchange, symbol, period, count=20)` | 查询 K 线 |
| `make_order(exchange, symbol, volume, price, order_type)` | 统一下单入口 |
| `cancel_order(exchange, symbol, order_id)` | 撤单 |
| `get_balance(exchange, symbol=None)` | 查询余额 |
| `get_position(exchange, symbol=None)` | 查询持仓 |
| `async_get_tick(...)` / `async_make_order(...)` | 异步接口，自动代理到对应 feed |
| `subscribe(dataname, topics)` | 发起 WebSocket 订阅 |
| `get_data_queue(exchange)` | 读取 WebSocket 推送结果 |
| `get_event_bus()` | 获取事件总线实例 |

## 仓库结构

- `bt_api_py/`: 核心包，包含 `BtApi`、注册表、数据容器、feeds、gateway、websocket 和风险管理模块。
- `tests/`: 单元测试、兼容性测试、网关测试和 WebSocket 测试。
- `docs/`: MkDocs 文档站点，按 Getting Started / Guides / Reference / Explanation 组织。
- `scripts/`: 开发和维护脚本，统一放置测试入口、文档生成和诊断脚本。
- `configs/`: 配置模板和示例文件，避免把环境样例继续堆在主目录。
- `examples/`: 网络测试和使用示例。
- `.github/workflows/`: CI、文档部署和发布流程。

### 主目录约定

- 根目录只保留包管理、文档入口和自动化配置，例如 `README.md`、`pyproject.toml`、`mkdocs.yml`、`Makefile`。
- 测试与运维脚本统一收纳到 `scripts/`，例如 `scripts/run_tests.sh`。
- 环境模板和样例配置统一收纳到 `configs/examples/`，例如 `configs/examples/security_compliance.env.example`。

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

## 开发与测试

### 本地快速验证

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 查看仓库测试脚本帮助
./scripts/run_tests.sh --help

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
# 完整基线测试建议把安全相关依赖一起装上
# 其中 security extra 包含 PyJWT、cryptography、bcrypt
pip install -e ".[dev,security]"

# 可选：使用仓库脚本跑更常见的本地测试组合
./scripts/run_tests.sh -m "not slow and not network"

# 运行非网络、非集成基线测试
pytest tests -m "not network and not integration and not performance and not e2e" -q

# 生成覆盖率报告
pytest tests -m "not network and not integration and not performance and not e2e" \
  --cov=bt_api_py \
  --cov-report=html \
  --cov-report=xml
```

### CI 说明

- Push / Pull Request: 运行 `Quality Gates`、`Compatibility` 矩阵和 Ubuntu 完整基线测试。
- 兼容性矩阵: Linux、macOS、Windows GitHub-hosted runner x Python `3.9` 到 `3.14`。
- Windows 说明: GitHub Actions 使用官方支持的 `windows-latest` hosted runner；项目兼容目标包含 Windows 11。

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
框架内置了自动重连能力。推荐通过 `subscribe()` 发起订阅，再用 `get_data_queue()` 或 `get_event_bus()` 消费推送数据，这样断线恢复后上层处理逻辑不需要改写。

### Q: 支持模拟交易吗？
支持！Binance 和 OKX 都支持测试网模式，在配置中设置 `testnet=True` 即可。CTP 也支持 SimNow 模拟环境。

### Q: 如何处理交易所 API 限流？
框架内置了速率限制器 (`rate_limiter.py`)，会自动根据各交易所的限制进行请求控制。您也可以自定义限流策略。

### Q: Security Compliance 模块需要额外安装什么？
如果你要使用 `security_compliance` 相关能力，或运行完整基线测试中的安全测试，请安装 `bt_api_py[security]`。其中包含 `PyJWT`、`cryptography` 和 `bcrypt`。仓库里的环境模板位于 `configs/examples/security_compliance.env.example`。

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
