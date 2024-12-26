import os
import time
import queue
import requests
import pandas as pd
from btpy.feeds.live_binance_feed import BinanceRequestDataSwap
from btpy.containers.exchanges.binance_exchange_data import BinanceExchangeDataSwap
from btpy.functions.utils import read_yaml_file


def download_funding_rate_from_binance():
    data_queue_ = queue.Queue()
    data_ = read_yaml_file("account_config.yaml")
    kwargs_ = {
        "public_key": data_['binance']['public_key'],
        "private_key": data_['binance']['private_key'],
        "exchange_data": BinanceExchangeDataSwap(),
        "topics": {"tick": {"symbol": "BTC-USDT"}}
    }
    live_binance_swap_feed = BinanceRequestDataSwap(data_queue_, **kwargs_)
    res = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    result = res.json()
    symbol_list = [item['symbol'] for item in result['symbols']]
    time_list = ["2019-12-31 00:00:00.000", "2020-03-31 00:00:00.000",
                 "2020-06-30 00:00:00.000", "2020-09-30 00:00:00.000",
                 "2020-12-31 00:00:00.000", "2021-03-31 00:00:00.000",
                 "2021-06-30 00:00:00.000", "2021-09-30 00:00:00.000",
                 "2021-12-31 00:00:00.000", "2022-03-31 00:00:00.000",
                 "2022-06-30 00:00:00.000", "2022-09-30 00:00:00.000",
                 "2022-12-31 00:00:00.000", "2023-03-31 00:00:00.000",
                 "2023-06-30 00:00:00.000", "2023-09-30 00:00:00.000",
                 "2023-12-31 00:00:00.000", "2024-03-31 00:00:00.000",
                 "2024-06-30 00:00:00.000", "2024-09-30 00:00:00.000",
                 "2024-12-31 00:00:00.000"]
    file_list = os.listdir("/Users/yunjinqi/Documents/data/binance_funding_rate_data/")
    # symbol_list = ["GASUSDT", "TOKENUSDT"]
    for symbol in symbol_list:
        file_name = f"funding_rate_{symbol}.csv"
        if file_name in file_list:
            print(f"{symbol} already downloaded")
            continue
        # data = pd.DataFrame(columns=['symbol', 'current_funding_rate', 'funding_rate_time'])
        funding_rate_data_list = []
        for i in range(len(time_list) - 1):
            funding_rate_data = live_binance_swap_feed.get_history_funding_rate(
                symbol, start_time=time_list[i], end_time=time_list[i+1], limit=1000)
            funding_rate_list = funding_rate_data.get_data()
            # print(funding_rate_list)
            result = []
            for item in funding_rate_list:
                item.init_data()
                # print("item", item)
                result.append([item.get_symbol_name(), item.get_current_funding_rate(), item.get_server_time()])
            time.sleep(1)
            df = pd.DataFrame(result, columns=['symbol', 'current_funding_rate', 'funding_rate_time'])
            if len(df) > 0:
                funding_rate_data_list.append(df)
                # print(df.head())
            else:
                print(f"{symbol} cannot get data in {time_list[i]} to {time_list[i+1]}")
        if len(funding_rate_data_list) == 0:
            continue
        data = pd.concat(funding_rate_data_list, axis=0)
        if len(data) == 0:
            print(f"{symbol} cannot get data")
            time.sleep(30)
            continue
        data.to_csv(f"/Users/yunjinqi/Documents/data/binance_funding_rate_data/funding_rate_{symbol}.csv", index=False)
        print(f"{symbol} done")


if __name__ == '__main__':
    download_funding_rate_from_binance()
