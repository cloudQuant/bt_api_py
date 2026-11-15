"""Synthetic, single-threaded source checks; no provider or account evidence."""
import os
import threading
from decimal import Decimal

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.ctp_gateway_local_profile import _strategy_origin_source_factory
from bt_api_py.gateway_strategy_origin_source import (
    GatewayStrategyOriginSource,
    GatewayStrategyOriginSourceError,
    snapshot_strategy_origin_instruments,
)
from bt_api_py.service_registry import ServiceCallContext


class FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class FakeDone:
    def is_set(self):
        return False


class FakeOwner:
    def __init__(self):
        self._pid = os.getpid()
        self._thread = threading.current_thread()
        self._condition = FakeLock()
        self._closing = False
        self._lifecycle_shutdown_requested = False
        self._done = FakeDone()
        self._binding = None
        self._lifecycle_epoch = 1


class FakeJournal:
    account_scope = 'account'
    journal_id = 'journal'
    generation = 'generation'

    def _assert_account_guard(self, guard):
        assert type(guard) is FakeGuard


class FakeGuard:
    pass


class FakeSession:
    pass


class FakeCoordinator:
    def __init__(self):
        self.version = 0
        self.state = 'READY'

    def matches_owner(self, account, generation):
        return (account, generation) == ('account', 'generation')

    def begin_change(self, domain):
        assert domain == 'session_source'
        self.version += 1
        self.state = 'CHANGING'
        return self.version

    def finish_change(self, token, status):
        assert token == self.version
        self.state = status

    def poison_domain(self, domain):
        assert domain == 'session_source'
        self.state = 'POISONED'


class FakeBinding:
    def assert_bound(self, owner, journal):
        if owner._closing or journal.generation != 'generation':
            raise RuntimeError


def _install_fakes(monkeypatch):
    import bt_api_ctp.gateway_session as session_module
    import bt_api_gateway.direct_command_journal as journal_module
    import bt_api_gateway.session_guard as guard_module

    import bt_api_py._gateway_owner_guard_binding as binding_module
    import bt_api_py.gateway_direct_owner as owner_module
    import bt_api_py.gateway_dispatch_claim as coordinator_module

    for module, name, replacement in (
        (journal_module, 'GatewayDirectCommandJournal', FakeJournal),
        (guard_module, 'LocalAccountSessionGuard', FakeGuard),
        (session_module, 'CtpGatewaySession', FakeSession),
        (binding_module, '_GatewayOwnerGuardBinding', FakeBinding),
        (owner_module, 'GatewayDirectOwner', FakeOwner),
        (coordinator_module, 'GatewayDispatchClaimCoordinator', FakeCoordinator),
    ):
        monkeypatch.setattr(module, name, replacement)


def _inputs():
    context = ServiceCallContext(WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy'),
        WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run'),
        'registration', 'principal')
    order = OrderRequest('rb', Side.BUY, OrderType.LIMIT, Decimal('1'), 'account', 'client',
                         price=Decimal('10'), exchange_id='SHFE')
    key = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
    return context, order, key


def _bound(monkeypatch, source):
    _install_fakes(monkeypatch)
    owner, journal, guard, session, coordinator = (FakeOwner(), FakeJournal(), FakeGuard(),
                                                    FakeSession(), FakeCoordinator())
    source._bind_owner(owner, journal, guard, session, coordinator, FakeBinding())
    owner._binding = object()
    return owner, journal, coordinator


def test_snapshot_is_copied_before_factory_and_returned_keys_are_detached(monkeypatch):
    context, order, original = _inputs()
    factory = _strategy_origin_source_factory((original,))
    object.__setattr__(original, 'product_type', 'option')
    source = factory(None, None, None)
    owner, journal, coordinator = _bound(monkeypatch, source)
    returned = source.resolve(context, order)
    assert returned.product_type == 'future'
    object.__setattr__(returned, 'product_type', 'option')
    assert not source.recheck(returned)
    assert source.resolve(context, order).product_type == 'future'
    assert source.bound_to(owner, journal, coordinator)
    with pytest.raises(AttributeError):
        source._resolve = lambda *_: returned


def test_callbacks_duplicate_keys_and_unbound_sources_are_refused():
    context, order, key = _inputs()
    with pytest.raises(TypeError):
        GatewayStrategyOriginSource(resolve=lambda *_: key, recheck=lambda *_: True)
    with pytest.raises(GatewayStrategyOriginSourceError):
        GatewayStrategyOriginSource(instruments=(key, key))
    with pytest.raises(GatewayStrategyOriginSourceError):
        GatewayStrategyOriginSource(instruments=(key,)).resolve(context, order)


@pytest.mark.parametrize('field', ['provider', 'environment', 'product_type', 'instrument_id'])
def test_factory_source_must_match_every_trusted_declaration_field(field):
    from dataclasses import replace

    _, _, key = _inputs()
    snapshot = snapshot_strategy_origin_instruments((key,))
    other = replace(key, **{field: 'different'})
    assert not GatewayStrategyOriginSource(instruments=(other,))._matches_snapshot(snapshot)


def test_source_close_changes_version_and_old_owner_cannot_reopen(monkeypatch):
    context, order, key = _inputs()
    source = GatewayStrategyOriginSource(instruments=(key,))
    owner, journal, coordinator = _bound(monkeypatch, source)
    before = coordinator.version
    source.close()
    assert coordinator.version != before and coordinator.state == 'UNAVAILABLE'
    assert not source.bound_to(owner, journal, coordinator)
    with pytest.raises(GatewayStrategyOriginSourceError):
        source.resolve(context, order)
    with pytest.raises(GatewayStrategyOriginSourceError):
        source._bind_owner(owner, journal, FakeGuard(), FakeSession(), coordinator, FakeBinding())


def test_epoch_change_or_owner_closing_invalidates_source(monkeypatch):
    context, order, key = _inputs()
    source = GatewayStrategyOriginSource(instruments=(key,))
    owner, journal, coordinator = _bound(monkeypatch, source)
    owner._lifecycle_epoch += 1
    assert not source.bound_to(owner, journal, coordinator)
    with pytest.raises(GatewayStrategyOriginSourceError):
        source.resolve(context, order)
    owner._lifecycle_epoch -= 1
    owner._closing = True
    assert not source.bound_to(owner, journal, coordinator)


def test_shutdown_requested_during_startup_cannot_bind(monkeypatch):
    _, _, key = _inputs()
    _install_fakes(monkeypatch)
    owner = FakeOwner()
    owner._lifecycle_shutdown_requested = True
    source = GatewayStrategyOriginSource(instruments=(key,))
    with pytest.raises(GatewayStrategyOriginSourceError):
        source._bind_owner(owner, FakeJournal(), FakeGuard(), FakeSession(), FakeCoordinator(), FakeBinding())


def test_failed_close_permanently_revokes_source_and_cleanup_can_retry(monkeypatch):
    context, order, key = _inputs()
    source = GatewayStrategyOriginSource(instruments=(key,))
    _, _, coordinator = _bound(monkeypatch, source)

    def fail(*_):
        raise RuntimeError('failure')

    coordinator.begin_change = fail
    poison = coordinator.poison_domain
    coordinator.poison_domain = fail
    with pytest.raises(RuntimeError, match='failure'):
        source.close()
    with pytest.raises(RuntimeError, match='failure'):
        source.close()
    with pytest.raises(GatewayStrategyOriginSourceError):
        source.resolve(context, order)
    coordinator.poison_domain = poison
    source.close()
    assert coordinator.state == 'POISONED'


@pytest.mark.parametrize('path', ['monitor', 'stream'])
def test_ctp_shutdown_fences_before_monitor_or_stream_failure(path):
    """Exercise the actual nested cleanup bodies with synchronous fake dependencies."""
    import ast
    import time
    from pathlib import Path
    from types import SimpleNamespace

    import bt_api_py.ctp_gateway_trading as trading

    module = ast.parse(Path(trading.__file__).read_text(encoding='utf-8'))
    bodies = {}
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name in {'close_resources', 'close_owner'}:
            node.body = [part for part in node.body if not isinstance(part, ast.Nonlocal)]
            bodies[node.name] = node
    assert set(bodies) == {'close_resources', 'close_owner'}
    events = []
    state = {'version': 0, 'available': True}
    validated_version = state['version']

    class Owner:
        def _fence_dispatch_shutdown(self):
            events.append('fence')
            state['version'] += 1
            state['available'] = False

        def close(self, _):
            pytest.fail('failure must precede owner.close')

    class Closing:
        def set(self):
            events.append('closing')
            assert not state['available']

    class Monitor:
        def close(self, _):
            events.append('monitor')
            return False

    class Stream:
        def begin_close(self):
            events.append('stream_begin')
            assert not state['available']

        def close(self, _):
            events.append('stream_close')
            return False

    holder = {'required_monitor': Monitor()} if path == 'monitor' else {'execution_stream': Stream()}
    namespace = {'owner': Owner(), 'closing': Closing(), 'holder': holder,
                 'settings': SimpleNamespace(close_timeout_seconds=2), 'time': time,
                 'CtpGatewayTradingError': trading.CtpGatewayTradingError,
                 'market_proxy': None, 'market_hub': None, 'hub_closed': False,
                 'proxy_closed': False}
    for name in ('close_owner', 'close_resources'):
        node = ast.Module(body=[bodies[name]], type_ignores=[])
        exec(compile(ast.fix_missing_locations(node), trading.__file__, 'exec'), namespace)  # noqa: S102
    with pytest.raises(trading.CtpGatewayTradingError):
        namespace['close_resources' if path == 'monitor' else 'close_owner']()
    assert events[0] == 'fence'
    assert not state['available'] and state['version'] != validated_version
