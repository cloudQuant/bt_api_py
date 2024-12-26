from btpy.containers.bars.binance_bar import BinanceWssBarData, BinanceRequestBarData


def test_binance_wss_bar_functions():
    # {"code":"0","msg":"","data":[["1696089660000","26990.4","27004.5","26990.3","27004.5","4794","47.94","1294336.087","1"]]}
    data = {'e': 'continuous_kline', 'E': 1607443058651, 'ps': 'BTCUSDT', 'ct': 'PERPETUAL', 'k': {'t': 1607443020000,
                                                                                                   'T': 1607443079999,
                                                                                                   'i': '1m',
                                                                                                   'f': 116467658886,
                                                                                                   'L': 116468012423,
                                                                                                   'o': '18787.00',
                                                                                                   'c': '18804.04',
                                                                                                   'h': '18804.04',
                                                                                                   'l': '18786.54',
                                                                                                   'v': '197.664',
                                                                                                   'n': 543,
                                                                                                   'x': 'false',
                                                                                                   'q': '3715253.19494',
                                                                                                   'V': '184.769',
                                                                                                   'Q': '3472925.84746',
                                                                                                   'B': '0'}}
    binance_bar_data = BinanceWssBarData(data, data['ps'], data['ct'], True)
    binance_bar_data.init_data()
    assert binance_bar_data.get_bar_status() == 0
    assert binance_bar_data.get_amount() == 3715253.19494
    assert binance_bar_data.get_volume() == 197.664
    assert binance_bar_data.get_low_price() == 18786.54
    assert binance_bar_data.get_close_price() == 18804.04
    assert binance_bar_data.get_open_price() == 18787.00
    assert binance_bar_data.get_high_price() == 18804.04
    assert binance_bar_data.get_open_time() == 1607443020000.0
    assert binance_bar_data.get_close_time() == 1607443079999.0
    assert binance_bar_data.get_asset_type() == "PERPETUAL"
    assert binance_bar_data.get_symbol_name() == "BTCUSDT"
    assert binance_bar_data.get_exchange_name() == "BINANCE"
    assert binance_bar_data.get_taker_buy_base_asset_volume() == 184.769


def test_binance_req_bar_functions():
    # {"code":"0","msg":"","data":[["1696089660000","26990.4","27004.5","26990.3","27004.5","4794","47.94","1294336.087","1"]]}
    data = [
        1607444700000,
        "18879.99",
        "18900.00",
        "18878.98",
        "18896.13",
        "492.363",
        1607444759999,
        "9302145.66080",
        1874,
        "385.983",
        "7292402.33267",
        "0"
    ]
    symbol = "BTCUSDT"
    asset_type = "PERPETUAL"
    binance_bar_data = BinanceRequestBarData(data, symbol, asset_type, True)
    binance_bar_data.init_data()
    assert binance_bar_data.get_bar_status() is None
    assert binance_bar_data.get_amount() == 9302145.66080
    assert binance_bar_data.get_volume() == 492.363
    assert binance_bar_data.get_low_price() == 18878.98
    assert binance_bar_data.get_close_price() == 18896.13
    assert binance_bar_data.get_open_price() == 18879.99
    assert binance_bar_data.get_high_price() == 18900.00
    assert binance_bar_data.get_open_time() == 1607444700000.0
    assert binance_bar_data.get_close_time() == 1607444759999.0
    assert binance_bar_data.get_asset_type() == "PERPETUAL"
    assert binance_bar_data.get_symbol_name() == "BTCUSDT"
    assert binance_bar_data.get_exchange_name() == "BINANCE"
    assert binance_bar_data.get_taker_buy_base_asset_volume() == 385.983
    assert binance_bar_data.get_event_type() == "BarEvent"


if __name__ == "__main__":
    test_binance_wss_bar_functions()
    test_binance_req_bar_functions()
