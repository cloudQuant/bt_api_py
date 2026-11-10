"""R20 strategy-side composition with isolated synthetic service facts.

These tests exercise public Gateway session/Registry transports, not a real
account, venue or Backtrader position projection.
"""

import queue
import threading

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from iter63_prepared_support import compose_claimed_dispatcher

from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ZmqTransportFactory
from bt_api_py.service_runtime_thread import OwnerOperationError, ThreadedRuntimeError
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    CLIENT_B,
    DOMAIN,
    OPS,
    SERVICE,
)
from tests.integration.test_iter63_gateway_dual_ports import _dual_client, _dual_config, _resources
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_direct_client import MemoryHost, _request

ACCOUNT_OPS = frozenset({"account.snapshot.typed"})


@pytest.fixture
def dual_service(tmp_path):
    endpoint = _free_endpoint()
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    started, stop = queue.Queue(), threading.Event()
    flags = {"allow_account": True}
    shared = {}
    threads = []

    def serve():
        host = None
        try:
            binding, read, model = _resources(tmp_path)
            shared.update(binding=binding, model=model)
            scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref)
            resources = [ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, OPS)]
            if flags["allow_account"]:
                resources.append(
                    ResourceGrant(
                        "gateway-a", "service.account_read", "scope-a", scope, ACCOUNT_OPS
                    )
                )
            grant = PrincipalGrant(CLIENT_A, tuple(resources))
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
                curve_public_key=server_keys[0],
                curve_secret_key=server_keys[1],
                principals={client_keys[0]: CLIENT_A.principal_ref},
                grants={CLIENT_A.principal_ref: (grant,)},
            )
            host = ServiceHost(
                _dual_config(endpoint),
                authority,
                tmp_path / "registry.sqlite",
                lambda *_: ComponentResources.borrowed(
                    {"binding": binding, "read_service": read}, ready=lambda: True
                ),
            )
            host.start()
            started.put(("ready", None))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as error:
            import traceback

            started.put(("error", type(error).__name__, traceback.format_exc()))
        finally:
            if host is not None and host.state == "started":
                host.close()

    def start(*, allow_account=True):
        flags["allow_account"] = allow_account
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        threads.append(thread)
        assert started.get(timeout=10) == ("ready", None)
        return thread

    try:
        yield endpoint, server_keys[0], client_keys, shared, start
    finally:
        stop.set()
        for thread in threads:
            thread.join(10)
            assert not thread.is_alive()


def _settings():
    return GatewayAccountClientSettings(
        expected_account=AccountKey("synthetic", "venue", "test", ACCOUNT),
        required_coverage=(AccountResourceKey("future", "main"),),
        requested_operations=ACCOUNT_OPS,
    )


def _make_session(service, settings):
    endpoint, server_public, client_keys, shared, _ = service
    identity = CLIENT_A
    route = GatewayClientRoute(
        "broker.direct_write",
        SERVICE.actor_ref,
        "gateway-a",
        "scope-a",
        WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref, run_id="run-a"),
        identity,
    )
    binding = GatewayCommandBinding(
        shared["binding"]._journal.journal_id, ACCOUNT, identity.actor_ref, identity.principal_ref
    )
    keys = ClientCurveKeys(server_public, *client_keys)
    session = GatewayStrategyClientSession(
        _dual_client(endpoint, identity),
        binding=binding,
        route=route,
        peer=SERVICE,
        security_profiles={"security-a": keys},
        host=MemoryHost(),
        requested_operations=OPS,
        account_settings=settings,
    )
    return session, keys


def test_real_curve_account_and_command_share_owner_and_freeze_inputs(dual_service, monkeypatch):
    endpoint, _, _, _, start = dual_service
    del endpoint
    start()
    tids = []
    factory = ZmqTransportFactory.__call__

    class Trace:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def request(self, request, *, timeout):
            tids.append(threading.get_ident())
            return self.wrapped.request(request, timeout=timeout)

        def close(self):
            self.wrapped.close()

    monkeypatch.setattr(
        ZmqTransportFactory, "__call__", lambda self, *args: Trace(factory(self, *args))
    )
    settings = _settings()
    session, keys = _make_session(dual_service, settings)
    assert tids == [] and session.status().state == "new"
    object.__setattr__(settings.expected_account, "account_id", "changed-after-construction")
    object.__setattr__(keys, "server_key", b"x" * 40)
    try:
        snapshot = session.start(timeout_seconds=10)
        mounts = {binding.mount: binding.operations for binding in snapshot.bindings}
        assert mounts == {"broker.direct_write": OPS, "store.account": ACCOUNT_OPS}
        assert session.market_client is None and session.account_client is not None
        view = session.account_client.refresh(timeout_seconds=5)
        assert view.observation.account_key.account_id == ACCOUNT
        assert session.account_client.snapshot(timeout_seconds=5).snapshot_id == view.snapshot_id
        assert session.command_client.reserve(_request(), timeout_seconds=5).outcome == "OBSERVED"
        assert tids and len(set(tids)) == 1 and tids[0] != threading.get_ident()
    finally:
        closed = session.close(timeout_seconds=10)
        assert closed.state == "closed" and closed.thread_alive is False


def test_missing_account_grant_fails_closed_before_client_use(dual_service):
    _, _, _, _, start = dual_service
    start(allow_account=False)
    session, _ = _make_session(dual_service, _settings())
    try:
        with pytest.raises(ThreadedRuntimeError, match="START_FAILED"):
            session.start(timeout_seconds=10)
        assert session.status().healthy is False
    finally:
        snapshot = session.close(timeout_seconds=10)
        assert not snapshot.healthy and not snapshot.thread_alive


def test_composite_cleanup_attempts_both_and_retries_only_local_failure(monkeypatch):
    import bt_api_py.gateway_strategy_account as account_module
    import bt_api_py.gateway_strategy_market as market_module
    from bt_api_py.gateway_strategy_client import _StrategyOwnerExtension

    calls = []

    class Account:
        cleanup_uncertainties = 1

        def __init__(self, *args, **kwargs):
            pass

        def start(self, raw):
            calls.append("account.start")

        def close(self):
            calls.append("account.close")

    class Market:
        cleanup_uncertainties = 2

        def __init__(self, *args, **kwargs):
            self.n = 0

        def start(self, raw):
            calls.append("market.start")

        def close(self):
            calls.append("market.close")
            self.n += 1
            if self.n == 1:
                raise RuntimeError("synthetic local close failure")

    monkeypatch.setattr(account_module, "GatewayAccountOwner", Account)
    monkeypatch.setattr(market_module, "GatewayMarketOwner", Market)
    extension = _StrategyOwnerExtension(
        object(),
        object(),
        instrument_settings=None,
        account_ref=ACCOUNT,
        scope_ref="scope-a",
        stream_scope=WireScope(DOMAIN),
        security_profiles={},
    )
    extension.start(object())
    with pytest.raises(OwnerOperationError, match="OWNER_CLEANUP_FAILED"):
        extension.close()
    assert calls == ["account.start", "market.start", "market.close", "account.close"]
    assert extension.cleanup_uncertainties == 3
    extension.close()
    assert calls.count("account.close") == 1 and calls.count("market.close") == 2
    assert extension.cleanup_uncertainties == 3


@pytest.fixture
def triple_service(tmp_path):
    from bt_api_gateway.market_hub import GatewayMarketHub

    from bt_api_py.service_config import parse_service_config
    from tests.integration.test_iter60_market_client_revocation_host import _KEY, _SyntheticProvider

    endpoint, market_endpoint = _free_endpoint(), _free_endpoint()
    server_keys, client_keys, b_keys, c_keys = (zmq.curve_keypair() for _ in range(4))
    client_c = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-c", "strategy-c")
    started, controls, stop = queue.Queue(), queue.Queue(), threading.Event()
    shared, threads = {"b_keys": b_keys, "tmp_path": tmp_path}, []

    def serve(allow_market, enable_cancel):
        host = hub = direct_owner = None
        try:
            binding, read, model = _resources(tmp_path)
            if enable_cancel:
                from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
                from bt_api_gateway.session_guard import LocalAccountSessionGuard

                from bt_api_py.direct_write import DirectWriteCoordinator
                from bt_api_py.gateway_direct_dispatch import (
                    compose_gateway_direct_cancel_dispatcher,
                )
                from bt_api_py.gateway_direct_owner import (
                    GatewayDirectOwner,
                    GatewayDirectOwnerResources,
                )
                from bt_api_py.gateway_direct_service import (
                    GatewayDirectGrant,
                    GatewayDirectServiceBinding,
                )
                from bt_api_py.write_admission import WriteAdmission
                from tests.integration.test_iter63_gateway_cancel_rpc import (
                    ROUTE,
                    SyntheticProvider,
                )

                (tmp_path / "cancel-guard").mkdir()
                owned = []

                def owner_factory():
                    guard = LocalAccountSessionGuard(
                        tmp_path / "cancel-guard", ACCOUNT, "synthetic-owner"
                    )
                    journal = GatewayDirectCommandJournal.create(
                        tmp_path / "cancel.sqlite", ACCOUNT, generation=guard.acquire()
                    )
                    cancel_provider = SyntheticProvider(tmp_path / "cancel.sqlite")
                    coordinator = DirectWriteCoordinator(
                        WriteAdmission({ROUTE: True}), lambda _: cancel_provider
                    )
                    dispatcher = compose_claimed_dispatcher(
                        coordinator,
                        ROUTE,
                        journal,
                        guard,
                        lambda _: True,
                        expected_local_provider=cancel_provider,
                    )
                    cancel_dispatcher = compose_gateway_direct_cancel_dispatcher(
                        coordinator,
                        ROUTE,
                        journal,
                        guard,
                        dispatcher,
                        lambda _: True,
                        expected_local_provider=cancel_provider,
                    )
                    owned.append((journal, cancel_provider))
                    return GatewayDirectOwnerResources(
                        dispatcher, journal, guard.release, cancel_dispatcher=cancel_dispatcher
                    )

                direct_owner = GatewayDirectOwner(
                    owner_factory, queue_size=4, max_queue_wait_seconds=10, busy_timeout_seconds=10
                )
                assert direct_owner.start(10)
                direct_journal, cancel_provider = owned[0]
                binding = GatewayDirectServiceBinding(
                    direct_journal,
                    {
                        CLIENT_A.principal_ref: GatewayDirectGrant(
                            CLIENT_A.actor_ref, frozenset({"rb"})
                        ),
                        CLIENT_B.principal_ref: GatewayDirectGrant(
                            CLIENT_B.actor_ref, frozenset({"rb"})
                        ),
                    },
                    owner=direct_owner,
                    cancel_enabled=True,
                )
                shared["cancel_provider"] = cancel_provider

            class MarketProvider(_SyntheticProvider):
                fail_release = False

                def unsubscribe(self, handle):
                    if self.fail_release:
                        return False
                    return super().unsubscribe(handle)

            provider = MarketProvider()
            allowed = {
                (CLIENT_A.principal_ref if allow_market else client_c.principal_ref): frozenset(
                    {_KEY}
                )
            }
            hub = GatewayMarketHub(
                provider,
                key_universe=frozenset({_KEY}),
                principal_allowed_keys=allowed,
                service_identity=SERVICE,
                service_scope=WireScope(DOMAIN),
            )
            provider.hub = hub
            shared.update(binding=binding, model=model, hub=hub, provider=provider)
            scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref)
            direct_ops = (
                OPS | frozenset({"direct.dispatch", "direct.cancel", "direct.cancel_status"})
                if enable_cancel
                else OPS
            )
            resources = [
                ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, direct_ops),
                ResourceGrant("gateway-a", "service.account_read", "scope-a", scope, ACCOUNT_OPS),
            ]
            if allow_market:
                resources.append(
                    ResourceGrant(
                        "gateway-a",
                        "service.market_source",
                        "scope-a",
                        scope,
                        frozenset(
                            {
                                "market.subscribe",
                                "market.unsubscribe",
                                "market.snapshot",
                                "market.head",
                                "market.read_after",
                            }
                        ),
                    )
                )
            grant = PrincipalGrant(CLIENT_A, tuple(resources))
            b_scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_B.actor_ref)
            b_grant = PrincipalGrant(
                CLIENT_B,
                (
                    ResourceGrant(
                        "gateway-a",
                        "service.direct_write",
                        "scope-a",
                        b_scope,
                        OPS,
                    ),
                ),
            )
            principals = {client_keys[0]: CLIENT_A.principal_ref, b_keys[0]: CLIENT_B.principal_ref}
            grant_map = {CLIENT_A.principal_ref: (grant,), CLIENT_B.principal_ref: (b_grant,)}
            if not allow_market:
                c_scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=client_c.actor_ref)
                market_ops = frozenset(
                    {
                        "market.subscribe",
                        "market.unsubscribe",
                        "market.snapshot",
                        "market.head",
                        "market.read_after",
                    }
                )
                c_grant = PrincipalGrant(
                    client_c,
                    (
                        ResourceGrant(
                            "gateway-a", "service.market_source", "scope-a", c_scope, market_ops
                        ),
                    ),
                )
                principals[c_keys[0]] = client_c.principal_ref
                grant_map[client_c.principal_ref] = (c_grant,)
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
                curve_public_key=server_keys[0],
                curve_secret_key=server_keys[1],
                principals=principals,
                grants=grant_map,
            )
            raw = _dual_config(endpoint).to_public_dict()
            raw["components"]["gateway"]["settings"]["bind_ports"].append("service.market_source")
            if enable_cancel:
                raw["components"]["gateway"]["settings"]["options"].update(
                    dispatch_enabled=True, cancel_enabled=True
                )
            raw["communication"]["profiles"]["server-transport"]["endpoints"]["market_bind"] = (
                market_endpoint
            )
            host = ServiceHost(
                parse_service_config(raw),
                authority,
                tmp_path / "registry.sqlite",
                lambda *_: ComponentResources.borrowed(
                    {"binding": binding, "read_service": read, "hub": hub}, ready=lambda: True
                ),
            )
            host.start()
            started.put(("ready", None))
            while not stop.is_set():
                try:
                    action, reply = controls.get_nowait()
                except queue.Empty:
                    action = None
                if action == "publish":
                    try:
                        reply.put(("published", provider.publish("test-tick")))
                    except BaseException as error:
                        reply.put(("error", type(error).__name__))
                elif action == "fail_release":
                    provider.fail_release = True
                    reply.put("ok")
                elif action == "restore_release":
                    provider.fail_release = False
                    reply.put("ok")
                elif action == "market_status":
                    reply.put(dict(hub.readiness))
                host.serve_once(10)
        except BaseException as error:
            import traceback

            started.put(("error", type(error).__name__, traceback.format_exc()))
        finally:
            if host is not None and host.state == "started":
                host.close()
            if hub is not None:
                hub.close()
            if direct_owner is not None:
                assert direct_owner.close(10)

    def start(*, allow_market=True, enable_cancel=False):
        thread = threading.Thread(target=serve, args=(allow_market, enable_cancel), daemon=True)
        thread.start()
        threads.append(thread)
        response = started.get(timeout=10)
        assert response == ("ready", None), response

    try:
        yield endpoint, market_endpoint, server_keys[0], client_keys, shared, controls, start, _KEY
    finally:
        stop.set()
        for thread in threads:
            thread.join(10)
            assert not thread.is_alive()


def _triple_session(service, *, with_account=True, enable_cancel=False):
    from bt_api_py.gateway_strategy_market import (
        GatewayMarketClientSettings,
        GatewayMarketSubscriptionSettings,
    )
    from bt_api_py.service_config import parse_client_config

    endpoint, market_endpoint, server_public, client_keys, shared, _, _, key = service
    raw = _dual_client(endpoint, CLIENT_A).to_public_dict()
    bindings = raw["clients"]["gateway"]["settings"]["bindings"]
    if not with_account:
        bindings[:] = [item for item in bindings if item["mount"] != "store.account"]
    market_binding = dict(bindings[0], mount="store.market")
    del market_binding["account_ref"]
    bindings.append(market_binding)
    raw["communication"]["profiles"]["client-transport"]["endpoints"]["market_connect"] = (
        market_endpoint
    )
    config = parse_client_config(raw)
    market_settings = GatewayMarketClientSettings(
        {"ticks": GatewayMarketSubscriptionSettings(key)},
        frozenset(
            {
                "market.subscribe",
                "market.unsubscribe",
                "market.snapshot",
                "market.head",
                "market.read_after",
            }
        ),
    )
    account_settings = _settings() if with_account else None
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id="run-a")
    route = GatewayClientRoute(
        "broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", scope, CLIENT_A
    )
    binding = GatewayCommandBinding(
        shared["binding"]._journal.journal_id, ACCOUNT, CLIENT_A.actor_ref, CLIENT_A.principal_ref
    )
    keys = ClientCurveKeys(server_public, *client_keys)
    cancel_host = None
    direct_ops = OPS
    if enable_cancel:
        from tests.integration.test_iter63_gateway_cancel_rpc import CancelHost

        cancel_host = CancelHost(shared["tmp_path"] / "client-cancel.sqlite")
        direct_ops = OPS | frozenset({"direct.dispatch", "direct.cancel", "direct.cancel_status"})
    session = GatewayStrategyClientSession(
        config,
        binding=binding,
        route=route,
        peer=SERVICE,
        security_profiles={"security-a": keys},
        host=MemoryHost(),
        cancel_host=cancel_host,
        requested_operations=direct_ops,
        account_settings=account_settings,
        market_settings=market_settings,
    )
    return session, account_settings, market_settings, keys


def test_three_public_ports_share_one_strategy_owner(triple_service, monkeypatch):
    import time

    import bt_api_py.gateway_strategy_account as account_module
    import bt_api_py.gateway_strategy_market as market_module
    from bt_api_py.service_market import ServiceMarketClient
    from bt_api_py.service_runtime import ServiceClientRuntime

    triple_service[6]()
    owner_tids = []
    raw_call = ServiceClientRuntime.call

    def trace_call(self, *args, **kwargs):
        owner_tids.append(threading.get_ident())
        return raw_call(self, *args, **kwargs)

    monkeypatch.setattr(ServiceClientRuntime, "call", trace_call)
    account_start, market_start, poll = (
        account_module.GatewayAccountOwner.start,
        market_module.GatewayMarketOwner.start,
        ServiceMarketClient.poll,
    )

    def start_account(self, raw):
        owner_tids.append(threading.get_ident())
        return account_start(self, raw)

    def start_market(self, raw):
        owner_tids.append(threading.get_ident())
        return market_start(self, raw)

    def poll_market(self, **kwargs):
        owner_tids.append(threading.get_ident())
        return poll(self, **kwargs)

    monkeypatch.setattr(account_module.GatewayAccountOwner, "start", start_account)
    monkeypatch.setattr(market_module.GatewayMarketOwner, "start", start_market)
    monkeypatch.setattr(ServiceMarketClient, "poll", poll_market)
    session, account_settings, market_settings, keys = _triple_session(triple_service)
    assert session.status().state == "new" and owner_tids == []
    object.__setattr__(account_settings.expected_account, "account_id", "mutated")
    object.__setattr__(
        next(iter(market_settings.subscriptions.values())).key.instrument,
        "native_symbol",
        "mutated",
    )
    object.__setattr__(keys, "server_key", b"x" * 40)
    try:
        snapshot = session.start(timeout_seconds=10)
        mounts = {item.mount: item.operations for item in snapshot.bindings}
        assert mounts == {
            "broker.direct_write": OPS,
            "store.account": ACCOUNT_OPS,
            "store.market": frozenset(
                {
                    "market.subscribe",
                    "market.unsubscribe",
                    "market.snapshot",
                    "market.head",
                    "market.read_after",
                }
            ),
        }
        assert all(
            item.scope.account_ref == ACCOUNT
            and item.scope.strategy_ref == CLIENT_A.actor_ref
            and item.scope.run_id == "run-a"
            for item in snapshot.bindings
        )
        assert (
            session.account_client.refresh(timeout_seconds=5).observation.account_key.account_id
            == ACCOUNT
        )
        assert session.command_client.reserve(_request(), timeout_seconds=5).outcome == "OBSERVED"
        market = session.market_client.subscription("ticks")
        assert triple_service[4]["provider"].starts == 0
        bootstrap = market.start(timeout_seconds=5)
        assert triple_service[4]["provider"].starts == 1
        assert bootstrap.stream_id == triple_service[7].stream_id
        reply = queue.Queue()
        triple_service[5].put(("publish", reply))
        assert reply.get(timeout=5)[0] == "published"
        deadline = time.monotonic() + 5
        event = None
        while event is None and time.monotonic() < deadline:
            event = market.poll(timeout_seconds=5)
        assert event is not None and event.payload["data"]["marker"] == "test-tick"
        assert event.header.scope == WireScope(DOMAIN)
        assert owner_tids and len(set(owner_tids)) == 1 and owner_tids[0] != threading.get_ident()
    finally:
        closed = session.close(timeout_seconds=10)
        assert closed.state == "closed" and not closed.thread_alive


def test_market_grant_missing_rejects_entire_combined_start(triple_service):
    triple_service[6](allow_market=False)
    session, _, _, _ = _triple_session(triple_service)
    try:
        with pytest.raises(ThreadedRuntimeError, match="START_FAILED"):
            session.start(timeout_seconds=10)
        assert not session.status().healthy
    finally:
        snapshot = session.close(timeout_seconds=10)
        assert not snapshot.healthy and not snapshot.thread_alive


@pytest.mark.parametrize("invalid", ["wrong_scope", "market_account_field"])
def test_market_config_scope_and_account_fields_fail_closed(invalid, monkeypatch):
    from bt_api_py.gateway_strategy_market import (
        GatewayMarketClientSettings,
        GatewayMarketSubscriptionSettings,
    )
    from bt_api_py.service_config import ServiceConfigError, parse_client_config
    from tests.integration.test_iter60_market_client_revocation_host import _KEY

    raw = _dual_client(_free_endpoint(), CLIENT_A).to_public_dict()
    bindings = raw["clients"]["gateway"]["settings"]["bindings"]
    market = dict(bindings[0], mount="store.market")
    if invalid == "wrong_scope":
        del market["account_ref"]
        market["scope_ref"] = "different-scope"
    bindings.append(market)
    raw["communication"]["profiles"]["client-transport"]["endpoints"]["market_connect"] = (
        _free_endpoint()
    )
    if invalid == "market_account_field":
        with pytest.raises(ServiceConfigError, match="FIELD_NOT_ALLOWED"):
            parse_client_config(raw)
        return
    config = parse_client_config(raw)
    settings = GatewayMarketClientSettings(
        {"ticks": GatewayMarketSubscriptionSettings(_KEY)},
        frozenset(
            {
                "market.subscribe",
                "market.unsubscribe",
                "market.snapshot",
                "market.head",
                "market.read_after",
            }
        ),
    )
    route = GatewayClientRoute(
        "broker.direct_write",
        SERVICE.actor_ref,
        "gateway-a",
        "scope-a",
        WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id="run-a"),
        CLIENT_A,
    )
    binding = GatewayCommandBinding(
        "synthetic-journal", ACCOUNT, CLIENT_A.actor_ref, CLIENT_A.principal_ref
    )
    server_public, _ = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()

    def no_transport(*args, **kwargs):
        pytest.fail("invalid config must not create a transport")

    monkeypatch.setattr(ZmqTransportFactory, "__call__", no_transport)
    with pytest.raises(ValueError, match="INVALID_CLIENT_CONFIGURATION"):
        GatewayStrategyClientSession(
            config,
            binding=binding,
            route=route,
            peer=SERVICE,
            security_profiles={
                "security-a": ClientCurveKeys(server_public, client_public, client_secret)
            },
            host=MemoryHost(),
            requested_operations=OPS,
            account_settings=_settings(),
            market_settings=settings,
        )


def test_partial_composite_start_retains_resources_for_cleanup(monkeypatch):
    import bt_api_py.gateway_strategy_account as account_module
    import bt_api_py.gateway_strategy_market as market_module
    from bt_api_py.gateway_strategy_client import _StrategyOwnerExtension

    calls = []

    class Account:
        cleanup_uncertainties = 0

        def __init__(self, *args, **kwargs):
            pass

        def start(self, raw):
            calls.append("account.start")

        def close(self):
            calls.append("account.close")

    class Market:
        cleanup_uncertainties = 0

        def __init__(self, *args, **kwargs):
            pass

        def start(self, raw):
            calls.append("market.start")
            raise RuntimeError("synthetic partial startup")

        def close(self):
            calls.append("market.close")

    monkeypatch.setattr(account_module, "GatewayAccountOwner", Account)
    monkeypatch.setattr(market_module, "GatewayMarketOwner", Market)
    extension = _StrategyOwnerExtension(
        object(),
        object(),
        instrument_settings=None,
        account_ref=ACCOUNT,
        scope_ref="scope-a",
        stream_scope=WireScope(DOMAIN),
        security_profiles={},
    )
    with pytest.raises(RuntimeError, match="synthetic partial startup"):
        extension.start(object())
    extension.close()
    assert calls == ["account.start", "market.start", "market.close", "account.close"]


def test_market_only_selection_does_not_register_account(triple_service):
    triple_service[6]()
    session, _, _, _ = _triple_session(triple_service, with_account=False)
    try:
        snapshot = session.start(timeout_seconds=10)
        assert {item.mount for item in snapshot.bindings} == {"broker.direct_write", "store.market"}
        assert session.account_client is None
        assert triple_service[4]["provider"].starts == 0
        assert (
            session.market_client.subscription("ticks").start(timeout_seconds=5).stream_id
            == triple_service[7].stream_id
        )
    finally:
        closed = session.close(timeout_seconds=10)
        assert closed.state == "closed" and not closed.thread_alive


def test_account_timeout_reuses_owner_result_then_revalidates(dual_service, monkeypatch):
    from bt_api_py.service_account import ServiceAccountClient

    dual_service[4]()
    entered, release = threading.Event(), threading.Event()
    refresh = ServiceAccountClient.refresh
    calls = []

    def delayed(self):
        view = refresh(self)
        calls.append(threading.get_ident())
        entered.set()
        if not release.wait(5):
            raise RuntimeError("synthetic timeout")
        return view

    monkeypatch.setattr(ServiceAccountClient, "refresh", delayed)
    session, _ = _make_session(dual_service, _settings())
    try:
        session.start(timeout_seconds=10)
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            session.account_client.refresh(timeout_seconds=0.2)
        assert entered.is_set()
        release.set()
        result = session.account_client.refresh(timeout_seconds=5)
        assert result.observation.account_key.account_id == ACCOUNT and len(calls) == 1
    finally:
        release.set()
        closed = session.close(timeout_seconds=10)
        assert closed.state == "closed" and not closed.thread_alive


def test_direct_only_curve_sub_cannot_receive_market(triple_service):
    """A receives both publications while valid direct-only B fails market auth."""
    import time

    from zmq.utils.monitor import recv_monitor_message

    from bt_api_py.service_config import parse_client_config
    from tests.integration.test_iter63_gateway_direct_service import _client_config

    triple_service[6]()
    session, _, _, _ = _triple_session(triple_service)
    endpoint, market_endpoint, server_key, _, shared, controls, _, _ = triple_service
    b_keys = shared["b_keys"]
    b_scope = WireScope(
        DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_B.actor_ref, run_id="run-b"
    )
    b_route = GatewayClientRoute(
        "broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", b_scope, CLIENT_B
    )
    b_binding = GatewayCommandBinding(
        shared["binding"]._journal.journal_id, ACCOUNT, CLIENT_B.actor_ref, CLIENT_B.principal_ref
    )
    b = GatewayStrategyClientSession(
        parse_client_config(_client_config(endpoint, CLIENT_B).to_public_dict()),
        binding=b_binding,
        route=b_route,
        peer=SERVICE,
        security_profiles={"security-a": ClientCurveKeys(server_key, *b_keys)},
        host=MemoryHost(),
        requested_operations=OPS,
    )
    context = zmq.Context()
    sub = context.socket(zmq.SUB)
    sub.linger = 0
    monitor = sub.get_monitor_socket(
        events=zmq.EVENT_HANDSHAKE_SUCCEEDED | zmq.EVENT_HANDSHAKE_FAILED_AUTH
    )
    sub.curve_serverkey, sub.curve_publickey, sub.curve_secretkey = server_key, *b_keys
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.connect(market_endpoint)
    try:
        b.start(timeout_seconds=10)
        assert (
            b.command_client.reserve(_request(key="b-key"), timeout_seconds=5).outcome == "OBSERVED"
        )
        session.start(timeout_seconds=10)
        market = session.market_client.subscription("ticks")
        market.start(timeout_seconds=5)
        assert monitor.poll(5000)
        event = recv_monitor_message(monitor)
        assert event["event"] == zmq.EVENT_HANDSHAKE_FAILED_AUTH
        time.sleep(0.25)
        received_a = []
        for _ in range(2):
            reply = queue.Queue()
            controls.put(("publish", reply))
            assert reply.get(timeout=5)[0] == "published"
            deadline = time.monotonic() + 3
            got = None
            while got is None and time.monotonic() < deadline:
                got = market.poll(timeout_seconds=5)
            assert got is not None and got.payload["data"]["marker"] == "test-tick"
            received_a.append(got.header.sequence)
            assert not sub.poll(500), "direct-only B received a market frame"
        assert received_a[1] > received_a[0]
    finally:
        sub.close()
        monitor.close()
        context.term()
        session.close(timeout_seconds=10)
        b.close(timeout_seconds=10)


def test_market_timeout_retains_one_dequeued_event_on_same_handle(triple_service, monkeypatch):
    import time

    from bt_api_py.service_market import ServiceMarketClient

    triple_service[6]()
    entered, release = threading.Event(), threading.Event()
    poll = ServiceMarketClient.poll
    deliveries = []

    def hold_dequeued(self, **kwargs):
        event = poll(self, **kwargs)
        if event is not None:
            deliveries.append(event.header.sequence)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic blocked caller")
        return event

    monkeypatch.setattr(ServiceMarketClient, "poll", hold_dequeued)
    session, _, _, _ = _triple_session(triple_service)
    try:
        session.start(timeout_seconds=10)
        market = session.market_client.subscription("ticks")
        market.start(timeout_seconds=5)
        reply = queue.Queue()
        triple_service[5].put(("publish", reply))
        assert reply.get(timeout=5)[0] == "published"
        deadline = time.monotonic() + 5
        timed_out = False
        while not timed_out and time.monotonic() < deadline:
            try:
                assert market.poll(timeout_seconds=0.2) is None
            except ThreadedRuntimeError as error:
                assert error.code == "OWNER_TIMEOUT_RUNNING"
                timed_out = True
        assert timed_out and entered.is_set()
        with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
            market.status(timeout_seconds=1)
        release.set()
        recovered = market.poll(timeout_seconds=5)
        assert recovered.payload["data"]["marker"] == "test-tick"
        assert deliveries == [recovered.header.sequence]
        assert market.poll(timeout_seconds=5) is None
    finally:
        release.set()
        closed = session.close(timeout_seconds=10)
        assert closed.state == "closed" and not closed.thread_alive


def test_pending_market_release_does_not_block_authorized_cancel(triple_service):
    import time

    from tests.integration.test_iter63_gateway_cancel_rpc import order

    triple_service[6](enable_cancel=True)
    session, _, _, _ = _triple_session(triple_service, enable_cancel=True)
    controls = triple_service[5]
    try:
        session.start(timeout_seconds=10)
        direct = session.command_client
        assert direct.reserve(order("pending-order"), timeout_seconds=5).outcome == "OBSERVED"
        assert direct.dispatch("pending-order", timeout_seconds=5).outcome == "QUEUED"
        deadline = time.monotonic() + 5
        while direct.status("pending-order", timeout_seconds=5).record.state != "SENT":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        session.market_client.subscription("ticks").start(timeout_seconds=5)
        reply = queue.Queue()
        controls.put(("fail_release", reply))
        assert reply.get(timeout=5) == "ok"
        # The client's own reference is dropped; the reply says the upstream
        # is not released yet instead of leaving the outcome unknown.
        released = session.runtime.call(
            "store.market",
            "market.unsubscribe",
            {"key": triple_service[7].to_dict()},
            timeout_seconds=5,
        )
        assert released["upstream_released"] is False
        status = queue.Queue()
        controls.put(("market_status", status))
        readiness = status.get(timeout=5)
        assert readiness["ready"] is False and readiness["pending_releases"] == 1
        denied = session.runtime.call(
            "store.market", "market.head", {"key": triple_service[7].to_dict()}, timeout_seconds=5
        )
        assert denied == {"status": "rejected", "code": "MARKET_NOT_READY"}
        assert (
            session.account_client.refresh(timeout_seconds=5).observation.account_key.account_id
            == ACCOUNT
        )
        assert (
            direct.cancel("pending-order", operation_id="cancel-pending", timeout_seconds=5).outcome
            == "QUEUED"
        )
        deadline = time.monotonic() + 5
        while direct.cancel_status("cancel-pending", timeout_seconds=5).record.state != "SENT":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert len(triple_service[4]["cancel_provider"].sent) == 1
    finally:
        reply = queue.Queue()
        controls.put(("restore_release", reply))
        assert reply.get(timeout=5) == "ok"
        closed = session.close(timeout_seconds=10)
        assert closed.state == "closed" and not closed.thread_alive
