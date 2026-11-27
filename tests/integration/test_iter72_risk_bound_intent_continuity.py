"""Real CURVE loopback INTEGRATION_SIM for cross-generation bound control-intent continuity.

Evidence level: INTEGRATION_SIM (real CURVE ZMQ ServiceHost + GatewayDirectServiceBinding
+ GatewayControlIntentContinuity + GatewayDirectCommandJournal SQLite + Registry
ServiceDispatchAdmission <-> ThreadedServiceClientRuntime + GatewayControlAdapter
+ real SQLite PretradeEngine v4 + R-05 v2 + risk_bound_intents).

Verifies:
1. Cleanly sealed G1 -> G2 (NOT_APPLIED_SEALED): claims & applies once over real CURVE;
2. Cleanly sealed G1 -> G2 (ALREADY_CONSUMED): reconciles to APPLIED without re-send;
3. Unsealed crash G1 -> G2 (UNSEALED_CRASH): fails closed with CONTROL_INTENT_CONTINUITY_GAP;
4. Strict config gating for both Gateway options and RiskServiceConfig.
"""

from __future__ import annotations

import hashlib
import json
import queue
import sqlite3
import threading
import traceback
from pathlib import Path

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.control_intent_continuity import GatewayControlIntentContinuity
from bt_api_gateway.control_intent_continuity_wire import (
    CONTROL_INTENT_CONTINUITY_OPERATIONS as GW_CONTINUITY_OPS,
)
from bt_api_gateway.control_intent_wire import CONTROL_INTENT_APPLY_OPERATION
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_control import GatewayDirectControl
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade import (
    CallerIdentity,
    PolicyStore,
    PretradeEngine,
    PretradeLedger,
    RuleRegistry,
)
from bt_api_risk.risk_order_journal import RiskOrderJournal
from test_account_order_observations import EPOCH
from test_pretrade_support import FakeFacts, FakeGateway, base_policy
from test_r05_order_cut_coverage import _build_cut, _fact_page

from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayControlIntentRoute,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
    validate_service_options,
)
from bt_api_py.risk_service_config import RiskServiceConfigError, parse_risk_service_config
from bt_api_py.risk_service_gateway import (
    CONTROL_INTENT_CONTINUITY_OPERATIONS,
    GatewayControlAdapter,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import BoundCallRoute, ThreadedServiceClientRuntime
from tests.integration.test_iter63_delegate_risk_execution_loopback import _read_service
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, DOMAIN, SERVICE
from tests.integration.test_iter63_gateway_dual_ports import _dual_config
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter64_risk_service_config import document
from tests.unit.test_iter64_risk_service_host import setup as setup_risk_files

RISK_CTRL_IDENTITY = WireIdentity(
    WireRole.APPLICATION_CLIENT, "risk-control-principal", "risk-control-actor"
)
ACCOUNT_SCOPE = WireScope(DOMAIN, account_ref=ACCOUNT)
DAY = "20261127"
OPERATOR = CallerIdentity(
    principal_ref="op-admin",
    actor_ref="op-actor",
    role="operator",
    strategy_refs=frozenset(),
    run_id=None,
    operations=frozenset({"risk.policy.publish", "risk.cancel.request"}),
    cancel_account_allowed=True,
)


def _start_curve_gateway(
    root: Path,
    *,
    server_public: bytes,
    server_secret: bytes,
    client_public: bytes,
    db_path: Path,
    guard_dir: Path,
    expected_journal_id: str | None = None,
    seal_on_close: bool = True,
):
    endpoint = _free_endpoint()
    fingerprint = hashlib.sha256(server_public).hexdigest()
    grants = PrincipalGrant(
        RISK_CTRL_IDENTITY,
        (
            ResourceGrant(
                "gateway-a",
                "service.direct_write",
                "scope-a",
                ACCOUNT_SCOPE,
                CONTROL_INTENT_CONTINUITY_OPERATIONS,
            ),
        ),
    )
    authority = TrustedServiceAuthority(
        identity=SERVICE,
        scope=WireScope(DOMAIN),
        scope_ref="scope-a",
        scope_version="scope-v1",
        security_ref="security-a",
        security_version="security-v1",
        registration_policy_ref="direct-policy-v1",
        registration_policy_version="direct-policy-v1",
        service_requirements_ref="direct-requirements-v1",
        service_requirements_version="direct-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: RISK_CTRL_IDENTITY.principal_ref},
        grants={RISK_CTRL_IDENTITY.principal_ref: (grants,)},
    )
    ready, stop = queue.Queue(), threading.Event()
    guard_dir.mkdir(parents=True, exist_ok=True)
    registry_path = root / f"registry-{id(ready)}.sqlite"

    def serve():
        owner = host = None
        state = {}
        try:
            def factory():
                guard = LocalAccountSessionGuard(guard_dir, ACCOUNT, "owner")
                generation = guard.acquire()
                if expected_journal_id is not None:
                    journal = GatewayDirectCommandJournal(
                        db_path,
                        ACCOUNT,
                        expected_journal_id=expected_journal_id,
                        minimum_revision=0,
                        generation=generation,
                        controls_enabled=True,
                        control_intent_history_enabled=True,
                        control_intent_continuity_enabled=True,
                    )
                    control = GatewayDirectControl(journal, guard)
                else:
                    journal = GatewayDirectCommandJournal.create(
                        db_path, ACCOUNT, generation=generation
                    )
                    control = journal.enable_controls(guard)
                    journal.enable_control_intent_history(guard)
                    journal.enable_control_intent_continuity(guard)
                continuity = GatewayControlIntentContinuity(journal, guard)
                continuity.open_generation()
                dispatcher = GatewayDirectDispatcher(
                    journal, "CTP", guard, lambda *_: None, lambda _: (), lambda _: True
                )
                cancel = GatewayDirectCancelDispatcher(
                    GatewayDirectCancelJournal(journal),
                    dispatcher,
                    guard,
                    "CTP",
                    lambda *_: None,
                    lambda _: True,
                )
                state.update(
                    journal=journal,
                    control=control,
                    continuity=continuity,
                    generation=generation,
                )

                def cleanup():
                    if seal_on_close:
                        continuity.seal_generation()
                    guard.release()

                return GatewayDirectOwnerResources(
                    dispatcher, journal, cleanup, cancel_dispatcher=cancel
                )

            owner = GatewayDirectOwner(
                factory, queue_size=8, max_queue_wait_seconds=5, busy_timeout_seconds=5
            )
            assert owner.start(10)
            journal = state["journal"]
            route = GatewayControlIntentRoute(
                "application.direct_write",
                "gateway-service",
                "gateway-a",
                "scope-a",
                SERVICE,
                fingerprint,
            )
            read_service = _read_service(root, {CLIENT_A.principal_ref})
            legacy_ops = CONTROL_INTENT_CONTINUITY_OPERATIONS - (
                {CONTROL_INTENT_APPLY_OPERATION} | GW_CONTINUITY_OPS
            )
            binding = GatewayDirectServiceBinding(
                journal,
                {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({"rb"}))},
                owner=owner,
                cancel_enabled=True,
                control=state["control"],
                control_grants={
                    RISK_CTRL_IDENTITY.principal_ref: GatewayControlGrant(
                        RISK_CTRL_IDENTITY.actor_ref,
                        True,
                        frozenset(),
                        legacy_ops,
                        kind="risk",
                        modes=frozenset({"STOP_NEW", "FREEZE"}),
                    )
                },
                control_intent_route=route,
            )
            config = _dual_config(endpoint).to_public_dict()
            config["components"]["gateway"]["settings"]["options"].update(
                dispatch_enabled=True,
                cancel_enabled=True,
                control_enabled=True,
                control_intent_enabled=True,
                control_intent_continuity_enabled=True,
            )
            host = ServiceHost(
                parse_service_config(config),
                authority=authority,
                registry_path=registry_path,
                resource_provider=lambda *_: ComponentResources.owned(
                    {"binding": binding, "read_service": read_service},
                    ready=lambda: True,
                    close=lambda: None,
                ),
            )
            host.start()
            ready.put(("ok", endpoint, journal.journal_id, state["generation"], fingerprint))
            while not stop.is_set():
                host.serve_burst(20, max_requests=16)
        except Exception:
            ready.put(("error", traceback.format_exc()))
        finally:
            if host is not None:
                host.close()
            if owner is not None:
                owner.close(5)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    result = ready.get(timeout=15)
    assert result[0] == "ok", result[1]
    _, endpoint, journal_id, generation, fingerprint = result

    def close():
        stop.set()
        thread.join(timeout=10)
        assert not thread.is_alive()

    return endpoint, journal_id, generation, fingerprint, close


def _connect_risk_control(
    endpoint: str,
    *,
    server_public: bytes,
    client_public: bytes,
    client_secret: bytes,
    fingerprint: str,
):
    client_cfg = parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": RISK_CTRL_IDENTITY.actor_ref},
            "clients": {
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "application.direct_write",
                                "service_id": "gateway-service",
                                "instance_id": "gateway-a",
                                "scope_ref": "scope-a",
                                "account_ref": ACCOUNT,
                                "transport_ref": "gateway",
                            }
                        ],
                        "options": {"request_timeout_ms": 5000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "gateway": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": "curve-gw",
                        "options": {"request_timeout_ms": 5000},
                    }
                },
            },
        }
    )
    keys = ClientCurveKeys(server_public, client_public, client_secret)
    runtime = ThreadedServiceClientRuntime(
        lambda: ServiceClientRuntime(
            client_cfg,
            identity=RISK_CTRL_IDENTITY,
            scope_resolver=lambda _ref: ACCOUNT_SCOPE,
            peer_resolver=lambda _service: SERVICE,
            transport_factory=ZmqTransportFactory({"curve-gw": keys}),
            requested_operations={"application.direct_write": CONTROL_INTENT_CONTINUITY_OPERATIONS},
            require_unregister_ack=True,
        ),
        require_unregister_ack=True,
    )
    runtime.start(timeout_seconds=10)
    route = BoundCallRoute(
        RISK_CTRL_IDENTITY,
        ACCOUNT_SCOPE,
        "scope-a",
        "gateway-service",
        "gateway-a",
        SERVICE,
    )
    adapter = GatewayControlAdapter(
        runtime,
        mount="application.direct_write",
        service_id="gateway-service",
        instance_id="gateway-a",
        scope_ref="scope-a",
        account_ref=ACCOUNT,
        timeout_seconds=5.0,
        route=route,
        server_key_fingerprint=fingerprint,
        control_intent_enabled=True,
        control_intent_continuity_enabled=True,
    )
    return runtime, adapter


def _seed_r05_cut(
    ledger: PretradeLedger,
    *,
    journal_id: str,
    generation: str,
    fingerprint: str,
    now_ns: int,
    seed_fact_page: bool,
) -> None:
    source = {
        "service_id": "gateway-service",
        "instance_id": "gateway-a",
        "peer_role": "service",
        "peer_principal": SERVICE.principal_ref,
        "peer_actor": SERVICE.actor_ref,
        "peer_delegate": None,
        "fingerprint": fingerprint,
    }
    current, q_pages, r_pages = _build_cut(
        fact_watermark=0,
        base_sequence=0,
        begin_sequence=0,
        end_sequence=0,
        source_prefix=0,
        trading_day=DAY,
        journal_id=journal_id,
        epoch=EPOCH,
    )
    header = dict(current["header"], owner_generation=generation)
    cut_ref = hashlib.sha256(
        json.dumps(
            ["sdk.gateway.order_cut.ref/1", header, current["query_manifest_digest"]],
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    current = {**current, "header": header, "cut_ref": cut_ref}
    q_pages = [{**p, "header": header, "cut_ref": cut_ref} for p in q_pages]
    r_pages = [{**p, "header": header, "cut_ref": cut_ref} for p in r_pages]
    fact_page = _fact_page(after=0, events=(), journal_id=journal_id, epoch=EPOCH)
    fact_page["gateway_account"] = ACCOUNT
    with ledger.transaction() as txn:
        if seed_fact_page:
            txn.apply_r05_bound_fact_page(source_binding=source, **fact_page)
        txn.apply_r05_order_cut_current(
            source_binding=source,
            gateway_account=ACCOUNT,
            observed_at_ns=now_ns,
            max_age_ns=60_000_000_000,
            page=current,
        )
        for qp in q_pages:
            txn.apply_r05_order_cut_query_page(
                source_binding=source, gateway_account=ACCOUNT, page=qp
            )
        for rp in r_pages:
            txn.apply_r05_order_cut_receipt_page(
                source_binding=source, gateway_account=ACCOUNT, page=rp
            )


def _setup_risk_engine(tmp_path: Path, *, journal_id: str, gen1: str, fingerprint: str, adapter, clock):
    risk_journal = RiskOrderJournal(tmp_path / "risk.sqlite", writer_id="iter72-test")
    scope = AccountScope("ctp", ACCOUNT, "simnow")
    fence_cm = risk_journal.writer_fence(scope)
    fence = fence_cm.__enter__()
    ledger = PretradeLedger(risk_journal, fence, schema_version=4)
    ledger.enable_r05_observations_v2()
    _seed_r05_cut(
        ledger,
        journal_id=journal_id,
        generation=gen1,
        fingerprint=fingerprint,
        now_ns=clock[0],
        seed_fact_page=True,
    )
    ledger._v2_clock_ns = lambda: clock[0]
    ledger.enable_bound_intents()
    facts = FakeFacts(lambda: clock[0])
    facts.day = DAY
    facts.set_account(close_profit="-250", position_profit="0", equity="9750", available="9750", day=DAY)
    engine = PretradeEngine(
        ledger,
        PolicyStore(ledger),
        RuleRegistry(),
        facts,
        FakeGateway(),
        adapter,
        account_scope=ACCOUNT,
        route="CTP___FUTURE",
        now_ns=lambda: clock[0],
        reservation_ttl_seconds=30.0,
        actor_principal="risk-actor",
        dispatch_recheck_seconds=5.0,
        dispatch_recheck_limit=10,
    )
    doc = base_policy()
    doc["account"] = {"route": "CTP___FUTURE", "account_scope": ACCOUNT}
    doc["loss"] = {"account_day": {"block": "100"}}
    doc["breach_actions"] = [
        {"rule": "loss.account_day", "scope": "account", "request": "stop_new"}
    ]
    engine.publish_policy(OPERATOR, doc, 0, "seed")
    return risk_journal, fence_cm, ledger, engine


class _DropFirstSendAdapter:
    """Forwards capture_intent_binding to real G1 adapter, simulates dropped send before transport."""

    def __init__(self, inner: GatewayControlAdapter, *, drop_apply_intent: bool = True) -> None:
        self.inner = inner
        self.drop_apply_intent = drop_apply_intent
        self.sent_before_drop = False

    def capture_intent_binding(self):
        return self.inner.capture_intent_binding()

    def apply_intent(self, **kwargs):
        from bt_api_risk.pretrade import ControlReply

        if self.drop_apply_intent:
            return ControlReply("TRANSPORT_NOT_STARTED", code="SIMULATED_LINK_DROP")
        # Actually deliver to G1, then simulate lost reply so Risk records UNKNOWN.
        reply = self.inner.apply_intent(**kwargs)
        assert reply.outcome == "OBSERVED" and reply.active is True
        self.sent_before_drop = True
        return ControlReply("UNKNOWN", code="SIMULATED_REPLY_LOST")

    def claim_control_intent_continuity(self, **kwargs):
        return self.inner.claim_control_intent_continuity(**kwargs)

    def apply_claimed_control_intent(self, **kwargs):
        return self.inner.apply_claimed_control_intent(**kwargs)

    def apply(self, **kwargs):
        return self.inner.apply(**kwargs)

    def status(self, **kwargs):
        return self.inner.status(**kwargs)

    def cancel_scope(self, **kwargs):
        return self.inner.cancel_scope(**kwargs)


def test_sealed_cross_gen_not_applied_claims_and_applies_once_over_curve(tmp_path: Path) -> None:
    server_pub, server_sec = zmq.curve_keypair()
    client_pub, client_sec = zmq.curve_keypair()
    db_path = tmp_path / "gw-journal.sqlite"
    guard_dir = tmp_path / "guard"
    clock = [10_000_000_000]

    ep1, journal_id, gen1, fp, close_gw1 = _start_curve_gateway(
        tmp_path / "gw1",
        server_public=server_pub,
        server_secret=server_sec,
        client_public=client_pub,
        db_path=db_path,
        guard_dir=guard_dir,
        seal_on_close=True,
    )
    rt1, ad1 = _connect_risk_control(
        ep1, server_public=server_pub, client_public=client_pub, client_secret=client_sec, fingerprint=fp
    )
    wrapper = _DropFirstSendAdapter(ad1, drop_apply_intent=True)
    risk_journal, fence_cm, ledger, engine = _setup_risk_engine(
        tmp_path, journal_id=journal_id, gen1=gen1, fingerprint=fp, adapter=wrapper, clock=clock
    )
    try:
        engine.maintain()
        with ledger.transaction() as txn:
            intent = txn.control_intents()[0]
            assert intent["state"] == "PENDING"
            cause_id = intent["cause_id"]

        # Cleanly close G1 (seals generation chain) and disconnect rt1.
        rt1.close(timeout_seconds=5)
        close_gw1()

        # Start G2 on the exact same SQLite journal over real CURVE.
        ep2, jid2, gen2, fp2, close_gw2 = _start_curve_gateway(
            tmp_path / "gw2",
            server_public=server_pub,
            server_secret=server_sec,
            client_public=client_pub,
            db_path=db_path,
            guard_dir=guard_dir,
            expected_journal_id=journal_id,
            seal_on_close=True,
        )
        assert jid2 == journal_id and gen2 != gen1 and fp2 == fp
        rt2, ad2 = _connect_risk_control(
            ep2, server_public=server_pub, client_public=client_pub, client_secret=client_sec, fingerprint=fp
        )
        try:
            wrapper.inner = ad2
            clock[0] += 5_000
            _seed_r05_cut(
                ledger,
                journal_id=journal_id,
                generation=gen2,
                fingerprint=fp,
                now_ns=clock[0],
                seed_fact_page=False,
            )

            res = engine.retry_bound_control_intent(cause_id)
            assert res["outcome"] == "APPLIED"
            assert res["proof_status"] == "NOT_APPLIED_SEALED"
            assert res["resent"] is True

            with ledger.transaction() as txn:
                row = txn.control_intent(cause_id)
                assert row["state"] == "APPLIED"
                assert row["attempts"] == 2
            assert engine.retry_bound_control_intent(cause_id)["outcome"] == "NOOP"
            assert engine.status_summary()["intent_retry_unavailable"] == 1
        finally:
            rt2.close(timeout_seconds=5)
            close_gw2()

        # Inspect durable SQLite proof rows on Gateway journal.
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            cause = conn.execute(
                "SELECT cause_id, active, generation FROM direct_control_causes WHERE cause_id=?",
                (cause_id,),
            ).fetchone()
            assert cause is not None and cause["active"] == 1 and cause["generation"] == gen2
            hist = conn.execute(
                "SELECT state, original_generation, consumed_generation "
                "FROM direct_control_intent_history WHERE intent_id=?",
                (cause_id,),
            ).fetchone()
            assert hist["state"] == "CONSUMED"
            assert hist["original_generation"] == gen1
            assert hist["consumed_generation"] == gen2
            claim = conn.execute(
                "SELECT proof_status, state, original_generation, claim_generation "
                "FROM direct_control_intent_continuity_claims WHERE intent_id=?",
                (cause_id,),
            ).fetchone()
            assert claim["proof_status"] == "NOT_APPLIED_SEALED"
            assert claim["state"] == "CONSUMED"
            assert claim["original_generation"] == gen1
            assert claim["claim_generation"] == gen2
        finally:
            conn.close()
    finally:
        fence_cm.__exit__(None, None, None)


def test_sealed_cross_gen_already_consumed_reconciles_without_resend_over_curve(tmp_path: Path) -> None:
    server_pub, server_sec = zmq.curve_keypair()
    client_pub, client_sec = zmq.curve_keypair()
    db_path = tmp_path / "gw-journal-ac.sqlite"
    guard_dir = tmp_path / "guard-ac"
    clock = [20_000_000_000]

    ep1, journal_id, gen1, fp, close_gw1 = _start_curve_gateway(
        tmp_path / "gw1",
        server_public=server_pub,
        server_secret=server_sec,
        client_public=client_pub,
        db_path=db_path,
        guard_dir=guard_dir,
        seal_on_close=True,
    )
    rt1, ad1 = _connect_risk_control(
        ep1, server_public=server_pub, client_public=client_pub, client_secret=client_sec, fingerprint=fp
    )
    wrapper = _DropFirstSendAdapter(ad1, drop_apply_intent=False)
    risk_journal, fence_cm, ledger, engine = _setup_risk_engine(
        tmp_path, journal_id=journal_id, gen1=gen1, fingerprint=fp, adapter=wrapper, clock=clock
    )
    try:
        engine.maintain()
        assert wrapper.sent_before_drop is True
        with ledger.transaction() as txn:
            intent = txn.control_intents()[0]
            assert intent["state"] == "UNKNOWN"
            cause_id = intent["cause_id"]

        rt1.close(timeout_seconds=5)
        close_gw1()

        ep2, _, gen2, _, close_gw2 = _start_curve_gateway(
            tmp_path / "gw2",
            server_public=server_pub,
            server_secret=server_sec,
            client_public=client_pub,
            db_path=db_path,
            guard_dir=guard_dir,
            expected_journal_id=journal_id,
            seal_on_close=True,
        )
        rt2, ad2 = _connect_risk_control(
            ep2, server_public=server_pub, client_public=client_pub, client_secret=client_sec, fingerprint=fp
        )
        try:
            wrapper.inner = ad2
            clock[0] += 5_000
            _seed_r05_cut(
                ledger,
                journal_id=journal_id,
                generation=gen2,
                fingerprint=fp,
                now_ns=clock[0],
                seed_fact_page=False,
            )
            res = engine.retry_bound_control_intent(cause_id)
            assert res == {
                "outcome": "APPLIED",
                "state": "APPLIED",
                "proof_status": "ALREADY_CONSUMED",
                "resent": False,
            }
            with ledger.transaction() as txn:
                row = txn.control_intent(cause_id)
                assert row["state"] == "APPLIED"
                assert row["attempts"] == 1
        finally:
            rt2.close(timeout_seconds=5)
            close_gw2()
    finally:
        fence_cm.__exit__(None, None, None)


def test_unsealed_crash_cross_gen_fails_closed_over_curve(tmp_path: Path) -> None:
    server_pub, server_sec = zmq.curve_keypair()
    client_pub, client_sec = zmq.curve_keypair()
    db_path = tmp_path / "gw-journal-crash.sqlite"
    guard_dir = tmp_path / "guard-crash"
    clock = [30_000_000_000]

    ep1, journal_id, gen1, fp, close_gw1 = _start_curve_gateway(
        tmp_path / "gw1",
        server_public=server_pub,
        server_secret=server_sec,
        client_public=client_pub,
        db_path=db_path,
        guard_dir=guard_dir,
        seal_on_close=False,  # Simulated unsealed crash!
    )
    rt1, ad1 = _connect_risk_control(
        ep1, server_public=server_pub, client_public=client_pub, client_secret=client_sec, fingerprint=fp
    )
    wrapper = _DropFirstSendAdapter(ad1, drop_apply_intent=True)
    risk_journal, fence_cm, ledger, engine = _setup_risk_engine(
        tmp_path, journal_id=journal_id, gen1=gen1, fingerprint=fp, adapter=wrapper, clock=clock
    )
    try:
        engine.maintain()
        with ledger.transaction() as txn:
            intent = txn.control_intents()[0]
            assert intent["state"] == "PENDING"
            cause_id = intent["cause_id"]

        rt1.close(timeout_seconds=5)
        close_gw1()

        ep2, _, gen2, _, close_gw2 = _start_curve_gateway(
            tmp_path / "gw2",
            server_public=server_pub,
            server_secret=server_sec,
            client_public=client_pub,
            db_path=db_path,
            guard_dir=guard_dir,
            expected_journal_id=journal_id,
            seal_on_close=True,
        )
        rt2, ad2 = _connect_risk_control(
            ep2, server_public=server_pub, client_public=client_pub, client_secret=client_sec, fingerprint=fp
        )
        try:
            wrapper.inner = ad2
            clock[0] += 5_000
            _seed_r05_cut(
                ledger,
                journal_id=journal_id,
                generation=gen2,
                fingerprint=fp,
                now_ns=clock[0],
                seed_fact_page=False,
            )
            res = engine.retry_bound_control_intent(cause_id)
            assert res == {"outcome": "REQUEST_DENIED", "code": "CONTROL_INTENT_CONTINUITY_GAP"}
            with ledger.transaction() as txn:
                row = txn.control_intent(cause_id)
                assert row["state"] == "PENDING"
                assert row["attempts"] == 1
        finally:
            rt2.close(timeout_seconds=5)
            close_gw2()
    finally:
        fence_cm.__exit__(None, None, None)


def test_continuity_option_config_gating(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="INVALID_DIRECT_OPTIONS"):
        validate_service_options(
            {
                "role": "trading_owner",
                "dispatch_enabled": True,
                "control_enabled": True,
                "control_intent_enabled": False,
                "control_intent_continuity_enabled": True,
            },
            ("service.direct_write",),
        )
    valid = validate_service_options(
        {
            "role": "trading_owner",
            "dispatch_enabled": True,
            "control_enabled": True,
            "control_intent_enabled": True,
            "control_intent_continuity_enabled": True,
        },
        ("service.direct_write",),
    )
    assert valid["control_intent_continuity_enabled"] is True

    setup_risk_files(tmp_path)
    doc = document()
    doc["pretrade"] = {"schema_version": 4}
    doc["account_facts_observation"] = {
        "poll_interval_seconds": 1,
        "timeout_seconds": "1.0",
        "page_size": 100,
        "order_cut_enabled": True,
        "order_cut_max_age_seconds": 60,
    }
    doc["bound_control_intents_enabled"] = False
    doc["control_intent_continuity_enabled"] = True
    with pytest.raises(RiskServiceConfigError, match="RISK_SERVICE_CONFIG_BOUND_CONTROL_INTENTS_INVALID"):
        parse_risk_service_config(doc, path=tmp_path / "risk.yaml")

    doc["bound_control_intents_enabled"] = True
    cfg = parse_risk_service_config(doc, path=tmp_path / "risk.yaml")
    assert cfg.control_intent_continuity_enabled is True
