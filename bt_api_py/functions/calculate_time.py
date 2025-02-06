from datetime import datetime, UTC
# import time
import pytz


def get_utc_time():
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def convert_utc_local_datetime(datetime_utc, timezone=pytz.timezone('Asia/Shanghai')):
    """
    Convert a UTC datetime object to a local datetime in the given timezone.
    If no timezone is provided, defaults to 'Asia/Shanghai'.

    :param datetime_utc: A datetime object in UTC.
    :param timezone: A pytz timezone object (optional).
    :return: A datetime object in the local timezone.
    """
    # 将 UTC 时间转换为目标时区的时间
    dtime_local = datetime_utc.astimezone(timezone)

    return dtime_local

def get_string_tz_time(tz='Asia/Singapore', string_format='%Y-%m-%d %H:%M:%S.%f'):
    """generate string timezone datetime in particular timezone
    :param  tz: timezone in pytz.common_timezones
    :param  string_format: string format
    :return: now: timestamp
    """
    tz = pytz.timezone(tz)
    now = datetime.now(tz).strftime(string_format)
    return now


def timestamp2datetime(timestamp, string_format='%Y-%m-%d %H:%M:%S.%f'):
    """change timestamp to datetime object
    :param  timestamp: datetime timestamp
    :param  string_format: string format
    :return: formatted_time (Str): timestamp
    """
    # 将时间戳转换为datetime对象
    dt_object = datetime.fromtimestamp(timestamp)
    # 将datetime对象格式化为字符串形式
    formatted_time = dt_object.strftime(string_format)
    return formatted_time


def datetime2timestamp(datetime_string="2023-06-01 09:30:00.000", string_format='%Y-%m-%d %H:%M:%S.%f'):
    """change the string format datetime to timestamp
    :param  datetime_string: the string format of datetime
    :param  string_format: string format
    :return: timestamp
    """
    time_date = datetime.strptime(datetime_string, string_format)
    timestamp = time_date.timestamp()
    return timestamp


def str2datetime(datetime_string="2023-06-01 09:30:00.0", string_format='%Y-%m-%d %H:%M:%S.%f'):
    """change datetime string to datetime
    :param  datetime_string: the string format of datetime
    :param  string_format: string format
    :return: datetime
    """
    return datetime.strptime(datetime_string, string_format)


def datetime2str(datetime_obj, string_format='%Y-%m-%d %H:%M:%S.%f'):
    """change datetime to string format
    :param  datetime_obj (datetime) timezone in pytz.common_timezones
    :param  string_format (str) string format
    :return:  datetime_str
    """
    return datetime_obj.strftime(string_format)


if __name__ == "__main__":
    print("获取当前的UTC时间:", get_utc_time())
    _timestamp = 1692611135.737
    print(timestamp2datetime(_timestamp))
    print("--------------------------------")
    print("考虑时区的时间", get_string_tz_time())
    print("--------------------------------")
    begin_time = "2023-06-01 10:00:00.2"
    print(f"时间戳 = {datetime2timestamp(begin_time)}")
    print("--------------------------------")
    datetime_obj_ = str2datetime(begin_time)
    print(datetime_obj_)
    datetime_str = datetime2str(datetime_obj_)
    print(datetime_str)
