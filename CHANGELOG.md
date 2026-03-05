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
- Registry Pattern for plug-and-play exchange extensions
- EventBus for publish/subscribe event dispatching
- `AbstractVenueFeed` protocol and `AsyncWrapperMixin`
- `RateLimiter` with sliding window / fixed window modes
- `ConnectionPool` for HTTP/WebSocket connection reuse
- `SecureCredentialManager` for encrypted API key storage
- Custom exception hierarchy (`BtApiError`, `ExchangeNotFoundError`, etc.)
- 73 exchange registrations
- CTP (China Futures) C++ extension via SWIG
- Cython performance extension for calculations
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
