from __future__ import annotations

import base64

import pytest

pytest.importorskip("cryptography", reason="cryptography package required for WebAuthn tests")
pytest.importorskip("pyotp", reason="pyotp package required for TOTP tests")

from bt_api_py.security_compliance.auth.mfa_provider import (
    MFAConfig,
    MFAProvider,
    MFAType,
    WebAuthnCredential,
)


def test_setup_totp_returns_backup_codes_copy():
    provider = MFAProvider()

    result = provider.setup_totp("user-a")
    returned_codes = result["backup_codes"]
    stored_codes = list(provider.get_mfa_status("user-a").backup_codes)

    returned_codes.pop()

    assert provider.get_mfa_status("user-a").backup_codes == stored_codes


def test_setup_hotp_returns_backup_codes_copy():
    provider = MFAProvider()

    result = provider.setup_hotp("user-a")
    returned_codes = result["backup_codes"]
    stored_codes = list(provider.get_mfa_status("user-a").backup_codes)

    returned_codes.clear()

    assert provider.get_mfa_status("user-a").backup_codes == stored_codes


def test_regenerate_backup_codes_returns_copy():
    provider = MFAProvider()
    provider.setup_totp("user-a")

    regenerated = provider.regenerate_backup_codes("user-a")
    stored_codes = list(provider.get_mfa_status("user-a").backup_codes)

    regenerated.append("999999")

    assert provider.get_mfa_status("user-a").backup_codes == stored_codes


def test_generate_backup_codes_avoids_duplicates(monkeypatch: pytest.MonkeyPatch):
    provider = MFAProvider(backup_codes_count=3)
    values = iter([123456, 123456, 234567, 234567, 345678])
    monkeypatch.setattr(
        "bt_api_py.security_compliance.auth.mfa_provider.secrets.randbelow", lambda _: next(values)
    )

    codes = provider._generate_backup_codes()

    assert codes == ["123456", "234567", "345678"]


def test_disable_mfa_removes_webauthn_credential_and_challenges():
    provider = MFAProvider()
    credential_id = base64.urlsafe_b64encode(b"cred-1").decode()
    provider._webauthn_credentials[credential_id] = WebAuthnCredential(
        credential_id=b"cred-1",
        public_key=b"public-key",
        user_handle=b"user-a",
        rp_id="localhost",
    )
    provider._mfa_configs["user-a"] = MFAConfig(
        user_id="user-a",
        mfa_type=MFAType.WEBAUTHN,
        is_enabled=True,
        backup_codes=["123456"],
        credential_id=credential_id,
    )
    provider._webauthn_challenges["user-a"] = {"challenge": "abc", "expires_at": 1.0}
    provider._webauthn_challenges["auth_user-a"] = {"challenge": "def", "expires_at": 1.0}

    disabled = provider.disable_mfa("user-a", backup_code="123456")

    assert disabled is True
    assert provider.get_mfa_status("user-a") is None
    assert credential_id not in provider._webauthn_credentials
    assert "user-a" not in provider._webauthn_challenges
    assert "auth_user-a" not in provider._webauthn_challenges
