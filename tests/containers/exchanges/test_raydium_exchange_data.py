"""Tests for RaydiumExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.raydium_exchange_data import RaydiumExchangeData


class TestRaydiumExchangeData:
    """Tests for RaydiumExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = RaydiumExchangeData()

        assert exchange.exchange_name == "RAYDIUM___DEX"
