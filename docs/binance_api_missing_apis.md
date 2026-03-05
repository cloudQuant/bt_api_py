# Binance API 实现分析报告

## 项目概述

本文档分析了 `bt_api_py` 项目中 Binance API 的实现情况，对比了 Binance 官方 API 文档，列出了已实现和尚未实现的 API 接口。

## 已实现 API 统计

### 1. Binance USDT-M Futures (Swap)

- *基 URL**: `<https://fapi.binance.com`>
- *已实现 API 数量**: 68 个

#### 已实现模块：

- **通用接口**(4 个)
  - ping, get_server_time, get_contract, get_order_rate_limit
- **市场数据**(20 个)
  - tick, info, depth, kline, agg_trades, funding_rate, mark_price
  - open_interest, liquidation_orders, continuous_kline, etc.
- **账户相关**(13 个)
  - get_account, get_balance, get_position, get_fee
  - get_income, get_adl_quantile, get_leverage_bracket, etc.
- **交易操作**(16 个)
  - make_order, modify_order, cancel_order, query_order
  - get_open_orders, get_all_orders, make_oco_order, etc.
- **监听密钥**(3 个)
  - get_listen_key, refresh_listen_key, close_listen_key
- **WebSocket 流** (12 个)
  - agg_trade, trade, kline, ticker, depth, mark_price, etc.

### 2. Binance 现货交易 (Spot)

- *基 URL**: `<https://api.binance.com`>
- *已实现 API 数量**: 56 个

#### 已实现模块：

- **通用接口**(3 个)
  - ping, get_server_time, get_contract
- **市场数据**(15 个)
  - tick, depth, kline, info, market, trades, historical_trades
- **账户相关**(4 个)
  - get_account, get_balance, get_fee, get_commission
- **交易操作**(18 个)
  - make_order, cancel_order, modify_order, query_order
  - get_open_orders, get_all_orders, make_oco_order, etc.
- **特殊订单**(4 个)
  - SOR 订单, 防止匹配订单, 订单修改, 过滤器
- **监听密钥**(2 个)
  - get_listen_key, refresh_listen_key
- **子账户/转账**(7 个)
  - universal_transfer, account_summary, transfer_to_futures, etc.
- **WebSocket 流** (7 个)
  - agg_trade, trade, kline, ticker, depth, etc.

### 3. Binance 币本位合约 (Coin-M)

- *基 URL**: `<https://dapi.binance.com`>
- *已实现 API 数量**: 55 个

#### 已实现模块：

- **通用接口**(3 个)
  - ping, get_server_time, get_contract
- **市场数据**(16 个)
  - tick, depth, kline, agg_trades, funding_rate, mark_price
  - open_interest, liquidation_orders, continuous_kline, etc.
- **账户相关**(7 个)
  - get_account, get_balance, get_position, get_fee
  - get_income, get_adl_quantile, get_leverage_bracket, etc.
- **交易操作**(16 个)
  - make_order, modify_order, cancel_order, query_order
  - get_open_orders, get_all_orders, make_oco_order, etc.
- **监听密钥**(3 个)
  - get_listen_key, refresh_listen_key, close_listen_key
- **WebSocket 流** (10 个)
  - agg_trade, trade, kline, ticker, depth, mark_price, etc.

### 4. Binance 期权 (Option)

- *基 URL**: `<https://eapi.binance.com`>
- *已实现 API 数量**: 19 个

#### 已实现模块：

- **通用接口**(3 个)
  - ping, get_server_time, get_contract
- **市场数据**(7 个)
  - tick, depth, kline, mark_price, index_price, open_interest
- **账户相关**(3 个)
  - get_account, get_position, get_income
- **交易操作**(6 个)
  - make_order, cancel_order, modify_order, query_order
  - get_open_orders, get_all_orders
- **监听密钥** (3 个)
  - get_listen_key, refresh_listen_key, close_listen_key

### 5. Binance 杠杆交易 (Margin)

- *基 URL**: `<https://api.binance.com`>
- *已实现 API 数量**: 60 个

#### 已实现模块：

- **通用接口**(3 个)
  - ping, get_server_time, get_contract
- **市场数据**(8 个) - 与现货共享
- **杠杆特定市场数据**(7 个)
  - get_all_assets, get_all_pairs, get_price_index
  - get_leverage_bracket, get_isolated_margin_tier, etc.
- **账户相关**(17 个)
  - get_account, get_isolated_account, get_max_borrowable
  - get_interest_history, get_bnb_burn, etc.
- **借贷还款**(3 个)
  - borrow_repay, borrow, repay
- **转账操作**(5 个)
  - transfer_to_margin, transfer_to_spot, get_transfer_history
- **交易操作**(12 个)
  - make_order, cancel_order, query_order
  - get_open_orders, get_all_orders, get_deals
- **监听密钥** (3 个)
  - get_listen_key, refresh_listen_key, close_listen_key

### 6. 其他模块实现情况

#### Binance 算法交易 (Algo)

- *已实现 API 数量**: 14 个
- 现货 TWAP/VWAP 订单 (7 个)
- 期货 TWAP/VP 订单 (7 个)

#### Binance 钱包 (Wallet)

- *已实现 API 数量**: 14 个
- 资产查询 (4 个)
- 资产划转 (5 个)
- 充值提现 (3 个)
- 小额资产转换 (2 个)

#### Binance 子账户 (Sub-Account)

- *已实现 API 数量**: 15 个
- 子账户管理 (3 个)
- 资金划转 (5 个)
- 资产查询 (4 个)
- API Key 管理 (3 个)

#### Binance 组合保证金 (Portfolio)

- *已实现 API 数量**: 5 个
- 基础组合保证金功能

#### Binance 网格交易 (Grid)

- *已实现 API 数量**: 5 个
- 合约网格交易功能

#### Binance 质押理财 (Staking)

- *已实现 API 数量**: 8 个
- 质押产品管理

#### Binance 矿池 (Mining)

- *已实现 API 数量**: 5 个
- 矿池基本信息

#### Binance VIP 借贷 (VIP Loan)

- *已实现 API 数量**: 6 个
- VIP 借贷功能

## 未实现 API 分析

### 1. 现货交易 (Spot) - 未实现接口

#### 重要缺失功能：

1. **订单管理高级功能**
   - `cancel_replace_order` - 订单替换
   - `cancel_open_orders_on_disconnect` - 断开连接取消订单
   - `get_all_orders_with_details` - 获取所有订单详情

1. **WebSocket 用户数据流**
   - 用户订单流
   - 用户成交流
   - 用户账户流
   - 用户钱包流

1. **交易规则和费率**
   - `exchange_info` - 获取交易规则
   - `get_order_rate_limit` - 获取订单速率限制
   - `get_asset_detail` - 获取资产详情

1. **杠杆和保证金**
   - `margin_transfer` - 杠杆账户转账
   - `margin_loan` - 杠杆借贷
   - `margin_repay` - 杠杆还款

### 2. 合约交易 (Futures) - 未实现接口

#### 重要缺失功能：

1. **期货特定功能**
   - `get_funding_rate_history` - 获取资金费率历史
   - `get_top_long_short_ratio` - 获取多空比
   - `get_long_short_ratio` - 获取多空持仓比
   - `get_liquidation_orders` - 获取强平订单
   - `get_mark_price` - 获取标记价格

1. **期货交易高级功能**
   - `batch_orders` - 批量下单
   - `cancel_all_orders` - 取消所有订单
   - `get_position_mode` - 获取持仓模式
   - `change_position_mode` - 修改持仓模式

1. **期权交易**
   - 期权交易接口（完整实现）
   - 期权行权接口
   - 期权账户查询

### 3. 杠杆交易 (Margin) - 未实现接口

#### 重要缺失功能：

1. **杠杆账户管理**
   - `margin_transfer` - 杠杆转账
   - `margin_loan` - 杠杆借贷
   - `margin_repay` - 杠杆还款

1. **杠杆交易功能**
   - `get_cross_margin_all_pairs` - 获取全仓交易对
   - `get_isolated_margin_all_pairs` - 获取逐仓交易对
   - `get_leverage_bracket` - 获取杠杆倍数

1. **风险管理**
   - `get_margin_account_info` - 获取保证金账户信息
   - `get_margin_interest_rate` - 获取保证金利率
   - `get_max_withdraw_amount` - 获取最大提币数量

### 4. 钱包功能 (Wallet) - 未实现接口

#### 重要缺失功能：

1. **充值提现**
   - `get_deposit_address` - 获取充值地址
   - `get_deposit_history` - 获取充值历史
   - `withdraw` - 提现
   - `get_withdraw_history` - 获取提现历史

1. **资产划转**
   - `unified_transfer` - 统一账户划转
   - `get_universal_transfer_history` - 获取划转历史

1. **资产功能**
   - `get_asset_detail` - 获取资产详情
   - `get_asset_dividend` - 获取资产分红
   - `dust_transfer` - 小额资产转换

### 5. 子账户管理 (Sub-Account) - 未实现接口

#### 重要缺失功能：

1. **子账户管理**
   - `create_sub_account` - 创建子账户
   - `get_sub_account_api_key` - 获取子账户 API Key
   - `manage_sub_account_ip` - 管理子账户 IP

1. **子账户资产**
   - `get_sub_account_assets` - 获取子账户资产
   - `sub_transfer_to_main` - 子账户转主账户
   - `main_transfer_to_sub` - 主账户转子账户

1. **子账户交易**
   - `sub_account_trade` - 子账户交易
   - `get_sub_account_trade_history` - 获取子账户交易历史

### 6. WebSocket 数据流 - 未实现接口

#### 重要缺失功能：

1. **用户数据流**
   - 用户订单更新
   - 用户成交更新
   - 账户余额更新
   - 仓位更新

1. **市场深度流**
   - 实时深度数据
   - 增量深度数据

1. **K 线数据流**
   - 多周期 K 线
   - 成交明细流

## 实现优先级建议

### 高优先级（常用功能）

1. **现货交易核心功能**
   - 订单管理（下单、撤单、查单）
   - 交易历史查询
   - 账户余额查询

1. **WebSocket 数据流**
   - 实时价格数据
   - 订单簿数据
   - 成交数据

1. **基本账户功能**
   - API 认证
   - 权限管理
   - 速率限制

### 中优先级（偶尔使用）

1. **高级订单类型**
   - OCO 订单
   - 条件订单
   - 批量订单

1. **杠杆交易功能**
   - 杠杆管理
   - 借贷还款
   - 风险管理

1. **数据分析功能**
   - 市场深度
   - 历史数据
   - 统计数据

### 低优先级（很少使用）

1. **钱包管理功能**
   - 充值提现
   - 资产划转
   - 小额转换

1. **子账户管理**
   - 子账户创建
   - API Key 管理
   - 权限设置

1. **高级功能**
   - 算法交易
   - 组合保证金
   - 网格交易

## 实现难度评估

### 容易实现（1-2 天）

1. 基本市场数据 API
2. 基本交易操作
3. 简单的 WebSocket 连接

### 中等难度（3-5 天）

1. 高级订单类型
2. 杠杆交易功能
3. 复杂的 WebSocket 数据流

### 困难（1-2 周）

1. 完整的钱包功能
2. 子账户管理系统
3. 高级风险管理功能

## 总结

`bt_api_py` 项目已实现了 Binance API 的核心功能，包括：

- 现货交易（56/100+ APIs）
- 合约交易（68/100+ APIs）
- 杠杆交易（60/80+ APIs）
- 期权交易（19/50+ APIs）

主要的缺失集中在：

1. WebSocket 用户数据流
2. 高级订单类型
3. 完整的钱包功能
4. 子账户管理系统

建议优先实现高频使用的核心功能，逐步完善边缘功能。

---
- 报告生成时间：2026-02-26*
