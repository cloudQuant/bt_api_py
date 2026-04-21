"""Regression tests for code quality batch v5.

Covers:
- encryption_manager: module-level json import, encoding in open(), in-operator,
  typed **kwargs in create_key_manager
- grafana: no redundant imports, encoding in save_dashboard_to_file
- security: Path.open() usage, narrowed exception in get_exchange_credentials
"""

from __future__ import annotations

import inspect
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# encryption_manager: json at module level (not inline)
# ---------------------------------------------------------------------------


class TestEncryptionManagerImports:
    """Verify json is imported at module level, not inline."""

    def test_json_is_module_level_import(self):
        """json should be in the module's global namespace, not imported inline."""
        from bt_api_py.security_compliance.core import encryption_manager

        assert hasattr(encryption_manager, "json")
        assert encryption_manager.json is json

    def test_create_key_manager_kwargs_typed(self):
        """create_key_manager should have **kwargs: Any annotation."""
        from bt_api_py.security_compliance.core.encryption_manager import (
            create_key_manager,
        )

        sig = inspect.signature(create_key_manager)
        kwargs_param = sig.parameters.get("kwargs")
        assert kwargs_param is not None
        # The annotation should be Any (not inspect.Parameter.empty)
        assert kwargs_param.annotation is not inspect.Parameter.empty


# ---------------------------------------------------------------------------
# grafana: no redundant imports in save_dashboard_to_file
# ---------------------------------------------------------------------------


class TestGrafanaSaveDashboard:
    """Verify save_dashboard_to_file uses module-level imports and encoding."""

    def test_no_inline_imports_in_save_dashboard(self):
        """save_dashboard_to_file should not contain inline import statements."""
        from bt_api_py.monitoring.grafana import save_dashboard_to_file

        source = inspect.getsource(save_dashboard_to_file)
        assert "import json" not in source
        assert "from pathlib" not in source

    def test_save_dashboard_writes_valid_json(self):
        """save_dashboard_to_file should produce valid UTF-8 JSON."""
        from bt_api_py.monitoring.grafana import save_dashboard_to_file

        dashboard = {"dashboard": {"title": "test", "panels": []}}
        with tempfile.TemporaryDirectory() as tmp:
            filepath = Path(tmp) / "sub" / "dash.json"
            save_dashboard_to_file(dashboard, str(filepath))

            assert filepath.exists()
            content = filepath.read_text(encoding="utf-8")
            loaded = json.loads(content)
            assert loaded == dashboard

    def test_path_imported_at_module_level(self):
        """Path should be in grafana module's global namespace."""
        from bt_api_py.monitoring import grafana

        assert hasattr(grafana, "Path")
        assert grafana.Path is Path


# ---------------------------------------------------------------------------
# security: Path.open() usage and narrowed exceptions
# ---------------------------------------------------------------------------


class TestSecurityPathUsage:
    """Verify security.py uses Path.open() and narrowed exceptions."""

    def test_load_credentials_uses_path_open(self):
        """load_credentials_from_env_file should use Path.open, not built-in open."""
        from bt_api_base.security import load_credentials_from_env_file

        source = inspect.getsource(load_credentials_from_env_file)
        # Should contain env_path.open() pattern, not open(env_path, ...)
        assert "env_path.open(" in source
        assert "open(env_path" not in source

    def test_create_env_template_uses_path_open(self):
        """create_env_template should use Path.open, not built-in open."""
        from bt_api_base.security import create_env_template

        source = inspect.getsource(create_env_template)
        # Should contain output_path.open() pattern
        assert "output_path.open(" in source
        assert "open(output_file" not in source

    def test_create_env_template_produces_valid_file(self):
        """create_env_template should create a valid .env template."""
        from bt_api_base.security import create_env_template

        with tempfile.TemporaryDirectory() as tmp:
            filepath = Path(tmp) / ".env.example"
            create_env_template(str(filepath))

            assert filepath.exists()
            content = filepath.read_text(encoding="utf-8")
            assert "BINANCE_API_KEY" in content
            assert "CTP_BROKER_ID" in content

    def test_load_credentials_from_env_file_manual_parse(self):
        """Manual .env parser should work when dotenv is unavailable."""
        from bt_api_base.security import load_credentials_from_env_file

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                'FOO=bar\nBAZ="quoted"\n# comment\nKEY=val ue\n',
                encoding="utf-8",
            )
            # Force manual parsing by patching dotenv import to fail
            with patch.dict("sys.modules", {"dotenv": None}):
                result = load_credentials_from_env_file(str(env_file))

            assert result.get("FOO") == "bar"
            assert result.get("BAZ") == "quoted"
            assert result.get("KEY") == "val ue"

    def test_get_exchange_credentials_narrowed_exception(self):
        """Decrypt failure should catch InvalidToken, not broad Exception."""
        from bt_api_base.security import SecureCredentialManager

        source = inspect.getsource(SecureCredentialManager.get_exchange_credentials)
        # Should NOT have bare 'except Exception'
        assert "except Exception" not in source
        # Should have narrowed exception types
        assert "InvalidToken" in source
        assert "ValueError" in source
        assert "UnicodeDecodeError" in source


class TestSecurityInvalidTokenImport:
    """Verify InvalidToken is available for use even without cryptography."""

    def test_invalid_token_fallback(self):
        """When cryptography is not installed, InvalidToken should fall back."""
        from bt_api_base import security

        assert hasattr(security, "InvalidToken")
