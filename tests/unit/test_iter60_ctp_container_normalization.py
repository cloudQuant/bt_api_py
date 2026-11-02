"""Iteration 60 (60.05): CTP standard getters keep SDK neutral rows unchanged.

Before contract v1, CTP order/position ``get_position_side()`` raised
NotImplementedError and ``_normalization._dict()`` skipped it. Now that the
getters answer, the neutral rows must carry the same values as before:
positions keep the lowercase CTP direction, close orders keep the strict
projection's ``LONG``/``SHORT`` and opening orders gain no position side.
"""

from __future__ import annotations

import pytest
from bt_api_ctp.containers.ctp.ctp_order import CtpOrderData
from bt_api_ctp.containers.ctp.ctp_position import CtpPositionData

from bt_api_py._normalization import _dict

_SYMBOL = "SA609"


@pytest.mark.parametrize("code, side", [("2", "long"), ("3", "short"), ("1", "net")])
def test_ctp_position_row_side_matches_direction(code, side):
    row = _dict(
        CtpPositionData(
            {"InstrumentID": _SYMBOL, "PosiDirection": code, "Position": 3, "ExchangeID": "CZCE"},
            _SYMBOL,
        )
    )
    assert row["position_side"] == side
    assert row["direction"] == side


@pytest.mark.parametrize(
    "direction, offset, side",
    [("1", "1", "LONG"), ("0", "1", "SHORT"), ("0", "0", None), ("1", "2", "UNRESOLVED")],
)
def test_ctp_order_row_position_side_is_the_native_projection(direction, offset, side):
    order = CtpOrderData(
        {
            "InstrumentID": _SYMBOL,
            "OrderRef": "1",
            "Direction": direction,
            "CombOffsetFlag": offset,
            "LimitPrice": 1500,
            "VolumeTotalOriginal": 1,
            "VolumeTraded": 0,
            "VolumeTotal": 1,
            "OrderStatus": "3",
            "ExchangeID": "CZCE",
        },
        _SYMBOL,
    )
    row = _dict(order)
    assert row.get("position_side") == side
