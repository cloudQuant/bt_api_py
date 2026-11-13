"""Synthetic CTP owner tests for explicitly reopening a migrated v2 journal."""
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
import zmq
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.ctp_gateway_trading import (
    CtpGatewayTradingError,
    build_ctp_gateway_trading_profile,
)
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.service_components import ServiceResourceConstructionError
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.test_iter63_ctp_gateway_trading import FakeSession, make


@contextmanager
def _fence():
    yield


def _delegated(settings, kind, key):
    instrument = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612")
    payload = {"symbol": "rb2612", "exchange_id": "SHFE", "idempotency_key": key,
               "offset": "open", "side": "buy", "quantity": "1", "quantity_unit": "lots"}
    identity = WireIdentity(WireRole.SERVICE, kind + "-principal", kind + "-actor")
    scope = WireScope("domain", account_ref=settings.account_scope, strategy_ref="strategy", run_id="run")
    command = GatewayDelegatedCommand(
        settings.account_scope, "strategy", "domain", identity.principal_ref, identity.actor_ref,
        kind, "run", key, gateway_payload_digest(payload), instrument, "grant-v1",
        parent_ref="parent" if kind == "execution" else None,
        child_id="child" if kind == "execution" else None,
    )
    grant = GatewayDelegateGrant(identity, settings.account_scope, "strategy", "domain", kind,
                                 "grant-v1", frozenset({instrument}), frozenset({"direct.reserve"}))
    context = SimpleNamespace(identity=identity, scope=scope, principal_ref=identity.principal_ref,
                              registration_id="registration")
    permit = authorize_delegated_command(
        context, grant, command, "direct.reserve", payload, registry_recheck=lambda _: True,
        current_grant=lambda: grant, instrument_recheck=lambda _: True,
        parent_recheck=(lambda *_: True) if kind == "execution" else None,
    )
    return identity.principal_ref, payload, permit


def _prepare_journal(settings, *, migrate, legacy_risk=False):
    """Only manipulate the temporary fake fixture's journal and local guard."""
    guard = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, "fixture-setup")
    generation = guard.acquire()
    try:
        journal = GatewayDirectCommandJournal(
            settings.journal_path, settings.account_scope, generation=generation,
            expected_journal_id=settings.expected_journal_id, minimum_revision=0,
        )
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        row = None
        if legacy_risk:
            principal, payload, permit = _delegated(settings, "risk", "legacy-risk")
            row = journal.reserve("strategy", "legacy-risk", principal, payload,
                                  reservation_fence=_fence, delegation_permit=permit)
        if migrate:
            journal.enable_risk_v2_binding_read_only(guard)
        return row
    finally:
        guard.release()


def _profile(tmp_path, settings, config, authority, grants, **kwargs):
    raw = config.to_public_dict()
    options = raw["components"]["gateway"]["settings"]["options"]
    options.update(control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True)
    config = parse_service_config(raw)
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, "operator-p", "operator-a")
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    ops = frozenset({"direct.control.status"})
    grant = ResourceGrant("gateway-a", "service.direct_write", config.scope_ref, scope, ops)
    public, _ = zmq.curve_keypair()
    authority = replace(
        authority,
        principals={**authority.principals, public: "operator-p"},
        grants={**authority.grants, "operator-p": (PrincipalGrant(identity, (grant,)),)},
    )
    profile = build_ctp_gateway_trading_profile(
        config, authority, tmp_path / "registry-v2.sqlite", profile_ref="ctp-v2-test",
        settings=settings, grants=grants,
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True,
        control_grants={"operator-p": GatewayControlGrant("operator-a", True, frozenset(), ops)},
        **kwargs,
    )
    return profile, options, authority


def _start(profile, options, authority):
    return profile.deployment_factory()._provide_resources(
        "gateway", "gateway-a", options, authority.scope
    )


def test_explicit_reopen_keeps_new_reservations_closed(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    row = _prepare_journal(settings, migrate=True, legacy_risk=True)
    settings = replace(settings, risk_v2_binding_enabled=True)
    profile, options, authority = _profile(tmp_path, settings, config, authority, grants)
    resources = _start(profile, options, authority)
    try:
        journal = resources.values["binding"]._journal
        assert journal._schema_version == 12
        assert journal._risk_v2_ddl_enabled is True
        with pytest.raises(DirectCommandJournalError, match="RISK_V2_BINDING_UNAVAILABLE"):
            journal.reserve("s", "new", "p", {"request": "synthetic"})
        for kind in ("risk", "execution"):
            principal, payload, permit = _delegated(settings, kind, "new-" + kind)
            with pytest.raises(DirectCommandJournalError, match="RISK_V2_BINDING_UNAVAILABLE"):
                journal.reserve("strategy", "new-" + kind, principal, payload,
                                reservation_fence=_fence, delegation_permit=permit)
        with pytest.raises(DirectCommandJournalError, match="RISK_V2_DISPATCH_UNAVAILABLE"):
            journal.begin_dispatch("strategy", "legacy-risk", row.reservation_id, row.digest,
                                   "risk-principal", row.version, {"id": "1"}, ("key",))
        assert journal.get("strategy", "legacy-risk", "risk-principal").status == "RESERVED"
        assert FakeSession.instances  # Fake session; this is not a no-I/O mode.
        assert FakeSession.instances[-1].provider.sends == []
    finally:
        resources.close()
    # A fresh owner must opt in again; the setting did not alter persisted mode.
    reopened = _start(profile, options, authority)
    try:
        assert reopened.values["binding"]._journal._risk_v2_ddl_enabled is True
        assert len(FakeSession.instances) == 2
    finally:
        reopened.close()


@pytest.mark.parametrize("damage", ["not_migrated", "bad_trigger"])
def test_explicit_reopen_refuses_without_fallback_or_session_start(tmp_path, monkeypatch, damage):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    _prepare_journal(settings, migrate=damage == "bad_trigger")
    if damage == "bad_trigger":
        with sqlite3.connect(settings.journal_path) as db:
            db.execute("DROP TRIGGER direct_risk_v2_bindings_no_update")
    profile, options, authority = _profile(
        tmp_path, replace(settings, risk_v2_binding_enabled=True), config, authority, grants
    )
    with pytest.raises(ServiceResourceConstructionError) as exc:
        _start(profile, options, authority)
    exc.value.resources.close()
    assert not FakeSession.instances
    guard = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, "retry")
    guard.acquire()
    guard.release()


def test_migrated_journal_refuses_default_mode(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    _prepare_journal(settings, migrate=True)
    profile, options, authority = _profile(tmp_path, settings, config, authority, grants)
    with pytest.raises(ServiceResourceConstructionError) as exc:
        _start(profile, options, authority)
    exc.value.resources.close()
    assert not FakeSession.instances


@pytest.mark.parametrize("migrated", [False, True])
def test_service_host_reopen_and_close_with_fake_session(tmp_path, monkeypatch, migrated):
    from bt_api_py.service_host import ServiceHostError

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    _prepare_journal(settings, migrate=migrated)
    profile, _, _ = _profile(
        tmp_path, replace(settings, risk_v2_binding_enabled=True), config, authority, grants,
    )
    deployment = profile.deployment_factory()
    if not migrated:
        with pytest.raises(ServiceHostError):
            deployment.start()
        assert not FakeSession.instances
        assert deployment.state == "failed"
        guard = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, "retry")
        guard.acquire()
        guard.release()
        return
    deployment.start()
    try:
        assert len(FakeSession.instances) == 1
        assert any(event[0] == "connect" for event in FakeSession.instances[0].events)
        assert FakeSession.instances[0].provider.sends == []
    finally:
        deployment.close()
    assert deployment.state == "closed"
    guard = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, "retry")
    guard.acquire()
    guard.release()


def test_explicit_reopen_refuses_release_audit_mismatch(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    _prepare_journal(settings, migrate=True)
    profile, options, authority = _profile(
        tmp_path, replace(settings, risk_v2_binding_enabled=True), config, authority, grants,
        control_release_audit_enabled=True,
    )
    with pytest.raises(ServiceResourceConstructionError) as exc:
        _start(profile, options, authority)
    exc.value.resources.close()
    assert not FakeSession.instances
    with sqlite3.connect(settings.journal_path) as db:
        assert db.execute("SELECT version FROM direct_risk_v2_mode").fetchone() == (2,)
        assert db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='direct_control_releases'"
        ).fetchone() == (0,)


def test_explicit_reopen_refuses_wrong_journal_identity(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    _prepare_journal(settings, migrate=True)
    profile, options, authority = _profile(
        tmp_path, replace(settings, risk_v2_binding_enabled=True, expected_journal_id="wrong"),
        config, authority, grants,
    )
    with pytest.raises(ServiceResourceConstructionError) as exc:
        _start(profile, options, authority)
    exc.value.resources.close()
    assert not FakeSession.instances


@pytest.mark.parametrize("option", [
    "upgrade_controls_from_v9", "upgrade_strategy_ledger_from_v10",
    "upgrade_account_extensions_from_v11", "upgrade_parent_authority_from_v12",
    "upgrade_account_facts_financial_from_v13", "upgrade_actor_stream_from_v14",
    "upgrade_strategy_origin_from_v15", "upgrade_control_cancel_from_v16",
    "upgrade_account_fact_recovery_from_v17", "upgrade_account_event_recovery_from_v18",
    "upgrade_control_release_audit",
])
def test_explicit_reopen_rejects_all_automatic_upgrades(tmp_path, monkeypatch, option):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    with pytest.raises(CtpGatewayTradingError, match="INVALID_RISK_V2_BINDING_CONFIGURATION"):
        _profile(tmp_path, replace(settings, risk_v2_binding_enabled=True),
                 config, authority, grants, **{option: True})


def test_invalid_mode_type_is_rejected(tmp_path, monkeypatch):
    _, settings, _, _, _, _ = make(tmp_path, monkeypatch)
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_SETTINGS"):
        replace(settings, risk_v2_binding_enabled=1)


def test_mode_requires_account_extensions(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    with pytest.raises(CtpGatewayTradingError, match="INVALID_RISK_V2_BINDING_CONFIGURATION"):
        build_ctp_gateway_trading_profile(
            config, authority, tmp_path / "registry.sqlite", profile_ref="ctp-v2-test",
            settings=replace(settings, risk_v2_binding_enabled=True), grants=grants,
            request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
            assert_admitted=lambda _: True,
        )
