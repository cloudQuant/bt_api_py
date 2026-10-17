from __future__ import annotations

from pathlib import Path

import pytest
from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_ctp.account_provider import CtpReadOnlyAccountSessionProvider
from bt_api_ctp.ctp.client import CtpNativeStopReceipt
from bt_api_ctp.ctp_env_selector import official_simnow_fronts
from bt_api_gateway.account_process import (
    GatewayAccountSessionProcessError,
    GatewayAccountSessionProcessProvider,
    GatewayAccountSessionProfileUnavailableError,
)
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayServiceAccountScope

from bt_api_py.ctp_account_process import (
    CTP_SIMNOW_SET2_7X24_MARKET_ENTITLEMENT_REF,
    CTP_SIMNOW_SET2_7X24_MARKET_OPERATOR_PROFILE_REF,
    CTP_SIMNOW_SET2_7X24_MARKET_SOURCE_REF,
    CTP_SIMNOW_SET2_7X24_OPERATOR_PROFILE_REF,
    CTP_SIMNOW_SET2_7X24_PROFILE_REF,
    CTP_SIMNOW_SET2_7X24_SESSION_REF,
    CtpSimNowSet2AccountProcessOwner,
    GatewayOperatorAccountProfileError,
    build_ctp_simnow_set2_account_operator_profile,
    build_ctp_simnow_set2_market_operator_profile,
    ctp_simnow_set2_account_key_from_environment,
    project_ctp_native_stop_receipt,
)
from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplicationError,
    GatewayOperatorProfile,
    GatewayOperatorProfileRegistry,
)
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

_SERVICE_ID = "ctp-simnow-service"
_INSTANCE_ID = "ctp-simnow-gateway"
_SCOPE_REF = "ctp-simnow-scope-v1"
_TRANSPORT_REF = "ctp-simnow-transport-v1"
_SECURITY_REF = "ctp-simnow-security-v1"
_DOMAIN = "paper:ctp-simnow"
_ACCOUNT_REF = "account:simnow-alias"
_STATE_SCOPE = "state:simnow-read"
_JOURNAL_SCOPE = "journal:simnow-read"
_PRINCIPAL_REF = "principal:simnow-reader"
_STRATEGY_REF = "strategy:simnow-reader"
_SERVICE_PRINCIPAL = "principal:simnow-service"


def _account_config(endpoint: str = "tcp://127.0.0.1:39011") -> dict[str, object]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "ctp-simnow-policy-v1",
        "service_requirements_ref": "ctp-simnow-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE_ID,
                    "bind_ports": ["service.account_read", "service.provider_session"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": CTP_SIMNOW_SET2_7X24_SESSION_REF,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {"high_water_mark": 32, "max_message_bytes": 262_144},
                }
            },
        },
    }


def _market_config(
    rpc_endpoint: str = "tcp://127.0.0.1:39012",
    market_endpoint: str = "tcp://127.0.0.1:39013",
) -> dict[str, object]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "ctp-simnow-policy-v1",
        "service_requirements_ref": "ctp-simnow-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE_ID,
                    "bind_ports": ["service.market_source"],
                    "options": {"role": "market_hub"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_endpoint, "market_bind": market_endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {"high_water_mark": 32, "max_message_bytes": 262_144},
                }
            },
        },
    }


def _authority(
    config, *, port: str, operations: frozenset[str]
) -> tuple[TrustedServiceAuthority, GatewayPrincipal]:
    account_scope = _JOURNAL_SCOPE
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    scope = WireScope(_DOMAIN)
    client_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF)
    resource = ResourceGrant(
        _INSTANCE_ID,
        port,
        _SCOPE_REF,
        WireScope(_DOMAIN, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF),
        operations,
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=scope,
        scope_ref=config.scope_ref,
        scope_version="ctp-simnow-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="ctp-simnow-security-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="ctp-simnow-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="ctp-simnow-requirements-v1",
        curve_public_key=b"P" * 40,
        curve_secret_key=b"S" * 40,
        principals={b"C" * 40: _PRINCIPAL_REF},
        grants={_PRINCIPAL_REF: (PrincipalGrant(client_identity, (resource,)),)},
    )
    principal = GatewayPrincipal(
        _PRINCIPAL_REF,
        frozenset({account_scope}),
        frozenset({_STATE_SCOPE}),
        frozenset({GatewayCommandKind.READ}),
    )
    return authority, principal


def test_set2_profile_is_exact_official_pair_and_key_uses_local_identity(monkeypatch) -> None:
    monkeypatch.setenv("CTP_USER_ID", "synthetic-investor")
    monkeypatch.setenv("CTP_BROKER_ID", "synthetic-broker")
    monkeypatch.setenv("CTP_ENV", "set1")
    monkeypatch.setenv("CTP_SET1_TD_FRONT_1", "tcp://127.0.0.1:1")

    key = ctp_simnow_set2_account_key_from_environment()

    assert key == AccountKey(
        provider="ctp",
        venue="simnow",
        environment="simnow",
        account_id="synthetic-investor",
        subaccount_id="synthetic-broker",
    )
    assert official_simnow_fronts("set2_7x24_4000x") == (
        "tcp://182.254.243.31:40001",
        "tcp://182.254.243.31:40011",
    )


def test_account_process_owner_is_fixed_one_shot_without_starting_child(monkeypatch) -> None:
    monkeypatch.setenv("CTP_USER_ID", "synthetic-investor")
    monkeypatch.setenv("CTP_BROKER_ID", "synthetic-broker")
    owner = CtpSimNowSet2AccountProcessOwner()

    provider = owner.create_provider(CTP_SIMNOW_SET2_7X24_SESSION_REF)

    assert type(provider) is GatewayAccountSessionProcessProvider
    assert provider._profile_ref == CTP_SIMNOW_SET2_7X24_PROFILE_REF
    assert provider._child_target.__module__ == "bt_api_py.ctp_account_process"
    assert provider.process_state == "new"
    assert provider.custody_retained is False
    with pytest.raises(GatewayAccountSessionProcessError) as caught:
        owner.create_provider(CTP_SIMNOW_SET2_7X24_SESSION_REF)
    assert caught.value.code == "SESSION_ALREADY_CLAIMED"


def test_profile_unavailable_error_does_not_echo_bad_reference(monkeypatch) -> None:
    from bt_api_py.ctp_account_process import create_ctp_simnow_set2_account_provider

    secret_like = "do-not-echo-this-value"
    with pytest.raises(GatewayAccountSessionProfileUnavailableError) as caught:
        create_ctp_simnow_set2_account_provider(secret_like)
    assert str(caught.value) == ""
    assert secret_like not in repr(caught.value)


def test_native_receipt_projector_requires_exact_current_complete_receipt() -> None:
    provider = CtpReadOnlyAccountSessionProvider(lambda: object())
    receipt = CtpNativeStopReceipt(
        connection_generation=17,
        join_required=True,
        join_completed=True,
        native_released=True,
        thread_alive=False,
        timed_out=False,
    )
    provider._native_owner_created = True
    provider._native_connection_generation = 17
    provider._last_native_stop_receipt = receipt

    projected = project_ctp_native_stop_receipt(provider, 17)

    assert projected == {
        "connection_generation": 17,
        "join_required": True,
        "join_completed": True,
        "native_released": True,
        "thread_alive": False,
        "timed_out": False,
        "complete": True,
    }
    with pytest.raises(GatewayAccountSessionProcessError) as caught:
        project_ctp_native_stop_receipt(provider, 18)
    assert caught.value.code == "NATIVE_RECEIPT_UNCONFIRMED"

    provider._native_connection_generation = 1
    provider._last_native_stop_receipt = CtpNativeStopReceipt(
        connection_generation=True,
        join_required=False,
        join_completed=True,
        native_released=True,
        thread_alive=False,
        timed_out=False,
    )
    with pytest.raises(GatewayAccountSessionProcessError) as bool_generation:
        project_ctp_native_stop_receipt(provider, 1)
    assert bool_generation.value.code == "NATIVE_RECEIPT_UNCONFIRMED"


def test_trusted_account_profile_builds_existing_operator_without_start(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CTP_USER_ID", "synthetic-investor")
    monkeypatch.setenv("CTP_BROKER_ID", "synthetic-broker")
    config = parse_service_config(_account_config())
    authority, principal = _authority(
        config,
        port="service.account_read",
        operations=frozenset({"account.head", "account.read_after", "account.snapshot"}),
    )
    journal = GatewayEventJournal(tmp_path / "account-events.sqlite3")
    account_scope = GatewayServiceAccountScope(
        account_ref=_ACCOUNT_REF,
        execution_domain_ref=_DOMAIN,
        journal_account_scope=_JOURNAL_SCOPE,
        account_read_strategy_scope=_STATE_SCOPE,
        shared_account_facts_scope=_STATE_SCOPE,
    )
    guard_directory = tmp_path / "guard"
    guard_directory.mkdir()
    profile = build_ctp_simnow_set2_account_operator_profile(
        config,
        authority,
        tmp_path / "service-registry.sqlite3",
        journal=journal,
        account_scope=account_scope,
        principals={_PRINCIPAL_REF: principal},
        guard_directory=guard_directory,
        state_scope=_STATE_SCOPE,
        owner_ref="owner:simnow-readonly",
    )

    write_capability_principal = GatewayPrincipal(
        principal_id=principal.principal_id,
        account_scopes=principal.account_scopes,
        strategy_scopes=principal.strategy_scopes,
        allowed_kinds=frozenset(
            {GatewayCommandKind.READ, GatewayCommandKind.SUBMIT}
        ),
    )
    with pytest.raises(GatewayOperatorAccountProfileError) as caught:
        build_ctp_simnow_set2_account_operator_profile(
            config,
            authority,
            tmp_path / "write-capable-registry.sqlite3",
            journal=journal,
            account_scope=account_scope,
            principals={_PRINCIPAL_REF: write_capability_principal},
            guard_directory=guard_directory,
            state_scope=_STATE_SCOPE,
            owner_ref="owner:simnow-readonly",
        )
    assert caught.value.code == "TRUSTED_BINDING_INVALID"

    registry = GatewayOperatorProfileRegistry((profile,))
    resolved = registry.resolve(CTP_SIMNOW_SET2_7X24_OPERATOR_PROFILE_REF)

    assert type(resolved) is GatewayOperatorProfile
    deployment = resolved.deployment_factory()
    assert deployment.state == "new"
    assert deployment.cleanup_status["unclaimed_provider_count"] == 0
    assert type(deployment._session_owner) is CtpSimNowSet2AccountProcessOwner
    assert deployment._session_owner._claimed is False

    with pytest.raises(GatewayOperatorApplicationError) as caught:
        registry.resolve("unregistered.ctp.profile")
    assert caught.value.code == "PROFILE_NOT_REGISTERED"


def test_market_profile_binds_only_trusted_ctp_quote_v2_keys_without_start(
    tmp_path: Path, monkeypatch
) -> None:
    config = parse_service_config(_market_config())
    authority, _principal = _authority(
        config,
        port="service.market_source",
        operations=frozenset(
            {
                "market.subscribe",
                "market.unsubscribe",
                "market.snapshot",
                "market.head",
                "market.read_after",
            }
        ),
    )
    key = MarketSubscriptionKey(
        source_ref=CTP_SIMNOW_SET2_7X24_MARKET_SOURCE_REF,
        entitlement_ref=CTP_SIMNOW_SET2_7X24_MARKET_ENTITLEMENT_REF,
        instrument=MarketInstrumentKey(
            provider="ctp",
            venue="SHFE",
            environment="simnow",
            product_type="future",
            instrument_id="rb2710",
            native_symbol="rb2710",
        ),
        channel="ctp.quote.v2",
    )
    profile = build_ctp_simnow_set2_market_operator_profile(
        config,
        authority,
        tmp_path / "market-registry.sqlite3",
        key_universe=(key,),
        principal_allowed_keys={_PRINCIPAL_REF: frozenset({key})},
    )
    registry = GatewayOperatorProfileRegistry((profile,))

    assert registry.resolve(CTP_SIMNOW_SET2_7X24_MARKET_OPERATOR_PROFILE_REF) is profile
    monkeypatch.setattr(
        "bt_api_py.ctp_account_process._read_ctp_market_environment",
        lambda: pytest.fail("deployment construction must not resolve credentials"),
    )
    deployment = profile.deployment_factory()
    assert deployment.state == "new"
    assert deployment.cleanup_status["unclaimed_provider_count"] == 0

    invalid_key = MarketSubscriptionKey(
        source_ref=CTP_SIMNOW_SET2_7X24_MARKET_SOURCE_REF,
        entitlement_ref=CTP_SIMNOW_SET2_7X24_MARKET_ENTITLEMENT_REF,
        instrument=MarketInstrumentKey(
            provider="ctp",
            venue="SHFE",
            environment="live",
            product_type="future",
            instrument_id="rb2710",
            native_symbol="rb2710",
        ),
        channel="ctp.quote.v2",
    )
    with pytest.raises(GatewayOperatorAccountProfileError) as caught:
        build_ctp_simnow_set2_market_operator_profile(
            config,
            authority,
            tmp_path / "bad-market-registry.sqlite3",
            key_universe=(invalid_key,),
            principal_allowed_keys={_PRINCIPAL_REF: frozenset({invalid_key})},
        )
    assert caught.value.code == "MARKET_UNIVERSE_INVALID"


def test_account_profile_rejects_valid_market_role_config(tmp_path: Path) -> None:
    config = parse_service_config(_market_config())
    with pytest.raises(GatewayOperatorAccountProfileError) as caught:
        build_ctp_simnow_set2_account_operator_profile(
            config,
            object(),
            tmp_path / "invalid-registry.sqlite3",
            journal=object(),
            account_scope=object(),
            principals={},
            guard_directory=tmp_path,
            state_scope=_STATE_SCOPE,
            owner_ref="owner:simnow-readonly",
        )
    assert caught.value.code == "SERVICE_CONFIG_INVALID"
