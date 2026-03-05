# Binance Margin Trading API 文档

> 来源: [developers.binance.com/docs/margin_trading](<https://developers.binance.com/docs/margin_trading)>
>
> 最后更新: 2026-02-26

本目录包含 Binance Margin Trading API 的接口文档，方便后续更新和参考。

## 文档索引

### 通用信息

| 文件 | 说明 |
|------|------|
| `change-log.md` | 变更日志 |
| `general-info.md` | 通用 API 信息、限频规则、签名方式 |
| `introduction.md` | Margin Trading 简介 |
| `common-definition.md` | 通用定义 |
| `error-code.md` | 错误码和错误信息 |
| `best-practice.md` | 最佳实践 |

### Market Data (行情数据)

| 文件 | 说明 |
|------|------|
| `cross-margin-collateral-ratio.md` | 全仓保证金抵押率 |
| `get-all-cross-margin-pairs.md` | 获取所有全仓杠杆交易对 |
| `get-all-isolated-margin-symbol.md` | 获取所有逐仓杠杆交易对 |
| `get-all-margin-assets.md` | 获取所有杠杆资产 |
| `get-delist-schedule.md` | 获取下架计划 |
| `get-list-schedule.md` | 获取上架计划 |
| `get-limit-price-pairs.md` | 获取限价交易对 |
| `get-margin-asset-risk-based-liquidation-ratio.md` | 获取杠杆资产风险清算比率 |
| `query-isolated-margin-tier-data.md` | 查询逐仓杠杆分层数据 |
| `query-margin-priceindex.md` | 查询杠杆价格指数 |
| `query-margin-available-inventory.md` | 查询杠杆可用库存 |
| `query-liability-coin-leverage-bracket.md` | 查询全仓 Pro 模式负债币杠杆档位 |
| `get-margin-restricted-assets.md` | 获取杠杆受限资产 |

### Borrow And Repay (借贷还款)

| 文件 | 说明 |
|------|------|
| `get-future-hourly-interest-rate.md` | 获取未来每小时利率 |
| `get-interest-history.md` | 获取利息历史 |
| `margin-account-borrow-repay.md` | 杠杆账户借贷/还款 |
| `query-borrow-repay.md` | 查询借贷/还款记录 |
| `query-margin-interest-rate-history.md` | 查询杠杆利率历史 |
| `query-max-borrow.md` | 查询最大可借 |

### Trade (交易)

| 文件 | 说明 |
|------|------|
| `margin-account-new-order.md` | 杠杆账户下单 |
| `margin-account-cancel-order.md` | 杠杆账户撤单 |
| `margin-account-cancel-all-open-orders.md` | 撤销某交易对所有挂单 |
| `query-margin-account-order.md` | 查询杠杆账户订单 |
| `query-margin-account-open-orders.md` | 查询杠杆账户当前挂单 |
| `query-margin-account-all-orders.md` | 查询杠杆账户所有订单 |
| `margin-account-new-oco.md` | 杠杆账户 OCO 下单 |
| `margin-account-cancel-oco.md` | 杠杆账户撤销 OCO |
| `query-margin-account-oco.md` | 查询杠杆 OCO 订单 |
| `query-margin-account-all-oco.md` | 查询所有杠杆 OCO |
| `query-margin-account-open-oco.md` | 查询杠杆当前挂起 OCO |
| `margin-account-new-oto.md` | 杠杆账户 OTO 下单 |
| `margin-account-new-otoco.md` | 杠杆账户 OTOCO 下单 |
| `query-margin-account-trade-list.md` | 查询杠杆成交记录 |
| `query-current-margin-order-count-usage.md` | 查询当前杠杆订单计数 |
| `small-liability-exchange.md` | 小额负债兑换 |
| `get-small-liability-exchange-coin-list.md` | 获取小额负债兑换币种列表 |
| `get-small-liability-exchange-history.md` | 获取小额负债兑换历史 |
| `margin-manual-liquidation.md` | 杠杆手动清算 |
| `query-margin-prevented-matches.md` | 查询杠杆自成交防范记录 |
| `create-special-key-of-low-latency-trading.md` | 创建低延迟交易特殊密钥 |
| `delete-special-key-of-low-latency-trading.md` | 删除低延迟交易特殊密钥 |
| `edit-ip-for-special-key-of-low-latency-trading.md` | 编辑低延迟交易特殊密钥 IP |
| `query-special-key-of-low-latency-trading.md` | 查询低延迟交易特殊密钥 |
| `query-special-key-list-of-low-latency-trading.md` | 查询低延迟交易特殊密钥列表 |

### Transfer (划转)

| 文件 | 说明 |
|------|------|
| `cross-margin-transfer.md` | 全仓杠杆划转 |
| `query-max-transfer-out-amount.md` | 查询最大可划转金额 |

### Account (账户)

| 文件 | 说明 |
|------|------|
| `query-cross-margin-account-details.md` | 查询全仓杠杆账户详情 |
| `get-summary-of-margin-account.md` | 获取杠杆账户摘要 |
| `query-isolated-margin-account-info.md` | 查询逐仓杠杆账户信息 |
| `enable-isolated-margin-account.md` | 启用逐仓杠杆账户 |
| `disable-isolated-margin-account.md` | 禁用逐仓杠杆账户 |
| `query-enabled-isolated-margin-account-limit.md` | 查询已启用逐仓杠杆账户上限 |
| `query-cross-margin-fee-data.md` | 查询全仓杠杆费率数据 |
| `query-isolated-margin-fee-data.md` | 查询逐仓杠杆费率数据 |
| `query-cross-isolated-margin-capital-flow.md` | 查询全仓/逐仓杠杆资金流水 |
| `get-bnb-burn-status.md` | 获取 BNB 抵扣状态 |
| `toggle-bnb-burn.md` | 切换 BNB 抵扣 |

### Trade Data Stream (交易数据流)

| 文件 | 说明 |
|------|------|
| `create-listen-token.md` | 创建 listenToken |
| `event-account-update.md` | 账户更新事件 |
| `event-balance-update.md` | 余额更新事件 |
| `event-order-update.md` | 订单更新事件 |

### Risk Data Stream (风控数据流)

| 文件 | 说明 |
|------|------|
| `overview.md` | 风控数据流概述 |
| `start-user-data-stream.md` | 创建 ListenKey |
| `keepalive-user-data-stream.md` | 延长 ListenKey 有效期 |
| `close-user-data-stream.md` | 关闭 ListenKey |
| `event-margin-call.md` | 追加保证金事件 |
| `event-liability-update.md` | 负债更新事件 |

## Base Endpoints

### REST API

- **https://api.binance.com**
- **https://api-gcp.binance.com**
- **https://api1.binance.com**~**<https://api4.binance.com**>

### SAPI (Margin 专用)

- 所有 Margin Trading 接口使用 `/sapi/v1/margin/` 前缀

## 认证方式

支持 HMAC、RSA 和 Ed25519 密钥。

| 安全类型 | 说明 |
|----------|------|
| `NONE` | 公开行情数据 |
| `TRADE` | 交易相关，下单/撤单 |
| `MARGIN` | 杠杆交易 |
| `USER_DATA` | 私有账户信息 |
| `USER_STREAM` | 管理用户数据流订阅 |

## 相关资源

- [官方 Python Connector](<https://github.com/binance/binance-connector-python)>
- [Postman Collections](<https://github.com/binance/binance-api-postman)>
- [Swagger/OpenAPI](<https://github.com/binance/binance-api-swagger)>
- [API 公告频道](<https://t.me/binance_api_announcements)>
- [Binance Margin Trading 官方文档](<https://developers.binance.com/docs/margin_trading)>
