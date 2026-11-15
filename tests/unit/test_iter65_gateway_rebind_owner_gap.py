"""Offline cross-owner diagnostic; no network, venue, or real account.

A genuine local A receipt does not establish authority for an independent B.
UNKNOWN is the Execution claim state, not proof of any remote send or non-send.
"""
from decimal import Decimal
from dataclasses import replace
import importlib.util
import hashlib
import json
import sqlite3
import threading
import time

import pytest

for optional_package in ('bt_api_execution', 'bt_api_gateway'):
    if importlib.util.find_spec(optional_package) is None:
        pytest.importorskip(optional_package)

from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.errors import InvalidStateTransition
from bt_api_execution.gateway_session import ExecutionGatewaySession
from bt_api_execution.store import SqliteExecutionStore
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_delegation import GatewayDelegateGrant
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_delegation_service import GatewayDelegationService, GatewayDelegationSettings, OPERATIONS as DELEGATE_OPS
from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources, GatewayOwnerStartupCleanupError
from bt_api_py.gateway_parent_service import GatewayParentGrant, GatewayParentSettings, GatewayParentService, OPERATIONS as PARENT_OPS
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot
from tests.unit.test_service_registry import _next_request, _register


class ReboundOwnerGap(Exception):
    """Raised only after the local claim and B's independently observed refusal."""


IDENTITY = WireIdentity(WireRole.SERVICE, 'exec', 'actor')
STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy')
SCOPE = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
TEMPLATE = WireScope('domain', account_ref='account', strategy_ref='strategy')
KEY = MarketInstrumentKey('ctp', 'SHFE', 'synthetic', 'future', 'rb', 'rb')
OPS = PARENT_OPS | DELEGATE_OPS


def config(route):
    return parse_service_config({
        'service_config_schema_version': 1, 'process_role': 'service',
        'service_id': 'execution-service', 'scope_ref': 'execution-scope',
        'transport_ref': 'listener', 'registration_policy_ref': 'grants',
        'service_requirements_ref': 'ready',
        'components': {'execution': {'config_schema_version': 2, 'settings': {
            'instance_id': 'execution', 'bind_ports': ['service.order_group_entry'],
            'options': {'role': 'group_coordinator', 'portfolio_ref': 'portfolio'}}}},
        'clients': {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
            'mount': route.mount, 'account_ref': route.scope.account_ref,
            'service_id': route.service_id, 'instance_id': route.instance_id,
            'scope_ref': route.scope_ref, 'transport_ref': 'gateway-rpc', 'required': True}]}}},
        'communication': {'config_schema_version': 1, 'profiles': {
            'listener': {'backend': 'zmq', 'config_schema_version': 1,
                'endpoints': {'rpc_bind': 'tcp://127.0.0.1:7854'}, 'security_profile_ref': 'test'},
            'gateway-rpc': {'backend': 'zmq', 'config_schema_version': 1,
                'endpoints': {'rpc_connect': 'tcp://127.0.0.1:7855'}, 'security_profile_ref': 'test'}}}})


def parent_spec():
    return dict(parent_ref='parent', registration_operation_id='register', risk_operation_id='risk',
        account_scope='account', strategy_scope='strategy', execution_domain_ref='domain',
        instrument=KEY.to_dict(), quantity='5', quantity_unit='lots', side='buy', offset='open',
        limit_price='5', creation_trading_day='20300101', allowed_trading_days=['20300101'],
        expires_at_ns=time.time_ns() + 60_000_000_000, grant_revision='g', plan_digest='a' * 64,
        plan_version='v1', target_position_ref=None, risk_selected=False, time_in_force='DAY')


class Gateway:
    def __init__(self, path, name, *, startup_hook=None, owner_observer=None, cleanup_observer=None,
                 startup_timeout=2):
        path.mkdir()
        directory = path / 'guard'
        directory.mkdir()
        self.path = path / 'gateway.sqlite'
        self.route = GatewayDelegateRoute('upstream.direct_write', 'gateway-' + name, 'instance-' + name,
                                          'scope', SCOPE, IDENTITY)
        self.native_calls = []
        self.parent_checks = []
        parts = {}
        def factory():
            guard = None
            def cleanup():
                if guard is not None:
                    guard.release()
                if cleanup_observer is not None:
                    cleanup_observer()
            try:
                guard = LocalAccountSessionGuard(directory, 'account', 'owner')
                generation = guard.acquire()
                if startup_hook is not None:
                    startup_hook(guard)
                journal = GatewayDirectCommandJournal.create(self.path, 'account', generation=generation)
                for enable in (journal.enable_controls, journal.enable_strategy_ledger,
                               journal.enable_account_extensions, journal.enable_parent_authority):
                    enable(guard)
                authority = GatewayParentAuthority(journal, guard, now_ns=time.time_ns,
                    trading_day=lambda: '20300101', transfer_completed=lambda *_: '0')
                def place(*args):
                    self.native_calls.append(args)
                    raise AssertionError('unexpected native call')
                dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, place, lambda _: ('native',), lambda _: True)
                parts.update(journal=journal, authority=authority, guard=guard)
                return GatewayDirectOwnerResources(dispatcher, journal, cleanup)
            except BaseException as error:
                raise GatewayOwnerStartupCleanupError(cleanup) from error
        self.owner = GatewayDirectOwner(factory, queue_size=2, max_queue_wait_seconds=2, busy_timeout_seconds=2)
        self.registry = None
        try:
            if owner_observer is not None:
                owner_observer(self.owner)
            if not self.owner.start(startup_timeout):
                raise RuntimeError('SYNTHETIC_GATEWAY_START_FAILED')
            self.journal, self.authority = parts['journal'], parts['authority']
            instance = self.route.instance_id
            strategy_ops = frozenset({'direct.reserve'})
            cap = ServiceCapability(instance, 'service.direct_write', OPS | strategy_ops,
                                    DELEGATE_OPS | frozenset({'direct.reserve', 'direct.parent.register', 'direct.parent.cancel'}))
            grants = {
                IDENTITY.principal_ref: (PrincipalGrant(IDENTITY, (ResourceGrant(instance, 'service.direct_write', 'scope', TEMPLATE, OPS),)),),
                STRATEGY.principal_ref: (PrincipalGrant(STRATEGY, (ResourceGrant(instance, 'service.direct_write', 'scope', TEMPLATE, strategy_ops),)),)}
            self.registry = ServiceRegistry(':memory:', service_id=self.route.service_id, capabilities=(cap,),
                                            grants=grants, policy_version='p', requirements_version='r')
            parent_grant = GatewayParentGrant(IDENTITY, 'account', 'strategy', 'domain', 'execution', 'g', frozenset({KEY}), PARENT_OPS)
            parent_service = GatewayParentService(GatewayParentSettings((parent_grant,)), owner=self.owner,
                journal=self.journal, authority=self.authority, instance_id=instance, scope_ref='scope')
            parent_service.configure(self.registry)
            grant = GatewayDelegateGrant(IDENTITY, 'account', 'strategy', 'domain', 'execution', 'g',
                frozenset({KEY}), frozenset({'direct.reserve', 'direct.dispatch'}))
            def check(db, command, payload):
                parts['guard'].assert_owned()
                assert db.in_transaction
                assert (command.account_scope, command.strategy_scope, command.execution_domain_ref,
                        command.parent_ref, command.child_id, command.request_key) == (
                            'account', 'strategy', 'domain', 'parent', 'child', 'child')
                assert gateway_payload_digest(dict(payload)) == command.payload_digest
                assert db.execute('SELECT 1 FROM direct_execution_parents WHERE account=? AND strategy=? AND parent_ref=?',
                                  (command.account_scope, command.strategy_scope, command.parent_ref)).fetchone() is None
                result = self.authority.recheck_child(db, command, payload)
                self.parent_checks.append(result)
                return result
            delegate = GatewayDelegationService(GatewayDelegationSettings((grant,), lambda item: item == KEY,
                parent_recheck=check), owner=self.owner, journal=self.journal, instance_id=instance, scope_ref='scope')
            delegate.configure(self.registry)
            self.registry.register_handler(instance, 'direct.reserve', lambda *_: {})
            for op in PARENT_OPS:
                self.registry.register_handler(instance, op, lambda ctx, args, op=op: parent_service.handle(op, ctx, args))
            for op in DELEGATE_OPS:
                self.registry.register_handler(instance, op, lambda ctx, args, op=op: delegate.handle(op, ctx, args))
            def register(identity, ops, key):
                return _register(self.registry, principal=identity.principal_ref, identity=identity, scope=SCOPE,
                    key=key, instance_id=instance, port='service.direct_write', scope_ref='scope', operations=tuple(ops))
            assert register(STRATEGY, strategy_ops, 'strategy')['ok']
            registration = register(IDENTITY, OPS, 'execution')
            assert registration['ok'], registration
            self.issued = registration['result']
            self.calls, self.replies = [], []
        except BaseException:
            self.close()
            raise

    def bind(self, session, monkeypatch, child_rows):
        route, issued = self.route, self.issued
        binding = BindingSnapshot(issued['registration_id'], issued['service_epoch'], True, route.mount,
            route.service_id, route.instance_id, route.scope_ref, SCOPE, OPS, True)
        monkeypatch.setattr(session.runtime, 'status', lambda: RuntimeSnapshot('running', True, True, None,
            (binding,), 0, 0, IDENTITY, SCOPE.run_id))
        def rpc(mount, op, args, **kwargs):
            assert mount == route.mount and kwargs['account_ref'] == 'account' and kwargs['scope_ref'] == 'scope'
            if op == 'direct.delegated_reserve':
                assert child_rows()[0]['state'] == 'UNKNOWN'  # claim precedes transport
            self.calls.append(op)
            response = self.registry.handle(IDENTITY.principal_ref, _next_request(op, {
                'registration_id': issued['registration_id'], 'service_epoch': issued['service_epoch'],
                'instance_id': route.instance_id, 'arguments': args}, identity=IDENTITY, scope=SCOPE))
            self.replies.append(response)
            assert response['ok'], response
            return response['result']
        monkeypatch.setattr(session.runtime, 'call', rpc)
        def bound_rpc(target, op, args, *, timeout_seconds):
            # Model the trusted bound dispatch independently of runtime.status;
            # this test uses a local Registry, not a real CURVE connection.
            expected = replace(session.parent_client._query_target,
                               registration_id=issued['registration_id'],
                               service_epoch=issued['service_epoch'])
            assert target == expected and op == 'direct.parent.query'
            assert (target.mount, target.service_id, target.instance_id, target.scope_ref,
                    target.scope, target.identity, target.run_id) == (
                        route.mount, route.service_id, route.instance_id, route.scope_ref,
                        SCOPE, IDENTITY, SCOPE.run_id)
            assert target.expected_peer == WireIdentity(WireRole.SERVICE, 'gateway-peer', 'gateway-actor')
            assert (target.port, target.transport_ref, target.security_profile_ref, target.endpoint) == (
                'service.direct_write', 'gateway-rpc', 'test', 'tcp://127.0.0.1:7855')
            assert target.server_key_fingerprint == hashlib.sha256(b'server').hexdigest()
            assert timeout_seconds == 5.0
            return rpc(target.mount, op, args, account_ref=target.scope.account_ref,
                       scope_ref=target.scope_ref)
        monkeypatch.setattr(session.runtime, 'call_bound', bound_rpc)

    def close(self):
        try:
            if self.registry is not None:
                self.registry.close()
        finally:
            if not self.owner.close(2):
                raise RuntimeError('SYNTHETIC_GATEWAY_CLEANUP_UNCONFIRMED')


def test_factory_failure_cleans_its_guard_on_owner_thread(tmp_path):
    seen = {}
    def fail_after_acquire(guard):
        seen['guard'] = guard
        seen['factory_thread'] = threading.get_ident()
        raise ValueError('synthetic factory failure')
    def cleaned():
        seen['cleanup_thread'] = threading.get_ident()
    with pytest.raises(RuntimeError, match='SYNTHETIC_GATEWAY_START_FAILED'):
        Gateway(tmp_path / 'failed', 'failed', startup_hook=fail_after_acquire,
                owner_observer=lambda owner: seen.update(owner=owner), cleanup_observer=cleaned)
    owner = seen['owner']
    owner._thread.join(2)
    assert not owner._thread.is_alive()
    assert seen['factory_thread'] == seen['cleanup_thread'] != threading.get_ident()
    assert seen['guard']._phase == 'idle'


def test_start_timeout_closes_owned_resources_when_factory_returns(tmp_path):
    seen = {}
    owner_ready, factory_entered, release_factory = (threading.Event() for _ in range(3))
    def observe(owner):
        seen['owner'] = owner
        owner_ready.set()
    def hold(guard):
        seen['guard'] = guard
        factory_entered.set()
        if not release_factory.wait(1):
            raise RuntimeError('test release timed out')
    def release_on_close():
        try:
            if owner_ready.wait(1):
                owner = seen['owner']
                with owner._condition:
                    seen['closing_observed'] = owner._condition.wait_for(lambda: owner._closing, timeout=1)
        finally:
            release_factory.set()
    helper = threading.Thread(target=release_on_close)
    helper.start()
    try:
        with pytest.raises(RuntimeError, match='SYNTHETIC_GATEWAY_START_FAILED'):
            Gateway(tmp_path / 'timeout', 'timeout', startup_hook=hold, owner_observer=observe,
                    startup_timeout=0.05)
    finally:
        release_factory.set()
        helper.join(2)
    assert not helper.is_alive() and factory_entered.is_set() and seen['closing_observed']
    owner = seen['owner']
    owner._thread.join(2)
    assert not owner._thread.is_alive() and seen['guard']._phase == 'idle'


@pytest.mark.xfail(strict=True, raises=ReboundOwnerGap,
                   reason='GAP: Execution parent is not bound to full Gateway service/instance identity')
def test_rebound_owner_cannot_claim_old_gateway_parent(tmp_path, monkeypatch):
    a = Gateway(tmp_path / 'a', 'a')
    b = None
    store = None
    try:
        b = Gateway(tmp_path / 'b', 'b')
        path = tmp_path / 'execution.sqlite'
        store = SqliteExecutionStore(path)
        scope = ExecutionScope('FAKE', 'test', 'account', 'strategy', '20300101')
        lease = store.acquire_or_renew_lease(scope, 'writer', ttl_ns=300_000_000_000)
        peer = WireIdentity(WireRole.SERVICE, 'gateway-peer', 'gateway-actor')
        def session(route):
            parsed = config(route)
            binding = parsed.to_public_dict()['clients']['gateway']['settings']['bindings'][0]
            assert (binding['service_id'], binding['instance_id'], binding['account_ref'],
                    binding['mount'], binding['scope_ref']) == (route.service_id, route.instance_id,
                    route.scope.account_ref, route.mount, route.scope_ref)
            return ExecutionGatewaySession(config=parsed, route=route, expected_gateway=peer,
                security_profiles={'test': ClientCurveKeys(b'server', b'public', b'secret')},
                store=store, scope=scope, lease=lease, admission=lambda *_: True)
        def children():
            with store._read_transaction() as cur:
                return [dict(row) for row in cur.execute('SELECT * FROM execution_parent_children')]
        original = session(a.route)
        a.bind(original, monkeypatch, children)
        spec = parent_spec()
        original.parent.accept(spec)
        accepted = original.parent.register('parent')
        assert accepted['gateway_state'] == 'ACCEPTED'
        assert a.calls == ['direct.parent.register'] and a.replies[0]['result']['status'] == 'OBSERVED'
        assert original.parent_client.query_parent(spec)['state'] == 'ACTIVE'
        with sqlite3.connect(a.path) as db:
            assert db.execute('SELECT state FROM direct_execution_parents').fetchone() == ('ACTIVE',)
        with sqlite3.connect(b.path) as db:
            assert db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0] == 0
        request = OrderRequest(symbol='rb', side=Side.BUY, order_type=OrderType.LIMIT, quantity=Decimal('1'),
            account_id='account', client_order_id='child', price=Decimal('5'), idempotency_key='child',
            quantity_unit='lots', offset='open', exchange_id='SHFE')
        digest = gateway_payload_digest(request.to_dict())
        command = GatewayDelegatedCommand('account', 'strategy', 'domain', 'exec', 'actor', 'execution',
            'run', 'child', digest, KEY, 'g', 'parent', 'child')
        original.parent.prepare_child('parent', dict(child_id='child', request_key='child',
            trading_day='20300101', offset='open', quantity='1', payload_digest=digest))
        before_parent, before_child = original.parent.get('parent'), children()
        assert before_child[0]['state'] == 'PREPARED'
        assert (a.route.service_id, a.route.instance_id) != (b.route.service_id, b.route.instance_id)
        assert a.route.scope == b.route.scope
        rebound = session(b.route)
        b.bind(rebound, monkeypatch, children)
        assert rebound.parent_client.query_parent(spec) is None
        try:
            result = rebound.parent.dispatch_child('parent', 'child', command, request, rebound.delegate_client)
        except InvalidStateTransition as error:
            # The bound Execution transport currently fails closed before a
            # claim; this does not test whether an enabled transport binds the
            # original Gateway service/instance identity across recovery.
            assert str(error) == 'bound Execution transport unavailable'
            assert children() == before_child
            assert original.parent.get('parent') == before_parent
            assert b.calls == ['direct.parent.query']
            assert a.native_calls == b.native_calls == []
            pytest.xfail('bound Execution transport unavailable; cross-owner claim remains untested')
        assert children()[0]['state'] == 'UNKNOWN'
        assert b.calls == ['direct.parent.query', 'direct.delegated_reserve'] and b.parent_checks == [False]
        assert result.outcome == 'UNKNOWN' and b.replies[-1]['result'] == {'status': 'UNKNOWN'}
        assert a.native_calls == b.native_calls == []
        with sqlite3.connect(b.path) as db:
            assert db.execute('SELECT count(*) FROM direct_journal_commands').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM direct_delegated_commands').fetchone()[0] == 0
        after_child = children()
        assert original.parent.get('parent') == before_parent
        store.close()
        store = SqliteExecutionStore(path)
        reopened = session(b.route)
        assert reopened.parent.get('parent') == before_parent
        assert children() == after_child
        assert json.loads(after_child[0]['request_json']) == json.loads(before_child[0]['request_json'])
        raise ReboundOwnerGap('B locally claimed old A child before B refused the missing parent')
    finally:
        try:
            if store is not None:
                store.close()
        finally:
            try:
                if b is not None:
                    b.close()
            finally:
                a.close()
