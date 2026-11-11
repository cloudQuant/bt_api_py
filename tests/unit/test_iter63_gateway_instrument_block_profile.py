"""The CTP trading profile watches unresolved returns whenever it has a control plane (D63-59)."""
import time
from dataclasses import replace

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py import gateway_instrument_block as block_module
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError, build_ctp_gateway_trading_profile
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.test_iter63_ctp_gateway_trading import make


def _deploy(tmp_path, monkeypatch, *, controls):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    options = raw["components"]["gateway"]["settings"]["options"]
    extra = {}
    if controls:
        options["control_enabled"] = True
        identity = WireIdentity(WireRole.APPLICATION_CLIENT, "operator-p", "operator-a")
        scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
        operations = frozenset({"direct.control.status"})
        resource = ResourceGrant("gateway-a", "service.direct_write", config.scope_ref, scope, operations)
        public, _ = zmq.curve_keypair()
        authority = replace(authority, principals={**authority.principals, public: "operator-p"},
                            grants={**authority.grants, "operator-p": (PrincipalGrant(identity, (resource,)),)})
        extra = {"control_grants": {"operator-p": GatewayControlGrant("operator-a", True, frozenset(), operations)},
                 "upgrade_controls_from_v9": True}
    config = parse_service_config(raw)
    profile = build_ctp_gateway_trading_profile(
        config, authority, tmp_path / "registry.sqlite", profile_ref="block", settings=settings, grants=grants,
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None, assert_admitted=lambda _: True,
        **extra)
    return profile.deployment_factory()._provide_resources("gateway", "gateway-a", options, authority.scope)


def test_profile_with_controls_installs_the_watcher_and_drives_it_on_the_owner_thread(tmp_path, monkeypatch):
    passes = []
    original = block_module.GatewayInstrumentBlock.maintain

    def counted(self):
        passes.append(self)
        return original(self)

    monkeypatch.setattr(block_module.GatewayInstrumentBlock, "maintain", counted)
    resources = _deploy(tmp_path, monkeypatch, controls=True)
    try:
        watcher = resources.values["instrument_block"]
        assert type(watcher) is block_module.GatewayInstrumentBlock
        binding = resources.values["binding"]
        assert watcher._journal is binding._journal and watcher._control is binding._control
        assert watcher.origin == block_module.unresolved_return_origin(binding._journal.account_scope)
        deadline = time.monotonic() + 5
        while len(passes) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(passes) >= 2 and all(item is watcher for item in passes)   # the profile's own loop calls it
        assert watcher._next > 0 and watcher._failed is None                  # and its passes read the journal
        # Released trades are handed to the strategy ledger; this profile has none, and says so.
        with pytest.raises(CtpGatewayTradingError, match="^STRATEGY_LEDGER_UNAVAILABLE$"):
            watcher._record_released({"cause_id": "c", "resume_id": "r", "scope_value": "SHFE:rb2701"}, [])
    finally:
        resources.close()


def test_profile_without_controls_has_no_watcher(tmp_path, monkeypatch):
    resources = _deploy(tmp_path, monkeypatch, controls=False)
    try:
        assert "instrument_block" not in resources.values
    finally:
        resources.close()


def test_the_trading_owner_declares_the_watcher_as_an_optional_resource():
    # The host refuses a profile that returns a resource it does not know (the start fails).
    from bt_api_py.service_components import _ROLE_BUILTINS

    assert "instrument_block" in _ROLE_BUILTINS[("gateway", "trading_owner")].optional_resource_keys
