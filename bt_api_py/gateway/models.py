from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass
class GatewayTick:
    timestamp: float
    symbol: str
    exchange: str = ""
    asset_type: str = ""
    local_time: float | None = None
    price: float = 0.0
    volume: float = 0.0
    direction: str = "buy"
    trade_id: str = ""
    bid_price: float | None = None
    ask_price: float | None = None
    bid_volume: float | None = None
    ask_volume: float | None = None
    openinterest: float = 0.0
    turnover: float = 0.0
    datetime: datetime | None = None
    instrument_id: str = ""
    exchange_id: str = ""
    trading_day: str = ""
    action_day: str = ""
    update_time: str = ""
    update_millisec: int = 0
    high_price: float | None = None
    low_price: float | None = None
    open_price: float | None = None
    prev_close: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.datetime is not None:
            payload["datetime"] = self.datetime.isoformat()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GatewayTick:
        data = dict(payload)
        dt_value = data.get("datetime")
        if isinstance(dt_value, str) and dt_value:
            data["datetime"] = datetime.fromisoformat(dt_value)
        else:
            data["datetime"] = None
        known = {f.name for f in cls.__dataclass_fields__.values()}
        data = {k: v for k, v in data.items() if k in known}
        return cls(**data)
