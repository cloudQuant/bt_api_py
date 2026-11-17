"""Disabled ProductID path does not add a CTP import to profile composition."""

import subprocess
import sys


def test_default_profile_builds_in_clean_process_without_importing_ctp():
    script = r"""
import builtins, sys
original = builtins.__import__
def deny(name, *args, **kwargs):
    if name == "bt_api_ctp" or name.startswith("bt_api_ctp."):
        raise ImportError("CTP intentionally unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = deny

import tempfile
from pathlib import Path
import zmq
from bt_api_base.account_protocol import AccountKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingSettings, build_ctp_gateway_trading_profile
from bt_api_py.gateway_direct_service import GatewayDirectGrant
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "guard").mkdir()
    (root / "flow").mkdir(mode=0o700)
    account = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    journal = GatewayDirectCommandJournal.create(root / "journal.sqlite", account.account_key_id, generation="old")
    settings = CtpGatewayTradingSettings(account, account.account_key_id, root / "guard", "owner", root / "journal.sqlite",
        journal.journal_id, 0, root / "flow", "source", "entitlement", write_enabled=False)
    service = WireIdentity(WireRole.SERVICE, "gateway", "gateway-service")
    strategy = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-p", "strategy")
    scope = WireScope("domain", account_ref=account.account_key_id, strategy_ref="strategy")
    public, secret = zmq.curve_keypair()
    client_public, _ = zmq.curve_keypair()
    authority = TrustedServiceAuthority(identity=service, scope=WireScope("domain"), scope_ref="scope",
        scope_version="v1", security_ref="security", security_version="v1", registration_policy_ref="policy",
        registration_policy_version="v1", service_requirements_ref="requirements", service_requirements_version="v1",
        curve_public_key=public, curve_secret_key=secret, principals={client_public: strategy.principal_ref},
        grants={strategy.principal_ref: (PrincipalGrant(strategy, (ResourceGrant("gateway-a", "service.direct_write", "scope", scope,
            frozenset({"direct.command_status"})),)),)})
    config = parse_service_config({"service_config_schema_version": 1, "process_role": "service", "service_id": "gateway-service",
        "scope_ref": "scope", "transport_ref": "server", "registration_policy_ref": "policy", "service_requirements_ref": "requirements",
        "components": {"gateway": {"config_schema_version": 2, "settings": {"instance_id": "gateway-a", "bind_ports": ["service.direct_write"],
            "options": {"role": "trading_owner", "dispatch_enabled": True}}}},
        "communication": {"config_schema_version": 1, "profiles": {"server": {"backend": "zmq", "config_schema_version": 1,
            "endpoints": {"rpc_bind": "tcp://127.0.0.1:1"}, "security_profile_ref": "security"}}}})
    profile = build_ctp_gateway_trading_profile(config, authority, root / "registry.sqlite", profile_ref="test", settings=settings,
        grants={strategy.principal_ref: GatewayDirectGrant("strategy", frozenset({"rb"}))},
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None, assert_admitted=lambda _: False)
    assert profile.profile_ref == "test"
    assert not any(name == "bt_api_ctp" or name.startswith("bt_api_ctp.") for name in sys.modules)
    assert "bt_api_py.gateway_instrument_product_service" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
