"""Offline factory composition with a real CTP session/provider and SQLite."""
import threading
from dataclasses import replace

import pytest
from bt_api_ctp.gateway_session import CtpGatewaySession
from bt_api_gateway.recovery_observation_journal import RecoveryObservationJournal

import bt_api_py.ctp_gateway_trading as trading
from bt_api_py.gateway_recovery_observation import GatewayRecoveryObservationSettings
from tests.test_iter63_ctp_gateway_trading import FakeProvider, FakeSession, _resources, _wait
from tests.unit.test_iter63_ctp_gateway_market_profile import setup


def options(tmp_path, settings, **changes):
    values = {'journal_path': tmp_path / 'source.sqlite', 'scope_ref': settings.account_scope,
                  'source_ref': settings.market_source_ref, 'source_capacity': 4, 'page_limit': 4,
                  'max_events': 20, 'max_bytes': 100000, 'sqlite_timeout_seconds': .1,
                  'poll_interval_seconds': .01, 'owner_wait_seconds': .1, 'close_timeout_seconds': 1}
    values.update(changes)
    return GatewayRecoveryObservationSettings(**values)


def test_factory_routes_capacity_to_real_ctp_provider_and_persists_page(tmp_path, monkeypatch):
    build, _, settings, authority, _ = setup(tmp_path, monkeypatch)
    seen = []
    class Session(FakeSession):
        def __init__(self, key, feed, stream, **kwargs):
            super().__init__(key, feed, stream, **kwargs)
            self.real = CtpGatewaySession(key, feed, stream, **kwargs)
        @property
        def market_provider(self):
            assert threading.get_ident() == self.owner
            seen.append(threading.get_ident())
            return self.real._market
    monkeypatch.setattr(trading, '_load_ctp_types', lambda: (Session, FakeProvider))
    cfg = options(tmp_path, settings)
    resources = _resources(build(recovery_observation_settings=cfg), authority)
    try:
        status = resources.values['binding'].recovery_observation_status
        _wait(lambda: bool(seen) and status()['pending_pages'] == 0
              and (tmp_path / 'source.sqlite').exists())
        journal = RecoveryObservationJournal(cfg.journal_path, scope_ref=cfg.scope_ref,
                    max_events=cfg.max_events, max_bytes=cfg.max_bytes, timeout_seconds=.1)
        _wait(lambda: bool(journal.read_after(0, limit=10).records))
        assert seen[0] == FakeSession.instances[-1].owner
        assert FakeSession.instances[-1].kwargs['recovery_observation_capacity'] == 4
    finally:
        resources.close()


def test_scope_and_storage_mismatch_rejected_before_resources(tmp_path, monkeypatch):
    build, _, settings, _, calls = setup(tmp_path, monkeypatch)
    base = options(tmp_path, settings)
    for change in ({'scope_ref': 'wrong'}, {'source_ref': 'wrong'},
                   {'journal_path': settings.journal_path},
                   {'journal_path': settings.guard_root / 'source.sqlite'}):
        with pytest.raises(trading.CtpGatewayTradingError, match='INVALID_RECOVERY_OBSERVATION_SETTINGS'):
            build(recovery_observation_settings=replace(base, **change))
    assert not calls and not FakeSession.instances


def test_selected_start_failure_returns_retryable_partial_resources(tmp_path, monkeypatch):
    import bt_api_py.gateway_recovery_observation as observation
    from bt_api_py.service_components import ServiceResourceConstructionError
    build, _, settings, authority, _ = setup(tmp_path, monkeypatch)
    class Session(FakeSession):
        def __init__(self, key, feed, stream, **kwargs):
            super().__init__(key, feed, stream, **kwargs)
            self.real = CtpGatewaySession(key, feed, stream, **kwargs)
        @property
        def market_provider(self):
            return self.real._market
    class BrokenJournal:
        def __init__(self, *args, **kwargs):
            raise OSError('private path')
    monkeypatch.setattr(trading, '_load_ctp_types', lambda: (Session, FakeProvider))
    monkeypatch.setattr(observation, 'RecoveryObservationJournal', BrokenJournal)
    with pytest.raises(ServiceResourceConstructionError) as error:
        _resources(build(recovery_observation_settings=options(tmp_path, settings)), authority,
                   cleanup_on_error=False)
    assert 'private path' not in str(error.value)
    error.value.resources.close()
    assert FakeSession.instances[-1].events[-1][0] == 'close'


def test_factory_close_fences_before_waiting_for_same_pending_page(tmp_path, monkeypatch):
    import bt_api_py.gateway_recovery_observation as observation
    from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError
    build, _, settings, authority, _ = setup(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    class Session(FakeSession):
        def __init__(self, key, feed, stream, **kwargs):
            super().__init__(key, feed, stream, **kwargs)
            self.real = CtpGatewaySession(key, feed, stream, **kwargs)
        @property
        def market_provider(self):
            return self.real._market
    class SlowJournal(RecoveryObservationJournal):
        def append_page(self, *args):
            entered.set()
            assert release.wait(3)
            return super().append_page(*args)
    monkeypatch.setattr(trading, '_load_ctp_types', lambda: (Session, FakeProvider))
    monkeypatch.setattr(observation, 'RecoveryObservationJournal', SlowJournal)
    cfg = options(tmp_path, settings, close_timeout_seconds=.05)
    resources = _resources(build(recovery_observation_settings=cfg), authority)
    binding = resources.values['binding']
    try:
        assert entered.wait(2)
        old_snapshot = binding._owner._lifecycle_snapshot()
        with pytest.raises(trading.CtpGatewayTradingError, match='RECOVERY_OBSERVATION_CLOSE_UNCONFIRMED'):
            resources.close()
        assert not binding._owner._lifecycle_is_current(old_snapshot)
        with pytest.raises(GatewayOwnerServiceCallError, match='SERVICE_CALL_NOT_READY'):
            binding._owner.submit_service_call(lambda: True)
        assert binding.recovery_observation_status()['pending_pages'] == 1
        release.set()
        _wait(lambda: binding.recovery_observation_status()['state'] == 'closed')
        journal = RecoveryObservationJournal(cfg.journal_path, scope_ref=cfg.scope_ref,
                    max_events=cfg.max_events, max_bytes=cfg.max_bytes, timeout_seconds=.1)
        records = journal.read_after(0, limit=10).records
        assert [record.kind for record in records] == ['epoch', 'capture_stopped']
        assert records[-1].through_sequence == 0 and records[-1].continuity == 'unknown'
    finally:
        release.set()
        resources.close()


def test_unselected_profile_does_not_load_recorder_or_pass_capacity(tmp_path, monkeypatch):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        "import sys; import bt_api_py.ctp_gateway_trading; "
        "assert 'bt_api_py.gateway_recovery_observation' not in sys.modules; "
        "assert 'bt_api_base.market_recovery_observation' not in sys.modules"],
        check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    build, _, _, authority, _ = setup(tmp_path, monkeypatch)
    class Session(FakeSession):
        @property
        def market_provider(self):
            return object()
    monkeypatch.setattr(trading, '_load_ctp_types', lambda: (Session, FakeProvider))
    resources = _resources(build(), authority)
    try:
        assert 'recovery_observation_capacity' not in FakeSession.instances[-1].kwargs
        assert not hasattr(resources.values['binding'], 'recovery_observation_status')
    finally:
        resources.close()


def test_service_host_real_ctp_recovery_observations_are_persisted(tmp_path, monkeypatch):
    """Real Host/owner/provider/journal; only the native CTP transports are fake."""
    import time
    from dataclasses import replace
    from pathlib import Path

    import zmq
    from bt_api_base.account_protocol import AccountKey
    from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
    from bt_api_base.service_protocol import WireScope
    from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
    from bt_api_ctp.feeds import live_ctp_feed
    from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream, CtpRequestDataFuture
    from bt_api_ctp.market_provider import CtpQuoteV2MarketProvider
    from bt_api_ctp.trusted_flow import flow_binding, trusted_flow
    from zmq.utils.monitor import recv_monitor_message

    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
    from tests.integration.test_service_account_instrument_metadata import _free_endpoint
    from tests.test_iter63_ctp_gateway_trading import CLIENT_A, DOMAIN, make

    ctp_tests = Path(__file__).resolve().parents[2] / 'bt_api' / 'bt_api_ctp' / 'tests'
    monkeypatch.syspath_prepend(str(ctp_tests))
    from test_iter62_settlement_readback_on_write import _login, _NativeApi
    from test_market_provider import _SyntheticMdClient

    account = AccountKey('ctp', 'simnow', 'simnow', 'account', '9999')
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, account_key=account)
    settings = replace(settings, close_timeout_seconds=5, session_stop_timeout_seconds=1,
                       connect_timeout_seconds=2)
    raw = config.to_public_dict()
    raw['components']['gateway']['settings']['bind_ports'].append('service.market_source')
    raw['communication']['profiles']['server-transport']['endpoints']['market_bind'] = _free_endpoint()
    config = parse_service_config(raw)
    entry = authority.grants[CLIENT_A.principal_ref][0]
    market_grant = ResourceGrant('gateway-a', 'service.market_source', 'scope-a',
        WireScope(DOMAIN, account_ref=settings.account_scope, strategy_ref=CLIENT_A.actor_ref),
        frozenset({'market.subscribe'}))
    client_public, client_secret = zmq.curve_keypair()
    authority = replace(authority, principals={client_public: CLIENT_A.principal_ref},
                        grants={CLIENT_A.principal_ref: (
                            PrincipalGrant(entry.identity, (*entry.resources, market_grant)),)})
    key = MarketSubscriptionKey('source', 'entitlement',
        MarketInstrumentKey('ctp', 'CFFEX', 'simnow', 'future', 'IF2612', 'IF2612'), 'tick')
    market_settings = trading.CtpGatewayMarketSettings(frozenset({key}),
        {CLIENT_A.principal_ref: frozenset({key})})

    def start(trader, **_kwargs):
        native = _NativeApi()
        native.client = trader
        trader._api = native
        _login(trader, native, session_id=19)
    def stop(trader, *, timeout):
        return CtpNativeStopReceipt(trader.connection_generation, False, True, True, False, False)
    class FakeMd(_SyntheticMdClient):
        instances = []
        _gateway_md_dispatch_coordinator = None
        def __init__(self, *args, **kwargs):
            self.trusted_flow_binding = flow_binding(trusted_flow(kwargs['trusted_flow_root'],
                                                                  kwargs['flow_account_scope']))
            super().__init__(*args)
        def _bind_gateway_md_dispatch_coordinator(self, coordinator, *, account_scope, service_generation):
            assert coordinator.matches_owner(account_scope, service_generation)
            assert self._gateway_md_dispatch_coordinator is None
            self._gateway_md_dispatch_coordinator = coordinator
    monkeypatch.setattr(TraderClient, 'start', start)
    monkeypatch.setattr(TraderClient, 'stop_and_wait', stop)
    monkeypatch.setattr(live_ctp_feed.ctp_client, 'MdClient', FakeMd)
    from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
    monkeypatch.setattr(trading, '_load_ctp_types', lambda: (CtpGatewaySession, CtpDirectWriteProvider))
    params = {'broker_id': '9999', 'user_id': 'account', 'password': 'synthetic',
        'td_front': 'tcp://offline-neutral-td.invalid:30001', 'md_front': 'tcp://offline-neutral-md.invalid:30011',
        'ctp_env_profile': 'config_front_pair', 'ctp_environment': 'simnow',
        'trusted_flow_root': str(settings.trusted_flow_root), 'flow_account_scope': settings.account_scope}
    def feed():
        return CtpRequestDataFuture(None, auto_settlement_confirm=False, **params)
    def stream(_key, queue):
        return CtpMarketStream(queue, **params)
    cfg = options(tmp_path, settings, source_capacity=64, max_events=512,
                  max_bytes=2_000_000, close_timeout_seconds=2)
    profile = trading.build_ctp_gateway_trading_profile(config, authority, tmp_path / 'host-registry.sqlite',
        profile_ref='real-ctp-recovery', settings=settings, grants=grants, request_feed_factory=feed,
        market_stream_factory=stream, assert_admitted=lambda _: True, market_settings=market_settings,
        recovery_observation_settings=cfg)
    deployment = profile.deployment_factory()
    captured = []
    original = deployment._resource_provider
    def capture_resources(*args):
        resources = original(*args)
        captured.append(resources)
        return resources
    deployment._resource_provider = capture_resources
    binding = recorder = journal = result = None
    try:
        deployment.start()
        assert deployment.state == 'started'
        context = zmq.Context()
        client = context.socket(zmq.DEALER)
        monitor = client.get_monitor_socket(events=zmq.EVENT_HANDSHAKE_SUCCEEDED)
        try:
            client.curve_serverkey = authority.curve_public_key
            client.curve_publickey = client_public
            client.curve_secretkey = client_secret
            client.connect(raw['communication']['profiles']['server-transport']['endpoints']['rpc_bind'])
            assert monitor.poll(timeout=3000)
            assert recv_monitor_message(monitor)['event'] == zmq.EVENT_HANDSHAKE_SUCCEEDED
        finally:
            monitor.close(linger=0)
            client.disable_monitor()
            client.close(linger=0)
            context.term()
        binding = captured[0].values['binding']
        recorder = binding.recovery_observation_status.__self__
        owner = binding._owner
        # Acquire the exact provider on its owner thread. Subscribe and drive
        # recovery there; the native callbacks below are synthetic.
        capture = recorder._capture
        # The capture closure binds the production session; inspect its closed-over
        # objects only to obtain the original provider for this owner-only exercise.
        closure = dict(zip(capture.__code__.co_freevars, (cell.cell_contents for cell in capture.__closure__), strict=False))
        provider = closure['bound_provider']
        assert type(provider) is CtpQuoteV2MarketProvider
        handle = owner.submit_service_call(lambda: provider.subscribe(key, generation='integration-g1')).result(timeout_seconds=3)
        assert FakeMd.instances
        FakeMd.instances[0].emit_disconnect()
        def drive():
            provider.maintain()
            return provider.source_epoch_reset_required(handle)
        deadline = time.monotonic() + 5
        while not owner.submit_service_call(drive).result(timeout_seconds=2):
            assert time.monotonic() < deadline
            time.sleep(.01)
        journal = RecoveryObservationJournal(cfg.journal_path, scope_ref=cfg.scope_ref,
            max_events=cfg.max_events, max_bytes=cfg.max_bytes, timeout_seconds=.1)
        def observations():
            rows, cursor = [], 0
            while True:
                page = journal.read_after(cursor, limit=256)
                rows.extend(page.records)
                assert len(rows) <= cfg.max_events
                if not page.has_more:
                    return [row.observation for row in rows if row.kind == 'observation']
                assert page.next_cursor > cursor
                cursor = page.next_cursor
        def has_episode_watermark():
            rows = observations()
            begin = next((row for row in rows if row.reason == 'BEGIN'), None)
            return begin is not None and any(row.reason == 'WATERMARK'
                                             and row.episode_id == begin.episode_id for row in rows)
        _wait(has_episode_watermark)
        before_ack = observations()
        begin = next(row for row in before_ack if row.reason == 'BEGIN')
        assert any(row.reason == 'WATERMARK' and row.episode_id == begin.episode_id
                   for row in before_ack)
        assert owner.submit_service_call(lambda: provider.acknowledge_source_epoch_reset(handle, epoch='e2')).result(timeout_seconds=2)
        _wait(lambda: any(row.state == 'RECOVERED' for row in observations()))
        result = observations()
        assert result[0].reason == 'BEGIN'
        assert any(row.reason == 'WATERMARK' for row in result)
        assert result[-1].reason == 'COMPLETED'
        assert len({row.episode_id for row in result}) == 1
    finally:
        if deployment.state in {'started', 'close_failed'}:
            deployment.close()
        if recorder is not None:
            assert deployment.state == 'closed'
            assert recorder.status()['state'] == 'closed'
            assert recorder.status()['pending_calls'] == recorder.status()['pending_pages'] == 0
            assert recorder.status()['pending_stop_marker'] == 0
            assert not recorder._thread.is_alive()
            if journal is not None and result is not None:
                records, cursor = [], 0
                while True:
                    page = journal.read_after(cursor, limit=256)
                    records.extend(page.records)
                    assert len(records) <= cfg.max_events
                    if not page.has_more:
                        break
                    assert page.next_cursor > cursor
                    cursor = page.next_cursor
                assert records[-1].kind == 'capture_stopped'
                assert records[-1].continuity == 'unknown'
                assert records[-1].through_sequence == result[-1].sequence
