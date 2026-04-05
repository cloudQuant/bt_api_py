from __future__ import annotations

from typing import Any


class TimerData:
    def __init__(self, data: Any) -> None:
        self.event_type = "Timer_update"
        self.data = data

    def get_data(self) -> Any:
        return self.data

    def __str__(self) -> str:
        return str(self.data)
