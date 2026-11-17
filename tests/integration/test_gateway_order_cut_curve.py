"""Real local Host/CURVE/journal, with a synthetic owner-local cut source."""

import hashlib
import queue
import threading
import traceback
from dataclasses import replace
from types import SimpleNamespace

import pytest
import zmq
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectOrderStatus,
    NativeOrderIdentity,
)
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.ctp_gateway_order_cut_source import CtpGatewayOrderCutSource
from bt_api_py.gateway_account_comparison import GatewayAccountComparison
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.gateway_order_cut_client import GatewayOrderCutClient
from bt_api_py.gateway_order_cut_codec import OPERATIONS
from bt_api_py.gateway_order_cut_service import GatewayOrderCutService
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceRegistry
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import (
    BoundCallRoute,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)
from tests.integration.test_iter63_delegate_risk_execution_loopback import (
    ACCOUNT_ONLY,
    RISK,
    _read_service,
    _service_client,
)
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, DOMAIN, SERVICE
from tests.integration.test_iter63_gateway_dual_ports import _dual_config
from tests.integration.test_service_account_instrument_metadata import _free_endpoint


def test_order_cut_real_host_curve_and_local_owner(tmp_path, monkeypatch):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    grant = PrincipalGrant(
        RISK,
        (ResourceGrant("gateway-a", "service.account_read", "scope-a", ACCOUNT_ONLY, OPERATIONS),),
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
    # The source object is a controlled test double of the exact source type;
    # journal, owner, Registry, Host and CURVE transport are real.
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
                    tmp_path / "journal.sqlite", ACCOUNT, generation=guard.acquire()
                )
                ledger = GatewayDirectExecutionLedger(journal, guard)
                control = journal.enable_controls(guard)
                journal.enable_strategy_ledger(guard)
                journal.enable_account_extensions(guard)
                journal.enable_parent_authority(guard)
                journal.enable_account_facts_financial(guard)
                journal.enable_observed_order_source(guard)
                source_ref, session_ref, day = "private-source", "private-session", "20300101"
                journal.begin_observed_order_source(
                    guard, source_ref=source_ref, session_ref=session_ref, trading_day=day
                )
                instrument = MarketInstrumentKey(
                    "ctp", "SHFE", "simnow", "future", "rb2601", "rb2601"
                )
                identity = NativeOrderIdentity("CTP", "ref-a", {"trading_day": day}, 1)
                fact = GatewayReturnFact(
                    ACCOUNT,
                    "CTP",
                    instrument,
                    day,
                    DirectExecutionEventKind.ORDER,
                    source_ref,
                    identity=identity,
                    identity_keys=("unmatched",),
                    binding_keys=("unmatched",),
                    venue_key="unmatched",
                    status=DirectOrderStatus.ACCEPTED,
                    side="buy",
                    offset="open",
                )
                assert ledger.ingest(fact).disposition == "UNATTRIBUTED"
                journal.record_observed_order_result(
                    guard,
                    source_ref=source_ref,
                    session_ref=session_ref,
                    trading_day=day,
                    source_sequence=1,
                    source_record_digest="b" * 64,
                    fact_digest=fact.fact_digest(),
                    gap_reason=None,
                )
                cut = journal.record_observed_order_query_cut(
                    guard,
                    source_ref=source_ref,
                    session_ref=session_ref,
                    trading_day=day,
                    begin_sequence=0,
                    end_sequence=1,
                    query_digest="a" * 64,
                    query_scope="ctp.futures.account_orders.v1",
                    query_row_count=2,
                    query_fact_digests=(fact.fact_digest(), fact.fact_digest()),
                )
                source = object.__new__(CtpGatewayOrderCutSource)
                source._fake_journal, source._fake_guard, source._fake_cut = journal, guard, cut
                source._fake_binding = SimpleNamespace(
                    source_ref=source_ref, generation=session_ref, trading_day=day
                )
                source._principals, source._execution_domain_ref = (
                    frozenset({RISK.principal_ref}),
                    DOMAIN,
                )
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
                state.update(journal=journal, source=source, control=control)
                return GatewayDirectOwnerResources(
                    dispatcher, journal, guard.release, cancel_dispatcher=cancel
                )

            owner = GatewayDirectOwner(
                factory, queue_size=4, max_queue_wait_seconds=5, busy_timeout_seconds=5
            )
            assert owner.start(10)
            journal = state["journal"]
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
            comparison = GatewayAccountComparison(
                owner,
                journal,
                account_scope=ACCOUNT,
                principals=frozenset({CLIENT_A.principal_ref}),
                instruments=frozenset(
                    {MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2601", "rb2601")}
                ),
            )
            config = _dual_config(endpoint).to_public_dict()
            config["components"]["gateway"]["settings"]["options"].update(
                dispatch_enabled=True,
                control_enabled=True,
                strategy_ledger_enabled=True,
                account_extensions_enabled=True,
                account_facts_financial_enabled=True,
                cancel_enabled=True,
            )
            host = ServiceHost(
                parse_service_config(config),
                authority,
                tmp_path / "registry",
                lambda *_: ComponentResources.borrowed(
                    {
                        "binding": binding,
                        "read_service": read,
                        "order_cut_source": state["source"],
                        "account_comparison": comparison,
                    },
                    ready=lambda: True,
                ),
            )
            host.start()
            ready.put(("ready", state, owner))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as error:
            ready.put(("error", type(error).__name__, str(error), traceback.format_exc()))
        finally:
            if host is not None and host.state == "started":
                host.close()
            if owner is not None:
                owner.close(10)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    runtime = None
    try:
        started = ready.get(timeout=20)
        assert started[0] == "ready", started
        config = _service_client(endpoint, RISK, "upstream.account")
        runtime = ThreadedServiceClientRuntime(
            lambda: ServiceClientRuntime(
                config,
                identity=RISK,
                scope_resolver=lambda _: ACCOUNT_ONLY,
                peer_resolver=lambda _: SERVICE,
                transport_factory=ZmqTransportFactory(
                    {"security-a": ClientCurveKeys(server_public, client_public, client_secret)},
                    strict_single_connection=True,
                ),
                run_id="risk-run",
                requested_operations={"upstream.account": OPERATIONS},
            )
        )
        assert runtime.start(timeout_seconds=10).healthy
        route = BoundCallRoute(
            RISK, ACCOUNT_ONLY, "scope-a", SERVICE.actor_ref, "gateway-a", SERVICE
        )
        client = GatewayOrderCutClient(
            runtime, route=route, fingerprint=hashlib.sha256(server_public).hexdigest()
        )

        def revision():
            with started[1]["journal"]._transaction(read_only=True) as db:
                return started[1]["journal"]._assert_anchor(db)

        initial_revision = started[2].submit_service_call(revision).result(timeout_seconds=5)
        issued = client.current()
        assert issued.outcome == "OBSERVED", issued
        raw_calls = []
        original_call = ServiceClientRuntime.call

        def record_call(self, *args, **kwargs):
            raw_calls.append(args[1])
            return original_call(self, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(ServiceClientRuntime, "call", record_call)
            current = client.validate_copy_issued(issued).page
        assert raw_calls == []  # Token checks are owner-local, not a read or retry.
        assert client.validate_copy_issued(replace(issued)).outcome == "UNKNOWN"
        attempts = []

        def timed_out(*args, **kwargs):
            attempts.append(None)
            raise ThreadedRuntimeError("TIMEOUT_BEFORE_START")

        with monkeypatch.context() as patch:
            patch.setattr(runtime, "call_bound", timed_out)
            assert client.validate_copy_issued(issued).outcome == "UNKNOWN"
        assert len(attempts) == 1
        with pytest.raises(TypeError):
            current["header"]["query_row_count"] = 99
        _, _, _, _, original_args, original_observation, token, _, _ = client._issued[-1]
        with monkeypatch.context() as patch:
            patch.setattr(ServiceClientRuntime, "call", record_call)
            with pytest.raises(ThreadedRuntimeError):
                runtime.call_bound(
                    "upstream.account",
                    "account.order_cut.query_page.v1",
                    original_args,
                    route=route,
                    expected_binding=original_observation,
                    _expected_token=token,
                    expected_server_key_fingerprint=hashlib.sha256(server_public).hexdigest(),
                    _validate_order_cut_only=True,
                )
        assert raw_calls == []  # A token for a different operation cannot trigger transport.
        assert current["header"]["query_row_count"] == 2
        assert "private-source" not in repr(current) and "private-session" not in repr(current)
        q1 = client.validate_copy_issued(
            client.query_page(cut_ref=current["cut_ref"], after_ordinal=0, limit=1)
        ).page
        assert q1["next_ordinal"] == 1 and q1["has_more"] and q1["items"][0]["ordinal"] == 1
        q2 = client.validate_copy_issued(
            client.query_page(
                cut_ref=current["cut_ref"],
                after_ordinal=1,
                limit=1,
                expected_manifest_digest=q1["manifest_digest"],
            )
        ).page
        assert q2["next_ordinal"] == 2 and not q2["has_more"]
        handler = GatewayOrderCutService.handle

        def short_budget(service, operation, context, arguments):
            if operation == "account.order_cut.query_page.v1":
                real_fits = context.result_fits
                context = replace(
                    context,
                    result_fits=lambda value: len(value.get("items", ())) <= 1 and real_fits(value),
                )
            return handler(service, operation, context, arguments)

        with monkeypatch.context() as patch:
            patch.setattr(GatewayOrderCutService, "handle", short_budget)
            short = client.validate_copy_issued(
                client.query_page(cut_ref=current["cut_ref"], after_ordinal=0, limit=2)
            ).page
        assert short["next_ordinal"] == 1 and len(short["items"]) == 1 and short["has_more"]
        marker_calls = []

        def marker(*args, **kwargs):
            marker_calls.append((args, kwargs))
            raise AssertionError("pure cut read must not write a migration cursor marker")

        with monkeypatch.context() as patch:
            patch.setattr(ServiceRegistry, "_record_migration_write_attempt", marker)
            assert client.current().outcome == "OBSERVED"
            assert (
                client.query_page(cut_ref=current["cut_ref"], after_ordinal=0, limit=1).outcome
                == "OBSERVED"
            )
            assert (
                client.receipt_page(
                    cut_ref=current["cut_ref"], after_source_sequence=0, limit=1
                ).outcome
                == "OBSERVED"
            )
        assert marker_calls == []
        receipts = client.validate_copy_issued(
            client.receipt_page(cut_ref=current["cut_ref"], after_source_sequence=0, limit=2)
        ).page
        assert len(receipts["items"]) == 1 and receipts["items"][0]["source_sequence"] == 1
        assert receipts["items"][0]["fact_digest"] == q1["items"][0]["fact_digest"]
        assert client.query_page(cut_ref="0" * 64, after_ordinal=0, limit=1).outcome == "UNKNOWN"
        assert client.current().outcome == "OBSERVED"
        assert (
            client.query_page(
                cut_ref=current["cut_ref"],
                after_ordinal=1,
                limit=1,
                expected_manifest_digest="0" * 64,
            ).outcome
            == "UNKNOWN"
        )
        source = started[1]["source"]
        original_cut = source._fake_cut
        original_manifest = GatewayDirectCommandJournal.read_observed_order_cut_manifest

        def change_after_manifest(journal, *args, **kwargs):
            page = original_manifest(journal, *args, **kwargs)
            source._fake_cut = None
            return page

        with monkeypatch.context() as patch:
            patch.setattr(
                GatewayDirectCommandJournal,
                "read_observed_order_cut_manifest",
                change_after_manifest,
            )
            assert client.current().outcome == "UNKNOWN"
        source._fake_cut = original_cut
        prior_issued = client.current()
        assert prior_issued.outcome == "OBSERVED"
        source._fake_cut = None
        try:
            assert client.current().outcome == "UNAVAILABLE"
            assert client.validate_copy_issued(prior_issued).outcome == "UNKNOWN"
        finally:
            source._fake_cut = original_cut
        issued_before_renewal = client.current()
        assert issued_before_renewal.outcome == "OBSERVED"

        def renew_after_call(self, *args, **kwargs):
            result = original_call(self, *args, **kwargs)
            self._maintain(force=True)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(ServiceClientRuntime, "call", renew_after_call)
            runtime.call("upstream.account", "account.order_cut.current.v1", {}, timeout_seconds=5)
        assert client.validate_copy_issued(issued_before_renewal).outcome == "UNKNOWN"
        assert client.current().outcome == "OBSERVED"
        final_revision = started[2].submit_service_call(revision).result(timeout_seconds=5)
        assert final_revision == initial_revision
        pending = client.current()
        assert pending.outcome == "OBSERVED"
        runtime.close(timeout_seconds=10)
        assert client.validate_copy_issued(pending).outcome == "UNKNOWN"
        client.close()
    finally:
        if runtime is not None:
            runtime.close(timeout_seconds=10)
        stop.set()
        thread.join(15)
        assert not thread.is_alive()


def test_formal_trading_profile_source_through_strict_curve(tmp_path, monkeypatch):
    """Real profile, owner, coordinator and Host; only CTP native leaf is synthetic."""
    import importlib
    import time
    from pathlib import Path

    from bt_api_base.account_protocol import AccountKey
    from bt_api_base.service_protocol import WireIdentity, WireRole
    from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture

    from bt_api_py import ctp_gateway_return_maintenance as return_module
    from bt_api_py import ctp_gateway_trading as trading
    from bt_api_py import gateway_instrument_service as instrument_module
    from bt_api_py.ctp_gateway_order_cut_source import CtpGatewayOrderCutSettings
    from bt_api_py.ctp_gateway_trading import CtpGatewayReturnSettings
    from tests.test_iter63_ctp_gateway_trading import _read_profile

    real_loader = trading._load_ctp_types
    real_maintenance = return_module.CtpGatewayReturnMaintenance
    real_instrument = instrument_module.GatewayInstrumentService
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, grants, read = _read_profile(
        tmp_path, monkeypatch, account_key=key
    )
    monkeypatch.setattr(trading, "_load_ctp_types", real_loader)
    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", real_maintenance)
    monkeypatch.setattr(instrument_module, "GatewayInstrumentService", real_instrument)
    guard = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, settings.owner_id)
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal(
        settings.journal_path,
        settings.account_scope,
        generation=generation,
        expected_journal_id=settings.expected_journal_id,
        minimum_revision=0,
    )
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    journal.enable_account_extensions(guard)
    journal.enable_parent_authority(guard)
    journal.enable_account_facts_financial(guard)
    journal.enable_observed_order_source(guard)
    guard.release()
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["options"].update(
        cancel_enabled=True,
        control_enabled=True,
        strategy_ledger_enabled=True,
        account_extensions_enabled=True,
        account_facts_financial_enabled=True,
    )
    config = parse_service_config(raw)
    reader = WireIdentity(WireRole.SERVICE, "reader", "reader-service")
    scope = WireScope(DOMAIN, account_ref=settings.account_scope)
    grant = ResourceGrant("gateway-a", "service.account_read", "scope-a", scope, OPERATIONS)
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    authority = replace(
        authority,
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={**authority.principals, client_public: reader.principal_ref},
        grants={**authority.grants, reader.principal_ref: (PrincipalGrant(reader, (grant,)),)},
    )
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests")
    )
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    native_calls, natives = [], []

    class Native(helper._NativeApi):
        def ReqQryOrder(self, field, rid):
            from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

            native_calls.append("orders")
            if (
                getattr(self, "source", None) is not None
                and self.source._session._order_query_cut is not None
            ):
                row = _order(TradingDay=helper.TODAY)
                self.client._spi.OnRtnOrder(SimpleNamespace(**row))
                self.client._handle_query_callback("orders", row, None, rid, False)
            self.client._handle_query_callback("orders", None, None, rid, True)
            return 0

        def ReqQryTrade(self, field, rid):
            native_calls.append("trades")
            self.client._handle_query_callback("trades", None, None, rid, True)
            return 0

        def ReqQryInvestorPosition(self, field, rid):
            native_calls.append("positions")
            self.client._handle_query_callback("positions", None, None, rid, True)
            return 0

        def ReqQryTradingAccount(self, field, rid):
            native_calls.append("account")
            self.client._handle_query_callback(
                "account",
                SimpleNamespace(
                    CurrencyID="CNY",
                    Balance=1000.0,
                    Available=900.0,
                    CurrMargin=100.0,
                    BrokerID="9999",
                    AccountID="account",
                    TradingDay=helper.TODAY,
                ),
                None,
                rid,
                True,
            )
            return 0

    def start(trader, **kwargs):
        native = Native()
        native.client = trader
        trader._api = native
        natives.append(native)
        helper._login(trader, native, session_id=19)

    monkeypatch.setattr(TraderClient, "start", start)
    original_begin = CtpGatewayOrderCutSource.begin

    def mark_cut(source):
        natives[0].source = source
        return original_begin(source)

    monkeypatch.setattr(CtpGatewayOrderCutSource, "begin", mark_cut)
    monkeypatch.setattr(
        TraderClient,
        "stop_and_wait",
        lambda trader, **kw: CtpNativeStopReceipt(
            trader.connection_generation, False, True, True, False, False
        ),
    )

    def feed():
        return CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=key.account_key_id,
        )

    control = {
        CLIENT_A.principal_ref: GatewayControlGrant(
            CLIENT_A.actor_ref, True, frozenset(), frozenset({"direct.control.status"})
        )
    }
    profile = trading.build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "profile-registry",
        profile_ref="cut",
        settings=settings,
        grants=grants,
        request_feed_factory=feed,
        market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True,
        account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref, "reader"}),
        return_settings=CtpGatewayReturnSettings(
            read.observation_instruments, query_interval_seconds=0.05
        ),
        control_grants=control,
        order_cut_observation=CtpGatewayOrderCutSettings(20, 20, frozenset({"reader"})),
    )
    ready, stop = queue.Queue(), threading.Event()

    def serve():
        deployment = None
        try:
            deployment = profile.deployment_factory()
            state = {}
            provider = deployment._resource_provider

            def capture_resources(*args):
                resources = provider(*args)
                state["owner"] = resources.values["binding"]._owner
                state["source"] = resources.values["order_cut_source"]
                return resources

            deployment._resource_provider = capture_resources
            deployment.start()
            ready.put(("ready", state))
            while not stop.is_set():
                deployment.serve_once(10)
        except BaseException as error:
            ready.put(("error", type(error).__name__, str(error), traceback.format_exc()))
        finally:
            if deployment is not None:
                deployment.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    runtime = None
    try:
        started = ready.get(timeout=20)
        assert started[0] == "ready", started
        endpoint = config.communication["profiles"]["server-transport"]["endpoints"]["rpc_bind"]
        raw_client = _service_client(endpoint, reader, "upstream.account").to_public_dict()
        raw_client["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = (
            settings.account_scope
        )
        client_config = parse_service_config(raw_client)
        runtime = ThreadedServiceClientRuntime(
            lambda: ServiceClientRuntime(
                client_config,
                identity=reader,
                scope_resolver=lambda _: scope,
                peer_resolver=lambda _: SERVICE,
                transport_factory=ZmqTransportFactory(
                    {"security-a": ClientCurveKeys(server_public, client_public, client_secret)},
                    strict_single_connection=True,
                ),
                run_id="risk-run",
                requested_operations={"upstream.account": OPERATIONS},
            )
        )
        assert runtime.start(timeout_seconds=10).healthy
        client = GatewayOrderCutClient(
            runtime,
            route=BoundCallRoute(reader, scope, "scope-a", SERVICE.actor_ref, "gateway-a", SERVICE),
            fingerprint=hashlib.sha256(server_public).hexdigest(),
        )
        deadline = time.monotonic() + 10
        issued = client.current()
        while issued.outcome != "OBSERVED" and time.monotonic() < deadline:
            time.sleep(0.02)
            issued = client.current()
        assert issued.outcome == "OBSERVED", issued
        current = client.validate_copy_issued(issued).page
        assert current["header"]["query_row_count"] == 1
        query = client.validate_copy_issued(
            client.query_page(cut_ref=current["cut_ref"], after_ordinal=0, limit=2)
        ).page
        receipt = client.validate_copy_issued(
            client.receipt_page(
                cut_ref=current["cut_ref"],
                after_source_sequence=current["header"]["begin_sequence"],
                limit=2,
            )
        ).page
        assert (
            len(query["items"]) == 1 and query["items"][0]["ordinal"] == 1 and not query["has_more"]
        )
        assert len(receipt["items"]) == 1 and not receipt["has_more"]
        assert receipt["items"][0]["source_sequence"] == current["header"]["begin_sequence"] + 1
        assert query["items"][0]["fact_digest"] == receipt["items"][0]["fact_digest"]

        def original_pages():
            source = started[1]["source"]
            cut, binding = source.current(), source.current_binding()
            kwargs = {
                "source_ref": binding.source_ref,
                "session_ref": binding.generation,
                "begin_sequence": cut.begin_sequence,
                "end_sequence": cut.end_sequence,
                "query_digest": cut.query_digest,
            }
            return (
                source.journal.read_observed_order_cut_manifest(source.guard, **kwargs, limit=2),
                source.journal.read_observed_order_cut_receipts(source.guard, **kwargs, limit=2),
            )

        original_query, original_receipt = (
            started[1]["owner"].submit_service_call(original_pages).result(timeout_seconds=5)
        )
        assert query["items"][0]["fact_digest"] == original_query.items[0].fact_digest()
        assert receipt["items"][0]["fact_digest"] == original_receipt.items[0].fact_digest
        assert receipt["items"][0]["source_sequence"] == original_receipt.items[0].source_sequence
        assert receipt["items"][0]["fact_sequence"] == original_receipt.items[0].fact_sequence
        assert native_calls.count("orders") >= 1 and len(natives) == 1
        client.close()
    finally:
        if runtime is not None:
            runtime.close(timeout_seconds=10)
        stop.set()
        thread.join(15)
        assert not thread.is_alive()
