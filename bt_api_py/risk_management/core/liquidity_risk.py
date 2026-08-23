"""流动性风险计算（流动性评分/买卖价差/市场深度/冲击成本）。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..containers.risk_metrics import LiquidityRiskMetrics


class LiquidityRiskMixin:
    """流动性风险计算方法（供 RiskCalculator 混入）。"""

    def _calculate_liquidity_risk(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> LiquidityRiskMetrics:
        """"""

        #
        liquidity_score = self._calculate_liquidity_score(position_data, market_data)

        #
        bid_ask_spread = self._calculate_bid_ask_spread(market_data)

        #
        market_depth = self._calculate_market_depth(market_data)

        #
        impact_cost = self._calculate_impact_cost(position_data, market_data)

        #
        volume_profile = self._calculate_volume_profile(market_data)

        #
        liquidation_value = self._calculate_liquidation_value(position_data, market_data)

        return LiquidityRiskMetrics(
            {
                "liquidity_score": liquidity_score,
                "bid_ask_spread": bid_ask_spread,
                "market_depth": market_depth,
                "impact_cost": impact_cost,
                "volume_profile": volume_profile,
                "liquidation_value": liquidation_value,
                "funding_constraints": {},  #
            }
        )

    def _calculate_liquidity_score(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> Decimal:
        """"""
        #
        bid_ask_spread = market_data.get("bid_ask_spread", 10)  # bps
        market_depth = market_data.get("market_depth", 1000000)  # USD
        volume_24h = market_data.get("volume_24h", 50000000)  # USD

        #  (0-1)
        spread_score = max(0, 1 - bid_ask_spread / 100)  # 100bps
        depth_score = min(1, market_depth / 10000000)  # 1000
        volume_score = min(1, volume_24h / 100000000)  # 1

        liquidity_score = (spread_score + depth_score + volume_score) / 3
        return Decimal(str(liquidity_score))

    def _calculate_bid_ask_spread(self, market_data: dict[str, Any]) -> Decimal:
        """"""
        bid_price = market_data.get("bid_price", 0)
        ask_price = market_data.get("ask_price", 0)
        mid_price = (bid_price + ask_price) / 2

        if mid_price == 0:
            return Decimal("0")

        spread_bps = ((ask_price - bid_price) / mid_price) * 10000
        return Decimal(str(spread_bps))

    def _calculate_market_depth(self, market_data: dict[str, Any]) -> Decimal:
        """"""
        bid_depth = market_data.get("bid_depth", 0)  #
        ask_depth = market_data.get("ask_depth", 0)  #
        total_depth = bid_depth + ask_depth

        return Decimal(str(total_depth))

    def _calculate_impact_cost(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> Decimal:
        """"""
        position_size = position_data.get("position_size", 0)
        market_depth = market_data.get("market_depth", 1000000)
        bid_ask_spread = market_data.get("bid_ask_spread", 10)

        if market_depth == 0:
            return Decimal("0")

        #
        size_ratio = abs(position_size) / market_depth
        spread_cost = bid_ask_spread / 2  # bps
        impact_cost = spread_cost * (1 + size_ratio)

        return Decimal(str(impact_cost))

    def _calculate_volume_profile(self, market_data: dict[str, Any]) -> dict[str, Any]:
        """"""
        volume_data = market_data.get("volume_by_price", {})

        if not volume_data:
            return {}

        total_volume = sum(volume_data.values())
        volume_profile = {}

        for price, volume in volume_data.items():
            volume_profile[str(price)] = {
                "volume": volume,
                "percentage": volume / total_volume if total_volume > 0 else 0,
            }

        return volume_profile

    def _calculate_liquidation_value(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> Decimal:
        """"""
        positions = position_data.get("positions", [])
        liquidation_value = 0

        for pos in positions:
            symbol = pos.get("symbol", "")
            quantity = pos.get("quantity", 0)

            #  (95%)
            current_price = market_data.get("current_prices", {}).get(symbol, 0)
            liquidation_price = current_price * 0.95

            liquidation_value += quantity * liquidation_price

        return Decimal(str(liquidation_value))
