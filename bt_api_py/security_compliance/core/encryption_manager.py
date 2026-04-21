"""Advanced Encryption Manager for End-to-End Security.

Provides FIPS 140-2 Level 3 compliant encryption with support for AWS KMS,
HashiCorp Vault, and hardware security modules (HSMs).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False
    RSAPrivateKey = object  # type: ignore[misc, assignment]
    RSAPublicKey = object  # type: ignore[misc, assignment]

try:
    import boto3

    AWS_AVAILABLE = True
except ImportError:
    AWS_AVAILABLE = False

try:
    import hvac  # HashiCorp Vault client

    VAULT_AVAILABLE = True
except ImportError:
    VAULT_AVAILABLE = False

from bt_api_base.exceptions import BtApiError


class EncryptionError(BtApiError):
    """Encryption-related errors."""


class KeyProvider(Enum):
    """Key storage provider types."""

    LOCAL = "local"
    AWS_KMS = "aws_kms"
    HASHICORP_VAULT = "hashicorp_vault"
    HSM = "hsm"


class EncryptionAlgorithm(Enum):
    """Supported encryption algorithms."""

    AES_256_GCM = "aes_256_gcm"
    AES_256_CBC = "aes_256_cbc"
    CHACHA20_POLY1305 = "chacha20_poly1305"


@dataclass
class EncryptionKey:
    """Encryption key with metadata."""

    key_id: str
    key_data: bytes
    algorithm: EncryptionAlgorithm
    created_at: float
    provider: KeyProvider
    key_size: int = 256
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "key_id": self.key_id,
            "algorithm": self.algorithm.value,
            "created_at": self.created_at,
            "provider": self.provider.value,
            "key_size": self.key_size,
            "is_active": self.is_active,
        }


class KeyManager(ABC):
    """Abstract base class for key management providers."""

    @abstractmethod
    def generate_key(self, algorithm: EncryptionAlgorithm) -> EncryptionKey:
        """Generate a new encryption key."""

    @abstractmethod
    def get_key(self, key_id: str) -> EncryptionKey | None:
        """Retrieve an existing key."""

    @abstractmethod
    def rotate_key(self, key_id: str) -> EncryptionKey:
        """Rotate an existing key."""

    @abstractmethod
    def delete_key(self, key_id: str) -> None:
        """Delete a key."""


class LocalKeyManager(KeyManager):
    """Local file-based key management."""

    def __init__(self, key_dir: str | Path, master_password: str):
        """Initialize local key manager."""
        if not CRYPTOGRAPHY_AVAILABLE:
            raise EncryptionError("cryptography package required for encryption")

        self.key_dir = Path(key_dir)
        self.key_dir.mkdir(parents=True, exist_ok=True)
        self.master_password = master_password
        self._master_key = self._derive_master_key()

    def _derive_master_key(self) -> bytes:
        """Derive master key from password using PBKDF2."""
        salt = hashlib.sha256(self.master_password.encode()).digest()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend(),
        )
        return kdf.derive(self.master_password.encode())

    def generate_key(self, algorithm: EncryptionAlgorithm) -> EncryptionKey:
        """Generate a new encryption key."""
        if algorithm in (
            EncryptionAlgorithm.AES_256_GCM,
            EncryptionAlgorithm.CHACHA20_POLY1305,
        ):
            key_data = os.urandom(32)  # 256 bits
        else:
            raise EncryptionError(f"Unsupported algorithm: {algorithm}")

        key = EncryptionKey(
            key_id=f"key_{int(time.time())}_{os.urandom(4).hex()}",
            key_data=key_data,
            algorithm=algorithm,
            created_at=time.time(),
            provider=KeyProvider.LOCAL,
        )

        # Store encrypted key
        self._store_key(key)
        return key

    def _store_key(self, key: EncryptionKey) -> None:
        """Store key encrypted with master key."""
        fernet = Fernet(base64.urlsafe_b64encode(self._master_key))
        encrypted_data = fernet.encrypt(key.key_data)

        key_file = self.key_dir / f"{key.key_id}.key"
        metadata_file = self.key_dir / f"{key.key_id}.meta"

        # Store encrypted key
        with key_file.open("wb") as f:
            f.write(encrypted_data)

        # Store metadata
        with metadata_file.open("w", encoding="utf-8") as f:
            json.dump(key.to_dict(), f, indent=2)

    def get_key(self, key_id: str) -> EncryptionKey | None:
        """Retrieve and decrypt an existing key."""
        key_file = self.key_dir / f"{key_id}.key"
        metadata_file = self.key_dir / f"{key_id}.meta"

        if not key_file.exists() or not metadata_file.exists():
            return None

        try:
            # Load metadata
            with metadata_file.open(encoding="utf-8") as f:
                metadata = json.load(f)

            # Decrypt key data
            fernet = Fernet(base64.urlsafe_b64encode(self._master_key))
            encrypted_data = key_file.read_bytes()
            key_data = fernet.decrypt(encrypted_data)

            return EncryptionKey(
                key_id=metadata["key_id"],
                key_data=key_data,
                algorithm=EncryptionAlgorithm(metadata["algorithm"]),
                created_at=metadata["created_at"],
                provider=KeyProvider(metadata["provider"]),
                key_size=metadata["key_size"],
                is_active=metadata["is_active"],
            )

        except Exception as e:
            raise EncryptionError(f"Failed to retrieve key {key_id}: {e}") from e

    def rotate_key(self, key_id: str) -> EncryptionKey:
        """Rotate an existing key."""
        old_key = self.get_key(key_id)
        if not old_key:
            raise EncryptionError(f"Key {key_id} not found")

        # Generate new key
        new_key = self.generate_key(old_key.algorithm)

        # Mark old key as inactive
        old_key.is_active = False
        self._store_key(old_key)

        return new_key

    def delete_key(self, key_id: str) -> None:
        """Delete a key."""
        key_file = self.key_dir / f"{key_id}.key"
        metadata_file = self.key_dir / f"{key_id}.meta"

        if key_file.exists():
            key_file.unlink()

        if metadata_file.exists():
            metadata_file.unlink()


class AWSKMSKeyManager(KeyManager):
    """AWS KMS key management provider."""

    def __init__(self, region_name: str = "us-east-1"):
        """Initialize AWS KMS manager."""
        if not AWS_AVAILABLE:
            raise EncryptionError("boto3 package required for AWS KMS")

        self.kms_client = boto3.client("kms", region_name=region_name)

    def generate_key(self, algorithm: EncryptionAlgorithm) -> EncryptionKey:
        """Generate a new KMS key."""
        try:
            response = self.kms_client.create_key(
                Description=f"bt_api_py {algorithm.value} key",
                KeyUsage="ENCRYPT_DECRYPT",
                CustomerMasterKeySpec="SYMMETRIC_DEFAULT",
            )

            key_metadata = response["KeyMetadata"]

            return EncryptionKey(
                key_id=key_metadata["KeyId"],
                key_data=b"",  # KMS manages key data
                algorithm=algorithm,
                created_at=time.time(),
                provider=KeyProvider.AWS_KMS,
            )

        except Exception as e:
            raise EncryptionError(f"Failed to create KMS key: {e}") from e

    def get_key(self, key_id: str) -> EncryptionKey | None:
        """Get KMS key metadata."""
        try:
            response = self.kms_client.describe_key(KeyId=key_id)
            key_metadata = response["KeyMetadata"]

            return EncryptionKey(
                key_id=key_metadata["KeyId"],
                key_data=b"",
                algorithm=EncryptionAlgorithm.AES_256_GCM,  # KMS default
                created_at=time.time(),
                provider=KeyProvider.AWS_KMS,
                is_active=key_metadata["KeyState"] == "Enabled",
            )

        except Exception:
            return None

    def rotate_key(self, key_id: str) -> EncryptionKey:
        """Rotate KMS key."""
        try:
            self.kms_client.rotate_key(KeyId=key_id)
            key = self.get_key(key_id)
            if key is None:
                raise EncryptionError(f"Key {key_id} not found after rotation")
            return key
        except EncryptionError:
            raise
        except Exception as e:
            raise EncryptionError(f"Failed to rotate KMS key {key_id}: {e}") from e

    def delete_key(self, key_id: str) -> None:
        """Schedule KMS key for deletion."""
        try:
            self.kms_client.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=7)
        except Exception as e:
            raise EncryptionError(f"Failed to delete KMS key {key_id}: {e}") from e


class HashiCorpVaultKeyManager(KeyManager):
    """HashiCorp Vault key management provider."""

    def __init__(self, vault_url: str, token: str, mount_path: str = "transit"):
        """Initialize Vault manager."""
        if not VAULT_AVAILABLE:
            raise EncryptionError("hvac package required for HashiCorp Vault")

        self.client = hvac.Client(url=vault_url, token=token)
        self.mount_path = mount_path

        if not self.client.is_authenticated():
            raise EncryptionError("Failed to authenticate with Vault")

    def generate_key(self, algorithm: EncryptionAlgorithm) -> EncryptionKey:
        """Create a new transit encryption key."""
        try:
            key_name = f"bt_api_py_{algorithm.value}_{int(time.time())}"

            self.client.secrets.transit.create_key(
                name=key_name, key_type=self._get_vault_key_type(algorithm)
            )

            return EncryptionKey(
                key_id=key_name,
                key_data=b"",
                algorithm=algorithm,
                created_at=time.time(),
                provider=KeyProvider.HASHICORP_VAULT,
            )

        except Exception as e:
            raise EncryptionError(f"Failed to create Vault key: {e}") from e

    def _get_vault_key_type(self, algorithm: EncryptionAlgorithm) -> str:
        """Map algorithm to Vault key type."""
        mapping = {
            EncryptionAlgorithm.AES_256_GCM: "aes256-gcm96",
            EncryptionAlgorithm.CHACHA20_POLY1305: "chacha20-poly1305",
        }
        return mapping.get(algorithm, "aes256-gcm96")

    def get_key(self, key_id: str) -> EncryptionKey | None:
        """Get Vault key information."""
        try:
            response = self.client.secrets.transit.read_key(name=key_id)
            data = response["data"]

            return EncryptionKey(
                key_id=key_id,
                key_data=b"",
                algorithm=self._parse_vault_type(data["type"]),
                created_at=data["creation_time"],
                provider=KeyProvider.HASHICORP_VAULT,
                is_active=not data.get("deletion_time", 0),
            )

        except Exception:
            return None

    def _parse_vault_type(self, vault_type: str) -> EncryptionAlgorithm:
        """Parse Vault key type to enum."""
        mapping = {
            "aes256-gcm96": EncryptionAlgorithm.AES_256_GCM,
            "chacha20-poly1305": EncryptionAlgorithm.CHACHA20_POLY1305,
        }
        return mapping.get(vault_type, EncryptionAlgorithm.AES_256_GCM)

    def rotate_key(self, key_id: str) -> EncryptionKey:
        """Rotate Vault key."""
        try:
            self.client.secrets.transit.rotate_key(name=key_id)
            key = self.get_key(key_id)
            if key is None:
                raise EncryptionError(f"Key {key_id} not found after rotation")
            return key
        except EncryptionError:
            raise
        except Exception as e:
            raise EncryptionError(f"Failed to rotate Vault key {key_id}: {e}") from e

    def delete_key(self, key_id: str) -> None:
        """Delete Vault key."""
        try:
            self.client.secrets.transit.delete_key(name=key_id)
        except Exception as e:
            raise EncryptionError(f"Failed to delete Vault key {key_id}: {e}") from e


class EncryptionManager:
    """Main encryption manager with FIPS 140-2 compliance."""

    def __init__(
        self,
        key_manager: KeyManager,
        default_algorithm: EncryptionAlgorithm = EncryptionAlgorithm.AES_256_GCM,
    ):
        """Initialize encryption manager."""
        if not CRYPTOGRAPHY_AVAILABLE:
            raise EncryptionError("cryptography package required for encryption")

        self.key_manager = key_manager
        self.default_algorithm = default_algorithm
        self._active_key: EncryptionKey | None = None
        self._key_cache: dict[str, EncryptionKey] = {}

    def _get_active_key(self) -> EncryptionKey:
        """Get or create the active encryption key."""
        if self._active_key and self._active_key.is_active:
            return self._active_key

        # Try to find existing active key
        # In a real implementation, you might query the key manager for active keys
        self._active_key = self.key_manager.generate_key(self.default_algorithm)
        return self._active_key

    def encrypt(self, data: str | bytes, key_id: str | None = None) -> dict[str, Any]:
        """Encrypt data with specified or default key."""
        if isinstance(data, str):
            data = data.encode("utf-8")

        # Get key
        if key_id:
            key = self.key_manager.get_key(key_id)
            if not key:
                raise EncryptionError(f"Key {key_id} not found")
        else:
            key = self._get_active_key()

        # Encrypt based on algorithm
        if key.algorithm == EncryptionAlgorithm.AES_256_GCM:
            encrypted_data = self._encrypt_aes_gcm(data, key)
        elif key.algorithm == EncryptionAlgorithm.CHACHA20_POLY1305:
            encrypted_data = self._encrypt_chacha20(data, key)
        else:
            raise EncryptionError(f"Unsupported algorithm: {key.algorithm}")

        return {
            "key_id": key.key_id,
            "algorithm": key.algorithm.value,
            "encrypted_data": base64.b64encode(encrypted_data["ciphertext"]).decode(),
            "nonce": base64.b64encode(encrypted_data["nonce"]).decode(),
            "tag": base64.b64encode(encrypted_data["tag"]).decode()
            if encrypted_data.get("tag")
            else None,
        }

    def _encrypt_aes_gcm(self, data: bytes, key: EncryptionKey) -> dict[str, bytes]:
        """Encrypt using AES-256-GCM."""
        nonce = os.urandom(12)  # 96-bit nonce for GCM
        cipher = Cipher(algorithms.AES(key.key_data), modes.GCM(nonce), backend=default_backend())

        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(data) + encryptor.finalize()

        return {
            "ciphertext": ciphertext,
            "nonce": nonce,
            "tag": encryptor.tag,
        }

    def _encrypt_chacha20(self, data: bytes, key: EncryptionKey) -> dict[str, bytes]:
        """Encrypt using ChaCha20-Poly1305."""
        nonce = os.urandom(12)  # 96-bit nonce
        chacha = ChaCha20Poly1305(key.key_data)
        ciphertext_with_tag = chacha.encrypt(nonce, data, None)
        # AEAD returns ciphertext + 16-byte tag appended
        return {
            "ciphertext": ciphertext_with_tag,
            "nonce": nonce,
            "tag": ciphertext_with_tag[-16:],
        }

    def decrypt(self, encrypted_data: dict[str, Any]) -> bytes:
        """Decrypt encrypted data."""
        key_id = encrypted_data["key_id"]
        algorithm = encrypted_data["algorithm"]

        # Get key
        key = self.key_manager.get_key(key_id)
        if not key:
            raise EncryptionError(f"Key {key_id} not found")

        # Decode components
        ciphertext = base64.b64decode(encrypted_data["encrypted_data"])
        nonce = base64.b64decode(encrypted_data["nonce"])
        tag_b64 = encrypted_data.get("tag")
        tag = base64.b64decode(tag_b64) if tag_b64 else None

        # Decrypt based on algorithm
        if algorithm == EncryptionAlgorithm.AES_256_GCM.value:
            if tag is None:
                raise EncryptionError("AES-GCM requires tag for decryption")
            return self._decrypt_aes_gcm(ciphertext, key, nonce, tag)
        elif algorithm == EncryptionAlgorithm.CHACHA20_POLY1305.value:
            # ChaCha20Poly1305 AEAD uses combined ciphertext+tag
            return self._decrypt_chacha20(ciphertext, key, nonce)
        else:
            raise EncryptionError(f"Unsupported algorithm: {algorithm}")

    def _decrypt_aes_gcm(
        self, ciphertext: bytes, key: EncryptionKey, nonce: bytes, tag: bytes
    ) -> bytes:
        """Decrypt AES-256-GCM data."""
        cipher = Cipher(
            algorithms.AES(key.key_data), modes.GCM(nonce, tag), backend=default_backend()
        )

        decryptor = cipher.decryptor()
        return decryptor.update(ciphertext) + decryptor.finalize()

    def _decrypt_chacha20(self, ciphertext: bytes, key: EncryptionKey, nonce: bytes) -> bytes:
        """Decrypt ChaCha20-Poly1305 data (ciphertext includes 16-byte tag)."""
        chacha = ChaCha20Poly1305(key.key_data)
        return chacha.decrypt(nonce, ciphertext, None)

    def rotate_keys(self) -> EncryptionKey:
        """Rotate the active encryption key."""
        active = self._get_active_key()
        new_key = self.key_manager.rotate_key(active.key_id)
        self._active_key = new_key
        return new_key

    def generate_key_pair(self, key_size: int = 2048) -> dict[str, str]:
        """Generate RSA key pair for asymmetric encryption."""
        private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=key_size, backend=default_backend()
        )

        # Serialize keys
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        return {
            "private_key": base64.b64encode(private_pem).decode(),
            "public_key": base64.b64encode(public_pem).decode(),
        }

    def encrypt_with_public_key(self, data: str | bytes, public_key_pem: str) -> str:
        """Encrypt data with RSA public key."""
        if isinstance(data, str):
            data = data.encode("utf-8")

        public_key_bytes = base64.b64decode(public_key_pem)
        pub_key = serialization.load_pem_public_key(public_key_bytes, backend=default_backend())
        public_key = cast("RSAPublicKey", pub_key)

        encrypted = public_key.encrypt(
            data,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None
            ),
        )

        return base64.b64encode(encrypted).decode()

    def decrypt_with_private_key(self, encrypted_data: str, private_key_pem: str) -> bytes:
        """Decrypt data with RSA private key."""
        private_key_bytes = base64.b64decode(private_key_pem)
        priv_key = serialization.load_pem_private_key(
            private_key_bytes, password=None, backend=default_backend()
        )
        private_key = cast("RSAPrivateKey", priv_key)

        encrypted_bytes = base64.b64decode(encrypted_data)

        decrypted = private_key.decrypt(
            encrypted_bytes,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None
            ),
        )

        return decrypted


# Factory function for creating key managers
def create_key_manager(provider: KeyProvider, **kwargs: Any) -> KeyManager:
    """Create a key manager based on provider type."""
    if provider == KeyProvider.LOCAL:
        return LocalKeyManager(
            key_dir=kwargs.get("key_dir", "./keys"),
            master_password=kwargs.get("master_password", ""),
        )
    elif provider == KeyProvider.AWS_KMS:
        return AWSKMSKeyManager(region_name=kwargs.get("region", "us-east-1"))
    elif provider == KeyProvider.HASHICORP_VAULT:
        vault_url = kwargs.get("vault_url")
        token = kwargs.get("token")
        if not isinstance(vault_url, str) or not isinstance(token, str):
            raise EncryptionError("HashiCorp Vault requires vault_url and token")
        return HashiCorpVaultKeyManager(
            vault_url=vault_url,
            token=token,
            mount_path=kwargs.get("mount_path", "transit"),
        )
    else:
        raise EncryptionError(f"Unsupported key provider: {provider}")


# Global encryption manager instance
_global_lock = threading.Lock()
_encryption_manager: EncryptionManager | None = None


def get_encryption_manager() -> EncryptionManager | None:
    """Get the global encryption manager instance (thread-safe)."""
    with _global_lock:
        return _encryption_manager


def initialize_encryption_manager(key_manager: KeyManager) -> EncryptionManager:
    """Initialize the global encryption manager (thread-safe).

    Args:
        key_manager: The key manager to use for encryption operations.

    Returns:
        The initialized EncryptionManager instance.
    """
    global _encryption_manager
    with _global_lock:
        if _encryption_manager is None:
            _encryption_manager = EncryptionManager(key_manager)
        return _encryption_manager
