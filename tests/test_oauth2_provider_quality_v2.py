import pytest

from bt_api_py.security_compliance.auth.oauth2_provider import GrantType, OAuth2Provider, OAuthError


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
def test_generate_jwt_id_token_rejects_invalid_scope_shapes(
    provider: OAuth2Provider, scopes
):
    with pytest.raises(OAuthError):
        provider.generate_jwt_id_token("user-a", "client-a", scopes)


def test_cleanup_expired_tokens_removes_used_auth_codes_and_refresh_tokens(provider: OAuth2Provider):
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
