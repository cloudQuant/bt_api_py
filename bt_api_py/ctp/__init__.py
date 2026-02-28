# 拆分后的子模块 — 提供按功能分类的导入路径
# 例如: from bt_api_py.ctp.ctp_trader_api import CThostFtdcTraderApi
# 例如: from bt_api_py.ctp.ctp_structs_order import CThostFtdcInputOrderField
from . import (
    _ctp_base,  # noqa: F401
    ctp_constants,  # noqa: F401
    ctp_md_api,  # noqa: F401
    ctp_structs_account,  # noqa: F401
    ctp_structs_common,  # noqa: F401
    ctp_structs_market,  # noqa: F401
    ctp_structs_order,  # noqa: F401
    ctp_structs_position,  # noqa: F401
    ctp_structs_query,  # noqa: F401
    ctp_structs_risk,  # noqa: F401
    ctp_structs_trade,  # noqa: F401
    ctp_structs_transfer,  # noqa: F401
    ctp_trader_api,  # noqa: F401
)
from ._ctp import *
from .ctp import *
