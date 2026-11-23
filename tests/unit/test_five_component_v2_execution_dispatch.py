"""Unit & loopback ZMQ CURVE tests for Win11 Gateway profile, Risk v2 Execution child RPCs, and ExecutionParentDispatcher."""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
import socket
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import zmq
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
from bt_api_base.gateway_execution_stream_protocol import GatewayExecutionObservation
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import (
    RISK_ADMIN_PORT,
    RISK_ORDER_OPERATIONS,
    RISK_SERVICE_PORT,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.account_journal_host import AccountJournalHost
from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.frozen_algorithm_plan import algorithm_plan_digest
from bt_api_gateway.active_order_cutpoint import build_gateway_order_cut_snapshot
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade import (
    AccountSnapshot,
    CallerIdentity,
    GatewayReply,
    InstrumentFacts,
    PolicyStore,
    PretradeEngine,
    PretradeLedger,
    PriceFact,
    RuleRegistry,
)
from bt_api_risk.pretrade.order_cut_bridge import apply_gateway_order_cut_snapshot
from bt_api_risk.risk_order_journal import RiskOrderJournal

from bt_api_py.ctp_gateway_local_profile import CtpLocalProfileError, _protected_json
from bt_api_py.execution_parent_service_host import ParentServiceHost, load_config as load_parent_cfg
from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import RiskPrincipal, ensure_private_directory, restrict_file
from bt_api_py.risk_service_host import POLICY_LABELS
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

DOMAIN = "paper"
ACCOUNT = "account-test-v2-dispatch"
STRATEGY = "11111111-1111-4111-8111-111111111111"
RUN_ID = "run-v2-exec-001"
INSTRUMENT = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _owner_only(path: Path) -> None:
    if os.name == "nt":
        import win32con
        import win32security

        sd = win32security.GetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION)
        owner = sd.GetSecurityDescriptorOwner()
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, owner)
        sd.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION, sd)
    else:
        restrict_file(path)


def _write_key(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    _owner_only(path)
    return path


class _RecordingGatewayPort:
    def __init__(self) -> None:
        self.reserves: list[dict] = []
        self.dispatches: list[dict] = []
        self._commands: dict[str, dict] = {}

    def reserve(self, **kwargs):
        self.reserves.append(dict(kwargs))
        key = kwargs["request_key"]
        digest = hashlib.sha256(kwargs["canonical_payload"].encode("utf-8")).hexdigest()
        res_id = f"res-{len(self.reserves)}"
        self._commands[key] = {"digest": digest, "reservation_id": res_id, "state": "RESERVED"}
        return GatewayReply(
            "OBSERVED",
            state="RESERVED",
            reservation_id=res_id,
            digest=digest,
            owner_generation="owner-1",
            reservation_generation="owner-1",
            version=1,
        )

    def dispatch(self, **kwargs):
        self.dispatches.append(dict(kwargs))
        key = kwargs["request_key"]
        cmd = self._commands[key]
        cmd["state"] = "DISPATCHING"
        return GatewayReply(
            "QUEUED",
            state="DISPATCHING",
            reservation_id=cmd["reservation_id"],
            digest=cmd["digest"],
            owner_generation="owner-1",
            reservation_generation="owner-1",
            version=2,
        )

    def release(self, **kwargs):
        key = kwargs["request_key"]
        cmd = self._commands.get(key, {})
        return GatewayReply(
            "OBSERVED",
            state="RELEASED",
            reservation_id=cmd.get("reservation_id"),
            digest=cmd.get("digest"),
            owner_generation="owner-1",
            reservation_generation="owner-1",
            version=2,
        )

    def status(self, **kwargs):
        key = kwargs["request_key"]
        cmd = self._commands.get(key)
        if cmd is None:
            return GatewayReply("OBSERVED", code="NOT_FOUND")
        return GatewayReply(
            "OBSERVED",
            state=cmd["state"],
            reservation_id=cmd["reservation_id"],
            digest=cmd["digest"],
            owner_generation="owner-1",
            reservation_generation="owner-1",
            version=2,
        )


class _StubControlPort:
    def apply(self, **_kwargs):
        return True

    def cancel_scope(self, **_kwargs):
        return True

    def cancel_scope_status(self, **_kwargs):
        return None


class _StubFactsPort:
    def __init__(self, instrument: MarketInstrumentKey, now_ns: int) -> None:
        self._instrument = instrument
        self._now_ns = now_ns

    def reference_price(self, item: MarketInstrumentKey) -> PriceFact | None:
        if item.instrument_id != self._instrument.instrument_id:
            return None
        return PriceFact(
            price=Decimal("3100"),
            source="tick",
            ts_ns=self._now_ns,
            valid_until_ns=self._now_ns + 60_000_000_000,
            quality_ok=True,
        )

    def market_ref(self, item: MarketInstrumentKey) -> dict | None:
        if item.instrument_id != self._instrument.instrument_id:
            return None
        return {"key": {}, "epoch": "e1", "sequence": 1, "message_id": "m1"}

    def instrument_facts(self, item: MarketInstrumentKey) -> InstrumentFacts | None:
        if item.instrument_id != self._instrument.instrument_id:
            return None
        return InstrumentFacts(instrument=item, multiplier=Decimal("10"), tick_size=Decimal("1"))

    def account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            as_of_ns=self._now_ns,
            trading_day="20300101",
            positions={},
            balance=Decimal("1000000"),
            available=Decimal("900000"),
            margin=Decimal("0"),
            close_profit=Decimal("0"),
            position_profit=Decimal("0"),
            commission=Decimal("0"),
            equity=Decimal("1000000"),
            currency="CNY",
            complete=True,
            valid_until_ns=self._now_ns + 60_000_000_000,
        )

    def strategy_financial(self, strategy_ref: str):
        return None

    def margin_rate(self, item: MarketInstrumentKey):
        return None

    def trading_day(self) -> str:
        return "20300101"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only _protected_json parity check")
def test_ctp_gateway_local_profile_protected_json_windows(tmp_path):
    private_dir = tmp_path / "private"
    ensure_private_directory(private_dir)
    ok_file = private_dir / "resource.json"
    ok_file.write_text(json.dumps({"hello": "world", "n": 1}), encoding="utf-8")
    # Inherited others-writable Windows DACL must fail closed
    with pytest.raises(CtpLocalProfileError):
        _protected_json(str(ok_file))
    # Owner-only DACL passes strict Windows parity verification
    _owner_only(ok_file)
    assert _protected_json(str(ok_file)) == {"hello": "world", "n": 1}

    dup_file = private_dir / "dup.json"
    dup_file.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    _owner_only(dup_file)
    with pytest.raises(CtpLocalProfileError):
        _protected_json(str(dup_file))

    nan_file = private_dir / "nan.json"
    nan_file.write_text('{"a": NaN}', encoding="utf-8")
    _owner_only(nan_file)
    with pytest.raises(CtpLocalProfileError):
        _protected_json(str(nan_file))


def test_execution_parent_dispatcher_end_to_end_to_risk_v2_and_gateway(tmp_path):
    """Full loopback ZMQ CURVE test: Strategy -> Execution -> Risk v2 -> Gateway delegate."""
    base = tmp_path / "e2e"
    ensure_private_directory(base)
    keys_dir = base / "keys"
    ensure_private_directory(keys_dir)
    risk_dir = base / "risk"
    ensure_private_directory(risk_dir)
    exec_dir = base / "exec"
    ensure_private_directory(exec_dir)
    exec_journal_dir = exec_dir / "journal"
    ensure_private_directory(exec_journal_dir)
    exec_reg_dir = exec_dir / "registry"
    ensure_private_directory(exec_reg_dir)
    for d in (base, keys_dir, risk_dir, exec_dir, exec_journal_dir, exec_reg_dir):
        _owner_only(d)

    risk_srv_pub_b, risk_srv_sec_b = zmq.curve_keypair()
    exec_srv_pub_b, exec_srv_sec_b = zmq.curve_keypair()
    exec_risk_pub_b, exec_risk_sec_b = zmq.curve_keypair()
    strat_exec_pub_b, strat_exec_sec_b = zmq.curve_keypair()

    risk_srv_pub = _write_key(keys_dir / "risk_srv.pub", risk_srv_pub_b)
    exec_srv_pub = _write_key(keys_dir / "exec_srv.pub", exec_srv_pub_b)
    exec_srv_sec = _write_key(keys_dir / "exec_srv.sec", exec_srv_sec_b)
    exec_risk_pub = _write_key(keys_dir / "exec_risk.pub", exec_risk_pub_b)
    exec_risk_sec = _write_key(keys_dir / "exec_risk.sec", exec_risk_sec_b)
    strat_exec_pub = _write_key(keys_dir / "strat_exec.pub", strat_exec_pub_b)

    risk_port_num = _free_port()
    exec_port_num = _free_port()
    risk_ep = f"tcp://127.0.0.1:{risk_port_num}"
    exec_ep = f"tcp://127.0.0.1:{exec_port_num}"

    # 1. Start real Risk ServiceHost with PretradeEngine(parent_contract_enabled=True) + RiskOrderService(parent_v2_execution_enabled=True)
    clock_ns = time.time_ns()
    gw_port = _RecordingGatewayPort()
    exec_identity = WireIdentity(WireRole.SERVICE, "execution-host-test", "execution-service-test")
    exec_ops = frozenset(
        {
            "risk.parent.reserve",
            "risk.parent.release",
            "risk.parent.status",
            "risk.order.reserve.v2",
            "risk.order.dispatch.v2",
            "risk.order.status",
            "risk.status",
        }
    )
    risk_principals = {
        exec_identity.principal_ref: RiskPrincipal(
            principal_ref=exec_identity.principal_ref,
            role="execution",
            actor_ref=exec_identity.actor_ref,
            strategy_refs=frozenset({STRATEGY}),
            operations=exec_ops,
            public_key=exec_risk_pub_b,
        )
    }
    risk_svc_cfg = parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": "risk-service-test",
            "scope_ref": "risk-scope-test",
            "transport_ref": "risk-server",
            "registration_policy_ref": POLICY_LABELS["registration_policy_ref"],
            "service_requirements_ref": POLICY_LABELS["service_requirements_ref"],
            "components": {
                "risk": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": "risk-inst-test",
                        "bind_ports": [RISK_SERVICE_PORT, RISK_ADMIN_PORT],
                        "options": {"role": "order_service"},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "risk-server": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": risk_ep},
                        "security_profile_ref": POLICY_LABELS["security_ref"],
                    }
                },
            },
        }
    )
    risk_authority = TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "risk-host-test", "risk-service-test"),
        scope=WireScope(DOMAIN),
        scope_ref="risk-scope-test",
        scope_version=POLICY_LABELS["scope_version"],
        security_ref=POLICY_LABELS["security_ref"],
        security_version=POLICY_LABELS["security_version"],
        registration_policy_ref=POLICY_LABELS["registration_policy_ref"],
        registration_policy_version=POLICY_LABELS["registration_policy_version"],
        service_requirements_ref=POLICY_LABELS["service_requirements_ref"],
        service_requirements_version=POLICY_LABELS["service_requirements_version"],
        curve_public_key=risk_srv_pub_b,
        curve_secret_key=risk_srv_sec_b,
        principals={exec_risk_pub_b: exec_identity.principal_ref},
        grants={
            exec_identity.principal_ref: (
                PrincipalGrant(
                    exec_identity,
                    (
                        ResourceGrant(
                            "risk-inst-test",
                            RISK_SERVICE_PORT,
                            "risk-scope-test",
                            WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY),
                            exec_ops & RISK_ORDER_OPERATIONS,
                        ),
                    ),
                ),
            )
        },
    )
    risk_stop = threading.Event()
    risk_ready = threading.Event()
    risk_errors: list[BaseException] = []

    def _run_risk() -> None:
        risk_host = None
        with ExitStack() as stack:
            try:
                journal = RiskOrderJournal(risk_dir / "risk.sqlite", writer_id="unit-e2e")
                fence = stack.enter_context(journal.writer_fence(AccountScope("ctp", ACCOUNT, "simnow")))
                ledger = PretradeLedger(journal, fence, schema_version=4, v2_clock_ns=lambda: clock_ns)
                ledger.enable_r05_observations_v2()
                engine = PretradeEngine(
                    ledger,
                    PolicyStore(ledger),
                    RuleRegistry(),
                    _StubFactsPort(INSTRUMENT, clock_ns),
                    gw_port,
                    _StubControlPort(),
                    account_scope=ACCOUNT,
                    route="CTP___FUTURE",
                    now_ns=lambda: clock_ns,
                    reservation_ttl_seconds=30.0,
                    actor_principal="risk-actor-test",
                    dispatch_recheck_seconds=5.0,
                    dispatch_recheck_limit=4,
                    parent_contract_enabled=True,
                )
                operator = CallerIdentity(
                    "operator-test",
                    "ops",
                    "operator",
                    frozenset(),
                    None,
                    frozenset({"risk.policy.publish"}),
                )
                policy_doc = {
                    "schema": "bt_api_risk.policy.v2",
                    "policy_id": "test-v2-dispatch",
                    "account": {"route": "CTP___FUTURE", "account_scope": ACCOUNT},
                    "per_order": {"max_quantity": {STRATEGY: {"rb2701": "10"}}},
                    "self_trade": {"enabled": True, "on_unverifiable": "deny"},
                    "closable_check": {"enabled": False},
                }
                engine.publish_policy(operator, policy_doc, 0, "unit-test")

                source_binding = {
                    "service_id": "gateway-service",
                    "instance_id": "gateway-one",
                    "peer_role": "service",
                    "peer_principal": "gateway-principal",
                    "peer_actor": "gateway-actor",
                    "peer_delegate": None,
                    "fingerprint": "f" * 64,
                }
                fact_epoch = "e" * 64
                journal_id = "gw-journal-e2e"
                obs_payload = GatewayExecutionObservation(
                    account_scope=ACCOUNT,
                    route="CTP___FUTURE",
                    trading_day="20300101",
                    instrument=INSTRUMENT,
                    kind=DirectExecutionEventKind.ORDER,
                    side="sell",
                    offset="open",
                    order_quantity="1",
                    filled_quantity="1",
                    limit_price="3100",
                    status=DirectOrderStatus.FILLED,
                    native_identity_digest=hashlib.sha256(b"seed-1").hexdigest(),
                    identity_key_digests=(hashlib.sha256(b"ref:seed-1").hexdigest(),),
                    venue_key_digest=None,
                ).to_mapping()
                fact_event = {
                    "sequence": 1,
                    "fact_digest": hashlib.sha256(
                        json.dumps(obs_payload, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                    "observation": obs_payload,
                    "disposition": "UNATTRIBUTED",
                    "reason": "UNMATCHED",
                    "strategy_scope": None,
                    "execution_sequence": None,
                    "reservation_id": None,
                    "origin": None,
                    "parent_ref": None,
                    "child_id": None,
                    "external_receipt_digest": None,
                    "phase": "LIVE",
                    "event_digest": hashlib.sha256(b"fact-event-1").hexdigest(),
                }
                with ledger.transaction() as txn:
                    txn.apply_r05_bound_fact_page(
                        source_binding=source_binding,
                        gateway_account=ACCOUNT,
                        gateway_journal_id=journal_id,
                        epoch=fact_epoch,
                        consumer_ref="risk-consumer-e2e",
                        after_sequence=0,
                        offered_sequence=1,
                        acknowledged_sequence=0,
                        bootstrap_count=0,
                        has_more=False,
                        events=(fact_event,),
                    )
                    txn.record_r05_bound_ack_observation(
                        source_binding=source_binding,
                        epoch=fact_epoch,
                        sequence=1,
                    )
                cut_snap = build_gateway_order_cut_snapshot(
                    account_scope=ACCOUNT,
                    journal_id=journal_id,
                    owner_generation="gen-1",
                    fact_epoch=fact_epoch,
                    fact_watermark=1,
                    source_binding_ref=hashlib.sha256(f"{ACCOUNT}:{journal_id}".encode()).hexdigest(),
                    current_session_trading_day="20300101",
                    registration_id="reg-1",
                    service_epoch="srv-epoch-1",
                    query_observations=(),
                )
                sync = apply_gateway_order_cut_snapshot(
                    ledger,
                    source_binding=source_binding,
                    gateway_account=ACCOUNT,
                    snapshot=cut_snap,
                    observed_at_ns=clock_ns,
                    max_age_ns=60_000_000_000,
                )
                assert sync.verified is True

                risk_component = RiskOrderService(
                    engine,
                    ledger,
                    account_scope=ACCOUNT,
                    route="CTP___FUTURE",
                    execution_domain_ref=DOMAIN,
                    principals=risk_principals,
                    instrument_for=lambda exch, sym: INSTRUMENT if (exch, sym) == ("SHFE", "rb2701") else None,
                    ready=lambda: True,
                    parent_v2_execution_enabled=True,
                )
                risk_host = ServiceHost(
                    risk_svc_cfg,
                    risk_authority,
                    risk_dir / "registry.sqlite",
                    lambda *_: ComponentResources.borrowed({"service": risk_component}, ready=lambda: True),
                    enable_risk_parent_execution_writes=True,
                )
                risk_host.start()
                risk_ready.set()
                while not risk_stop.is_set():
                    risk_host.serve_once(50)
            except BaseException as exc:
                risk_errors.append(exc)
                risk_ready.set()
            finally:
                if risk_host is not None:
                    try:
                        risk_host.close()
                    except Exception:
                        pass

    risk_thread = threading.Thread(target=_run_risk, daemon=True)
    risk_thread.start()
    assert risk_ready.wait(10.0)
    assert not risk_errors, risk_errors

    # 2. Bootstrap Execution journal and start ParentServiceHost with dispatch wired to Risk
    storage_id = str(uuid.uuid4())
    local_scope = ExecutionScope("ctp", "simnow", ACCOUNT, STRATEGY)
    with AccountJournalHost(root=str(exec_journal_dir), scope=local_scope, storage_id=storage_id) as j:
        j.acquire_intake_lease(local_scope, "exec-boot", ttl_ns=30_000_000_000)

    parent_cfg_path = exec_dir / "parent_config.json"
    parent_cfg_path.write_text(
        json.dumps(
            {
                "schema": "bt_api_execution.parent_service.v1",
                "service": {
                    "service_config_schema_version": 1,
                    "process_role": "service",
                    "service_id": exec_identity.actor_ref,
                    "scope_ref": "exec-scope-test",
                    "transport_ref": "exec-server",
                    "registration_policy_ref": "exec-policy-v1",
                    "service_requirements_ref": "exec-req-v1",
                    "components": {
                        "execution": {
                            "config_schema_version": 2,
                            "settings": {
                                "instance_id": "exec-inst-test",
                                "bind_ports": ["service.execution_parent"],
                                "options": {"role": "atomic_intake"},
                            },
                        }
                    },
                    "communication": {
                        "config_schema_version": 1,
                        "profiles": {
                            "exec-server": {
                                "backend": "zmq",
                                "config_schema_version": 1,
                                "endpoints": {"rpc_bind": exec_ep},
                                "security_profile_ref": "exec-curve",
                            }
                        },
                    },
                },
                "service_identity": {
                    "role": "service",
                    "principal_ref": exec_identity.principal_ref,
                    "actor_ref": exec_identity.actor_ref,
                },
                "authority_versions": {
                    "scope": "v1",
                    "security": "v1",
                    "registration_policy": "v1",
                    "requirements": "v1",
                },
                "execution_scope": {
                    "provider": "ctp",
                    "environment": "simnow",
                    "account_ref": ACCOUNT,
                    "strategy_id": STRATEGY,
                },
                "wire_scope": {
                    "execution_domain_ref": DOMAIN,
                    "account_ref": ACCOUNT,
                    "strategy_ref": STRATEGY,
                    "run_id": RUN_ID,
                },
                "storage": {
                    "journal_root": str(exec_journal_dir),
                    "registry_path": str(exec_reg_dir / "registry.sqlite"),
                    "storage_id": storage_id,
                    "writer_mode": "single_host_cooperative",
                    "require_existing": True,
                },
                "server_keys": {
                    "public_key_file": str(exec_srv_pub),
                    "secret_key_file": str(exec_srv_sec),
                },
                "strategy_grants": [
                    {
                        "identity": {
                            "role": "strategy_client",
                            "principal_ref": "strategy-principal-test",
                            "actor_ref": STRATEGY,
                        },
                        "scope_ref": "exec-scope-test",
                        "revision": "grant-v1",
                        "instruments": [INSTRUMENT.to_dict()],
                        "operations": ["execution.parent.submit", "execution.parent.query"],
                        "public_key_file": str(strat_exec_pub),
                    }
                ],
                "action_grant": {
                    "revision": "action-v1",
                    "instruments": [INSTRUMENT.to_dict()],
                    "plan_versions": ["1"],
                    "actions": ["accept", "query"],
                },
                "schedule_build_id": "build-local-1",
                "algorithm_policies": [
                    {
                        "plan_version": "1",
                        "schedules": [{"name": "PASS", "version": 1, "params": {}, "max_retry": 0}],
                    }
                ],
                "lease": {"owner": "exec-owner-live", "ttl_ns": 30_000_000_000},
                "dispatch": {
                    "route": "CTP___FUTURE",
                    "risk_endpoint": risk_ep,
                    "risk_service_id": "risk-service-test",
                    "risk_instance_id": "risk-inst-test",
                    "risk_scope_ref": "risk-scope-test",
                    "risk_peer": {
                        "role": "service",
                        "principal_ref": "risk-host-test",
                        "actor_ref": "risk-service-test",
                    },
                    "risk_server_public": str(risk_srv_pub),
                    "client_public": str(exec_risk_pub),
                    "client_secret": str(exec_risk_sec),
                    "poll_interval_seconds": 0.05,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _owner_only(parent_cfg_path)

    exec_host = ParentServiceHost(load_parent_cfg(str(parent_cfg_path)))
    exec_stop = threading.Event()
    exec_ready = threading.Event()
    exec_errors: list[BaseException] = []

    def _run_exec() -> None:
        try:
            exec_host.start()
            exec_ready.set()
            while not exec_stop.is_set():
                exec_host.poll()
        except BaseException as exc:
            exec_errors.append(exc)
            exec_ready.set()
        finally:
            try:
                exec_host.close()
            except Exception:
                pass

    exec_thread = threading.Thread(target=_run_exec, daemon=True)
    exec_thread.start()
    assert exec_ready.wait(15.0)
    assert not exec_errors, exec_errors

    # 3. Strategy submits parent order to Execution over real ZMQ CURVE
    from bt_api_transport_zmq.server import CurveClientCredentials
    from bt_api_transport_zmq.service_v2 import ZmqServiceClient
    from tests.unit.test_service_host import _request

    strat_identity = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal-test", STRATEGY)
    strat_scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY, run_id=RUN_ID)
    strat_client = ZmqServiceClient(
        exec_ep,
        exec_identity,
        credentials=CurveClientCredentials(exec_srv_pub_b, strat_exec_pub_b, strat_exec_sec_b),
    )
    try:
        reg_reply = strat_client.request(
            _request(
                "service.register",
                {
                    "registration_key": "strat-reg-e2e",
                    "client_session_id": "strat-sess-e2e",
                    "instance_id": "exec-inst-test",
                    "port": "service.execution_parent",
                    "scope_ref": "exec-scope-test",
                    "operations": ["execution.parent.query", "execution.parent.submit"],
                },
                identity=strat_identity,
                scope=strat_scope,
                schema_id="sdk.service.control",
            ),
            timeout=5.0,
        ).payload
        assert reg_reply["ok"] is True, reg_reply
        reg_id = reg_reply["result"]["registration_id"]
        svc_epoch = reg_reply["result"]["service_epoch"]

        expires_at_ns = time.time_ns() + 600_000_000_000
        plan = {
            "schema_version": 1,
            "plan_version": "1",
            "build_id": "build-local-1",
            "schedule": {
                "class": "PASS",
                "version": 1,
                "binding": {
                    "deadline_ns": expires_at_ns,
                    "quantity_step": "1",
                    "parent_limit": "3100",
                    "price_step": "1",
                    "retry_limit": 0,
                },
                "params": {},
            },
        }
        payload = {
            "request_id": "req-e2e-001",
            "order_type": "limit",
            "spec": {
                "account_scope": ACCOUNT,
                "strategy_scope": STRATEGY,
                "execution_domain_ref": DOMAIN,
                "parent_ref": "parent-e2e-001",
                "registration_operation_id": "reg-op-e2e-001",
                "risk_operation_id": "risk-op-e2e-001",
                "risk_selected": True,
                "instrument": INSTRUMENT.to_dict(),
                "side": "buy",
                "offset": "open",
                "quantity": "1",
                "quantity_unit": "lots",
                "limit_price": "3100",
                "time_in_force": "DAY",
                "creation_trading_day": "20300101",
                "allowed_trading_days": ["20300101"],
                "expires_at_ns": expires_at_ns,
                "grant_revision": "grant-v1",
                "target_position_ref": None,
                "plan_version": "1",
                "plan_digest": algorithm_plan_digest(plan),
            },
            "algorithm_plan": plan,
        }
        sub_reply = strat_client.request(
            _request(
                "execution.parent.submit",
                {
                    "registration_id": reg_id,
                    "service_epoch": svc_epoch,
                    "instance_id": "exec-inst-test",
                    "arguments": payload,
                },
                identity=strat_identity,
                scope=strat_scope,
                schema_id="sdk.service.call",
            ),
            timeout=5.0,
        ).payload
        assert sub_reply["ok"] is True, sub_reply
        assert sub_reply["result"]["status"] == "RECEIVED", sub_reply

        # Wait for ExecutionParentDispatcher -> Risk v2 -> Gateway delegate reserve & dispatch!
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not gw_port.dispatches:
            time.sleep(0.05)

        assert len(gw_port.reserves) == 1, gw_port.reserves
        assert len(gw_port.dispatches) == 1, gw_port.dispatches
        assert gw_port.reserves[0]["parent_ref"] == "parent-e2e-001"
        assert gw_port.reserves[0]["child_id"] == "parent-e2e-001:c0"
        assert gw_port.dispatches[0]["request_key"] == gw_port.reserves[0]["request_key"]
    finally:
        strat_client.close()
        exec_stop.set()
        exec_thread.join(timeout=10.0)
        risk_stop.set()
        risk_thread.join(timeout=10.0)
