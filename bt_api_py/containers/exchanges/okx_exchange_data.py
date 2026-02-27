import json
import time
import copy
import datetime
from bt_api_py.containers.exchanges.exchange_data import ExchangeData


class OkxExchangeData(ExchangeData):
    """Base class for all OKX exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_paths, wss_paths.
    """

    def __init__(self):
        """这个类存放一些交易所用到的参数
        """
        super().__init__()
        self.exchange_name = 'OkxSwap'
        self.rest_url = 'https://www.okx.com'
        self.account_wss_url = 'wss://ws.okx.com:8443/ws/v5/private'
        self.wss_url = 'wss://ws.okx.com:8443/ws/v5/public'
        self.kline_wss_url = 'wss://ws.okx.com:8443/ws/v5/business'
        self.symbol_leverage_dict = {'BTC-USDT': 100,
                                     'ETH-USDT': 10,
                                     'BCH-USDT': 10,
                                     'DOGE-USDT': 0.001,
                                     "BNB-USDT": 10,
                                     "OP-USDT": 1, }

        self.rest_paths = {
            # --- Trading Account ---
            'get_account': 'GET /api/v5/account/balance',
            'get_balance': 'GET /api/v5/account/balance',
            'get_instruments': 'GET /api/v5/account/instruments',
            'get_balance_assert': 'GET /api/v5/asset/balances',
            'get_position': 'GET /api/v5/account/positions',
            'get_positions': 'GET /api/v5/account/positions',
            'get_positions_history': 'GET /api/v5/account/positions-history',
            'get_account_position_risk': 'GET /api/v5/account/account-position-risk',
            'get_bills': 'GET /api/v5/account/bills',
            'get_bills_archive': 'GET /api/v5/account/bills-archive',
            'apply_bills_history_archive': 'POST /api/v5/account/bills-history-archive',
            'get_bills_history_archive': 'GET /api/v5/account/bills-history-archive',
            'get_fee': 'GET /api/v5/account/trade-fee',
            'get_config': 'GET /api/v5/account/config',
            'set_mode': 'POST /api/v5/account/set-position-mode',
            'set_lever': 'POST /api/v5/account/set-leverage',
            'get_lever': 'GET /api/v5/account/leverage-info',
            'get_adjust_leverage_info': 'GET /api/v5/account/adjust-leverage-info',
            'get_max_size': 'GET /api/v5/account/max-size',
            'get_max_avail_size': 'GET /api/v5/account/max-avail-size',
            'set_margin_balance': 'POST /api/v5/account/position/margin-balance',
            'get_max_loan': 'GET /api/v5/account/max-loan',
            'get_interest_accrued': 'GET /api/v5/account/interest-accrued',
            'get_interest_rate': 'GET /api/v5/account/interest-rate',
            'set_fee_type': 'POST /api/v5/account/set-fee-type',
            'set_greeks': 'POST /api/v5/account/set-greeks',
            'set_isolated_mode': 'POST /api/v5/account/set-isolated-mode',
            'get_max_withdrawal': 'GET /api/v5/account/max-withdrawal',
            'get_risk_state': 'GET /api/v5/account/risk-state',
            'get_interest_limits': 'GET /api/v5/account/interest-limits',
            'borrow_repay': 'POST /api/v5/account/borrow-repay',
            'set_auto_repay': 'POST /api/v5/account/set-auto-repay',
            'get_borrow_repay_history': 'GET /api/v5/account/borrow-repay-history',
            'get_greeks': 'GET /api/v5/account/greeks',
            'get_position_tiers': 'GET /api/v5/account/position-tiers',
            'set_auto_loan': 'POST /api/v5/account/set-auto-loan',
            'set_account_level': 'POST /api/v5/account/set-account-level',
            'account_level_switch_preset': 'POST /api/v5/account/account-level-switch-preset',
            'account_level_switch_precheck': 'GET /api/v5/account/account-level-switch-precheck',
            'set_collateral_assets': 'POST /api/v5/account/set-collateral-assets',
            'get_collateral_assets': 'GET /api/v5/account/collateral-assets',
            'position_builder': 'POST /api/v5/account/position-builder',
            'position_builder_trend': 'POST /api/v5/account/position-builder-trend',
            'set_risk_offset_amt': 'POST /api/v5/account/set-riskOffset-amt',
            'activate_option': 'POST /api/v5/account/activate-option',
            'move_positions': 'POST /api/v5/account/position/move-positions',
            'get_move_positions_history': 'GET /api/v5/account/position/move-positions-history',
            'set_auto_earn': 'POST /api/v5/account/set-auto-earn',
            'set_settle_currency': 'POST /api/v5/account/set-settle-currency',
            'set_trading_config': 'POST /api/v5/account/set-trading-config',
            'set_delta_neutral_precheck': 'POST /api/v5/account/set-delta-neutral-precheck',
            'mmp_reset': 'POST /api/v5/account/mmp-reset',
            'set_mmp_config': 'POST /api/v5/account/mmp-config',
            'get_mmp_config': 'GET /api/v5/account/mmp-config',
            # --- Trade ---
            'make_order': 'POST /api/v5/trade/order',
            'make_orders': 'POST /api/v5/trade/batch-orders',
            'cancel_order': 'POST /api/v5/trade/cancel-order',
            'cancel_orders': 'POST /api/v5/trade/cancel-batch-orders',
            'cancel_all': 'POST /api/v5/trade/cancel-batch-orders',
            'amend_order': 'POST /api/v5/trade/amend-order',
            'amend_orders': 'POST /api/v5/trade/amend-batch-orders',
            'close_position': 'POST /api/v5/trade/close-position',
            'query_order': 'GET /api/v5/trade/order',
            'get_open_orders': 'GET /api/v5/trade/orders-pending',
            'get_order_history': 'GET /api/v5/trade/orders-history',
            'get_order_history_archive': 'GET /api/v5/trade/orders-history-archive',
            'get_deals': 'GET /api/v5/trade/fills-history',
            'get_fills': 'GET /api/v5/trade/fills',
            'get_fills_history': 'GET /api/v5/trade/fills-history',
            'get_easy_convert_currency_list': 'GET /api/v5/trade/easy-convert-currency-list',
            'easy_convert': 'POST /api/v5/trade/easy-convert',
            'get_easy_convert_history': 'GET /api/v5/trade/easy-convert-history',
            'get_one_click_repay_currency_list': 'GET /api/v5/trade/one-click-repay-currency-list',
            'one_click_repay': 'POST /api/v5/trade/one-click-repay',
            'get_one_click_repay_history': 'GET /api/v5/trade/one-click-repay-history',
            'mass_cancel': 'POST /api/v5/trade/mass-cancel',
            'cancel_all_after': 'POST /api/v5/trade/cancel-all-after',
            'get_account_rate_limit': 'GET /api/v5/trade/account-rate-limit',
            'order_precheck': 'POST /api/v5/trade/order-precheck',
            # --- Algo Trading ---
            'make_algo_order': 'POST /api/v5/trade/order-algo',
            'cancel_algo_order': 'POST /api/v5/trade/cancel-algos',
            'amend_algo_order': 'POST /api/v5/trade/amend-algos',
            'get_algo_order': 'GET /api/v5/trade/order-algo',
            'get_algo_orders_pending': 'GET /api/v5/trade/orders-algo-pending',
            'get_algo_orders_history': 'GET /api/v5/trade/orders-algo-history',
            # --- Grid Trading ---
            'grid_order_algo': 'POST /api/v5/tradingBot/grid/order-algo',
            'grid_amend_order_algo_basic': 'POST /api/v5/tradingBot/grid/amend-order-algo',
            'grid_amend_order_algo': 'POST /api/v5/tradingBot/grid/adjust-order-algo',
            'grid_stop_order_algo': 'POST /api/v5/tradingBot/grid/stop-order-algo',
            'grid_close_position': 'POST /api/v5/tradingBot/grid/close-position',
            'grid_cancel_close_order': 'POST /api/v5/tradingBot/grid/cancel-close-order',
            'grid_order_instant_trigger': 'POST /api/v5/tradingBot/grid/order-instant-trigger',
            'grid_orders_algo_pending': 'GET /api/v5/tradingBot/grid/orders-algo-pending',
            'grid_orders_algo_history': 'GET /api/v5/tradingBot/grid/orders-algo-history',
            'grid_orders_algo_details': 'GET /api/v5/tradingBot/grid/orders-algo-details',
            'grid_sub_orders': 'GET /api/v5/tradingBot/grid/sub-orders',
            'grid_positions': 'GET /api/v5/tradingBot/grid/positions',
            'grid_withdraw_income': 'POST /api/v5/tradingBot/grid/withdraw-income',
            'grid_compute_margin_balance': 'POST /api/v5/tradingBot/grid/compute-margin-balance',
            'grid_margin_balance': 'POST /api/v5/tradingBot/grid/margin-balance',
            'grid_add_investment': 'POST /api/v5/tradingBot/grid/add-investment',
            'grid_get_ai_param': 'GET /api/v5/tradingBot/grid/ai-param',
            'grid_compute_min_investment': 'POST /api/v5/tradingBot/grid/min-investment',
            'grid_rsi_back_testing': 'GET /api/v5/tradingBot/public/rsi-back-testing',
            'grid_max_grid_quantity': 'GET /api/v5/tradingBot/grid/max-grid-quantity',
            # --- Copy Trading ---
            'copytrading_get_current_subpositions': 'GET /api/v5/copytrading/current-subpositions',
            'copytrading_get_subpositions_history': 'GET /api/v5/copytrading/subpositions-history',
            'copytrading_algo_order': 'POST /api/v5/copytrading/algo-order',
            'copytrading_close_subposition': 'POST /api/v5/copytrading/close-subposition',
            'copytrading_get_instruments': 'GET /api/v5/copytrading/instruments',
            'copytrading_set_instruments': 'POST /api/v5/copytrading/set-instruments',
            'copytrading_get_profit_sharing_details': 'GET /api/v5/copytrading/profit-sharing-details',
            'copytrading_get_total_profit_sharing': 'GET /api/v5/copytrading/total-profit-sharing',
            'copytrading_get_unrealized_profit_sharing_details': 'GET /api/v5/copytrading/unrealized-profit-sharing-details',
            'copytrading_get_total_unrealized_profit_sharing': 'GET /api/v5/copytrading/total-unrealized-profit-sharing',
            'copytrading_set_profit_sharing_ratio': 'POST /api/v5/copytrading/set-profit-sharing-ratio',
            'copytrading_get_config': 'GET /api/v5/copytrading/config',
            'copytrading_first_copy_settings': 'POST /api/v5/copytrading/first-copy-settings',
            'copytrading_amend_copy_settings': 'POST /api/v5/copytrading/amend-copy-settings',
            'copytrading_stop_copy_trading': 'POST /api/v5/copytrading/stop-copy-trading',
            'copytrading_get_copy_settings': 'GET /api/v5/copytrading/copy-settings',
            'copytrading_get_batch_leverage_info': 'GET /api/v5/copytrading/batch-leverage-info',
            'copytrading_get_copy_trading_configuration': 'GET /api/v5/copytrading/copy-trading-configuration',
            'copytrading_public_lead_traders': 'GET /api/v5/copytrading/public-lead-traders',
            'copytrading_public_weekly_pnl': 'GET /api/v5/copytrading/public-weekly-pnl',
            'copytrading_public_pnl': 'GET /api/v5/copytrading/public-pnl',
            'copytrading_public_stats': 'GET /api/v5/copytrading/public-stats',
            'copytrading_public_preference_currency': 'GET /api/v5/copytrading/public-preference-currency',
            'copytrading_public_current_subpositions': 'GET /api/v5/copytrading/public-current-subpositions',
            'copytrading_public_subpositions_history': 'GET /api/v5/copytrading/public-subpositions-history',
            'copytrading_public_copy_traders': 'GET /api/v5/copytrading/public-copy-traders',
            # --- RFQ (Request for Quote) / Block Trading ---
            'get_counterparties': 'GET /api/v5/rfq/counterparties',
            'create_rfq': 'POST /api/v5/rfq/create-rfq',
            'cancel_rfq': 'POST /api/v5/rfq/cancel-rfq',
            'cancel_multiple_rfqs': 'POST /api/v5/rfq/cancel-multiple-rfqs',
            'cancel_all_rfqs': 'POST /api/v5/rfq/cancel-all-rfqs',
            'execute_quote': 'POST /api/v5/rfq/execute-quote',
            'get_quote_products': 'GET /api/v5/rfq/quote-products',
            'set_quote_products': 'POST /api/v5/rfq/set-quote-products',
            'rfq_mmp_reset': 'POST /api/v5/rfq/mmp-reset',
            'rfq_mmp_config': 'POST /api/v5/rfq/mmp-config',
            'get_rfq_mmp_config': 'GET /api/v5/rfq/mmp-config',
            'create_quote': 'POST /api/v5/rfq/create-quote',
            'cancel_quote': 'POST /api/v5/rfq/cancel-quote',
            'cancel_multiple_quotes': 'POST /api/v5/rfq/cancel-multiple-quotes',
            'cancel_all_quotes': 'POST /api/v5/rfq/cancel-all-quotes',
            'rfq_cancel_all_after': 'POST /api/v5/rfq/cancel-all-after',
            'get_rfqs': 'GET /api/v5/rfq/rfqs',
            'get_rfq_quotes': 'GET /api/v5/rfq/quotes',
            'get_rfq_trades': 'GET /api/v5/rfq/trades',
            'get_public_rfq_trades': 'GET /api/v5/rfq/public-trades',
            'get_block_tickers': 'GET /api/v5/market/block-tickers',
            'get_block_ticker': 'GET /api/v5/market/block-ticker',
            'get_public_block_trades': 'GET /api/v5/rfq/public-block-trades',
            # --- Market Data ---
            'get_tick': 'GET /api/v5/market/ticker',
            'get_tickers': 'GET /api/v5/market/tickers',
            'get_depth': 'GET /api/v5/market/books',
            'get_depth_full': 'GET /api/v5/market/books-full',
            'get_kline': 'GET /api/v5/market/candles',
            'get_kline_his': 'GET /api/v5/market/history-candles',
            'get_trades': 'GET /api/v5/market/trades',
            'get_trades_history': 'GET /api/v5/market/history-trades',
            'get_option_instrument_family_trades': 'GET /api/v5/market/option/instrument-family-trades',
            'get_option_trades': 'GET /api/v5/public/option-trades',
            'get_24h_volume': 'GET /api/v5/market/platform-24-volume',
            'get_call_auction_details': 'GET /api/v5/market/call-auction-details',
            'get_index_price': 'GET /api/v5/market/index-tickers',
            'get_index_candles': 'GET /api/v5/market/index-candles',
            'get_index_candles_history': 'GET /api/v5/market/history-index-candles',
            'get_mark_price_candles': 'GET /api/v5/market/mark-price-candles',
            'get_mark_price_candles_history': 'GET /api/v5/market/history-mark-price-candles',
            'get_exchange_rate': 'GET /api/v5/market/exchange-rate',
            'get_index_components': 'GET /api/v5/market/index-components',
            # --- Public Data ---
            'get_public_instruments': 'GET /api/v5/public/instruments',
            'get_delivery_exercise_history': 'GET /api/v5/public/delivery-exercise-history',
            'get_estimated_settlement_price': 'GET /api/v5/public/estimated-settlement-price',
            'get_settlement_history': 'GET /api/v5/public/settlement-history',
            'get_funding_rate': 'GET /api/v5/public/funding-rate',
            'get_funding_rate_history': 'GET /api/v5/public/funding-rate-history',
            'get_mark_price': 'GET /api/v5/public/mark-price',
            'get_open_interest': 'GET /api/v5/public/open-interest',
            'get_price_limit': 'GET /api/v5/public/price-limit',
            'get_opt_summary': 'GET /api/v5/public/opt-summary',
            'get_estimated_price': 'GET /api/v5/public/estimated-price',
            'get_discount_rate': 'GET /api/v5/public/discount-rate-interest-free-quota',
            'get_interest_rate_loan_quota': 'GET /api/v5/public/interest-rate-loan-quota',
            'get_system_time': 'GET /api/v5/public/time',
            'get_position_tiers_public': 'GET /api/v5/public/position-tiers',
            'get_underlying': 'GET /api/v5/public/underlying',
            'get_insurance_fund': 'GET /api/v5/public/insurance-fund',
            'convert_contract_coin': 'GET /api/v5/public/convert-contract-coin',
            'get_instrument_tick_bands': 'GET /api/v5/public/instrument-tick-bands',
            'get_premium_history': 'GET /api/v5/public/premium-history',
            'get_economic_calendar': 'GET /api/v5/public/economic-calendar',
            # --- Funding Account ---
            'get_currencies': 'GET /api/v5/asset/currencies',
            'get_asset_balances': 'GET /api/v5/asset/balances',
            'get_non_tradable_assets': 'GET /api/v5/asset/non-tradable-assets',
            'get_asset_valuation': 'GET /api/v5/asset/asset-valuation',
            'transfer': 'POST /api/v5/asset/transfer',
            'get_transfer_state': 'GET /api/v5/asset/transfer-state',
            'get_asset_bills': 'GET /api/v5/asset/bills',
            'get_asset_bills_history': 'GET /api/v5/asset/bills-history',
            'get_deposit_address': 'GET /api/v5/asset/deposit-address',
            'get_deposit_history': 'GET /api/v5/asset/deposit-history',
            'get_deposit_withdraw_status': 'GET /api/v5/asset/deposit-withdraw-status',
            'withdrawal': 'POST /api/v5/asset/withdrawal',
            'cancel_withdrawal': 'POST /api/v5/asset/cancel-withdrawal',
            'get_withdrawal_history': 'GET /api/v5/asset/withdrawal-history',
            'get_exchange_list': 'GET /api/v5/asset/exchange-list',
            'apply_monthly_statement': 'POST /api/v5/asset/monthly-statement',
            'get_monthly_statement': 'GET /api/v5/asset/monthly-statement',
            'get_convert_currencies': 'GET /api/v5/asset/convert/currencies',
            'get_convert_currency_pair': 'GET /api/v5/asset/convert/currency-pair',
            'convert_estimate_quote': 'POST /api/v5/asset/convert/estimate-quote',
            'convert_trade': 'POST /api/v5/asset/convert/trade',
            'get_convert_history': 'GET /api/v5/asset/convert/history',
            'get_deposit_payment_methods': 'GET /api/v5/asset/deposit-payment-methods',
            'get_withdrawal_payment_methods': 'GET /api/v5/asset/withdrawal-payment-methods',
            'create_withdrawal_order': 'POST /api/v5/asset/withdrawal-order',
            'cancel_withdrawal_order': 'POST /api/v5/asset/cancel-withdrawal-order',
            'get_withdrawal_order_history': 'GET /api/v5/asset/withdrawal-order-history',
            'get_withdrawal_order_detail': 'GET /api/v5/asset/withdrawal-order-detail',
            'get_deposit_order_history': 'GET /api/v5/asset/deposit-order-history',
            'get_deposit_order_detail': 'GET /api/v5/asset/deposit-order-detail',
            'get_buy_sell_currencies': 'GET /api/v5/asset/buy-sell/currencies',
            'get_buy_sell_currency_pair': 'GET /api/v5/asset/buy-sell/currency-pair',
            'get_buy_sell_quote': 'GET /api/v5/asset/buy-sell/quote',
            'buy_sell_trade': 'POST /api/v5/asset/buy-sell/trade',
            'get_buy_sell_history': 'GET /api/v5/asset/buy-sell/history',
            # --- Sub-account ---
            'sub_account': 'GET /api/v5/account/subaccount/balances',
            'get_sub_account_list': 'GET /api/v5/users/subaccount/list',
            'create_sub_account': 'POST /api/v5/users/subaccount/create-subaccount',
            'create_sub_account_api_key': 'POST /api/v5/users/subaccount/apikey',
            'get_sub_account_api_key': 'GET /api/v5/users/subaccount/apikey',
            'reset_sub_account_api_key': 'POST /api/v5/users/subaccount/modify-apikey',
            'delete_sub_account_api_key': 'POST /api/v5/users/subaccount/delete-apikey',
            'get_sub_account_funding_balance': 'GET /api/v5/asset/subaccount/balances',
            'get_sub_account_max_withdrawal': 'GET /api/v5/account/subaccount/max-withdrawal',
            'get_sub_account_transfer_history': 'GET /api/v5/asset/subaccount/bills',
            'get_managed_sub_account_bills': 'GET /api/v5/asset/subaccount/managed-subaccount-bills',
            'sub_account_transfer': 'POST /api/v5/asset/subaccount/transfer',
            'set_sub_account_transfer_out': 'POST /api/v5/users/subaccount/set-transfer-out',
            'get_custody_sub_account_list': 'GET /api/v5/users/subaccount/entrust-subaccount-list',
            # --- Trading Statistics ---
            'get_support_coin': 'GET /api/v5/rubik/stat/trading-data/support-coin',
            'get_contract_oi_history': 'GET /api/v5/rubik/stat/contracts/open-interest-history',
            'get_taker_volume': 'GET /api/v5/rubik/stat/taker-volume',
            'get_taker_volume_contract': 'GET /api/v5/rubik/stat/taker-volume-contract',
            'get_margin_loan_ratio': 'GET /api/v5/rubik/stat/margin/loan-ratio',
            'get_long_short_ratio': 'GET /api/v5/rubik/stat/contracts/long-short-account-ratio',
            'get_long_short_ratio_top_trader': 'GET /api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader',
            'get_contract_long_short_ratio': 'GET /api/v5/rubik/stat/contracts/open-interest-volume-ratio',
            'get_option_long_short_ratio': 'GET /api/v5/rubik/stat/option/open-interest-volume-ratio',
            'get_contracts_oi_volume': 'GET /api/v5/rubik/stat/contracts/open-interest-volume',
            'get_option_oi_volume': 'GET /api/v5/rubik/stat/option/open-interest-volume',
            'get_put_call_ratio': 'GET /api/v5/rubik/stat/option/open-interest-volume-ratio',
            'get_option_oi_volume_expiry': 'GET /api/v5/rubik/stat/option/open-interest-volume-expiry',
            'get_option_oi_volume_strike': 'GET /api/v5/rubik/stat/option/open-interest-volume-strike',
            'get_option_taker_flow': 'GET /api/v5/rubik/stat/option/taker-block-volume',
            # --- Status ---
            'get_system_status': 'GET /api/v5/system/status',
            'get_announcements': 'GET /api/v5/support/announcements',
            'get_announcement_types': 'GET /api/v5/support/announcement-types',
            # --- Spread Trading ---
            'sprd_order': 'POST /api/v5/sprd/order',
            'sprd_cancel_order': 'POST /api/v5/sprd/cancel-order',
            'sprd_get_order': 'GET /api/v5/sprd/order',
            'sprd_get_orders_pending': 'GET /api/v5/sprd/orders-pending',
            'sprd_get_orders_history': 'GET /api/v5/sprd/orders-history',
            'sprd_get_trades': 'GET /api/v5/sprd/trades',
        }

        self.wss_paths = {
            'tick': {'args': [{'channel': 'tickers', 'instType': 'SWAP', 'instId': '<symbol>'}], 'op': 'subscribe'},
            'depth': {'args': [{"channel": "books5", "instType": "SWAP", "instId": '<symbol>'}], 'op': 'subscribe'},
            'books': {'args': [{"channel": "books", "instType": "SWAP", "instId": '<symbol>'}], 'op': 'subscribe'},
            "bidAsk": {'args': [{"channel": "bbo-tbt", "instType": "SWAP", "instId": '<symbol>'}], 'op': 'subscribe'},
            'increDepthFlow': {'args': [{"channel": "books50-l2-tbt", "instId": '<symbol>'}], 'op': 'subscribe'},
            'books_l2_tbt': {'args': [{"channel": "books-l2-tbt", "instId": '<symbol>'}], 'op': 'subscribe'},
            'books_sbe_tbt': {'args': [{"channel": "books-sbe-tbt", "instId": '<symbol>'}], 'op': 'subscribe'},
            'trades': {'args': [{"channel": "trades", "instId": '<symbol>'}], 'op': 'subscribe'},
            'trades_all': {'args': [{"channel": "trades-all", "instId": '<symbol>'}], 'op': 'subscribe'},
            'opt_trades': {'args': [{"channel": "opt-trades", "instFamily": '<symbol>'}], 'op': 'subscribe'},
            'call_auction_details': {'args': [{"channel": "call-auction-details", "instId": '<symbol>'}], 'op': 'subscribe'},
            'funding_rate': {'args': [{"channel": "funding-rate", "instId": '<symbol>'}], 'op': 'subscribe'},
            'mark_price': {'args': [{"channel": "mark-price", "instId": '<symbol>'}], 'op': 'subscribe'},
            'open_interest': {'args': [{"channel": "open-interest", "instId": '<symbol>'}], 'op': 'subscribe'},
            'price_limit': {'args': [{"channel": "price-limit", "instId": '<symbol>'}], 'op': 'subscribe'},
            'opt_summary': {'args': [{"channel": "opt-summary", "instType": "OPTION"}], 'op': 'subscribe'},
            'estimated_price': {'args': [{"channel": "estimated-price", "instId": '<symbol>'}], 'op': 'subscribe'},
            'index_tickers': {'args': [{"channel": "index-tickers", "instId": '<symbol>'}], 'op': 'subscribe'},
            'instruments': {'args': [{"channel": "instruments", "instType": "SWAP"}], 'op': 'subscribe'},
            'liquidation_orders': {'args': [{"channel": "liquidation-orders", "instType": "SWAP"}], 'op': 'subscribe'},
            'adl_warning': {'args': [{"channel": "adl-warning", "instType": "SWAP"}], 'op': 'subscribe'},
            'status': {'args': [{"channel": "status"}], 'op': 'subscribe'},
            'orders': {"args": [{"channel": "orders", "instType": "SWAP", "instId": '<symbol>'}], "op": "subscribe"},
            'fills': {"args": [{"channel": "fills", "instType": "SWAP", "instId": '<symbol>'}], "op": "subscribe"},
            'account': {"args": [{"channel": "account", "instType": "SWAP", "instId": '<symbol>'}], "op": "subscribe"},
            'positions': {"args": [{"channel": "positions", "instType": "SWAP", "instId": '<symbol>'}],
                          "op": "subscribe"},
            'balance_position': {"args": [{"channel": "balance_and_position"}], "op": "subscribe"},
            'liquidation_warning': {"args": [{"channel": "liquidation-warning", "instType": "SWAP"}],
                                    "op": "subscribe"},
            'account_greeks': {"args": [{"channel": "account-greeks"}], "op": "subscribe"},
            'deposit_info': {"args": [{"channel": "deposit-info"}], "op": "subscribe"},
            'withdrawal_info': {"args": [{"channel": "withdrawal-info"}], "op": "subscribe"},
            'kline': {'args': [{"channel": "candle<period>", "instId": '<symbol>'}], 'op': 'subscribe'},
            'kline_index': {'args': [{"channel": "index-candle<period>", "instId": '<symbol>'}], 'op': 'subscribe'},
            'kline_mark_price': {'args': [{"channel": "mark-price-candle<period>", "instId": '<symbol>'}], 'op': 'subscribe'},
            'economic_calendar': {'args': [{"channel": "economic-calendar"}], 'op': 'subscribe'},
            # --- Copy Trading WS channels (business) ---
            'lead_trading_notification': {'args': [{"channel": "lead-trading-notification"}], 'op': 'subscribe'},
            # --- Algo WS channels (business) ---
            'algo_orders': {'args': [{"channel": "orders-algo", "instType": "SWAP", "instId": '<symbol>'}],
                            'op': 'subscribe'},
            'grid_orders_spot': {'args': [{"channel": "grid-orders-spot"}], 'op': 'subscribe'},
            'algo_advance': {'args': [{"channel": "algo-advance", "instType": "SWAP", "instId": '<symbol>'}],
                            'op': 'subscribe'},
            'grid_orders_contract': {'args': [{"channel": "grid-orders-contract"}], 'op': 'subscribe'},
            'grid_positions': {'args': [{"channel": "grid-positions", "instType": "SWAP"}], 'op': 'subscribe'},
            'grid_sub_orders': {'args': [{"channel": "grid-sub-orders", "instType": "SWAP"}], 'op': 'subscribe'},
            # --- Spread WS channels ---
            'sprd_orders': {'args': [{"channel": "sprd-orders"}], 'op': 'subscribe'},
            'sprd_tickers': {'args': [{"channel": "sprd-tickers", "sprdId": '<symbol>'}], 'op': 'subscribe'},
            # --- RFQ/Block Trading WS channels ---
            'rfqs': {'args': [{"channel": "rfqs"}], 'op': 'subscribe'},
            'quotes': {'args': [{"channel": "quotes"}], 'op': 'subscribe'},
            'struc_block_trades': {'args': [{"channel": "struc-block-trades"}], 'op': 'subscribe'},
            'public_struc_block_trades': {'args': [{"channel": "public-struc-block-trades"}], 'op': 'subscribe'},
            'public_block_trades': {'args': [{"channel": "public-block-trades"}], 'op': 'subscribe'},
            'block_tickers': {'args': [{"channel": "block-tickers"}], 'op': 'subscribe'},
        }

        self.kline_periods = {
            '1m': '1m',
            '3m': '3m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '1h': '1H',
            '2h': '2H',
            '4h': '4H',
            '6h': '6H',
            '12h': '12H',
            '1d': '1D',
            '1w': '1W',
            '1M': '1M',
            '3M': '3M',
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.status_dict = {
            'live': 'submit',
            'partially_filled': 'partial-filled',
            'filled': 'filled',
            'canceled': 'cancel',
            'mmp_canceled': 'cancel',
        }

    # noinspection PyMethodMayBeStatic
    def get_symbol(self, symbol):
        return symbol.replace('/', '-').upper() + "-SWAP"

    # noinspection PyMethodMayBeStatic
    def get_symbol_re(self, symbol):
        return symbol.replace('-', '/').lower().rsplit("/", 1)[0]

    # noinspection PyMethodMayBeStatic
    def get_period(self, key):
        if key not in self.kline_periods:
            return key
        return self.kline_periods[key]

    def get_rest_path(self, key):
        if key not in self.rest_paths or self.rest_paths[key] == '':
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    # noinspection PyMethodMayBeStatic
    def str2int(self, time_str):
        dt = datetime.datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        timestamp = int((time.mktime(dt.timetuple()) + dt.microsecond / 1000000) * 1000)
        return timestamp

    def get_wss_path(self, **kwargs):
        """拿wss订阅字段
        Returns:
            TYPE: Description
        """
        key = kwargs['topic']
        if key == "mark_price" or key == "positions":
            if 'symbol' in kwargs:
                kwargs['symbol'] = kwargs['symbol']
        else:
            if 'symbol' in kwargs:
                kwargs['symbol'] = self.get_symbol(kwargs['symbol'])
        if 'period' in kwargs:
            kwargs['period'] = self.get_period(kwargs['period'])
        if key not in self.wss_paths or self.wss_paths[key] == '':
            self.raise_path_error(self.exchange_name, key)
        # print("kwargs", kwargs)
        req = copy.deepcopy(self.wss_paths[key])
        for k, v in req["args"][0].items():
            symbol = kwargs.get('symbol', '')
            # print("symbol", symbol, "k = ", k, "v = ", v)
            req["args"][0][k] = req["args"][0][k].replace("<symbol>", symbol)
            if "USDT" in symbol:
                currency = symbol.split("-")[1]  # self.symbol.split("-")[0] + "-" +
            else:
                currency = symbol.split("-")[0]
            req["args"][0][k] = req["args"][0][k].replace("<currency>", currency)
            req["args"][0][k] = req["args"][0][k].replace("<period>", kwargs.get('period', ''))
        req = json.dumps(req)
        # print("req_1", req)
        return req


class OkxExchangeDataSwap(OkxExchangeData):
    """OKX USDT-M Perpetual Swap."""
    pass


class OkxExchangeDataFutures(OkxExchangeData):
    """OKX Futures (expiry-based contracts)."""

    def __init__(self):
        super(OkxExchangeDataFutures, self).__init__()
        self.exchange_name = 'OkxFutures'
        # Override instType in wss_paths for FUTURES
        for key in ['tick', 'depth', 'books', 'bidAsk', 'orders', 'account', 'positions']:
            if key in self.wss_paths:
                for arg in self.wss_paths[key]['args']:
                    if 'instType' in arg:
                        arg['instType'] = 'FUTURES'

    def get_symbol(self, symbol):
        return symbol.replace('/', '-').upper()

    def get_symbol_re(self, symbol):
        return symbol.replace('-', '/').lower()


class OkxExchangeDataSpot(OkxExchangeData):
    """OKX Spot Trading."""

    def __init__(self):
        super(OkxExchangeDataSpot, self).__init__()
        self.exchange_name = 'OkxSpot'
        # Spot overrides: fix get_balance, add spot-specific paths
        self.rest_paths.update({
            'get_instruments': 'GET /api/v5/public/instruments',
            'get_balance': 'GET /api/v5/account/balance',
            'get_kline': 'GET /api/v5/market/history-candles',
            'set_leverage': 'POST /api/v5/account/set-leverage',
            'get_interest_rate': 'GET /api/v5/account/interest-rate',
        })

        self.wss_paths = {
            'tick': {'args': [{"channel": "tickers", "instId": '<symbol>'}], 'op': 'subscribe'},
            'depth': {'args': [{"channel": "books5", "instId": '<symbol>'}], 'op': 'subscribe'},
            'books': {'args': [{"channel": "books", "instId": '<symbol>'}], 'op': 'subscribe'},
            'bidAsk': {'args': [{"channel": "bbo-tbt", "instId": '<symbol>'}], 'op': 'subscribe'},
            'increDepthFlow': {'args': [{"channel": "books50-l2-tbt", "instId": '<symbol>'}], 'op': 'subscribe'},
            'books_l2_tbt': {'args': [{"channel": "books-l2-tbt", "instId": '<symbol>'}], 'op': 'subscribe'},
            'mark_price': {'args': [{"channel": "mark-price", "instId": '<symbol>'}], 'op': 'subscribe'},
            'orders': {"args": [{"channel": "orders", "instType": "MARGIN", "instId": '<symbol>'}], "op": "subscribe"},
            'balance': {"args": [{"channel": "account", "ccy": '<currency>'}], "op": "subscribe"},
            'account': {"args": [{"channel": "account", "instType": "SPOT", "instId": '<symbol>'}], "op": "subscribe"},
            'positions': {"args": [{"channel": "positions", "instType": "SPOT", "instId": '<symbol>'}],
                          "op": "subscribe"},
            'position': {"args": [{"channel": "positions", "instType": "SPOT", "instId": '<symbol>'}],
                         "op": "subscribe"},
            'balance_position': {"args": [{"channel": "balance_and_position"}], "op": "subscribe"},
            'kline': {'args': [{"channel": "candle<period>", "instId": '<symbol>'}], 'op': 'subscribe'},
            'algo_orders': {'args': [{"channel": "orders-algo", "instType": "SPOT", "instId": '<symbol>'}],
                            'op': 'subscribe'},
        }

    def get_symbol(self, symbol):
        return symbol.replace('/', '-').upper()

    # noinspection PyMethodMayBeStatic
    def get_symbol_re(self, symbol):
        return symbol.replace('-', '/').lower()

    def get_wss_path(self, **kwargs):
        """拿wss订阅字段
        Returns:
            TYPE: Description
        """
        key = kwargs['topic']
        if 'symbol' in kwargs:
            kwargs['symbol'] = self.get_symbol(kwargs['symbol'])
        if 'period' in kwargs:
            kwargs['period'] = self.get_period(kwargs['period'])

        if key not in self.wss_paths or self.wss_paths[key] == '':
            self.raise_path_error(self.exchange_name, key)
        req = copy.deepcopy(self.wss_paths[key])
        for k, v in req["args"][0].items():
            symbol = kwargs.get('symbol', '')
            req["args"][0][k] = req["args"][0][k].replace("<symbol>", symbol)
            req["args"][0][k] = req["args"][0][k].replace("<currency>", symbol.split("-")[0])
            req["args"][0][k] = req["args"][0][k].replace("<period>", kwargs.get('period', ''))
        return json.dumps(req)
