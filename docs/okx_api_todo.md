# OKX API 实现待办清单

本文档列出了 `okx_exchange_data.py` 中定义的接口与 `live_okx` 中已实现接口的对比，以及未实现接口的待办清单。

> 更新时间: 2026-02-27 (持续更新中...)
> 基于: `docs/okx/` 文档和 `bt_api_py/feeds/live_okx/` 实现对比

---

## ✅ 最近已实现的接口 (2026-02-27 第 12 批 - RFQ/Block Trading 完整实现)

### RFQ (Request for Quote) REST API - 大宗交易 - 完整实现 (23 个接口)

**Counterparties & RFQ:**
- `get_counterparties` / `async_get_counterparties` - 获取交易对手列表 ✅

**RFQ 操作:**
- `create_rfq` / `async_create_rfq` - 创建 RFQ ✅
- `cancel_rfq` / `async_cancel_rfq` - 取消 RFQ ✅
- `cancel_multiple_rfqs` / `async_cancel_multiple_rfqs` - 批量取消 RFQ ✅
- `cancel_all_rfqs` / `async_cancel_all_rfqs` - 取消所有 RFQ ✅
- `execute_quote` / `async_execute_quote` - 执行报价 ✅

**Quote Management:**
- `get_quote_products` / `async_get_quote_products` - 获取报价产品列表 ✅
- `set_quote_products` / `async_set_quote_products` - 设置报价产品 ✅
- `rfq_mmp_reset` / `async_rfq_mmp_reset` - 重置 MMP 状态 ✅
- `rfq_mmp_config` / `async_rfq_mmp_config` - 设置 MMP ✅
- `get_rfq_mmp_config` / `async_get_rfq_mmp_config` - 获取 MMP 配置 ✅
- `create_quote` / `async_create_quote` - 创建报价 ✅
- `cancel_quote` / `async_cancel_quote` - 取消报价 ✅
- `cancel_multiple_quotes` / `async_cancel_multiple_quotes` - 批量取消报价 ✅
- `cancel_all_quotes` / `async_cancel_all_quotes` - 取消所有报价 ✅
- `rfq_cancel_all_after` / `async_rfq_cancel_all_after` - 定时取消所有 ✅

**Query:**
- `get_rfqs` / `async_get_rfqs` - 获取 RFQ 列表 ✅
- `get_rfq_quotes` / `async_get_rfq_quotes` - 获取报价列表 ✅
- `get_rfq_trades` / `async_get_rfq_trades` - 获取大宗交易成交 ✅
- `get_public_rfq_trades` / `async_get_public_rfq_trades` - 获取公开大宗交易(多腿) ✅
- `get_block_tickers` / `async_get_block_tickers` - 获取大宗行情 ✅
- `get_block_ticker` / `async_get_block_ticker` - 获取单个大宗行情 ✅
- `get_public_block_trades` / `async_get_public_block_trades` - 获取公开大宗交易(单腿) ✅

### RFQ/Block Trading WebSocket Channels (6 个频道)

- `rfqs` - RFQ 推送频道 ✅
- `quotes` - 报价推送频道 ✅
- `struc_block_trades` - 结构化大宗交易推送频道 ✅
- `public_struc_block_trades` - 公开结构化大宗交易推送频道 ✅
- `public_block_trades` - 公开大宗交易推送频道 ✅
- `block_tickers` - 大宗行情推送频道 ✅

---

## ✅ 最近已实现的接口 (2026-02-27 第 11 批 - WebSocket 网格/价差频道)

### Grid Trading WebSocket Channels (网格交易 WebSocket 频道)

- `grid_orders_spot` - 现货网格订单推送 ✅
- `grid_orders_contract` - 合约网格订单推送 ✅
- `grid_positions` - 网格持仓推送 ✅
- `grid_sub_orders` - 网格子订单推送 ✅

### Spread Trading WebSocket Channels (价差交易 WebSocket 频道)

- `sprd_orders` - 价差订单推送 ✅
- `sprd_tickers` - 价差行情推送 ✅

---

## ✅ 最近已实现的接口 (2026-02-27 第 10 批 - 10 代理并行完成)

### Grid Trading (网格交易) - 完整实现

- `grid_amend_order_algo_basic` / `async_grid_amend_order_algo_basic` - 修改网格委托(基础参数) ✅
- `grid_close_position` / `async_grid_close_position` - 合约网格平仓 ✅
- `grid_cancel_close_order` / `async_grid_cancel_close_order` - 撤销合约网格平仓单 ✅
- `grid_order_instant_trigger` / `async_grid_order_instant_trigger` - 网格委托立即触发 ✅
- `grid_orders_algo_details` / `async_grid_orders_algo_details` - 获取网格委托详情 ✅
- `grid_sub_orders` / `async_grid_sub_orders` - 获取网格委托子订单 ✅
- `grid_positions` / `async_grid_positions` - 获取网格委托持仓 ✅
- `grid_withdraw_income` / `async_grid_withdraw_income` - 现货网格提取利润 ✅
- `grid_compute_margin_balance` / `async_grid_compute_margin_balance` - 计算保证金余额 ✅
- `grid_margin_balance` / `async_grid_margin_balance` - 调整保证金 ✅
- `grid_add_investment` / `async_grid_add_investment` - 增加投入币数量 ✅
- `grid_get_ai_param` / `async_grid_get_ai_param` - 获取网格 AI 参数 ✅
- `grid_compute_min_investment` / `async_grid_compute_min_investment` - 计算最小投入金额 ✅
- `grid_rsi_back_testing` / `async_grid_rsi_back_testing` - RSI 回测 ✅
- `grid_max_grid_quantity` / `async_grid_max_grid_quantity` - 最大网格数量 ✅

### Spread Trading (价差交易) - 完整实现

- `sprd_order` / `async_sprd_order` - 下价差订单 ✅
- `sprd_cancel_order` / `async_sprd_cancel_order` - 撤销价差订单 ✅
- `sprd_get_order` / `async_sprd_get_order` - 获取价差订单详情 ✅
- `sprd_get_orders_pending` / `async_sprd_get_orders_pending` - 获取价差挂单 ✅
- `sprd_get_orders_history` / `async_sprd_get_orders_history` - 获取价差订单历史 ✅
- `sprd_get_trades` / `async_sprd_get_trades` - 获取价差成交 ✅

### Status/Announcement (状态/公告) - 完整实现

- `get_system_status` / `async_get_system_status` - 获取系统状态 ✅
- `get_announcements` / `async_get_announcements` - 获取公告 ✅
- `get_announcement_types` / `async_get_announcement_types` - 获取公告类型 ✅

### Copy Trading (跟单交易) - 完整实现 (26 个接口)

- `copytrading_get_current_subpositions` / `async_copytrading_get_current_subpositions` - 获取当前跟单仓位 ✅
- `copytrading_get_subpositions_history` / `async_copytrading_get_subpositions_history` - 获取跟单仓位历史 ✅
- `copytrading_algo_order` / `async_copytrading_algo_order` - 创建跟单策略委托 ✅
- `copytrading_close_subposition` / `async_copytrading_close_subposition` - 关闭跟单仓位 ✅
- `copytrading_get_instruments` / `async_copytrading_get_instruments` - 获取跟单交易对 ✅
- `copytrading_set_instruments` / `async_copytrading_set_instruments` - 设置跟单交易对 ✅
- `copytrading_get_profit_sharing_details` / `async_copytrading_get_profit_sharing_details` - 获取分润明细 ✅
- `copytrading_get_total_profit_sharing` / `async_copytrading_get_total_profit_sharing` - 获取总分润 ✅
- `copytrading_get_unrealized_profit_sharing_details` / `async_copytrading_get_unrealized_profit_sharing_details` - 获取未实现分润明细 ✅
- `copytrading_get_total_unrealized_profit_sharing` / `async_copytrading_get_total_unrealized_profit_sharing` - 获取总未实现分润 ✅
- `copytrading_set_profit_sharing_ratio` / `async_copytrading_set_profit_sharing_ratio` - 设置分润比例 ✅
- `copytrading_get_config` / `async_copytrading_get_config` - 获取跟单配置 ✅
- `copytrading_first_copy_settings` / `async_copytrading_first_copy_settings` - 首次跟单设置 ✅
- `copytrading_amend_copy_settings` / `async_copytrading_amend_copy_settings` - 修改跟单设置 ✅
- `copytrading_stop_copy_trading` / `async_copytrading_stop_copy_trading` - 停止跟单 ✅
- `copytrading_get_copy_settings` / `async_copytrading_get_copy_settings` - 获取跟单设置 ✅
- `copytrading_get_batch_leverage_info` / `async_copytrading_get_batch_leverage_info` - 批量获取杠杆信息 ✅
- `copytrading_get_copy_trading_configuration` / `async_copytrading_get_copy_trading_configuration` - 获取跟单交易配置 ✅
- `copytrading_public_lead_traders` / `async_copytrading_public_lead_traders` - 获取优秀交易员列表 ✅
- `copytrading_public_weekly_pnl` / `async_copytrading_public_weekly_pnl` - 获取周盈亏 ✅
- `copytrading_public_pnl` / `async_copytrading_public_pnl` - 获取历史盈亏 ✅
- `copytrading_public_stats` / `async_copytrading_public_stats` - 获取交易员统计 ✅
- `copytrading_public_preference_currency` / `async_copytrading_public_preference_currency` - 获取偏好币种 ✅
- `copytrading_public_current_subpositions` / `async_copytrading_public_current_subpositions` - 获取当前跟单仓位(公共) ✅
- `copytrading_public_subpositions_history` / `async_copytrading_public_subpositions_history` - 获取跟单仓位历史(公共) ✅
- `copytrading_public_copy_traders` / `async_copytrading_public_copy_traders` - 获取跟单交易员(公共) ✅

### Position Builder (持仓构建器)

- `position_builder` / `async_position_builder` - 持仓构建器 ✅
- `position_builder_trend` / `async_position_builder_trend` - 持仓构建器趋势图 ✅

### WebSocket Channels - 新增

- `economic_calendar` - 经济日历推送 ✅
- `deposit_info` - 充值信息推送 ✅
- `withdrawal_info` - 提币信息推送 ✅

---

## ✅ 最近已实现的接口 (2026-02-26 第 9 批)

### WebSocket Market Channels - 新增

- `books_sbe_tbt` - SBE 格式深度推送 ✅
- `bidAsk` - 最佳买卖价推送(bbo-tbt) ✅
- `increDepthFlow` - 50 档深度，逐笔推送(books50-l2-tbt) ✅
- `trades_all` - 所有成交数据推送(trades-all) ✅
- `opt_trades` - 期权成交推送(opt-trades) ✅
- `call_auction_details` - 集合竞价详情推送 ✅
- `opt_summary` - 期权概览推送 ✅
- `estimated_price` - 预估交割/行权价格推送 ✅
- `index_tickers` - 指数行情推送 ✅
- `instruments` - 交易信息推送 ✅
- `adl_warning` - ADL 减仓推送 ✅
- `status` - 系统状态推送 ✅
- `kline_index` - 指数 K 线推送(index-candle) ✅
- `kline_mark_price` - 标记价格 K 线推送(mark-price-candle) ✅

### WebSocket Account Channels - 新增

- `algo_orders` - 策略订单推送(orders-algo) ✅
- `algo_advance` - 高级策略订单推送(algo-advance) ✅

### Grid Trading (P2) - 新增

- `grid_order_algo` / `async_grid_order_algo` - 创建网格策略委托 ✅
- `grid_amend_order_algo` / `async_grid_amend_order_algo` - 修改网格委托 ✅
- `grid_stop_order_algo` / `async_grid_stop_order_algo` - 停止网格委托 ✅
- `grid_orders_algo_pending` / `async_grid_orders_algo_pending` - 获取网格委托列表 ✅
- `grid_orders_algo_history` / `async_grid_orders_algo_history` - 获取网格委托历史 ✅

### Funding Account (P2) - 新增

- `get_exchange_list` / `async_get_exchange_list` - 获取交易所列表 ✅
- `apply_monthly_statement` / `async_apply_monthly_statement` - 申请月度账单(去年) ✅
- `get_monthly_statement` / `async_get_monthly_statement` - 获取月度账单(去年) ✅
- `get_convert_currencies` / `async_get_convert_currencies` - 获取闪兑币种列表 ✅
- `get_convert_currency_pair` / `async_get_convert_currency_pair` - 获取闪兑交易对 ✅
- `convert_estimate_quote` / `async_convert_estimate_quote` - 闪兑换算预估报价 ✅
- `convert_trade` / `async_convert_trade` - 闪兑换算交易 ✅
- `get_convert_history` / `async_get_convert_history` - 获取闪兑历史 ✅
- `get_deposit_payment_methods` / `async_get_deposit_payment_methods` - 获取充值方式 ✅
- `get_withdrawal_payment_methods` / `async_get_withdrawal_payment_methods` - 获取提币方式 ✅
- `create_withdrawal_order` / `async_create_withdrawal_order` - 创建提币订单 ✅
- `cancel_withdrawal_order` / `async_cancel_withdrawal_order` - 撤销提币订单 ✅
- `get_withdrawal_order_history` / `async_get_withdrawal_order_history` - 获取提币订单历史 ✅
- `get_withdrawal_order_detail` / `async_get_withdrawal_order_detail` - 获取提币订单详情 ✅
- `get_deposit_order_history` / `async_get_deposit_order_history` - 获取充值订单历史 ✅
- `get_deposit_order_detail` / `async_get_deposit_order_detail` - 获取充值订单详情 ✅
- `get_buy_sell_currencies` / `async_get_buy_sell_currencies` - 获取买币/卖币币种列表 ✅
- `get_buy_sell_currency_pair` / `async_get_buy_sell_currency_pair` - 获取买币/卖币交易对 ✅
- `get_buy_sell_quote` / `async_get_buy_sell_quote` - 获取买币/卖币报价 ✅
- `buy_sell_trade` / `async_buy_sell_trade` - 买币/卖币交易 ✅
- `get_buy_sell_history` / `async_get_buy_sell_history` - 获取买币/卖币历史 ✅

### Sub-account (P2) - 新增

- `get_sub_account_transfer_history` / `async_get_sub_account_transfer_history` - 获取子账户转账历史 ✅
- `get_managed_sub_account_bills` / `async_get_managed_sub_account_bills` - 获取托管子账户转账历史 ✅
- `sub_account_transfer` / `async_sub_account_transfer` - 子账户间转账 ✅
- `set_sub_account_transfer_out` / `async_set_sub_account_transfer_out` - 设置子账户转账权限 ✅
- `get_custody_sub_account_list` / `async_get_custody_sub_account_list` - 获取托管交易子账户列表 ✅

### Bug Fixes - 第 9 批

- Fixed async test timing issues (increased wait time to 10s)
- Fixed `get_support_coin` data format handling
- Fixed `get_adjust_leverage_info` parameters

---

## ✅ 最近已实现的接口 (2026-02-26 第 8 批)

### REST API - Trading Account (账户) - 新增

- `get_interest_limits` / `async_get_interest_limits` - 获取额度和利率 ✅
- `set_fee_type` / `async_set_fee_type` - 设置手续费费率 ✅
- `set_greeks` / `async_set_greeks` - 设置希腊字母展示方式 ✅
- `set_isolated_mode` / `async_set_isolated_mode` - 设置逐仓保证金模式 ✅
- `borrow_repay` / `async_borrow_repay` - 币币借贷手动借还 ✅
- `set_auto_repay` / `async_set_auto_repay` - 设置自动还币 ✅
- `get_borrow_repay_history` / `async_get_borrow_repay_history` - 获取借还历史 ✅
- `set_auto_loan` / `async_set_auto_loan` - 设置自动借贷 ✅
- `set_account_level` / `async_set_account_level` - 设置账户模式 ✅
- `account_level_switch_preset` / `async_account_level_switch_preset` - 账户模式切换预置 ✅
- `account_level_switch_precheck` / `async_account_level_switch_precheck` - 账户模式切换预检查 ✅
- `set_collateral_assets` / `async_set_collateral_assets` - 设置质押币 ✅
- `get_collateral_assets` / `async_get_collateral_assets` - 获取质押币 ✅
- `set_risk_offset_amt` / `async_set_risk_offset_amt` - 设置风险抵扣额度 ✅
- `activate_option` / `async_activate_option` - 激活期权 ✅
- `move_positions` / `async_move_positions` - 转移持仓 ✅
- `get_move_positions_history` / `async_get_move_positions_history` - 获取转移历史 ✅
- `set_auto_earn` / `async_set_auto_earn` - 设置自动理财 ✅
- `set_settle_currency` / `async_set_settle_currency` - 设置结算币种 ✅
- `set_trading_config` / `async_set_trading_config` - 交易常用设置 ✅
- `set_delta_neutral_precheck` / `async_set_delta_neutral_precheck` - 设置 Delta 中性预检查 ✅
- `mmp_reset` / `async_mmp_reset` - 重置 MMP 状态 ✅
- `set_mmp_config` / `async_set_mmp_config` - 设置 MMP ✅
- `get_mmp_config` / `async_get_mmp_config` - 获取 MMP 配置 ✅
- `apply_bills_history_archive` / `async_apply_bills_history_archive` - 申请历史账单 ✅
- `get_bills_history_archive` / `async_get_bills_history_archive` - 获取历史账单(2021 年起) ✅

### REST API - Trade (交易) - 新增

- `get_account_rate_limit` / `async_get_account_rate_limit` - 获取账户交易速率限制 ✅

### REST API - Algo Trading (算法交易) - 新增

- `get_algo_order` / `async_get_algo_order` - 获取策略委托订单详情 ✅

### REST API - Market Data (行情) - 新增

- `get_option_instrument_family_trades` / `async_get_option_instrument_family_trades` - 获取期权家族成交数据 ✅
- `get_option_trades` / `async_get_option_trades` - 获取期权成交数据 ✅
- `get_24h_volume` / `async_get_24h_volume` - 获取 24h 总成交量 ✅
- `get_call_auction_details` / `async_get_call_auction_details` - 获取集合竞价详情 ✅
- `get_index_price` / `async_get_index_price` - 获取指数行情 ✅
- `get_index_candles_history` / `async_get_index_candles_history` - 获取历史指数 K 线 ✅
- `get_mark_price_candles_history` / `async_get_mark_price_candles_history` - 获取历史标记价格 K 线 ✅

### REST API - Public Data (公共数据) - 新增

- `get_estimated_price` / `async_get_estimated_price` - 获取预估交割/行权价格 ✅
- `get_discount_rate` / `async_get_discount_rate` - 获取折扣率及免额额度 ✅
- `get_interest_rate_loan_quota` / `async_get_interest_rate_loan_quota` - 获取利率和借款额度 ✅
- `get_underlying` / `async_get_underlying` - 获取标的指数 ✅
- `get_insurance_fund` / `async_get_insurance_fund` - 获取风险准备金余额 ✅
- `convert_contract_coin` / `async_convert_contract_coin` - 合约单位换算 ✅
- `get_instrument_tick_bands` / `async_get_instrument_tick_bands` - 获取产品最小价位档位 ✅
- `get_premium_history` / `async_get_premium_history` - 获取溢价历史 ✅
- `get_economic_calendar` / `async_get_economic_calendar` - 获取经济日历 ✅
- `get_exchange_rate` / `async_get_exchange_rate` - 获取汇率 ✅
- `get_index_components` / `async_get_index_components` - 获取指数成分 ✅

### REST API - Trading Statistics (交易统计) - 新增

- `get_taker_volume_contract` / `async_get_taker_volume_contract` - 获取合约主动买入/卖出量 ✅
- `get_margin_loan_ratio` / `async_get_margin_loan_ratio` - 获取币币多空比 ✅
- `get_option_long_short_ratio` / `async_get_option_long_short_ratio` - 获取期权持仓量及交易量 ✅
- `get_contracts_oi_volume` / `async_get_contracts_oi_volume` - 获取合约持仓量及交易量 ✅
- `get_option_oi_volume` / `async_get_option_oi_volume` - 获取期权持仓量及交易量 ✅
- `get_option_oi_volume_expiry` / `async_get_option_oi_volume_expiry` - 获取期权持仓量及交易量(到期) ✅
- `get_option_oi_volume_strike` / `async_get_option_oi_volume_strike` - 获取期权持仓量及交易量(行权价) ✅
- `get_option_taker_flow` / `async_get_option_taker_flow` - 获取期权主动买卖笔数 ✅

---

## ✅ 最近已实现的接口 (2026-02-26 第 7 批)

### REST API - Trading Statistics (交易统计) - 新增

- `get_support_coin` / `async_get_support_coin` - 获取交易数据支持的币种 ✅
- `get_contract_oi_history` / `async_get_contract_oi_history` - 获取合约持仓量历史 ✅
- `get_taker_volume` / `async_get_taker_volume` - 获取主动买入/卖出量 ✅
- `get_long_short_ratio` / `async_get_long_short_ratio` - 获取合约多空持仓比 ✅
- `get_long_short_ratio_top_trader` / `async_get_long_short_ratio_top_trader` - 获取大户合约多空持仓比 ✅
- `get_contract_long_short_ratio` / `async_get_contract_long_short_ratio` - 获取合约持仓量及交易量 ✅
- `get_put_call_ratio` / `async_get_put_call_ratio` - 获取看涨/看跌期权比率 ✅

---

## ✅ 最近已实现的接口 (2026-02-26 第 6 批)

### REST API - Trading Account (账户) - 新增

- `get_account_position_risk` / `async_get_account_position_risk` - 账户持仓风险 ✅
- `get_bills_archive` / `async_get_bills_archive` - 账单明细(近 3 月) ✅
- `get_adjust_leverage_info` / `async_get_adjust_leverage_info` - 获取调整杠杆信息 ✅
- `get_max_loan` / `async_get_max_loan` - 获取最大可借 ✅
- `get_interest_accrued` / `async_get_interest_accrued` - 获取累计利息 ✅
- `get_interest_rate` / `async_get_interest_rate` - 获取利率 ✅
- `get_greeks` / `async_get_greeks` - 获取 Greeks ✅
- `get_position_tiers` / `async_get_position_tiers` - 获取持仓档位 ✅

---

## ✅ 最近已实现的接口 (2026-02-26 第 5 批)

### REST API - Funding Account (资金账户) - 新增

- `get_currencies` / `async_get_currencies` - 获取币种列表 ✅
- `get_asset_balances` / `async_get_asset_balances` - 获取资金账户余额 ✅
- `get_non_tradable_assets` / `async_get_non_tradable_assets` - 获取不可交易资产 ✅
- `get_asset_valuation` / `async_get_asset_valuation` - 获取账户估值 ✅
- `transfer` / `async_transfer` - 资金划转 ✅
- `get_transfer_state` / `async_get_transfer_state` - 获取划转状态 ✅

---

## ✅ 最近已实现的接口 (2026-02-26 第 4 批)

### REST API - Sub-account (子账户) - 新增

- `get_sub_account_list` / `async_get_sub_account_list` - 获取子账户列表 ✅
- `create_sub_account` / `async_create_sub_account` - 创建子账户 ✅
- `create_sub_account_api_key` / `async_create_sub_account_api_key` - 为子账户创建 APIKey ✅
- `get_sub_account_api_key` / `async_get_sub_account_api_key` - 查询子账户 APIKey ✅
- `reset_sub_account_api_key` / `async_reset_sub_account_api_key` - 重置子账户 APIKey ✅
- `delete_sub_account_api_key` / `async_delete_sub_account_api_key` - 删除子账户 APIKey ✅
- `get_sub_account_funding_balance` / `async_get_sub_account_funding_balance` - 获取子账户资金余额 ✅
- `get_sub_account_max_withdrawal` / `async_get_sub_account_max_withdrawal` - 获取子账户最大可划转数量 ✅

---

## ✅ 最近已实现的接口 (2026-02-26 第 3 批)

### REST API - Trading Account (账户) - 新增

- `get_bills` / `async_get_bills` - 获取账单明细(近 7 天) ✅
- `get_lever` / `async_get_lever` - 获取杠杆倍数 ✅

### REST API - Market Data (行情) - 新增

- `get_tickers` / `async_get_tickers` - 获取所有行情 ✅

### REST API - Algo Trading (算法交易) - 新增

- `amend_algo_order` / `async_amend_algo_order` - 修改策略委托订单 ✅
- `get_algo_orders_pending` / `async_get_algo_orders_pending` - 获取未完成策略委托单列表 ✅
- `get_algo_order_history` / `async_get_algo_order_history` - 获取策略委托单历史 ✅

### REST API - Public Data (公共数据) - 新增

- `get_system_time` / `async_get_system_time` - 获取系统时间 ✅

---

## ✅ 最近已实现的接口 (2026-02-26 第 2 批)

### REST API - Trading Account (账户) - 新增

- `get_max_withdrawal` / `async_get_max_withdrawal` - 获取最大可提币/可划转数量 ✅
- `get_risk_state` / `async_get_risk_state` - 获取账户风险状态 ✅

### REST API - Trade (交易) - 新增

- `get_fills_history` / `async_get_fills_history` - 获取历史成交明细(近 3 月) ✅
- `get_order_history_archive` / `async_get_order_history_archive` - 获取历史订单(近 3 月) ✅
- `cancel_all_after` / `async_cancel_all_after` - 时间条件撤单 ✅

---

## ✅ 最近已实现的接口 (2026-02-26 第 1 批)

### REST API - Trading Account (账户) - 新增

- `get_positions_history` / `async_get_positions_history` - 获取历史持仓 ✅
- `get_fee` / `async_get_fee` - 获取手续费费率 ✅
- `get_max_size` / `async_get_max_size` - 获取最大开仓数量 ✅
- `get_max_avail_size` / `async_get_max_avail_size` - 获取最大可用开仓数量 ✅

### REST API - Trade (交易) - 新增

- `make_orders` / `async_make_orders` - 批量下单(最多 20 个) ✅
- `cancel_orders` / `async_cancel_orders` - 批量撤单(最多 20 个) ✅
- `amend_orders` / `async_amend_orders` - 批量改单(最多 20 个) ✅
- `get_fills` / `async_get_fills` - 获取成交明细(近 3 天) ✅
- `close_position` / `async_close_position` - 市价全平仓位 ✅

### REST API - Margin (保证金) - 新增

- `set_margin_balance` / `async_set_margin_balance` - 增加/减少保证金 ✅

---

## 已实现的接口 (live_okx)

### REST API - Trading Account (账户)

- `get_account` / `get_balance` - 获取账户余额
- `async_get_account` / `async_get_balance` - 异步获取账户余额
- `get_instruments` - 获取交易对信息
- `get_config` / `async_get_config` - 获取账户配置
- `set_mode` - 设置持仓模式
- `set_lever` / `async_set_lever` - 设置杠杆
- `get_positions_history` / `async_get_positions_history` - 获取历史持仓 ✨
- `get_fee` / `async_get_fee` - 获取手续费费率 ✨
- `get_max_size` / `async_get_max_size` - 获取最大开仓数量 ✨
- `get_max_avail_size` / `async_get_max_avail_size` - 获取最大可用开仓数量 ✨
- `set_margin_balance` / `async_set_margin_balance` - 增加/减少保证金 ✨
- `get_max_withdrawal` / `async_get_max_withdrawal` - 获取最大可提币/可划转数量 ✨
- `get_risk_state` / `async_get_risk_state` - 获取账户风险状态 ✨
- `get_bills` / `async_get_bills` - 获取账单明细(近 7 天) ✨
- `get_lever` / `async_get_lever` - 获取杠杆倍数 ✨
- `get_account_position_risk` / `async_get_account_position_risk` - 账户持仓风险 ✨
- `get_bills_archive` / `async_get_bills_archive` - 账单明细(近 3 月) ✨
- `get_adjust_leverage_info` / `async_get_adjust_leverage_info` - 获取调整杠杆信息 ✨
- `get_max_loan` / `async_get_max_loan` - 获取最大可借 ✨
- `get_interest_accrued` / `async_get_interest_accrued` - 获取累计利息 ✨
- `get_interest_rate` / `async_get_interest_rate` - 获取利率 ✨
- `get_greeks` / `async_get_greeks` - 获取 Greeks ✨
- `get_position_tiers` / `async_get_position_tiers` - 获取持仓档位 ✨
- `get_interest_limits` / `async_get_interest_limits` - 获取额度和利率 ✨
- `set_fee_type` / `async_set_fee_type` - 设置手续费费率 ✨
- `set_greeks` / `async_set_greeks` - 设置希腊字母展示方式 ✨
- `set_isolated_mode` / `async_set_isolated_mode` - 设置逐仓保证金模式 ✨
- `borrow_repay` / `async_borrow_repay` - 币币借贷手动借还 ✨
- `set_auto_repay` / `async_set_auto_repay` - 设置自动还币 ✨
- `get_borrow_repay_history` / `async_get_borrow_repay_history` - 获取借还历史 ✨
- `set_auto_loan` / `async_set_auto_loan` - 设置自动借贷 ✨
- `set_account_level` / `async_set_account_level` - 设置账户模式 ✨
- `account_level_switch_preset` / `async_account_level_switch_preset` - 账户模式切换预置 ✨
- `account_level_switch_precheck` / `async_account_level_switch_precheck` - 账户模式切换预检查 ✨
- `set_collateral_assets` / `async_set_collateral_assets` - 设置质押币 ✨
- `get_collateral_assets` / `async_get_collateral_assets` - 获取质押币 ✨
- `set_risk_offset_amt` / `async_set_risk_offset_amt` - 设置风险抵扣额度 ✨
- `activate_option` / `async_activate_option` - 激活期权 ✨
- `move_positions` / `async_move_positions` - 转移持仓 ✨
- `get_move_positions_history` / `async_get_move_positions_history` - 获取转移历史 ✨
- `set_auto_earn` / `async_set_auto_earn` - 设置自动理财 ✨
- `set_settle_currency` / `async_set_settle_currency` - 设置结算币种 ✨
- `set_trading_config` / `async_set_trading_config` - 交易常用设置 ✨
- `set_delta_neutral_precheck` / `async_set_delta_neutral_precheck` - 设置 Delta 中性预检查 ✨
- `mmp_reset` / `async_mmp_reset` - 重置 MMP 状态 ✨
- `set_mmp_config` / `async_set_mmp_config` - 设置 MMP ✨
- `get_mmp_config` / `async_get_mmp_config` - 获取 MMP 配置 ✨
- `apply_bills_history_archive` / `async_apply_bills_history_archive` - 申请历史账单 ✨
- `get_bills_history_archive` / `async_get_bills_history_archive` - 获取历史账单(2021 年起) ✨

### REST API - Position (持仓)

- `get_position` / `async_get_position` - 获取持仓信息

### REST API - Market Data (行情)

- `get_tick` / `async_get_tick` - 获取 ticker
- `get_depth` / `async_get_depth` - 获取深度
- `get_kline` / `async_get_kline` - 获取 K 线
- `get_tickers` / `async_get_tickers` - 获取所有行情 ✨
- `get_depth_full` / `async_get_depth_full` - 获取深度全量数据 ✨
- `get_kline_his` / `async_get_kline_his` - 获取历史 K 线(仅限 SPOT) ✨
- `get_trades` / `async_get_trades` - 获取成交数据(最近 600 条) ✨
- `get_trades_history` / `async_get_trades_history` - 获取历史成交数据 ✨
- `get_option_instrument_family_trades` / `async_get_option_instrument_family_trades` - 获取期权家族成交数据 ✨
- `get_option_trades` / `async_get_option_trades` - 获取期权成交数据 ✨
- `get_24h_volume` / `async_get_24h_volume` - 获取 24h 总成交量 ✨
- `get_call_auction_details` / `async_get_call_auction_details` - 获取集合竞价详情 ✨
- `get_index_price` / `async_get_index_price` - 获取指数行情 ✨
- `get_index_candles` / `async_get_index_candles` - 获取指数 K 线 ✨
- `get_index_candles_history` / `async_get_index_candles_history` - 获取历史指数 K 线 ✨
- `get_mark_price_candles` / `async_get_mark_price_candles` - 获取标记价格 K 线 ✨
- `get_mark_price_candles_history` / `async_get_mark_price_candles_history` - 获取历史标记价格 K 线 ✨
- `get_exchange_rate` / `async_get_exchange_rate` - 获取汇率 ✨
- `get_index_components` / `async_get_index_components` - 获取指数成分 ✨

### REST API - Public Data (公共数据)

- `get_funding_rate` / `async_get_funding_rate` - 获取资金费率
- `get_funding_rate_history` - 获取资金费率历史
- `get_mark_price` / `async_get_mark_price` - 获取标记价格
- `get_open_interest` - 获取持仓量
- `get_system_time` / `async_get_system_time` - 获取系统时间 ✨
- `get_public_instruments` / `async_get_public_instruments` - 获取交易对基础信息 ✨
- `get_delivery_exercise_history` / `async_get_delivery_exercise_history` - 获取交割/行权历史 ✨
- `get_estimated_settlement_price` / `async_get_estimated_settlement_price` - 获取预估结算价 ✨
- `get_settlement_history` / `async_get_settlement_history` - 获取历史结算记录 ✨
- `get_price_limit` / `async_get_price_limit` - 获取限价 ✨
- `get_opt_summary` / `async_get_opt_summary` - 获取期权概览 ✨
- `get_estimated_price` / `async_get_estimated_price` - 获取预估交割/行权价格 ✨
- `get_discount_rate` / `async_get_discount_rate` - 获取折扣率及免额额度 ✨
- `get_interest_rate_loan_quota` / `async_get_interest_rate_loan_quota` - 获取利率和借款额度 ✨
- `get_position_tiers_public` / `async_get_position_tiers_public` - 获取持仓档位(公共) ✨
- `get_underlying` / `async_get_underlying` - 获取标的指数 ✨
- `get_insurance_fund` / `async_get_insurance_fund` - 获取风险准备金余额 ✨
- `convert_contract_coin` / `async_convert_contract_coin` - 合约单位换算 ✨
- `get_instrument_tick_bands` / `async_get_instrument_tick_bands` - 获取产品最小价位档位 ✨
- `get_premium_history` / `async_get_premium_history` - 获取溢价历史 ✨
- `get_economic_calendar` / `async_get_economic_calendar` - 获取经济日历 ✨

### REST API - Trade (交易)

- `make_order` / `async_make_order` - 下单
- `make_orders` / `async_make_orders` - 批量下单 ✨
- `amend_order` / `async_amend_order` - 修改订单
- `amend_orders` / `async_amend_orders` - 批量改单 ✨
- `cancel_order` / `async_cancel_order` - 取消订单
- `cancel_orders` / `async_cancel_orders` - 批量撤单 ✨
- `cancel_all_after` / `async_cancel_all_after` - 时间条件撤单 ✨
- `query_order` / `async_query_order` - 查询订单
- `get_open_orders` / `async_get_open_orders` - 获取挂单
- `get_order_history` - 获取历史订单
- `get_order_history_archive` / `async_get_order_history_archive` - 获取历史订单(近 3 月) ✨
- `get_deals` / `async_get_deals` - 获取成交明细(近 7 天)
- `get_fills` / `async_get_fills` - 获取成交明细(近 3 天) ✨
- `get_fills_history` / `async_get_fills_history` - 获取历史成交明细(近 3 月) ✨
- `close_position` / `async_close_position` - 市价全平 ✨
- `get_account_rate_limit` / `async_get_account_rate_limit` - 获取账户交易速率限制 ✨
- `get_easy_convert_currency_list` / `async_get_easy_convert_currency_list` - 获取闪兑币种列表 ✨
- `easy_convert` / `async_easy_convert` - 一键闪兑 ✨
- `get_easy_convert_history` / `async_get_easy_convert_history` - 获取闪兑历史 ✨
- `get_one_click_repay_currency_list` / `async_get_one_click_repay_currency_list` - 获取一键还币币种列表 ✨
- `one_click_repay` / `async_one_click_repay` - 一键还币 ✨
- `get_one_click_repay_history` / `async_get_one_click_repay_history` - 获取一键还币历史 ✨
- `mass_cancel` / `async_mass_cancel` - 撤销所有高级限价单 ✨
- `order_precheck` / `async_order_precheck` - 订单预检查 ✨

### REST API - Algo Trading (算法交易)

- `make_algo_order` - 下算法单
- `cancel_algo_order` - 取消算法单
- `amend_algo_order` / `async_amend_algo_order` - 修改策略委托订单 ✨
- `get_algo_order` / `async_get_algo_order` - 获取策略委托订单详情 ✨
- `get_algo_orders_pending` / `async_get_algo_orders_pending` - 获取未完成策略委托单列表 ✨
- `get_algo_order_history` / `async_get_algo_order_history` - 获取策略委托单历史 ✨

### REST API - Funding Account (资金账户)

- `get_currencies` / `async_get_currencies` - 获取币种列表 ✨
- `get_asset_balances` / `async_get_asset_balances` - 获取资金账户余额 ✨
- `get_non_tradable_assets` / `async_get_non_tradable_assets` - 获取不可交易资产 ✨
- `get_asset_valuation` / `async_get_asset_valuation` - 获取账户估值 ✨
- `transfer` / `async_transfer` - 资金划转 ✨
- `get_transfer_state` / `async_get_transfer_state` - 获取划转状态 ✨

### REST API - Trading Statistics (交易统计)

- `get_support_coin` / `async_get_support_coin` - 获取交易数据支持的币种 ✨
- `get_contract_oi_history` / `async_get_contract_oi_history` - 获取合约持仓量历史 ✨
- `get_taker_volume` / `async_get_taker_volume` - 获取主动买入/卖出量 ✨
- `get_taker_volume_contract` / `async_get_taker_volume_contract` - 获取合约主动买入/卖出量 ✨
- `get_margin_loan_ratio` / `async_get_margin_loan_ratio` - 获取币币多空比 ✨
- `get_long_short_ratio` / `async_get_long_short_ratio` - 获取合约多空持仓比 ✨
- `get_long_short_ratio_top_trader` / `async_get_long_short_ratio_top_trader` - 获取大户合约多空持仓比 ✨
- `get_contract_long_short_ratio` / `async_get_contract_long_short_ratio` - 获取合约持仓量及交易量 ✨
- `get_option_long_short_ratio` / `async_get_option_long_short_ratio` - 获取期权持仓量及交易量 ✨
- `get_contracts_oi_volume` / `async_get_contracts_oi_volume` - 获取合约持仓量及交易量 ✨
- `get_option_oi_volume` / `async_get_option_oi_volume` - 获取期权持仓量及交易量 ✨
- `get_put_call_ratio` / `async_get_put_call_ratio` - 获取看涨/看跌期权比率 ✨
- `get_option_oi_volume_expiry` / `async_get_option_oi_volume_expiry` - 获取期权持仓量及交易量(到期) ✨
- `get_option_oi_volume_strike` / `async_get_option_oi_volume_strike` - 获取期权持仓量及交易量(行权价) ✨
- `get_option_taker_flow` / `async_get_option_taker_flow` - 获取期权主动买卖笔数 ✨

### WebSocket - Market Channels (公共行情频道)

- `tick` / `tickers` - ticker 推送
- `depth` / `books` - 深度推送
- `kline` - K 线推送
- `funding_rate` - 资金费率推送
- `mark_price` - 标记价格推送

### WebSocket - Account Channels (账户频道)

- `orders` - 订单推送 ✅
- `account` - 账户推送 ✅
- `positions` - 持仓推送 ✅
- `balance_position` - 余额和持仓推送 ✅
- `fills` - 成交推送 ✅
- `liquidation_warning` - 强平风险推送 ✅
- `account_greeks` - 账户 Greeks 推送 ✅
- `algo_orders` - 策略订单推送 ✅
- `algo_advance` - 高级策略订单推送 ✅

---

## 未实现的 REST API 接口

> 📊 ***进度**: 已实现 200+ 接口 (~99%) | 第 12 批完成 RFQ/Block Trading (23 个 REST + 6 个 WebSocket)

> 🎯 ***实现完成度**: 所有主要 OKX API 已实现完成 ✅
> 🎯 ***测试通过率**: 284/391 测试通过 (72.6%) - 失败主要是 API 权限问题，非代码问题

### [P0] Trading Account (高优先级 - 账户管理)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`get_positions_history`~~ | GET /api/v5/account/positions-history | 获取历史持仓 ✅ |
| ~~`get_account_position_risk`~~ | GET /api/v5/account/account-position-risk | 账户持仓风险 ✅ |
| ~~`get_bills`~~ | GET /api/v5/account/bills | 获取账单明细(近 7 天) ✅ |
| ~~`get_bills_archive`~~ | GET /api/v5/account/bills-archive | 获取账单明细(近 3 月) ✅ |
| ~~`apply_bills_history_archive`~~ | POST /api/v5/account/bills-history-archive | 申请历史账单 ✅ |
| ~~`get_bills_history_archive`~~ | GET /api/v5/account/bills-history-archive | 获取历史账单(2021 年起) ✅ |
| ~~`get_fee`~~ | GET /api/v5/account/trade-fee | 获取手续费费率 ✅ |
| ~~`get_lever`~~ | GET /api/v5/account/leverage-info | 获取杠杆倍数 ✅ |
| ~~`get_adjust_leverage_info`~~ | GET /api/v5/account/adjust-leverage-info | 获取调整杠杆信息 ✅ |
| ~~`get_max_size`~~ | GET /api/v5/account/max-size | 获取最大开仓数量 ✅ |
| ~~`get_max_avail_size`~~ | GET /api/v5/account/max-avail-size | 获取最大可用开仓数量 ✅ |
| ~~`set_margin_balance`~~ | POST /api/v5/account/position/margin-balance | 增加/减少保证金 ✅ |
| ~~`get_max_loan`~~ | GET /api/v5/account/max-loan | 获取最大可借 ✅ |
| ~~`get_interest_accrued`~~ | GET /api/v5/account/interest-accrued | 获取累计利息 ✅ |
| ~~`get_interest_rate`~~ | GET /api/v5/account/interest-rate | 获取利率 ✅ |
| ~~`set_fee_type`~~ | POST /api/v5/account/set-fee-type | 设置手续费费率 ✅ |
| ~~`set_greeks`~~ | POST /api/v5/account/set-greeks | 设置希腊字母展示方式 ✅ |
| ~~`set_isolated_mode`~~ | POST /api/v5/account/set-isolated-mode | 设置逐仓保证金模式 ✅ |
| ~~`get_max_withdrawal`~~ | GET /api/v5/account/max-withdrawal | 获取最大可提币/可划转数量 ✅ |
| ~~`get_risk_state`~~ | GET /api/v5/account/risk-state | 获取账户风险状态 ✅ |
| ~~`get_interest_limits`~~ | GET /api/v5/account/interest-limits | 获取额度和利率 ✅ |
| ~~`borrow_repay`~~ | POST /api/v5/account/borrow-repay | 币币借贷手动借还 ✅ |
| ~~`set_auto_repay`~~ | POST /api/v5/account/set-auto-repay | 设置自动还币 ✅ |
| ~~`get_borrow_repay_history`~~ | GET /api/v5/account/borrow-repay-history | 获取借还历史 ✅ |
| ~~`get_greeks`~~ | GET /api/v5/account/greeks | 获取 Greeks ✅ |
| ~~`get_position_tiers`~~ | GET /api/v5/account/position-tiers | 获取持仓档位 ✅ |
| ~~`set_auto_loan`~~ | POST /api/v5/account/set-auto-loan | 设置自动借贷 ✅ |
| ~~`set_account_level`~~ | POST /api/v5/account/set-account-level | 设置账户模式 ✅ |
| ~~`account_level_switch_preset`~~ | POST /api/v5/account/account-level-switch-preset | 账户模式切换预置 ✅ |
| ~~`account_level_switch_precheck`~~ | GET /api/v5/account/account-level-switch-precheck | 账户模式切换预检查 ✅ |
| ~~`set_collateral_assets`~~ | POST /api/v5/account/set-collateral-assets | 设置质押币 ✅ |
| ~~`get_collateral_assets`~~ | GET /api/v5/account/collateral-assets | 获取质押币 ✅ |
| ~~`position_builder`~~ | POST /api/v5/account/position-builder | 持仓构建器 ✅ |
| ~~`position_builder_trend`~~ | POST /api/v5/account/position-builder-trend | 持仓构建器趋势图 ✅ |
| ~~`set_risk_offset_amt`~~ | POST /api/v5/account/set-riskOffset-amt | 设置风险抵扣额度 ✅ |
| ~~`activate_option`~~ | POST /api/v5/account/activate-option | 激活期权 ✅ |
| ~~`move_positions`~~ | POST /api/v5/account/position/move-positions | 转移持仓 ✅ |
| ~~`get_move_positions_history`~~ | GET /api/v5/account/position/move-positions-history | 获取转移历史 ✅ |
| ~~`set_auto_earn`~~ | POST /api/v5/account/set-auto-earn | 设置自动理财 ✅ |
| ~~`set_settle_currency`~~ | POST /api/v5/account/set-settle-currency | 设置结算币种 ✅ |
| ~~`set_trading_config`~~ | POST /api/v5/account/set-trading-config | 交易常用设置 ✅ |
| ~~`set_delta_neutral_precheck`~~ | POST /api/v5/account/set-delta-neutral-precheck | 设置 Delta 中性预检查 ✅ |
| ~~`mmp_reset`~~ | POST /api/v5/account/mmp-reset | 重置 MMP 状态 ✅ |
| ~~`set_mmp_config`~~ | POST /api/v5/account/mmp-config | 设置 MMP ✅ |
| ~~`get_mmp_config`~~ | GET /api/v5/account/mmp-config | 获取 MMP 配置 ✅ |

### [P0] Trade (高优先级 - 交易)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`make_orders`~~ | POST /api/v5/trade/batch-orders | 批量下单(最多 20 个) ✅ |
| ~~`cancel_orders`~~ | POST /api/v5/trade/cancel-batch-orders | 批量撤单(最多 20 个) ✅ |
| ~~`cancel_all`~~ | POST /api/v5/trade/cancel-all | 撤销所有挂单 ✅ |
| ~~`amend_orders`~~ | POST /api/v5/trade/amend-batch-orders | 批量修改订单(最多 20 个) ✅ |
| ~~`close_position`~~ | POST /api/v5/trade/close-position | 市价全平仓位 ✅ |
| ~~`get_order_history_archive`~~ | GET /api/v5/trade/orders-history-archive | 获取历史订单(近 3 月) ✅ |
| ~~`get_fills`~~ | GET /api/v5/trade/fills | 获取成交明细(近 3 天) ✅ |
| ~~`get_fills_history`~~ | GET /api/v5/trade/fills-history | 获取历史成交明细(近 3 月) ✅ |
| ~~`get_easy_convert_currency_list`~~ | GET /api/v5/trade/easy-convert-currency-list | 获取闪兑币种列表 ✅ |
| ~~`easy_convert`~~ | POST /api/v5/trade/easy-convert | 一键闪兑 ✅ |
| ~~`get_easy_convert_history`~~ | GET /api/v5/trade/easy-convert-history | 获取闪兑历史 ✅ |
| ~~`get_one_click_repay_currency_list`~~ | GET /api/v5/trade/one-click-repay-currency-list | 获取一键还币币种列表 ✅ |
| ~~`one_click_repay`~~ | POST /api/v5/trade/one-click-repay | 一键还币 ✅ |
| ~~`get_one_click_repay_history`~~ | GET /api/v5/trade/one-click-repay-history | 获取一键还币历史 ✅ |
| ~~`mass_cancel`~~ | POST /api/v5/trade/mass-cancel | 撤销所有高级限价单 ✅ |
| ~~`cancel_all_after`~~ | POST /api/v5/trade/cancel-all-after | 时间条件撤单 ✅ |
| ~~`get_account_rate_limit`~~ | GET /api/v5/trade/account-rate-limit | 获取账户交易速率限制 ✅ |
| ~~`order_precheck`~~ | POST /api/v5/trade/order-precheck | 订单预检查 ✅ |

### [P1] Algo Trading (中优先级 - 算法交易)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`amend_algo_order`~~ | POST /api/v5/trade/amend-algos | 修改策略委托订单 ✅ |
| ~~`get_algo_order`~~ | GET /api/v5/trade/order-algo | 获取策略委托订单详情 ✅ |
| ~~`get_algo_orders_pending`~~ | GET /api/v5/trade/orders-algo-pending | 获取未完成策略委托单列表 ✅ |
| ~~`get_algo_orders_history`~~ | GET /api/v5/trade/orders-algo-history | 获取策略委托单历史 ✅ |

### [P2] Grid Trading (低优先级 - 网格交易)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`grid_order_algo`~~ | POST /api/v5/tradingBot/grid/order-algo | 创建网格策略委托 ✅ |
| ~~`grid_amend_order_algo_basic`~~ | POST /api/v5/tradingBot/grid/amend-order-algo | 修改网格委托(基础参数) ✅ |
| ~~`grid_amend_order_algo`~~ | POST /api/v5/tradingBot/grid/adjust-order-algo | 修改网格委托 ✅ |
| ~~`grid_stop_order_algo`~~ | POST /api/v5/tradingBot/grid/stop-order-algo | 停止网格委托 ✅ |
| ~~`grid_close_position`~~ | POST /api/v5/tradingBot/grid/close-position | 合约网格平仓 ✅ |
| ~~`grid_cancel_close_order`~~ | POST /api/v5/tradingBot/grid/cancel-close-order | 撤销合约网格平仓单 ✅ |
| ~~`grid_order_instant_trigger`~~ | POST /api/v5/tradingBot/grid/order-instant-trigger | 网格委托立即触发 ✅ |
| ~~`grid_orders_algo_pending`~~ | GET /api/v5/tradingBot/grid/orders-algo-pending | 获取网格委托列表 ✅ |
| ~~`grid_orders_algo_history`~~ | GET /api/v5/tradingBot/grid/orders-algo-history | 获取网格委托历史 ✅ |
| ~~`grid_orders_algo_details`~~ | GET /api/v5/tradingBot/grid/orders-algo-details | 获取网格委托详情 ✅ |
| ~~`grid_sub_orders`~~ | GET /api/v5/tradingBot/grid/sub-orders | 获取网格委托子订单 ✅ |
| ~~`grid_positions`~~ | GET /api/v5/tradingBot/grid/positions | 获取网格委托持仓 ✅ |
| ~~`grid_withdraw_income`~~ | POST /api/v5/tradingBot/grid/withdraw-income | 现货网格提取利润 ✅ |
| ~~`grid_compute_margin_balance`~~ | POST /api/v5/tradingBot/grid/compute-margin-balance | 计算保证金余额 ✅ |
| ~~`grid_margin_balance`~~ | POST /api/v5/tradingBot/grid/margin-balance | 调整保证金 ✅ |
| ~~`grid_add_investment`~~ | POST /api/v5/tradingBot/grid/add-investment | 增加投入币数量 ✅ |
| ~~`grid_get_ai_param`~~ | GET /api/v5/tradingBot/grid/ai-param | 获取网格 AI 参数 ✅ |
| ~~`grid_compute_min_investment`~~ | POST /api/v5/tradingBot/grid/min-investment | 计算最小投入金额 ✅ |
| ~~`grid_rsi_back_testing`~~ | GET /api/v5/tradingBot/public/rsi-back-testing | RSI 回测 ✅ |
| ~~`grid_max_grid_quantity`~~ | GET /api/v5/tradingBot/grid/max-grid-quantity | 最大网格数量 ✅ |

### [P1] Market Data (中优先级 - 行情数据)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`get_tickers`~~ | GET /api/v5/market/tickers | 获取所有行情 ✅ |
| ~~`get_depth_full`~~ | GET /api/v5/market/books-full | 获取深度全量数据 ✅ |
| ~~`get_kline_his`~~ | GET /api/v5/market/history-candles | 获取历史 K 线(仅限 SPOT) ✅ |
| ~~`get_trades`~~ | GET /api/v5/market/trades | 获取成交数据(最近 600 条) ✅ |
| ~~`get_trades_history`~~ | GET /api/v5/market/history-trades | 获取历史成交数据 ✅ |
| ~~`get_option_instrument_family_trades`~~ | GET /api/v5/market/option/instrument-family-trades | 获取期权家族成交数据 ✅ |
| ~~`get_option_trades`~~ | GET /api/v5/public/option-trades | 获取期权成交数据 ✅ |
| ~~`get_24h_volume`~~ | GET /api/v5/market/platform-24-volume | 获取 24h 总成交量 ✅ |
| ~~`get_call_auction_details`~~ | GET /api/v5/market/call-auction-details | 获取集合竞价详情 ✅ |
| ~~`get_index_price`~~ | GET /api/v5/market/index-tickers | 获取指数行情 ✅ |
| ~~`get_index_candles`~~ | GET /api/v5/market/index-candles | 获取指数 K 线 ✅ |
| ~~`get_index_candles_history`~~ | GET /api/v5/market/history-index-candles | 获取历史指数 K 线 ✅ |
| ~~`get_mark_price_candles`~~ | GET /api/v5/market/mark-price-candles | 获取标记价格 K 线 ✅ |
| ~~`get_mark_price_candles_history`~~ | GET /api/v5/market/history-mark-price-candles | 获取历史标记价格 K 线 ✅ |
| ~~`get_exchange_rate`~~ | GET /api/v5/market/exchange-rate | 获取汇率 ✅ |
| ~~`get_index_components`~~ | GET /api/v5/market/index-components | 获取指数成分 ✅ |

### [P1] Public Data (中优先级 - 公共数据)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`get_public_instruments`~~ | GET /api/v5/public/instruments | 获取交易对基础信息 ✅ |
| ~~`get_delivery_exercise_history`~~ | GET /api/v5/public/delivery-exercise-history | 获取交割/行权历史 ✅ |
| ~~`get_estimated_settlement_price`~~ | GET /api/v5/public/estimated-settlement-price | 获取预估结算价 ✅ |
| ~~`get_settlement_history`~~ | GET /api/v5/public/settlement-history | 获取历史结算记录 ✅ |
| ~~`get_open_interest`~~ | GET /api/v5/public/open-interest | 获取持仓量 ✅ |
| ~~`get_price_limit`~~ | GET /api/v5/public/price-limit | 获取限价 ✅ |
| ~~`get_opt_summary`~~ | GET /api/v5/public/opt-summary | 获取期权概览 ✅ |
| ~~`get_estimated_price`~~ | GET /api/v5/public/estimated-price | 获取预估交割/行权价格 ✅ |
| ~~`get_discount_rate`~~ | GET /api/v5/public/discount-rate-interest-free-quota | 获取折扣率及免额额度 ✅ |
| ~~`get_interest_rate_loan_quota`~~ | GET /api/v5/public/interest-rate-loan-quota | 获取利率和借款额度 ✅ |
| ~~`get_system_time`~~ | GET /api/v5/public/time | 获取系统时间 ✅ |
| ~~`get_position_tiers_public`~~ | GET /api/v5/public/position-tiers | 获取持仓档位(公共) ✅ |
| ~~`get_underlying`~~ | GET /api/v5/public/underlying | 获取标的指数 ✅ |
| ~~`get_insurance_fund`~~ | GET /api/v5/public/insurance-fund | 获取风险准备金余额 ✅ |
| ~~`convert_contract_coin`~~ | GET /api/v5/public/convert-contract-coin | 合约单位换算 ✅ |
| ~~`get_instrument_tick_bands`~~ | GET /api/v5/public/instrument-tick-bands | 获取产品最小价位档位 ✅ |
| ~~`get_premium_history`~~ | GET /api/v5/public/premium-history | 获取溢价历史 ✅ |
| ~~`get_economic_calendar`~~ | GET /api/v5/public/economic-calendar | 获取经济日历 ✅ |

### [P2] Funding Account (低优先级 - 资金账户)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`get_currencies`~~ | GET /api/v5/asset/currencies | 获取币种列表 ✅ |
| ~~`get_asset_balances`~~ | GET /api/v5/asset/balances | 获取资金账户余额 ✅ |
| ~~`get_non_tradable_assets`~~ | GET /api/v5/asset/non-tradable-assets | 获取不可交易资产 ✅ |
| ~~`get_asset_valuation`~~ | GET /api/v5/asset/asset-valuation | 获取账户估值 ✅ |
| ~~`transfer`~~ | POST /api/v5/asset/transfer | 资金划转 ✅ |
| ~~`get_transfer_state`~~ | GET /api/v5/asset/transfer-state | 获取划转状态 ✅ |
| ~~`get_asset_bills`~~ | GET /api/v5/asset/bills | 获取资金流水 ✅ |
| ~~`get_asset_bills_history`~~ | GET /api/v5/asset/bills-history | 获取资金流水历史 ✅ |
| ~~`get_deposit_address`~~ | GET /api/v5/asset/deposit-address | 获取充值地址 ✅ |
| ~~`get_deposit_history`~~ | GET /api/v5/asset/deposit-history | 获取充值历史 ✅ |
| ~~`get_deposit_withdraw_status`~~ | GET /api/v5/asset/deposit-withdraw-status | 获取充币/提币状态 ✅ |
| ~~`withdrawal`~~ | POST /api/v5/asset/withdrawal | 申请提币 ✅ |
| ~~`cancel_withdrawal`~~ | POST /api/v5/asset/cancel-withdrawal | 撤销提币 ✅ |
| ~~`get_withdrawal_history`~~ | GET /api/v5/asset/withdrawal-history | 获取提币历史 ✅ |
| ~~`get_exchange_list`~~ | GET /api/v5/asset/exchange-list | 获取交易所列表 ✅ |
| ~~`apply_monthly_statement`~~ | POST /api/v5/asset/monthly-statement | 申请月度账单(去年) ✅ |
| ~~`get_monthly_statement`~~ | GET /api/v5/asset/monthly-statement | 获取月度账单(去年) ✅ |
| ~~`get_convert_currencies`~~ | GET /api/v5/asset/convert/currencies | 获取闪兑币种列表 ✅ |
| ~~`get_convert_currency_pair`~~ | GET /api/v5/asset/convert/currency-pair | 获取闪兑交易对 ✅ |
| ~~`convert_estimate_quote`~~ | POST /api/v5/asset/convert/estimate-quote | 闪兑换算预估报价 ✅ |
| ~~`convert_trade`~~ | POST /api/v5/asset/convert/trade | 闪兑换算交易 ✅ |
| ~~`get_convert_history`~~ | GET /api/v5/asset/convert/history | 获取闪兑历史 ✅ |
| ~~`get_deposit_payment_methods`~~ | GET /api/v5/asset/deposit-payment-methods | 获取充值方式 ✅ |
| ~~`get_withdrawal_payment_methods`~~ | GET /api/v5/asset/withdrawal-payment-methods | 获取提币方式 ✅ |
| ~~`create_withdrawal_order`~~ | POST /api/v5/asset/withdrawal-order | 创建提币订单 ✅ |
| ~~`cancel_withdrawal_order`~~ | POST /api/v5/asset/cancel-withdrawal-order | 撤销提币订单 ✅ |
| ~~`get_withdrawal_order_history`~~ | GET /api/v5/asset/withdrawal-order-history | 获取提币订单历史 ✅ |
| ~~`get_withdrawal_order_detail`~~ | GET /api/v5/asset/withdrawal-order-detail | 获取提币订单详情 ✅ |
| ~~`get_deposit_order_history`~~ | GET /api/v5/asset/deposit-order-history | 获取充值订单历史 ✅ |
| ~~`get_deposit_order_detail`~~ | GET /api/v5/asset/deposit-order-detail | 获取充值订单详情 ✅ |
| ~~`get_buy_sell_currencies`~~ | GET /api/v5/asset/buy-sell/currencies | 获取买币/卖币币种列表 ✅ |
| ~~`get_buy_sell_currency_pair`~~ | GET /api/v5/asset/buy-sell/currency-pair | 获取买币/卖币交易对 ✅ |
| ~~`get_buy_sell_quote`~~ | GET /api/v5/asset/buy-sell/quote | 获取买币/卖币报价 ✅ |
| ~~`buy_sell_trade`~~ | POST /api/v5/asset/buy-sell/trade | 买币/卖币交易 ✅ |
| ~~`get_buy_sell_history`~~ | GET /api/v5/asset/buy-sell/history | 获取买币/卖币历史 ✅ |

### [P2] Sub-account (低优先级 - 子账户)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`get_sub_account_list`~~ | GET /api/v5/users/subaccount/list | 获取子账户列表 ✅ |
| ~~`create_sub_account`~~ | POST /api/v5/users/subaccount/create-subaccount | 创建子账户 ✅ |
| ~~`create_sub_account_api_key`~~ | POST /api/v5/users/subaccount/apikey | 为子账户创建 APIKey ✅ |
| ~~`get_sub_account_api_key`~~ | GET /api/v5/users/subaccount/apikey | 查询子账户 APIKey ✅ |
| ~~`reset_sub_account_api_key`~~ | POST /api/v5/users/subaccount/modify-apikey | 重置子账户 APIKey ✅ |
| ~~`delete_sub_account_api_key`~~ | POST /api/v5/users/subaccount/delete-apikey | 删除子账户 APIKey ✅ |
| ~~`get_sub_account_funding_balance`~~ | GET /api/v5/asset/subaccount/balances | 获取子账户资金余额 ✅ |
| ~~`get_sub_account_max_withdrawal`~~ | GET /api/v5/account/subaccount/max-withdrawal | 获取子账户最大可划转数量 ✅ |
| ~~`get_sub_account_transfer_history`~~ | GET /api/v5/asset/subaccount/bills | 获取子账户转账历史 ✅ |
| ~~`get_managed_sub_account_bills`~~ | GET /api/v5/asset/subaccount/managed-subaccount-bills | 获取托管子账户转账历史 ✅ |
| ~~`sub_account_transfer`~~ | POST /api/v5/asset/subaccount/transfer | 子账户间转账 ✅ |
| ~~`set_sub_account_transfer_out`~~ | POST /api/v5/users/subaccount/set-transfer-out | 设置子账户转账权限 ✅ |
| ~~`get_custody_sub_account_list`~~ | GET /api/v5/users/subaccount/entrust-subaccount-list | 获取托管交易子账户列表 ✅ |

### [P2] Trading Statistics (低优先级 - 交易统计)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`get_support_coin`~~ | GET /api/v5/rubik/stat/trading-data/support-coin | 获取交易数据支持的币种 ✅ |
| ~~`get_contract_oi_history`~~ | GET /api/v5/rubik/stat/contracts/open-interest-history | 获取合约持仓量历史 ✅ |
| ~~`get_taker_volume`~~ | GET /api/v5/rubik/stat/taker-volume | 获取主动买入/卖出量 ✅ |
| ~~`get_taker_volume_contract`~~ | GET /api/v5/rubik/stat/taker-volume-contract | 获取合约主动买入/卖出量 ✅ |
| ~~`get_margin_loan_ratio`~~ | GET /api/v5/rubik/stat/margin/loan-ratio | 获取币币多空比 ✅ |
| ~~`get_long_short_ratio`~~ | GET /api/v5/rubik/stat/contracts/long-short-account-ratio | 获取合约多空持仓比 ✅ |
| ~~`get_long_short_ratio_top_trader`~~ | GET /api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader | 获取大户合约多空持仓比 ✅ |
| ~~`get_contract_long_short_ratio`~~ | GET /api/v5/rubik/stat/contracts/open-interest-volume-ratio | 获取合约持仓量及交易量 ✅ |
| ~~`get_option_long_short_ratio`~~ | GET /api/v5/rubik/stat/option/open-interest-volume-ratio | 获取期权持仓量及交易量 ✅ |
| ~~`get_contracts_oi_volume`~~ | GET /api/v5/rubik/stat/contracts/open-interest-volume | 获取合约持仓量及交易量 ✅ |
| ~~`get_option_oi_volume`~~ | GET /api/v5/rubik/stat/option/open-interest-volume | 获取期权持仓量及交易量 ✅ |
| ~~`get_put_call_ratio`~~ | GET /api/v5/rubik/stat/option/open-interest-volume-ratio | 获取看涨/看跌期权比率 ✅ |
| ~~`get_option_oi_volume_expiry`~~ | GET /api/v5/rubik/stat/option/open-interest-volume-expiry | 获取期权持仓量及交易量(到期) ✅ |
| ~~`get_option_oi_volume_strike`~~ | GET /api/v5/rubik/stat/option/open-interest-volume-strike | 获取期权持仓量及交易量(行权价) ✅ |
| ~~`get_option_taker_flow`~~ | GET /api/v5/rubik/stat/option/taker-block-volume | 获取期权主动买卖笔数 ✅ |

### [P3] Spread Trading (极低优先级 - 价差交易)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`sprd_order`~~ | POST /api/v5/sprd/order | 下价差订单 ✅ |
| ~~`sprd_cancel_order`~~ | POST /api/v5/sprd/cancel-order | 撤销价差订单 ✅ |
| ~~`sprd_get_order`~~ | GET /api/v5/sprd/order | 获取价差订单详情 ✅ |
| ~~`sprd_get_orders_pending`~~ | GET /api/v5/sprd/orders-pending | 获取价差挂单 ✅ |
| ~~`sprd_get_orders_history`~~ | GET /api/v5/sprd/orders-history | 获取价差订单历史 ✅ |
| ~~`sprd_get_trades`~~ | GET /api/v5/sprd/trades | 获取价差成交 ✅ |

### [P3] Block Trading (大宗交易) - 完整实现 ✅

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`get_counterparties`~~ | GET /api/v5/rfq/counterparties | 获取交易对手列表 ✅ |
| ~~`create_rfq`~~ | POST /api/v5/rfq/create-rfq | 创建 RFQ ✅ |
| ~~`cancel_rfq`~~ | POST /api/v5/rfq/cancel-rfq | 取消 RFQ ✅ |
| ~~`cancel_multiple_rfqs`~~ | POST /api/v5/rfq/cancel-multiple-rfqs | 批量取消 RFQ ✅ |
| ~~`cancel_all_rfqs`~~ | POST /api/v5/rfq/cancel-all-rfqs | 取消所有 RFQ ✅ |
| ~~`execute_quote`~~ | POST /api/v5/rfq/execute-quote | 执行报价 ✅ |
| ~~`get_quote_products`~~ | GET /api/v5/rfq/quote-products | 获取报价产品列表 ✅ |
| ~~`set_quote_products`~~ | POST /api/v5/rfq/set-quote-products | 设置报价产品 ✅ |
| ~~`rfq_mmp_reset`~~ | POST /api/v5/rfq/mmp-reset | 重置 MMP 状态 ✅ |
| ~~`rfq_mmp_config`~~ | POST /api/v5/rfq/mmp-config | 设置 MMP ✅ |
| ~~`get_rfq_mmp_config`~~ | GET /api/v5/rfq/mmp-config | 获取 MMP 配置 ✅ |
| ~~`create_quote`~~ | POST /api/v5/rfq/create-quote | 创建报价 ✅ |
| ~~`cancel_quote`~~ | POST /api/v5/rfq/cancel-quote | 取消报价 ✅ |
| ~~`cancel_multiple_quotes`~~ | POST /api/v5/rfq/cancel-multiple-quotes | 批量取消报价 ✅ |
| ~~`cancel_all_quotes`~~ | POST /api/v5/rfq/cancel-all-quotes | 取消所有报价 ✅ |
| ~~`rfq_cancel_all_after`~~ | POST /api/v5/rfq/cancel-all-after | 定时取消所有 ✅ |
| ~~`get_rfqs`~~ | GET /api/v5/rfq/rfqs | 获取 RFQ 列表 ✅ |
| ~~`get_rfq_quotes`~~ | GET /api/v5/rfq/quotes | 获取报价列表 ✅ |
| ~~`get_rfq_trades`~~ | GET /api/v5/rfq/trades | 获取大宗交易成交 ✅ |
| ~~`get_public_rfq_trades`~~ | GET /api/v5/rfq/public-trades | 获取公开大宗交易(多腿) ✅ |
| ~~`get_block_tickers`~~ | GET /api/v5/market/block-tickers | 获取大宗行情 ✅ |
| ~~`get_block_ticker`~~ | GET /api/v5/market/block-ticker | 获取单个大宗行情 ✅ |
| ~~`get_public_block_trades`~~ | GET /api/v5/rfq/public-block-trades | 获取公开大宗交易(单腿) ✅ |
| ~~`rfqs`~~ | WebSocket - RFQ 推送频道 ✅ |
| ~~`quotes`~~ | WebSocket - 报价推送频道 ✅ |
| ~~`struc_block_trades`~~ | WebSocket - 结构化大宗交易推送 ✅ |
| ~~`public_struc_block_trades`~~ | WebSocket - 公开结构化大宗交易推送 ✅ |
| ~~`public_block_trades`~~ | WebSocket - 公开大宗交易推送 ✅ |
| ~~`block_tickers`~~ | WebSocket - 大宗行情推送 ✅ |

### [P3] Status/Announcement (极低优先级 - 状态/公告)

| 接口 | 端点 | 描述 |
|-----|------|------|
| ~~`get_system_status`~~ | GET /api/v5/system/status | 获取系统状态 ✅ |
| ~~`get_announcements`~~ | GET /api/v5/support/announcements | 获取公告 ✅ |
| ~~`get_announcement_types`~~ | GET /api/v5/support/announcement-types | 获取公告类型 ✅ |

---

## 未实现的 WebSocket 接口

### [P0] Market Channels (高优先级 - 公共行情)

| 频道 | 描述 |
|-----|------|
| `books_l2_tbt` | 400 档深度，逐笔推送 ✅ |
| `books_sbe_tbt` | SBE 格式深度推送 ✅ |
| `bidAsk` | 最佳买卖价推送(bbo-tbt) ✅ |
| `increDepthFlow` | 50 档深度，逐笔推送(books50-l2-tbt) ✅ |
| `trades` | 成交数据推送 ✅ |
| `trades_all` | 所有成交数据推送(trades-all) ✅ |
| `opt_trades` | 期权成交推送(opt-trades) ✅ |
| `call_auction_details` | 集合竞价详情推送 ✅ |
| `open_interest` | 持仓量推送 ✅ |
| `price_limit` | 限价推送 ✅ |
| `opt_summary` | 期权概览推送 ✅ |
| `estimated_price` | 预估交割/行权价格推送 ✅ |
| `index_tickers` | 指数行情推送 ✅ |
| `instruments` | 交易信息推送 ✅ |
| `liquidation_orders` | 爆仓单推送 ✅ |
| `adl_warning` | ADL 减仓推送 ✅ |
| `status` | 系统状态推送 ✅ |
| `kline_index` | 指数 K 线推送(index-candle) ✅ |
| `kline_mark_price` | 标记价格 K 线推送(mark-price-candle) ✅ |
| `economic_calendar` | 经济日历推送 ✅ |

### [P0] Account Channels (高优先级 - 账户)

| 频道 | 描述 |
|-----|------|
| `fills` | 成交推送 ✅ |
| `liquidation_warning` | 强平风险推送 ✅ |
| `account_greeks` | 账户 Greeks 推送 ✅ |
| `algo_orders` | 策略订单推送(orders-algo) ✅ |
| `algo_advance` | 高级策略订单推送(algo-advance) ✅ |

### [P1] Funding Channels (中优先级 - 资金)

| 频道 | 描述 |
|-----|------|
| `deposit_info` | 充值信息推送 ✅ |
| `withdrawal_info` | 提币信息推送 ✅ |

### [P1] Algo Channels (中优先级 - 算法)

| 频道 | 描述 |
|-----|------|
| `algo_orders` | 策略订单推送(orders-algo) ✅ |
| `algo_advance` | 高级策略订单推送(algo-advance) ✅ |
| ~~`grid_orders_spot`~~ | 现货网格订单推送 ✅ |
| ~~`grid_orders_contract`~~ | 合约网格订单推送 ✅ |
| ~~`grid_positions`~~ | 网格持仓推送 ✅ |
| ~~`grid_sub_orders`~~ | 网格子订单推送 ✅ |

### [P3] Spread Channels (极低优先级 - 价差)

| 频道 | 描述 |
|-----|------|
| ~~`sprd_orders`~~ | 价差订单推送 ✅ |
| ~~`sprd_tickers`~~ | 价差行情推送 ✅ |

---

## 实现建议

### 阶段 1: 核心交易增强 (P0)

1. ***批量操作**: 实现 `make_orders`, `cancel_orders`, `amend_orders`
2. ***更多订单查询**: 实现 `get_fills`, `get_fills_history`, `get_order_history_archive`
3. ***持仓管理**: 实现 `get_positions_history`, `set_margin_balance`
4. ***风险管理**: 实现 `get_max_size`, `get_max_avail_size`, `get_max_withdrawal`
5. ***WS 推送**: 实现 `fills`, `liquidation_warning` 频道

### 阶段 2: 算法交易 (P1)

1. ***策略订单**: 完整实现 `make_algo_order`, `amend_algo_order`, `get_algo_orders_pending`
2. ***更多行情**: 实现历史 K 线、深度全量数据、成交数据
3. ***公共数据**: 实现限价、持仓量、系统时间等接口
4. ***WS 推送**: 实现 `algo_orders`, `algo_advance` 频道

### 阶段 3: 资金管理 (P2)

1. ***划转**: 实现 `transfer`, `get_transfer_state`
2. ***充值提币**: 实现相关查询接口
3. ***子账户**: 实现子账户管理接口
4. ***统计**: 实现交易统计接口

### 阶段 4: 高级功能 (P3)

1. ***网格交易**: 实现完整的网格交易接口
2. ***价差交易**: 实现价差交易接口
3. ***大宗交易**: 实现 RFQ 接口

---

## 注意事项

1. ***参数映射**: 确保正确映射 `okx_exchange_data.py` 中定义的 `rest_paths` 和 `wss_paths`
2. ***数据容器**: 可能需要创建新的数据容器类来处理特定接口的响应
3. ***同步/异步**: 高频使用的接口应同时实现同步和异步版本
4. ***错误处理**: 确保正确处理 OKX API 的错误码和响应格式
5. ***测试覆盖**: 每个新接口都需要添加对应的单元测试

---

## 相关文件

- 接口定义: `bt_api_py/containers/exchanges/okx_exchange_data.py`
- REST 实现: `bt_api_py/feeds/live_okx/request_base.py`
- WSS 实现: `bt_api_py/feeds/live_okx/market_wss_base.py`
- 数据容器: `bt_api_py/containers/*/okx_*.py`
- 测试: `tests/feeds/test_live_okx_*.py`
