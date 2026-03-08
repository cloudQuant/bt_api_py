"""bt_api_py Security Compliance and Audit Framework.

This module provides a comprehensive security framework designed for financial industry
compliance, including zero-trust architecture, end-to-end encryption, audit logging,
and regulatory compliance support.

Compliance Standards Supported:
- PCI DSS Level 1 (Payment Card Industry Data Security Standard)
- SOX 404 (Sarbanes-Oxley Act)
- MiFID II (Markets in Financial Instruments Directive)
- GDPR (General Data Protection Regulation)
- ISO 27001 (Information Security Management)
- NIST Cybersecurity Framework
- SOC 2 Type II
- FIPS 140-2 Level 3 (Federal Information Processing Standard)
- Common Criteria (ISO/IEC 15408)
"""

from .core.access_control import AccessControlManager, Role, Permission
from .core.audit_logger import AuditLogger, AuditEvent, EventType
from .core.encryption_manager import EncryptionManager
from .core.compliance_monitor import ComplianceMonitor
from .core.threat_detection import ThreatDetector
from .core.identity_manager import IdentityManager
from .auth.oauth2_provider import OAuth2Provider
from .auth.mfa_provider import MFAProvider
from .data.protection import DataProtectionManager
from .network.tls_manager import TLSManager
from .monitoring.security_monitoring import SecurityMonitoring
from .recovery.disaster_recovery import DisasterRecoveryManager

__all__ = [
    # Core Security Components
    "AccessControlManager",
    "Role",
    "Permission",
    "AuditLogger",
    "AuditEvent",
    "EventType",
    "EncryptionManager",
    "ComplianceMonitor",
    "ThreatDetector",
    "IdentityManager",
    # Authentication
    "OAuth2Provider",
    "MFAProvider",
    # Data Protection
    "DataProtectionManager",
    # Network Security
    "TLSManager",
    # Monitoring
    "SecurityMonitoring",
    # Recovery
    "DisasterRecoveryManager",
]

# Security Framework Version
__version__ = "1.0.0"
__compliance_version__ = "2024.3"

# Default security configuration
DEFAULT_SECURITY_CONFIG = {
    "encryption": {
        "algorithm": "AES-256-GCM",
        "key_derivation": "PBKDF2",
        "iterations": 100000,
        "fips_compliant": True,
    },
    "authentication": {
        "mfa_required": True,
        "session_timeout": 3600,  # 1 hour
        "max_login_attempts": 3,
        "lockout_duration": 900,  # 15 minutes
    },
    "audit": {
        "log_all_access": True,
        "log_retention_days": 2555,  # 7 years for SOX compliance
        "real_time_alerts": True,
    },
    "network": {
        "tls_version": "1.3",
        "cipher_suites": ["TLS_AES_256_GCM_SHA384"],
        "certificate_validation": "strict",
    },
    "compliance": {
        "pci_dss_level": 1,
        "sox_compliance": True,
        "gdpr_compliance": True,
        "mifid_ii_compliance": True,
    },
}
