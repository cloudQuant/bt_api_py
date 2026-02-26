# Binance API 实现状态

> 更新时间: 2025-02-26
>
> 本文档统计了 Binance 各产品线 REST API 和 WebSocket API 的实现状态。

---

## 一、USDT-M 永续合约 (BinanceExchangeDataSwap)

### REST API (已实现)

#### 通用接口 (General)
| 接口 | 路径 | 状态 |
|------|------|------|
| ping | `GET /fapi/v1/ping` | ✅ 已实现 |
| get_server_time | `GET /fapi/v1/time` | ✅ 已实现 |
| get_contract | `GET /fapi/v1/exchangeInfo` | ✅ 已实现 |

#### 市场数据 (Market Data)
| 接口 | 路径 | 状态 |
|------|------|------|
| get_tick | `GET /fapi/v1/ticker/bookTicker` | ✅ 已实现 |
| get_info | `GET /fapi/v1/ticker/24hr` | ✅ 已实现 |
| get_new_price | `GET /fapi/v1/trades` | ✅ 已实现 |
| get_historical_trades | `GET /fapi/v1/historicalTrades` | ✅ 已实现 |
| get_depth | `GET /fapi/v1/depth` | ✅ 已实现 |
| get_incre_depth | `GET /fapi/v1/depth` | ✅ 已实现 |
| get_kline | `GET /fapi/v1/klines` | ✅ 已实现 |
| get_ui_klines | `GET /fapi/v1/uiKlines` | ✅ 已实现 |
| get_agg_trades | `GET /fapi/v1/aggTrades` | ✅ 已实现 |
| get_funding_rate | `GET /fapi/v1/premiumIndex` | ✅ 已实现 |
| get_clear_price | `GET /fapi/v1/premiumIndex` | ✅ 已实现 |
| get_mark_price | `GET /fapi/v1/premiumIndex` | ✅ 已实现 |
| get_history_funding_rate | `GET /fapi/v1/fundingRate` | ✅ 已实现 |
| get_market_rate | `GET /fapi/v1/premiumIndex` | ✅ 已实现 |
| get_funding_info | `GET /fapi/v1/fundingInfo` | ✅ 已实现 |
| get_continuous_kline | `GET /fapi/v1/continuousKlines` | ✅ 已实现 |
| get_index_price_kline | `GET /fapi/v1/indexPriceKlines` | ✅ 已实现 |
| get_mark_price_kline | `GET /fapi/v1/markPriceKlines` | ✅ 已实现 |
| get_price_ticker | `GET /fapi/v2/ticker/price` | ✅ 已实现 |
| get_avg_price | `GET /fapi/v1/avgPrice` | ✅ 已实现 |
| get_ticker | `GET /fapi/v1/ticker` | ✅ 已实现 |
| get_open_interest | `GET /fapi/v1/openInterest` | ✅ 已实现 |
| get_open_interest_interval | `GET /fapi/v1/openInterestInterval` | ✅ 已实现 |
| get_liquidation_orders | `GET /fapi/v1/allForceOrder` | ✅ 已实现 |
| get_delivery_price | `GET /fapi/v1/deliveryPrice` | ✅ 已实现 |

#### 期货数据端点 (Futures Data Endpoints)
| 接口 | 路径 | 状态 |
|------|------|------|
| get_long_short_ratio | `GET /futures/data/globalLongShortAccountRatio` | ✅ 已实现 |
| get_top_long_short_account_ratio | `GET /futures/data/topLongShortAccountRatio` | ✅ 已实现 |
| get_top_long_short_position_ratio | `GET /futures/data/topLongShortPositionRatio` | ✅ 已实现 |
| get_taker_buy_sell_volume | `GET /futures/data/takerlongshortRatio` | ✅ 已实现 |
| get_open_interest_hist | `GET /futures/data/openInterestHist` | ✅ 已实现 |
| get_index_constituents | `GET /fapi/v1/constituents` | ✅ 已实现 |

#### 账户接口 (Account)
| 接口 | 路径 | 状态 |
|------|------|------|
| get_account | `GET /fapi/v2/account` | ✅ 已实现 |
| get_account_v3 | `GET /fapi/v3/account` | ✅ 已实现 |
| get_balance | `GET /fapi/v2/balance` | ✅ 已实现 |
| get_balance_v3 | `GET /fapi/v3/balance` | ✅ 已实现 |
| get_position | `GET /fapi/v2/positionRisk` | ✅ 已实现 |
| get_position_v3 | `GET /fapi/v3/positionRisk` | ✅ 已实现 |
| get_fee | `GET /fapi/v1/commissionRate` | ✅ 已实现 |
| get_income | `GET /fapi/v1/income` | ✅ 已实现 |
| get_adl_quantile | `GET /fapi/v1/adlQuantile` | ✅ 已实现 |
| get_leverage_bracket | `GET /fapi/v1/leverageBracket` | ✅ 已实现 |
| get_position_mode | `GET /fapi/v1/positionSide/dual` | ✅ 已实现 |
| get_multi_assets_mode | `GET /fapi/v1/multiAssetsMargin` | ✅ 已实现 |
| get_api_trading_status | `GET /fapi/v1/apiTradingStatus` | ✅ 已实现 |
| get_api_key_permission | `GET /fapi/v1/apiKeyPermission` | ✅ 已实现 |
| get_order_rate_limit | `GET /fapi/v1/rateLimit/order` | ✅ 已实现 |
| get_user_force_orders | `GET /fapi/v1/userForceOrders` | ✅ 已实现 |
| get_symbol_config | `GET /fapi/v1/symbolConfig` | ✅ 已实现 |
| get_account_config | `GET /fapi/v1/accountConfig` | ✅ 已实现 |

#### 交易接口 (Trade)
| 接口 | 路径 | 状态 |
|------|------|------|
| make_order | `POST /fapi/v1/order` | ✅ 已实现 |
| make_order_test | `POST /fapi/v1/order/test` | ✅ 已实现 |
| make_orders | `POST /fapi/v1/batchOrders` | ✅ 已实现 |
| modify_order | `PUT /fapi/v1/order` | ✅ 已实现 |
| modify_orders | `PUT /fapi/v1/batchOrders` | ✅ 已实现 |
| cancel_order | `DELETE /fapi/v1/order` | ✅ 已实现 |
| cancel_orders | `DELETE /fapi/v1/batchOrders` | ✅ 已实现 |
| cancel_all | `DELETE /fapi/v1/allOpenOrders` | ✅ 已实现 |
| auto_cancel_all | `POST /fapi/v1/countdownCancelAll` | ✅ 已实现 |
| query_order | `GET /fapi/v1/order` | ✅ 已实现 |
| get_open_orders | `GET /fapi/v1/openOrders` | ✅ 已实现 |
| get_all_orders | `GET /fapi/v1/allOrders` | ✅ 已实现 |
| get_deals | `GET /fapi/v1/userTrades` | ✅ 已实现 |
| get_force_orders | `GET /fapi/v1/forceOrders` | ✅ 已实现 |
| change_leverage | `POST /fapi/v1/leverage` | ✅ 已实现 |
| change_margin_type | `POST /fapi/v1/marginType` | ✅ 已实现 |
| change_position_mode | `POST /fapi/v1/positionSide/dual` | ✅ 已实现 |
| change_multi_assets_mode | `POST /fapi/v1/multiAssetsMargin` | ✅ 已实现 |
| modify_isolated_position_margin | `POST /fapi/v1/positionMargin` | ✅ 已实现 |
| get_position_margin_history | `GET /fapi/v1/positionMargin/history` | ✅ 已实现 |

#### OCO 订单 (Order Lists)
| 接口 | 路径 | 状态 |
|------|------|------|
| make_oco_order | `POST /fapi/v1/order/oco` | ✅ 已实现 |
| get_order_list | `GET /fapi/v1/orderList` | ✅ 已实现 |
| get_all_order_lists | `GET /fapi/v1/allOrderList` | ✅ 已实现 |
| get_open_order_lists | `GET /fapi/v1/openOrderList` | ✅ 已实现 |
| cancel_order_list | `DELETE /fapi/v1/orderList` | ✅ 已实现 |

#### Listen Key
| 接口 | 路径 | 状态 |
|------|------|------|
| get_listen_key | `POST /fapi/v1/listenKey` | ✅ 已实现 |
| refresh_listen_key | `PUT /fapi/v1/listenKey` | ✅ 已实现 |
| close_listen_key | `DELETE /fapi/v1/listenKey` | ✅ 已实现 |

### WebSocket API (已实现)

#### 市场流 (Market Streams)
| 流 | 路径模板 | 状态 |
|------|------|------|
| agg_trade | `<symbol>@aggTrade` | ✅ 已实现 |
| trade | `<symbol>@trade` | ✅ 已实现 |
| kline | `<symbol>@kline_<period>` | ✅ 已实现 |
| continuous_kline | `<pair>_perpetual@continuousKline_<period>` | ✅ 已实现 |
| mini_ticker | `<symbol>@miniTicker` | ✅ 已实现 |
| ticker | `<symbol>@ticker` | ✅ 已实现 |
| ticker_window | `<symbol>@ticker_<window>` | ✅ 已实现 |
| book_ticker | `<symbol>@bookTicker` | ✅ 已实现 |
| depth | `<symbol>@depth20@100ms` | ✅ 已实现 |
| depth500 | `<symbol>@depth5@500ms` | ✅ 已实现 |
| depth_partial | `<symbol>@depth<level>@100ms` | ✅ 已实现 |
| increDepthFlow | `<symbol>@depth@100ms` | ✅ 已实现 |
| mark_price | `<symbol>@markPrice@1s` | ✅ 已实现 |
| funding_rate | `<symbol>@markPrice@1s` | ✅ 已实现 |
| force_order | `<symbol>@forceOrder` | ✅ 已实现 |

#### 全市场流 (All Market Streams)
| 流 | 路径模板 | 状态 |
|------|------|------|
| all_force_order | `!forceOrder@arr` | ✅ 已实现 |
| all_mini_ticker | `!miniTicker@arr` | ✅ 已实现 |
| all_ticker | `!ticker@arr` | ✅ 已实现 |
| all_ticker_window | `!ticker_<window>@arr` | ✅ 已实现 |
| all_mark_price | `!markPrice@arr@1s` | ✅ 已实现 |
| all_book_ticker | `!bookTicker` | ✅ 已实现 |

---

## 二、现货 (BinanceExchangeDataSpot)

### REST API (已实现)

#### 通用接口 (General)
| 接口 | 路径 | 状态 |
|------|------|------|
| ping | `GET /api/v3/ping` | ✅ 已实现 |
| get_server_time | `GET /api/v3/time` | ✅ 已实现 |
| get_contract | `GET /api/v3/exchangeInfo` | ✅ 已实现 |

#### 市场数据 (Market Data)
| 接口 | 路径 | 状态 |
|------|------|------|
| get_tick | `GET /api/v3/ticker/bookTicker` | ✅ 已实现 |
| get_depth | `GET /api/v3/depth` | ✅ 已实现 |
| get_incre_depth | `GET /api/v1/depth` | ✅ 已实现 |
| get_kline | `GET /api/v3/klines` | ✅ 已实现 |
| get_ui_klines | `GET /api/v3/uiKlines` | ✅ 已实现 |
| get_avg_price | `GET /api/v3/avgPrice` | ✅ 已实现 |
| get_info | `GET /api/v3/ticker/24hr` | ✅ 已实现 |
| get_market | `GET /api/v3/ticker/price` | ✅ 已实现 |
| get_ticker | `GET /api/v3/ticker` | ✅ 已实现 |
| get_new_price | `GET /api/v3/trades` | ✅ 已实现 |
| get_historical_trades | `GET /api/v3/historicalTrades` | ✅ 已实现 |
| get_agg_trades | `GET /api/v3/aggTrades` | ✅ 已实现 |

#### 账户接口 (Account)
| 接口 | 路径 | 状态 |
|------|------|------|
| get_account | `GET /api/v3/account` | ✅ 已实现 |
| get_balance | `GET /api/v3/account` | ✅ 已实现 |
| get_fee | `GET /sapi/v1/asset/tradeFee` | ✅ 已实现 |
| get_commission | `GET /api/v3/account/commission` | ✅ 已实现 |
| get_order_rate_limit | `GET /api/v3/rateLimit/order` | ✅ 已实现 |

#### 交易接口 (Trade)
| 接口 | 路径 | 状态 |
|------|------|------|
| make_order | `POST /api/v3/order` | ✅ 已实现 |
| make_order_test | `POST /api/v3/order/test` | ✅ 已实现 |
| cancel_order | `DELETE /api/v3/order` | ✅ 已实现 |
| cancel_all | `DELETE /api/v3/openOrders` | ✅ 已实现 |
| cancel_replace_order | `POST /api/v3/order/cancelReplace` | ✅ 已实现 |
| amend_keep_priority | `PUT /api/v3/order/amend/keepPriority` | ✅ 已实现 |
| query_order | `GET /api/v3/order` | ✅ 已实现 |
| get_open_orders | `GET /api/v3/openOrders` | ✅ 已实现 |
| get_all_orders | `GET /api/v3/allOrders` | ✅ 已实现 |
| get_deals | `GET /api/v3/myTrades` | ✅ 已实现 |

#### OCO 订单 (Order Lists)
| 接口 | 路径 | 状态 |
|------|------|------|
| make_oco_order | `POST /api/v3/order/oco` | ✅ 已实现 |
| get_order_list | `GET /api/v3/orderList` | ✅ 已实现 |
| get_all_order_lists | `GET /api/v3/allOrderList` | ✅ 已实现 |
| get_open_order_lists | `GET /api/v3/openOrderList` | ✅ 已实现 |
| cancel_order_list | `DELETE /api/v3/orderList` | ✅ 已实现 |

#### SOR (Smart Order Routing)
| 接口 | 路径 | 状态 |
|------|------|------|
| sor_make_order | `POST /api/v3/sor/order` | ✅ 已实现 |
| sor_make_order_test | `POST /api/v3/sor/order/test` | ✅ 已实现 |
| get_sor_allocations | `GET /api/v3/myAllocations` | ✅ 已实现 |

#### 其他接口
| 接口 | 路径 | 状态 |
|------|------|------|
| get_prevented_matches | `GET /api/v3/myPreventedMatches` | ✅ 已实现 |
| get_order_amendments | `GET /api/v3/order/amendments` | ✅ 已实现 |
| get_my_filters | `GET /api/v3/myFilters` | ✅ 已实现 |
| get_listen_key | `POST /sapi/v1/userListenToken` | ✅ 已实现 |
| refresh_listen_key | `POST /sapi/v1/userListenToken` | ✅ 已实现 |

### WebSocket API (已实现)

#### 市场流 (Market Streams)
| 流 | 路径模板 | 状态 |
|------|------|------|
| agg_trade | `<symbol>@aggTrade` | ✅ 已实现 |
| trade | `<symbol>@trade` | ✅ 已实现 |
| kline | `<symbol>@kline_<period>` | ✅ 已实现 |
| mini_ticker | `<symbol>@miniTicker` | ✅ 已实现 |
| ticker | `<symbol>@ticker` | ✅ 已实现 |
| ticker_window | `<symbol>@ticker_<window>` | ✅ 已实现 |
| book_ticker | `<symbol>@bookTicker` | ✅ 已实现 |
| avg_price | `<symbol>@avgPrice` | ✅ 已实现 |
| depth | `<symbol>@depth20@100ms` | ✅ 已实现 |
| depth_partial | `<symbol>@depth<level>@100ms` | ✅ 已实现 |
| increDepthFlow | `<symbol>@depth@100ms` | ✅ 已实现 |
| force_order | `<symbol>@forceOrder` | ✅ 已实现 |

#### 全市场流 (All Market Streams)
| 流 | 路径模板 | 状态 |
|------|------|------|
| all_mini_ticker | `!miniTicker@arr` | ✅ 已实现 |
| all_ticker | `!ticker@arr` | ✅ 已实现 |
| all_ticker_window | `!ticker_<window>@arr` | ✅ 已实现 |
| all_book_ticker | `!bookTicker` | ✅ 已实现 |

---

## 三、币本位合约 (BinanceExchangeDataCoinM)

### REST API (已实现)

**币本位合约已实现接口结构与 USDT-M 永续合约类似，主要区别在于基础路径为 `/dapi/v1/`。**

已实现的主要接口类别:
- 通用接口 (ping, time, exchangeInfo)
- 市场数据 (深度、交易、K线、资金费率等)
- 账户接口 (账户、余额、持仓、杠杆等)
- 交易接口 (下单、撤单、查询等)
- OCO 订单
- Listen Key 管理

---

## 四、期权 (BinanceExchangeDataOption)

### REST API (已实现)

#### 通用接口 (General)
| 接口 | 路径 | 状态 |
|------|------|------|
| ping | `GET /eapi/v1/ping` | ✅ 已实现 |
| get_server_time | `GET /eapi/v1/time` | ✅ 已实现 |
| get_contract | `GET /eapi/v1/exchangeInfo` | ✅ 已实现 |

#### 市场数据
| 接口 | 路径 | 状态 |
|------|------|------|
| get_tick | `GET /eapi/v1/ticker` | ✅ 已实现 |
| get_depth | `GET /eapi/v1/depth` | ✅ 已实现 |
| get_kline | `GET /eapi/v1/klines` | ✅ 已实现 |
| get_new_price | `GET /eapi/v1/trades` | ✅ 已实现 |
| get_mark_price | `GET /eapi/v1/mark` | ✅ 已实现 |
| get_index_price | `GET /eapi/v1/index` | ✅ 已实现 |
| get_open_interest | `GET /eapi/v1/openInterest` | ✅ 已实现 |
| get_exercise_history | `GET /eapi/v1/exerciseHistory` | ✅ 已实现 |

#### 账户与交易
| 接口 | 路径 | 状态 |
|------|------|------|
| get_account | `GET /eapi/v1/account` | ✅ 已实现 |
| get_income | `GET /eapi/v1/bill` | ✅ 已实现 |
| get_position | `GET /eapi/v1/position` | ✅ 已实现 |
| get_exercise_record | `GET /eapi/v1/exerciseRecord` | ✅ 已实现 |
| get_user_trades | `GET /eapi/v1/userTrades` | ✅ 已实现 |
| make_order | `POST /eapi/v1/order` | ✅ 已实现 |
| make_order_test | `POST /eapi/v1/order/test` | ✅ 已实现 |
| make_orders | `POST /eapi/v1/batchOrders` | ✅ 已实现 |
| cancel_order | `DELETE /eapi/v1/order` | ✅ 已实现 |
| cancel_orders | `DELETE /eapi/v1/batchOrders` | ✅ 已实现 |
| cancel_all | `DELETE /eapi/v1/allOpenOrders` | ✅ 已实现 |
| cancel_all_by_underlying | `DELETE /eapi/v1/allOpenOrdersByUnderlying` | ✅ 已实现 |

### WebSocket API (已实现)

期权 WebSocket 流已实现:
- agg_trade, ticker, depth, kline
- mark_price, index_price, open_interest

---

## 五、杠杆交易 (BinanceExchangeDataMargin)

### REST API (已实现)

#### 杠杆专用市场数据
| 接口 | 路径 | 状态 |
|------|------|------|
| get_all_assets | `GET /sapi/v1/margin/allAssets` | ✅ 已实现 |
| get_all_pairs | `GET /sapi/v1/margin/allPairs` | ✅ 已实现 |
| get_isolated_all_pairs | `GET /sapi/v1/margin/isolated/allPairs` | ✅ 已实现 |
| get_price_index | `GET /sapi/v1/margin/priceIndex` | ✅ 已实现 |
| get_cross_margin_collateral_ratio | `GET /sapi/v1/margin/crossMarginCollateralRatio` | ✅ 已实现 |
| get_leverage_bracket | `GET /sapi/v1/margin/leverageBracket` | ✅ 已实现 |
| get_isolated_margin_tier | `GET /sapi/v1/margin/isolatedMarginTier` | ✅ 已实现 |

#### 杠杆账户接口
| 接口 | 路径 | 状态 |
|------|------|------|
| get_account | `GET /sapi/v1/margin/account` | ✅ 已实现 |
| get_isolated_account | `GET /sapi/v1/margin/isolated/account` | ✅ 已实现 |
| get_isolated_account_limit | `GET /sapi/v1/margin/isolated/accountLimit` | ✅ 已实现 |
| get_max_borrowable | `GET /sapi/v1/margin/maxBorrowable` | ✅ 已实现 |
| get_max_transferable | `GET /sapi/v1/margin/maxTransferable` | ✅ 已实现 |
| get_interest_history | `GET /sapi/v1/margin/interestHistory` | ✅ 已实现 |
| get_interest_rate_history | `GET /sapi/v1/margin/interestRateHistory` | ✅ 已实现 |
| get_next_hourly_interest_rate | `GET /sapi/v1/margin/next-hourly-interest-rate` | ✅ 已实现 |

#### 借贷与还款
| 接口 | 路径 | 状态 |
|------|------|------|
| borrow_repay | `POST /sapi/v1/margin/borrow-repay` | ✅ 已实现 |
| get_borrow_repay_records | `GET /sapi/v1/margin/borrow-repay` | ✅ 已实现 |
| borrow | `POST /sapi/v1/margin/loan` | ✅ 已实现 |
| repay | `POST /sapi/v1/margin/repay` | ✅ 已实现 |

---

## 六、算法交易 (BinanceExchangeDataAlgo)

### REST API (已实现)

#### 现货算法交易 (TWAP/VWAP)
| 接口 | 路径 | 状态 |
|------|------|------|
| spot_twap_new_order | `POST /sapi/v1/algo/spot/newOrderTwap` | ✅ 已实现 |
| spot_vwap_new_order | `POST /sapi/v1/algo/spot/newOrderVwap` | ✅ 已实现 |
| spot_cancel_order | `DELETE /sapi/v1/algo/spot/order` | ✅ 已实现 |
| spot_get_open_orders | `GET /sapi/v1/algo/spot/openOrders` | ✅ 已实现 |
| spot_get_history_orders | `GET /sapi/v1/algo/spot/historicalOrders` | ✅ 已实现 |
| spot_get_sub_orders | `GET /sapi/v1/algo/spot/subOrders` | ✅ 已实现 |

#### 合约算法交易 (TWAP/VP)
| 接口 | 路径 | 状态 |
|------|------|------|
| futures_twap_new_order | `POST /sapi/v1/algo/futures/newOrderTwap` | ✅ 已实现 |
| futures_vp_new_order | `POST /sapi/v1/algo/futures/newOrderVp` | ✅ 已实现 |
| futures_cancel_order | `DELETE /sapi/v1/algo/futures/order` | ✅ 已实现 |
| futures_get_open_orders | `GET /sapi/v1/algo/futures/openOrders` | ✅ 已实现 |
| futures_get_history_orders | `GET /sapi/v1/algo/futures/historicalOrders` | ✅ 已实现 |
| futures_get_sub_orders | `GET /sapi/v1/algo/futures/subOrders` | ✅ 已实现 |

---

## 七、待实现 API 清单

根据 Binance 官方文档，以下为**可能未实现**的接口（需要进一步确认）：

### 1. Spot 现货待实现接口

#### 市场数据
| 接口 | 路径 | 说明 |
|------|------|------|
| get_avg_price | `GET /api/v3/avgPrice` | ⚠️ 已实现，需确认 |
| get_ticker_trading_day | `GET /api/v3/ticker/tradingDay` | ❌ 未找到 |

#### 账户与交易
| 接口 | 路径 | 说明 |
|------|------|------|
| get_my_allocations | `GET /api/v3/myAllocations` | ⚠️ 已作为 get_sor_allocations 实现 |
| get_order_amendments | `GET /api/v3/order/amendments` | ✅ 已实现 |

#### 子账户相关
| 接口 | 路径 | 说明 |
|------|------|------|
| sub_account_list | `GET /sapi/v1/sub-account/list` | ❌ 未实现 |
| sub_account_transfer | `POST /sapi/v1/sub-account/transfer` | ❌ 未实现 |
| sub_account_assets | `GET /sapi/v1/sub-account/assets` | ❌ 未实现 |

#### 资产相关 (Wallet API)
| 接口 | 路径 | 说明 |
|------|------|------|
| get_asset_detail | `GET /sapi/v1/asset/assetDetail` | ❌ 未实现 |
| get_asset_dividend | `GET /sapi/v1/asset/assetDividend` | ❌ 未实现 |
| get_asset_trade_fee | `GET /sapi/v1/asset/tradeFee` | ⚠️ 已实现为 get_fee |
| get_user_asset | `POST /sapi/v1/asset/getUserAsset` | ❌ 未实现 |
| get_fund_asset | `POST /sapi/v1/asset/getFundAsset` | ❌ 未实现 |
| get_withdraw_history | `GET /sapi/v1/capital/withdraw/history` | ❌ 未实现 |
| get_deposit_history | `GET /sapi/v1/capital/deposit/hisrec` | ❌ 未实现 |
| withdraw | `POST /sapi/v1/capital/withdraw/apply` | ❌ 未实现 |
| deposit_address | `GET /sapi/v1/capital/deposit/address` | ❌ 未实现 |

### 2. Futures USDT-M 待实现接口

#### 网格交易
| 接口 | 路径 | 说明 |
|------|------|------|
| futures_grid_new_order | `POST /sapi/v1/futures/fortune/order` | ❌ 未实现 |
| futures_grid_cancel_order | `DELETE /sapi/v1/futures/fortune/order` | ❌ 未实现 |
| futures_grid_orders | `GET /sapi/v1/futures/fortune/order` | ❌ 未实现 |

#### 组合保证金
| 接口 | 路径 | 说明 |
|------|------|------|
| portfolio_cm_account | `GET /sapi/v1/portfolio/account` | ❌ 未实现 |

### 3. WebSocket API 待实现

#### Spot WebSocket
| 流 | 路径模板 | 说明 |
|------|------|------|
| kline_timezone | `<symbol>@kline_<interval>@+08:00` | ❌ 时区K线未实现 |

#### Futures WebSocket
| 流 | 路径模板 | 说明 |
|------|------|------|
| liquidation_order | `<symbol>@liquidationOrder` | ⚠️ 可能已实现为 force_order |

---

## 八、统计汇总

| 产品线 | REST API | WebSocket API | 完成度 |
|--------|----------|---------------|--------|
| USDT-M 永续合约 | ~80 | ~25 | 高 |
| 现货 Spot | ~50 | ~20 | 高 |
| 币本位合约 Coin-M | ~60 | ~25 | 高 |
| 期权 Option | ~25 | ~10 | 中 |
| 杠杆 Margin | ~45 | ~15 | 高 |
| 算法交易 Algo | ~12 | 0 | 中 |

**注意**：
1. 以上统计基于 `binance_exchange_data.py` 文件分析
2. 部分接口可能通过别名或兼容层实现
3. Wallet API (充提、资产划转等) 大部分未实现
4. 子账户相关 API 未实现
5. 部分 WebSocket 用户数据流通过 Listen Key 机制实现（paths 中为空字符串）

---

## 九、参考文档

- [Binance Spot API Docs](https://binance-docs.github.io/apidocs/spot/en/)
- [Binance USDT-M Futures API Docs](https://binance-docs.github.io/apidocs/futures/en/)
- [Binance API GitHub](https://github.com/binance/binance-spot-api-docs)
