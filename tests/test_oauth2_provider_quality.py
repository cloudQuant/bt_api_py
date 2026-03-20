import time

import pytest

from bt_api_py.security_compliance.auth.oauth2_provider import GrantType, OAuth2Provider, OAuthError


class TestOAuth2ProviderQuality:
    def test_register_client_copies_mutable_inputs(self):
        provider = OAuth2Provider("https://issuer.example.com")
        redirect_uris = ["https://app.example.com/callback"]
        scopes = {"read"}
        grant_types = {GrantType.AUTHORIZATION_CODE}

        client = provider.register_client(
            client_id="client-a",
            client_secret="secret",
            redirect_uris=redirect_uris,
            scopes=scopes,
            grant_types=grant_types,
        )

        redirect_uris.append("https://evil.example.com/callback")
        scopes.add("write")
        grant_types.add(GrantType.REFRESH_TOKEN)

        assert client.redirect_uris == ["https://app.example.com/callback"]
        assert client.scopes == {"read"}
        assert client.grant_types == {GrantType.AUTHORIZATION_CODE}

    def test_generate_access_token_rejects_scopes_outside_client_scopes(self):
        provider = OAuth2Provider("https://issuer.example.com")
        provider.register_client(
            client_id="client-a",
            client_secret="secret",
            redirect_uris=["https://app.example.com/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )

        with pytest.raises(OAuthError, match="Invalid scopes"):
            provider.generate_access_token(
                client_id="client-a",
                user_id="user-a",
                scopes={"write"},
                grant_type=GrantType.AUTHORIZATION_CODE,
            )

    def test_refresh_access_token_rotates_token_and_uses_configured_lifetime(self):
        lifetime = 120
        provider = OAuth2Provider(
            "https://issuer.example.com",
            refresh_token_lifetime=lifetime,
            enable_token_rotation=True,
        )
        provider.register_client(
            client_id="client-a",
            client_secret="secret",
            redirect_uris=["https://app.example.com/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )
        provider.register_user("user-a", "user-a", "user@example.com")

        access_token = provider.generate_access_token(
            client_id="client-a",
            user_id="user-a",
            scopes={"read"},
            grant_type=GrantType.AUTHORIZATION_CODE,
        )

        original_refresh = access_token.refresh_token
        assert original_refresh is not None
        original_refresh_token = provider._refresh_tokens[original_refresh]
        remaining = original_refresh_token.expires_at - time.time()
        assert 0 < remaining <= lifetime

        refreshed = provider.refresh_access_token(original_refresh, "client-a")

        assert refreshed.refresh_token is not None
        assert refreshed.refresh_token != original_refresh
        assert provider._refresh_tokens[original_refresh].used is True
        rotated_refresh = provider._refresh_tokens[refreshed.refresh_token]
        rotated_remaining = rotated_refresh.expires_at - time.time()
        assert 0 < rotated_remaining <= lifetime

    def test_refresh_access_token_reuses_token_when_rotation_disabled(self):
        provider = OAuth2Provider(
            "https://issuer.example.com",
            enable_token_rotation=False,
        )
        provider.register_client(
            client_id="client-a",
            client_secret="secret",
            redirect_uris=["https://app.example.com/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )
        provider.register_user("user-a", "user-a", "user@example.com")

        access_token = provider.generate_access_token(
            client_id="client-a",
            user_id="user-a",
            scopes={"read"},
            grant_type=GrantType.AUTHORIZATION_CODE,
        )

        original_refresh = access_token.refresh_token
        assert original_refresh is not None

        refreshed = provider.refresh_access_token(original_refresh, "client-a")

        assert refreshed.refresh_token == original_refresh
        assert provider._refresh_tokens[original_refresh].used is False

    def test_generate_authorization_code_requires_existing_user(self):
        provider = OAuth2Provider("https://issuer.example.com")
        provider.register_client(
            client_id="client-a",
            client_secret="secret",
            redirect_uris=["https://app.example.com/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )

        with pytest.raises(OAuthError, match="User not found"):
            provider.generate_authorization_code(
                client_id="client-a",
                user_id="missing-user",
                redirect_uri="https://app.example.com/callback",
                scopes={"read"},
            )

    @pytest.mark.parametrize(
        ("kwargs", "error_match"),
        [
            ({"issuer_url": None}, "issuer_url"),
            ({"issuer_url": "https://issuer.example.com", "token_lifetime": True}, "token_lifetime"),
            (
                {"issuer_url": "https://issuer.example.com", "refresh_token_lifetime": 0},
                "refresh_token_lifetime",
            ),
            ({"issuer_url": "https://issuer.example.com", "enable_pkce": "yes"}, "enable_pkce"),
            (
                {"issuer_url": "https://issuer.example.com", "enable_token_rotation": 1},
                "enable_token_rotation",
            ),
        ],
    )
    def test_constructor_rejects_invalid_scalar_inputs(self, kwargs, error_match):
        with pytest.raises(OAuthError, match=error_match):
            OAuth2Provider(**kwargs)

    def test_register_client_rejects_non_boolean_is_confidential(self):
        provider = OAuth2Provider("https://issuer.example.com")

        with pytest.raises(OAuthError, match="is_confidential"):
            provider.register_client(
                client_id="client-a",
                client_secret="secret",
                redirect_uris=["https://app.example.com/callback"],
                scopes={"read"},
                grant_types={GrantType.AUTHORIZATION_CODE},
                is_confidential="true",
            )

    def test_register_user_rejects_non_boolean_mfa_enabled(self):
        provider = OAuth2Provider("https://issuer.example.com")

        with pytest.raises(OAuthError, match="mfa_enabled"):
            provider.register_user("user-a", "user-a", "user@example.com", mfa_enabled="yes")

    @pytest.mark.parametrize(
        ("field_name", "register_kwargs", "error_match"),
        [
            (
                "redirect_uris",
                {
                    "client_id": "client-a",
                    "client_secret": "secret",
                    "redirect_uris": [123],
                    "scopes": {"read"},
                    "grant_types": {GrantType.AUTHORIZATION_CODE},
                },
                "redirect_uri",
            ),
            (
                "scopes",
                {
                    "client_id": "client-a",
                    "client_secret": "secret",
                    "redirect_uris": ["https://app.example.com/callback"],
                    "scopes": [None],
                    "grant_types": {GrantType.AUTHORIZATION_CODE},
                },
                "scope",
            ),
            (
                "grant_types",
                {
                    "client_id": "client-a",
                    "client_secret": "secret",
                    "redirect_uris": ["https://app.example.com/callback"],
                    "scopes": {"read"},
                    "grant_types": [None],
                },
                "grant_type",
            ),
        ],
    )
    def test_register_client_rejects_non_string_iterable_items(
        self, field_name, register_kwargs, error_match
    ):
        provider = OAuth2Provider("https://issuer.example.com")

        with pytest.raises(OAuthError, match=error_match):
            provider.register_client(**register_kwargs)

    def test_validate_access_token_rejects_invalid_required_scopes_shape(self):
        provider = OAuth2Provider("https://issuer.example.com")
        provider.register_client(
            client_id="client-a",
            client_secret="secret",
            redirect_uris=["https://app.example.com/callback"],
            scopes={"read"},
            grant_types={GrantType.AUTHORIZATION_CODE},
        )
        access_token = provider.generate_access_token(
            client_id="client-a",
            user_id="user-a",
            scopes={"read"},
            grant_type=GrantType.AUTHORIZATION_CODE,
        )

        with pytest.raises(OAuthError, match="scopes must be an iterable of strings"):
            provider.validate_access_token(access_token.token, required_scopes="read")
