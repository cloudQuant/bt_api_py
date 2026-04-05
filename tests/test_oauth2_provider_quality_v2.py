from __future__ import annotations

import pytest

pytest.importorskip("cryptography", reason="cryptography package required for OAuth2Provider")

from bt_api_py.security_compliance.auth.oauth2_provider import (
    GrantType,
    OAuth2Provider,
    OAuthError,
    TokenType,
)


@pytest.fixture
def provider() -> OAuth2Provider:
    provider = OAuth2Provider("https://issuer.example.com")
    provider.register_client(
        client_id="client-a",
        client_secret="secret",
        redirect_uris=["https://app.example.com/callback"],
        scopes={"openid", "read"},
        grant_types={GrantType.AUTHORIZATION_CODE},
    )
    provider.register_user("user-a", "user-a", "user@example.com")
    return provider


def test_refresh_access_token_rejects_inactive_user(provider: OAuth2Provider):
    access_token = provider.generate_access_token(
        client_id="client-a",
        user_id="user-a",
        scopes={"read"},
        grant_type=GrantType.AUTHORIZATION_CODE,
    )
    refresh_token = access_token.refresh_token
    assert refresh_token is not None

    provider._users["user-a"].is_active = False

    with pytest.raises(OAuthError, match="User is inactive"):
        provider.refresh_access_token(refresh_token, "client-a")

    assert provider._refresh_tokens[refresh_token].used is False


def test_generate_jwt_id_token_rejects_inactive_user(provider: OAuth2Provider):
    provider._users["user-a"].is_active = False

    with pytest.raises(OAuthError, match="User is inactive"):
        provider.generate_jwt_id_token("user-a", "client-a", {"openid"})


def test_generate_jwt_id_token_rejects_scopes_outside_client_scopes(provider: OAuth2Provider):
    with pytest.raises(OAuthError, match="Invalid scopes"):
        provider.generate_jwt_id_token("user-a", "client-a", {"write"})


@pytest.mark.parametrize("scopes", ["openid", [None]])
def test_generate_jwt_id_token_rejects_invalid_scope_shapes(provider: OAuth2Provider, scopes):
    with pytest.raises(OAuthError):
        provider.generate_jwt_id_token("user-a", "client-a", scopes)


def test_cleanup_expired_tokens_removes_used_auth_codes_and_refresh_tokens(
    provider: OAuth2Provider,
):
    auth_code = provider.generate_authorization_code(
        client_id="client-a",
        user_id="user-a",
        redirect_uri="https://app.example.com/callback",
        scopes={"read"},
    )
    provider.validate_authorization_code(
        auth_code,
        client_id="client-a",
        redirect_uri="https://app.example.com/callback",
    )

    access_token = provider.generate_access_token(
        client_id="client-a",
        user_id="user-a",
        scopes={"read"},
        grant_type=GrantType.AUTHORIZATION_CODE,
    )
    refresh_token = access_token.refresh_token
    assert refresh_token is not None
    assert provider.revoke_token(refresh_token) is True

    cleanup_counts = provider.cleanup_expired_tokens()

    assert cleanup_counts == {
        "authorization_codes": 1,
        "access_tokens": 0,
        "refresh_tokens": 1,
    }
    assert auth_code not in provider._auth_codes
    assert refresh_token not in provider._refresh_tokens


def test_generate_authorization_code_validates_redirect_user_and_pkce(provider: OAuth2Provider):
    with pytest.raises(OAuthError, match="Invalid redirect URI"):
        provider.generate_authorization_code(
            client_id="client-a",
            user_id="user-a",
            redirect_uri="https://bad.example.com/callback",
            scopes={"read"},
        )

    provider._users["user-a"].is_active = False
    with pytest.raises(OAuthError, match="User is inactive"):
        provider.generate_authorization_code(
            client_id="client-a",
            user_id="user-a",
            redirect_uri="https://app.example.com/callback",
            scopes={"read"},
        )
    provider._users["user-a"].is_active = True

    provider.enable_pkce = False
    with pytest.raises(OAuthError, match="PKCE not supported"):
        provider.generate_authorization_code(
            client_id="client-a",
            user_id="user-a",
            redirect_uri="https://app.example.com/callback",
            scopes={"read"},
            code_challenge="challenge",
        )
    provider.enable_pkce = True

    with pytest.raises(OAuthError, match="Code challenge required"):
        provider.generate_authorization_code(
            client_id="client-a",
            user_id="user-a",
            redirect_uri="https://app.example.com/callback",
            scopes={"read"},
            code_challenge_method="plain",
        )

    with pytest.raises(OAuthError, match="Unsupported PKCE method"):
        provider.generate_authorization_code(
            client_id="client-a",
            user_id="user-a",
            redirect_uri="https://app.example.com/callback",
            scopes={"read"},
            code_challenge="challenge",
            code_challenge_method="S512",
        )


def test_validate_authorization_code_checks_client_redirect_and_pkce(provider: OAuth2Provider):
    code = provider.generate_authorization_code(
        client_id="client-a",
        user_id="user-a",
        redirect_uri="https://app.example.com/callback",
        scopes={"read"},
        code_challenge="plain-secret",
        code_challenge_method="plain",
    )

    with pytest.raises(OAuthError, match="Client ID mismatch"):
        provider.validate_authorization_code(
            code,
            client_id="other-client",
            redirect_uri="https://app.example.com/callback",
            code_verifier="plain-secret",
        )

    fresh_code = provider.generate_authorization_code(
        client_id="client-a",
        user_id="user-a",
        redirect_uri="https://app.example.com/callback",
        scopes={"read"},
        code_challenge="plain-secret",
        code_challenge_method="plain",
    )
    with pytest.raises(OAuthError, match="Redirect URI mismatch"):
        provider.validate_authorization_code(
            fresh_code,
            client_id="client-a",
            redirect_uri="https://wrong.example.com/callback",
            code_verifier="plain-secret",
        )

    pkce_code = provider.generate_authorization_code(
        client_id="client-a",
        user_id="user-a",
        redirect_uri="https://app.example.com/callback",
        scopes={"read"},
        code_challenge="plain-secret",
        code_challenge_method="plain",
    )
    with pytest.raises(OAuthError, match="Code verifier required"):
        provider.validate_authorization_code(
            pkce_code,
            client_id="client-a",
            redirect_uri="https://app.example.com/callback",
        )

    second_pkce_code = provider.generate_authorization_code(
        client_id="client-a",
        user_id="user-a",
        redirect_uri="https://app.example.com/callback",
        scopes={"read"},
        code_challenge="plain-secret",
        code_challenge_method="plain",
    )
    with pytest.raises(OAuthError, match="Invalid code verifier"):
        provider.validate_authorization_code(
            second_pkce_code,
            client_id="client-a",
            redirect_uri="https://app.example.com/callback",
            code_verifier="wrong-secret",
        )

    valid_code = provider.generate_authorization_code(
        client_id="client-a",
        user_id="user-a",
        redirect_uri="https://app.example.com/callback",
        scopes={"read"},
        code_challenge="plain-secret",
        code_challenge_method="plain",
    )
    auth_code = provider.validate_authorization_code(
        valid_code,
        client_id="client-a",
        redirect_uri="https://app.example.com/callback",
        code_verifier="plain-secret",
    )

    assert auth_code.used is True


def test_generate_and_validate_access_token_guards(provider: OAuth2Provider):
    with pytest.raises(OAuthError, match="Grant type not allowed"):
        provider.generate_access_token(
            client_id="client-a",
            user_id="user-a",
            scopes={"read"},
            grant_type=GrantType.CLIENT_CREDENTIALS,
        )

    with pytest.raises(OAuthError, match="User ID required"):
        provider.generate_access_token(
            client_id="client-a",
            user_id=None,
            scopes={"read"},
            grant_type=GrantType.AUTHORIZATION_CODE,
        )

    access_token = provider.generate_access_token(
        client_id="client-a",
        user_id="user-a",
        scopes={"read"},
        grant_type=GrantType.AUTHORIZATION_CODE,
    )

    validated = provider.validate_access_token(access_token.token, required_scopes={"read"})

    assert validated.token_type == TokenType.BEARER
    assert validated.get_expires_in() >= 0

    with pytest.raises(OAuthError, match="Insufficient scopes"):
        provider.validate_access_token(access_token.token, required_scopes={"write"})

    access_token.expires_at = 0
    with pytest.raises(OAuthError, match="Access token expired"):
        provider.validate_access_token(access_token.token)


def test_refresh_access_token_rotation_and_reuse(provider: OAuth2Provider):
    access_token = provider.generate_access_token(
        client_id="client-a",
        user_id="user-a",
        scopes={"read"},
        grant_type=GrantType.AUTHORIZATION_CODE,
    )
    refresh_token = access_token.refresh_token
    assert refresh_token is not None

    refreshed = provider.refresh_access_token(refresh_token, "client-a")

    assert provider._refresh_tokens[refresh_token].used is True
    assert refreshed.refresh_token is not None
    assert refreshed.refresh_token != refresh_token

    with pytest.raises(OAuthError, match="expired or used"):
        provider.refresh_access_token(refresh_token, "client-a")


def test_refresh_access_token_without_rotation_reuses_refresh_token():
    provider = OAuth2Provider("https://issuer.example.com", enable_token_rotation=False)
    provider.register_client(
        client_id="client-a",
        client_secret="secret",
        redirect_uris=["https://app.example.com/callback"],
        scopes={"openid", "read"},
        grant_types={GrantType.AUTHORIZATION_CODE},
    )
    provider.register_user("user-a", "user-a", "user@example.com")

    access_token = provider.generate_access_token(
        client_id="client-a",
        user_id="user-a",
        scopes={"read"},
        grant_type=GrantType.AUTHORIZATION_CODE,
    )
    refresh_token = access_token.refresh_token
    assert refresh_token is not None

    refreshed = provider.refresh_access_token(refresh_token, "client-a")

    assert refreshed.refresh_token == refresh_token
    assert provider._refresh_tokens[refresh_token].used is False


def test_revoke_introspect_jwt_and_info_helpers(provider: OAuth2Provider):
    access_token = provider.generate_access_token(
        client_id="client-a",
        user_id="user-a",
        scopes={"openid", "read"},
        grant_type=GrantType.AUTHORIZATION_CODE,
    )
    refresh_token = access_token.refresh_token
    assert refresh_token is not None

    access_info = provider.introspect_token(access_token.token)
    refresh_info = provider.introspect_token(refresh_token)
    inactive_info = provider.introspect_token("missing-token")

    assert access_info["active"] is True
    assert access_info["scope"] == "openid read"
    assert refresh_info["token_type"] == "refresh_token"
    assert inactive_info == {"active": False}

    assert provider.revoke_token(access_token.token) is True
    assert provider.revoke_token(refresh_token) is True
    assert provider.revoke_token("missing-token") is False

    jwt_token = provider.generate_jwt_id_token("user-a", "client-a", {"openid"})
    payload = provider.validate_jwt(jwt_token, audience="client-a")

    assert payload["sub"] == "user-a"
    assert payload["aud"] == "client-a"

    with pytest.raises(OAuthError, match="Invalid JWT"):
        provider.validate_jwt("not-a-jwt", audience="client-a")

    assert provider.get_client_info("client-a") is not None
    assert provider.get_user_info("user-a") is not None
    assert provider.get_client_info("missing") is None
    assert provider.get_user_info("missing") is None
