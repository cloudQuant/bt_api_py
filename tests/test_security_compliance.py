"""Security Compliance Test Suite.

Tests for the security compliance framework to ensure
proper functioning of all security components.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

import bt_api_py.security_compliance as security_compliance_module
import bt_api_py.security_compliance.core.audit_logger as audit_logger_module
import bt_api_py.security_compliance.core.encryption_manager as encryption_module
import bt_api_py.security_compliance.framework as framework_module
from bt_api_py.security_compliance.auth.mfa_provider import MFAProvider
from bt_api_py.security_compliance.auth.oauth2_provider import GrantType, OAuth2Provider, TokenType
from bt_api_py.security_compliance.core.access_control import (
    AccessControlManager,
    PermissionLevel,
    Resource,
)
from bt_api_py.security_compliance.core.audit_logger import (
    AuditError,
    AuditEvent,
    AuditLogger,
    EventType,
    SeverityLevel,
    get_audit_logger,
    initialize_audit_logger,
    log_audit_event,
)
from bt_api_py.security_compliance.core.compliance_monitor import (
    ComplianceMonitor,
    ComplianceRule,
    ComplianceStandard,
)
from bt_api_py.security_compliance.core.encryption_manager import (
    EncryptionAlgorithm,
    EncryptionError,
    EncryptionManager,
    KeyProvider,
    LocalKeyManager,
    create_key_manager,
    get_encryption_manager,
    initialize_encryption_manager,
)
from bt_api_py.security_compliance.core.identity_manager import (
    IdentityManager,
    IdentityProvider,
    UserStatus,
)
from bt_api_py.security_compliance.data.protection import DataProtectionManager
from bt_api_py.security_compliance.framework import (
    SecurityFramework,
    audit_access,
    create_security_config_from_env,
    get_security_framework,
    initialize_security_framework,
    integrate_with_bt_api,
    require_permission,
)
from bt_api_py.security_compliance.recovery.disaster_recovery import DisasterRecoveryManager


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
        self.access_control.create_role("test_role", "Test role")

        self.access_control.assign_role("test_user", "test_role")

        assert user.has_role("test_role")

    def test_permission_check(self):
        """Test permission checking."""
        self.access_control.create_user("trader_user", "trader", "trader@example.com")

        # User should have trader permissions by default
        assert self.access_control.check_permission(
            "trader_user", Resource.MARKET_DATA, "read", PermissionLevel.READ
        )

    def test_access_denied(self):
        """Test access denied scenarios."""
        self.access_control.create_user("trader_user", "trader", "trader@example.com")

        # User should not have admin permissions
        assert not self.access_control.check_permission(
            "trader_user", Resource.SECURITY_CONFIG, "write", PermissionLevel.ADMIN
        )


class TestSecurityCompliancePackage:
    """Test security_compliance package exports and defaults."""

    def test_package_exports_and_versions(self):
        assert security_compliance_module.__version__ == "1.0.0"
        assert security_compliance_module.__compliance_version__ == "2024.3"
        assert "AccessControlManager" in security_compliance_module.__all__
        assert "OAuth2Provider" in security_compliance_module.__all__
        assert "TLSManager" in security_compliance_module.__all__
        assert security_compliance_module.AccessControlManager is AccessControlManager
        assert security_compliance_module.OAuth2Provider is OAuth2Provider
        assert security_compliance_module.AuditLogger is AuditLogger
        assert security_compliance_module.ThreatDetector.__name__ == "ThreatDetector"

    def test_default_security_config_contains_expected_defaults(self):
        config = security_compliance_module.DEFAULT_SECURITY_CONFIG

        assert config["encryption"] == {
            "algorithm": "AES-256-GCM",
            "key_derivation": "PBKDF2",
            "iterations": 100000,
            "fips_compliant": True,
        }
        assert config["authentication"]["mfa_required"] is True
        assert config["authentication"]["session_timeout"] == 3600
        assert config["audit"]["log_all_access"] is True
        assert config["audit"]["log_retention_days"] == 2555
        assert config["network"]["tls_version"] == "1.3"
        assert config["network"]["cipher_suites"] == ["TLS_AES_256_GCM_SHA384"]
        assert config["compliance"] == {
            "pci_dss_level": 1,
            "sox_compliance": True,
            "gdpr_compliance": True,
            "mifid_ii_compliance": True,
        }


class TestAuditLogger:
    """Test audit logging functionality."""

    def setup_method(self):
        """Setup test environment."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_audit_"))
        self.audit_logger = AuditLogger(log_file=self.temp_dir / "test_audit.log")

    def teardown_method(self):
        """Cleanup test environment."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

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

    def test_subscribers_receive_events_and_errors_are_suppressed(self):
        received = []

        def good_callback(event):
            received.append(event.event_id)

        def bad_callback(event):
            raise RuntimeError(f"boom:{event.event_id}")

        self.audit_logger.subscribe(good_callback)
        self.audit_logger.subscribe(bad_callback)

        first_event = AuditEvent(event_type=EventType.ORDER_CREATED, user_id="user1")
        second_event = AuditEvent(event_type=EventType.ORDER_CREATED, user_id="user2")

        self.audit_logger.log_event(first_event)
        self.audit_logger.unsubscribe(good_callback)
        self.audit_logger.unsubscribe(good_callback)
        self.audit_logger.log_event(second_event)

        assert received == [first_event.event_id]

    def test_verify_log_integrity_handles_missing_and_corrupt_logs(self):
        missing_logger = AuditLogger(log_file=self.temp_dir / "missing.log")

        assert missing_logger.verify_log_integrity() == {
            "status": "no_log_file",
            "verified_events": 0,
            "violations": [],
        }

        first_event = AuditEvent(event_type=EventType.USER_LOGIN, user_id="user1")
        self.audit_logger.log_event(first_event)
        corrupt_event = AuditEvent(
            event_type=EventType.USER_LOGOUT,
            user_id="user2",
            previous_hash="wrong-hash",
        )

        with self.audit_logger.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(corrupt_event.to_dict()) + "\n")
            f.write("{bad json}\n")

        integrity = self.audit_logger.verify_log_integrity()

        assert integrity["status"] == "violations_found"
        assert integrity["verified_events"] == 2
        assert {violation["violation"] for violation in integrity["violations"]} == {
            "chain_hash_mismatch",
            "parse_error",
        }

    def test_search_events_filters_limit_and_malformed_lines(self):
        events = [
            AuditEvent(event_type=EventType.USER_LOGIN, user_id="user1", timestamp=100.0),
            AuditEvent(event_type=EventType.ORDER_CREATED, user_id="user1", timestamp=200.0),
            AuditEvent(event_type=EventType.USER_LOGIN, user_id="user2", timestamp=300.0),
        ]
        for event in events:
            self.audit_logger.log_event(event)

        with self.audit_logger.log_file.open("a", encoding="utf-8") as f:
            f.write("not-json\n")

        filtered = self.audit_logger.search_events(
            event_type=EventType.USER_LOGIN,
            start_time=150.0,
            end_time=350.0,
            limit=1,
        )

        assert len(filtered) == 1
        assert filtered[0].user_id == "user2"
        assert filtered[0].timestamp == 300.0

    def test_get_compliance_report_and_cleanup_old_events(self, monkeypatch: pytest.MonkeyPatch):
        current_time = 1_000_000.0
        old_event = AuditEvent(
            event_type=EventType.ORDER_CREATED,
            user_id="old-user",
            severity=SeverityLevel.HIGH,
            timestamp=current_time - (3 * 24 * 60 * 60),
        )
        recent_event = AuditEvent(
            event_type=EventType.ORDER_CREATED,
            user_id="recent-user",
            severity=SeverityLevel.MEDIUM,
            timestamp=current_time - 60,
        )
        gdpr_event = AuditEvent(
            event_type=EventType.DATA_EXPORTED,
            user_id="gdpr-user",
            severity=SeverityLevel.LOW,
            timestamp=current_time - 30,
        )
        for event in [old_event, recent_event, gdpr_event]:
            self.audit_logger.log_event(event)

        report = self.audit_logger.get_compliance_report(
            "SOX",
            start_date="1970-01-08T00:00:00",
            end_date="1970-01-13T23:59:59",
        )

        assert report["standard"] == "SOX"
        assert report["statistics"]["total_events"] == 2
        assert report["statistics"]["by_type"][EventType.ORDER_CREATED.value] == 2
        assert report["statistics"]["by_outcome"]["success"] == 2
        assert len(report["statistics"]["timeline"]) == 2
        assert len(report["events"]) == 2

        self.audit_logger.retention_days = 1
        monkeypatch.setattr(audit_logger_module.time, "time", lambda: current_time)

        removed = self.audit_logger.cleanup_old_events()
        remaining = self.audit_logger.search_events(limit=10)

        assert removed == 1
        assert {event.user_id for event in remaining} == {"recent-user", "gdpr-user"}

    def test_global_audit_logger_helpers(self):
        original_logger = audit_logger_module._audit_logger
        audit_logger_module._audit_logger = None
        try:
            with pytest.raises(AuditError, match="not initialized"):
                log_audit_event(EventType.USER_LOGIN, user_id="before-init")

            global_logger = initialize_audit_logger(self.temp_dir / "global_audit.log")

            assert get_audit_logger() is global_logger

            log_audit_event(EventType.USER_LOGIN, user_id="global-user")
            events = global_logger.search_events(user_id="global-user")

            assert len(events) == 1
            assert events[0].user_id == "global-user"
        finally:
            audit_logger_module._audit_logger = original_logger


class TestEncryptionManager:
    """Test encryption management."""

    def setup_method(self):
        """Setup test environment."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_encryption_"))
        key_manager = create_key_manager(
            KeyProvider.LOCAL,
            key_dir=self.temp_dir / "keys",
            master_password="password",
        )
        self.encryption_manager = EncryptionManager(key_manager)

    def teardown_method(self):
        """Cleanup test environment."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

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

    def test_local_key_manager_missing_lookup_and_rotation_error(self):
        local_manager = LocalKeyManager(self.temp_dir / "extra_keys", "password")

        assert local_manager.get_key("missing") is None

        with pytest.raises(EncryptionError, match="not found"):
            local_manager.rotate_key("missing")

    def test_local_key_manager_delete_key_removes_files(self):
        local_manager = LocalKeyManager(self.temp_dir / "delete_keys", "password")
        key = local_manager.generate_key(EncryptionAlgorithm.AES_256_GCM)

        key_file = local_manager.key_dir / f"{key.key_id}.key"
        meta_file = local_manager.key_dir / f"{key.key_id}.meta"
        assert key_file.exists()
        assert meta_file.exists()

        local_manager.delete_key(key.key_id)

        assert not key_file.exists()
        assert not meta_file.exists()

    def test_local_key_manager_rejects_unsupported_algorithm(self):
        local_manager = LocalKeyManager(self.temp_dir / "unsupported_keys", "password")

        with pytest.raises(EncryptionError, match="Unsupported algorithm"):
            local_manager.generate_key(EncryptionAlgorithm.AES_256_CBC)

    def test_encrypt_with_missing_key_id_raises(self):
        with pytest.raises(EncryptionError, match="Key missing not found"):
            self.encryption_manager.encrypt("payload", key_id="missing")

    def test_decrypt_requires_tag_and_rejects_unknown_algorithm(self):
        encrypted = self.encryption_manager.encrypt("payload")
        encrypted_without_tag = dict(encrypted)
        encrypted_without_tag["tag"] = None

        with pytest.raises(EncryptionError, match="requires tag"):
            self.encryption_manager.decrypt(encrypted_without_tag)

        encrypted_with_bad_algorithm = dict(encrypted)
        encrypted_with_bad_algorithm["algorithm"] = "unknown"

        with pytest.raises(EncryptionError, match="Unsupported algorithm"):
            self.encryption_manager.decrypt(encrypted_with_bad_algorithm)

    def test_chacha20_encrypt_decrypt_roundtrip(self):
        key = self.encryption_manager.key_manager.generate_key(
            EncryptionAlgorithm.CHACHA20_POLY1305
        )

        encrypted = self.encryption_manager.encrypt("payload", key_id=key.key_id)
        decrypted = self.encryption_manager.decrypt(encrypted)

        assert decrypted == b"payload"

    def test_rsa_key_pair_encrypt_decrypt_roundtrip(self):
        key_pair = self.encryption_manager.generate_key_pair()

        encrypted = self.encryption_manager.encrypt_with_public_key(
            "secret", key_pair["public_key"]
        )
        decrypted = self.encryption_manager.decrypt_with_private_key(
            encrypted, key_pair["private_key"]
        )

        assert decrypted == b"secret"

    def test_create_key_manager_validation_and_singleton_initialization(self):
        local_manager = create_key_manager(
            KeyProvider.LOCAL,
            key_dir=self.temp_dir / "factory_keys",
            master_password="password",
        )

        assert isinstance(local_manager, LocalKeyManager)

        with pytest.raises(EncryptionError, match="vault_url and token"):
            create_key_manager(KeyProvider.HASHICORP_VAULT)

        with pytest.raises(EncryptionError, match="Unsupported key provider"):
            create_key_manager("invalid_provider")

        original_manager = encryption_module._encryption_manager
        encryption_module._encryption_manager = None
        try:
            initialized = initialize_encryption_manager(local_manager)
            fetched = get_encryption_manager()
            second = initialize_encryption_manager(
                LocalKeyManager(self.temp_dir / "second_factory_keys", "password")
            )

            assert fetched is initialized
            assert second is initialized
        finally:
            encryption_module._encryption_manager = original_manager


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
        self.oauth2.register_client(
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
        self.oauth2.register_client(
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


class TestComplianceMonitor:
    """Test compliance monitor functionality."""

    def setup_method(self):
        self.monitor = ComplianceMonitor()

    def test_default_rules_are_initialized(self):
        rule_ids = {rule.rule_id for rule in self.monitor.rules}
        standards = {rule.standard for rule in self.monitor.rules}

        assert rule_ids == {"SOX_001", "MIFID_001", "PCI_001"}
        assert standards == {
            ComplianceStandard.SOX,
            ComplianceStandard.MIFID_II,
            ComplianceStandard.PCI_DSS,
        }

    def test_run_compliance_check_filters_standard_and_handles_errors(self):
        self.monitor.rules.append(
            ComplianceRule(
                rule_id="SOX_FAIL",
                standard=ComplianceStandard.SOX,
                description="failing rule",
                check_function=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                severity="critical",
            )
        )

        sox_results = self.monitor.run_compliance_check(ComplianceStandard.SOX)

        assert set(sox_results) == {"SOX_001", "SOX_FAIL"}
        assert sox_results["SOX_001"]["passed"] is True
        assert sox_results["SOX_FAIL"]["passed"] is False
        assert sox_results["SOX_FAIL"]["error"] == "boom"
        assert sox_results["SOX_FAIL"]["severity"] == "critical"

    def test_generate_compliance_report_groups_results(self):
        self.monitor.rules = [
            ComplianceRule(
                rule_id="SOX_OK",
                standard=ComplianceStandard.SOX,
                description="ok",
                check_function=lambda: True,
            ),
            ComplianceRule(
                rule_id="SOX_BAD",
                standard=ComplianceStandard.SOX,
                description="bad",
                check_function=lambda: False,
            ),
            ComplianceRule(
                rule_id="GDPR_OK",
                standard=ComplianceStandard.GDPR,
                description="gdpr",
                check_function=lambda: True,
                severity="medium",
            ),
        ]

        report = self.monitor.generate_compliance_report()

        assert report["summary"] == {
            "total_rules": 3,
            "passed": 2,
            "failed": 1,
            "compliance_percentage": pytest.approx(66.66666666666666),
        }
        assert report["by_standard"][ComplianceStandard.SOX.value]["total"] == 2
        assert report["by_standard"][ComplianceStandard.SOX.value]["failed"] == 1
        assert report["by_standard"][ComplianceStandard.GDPR.value]["passed"] == 1

    def test_generate_compliance_report_handles_no_rules(self):
        self.monitor.rules = []

        report = self.monitor.generate_compliance_report()

        assert report["summary"] == {
            "total_rules": 0,
            "passed": 0,
            "failed": 0,
            "compliance_percentage": 0,
        }
        assert report["results"] == {}
        assert report["by_standard"] == {}


class TestIdentityManager:
    """Test identity manager functionality."""

    def setup_method(self):
        self.manager = IdentityManager(
            {
                "ldap": {"users": {"ldap_user": {}}},
                "saml": {"issuer": "https://idp.example.com"},
            }
        )

    def test_create_and_authenticate_local_identity(self):
        identity = self.manager.create_identity(
            "alice",
            "alice@example.com",
            "Alice Trader",
            password="secret123",
            department="trading",
        )

        assert self.manager.verify_password(identity.identity_id, "secret123") is True
        assert self.manager.verify_password(identity.identity_id, "wrong") is False
        assert self.manager.verify_password("missing", "secret123") is False

        authenticated = self.manager.authenticate_user("alice", "secret123")

        assert authenticated is identity
        assert authenticated is not None
        assert authenticated.last_login is not None
        assert identity.attributes["department"] == "trading"

    def test_external_provider_authentication_and_lookup(self):
        ldap_identity = self.manager.create_identity(
            "ldap_user",
            "ldap@example.com",
            "LDAP User",
            provider=IdentityProvider.LDAP,
        )
        saml_identity = self.manager.create_identity(
            "saml_user",
            "saml@example.com",
            "SAML User",
            provider=IdentityProvider.SAML,
        )

        assert IdentityProvider.LDAP in self.manager._providers
        assert IdentityProvider.SAML in self.manager._providers
        assert (
            self.manager.authenticate_user("ldap_user", "anything", IdentityProvider.LDAP)
            is ldap_identity
        )
        assert (
            self.manager.authenticate_user("saml_user", "anything", IdentityProvider.SAML) is None
        )
        assert self.manager.get_identity_by_username("ldap_user") is ldap_identity
        assert self.manager.get_identity(ldap_identity.identity_id) is ldap_identity
        assert self.manager.get_identity(saml_identity.identity_id) is saml_identity

    def test_group_membership_permissions_and_search_filters(self):
        identity = self.manager.create_identity(
            "bob",
            "bob@example.com",
            "Bob Ops",
            password="secret123",
        )
        group = self.manager.create_group("Ops", "Operations team", {"traders"})
        group.attributes["permissions"] = {"orders:read", "orders:write"}

        assert self.manager.add_user_to_group("missing", group.group_id) is False
        assert self.manager.add_user_to_group(identity.identity_id, group.group_id) is True
        assert group.group_id in self.manager.get_user_groups(identity.identity_id)
        assert identity.identity_id in self.manager.get_group_members(group.group_id)

        assert (
            self.manager.update_identity(
                identity.identity_id,
                department="operations",
                custom_flag=True,
            )
            is True
        )
        assert self.manager.update_identity("missing", department="ignored") is False

        permissions = self.manager.get_user_permissions(identity.identity_id)
        results = self.manager.search_identities(
            query="bob",
            group_id=group.group_id,
            department="operations",
            status=UserStatus.ACTIVE,
            limit=1,
        )

        assert set(permissions["direct_permissions"]) == {"orders:read", "orders:write"}
        assert permissions["groups"] == [group.group_id]
        assert results == [identity]

        assert self.manager.remove_user_from_group(identity.identity_id, group.group_id) is True
        assert self.manager.remove_user_from_group(identity.identity_id, "missing") is False

    def test_status_transitions_sync_metadata_and_summary(self):
        local_identity = self.manager.create_identity(
            "carol",
            "carol@example.com",
            "Carol Admin",
            password="secret123",
        )
        ldap_identity = self.manager.create_identity(
            "dave",
            "dave@example.com",
            "Dave LDAP",
            provider=IdentityProvider.LDAP,
        )

        assert self.manager.suspend_user(local_identity.identity_id, "policy") is True
        assert local_identity.status == UserStatus.SUSPENDED
        assert self.manager.authenticate_user("carol", "secret123") is None

        assert self.manager.activate_user(local_identity.identity_id) is True
        assert local_identity.status == UserStatus.ACTIVE
        assert "suspension_reason" not in local_identity.attributes

        assert self.manager.lock_user(ldap_identity.identity_id, "security") is True
        assert ldap_identity.status == UserStatus.LOCKED
        assert self.manager.lock_user("missing", "security") is False
        assert self.manager.suspend_user("missing", "policy") is False
        assert self.manager.activate_user("missing") is False

        ldap_sync = self.manager.sync_with_external_provider(IdentityProvider.LDAP)
        saml_sync = self.manager.sync_with_external_provider(IdentityProvider.SAML)
        local_sync = self.manager.sync_with_external_provider(IdentityProvider.LOCAL)
        metadata = self.manager.generate_saml_metadata()
        summary = self.manager.get_identity_summary()

        assert ldap_sync["provider"] == IdentityProvider.LDAP.value
        assert saml_sync["provider"] == IdentityProvider.SAML.value
        assert local_sync == {
            "provider": IdentityProvider.LOCAL.value,
            "created": 0,
            "updated": 0,
            "errors": [],
        }
        assert "EntityDescriptor" in metadata
        assert summary["total_identities"] == 2
        assert summary["by_status"][UserStatus.ACTIVE.value] == 1
        assert summary["by_status"][UserStatus.LOCKED.value] == 1
        assert summary["by_provider"][IdentityProvider.LOCAL.value] == 1
        assert summary["by_provider"][IdentityProvider.LDAP.value] == 1


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


class TestDisasterRecoveryManager:
    """Test disaster recovery functionality."""

    def setup_method(self):
        self.manager = DisasterRecoveryManager()

    def test_default_configs_and_status(self):
        status = self.manager.get_recovery_status()

        assert status == {
            "status": "normal",
            "backup_configs": 1,
            "recovery_plans": 1,
        }
        assert "database_daily" in self.manager._backups
        assert "primary_recovery" in self.manager._recovery_plans

    def test_create_and_initiate_backup(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "bt_api_py.security_compliance.recovery.disaster_recovery.time.time",
            lambda: 12345.0,
        )

        backup = self.manager.create_backup_config(
            "Hourly Backup",
            "hourly",
            7,
            ["s3://backup/hourly"],
        )

        started = self.manager.initiate_backup(backup.backup_id)
        missing = self.manager.initiate_backup("missing")

        assert backup.backup_id == "backup_12345"
        assert started == {
            "success": True,
            "backup_id": "backup_12345",
            "started_at": 12345.0,
            "locations": ["s3://backup/hourly"],
        }
        assert missing == {
            "success": False,
            "error": "Backup configuration not found",
        }

    def test_create_and_execute_recovery_plan(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "bt_api_py.security_compliance.recovery.disaster_recovery.time.time",
            lambda: 45678.0,
        )

        plan = self.manager.create_recovery_plan(
            "Regional Failover",
            "Recover from regional outage",
            ["region_outage"],
            ["switch traffic", "restore services"],
            2,
            1,
            [{"role": "Lead", "contact": "lead@example.com"}],
        )

        started = self.manager.initiate_recovery(plan.plan_id)
        tested = self.manager.test_recovery_plan(plan.plan_id)
        missing_start = self.manager.initiate_recovery("missing")
        missing_test = self.manager.test_recovery_plan("missing")

        assert plan.plan_id == "plan_45678"
        assert started["success"] is True
        assert started["plan_id"] == "plan_45678"
        assert started["recovery_steps"] == ["switch traffic", "restore services"]
        assert started["contacts"] == [{"role": "Lead", "contact": "lead@example.com"}]
        assert started["initiated_at"] == 45678.0
        assert self.manager.get_recovery_status()["status"] == "recovering"
        assert tested["test_results"]["steps_tested"] == 2
        assert tested["test_results"]["steps_passed"] == 2
        assert tested["test_results"]["test_date"] == 45678.0
        assert missing_start == {
            "success": False,
            "error": "Recovery plan not found",
        }
        assert missing_test == {
            "success": False,
            "error": "Recovery plan not found",
        }

    def test_generate_recovery_report(self):
        report = self.manager.generate_recovery_report()

        assert report["current_status"] == "normal"
        assert report["backup_configurations"][0] == {
            "backup_id": "database_daily",
            "name": "Daily Database Backup",
            "frequency": "daily",
            "retention_days": 30,
        }
        assert report["recovery_plans"][0]["plan_id"] == "primary_recovery"
        assert report["recovery_plans"][0]["rto_hours"] == 4
        assert "server_failure" in report["recovery_plans"][0]["disaster_types"]


class TestSecurityFramework:
    """Test integrated security framework."""

    def setup_method(self):
        """Setup test environment."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_framework_audit_"))
        config = {
            "encryption": {"provider": "local", "algorithm": "aes_256_gcm"},
            "audit": {"log_file": str(self.temp_dir / "test_framework_audit.log")},
        }
        self.framework = SecurityFramework(config)

    def teardown_method(self):
        """Cleanup test environment."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

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
        self.framework.access_control.create_user("trader_001", "john_trader", "john@company.com")

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


class TestSecurityFrameworkHelpers:
    """Test security framework helpers."""

    def teardown_method(self):
        framework_module._security_framework = None

    def test_create_security_config_from_env(self, monkeypatch: pytest.MonkeyPatch):
        env_values = {
            "SECURITY_KEY_PROVIDER": "local",
            "SECURITY_KEY_DIR": "/tmp/security_keys",
            "SECURITY_MASTER_PASSWORD": "master-secret",
            "AWS_REGION": "eu-west-1",
            "VAULT_URL": "https://vault.example.com",
            "VAULT_TOKEN": "vault-token",
            "ENCRYPTION_ALGORITHM": "aes_256_gcm",
            "AUTO_KEY_ROTATION": "true",
            "AUDIT_LOG_FILE": "/tmp/audit.log",
            "AUDIT_RETENTION_DAYS": "30",
            "AUDIT_REAL_TIME": "false",
            "OAUTH2_ISSUER_URL": "https://issuer.example.com",
            "OAUTH2_TOKEN_LIFETIME": "7200",
            "OAUTH2_REFRESH_TOKEN_LIFETIME": "86400",
            "OAUTH2_ENABLE_PKCE": "false",
            "OAUTH2_ENABLE_TOKEN_ROTATION": "false",
            "MFA_ISSUER_NAME": "bt-api-tests",
            "MFA_TOTP_WINDOW": "3",
            "MFA_HOTP_COUNTER_START": "5",
            "MFA_BACKUP_CODES_COUNT": "12",
            "MFA_RP_ID": "example.com",
            "MFA_RP_NAME": "Example RP",
            "DATA_MASKING_ENABLED": "false",
            "DATA_RETENTION_DAYS": "90",
            "GDPR_COMPLIANCE": "false",
            "TLS_VERSION": "1.2",
            "TLS_CIPHER_SUITES": "TLS_A,TLS_B",
            "TLS_CERT_VALIDATION": "relaxed",
            "SECURITY_MONITORING": "false",
            "ALERT_FAILED_LOGINS": "7",
            "ALERT_UNAUTHORIZED_ACCESS": "9",
            "BACKUP_ENABLED": "false",
            "BACKUP_FREQUENCY": "hourly",
            "BACKUP_RETENTION_DAYS": "14",
            "PCI_DSS_LEVEL": "2",
            "SOX_COMPLIANCE": "false",
            "MIFID_II_COMPLIANCE": "false",
            "SECURITY_ENCRYPTION_KEY": "enc-key",
        }
        for key, value in env_values.items():
            monkeypatch.setenv(key, value)

        config = create_security_config_from_env()

        assert config["encryption"]["provider"] == "local"
        assert config["encryption"]["provider_config"]["key_dir"] == "/tmp/security_keys"
        assert config["encryption"]["auto_rotate"] is True
        assert config["audit"]["retention_days"] == 30
        assert config["audit"]["real_time"] is False
        assert config["oauth2"]["token_lifetime"] == 7200
        assert config["oauth2"]["enable_pkce"] is False
        assert config["mfa"]["backup_codes_count"] == 12
        assert config["data_protection"]["enable_data_masking"] is False
        assert config["tls"]["cipher_suites"] == ["TLS_A", "TLS_B"]
        assert config["monitoring"]["alert_thresholds"]["failed_logins"] == 7
        assert config["disaster_recovery"]["backup_enabled"] is False
        assert config["compliance"]["pci_dss_level"] == 2
        assert config["compliance"]["gdpr_compliance"] is False
        assert config["encryption_key"] == "enc-key"

    def test_initialize_and_get_security_framework(self, monkeypatch: pytest.MonkeyPatch):
        config = {
            "encryption": {"provider": "local", "provider_config": {"key_dir": "./tmp_keys"}},
            "audit": {"log_file": "./tmp_audit.log"},
        }

        initialized = initialize_security_framework(config)
        fetched = get_security_framework()

        assert fetched is initialized

        monkeypatch.setattr(
            framework_module,
            "create_security_config_from_env",
            lambda: config,
        )
        initialized_from_env = initialize_security_framework()

        assert initialized_from_env is get_security_framework()

    def test_integrate_with_bt_api_requires_framework(self):
        class DummyBtApi:
            def create_feed(self, *args, **kwargs):
                return {"args": args, "kwargs": kwargs}

        framework_module._security_framework = None

        with pytest.raises(RuntimeError, match="not initialized"):
            integrate_with_bt_api(DummyBtApi())

    def test_integrate_with_bt_api_wraps_create_feed(self):
        class DummyAccessControl:
            def __init__(self):
                self.calls = []

            def require_permission(self, user_id, resource, action, level):
                self.calls.append((user_id, resource, action, level))

        class DummyAuditLogger:
            def __init__(self):
                self.events = []

            def log_event(self, event):
                self.events.append(event)

        class DummyFramework:
            def __init__(self):
                self.access_control = DummyAccessControl()
                self.audit_logger = DummyAuditLogger()

        class DummyBtApi:
            def __init__(self):
                self.calls = []

            def create_feed(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "created"

        bt_api = DummyBtApi()
        framework_module._security_framework = DummyFramework()

        secured = integrate_with_bt_api(bt_api)
        result = secured.create_feed("BINANCE", market="spot", user_id="user-1")

        assert result == "created"
        assert secured.security is framework_module._security_framework
        assert framework_module._security_framework.access_control.calls == [
            ("user-1", Resource.EXCHANGE_CONFIG, "create", PermissionLevel.WRITE)
        ]
        assert len(framework_module._security_framework.audit_logger.events) == 1
        assert framework_module._security_framework.audit_logger.events[0].action == "create"
        assert bt_api.calls == [(("BINANCE",), {"market": "spot"})]

    def test_require_permission_decorator(self):
        calls = []

        class DummyAccessControl:
            def require_permission(self, user_id, resource, action, level):
                calls.append((user_id, resource, action, level))

        class DummyFramework:
            def __init__(self):
                self.access_control = DummyAccessControl()

        @require_permission(Resource.EXCHANGE_CONFIG, "update", PermissionLevel.ADMIN)
        def secured_action(*, user_id=None):
            return "ok"

        framework_module._security_framework = None
        assert secured_action(user_id="user-1") == "ok"
        assert calls == []

        framework_module._security_framework = DummyFramework()
        assert secured_action(user_id="user-2") == "ok"
        assert secured_action() == "ok"
        assert calls == [("user-2", Resource.EXCHANGE_CONFIG, "update", PermissionLevel.ADMIN)]

    def test_audit_access_decorator_success_and_failure(self):
        class DummyAuditLogger:
            def __init__(self):
                self.events = []

            def log_event(self, event):
                self.events.append(event)

        class DummyFramework:
            def __init__(self):
                self.audit_logger = DummyAuditLogger()

        @audit_access(EventType.CONFIG_CHANGE, SeverityLevel.MEDIUM)
        def successful(*, user_id=None):
            return "done"

        @audit_access(EventType.CONFIG_CHANGE, SeverityLevel.MEDIUM)
        def failing(*, user_id=None):
            raise ValueError("boom")

        framework_module._security_framework = None
        assert successful(user_id="anon") == "done"

        framework_module._security_framework = DummyFramework()
        assert successful(user_id="user-1") == "done"

        with pytest.raises(ValueError, match="boom"):
            failing(user_id="user-2")

        events = framework_module._security_framework.audit_logger.events
        assert [event.action for event in events] == ["execute", "success", "execute", "error"]
        assert events[0].user_id == "user-1"
        assert events[1].outcome == "success"
        assert events[3].severity == SeverityLevel.HIGH


class TestDataProtectionManagerExtended:
    """Extended tests for DataProtectionManager data classification and GDPR operations."""

    def test_init_classifications_and_patterns(self):
        """Test default classifications and anonymization patterns initialization."""
        from bt_api_py.security_compliance.data.protection import (
            DataProtectionManager,
            SensitiveDataType,
        )

        manager = DataProtectionManager(None, {})

        # Check classifications
        assert SensitiveDataType.PII in manager._classifications
        assert SensitiveDataType.FINANCIAL in manager._classifications
        assert SensitiveDataType.TRANSACTION in manager._classifications

        # Check patterns
        assert "email" in manager._anonymization_patterns
        assert "phone" in manager._anonymization_patterns
        assert "credit_card" in manager._anonymization_patterns

    def test_classify_data_with_email_and_transaction(self):
        """Test data classification identifies PII and transaction data."""
        from bt_api_py.security_compliance.data.protection import (
            DataProtectionManager,
            SensitiveDataType,
        )

        manager = DataProtectionManager(None, {})

        data = {
            "email": "user@example.com",
            "amount": 100.0,
            "currency": "USD",
        }

        classification = manager.classify_data(data)

        assert classification.get(SensitiveDataType.PII) is True
        assert classification.get(SensitiveDataType.TRANSACTION) is True

    def test_classify_data_with_credit_card(self):
        """Test data classification identifies financial and PCI_DSS data."""
        from bt_api_py.security_compliance.data.protection import (
            DataProtectionManager,
            SensitiveDataType,
        )

        manager = DataProtectionManager(None, {})

        data = {"card": "1234-5678-9012-3456"}

        classification = manager.classify_data(data)

        assert classification.get(SensitiveDataType.FINANCIAL) is True
        assert classification.get(SensitiveDataType.PCI_DSS) is True

    def test_mask_data_string_full_and_partial(self):
        """Test string masking with full and partial levels."""
        from bt_api_py.security_compliance.data.protection import DataProtectionManager

        manager = DataProtectionManager(None, {})

        # Full mask
        assert manager.mask_data("secret", mask_level="full") == "******"

        # Partial mask - long string
        assert manager.mask_data("abcdefgh", mask_level="partial") == "ab****gh"

        # Partial mask - short string
        assert manager.mask_data("ab", mask_level="partial") == "**"

    def test_mask_data_dict_and_list(self):
        """Test masking for dict and list data structures."""
        from bt_api_py.security_compliance.data.protection import DataProtectionManager

        manager = DataProtectionManager(None, {})

        # Dict - "value123" (8 chars) -> "va****23" (2 + 4 asterisks + 2)
        masked_dict = manager.mask_data({"key": "value123"}, mask_level="partial")
        assert masked_dict["key"] == "va****23"

        # List
        masked_list = manager.mask_data(["abc", "xyz"], mask_level="full")
        assert masked_list == ["***", "***"]

    def test_mask_data_email_level(self):
        """Test email-specific masking preserves domain."""
        from bt_api_py.security_compliance.data.protection import DataProtectionManager

        manager = DataProtectionManager(None, {})

        masked = manager.mask_data("user@example.com", mask_level="email")
        assert masked.endswith("@example.com")
        assert masked.startswith("us")

    def test_anonymize_data_email_and_phone(self):
        """Test data anonymization for email and phone."""
        from bt_api_py.security_compliance.data.protection import DataProtectionManager

        manager = DataProtectionManager(None, {})

        data = {
            "email": "test@example.com",
            "phone": "123-456-7890",
        }

        anonymized = manager.anonymize_data(data)

        assert anonymized["email"] != "test@example.com"
        assert anonymized["phone"].startswith("***-***-")

    def test_anonymize_data_credit_card_and_ssn(self):
        """Test data anonymization for credit card and SSN."""
        from bt_api_py.security_compliance.data.protection import DataProtectionManager

        manager = DataProtectionManager(None, {})

        data = {
            "card": "1234-5678-9012-3456",
            "ssn": "123-45-6789",
        }

        anonymized = manager.anonymize_data(data)

        assert anonymized["card"].startswith("****-****-****-")
        assert anonymized["ssn"].startswith("***-**-")

    def test_register_data_subject_with_consent(self, monkeypatch):
        """Test data subject registration with consent data."""
        from bt_api_py.security_compliance.data.protection import (
            DataProtectionManager,
            DataSubject,
        )

        monkeypatch.setattr("time.time", lambda: 1000.0)

        manager = DataProtectionManager(None, {})

        subject = manager.register_data_subject(
            subject_id="user-1",
            identifiers={"email": "user@example.com"},
            consent_data={"purpose": "marketing", "lawful_basis": "consent"},
        )

        assert isinstance(subject, DataSubject)
        assert subject.subject_id == "user-1"
        assert subject.identifiers == {"email": "user@example.com"}
        assert len(subject.consent_records) == 1
        assert manager._data_subjects["user-1"] is subject

    def test_record_consent_and_withdraw(self, monkeypatch):
        """Test consent recording and withdrawal."""
        from bt_api_py.security_compliance.data.protection import DataProtectionManager

        monkeypatch.setattr("time.time", lambda: 2000.0)

        manager = DataProtectionManager(None, {})

        # Register subject
        manager.register_data_subject("user-2", {"email": "u2@example.com"})

        # Record consent
        manager.record_consent("user-2", {"agreed": True}, "analytics")

        subject = manager._data_subjects["user-2"]
        assert len(subject.consent_records) == 1
        assert subject.consent_records[0]["purpose"] == "analytics"

        # Withdraw consent
        manager.withdraw_consent("user-2", purpose="analytics")
        assert subject.consent_records[0]["withdrawn"] is True

        # Withdraw all
        manager.record_consent("user-2", {"agreed": True}, "marketing")
        manager.withdraw_consent("user-2")  # Withdraw all
        assert all(r["withdrawn"] for r in subject.consent_records)

    def test_withdraw_consent_nonexistent_subject(self):
        """Test withdrawing consent for non-existent subject raises error."""
        from bt_api_py.security_compliance.data.protection import (
            DataProtectionError,
            DataProtectionManager,
        )

        manager = DataProtectionManager(None, {})

        with pytest.raises(DataProtectionError, match="not found"):
            manager.withdraw_consent("nonexistent")

    def test_request_right_to_be_forgotten(self, monkeypatch):
        """Test GDPR right to be forgotten request."""
        from bt_api_py.security_compliance.data.protection import DataProtectionManager

        monkeypatch.setattr("time.time", lambda: 3000.0)

        manager = DataProtectionManager(None, {})

        manager.register_data_subject("user-3", {"email": "u3@example.com"})

        request_id = manager.request_right_to_be_forgotten("user-3", "User request")

        assert request_id.startswith("gdpr_3000_")

        subject = manager._data_subjects["user-3"]
        assert len(subject.deletion_requests) == 1
        assert subject.deletion_requests[0]["status"] == "pending"

    def test_process_data_deletion(self, monkeypatch):
        """Test processing deletion request."""
        from bt_api_py.security_compliance.data.protection import (
            DataProtectionError,
            DataProtectionManager,
        )

        monkeypatch.setattr("time.time", lambda: 4000.0)

        manager = DataProtectionManager(None, {})

        manager.register_data_subject("user-4", {"email": "u4@example.com"})
        request_id = manager.request_right_to_be_forgotten("user-4", "Test")

        result = manager.process_data_deletion(request_id)

        assert result["status"] == "completed"
        assert result["subject_id"] == "user-4"
        assert "user-4" not in manager._data_subjects

        # Non-existent request
        with pytest.raises(DataProtectionError, match="not found"):
            manager.process_data_deletion("gdpr_nonexistent")

    def test_check_retention_policies(self):
        """Test retention policy checking."""
        from bt_api_py.security_compliance.data.protection import DataProtectionManager

        manager = DataProtectionManager(None, {})

        expired = manager.check_retention_policies()

        assert "personal_identifiable_information" in expired
        assert "financial_data" in expired

    def test_generate_data_protection_report(self):
        """Test comprehensive data protection report generation."""
        from bt_api_py.security_compliance.data.protection import DataProtectionManager

        manager = DataProtectionManager(None, {})

        manager.register_data_subject("user-5", {"email": "u5@example.com"})
        manager.record_consent("user-5", {"agreed": True}, "analytics")

        report = manager.generate_data_protection_report()

        assert report["data_subjects_count"] == 1
        assert report["consent_records"] == 1
        assert report["deletion_requests"] == 0
        assert "retention_policies" in report
        assert report["gdpr_compliance"]["right_to_access"] is True
        assert report["pci_dss_compliance"]["network_security"] is True
        assert report["encryption_enabled"] is False


class TestTLSManager:
    """Tests for TLSManager SSL context and certificate validation."""

    def test_default_config_initialization(self):
        """Test TLSManager with default configuration."""
        from bt_api_py.security_compliance.network.tls_manager import TLSManager

        manager = TLSManager({})

        assert manager.version == "1.3"
        assert manager.cipher_suites == ["TLS_AES_256_GCM_SHA384"]
        assert manager.certificate_validation == "strict"

    def test_custom_config_initialization(self):
        """Test TLSManager with custom configuration."""
        from bt_api_py.security_compliance.network.tls_manager import TLSManager

        manager = TLSManager(
            {
                "version": "1.2",
                "cipher_suites": [],
                "certificate_validation": "none",
            }
        )

        assert manager.version == "1.2"
        assert manager.cipher_suites == []
        assert manager.certificate_validation == "none"

    def test_get_ssl_context_tls_1_3(self):
        """Test SSL context creation with TLS 1.3."""
        import ssl

        from bt_api_py.security_compliance.network.tls_manager import TLSManager

        # Use empty cipher_suites to avoid platform-specific cipher errors
        manager = TLSManager({"version": "1.3", "cipher_suites": []})
        context = manager.get_ssl_context()

        assert context.minimum_version == ssl.TLSVersion.TLSv1_3
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True

    def test_get_ssl_context_tls_1_2(self):
        """Test SSL context creation with TLS 1.2."""
        import ssl

        from bt_api_py.security_compliance.network.tls_manager import TLSManager

        manager = TLSManager({"version": "1.2", "cipher_suites": []})
        context = manager.get_ssl_context()

        assert context.minimum_version == ssl.TLSVersion.TLSv1_2

    def test_validate_certificate_invalid_path(self):
        """Test certificate validation with non-existent file."""
        from bt_api_py.security_compliance.network.tls_manager import TLSManager

        manager = TLSManager({})
        result = manager.validate_certificate("/nonexistent/path/cert.pem")

        assert result is False


class TestSecurityMonitoring:
    """Tests for SecurityMonitoring alert and handler management."""

    def test_create_alert_and_handler_notification(self, monkeypatch):
        """Test alert creation with handler notification."""
        from bt_api_py.security_compliance.monitoring.security_monitoring import (
            AlertSeverity,
            SecurityAlert,
            SecurityMonitoring,
        )

        # Freeze time for deterministic alert_id
        monkeypatch.setattr("time.time", lambda: 12345.0)

        # Handler that collects alerts
        collected = []

        def handler(alert: SecurityAlert):
            collected.append(alert)

        # Use None for audit_logger to avoid broken import path in source
        monitor = SecurityMonitoring(None)
        monitor.add_alert_handler(handler)

        # Create alert
        alert = monitor.create_alert(
            title="Test Alert",
            description="Test description",
            severity=AlertSeverity.HIGH,
            source="test_source",
            extra_info="details",
        )

        assert alert.alert_id == "sec_12345_0"
        assert alert.title == "Test Alert"
        assert alert.severity == AlertSeverity.HIGH
        assert alert.source == "test_source"
        assert alert.details == {"extra_info": "details"}
        assert alert.acknowledged is False
        assert alert.resolved is False
        assert len(collected) == 1
        assert collected[0] is alert

    def test_get_alerts_filtering_and_sorting(self, monkeypatch):
        """Test alert filtering by severity and acknowledged status."""
        from bt_api_py.security_compliance.monitoring.security_monitoring import (
            AlertSeverity,
            SecurityMonitoring,
        )

        monkeypatch.setattr("time.time", lambda: 100.0)

        monitor = SecurityMonitoring(None)

        # Create multiple alerts with different severities
        monitor.create_alert("A1", "desc", AlertSeverity.LOW, "s1")
        monitor.create_alert("A2", "desc", AlertSeverity.HIGH, "s2")
        monitor.create_alert("A3", "desc", AlertSeverity.CRITICAL, "s3")
        monitor.create_alert("A4", "desc", AlertSeverity.LOW, "s4")

        # Acknowledge one
        monitor.acknowledge_alert("sec_100_0")

        # Filter by severity
        high_alerts = monitor.get_alerts(severity=AlertSeverity.HIGH)
        assert len(high_alerts) == 1
        assert high_alerts[0].title == "A2"

        # Filter by acknowledged status
        unacked = monitor.get_alerts(acknowledged=False)
        assert len(unacked) == 3

        acked = monitor.get_alerts(acknowledged=True)
        assert len(acked) == 1
        assert acked[0].title == "A1"

        # Test limit
        all_alerts = monitor.get_alerts(limit=2)
        assert len(all_alerts) == 2

    def test_acknowledge_and_resolve_alert(self, monkeypatch):
        """Test alert acknowledgment and resolution."""
        from bt_api_py.security_compliance.monitoring.security_monitoring import (
            AlertSeverity,
            SecurityMonitoring,
        )

        monkeypatch.setattr("time.time", lambda: 200.0)

        monitor = SecurityMonitoring(None)
        alert = monitor.create_alert("Test", "desc", AlertSeverity.MEDIUM, "src")

        assert alert.acknowledged is False
        assert alert.resolved is False

        # Acknowledge
        result = monitor.acknowledge_alert("sec_200_0")
        assert result is True
        assert alert.acknowledged is True

        # Non-existent alert
        assert monitor.acknowledge_alert("nonexistent") is False

        # Resolve
        result = monitor.resolve_alert("sec_200_0")
        assert result is True
        assert alert.resolved is True

        assert monitor.resolve_alert("nonexistent") is False

    def test_get_monitoring_summary(self, monkeypatch):
        """Test monitoring summary statistics."""
        from bt_api_py.security_compliance.monitoring.security_monitoring import (
            AlertSeverity,
            SecurityMonitoring,
        )

        # Use fixed time, then advance for "recent_24h" test
        monkeypatch.setattr("time.time", lambda: 1000.0)

        monitor = SecurityMonitoring(None)

        # Create alerts with different severities
        monitor.create_alert("C1", "desc", AlertSeverity.CRITICAL, "s1")
        monitor.create_alert("H1", "desc", AlertSeverity.HIGH, "s2")
        monitor.create_alert("H2", "desc", AlertSeverity.HIGH, "s3")
        monitor.create_alert("L1", "desc", AlertSeverity.LOW, "s4")

        summary = monitor.get_monitoring_summary()

        assert summary["total_alerts"] == 4
        assert summary["unacknowledged"] == 4
        assert summary["critical"] == 1
        assert summary["high"] == 2
        assert summary["recent_24h"] == 4  # All within 24h of frozen time

    def test_handler_errors_suppressed(self, monkeypatch):
        """Test that handler exceptions don't break alert creation."""
        from bt_api_py.security_compliance.monitoring.security_monitoring import (
            AlertSeverity,
            SecurityMonitoring,
        )

        monkeypatch.setattr("time.time", lambda: 300.0)

        # Handler that raises
        def bad_handler(alert):
            raise RuntimeError("handler error")

        monitor = SecurityMonitoring(None)
        monitor.add_alert_handler(bad_handler)

        # Should not raise
        alert = monitor.create_alert("Test", "desc", AlertSeverity.LOW, "src")
        assert alert.alert_id == "sec_300_0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
