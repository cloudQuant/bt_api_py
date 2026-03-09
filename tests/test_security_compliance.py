"""Security Compliance Test Suite.

Tests for the security compliance framework to ensure
proper functioning of all security components.
"""

from pathlib import Path

import pytest

from bt_api_py.security_compliance.auth.mfa_provider import MFAProvider
from bt_api_py.security_compliance.auth.oauth2_provider import GrantType, OAuth2Provider, TokenType
from bt_api_py.security_compliance.core.access_control import (
    AccessControlManager,
    PermissionLevel,
    Resource,
)
from bt_api_py.security_compliance.core.audit_logger import (
    AuditEvent,
    AuditLogger,
    EventType,
    SeverityLevel,
)
from bt_api_py.security_compliance.core.encryption_manager import (
    EncryptionManager,
    KeyProvider,
    create_key_manager,
)
from bt_api_py.security_compliance.data.protection import DataProtectionManager
from bt_api_py.security_compliance.framework import SecurityFramework


class TestAccessControl:
    """Test access control functionality."""

    def setup_method(self):
        """Setup test environment."""
        self.access_control = AccessControlManager()

    def test_create_user(self):
        """Test user creation."""
        user = self.access_control.create_user("test_user", "testuser", "test@example.com")

        assert user.user_id == "test_user"
        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.is_active is True

    def test_create_role(self):
        """Test role creation."""
        role = self.access_control.create_role("test_role", "Test role description")

        assert role.name == "test_role"
        assert role.description == "Test role description"
        assert role.is_system_role is False

    def test_assign_role(self):
        """Test role assignment."""
        user = self.access_control.create_user("test_user", "testuser", "test@example.com")
        role = self.access_control.create_role("test_role", "Test role")

        self.access_control.assign_role("test_user", "test_role")

        assert user.has_role("test_role")

    def test_permission_check(self):
        """Test permission checking."""
        user = self.access_control.create_user("trader_user", "trader", "trader@example.com")

        # User should have trader permissions by default
        assert self.access_control.check_permission(
            "trader_user", Resource.MARKET_DATA, "read", PermissionLevel.READ
        )

    def test_access_denied(self):
        """Test access denied scenarios."""
        user = self.access_control.create_user("trader_user", "trader", "trader@example.com")

        # User should not have admin permissions
        assert not self.access_control.check_permission(
            "trader_user", Resource.SECURITY_CONFIG, "write", PermissionLevel.ADMIN
        )


class TestAuditLogger:
    """Test audit logging functionality."""

    def setup_method(self):
        """Setup test environment."""
        self.temp_dir = Path("/tmp/test_audit")
        self.temp_dir.mkdir(exist_ok=True)
        self.audit_logger = AuditLogger(log_file=self.temp_dir / "test_audit.log")

    def teardown_method(self):
        """Cleanup test environment."""
        import shutil

        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_log_event(self):
        """Test event logging."""
        event = AuditEvent(
            event_type=EventType.USER_LOGIN, user_id="test_user", severity=SeverityLevel.MEDIUM
        )

        self.audit_logger.log_event(event)

        # Verify event was logged
        assert len(self.audit_logger._events) == 1
        assert self.audit_logger._events[0].event_type == EventType.USER_LOGIN

    def test_log_integrity(self):
        """Test audit log integrity."""
        # Log multiple events
        for i in range(5):
            event = AuditEvent(
                event_type=EventType.USER_LOGIN,
                user_id=f"test_user_{i}",
                severity=SeverityLevel.MEDIUM,
            )
            self.audit_logger.log_event(event)

        # Verify integrity
        integrity = self.audit_logger.verify_log_integrity()
        assert integrity["status"] == "verified"
        assert integrity["verified_events"] == 5

    def test_search_events(self):
        """Test event searching."""
        # Log events for different users
        for user_id in ["user1", "user2", "user1"]:
            event = AuditEvent(
                event_type=EventType.USER_LOGIN, user_id=user_id, severity=SeverityLevel.MEDIUM
            )
            self.audit_logger.log_event(event)

        # Search for specific user
        events = self.audit_logger.search_events(user_id="user1")
        assert len(events) == 2
        assert all(event.user_id == "user1" for event in events)


class TestEncryptionManager:
    """Test encryption management."""

    def setup_method(self):
        """Setup test environment."""
        key_manager = create_key_manager(KeyProvider.LOCAL)
        self.encryption_manager = EncryptionManager(key_manager)

    def test_encrypt_decrypt(self):
        """Test encryption and decryption."""
        original_data = "sensitive information"

        # Encrypt data
        encrypted = self.encryption_manager.encrypt(original_data)

        # Decrypt data
        decrypted = self.encryption_manager.decrypt(encrypted)

        assert decrypted.decode() == original_data

    def test_key_rotation(self):
        """Test key rotation."""
        old_key = self.encryption_manager._get_active_key()

        # Rotate key
        new_key = self.encryption_manager.rotate_keys()

        assert new_key != old_key
        assert new_key.is_active is True


class TestOAuth2Provider:
    """Test OAuth2 provider functionality."""

    def setup_method(self):
        """Setup test environment."""
        self.oauth2 = OAuth2Provider("https://test.example.com")

    def test_register_client(self):
        """Test client registration."""
        client = self.oauth2.register_client(
            client_id="test_client",
            client_secret="secret123",
            redirect_uris=["https://test.example.com/callback"],
            scopes=["read", "write"],
            grant_types={GrantType.AUTHORIZATION_CODE},
        )

        assert client.client_id == "test_client"
        assert client.can_use_grant_type(GrantType.AUTHORIZATION_CODE)

    def test_generate_access_token(self):
        """Test access token generation."""
        client = self.oauth2.register_client(
            client_id="test_client",
            client_secret="secret123",
            redirect_uris=["https://test.example.com/callback"],
            scopes=["read", "write"],
            grant_types={GrantType.AUTHORIZATION_CODE},
        )

        token = self.oauth2.generate_access_token(
            client_id="test_client",
            user_id="test_user",
            scopes={"read"},
            grant_type=GrantType.AUTHORIZATION_CODE,
        )

        assert token.token_type == TokenType.BEARER
        assert token.client_id == "test_client"
        assert token.user_id == "test_user"

    def test_validate_access_token(self):
        """Test access token validation."""
        client = self.oauth2.register_client(
            client_id="test_client",
            client_secret="secret123",
            redirect_uris=["https://test.example.com/callback"],
            scopes=["read", "write"],
            grant_types={GrantType.AUTHORIZATION_CODE},
        )

        token = self.oauth2.generate_access_token(
            client_id="test_client",
            user_id="test_user",
            scopes={"read"},
            grant_type=GrantType.AUTHORIZATION_CODE,
        )

        # Validate token
        validated_token = self.oauth2.validate_access_token(token.token)
        assert validated_token.client_id == "test_client"
        assert validated_token.user_id == "test_user"


class TestMFAProvider:
    """Test MFA provider functionality."""

    def setup_method(self):
        """Setup test environment."""
        self.mfa_provider = MFAProvider()

    def test_setup_totp(self):
        """Test TOTP setup."""
        result = self.mfa_provider.setup_totp("test_user")

        assert "secret" in result
        assert "backup_codes" in result
        assert len(result["backup_codes"]) == 10

    def test_verify_totp(self):
        """Test TOTP verification."""
        # Setup TOTP
        result = self.mfa_provider.setup_totp("test_user")
        secret = result["secret"]

        # Generate TOTP token (this would normally come from user's authenticator app)
        import pyotp

        totp = pyotp.TOTP(secret)
        token = totp.now()

        # Verify token
        is_valid = self.mfa_provider.verify_totp("test_user", token)
        assert is_valid is True

    def test_webauthn_registration_options(self):
        """Test WebAuthn registration options."""
        options = self.mfa_provider.generate_webauthn_registration_options(
            "test_user", "testuser", "Test User"
        )

        assert "challenge" in options
        assert "rp" in options
        assert "user" in options
        assert "pubKeyCredParams" in options


class TestDataProtectionManager:
    """Test data protection functionality."""

    def setup_method(self):
        """Setup test environment."""

        # Mock encryption manager
        class MockEncryptionManager:
            def encrypt(self, data):
                return {"encrypted": str(data)}

            def decrypt(self, data):
                return data.get("encrypted", "").encode()

        self.data_protection = DataProtectionManager(MockEncryptionManager(), {})

    def test_classify_data(self):
        """Test data classification."""
        data = {"email": "user@example.com", "ssn": "123-45-6789", "amount": 1000.00}

        classification = self.data_protection.classify_data(data)

        # Should detect PII and financial data
        assert len(classification) > 0

    def test_mask_data(self):
        """Test data masking."""
        data = {"email": "user@example.com", "ssn": "123456789"}

        masked = self.data_protection.mask_data(data)

        assert masked["email"] != "user@example.com"
        assert "*" in masked["email"]

    def test_anonymize_data(self):
        """Test data anonymization."""
        data = {"email": "user@example.com", "phone": "555-123-4567"}

        anonymized = self.data_protection.anonymize_data(data)

        # Email should be anonymized but domain preserved
        assert "@" in anonymized["email"]
        assert "user" not in anonymized["email"]

    def test_data_subject_registration(self):
        """Test data subject registration."""
        subject = self.data_protection.register_data_subject(
            subject_id="user_123",
            identifiers={"email": "user@example.com"},
            consent_data={"purpose": "trading", "lawful_basis": "consent"},
        )

        assert subject.subject_id == "user_123"
        assert len(subject.consent_records) == 1

    def test_right_to_be_forgotten(self):
        """Test GDPR right to be forgotten."""
        # Register data subject
        subject = self.data_protection.register_data_subject(
            subject_id="user_123", identifiers={"email": "user@example.com"}
        )

        # Request deletion
        request_id = self.data_protection.request_right_to_be_forgotten(
            "user_123", "User withdrawal of consent"
        )

        assert request_id.startswith("gdpr_")
        assert len(subject.deletion_requests) == 1


class TestSecurityFramework:
    """Test integrated security framework."""

    def setup_method(self):
        """Setup test environment."""
        config = {
            "encryption": {"provider": "local", "algorithm": "aes_256_gcm"},
            "audit": {"log_file": "/tmp/test_framework_audit.log"},
        }
        self.framework = SecurityFramework(config)

    def test_framework_initialization(self):
        """Test framework initialization."""
        assert self.framework.encryption_manager is not None
        assert self.framework.access_control is not None
        assert self.framework.audit_logger is not None
        assert self.framework.oauth2_provider is not None
        assert self.framework.mfa_provider is not None
        assert self.framework.data_protection is not None

    def test_security_status(self):
        """Test security status reporting."""
        status = self.framework.get_security_status()

        assert "encryption" in status
        assert "access_control" in status
        assert "audit" in status
        assert "compliance" in status
        assert "timestamp" in status

    def test_user_lifecycle(self):
        """Test complete user lifecycle with security."""
        # Create user
        user = self.framework.access_control.create_user(
            "trader_001", "john_trader", "john@company.com"
        )

        # Setup MFA
        totp_setup = self.framework.mfa_provider.setup_totp("trader_001")

        # Assign role
        self.framework.access_control.assign_role("trader_001", "trader")

        # Verify permissions
        has_permission = self.framework.access_control.check_permission(
            "trader_001", Resource.ORDER_MANAGEMENT, "create", PermissionLevel.WRITE
        )

        assert has_permission
        assert totp_setup["secret"] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
