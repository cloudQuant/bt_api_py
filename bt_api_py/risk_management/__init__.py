"""Optional risk facade; risk policy and analytics are owned by bt_api_risk."""

from importlib import import_module

__all__ = [
    "RiskManager", "RiskAssessor", "RiskEnsembleModel", "AnomalyDetector",
    "RiskMetrics", "RiskEvent", "RiskEventType", "RiskLevel", "DEFAULT_RISK_CONFIG",
]


def __getattr__(name):
    if name not in __all__ and name not in {"__version__", "__compliance_standards__"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("bt_api_risk"), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
