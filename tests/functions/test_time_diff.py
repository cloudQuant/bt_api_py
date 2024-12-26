import ntplib
from datetime import datetime


def get_network_time():
    client = ntplib.NTPClient()
    response = client.request('time.apple.com')
    return datetime.fromtimestamp(response.tx_time)


def get_system_time():
    return datetime.now()


def test_compare_times():
    network_time = get_network_time()
    system_time = get_system_time()
    time_diff = abs((network_time - system_time).total_seconds())
    print(f"Network Time: {network_time}")
    print(f"System Time: {system_time}")
    print(f"Time Difference: {time_diff} seconds")
    if time_diff < 0.5:  # 允许 1 秒的误差
        print("系统时间与网络时间一致。")
    else:
        assert False, "系统时间与网络时间不一致。"


if __name__ == "__main__":
    test_compare_times()
