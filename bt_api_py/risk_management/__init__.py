"""智能风控和合规监控系统

Intelligent Risk Management and Compliance Monitoring System for 73+ exchanges.

核心功能:
1. 实时风险监控 - 交易前、交易中、交易后全流程风控
2. 智能风险评估 - 机器学习驱动的风险评分和预测
3. 合规监控 - 市场操纵检测、内幕交易监控、反洗钱
4. 动态限制管理 - 基于风险等级的动态调整和自动限制
5. 异常检测 - 行为分析、模式识别、异常交易检测
6. 实时监控仪表板 - 风险指标可视化、告警系统、决策支持

技术特性:
- 机器学习模型 (随机森林、神经网络、异常检测算法)
- 实时流处理 (异步事件处理)
- 复杂事件处理 (CEP) 引擎
- 图计算 (关系网络分析)
- 时间序列数据库 (风险指标存储)
- 规则引擎 (基于事件和模式的规则系统)

合规标准:
- 市场操纵检测 (spoofing、layering、front running)
- 反洗钱 (AML) 和了解你的客户 (KYC)
- 交易报告 (MiFID II、SEC Rule 606)
- 仓位限制和集中度限制
- 信用风险和保证金管理
"""

from .core.risk_manager import RiskManager
from .core.risk_assessor import RiskAssessor
from .ml_models.ensemble_model import RiskEnsembleModel
from .ml_models.anomaly_detector import AnomalyDetector
from .core.risk_manager import RiskManager
from .core.risk_assessor import RiskAssessor
from .ml_models.ensemble_model import RiskEnsembleModel
from .ml_models.anomaly_detector import AnomalyDetector
from .containers.risk_metrics import RiskMetrics
from .containers.risk_events import RiskEvent, RiskEventType, RiskLevel

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
from .containers.risk_metrics import RiskMetrics
from .containers.risk_events import RiskEvent, RiskEventType, RiskLevel

__all__ = [
    # Core Risk Management
    "RiskManager",
    "RiskAssessor",
    # ML Models
    "RiskEnsembleModel",
    "AnomalyDetector",
    # Monitoring
    "RealTimeMonitor",
    "AlertSystem",
    # Compliance
    "ComplianceEngine",
    "MarketManipulationDetector",
    # Data Containers
    "RiskMetrics",
    "RiskEvent",
    "RiskEventType",
    "RiskLevel",
]

# 版本信息
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

# 默认配置
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
