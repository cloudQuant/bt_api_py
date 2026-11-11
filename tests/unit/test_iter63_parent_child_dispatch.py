"""An Execution parent's child order through the real decision-B composition (63.13 base).

Real journal (parent authority enabled), guard, dispatcher, SDK write coordinator,
claim coordinator sealed with the journal change port, and parent authority; only
the provider is synthetic. A SENT result means the synthetic native call ran once;
it is not venue acceptance. UNIT/FAKE evidence: no provider session, no account.
"""

import threading
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import (
    GatewayDirectCommandJournal,
    inspect_unresolved_dispatch,
)
from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command
from bt_api_gateway.direct_dispatch import (
    DirectDispatchError,
    GatewayDirectDispatcher,
    NativeDispatchOutcome,
)
from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_direct_dispatch import compose_gateway_direct_dispatcher
from bt_api_py.gateway_dispatch_claim import (
    GatewayDispatchClaimCoordinator,
    GatewayJournalDispatchChangePort,
    GatewayNativeDispatchClaimPort,
    seal_gateway_dispatch_coverage,
)
from tests.unit.test_iter63_gateway_dispatch_claim_b import WIRED, ClaimingProvider, HookAdmission
from tests.unit.test_iter63_gateway_prepared_place import ROUTE

ACCOUNT, STRATEGY, DAY = 'account-a', 'strategy-a', '20300101'
INSTRUMENT = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
EXECUTION = WireIdentity(WireRole.SERVICE, 'exec', 'actor')
SCOPE = WireScope('domain', account_ref=ACCOUNT, strategy_ref=STRATEGY, run_id='run')
OPERATIONS = frozenset({'direct.reserve', 'direct.dispatch', 'direct.release'})


class Crash(BaseException):
    """Stands for the process dying at this point: no handler below it runs."""


@contextmanager
def _open():
    yield


def _spec():
    return {'account_scope': ACCOUNT, 'strategy_scope': STRATEGY, 'parent_ref': 'parent',
            'registration_operation_id': 'register', 'execution_domain_ref': 'domain',
            'instrument': INSTRUMENT.to_dict(), 'quantity': '3', 'quantity_unit': 'lots', 'side': 'buy',
            'offset': 'open', 'limit_price': '10', 'creation_trading_day': DAY,
            'allowed_trading_days': [DAY], 'expires_at_ns': 10_000, 'grant_revision': 'v1',
            'target_position_ref': None, 'plan_digest': 'b' * 64, 'plan_version': 'v1',
            'time_in_force': 'DAY'}


def _grant(revision='v1'):
    return GatewayDelegateGrant(EXECUTION, ACCOUNT, STRATEGY, 'domain', 'execution', revision,
                                frozenset({INSTRUMENT}), OPERATIONS)


class Harness:
    def __init__(self, tmp_path, **port_options):
        self.tmp_path = tmp_path
        (tmp_path / 'guard').mkdir()
        self.state = {'day': DAY, 'now': 100, 'lease': True, 'grant': _grant(), 'transfer_hook': None}
        self.guard = LocalAccountSessionGuard(tmp_path / 'guard', ACCOUNT, 'owner')
        generation = self.guard.acquire()
        self.journal = GatewayDirectCommandJournal.create(tmp_path / 'journal.sqlite', ACCOUNT,
                                                          generation=generation)
        for enable in (self.journal.enable_controls, self.journal.enable_strategy_ledger,
                       self.journal.enable_account_extensions, self.journal.enable_parent_authority):
            enable(self.guard)
        self.authority = self._authority(self.journal, self.guard)
        self.provider = ClaimingProvider()
        self.admission = HookAdmission({ROUTE: True})
        self.writes = DirectWriteCoordinator(self.admission, lambda _: self.provider)
        self.claims, sealer = GatewayDispatchClaimCoordinator.with_coverage_sealer(ACCOUNT, generation)
        self.journal.install_dispatch_change_port(guard=self.guard,
                                                  port=GatewayJournalDispatchChangePort(self.claims))
        seal_gateway_dispatch_coverage(self.claims, sealer, wired=WIRED)
        self.port = GatewayNativeDispatchClaimPort(self.claims, journal_id='journal-a', **port_options)
        self.validations = 0
        original = self.port.claim_native

        def counted(record, identity, validate):
            def counting(horizon_ns):
                self.validations += 1
                return validate(horizon_ns)
            return original(record, identity, counting)
        self.port.claim_native = counted
        self.dispatcher = compose_gateway_direct_dispatcher(
            self.writes, ROUTE, self.journal, self.guard, lambda _record: True,
            expected_local_provider=self.provider, prepared_place=True, claim_port=self.port)
        # The Registry stand-in: one reentrant lock and a lease, as ServiceDispatchAdmission.
        self.registry_lock = threading.RLock()
        self.parent_recheck = None

    def _authority(self, journal, guard):
        state = self.state

        def transfer_completed(db, spec):
            hook = state['transfer_hook']
            if hook is not None:
                hook()
            return '0'
        return GatewayParentAuthority(journal, guard, now_ns=lambda: state['now'],
                                      trading_day=lambda: state['day'], transfer_completed=transfer_completed)

    def check(self):
        if not self.state['lease']:
            raise RuntimeError('REGISTRATION_EXPIRED')

    def permit(self, key, operation, child_id=None):
        payload = OrderRequest(symbol='rb', side=Side.BUY, order_type=OrderType.LIMIT, quantity=Decimal('1'),
            price=Decimal('10'), account_id=ACCOUNT, client_order_id='order-' + key, idempotency_key=key,
            offset='open', quantity_unit='lots', exchange_id='SHFE').to_dict()
        command = GatewayDelegatedCommand(ACCOUNT, STRATEGY, 'domain', 'exec', 'actor', 'execution', 'run', key,
                                          gateway_payload_digest(payload), INSTRUMENT, 'v1', 'parent',
                                          child_id or key)
        ctx = SimpleNamespace(identity=EXECUTION, scope=SCOPE, principal_ref='exec', registration_id='reg')
        state = self.state

        def parent_recheck(db, cmd, order):
            check = self.parent_recheck or self.authority.recheck_child
            return check(db, cmd, order)
        permit = authorize_delegated_command(ctx, _grant(), command, operation, payload,
            registry_recheck=lambda _ctx: (self.check(), True)[1], current_grant=lambda: state['grant'],
            instrument_recheck=lambda value: value == INSTRUMENT, parent_recheck=parent_recheck)
        return payload, permit

    def register(self):
        return self.authority.register_parent(_spec(), principal='exec', actor_ref='actor', fence=_open,
                                              recheck=lambda: True)

    def freeze(self, authority=None):
        return (authority or self.authority).cancel_parent(ACCOUNT, STRATEGY, 'parent', 'register', 'freeze', 1,
            principal='exec', actor_ref='actor', fence=_open, recheck=lambda: True)

    def reserve(self, key='child-1', child_id=None):
        payload, permit = self.permit(key, 'direct.reserve', child_id)
        return self.journal.reserve(STRATEGY, key, 'exec', payload, reservation_fence=_open,
                                    delegation_permit=permit)

    def request_fence(self, _row):
        @contextmanager
        def scope():
            with self.registry_lock:
                self.check()
                yield self.check
        return scope()

    def dispatch(self, row):
        _, permit = self.permit(row.request_key, 'direct.dispatch')
        return self.dispatcher.dispatch(row.strategy_scope, row.request_key, row.principal, row.reservation_id,
                                        row.digest, row.version, request_fence=self.request_fence,
                                        delegation_permit=permit)

    def row(self, key='child-1'):
        return self.journal.get(STRATEGY, key, 'exec')

    def at_claim(self, action):
        """Run ``action`` once the dispatch entry checks passed, right before the claim."""
        counted = self.port.claim_native

        def claim_native(record, identity, validate):
            action()
            return counted(record, identity, validate)
        self.port.claim_native = claim_native

    def restart(self):
        """The owner's process is gone; a new owner opens the same journal."""
        self.guard.release()
        self.guard = LocalAccountSessionGuard(self.tmp_path / 'guard', ACCOUNT, 'owner')
        generation = self.guard.acquire()
        journal = GatewayDirectCommandJournal(self.tmp_path / 'journal.sqlite', ACCOUNT, generation=generation,
            expected_journal_id=self.journal.journal_id, minimum_revision=0, controls_enabled=True,
            strategy_ledger_enabled=True, account_extensions_enabled=True, parent_authority_enabled=True)
        return journal, self._authority(journal, self.guard)

    def close(self):
        self.guard.release()


def _open_commands(h):
    counts = inspect_unresolved_dispatch(h.tmp_path / 'journal.sqlite', ACCOUNT)
    return {key: value for key, value in counts.items() if key.startswith('commands_')}


@pytest.fixture
def h(tmp_path):
    value = Harness(tmp_path)
    yield value
    value.close()


def test_parent_child_is_sent_once_and_the_claim_is_retired(h):
    h.register()
    row = h.reserve()
    result = h.dispatch(row)
    assert result.status == 'SENT' and len(h.provider.sends) == 1 and h.provider.checks == 1
    assert h.claims.capacity_status()['commands'] == 0
    again = h.dispatch(row)
    assert again.status == 'SENT' and len(h.provider.sends) == 1
    # Another request that reuses the child id is refused at reserve: one child, one order.
    from bt_api_gateway.direct_delegation import GatewayDelegationDenied
    with pytest.raises(GatewayDelegationDenied):
        h.reserve('child-x', child_id='child-1')
    assert len(h.provider.sends) == 1


@pytest.mark.parametrize('change', ['lease', 'grant'])
@pytest.mark.parametrize('when', ['claim', 'marker'])
def test_revoked_execution_lease_or_grant_before_the_marker_sends_nothing(h, change, when):
    h.register()
    row = h.reserve()

    def revoke():
        if change == 'lease':
            h.state['lease'] = False
        else:
            h.state['grant'] = _grant('v2')
    if when == 'claim':
        h.at_claim(revoke)
        expected = 'DISPATCH_CLAIM_UNAVAILABLE' if change == 'lease' else 'DELEGATION_DENIED'
    else:
        h.provider.on_prepare = revoke   # after the claim, before the marker commit
        expected = 'PRE_DISPATCH_FAILED'
    with pytest.raises(DirectDispatchError, match=expected):
        h.dispatch(row)
    current = h.row()
    assert current.status == 'RESERVED' and current.version == row.version
    assert current.native_identity_json is None
    assert h.provider.sends == [] and h.provider.checks == 0
    assert h.claims.capacity_status()['commands'] == 0
    if when == 'claim':
        assert h.provider.prepared == []


@pytest.mark.parametrize('change', ['frozen', 'expired', 'other_day'])
@pytest.mark.parametrize('when', ['claim', 'marker'])
def test_frozen_expired_or_foreign_day_parent_sends_nothing(h, change, when):
    h.register()
    row = h.reserve()

    def apply():
        if change == 'frozen':
            h.freeze()
        elif change == 'expired':
            h.state['now'] = 10_000
        else:
            h.state['day'] = '20300102'
    if when == 'claim':
        h.at_claim(apply)
        expected = 'DELEGATION_DENIED'
    else:
        h.provider.on_prepare = apply
        expected = 'PRE_DISPATCH_FAILED'
    with pytest.raises(DirectDispatchError, match=expected):
        h.dispatch(row)
    current = h.row()
    assert current.status == 'RESERVED' and current.native_identity_json is None
    assert h.provider.sends == [] and h.claims.capacity_status()['commands'] == 0
    if change == 'frozen':
        # The journal explains the child: unsent, so released under the frozen parent.
        target, = h.authority.query_operation(ACCOUNT, STRATEGY, 'parent', 'freeze', principal='exec',
            actor_ref='actor', fence=_open, recheck=lambda: True)['result']['targets']
        assert (target['child_id'], target['phase']) == ('child-1', 'RELEASE_PENDING')


def test_parent_frozen_while_the_claim_reads_it_makes_the_claim_stale_and_sends_nothing(h):
    h.register()
    row = h.reserve()
    armed = []

    def freeze_once():
        # The read snapshot already saw ACTIVE; the freeze commits now (owner thread).
        if armed and armed.pop():
            h.freeze()
    h.state['transfer_hook'] = freeze_once
    h.at_claim(lambda: armed.append(True))
    with pytest.raises(DirectDispatchError, match='DELEGATION_DENIED'):
        h.dispatch(row)
    assert h.validations == 2   # the first candidate went stale and was validated again
    assert h.row().status == 'RESERVED' and h.provider.prepared == [] and h.provider.sends == []


def test_journal_write_during_the_parent_read_retries_the_claim_then_sends_once(h):
    h.register()
    row = h.reserve()
    armed = []

    def unrelated_write_once():
        if armed and armed.pop():
            h.journal.reserve(STRATEGY, 'unrelated', 'other', {'symbol': 'rb', 'offset': 'open'})
    h.state['transfer_hook'] = unrelated_write_once
    h.at_claim(lambda: armed.append(True))
    assert h.dispatch(row).status == 'SENT'
    assert h.validations == 2 and len(h.provider.sends) == 1


def test_crash_inside_the_parent_read_leaves_a_reserved_child_the_journal_explains(h):
    h.register()
    row = h.reserve()
    armed = []

    def crash():
        if armed:
            raise Crash()
    h.state['transfer_hook'] = crash
    h.at_claim(lambda: armed.append(True))
    with pytest.raises(Crash):
        h.dispatch(row)
    current = h.row()
    assert current.status == 'RESERVED' and current.version == row.version
    assert current.native_identity_json is None and h.provider.prepared == [] and h.provider.sends == []
    assert h.claims.capacity_status()['commands'] == 0
    h.state['transfer_hook'] = None
    journal, authority = h.restart()
    # A reservation of the lost owner generation can only expire: it was never sent.
    assert journal.expire(STRATEGY, 'child-1', 'exec').status == 'EXPIRED'
    assert _open_commands(h) == {'commands_dispatching': 0, 'commands_unknown': 0}
    assert h.freeze(authority)['targets'] == []


def test_crash_between_claim_and_marker_leaves_a_reserved_child(h):
    h.register()
    row = h.reserve()

    def crash():
        raise Crash()
    h.provider.on_prepare = crash
    with pytest.raises(Crash):
        h.dispatch(row)
    current = h.row()
    assert current.status == 'RESERVED' and current.native_identity_json is None
    assert h.provider.sends == [] and h.claims.capacity_status()['commands'] == 0
    journal, _ = h.restart()
    assert journal.expire(STRATEGY, 'child-1', 'exec').status == 'EXPIRED'
    assert _open_commands(h) == {'commands_dispatching': 0, 'commands_unknown': 0}


@pytest.mark.parametrize('sent', [False, True])
def test_crash_after_the_marker_never_sends_the_child_twice(h, sent):
    h.register()
    row = h.reserve()

    def crash():
        raise Crash()
    if sent:
        h.provider.observer = crash          # after the synthetic native send
    else:
        h.provider.before_native = crash     # after the marker, before the native call
    with pytest.raises(Crash):
        h.dispatch(row)
    h.provider.observer = h.provider.before_native = lambda: None
    assert h.row().status == 'DISPATCHING' and len(h.provider.sends) == int(sent)
    again = h.dispatch(row)
    assert again.status == 'DISPATCHING' and len(h.provider.sends) == int(sent)
    journal, authority = h.restart()
    # DISPATCHING is an open venue outcome; recovery resolves it by the original identity.
    assert journal.get(STRATEGY, 'child-1', 'exec').native_identity_json is not None
    assert _open_commands(h) == {'commands_dispatching': 1, 'commands_unknown': 0}
    target, = h.freeze(authority)['targets']
    assert (target['child_id'], target['phase']) == ('child-1', 'WAIT_DISPATCH')


def test_other_threads_never_wait_for_the_childs_native_call(h):
    """Registry, claim-domain and journal writers proceed while the native call runs."""
    h.register()
    row = h.reserve()
    done = threading.Event()

    def writer():
        with h.registry_lock:
            token = h.claims.begin_change('registry')
            h.claims.finish_change_current(token)
        h.journal.reserve(STRATEGY, 'other-thread', 'other', {'symbol': 'rb', 'offset': 'open'})
        done.set()

    finished = []

    def native_in_progress():
        threading.Thread(target=writer, daemon=True).start()
        finished.append(done.wait(5.0))
    h.provider.before_native = native_in_progress
    result = h.dispatch(row)
    assert finished == [True], 'a writer waited for the native call'
    assert result.status == 'SENT' and len(h.provider.sends) == 1


def test_market_gated_parent_child_is_sent_and_reads_no_journal_in_the_native_fence(tmp_path):
    from iter63_claim_support import SyntheticClaimPort

    from tests.unit.test_iter63_gateway_market_dispatch_gate import (
        KEY,
        _delegated_real_journal_case,
    )

    guard, journal, gate, row, delegated, market = _delegated_real_journal_case(tmp_path, parent=True)
    identity = NativeOrderIdentity('CTP', 'native-ref', {'trading_day': '20300101'}, 1)
    sends, opened = [], []

    def forbidden(*_args, **_kwargs):
        opened.append(True)
        raise AssertionError('journal opened inside the native fence')

    def native(order, port, scope):
        marker = port.commit(PreDispatchRecord(WriteOperation.PLACE, scope, 'CTP', KEY.instrument.native_symbol,
                                               identity, None, order, '20300101', 1, 'synthetic'))
        with port.native_fence() as final_recheck:
            journal._connection = forbidden
            try:
                final_recheck()
                sends.append('native')
            finally:
                del journal._connection
        return NativeDispatchOutcome(True, identity, marker)

    @contextmanager
    def fence(_record):
        yield lambda: None

    dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, native, lambda _value: ('native-key',),
                                         lambda _record: True, claim_port=SyntheticClaimPort())
    try:
        result = dispatcher.dispatch(row.strategy_scope, row.request_key, row.principal, row.reservation_id,
                                     row.digest, row.version, request_fence=fence, delegation_permit=delegated,
                                     market_gate=gate, market_permit=market)
        assert result.status == 'SENT' and sends == ['native'] and opened == []
    finally:
        guard.release()
