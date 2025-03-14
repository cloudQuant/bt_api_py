import os
import time
import queue
import requests
import random
import pandas as pd
from backtrader.stores.cryptostore import CryptoStore
from bt_api_py.feeds.live_binance_feed import BinanceRequestDataSwap
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSwap
from bt_api_py.functions.utils import read_yaml_file



def get_spot_symbol_list():
    res = requests.get("https://api.binance.com/api/v3/exchangeInfo")
    result = res.json()
    result = result['symbols']
    # swap_symbol_list = [i['symbol'] for i in result if i['contractType']=='PERPETUAL']
    swap_symbol_list = [i['symbol'] for i in result]
    return swap_symbol_list

def download_spot_history_bar_from_binance():
    account_config_data = read_yaml_file('account_config.yaml')
    exchange_params = {
        "OKX___SPOT": {
            "public_key": account_config_data['okx']['public_key'],
            "private_key": account_config_data['okx']['private_key'],
            "passphrase": account_config_data['okx']["passphrase"],
        },
        "BINANCE___SPOT": {
            "public_key": account_config_data['binance']['public_key'],
            "private_key": account_config_data['binance']['private_key']
        }
    }
    crypto_store = CryptoStore(exchange_params, debug=True)
    symbol_list = get_spot_symbol_list()
    random.shuffle(symbol_list)
    # 如果不存在这个文件夹，创建一个
    if not os.path.exists("./binance_spot_history_bar_data/"):
        os.mkdir("binance_spot_history_bar_data/")
    file_list = os.listdir("./binance_spot_history_bar_data/")
    # symbol_list = ["GASUSDT", "TOKENUSDT"]
    for symbol in symbol_list:
        file_name = f"spot_history_bar_{symbol}.csv"
        if file_name in file_list:
            print(f"{symbol} already downloaded")
            continue
        # data = pd.DataFrame(columns=['symbol', 'current_funding_rate', 'funding_rate_time'])
        bar_data_list = crypto_store.download_history_bars("BINANCE___SPOT___"+symbol,
                                                           "15m",
                                                           1500,
                                                           "2019-12-31 00:00:00",
                                                           "2025-03-08 00:00:00")
        if len(bar_data_list) == 0:
            continue
        bar_data_list = [i.get_all_data() for i in bar_data_list]
        data = pd.DataFrame(bar_data_list)
        if len(data) == 0:
            print(f"{symbol} cannot get data")
            time.sleep(30)
            continue
        data.to_csv(f"binance_spot_history_bar_data/spot_history_bar_{symbol}.csv", index=False)
        print(f"{symbol} done")
        time.sleep(30)


if __name__ == '__main__':
    # get_swap_symbol_list()
    download_spot_history_bar_from_binance()
    # while True:
    #     try:
    #         download_funding_rate_from_binance()
    #     except Exception as e:
    #         print(e)
    #         time.sleep(6)