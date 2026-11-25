"""Real local CURVE Gateway -> Risk R-05 v2 + R-04 qualified cancel-attempt stream integration.

Evidence level: INTEGRATION (real local ZMQ CURVE ServiceHost + SQLite Gateway journal
+ SQLite fenced PretradeLedger + PretradeEngine; synthetic exchange facts/cancel payloads).
"""
from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import traceback
from decimal import Decimal
from types import SimpleNamespace

import pytest
import zmq
from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectOrderStatus,
    NativeOrderIdentity,
)
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import RiskOrderView, canonical_risk_json
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, _identity
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade import PolicyStore, PretradeEngine, RuleRegistry
from bt_api_risk.pretrade.ledger import PretradeLedger
from bt_api_risk.pretrade.r04_v2_observation_contract import BuiltinR04CombinedV2
from bt_api_risk.pretrade.r04_v2_registry_candidate import R04ArtifactBinding
from bt_api_risk.risk_order_journal import RiskOrderJournal
from test_pretrade_support import FakeControl, FakeFacts, FakeGateway, OPERATOR, base_policy, caller

from bt_api_py.ctp_gateway_order_cut_source import CtpGatewayOrderCutSource
from bt_api_py.gateway_account_cancel_attempts import (
    OPERATIONS as CANCEL_ATTEMPT_OPERATIONS,
    GatewayAccountCancelAttempts,
)
from bt_api_py.gateway_account_cancel_attempts_client import GatewayAccountCancelAttemptsClient
from bt_api_py.gateway_account_comparison import GatewayAccountComparison
from bt_api_py.gateway_account_facts import ACCOUNT_FACTS_OPERATIONS, GatewayAccountFactsObserver
from bt_api_py.gateway_account_facts_client import GatewayAccountFactsClient
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverRoute
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.gateway_order_cut_client import GatewayOrderCutClient
from bt_api_py.gateway_order_cut_codec import OPERATIONS as ORDER_CUT_OPERATIONS
from bt_api_py.risk_account_facts_coordinator import RiskAccountFactsCoordinator
from bt_api_py.risk_service_config import (
    RiskServiceConfigError,
    parse_risk_service_config,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import BoundCallRoute, ThreadedServiceClientRuntime
from tests.integration.test_iter63_delegate_risk_execution_loopback import (
    ACCOUNT_ONLY,
    RISK,
    _read_service,
    _service_client,
)
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, DOMAIN, SERVICE
from tests.integration.test_iter63_gateway_dual_ports import _dual_config
from tests.integration.test_service_account_instrument_metadata import _free_endpoint

DAY = "20261126"
RB = MarketInstrumentKey("ctp", "SHFE", "simnow", "FUTURE", "rb2701", "rb2701")
R04_NAME = "trusted.rules:CurveBuiltinR04"


class _R04Rule(BuiltinR04CombinedV2):
    implementation_version = 1
    result_contract_version = 2
    observation_contract_version = 2


def test_curve_gateway_to_risk_r04_r11_joint_coverage_and_enforcement(tmp_path, monkeypatch):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    fingerprint = hashlib.sha256(server_public).hexdigest()
    all_ops = ACCOUNT_FACTS_OPERATIONS | ORDER_CUT_OPERATIONS | CANCEL_ATTEMPT_OPERATIONS
    grant = PrincipalGrant(
        RISK,
        (ResourceGrant("gateway-a", "service.account_read", "scope-a", ACCOUNT_ONLY, all_ops),),
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
        principals={client_public: RISK.principal_ref},
        grants={RISK.principal_ref: (grant,)},
    )
    monkeypatch.setattr(CtpGatewayOrderCutSource, "journal", property(lambda s: s._fake_journal))
    monkeypatch.setattr(CtpGatewayOrderCutSource, "guard", property(lambda s: s._fake_guard))
    monkeypatch.setattr(CtpGatewayOrderCutSource, "current", lambda s: s._fake_cut)
    monkeypatch.setattr(CtpGatewayOrderCutSource, "current_binding", lambda s: s._fake_binding)

    ready, stop = queue.Queue(), threading.Event()
    (tmp_path / "guard").mkdir()

    def serve():
        owner = host = None
        try:
            state = {}

            def factory():
                guard = LocalAccountSessionGuard(tmp_path / "guard", ACCOUNT, "owner")
                journal = GatewayDirectCommandJournal.create(
                    tmp_path / "gateway.sqlite", ACCOUNT, generation=guard.acquire()
                )
                ledger = GatewayDirectExecutionLedger(journal, guard)
                # Reserve & dispatch one order before enabling strategy-ledger ports (test setup)
                payload = {"symbol": RB.native_symbol, "side": "buy", "quantity": "1", "idempotency_key": "ord-1"}
                order = journal.reserve("strat-a", "ord-1", CLIENT_A.principal_ref, payload)
                identity = NativeOrderIdentity("CTP", "ref-1", {"trading_day": DAY, "front_id": 1}, 1)
                journal.begin_dispatch(
                    "strat-a", "ord-1", order.reservation_id, order.digest,
                    CLIENT_A.principal_ref, 1, _identity(identity, "CTP"), ("k-ord-1",),
                )
                control = journal.enable_controls(guard)
                journal.enable_strategy_ledger(guard)
                journal.enable_account_extensions(guard)
                journal.enable_parent_authority(guard)
                journal.enable_account_facts_financial(guard)
                journal.enable_actor_stream(guard)
                journal.enable_strategy_origin(guard)
                journal.enable_control_cancel(guard)
                journal.enable_account_fact_recovery(guard)
                journal.enable_account_event_recovery(guard)
                journal.enable_observed_order_source(guard)
                journal.enable_cancel_attempt_stream(guard)
                journal._cancel_attempt_stream_qualified = True

                source_ref, session_ref = "simnow-src", "simnow-sess-1"
                journal.begin_observed_order_source(
                    guard, source_ref=source_ref, session_ref=session_ref, trading_day=DAY
                )
                cmd = GatewayDelegatedCommand(
                    account_scope=ACCOUNT,
                    strategy_scope="strat-a",
                    execution_domain_ref=DOMAIN,
                    actor_principal=CLIENT_A.principal_ref,
                    actor_ref=CLIENT_A.actor_ref,
                    actor_kind="risk",
                    run_id="run-1",
                    request_key="ord-1",
                    payload_digest=order.digest,
                    instrument=RB,
                    grant_revision="rev-1",
                )
                with journal._transaction() as tx:
                    tx.execute(
                        "INSERT INTO direct_delegated_commands VALUES (?,?,?,?,?,?,?)",
                        (ACCOUNT, "strat-a", "ord-1", order.reservation_id,
                         CLIENT_A.principal_ref, cmd.canonical_json, cmd.digest),
                    )
                cancel_journal = GatewayDirectCancelJournal(journal)
                attempt = cancel_journal.request(
                    "strat-a", "cancel-1", CLIENT_A.principal_ref, "ord-1",
                    order.reservation_id, order.digest, target_identity=identity, symbol=RB.native_symbol,
                )
                prepared = PreparedCancelRequest("ctp", identity, RB.native_symbol, DAY, 2, {"req": 1}, ("ck-1",))
                cancel_journal.begin_dispatch("strat-a", "cancel-1", CLIENT_A.principal_ref, 1, prepared)
                cancel_journal.finish(
                    "strat-a", "cancel-1", CLIENT_A.principal_ref, 2,
                    attempt.attempt_id, prepared.digest(), journal._generation, True,
                )

                # Record empty clean query cut (0 open orders after cancel)
                cut = journal.record_observed_order_query_cut(
                    guard,
                    source_ref=source_ref,
                    session_ref=session_ref,
                    trading_day=DAY,
                    begin_sequence=0,
                    end_sequence=0,
                    query_digest="a" * 64,
                    query_scope="ctp.futures.account_orders.v1",
                    query_row_count=0,
                    query_fact_digests=(),
                )
                source = object.__new__(CtpGatewayOrderCutSource)
                source._fake_journal, source._fake_guard, source._fake_cut = journal, guard, cut
                source._fake_binding = SimpleNamespace(
                    source_ref=source_ref, generation=session_ref, trading_day=DAY
                )
                source._principals, source._execution_domain_ref = frozenset({RISK.principal_ref}), DOMAIN
                dispatcher = GatewayDirectDispatcher(
                    journal, "CTP", guard, lambda *_: None, lambda _: (), lambda _: True
                )
                cancel_disp = GatewayDirectCancelDispatcher(
                    cancel_journal, dispatcher, guard, "CTP", lambda *_: None, lambda _: True
                )
                state.update(journal=journal, ledger=ledger, source=source, control=control)
                return GatewayDirectOwnerResources(
                    dispatcher, journal, guard.release, cancel_dispatcher=cancel_disp
                )

            owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=5, busy_timeout_seconds=5)
            assert owner.start(10)
            journal, ledger = state["journal"], state["ledger"]
            read = _read_service(tmp_path, {RISK.principal_ref, CLIENT_A.principal_ref})
            binding = GatewayDirectServiceBinding(
                journal,
                {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({"rb"}))},
                owner=owner,
                strategy_financial_enabled=True,
                cancel_enabled=True,
                control=state["control"],
                control_grants={
                    CLIENT_A.principal_ref: GatewayControlGrant(
                        CLIENT_A.actor_ref, True, frozenset(), frozenset({"direct.control.status"})
                    )
                },
            )
            facts_obs = GatewayAccountFactsObserver(
                owner, journal, ledger, account_scope=ACCOUNT,
                execution_domain_ref=DOMAIN, principals=frozenset({RISK.principal_ref}),
            )
            cancel_obs = GatewayAccountCancelAttempts(
                owner, journal, account_scope=ACCOUNT,
                execution_domain_ref=DOMAIN, principals=frozenset({RISK.principal_ref}),
            )
            comparison = GatewayAccountComparison(
                owner, journal, account_scope=ACCOUNT,
                principals=frozenset({CLIENT_A.principal_ref}), instruments=frozenset({RB}),
            )
            config = _dual_config(endpoint).to_public_dict()
            config["components"]["gateway"]["settings"]["options"].update(
                dispatch_enabled=True,
                control_enabled=True,
                strategy_ledger_enabled=True,
                account_extensions_enabled=True,
                account_facts_financial_enabled=True,
                cancel_enabled=True,
                strategy_origin_enabled=True,
                control_cancel_enabled=True,
                account_fact_recovery_enabled=True,
                account_event_recovery_enabled=True,
                cancel_attempt_stream_enabled=True,
                cancel_attempt_stream_qualified=True,
            )
            host = ServiceHost(
                parse_service_config(config),
                authority,
                tmp_path / "registry",
                lambda *_: ComponentResources.borrowed(
                    {
                        "binding": binding,
                        "read_service": read,
                        "account_facts": facts_obs,
                        "account_cancel_attempts": cancel_obs,
                        "order_cut_source": state["source"],
                        "account_comparison": comparison,
                    },
                    ready=lambda: True,
                ),
            )
            host.start()
            ready.put(("ready",))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as error:
            ready.put(("error", type(error).__name__, str(error), traceback.format_exc()))
        finally:
            if host is not None and host.state == "started":
                host.close()
            if owner is not None:
                owner.close(10)

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    status_msg = ready.get(timeout=15)
    assert status_msg == ("ready",), status_msg

    # Start real ZMQ CURVE client runtime & fenced Risk ledger + engine
    risk_clock_ns = [time.time_ns()]
    risk_journal = RiskOrderJournal(tmp_path / "risk.sqlite", writer_id="curve-risk-test")
    scope = AccountScope(provider="ctp", account_id="acct-curve", environment="simnow")

    client_cfg = _service_client(endpoint, RISK, "upstream.account")
    runtime = ThreadedServiceClientRuntime(
        lambda: ServiceClientRuntime(
            client_cfg,
            identity=RISK,
            scope_resolver=lambda _: ACCOUNT_ONLY,
            peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory(
                {"security-a": ClientCurveKeys(server_public, client_public, client_secret)},
                strict_single_connection=True,
            ),
            run_id="risk-curve-run",
            requested_operations={"upstream.account": all_ops},
        )
    )
    try:
        snap = runtime.start(timeout_seconds=10.0)
        assert snap.healthy is True

        with risk_journal.writer_fence(scope) as fence:
            ledger = PretradeLedger(risk_journal, fence, schema_version=4)
            ledger.enable_r05_observations_v2()
            ledger.enable_r04_coverage()
            ledger._v2_clock_ns = lambda: risk_clock_ns[0]

            obs_route = GatewayAccountObserverRoute(
                "upstream.account", SERVICE.actor_ref, "gateway-a", "scope-a", ACCOUNT_ONLY, RISK
            )
            bound_route = BoundCallRoute(RISK, ACCOUNT_ONLY, "scope-a", SERVICE.actor_ref, "gateway-a", SERVICE)
            facts_client = GatewayAccountFactsClient(
                runtime, route=obs_route, peer=SERVICE, fingerprint=fingerprint
            )
            cut_client = GatewayOrderCutClient(
                runtime, route=bound_route, fingerprint=fingerprint
            )
            cancel_client = GatewayAccountCancelAttemptsClient(
                runtime, route=obs_route, peer=SERVICE, fingerprint=fingerprint, allow_qualified=True
            )
            coord = RiskAccountFactsCoordinator(
                facts_client,
                ledger,
                limit=64,
                timeout_seconds=5.0,
                order_cut_client=cut_client,
                cut_max_age_seconds=60.0,
                cut_limit=64,
                cut_timeout_seconds=5.0,
                now_ns=lambda: risk_clock_ns[0],
                cancel_attempts_client=cancel_client,
                cancel_max_age_seconds=60.0,
                cancel_limit=64,
                cancel_timeout_seconds=5.0,
            )

            # 1) Pull facts + cut + qualified cancel attempts over REAL CURVE!
            f_res = coord.poll_once()
            assert f_res.status in ("APPLIED", "EMPTY"), f_res
            cut_res = coord.poll_order_cut_once()
            assert cut_res.status == "CUT_APPLIED", cut_res
            c_res = coord.poll_cancel_attempts_once()
            assert c_res.status == "APPLIED" and c_res.cursor == 2, c_res

            # Verify idempotent per-attempt_id full-key cancel count == 1 (DISPATCHING + SENT -> 1!)
            with ledger.transaction() as txn:
                assert txn.r04_cov_get_count(RB, DAY, "cancels") == Decimal(1)
                assert txn.r04_cov_day_coverage_verified(trading_day=DAY, now_ns=risk_clock_ns[0]) is True

            # 2) Attach PretradeEngine and verify status_summary() == COMPLETE + live R-04 enforcement!
            fake_facts = FakeFacts(lambda: risk_clock_ns[0])
            fake_facts.day = DAY
            fake_facts.set_account(day=DAY)
            fake_facts.set_price(RB, "100")
            artifact = R04ArtifactBinding(
                implementation=R04_NAME,
                implementation_version=1,
                claim_contract_version=2,
                observation_contract_version=2,
                rule_class=_R04Rule,
                artifact_ref="curve-r04",
            )
            engine = PretradeEngine(
                ledger,
                PolicyStore(ledger),
                RuleRegistry({}, r04_artifacts={R04_NAME: artifact}),
                fake_facts,
                FakeGateway(),
                FakeControl(),
                account_scope=ACCOUNT,
                route="CTP___FUTURE",
                now_ns=lambda: risk_clock_ns[0],
                reservation_ttl_seconds=10,
                actor_principal=OPERATOR.principal_ref,
                dispatch_recheck_seconds=1.0,
                dispatch_recheck_limit=1,
            )
            engine.reset_observations("e1", -1)
            policy_doc = base_policy(
                closable_check={"enabled": False},
                self_trade={"enabled": False},
                counters={"cancels": {"warn": 1, "block": 1}},
                r04_binding={
                    "implementation": R04_NAME,
                    "implementation_version": 1,
                    "claim_contract_version": 2,
                    "observation_contract_version": 2,
                },
            )
            policy_doc["account"]["account_scope"] = ACCOUNT
            engine.publish_policy(OPERATOR, policy_doc, 0, "curve-r04-policy")

            summary = engine.status_summary()
            assert summary["available"] is True
            assert summary["counts_coverage"] == {"scope": "account", "status": "COMPLETE"}
            assert summary["threshold_utilization"] == {
                "available": True,
                "status": "COMPLETE",
                "scope": "account",
                "reasons": [],
            }

            # Live R-04 cancel threshold (block=1, observed cancel=1) blocks OPEN, allows CLOSE!
            open_view = RiskOrderView(
                account_scope=ACCOUNT, route="CTP___FUTURE", strategy_ref="strat-a", origin="strategy",
                request_key="open-after-cancel", instrument=RB, side="BUY", offset="OPEN",
                position_side="NET", quantity=Decimal("1"), quantity_unit="lot",
                order_type="LIMIT", limit_price=Decimal("100"),
            )
            who = caller("strat-a")
            res_open = engine.reserve(who, open_view, canonical_risk_json(open_view.to_mapping()), "run-1")
            if res_open.outcome == "RESERVED":
                res_open = engine.dispatch(
                    who, open_view.request_key, res_open.permit_id, res_open.reservation_id, open_view.digest()
                )
            assert res_open.outcome == "DENIED" and res_open.code == "risk_order_count_limit"

            close_view = RiskOrderView(
                account_scope=ACCOUNT, route="CTP___FUTURE", strategy_ref="strat-a", origin="strategy",
                request_key="close-after-cancel", instrument=RB, side="SELL", offset="CLOSE",
                position_side="NET", quantity=Decimal("1"), quantity_unit="lot",
                order_type="LIMIT", limit_price=Decimal("100"),
            )
            res_close = engine.reserve(who, close_view, canonical_risk_json(close_view.to_mapping()), "run-1")
            assert res_close.outcome == "RESERVED"
            disp_close = engine.dispatch(
                who, close_view.request_key, res_close.permit_id, res_close.reservation_id, close_view.digest()
            )
            assert disp_close.state == "DISPATCHED"
    finally:
        runtime.close(timeout_seconds=5.0)
        stop.set()
        server.join(timeout=10)


def test_risk_service_config_cancel_attempts_validation(tmp_path):
    from tests.unit.test_iter64_risk_service_config import document, write_keys

    write_keys(tmp_path)
    doc = document()
    doc["pretrade"] = {"schema_version": 4}
    doc["account_facts_observation"] = {
        "poll_interval_seconds": "1.0",
        "timeout_seconds": "2.0",
        "page_size": 64,
        "order_cut_enabled": True,
        "order_cut_max_age_seconds": "60.0",
        "cancel_attempts_enabled": True,
        "cancel_attempts_max_age_seconds": "60.0",
        "cancel_attempts_timeout_seconds": "2.0",
        "cancel_attempts_page_size": 128,
    }
    cfg = parse_risk_service_config(doc, path=tmp_path / "risk.yaml")
    obs = cfg.account_facts_observation
    assert obs is not None
    assert obs.cancel_attempts_enabled is True
    assert obs.cancel_attempts_max_age_seconds == 60.0
    assert obs.cancel_attempts_timeout_seconds == 2.0
    assert obs.cancel_attempts_page_size == 128

    # Reject cancel_attempts_enabled without order_cut_enabled
    bad1 = document()
    bad1["pretrade"] = {"schema_version": 4}
    bad1["account_facts_observation"] = {
        "poll_interval_seconds": "1.0",
        "timeout_seconds": "2.0",
        "page_size": 64,
        "cancel_attempts_enabled": True,
        "cancel_attempts_max_age_seconds": "60.0",
    }
    with pytest.raises(RiskServiceConfigError, match="RISK_SERVICE_CONFIG_ACCOUNT_FACTS_INVALID"):
        parse_risk_service_config(bad1, path=tmp_path / "bad1.yaml")

    # Reject cancel_attempts_enabled without cancel_attempts_max_age_seconds
    bad2 = document()
    bad2["pretrade"] = {"schema_version": 4}
    bad2["account_facts_observation"] = {
        "poll_interval_seconds": "1.0",
        "timeout_seconds": "2.0",
        "page_size": 64,
        "order_cut_enabled": True,
        "order_cut_max_age_seconds": "60.0",
        "cancel_attempts_enabled": True,
    }
    with pytest.raises(RiskServiceConfigError, match="RISK_SERVICE_CONFIG_ACCOUNT_FACTS_INVALID"):
        parse_risk_service_config(bad2, path=tmp_path / "bad2.yaml")

