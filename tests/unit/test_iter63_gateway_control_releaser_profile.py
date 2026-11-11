"""Installed options for the control release audit (I63-E144); synthetic profile, no account."""
import json
import sqlite3
from dataclasses import replace

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)

from bt_api_py.ctp_gateway_operator_profile import (
    CtpGatewayTrustedResources,
    create_ctp_gateway_profile_factory,
)
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError, build_ctp_gateway_trading_profile
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.service_components import ServiceResourceConstructionError
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.test_iter63_ctp_gateway_trading import make


class Profile:
    """A control-only (schema 10) trading profile that can be started more than once."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        _, self.settings, config, authority, self.grants, _ = make(tmp_path, monkeypatch)
        raw = config.to_public_dict()
        self.opts = raw["components"]["gateway"]["settings"]["options"]
        self.opts["control_enabled"] = True
        self.config = parse_service_config(raw)
        identity = WireIdentity(WireRole.APPLICATION_CLIENT, "operator-p", "operator-a")
        scope = WireScope(authority.scope.execution_domain_ref, account_ref=self.settings.account_scope)
        self.ops = frozenset({"direct.control.apply", "direct.control.resume", "direct.control.status"})
        resource = ResourceGrant("gateway-a", "service.direct_write", self.config.scope_ref, scope, self.ops)
        public, _ = zmq.curve_keypair()
        self.authority = replace(authority, principals={**authority.principals, public: "operator-p"},
                                 grants={**authority.grants, "operator-p": (PrincipalGrant(identity, (resource,)),)})
        self.starts = 0

    def build(self, **options):
        self.starts += 1
        return build_ctp_gateway_trading_profile(
            self.config, self.authority, self.tmp_path / f"registry-{self.starts}.sqlite",
            profile_ref="release-audit-test", settings=self.settings, grants=self.grants,
            request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
            assert_admitted=lambda _: True,
            control_grants={"operator-p": GatewayControlGrant("operator-a", True, frozenset(), self.ops)},
            **options)

    def start(self, **options):
        deployment = self.build(**options).deployment_factory()
        return deployment._provide_resources("gateway", "gateway-a", self.opts, self.authority.scope)


def refused_start(profile, code, **options):
    # A start refused for the release audit names its code to the operator (other journal
    # refusals at start stay the generic construction failure); the partial resources must
    # still be closed. A start that is not refused is closed before the test fails, so no
    # owner thread outlives it.
    try:
        resources = profile.start(**options)
    except ServiceResourceConstructionError as refused:
        refused.resources.close()
        assert str(refused) == code
        return
    resources.close()
    pytest.fail("the start was not refused")


def journal_state(profile):
    """(schema version, whether the release audit table exists), read from the file."""
    db = sqlite3.connect(profile.settings.journal_path)
    try:
        return (db.execute("SELECT schema_version FROM direct_journal_meta").fetchone()[0],
                db.execute("SELECT 1 FROM sqlite_master WHERE name='direct_control_releases'").fetchone() is not None)
    finally:
        db.close()


@pytest.fixture
def refusals(monkeypatch):
    """The release-audit refusals of the journal while it is opened."""
    seen = []
    original = GatewayDirectCommandJournal._assert_release_audit

    def recorded(self, db):
        try:
            return original(self, db)
        except DirectCommandJournalError as error:
            seen.append(error.code)
            raise

    monkeypatch.setattr(GatewayDirectCommandJournal, "_assert_release_audit", recorded)
    return seen


def test_the_options_are_strict_bools_and_need_the_control_option(tmp_path, monkeypatch):
    profile = Profile(tmp_path, monkeypatch)
    for options in ({"control_release_audit_enabled": 1}, {"upgrade_control_release_audit": "yes"}):
        with pytest.raises(CtpGatewayTradingError, match="^INVALID_CONTROL_CONFIGURATION$"):
            profile.build(upgrade_controls_from_v9=True, **options)
    with pytest.raises(CtpGatewayTradingError, match="^INVALID_CONTROL_RELEASE_AUDIT_CONFIGURATION$"):
        profile.build(upgrade_controls_from_v9=True, upgrade_control_release_audit=True)
    # Without the control option there is no cause to release.
    plain = tmp_path / "plain"
    plain.mkdir()
    _, settings, config, authority, grants, _ = make(plain, monkeypatch)
    with pytest.raises(CtpGatewayTradingError, match="^INVALID_CONTROL_RELEASE_AUDIT_CONFIGURATION$"):
        build_ctp_gateway_trading_profile(config, authority, plain / "registry.sqlite",
            profile_ref="plain", settings=settings, grants=grants, request_feed_factory=lambda: None,
            market_stream_factory=lambda *_: None, assert_admitted=lambda _: True,
            control_release_audit_enabled=True)


def test_the_profile_upgrades_only_when_asked_and_then_always_needs_the_audit(tmp_path, monkeypatch, refusals):
    profile = Profile(tmp_path, monkeypatch)
    # A journal at 10 without the audit, as every journal is today.
    resources = profile.start(upgrade_controls_from_v9=True)
    try:
        assert resources.values["binding"]._journal._control_release_audit is False
    finally:
        resources.close()
    assert journal_state(profile) == (10, False)
    # Asked for, but not permitted: refused, nothing changed.
    refused_start(profile, "CONTROL_RELEASE_AUDIT_UPGRADE_REQUIRED", control_release_audit_enabled=True)
    assert refusals == ["CONTROL_RELEASE_AUDIT_UPGRADE_REQUIRED"] and journal_state(profile) == (10, False)
    # Permitted: upgraded in place, schema version unchanged, and a release names its operator.
    resources = profile.start(control_release_audit_enabled=True, upgrade_control_release_audit=True)
    try:
        binding = resources.values["binding"]
        journal = binding._journal
        assert journal._control_release_audit is True and journal._schema_version == 10
        control = binding._control

        def apply_and_release():
            control.apply("stop", "STOP_NEW", scope_kind="account", origin="operator:" + "0" * 64)
            return control.resume("stop", expected_version=1, operation_id="r1", releaser="operator:" + "1" * 64)

        released = binding._owner.submit_service_call(apply_and_release).result(timeout_seconds=2)
        assert released["releaser"] == "operator:" + "1" * 64
    finally:
        resources.close()
    assert journal_state(profile) == (10, True)
    # The permission may stay installed: an upgraded journal is just opened.
    del refusals[:]
    resources = profile.start(control_release_audit_enabled=True, upgrade_control_release_audit=True)
    resources.close()
    resources = profile.start(control_release_audit_enabled=True)
    resources.close()
    assert refusals == []
    # Taking the option away does not silently open the journal without its audit.
    refused_start(profile, "CONTROL_RELEASE_AUDIT_MISMATCH")
    assert refusals == ["CONTROL_RELEASE_AUDIT_MISMATCH"] and journal_state(profile) == (10, True)


def test_a_new_journal_gets_the_audit_after_its_schema_upgrade(tmp_path, monkeypatch):
    profile = Profile(tmp_path, monkeypatch)
    assert journal_state(profile) == (9, False)
    # Not permitted: refused before this start upgrades anything, the schema included.
    refused_start(profile, "CONTROL_RELEASE_AUDIT_UPGRADE_REQUIRED", upgrade_controls_from_v9=True,
                  control_release_audit_enabled=True)
    assert journal_state(profile) == (9, False)
    resources = profile.start(upgrade_controls_from_v9=True, control_release_audit_enabled=True,
                              upgrade_control_release_audit=True)
    try:
        journal = resources.values["binding"]._journal
        assert (journal._schema_version, journal._control_release_audit) == (10, True)
    finally:
        resources.close()
    assert journal_state(profile) == (10, True)


def test_the_operator_profile_forwards_both_options(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import bt_api_py.ctp_gateway_operator_profile as module

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    resources = CtpGatewayTrustedResources(
        authority, tmp_path / "registry.sqlite", settings, grants, lambda: None, lambda *_args: None,
        lambda _: True, control_release_audit_enabled=True, upgrade_control_release_audit=True)
    seen = []

    def builder(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(deployment_factory=lambda: None)

    monkeypatch.setattr(module, "build_ctp_gateway_trading_profile", builder)
    create_ctp_gateway_profile_factory("ctp-test", resource_loader=lambda _: resources).create_deployment(config)
    assert seen[0]["control_release_audit_enabled"] is True and seen[0]["upgrade_control_release_audit"] is True


def test_the_bundle_section_is_optional_and_strict(tmp_path, monkeypatch):
    from bt_api_py.ctp_gateway_local_profile import (
        CtpLocalProfileError,
        load_simnow_trading_resources,
    )
    from tests.unit.test_iter63_ctp_gateway_local_profile import _fixture, _operator, _returns

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    doc["controls"] = {"operators": [_operator(doc)], "upgrade_controls_from_v9": False}

    def load():
        file.write_text(json.dumps(doc), encoding="utf-8")
        resources = load_simnow_trading_resources(config)
        return resources.control_release_audit_enabled, resources.upgrade_control_release_audit

    assert load() == (False, False)
    doc["controls"]["release_audit"] = {"upgrade_journal": False}
    assert load() == (True, False)
    doc["controls"]["release_audit"] = {"upgrade_journal": True}
    assert load() == (True, True)
    # It walks no schema: the drain chain stays off unless the drain section asks.
    assert load_simnow_trading_resources(config).upgrade_control_cancel_from_v16 is False
    for bad in ({"upgrade_journal": 1}, {}, {"upgrade_journal": True, "extra": 1}, True, None, []):
        doc["controls"]["release_audit"] = bad
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load()
