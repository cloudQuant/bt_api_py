# Binance API 待实现清单

> 创建时间: 2025-02-26
>
> 本文档列出了 Binance API 中尚未实现的接口，按优先级分类。

---

## 优先级说明

- 🔴 ***P0 - 高优先级**: 常用核心功能，建议优先实现
- 🟡 ***P1 - 中优先级**: 较常用功能，建议后续实现
- 🟢 ***P2 - 低优先级**: 不常用或特定场景功能

---

## 一、Wallet API (资产钱包) 🔴 P0

### 1.1 资产查询

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_wallet_balance | `GET /sapi/v1/asset/wallet/balance` | 获取钱包余额 | 🔴 P0 |
| [ ] | get_asset_detail | `GET /sapi/v1/asset/assetDetail` | 获取资产详情 | 🟡 P1 |
| [ ] | get_asset_ledger | `GET /sapi/v1/asset/ledger` | 获取资产账本(转账历史) | 🟡 P1 |
| [ ] | get_asset_dividend | `GET /sapi/v1/asset/assetDividend` | 获取资产分红记录 | 🟢 P2 |

### 1.2 资产划转

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | asset_transfer | `POST /sapi/v1/asset/transfer` | 资产划转(通用) | 🔴 P0 |
| [ ] | get_asset_transfer | `GET /sapi/v1/asset/transfer` | 查询划转状态 | 🟡 P1 |
| [ ] | transfer_to_futures_main | `POST /sapi/v1/asset/transfer-to-future-main-account` | 转账至合约主账户 | 🟡 P1 |
| [ ] | transfer_to_futures_sub | `POST /sapi/v1/asset/transfer-to-future-sub-account` | 转账至合约子账户 | 🟢 P2 |
| [ ] | transfer_to_um | `POST /sapi/v1/asset/transfer-to-UM` | 转账至 U 本位合约 | 🟡 P1 |
| [ ] | transfer_to_isolated_margin | `POST /sapi/v1/asset/transfer-to-isolated-margin` | 转账至逐仓杠杆 | 🟡 P1 |

### 1.3 充值相关

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_deposit_address | `GET /sapi/v1/capital/deposit/address` | 获取充值地址 | 🔴 P0 |
| [ ] | get_deposit_history | `GET /sapi/v1/capital/deposit/hisrec` | 获取充值记录 | 🟡 P1 |

### 1.4 提现相关

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | withdraw | `POST /sapi/v1/capital/withdraw/apply` | 提现申请 | 🔴 P0 |
| [ ] | get_withdraw_history | `GET /sapi/v1/capital/withdraw/history` | 获取提现记录 | 🟡 P1 |
| [ ] | get_withdraw_address | `GET /sapi/v1/capital/withdraw/address` | 获取提现地址 | 🟢 P2 |

### 1.5 小额资产转换 (Dust)

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_dust | `GET /sapi/v1/asset/dust` | 获取小额资产列表 | 🟡 P1 |
| [ ] | dust_transfer | `POST /sapi/v1/asset/dust/btc` | 小额资产转换 BNB | 🟡 P1 |

---

## 二、Sub-account API (子账户) 🟡 P1

### 2.1 子账户管理

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_sub_account_list | `GET /sapi/v1/sub-account/list` | 获取子账户列表 | 🟡 P1 |
| [ ] | get_sub_account_status | `GET /sapi/v1/sub-account/status` | 获取子账户状态 | 🟢 P2 |
| [ ] | get_sub_account_spot_summary | `GET /sapi/v1/sub-account/spotSummary` | 获取子账户现货摘要 | 🟡 P1 |

### 2.2 子账户资金划转

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | sub_transfer_to_main | `POST /sapi/v1/sub-account/transfer/sub-to-main` | 子账户转主账户 | 🟡 P1 |
| [ ] | main_transfer_to_sub | `POST /sapi/v1/sub-account/transfer/main-to-sub` | 主账户转子账户 | 🟡 P1 |
| [ ] | sub_transfer_to_sub | `POST /sapi/v1/sub-account/transfer/sub-to-sub` | 子账户互转 | 🟢 P2 |
| [ ] | get_sub_transfer_history | `GET /sapi/v1/sub-account/sub-transfer-history` | 子账户划转历史 | 🟢 P2 |

### 2.3 子账户资产查询

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_sub_account_assets | `GET /sapi/v1/sub-account/assets` | 获取子账户资产 | 🟡 P1 |
| [ ] | get_sub_account_margin_account | `GET /sapi/v1/sub-account/margin/account` | 子账户杠杆信息 | 🟢 P2 |
| [ ] | get_sub_account_futures_account | `GET /sapi/v1/sub-account/futuresAccount` | 子账户合约信息 | 🟢 P2 |

### 2.4 子账户 API Key 管理

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | create_sub_api_key | `POST /sapi/v1/sub-account/apiKey` | 创建子账户 API Key | 🟢 P2 |
| [ ] | get_sub_api_key | `GET /sapi/v1/sub-account/apiKey` | 获取子账户 API Key | 🟢 P2 |
| [ ] | delete_sub_api_key | `DELETE /sapi/v1/sub-account/apiKey` | 删除子账户 API Key | 🟢 P2 |

---

## 三、现货 API 额外接口 🟡 P1

### 3.1 现货账户快照

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_account_snapshot | `GET /sapi/v1/accountSnapshot` | 获取账户快照 | 🟡 P1 |

### 3.2 现货交易额外接口

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_ticker_trading_day | `GET /api/v3/ticker/tradingDay` | 交易日统计数据 | 🟢 P2 |

---

## 四、杠杆 API 额外接口 🟡 P1

### 4.1 杠杆账户额外接口

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_cross_margin_data | `GET /sapi/v1/margin/crossMarginData` | 全仓保证金数据 | 🟡 P1 |
| [ ] | get_isolated_margin_data | `GET /sapi/v1/margin/isolatedMarginData` | 逐仓保证金数据 | 🟡 P1 |
| [ ] | get_capital_flow | `GET /sapi/v1/margin/capital-flow` | 资金流水 | 🟢 P2 |
| [ ] | get_bnb_burn | `GET /sapi/v1/bnbBurn` | 获取 BNB 抵扣状态 | 🟢 P2 |
| [ ] | toggle_bnb_burn | `POST /sapi/v1/bnbBurn` | 开关 BNB 抵扣 | 🟢 P2 |

### 4.2 杠杆交易额外接口

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | manual_liquidation | `POST /sapi/v1/margin/manual-liquidation` | 手动清算 | 🟢 P2 |
| [ ] | exchange_small_liability | `POST /sapi/v1/margin/exchange-small-liability` | 小额负债兑换 | 🟢 P2 |
| [ ] | get_small_liability_history | `GET /sapi/v1/margin/exchange-small-liability-history` | 小额负债兑换历史 | 🟢 P2 |
| [ ] | set_max_leverage | `POST /sapi/v1/margin/max-leverage` | 设置最大杠杆 | 🟢 P2 |

---

## 五、合约 API 额外接口 🟡 P1

### 5.1 组合保证金 (Portfolio Margin)

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_portfolio_account | `GET /sapi/v1/portfolio/account` | PM 账户信息 | 🟡 P1 |
| [ ] | get_portfolio_collateral_rate | `GET /sapi/v1/portfolio/collateralRate` | PM 抵押率 | 🟢 P2 |
| [ ] | portfolio_transfer | `POST /sapi/v1/portfolio/transfer` | PM 资产划转 | 🟢 P2 |

### 5.2 合约网格交易

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | futures_grid_new_order | `POST /sapi/v1/futures/fortune/order` | 创建网格订单 | 🟡 P1 |
| [ ] | futures_grid_cancel_order | `DELETE /sapi/v1/futures/fortune/order` | 取消网格订单 | 🟡 P1 |
| [ ] | get_futures_grid_orders | `GET /sapi/v1/futures/fortune/order` | 查询网格订单 | 🟡 P1 |
| [ ] | get_futures_grid_position | `GET /sapi/v1/futures/fortune/position` | 查询网格持仓 | 🟢 P2 |

### 5.3 合约额外接口

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_futures_transfer_history | `GET /sapi/v1/futures/transfer` | 合约划转历史 | 🟡 P1 |
| [ ] | futures_transfer | `POST /sapi/v1/futures/transfer` | 合约账户划转 | 🟡 P1 |

---

## 六、Staking API 🟢 P2

### 6.1 Staking 产品

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_staking_products | `GET /sapi/v1/staking/productList` | 获取 Staking 产品列表 | 🟢 P2 |
| [ ] | staking_purchase | `POST /sapi/v1/staking/purchase` | 购买 Staking 产品 | 🟢 P2 |
| [ ] | staking_redeem | `POST /sapi/v1/staking/redeem` | 赎回 Staking 产品 | 🟢 P2 |
| [ ] | get_staking_position | `GET /sapi/v1/staking/position` | 获取 Staking 持仓 | 🟢 P2 |
| [ ] | get_staking_history | `GET /sapi/v1/staking/stakingRecord` | 获取 Staking 历史 | 🟢 P2 |

---

## 七、Mining API (矿池) 🟢 P2

### 7.1 矿池接口

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_mining_algo_list | `GET /sapi/v1/mining/pub/algoList` | 获取算力列表 | 🟢 P2 |
| [ ] | get_mining_worker_list | `GET /sapi/v1/mining/worker/list` | 获取矿工列表 | 🟢 P2 |
| [ ] | get_mining_statistics | `GET /sapi/v1/mining/statistics/user/status` | 获取挖矿统计 | 🟢 P2 |

---

## 八、VIP Loan API (VIP 借贷) 🟢 P2

### 8.1 VIP Loan

| 任务 | 接口 | 路径 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | get_vip_loan_ongoing_orders | `GET /sapi/v1/loan/ongoing/order` | 获取进行中订单 | 🟢 P2 |
| [ ] | vip_loan_borrow | `POST /sapi/v1/loan/borrow` | VIP 借贷借款 | 🟢 P2 |
| [ ] | vip_loan_repay | `POST /sapi/v1/loan/repay` | VIP 借贷还款 | 🟢 P2 |

---

## 九、WebSocket API 待实现 🟡 P1

### 9.1 现货 WebSocket

| 任务 | 流 | 路径模板 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | kline_timezone | `<symbol>@kline_<interval>@+08:00` | UTC+8 时区 K 线 | 🟢 P2 |

### 9.2 合约 WebSocket

| 任务 | 流 | 路径模板 | 说明 | 状态 |
|------|------|------|------|------|
| [ ] | liquidation_order | `<symbol>@liquidationOrder` | 强平订单流 | 🟡 P1 |

---

## 十、实现进度跟踪

### 按优先级统计

| 优先级 | 接口数量 | 说明 |
|--------|----------|------|
| 🔴 P0 (高) | 7 | 核心功能：钱包余额、划转、充值、提现 |
| 🟡 P1 (中) | 35 | 常用功能：子账户、合约划转、杠杆、网格交易 |
| 🟢 P2 (低) | 25 | 不常用功能：Staking、矿池、VIP 借贷 |

### 按类别统计

| 类别 | 待实现数量 | 优先级 |
|------|------------|--------|
| Wallet API | 17 | 🔴 高 |
| Sub-account API | 13 | 🟡 中 |
| Spot 额外 | 2 | 🟡 中 |
| Margin 额外 | 10 | 🟡 中 |
| Futures 额外 | 7 | 🟡 中 |
| Staking API | 5 | 🟢 低 |
| Mining API | 3 | 🟢 低 |
| VIP Loan API | 3 | 🟢 低 |
| WebSocket | 2 | 🟡 中 |

***总计**: 约 62 个接口待实现

---

## 十一、实现进度

### ✅ 已完成 (2025-02-26)

#### 阶段一：核心钱包功能 (P0) ✅

- [x] get_wallet_balance - 钱包余额
- [x] asset_transfer - 资产划转
- [x] get_asset_transfer - 划转查询
- [x] transfer_to_futures_main - 转账至合约主账户
- [x] transfer_to_futures_sub - 转账至合约子账户
- [x] transfer_to_um - 转账至 U 本位合约
- [x] transfer_to_isolated_margin - 转账至逐仓杠杆
- [x] get_deposit_address - 充值地址
- [x] get_deposit_history - 充值记录
- [x] withdraw - 提现申请
- [x] get_withdraw_history - 提现记录
- [x] get_withdraw_address - 提现地址
- [x] get_asset_detail - 资产详情
- [x] get_asset_ledger - 资产账本
- [x] get_asset_dividend - 资产分红
- [x] get_dust - 小额资产列表
- [x] dust_transfer - 小额资产转换

#### 阶段二：子账户管理 (P1) ✅

- [x] get_sub_account_list - 子账户列表
- [x] get_sub_account_status - 子账户状态
- [x] get_sub_account_spot_summary - 子账户现货摘要
- [x] sub_transfer_to_main - 子账户转主账户
- [x] main_transfer_to_sub - 主账户转子账户
- [x] sub_transfer_to_sub - 子账户互转
- [x] get_sub_transfer_history - 子账户划转历史
- [x] get_sub_account_universal_transfer - 子账户通用划转
- [x] get_sub_account_assets - 子账户资产
- [x] get_sub_account_margin_account - 子账户杠杆信息
- [x] get_sub_account_margin_summary - 子账户杠杆摘要
- [x] get_sub_account_futures_account - 子账户合约信息
- [x] create_sub_api_key - 创建子账户 API Key
- [x] get_sub_api_key - 获取子账户 API Key
- [x] delete_sub_api_key - 删除子账户 API Key
- [x] get_sub_api_ip_restriction - IP 限制
- [x] delete_sub_ip_restriction - 删除 IP 限制

#### 阶段三：合约增强 (P1) ✅

- [x] get_portfolio_account - PM 账户信息
- [x] get_portfolio_collateral_rate - PM 抵押率
- [x] portfolio_transfer - PM 资产划转
- [x] futures_grid_new_order - 创建网格订单
- [x] futures_grid_cancel_order - 取消网格订单
- [x] get_futures_grid_orders - 查询网格订单
- [x] get_futures_grid_position - 查询网格持仓
- [x] get_futures_grid_income - 查询网格收益

#### 阶段四：其他功能 (P2) ✅

- [x] get_staking_products - Staking 产品列表
- [x] staking_purchase - 购买 Staking 产品
- [x] staking_redeem - 赎回 Staking 产品
- [x] get_staking_position - Staking 持仓
- [x] get_staking_history - Staking 历史
- [x] get_mining_algo_list - 算力列表
- [x] get_mining_worker_list - 矿工列表
- [x] get_mining_statistics - 挖矿统计
- [x] get_vip_loan_ongoing_orders - VIP 借贷进行中订单
- [x] vip_loan_borrow - VIP 借贷借款
- [x] vip_loan_repay - VIP 借贷还款
- [x] get_vip_loan_history - VIP 借贷历史
- [x] get_vip_repayment_history - VIP 还款历史

---

## 十二、代码实现位置

### 新增类

所有新增的 API 类已在 `bt_api_py/containers/exchanges/binance_exchange_data.py` 中实现：

| 类名 | 功能 | 接口数量 |
|------|------|----------|
| `BinanceExchangeDataWallet` | 钱包 API | 17 |
| `BinanceExchangeDataSubAccount` | 子账户 API | 17 |
| `BinanceExchangeDataPortfolio` | 组合保证金 API | 3 |
| `BinanceExchangeDataGrid` | 网格交易 API | 5 |
| `BinanceExchangeDataStaking` | 质押理财 API | 5 |
| `BinanceExchangeDataMining` | 矿池 API | 3 |
| `BinanceExchangeDataVipLoan` | VIP 借贷 API | 5 |

### 测试文件

测试文件位于 `tests/containers/exchanges/test_binance_wallet_api.py`，包含 19 个测试用例。

---

## 十三、实现建议

### 阶段一：核心钱包功能 (P0) ✅ 已完成

```bash

1. get_wallet_balance - 钱包余额
2. asset_transfer - 资产划转
3. get_deposit_address - 充值地址
4. get_deposit_history - 充值记录
5. withdraw - 提现
6. get_withdraw_history - 提现记录
7. get_asset_transfer - 划转查询

```bash

### 阶段二：子账户管理 (P1)

```bash

1. 子账户列表
2. 子账户划转
3. 子账户资产查询

```bash

### 阶段三：合约增强 (P1)

```bash

1. 组合保证金
2. 网格交易
3. 合约划转

```bash

---

## 十二、代码修改位置

### 新增接口位置

所有接口定义在 `bt_api_py/containers/exchanges/binance_exchange_data.py`：

1. **Wallet API**- 新增 `BinanceExchangeDataWallet` 类

2.**Sub-account API**- 新增 `BinanceExchangeDataSubAccount` 类
3.**Staking/Mining** - 新增对应的专用类

### 实现步骤

1. 在对应的类中添加 `rest_paths` 字典
2. 如需 WebSocket，添加 `wss_paths` 字典
3. 更新对应的请求处理逻辑
4. 添加单元测试

---

## 十三、参考文档

- [Binance Spot API](<https://binance-docs.github.io/apidocs/spot/en/)>
- [Binance USDT-M Futures API](<https://binance-docs.github.io/apidocs/futures/en/)>
- [Binance Wallet API](<https://binance-docs.github.io/apidocs/spot/en/#wallet-endpoints)>
