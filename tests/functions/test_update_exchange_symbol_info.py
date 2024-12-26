import os
from btpy.functions.update_data.update_exchange_symbol_info import *


def test_update_binance_swap_symbol_info():
    update_binance_swap_symbol_info()
    root = get_package_path("btpy")
    full_file_name = root + f"/configs/binance_swap_symbol_info.pkl"
    if not os.path.exists(full_file_name):
        assert False
    with open(full_file_name, "rb") as f:
        data = pickle.load(f)
    assert "BTCUSDT" in data
    value_1 = data["BTCUSDT"]
    value_1.init_data()
    assert value_1.get_symbol_name() == "BTCUSDT"
    assert value_1.get_price_unit() > 0
    assert value_1.get_price_digital() > 0
    assert value_1.get_qty_unit() > 0
    assert value_1.get_qty_digital() > 0


def test_update_binance_spot_symbol_info():
    update_binance_spot_symbol_info()
    root = get_package_path("btpy")
    full_file_name = root + f"/configs/binance_spot_symbol_info.pkl"
    if not os.path.exists(full_file_name):
        assert False
    with open(full_file_name, "rb") as f:
        data = pickle.load(f)
    assert "BTCUSDT" in data
    value_1 = data["BTCUSDT"]
    value_1.init_data()
    assert value_1.get_symbol_name() == "BTCUSDT"
    assert value_1.get_price_unit() > 0
    assert value_1.get_price_digital() > 0
    assert value_1.get_qty_unit() > 0
    assert value_1.get_qty_digital() > 0


if __name__ == "__main__":
    # test_update_binance_swap_symbol_info()
    test_update_binance_spot_symbol_info()
