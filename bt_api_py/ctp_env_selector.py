"""
CTP SimNow 环境自动选择器

根据当前时间自动选择第一套或第二套 SimNow 环境：
- 第一套：交易时段（与生产一致），支持3组前置地址
- 第二套：7x24 环境，交易日 16:00~次日09:00，非交易日 16:00~次日12:00

使用方式：
    from bt_api_py.ctp_env_selector import get_ctp_fronts
    td_front, md_front, env_name = get_ctp_fronts()
"""

from __future__ import annotations

import os
from datetime import datetime, time

# ── 交易时段定义 (中国标准时间 UTC+8) ──────────────────────────
# 期货交易时段：
#   日盘: 09:00-11:30, 13:30-15:00
#   夜盘: 21:00-次日02:30 (部分品种到 01:00 或 23:00)
# 第一套环境与生产一致，覆盖这些时段

_TRADING_SESSIONS = [
    (time(9, 0), time(11, 30)),
    (time(13, 30), time(15, 0)),
    (time(21, 0), time(23, 59, 59)),
]

# 夜盘跨日部分 00:00 ~ 02:30
_NIGHT_SESSION_AFTER_MIDNIGHT = (time(0, 0), time(2, 30))

# 第二套环境服务时段：
#   交易日: 16:00 ~ 次日 09:00
#   非交易日: 16:00 ~ 次日 12:00
# 简化判断: 非交易时段都可用第二套


def _is_weekday(dt: datetime) -> bool:
    """判断是否为工作日 (周一~周五)"""
    return dt.weekday() < 5


def _in_trading_session(now: datetime) -> bool:
    """判断当前时间是否在第一套环境可用的交易时段内"""
    t = now.time()

    # 夜盘跨日 00:00~02:30 — 属于前一天的夜盘
    if _NIGHT_SESSION_AFTER_MIDNIGHT[0] <= t <= _NIGHT_SESSION_AFTER_MIDNIGHT[1]:
        return True

    # 检查日盘 + 夜盘 21:00~23:59
    return any(start <= t <= end for start, end in _TRADING_SESSIONS)


def _is_set1_available(now: datetime) -> bool:
    """判断第一套环境是否可用（需要在交易日的交易时段内）"""
    t = now.time()

    # 夜盘跨日 00:00~02:30: 需要看前一天是否是工作日
    if _NIGHT_SESSION_AFTER_MIDNIGHT[0] <= t <= _NIGHT_SESSION_AFTER_MIDNIGHT[1]:
        # 周六凌晨 (weekday=5) 仍属于周五夜盘
        prev_day_weekday = (now.weekday() - 1) % 7
        return prev_day_weekday < 5  # 前一天是工作日

    # 日盘 + 夜盘 21:00 起
    if not _is_weekday(now):
        return False

    return _in_trading_session(now)


def get_ctp_fronts(
    env: str = "",
    now: datetime | None = None,
) -> tuple[str, str, str]:
    """
    获取 CTP 前置地址。

    Parameters
    ----------
    env : str
        环境选择: "auto" / "set1" / "set2"。
        为空时从 CTP_ENV 环境变量读取，默认 "auto"。
    now : datetime, optional
        当前时间，默认 datetime.now()。供测试用。

    Returns
    -------
    (td_front, md_front, env_name) : tuple[str, str, str]
        交易前置、行情前置、环境名称("set1_groupN" 或 "set2_7x24")
    """
    if now is None:
        now = datetime.now()

    if not env:
        env = os.environ.get("CTP_ENV", "auto").strip().lower()

    if env == "set1":
        return _get_set1_fronts()
    elif env == "set2":
        return _get_set2_fronts()
    else:
        # auto 模式
        if _is_set1_available(now):
            return _get_set1_fronts()
        else:
            return _get_set2_fronts()


def _get_set1_fronts() -> tuple[str, str, str]:
    """获取第一套环境前置地址（根据 CTP_SET1_GROUP 选组）"""
    group = os.environ.get("CTP_SET1_GROUP", "1").strip()
    td = os.environ.get(f"CTP_SET1_TD_FRONT_{group}", "tcp://182.254.243.31:30001")
    md = os.environ.get(f"CTP_SET1_MD_FRONT_{group}", "tcp://182.254.243.31:30011")
    # 同步写入兼容变量
    os.environ["CTP_TD_FRONT"] = td
    os.environ["CTP_MD_FRONT"] = md
    return td, md, f"set1_group{group}"


def _get_set2_fronts() -> tuple[str, str, str]:
    """获取第二套 7x24 环境前置地址"""
    td = os.environ.get("CTP_SET2_TD_FRONT", "tcp://182.254.243.31:40001")
    md = os.environ.get("CTP_SET2_MD_FRONT", "tcp://182.254.243.31:40011")
    # 同步写入兼容变量
    os.environ["CTP_TD_FRONT"] = td
    os.environ["CTP_MD_FRONT"] = md
    return td, md, "set2_7x24"


def apply_ctp_env() -> tuple[str, str, str]:
    """
    一键应用 CTP 环境选择，更新 CTP_TD_FRONT / CTP_MD_FRONT 环境变量。
    建议在 load_dotenv() 之后立即调用。

    Returns
    -------
    (td_front, md_front, env_name) : tuple[str, str, str]
    """
    td, md, name = get_ctp_fronts()
    return td, md, name
