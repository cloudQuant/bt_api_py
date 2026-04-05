from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import bt_api_py.security as security_module
from bt_api_py.security import (
    SecureCredentialManager,
    create_env_template,
    load_credentials_from_env_file,
)


class _FakeCipher:
    def encrypt(self, data: bytes) -> bytes:
        return b"enc:" + data

    def decrypt(self, data: bytes) -> bytes:
        assert data.startswith(b"enc:")
        return data[4:]


def test_encrypt_and_decrypt_passthrough_without_cipher() -> None:
    manager = SecureCredentialManager()

    encrypted = manager.encrypt_credential("secret-value")
    decrypted = manager.decrypt_credential(encrypted)

    assert encrypted == "secret-value"
    assert decrypted == "secret-value"


def test_encrypt_and_decrypt_with_cipher() -> None:
    manager = SecureCredentialManager()
    manager._cipher = _FakeCipher()

    encrypted = manager.encrypt_credential("secret-value")
    decrypted = manager.decrypt_credential(encrypted)

    assert encrypted != "secret-value"
    assert decrypted == "secret-value"


def test_init_with_encryption_key_creates_real_cipher_and_roundtrip() -> None:
    if not security_module.CRYPTO_AVAILABLE:
        pytest.skip("cryptography unavailable")

    manager = SecureCredentialManager("strong-password")

    encrypted = manager.encrypt_credential("secret-value")
    decrypted = manager.decrypt_credential(encrypted)

    assert manager._cipher is not None
    assert manager._salt is not None
    assert encrypted != "secret-value"
    assert decrypted == "secret-value"


def test_init_with_encryption_key_without_crypto_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security_module, "CRYPTO_AVAILABLE", False)

    with pytest.raises(ImportError, match="cryptography package required"):
        SecureCredentialManager("strong-password")


def test_load_from_env_returns_value_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BT_API_PY_TEST_KEY", "configured")

    assert SecureCredentialManager.load_from_env("BT_API_PY_TEST_KEY") == "configured"
    assert SecureCredentialManager.load_from_env("BT_API_PY_MISSING_KEY", "fallback") == "fallback"


@pytest.mark.parametrize(
    ("api_key", "min_length", "expected"),
    [
        (None, 16, False),
        ("", 16, False),
        ("short", 16, False),
        ("your_token_here", 8, False),
        (" example ", 4, False),
        ("abcd1234efgh5678", 16, True),
    ],
)
def test_validate_api_key_cases(api_key: str | None, min_length: int, expected: bool) -> None:
    assert SecureCredentialManager.validate_api_key(api_key, min_length=min_length) is expected


def test_mask_credential_formats_visible_segments() -> None:
    assert (
        SecureCredentialManager.mask_credential("abcd1234wxyz", visible_chars=4) == "abcd****wxyz"
    )
    assert SecureCredentialManager.mask_credential("abcd1234", visible_chars=0) == "********"


@pytest.mark.parametrize(
    ("exchange", "env_values", "expected"),
    [
        (
            "BINANCE",
            {
                "BINANCE_API_KEY": "binance-key",
                "BINANCE_SECRET": "binance-secret",
                "BINANCE_TESTNET": "true",
            },
            {"api_key": "binance-key", "secret": "binance-secret", "testnet": True},
        ),
        (
            "OKX",
            {
                "OKX_API_KEY": "okx-key",
                "OKX_SECRET": "okx-secret",
                "OKX_PASSPHRASE": "okx-pass",
                "OKX_TESTNET": "false",
            },
            {
                "api_key": "okx-key",
                "secret": "okx-secret",
                "passphrase": "okx-pass",
                "testnet": False,
            },
        ),
        (
            "CTP",
            {
                "CTP_BROKER_ID": "9999",
                "CTP_USER_ID": "user",
                "CTP_PASSWORD": "pass",
                "CTP_MD_FRONT": "md-front",
                "CTP_TD_FRONT": "td-front",
            },
            {
                "broker_id": "9999",
                "user_id": "user",
                "password": "pass",
                "md_front": "md-front",
                "td_front": "td-front",
            },
        ),
        (
            "IB",
            {
                "IB_ACCOUNT_ID": "acct",
                "IB_USERNAME": "user",
                "IB_PASSWORD": "pass",
            },
            {"account_id": "acct", "username": "user", "password": "pass"},
        ),
        ("UNKNOWN", {}, {}),
    ],
)
def test_get_exchange_credentials_plaintext_branches(
    exchange: str,
    env_values: dict[str, str],
    expected: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SecureCredentialManager()
    monkeypatch.setattr(
        manager, "load_from_env", lambda key, default=None: env_values.get(key, default)
    )

    assert manager.get_exchange_credentials(exchange) == expected


def test_get_exchange_credentials_decrypts_string_fields_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SecureCredentialManager()
    manager._cipher = object()
    env_values = {
        "OKX_API_KEY": "enc-key",
        "OKX_SECRET": "enc-secret",
        "OKX_PASSPHRASE": "enc-pass",
        "OKX_TESTNET": "true",
    }

    monkeypatch.setattr(
        manager, "load_from_env", lambda key, default=None: env_values.get(key, default)
    )
    monkeypatch.setattr(manager, "decrypt_credential", lambda value: f"decrypted:{value}")

    credentials = manager.get_exchange_credentials("OKX", encrypted=True)

    assert credentials == {
        "api_key": "decrypted:enc-key",
        "secret": "decrypted:enc-secret",
        "passphrase": "decrypted:enc-pass",
        "testnet": True,
    }


def test_load_credentials_from_env_file_missing_file_returns_empty(tmp_path) -> None:
    missing_file = tmp_path / "missing.env"

    assert load_credentials_from_env_file(missing_file) == {}


def test_load_credentials_from_env_file_manual_parse(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('FOO=bar\nQUOTED=" spaced value "\n#IGNORED=1\n', encoding="utf-8")

    with patch.dict(sys.modules, {"dotenv": None}):
        credentials = load_credentials_from_env_file(env_file)

    assert credentials == {"FOO": "bar", "QUOTED": " spaced value "}


def test_create_env_template_writes_expected_content(tmp_path) -> None:
    env_template = tmp_path / ".env.example"

    create_env_template(env_template)

    content = env_template.read_text(encoding="utf-8")
    assert "BINANCE_API_KEY=your_binance_api_key_here" in content
    assert "ENCRYPTION_KEY=your_encryption_key_here" in content
