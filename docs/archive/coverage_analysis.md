# Test Coverage Analysis Report
==================================================

## Exchange Coverage
Total exchanges: 68
Tested exchanges: 68 (100.0%)
Untested exchanges: 0

## Module Test Distribution
feeds: 34 test files
containers: 285 test files
registry: 2 test files
event_bus: 1 test files
exceptions: 1 test files

## Overall Coverage: 51.6%

## Files with Low Coverage (< 60%)
- bt_api_py/bt_api.py: 57.1%
- bt_api_py/containers/exchanges/balancer_exchange_data.py: 44.4%
- bt_api_py/containers/exchanges/bequant_exchange_data.py: 42.6%
- bt_api_py/containers/exchanges/bigone_exchange_data.py: 22.6%
- bt_api_py/containers/exchanges/bybit_exchange_data.py: 28.7%
- bt_api_py/containers/exchanges/curve_exchange_data.py: 47.9%
- bt_api_py/containers/exchanges/dydx_exchange_data.py: 44.3%
- bt_api_py/containers/exchanges/htx_exchange_data.py: 32.1%
- bt_api_py/containers/exchanges/mexc_exchange_data.py: 48.2%
- bt_api_py/containers/exchanges/okx_exchange_data.py: 44.7%
- bt_api_py/containers/exchanges/pancakeswap_exchange_data.py: 12.5%
- bt_api_py/containers/exchanges/sushiswap_exchange_data.py: 37.2%
- bt_api_py/containers/exchanges/uniswap_exchange_data.py: 31.4%
- bt_api_py/containers/exchanges/uniswap_pool.py: 38.9%
- bt_api_py/containers/exchanges/uniswap_quote.py: 47.8%
- bt_api_py/containers/exchanges/uniswap_ticker.py: 48.2%
- bt_api_py/containers/fundingrates/binance_funding_rate.py: 59.6%
- bt_api_py/containers/orderbooks/bitget_orderbook.py: 31.8%
- bt_api_py/containers/orderbooks/coinbase_orderbook.py: 35.3%
- bt_api_py/containers/orderbooks/gateio_orderbook.py: 48.7%
- bt_api_py/containers/orderbooks/kraken_orderbook.py: 35.4%
- bt_api_py/containers/orderbooks/mexc_orderbook.py: 35.2%
- bt_api_py/containers/orders/bitget_order.py: 49.8%
- bt_api_py/containers/orders/coinbase_order.py: 44.1%
- bt_api_py/containers/orders/gateio_order.py: 58.5%
- bt_api_py/containers/orders/hyperliquid_order.py: 50.5%
- bt_api_py/containers/orders/kraken_order.py: 37.7%
- bt_api_py/containers/orders/mexc_order.py: 38.4%
- bt_api_py/containers/tickers/bitbns_ticker.py: 43.7%
- bt_api_py/containers/tickers/bitflyer_ticker.py: 52.3%
- bt_api_py/containers/tickers/bitget_ticker.py: 52.9%
- bt_api_py/containers/tickers/coinbase_ticker.py: 46.0%
- bt_api_py/containers/tickers/dydx_ticker.py: 40.0%
- bt_api_py/containers/tickers/mexc_ticker.py: 42.2%
- bt_api_py/containers/tickers/pancakeswap_ticker.py: 52.9%
- bt_api_py/containers/tickers/raydium_ticker.py: 59.6%
- bt_api_py/containers/trades/bitget_trade.py: 53.3%
- bt_api_py/containers/trades/coinbase_trade.py: 45.5%
- bt_api_py/containers/trades/gateio_trade.py: 53.8%
- bt_api_py/containers/trades/mexc_trade.py: 52.5%
- bt_api_py/ctp/_ctp_base.py: 17.6%
- bt_api_py/ctp/client.py: 44.7%
- bt_api_py/ctp/ctp_md_api.py: 55.3%
- bt_api_py/ctp/ctp_trader_api.py: 52.3%
- bt_api_py/errors/bybit_translator.py: 23.8%
- bt_api_py/errors/error_framework_htx.py: 13.7%
- bt_api_py/errors/error_framework_pancakeswap_error_translator.py: 6.8%
- bt_api_py/errors/ib_web_translator.py: 26.3%
- bt_api_py/errors/kraken_translator.py: 12.5%
- bt_api_py/exchange_registers/register_balancer.py: 45.0%
- bt_api_py/exchange_registers/register_binance.py: 36.4%
- bt_api_py/exchange_registers/register_bitget.py: 31.7%
- bt_api_py/exchange_registers/register_ctp.py: 41.2%
- bt_api_py/exchange_registers/register_dydx.py: 58.6%
- bt_api_py/exchange_registers/register_htx.py: 32.3%
- bt_api_py/exchange_registers/register_hyperliquid.py: 37.8%
- bt_api_py/exchange_registers/register_ib.py: 10.0%
- bt_api_py/exchange_registers/register_ib_web.py: 41.5%
- bt_api_py/exchange_registers/register_okx.py: 32.7%
- bt_api_py/exchange_registers/register_pancakeswap.py: 54.5%
- bt_api_py/exchange_registers/register_uniswap.py: 32.3%
- bt_api_py/feeds/live_balancer/request_base.py: 20.4%
- bt_api_py/feeds/live_balancer/spot.py: 23.3%
- bt_api_py/feeds/live_bequant/request_base.py: 24.7%
- bt_api_py/feeds/live_bequant/spot.py: 38.5%
- bt_api_py/feeds/live_bigone/request_base.py: 15.9%
- bt_api_py/feeds/live_bigone/spot.py: 39.0%
- bt_api_py/feeds/live_binance/account_wss_base.py: 17.5%
- bt_api_py/feeds/live_binance/algo.py: 50.0%
- bt_api_py/feeds/live_binance/coin_m.py: 52.2%
- bt_api_py/feeds/live_binance/grid.py: 15.7%
- bt_api_py/feeds/live_binance/margin.py: 19.6%
- bt_api_py/feeds/live_binance/market_wss_base.py: 11.6%
- bt_api_py/feeds/live_binance/mining.py: 28.9%
- bt_api_py/feeds/live_binance/option.py: 52.2%
- bt_api_py/feeds/live_binance/portfolio.py: 26.5%
- bt_api_py/feeds/live_binance/request_base.py: 15.1%
- bt_api_py/feeds/live_binance/spot.py: 14.1%
- bt_api_py/feeds/live_binance/staking.py: 13.6%
- bt_api_py/feeds/live_binance/sub_account.py: 18.6%
- bt_api_py/feeds/live_binance/vip_loan.py: 12.4%
- bt_api_py/feeds/live_binance/wallet.py: 13.6%
- bt_api_py/feeds/live_bingx/spot.py: 31.8%
- bt_api_py/feeds/live_bitbank/spot.py: 27.7%
- bt_api_py/feeds/live_bitbns/request_base.py: 48.1%
- bt_api_py/feeds/live_bitbns/spot.py: 18.3%
- bt_api_py/feeds/live_bitflyer/request_base.py: 56.6%
- bt_api_py/feeds/live_bitflyer/spot.py: 25.5%
- bt_api_py/feeds/live_bitget/request_base.py: 35.8%
- bt_api_py/feeds/live_bitget/spot.py: 21.3%
- bt_api_py/feeds/live_bitget/swap.py: 20.0%
- bt_api_py/feeds/live_bybit/request_base.py: 31.4%
- bt_api_py/feeds/live_bybit/spot.py: 22.3%
- bt_api_py/feeds/live_bybit/swap.py: 21.5%
- bt_api_py/feeds/live_coinbase/request_base.py: 48.5%
- bt_api_py/feeds/live_coinbase/spot.py: 21.6%
- bt_api_py/feeds/live_cow_swap/request_base.py: 41.8%
- bt_api_py/feeds/live_cow_swap/spot.py: 24.9%
- bt_api_py/feeds/live_ctp_feed.py: 48.0%
- bt_api_py/feeds/live_curve/request_base.py: 43.9%
- bt_api_py/feeds/live_curve/spot.py: 26.5%
- bt_api_py/feeds/live_dydx/request_base.py: 24.4%
- bt_api_py/feeds/live_dydx/spot.py: 15.0%
- bt_api_py/feeds/live_gateio/request_base.py: 37.1%
- bt_api_py/feeds/live_gateio/spot.py: 19.8%
- bt_api_py/feeds/live_gateio/swap.py: 19.8%
- bt_api_py/feeds/live_htx/coin_swap.py: 47.8%
- bt_api_py/feeds/live_htx/margin.py: 50.0%
- bt_api_py/feeds/live_htx/option.py: 47.8%
- bt_api_py/feeds/live_htx/request_base.py: 42.6%
- bt_api_py/feeds/live_htx/spot.py: 17.2%
- bt_api_py/feeds/live_htx/usdt_swap.py: 23.0%
- bt_api_py/feeds/live_hyperliquid/account_wss_base.py: 30.0%
- bt_api_py/feeds/live_hyperliquid/market_wss_base.py: 32.2%
- bt_api_py/feeds/live_hyperliquid/request_base.py: 21.9%
- bt_api_py/feeds/live_hyperliquid/spot.py: 16.0%
- bt_api_py/feeds/live_hyperliquid_feed.py: 0.0%
- bt_api_py/feeds/live_ib_web_feed.py: 38.5%
- bt_api_py/feeds/live_ib_web_stream.py: 10.8%
- bt_api_py/feeds/live_kraken/futures.py: 26.3%
- bt_api_py/feeds/live_kraken/request_base.py: 25.0%
- bt_api_py/feeds/live_kraken/spot.py: 17.2%
- bt_api_py/feeds/live_mexc/account_wss_base.py: 0.0%
- bt_api_py/feeds/live_mexc/market_wss_base.py: 0.0%
- bt_api_py/feeds/live_mexc/request_base.py: 24.2%
- bt_api_py/feeds/live_mexc/spot.py: 20.1%
- bt_api_py/feeds/live_mexc_feed.py: 0.0%
- bt_api_py/feeds/live_okx/account_wss_base.py: 57.1%
- bt_api_py/feeds/live_okx/futures.py: 53.3%
- bt_api_py/feeds/live_okx/market_wss_base.py: 8.0%
- bt_api_py/feeds/live_okx/mixins/account_mixin.py: 16.3%
- bt_api_py/feeds/live_okx/mixins/copy_trading_mixin.py: 15.7%
- bt_api_py/feeds/live_okx/mixins/funding_mixin.py: 13.2%
- bt_api_py/feeds/live_okx/mixins/grid_trading_mixin.py: 11.8%
- bt_api_py/feeds/live_okx/mixins/market_data_mixin.py: 14.3%
- bt_api_py/feeds/live_okx/mixins/normalizers.py: 15.4%
- bt_api_py/feeds/live_okx/mixins/rfq_mixin.py: 16.3%
- bt_api_py/feeds/live_okx/mixins/spread_trading_mixin.py: 14.0%
- bt_api_py/feeds/live_okx/mixins/statistics_mixin.py: 10.9%
- bt_api_py/feeds/live_okx/mixins/status_mixin.py: 23.6%
- bt_api_py/feeds/live_okx/mixins/sub_account_mixin.py: 16.5%
- bt_api_py/feeds/live_okx/mixins/trade_mixin.py: 11.7%
- bt_api_py/feeds/live_okx/mixins/trading_account_mixin.py: 13.4%
- bt_api_py/feeds/live_okx/spot.py: 36.8%
- bt_api_py/feeds/live_pancakeswap/request_base.py: 25.4%
- bt_api_py/feeds/live_pancakeswap/spot.py: 18.9%
- bt_api_py/feeds/live_raydium/request_base.py: 27.8%
- bt_api_py/feeds/live_raydium/spot.py: 25.7%
- bt_api_py/feeds/live_sushiswap/request_base.py: 21.8%
- bt_api_py/feeds/live_sushiswap/spot.py: 24.1%
- bt_api_py/feeds/live_uniswap/request_base.py: 21.1%
- bt_api_py/feeds/live_uniswap/spot.py: 23.9%
- bt_api_py/feeds/my_websocket_app.py: 15.1%
- bt_api_py/functions/analysis_deals.py: 5.8%
- bt_api_py/functions/analysis_log.py: 45.0%
- bt_api_py/functions/async_base.py: 44.1%
- bt_api_py/functions/async_send_message.py: 37.6%
- bt_api_py/functions/browser_cookies.py: 32.3%
- bt_api_py/functions/calculate_numbers.py: 46.0%
- bt_api_py/functions/ib_web_session.py: 22.4%
- bt_api_py/functions/update_data/download_bars_from_okex.py: 13.4%
- bt_api_py/functions/update_data/download_funding_rate_from_binance.py: 19.8%
- bt_api_py/functions/update_data/download_spot_history_bar_from_binance.py: 25.4%
- bt_api_py/functions/update_data/download_spot_history_bar_from_okx.py: 25.4%
- bt_api_py/functions/update_data/download_swap_history_bar_from_binance.py: 25.0%
- bt_api_py/gateway/__init__.py: 5.6%
- bt_api_py/gateway/adapters/binance_adapter.py: 41.4%
- bt_api_py/gateway/adapters/ctp_adapter.py: 22.6%
- bt_api_py/gateway/adapters/ib_web_adapter.py: 35.3%
- bt_api_py/gateway/adapters/okx_adapter.py: 39.0%
- bt_api_py/monitoring/collector.py: 43.5%
- bt_api_py/monitoring/decorators.py: 57.2%
- bt_api_py/monitoring/elk.py: 10.0%
- bt_api_py/monitoring/exchange_health.py: 53.6%
- bt_api_py/monitoring/grafana.py: 33.8%
- bt_api_py/monitoring/prometheus.py: 20.1%
- bt_api_py/risk_management/core/limits_manager.py: 39.2%
- bt_api_py/risk_management/ml_models/anomaly_detector.py: 44.7%
- bt_api_py/security_compliance/auth/mfa_provider.py: 43.6%
- bt_api_py/security_compliance/core/access_control.py: 58.8%
- bt_api_py/websocket/__init__.py: 43.0%

## Critical Paths Analysis
The following critical paths need more comprehensive testing:
- Exchange connection/error handling
- WebSocket stream reconnection logic
- Rate limiting implementation
- Data normalization across exchanges