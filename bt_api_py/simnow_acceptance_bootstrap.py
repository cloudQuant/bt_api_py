"""Create new, private SimNow read-only service material, without starting services.

This does not prove account identity, source continuity, a unique owner, or readiness.
Configuring a Monitor heartbeat does not prove that a connection was established.
Never reuse a directory after any attempt, including a failed attempt.
"""
from __future__ import annotations

import json
import os
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class SimnowBootstrapError(RuntimeError):
    """A finite, value-free failure; underlying errors are deliberately suppressed."""


@dataclass(frozen=True)
class BootstrapResult:
    root: Path
    gateway_config: Path
    gateway_resources: Path
    risk_config: Path
    monitor_config: Path
    required_monitor_configured: bool = True
    ready: bool = False


def _write(path: Path, value: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        raise


def _json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode())


def _open_empty_directory(path: Path) -> int:
    if os.name != "posix" or not path.is_absolute() or any(p in (".", "..") for p in str(path).split("/")):
        raise ValueError
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parts[1:]:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            try:
                os.stat(".git", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError
            st = os.fstat(fd)
            if st.st_uid not in (0, os.geteuid()) or (st.st_mode & 0o022 and not (st.st_uid == 0 and st.st_mode & stat.S_ISVTX)):
                raise ValueError
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or st.st_mode & 0o077 or os.listdir(fd):
            raise ValueError
        return fd
    except BaseException:
        os.close(fd)
        raise


def bootstrap_simnow_acceptance(*, root: str | os.PathLike, credentials: Mapping[str, str],
                                instrument: Mapping[str, str], strategy_uuid: str,
                                gateway_port: int = 17200, risk_port: int = 17210,
                                monitor_ingress_port: int = 17220, monitor_heartbeat_port: int = 17221) -> BootstrapResult:
    """Build files in an existing empty private directory; failure leaves a marker.

    ``instrument`` requires venue, native_symbol, multiplier, tick_size and
    margin_pool. Caller must independently establish facts and owner exclusivity.
    Existing files are never reused or removed. No network or provider is started.
    """
    try:
        from bt_api_base.account_protocol import AccountKey
        from bt_api_base.market_protocol import MarketInstrumentKey
        from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
        from bt_api_gateway.session_guard import LocalAccountSessionGuard
        from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
        import zmq

        root = Path(root)
        credential_fields = {"broker_id", "user_id", "password", "auth_code", "app_id", "td_front", "md_front"}
        instrument_fields = {"venue", "native_symbol", "multiplier", "tick_size", "margin_pool"}
        if (not isinstance(credentials, Mapping) or set(credentials) != credential_fields
                or not isinstance(instrument, Mapping) or set(instrument) != instrument_fields
                or any(type(v) is not str or not v or v != v.strip() for v in (*credentials.values(), *instrument.values()))
                or str(uuid.UUID(strategy_uuid)) != strategy_uuid):
            raise ValueError
        from bt_api_base.account_protocol import AccountResourceKey
        from bt_api_ctp.account_resource_coverage import CTP_ACCOUNT_RESOURCE_COVERAGE

        if (AccountResourceKey("future", instrument["margin_pool"]),) != CTP_ACCOUNT_RESOURCE_COVERAGE:
            raise ValueError
        ports = (gateway_port, risk_port, monitor_ingress_port, monitor_heartbeat_port)
        if any(type(p) is not int or not 1 <= p <= 65535 for p in ports) or len(set(ports)) != 4:
            raise ValueError
        key = AccountKey("ctp", "simnow", "simnow", credentials["user_id"], credentials["broker_id"])
        scope = key.account_key_id
        symbol = instrument["native_symbol"]
        market_key = MarketInstrumentKey("ctp", instrument["venue"], "simnow", "future", symbol, symbol).to_dict()
        root_fd = _open_empty_directory(root)
        try:
            fd = os.open("BOOTSTRAP_STARTED", os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
            os.close(fd)
        finally:
            os.close(root_fd)
        for name in ("guard", "flow", "state", "keys", "risk-state", "monitor-state"):
            (root / name).mkdir(mode=0o700)
        guard = LocalAccountSessionGuard(root / "guard", scope, "simnow-bootstrap")
        acquired = False
        try:
            generation = guard.acquire()
            acquired = True
            journal = GatewayDirectCommandJournal.create(root / "state" / "gateway.sqlite", scope, generation=generation)
            journal.enable_controls(guard)
            journal.enable_strategy_ledger(guard)
            journal.enable_account_extensions(guard)
            journal_id = journal.journal_id
        finally:
            if acquired:
                # No provider was opened by this initializer.
                guard.release()
        issuer_path = root / "state" / "issuer.sqlite"
        _write(issuer_path, b"")
        issuer = DurableOrderCorrelationIssuer(issuer_path)
        try:
            issuer_id = issuer.issuer_id
        finally:
            issuer.close()
        keys, secrets = {}, {}
        for name in ("gateway", "strategy", "delegate", "control", "risk", "risk_operator", "events", "heartbeat", "producer", "gateway_heartbeat"):
            public, secret = zmq.curve_keypair()
            keys[name] = public.decode("ascii")
            secrets[name] = secret.decode("ascii")
            _write(root / "keys" / (name + ".public"), public)
            _write(root / "keys" / (name + ".secret"), secret)
        endpoint = lambda port: f"tcp://127.0.0.1:{port}"
        domain = "paper:simnow"
        def identity(role, principal, actor):
            return {"role": role, "principal_ref": principal, "actor_ref": actor, "delegate_ref": None}
        def wire(strategy=None, account=True):
            return {"execution_domain_ref": domain, "account_ref": scope if account else None,
                    "account_refs": [], "strategy_ref": strategy, "run_id": None, "manual_book_ref": None,
                    "portfolio_ref": None, "group_id": None, "leg_id": None}
        def resource(port, operations, strategy=None):
            return {"instance_id": "gateway-a", "port": port, "scope_ref": "scope-a",
                    "scope": wire(strategy), "operations": operations}
        account_ops = ["account.events.ack", "account.events.progress", "account.events.read_after"]
        controls = ["direct.control.apply", "direct.control.status", "direct.control.list",
                    "direct.control.cancel_scope", "direct.control.cancel_scope_status"]
        client_ops = ["direct.command_status"]
        delegate_ops = ["direct.delegated_reserve", "direct.delegated_dispatch", "direct.delegate_actor_status",
                        "direct.delegate_actor_cancel", "direct.delegate_actor_cancel_status",
                        "direct.delegate_actor_release", "direct.delegate_actor_release_status"]
        bundle = {
            "schema_version": 1, "registry_path": str(root / "state" / "registry.sqlite"),
            "settings": {"account_key": key.to_dict(), "account_scope": scope, "guard_root": str(root / "guard"),
                         "owner_id": "gateway-owner", "journal_path": str(root / "state" / "gateway.sqlite"),
                         "expected_journal_id": journal_id, "minimum_revision": 0, "trusted_flow_root": str(root / "flow"),
                         "market_source_ref": "simnow-market", "market_entitlement_ref": "simnow-entitlement", "write_enabled": False},
            "credentials": dict(credentials),
            "authority": {"identity": identity("service", "gateway-principal", "gateway-service"), "scope": wire(account=False),
                          "scope_ref": "scope-a", "scope_version": "scope-v1", "security_ref": "security-a", "security_version": "security-v1",
                          "registration_policy_ref": "direct-policy-v1", "registration_policy_version": "direct-policy-v1",
                          "service_requirements_ref": "direct-requirements-v1", "service_requirements_version": "direct-requirements-v1",
                          "curve_public_key": keys["gateway"], "curve_secret_key": secrets["gateway"]},
            "clients": [{"identity": identity("strategy_client", "strategy-principal", strategy_uuid), "curve_public_key": keys["strategy"],
                         "resources": [resource("service.direct_write", client_ops, strategy_uuid)], "symbols": [symbol]}],
            "market": None, "returns": {"instruments": [market_key], "query_interval_seconds": 1},
            "required_monitor": {"monitor_identity": identity("service", "heartbeat-principal", "heartbeat-service"),
                "instance_id": "heartbeat-instance", "scope_ref": "heartbeat-scope", "rpc_connect": endpoint(monitor_heartbeat_port),
                "server_curve_public_key": keys["heartbeat"], "client_curve_public_key": keys["gateway_heartbeat"],
                "client_curve_secret_key": secrets["gateway_heartbeat"],
                "interval_seconds": 1, "timeout_seconds": 5, "automatic_resume": False},
            "controls": {"upgrade_controls_from_v9": False, "operators": [{"identity": identity("application_client", "risk-control", "risk-control-actor"),
                "curve_public_key": keys["control"], "resources": [resource("service.direct_write", controls)],
                "account_scope_allowed": True, "strategies": [], "kind": "risk", "modes": ["STOP_NEW", "FREEZE"], "instrument_scope": True}]},
            "strategy_ledger": {"upgrade_from_v10": False, "account_comparison": {"upgrade_from_v11": False, "refresh_seconds": 30}},
            "delegation": {"delegates": [{"identity": identity("service", "risk-principal", "risk-delegate"), "curve_public_key": keys["delegate"],
                "kind": "risk", "revision": "risk-v1", "strategies": {strategy_uuid: {"instruments": [market_key],
                "operations": ["direct.reserve", "direct.dispatch", "direct.command_status", "direct.cancel", "direct.cancel_status", "direct.release"]}}, "resources": [resource("service.direct_write", delegate_ops, strategy_uuid),
                resource("service.account_read", account_ops)]}], "risk_only_submit": [strategy_uuid]},
            "account_observation": {"read_journal_path": str(root / "state" / "account-read.sqlite"),
                "observation_identity_path": str(issuer_path), "expected_observation_issuer_id": issuer_id,
                "state_scope": "account-state", "required_coverage": [{"product_type": "future", "margin_pool": instrument["margin_pool"]}],
                "observers": [], "operator_reads": [{"principal_ref": "risk-control", "resources": [resource("service.account_read", ["account.snapshot.typed"])]}]}}
        options = {"role": "trading_owner", "dispatch_enabled": True, "cancel_enabled": True, "control_enabled": True,
                   "strategy_ledger_enabled": True, "account_extensions_enabled": True, "delegation_enabled": True}
        service = {"service_config_schema_version": 1, "process_role": "service", "service_id": "gateway-service", "scope_ref": "scope-a",
                   "transport_ref": "server-transport", "registration_policy_ref": "direct-policy-v1", "service_requirements_ref": "direct-requirements-v1",
                   "components": {"gateway": {"config_schema_version": 2, "settings": {"instance_id": "gateway-a", "bind_ports": ["service.direct_write", "service.account_read"], "options": options}}},
                   "communication": {"config_schema_version": 1, "profiles": {"server-transport": {"backend": "zmq", "config_schema_version": 1,
                       "endpoints": {"rpc_bind": endpoint(gateway_port)}, "security_profile_ref": "security-a"}}}}
        risk = {"schema": "bt_api_risk.service.v1", "service": {"service_id": "risk-service", "principal_ref": "risk-host", "instance_id": "risk-a",
                  "scope_ref": "risk-scope", "rpc_bind": endpoint(risk_port), "public_key_file": "keys/risk.public", "secret_key_file": "keys/risk.secret"},
                "account": {"route": "CTP___FUTURE", "provider": "ctp", "environment": "simnow", "account_scope": scope, "execution_domain_ref": domain,
                    "instruments": {symbol: {"venue": instrument["venue"], "native_symbol": symbol, "product_type": "future", "multiplier": instrument["multiplier"], "tick_size": instrument["tick_size"]}}},
                "storage": {"directory": "risk-state"}, "policy": {"file": "risk-policy.json"},
                "principals": [{"principal_ref": "strategy-principal", "role": "strategy", "actor_ref": strategy_uuid, "strategy_refs": [strategy_uuid],
                    "operations": ["risk.order.reserve", "risk.order.dispatch", "risk.order.release", "risk.order.status", "risk.status"], "public_key_file": "keys/strategy.public"},
                    {"principal_ref": "risk-operator", "role": "operator", "actor_ref": "risk-operator-actor", "strategy_refs": [], "operations": ["risk.admin.status"], "public_key_file": "keys/risk_operator.public"}],
                "gateway": {"service_id": "gateway-service", "principal_ref": "gateway-principal", "instance_id": "gateway-a", "scope_ref": "scope-a", "rpc_endpoint": endpoint(gateway_port),
                    "server_public_key_file": "keys/gateway.public", "account_snapshot": {"resources": [{"product_type": "future", "margin_pool": instrument["margin_pool"]}]}, "delegate": {"principal_ref": "risk-principal", "actor_ref": "risk-delegate", "public_key_file": "keys/delegate.public", "secret_key_file": "keys/delegate.secret", "grant_revisions": {strategy_uuid: "risk-v1"}},
                    "control": {"principal_ref": "risk-control", "actor_ref": "risk-control-actor", "public_key_file": "keys/control.public", "secret_key_file": "keys/control.secret"}},
                "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": "10", "dispatch_recheck_seconds": 5},
                "refresh": {"loop_timeout_ms": 20, "observation_poll_seconds": "1", "maintain_seconds": 1, "dispatch_recheck_limit": 8, "account_snapshot_seconds": "10", "account_snapshot_max_age_seconds": "30"}}
        policy = {"schema": "bt_api_risk.policy.v2", "policy_id": "simnow-readonly", "account": {"route": "CTP___FUTURE", "account_scope": scope},
                  "per_order": {"max_quantity": {strategy_uuid: {symbol: "1"}}}}
        def listener(name, principal, actor, key_name, port):
            return {"service_id": name + "-service", "principal_ref": name + "-principal", "instance_id": name + "-instance", "scope_ref": name + "-scope",
                    "rpc_bind": endpoint(port), "public_key_file": "keys/" + name + ".public", "secret_key_file": "keys/" + name + ".secret",
                    "principals": [{"principal_ref": principal, "actor_ref": actor, "public_key_file": "keys/" + key_name + ".public"}]}
        monitor = {"schema": "bt_api_monitor.service.v1", "scope": {"execution_domain_ref": domain, "account_ref": scope},
                   "storage": {"directory": "monitor-state", "outbox_scope": "events", "event_types": ["service.observation"]},
                   "ingress": listener("events", "producer", "producer-service", "producer", monitor_ingress_port), "heartbeat": listener("heartbeat", "gateway-principal", "gateway-service", "gateway_heartbeat", monitor_heartbeat_port)}
        _json(root / "gateway-resources.json", bundle)
        _json(root / "gateway.json", {"gateway_operator_config_schema_version": 2, "profile_ref": "ctp-simnow-trading", "service_config": service})
        _json(root / "risk.json", risk)
        _json(root / "risk-policy.json", policy)
        _json(root / "monitor.json", monitor)
        from bt_api_py.risk_service_config import load_risk_service_config
        from bt_api_py.monitor_service_config import load_monitor_service_config
        from bt_api_risk.pretrade.policy import validate_policy_v2_document

        load_risk_service_config(root / "risk.json")
        load_monitor_service_config(root / "monitor.json")
        validate_policy_v2_document(policy)
        _write(root / "BOOTSTRAP_COMPLETE", b"CONFIGURED_NOT_STARTED\n")
        return BootstrapResult(root, root / "gateway.json", root / "gateway-resources.json", root / "risk.json", root / "monitor.json")
    except Exception:
        raise SimnowBootstrapError("SIMNOW_BOOTSTRAP_FAILED") from None
