# 需求文档 - 全球主要交易所与券商统一交易框架

## 项目背景

bt_api_py 是一个统一的多交易所交易框架，当前已支持 Binance、OKX、CTP，并提供 IB 连接骨架。项目目标升级为 **覆盖全球主要交易所与券商** 的统一交易框架，范围包括：

- 数字货币 CEX 与 DEX
- 中国股票市场（QMT）
- 中国期货/期权市场（CTP）
- 海外券商与多资产市场（IB）

该框架需在 **统一接口与内部模型** 的前提下，兼容 **HTTP/WS**（CEX/类 CEX DEX）、**SPI/回调**（CTP）、**TWS/Gateway**（IB）、**本地终端 API**（QMT）等不同技术形态。

## 术语表

| 术语 | 定义 |
|------|------|
| **Venue** | 交易场所的统一称呼，可能是交易所或券商，如 BINANCE、CTP、IB、QMT |
| **CEX** | 中心化交易所，基于 REST API/WebSocket |
| **DEX** | 去中心化交易所，链上或类 CEX API |
| **Broker** | 券商或网关，使用原生 API（CTP/IB/QMT） |
| **Feed** | 请求/交易接口，继承 `Feed` 基类 |
| **DataStream** | 推送/流式接口，继承 `BaseDataStream` |
| **ExchangeData** | 场所配置类，存储连接参数、路径、周期映射 |
| **Registry** | 全局注册表 `ExchangeRegistry`，管理所有场所组件 |
| **Asset Type** | 资产类型：SPOT、SWAP、FUTURE、OPTION、STK、FUND、BOND、FX 等 |
| **Instrument** | 交易标的的统一表示，包含交易所标识、代码、到期日、合约乘数等元信息 |
| **Symbol Mapping** | 内部标识 ↔ 场所标识映射，通过 `SymbolManager` 管理 |
| **RateLimiter** | 限流器，控制 HTTP 请求频率与权重 |
| **Capability** | 能力描述，标识某场所是否支持某方法，如 cancel_all、get_kline |

---

## 需求分类

### 阶段 0：多市场统一架构（必需）

在扩展到全球多类型市场前，必须解决架构不一致与可扩展性问题。

#### 需求 0.1：统一 Venue 接口协议

**用户故事：** 作为开发者，我希望 CEX/DEX/CTP/IB/QMT 使用统一协议接口，以便上层 BtApi 透明调用。

**验收标准：**
1. 定义 `AbstractVenueFeed` 协议，包含核心方法 `get_tick`、`get_depth`、`make_order`、`cancel_order`、`get_balance`、`get_kline`
2. 同步与异步方法同时存在，且异步方法具备真实实现
3. 引入 `capabilities` 机制，缺失能力时返回标准化 `NotSupported`
4. 协议覆盖 CEX/DEX/Broker 三类场所

#### 需求 0.2：统一连接生命周期

**用户故事：** 作为使用者，我希望所有场所有一致的连接、断开、重连、心跳管理方式。

**验收标准：**
1. Feed 层新增 `connect()` / `disconnect()` 标准方法（对 HTTP 场所可为空实现）
2. DataStream 统一状态机（连接中/已连接/断开/重连）
3. 对 CTP/IB/QMT 这类长连接场所提供显式连接管理

#### 需求 0.3：统一 Instrument 模型

**用户故事：** 作为开发者，我希望用统一的 Instrument 表示现货、期货、期权、股票、外汇等标的。

**验收标准：**
1. 定义 `Instrument` 数据模型（symbol、venue、asset_type、expiry、strike、multiplier 等）
2. `SymbolManager` 支持存储 Instrument 元信息并做双向映射
3. 对 CTP 合约、IB Contract、QMT 证券代码都可统一映射

#### 需求 0.4：配置与代码职责边界重定义

**用户故事：** 作为开发者，我希望静态信息可配置，动态逻辑写代码，避免重复和硬编码。

**验收标准：**
1. 配置文件支持：URL/前置地址、路径映射、周期映射、支持资产类型
2. CTP/IB/QMT 等非 HTTP 场所也支持配置化连接参数
3. 配置必须通过 schema 校验，缺字段时报清晰错误

#### 需求 0.5：HTTP 客户端升级

**用户故事：** 作为开发者，我希望同步与异步请求共用同一 HTTP 客户端库。

**验收标准：**
1. 使用 httpx 替换 requests
2. 连接池复用，支持异步 client
3. 对非 HTTP 场所不强制依赖 httpx
4. 错误处理标准化（含 status code、响应内容）

#### 需求 0.6：统一限流器

**用户故事：** 作为用户，我希望框架自动处理不同交易所限流规则。

**验收标准：**
1. 支持滑动窗口、固定窗口、端点级限流
2. 配置化限流规则（含 weight/endpoint）
3. 对非 HTTP 场所可关闭或替换为场所内置流控

#### 需求 0.7：统一错误框架

**用户故事：** 作为用户，我希望所有场所的错误都统一处理，便于调试和异常处理。

**验收标准：**
1. 定义统一错误码体系（网络/认证/限流/业务/系统/能力 分类）
2. 每个场所实现错误翻译器（场所原始错误 → 统一错误）
3. 错误包含上下文信息（场所、请求、响应）
4. 错误日志标准化

#### 需求 0.8：非 HTTP 场所异步策略

**用户故事：** 作为开发者，我希望非 HTTP 场所（CTP/IB/QMT）也能在异步上下文中使用，无需特殊处理。

**验收标准：**
1. HTTP 场所（CEX/类CEX DEX）使用 httpx 提供真正的异步实现
2. 非 HTTP 场所（CTP/IB/QMT）通过 `asyncio.to_thread` / `run_in_executor` 自动包装同步方法
3. 上层 BtApi 无需区分场所类型即可调用同步或异步版本
4. `AbstractVenueFeed` 协议中异步方法提供默认包装实现，子类可选择性覆盖

#### 需求 0.9：向后兼容策略

**用户故事：** 作为现有用户，我希望升级框架后现有代码仍能正常运行。

**验收标准：**
1. 现有 `Feed` 基类的方法签名保持兼容（`extra_data` + `**kwargs` 模式不变）
2. `AbstractVenueFeed` 协议的方法签名必须是现有 `Feed` 签名的超集
3. `ExchangeRegistry` API 不变
4. 新增的 `connect()`/`disconnect()` 对 HTTP 场所默认为空实现
5. 废弃的方法通过 `warnings.warn` 提示，保留至少一个大版本

---

### 阶段 1：核心场所稳态化（现有 + 关键场所）

#### 需求 1.1：现有场所对齐统一协议

**验收标准：**
1. Binance、OKX、CTP 完成协议对齐（声明 capabilities、集成 ErrorTranslator、使用 HttpClient）
2. 现有接口保持向后兼容（方法签名不变，新增 connect/disconnect 为 no-op）
3. 使用 `respx` mock 的单元测试通过
4. 配置驱动化（每个场所创建 YAML 配置文件）

#### 需求 1.2：IB 可用化

**验收标准：**
1. IB TWS/Gateway 连接可用
2. 支持 STK/FUT/OPT 基础下单与查询
3. Market/Account 流式订阅可用

#### 需求 1.3：QMT 对接

**验收标准：**
1. QMT 连接方式明确（本地终端/SDK）并实现基础封装
2. 支持 A 股/ETF 基础下单、撤单、查询
3. 行情订阅与交易回报推送可用

---

### 阶段 2：Crypto CEX 核心扩展

第一批 10 CEX 交易所（Binance/OKX 已完成）：Bybit、Bitget、Kraken、KuCoin、Gate.io、Coinbase（Advanced Trade）、HTX、MEXC

> **注意事项：**
> - Coinbase Pro 已合并到 Coinbase Advanced Trade
> - Binance US 在多个地区已停止服务，暂不纳入

**验收标准：**
1. 至少支持 SPOT + SWAP（Coinbase 仅 SPOT）
2. 统一 Feed/DataStream 接口
3. 通过统一测试套件（`ExchangeTestBase`）
4. 每个交易所提供 fixture JSON 用于 mock 离线测试
5. 符号映射不能硬编码字符数，需支持变长 base（如 DOGE-USDT）

---

### 阶段 3：扩展 CEX

扩展至 50+ 中大型 CEX，按交易量与可用性排序。

**验收标准：**
1. 代码覆盖率 80%+
2. 文档自动生成

---

### 阶段 4：DEX 集成

支持类 CEX DEX + 链上 DEX（EVM + Solana）。

**DEX 分类：**

| 分类 | 代表 | 接入方式 | 基类 |
|------|------|----------|------|
| 类 CEX DEX | Hyperliquid, dYdX v4 | REST/WS API | `Feed`（与 CEX 相同） |
| EVM 链上 DEX | Uniswap, PancakeSwap, SushiSwap | 智能合约（web3.py） | `EvmDexFeed` |
| Solana 链上 DEX | Jupiter, Raydium, Orca | Solana RPC（solana-py） | `SolanaDexFeed` |

**验收标准：**
1. 三类 DEX 均实现 `AbstractVenueFeed` 协议
2. 类 CEX DEX 直接使用 `Feed` 基类，与 CEX 完全一致
3. 支持 quote/swap/liquidity 查询
4. EVM DEX 支持 token approve 和 gas 估算
5. Solana DEX 支持 transaction 构建和签名

---

### 阶段 5：文档与工具

**验收标准：**
1. 文档自动生成
2. 支持全局覆盖清单与能力矩阵

---

## 非功能性需求

### 性能
1. 框架自身处理开销 < 10ms（不含网络）
2. HTTP 连接池复用，避免重复握手
3. 支持 50+ 并发 WebSocket/长连接
4. 内存占用 < 500MB（10 个场所同时连接）

### 可维护性
1. 新 CEX 场所集成时间 < 2 周（使用脚手架）
2. 代码重复率 < 30%
3. 所有公共 API 有 docstring
4. 测试覆盖率 > 80%

### 可靠性
1. 网络错误自动重试（指数退避，最大 3 次）
2. WebSocket/长连接支持自动重连（指数退避，最大间隔 60s）
3. 限流自动处理（RateLimiter 自动等待）
4. 资源自动清理（HTTP 连接池、WebSocket 连接、线程）
5. 错误统一分类与日志标准化

### 安全
1. API Key/Secret 脱敏（日志中 mask）
2. 私钥不落盘，不进版本控制
3. 权限最小化

### 兼容性
1. Python 3.9+（httpx 最低要求）
2. Linux/macOS/Windows
3. 对 Windows-only 的 QMT 允许特例部署
4. QMT 相关测试在 CI 环境自动跳过（非 Windows 或缺少 xtquant 时）

---

## 依赖关系

```
阶段 0 (统一架构: 协议 + Instrument + 配置 + httpx + 限流器 + 错误框架)
    ↓
阶段 1 (核心场所: Binance/OKX/CTP 对齐 + IB 可用化 + QMT 对接)
    ↓
阶段 2 (10 个核心 CEX)     阶段 5 (文档，与 2/3/4 并行)
    ↓
阶段 3 (50+ 扩展 CEX)
    ↓
阶段 4 (DEX: 2 类CEX + 5 EVM + 3 Solana)
```

## 工时预估（含 50% buffer）

| 阶段 | 预估工时 | 说明 |
|------|----------|------|
| 阶段 0 | 20-30 天 | 架构与协议统一 |
| 阶段 1 | 20-30 天 | IB + QMT + 现有对齐 |
| 阶段 2 | 40-50 天 | 10 个核心 CEX |
| 阶段 3 | 100-150 天 | 50 个 CEX |
| 阶段 4 | 40-60 天 | 10+ DEX |
| 阶段 5 | 5-8 天 | 文档与工具 |
