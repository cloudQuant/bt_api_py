"""市场风险计算（VaR/CVaR/波动率/Beta/相关性/压力测试）。"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal
from typing import Any

import numpy as np


class MarketRiskMixin:
    """市场风险计算方法（供 RiskCalculator 混入）。"""

    min_data_points: int
    default_volatility_window: int
    stress_scenarios: dict[str, dict[str, Any]]

    def _calculate_returns(self, price_history: list[float]) -> list[float]:
        """"""
        if len(price_history) < 2:
            return []

        returns = []
        for i in range(1, len(price_history)):
            if price_history[i - 1] != 0:
                ret = (price_history[i] - price_history[i - 1]) / price_history[i - 1]
                returns.append(ret)

        return returns

    def _calculate_var(
        self, returns: list[float], confidence: float = 0.95, time_horizon: int = 1
    ) -> Decimal:
        """VaR (Value at Risk)"""
        if not returns or len(returns) < self.min_data_points:
            return Decimal("0")

        #
        var_percentile = (1 - confidence) * 100
        var = np.percentile(returns, var_percentile)

        #
        var_time_adjusted = var * math.sqrt(time_horizon)

        return Decimal(str(abs(var_time_adjusted)))

    def _calculate_cvar(self, returns: list[float], confidence: float = 0.95) -> Decimal:
        """CVaR (Conditional Value at Risk) / Expected Shortfall"""
        if not returns or len(returns) < self.min_data_points:
            return Decimal("0")

        var_percentile = (1 - confidence) * 100
        var_threshold = np.percentile(returns, var_percentile)

        # VaR
        tail_losses = [r for r in returns if r <= var_threshold]
        if not tail_losses:
            return Decimal("0")

        cvar = statistics.mean(tail_losses)
        return Decimal(str(abs(cvar)))

    def _calculate_volatility(self, returns: list[float], window: int | None = None) -> Decimal:
        """"""
        if not returns:
            return Decimal("0")

        window = window or self.default_volatility_window
        if len(returns) < 2:
            return Decimal("0")

        #
        recent_returns = returns[-window:] if len(returns) > window else returns

        if len(recent_returns) < 2:
            return Decimal("0")

        volatility = statistics.stdev(recent_returns)
        return Decimal(str(volatility))

    def _calculate_beta(self, asset_returns: list[float], market_returns: list[float]) -> Decimal:
        """Beta"""
        if len(asset_returns) < 2 or len(market_returns) < 2:
            return Decimal("1.0")  #

        #
        min_len = min(len(asset_returns), len(market_returns))
        asset_returns = asset_returns[-min_len:]
        market_returns = market_returns[-min_len:]

        if len(asset_returns) < 2:
            return Decimal("1.0")

        #
        if statistics.stdev(market_returns) == 0:
            return Decimal("1.0")

        covariance = statistics.covariance(asset_returns, market_returns)
        market_variance = statistics.variance(market_returns)

        if market_variance == 0:
            return Decimal("1.0")

        beta = covariance / market_variance
        return Decimal(str(beta))

    def _calculate_correlation_matrix(
        self, asset_returns: dict[str, list[float]]
    ) -> dict[str, dict[str, float]]:
        """"""
        correlation_matrix: dict[str, dict[str, float]] = {}
        assets = list(asset_returns.keys())

        for asset1 in assets:
            correlation_matrix[asset1] = {}
            returns1 = asset_returns[asset1]

            if len(returns1) < 2:
                correlation_matrix[asset1][asset1] = 1.0
                continue

            for asset2 in assets:
                returns2 = asset_returns[asset2]

                if len(returns2) < 2:
                    correlation_matrix[asset1][asset2] = 0.0
                    continue

                if asset1 == asset2:
                    correlation_matrix[asset1][asset2] = 1.0
                else:
                    #
                    min_len = min(len(returns1), len(returns2))
                    r1 = returns1[-min_len:]
                    r2 = returns2[-min_len:]

                    if len(r1) < 2 or statistics.stdev(r1) == 0 or statistics.stdev(r2) == 0:
                        correlation_matrix[asset1][asset2] = 0.0
                    else:
                        correlation = np.corrcoef(r1, r2)[0, 1]
                        correlation_matrix[asset1][asset2] = (
                            float(correlation) if not math.isnan(correlation) else 0.0
                        )

        return correlation_matrix

    def _run_stress_tests(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> dict[str, Any]:
        """"""
        stress_results = {}

        for scenario_name, scenario_params in self.stress_scenarios.items():
            portfolio_value = position_data.get("portfolio_value", 0)

            if scenario_name == "market_crash":
                price_change = scenario_params.get("price_change", -0.3)
                stressed_value = portfolio_value * (1 + price_change)
                loss = portfolio_value - stressed_value

            elif scenario_name == "liquidity_crisis":
                scenario_params.get("spread_increase", 3.0)
                scenario_params.get("volume_decrease", 0.5)
                #
                stressed_value = portfolio_value * (1 - 0.1)  # 10%
                loss = portfolio_value - stressed_value

            elif scenario_name == "credit_event":
                default_rate = scenario_params.get("default_rate", 0.05)
                stressed_value = portfolio_value * (1 - default_rate)
                loss = portfolio_value - stressed_value

            else:
                loss = 0

            stress_results[scenario_name] = {
                "portfolio_loss": loss,
                "loss_percentage": (loss / portfolio_value * 100) if portfolio_value > 0 else 0,
                "scenario_params": scenario_params,
            }

        return stress_results

    def _run_scenario_analysis(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> dict[str, Any]:
        """"""
        scenarios = {
            "best_case": {"return": 0.15, "probability": 0.1},
            "moderate_case": {"return": 0.05, "probability": 0.6},
            "worst_case": {"return": -0.20, "probability": 0.3},
        }

        portfolio_value = position_data.get("portfolio_value", 0)
        scenario_results = {}

        for scenario_name, params in scenarios.items():
            expected_return = params["return"]
            probability = params["probability"]
            expected_value = portfolio_value * (1 + expected_return)

            scenario_results[scenario_name] = {
                "expected_value": expected_value,
                "expected_return": expected_return,
                "probability": probability,
                "expected_pnl": expected_value - portfolio_value,
            }

        return scenario_results
