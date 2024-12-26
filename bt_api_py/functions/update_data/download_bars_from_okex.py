import sys
import os
import time
import datetime
import pandas as pd
import requests


def get_okex_instrument_info(instrument_type="SWAP"):
    """
    获取okex的symbol数据
    :return: data
    """
    url = f"https://www.okx.com/api/v5/public/instruments?instType={instrument_type}"
    data = requests.get(url).json()
    return data


def download_okex_bars(symbol, period, begin_time, end_time, dst="okex_btc-usdt.csv"):
    """
    下载okex的bars,并保存到dst文件中
    ：param: symbol, 品种符号
    ：param: period, bar的周期数,如 1m, 1h
    : param: begin_time, 开始时间
    : param: end_time, 结束时间
    ：param: dst, 下载数据的地址
    :return: None
    """
    result = []
    if os.path.exists(dst):
        last_df = pd.read_csv(dst)
        result = [last_df]
        last_end_time = list(last_df['datetime'])[-1]
        begin_time = datetime.datetime.fromisoformat(max(begin_time, last_end_time))
    else:
        begin_time = datetime.datetime.fromisoformat(begin_time)
    stop_time = datetime.datetime.fromisoformat(end_time)
    count = 0
    # begin_time = datetime.datetime.now()
    while True:
        try:
            if period == "1m":
                end_time = begin_time + datetime.timedelta(hours=1)
            elif period == "3m":
                end_time = begin_time + datetime.timedelta(hours=5)
            elif period == "5m":
                end_time = begin_time + datetime.timedelta(hours=9)
            elif period == "15m":
                end_time = begin_time + datetime.timedelta(hours=25)
            elif period == "30m":
                end_time = begin_time + datetime.timedelta(hours=50)
            elif period == "1H":
                end_time = begin_time + datetime.timedelta(hours=100)
            elif period == "1D":
                end_time = begin_time + datetime.timedelta(hours=24*100)
            elif period == "1Dutc":
                end_time = begin_time + datetime.timedelta(hours=24*100)
            begin_stamp = begin_time.timestamp() * 1000
            end_stamp = end_time.timestamp() * 1000
            # print('begin_time',begin_time)
            # print(begin_stamp,end_stamp)
            # print(pd.to_datetime(begin_stamp,unit="ms"))
            # assert 0
            # begin_stamp = "1597026383085"
            # after_stamp = "1597036383085"
            # data = task.getKline_2("BTC-USDT", '1m', after=str(int(end_stamp)), before=str(int(begin_stamp)))
            # data = task.getKline_2("BTC-USDT", '1m')
            url = f"https://www.okex.com/api/v5/market/history-candles?instId={symbol}&bar={period}&after={int(end_stamp)}&before={int(begin_stamp)}"
            data = requests.get(url).json()
            if not data['data']:
                print(f"下载失败: {symbol}, {count}, 开始时间: {begin_time}, 结束时间: {end_time}")
                print(url)
                begin_time = end_time
                if end_time > stop_time:
                    break
                continue
            df = pd.DataFrame(data['data'],
                              columns=['datetime', 'open', 'high', 'low', 'close', 'volume', "volCcy", "volCcyQuote",
                                       "status"])
            df['datetime'] = pd.to_datetime(df["datetime"], unit="ms")
            result.append(df)
            count += 1
            print(f"下载成功: {symbol}, {count}, 开始时间: {begin_time}, 结束时间: {end_time}")
            begin_time = end_time
            time.sleep(0.1)
            if end_time > stop_time:
                break
            if count % 10 == 0:
                data = pd.concat(result, axis=0)
                # data.to_csv("BINANCE.BTCUSDT.HOT_m1_origin.csv", index=False)
                # print(data.head())
                # data[['date','time']] = data['datetime'].str.split(expand=True)
                data = data[['datetime', 'open', 'high', 'low', 'close', 'volume']]
                data = data.drop_duplicates("datetime")
                # data.columns = ['<Date>', '<Time>', '<Open>', '<High>', '<Low>', '<Close>', '<Volume>']
                data.to_csv(dst, index=False)
                print(f"当前数据长度为: {len(data)}")
        except Exception as e:
            time.sleep(3)
            print(e)
    if len(result) == 0:
        return 
    data = pd.concat(result, axis=0)
    # data.to_csv("BINANCE.BTCUSDT.HOT_m1_origin.csv", index=False)
    # print(data.head())
    # data[['date','time']] = data['datetime'].str.split(expand=True)
    data = data[['datetime', 'open', 'high', 'low', 'close', 'volume']]
    # data.columns = ['<Date>', '<Time>', '<Open>', '<High>', '<Low>', '<Close>', '<Volume>']
    data = data.drop_duplicates("datetime")
    data.to_csv(dst, index=False)


def download_all_bars(period="1H", begin_time="2020-01-01 00:00:00", end_time="2023-10-31 00:00:00"):
    """
    下载okex的所有bar
    ：param: period, bar的周期数,如 1m, 1h
    : param: begin_time, 开始时间
    : param: end_time, 结束时间
    :return: None
    """
    instrument_info_list = get_okex_instrument_info()
    symbol_list = [i["instId"] for i in instrument_info_list['data']]
    for symbol in symbol_list:
        if not os.path.exists(f"./datas/{period}/"):
            os.mkdir(f"./datas/{period}/")
        dst = f"./datas/{period}/" + symbol + ".csv"
        download_okex_bars(symbol, period, begin_time, end_time, dst)


if __name__ == "__main__":
    download_all_bars(period="1D", begin_time="2020-01-01 00:00:00", end_time="2023-10-31 00:00:00")