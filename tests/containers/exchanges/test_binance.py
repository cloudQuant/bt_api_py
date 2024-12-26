from btpy.containers.exchanges.binance_exchange_data import NormalizedBinanceOrderStatus


def test_binance_order_status():
    assert NormalizedBinanceOrderStatus.submit.value == "NEW"
    assert NormalizedBinanceOrderStatus.filled.value == "FILLED"
