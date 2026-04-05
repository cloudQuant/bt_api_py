"""Tests for oauth2_provider module - pure local logic."""

from __future__ import annotations

import time

import pytest

from bt_api_py.security_compliance.auth.oauth2_provider import (
    AccessToken,
    AuthorizationCode,
    GrantType,
    OAuthClient,
    OAuthError,
    OAuthUser,
    RefreshToken,
    TokenType,
)

# Skip tests requiring cryptography if not available
pytest.importorskip("cryptography")

from bt_api_py.security_compliance.auth.oauth2_provider import OAuth2Provider


class TestGrantType:
    """Tests for GrantType enum."""

    def test_enum_values(self):
        """Test all enum values exist."""
        assert GrantType.AUTHORIZATION_CODE.value == "authorization_code"
        assert GrantType.CLIENT_CREDENTIALS.value == "client_credentials"
        assert GrantType.REFRESH_TOKEN.value == "refresh_token"
        assert GrantType.PKCE.value == "authorization_code_with_pkce"


class TestTokenType:
    """Tests for TokenType enum."""

    def test_enum_values(self):
        """Test all enum values exist."""
        assert TokenType.BEARER.value == "Bearer"
        assert TokenType.JWT.value == "JWT"


class TestOAuthClient:
    """Tests for OAuthClient dataclass."""

    def test_init(self):
        """Test initialization."""
        client = OAuthClient(
            client_id="test_client",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read", "write"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )

        assert client.client_id == "test_client"
        assert client.client_secret == "secret"
        assert client.redirect_uris == ["http://localhost/callback"]
        assert client.scopes == {"read", "write"}
        assert client.grant_types == {GrantType.AUTHORIZATION_CODE}
        assert client.is_confidential is True

    def test_can_use_grant_type(self):
        """Test grant type check."""
        client = OAuthClient(
            client_id="test",
            client_secret="secret",
            redirect_uris=["http://localhost"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE, GrantType.REFRESH_TOKEN},
        )

        assert client.can_use_grant_type(GrantType.AUTHORIZATION_CODE) is True
        assert client.can_use_grant_type(GrantType.CLIENT_CREDENTIALS) is False

    def test_has_scope(self):
        """Test scope check."""
        client = OAuthClient(
            client_id="test",
            client_secret="secret",
            redirect_uris=["http://localhost"],
            scopes={"read", "write"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )

        assert client.has_scope("read") is True
        assert client.has_scope("admin") is False


class TestOAuthUser:
    """Tests for OAuthUser dataclass."""

    def test_init(self):
        """Test initialization."""
        user = OAuthUser(user_id="user1", username="testuser", email="test@example.com")

        assert user.user_id == "user1"
        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.is_active is True
        assert user.mfa_enabled is False


class TestAuthorizationCode:
    """Tests for AuthorizationCode dataclass."""

    def test_init(self):
        """Test initialization."""
        code = AuthorizationCode(
            code="code123",
            client_id="client1",
            user_id="user1",
            redirect_uri="http://localhost/callback",
            scopes={"read"},
        )

        assert code.code == "code123"
        assert code.client_id == "client1"
        assert code.used is False

    def test_is_expired(self):
        """Test expiration check."""
        code = AuthorizationCode(
            code="code123",
            client_id="client1",
            user_id="user1",
            redirect_uri="http://localhost/callback",
            scopes={"read"},
            expires_at=time.time() - 1,  # Already expired
        )

        assert code.is_expired() is True

    def test_is_valid(self):
        """Test validity check."""
        code = AuthorizationCode(
            code="code123",
            client_id="client1",
            user_id="user1",
            redirect_uri="http://localhost/callback",
            scopes={"read"},
        )

        assert code.is_valid() is True

        code.used = True
        assert code.is_valid() is False


class TestAccessToken:
    """Tests for AccessToken dataclass."""

    def test_init(self):
        """Test initialization."""
        token = AccessToken(
            token="token123",
            token_type=TokenType.BEARER,
            client_id="client1",
            user_id="user1",
            scopes={"read"},
            expires_at=time.time() + 3600,
        )

        assert token.token == "token123"
        assert token.token_type == TokenType.BEARER
        assert token.refresh_token is None

    def test_is_expired(self):
        """Test expiration check."""
        token = AccessToken(
            token="token123",
            token_type=TokenType.BEARER,
            client_id="client1",
            user_id="user1",
            scopes={"read"},
            expires_at=time.time() - 1,
        )

        assert token.is_expired() is True

    def test_is_valid(self):
        """Test validity check."""
        token = AccessToken(
            token="token123",
            token_type=TokenType.BEARER,
            client_id="client1",
            user_id="user1",
            scopes={"read"},
            expires_at=time.time() + 3600,
        )

        assert token.is_valid() is True

    def test_get_expires_in(self):
        """Test expires_in calculation."""
        token = AccessToken(
            token="token123",
            token_type=TokenType.BEARER,
            client_id="client1",
            user_id="user1",
            scopes={"read"},
            expires_at=time.time() + 1800,
        )

        expires_in = token.get_expires_in()
        assert 1790 <= expires_in <= 1810  # Around 30 minutes

    def test_get_expires_in_expired(self):
        """Test expires_in for expired token."""
        token = AccessToken(
            token="token123",
            token_type=TokenType.BEARER,
            client_id="client1",
            user_id="user1",
            scopes={"read"},
            expires_at=time.time() - 100,
        )

        assert token.get_expires_in() == 0


class TestRefreshToken:
    """Tests for RefreshToken dataclass."""

    def test_init(self):
        """Test initialization."""
        token = RefreshToken(
            token="refresh123",
            client_id="client1",
            user_id="user1",
            scopes={"read"},
        )

        assert token.token == "refresh123"
        assert token.used is False

    def test_is_expired(self):
        """Test expiration check."""
        token = RefreshToken(
            token="refresh123",
            client_id="client1",
            user_id="user1",
            scopes={"read"},
            expires_at=time.time() - 1,
        )

        assert token.is_expired() is True

    def test_is_valid(self):
        """Test validity check."""
        token = RefreshToken(
            token="refresh123",
            client_id="client1",
            user_id="user1",
            scopes={"read"},
        )

        assert token.is_valid() is True

        token.used = True
        assert token.is_valid() is False


class TestOAuth2Provider:
    """Tests for OAuth2Provider class."""

    def test_init(self):
        """Test initialization."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        assert provider.issuer_url == "https://auth.example.com"
        assert provider.token_lifetime == 3600
        assert provider.enable_pkce is True

    def test_init_custom_params(self):
        """Test initialization with custom parameters."""
        provider = OAuth2Provider(
            issuer_url="https://auth.example.com",
            token_lifetime=7200,
            refresh_token_lifetime=604800,
            enable_pkce=False,
            enable_token_rotation=False,
        )

        assert provider.token_lifetime == 7200
        assert provider.refresh_token_lifetime == 604800
        assert provider.enable_pkce is False
        assert provider.enable_token_rotation is False

    def test_init_invalid_issuer_url(self):
        """Test initialization with invalid issuer URL."""
        with pytest.raises(OAuthError, match="issuer_url must be a non-empty string"):
            OAuth2Provider(issuer_url="")

    def test_init_invalid_token_lifetime(self):
        """Test initialization with invalid token lifetime."""
        with pytest.raises(OAuthError, match="token_lifetime must be positive"):
            OAuth2Provider(issuer_url="https://auth.example.com", token_lifetime=0)

    def test_init_invalid_token_lifetime_bool(self):
        """Test initialization with bool as token lifetime."""
        with pytest.raises(OAuthError, match="token_lifetime must be positive"):
            OAuth2Provider(issuer_url="https://auth.example.com", token_lifetime=True)  # type: ignore

    def test_init_invalid_enable_pkce(self):
        """Test initialization with invalid enable_pkce."""
        with pytest.raises(OAuthError, match="enable_pkce must be a boolean"):
            OAuth2Provider(issuer_url="https://auth.example.com", enable_pkce="yes")  # type: ignore

    def test_register_client(self):
        """Test client registration."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        client = provider.register_client(
            client_id="client1",
            client_secret="secret1",
            redirect_uris=["http://localhost/callback"],
            scopes={"read", "write"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )

        assert client.client_id == "client1"
        assert provider.get_client_info("client1") == client

    def test_register_client_invalid_id(self):
        """Test client registration with invalid ID."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        with pytest.raises(OAuthError, match="client_id must be a non-empty string"):
            provider.register_client(
                client_id="",
                client_secret="secret",
                redirect_uris=["http://localhost"],
                scopes={"read"},
                grant_types={GrantType.AUTHORIZATION_CODE},
            )

    def test_register_client_invalid_scopes_string(self):
        """Test client registration with string scopes."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        with pytest.raises(OAuthError, match="scopes must be an iterable"):
            provider.register_client(
                client_id="client1",
                client_secret="secret",
                redirect_uris=["http://localhost"],
                scopes="read",  # type: ignore
                grant_types={GrantType.AUTHORIZATION_CODE},
            )

    def test_register_client_empty_redirect_uris(self):
        """Test client registration with empty redirect URIs."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        with pytest.raises(OAuthError, match="redirect_uris must not be empty"):
            provider.register_client(
                client_id="client1",
                client_secret="secret",
                redirect_uris=[],
                scopes={"read"},
                grant_types={GrantType.AUTHORIZATION_CODE},
            )

    def test_register_client_invalid_grant_types_string(self):
        """Test client registration with string grant types."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        with pytest.raises(OAuthError, match="grant_types must be an iterable"):
            provider.register_client(
                client_id="client1",
                client_secret="secret",
                redirect_uris=["http://localhost"],
                scopes={"read"},
                grant_types="authorization_code",  # type: ignore
            )

    def test_register_user(self):
        """Test user registration."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        user = provider.register_user(
            user_id="user1", username="testuser", email="test@example.com"
        )

        assert user.user_id == "user1"
        assert provider.get_user_info("user1") == user

    def test_register_user_invalid_id(self):
        """Test user registration with invalid ID."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        with pytest.raises(OAuthError, match="user_id must be a non-empty string"):
            provider.register_user(user_id="", username="test", email="test@example.com")

    def test_generate_authorization_code(self):
        """Test authorization code generation."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )
        provider.register_user(user_id="user1", username="test", email="test@example.com")

        code = provider.generate_authorization_code(
            client_id="client1",
            user_id="user1",
            redirect_uri="http://localhost/callback",
            scopes={"read"},
        )

        assert code is not None
        assert len(code) > 10

    def test_generate_authorization_code_invalid_client(self):
        """Test authorization code with invalid client."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        with pytest.raises(OAuthError, match="Invalid client"):
            provider.generate_authorization_code(
                client_id="unknown",
                user_id="user1",
                redirect_uri="http://localhost/callback",
                scopes={"read"},
            )

    def test_generate_authorization_code_invalid_redirect_uri(self):
        """Test authorization code with invalid redirect URI."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )
        provider.register_user(user_id="user1", username="test", email="test@example.com")

        with pytest.raises(OAuthError, match="Invalid redirect URI"):
            provider.generate_authorization_code(
                client_id="client1",
                user_id="user1",
                redirect_uri="http://evil.com/callback",
                scopes={"read"},
            )

    def test_generate_authorization_code_pkce_not_supported(self):
        """Test authorization code with PKCE when not supported."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com", enable_pkce=False)
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )
        provider.register_user(user_id="user1", username="test", email="test@example.com")

        with pytest.raises(OAuthError, match="PKCE not supported"):
            provider.generate_authorization_code(
                client_id="client1",
                user_id="user1",
                redirect_uri="http://localhost/callback",
                scopes={"read"},
                code_challenge="challenge123",
            )

    def test_validate_authorization_code(self):
        """Test authorization code validation."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )
        provider.register_user(user_id="user1", username="test", email="test@example.com")

        code = provider.generate_authorization_code(
            client_id="client1",
            user_id="user1",
            redirect_uri="http://localhost/callback",
            scopes={"read"},
        )

        auth_code = provider.validate_authorization_code(
            code=code, client_id="client1", redirect_uri="http://localhost/callback"
        )

        assert auth_code.code == code
        assert auth_code.used is True  # Should be marked as used

    def test_validate_authorization_code_invalid(self):
        """Test validation of invalid authorization code."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        with pytest.raises(OAuthError, match="Invalid authorization code"):
            provider.validate_authorization_code(
                code="invalid", client_id="client1", redirect_uri="http://localhost/callback"
            )

    def test_generate_access_token(self):
        """Test access token generation."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.CLIENT_CREDENTIALS},
        )

        token = provider.generate_access_token(
            client_id="client1",
            user_id=None,
            scopes={"read"},
            grant_type=GrantType.CLIENT_CREDENTIALS,
        )

        assert token.token is not None
        assert token.token_type == TokenType.BEARER
        assert token.refresh_token is None  # No refresh for client credentials

    def test_generate_access_token_with_refresh(self):
        """Test access token generation with refresh token."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )
        provider.register_user(user_id="user1", username="test", email="test@example.com")

        token = provider.generate_access_token(
            client_id="client1",
            user_id="user1",
            scopes={"read"},
            grant_type=GrantType.AUTHORIZATION_CODE,
        )

        assert token.refresh_token is not None

    def test_generate_access_token_invalid_client(self):
        """Test access token generation with invalid client."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        with pytest.raises(OAuthError, match="Invalid client"):
            provider.generate_access_token(
                client_id="unknown",
                user_id=None,
                scopes={"read"},
                grant_type=GrantType.CLIENT_CREDENTIALS,
            )

    def test_validate_access_token(self):
        """Test access token validation."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read", "write"},
            grant_types={GrantType.CLIENT_CREDENTIALS},
        )

        token = provider.generate_access_token(
            client_id="client1",
            user_id=None,
            scopes={"read"},
            grant_type=GrantType.CLIENT_CREDENTIALS,
        )

        validated = provider.validate_access_token(token.token)

        assert validated.token == token.token

    def test_validate_access_token_invalid(self):
        """Test validation of invalid access token."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        with pytest.raises(OAuthError, match="Invalid access token"):
            provider.validate_access_token("invalid_token")

    def test_validate_access_token_insufficient_scopes(self):
        """Test access token validation with insufficient scopes."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.CLIENT_CREDENTIALS},
        )

        token = provider.generate_access_token(
            client_id="client1",
            user_id=None,
            scopes={"read"},
            grant_type=GrantType.CLIENT_CREDENTIALS,
        )

        with pytest.raises(OAuthError, match="Insufficient scopes"):
            provider.validate_access_token(token.token, required_scopes={"admin"})

    def test_refresh_access_token(self):
        """Test access token refresh."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )
        provider.register_user(user_id="user1", username="test", email="test@example.com")

        token = provider.generate_access_token(
            client_id="client1",
            user_id="user1",
            scopes={"read"},
            grant_type=GrantType.AUTHORIZATION_CODE,
        )

        new_token = provider.refresh_access_token(
            refresh_token=token.refresh_token, client_id="client1"
        )

        assert new_token.token != token.token

    def test_refresh_access_token_invalid(self):
        """Test refresh with invalid token."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        with pytest.raises(OAuthError, match="Invalid refresh token"):
            provider.refresh_access_token(refresh_token="invalid", client_id="client1")

    def test_revoke_token_access(self):
        """Test revoking access token."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.CLIENT_CREDENTIALS},
        )

        token = provider.generate_access_token(
            client_id="client1",
            user_id=None,
            scopes={"read"},
            grant_type=GrantType.CLIENT_CREDENTIALS,
        )

        result = provider.revoke_token(token.token)

        assert result is True
        with pytest.raises(OAuthError, match="Invalid access token"):
            provider.validate_access_token(token.token)

    def test_revoke_token_refresh(self):
        """Test revoking refresh token."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )
        provider.register_user(user_id="user1", username="test", email="test@example.com")

        token = provider.generate_access_token(
            client_id="client1",
            user_id="user1",
            scopes={"read"},
            grant_type=GrantType.AUTHORIZATION_CODE,
        )

        result = provider.revoke_token(token.refresh_token)

        assert result is True

    def test_revoke_token_unknown(self):
        """Test revoking unknown token."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        result = provider.revoke_token("unknown_token")

        assert result is False

    def test_introspect_token_active(self):
        """Test token introspection for active token."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.CLIENT_CREDENTIALS},
        )

        token = provider.generate_access_token(
            client_id="client1",
            user_id=None,
            scopes={"read"},
            grant_type=GrantType.CLIENT_CREDENTIALS,
        )

        result = provider.introspect_token(token.token)

        assert result["active"] is True
        assert result["client_id"] == "client1"

    def test_introspect_token_inactive(self):
        """Test token introspection for unknown token."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")

        result = provider.introspect_token("unknown_token")

        assert result["active"] is False

    def test_cleanup_expired_tokens(self):
        """Test cleanup of expired tokens."""
        provider = OAuth2Provider(issuer_url="https://auth.example.com")
        provider.register_client(
            client_id="client1",
            client_secret="secret",
            redirect_uris=["http://localhost/callback"],
            scopes={"read"},
            grant_types={GrantType.CLIENT_CREDENTIALS},
        )

        # Create an expired token manually
        expired_token = AccessToken(
            token="expired",
            token_type=TokenType.BEARER,
            client_id="client1",
            user_id=None,
            scopes={"read"},
            expires_at=time.time() - 100,
        )
        provider._access_tokens["expired"] = expired_token

        counts = provider.cleanup_expired_tokens()

        assert counts["access_tokens"] == 1
        assert "expired" not in provider._access_tokens


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
