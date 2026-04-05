from __future__ import annotations

from datetime import datetime, tzinfo

import pytz

from bt_api_py._compat import UTC


def get_utc_time() -> str:
    """Get current UTC time in ISO format.

    Returns:
        Current UTC time string in format "YYYY-MM-DDTHH:MM:SS.ffffffZ".
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def convert_utc_local_datetime(
    datetime_utc: datetime, timezone: tzinfo = pytz.timezone("Asia/Shanghai")
) -> datetime:
    """Convert UTC datetime to local datetime in given timezone.

    Args:
        datetime_utc: A datetime object in UTC.
        timezone: A pytz timezone object. Defaults to 'Asia/Shanghai'.

    Returns:
        A datetime object in the local timezone.
    """
    dtime_local = datetime_utc.astimezone(timezone)
    return dtime_local


def get_string_tz_time(
    tz: str = "Asia/Singapore", string_format: str = "%Y-%m-%d %H:%M:%S.%f"
) -> str:
    """Generate string timezone datetime in particular timezone.

    Args:
        tz: Timezone in pytz.common_timezones.
        string_format: String format for output.

    Returns:
        Formatted timestamp string.
    """
    tz_obj = pytz.timezone(tz)
    now = datetime.now(tz_obj).strftime(string_format)
    return now


def timestamp2datetime(timestamp: float, string_format: str = "%Y-%m-%d %H:%M:%S.%f") -> str:
    """Convert timestamp to formatted datetime string.

    Args:
        timestamp: Datetime timestamp.
        string_format: String format for output.

    Returns:
        Formatted datetime string.
    """
    dt_object = datetime.fromtimestamp(timestamp)
    formatted_time = dt_object.strftime(string_format)
    return formatted_time


def datetime2timestamp(
    datetime_string: str = "2023-06-01 09:30:00.000",
    string_format: str = "%Y-%m-%d %H:%M:%S.%f",
) -> float:
    """Convert string format datetime to timestamp.

    Args:
        datetime_string: The string format of datetime.
        string_format: String format of input.

    Returns:
        Timestamp as float.
    """
    time_date = datetime.strptime(datetime_string, string_format)
    timestamp = time_date.timestamp()
    return timestamp


def str2datetime(
    datetime_string: str = "2023-06-01 09:30:00.0",
    string_format: str = "%Y-%m-%d %H:%M:%S.%f",
) -> datetime:
    """Convert datetime string to datetime object.

    Args:
        datetime_string: The string format of datetime.
        string_format: String format of input.

    Returns:
        Datetime object.
    """
    return datetime.strptime(datetime_string, string_format)


def datetime2str(datetime_obj: datetime, string_format: str = "%Y-%m-%d %H:%M:%S.%f") -> str:
    """Convert datetime to string format.

    Args:
        datetime_obj: Datetime object to convert.
        string_format: String format for output.

    Returns:
        Formatted datetime string.
    """
    return datetime_obj.strftime(string_format)


def _main() -> None:
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


if __name__ == "__main__":
    _main()
