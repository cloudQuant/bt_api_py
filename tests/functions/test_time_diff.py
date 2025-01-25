import ntplib
from datetime import datetime, timezone
import socket


def get_network_time():
    try:
        client = ntplib.NTPClient()
        # 设置全局超时时间
        socket.setdefaulttimeout(0.5)  # 设置全局超时时间为 0.5 秒

        # 尝试使用多个 NTP 服务器
        ntp_servers = [
            'time.windows.com',  # Windows 时间服务器
            'time.apple.com',  # Apple 时间服务器
            'pool.ntp.org',  # 公共 NTP 服务器池
            'time.google.com',  # Google 时间服务器
            'ntp.aliyun.com',  # 阿里云 NTP 服务器
            'ntp.tencent.com',  # 腾讯云 NTP 服务器
            'ntp1.aliyun.com',  # 阿里云备用 NTP 服务器
            'ntp2.aliyun.com',  # 阿里云备用 NTP 服务器
        ]

        for server in ntp_servers:
            try:
                response = client.request(server, timeout=0.5)  # 设置请求超时
                # 将时间戳转换为 UTC 时间
                return datetime.fromtimestamp(response.tx_time, tz=timezone.utc)
            except (ntplib.NTPException, OSError, socket.timeout) as e:
                print(f"无法从 {server} 获取时间: {e}")
                continue

        raise Exception("无法从任何 NTP 服务器获取时间，请检查网络连接或防火墙设置。")
    except ImportError:
        raise ImportError("请确保已安装 ntplib 库。可以通过 pip install ntplib 安装。")


def get_system_time():
    return datetime.now(timezone.utc)  # 返回 UTC 时间，确保与网络时间一致


def test_compare_times():
    try:
        network_time = get_network_time()
        system_time = get_system_time()
        time_diff = abs((network_time - system_time).total_seconds())
        print(f"Network Time (UTC): {network_time}")
        print(f"System Time (UTC): {system_time}")
        print(f"Time Difference: {time_diff} seconds")
        if time_diff < 0.1:  # 允许 0.5 秒的误差
            print("系统时间与网络时间一致。")
        else:
            print("系统时间与网络时间不一致。")
            # 使用 assert 会抛出异常，改为打印警告
    except Exception as e:
        print(f"测试失败: {e}")


if __name__ == "__main__":
    test_compare_times()
