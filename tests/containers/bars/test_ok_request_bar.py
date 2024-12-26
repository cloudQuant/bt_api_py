import requests
import time
import json
import rapidjson
from btpy.containers.bars.okx_bar import OkxBarData


def test_get_history_bar():
    url = ("https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP&bar=1m&after=1696089720000&before"
           "=1696089600000")
    # url = "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP"
    begin_time = time.perf_counter()
    r = requests.get(url)
    end_time = time.perf_counter()
    print(f"request.get(url) consume time = {end_time - begin_time}")
    assert r.status_code == 200
    assert isinstance(r.text, str)
    get_text_time = 0
    for i in range(1000):
        begin_time = time.perf_counter()
        _t = r.text
        end_time = time.perf_counter()
        get_text_time += (end_time - begin_time)
    print(f"r.text = {r.text}")
    get_json_time = 0
    for i in range(1000):
        begin_time = time.perf_counter()
        _j = r.json()
        # _j = rapidjson.loads(r.text)
        end_time = time.perf_counter()
        get_json_time += (end_time - begin_time)

    get_rapidjson_time = 0
    for i in range(1000):
        begin_time = time.perf_counter()
        # _j = r.json()
        _j = rapidjson.loads(r.text)
        end_time = time.perf_counter()
        get_rapidjson_time += (end_time - begin_time)

    print(f"r.json = {r.json()}")
    print(f"rapidjson: {get_rapidjson_time / 1000} ms, json: {get_json_time / 1000}ms, "
          f"get_text_time: {get_text_time / 1000}ms")
    assert get_rapidjson_time > get_text_time
    # print(f"get_json_time: {get_json_time} ms, get_text_time: {get_text_time}")


def test_ok_bar_functions():
    # {"code":"0","msg":"","data":[["1696089660000","26990.4","27004.5","26990.3","27004.5","4794","47.94","1294336.087","1"]]}
    url = ("https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP&bar=1m&after=1696089720000&before"
           "=1696089600000")
    # url = "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP"
    r = requests.get(url)
    okx_bar_data = OkxBarData(json.loads(r.text)['data'][0], "BTC-USDT", "SWAP", True)
    okx_bar_data.init_data()
    assert okx_bar_data.get_bar_status() == 1
    assert okx_bar_data.get_quote_asset_volume() == 1294336.087
    assert okx_bar_data.get_amount() is None
    assert okx_bar_data.get_volume() == 4794
    assert okx_bar_data.get_low_price() == 26990.3
    assert okx_bar_data.get_close_price() == 27004.5
    assert okx_bar_data.get_open_price() == 26990.4
    assert okx_bar_data.get_high_price() == 27004.5
    assert okx_bar_data.get_open_time() is None
    assert okx_bar_data.get_close_time() is None
    assert okx_bar_data.get_asset_type() == "SWAP"
    assert okx_bar_data.get_symbol_name() == "BTC-USDT"
    assert okx_bar_data.get_exchange_name() == "OKX"
    assert okx_bar_data.get_base_asset_volume() == 47.94
    assert okx_bar_data.get_event_type() == "BarEvent"


if __name__ == "__main__":
    test_get_history_bar()
    test_ok_bar_functions()
