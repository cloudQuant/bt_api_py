"""Security Compliance Configuration and Integration.

This module provides the main integration point for the security framework
with bt_api_py, including configuration management and initialization.
"""

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .core.access_control import AccessControlManager, Resource, PermissionLevel
from .core.audit_logger import AuditLogger, EventType, SeverityLevel
from .core.encryption_manager import EncryptionManager, create_key_manager, KeyProvider
from .auth.oauth2_provider import OAuth2Provider
from .auth.mfa_provider import MFAProvider
from .data.protection import DataProtectionManager
from .network.tls_manager import TLSManager
from .monitoring.security_monitoring import SecurityMonitoring
from .recovery.disaster_recovery import DisasterRecoveryManager


class SecurityFramework:
    """Main security framework integration class."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize security framework with configuration."""
        self.config = config
        self._initialize_components()

    def _initialize_components(self):
        """Initialize all security components."""
        # Initialize encryption manager
        self.encryption_manager = self._init_encryption_manager()

        # Initialize access control
        self.access_control = AccessControlManager(encryption_key=self.config.get("encryption_key"))

        # Initialize audit logger
        self.audit_logger = self._init_audit_logger()

        # Initialize OAuth2 provider
        self.oauth2_provider = self._init_oauth2_provider()

        # Initialize MFA provider
        self.mfa_provider = self._init_mfa_provider()

        # Initialize data protection
        self.data_protection = DataProtectionManager(
            encryption_manager=self.encryption_manager,
            config=self.config.get("data_protection", {}),
        )

        # Initialize TLS manager
        self.tls_manager = TLSManager(config=self.config.get("tls", {}))

        # Initialize security monitoring
        self.security_monitoring = SecurityMonitoring(
            audit_logger=self.audit_logger, config=self.config.get("monitoring", {})
        )

        # Initialize disaster recovery
        self.disaster_recovery = DisasterRecoveryManager(
            config=self.config.get("disaster_recovery", {})
        )

    def _init_encryption_manager(self) -> EncryptionManager:
        """Initialize encryption manager based on configuration."""
        encryption_config = self.config.get("encryption", {})
        provider_type = KeyProvider(encryption_config.get("provider", "local"))

        key_manager = create_key_manager(
            provider=provider_type, **encryption_config.get("provider_config", {})
        )

        return EncryptionManager(
            key_manager=key_manager,
            default_algorithm=encryption_config.get("algorithm", "aes_256_gcm"),
        )

    def _init_audit_logger(self) -> AuditLogger:
        """Initialize audit logger."""
        audit_config = self.config.get("audit", {})

        return AuditLogger(
            log_file=audit_config.get("log_file", "./logs/audit.log"),
            encryption_key=self.config.get("encryption_key"),
            retention_days=audit_config.get("retention_days", 2555),
            enable_real_time=audit_config.get("real_time", True),
        )

    def _init_oauth2_provider(self) -> OAuth2Provider:
        """Initialize OAuth2 provider."""
        oauth_config = self.config.get("oauth2", {})

        return OAuth2Provider(
            issuer_url=oauth_config.get("issuer_url", "https://localhost:8443"),
            private_key_path=oauth_config.get("private_key_path"),
            token_lifetime=oauth_config.get("token_lifetime", 3600),
            refresh_token_lifetime=oauth_config.get("refresh_token_lifetime", 2592000),
            enable_pkce=oauth_config.get("enable_pkce", True),
            enable_token_rotation=oauth_config.get("enable_token_rotation", True),
        )

    def _init_mfa_provider(self) -> MFAProvider:
        """Initialize MFA provider."""
        mfa_config = self.config.get("mfa", {})

        return MFAProvider(
            issuer_name=mfa_config.get("issuer_name", "bt_api_py"),
            totp_window=mfa_config.get("totp_window", 1),
            hotp_counter_start=mfa_config.get("hotp_counter_start", 0),
            backup_codes_count=mfa_config.get("backup_codes_count", 10),
            rp_id=mfa_config.get("rp_id", "localhost"),
            rp_name=mfa_config.get("rp_name", "bt_api_py Trading Platform"),
        )

    def get_security_status(self) -> Dict[str, Any]:
        """Get comprehensive security status."""
        return {
            "encryption": {
                "provider": self.config.get("encryption", {}).get("provider", "local"),
                "algorithm": self.config.get("encryption", {}).get("algorithm", "aes_256_gcm"),
                "key_rotation_enabled": self.config.get("encryption", {}).get("auto_rotate", False),
            },
            "access_control": {
                "users_count": len(self.access_control._users),
                "roles_count": len(self.access_control._roles),
                "active_sessions": len(self.access_control._session_contexts),
            },
            "audit": {
                "log_file": str(self.config.get("audit", {}).get("log_file", "./logs/audit.log")),
                "retention_days": self.config.get("audit", {}).get("retention_days", 2555),
                "real_time_enabled": self.config.get("audit", {}).get("real_time", True),
            },
            "compliance": {
                "pci_dss_level": self.config.get("compliance", {}).get("pci_dss_level", 1),
                "sox_compliance": self.config.get("compliance", {}).get("sox_compliance", True),
                "mifid_ii_compliance": self.config.get("compliance", {}).get(
                    "mifid_ii_compliance", True
                ),
                "gdpr_compliance": self.config.get("compliance", {}).get("gdpr_compliance", True),
            },
            "timestamp": time.time(),
        }


def create_security_config_from_env() -> Dict[str, Any]:
    """Create security configuration from environment variables."""
    return {
        "encryption": {
            "provider": os.getenv("SECURITY_KEY_PROVIDER", "local"),
            "provider_config": {
                "key_dir": os.getenv("SECURITY_KEY_DIR", "./security/keys"),
                "master_password": os.getenv("SECURITY_MASTER_PASSWORD"),
                "region": os.getenv("AWS_REGION", "us-east-1"),
                "vault_url": os.getenv("VAULT_URL"),
                "token": os.getenv("VAULT_TOKEN"),
            },
            "algorithm": os.getenv("ENCRYPTION_ALGORITHM", "aes_256_gcm"),
            "auto_rotate": os.getenv("AUTO_KEY_ROTATION", "false").lower() == "true",
        },
        "audit": {
            "log_file": os.getenv("AUDIT_LOG_FILE", "./logs/audit.log"),
            "retention_days": int(os.getenv("AUDIT_RETENTION_DAYS", "2555")),
            "real_time": os.getenv("AUDIT_REAL_TIME", "true").lower() == "true",
        },
        "oauth2": {
            "issuer_url": os.getenv("OAUTH2_ISSUER_URL", "https://localhost:8443"),
            "private_key_path": os.getenv("OAUTH2_PRIVATE_KEY_PATH"),
            "token_lifetime": int(os.getenv("OAUTH2_TOKEN_LIFETIME", "3600")),
            "refresh_token_lifetime": int(os.getenv("OAUTH2_REFRESH_TOKEN_LIFETIME", "2592000")),
            "enable_pkce": os.getenv("OAUTH2_ENABLE_PKCE", "true").lower() == "true",
            "enable_token_rotation": os.getenv("OAUTH2_ENABLE_TOKEN_ROTATION", "true").lower()
            == "true",
        },
        "mfa": {
            "issuer_name": os.getenv("MFA_ISSUER_NAME", "bt_api_py"),
            "totp_window": int(os.getenv("MFA_TOTP_WINDOW", "1")),
            "hotp_counter_start": int(os.getenv("MFA_HOTP_COUNTER_START", "0")),
            "backup_codes_count": int(os.getenv("MFA_BACKUP_CODES_COUNT", "10")),
            "rp_id": os.getenv("MFA_RP_ID", "localhost"),
            "rp_name": os.getenv("MFA_RP_NAME", "bt_api_py Trading Platform"),
        },
        "data_protection": {
            "enable_data_masking": os.getenv("DATA_MASKING_ENABLED", "true").lower() == "true",
            "data_retention_days": int(os.getenv("DATA_RETENTION_DAYS", "2555")),
            "gdpr_compliance": os.getenv("GDPR_COMPLIANCE", "true").lower() == "true",
        },
        "tls": {
            "version": os.getenv("TLS_VERSION", "1.3"),
            "cipher_suites": os.getenv("TLS_CIPHER_SUITES", "TLS_AES_256_GCM_SHA384").split(","),
            "certificate_validation": os.getenv("TLS_CERT_VALIDATION", "strict"),
        },
        "monitoring": {
            "enable_real_time": os.getenv("SECURITY_MONITORING", "true").lower() == "true",
            "alert_thresholds": {
                "failed_logins": int(os.getenv("ALERT_FAILED_LOGINS", "5")),
                "unauthorized_access": int(os.getenv("ALERT_UNAUTHORIZED_ACCESS", "3")),
            },
        },
        "disaster_recovery": {
            "backup_enabled": os.getenv("BACKUP_ENABLED", "true").lower() == "true",
            "backup_frequency": os.getenv("BACKUP_FREQUENCY", "daily"),
            "backup_retention_days": int(os.getenv("BACKUP_RETENTION_DAYS", "30")),
        },
        "compliance": {
            "pci_dss_level": int(os.getenv("PCI_DSS_LEVEL", "1")),
            "sox_compliance": os.getenv("SOX_COMPLIANCE", "true").lower() == "true",
            "mifid_ii_compliance": os.getenv("MIFID_II_COMPLIANCE", "true").lower() == "true",
            "gdpr_compliance": os.getenv("GDPR_COMPLIANCE", "true").lower() == "true",
        },
        "encryption_key": os.getenv("SECURITY_ENCRYPTION_KEY"),
    }


# Global security framework instance
_security_framework: Optional[SecurityFramework] = None


def get_security_framework() -> Optional[SecurityFramework]:
    """Get the global security framework instance."""
    return _security_framework


def initialize_security_framework(config: Optional[Dict[str, Any]] = None) -> SecurityFramework:
    """Initialize the global security framework."""
    global _security_framework

    if config is None:
        config = create_security_config_from_env()

    _security_framework = SecurityFramework(config)
    return _security_framework


def integrate_with_bt_api(bt_api_instance):
    """Integrate security framework with bt_api_py instance."""
    framework = get_security_framework()
    if not framework:
        raise RuntimeError("Security framework not initialized")

    # Wrap methods with security checks
    original_create_feed = bt_api_instance.create_feed

    def secure_create_feed(*args, **kwargs):
        # Extract user context from kwargs or get from session
        user_id = kwargs.pop("user_id", None)
        if user_id:
            # Check permissions
            framework.access_control.require_permission(
                user_id, Resource.EXCHANGE_CONFIG, "create", PermissionLevel.WRITE
            )

            # Log the action
            framework.audit_logger.log_event(
                EventType.USER_LOGIN,  # Use appropriate event type
                user_id=user_id,
                resource="exchange_feed",
                action="create",
                details={"args": args, "kwargs": kwargs},
            )

        return original_create_feed(*args, **kwargs)

    # Replace method with secured version
    bt_api_instance.create_feed = secure_create_feed

    # Add security methods to bt_api instance
    bt_api_instance.security = framework

    return bt_api_instance


# Decorator for security
def require_permission(
    resource: Resource, action: str, level: PermissionLevel = PermissionLevel.READ
):
    """Decorator to require specific permissions for a function."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            framework = get_security_framework()
            if not framework:
                return func(*args, **kwargs)

            # Extract user_id from kwargs or assume from session
            user_id = kwargs.get("user_id")
            if user_id:
                framework.access_control.require_permission(user_id, resource, action, level)

            return func(*args, **kwargs)

        return wrapper

    return decorator


# Security check decorator
def audit_access(event_type: EventType, severity: SeverityLevel = SeverityLevel.MEDIUM):
    """Decorator to audit function access."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            framework = get_security_framework()
            if not framework:
                return func(*args, **kwargs)

            # Extract user context
            user_id = kwargs.get("user_id", "anonymous")

            # Log before execution
            framework.audit_logger.log_event(
                event_type=event_type,
                user_id=user_id,
                severity=severity,
                resource=func.__name__,
                action="execute",
                details={"args": args, "kwargs": kwargs},
            )

            try:
                result = func(*args, **kwargs)

                # Log successful execution
                framework.audit_logger.log_event(
                    event_type=event_type,
                    user_id=user_id,
                    severity=severity,
                    resource=func.__name__,
                    action="success",
                    outcome="success",
                )

                return result

            except Exception as e:
                # Log failed execution
                framework.audit_logger.log_event(
                    event_type=event_type,
                    user_id=user_id,
                    severity=SeverityLevel.HIGH,
                    resource=func.__name__,
                    action="error",
                    outcome="failure",
                    details={"error": str(e)},
                )

                raise

        return wrapper

    return decorator
