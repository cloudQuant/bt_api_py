"""Regression tests for code quality batch v6.

Covers:
- audit_logger: Path.open() with encoding="utf-8" for all text-mode file ops,
  NamedTemporaryFile encoding
- ml_base: Path(file_path).open() in save_model / load_model
- encryption_manager: key_file.open() / metadata_file.open() for Path objects
- oauth2_provider: Path(key_path).open("rb") for key loading
"""

import ast
import inspect
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# audit_logger: all text-mode open() calls should use Path.open + encoding
# ---------------------------------------------------------------------------


class TestAuditLoggerPathOpen:
    """Verify audit_logger uses self.log_file.open() with encoding."""

    def _get_source(self):
        from bt_api_py.security_compliance.core import audit_logger

        return inspect.getsource(audit_logger.AuditLogger)

    def test_no_builtin_open_on_log_file(self):
        """SecureAuditLogger should not call open(self.log_file, ...) directly."""
        source = self._get_source()
        # Should NOT have open(self.log_file ...) pattern
        assert "open(self.log_file" not in source

    def test_log_file_open_has_encoding(self):
        """All self.log_file.open() calls for text mode should include encoding."""
        source = self._get_source()
        # Every self.log_file.open( call should have encoding
        import re

        calls = re.findall(r"self\.log_file\.open\([^)]*\)", source)
        assert len(calls) >= 4, f"Expected at least 4 Path.open() calls, got {len(calls)}"
        for call in calls:
            assert "encoding" in call, f"Missing encoding in: {call}"

    def test_named_temporary_file_has_encoding(self):
        """NamedTemporaryFile text-mode should include encoding='utf-8'."""
        source = self._get_source()
        assert 'encoding="utf-8"' in source

    def test_log_and_verify_roundtrip(self):
        """Log an event and verify integrity reads it back correctly."""
        from bt_api_py.security_compliance.core.audit_logger import (
            AuditEvent,
            AuditLogger,
            EventType,
        )

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit.log"
            logger = AuditLogger(log_file=log_path)

            event = AuditEvent(
                event_type=EventType.USER_LOGIN,
                user_id="test_user",
                details={"ip": "127.0.0.1"},
            )
            logger.log_event(event)

            # Verify file exists and is valid UTF-8
            content = log_path.read_text(encoding="utf-8")
            assert "test_user" in content

            # Verify integrity
            result = logger.verify_log_integrity()
            assert result["status"] == "verified"
            assert result["verified_events"] == 1

    def test_search_events_roundtrip(self):
        """search_events should find logged events."""
        from bt_api_py.security_compliance.core.audit_logger import (
            AuditEvent,
            AuditLogger,
            EventType,
        )

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit_search.log"
            logger = AuditLogger(log_file=log_path)

            event = AuditEvent(
                event_type=EventType.ORDER_CREATED,
                user_id="trader1",
                details={"symbol": "BTCUSDT"},
            )
            logger.log_event(event)

            results = logger.search_events(event_type=EventType.ORDER_CREATED)
            assert len(results) == 1
            assert results[0].user_id == "trader1"


# ---------------------------------------------------------------------------
# ml_base: save_model / load_model should use Path().open()
# ---------------------------------------------------------------------------


class TestMLBasePathOpen:
    """Verify ml_base uses Path(file_path).open() for model persistence."""

    def test_save_model_uses_path_open(self):
        """save_model should use Path(file_path).open, not built-in open."""
        from bt_api_py.risk_management.ml_models.ml_base import BaseMLModel

        source = inspect.getsource(BaseMLModel.save_model)
        assert "Path(file_path).open(" in source
        assert "open(file_path" not in source

    def test_load_model_uses_path_open(self):
        """load_model should use Path(file_path).open, not built-in open."""
        from bt_api_py.risk_management.ml_models.ml_base import BaseMLModel

        source = inspect.getsource(BaseMLModel.load_model)
        assert "Path(file_path).open(" in source
        assert "open(file_path" not in source

    def test_path_imported_at_module_level(self):
        """Path should be in ml_base module's global namespace."""
        from bt_api_py.risk_management.ml_models import ml_base

        assert hasattr(ml_base, "Path")
        assert ml_base.Path is Path


# ---------------------------------------------------------------------------
# encryption_manager: key_file.open() / metadata_file.open()
# ---------------------------------------------------------------------------


class TestEncryptionManagerPathOpen:
    """Verify encryption_manager uses Path.open() for key/metadata files."""

    def test_store_key_uses_path_open(self):
        """_store_key should use key_file.open() and metadata_file.open()."""
        from bt_api_py.security_compliance.core.encryption_manager import LocalKeyManager

        source = inspect.getsource(LocalKeyManager._store_key)
        assert "key_file.open(" in source
        assert "metadata_file.open(" in source
        assert "open(key_file" not in source
        assert "open(metadata_file" not in source

    def test_get_key_uses_path_open(self):
        """get_key should use metadata_file.open()."""
        from bt_api_py.security_compliance.core.encryption_manager import LocalKeyManager

        source = inspect.getsource(LocalKeyManager.get_key)
        assert "metadata_file.open(" in source
        assert "open(metadata_file" not in source


# ---------------------------------------------------------------------------
# oauth2_provider: Path(key_path).open("rb")
# ---------------------------------------------------------------------------


class TestOAuth2ProviderPathOpen:
    """Verify oauth2_provider uses Path.open() for key loading."""

    def test_load_key_uses_path_open(self):
        """_load_or_generate_key should use Path(key_path).open('rb')."""
        from bt_api_py.security_compliance.auth.oauth2_provider import OAuth2Provider

        source = inspect.getsource(OAuth2Provider._load_or_generate_key)
        assert "Path(key_path).open(" in source
        assert "open(key_path" not in source
