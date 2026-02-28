"""
Security utilities for API key management and encryption.

Provides secure storage and retrieval of API credentials using encryption
and environment variable management.
"""

import base64
import os
from pathlib import Path
from typing import Any

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
    
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


class SecureCredentialManager:
    """
    Manage API credentials securely with optional encryption.
    
    Features:
    - Environment variable loading with .env support
    - Optional encryption for stored credentials
    - Secure credential validation
    - Credential masking for logging
    """
    
    def __init__(self, encryption_key: str | None = None):
        """
        Initialize credential manager.
        
        Args:
            encryption_key: Optional encryption key for credential storage.
                          If None, credentials are stored in plaintext (less secure).
        """
        self._encryption_key = encryption_key
        self._cipher = None
        
        if encryption_key and CRYPTO_AVAILABLE:
            self._cipher = self._create_cipher(encryption_key)
        elif encryption_key and not CRYPTO_AVAILABLE:
            raise ImportError(
                "cryptography package required for encryption. "
                "Install with: pip install bt_api_py[ib_web]"
            )
    
    @staticmethod
    def _create_cipher(password: str) -> "Fernet":
        """Create Fernet cipher from password."""
        # Derive key from password using PBKDF2
        kdf = PBKDF2(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"bt_api_py_salt",  # In production, use random salt
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return Fernet(key)
    
    def encrypt_credential(self, credential: str) -> str:
        """Encrypt a credential string."""
        if not self._cipher:
            return credential
        
        encrypted = self._cipher.encrypt(credential.encode())
        return base64.urlsafe_b64encode(encrypted).decode()
    
    def decrypt_credential(self, encrypted_credential: str) -> str:
        """Decrypt an encrypted credential."""
        if not self._cipher:
            return encrypted_credential
        
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_credential.encode())
        decrypted = self._cipher.decrypt(encrypted_bytes)
        return decrypted.decode()
    
    @staticmethod
    def load_from_env(key: str, default: str | None = None) -> str | None:
        """
        Load credential from environment variable.
        
        Args:
            key: Environment variable name
            default: Default value if not found
            
        Returns:
            Credential value or default
        """
        return os.getenv(key, default)
    
    @staticmethod
    def mask_credential(credential: str, visible_chars: int = 4) -> str:
        """
        Mask credential for safe logging.
        
        Args:
            credential: The credential to mask
            visible_chars: Number of characters to show at start/end
            
        Returns:
            Masked credential (e.g., "abcd****wxyz")
        """
        if not credential or len(credential) <= visible_chars * 2:
            return "****"
        
        start = credential[:visible_chars]
        end = credential[-visible_chars:]
        return f"{start}{'*' * (len(credential) - visible_chars * 2)}{end}"
    
    @staticmethod
    def validate_api_key(api_key: str, min_length: int = 16) -> bool:
        """
        Validate API key format.
        
        Args:
            api_key: The API key to validate
            min_length: Minimum required length
            
        Returns:
            True if valid, False otherwise
        """
        if not api_key or not isinstance(api_key, str):
            return False
        
        if len(api_key) < min_length:
            return False
        
        # Check for common placeholder values
        placeholders = ["your_api_key", "api_key_here", "placeholder", "example"]
        if api_key.lower() in placeholders:
            return False
        
        return True
    
    def get_exchange_credentials(
        self, exchange: str, encrypted: bool = False
    ) -> dict[str, Any]:
        """
        Get credentials for a specific exchange from environment.
        
        Args:
            exchange: Exchange name (BINANCE, OKX, CTP, IB)
            encrypted: Whether credentials are encrypted
            
        Returns:
            Dictionary of credentials
        """
        exchange = exchange.upper()
        credentials = {}
        
        if exchange == "BINANCE":
            credentials = {
                "api_key": self.load_from_env("BINANCE_API_KEY"),
                "secret": self.load_from_env("BINANCE_SECRET"),
                "testnet": self.load_from_env("BINANCE_TESTNET", "false").lower() == "true",
            }
        elif exchange == "OKX":
            credentials = {
                "api_key": self.load_from_env("OKX_API_KEY"),
                "secret": self.load_from_env("OKX_SECRET"),
                "passphrase": self.load_from_env("OKX_PASSPHRASE"),
                "testnet": self.load_from_env("OKX_TESTNET", "false").lower() == "true",
            }
        elif exchange == "CTP":
            credentials = {
                "broker_id": self.load_from_env("CTP_BROKER_ID"),
                "user_id": self.load_from_env("CTP_USER_ID"),
                "password": self.load_from_env("CTP_PASSWORD"),
                "md_front": self.load_from_env("CTP_MD_FRONT"),
                "td_front": self.load_from_env("CTP_TD_FRONT"),
            }
        elif exchange == "IB":
            credentials = {
                "account_id": self.load_from_env("IB_ACCOUNT_ID"),
                "username": self.load_from_env("IB_USERNAME"),
                "password": self.load_from_env("IB_PASSWORD"),
            }
        
        # Decrypt if needed
        if encrypted and self._cipher:
            for key, value in credentials.items():
                if value and isinstance(value, str) and key != "testnet":
                    try:
                        credentials[key] = self.decrypt_credential(value)
                    except Exception:
                        pass  # Keep original if decryption fails
        
        return credentials


def load_credentials_from_env_file(env_file: str | Path = ".env") -> dict[str, str]:
    """
    Load credentials from .env file.
    
    Args:
        env_file: Path to .env file
        
    Returns:
        Dictionary of environment variables
    """
    try:
        from dotenv import dotenv_values
        
        return dotenv_values(env_file)
    except ImportError:
        # Fallback: manual parsing
        env_vars = {}
        env_path = Path(env_file)
        
        if not env_path.exists():
            return env_vars
        
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")
        
        return env_vars


def create_env_template(output_file: str | Path = ".env.example") -> None:
    """
    Create a template .env file with placeholder credentials.
    
    Args:
        output_file: Path to output file
    """
    template = """# Binance API Credentials
BINANCE_API_KEY=your_binance_api_key_here
BINANCE_SECRET=your_binance_secret_here
BINANCE_TESTNET=false

# OKX API Credentials
OKX_API_KEY=your_okx_api_key_here
OKX_SECRET=your_okx_secret_here
OKX_PASSPHRASE=your_okx_passphrase_here
OKX_TESTNET=false

# CTP Credentials
CTP_BROKER_ID=9999
CTP_USER_ID=your_ctp_user_id_here
CTP_PASSWORD=your_ctp_password_here
CTP_MD_FRONT=tcp://180.168.146.187:10211
CTP_TD_FRONT=tcp://180.168.146.187:10201

# Interactive Brokers Credentials
IB_ACCOUNT_ID=your_ib_account_id_here
IB_USERNAME=your_ib_username_here
IB_PASSWORD=your_ib_password_here

# Security Settings
ENCRYPTION_KEY=your_encryption_key_here

# Testing
SKIP_LIVE_TESTS=true
"""
    
    with open(output_file, "w") as f:
        f.write(template)
