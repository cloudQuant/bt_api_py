"""OAuth 2.0 + OpenID Connect Provider for Financial Authentication.

Implements secure OAuth 2.0 authorization with PKCE, token rotation,
and financial industry compliance (PSD2, Open Banking).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from numbers import Integral
from pathlib import Path
from typing import Any

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: F401

    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

from bt_api_py.exceptions import BtApiError


class OAuthError(BtApiError):
    """OAuth 2.0 related errors."""


class GrantType(Enum):
    """OAuth 2.0 grant types."""

    AUTHORIZATION_CODE = "authorization_code"
    CLIENT_CREDENTIALS = "client_credentials"
    REFRESH_TOKEN = "refresh_token"
    PKCE = "authorization_code_with_pkce"


class TokenType(Enum):
    """Token types."""

    BEARER = "Bearer"
    JWT = "JWT"


@dataclass
class OAuthClient:
    """OAuth 2.0 client application."""

    client_id: str
    client_secret: str
    redirect_uris: list[str]
    scopes: set[str]
    grant_types: set[GrantType]
    is_confidential: bool = True
    created_at: float = field(default_factory=time.time)

    def can_use_grant_type(self, grant_type: GrantType) -> bool:
        """Check if client can use specific grant type."""
        return grant_type in self.grant_types

    def has_scope(self, scope: str) -> bool:
        """Check if client has requested scope."""
        return scope in self.scopes


@dataclass
class OAuthUser:
    """OAuth 2.0 resource owner."""

    user_id: str
    username: str
    email: str
    is_active: bool = True
    mfa_enabled: bool = False
    totp_secret: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class AuthorizationCode:
    """OAuth 2.0 authorization code."""

    code: str
    client_id: str
    user_id: str
    redirect_uri: str
    scopes: set[str]
    code_challenge: str | None = None
    code_challenge_method: str | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 600)  # 10 minutes
    used: bool = False

    def is_expired(self) -> bool:
        """Check if authorization code has expired."""
        return time.time() > self.expires_at

    def is_valid(self) -> bool:
        """Check if authorization code is valid."""
        return not self.used and not self.is_expired()


@dataclass
class AccessToken:
    """OAuth 2.0 access token."""

    token: str
    token_type: TokenType
    client_id: str
    user_id: str | None
    scopes: set[str]
    expires_at: float
    created_at: float = field(default_factory=time.time)
    refresh_token: str | None = None

    def is_expired(self) -> bool:
        """Check if access token has expired."""
        return time.time() > self.expires_at

    def is_valid(self) -> bool:
        """Check if access token is valid."""
        return not self.is_expired()

    def get_expires_in(self) -> int:
        """Get remaining time in seconds."""
        remaining = int(self.expires_at - time.time())
        return max(0, remaining)


@dataclass
class RefreshToken:
    """OAuth 2.0 refresh token."""

    token: str
    client_id: str
    user_id: str
    scopes: set[str]
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 2592000)  # 30 days
    used: bool = False

    def is_expired(self) -> bool:
        """Check if refresh token has expired."""
        return time.time() > self.expires_at

    def is_valid(self) -> bool:
        """Check if refresh token is valid."""
        return not self.used and not self.is_expired()


class OAuth2Provider:
    """OAuth 2.0 + OpenID Connect provider."""

    def __init__(
        self,
        issuer_url: str,
        private_key_path: str | None = None,
        token_lifetime: int = 3600,  # 1 hour
        refresh_token_lifetime: int = 2592000,  # 30 days
        enable_pkce: bool = True,
        enable_token_rotation: bool = True,
    ):
        """Initialize OAuth 2.0 provider."""
        self.issuer_url = self._require_text("issuer_url", issuer_url)
        self.token_lifetime = self._require_positive_int("token_lifetime", token_lifetime)
        self.refresh_token_lifetime = self._require_positive_int(
            "refresh_token_lifetime", refresh_token_lifetime
        )
        self.enable_pkce = self._require_bool("enable_pkce", enable_pkce)
        self.enable_token_rotation = self._require_bool(
            "enable_token_rotation", enable_token_rotation
        )

        # Storage
        self._clients: dict[str, OAuthClient] = {}
        self._users: dict[str, OAuthUser] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}

        # Cryptographic keys
        self._private_key = self._load_or_generate_key(private_key_path)
        self._public_key = self._private_key.public_key()

    @staticmethod
    def _require_text(field_name: str, value: str) -> str:
        if not isinstance(value, str):
            raise OAuthError(f"{field_name} must be a non-empty string")
        text = value.strip()
        if not text:
            raise OAuthError(f"{field_name} must be a non-empty string")
        return text

    @staticmethod
    def _require_positive_int(field_name: str, value: int) -> int:
        if isinstance(value, bool):
            raise OAuthError(f"{field_name} must be positive")
        if isinstance(value, Integral):
            numeric = int(value)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                raise OAuthError(f"{field_name} must be positive")
            try:
                numeric = int(text)
            except ValueError as exc:
                raise OAuthError(f"{field_name} must be positive") from exc
        else:
            raise OAuthError(f"{field_name} must be positive")
        if numeric <= 0:
            raise OAuthError(f"{field_name} must be positive")
        return numeric

    @staticmethod
    def _require_bool(field_name: str, value: bool) -> bool:
        if not isinstance(value, bool):
            raise OAuthError(f"{field_name} must be a boolean")
        return value

    @classmethod
    def _normalize_scopes(cls, scopes: Iterable[str] | None) -> set[str]:
        if scopes is None:
            return set()
        if isinstance(scopes, str):
            raise OAuthError("scopes must be an iterable of strings")
        normalized: set[str] = set()
        try:
            iterator = iter(scopes)
        except TypeError as exc:
            raise OAuthError("scopes must be an iterable of strings") from exc
        for scope in iterator:
            normalized.add(cls._require_text("scope", scope))
        return normalized

    @classmethod
    def _normalize_redirect_uris(cls, redirect_uris: Iterable[str]) -> list[str]:
        if isinstance(redirect_uris, str):
            raise OAuthError("redirect_uris must be an iterable of strings")
        try:
            iterator = iter(redirect_uris)
        except TypeError as exc:
            raise OAuthError("redirect_uris must be an iterable of strings") from exc
        normalized = [cls._require_text("redirect_uri", uri) for uri in iterator]
        if not normalized:
            raise OAuthError("redirect_uris must not be empty")
        return normalized

    @classmethod
    def _normalize_grant_types(cls, grant_types: Iterable[GrantType | str]) -> set[GrantType]:
        if isinstance(grant_types, (str, bytes)):
            raise OAuthError("grant_types must be an iterable of GrantType values")
        normalized: set[GrantType] = set()
        try:
            iterator = iter(grant_types)
        except TypeError as exc:
            raise OAuthError("grant_types must be an iterable of GrantType values") from exc
        for grant_type in iterator:
            if isinstance(grant_type, GrantType):
                normalized.add(grant_type)
                continue
            try:
                normalized.add(GrantType(cls._require_text("grant_type", grant_type)))
            except ValueError as exc:
                raise OAuthError(f"Unsupported grant type: {grant_type}") from exc
        if not normalized:
            raise OAuthError("grant_types must not be empty")
        return normalized

    @staticmethod
    def _validate_client_scopes(client: OAuthClient, scopes: set[str]) -> None:
        if scopes and not scopes.issubset(client.scopes):
            raise OAuthError("Invalid scopes")

    @staticmethod
    def _supports_refresh_grant(client: OAuthClient) -> bool:
        return bool(
            client.grant_types
            & {GrantType.REFRESH_TOKEN, GrantType.AUTHORIZATION_CODE, GrantType.PKCE}
        )

    def _get_active_user(self, user_id: str) -> OAuthUser:
        user = self._users.get(self._require_text("user_id", user_id))
        if not user:
            raise OAuthError("User not found")
        if not user.is_active:
            raise OAuthError("User is inactive")
        return user

    def _issue_access_token(
        self,
        *,
        client_id: str,
        user_id: str | None,
        scopes: set[str],
        issue_refresh_token: bool,
    ) -> AccessToken:
        token = secrets.token_urlsafe(32)
        access_token = AccessToken(
            token=token,
            token_type=TokenType.BEARER,
            client_id=client_id,
            user_id=user_id,
            scopes=set(scopes),
            expires_at=time.time() + self.token_lifetime,
        )
        if issue_refresh_token:
            if user_id is None:
                raise OAuthError("User ID required for refresh token issuance")
            refresh_token = self._generate_refresh_token(client_id, user_id, scopes)
            access_token.refresh_token = refresh_token.token
        self._access_tokens[token] = access_token
        return access_token

    def _load_or_generate_key(self, key_path: str | None):
        """Load or generate RSA key for JWT signing."""
        if not CRYPTO_AVAILABLE:
            raise OAuthError("cryptography package is required for OAuth2Provider")
        if key_path and Path(key_path).exists():
            with Path(key_path).open("rb") as f:
                return serialization.load_pem_private_key(
                    f.read(), password=None, backend=default_backend()
                )
        else:
            # Generate new key
            private_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048, backend=default_backend()
            )

            # Save if path provided
            if key_path:
                pem = private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
                key_file = Path(key_path)
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.write_bytes(pem)

            return private_key

    def register_client(
        self,
        client_id: str,
        client_secret: str,
        redirect_uris: list[str],
        scopes: set[str],
        grant_types: set[GrantType],
        is_confidential: bool = True,
    ) -> OAuthClient:
        """Register an OAuth client."""
        client = OAuthClient(
            client_id=self._require_text("client_id", client_id),
            client_secret=self._require_text("client_secret", client_secret),
            redirect_uris=self._normalize_redirect_uris(redirect_uris),
            scopes=self._normalize_scopes(scopes),
            grant_types=self._normalize_grant_types(grant_types),
            is_confidential=self._require_bool("is_confidential", is_confidential),
        )

        self._clients[client.client_id] = client
        return client

    def register_user(
        self, user_id: str, username: str, email: str, mfa_enabled: bool = False
    ) -> OAuthUser:
        """Register a resource owner."""
        user = OAuthUser(
            user_id=self._require_text("user_id", user_id),
            username=self._require_text("username", username),
            email=self._require_text("email", email),
            mfa_enabled=self._require_bool("mfa_enabled", mfa_enabled),
        )

        self._users[user.user_id] = user
        return user

    def generate_authorization_code(
        self,
        client_id: str,
        user_id: str,
        redirect_uri: str,
        scopes: set[str],
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
    ) -> str:
        """Generate authorization code for authorization flow."""
        client_id = self._require_text("client_id", client_id)
        user_id = self._require_text("user_id", user_id)
        redirect_uri = self._require_text("redirect_uri", redirect_uri)
        scopes = self._normalize_scopes(scopes)

        # Validate client
        client = self._clients.get(client_id)
        if not client:
            raise OAuthError("Invalid client")

        user = self._users.get(user_id)
        if not user:
            raise OAuthError("User not found")
        if not user.is_active:
            raise OAuthError("User is inactive")

        # Validate redirect URI
        if redirect_uri not in client.redirect_uris:
            raise OAuthError("Invalid redirect URI")

        self._validate_client_scopes(client, scopes)

        # Validate PKCE
        if code_challenge and not self.enable_pkce:
            raise OAuthError("PKCE not supported")
        if code_challenge_method and not code_challenge:
            raise OAuthError("Code challenge required")
        if code_challenge_method not in (None, "plain", "S256"):
            raise OAuthError(f"Unsupported PKCE method: {code_challenge_method}")

        # Generate code
        code = secrets.token_urlsafe(32)

        auth_code = AuthorizationCode(
            code=code,
            client_id=client_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            scopes=scopes,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )

        self._auth_codes[code] = auth_code
        return code

    def validate_authorization_code(
        self, code: str, client_id: str, redirect_uri: str, code_verifier: str | None = None
    ) -> AuthorizationCode:
        """Validate authorization code and return auth code object."""
        code = self._require_text("code", code)
        client_id = self._require_text("client_id", client_id)
        redirect_uri = self._require_text("redirect_uri", redirect_uri)
        if code_verifier is not None:
            code_verifier = self._require_text("code_verifier", code_verifier)
        auth_code = self._auth_codes.get(code)
        if not auth_code:
            raise OAuthError("Invalid authorization code")

        if not auth_code.is_valid():
            raise OAuthError("Authorization code expired or used")

        if auth_code.client_id != client_id:
            raise OAuthError("Client ID mismatch")

        if auth_code.redirect_uri != redirect_uri:
            raise OAuthError("Redirect URI mismatch")

        # Validate PKCE if present
        if auth_code.code_challenge:
            if not code_verifier:
                raise OAuthError("Code verifier required")

            if not self._validate_pkce(
                code_verifier, auth_code.code_challenge, auth_code.code_challenge_method
            ):
                raise OAuthError("Invalid code verifier")

        # Mark as used
        auth_code.used = True
        return auth_code

    def _validate_pkce(
        self, code_verifier: str, code_challenge: str, code_challenge_method: str | None
    ) -> bool:
        """Validate PKCE code verifier."""
        method = code_challenge_method or "plain"

        if method == "plain":
            return code_verifier == code_challenge
        elif method == "S256":
            # SHA256 of code_verifier, base64url-encoded
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            return challenge == code_challenge
        else:
            raise OAuthError(f"Unsupported PKCE method: {method}")

    def generate_access_token(
        self, client_id: str, user_id: str | None, scopes: set[str], grant_type: GrantType
    ) -> AccessToken:
        """Generate access token."""
        client_id = self._require_text("client_id", client_id)
        scopes = self._normalize_scopes(scopes)

        # Validate client
        client = self._clients.get(client_id)
        if not client:
            raise OAuthError("Invalid client")

        if not client.can_use_grant_type(grant_type):
            raise OAuthError("Grant type not allowed for client")

        self._validate_client_scopes(client, scopes)
        if grant_type in {GrantType.AUTHORIZATION_CODE, GrantType.PKCE, GrantType.REFRESH_TOKEN}:
            if user_id is None:
                raise OAuthError("User ID required for grant type")
            user_id = self._require_text("user_id", user_id)

        return self._issue_access_token(
            client_id=client_id,
            user_id=user_id,
            scopes=scopes,
            issue_refresh_token=grant_type in {GrantType.AUTHORIZATION_CODE, GrantType.PKCE},
        )

    def _generate_refresh_token(
        self, client_id: str, user_id: str, scopes: set[str]
    ) -> RefreshToken:
        """Generate refresh token."""
        token = secrets.token_urlsafe(32)

        refresh_token = RefreshToken(
            token=token,
            client_id=client_id,
            user_id=self._require_text("user_id", user_id),
            scopes=set(scopes),
            expires_at=time.time() + self.refresh_token_lifetime,
        )

        self._refresh_tokens[token] = refresh_token
        return refresh_token

    def validate_access_token(
        self, token: str, required_scopes: set[str] | None = None
    ) -> AccessToken:
        """Validate access token."""
        token = self._require_text("token", token)
        required_scopes = self._normalize_scopes(required_scopes)
        access_token = self._access_tokens.get(token)
        if not access_token:
            raise OAuthError("Invalid access token")

        if not access_token.is_valid():
            raise OAuthError("Access token expired")

        # Check scopes if required
        if required_scopes and not required_scopes.issubset(access_token.scopes):
            raise OAuthError("Insufficient scopes")

        return access_token

    def refresh_access_token(self, refresh_token: str, client_id: str) -> AccessToken:
        """Refresh access token using refresh token."""
        client_id = self._require_text("client_id", client_id)
        token_obj = self._refresh_tokens.get(refresh_token)
        if not token_obj:
            raise OAuthError("Invalid refresh token")

        if not token_obj.is_valid():
            raise OAuthError("Refresh token expired or used")

        if token_obj.client_id != client_id:
            raise OAuthError("Client ID mismatch")

        client = self._clients.get(client_id)
        if not client:
            raise OAuthError("Invalid client")
        if not self._supports_refresh_grant(client):
            raise OAuthError("Grant type not allowed for client")

        self._get_active_user(token_obj.user_id)

        if self.enable_token_rotation:
            token_obj.used = True

        access_token = self._issue_access_token(
            client_id=token_obj.client_id,
            user_id=token_obj.user_id,
            scopes=set(token_obj.scopes),
            issue_refresh_token=self.enable_token_rotation,
        )
        if not self.enable_token_rotation:
            access_token.refresh_token = token_obj.token

        return access_token

    def revoke_token(self, token: str) -> bool:
        """Revoke access or refresh token."""
        # Check access token
        if token in self._access_tokens:
            del self._access_tokens[token]
            return True

        # Check refresh token
        if token in self._refresh_tokens:
            self._refresh_tokens[token].used = True
            return True

        return False

    def introspect_token(self, token: str) -> dict[str, Any]:
        """Introspect token (RFC 7662)."""
        # Check access token
        if token in self._access_tokens:
            access_token = self._access_tokens[token]
            return {
                "active": access_token.is_valid(),
                "client_id": access_token.client_id,
                "user_id": access_token.user_id,
                "scope": " ".join(sorted(access_token.scopes)),
                "exp": int(access_token.expires_at),
                "iat": int(access_token.created_at),
                "token_type": access_token.token_type.value,
            }

        # Check refresh token
        if token in self._refresh_tokens:
            refresh_token = self._refresh_tokens[token]
            return {
                "active": refresh_token.is_valid(),
                "client_id": refresh_token.client_id,
                "user_id": refresh_token.user_id,
                "scope": " ".join(sorted(refresh_token.scopes)),
                "exp": int(refresh_token.expires_at),
                "iat": int(refresh_token.created_at),
                "token_type": "refresh_token",
            }

        return {"active": False}

    def generate_jwt_id_token(self, user_id: str, client_id: str, scopes: set[str]) -> str:
        """Generate OpenID Connect ID token."""
        user_id = self._require_text("user_id", user_id)
        client_id = self._require_text("client_id", client_id)
        scopes = self._normalize_scopes(scopes)

        user = self._get_active_user(user_id)
        client = self._clients.get(client_id)
        if not client:
            raise OAuthError("Invalid client")
        self._validate_client_scopes(client, scopes)

        # JWT header
        header = {
            "alg": "RS256",
            "typ": "JWT",
            "kid": "1",  # Key ID
        }

        # JWT payload
        now = int(time.time())
        payload = {
            "iss": self.issuer_url,
            "sub": user_id,
            "aud": client_id,
            "exp": now + 3600,  # 1 hour
            "iat": now,
            "preferred_username": user.username,
            "email": user.email,
            "email_verified": True,
            "scope": " ".join(sorted(scopes)),
        }

        # Sign JWT
        import jwt

        return jwt.encode(payload, self._private_key, algorithm="RS256", headers=header)

    def validate_jwt(self, token: str, audience: str | None = None) -> dict[str, Any]:
        """Validate JWT token."""
        try:
            import jwt

            # Get public key for verification
            public_key_pem = self._public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )

            # Verify and decode
            payload = jwt.decode(
                token,
                public_key_pem,
                algorithms=["RS256"],
                audience=audience,
                issuer=self.issuer_url,
            )

            return payload

        except Exception as e:
            raise OAuthError(f"Invalid JWT: {e}") from e

    def get_client_info(self, client_id: str) -> OAuthClient | None:
        """Get client information."""
        return self._clients.get(client_id)

    def get_user_info(self, user_id: str) -> OAuthUser | None:
        """Get user information."""
        return self._users.get(user_id)

    def cleanup_expired_tokens(self) -> dict[str, int]:
        """Clean up expired tokens."""
        cleanup_counts = {"authorization_codes": 0, "access_tokens": 0, "refresh_tokens": 0}

        # Clean expired or already-used authorization codes
        expired_codes = [
            code for code, auth_code in self._auth_codes.items() if not auth_code.is_valid()
        ]
        for code in expired_codes:
            del self._auth_codes[code]
            cleanup_counts["authorization_codes"] += 1

        # Clean expired access tokens
        expired_access = [
            token
            for token, access_token in self._access_tokens.items()
            if access_token.is_expired()
        ]
        for token in expired_access:
            del self._access_tokens[token]
            cleanup_counts["access_tokens"] += 1

        # Clean expired or revoked/used refresh tokens
        expired_refresh = [
            token
            for token, refresh_token in self._refresh_tokens.items()
            if not refresh_token.is_valid()
        ]
        for token in expired_refresh:
            del self._refresh_tokens[token]
            cleanup_counts["refresh_tokens"] += 1

        return cleanup_counts
