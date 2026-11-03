"""Observation assembly keeps account wire format explicit and scoped."""
from dataclasses import replace
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_py import service_config, service_market, service_runtime
from bt_api_py.service_observation_assembly import (
    ServiceObservationError, ServiceObservationInstrument, _build_service_observation_runtime,
)
from bt_api_py.service_observation_clients import open_service_observation_clients
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError
from tests.unit import test_service_account as fixture
from tests.unit import test_service_account_typed_rpc as typed_fixture
from tests.unit import test_service_market as market_fixture


class FakeRuntime(ServiceClientRuntime):
    instances = []
    capability = frozenset({'account.snapshot', 'account.snapshot.typed', 'account.instrument_metadata'})
    bad_map = False
    cleanup_failures = unregister_uncertainties = unknown_registration_count = 0

    def __init__(self, parsed, *, requested_operations, **kwargs):
        self.requested = requested_operations
        self.calls = []
        self._state = 'new'
        self.instances.append(self)

    @property
    def healthy(self):
        return self.state == 'registered'

    def start(self):
        if not self.requested['store.account'] <= self.capability:
            raise ServiceRuntimeError('CAPABILITY_UNAVAILABLE')
        self._state = 'registered'

    def maintain(self):
        pass

    def account_binding_view(self, mount, account_ref, *, required_operations):
        if not required_operations <= self.capability:
            raise ServiceRuntimeError('ACCOUNT_BINDING_UNAVAILABLE')
        return SimpleNamespace(mount=mount, service_id='service', instance_id='instance', scope_ref='account-scope',
                               account_ref=account_ref, registration_id='reg', service_epoch='epoch', rpc_scope='scope',
                               transport_ref='transport', operations=self.requested['store.account'])

    def call(self, mount, operation, arguments, *, account_ref, scope_ref):
        self.calls.append(operation)
        observation = replace(fixture._observation(), open_orders=(), open_orders_complete=True)
        if operation == 'account.snapshot.typed':
            reply = typed_fixture._typed_reply(observation)
            if self.bad_map:
                reply['snapshot']['payload']['observation']['data']['balances'] *= 257
            return reply
        if operation == 'account.snapshot':
            return fixture._reply(observation, cursor=0)
        raise AssertionError('unexpected account operation')

    def close(self):
        self._state = 'closed'


class FakeMarket:
    def __init__(self, *args, **kwargs):
        pass
    def close(self):
        pass


def service_fixture(snapshot_format=None):
    bindings = [
        {'mount': 'store.account', 'account_ref': fixture._ACCOUNT_REF, 'scope_ref': 'account-scope', 'service_id': 'service', 'transport_ref': 'transport'},
        {'mount': 'store.market', 'scope_ref': 'market-scope', 'service_id': 'service', 'transport_ref': 'transport'},
    ]
    public = {'registration': {'strategy_ref': 'strategy'},
              'clients': {'gateway': {'settings': {'bindings': bindings}}},
              'communication': {'profiles': {'transport': {'security_profile_ref': 'keys'}}}}
    account = {'mount': 'store.account', 'account_ref': fixture._ACCOUNT_REF,
               'key': fixture._ACCOUNT.to_dict(), 'required_coverage': [x.to_dict() for x in fixture._COVERAGE]}
    if snapshot_format is not None:
        account['snapshot_format'] = snapshot_format
    service = {'client_config': public, 'identity': {'principal_ref': 'principal', 'strategy_ref': 'strategy', 'execution_domain_ref': 'domain'},
               'service_principals': {'service': 'peer'},
               'curve_key_envs': {'keys': {'server_key_env': 'SERVER', 'client_public_key_env': 'PUBLIC', 'client_secret_key_env': 'SECRET'}},
               'account': account, 'market': {'mount': 'store.market', 'source_ref': market_fixture._KEY.source_ref,
                                             'entitlement_ref': market_fixture._KEY.entitlement_ref}}
    return service, {'SERVER': 'A' * 40, 'PUBLIC': 'B' * 40, 'SECRET': 'C' * 40}


@pytest.fixture
def fake_components(monkeypatch):
    FakeRuntime.instances.clear()
    FakeRuntime.capability = frozenset({'account.snapshot', 'account.snapshot.typed', 'account.instrument_metadata'})
    FakeRuntime.bad_map = False
    monkeypatch.setattr(service_config, 'parse_client_config', lambda raw: SimpleNamespace(to_public_dict=lambda: raw))
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', FakeRuntime)
    monkeypatch.setattr(service_market, 'ServiceMarketClient', FakeMarket)


@pytest.mark.parametrize('fmt,metadata,expected', [
    # Typed is the default since 60.08 (G5); generic is an explicit selection.
    (None, False, {'account.snapshot.typed'}),
    ('generic', True, {'account.snapshot', 'account.instrument_metadata'}),
    ('typed', False, {'account.snapshot.typed'}), ('typed', True, {'account.snapshot.typed', 'account.instrument_metadata'}),
])
def test_selection_controls_exact_requested_operations(fake_components, fmt, metadata, expected):
    service, env = service_fixture(fmt)
    runtime, *_ = _build_service_observation_runtime(service, env, require_instrument_metadata=metadata)
    assert runtime.requested['store.account'] == expected
    assert runtime.requested['store.market']


@pytest.mark.parametrize('bad', [None, True, 1, 'TYPED', 'typed '])
def test_invalid_format_is_rejected_before_runtime(fake_components, bad):
    service, env = service_fixture()
    service['account']['snapshot_format'] = bad
    with pytest.raises(ServiceObservationError, match='SERVICE_ACCOUNT_SNAPSHOT_FORMAT_INVALID'):
        _build_service_observation_runtime(service, env, require_instrument_metadata=False)
    assert not FakeRuntime.instances


def test_unknown_account_field_is_rejected(fake_components):
    service, env = service_fixture()
    service['account']['unexpected'] = True
    with pytest.raises(ServiceObservationError, match='SERVICE_ACCOUNT_CONFIG_INVALID'):
        _build_service_observation_runtime(service, env, require_instrument_metadata=False)
    assert not FakeRuntime.instances


def test_sdk_observation_uses_typed_and_refuses_missing_capability_or_oversized_map(fake_components):
    service, env = service_fixture('typed')
    instruments = (ServiceObservationInstrument('sub', market_key=market_fixture._KEY),)
    clients = open_service_observation_clients(service, instruments=instruments, environ=env)
    assert clients.runtime.calls == ['account.snapshot.typed']
    clients.close()
    FakeRuntime.capability = frozenset({'account.snapshot'})
    with pytest.raises(ServiceObservationError, match='SERVICE_SESSION_START_FAILED'):
        open_service_observation_clients(service, instruments=instruments, environ=env)
    assert FakeRuntime.instances[-1].state == 'closed'
    assert FakeRuntime.instances[-1].calls == []
    FakeRuntime.capability = frozenset({'account.snapshot', 'account.snapshot.typed'})
    FakeRuntime.bad_map = True
    with pytest.raises(ServiceObservationError, match='SERVICE_SESSION_START_FAILED'):
        open_service_observation_clients(service, instruments=instruments, environ=env)
    assert FakeRuntime.instances[-1].calls == ['account.snapshot.typed']
    assert FakeRuntime.instances[-1].state == 'closed'


def test_backtrader_session_through_real_sdk_assembly_fails_closed(fake_components):
    # Load the sibling checkout directly: no installed Backtrader assumption.
    bt_source = Path(__file__).resolve().parents[3] / 'backtrader/backtrader/service/service_observation.py'
    if not bt_source.is_file():
        pytest.skip('sibling Backtrader source checkout unavailable')
    spec = importlib.util.spec_from_file_location('test_bt_observation_bridge', bt_source)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    service, env = service_fixture('typed')
    instruments = (ServiceObservationInstrument('sub', market_key=market_fixture._KEY),)
    session = bridge.open_service_read_session(service, instruments=instruments, environ=env)
    assert session.runtime.calls == ['account.snapshot.typed']
    assert session.account_view.observation.account_key == fixture._ACCOUNT
    session.close()
    generic, _ = service_fixture('generic')
    generic['account'].update(valuation_currency='USD', position_mode='net',
                              resource_by_leg={'call': {'product_type': 'spot', 'margin_pool': 'cash'}},
                              qty_unit='contracts')
    generic_session = bridge.open_service_read_session(generic, instruments=instruments, environ=env)
    assert generic_session.runtime.calls == ['account.snapshot']
    generic_session.close()
    FakeRuntime.capability = frozenset({'account.snapshot'})
    with pytest.raises(ServiceObservationError, match='SERVICE_SESSION_START_FAILED'):
        bridge.open_service_read_session(service, instruments=instruments, environ=env)
    assert FakeRuntime.instances[-1].state == 'closed'
    assert FakeRuntime.instances[-1].calls == []
    service['account']['snapshot_format'] = 'bad'
    with pytest.raises(ServiceObservationError, match='SERVICE_ACCOUNT_SNAPSHOT_FORMAT_INVALID'):
        bridge.open_service_read_session(service, instruments=instruments, environ=env)

    for field, value in (
        ('snapshop_format', 'typed'), ('unknown', True), ('snapshot_format', True),
        ('position_mode', []), ('valuation_currency', 1), ('qty_unit', None),
        ('resource_by_leg', []),
    ):
        candidate, env = service_fixture()
        candidate['account'][field] = value
        count = len(FakeRuntime.instances)
        with pytest.raises(ServiceObservationError):
            bridge.open_service_read_session(candidate, instruments=instruments, environ=env)
        assert len(FakeRuntime.instances) == count
