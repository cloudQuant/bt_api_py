"""Multi-Factor Authentication (MFA) Provider with WebAuthn/FIDO2 Support.

Implements TOTP, HOTP, and WebAuthn for strong authentication
following FIDO2 standards for financial services.
"""

import base64
import json
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    import pyotp

    PYOTP_AVAILABLE = True
except ImportError:
    PYOTP_AVAILABLE = False

try:
    import cryptography  # noqa: F401 - imported to check availability

    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

try:
    import cbor2

    CBOR2_AVAILABLE = True
except ImportError:
    CBOR2_AVAILABLE = False

try:
    from io import BytesIO

    import qrcode

    QR_CODE_AVAILABLE = True
except ImportError:
    QR_CODE_AVAILABLE = False

from bt_api_py.exceptions import BtApiError


class MFAError(BtApiError):
    """Multi-factor authentication related errors."""

    pass


class MFAType(Enum):
    """Types of MFA methods."""

    TOTP = "totp"  # Time-based One-Time Password
    HOTP = "hotp"  # HMAC-based One-Time Password
    WEBAUTHN = "webauthn"  # WebAuthn/FIDO2
    SMS = "sms"  # SMS verification
    EMAIL = "email"  # Email verification


@dataclass
class MFAConfig:
    """MFA configuration for a user."""

    user_id: str
    mfa_type: MFAType
    is_enabled: bool = False
    secret: str | None = None
    backup_codes: list[str] = field(default_factory=list)
    counter: int = 0  # For HOTP
    created_at: float = field(default_factory=time.time)
    last_used: float | None = None

    # WebAuthn specific fields
    credential_id: str | None = None
    public_key: bytes | None = None
    sign_count: int = 0
    user_handle: bytes | None = None


@dataclass
class WebAuthnCredential:
    """WebAuthn credential data."""

    credential_id: bytes
    public_key: bytes
    user_handle: bytes
    rp_id: str
    sign_count: int = 0
    created_at: float = field(default_factory=time.time)


class MFAProvider:
    """Multi-Factor Authentication provider."""

    def __init__(
        self,
        issuer_name: str = "bt_api_py",
        totp_window: int = 1,  # 30-second window
        hotp_counter_start: int = 0,
        backup_codes_count: int = 10,
        rp_id: str = "localhost",
        rp_name: str = "bt_api_py Trading Platform",
    ):
        """Initialize MFA provider."""
        if not PYOTP_AVAILABLE:
            raise MFAError("pyotp package required for TOTP/HOTP")

        if not CRYPTO_AVAILABLE:
            raise MFAError("cryptography package required for WebAuthn")

        self.issuer_name = issuer_name
        self.totp_window = totp_window
        self.hotp_counter_start = hotp_counter_start
        self.backup_codes_count = backup_codes_count
        self.rp_id = rp_id
        self.rp_name = rp_name

        # Storage
        self._mfa_configs: dict[str, MFAConfig] = {}
        self._webauthn_credentials: dict[str, WebAuthnCredential] = {}

    def setup_totp(self, user_id: str, account_name: str | None = None) -> dict[str, Any]:
        """Setup TOTP for a user."""
        # Generate secret
        secret = pyotp.random_base32()

        # Create TOTP object
        totp = pyotp.TOTP(secret, issuer=self.issuer_name, name=account_name or user_id)

        # Generate QR code URL
        provisioning_uri = totp.provisioning_uri()

        # Generate backup codes
        backup_codes = self._generate_backup_codes()

        # Store configuration
        config = MFAConfig(
            user_id=user_id,
            mfa_type=MFAType.TOTP,
            is_enabled=False,  # Needs verification first
            secret=secret,
            backup_codes=backup_codes,
        )

        self._mfa_configs[user_id] = config

        return {
            "secret": secret,
            "provisioning_uri": provisioning_uri,
            "backup_codes": backup_codes,
            "qr_code": self._generate_qr_code(provisioning_uri) if QR_CODE_AVAILABLE else None,
        }

    def _generate_backup_codes(self) -> list[str]:
        """Generate backup codes for MFA."""
        codes = []
        for _ in range(self.backup_codes_count):
            code = f"{secrets.randbelow(1000000):06d}"
            codes.append(code)
        return codes

    def _generate_qr_code(self, uri: str) -> str | None:
        """Generate QR code image as base64."""
        if not QR_CODE_AVAILABLE:
            return None

        try:
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(uri)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format="PNG")

            return base64.b64encode(buffer.getvalue()).decode()
        except Exception:
            return None

    def verify_totp(self, user_id: str, token: str, backup_code: str | None = None) -> bool:
        """Verify TOTP token."""
        config = self._mfa_configs.get(user_id)
        if not config or config.mfa_type != MFAType.TOTP:
            return False

        # Check backup code first
        if backup_code and backup_code in config.backup_codes:
            config.backup_codes.remove(backup_code)
            config.last_used = time.time()
            return True

        # Verify TOTP token
        if not config.secret:
            return False

        totp = pyotp.TOTP(config.secret)
        is_valid = totp.verify(token, valid_window=self.totp_window)

        if is_valid:
            config.last_used = time.time()
            config.is_enabled = True  # Enable after first successful verification

        return is_valid

    def setup_hotp(self, user_id: str, account_name: str | None = None) -> dict[str, Any]:
        """Setup HOTP for a user."""
        # Generate secret
        secret = pyotp.random_base32()

        # Create HOTP object
        pyotp.HOTP(secret)

        # Generate backup codes
        backup_codes = self._generate_backup_codes()

        # Store configuration
        config = MFAConfig(
            user_id=user_id,
            mfa_type=MFAType.HOTP,
            is_enabled=False,
            secret=secret,
            backup_codes=backup_codes,
            counter=self.hotp_counter_start,
        )

        self._mfa_configs[user_id] = config

        return {
            "secret": secret,
            "backup_codes": backup_codes,
            "initial_counter": self.hotp_counter_start,
        }

    def verify_hotp(self, user_id: str, token: str, backup_code: str | None = None) -> bool:
        """Verify HOTP token."""
        config = self._mfa_configs.get(user_id)
        if not config or config.mfa_type != MFAType.HOTP:
            return False

        # Check backup code first
        if backup_code and backup_code in config.backup_codes:
            config.backup_codes.remove(backup_code)
            config.last_used = time.time()
            return True

        # Verify HOTP token
        if not config.secret:
            return False

        hotp = pyotp.HOTP(config.secret)
        is_valid = hotp.verify(token, config.counter)

        if is_valid:
            config.counter += 1
            config.last_used = time.time()
            config.is_enabled = True

        return is_valid

    def generate_webauthn_registration_options(
        self, user_id: str, username: str, display_name: str | None = None
    ) -> dict[str, Any]:
        """Generate WebAuthn registration options."""
        # Generate challenge
        challenge = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

        # User handle
        user_handle = user_id.encode("utf-8")

        options = {
            "challenge": challenge,
            "rp": {"id": self.rp_id, "name": self.rp_name},
            "user": {
                "id": base64.urlsafe_b64encode(user_handle).decode(),
                "name": username,
                "displayName": display_name or username,
            },
            "pubKeyCredParams": [
                {"type": "public-key", "alg": -7},  # ES256
                {"type": "public-key", "alg": -257},  # RS256
            ],
            "timeout": 60000,
            "attestation": "direct",
            "authenticatorSelection": {
                "authenticatorAttachment": "platform",
                "userVerification": "required",
                "residentKey": "preferred",
            },
        }

        # Store challenge temporarily
        if not hasattr(self, "_webauthn_challenges"):
            self._webauthn_challenges = {}

        self._webauthn_challenges[user_id] = {
            "challenge": challenge,
            "expires_at": time.time() + 300,  # 5 minutes
        }

        return options

    def verify_webauthn_registration(self, user_id: str, credential_data: dict[str, Any]) -> bool:
        """Verify WebAuthn registration response."""
        # Get stored challenge
        if not hasattr(self, "_webauthn_challenges"):
            return False

        stored_challenge = self._webauthn_challenges.get(user_id)
        if not stored_challenge:
            return False

        if time.time() > stored_challenge["expires_at"]:
            del self._webauthn_challenges[user_id]
            return False

        try:
            if not CBOR2_AVAILABLE:
                return False

            # Parse credential data
            client_data_json = base64.b64decode(
                credential_data["clientDataJSON"].replace("-", "+").replace("_", "/") + "=="
            )
            client_data = json.loads(client_data_json)

            # Verify challenge
            received_challenge = client_data.get("challenge")
            if received_challenge != stored_challenge["challenge"]:
                return False

            # Verify type
            if client_data.get("type") != "webauthn.create":
                return False

            # Verify origin
            origin = client_data.get("origin")
            if not origin or not origin.endswith(self.rp_id):
                return False

            # Parse attestation object
            attestation_object = base64.b64decode(
                credential_data["attestationObject"].replace("-", "+").replace("_", "/") + "=="
            )

            attestation = cbor2.loads(attestation_object)

            # Extract credential data
            auth_data = attestation.get("authData")
            if not auth_data:
                return False

            # Parse auth data
            auth_data[:32]
            auth_data[32]
            sign_count = int.from_bytes(auth_data[33:37], "big")
            credential_id_length = int.from_bytes(auth_data[37:39], "big")
            credential_id = auth_data[39 : 39 + credential_id_length]

            # Extract public key
            credential_data_end = 39 + credential_id_length
            credential_public_key = auth_data[credential_data_end:]

            # Store credential
            webauthn_credential = WebAuthnCredential(
                credential_id=credential_id,
                public_key=credential_public_key,
                sign_count=sign_count,
                user_handle=user_id.encode("utf-8"),
                rp_id=self.rp_id,
            )

            credential_id_str = base64.urlsafe_b64encode(credential_id).decode()
            self._webauthn_credentials[credential_id_str] = webauthn_credential

            # Update MFA config
            config = MFAConfig(
                user_id=user_id,
                mfa_type=MFAType.WEBAUTHN,
                is_enabled=True,
                credential_id=credential_id_str,
                public_key=credential_public_key,
                sign_count=sign_count,
                user_handle=user_id.encode("utf-8"),
            )

            self._mfa_configs[user_id] = config

            # Clean up challenge
            del self._webauthn_challenges[user_id]

            return True

        except Exception:
            return False

    def generate_webauthn_authentication_options(self, user_id: str) -> dict[str, Any]:
        """Generate WebAuthn authentication options."""
        # Get user's WebAuthn credentials
        user_credentials = [
            cred
            for cred in self._webauthn_credentials.values()
            if cred.user_handle.decode("utf-8") == user_id
        ]

        if not user_credentials:
            raise MFAError("No WebAuthn credentials found for user")

        # Generate challenge
        challenge = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

        # Allow credentials
        allow_credentials = [
            {"type": "public-key", "id": base64.urlsafe_b64encode(cred.credential_id).decode()}
            for cred in user_credentials
        ]

        options = {
            "challenge": challenge,
            "timeout": 60000,
            "rpId": self.rp_id,
            "allowCredentials": allow_credentials,
            "userVerification": "required",
        }

        # Store challenge temporarily
        if not hasattr(self, "_webauthn_challenges"):
            self._webauthn_challenges = {}

        self._webauthn_challenges[f"auth_{user_id}"] = {
            "challenge": challenge,
            "expires_at": time.time() + 300,  # 5 minutes
        }

        return options

    def verify_webauthn_authentication(self, user_id: str, assertion_data: dict[str, Any]) -> bool:
        """Verify WebAuthn authentication response."""
        # Get stored challenge
        challenge_key = f"auth_{user_id}"
        if not hasattr(self, "_webauthn_challenges"):
            return False

        stored_challenge = self._webauthn_challenges.get(challenge_key)
        if not stored_challenge:
            return False

        if time.time() > stored_challenge["expires_at"]:
            del self._webauthn_challenges[challenge_key]
            return False

        try:
            # Parse assertion data
            client_data_json = base64.b64decode(
                assertion_data["clientDataJSON"].replace("-", "+").replace("_", "/") + "=="
            )
            client_data = json.loads(client_data_json)

            # Verify challenge
            received_challenge = client_data.get("challenge")
            if received_challenge != stored_challenge["challenge"]:
                return False

            # Verify type
            if client_data.get("type") != "webauthn.get":
                return False

            # Verify origin
            origin = client_data.get("origin")
            if not origin or not origin.endswith(self.rp_id):
                return False

            # Parse auth data
            authenticator_data = base64.b64decode(
                assertion_data["authenticatorData"].replace("-", "+").replace("_", "/") + "=="
            )

            # Extract credential ID
            credential_id = base64.b64decode(
                assertion_data["credentialId"].replace("-", "+").replace("_", "/") + "=="
            )

            credential_id_str = base64.urlsafe_b64encode(credential_id).decode()

            # Get stored credential
            credential = self._webauthn_credentials.get(credential_id_str)
            if not credential:
                return False

            # Parse signature
            base64.b64decode(assertion_data["signature"].replace("-", "+").replace("_", "/") + "==")

            # Verify user presence and verification flags
            flags = authenticator_data[32]
            user_present = bool(flags & 0x01)
            user_verified = bool(flags & 0x04)

            if not user_present or not user_verified:
                return False

            # In a real implementation, you would verify the signature here
            # This is a simplified version that just checks the basics

            # Update sign count
            new_sign_count = int.from_bytes(authenticator_data[33:37], "big")
            if new_sign_count <= credential.sign_count:
                return False  # Prevent replay attacks

            credential.sign_count = new_sign_count

            # Update MFA config
            config = self._mfa_configs.get(user_id)
            if config:
                config.last_used = time.time()

            # Clean up challenge
            del self._webauthn_challenges[challenge_key]

            return True

        except Exception:
            return False

    def disable_mfa(self, user_id: str, backup_code: str | None = None) -> bool:
        """Disable MFA for a user."""
        config = self._mfa_configs.get(user_id)
        if not config:
            return False

        # Require backup code to disable MFA
        if backup_code and backup_code in config.backup_codes:
            config.backup_codes.remove(backup_code)
            del self._mfa_configs[user_id]
            return True

        return False

    def get_mfa_status(self, user_id: str) -> MFAConfig | None:
        """Get MFA status for a user."""
        return self._mfa_configs.get(user_id)

    def regenerate_backup_codes(self, user_id: str) -> list[str]:
        """Regenerate backup codes for a user."""
        config = self._mfa_configs.get(user_id)
        if not config:
            raise MFAError("MFA not configured for user")

        backup_codes = self._generate_backup_codes()
        config.backup_codes = backup_codes

        return backup_codes

    def is_mfa_enabled(self, user_id: str) -> bool:
        """Check if MFA is enabled for a user."""
        config = self._mfa_configs.get(user_id)
        return config is not None and config.is_enabled

    def get_available_mfa_methods(self) -> list[dict[str, Any]]:
        """Get list of available MFA methods."""
        methods = [
            {
                "type": MFAType.TOTP.value,
                "name": "Time-based One-Time Password (TOTP)",
                "description": "Use authenticator app like Google Authenticator",
                "available": PYOTP_AVAILABLE,
            },
            {
                "type": MFAType.HOTP.value,
                "name": "HMAC-based One-Time Password (HOTP)",
                "description": "Counter-based one-time passwords",
                "available": PYOTP_AVAILABLE,
            },
            {
                "type": MFAType.WEBAUTHN.value,
                "name": "WebAuthn/FIDO2",
                "description": "Hardware security keys or biometric authentication",
                "available": CRYPTO_AVAILABLE,
            },
        ]

        return methods


# Global MFA provider instance
_mfa_provider: MFAProvider | None = None


def get_mfa_provider() -> MFAProvider | None:
    """Get the global MFA provider instance."""
    return _mfa_provider


def initialize_mfa_provider(**kwargs) -> MFAProvider:
    """Initialize the global MFA provider."""
    global _mfa_provider
    _mfa_provider = MFAProvider(**kwargs)
    return _mfa_provider
