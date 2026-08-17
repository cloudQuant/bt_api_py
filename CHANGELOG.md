# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- Fix deprecated `logger.warn()` → `logger.warning()` across 80+ files
- Fix MEXC WebSocket `_setup_logger` indentation error in `market_wss_base.py` and `account_wss_base.py`
- Replace residual `print()` with logger in `MyWebsocketApp.message_rsp()`
- Fix hardcoded salt in `security.py` — now uses random salt via `os.urandom(16)`

### Changed
- Remove `cython` from runtime dependencies (build-only dependency)
- Relax `numpy` version constraint from `<2.0.0` to allow NumPy 2.x
- CI: MyPy type checking now fails CI on errors (was `continue-on-error`)
- CI: Bandit security scan now fails CI on findings (was `continue-on-error`)
- Add pytest timeout (30s per test) to prevent hanging tests

### Added
- `CHANGELOG.md` — this file

## [0.15.0] - 2025-01-01

### Added
- Unified `BtApi` interface — call `get_tick()`, `make_order()` etc. directly on `BtApi`
- `AbstractVenueFeed` protocol and `AsyncWrapperMixin`
- `RateLimiter` with sliding window / fixed window modes
- `ConnectionPool` for HTTP/WebSocket connection reuse
- `SecureCredentialManager` for encrypted API key storage
- Custom exception hierarchy (`BtApiError`, `ExchangeNotFoundError`, etc.)
- 73 exchange registrations
- WebSocket exponential backoff reconnection in `MyWebsocketApp`
- Comprehensive test suite with unit/integration markers
- MkDocs documentation site
- GitHub Actions CI/CD (tests, lint, security)

## [0.14.0] - 2024-12-01

### Added
- OKX spot/swap full implementation with WebSocket support
- Binance spot/swap with account WebSocket streams
- HTX (Huobi) spot trading support
- Interactive Brokers Web API integration
- Browser cookie support for IB Web API

## [0.13.0] - 2024-11-01

### Added
- Initial multi-exchange framework
- Standardized data containers (Ticker, OrderBook, Bar, Order, Position, etc.)
- YAML-based exchange configuration system

## 文档修改记录 (Documentation log)

以下为原 `docs/getting-started/change_log.md` 的文档维护记录（按日期归档）。

### 2026-02-28 文档质量大幅提升
- 修复所有文档中 ` ```bash ` 代码围栏错误关闭的问题（影响 50+ 个文件）
- 重写 docs/index.md：更新首页内容，修复示例代码匹配实际 BtApi 构造函数，添加 HTX 到交易所表格
- 新增 HTX (Huobi) 交易所文档
- 修复 docs/examples/api_examples.md：修复 15+ 处代码块格式错误
- 更新 mkdocs.yml 导航结构：新增 HTX (Huobi) 文档区

### 2026-02-28 完善项目文档体系
- 重写 README.md：新增项目特性、支持交易所表格、架构概览、快速开始指南、项目结构说明
- 新增 docs/architecture.md、docs/usage_guide.md、docs/developer_guide.md、docs/index.md

### 2026-02-26 更新 IBKR Web API 文档
- 更新 trading.md、account_management.md 和 index.md 的时间戳
- 新增 api_reference_quick.md、implementation_guide.md 中文指南

### 2025-02-03 增加统一接口 BtApi 可以直接连接多个交易所
