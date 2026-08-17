"""LocalKeyManager PBKDF2 随机盐 + 密钥文件权限测试（E-03）。"""

from __future__ import annotations

import hashlib
import os
import stat

from bt_api_py.security_compliance.core import encryption_manager
from bt_api_py.security_compliance.core.encryption_manager import (
    EncryptionAlgorithm,
    LocalKeyManager,
)


def test_pbkdf2_salt_is_random_across_key_dirs(tmp_path) -> None:
    """相同密码、不同 key_dir 的 master key 盐必须随机（非确定性 sha256(pw)）。"""
    m1 = LocalKeyManager(tmp_path / "k1", "pw")
    m2 = LocalKeyManager(tmp_path / "k2", "pw")
    assert m1._derive_master_key() != m2._derive_master_key()


def test_master_key_stable_within_same_key_dir(tmp_path) -> None:
    """同一 key_dir 重新实例化必须读回相同盐，master key 稳定（可解密历史 key）。"""
    m1 = LocalKeyManager(tmp_path / "k", "pw")
    key = m1.generate_key(EncryptionAlgorithm.AES_256_GCM)
    m2 = LocalKeyManager(tmp_path / "k", "pw")
    assert m1._derive_master_key() == m2._derive_master_key()
    # 新实例能解密旧 key
    assert m2.get_key(key.key_id) is not None


def test_key_file_and_dir_permissions(tmp_path) -> None:
    """key 文件必须 0600，key_dir 必须 0700。"""
    m = LocalKeyManager(tmp_path / "k", "pw")
    key = m.generate_key(EncryptionAlgorithm.AES_256_GCM)
    key_file = m.key_dir / f"{key.key_id}.key"
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(m.key_dir).st_mode) == 0o700


def test_legacy_salt_fallback_uses_deterministic_salt(tmp_path, monkeypatch) -> None:
    """旧格式（有 .key 文件但无 salt 文件）回退确定性盐并告警，保证可解密历史 key。"""
    key_dir = tmp_path / "k"
    key_dir.mkdir(parents=True, exist_ok=True)
    (key_dir / "legacy.key").write_bytes(b"dummy")

    warnings: list[str] = []
    monkeypatch.setattr(
        encryption_manager.logger, "warning", lambda msg, *a, **k: warnings.append(msg)
    )
    m = LocalKeyManager(key_dir, "pw")

    assert m._salt == hashlib.sha256(b"pw").digest()
    assert warnings  # 记录告警
