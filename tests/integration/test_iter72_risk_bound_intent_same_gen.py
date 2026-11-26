"""Real CURVE loopback INTEGRATION_SIM for Risk same-generation bound control intents (Iter 72 GAP 1).

Tests real CURVE ServiceHost (GatewayDirectServiceBinding + GatewayControlIntentRoute +
GatewayControlIntentHistory + Registry ServiceDispatchAdmission) <-> ThreadedServiceClientRuntime
+ GatewayControlAdapter(control_intent_enabled=True) + real SQLite PretradeEngine
(schema v4 + R-05 v2 + risk_bound_intents).

No provider, SimNow, or automatic cross-generation retry is enabled.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import traceback
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.control_intent_wire import CONTROL_INTENT_APPLY_OPERATION
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
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
from bt_api_risk.pretrade.account_bound_intents import request_digest_for_control
from bt_api_risk.risk_order_journal import RiskOrderJournal
from test_account_order_observations import EPOCH
from test_pretrade_support import FakeFacts, FakeGateway, base_policy
from test_r05_order_cut_coverage import _build_cut, _fact_page

from bt_api_py import risk_service_host as host_module
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayControlIntentRoute,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.risk_service_config import (
    RiskAccountFactsObservation,
    RiskServiceConfigError,
    ensure_private_directory,
    load_risk_service_config,
    parse_risk_service_config,
)
from bt_api_py.risk_service_gateway import (
    CONTROL_INTENT_OPERATIONS,
    GatewayControlAdapter,
)
from bt_api_py.risk_service_host import RiskServiceHost, RiskServiceStartError
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
DAY = "20261126"
OPERATOR = CallerIdentity(
    principal_ref="op-admin",
    actor_ref="op-actor",
    role="operator",
    strategy_refs=frozenset(),
    run_id=None,
    operations=frozenset({"risk.policy.publish", "risk.cancel.request"}),
    cancel_account_allowed=True,
)


@pytest.fixture(autouse=True)
def _clean_probe_state():
    def reset():
        host_module._RETAINED_HOSTS.clear()
        host_module._PROBE_STATE["active"] = False
        host_module._PROBE_STATE["uncertain"].clear()

    reset()
    yield
    reset()


def _start_curve_gateway_with_control_intents(
    root: Path,
    *,
    server_public: bytes,
    server_secret: bytes,
    client_public: bytes,
    db_path: Path,
    guard_dir: Path,
    expected_journal_id: str | None = None,
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
                CONTROL_INTENT_OPERATIONS,
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
        try:
            state = {}

            def factory():
                guard = LocalAccountSessionGuard(guard_dir, ACCOUNT, "owner")
                generation = guard.acquire()
                if expected_journal_id is not None:
                    from bt_api_gateway.direct_control import GatewayDirectControl

                    journal = GatewayDirectCommandJournal(
                        db_path,
                        ACCOUNT,
                        expected_journal_id=expected_journal_id,
                        minimum_revision=0,
                        generation=generation,
                        controls_enabled=True,
                        control_intent_history_enabled=True,
                    )
                    control = GatewayDirectControl(journal, guard)
                else:
                    journal = GatewayDirectCommandJournal.create(
                        db_path, ACCOUNT, generation=generation
                    )
                    control = journal.enable_controls(guard)
                    journal.enable_control_intent_history(guard)
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
                    generation=generation,
                )
                return GatewayDirectOwnerResources(
                    dispatcher, journal, guard.release, cancel_dispatcher=cancel
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
                        CONTROL_INTENT_OPERATIONS - {CONTROL_INTENT_APPLY_OPERATION},
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
            ready.put(
                (
                    "ok",
                    endpoint,
                    journal.journal_id,
                    state["generation"],
                    fingerprint,
                    owner,
                    state["control"],
                )
            )
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
    _, endpoint, journal_id, generation, fingerprint, owner, control_store = result

    def close():
        stop.set()
        thread.join(timeout=10)
        assert not thread.is_alive()

    return endpoint, journal_id, generation, fingerprint, owner, control_store, close


def _build_control_client_runtime(
    endpoint: str,
    *,
    server_public: bytes,
    client_public: bytes,
    client_secret: bytes,
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
    curve = ClientCurveKeys(server_public, client_public, client_secret)
    factory = ZmqTransportFactory({"curve-gw": curve})
    runtime = ThreadedServiceClientRuntime(
        lambda: ServiceClientRuntime(
            client_cfg,
            identity=RISK_CTRL_IDENTITY,
            scope_resolver=lambda _ref: ACCOUNT_SCOPE,
            peer_resolver=lambda _service: SERVICE,
            transport_factory=factory,
            requested_operations={"application.direct_write": CONTROL_INTENT_OPERATIONS},
            require_unregister_ack=True,
        ),
        require_unregister_ack=True,
    )
    runtime.start(timeout_seconds=5.0)
    return runtime


def _seed_r05_cut_for_gateway(
    ledger: PretradeLedger,
    *,
    journal_id: str,
    owner_generation: str,
    fingerprint: str,
    now_ns: int,
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
    # Match exact Gateway owner_generation and recompute cut_ref.
    header = dict(current["header"], owner_generation=owner_generation)
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
    # Also match gateway_account to ACCOUNT.
    fact_page = _fact_page(after=0, events=(), journal_id=journal_id, epoch=EPOCH)
    fact_page["gateway_account"] = ACCOUNT
    with ledger.transaction() as txn:
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


def test_real_curve_gateway_and_risk_engine_same_gen_bound_control_intent_and_cross_gen_refusal(
    tmp_path,
) -> None:
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    gw_db = tmp_path / "gw" / "gateway.sqlite"
    gw_db.parent.mkdir(parents=True)
    guard_dir = tmp_path / "gw" / "guard"

    endpoint1, journal_id, gen1, fingerprint, owner1, control1, close_gw1 = (
        _start_curve_gateway_with_control_intents(
            tmp_path / "gw1",
            server_public=server_public,
            server_secret=server_secret,
            client_public=client_public,
            db_path=gw_db,
            guard_dir=guard_dir,
        )
    )
    runtime1 = _build_control_client_runtime(
        endpoint1,
        server_public=server_public,
        client_public=client_public,
        client_secret=client_secret,
    )
    now_ns = 10_000_000_000
    risk_db = tmp_path / "risk" / "risk.sqlite"
    risk_db.parent.mkdir(parents=True)
    scope = AccountScope("ctp", ACCOUNT, "simnow")
    risk_journal = RiskOrderJournal(risk_db, writer_id="iter72-sim")
    try:
        with risk_journal.writer_fence(scope) as fence:
            ledger = PretradeLedger(risk_journal, fence, schema_version=4)
            ledger.enable_r05_observations_v2()
            _seed_r05_cut_for_gateway(
                ledger,
                journal_id=journal_id,
                owner_generation=gen1,
                fingerprint=fingerprint,
                now_ns=now_ns,
            )
            ledger._v2_clock_ns = lambda: now_ns
            ledger.enable_bound_intents()

            route = BoundCallRoute(
                RISK_CTRL_IDENTITY,
                ACCOUNT_SCOPE,
                "scope-a",
                "gateway-service",
                "gateway-a",
                SERVICE,
            )
            adapter1 = GatewayControlAdapter(
                runtime1,
                mount="application.direct_write",
                service_id="gateway-service",
                instance_id="gateway-a",
                scope_ref="scope-a",
                account_ref=ACCOUNT,
                timeout_seconds=5.0,
                route=route,
                server_key_fingerprint=fingerprint,
                control_intent_enabled=True,
            )
            facts = FakeFacts(lambda: now_ns)
            facts.day = DAY
            facts.set_account(
                close_profit="-250",
                position_profit="0",
                equity="9750",
                available="9750",
                day=DAY,
            )
            engine = PretradeEngine(
                ledger,
                PolicyStore(ledger),
                RuleRegistry(),
                facts,
                FakeGateway(),
                adapter1,
                account_scope=ACCOUNT,
                route="CTP___FUTURE",
                now_ns=lambda: now_ns,
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

            # Same-generation breach over real CURVE: capture -> verify R-05 -> bind -> apply_intent -> APPLIED!
            result = engine.maintain()
            assert result["intent_retry_unavailable"] == 1
            with ledger.transaction() as txn:
                intents = txn.control_intents()
                assert len(intents) == 1
                row = intents[0]
                cause_id = row["cause_id"]
                assert row["state"] == "APPLIED"
                assert row["attempts"] == 1
                assert row["gw_version"] == 1
                expected_digest = request_digest_for_control(
                    cause_id=cause_id,
                    mode=row["mode"],
                    scope_kind=row["scope_kind"],
                    scope_value=row["scope_value"],
                    latch_id=row["latch_id"],
                )
                persisted_binding = txn.bound_intent_binding(
                    intent_kind="control",
                    intent_id=cause_id,
                    origin_principal="risk-actor",
                    request_digest=expected_digest,
                )
                assert persisted_binding is not None
                assert persisted_binding.journal_id == journal_id
                assert persisted_binding.owner_generation == gen1
                assert CONTROL_INTENT_APPLY_OPERATION in persisted_binding.operations

            # Verify Gateway actually activated the control cause in generation 1.
            cause = owner1.submit_service_call(
                lambda: control1.get(cause_id)
            ).result(timeout_seconds=5)
            assert cause is not None and bool(cause["active"]) is True and cause["version"] == 1

            # Now stop Gateway gen1 and start Gateway gen2 on the same journal DB -> new owner_generation!
            runtime1.close(timeout_seconds=5.0)
            runtime1 = None
            close_gw1()
            close_gw1 = None

            endpoint2, journal_id2, gen2, fingerprint2, _owner2, control2, close_gw2 = (
                _start_curve_gateway_with_control_intents(
                    tmp_path / "gw2",
                    server_public=server_public,
                    server_secret=server_secret,
                    client_public=client_public,
                    db_path=gw_db,
                    guard_dir=guard_dir,
                    expected_journal_id=journal_id,
                )
            )
            runtime2 = _build_control_client_runtime(
                endpoint2,
                server_public=server_public,
                client_public=client_public,
                client_secret=client_secret,
            )
            try:
                assert journal_id2 == journal_id
                assert gen2 != gen1
                adapter2 = GatewayControlAdapter(
                    runtime2,
                    mount="application.direct_write",
                    service_id="gateway-service",
                    instance_id="gateway-a",
                    scope_ref="scope-a",
                    account_ref=ACCOUNT,
                    timeout_seconds=5.0,
                    route=route,
                    server_key_fingerprint=fingerprint2,
                    control_intent_enabled=True,
                )
                # Attempting to replay gen1's binding against gen2 fails closed before transport!
                cross_gen_reply = adapter2.apply_intent(
                    binding=persisted_binding,
                    request_digest=expected_digest,
                    cause_id=cause_id,
                    mode="STOP_NEW",
                    scope_kind="account",
                    scope_value="",
                    latch_id=row["latch_id"],
                )
                assert cross_gen_reply.outcome == "TRANSPORT_NOT_STARTED"
                assert cross_gen_reply.code == "CONTROL_INTENT_BINDING_MISMATCH"
                assert engine.status_summary()["intent_retry_unavailable"] == 1
            finally:
                runtime2.close(timeout_seconds=5.0)
                close_gw2()
    finally:
        if runtime1 is not None:
            runtime1.close(timeout_seconds=5.0)
        if close_gw1 is not None:
            close_gw1()


def test_risk_service_config_and_host_bound_control_intents_gate(tmp_path) -> None:
    setup_risk_files(tmp_path)
    # Rejected unless pretrade v4 + account_facts_observation.order_cut_enabled=True
    bad = document()
    bad["bound_control_intents_enabled"] = True
    with pytest.raises(RiskServiceConfigError, match="BOUND_CONTROL_INTENTS_INVALID"):
        parse_risk_service_config(bad, path=tmp_path / "risk.yaml")

    bad_no_cut = document()
    bad_no_cut["pretrade"] = {"schema_version": 4}
    bad_no_cut["account_facts_observation"] = {
        "poll_interval_seconds": 1,
        "timeout_seconds": "1.0",
        "page_size": 8,
    }
    bad_no_cut["bound_control_intents_enabled"] = True
    with pytest.raises(RiskServiceConfigError, match="BOUND_CONTROL_INTENTS_INVALID"):
        parse_risk_service_config(bad_no_cut, path=tmp_path / "risk.yaml")

    good = document()
    good["pretrade"] = {"schema_version": 4}
    good["account_facts_observation"] = {
        "poll_interval_seconds": 1,
        "timeout_seconds": "1.0",
        "page_size": 8,
        "order_cut_enabled": True,
        "order_cut_max_age_seconds": 30,
    }
    good["bound_control_intents_enabled"] = True
    cfg = parse_risk_service_config(good, path=tmp_path / "risk.yaml")
    assert cfg.bound_control_intents_enabled is True

    # Host refuses DB missing risk_bound_intents without silently installing/migrating.
    config = replace(
        load_risk_service_config(tmp_path / "risk_service.json"),
        pretrade_schema_version=4,
        account_facts_observation=RiskAccountFactsObservation(
            1.0, 0.5, 8, order_cut_enabled=True, order_cut_max_age_seconds=30.0
        ),
        bound_control_intents_enabled=True,
    )
    ensure_private_directory(config.storage_directory)
    journal = RiskOrderJournal(config.risk_database, writer_id="seed")
    scope = AccountScope(config.provider, config.account_scope, config.environment)
    with journal.writer_fence(scope) as fence:
        ledger = PretradeLedger(journal, fence, schema_version=4)
        ledger.enable_r05_observations_v2()

    host_missing = RiskServiceHost(config)
    with pytest.raises(RiskServiceStartError, match="RISK_SERVICE_BOUND_INTENTS_REQUIRED"):
        host_missing.open_storage()
    assert host_missing.close() is True

    # Once explicitly installed, RiskServiceHost attaches cleanly and enables bound intents.
    with journal.writer_fence(scope) as fence:
        ledger = PretradeLedger(journal, fence, schema_version=4)
        ledger.attach_existing_r05_observations_v2()
        ledger.enable_bound_intents()

    host_ok = RiskServiceHost(config)
    host_ok.open_storage()
    try:
        assert host_ok.ledger._bound_intents_enabled is True
    finally:
        assert host_ok.close() is True
