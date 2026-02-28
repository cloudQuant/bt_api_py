# bt_api_py 文档索引

欢迎阅读 bt_api_py 文档。bt_api_py 是一个统一的多交易所交易 API 框架，支持现货、合约、期货等多种交易类型。

---

## 核心文档

| 文档 | 说明 |
|------|------|
| [README](../README.md) | 项目概览、特性、快速开始 |
| [架构设计](architecture.md) | 核心架构、设计模式、数据流 |
| [API 使用指南](usage_guide.md) | 完整的使用教程和代码示例 |
| [开发者指南](developer_guide.md) | 如何扩展、贡献代码、添加新交易所 |
| [更新日志](change_log.md) | 版本变更记录 |

---

## 交易所 API 参考文档

### Binance

| 文档 | 说明 |
|------|------|
| [Binance 现货](binance/spot/) | Spot 现货交易 API |
| [Binance 合约](binance/derivatives/) | USDT-M / COIN-M 永续和交割合约 API |
| [Binance 杠杆](binance/margin_trading/) | 逐仓/全仓杠杆交易 API |
| [Binance 算法](binance/algo/) | 算法交易 API |
| [API 实现状态](binance_api_implementation_status.md) | Binance API 接口实现进度 |
| [待实现 API](binance_api_missing_apis.md) | 尚未实现的 Binance API 列表 |
| [开发计划](binance_api_todo.md) | Binance API 开发计划 |

### OKX

| 文档 | 说明 |
|------|------|
| [OKX 概览](okx/overview.md) | OKX API 总体概览 |
| [交易账户](okx/trading_account.md) | 账户管理 API |
| [订单交易](okx/order_book_trading_trade.md) | 下单/撤单等交易 API |
| [行情数据](okx/market_data.md) | 行情和历史数据 API |
| [公共数据](okx/public_data.md) | 合约信息、费率等公共 API |
| [资金账户](okx/funding_account.md) | 充提、资金划转 API |
| [子账户](okx/sub_account.md) | 子账户管理 API |
| [算法交易](okx/order_book_trading_algo.md) | 算法订单 API |
| [网格交易](okx/order_book_trading_grid.md) | 网格策略 API |
| [其他交易](okx/order_book_trading_others.md) | 其他交易功能 API |
| [大宗交易](okx/block_trading.md) | 大宗交易 API |
| [金融产品](okx/financial_product.md) | 理财产品 API |
| [价差交易](okx/spread_trading.md) | 价差策略 API |
| [交易统计](okx/trading_statistics.md) | 交易数据统计 API |
| [状态与错误](okx/status_announcement_error.md) | 系统状态和错误码 |
| [代理返佣](okx/affiliate.md) | 代理返佣 API |
| [OKX 开发计划](okx_api_todo.md) | OKX API 开发计划 |

### Interactive Brokers (Web API)

| 文档 | 说明 |
|------|------|
| [IB Web API 概览](ib_web_api/overview.md) | IB Web API 总体概览 |
| [API 快速参考](ib_web_api/api_reference_quick.md) | 常用端点和参数速查 |
| [实现指南](ib_web_api/implementation_guide.md) | Python 对接实现教程 |
| [账户管理](ib_web_api/account_management.md) | 账户查询和管理 |
| [交易](ib_web_api/trading.md) | 下单和订单管理 |
| [文档索引](ib_web_api/index.md) | IB Web API 文档目录 |

### CTP (中国期货)

CTP 使用 SWIG 绑定的 C++ API，相关代码位于 `bt_api_py/ctp/` 目录。

| 文档 | 说明 |
|------|------|
| [CTP SWIG 重构计划](ctp_swig_refactoring_plan.md) | CTP 绑定重构方案 |

### 更多交易所

`docs/exchanges/` 目录包含 60+ 个交易所的 API 参考文档，供未来扩展使用。

---

## AI 开发者资源

| 文档 | 说明 |
|------|------|
| [项目上下文](../_bmad-output/project-context.md) | AI Agent 的项目规则和模式参考 |

---

*最后更新: 2026-02-28*
