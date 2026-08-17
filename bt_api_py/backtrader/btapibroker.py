"""@deprecated: 占位 broker 包装，非 backtrader 集成。

本模块仅提供 ``load_adapter`` 的薄包装，不是 backtrader 官方 BrokerBase/Store
集成。推荐通过 ``bt_api_py.forwarding.ForwardingClient`` 接入 backtrader。
真正的 backtrader store 集成另立 backlog（见 docs/decisions/2026-08-16-placeholder-modules.md）。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from bt_api_py.brokers import BrokerAdapter, load_adapter


@dataclass(slots=True)
class BtApiBroker:
    """@deprecated 占位 broker 包装（非 backtrader 集成）。"""

    adapter_name: str = "mock"

    def create_adapter(self) -> BrokerAdapter:
        """create_adapter method"""
        warnings.warn(
            "BtApiBroker is deprecated; use bt_api_py.forwarding.ForwardingClient instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return load_adapter(self.adapter_name)
