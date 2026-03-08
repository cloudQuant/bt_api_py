# bt_api_py 项目文档索引

生成日期：2026-03-07  
扫描级别：Exhaustive（详尽扫描）  
项目类型：Python Library

---

## 项目概述

**bt_api_py** - 统一多交易所交易API框架

**核心价值：**
- 🔌 即插即用的交易所扩展（Registry模式）
- 🌐 统一的多交易所接口（73+交易所）
- ⚡ 三种API模式（同步REST/异步REST/WebSocket）
- 📦 标准化数据容器（27种类型）
- 🚀 高性能C/C++扩展（Cython/SWIG）

**技术栈：**
- 语言：Python 3.11+
- 数据：pandas 2.0+, numpy 1.26+
- 异步：aiohttp 3.9.0+
- 同步：requests 2.31+, httpx 0.27.0+
- WebSocket：websocket-client 1.6+
- 测试：pytest 7.0+
- 质量：ruff 0.1+, mypy 1.0+

**项目统计：**
- Python源文件：764个
- 测试文件：201个
- 支持的交易所：73+个
- 数据容器类型：27种

---

## 快速参考

### 项目信息

| 属性 | 值 |
|------|-----|
| **类型** | Python Library |
| **主要语言** | Python 3.11+ |
| **架构模式** | Registry + Event-Driven |
| **支持平台** | Linux/Windows/macOS |
| **开源协议** | MIT License |
| **PyPI包** | bt_api_py |
| **当前版本** | 0.15 |

### 关键技术

| 类别 | 技术 | 用途 |
|------|------|------|
| 核心框架 | Registry Pattern | 交易所注册和动态加载 |
| 核心框架 | Event-Driven Architecture | 事件分发和订阅 |
| 数据处理 | pandas, numpy | 数据分析和数值计算 |
| HTTP客户端 | aiohttp, requests, httpx | REST API调用 |
| 实时数据 | websocket-client | WebSocket连接 |
| 性能优化 | Cython, SWIG | C/C++扩展 |
| 测试框架 | pytest + xdist | 并行测试 |
| 代码质量 | ruff, mypy | Linting和类型检查 |
| 文档引擎 | MkDocs + Material | 静态文档生成 |

---

## 生成的文档

### 核心文档

- ✅ [项目概览](./project-overview.md) - 项目完整介绍、技术栈、架构设计
- ✅ [源代码树分析](./source-tree-analysis.md) - 完整目录结构、关键入口点、架构约束
- ✅ [开发指南](./development-guide.md) - 开发设置、测试、代码风格、调试技巧

### 现有文档

- ✅ [README.md](../README.md) - 项目主要说明
- ✅ [CONTRIBUTING.md](../CONTRIBUTING.md) - 贡献指南
- ✅ [AGENTS.md](../AGENTS.md) - AI代理开发指南
- ✅ [CHANGELOG.md](../CHANGELOG.md) - 变更日志

---

## 文档结构（Diátaxis框架）

### 1. 入门（Getting Started）

**适合：初次使用的开发者**

- [安装指南](./getting-started/installation.md) - 安装步骤和环境配置
- [快速开始](./getting-started/quickstart.md) - 30秒快速上手
- [常见问题](./getting-started/faq.md) - FAQ和故障排除
- [变更日志](./getting-started/change_log.md) - 版本更新记录

### 2. 使用指南（Guides）

**适合：解决特定问题的开发者**

- [使用指南](./guides/usage_guide.md) - 完整使用教程
- [最佳实践](./guides/best_practices.md) - 推荐的使用模式
- [性能优化](./guides/performance.md) - 性能调优建议
- [安全最佳实践](./guides/security_best_practices.md) - 安全配置
- [错误处理](./guides/error_handling.md) - 异常处理模式
- [参数配置](./guides/params.md) - 配置参数详解
- [API示例](./guides/examples/api_examples.md) - 代码示例

### 3. API参考（Reference）

**适合：查阅具体API的开发者**

- [API索引](./reference/index.md) - API完整索引
- [BtApi](./reference/bt_api.md) - 统一API入口
- [Registry](./reference/registry.md) - 交易所注册表
- [EventBus](./reference/event_bus.md) - 事件总线
- [数据容器](./reference/data_containers.md) - 27种数据容器
- [异常体系](./reference/exceptions.md) - 自定义异常
- [认证配置](./reference/auth_config.md) - 认证配置类
- [WebSocket](./reference/websocket.md) - WebSocket API

### 4. 深度解读（Explanation）

**适合：理解架构原理的开发者**

- [架构设计](./explanation/architecture.md) - 核心架构和设计模式
- [开发者指南](./explanation/developer_guide.md) - 深度开发指南
- [交易所集成模式](./explanation/exchange_integration_patterns.md) - 集成模式
- [多策略架构](./explanation/multi_strategy_architecture.md) - 策略架构

### 5. 交易所文档（Exchanges）

**交易所特定文档：**

- [Binance](./exchanges/binance/index.md) - 现货/合约/期权
- [OKX](./exchanges/okx/index.md) - 现货/合约
- [HTX](./exchanges/htx/index.md) - 现货/合约
- [CTP](./exchanges/ctp/quickstart.md) - 中国期货
- [Interactive Brokers](./exchanges/ib/index.md) - 股票/期货

---

## 项目入口点

### 1. 用户API入口

```python
from bt_api_py import BtApi

# 创建API实例
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "your_key",
        "secret": "your_secret",
    },
})

# 获取行情
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")

# 下单
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=50000,
)
```

### 2. 交易所注册

```python
from bt_api_py import ExchangeRegistry
from bt_api_py.feeds.live_newexchange import NewExchangeFeed

# 注册新交易所
ExchangeRegistry.register_feed("NEW___SPOT", NewExchangeFeed)

# 创建Feed实例
feed = ExchangeRegistry.create_feed("NEW___SPOT", data_queue)
```

### 3. 事件订阅

```python
from bt_api_py import EventBus

# 创建事件总线
bus = EventBus()

# 订阅事件
bus.on("ticker", lambda data: print(f"Ticker: {data}"))
bus.on("order", lambda data: print(f"Order: {data}"))

# 发布事件
bus.emit("ticker", ticker_data)
```

### 4. 数据容器

```python
from bt_api_py.containers.orders import BinanceOrderData

# 创建订单容器
order = BinanceOrderData(order_info, has_been_json_encoded=True)

# 初始化数据
order.init_data()

# 访问数据
symbol = order.get_symbol()
quantity = order.get_quantity()
price = order.get_price()
```

---

## 支持的交易所

### ✅ 完整支持（REST + WebSocket + 测试通过）

| 交易所 | 代码 | 现货 | 合约 | 期权 | 股票 | 单元测试 |
|--------|------|:----:|:----:|:----:|:----:|:--------:|
| **Binance** | `BINANCE___SPOT` / `BINANCE___SWAP` | ✅ | ✅ | ✅ | — | ✅ |
| **HTX (Huobi)** | `HTX___SPOT` / `HTX___USDT_SWAP` | ✅ | ✅ | ✅ | — | ✅ |
| **CTP** (中国期货) | `CTP___FUTURE` | — | — | — | — | ✅ |
| **Interactive Brokers** | `IB_WEB___STK` / `IB_WEB___FUT` | — | — | — | ✅ | ✅ |

### 🔧 已实现REST API（单元测试部分通过）

| 交易所 | 类型 | 测试状态 |
|--------|------|:--------:|
| OKX | CEX | ⚠️ 部分失败 |
| Bybit | CEX | ✅ 37通过 |
| Bitget | CEX | ✅ 45通过 |
| Kraken | CEX | ✅ 46通过 |
| Gate.io | CEX | ✅ 56通过 |
| Upbit | CEX | ✅ 101通过 |
| Crypto.com | CEX | ✅ 97通过 |
| HitBTC | CEX | ✅ 103通过 |
| Phemex | CEX | ✅ 65通过 |
| Gemini | CEX | ✅ 20通过 |
| KuCoin | CEX | ⚠️ 16失败/47通过 |
| MEXC | CEX | ⚠️ 11失败/42通过 |
| Bitfinex | CEX | ⚠️ 13失败/43通过 |
| Coinbase | CEX | ⚠️ 19失败/45通过 |
| Hyperliquid | DEX | ⚠️ 5失败/32通过 |
| dYdX | DEX | ✅ 通过 |
| BYDFi | CEX | ⚠️ 1失败/17通过 |

### 📋 已注册（基础框架就绪）

40+个交易所已完成注册和基础代码框架，单元测试全部通过。

---

## 核心架构

### 设计模式

**1. Registry Pattern（注册表模式）**
- 动态注册交易所
- 无需修改核心代码
- 支持运行时扩展

**2. Event-Driven Architecture（事件驱动架构）**
- EventBus事件总线
- 发布/订阅模式
- 松耦合组件通信

**3. Protocol-based Abstraction（协议抽象）**
- AbstractFeed协议
- 标准化接口定义
- 类型安全

**4. Factory Pattern（工厂模式）**
- Registry创建实例
- 统一的对象创建
- 依赖注入

**5. Data Container Pattern（数据容器模式）**
- 标准化数据结构
- 自动初始化（AutoInitMixin）
- 统一的访问接口

### 分层架构

```
用户应用层 (User Code)
    ↓
API层 (BtApi)
    ↓
集成层 (Feeds)
    ↓
数据层 (Containers)
    ↓
基础设施层 (Registry, EventBus, Exceptions)
```

---

## 数据容器

### 27种标准化数据容器

**市场数据：**
- Ticker（行情）
- Bar（K线）
- OrderBook（订单簿）
- MarkPrice（标记价格）
- FundingRate（资金费率）
- OpenInterest（持仓量）

**交易数据：**
- Order（订单）
- Trade（成交）
- Position（持仓）
- Balance（余额）
- Account（账户）
- Asset（资产）

**配置数据：**
- Symbol（交易对）
- Exchange（交易所配置）
- Instrument（合约信息）
- PriceLimit（价格限制）

**特殊数据：**
- Greeks（期权希腊值）
- Liquidation（强平）
- Pool（流动性池）
- Income（收益）
- RequestData（请求数据）
- Timer（定时器）

**平台特定：**
- CTP容器（中国期货）
- IB容器（Interactive Brokers）

---

## 开发工作流

### 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/cloudQuant/bt_api_py.git
cd bt_api_py

# 2. 安装开发依赖
make install-dev

# 3. 运行测试
make test

# 4. 检查代码质量
make check
```

### 常用命令

```bash
# 测试
make test              # 所有测试（排除CTP）
make test-fast         # 快速测试
make test-cov          # 带覆盖率
make test-html         # HTML报告

# 代码质量
make format            # 格式化代码
make lint              # Lint检查
make type-check        # 类型检查
make check             # 所有检查

# 安装
make install           # 安装包
make install-dev       # 安装开发依赖

# 清理
make clean             # 清理构建产物
make clean-test        # 清理测试产物
make clean-all         # 清理所有

# Pre-commit
make pre-commit        # 安装Git钩子
make pre-commit-run    # 运行所有检查
```

### 测试策略

**测试标记：**
- `@pytest.mark.unit` - 单元测试
- `@pytest.mark.integration` - 集成测试
- `@pytest.mark.slow` - 慢速测试（>1秒）
- `@pytest.mark.network` - 需要网络
- `@pytest.mark.binance` - Binance特定
- `@pytest.mark.okx` - OKX特定
- `@pytest.mark.ctp` - CTP特定
- `@pytest.mark.ib` - IB特定

**运行测试：**
```bash
# 运行特定标记
pytest tests -m unit -v
pytest tests -m "not slow and not network" -v
pytest tests -m binance -v

# 运行单个文件
pytest tests/test_registry.py -v

# 运行单个测试
pytest tests/test_registry.py::TestRegistryInstance::test_register_and_create_feed -v
```

---

## 代码风格

### 命名约定

**交易所标识：**
```
EXCHANGE___TYPE
例如：BINANCE___SPOT, OKX___SWAP, CTP___FUTURE, IB_WEB___STK
```

**文件和目录：**
- snake_case（如 `live_binance.py`, `binance_order.py`）

**类：**
- PascalCase（如 `BinanceSpotFeed`, `BinanceOrderData`）

**函数和方法：**
- snake_case（如 `get_ticker`, `create_order`）

**常量：**
- UPPER_SNAKE_CASE（如 `DEFAULT_TIMEOUT`, `MAX_RETRIES`）

**私有属性：**
- 前缀下划线（如 `_feed_classes`, `_default`）

### 类型提示

```python
# 公共API必须添加类型提示
def calculate_position_size(
    account_balance: float,
    risk_percent: float,
    stop_loss_distance: float
) -> float:
    """计算仓位大小"""
    risk_amount = account_balance * (risk_percent / 100)
    return risk_amount / stop_loss_distance

# 使用现代Python 3.11+语法
def get_exchange_names(self) -> list[str]:
    return list(self._feed_classes.keys())

# 使用联合类型
def get_stream_class(
    self,
    exchange_name: str,
    stream_type: str
) -> Any | None:
    return self._stream_classes.get(exchange_name, {}).get(stream_type)
```

### 错误处理

**使用自定义异常：**

```python
from bt_api_py.exceptions import (
    BtApiError,
    ExchangeNotFoundError,
    OrderError,
    InsufficientBalanceError,
)

# ✓ 正确
def create_order(self, symbol: str, quantity: float) -> Order:
    if not self.has_balance(quantity):
        raise InsufficientBalanceError(
            exchange_name=self.exchange_name,
            symbol=symbol,
            required=quantity,
            available=self.balance
        )
    # 订单创建逻辑

# ✗ 错误
def create_order(self, symbol: str, quantity: float):
    assert quantity > 0  # 不要使用assert
    raise Exception("Error")  # 不要使用通用异常
```

---

## 项目目录结构

```
bt_api_py/
├── bt_api_py/              # 主要Python包
│   ├── bt_api.py          # BtApi主类 [入口点]
│   ├── registry.py        # ExchangeRegistry [核心架构]
│   ├── event_bus.py       # EventBus [核心架构]
│   ├── exceptions.py      # 异常层次结构 [错误处理]
│   ├── containers/        # 数据容器 [数据层]
│   │   ├── orders/       # 订单容器
│   │   ├── tickers/      # 行情容器
│   │   ├── bars/         # K线容器
│   │   └── ...           # 其他24种容器
│   ├── feeds/             # 交易所Feed [集成层]
│   │   ├── live_binance/ # Binance实现
│   │   ├── live_okx/     # OKX实现
│   │   ├── live_htx/     # HTX实现
│   │   └── ...           # 其他70+交易所
│   ├── ctp/               # CTP扩展 [C++绑定]
│   ├── functions/         # 功能函数 [业务逻辑]
│   ├── utils/             # 工具函数 [辅助工具]
│   └── exchange_registers/ # 交易所注册 [自动加载]
├── tests/                 # 测试套件 [质量保证]
│   ├── feeds/            # Feed测试
│   ├── containers/       # 容器测试
│   └── test_*.py         # 其他测试
├── docs/                  # 文档 [用户指南]
│   ├── getting-started/  # 入门
│   ├── guides/           # 使用指南
│   ├── reference/        # API参考
│   ├── explanation/      # 深度解读
│   └── exchanges/        # 交易所文档
├── pyproject.toml         # 项目配置 [核心配置]
├── setup.py               # 安装脚本 [构建配置]
├── Makefile               # 常用命令 [开发工具]
├── run_tests.sh           # 测试脚本 [测试工具]
├── README.md              # 项目说明 [主要文档]
├── CONTRIBUTING.md        # 贡献指南 [开发指南]
└── AGENTS.md              # AI代理指南 [AI开发]
```

---

## 关键文件

### 核心文件

- `bt_api_py/bt_api.py` - BtApi主类，统一API入口
- `bt_api_py/registry.py` - ExchangeRegistry，交易所注册表
- `bt_api_py/event_bus.py` - EventBus，事件总线
- `bt_api_py/exceptions.py` - 自定义异常层次结构
- `bt_api_py/__init__.py` - 包入口，导出主要API

### 配置文件

- `pyproject.toml` - 项目配置、依赖、工具配置
- `setup.py` - 安装脚本、C/C++扩展编译
- `Makefile` - 常用命令快捷方式
- `.pre-commit-config.yaml` - Pre-commit钩子配置
- `conftest.py` - Pytest全局配置

### 文档文件

- `README.md` - 项目主要说明
- `CONTRIBUTING.md` - 贡献指南
- `AGENTS.md` - AI代理开发指南
- `CHANGELOG.md` - 变更日志
- `docs/index.md` - 文档主页

---

## 异常层次结构

```
BtApiError (基类)
├── ExchangeNotFoundError - 交易所未注册
├── ExchangeConnectionError - 连接失败
│   └── AuthenticationError - 认证失败
├── RequestError - REST请求失败
│   └── RateLimitError - API速率限制
├── RequestTimeoutError - 请求超时
├── OrderError - 订单操作失败
│   ├── InsufficientBalanceError - 余额不足
│   ├── InvalidOrderError - 无效订单参数
│   └── OrderNotFoundError - 订单不存在
├── SubscribeError - 订阅失败
├── DataParseError - 数据解析失败
├── NetworkError - 网络错误
├── InvalidSymbolError - 无效交易对
├── ConfigurationError - 配置错误
├── WebSocketError - WebSocket错误
└── RequestFailedError - 通用请求失败
```

---

## 性能特性

### Cython扩展

- `functions/calculate_number.py` - 数值计算优化
- 编译为`.so`（Linux/macOS）或`.pyd`（Windows）
- 性能提升10-100倍

### C++绑定

- CTP使用SWIG绑定C++库
- 直接调用底层高性能API
- 支持Linux/Windows/macOS

### 异步支持

- aiohttp用于异步HTTP请求
- asyncio协程支持
- 并发请求处理

---

## 测试覆盖率

### 统计

- 测试文件：201个
- 测试框架：pytest + xdist（并行测试）
- 覆盖率目标：>60%
- 关键路径：100%（Registry, EventBus, BtApi）

### 查看覆盖率

```bash
make test-cov
open htmlcov/index.html
```

---

## 获取帮助

### 文档资源

- **在线文档**：https://cloudquant.github.io/bt_api_py/
- **GitHub仓库**：https://github.com/cloudQuant/bt_api_py
- **PyPI包**：https://pypi.org/project/bt_api_py/

### 社区支持

- **Issues**：https://github.com/cloudQuant/bt_api_py/issues
- **Email**：yunjinqi@gmail.com

---

## 相关文档

### 核心文档
- [项目概览](./project-overview.md) - 完整项目介绍
- [源代码树分析](./source-tree-analysis.md) - 目录结构详解
- [开发指南](./development-guide.md) - 开发设置和最佳实践

### 入门文档
- [安装指南](./getting-started/installation.md) - 安装步骤
- [快速开始](./getting-started/quickstart.md) - 30秒上手
- [常见问题](./getting-started/faq.md) - FAQ

### 使用指南
- [使用指南](./guides/usage_guide.md) - 完整教程
- [最佳实践](./guides/best_practices.md) - 推荐模式
- [性能优化](./guides/performance.md) - 性能调优

### API参考
- [BtApi参考](./reference/bt_api.md) - 主API文档
- [Registry参考](./reference/registry.md) - 注册表文档
- [数据容器参考](./reference/data_containers.md) - 容器文档

### 深度解读
- [架构设计](./explanation/architecture.md) - 架构原理
- [开发者指南](./explanation/developer_guide.md) - 深度指南

---

## 快速开始

### 安装

```bash
pip install bt_api_py
```

### 基本使用

```python
from bt_api_py import BtApi

# 创建API实例
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    },
})

# 获取行情
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
print(f"BTC 现价: {ticker.get_last_price()}")

# 下单交易
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=50000,
    order_type="limit",
)

# 批量查询所有交易所行情
all_tickers = api.get_all_ticks("BTCUSDT")
```

---

## 总结

**bt_api_py** 是一个成熟、专业、高性能的多交易所交易API框架。通过统一接口、标准化数据和即插即用架构，极大地简化了量化交易系统的开发。

**核心优势：**
- ✅ 73+交易所支持
- ✅ 27种标准化数据容器
- ✅ 三种API模式（同步/异步/WebSocket）
- ✅ 完善的测试覆盖（201个测试文件）
- ✅ 高性能Cython/C++扩展
- ✅ 活跃的社区和完善的文档

**适用场景：**
- 量化交易策略开发
- 套利交易系统
- 做市商系统
- 资产管理平台
- 交易机器人

**开始使用：**
```bash
pip install bt_api_py
```

欢迎加入bt_api_py社区！🚀
