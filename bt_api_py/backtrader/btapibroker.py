"""Module-level docstring."""
from __future__ import annotations

from dataclasses import dataclass

from bt_api_py.brokers import BrokerAdapter, load_adapter


@dataclass(slots=True)
class BtApiBroker:
    """Class BtApiBroker"""
    adapter_name: str = "mock"

    def create_adapter(self) -> BrokerAdapter:
        """create_adapter method"""
        return load_adapter(self.adapter_name)
