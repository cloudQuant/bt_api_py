# 实现任务 - 全球多市场统一交易框架

## 任务概览

```bash
阶段 0: 统一协议与基础设施
    ↓
阶段 1: 核心场所稳态化 (Binance/OKX/CTP/IB/QMT)
    ↓
阶段 2: 10 个核心 CEX
    ↓
阶段 3: 50+ 扩展 CEX
    ↓
阶段 4: DEX (类 CEX + 链上)
    ↓
阶段 5: 文档与工具

```bash

- --

## 阶段 0：统一协议与基础设施

### 任务 0.1：AbstractVenueFeed 协议

- [ ] 0.1.1 创建 `bt_api_py/feeds/abstract_feed.py`
    - 定义 `AbstractVenueFeed` Protocol（方法签名必须兼容现有 Feed 的 `extra_data` + `**kwargs` 模式）
    - 包含所有核心方法签名（同步 + 异步）
    - 定义 `capabilities` 属性
    - 实现 `AsyncWrapperMixin`（为非 HTTP 场所提供 run_in_executor 包装）

- [ ] 0.1.2 创建协议符合性检查工具
    - `check_protocol_compliance(feed_class)` 函数
    - 返回缺失方法列表

- [ ] 0.1.3 编写测试
    - 测试 Protocol 定义
    - 测试符合性检查

- *预计工时**: 1-2 天

- --

### 任务 0.2：Capability 机制

- [ ] 0.2.1 创建 `bt_api_py/feeds/capability.py`
    - 定义 `Capability` 枚举（完整版）
    - 定义 `CapabilityProvider` 基类
    - 定义 `NotSupportedError` 异常

- [ ] 0.2.2 编写测试
    - 测试 Capability 枚举完整性
    - 测试 capability 检查逻辑

- *预计工时**: 1 天

- --

### 任务 0.3：Instrument 模型

- [ ] 0.3.1 创建 `bt_api_py/containers/instrument.py`
    - 定义 `Asset` 枚举
    - 定义 `Instrument` dataclass（完整版）
    - 实现 `is_expired`、`is_listed` 属性
    - 实现 `with_params` 方法

- [ ] 0.3.2 创建 `bt_api_py/instrument_manager.py`
    - 实现 `InstrumentManager` 类
    - 实现注册、查询、转换方法
    - 实现从配置加载

- [ ] 0.3.3 整合 `SymbolManager`
    - 保留现有字符串映射功能
    - 添加 Instrument 支持
    - 提供兼容层

- [ ] 0.3.4 编写测试
    - 测试 Instrument 创建
    - 测试 InstrumentManager 注册/查询
    - 测试符号转换

- *预计工时**: 2-3 天

- --

### 任务 0.4：HTTP 客户端升级

- [ ] 0.4.1 创建 `bt_api_py/feeds/http_client.py`
    - 实现 `HttpClient` 类
    - 实现同步 `request()` 方法
    - 实现异步 `async_request()` 方法
    - 集成错误处理
    - 实现连接池配置

- [ ] 0.4.2 重构 `bt_api_py/feeds/feed.py`
    - 移除 requests 依赖
    - 使用 `HttpClient` 替代直接 requests 调用
    - 更新 `http_request()` 方法签名
    - 保持现有方法向后兼容

- [ ] 0.4.3 编写测试
    - 测试 HTTP 客户端功能
    - 测试同步/异步请求
    - 性能基准测试

- *预计工时**: 2-3 天

- --

### 任务 0.5：统一限流器

- [ ] 0.5.1 创建 `bt_api_py/rate_limiter.py`
    - 定义限流相关枚举
    - 定义 `RateLimitRule` dataclass
    - 实现 `SlidingWindowLimiter`
    - 实现 `FixedWindowLimiter`
    - 实现 `RateLimiter` 统一入口
    - 支持端点 glob 匹配
    - 支持权重映射

- [ ] 0.5.2 编写测试
    - 测试滑动窗口限流
    - 测试固定窗口限流
    - 测试端点匹配
    - 测试权重计算
    - 性能测试（10 万次调用）

- *预计工时**: 3-4 天

- --

### 任务 0.6：配置系统与 Schema 校验

- [ ] 0.6.1 创建 `bt_api_py/config_loader.py`
    - 定义所有 pydantic 模型
    - 实现 `load_exchange_config()` 函数
    - 支持热重载（开发环境）
    - 配置文件不存在时抛出清晰错误

- [ ] 0.6.2 创建配置目录和示例
    - `bt_api_py/configs/exchanges/.gitkeep`
    - `configs/exchanges/_schema.yaml`（配置模板）

- [ ] 0.6.3 迁移现有配置
    - 创建 `binance.yaml`
    - 创建 `okx.yaml`
    - 创建 `ctp.yaml`
    - 验证配置与代码一致性

- [ ] 0.6.4 编写测试
    - 测试配置加载
    - 测试 schema 校验
    - 测试配置验证

- *预计工时**: 2-3 天

- --

### 任务 0.7：统一错误框架

- [ ] 0.7.1 创建 `bt_api_py/error_framework.py`
    - 定义 `ErrorCategory` 枚举
    - 定义 `UnifiedErrorCode` int 枚举
    - 定义 `UnifiedError` dataclass
    - 定义 `ErrorTranslator` 基类
    - 实现 `CEXErrorTranslator`
    - 实现 `BinanceErrorTranslator`
    - 实现 `OKXErrorTranslator`
    - 实现 `CTPErrorTranslator`

- [ ] 0.7.2 集成到 Feed 基类
    - 添加 `_get_error_translator()` 方法
    - 在 `http_request()` 后调用翻译器

- [ ] 0.7.3 编写测试
    - 测试所有错误码映射
    - 测试错误翻译器
    - 测试错误上下文保留

- *预计工时**: 2-3 天

- --

### 任务 0.8：连接管理混入（仅用于 Feed）

> **注意**: `BaseDataStream` 已有完整的 ConnectionState 管理，此任务仅为 Feed（REST 请求类）添加统一连接生命周期接口。

- [ ] 0.8.1 创建 `bt_api_py/feeds/connection_mixin.py`
    - 定义 `ConnectionState` 枚举
    - 实现 `ConnectionMixin` 类（仅用于 Feed 基类）
    - HTTP 场所的 connect/disconnect 默认为 no-op
    - CTP/IB/QMT 子类覆盖为显式连接管理

- [ ] 0.8.2 集成到 `feed.py` 基类
    - Feed 继承 ConnectionMixin
    - 不修改 BaseDataStream（已有状态管理）

- [ ] 0.8.3 编写测试
    - 测试状态转换
    - 测试 HTTP 场所 no-op 行为

- *依赖**: 任务 0.1
- *预计工时**: 1-2 天

- --

### 任务 0.9：Feed 基类重构

> 确保现有 Feed 基类符合 AbstractVenueFeed 协议，同时保持向后兼容。

- [ ] 0.9.1 为 Feed 基类添加 `connect()`/`disconnect()`/`is_connected()` 方法
    - 默认为 no-op
    - 现有子类无需修改

- [ ] 0.9.2 为 Feed 基类添加 `capabilities` 属性
    - 默认返回空集合
    - 现有子类逐步声明

- [ ] 0.9.3 验证 `isinstance(binance_feed, AbstractVenueFeed)` 通过

- [ ] 0.9.4 废弃旧方法时添加 `warnings.warn`

- *依赖**: 任务 0.1, 0.2, 0.8
- *预计工时**: 2-3 天

- --

### 阶段 0 检查点

- [ ] 所有测试通过
- [ ] 性能基准满足目标
- [ ] 代码审查完成
- [ ] 现有 Binance/OKX 仍能正常运行（向后兼容回归测试）

- *累计工时**: 20-30 天

- --

## 阶段 1：核心场所稳态化

### 任务 1.1：Binance 对齐

- [ ] 1.1.1 实现 `BinanceSwapFeed.capabilities()`
- [ ] 1.1.2 实现 `BinanceSpotFeed.capabilities()`
- [ ] 1.1.3 引入 `HttpClient` 替换直接 requests 调用
- [ ] 1.1.4 引入 `RateLimiter`
- [ ] 1.1.5 引入 `BinanceErrorTranslator`
- [ ] 1.1.6 实现 `connect()`/`disconnect()`（no-op）
- [ ] 1.1.7 实现真实异步方法（使用 httpx）
- [ ] 1.1.8 配置驱动化（创建 `binance.yaml`）
- [ ] 1.1.9 编写测试

- *预计工时**: 3-4 天

- --

### 任务 1.2：OKX 对齐

- [ ] 1.2.1 - 1.2.8 同 Binance 流程

- *预计工时**: 3-4 天

- --

### 任务 1.3：CTP 对齐

- [ ] 1.3.1 实现 `CtpFeed.capabilities()`
- [ ] 1.3.2 实现 `connect()`/`disconnect()`（显式连接管理）
- [ ] 1.3.3 引入 `CTPErrorTranslator`
- [ ] 1.3.4 配置驱动化（创建 `ctp.yaml`）
- [ ] 1.3.5 编写测试

- *预计工时**: 3-4 天

- --

### 任务 1.4：IB 可用化

- [ ] 1.4.1 创建 `bt_api_py/feeds/live_ib_feed.py`
    - 使用 ib_insync
    - 实现连接管理
    - 实现 STK/FUT/OPT 下单
    - 实现市场数据订阅
    - 实现账户数据流

- [ ] 1.4.2 创建 `bt_api_py/containers/ib/` 数据容器

- [ ] 1.4.3 创建 `configs/exchanges/ib.yaml`

- [ ] 1.4.4 注册到 Registry

- [ ] 1.4.5 编写测试（使用 paper account）

- *预计工时**: 5-7 天

- --

### 任务 1.5：QMT 对接

- [ ] 1.5.1 创建 `bt_api_py/feeds/live_qmt_feed.py`
    - 封装 xtquant 连接
    - 实现事件驱动机制
    - 实现 A 股/ETF 下单
    - 实现行情订阅

- [ ] 1.5.2 创建 `bt_api_py/containers/qmt/` 数据容器

- [ ] 1.5.3 创建 `configs/exchanges/qmt.yaml`

- [ ] 1.5.4 实现 CI/CD 跳过逻辑

- [ ] 1.5.5 编写测试（mock 数据）

- *预计工时**: 5-7 天

- --

### 任务 1.6：统一测试套件

- [ ] 1.6.1 创建 `tests/feeds/test_abstract_feed.py`
    - 测试所有 Feed 实现符合协议
    - 参数化测试所有方法

- [ ] 1.6.2 创建 `tests/feeds/base_test_suite.py`
    - 定义通用测试基类
    - 包含：下单、撤单、查询、行情等测试

- [ ] 1.6.3 为每个已实现场所运行测试套件

- *预计工时**: 2-3 天

- --

### 阶段 1 检查点

- [ ] Binance/OKX/CTP/IB/QMT 全部对齐
- [ ] 统一测试套件全部通过
- [ ] 性能基准测试通过

- *累计工时**: 41-60 天（含阶段 0）

- --

## 阶段 2：10 个核心 CEX

每个 CEX 交付标准：

- [ ] 配置文件
- [ ] Feed 实现（同步 + 异步）
- [ ] DataStream 实现
- [ ] ErrorTranslator
- [ ] Fixture + Mock 测试
- [ ] 集成测试（测试网/沙盒）

### 任务 2.1：Bybit 集成

- *预计工时**: 4-5 天

### 任务 2.2：Bitget 集成

- *预计工时**: 4-5 天

### 任务 2.3：Kraken 集成

- *预计工时**: 4-5 天

### 任务 2.4：KuCoin 集成

- *预计工时**: 4-5 天

### 任务 2.5：Gate.io 集成

- *预计工时**: 4-5 天

### 任务 2.6：Coinbase 集成

- *预计工时**: 3-4 天（主要 SPOT）

### 任务 2.7：HTX (Huobi) 集成

- *预计工时**: 4-5 天

### 任务 2.8：MEXC 集成

- *预计工时**: 4-5 天

### 任务 2.9：Crypto.com 集成

- *预计工时**: 4-5 天

### 任务 2.10：Bitfinex 集成

> 替换原 Binance US（已在多个地区停止服务）

- *预计工时**: 4-5 天

- --

### 阶段 2 检查点

- [ ] 10 个 CEX 全部实现
- [ ] 统一测试套件全部通过
- [ ] 代码覆盖率 > 80%

- *累计工时**: 62-90 天（含阶段 0-1）

- --

## 阶段 3：扩展 50+ CEX

### 任务 3.1：批量工具开发

- [ ] 3.1.1 完善 `bt_api_py/tools/generate_scaffold.py`
- [ ] 3.1.2 完善 `bt_api_py/tools/validate_exchange.py`
- [ ] 3.1.3 创建进度追踪 `configs/exchange_progress.yaml`

- *预计工时**: 2-3 天

- --

### 任务 3.2：批量集成

按优先级排序的交易所列表（第二批）：

| 交易所 | 优先级 | 预计工时 |

|--------|--------|----------|

| BitMEX | 高 | 3 天 |

| Bitfinex | 高 | 4 天 |

| Kraken Futures | 高 | 3 天 |

| KuCoin Futures | 中 | 3 天 |

| Gate.io Futures | 中 | 3 天 |

| Bitstamp | 中 | 3 天 |

| Gemini | 中 | 3 天 |

| Bittrex | 低 | 3 天 |

| Poloniex | 低 | 3 天 |

| ... | ... | ... |

（此处列出 50 个交易所清单）

- --

## 阶段 4：DEX 集成

### 任务 4.1：DEX 基础设施

- [ ] 4.1.1 创建 `bt_api_py/feeds/dex/base.py`
    - 定义 `DexFeed` 基类
    - 定义链上交易接口

- [ ] 4.1.2 创建 `bt_api_py/feeds/dex/evm.py`
    - EVM DEX 基类
    - Web3 集成

- [ ] 4.1.3 实现通用功能
    - Gas 费估算
    - 余额查询
    - 授权管理

- *预计工时**: 5-7 天

- --

### 任务 4.2：类 CEX DEX

- [ ] 4.2.1 Hyperliquid 集成
- [ ] 4.2.2 dYdX v4 集成

- *预计工时**: 8-10 天

- --

### 任务 4.3：Uniswap 系

- [ ] 4.3.1 Uniswap V2 集成
- [ ] 4.3.2 Uniswap V3 集成
- [ ] 4.3.3 PancakeSwap 集成（BSC）
- [ ] 4.3.4 SushiSwap 集成（多链）

- *预计工时**: 10-12 天

- --

### 任务 4.4：Solana DEX

- [ ] 4.4.1 创建 Solana DEX 基类
- [ ] 4.4.2 Jupiter 集成
- [ ] 4.4.3 Raydium 集成
- [ ] 4.4.4 Orca 集成

- *预计工时**: 10-12 天

- --

## 阶段 5：文档与工具

### 任务 5.1：文档生成器

- [ ] 5.1.1 从代码生成文档的工具
- [ ] 5.1.2 生成覆盖清单
- [ ] 5.1.3 生成能力矩阵

- *预计工时**: 5-7 天

- --

## 质量门禁

每个阶段必须满足以下质量标准：

- [ ] Mock 测试必须通过
- [ ] 关键场所集成测试可用
- [ ] 文档自动生成
- [ ] 性能基准达标
- [ ] 代码覆盖率 > 80%

- --

## 风险与缓解

| 风险 | 影响 | 缓解措施 |

|------|------|----------|

| QMT 环境依赖 | 高 | 本地模拟 + CI 跳过 |

| IB 权限限制 | 中 | paper account + mock |

| CTP 流控限制 | 中 | 严格限流 + 批量查询减少 |

| CEX API 变更 | 高 | 配置版本化 + fixture 回归 |

| 异步实现复杂 | 中 | AsyncWrapperMixin 提供默认包装，HTTP 场所用 httpx 真异步 |

| 向后兼容风险 | 高 | Protocol 签名兼容现有 Feed，废弃方法保留一个大版本 |
