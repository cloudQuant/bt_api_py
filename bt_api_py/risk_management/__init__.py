"""

Intelligent Risk Management and Compliance Monitoring System for 73+ exchanges.

:
1.  - 、、
2.  - 
3.  - 、、
4.  - 
5.  - 、、
6.  - 、、

:
-  (、、)
-  ()
-  (CEP) 
-  ()
-  ()
-  ()

:
-  (spoofing、layering、front running)
-  (AML)  (KYC)
-  (MiFID II、SEC Rule 606)
- 
- 
"""

from __future__ import annotations

from .containers.risk_events import RiskEvent, RiskEventType, RiskLevel
from .containers.risk_metrics import RiskMetrics
from .core.risk_assessor import RiskAssessor
from .core.risk_manager import RiskManager
from .ml_models.anomaly_detector import AnomalyDetector
from .ml_models.ensemble_model import RiskEnsembleModel

__all__ = [
    # Core Risk Management
    "RiskManager",
    "RiskAssessor",
    # ML Models
    "RiskEnsembleModel",
    "AnomalyDetector",
    # Data Containers
    "RiskMetrics",
    "RiskEvent",
    "RiskEventType",
    "RiskLevel",
]

# 
__version__ = "1.0.0"
__compliance_standards__ = [
    "MiFID II",
    "SEC Rule 606",
    "Market Abuse Regulation (MAR)",
    "Anti-Money Laundering (AML)",
    "KYC",
    "Basel III",
    "IOSCO Principles",
]

# 
DEFAULT_RISK_CONFIG = {
    "risk_thresholds": {
        "low": 0.3,
        "medium": 0.6,
        "high": 0.8,
        "critical": 0.9,
    },
    "monitoring": {
        "real_time_enabled": True,
        "batch_processing_interval": 60,  # seconds
        "alert_cooldown": 300,  # seconds
    },
    "ml_models": {
        "ensemble_weights": {"rf": 0.4, "nn": 0.4, "xgb": 0.2},
        "retraining_interval": 86400,  # 24 hours
        "min_samples": 1000,
    },
    "compliance": {
        "market_manipulation_detection": True,
        "aml_monitoring": True,
        "position_limits": True,
        "reporting_requirements": True,
    },
    "performance": {
        "max_processing_time_ms": 100,
        "memory_limit_mb": 1024,
        "concurrency_limit": 10,
    },
}
