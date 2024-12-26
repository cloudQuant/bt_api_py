import pickle
import yaml
import requests
import pandas as pd
from bt_api_py.containers.symbols.binance_symbol import BinanceSwapSymbolData, BinanceSpotSymbolData
from bt_api_py.functions.utils import get_package_path


def update_symbol_info(exchange_name, asset_type):
    if exchange_name == "BINANCE" and asset_type == "SPOT":
        update_binance_spot_symbol_info()
    if exchange_name == "BINANCE" and asset_type == "SWAP":
        update_binance_swap_symbol_info()


def update_okex_symbol_info(instrument_type="SWAP"):
    """
    更新bit的symbol的数据
    :param instrument_type: 品种类型
    :return: 返回okex合约的symbol的数据
    """
    url = "https://www.okx.com/api/v5/public/instruments?instType=" + instrument_type
    page = requests.get(url)
    data = page.json()['data']
    df = pd.DataFrame(data)
    root = get_package_path("lv")
    # print(root)
    df.to_csv(root + f"/configs/okex_{instrument_type}_symbol.csv")


def update_binance_swap_symbol_info():
    res = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    result = res.json()
    data = {}
    for symbol_info in result["symbols"]:
        symbol_name = symbol_info["symbol"]
        data[symbol_name] = BinanceSwapSymbolData(symbol_info, True)
    root = get_package_path("bt_api_py")
    with open(root + f"/configs/binance_swap_symbol_info.pkl", 'wb') as f:
        pickle.dump(data, f)
    print("update binance swap symbol info succeed")
    return data


def update_binance_spot_symbol_info():
    res = requests.get("https://api.binance.com/api/v3/exchangeInfo")
    result = res.json()
    data = {}
    for symbol_info in result["symbols"]:
        symbol_name = symbol_info["symbol"]
        data[symbol_name] = BinanceSpotSymbolData(symbol_info, True)
    root = get_package_path("bt_api_py")
    with open(root + f"/configs/binance_spot_symbol_info.pkl", 'wb') as f:
        pickle.dump(data, f)
    print("update binance swap symbol info succeed")
    return data


if __name__ == "__main__":
    pass
