"""持仓集中度与行业敞口计算。"""

from __future__ import annotations

from typing import Any

from ..containers.risk_metrics import PositionConcentration, SectorExposure


class PositionRiskMixin:
    """持仓风险计算方法（供 RiskCalculator 混入）。"""

    def _calculate_position_concentration(
        self, position_data: dict[str, Any]
    ) -> PositionConcentration:
        """"""
        positions = position_data.get("positions", [])
        total_value = sum(pos.get("value", 0) for pos in positions)

        if total_value == 0:
            return PositionConcentration({})

        # 
        weights = [pos.get("value", 0) / total_value for pos in positions]
        herfindahl_index = sum(w**2 for w in weights)

        # 10
        sorted_positions = sorted(positions, key=lambda x: x.get("value", 0), reverse=True)
        top_10_value = sum(pos.get("value", 0) for pos in sorted_positions[:10])
        top_10_ratio = top_10_value / total_value

        # 
        single_position_max = max(weights) if weights else 0

        return PositionConcentration(
            {
                "herfindahl_index": herfindahl_index,
                "top_10_holdings_ratio": top_10_ratio,
                "single_position_max": single_position_max,
                "sector_concentration": {},  # 
                "geographic_concentration": {},  # 
            }
        )

    def _calculate_sector_exposure(self, position_data: dict[str, Any]) -> SectorExposure:
        """"""
        positions = position_data.get("positions", [])
        total_value = sum(pos.get("value", 0) for pos in positions)

        if total_value == 0:
            return SectorExposure({})

        # 
        sector_exposure: dict[str, float] = {}
        for pos in positions:
            sector = pos.get("sector", "other")
            value = pos.get("value", 0)
            sector_exposure[sector] = sector_exposure.get(sector, 0) + value

        # 
        sector_percentages = {}
        for sector, value in sector_exposure.items():
            sector_percentages[sector] = value / total_value

        return SectorExposure(sector_percentages)
