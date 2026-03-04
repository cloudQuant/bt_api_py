# bt_api_py

[![Python 3.11+](<https://img.shields.io/badge/python-3.11%2B-blue.svg)](<https://www.python.org/downloads/>)>
[![License: MIT](<https://img.shields.io/badge/License-MIT-green.svg)](<https://opensource.org/licenses/MIT>)>
[![PyPI](<https://img.shields.io/pypi/v/bt_api_py.svg)](<https://pypi.org/project/bt_api_py/>)>
[![Documentation](<https://img.shields.io/badge/docs-latest-blue.svg)](<https://cloudquant.github.io/bt_api_py/>)>

- **bt_api_py** 是一个专业的统一多交易所交易 API 框架，专为量化交易者和机构投资者设计。支持现货、合约、期货、股票等多种交易类型，提供同步、异步和 WebSocket 三种接口模式，让您用一套代码轻松对接全球主流交易所。

## 核心优势

1. **🔌 即插即用的交易所扩展** — 基于 Registry 设计模式，新增交易所无需修改核心代码，只需注册即可使用，真正实现了开闭原则
2. **🌐 统一的多交易所接口** — 一套 API 代码同时管理 Binance、OKX、CTP、Interactive Brokers 等多个交易所，大幅降低开发和维护成本
3. **⚡ 三种 API 模式自由切换** — 同时支持同步 REST、异步 REST、WebSocket 实时推送，满足不同场景的性能需求
4. **📦 丰富的标准化数据容器** — 提供 20+ 种标准化数据类型（Ticker、OrderBook、Bar、Order、Position 等），屏蔽各交易所数据格式差异
5. **🚀 高性能 C/C++ 扩展** — 核心计算模块使用 Cython 和 C++ 实现，关键路径性能优化，适合高频交易场景

:books:**[在线文档](https://cloudquant.github.io/bt_api_py/)**|> :rocket: [快速开始](https://cloudquant.github.io/bt_api_py/quickstart/) | [English](README.en.md) | 中文

- --

## 适用场景

- **量化交易策略开发** — 统一接口简化多交易所策略开发和回测
- **套利交易系统** — 同时连接多个交易所，捕捉跨市场套利机会
- **做市商系统** — 高性能 WebSocket 实时行情，支持低延迟交易
- **资产管理平台** — 统一管理多个交易所的账户、订单和持仓
- **交易机器人** — 事件驱动架构，轻松构建自动化交易机器人

## 核心特性

### 多交易所统一接口
通过 `BtApi` 类统一管理 Binance、OKX、CTP(中国期货)、Interactive Brokers 等交易所，一套代码适配所有平台。

### 三种 API 模式
- **同步 REST API** — 简单直接，适合脚本和策略回测
- **异步 REST API** — 高并发场景，提升吞吐量
- **WebSocket 实时推送** — 低延迟行情订阅，适合高频交易

### 即插即用架构
基于 Registry 设计模式，新增交易所只需实现接口并注册，无需修改核心代码，完美符合开闭原则。

### 事件驱动机制
内置 EventBus 事件总线，支持回调模式，轻松处理行情更新、订单变化、成交通知等事件。

### 标准化数据容器
提供 20+ 种标准化数据类型：
- **行情数据**: Ticker, OrderBook, Bar, MarkPrice, FundingRate
- **交易数据**: Order, Trade, Position, Balance, Account
- **其他数据**: Symbol, Instrument, Liquidation, Greek 等

### 跨平台支持
支持 Linux (x86_64)、Windows (x64)、macOS (arm64/x86_64)，一次开发，多平台部署。

## 支持的交易所

> 测试状态通过 `bash scripts/run_exchange_tests.sh <name>` 验证（迭代5，2026-03）

### ✅ 已完整支持（REST + WebSocket + 测试通过）

| 交易所 | 代码 | 现货 | 合约 | 期权 | 股票 | 单元测试 | 说明 |
|--------|------|:----:|:----:|:----:|:----:|:--------:|------|
| **Binance** | `BINANCE___SPOT` / `BINANCE___SWAP` 等 | ✅ | ✅ | ✅ | — | ✅ 通过 | 现货、合约、杠杆、期权、算法交易、网格、挖矿、质押、钱包、子账户、VIP借币 |
| **HTX (Huobi)** | `HTX___SPOT` / `HTX___USDT_SWAP` 等 | ✅ | ✅ | ✅ | — | ✅ 通过 | 现货、杠杆、U本位永续、币本位永续、期权 |
| **CTP** (中国期货) | `CTP___FUTURE` | — | ✅ | — | — | ✅ 通过 | 中国期货市场（上期所、大商所、郑商所、中金所） |
| **Interactive Brokers** | `IB_WEB___STK` / `IB_WEB___FUT` | — | — | — | ✅ | ✅ 通过 | 美股、期货（通过 Web API） |

### 🔧 已实现 REST API（单元测试部分通过）

| 交易所 | 类型 | 单元测试 | 备注 |
|--------|------|:--------:|------|
| **OKX** | CEX | ⚠️ 部分失败 | mock 目标路径问题（httpx），主体逻辑正确 |
| **Bybit** | CEX | ✅ 37 通过 | |
| **Bitget** | CEX | ✅ 45 通过 | |
| **Kraken** | CEX | ✅ 46 通过 | |
| **Gate.io** | CEX | ✅ 56 通过 | |
| **Upbit** | CEX | ✅ 101 通过 (4 skip) | |
| **Crypto.com** | CEX | ✅ 97 通过 | |
| **HitBTC** | CEX | ✅ 103 通过 (5 skip) | |
| **Phemex** | CEX | ✅ 65 通过 (5 skip) | |
| **Gemini** | CEX | ✅ 20 通过 | |
| **KuCoin** | CEX | ⚠️ 16 失败 / 47 通过 | mock 目标路径问题 |
| **MEXC** | CEX | ⚠️ 11 失败 / 42 通过 | mock 目标路径问题 |
| **Bitfinex** | CEX | ⚠️ 13 失败 / 43 通过 | mock 目标路径问题 |
| **Coinbase** | CEX | ⚠️ 19 失败 / 45 通过 | 部分 import 路径变更 |
| **Hyperliquid** | DEX | ⚠️ 5 失败 / 32 通过 | mock 问题 |
| **dYdX** | DEX | ✅ 通过 | |
| **BYDFi** | CEX | ⚠️ 1 失败 / 17 通过 | JSON 解析 bug |

### 📋 已注册（基础框架就绪，单元测试通过）

以下交易所已完成注册和基础代码框架，单元测试全部通过：

<details>
<summary>点击展开全部已注册交易所及测试状态</summary>

**中心化交易所 (CEX) — 测试全部通过 ✅**

| 交易所 | 测试数 | 交易所 | 测试数 | 交易所 | 测试数 |
|--------|--------|--------|--------|--------|--------|
| **Bequant** | 96 passed | **BigONE** | 96 passed | **BingX** | 11 passed |
| **Bitbank** | 12 passed | **BitBNS** | 12 passed | **Bitflyer** | 17 passed |
| **Bithumb** | 17 passed | **Bitinka** | 16 passed | **BitMart** | 94 passed |
| **Bitrue** | 99 passed | **Bitso** | 98 passed | **Bitstamp** | 101 passed |
| **Bitunix** | 16 passed | **Bitvavo** | 16 passed | **BTC Markets** | 19 passed |
| **BTCTurk** | 20 passed | **Buda** | 20 passed | **Coincheck** | 18 passed |
| **CoinDCX** | 18 passed | **CoinEx** | 96 passed | **Coinone** | 98 passed |
| **CoinSpot** | 77 passed | **CoinSwitch** | 75 passed | **EXMO** | 19 passed |
| **Foxbit** | 19 passed | **Giottus** | 18 passed | **Independent Reserve** | 78 passed |
| **Korbit** | 84 passed | **LATOKEN** | 90 passed | **LocalBitcoins** | 71 passed |
| **Luno** | 15 passed | **Mercado Bitcoin** | 18 passed | **Ripio** | 22 passed |
| **SatoshiTango** | 20 passed | **Swyftx** | 73 passed | **VALR** | 73 passed |
| **WazirX** | 74 passed | **YoBit** | 83 passed | **Zaif** | 92 passed |
| **Zebpay** | 93 passed | | | | |

**去中心化交易所 (DEX) — 测试通过 ✅**

| 交易所 | 测试数 |
|--------|--------|
| Balancer / Cow Swap / Curve / GMX / PancakeSwap / Raydium / SushiSwap / Uniswap | 172 passed |

</details>

> **总计**: 4 个完整支持 + 17 个 REST API 实现 + 40+ 个已注册 = **73 个交易所**
>
> **提示**: 欢迎提交 Issue 或 PR 帮助完善更多交易所的支持！

## 安装

### 方式一：从 PyPI 安装（推荐）

```bash
pip install bt_api_py
```

### 方式二：从源码安装

```bash
git clone https://github.com/cloudQuant/bt_api_py
cd bt_api_py
pip install -r requirements.txt
pip install .
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
│   │   ├── abstract_feed.py    # 统一交易所协议
│   │   ├── live_binance/       # Binance 实现
│   │   ├── live_okx/           # OKX 实现
│   │   ├── live_htx/           # HTX (Huobi) 实现
│   │   ├── live_ctp_feed.py    # CTP 实现
│   │   ├── live_ib_web_feed.py # IB Web API 实现
│   │   └── register_*.py       # 交易所注册模块 (73 个交易所)
│   ├── functions/              # 工具函数
│   └── ctp/                    # CTP C++ 扩展
├── docs/                       # 文档 (MkDocs)
├── tests/                      # 单元测试和集成测试
├── examples/                   # 示例代码
└── scripts/                    # 构建和部署脚本
```

## 文档

详细文档请访问: **[https://cloudquant.github.io/bt_api_py/](https://cloudquant.github.io/bt_api_py/)**

### 核心文档

- [快速入门](https://cloudquant.github.io/bt_api_py/quickstart/) - 5 分钟上手指南
- [安装指南](https://cloudquant.github.io/bt_api_py/installation/) - 安装和配置
- [架构设计](https://cloudquant.github.io/bt_api_py/architecture/) - 核心架构和设计模式
- [使用指南](https://cloudquant.github.io/bt_api_py/usage_guide/) - 完整的使用教程
- [开发者指南](https://cloudquant.github.io/bt_api_py/developer_guide/) - 如何扩展和贡献代码

### 交易所指南

- [Binance API](https://cloudquant.github.io/bt_api_py/binance/) - 现货/合约 API 文档
- [OKX API](https://cloudquant.github.io/bt_api_py/okx/) - 全品类 API 文档
- [CTP 期货](https://cloudquant.github.io/bt_api_py/ctp_quickstart/) - CTP 快速入门
- [Interactive Brokers](https://cloudquant.github.io/bt_api_py/ib_quickstart/) - IB 快速入门

## 运行测试

### 前置条件

1. 配置 API 密钥（参考测试文件中的配置）
2. 确保账户有足够资金（建议使用测试网）
3. 添加 IP 到交易所白名单

### 运行测试

```bash
# 运行所有测试
pytest tests -v

# 并行运行 (推荐，速度更快)
pytest tests -n 4

# 运行指定交易所测试
pytest tests -m binance -v
pytest tests -m okx -v
pytest tests -m ctp -v

# 运行指定测试文件
pytest tests/feeds/test_live_binance_spot_wss_data.py -v

# 生成测试覆盖率报告
pytest tests --cov=bt_api_py --cov-report=html
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
A: 支持 Python 3.11, 3.12, 3.13。推荐使用 Python 3.11+ 以获得最佳性能。

### Q: 如何添加新的交易所？
A: 请参考 [开发者指南](https://cloudquant.github.io/bt_api_py/developer_guide/)，实现 `AbstractFeed` 接口并注册到 `ExchangeRegistry` 即可。

### Q: WebSocket 连接断开怎么办？
A: 框架内置了自动重连机制，会在断开后自动尝试重新连接。您也可以通过事件总线监听连接状态。

### Q: 支持模拟交易吗？
A: 支持！Binance 和 OKX 都支持测试网模式，在配置中设置 `testnet=True` 即可。

### Q: 如何获取技术支持？
A: 可以通过以下方式获取帮助：
- [在线文档](https://cloudquant.github.io/bt_api_py/)
- [GitHub Issue](https://github.com/cloudQuant/bt_api_py/issues)
- 发送邮件至 yunjinqi@gmail.com

## 贡献

我们欢迎所有形式的贡献！无论是报告 Bug、提出新功能建议、改进文档还是提交代码。

### 如何贡献

1. Fork 本仓库
2. 创建您的特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交您的更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启一个 Pull Request

详细贡献指南请查看 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [开发者指南](https://cloudquant.github.io/bt_api_py/developer_guide/)。

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
