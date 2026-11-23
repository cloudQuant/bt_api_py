"""Track 2 SDK unit & 5-component integration-sim tests (T-R2-01 through T-R2-09).

All tests run completely offline using real local SQLite journals and real SDK
protocol objects; no network sockets or real exchange credentials are used.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.gateway_readiness_protocol import GatewayReadinessProofError
from bt_api_base.gateway_risk_first_reserve_v2 import (
    GatewayRiskFirstReserveRequestV2,
    risk_first_reserve_nonce_digest,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import RiskOrderResult, RiskOrderView
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command
from bt_api_gateway.readiness_proof import (
    STRICT_STRATEGY_DEFAULT_OPERATIONS,
    GatewayReadinessProofIssuer,
)
from bt_api_gateway.risk_v2_first_reserve_coordinator import GatewayRiskFirstReserveV2Coordinator
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_monitor.five_component_health import (
    ComponentObservation,
    evaluate_five_component_health,
)

from bt_api_py._execution_route_match import _OrderFacts, _Outcome
from bt_api_py.bt_api import BtApi
from bt_api_py.direct_api_assembly import DirectApiAssembly, DirectApiAssemblyError
from bt_api_py.execution_parent_service_host import (
    ParentConfig,
    ParentServiceError,
    ParentServiceHost,
)
from bt_api_py.execution_routing import (
    ExecutionOrderRouter,
    ExecutionRoutingError,
    ExecutionRoutingOutcome,
    install_execution_routing_tables,
    parse_execution_routing,
)
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_direct_client import GatewayCommandBinding
from bt_api_py.gateway_readiness_client import GatewayReadinessClient
from bt_api_py.gateway_strategy_config import (
    STRICT_FIVE_COMPONENT_PROFILE,
    GatewayStrategyConfigError,
    parse_gateway_strategy_config,
)
from bt_api_py.risk_parent_port import RiskParentPort
from bt_api_py.risk_service_client import RiskParentCallResult

NOW_NS = 1_700_000_000_000_000_000
SHARED_KEY = b"s" * 32
INSTRUMENT = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701")


# ---------------------------------------------------------------------------
# T-R2-01: strict_five_component GatewayStrategyConfig profile
# ---------------------------------------------------------------------------


def _minimal_strict_raw(**overrides):
    raw = {
        "schema_version": 1,
        "exchange_name": "CTP___FUTURE",
        "client_config": {},
        "binding": {},
        "route": {},
        "peer": {},
        "requested_operations": ["direct.delegated_cancel", "direct.command_status"],
        "execution_time_policy": {},
        "instruments_by_ref": {},
        "risk": {"client_config": {}},
        "execution": {"client_config": {}},
    }
    raw.update(overrides)
    return raw


@pytest.mark.parametrize(
    "overrides,expected_code",
    [
        ({"risk": "none"}, "GATEWAY_RISK_REQUIRED"),
        ({"execution": "none"}, "GATEWAY_EXECUTION_REQUIRED"),
        ({"exchange_name": "OKX___SWAP"}, "GATEWAY_VENUE_UNSUPPORTED"),
        ({"requested_operations": ["direct.dispatch"]}, "GATEWAY_PRINCIPAL_BYPASS_UNSAFE"),
        ({"requested_operations": ["direct.reserve"]}, "GATEWAY_PRINCIPAL_BYPASS_UNSAFE"),
        (
            {"execution": {"client_config": {}, "allow_direct_path": True}},
            "GATEWAY_DIRECT_PATH_BYPASS_FORBIDDEN",
        ),
        (
            {"execution": {"client_config": {}, "routing": {"allow_direct_path": True}}},
            "GATEWAY_DIRECT_PATH_BYPASS_FORBIDDEN",
        ),
    ],
)
def test_t_r2_01_strict_five_component_profile_rejects_unsafe_configs(overrides, expected_code):
    raw = _minimal_strict_raw(**overrides)
    with pytest.raises(GatewayStrategyConfigError) as exc_info:
        parse_gateway_strategy_config(raw, profile=STRICT_FIVE_COMPONENT_PROFILE)
    assert str(exc_info.value) == expected_code


def test_t_r2_01_strict_profile_missing_risk_or_execution_rejected():
    raw_no_risk = _minimal_strict_raw()
    raw_no_risk.pop("risk")
    with pytest.raises(GatewayStrategyConfigError) as r_exc:
        parse_gateway_strategy_config(raw_no_risk, profile=STRICT_FIVE_COMPONENT_PROFILE)
    assert str(r_exc.value) == "GATEWAY_RISK_REQUIRED"

    raw_no_exec = _minimal_strict_raw()
    raw_no_exec.pop("execution")
    with pytest.raises(GatewayStrategyConfigError) as e_exc:
        parse_gateway_strategy_config(raw_no_exec, profile=STRICT_FIVE_COMPONENT_PROFILE)
    assert str(e_exc.value) == "GATEWAY_EXECUTION_REQUIRED"


# ---------------------------------------------------------------------------
# T-R2-02: GatewayReadinessClient + GatewayReadinessProofIssuer end-to-end
# ---------------------------------------------------------------------------


def test_t_r2_02_gateway_readiness_client_verifies_issuer_attestation_and_rollback():
    issuer = GatewayReadinessProofIssuer(
        gateway_service_id="gw-svc-1",
        gateway_instance_id="gw-inst-1",
        gateway_scope_ref="scope-ctp",
        risk_service_ref="risk-svc-1",
        risk_protocol_version="2.0",
        execution_service_ref="exec-svc-1",
        execution_protocol_version="2.0",
        monitor_service_ref="mon-svc-1",
        monitor_protocol_version="1.0",
        runtime_service_ref="rt-svc-1",
        issuer_key_id="gw-key-1",
        shared_key=SHARED_KEY,
    )
    issued = []

    def fetch():
        att = issuer.issue_attestation(
            account_scope="acct-1",
            strategy_scope="strat-1",
            route="CTP___FUTURE",
            strategy_principal_operations=STRICT_STRATEGY_DEFAULT_OPERATIONS,
            risk_state="READY",
            execution_state="READY",
            last_acked_heartbeat_age_ms=100,
            monitor_stop_new_active=False,
            registered_run_id="run-1",
            now_ns=NOW_NS,
        )
        issued.append(att)
        return att.to_mapping()

    client = GatewayReadinessClient(
        fetch,
        expected_account_scope="acct-1",
        expected_strategy_scope="strat-1",
        expected_route="CTP___FUTURE",
        expected_run_id="run-1",
        shared_key=SHARED_KEY,
        require_attestation=True,
        clock_ns=lambda: NOW_NS + 1_000,
    )
    p1 = client.verify_ready()
    assert p1.proof_generation == 1
    p2 = client.verify_ready()
    assert p2.proof_generation == 2

    # Rollback to older generation is rejected
    stale_client = GatewayReadinessClient(
        lambda: issued[0].to_mapping(),
        expected_account_scope="acct-1",
        expected_strategy_scope="strat-1",
        expected_route="CTP___FUTURE",
        expected_run_id="run-1",
        shared_key=SHARED_KEY,
        require_attestation=True,
        clock_ns=lambda: NOW_NS + 1_000,
    )
    stale_client._last_generation = 2
    with pytest.raises(GatewayReadinessProofError) as rb_exc:
        stale_client.verify_ready()
    assert rb_exc.value.code == "GATEWAY_READINESS_COMPONENT_NOT_READY"

    # Wrong shared key -> GATEWAY_READINESS_AUTH_FAILED
    bad_key_client = GatewayReadinessClient(
        lambda: issued[0].to_mapping(),
        shared_key=b"z" * 32,
        require_attestation=True,
        clock_ns=lambda: NOW_NS + 1_000,
    )
    with pytest.raises(GatewayReadinessProofError) as auth_exc:
        bad_key_client.verify_ready()
    assert auth_exc.value.code == "GATEWAY_READINESS_AUTH_FAILED"

    # Transport error -> GATEWAY_READINESS_UNREACHABLE
    offline_client = GatewayReadinessClient(
        lambda: (_ for _ in ()).throw(ConnectionError("timeout")),
        clock_ns=lambda: NOW_NS,
    )
    with pytest.raises(GatewayReadinessProofError) as net_exc:
        offline_client.verify_ready()
    assert net_exc.value.code == "GATEWAY_READINESS_UNREACHABLE"


# ---------------------------------------------------------------------------
# T-R2-03: GatewayBtApi._risk_reserve with real v2 Gateway journal first-creation proof
# ---------------------------------------------------------------------------


@contextmanager
def _noop_fence():
    yield


def test_t_r2_03_gateway_api_risk_reserve_v2_first_creation_proof(tmp_path):
    guard_dir = tmp_path / "guard"
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, "acct-1", "owner-1")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(tmp_path / "db", "acct-1", generation=generation)
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    journal.enable_account_extensions(guard)
    coordinator = GatewayRiskFirstReserveV2Coordinator(journal, guard)

    try:
        identity = WireIdentity(
            role=WireRole.SERVICE, principal_ref="risk-principal", actor_ref="risk-actor"
        )
        scope = WireScope(
            execution_domain_ref="dom-1",
            account_ref="acct-1",
            strategy_ref="strat-1",
            run_id="run-1",
        )
        grant = GatewayDelegateGrant(
            identity=identity,
            account_scope="acct-1",
            strategy_scope="strat-1",
            execution_domain_ref="dom-1",
            actor_kind="risk",
            revision="grant-v1",
            instruments=frozenset({INSTRUMENT}),
            operations=frozenset({"direct.reserve", "direct.dispatch"}),
        )

        class _IntentHost:
            def __init__(self):
                self._intents = {}

            def create_risk_reserve_intent(self, candidate):
                self._intents[candidate.request_key] = candidate
                return object()

            def load_intent(self, key):
                return self._intents.get(key)

            def consume_risk_reserve_ticket(self, candidate, ticket):
                return ticket is not None and candidate.request_key in self._intents

        class _V2RiskClient:
            available = True

            def __init__(self, *, tamper: bool = False):
                self.tamper = tamper
                self._counter = 0

            def reconnect(self, *, timeout_seconds=5.0):
                return None

            def reserve_v2(self, candidate, *, timeout_seconds=5.0):
                import json as _json

                self._counter += 1
                payload = _json.loads(candidate.canonical_payload)
                cmd = GatewayDelegatedCommand(
                    account_scope="acct-1",
                    strategy_scope="strat-1",
                    execution_domain_ref="dom-1",
                    actor_principal="risk-principal",
                    actor_ref="risk-actor",
                    actor_kind="risk",
                    run_id="run-1",
                    request_key=candidate.request_key,
                    payload_digest=gateway_payload_digest(payload),
                    instrument=INSTRUMENT,
                    grant_revision="grant-v1",
                )
                ctx = SimpleNamespace(
                    identity=identity,
                    scope=scope,
                    principal_ref="risk-principal",
                    registration_id="reg-1",
                )
                permit = authorize_delegated_command(
                    ctx,
                    grant,
                    cmd,
                    "direct.reserve",
                    payload,
                    registry_recheck=lambda _: True,
                    current_grant=lambda: grant,
                    instrument_recheck=lambda inst: inst == INSTRUMENT,
                )
                v2_req = GatewayRiskFirstReserveRequestV2(
                    command=cmd,
                    risk_service_id="risk-svc-1",
                    risk_instance_id="risk-inst-1",
                    strategy_principal="strat-principal",
                    risk_registration_id="reg-1",
                    risk_service_epoch="epoch-1",
                    risk_permit_id=f"perm-{self._counter}",
                    risk_view_digest="ab" * 32,
                    attempt_nonce_digest=risk_first_reserve_nonce_digest(
                        bytes([self._counter]) * 32
                    ),
                    requested_ttl_ms=5000,
                )
                proof = coordinator.reserve_first_v2(
                    v2_req,
                    payload,
                    delegation_permit=permit,
                    reservation_fence=_noop_fence,
                )
                if self.tamper:
                    proof = replace(proof, request_commitment_digest="00" * 32)
                answer = RiskOrderResult(
                    request_key=candidate.request_key,
                    outcome="RESERVED",
                    state="RESERVED",
                    permit_id=f"perm-{self._counter}",
                    reservation_id=proof.reservation_id,
                    view_digest="ab" * 32,
                )
                return SimpleNamespace(
                    outcome="ANSWERED",
                    answer=answer,
                    first_reserve_request=v2_req,
                    first_reserve_proof=proof,
                )

        import threading

        binding = GatewayCommandBinding(
            journal_id=journal.journal_id,
            account_scope="acct-1",
            strategy_scope="strat-1",
            principal="strat-principal",
        )
        from bt_api_py._contracts.models import OrderRequest, OrderType, Side
        from bt_api_py.gateway_direct_client import GatewayClientRoute

        route = GatewayClientRoute(
            mount="broker.direct_write",
            service_id="gw-svc",
            instance_id="gw-inst",
            scope_ref="scope-1",
            scope=scope,
            identity=WireIdentity(
                role=WireRole.STRATEGY_CLIENT,
                principal_ref="strat-principal",
                actor_ref="strat-1",
            ),
        )

        def _make_api(risk_client):
            api = object.__new__(GatewayBtApi)
            api._lock = threading.RLock()
            api._host = _IntentHost()
            api._risk = risk_client
            api._binding_object = binding
            api._route_object = route
            api._risk_keys = set()
            api._risk_permits = {}
            api._risk_uncertain_keys = set()
            return api

        req1 = OrderRequest(
            symbol="rb2701",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("3500"),
            account_id="acct-1",
            client_order_id="cli-v2-ord-1",
            idempotency_key="v2-ord-1",
            exchange_id="SHFE",
            offset="open",
        )

        # 1. V1 Risk client without reserve_v2 stays strictly fail-closed UNKNOWN
        v1_api = _make_api(SimpleNamespace(available=True))
        v1_res = v1_api._risk_reserve(req1, 5.0, 5.0, None)
        assert v1_res.outcome == "UNKNOWN"
        assert v1_res.code == "RISK_OUTCOME_UNKNOWN"

        # 2. Authentic v2 proof from real SQLite journal -> RESERVED + permit recorded
        v2_api = _make_api(_V2RiskClient(tamper=False))
        v2_res = v2_api._risk_reserve(req1, 5.0, 5.0, None)
        assert v2_res.outcome == "RESERVED"
        assert v2_res.record is not None
        assert "v2-ord-1" in v2_api._risk_permits
        assert "v2-ord-1" not in v2_api._risk_uncertain_keys

        # 3. Tampered v2 proof -> stays strictly fail-closed UNKNOWN
        req2 = replace(req1, idempotency_key="v2-ord-2")
        tamper_api = _make_api(_V2RiskClient(tamper=True))
        t_res = tamper_api._risk_reserve(req2, 5.0, 5.0, None)
        assert t_res.outcome == "UNKNOWN"
        assert "v2-ord-2" not in tamper_api._risk_permits
        assert "v2-ord-2" in tamper_api._risk_uncertain_keys
    finally:
        guard.release()


# ---------------------------------------------------------------------------
# T-R2-04: RiskParentPort real child reserve / dispatch path
# ---------------------------------------------------------------------------


def test_t_r2_04_risk_parent_port_wires_child_reserve_and_dispatch():
    class _V2ChildRiskClient:
        def available(self):
            return True

        def parent_reserve(self, view):
            return RiskParentCallResult("ANSWERED", parent_outcome="RESERVED", code=None)

        def child_reserve(self, parent_ref, child_view, canonical_payload, run_id):
            return RiskOrderResult(
                request_key=child_view.request_key,
                outcome="RESERVED",
                state="RESERVED",
                permit_id="child-perm-1",
                reservation_id="child-resv-1",
                view_digest=child_view.digest(),
            )

        def child_dispatch(self, parent_ref, request_key, permit_id, reservation_id, view_digest):
            return RiskOrderResult(
                request_key=request_key,
                outcome="DISPATCHED",
                state="DISPATCHED",
                permit_id=permit_id,
                reservation_id=reservation_id,
                view_digest=view_digest,
            )

    port = RiskParentPort(_V2ChildRiskClient(), route="CTP___FUTURE")
    child_view = RiskOrderView(
        account_scope="acct-1",
        route="CTP___FUTURE",
        strategy_ref="strat-1",
        request_key="child-1",
        instrument=INSTRUMENT,
        side="BUY",
        offset="OPEN",
        position_side="NET",
        quantity=Decimal("1"),
        quantity_unit="contracts",
        order_type="LIMIT",
        limit_price=Decimal("3500"),
        trading_day="20261123",
        origin="execution",
        parent_ref="parent-1",
        child_id="child-1",
    )

    # Before parent reserved -> UNKNOWN / risk_parent_not_reserved
    pre = port.reserve_child(
        {"parent_ref": "parent-1", "child_view": child_view, "canonical_payload": "{}"}
    )
    assert pre == {"status": "UNKNOWN", "code": "risk_parent_not_reserved"}

    # Reserve parent first
    p_reply = port.reserve_parent(
        {
            "spec": {
                "account_scope": "acct-1",
                "parent_ref": "parent-1",
                "instrument": INSTRUMENT.to_dict(),
                "side": "buy",
                "offset": "open",
                "quantity": "5",
                "quantity_unit": "contracts",
                "limit_price": "3500",
                "creation_trading_day": "20261123",
                "allowed_trading_days": ["20261123"],
            },
            "strategy": {"strategy_ref": "strat-1", "run_id": "run-1"},
        }
    )
    assert p_reply == {"status": "ACCEPTED", "code": None}

    # Reserve & dispatch child succeed after parent reserved
    c_res = port.reserve_child(
        {
            "parent_ref": "parent-1",
            "child_view": child_view,
            "canonical_payload": '{"idempotency_key":"child-1"}',
            "run_id": "run-1",
        }
    )
    assert c_res["status"] == "ACCEPTED"
    assert c_res["permit_id"] == "child-perm-1"

    d_res = port.dispatch_child(
        {
            "parent_ref": "parent-1",
            "request_key": "child-1",
            "permit_id": c_res["permit_id"],
            "reservation_id": c_res["reservation_id"],
            "view_digest": c_res["view_digest"],
        }
    )
    assert d_res["status"] == "DISPATCHED"


# ---------------------------------------------------------------------------
# T-R2-05: ParentServiceHost optional risk_port injection
# ---------------------------------------------------------------------------


def test_t_r2_05_parent_service_host_validates_risk_port():
    dummy_cfg = object.__new__(ParentConfig)
    stub_port = SimpleNamespace(reserve_parent=lambda payload: {"status": "ACCEPTED"})
    host = ParentServiceHost(dummy_cfg, risk_port=stub_port)
    assert host.risk_port is stub_port

    with pytest.raises(ParentServiceError) as exc_info:
        ParentServiceHost(dummy_cfg, risk_port=object())
    assert str(exc_info.value) == "INVALID_CONFIGURATION"


# ---------------------------------------------------------------------------
# T-R2-07: Strict Direct mode rejects service & hybrid routing options
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_opt,expected_code",
    [
        ({"market_runtime_factory": object()}, "DIRECT_SERVICE_KEY_FORBIDDEN"),
        ({"forwarding_config": object()}, "DIRECT_SERVICE_KEY_FORBIDDEN"),
        ({"transport_mode": "zmq"}, "DIRECT_SERVICE_KEY_FORBIDDEN"),
        ({"execution_order_routing": object()}, "LEGACY_HYBRID_ROUTING_RETIRED"),
        ({"service_client_config": {}}, "LEGACY_HYBRID_ROUTING_RETIRED"),
        ({"gateway_client_config": {}}, "LEGACY_HYBRID_ROUTING_RETIRED"),
        ({"execution_parent_port": object()}, "LEGACY_HYBRID_ROUTING_RETIRED"),
    ],
)
def test_t_r2_07_direct_api_assembly_strict_direct_rejects_service_and_hybrid_keys(
    bad_opt, expected_code
):
    options = {"debug": False, **bad_opt}
    with pytest.raises(DirectApiAssemblyError) as exc_info:
        DirectApiAssembly.from_options(
            options=options,
            exchange_kwargs={},
            execution_config={},
            api_factory=lambda **kw: kw,
            api=None,
            expected_exchange_names=("CTP___FUTURE",),
            execution_option_keys=(),
            market_data_only=True,
            strict_direct=True,
        )
    assert exc_info.value.code == expected_code


def test_t_r2_07_bt_api_strict_direct_rejects_market_runtime_and_hybrid_routing():
    with pytest.raises(ValueError, match="DIRECT_SERVICE_KEY_FORBIDDEN"):
        BtApi(exchange_kwargs={}, market_runtime_factory=object(), strict_direct=True)
    with pytest.raises(ValueError, match="LEGACY_HYBRID_ROUTING_RETIRED"):
        BtApi(exchange_kwargs={}, execution_order_routing=object(), strict_direct=True)


# ---------------------------------------------------------------------------
# T-R2-08: Five-component Monitor health agrees with ReadinessProof
# ---------------------------------------------------------------------------


def test_t_r2_08_monitor_five_component_health_integration():
    def obs(name: str, state: str = "READY"):
        return ComponentObservation(
            component=name,
            service_ref=f"{name}-svc",
            protocol_version="1.0",
            state=state,
            observed_at_ns=NOW_NS - 1000,
            max_age_ns=30_000_000_000,
        )

    snap = evaluate_five_component_health(
        gateway=obs("gateway"),
        risk=obs("risk"),
        execution=obs("execution"),
        monitor=obs("monitor"),
        runtime=obs("runtime"),
        now_ns=NOW_NS,
        monitor_required=True,
        last_acked_heartbeat_age_ms=50,
    )
    assert snap.all_ready is True
    assert snap.overall_state == "READY"


# ---------------------------------------------------------------------------
# T-R2-09: PASS v2 market/conditional routing & forbid_direct_path=True
# ---------------------------------------------------------------------------


def _routing_config_dict(*, algorithm=None, order_types=None):
    rule = {
        "id": "rule-pass-v2",
        "match": {"instrument": INSTRUMENT.to_dict()},
        "scheme": {"name": "pass-v1", "version": 1},
        "algorithm": algorithm or {"class": "PASS", "version": 2, "params": {}},
        "deadline": {"kind": "absolute", "at": "2099-12-31T15:00:00Z"},
        "time_in_force": "DAY",
        "trading_day": "20261123",
        "schedule_binding": {"quantity_step": "1", "price_step": "0.5", "retry_limit": 0},
    }
    if order_types is not None:
        rule["match"]["order_types"] = order_types
    return {
        "schema_version": 2,
        "algorithm_build_id": "build-iter62",
        "component": "exec-inst-1",
        "account_ref": "acct-1",
        "strategy_ref": "strat-1",
        "execution_domain_ref": "dom-1",
        "grant_revision": "grant-v1",
        "delegation": {
            "delegation_id": "delegation-1",
            "principal_ref": "exec-principal",
        },
        "instruments": [INSTRUMENT.to_dict()],
        "rules": [rule],
    }


@pytest.mark.parametrize("order_type", ["limit", "market", "stop", "stop_limit", "conditional"])
def test_t_r2_09_pass_v2_routing_matches_all_passthrough_order_types(order_type):
    cfg = parse_execution_routing(
        _routing_config_dict(order_types=["limit", "market", "stop", "stop_limit", "conditional"])
    )
    # Round-trip via to_plain preserves configuration_id
    reloaded = parse_execution_routing(cfg.to_plain())
    assert reloaded == cfg

    facts = _OrderFacts(INSTRUMENT, "buy", "open", order_type, Decimal("2"), "contracts")
    decision = cfg.rule_set.match(facts)
    assert decision.outcome is _Outcome.MATCH
    assert decision.rule_index == 0


def test_t_r2_09_slice_and_pass_v1_remain_limit_only_and_forbid_direct_path_blocks_bypass():
    # SLICE v1 rejects market order_types
    with pytest.raises(ExecutionRoutingError) as slice_exc:
        parse_execution_routing(
            _routing_config_dict(
                algorithm={"class": "SLICE", "version": 1, "params": {"maximum": "2"}},
                order_types=["market"],
            )
        )
    assert slice_exc.value.code == "EXECUTION_ORDER_TYPE_UNSUPPORTED"

    # ExecutionOrderRouter with forbid_direct_path=True rejects unmatched order
    cfg = parse_execution_routing(
        _routing_config_dict(order_types=["limit"])  # only matches limit
    )
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("BEGIN")
    install_execution_routing_tables(db)
    db.execute("COMMIT")
    router = ExecutionOrderRouter(
        db=db,
        config=cfg,
        execution_port=object(),
        risk_selected=True,
        forbid_direct_path=True,
    )

    from bt_api_py._contracts.models import OrderRequest, OrderType, Side
    from bt_api_py.gateway_direct_client import GatewayClientRoute, build_gateway_order_intent

    binding = GatewayCommandBinding(
        journal_id="j-1",
        account_scope="acct-1",
        strategy_scope="strat-1",
        principal="strat-p",
    )
    scope = WireScope(
        execution_domain_ref="dom-1",
        account_ref="acct-1",
        strategy_ref="strat-1",
        run_id="run-1",
    )
    route = GatewayClientRoute(
        mount="broker.direct_write",
        service_id="gw-svc",
        instance_id="gw-inst",
        scope_ref="scope-1",
        scope=scope,
        identity=WireIdentity(
            role=WireRole.STRATEGY_CLIENT,
            principal_ref="strat-p",
            actor_ref="strat-1",
        ),
    )
    unmatched_req = OrderRequest(
        symbol="rb2701",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=None,
        account_id="acct-1",
        client_order_id="cli-unmatched-mkt-1",
        idempotency_key="unmatched-mkt-1",
        exchange_id="SHFE",
        offset="open",
    )
    intent = build_gateway_order_intent(binding, route, unmatched_req, ttl_seconds=5.0)
    res = router.route(intent)
    assert res.outcome is ExecutionRoutingOutcome.EXECUTION_REJECTED
    assert res.code == "GATEWAY_DIRECT_PATH_BYPASS_FORBIDDEN"
