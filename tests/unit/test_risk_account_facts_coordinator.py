"""Synthetic strict-bound client plus real Risk SQLite, no real account."""
import threading
from contextlib import contextmanager
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade.ledger import PretradeLedger
from bt_api_risk.risk_order_journal import RiskOrderJournal
from bt_api_risk.risk_order_journal_contracts import RiskOrderJournalError
from bt_api_risk.risk_order_journal_storage import RiskOrderJournalStorage

from bt_api_py.gateway_account_facts_client import GatewayAccountFactsClient
from bt_api_py.risk_account_facts_coordinator import RiskAccountFactsCoordinator

pytest_plugins = ('tests.unit.test_gateway_account_facts_v2',)


@contextmanager
def risk_ledger(path):
    journal = RiskOrderJournal(path, writer_id='synthetic')
    with journal.writer_fence(AccountScope('ctp', 'synthetic', 'test')) as fence:
        ledger = PretradeLedger(journal, fence, schema_version=4)
        ledger.enable_r05_observations_v2()
        yield ledger, journal


def progress(ledger):
    with ledger.transaction() as txn:
        return txn.r05_progress()


def test_strict_read_commits_before_ack(v2, tmp_path, monkeypatch):
    client, runtime, calls, _, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, journal):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        original = runtime.call_bound
        def checked(*args, **kwargs):
            if args[1] == 'account.facts.ack':
                # An independent immediate transaction proves the apply lock
                # is released and the pending intent is already durable.
                with journal._transaction() as db:
                    assert tuple(db.execute('SELECT cursor,pending FROM risk_r05_state').fetchone()) == (1, 1)
            return original(*args, **kwargs)
        monkeypatch.setattr(runtime, 'call_bound', checked)
        result = coordinator.poll_once()
        assert (result.status, result.cursor) == ('APPLIED', 1)
        assert progress(ledger)['ack_observed'] == 1 and progress(ledger)['pending'] is None
        assert calls == ['account.facts.read_after.v2', 'account.facts.ack']


@pytest.mark.parametrize('remote_ack', [0, 2])
def test_lost_ack_requires_explicit_original_range_reread(v2, tmp_path, monkeypatch, remote_ack):
    client, runtime, calls, wire, _ = v2
    first = wire['page']['events'][0]
    second = dict(first, sequence=2, phase='LIVE', fact_digest='d' * 64, event_digest='e' * 64)
    wire['page'].update(events=[first, second], offered_sequence=2)
    request_args = []
    original = runtime.call_bound
    failed = []
    def uncertain_first(_mount, operation, arguments, **kwargs):
        request_args.append((operation, dict(arguments)))
        if operation == 'account.facts.ack' and not failed:
            failed.append(True)
            raise TimeoutError('synthetic timeout')
        result = original(_mount, operation, arguments, **kwargs)
        if operation == 'account.facts.ack':
            response = dict(result.result)
            response['progress'] = dict(response['progress'], offered_sequence=3)
            return replace(result, result=response)
        return result
    monkeypatch.setattr(runtime, 'call_bound', uncertain_first)
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, _):
        coordinator = RiskAccountFactsCoordinator(client, ledger, limit=10)
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert progress(ledger)['pending'] == 2
        assert coordinator.poll_once().status == 'RECOVERY_REQUIRED'
        assert len(request_args) == 2
        wire['page'].update(offered_sequence=3, acknowledged_sequence=remote_ack, has_more=True)
        assert coordinator.recover_pending_once().status == 'APPLIED'
        assert request_args[2][1]['limit'] == 2 and request_args[2][1]['after_sequence'] == 0
        assert sum(op == 'account.facts.ack' for op, _ in request_args) == (2 if remote_ack == 0 else 1)
        assert progress(ledger)['ack_observed'] == 2 and progress(ledger)['pending'] is None
        assert calls.count('account.facts.read_after.v2') == 2


@pytest.mark.parametrize('mutation', ['event', 'journal', 'short', 'remote_ahead'])
def test_changed_recovery_never_acks_or_erases_pending(v2, tmp_path, monkeypatch, mutation):
    client, runtime, _, wire, _ = v2
    original = runtime.call_bound
    calls = []
    def ack_times_out(_mount, operation, arguments, **kwargs):
        calls.append(operation)
        if operation == 'account.facts.ack':
            raise TimeoutError('synthetic timeout')
        return original(_mount, operation, arguments, **kwargs)
    monkeypatch.setattr(runtime, 'call_bound', ack_times_out)
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, _):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        assert coordinator.poll_once().status == 'UNKNOWN'
        if mutation == 'event':
            wire['page']['events'][0]['reason'] = 'CHANGED'
        elif mutation == 'journal':
            wire['journal_id'] = 'different-journal'
        elif mutation == 'short':
            wire['page'].update(events=[], offered_sequence=0, bootstrap_count=0)
        else:
            wire['page'].update(acknowledged_sequence=2, offered_sequence=2, has_more=True)
        assert coordinator.recover_pending_once().status == 'UNKNOWN'
        assert calls.count('account.facts.ack') == 1
        assert progress(ledger)['pending'] == 1


def test_empty_page_stores_anchor_without_ack(v2, tmp_path):
    client, _, calls, wire, _ = v2
    wire['page'].update(events=[], has_more=False, bootstrap_count=0, offered_sequence=0)
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, _):
        result = RiskAccountFactsCoordinator(client, ledger).poll_once()
        assert (result.status, result.cursor) == ('EMPTY', 0)
        assert progress(ledger)['gateway_journal_id'] == 'journal-1'
        with ledger.transaction() as txn:
            assert txn.r05_source_binding()['fingerprint'] == client._expected_fingerprint
        assert calls == ['account.facts.read_after.v2']


def test_commit_unknown_cannot_ack_or_be_bypassed(v2, tmp_path, monkeypatch):
    client, runtime, calls, _, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, journal):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        original = RiskOrderJournalStorage._transaction
        attempts = []
        @contextmanager
        def uncertain_commit(owner, **kwargs):
            with original(owner, **kwargs) as db:
                yield db
            attempts.append(1)
            # The first transaction is progress; the second applies the page.
            if len(attempts) == 2:
                raise OSError('synthetic commit uncertainty')
        monkeypatch.setattr(RiskOrderJournalStorage, '_transaction', uncertain_commit)
        assert coordinator.poll_once().status == 'UNKNOWN'
        monkeypatch.setattr(RiskOrderJournalStorage, '_transaction', original)
        assert calls == ['account.facts.read_after.v2']
        with journal._transaction() as db:
            assert db.execute('SELECT count(*) FROM risk_r05_facts').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM risk_r05_source_binding').fetchone()[0] == 1
        assert coordinator.poll_once().status == 'UNKNOWN'
        with pytest.raises(RiskOrderJournalError, match='UNCERTAIN'):
            PretradeLedger(journal, ledger._fence, schema_version=4)


def test_wrong_thread_and_source_binding_drift(v2, tmp_path):
    client, _, calls, _, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, _):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        errors = []
        def other_thread():
            try:
                coordinator.poll_once()
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(5)
        assert errors == ['R05_FACTS_WRONG_OWNER']
        client._expected_fingerprint = '0' * 64
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert calls == []


def test_lost_read_never_automatically_retries(v2, tmp_path, monkeypatch):
    client, runtime, _, _, _ = v2
    calls = []
    def timeout(*args, **_kwargs):
        calls.append(args[1])
        raise TimeoutError('synthetic timeout')
    monkeypatch.setattr(runtime, 'call_bound', timeout)
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, _):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert coordinator.recover_pending_once().status == 'UNKNOWN'
        assert calls == ['account.facts.read_after.v2']
        assert progress(ledger) is None


def test_mutated_issued_dto_after_commit_never_reaches_ack_transport(v2, tmp_path, monkeypatch):
    client, _, calls, _, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, _):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        original = RiskOrderJournalStorage._transaction
        attempts = []
        @contextmanager
        def mutate_after_apply(owner, **kwargs):
            with original(owner, **kwargs) as db:
                yield db
            attempts.append(1)
            if len(attempts) == 2:
                issued = client._issued_v2[0]
                object.__setattr__(issued.page.events[0], 'reason', 'FORGED_AFTER_COPY')
        monkeypatch.setattr(RiskOrderJournalStorage, '_transaction', mutate_after_apply)
        assert coordinator.poll_once().status == 'UNKNOWN'
        monkeypatch.setattr(RiskOrderJournalStorage, '_transaction', original)
        assert calls == ['account.facts.read_after.v2']
        assert progress(ledger)['pending'] == 1
        assert coordinator.poll_once().status == 'RECOVERY_REQUIRED'


def test_reentrant_poll_cannot_issue_second_read_or_ack(v2, tmp_path, monkeypatch):
    client, runtime, calls, _, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, _):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        original = runtime.call_bound
        def callback(*args, **kwargs):
            with pytest.raises(RuntimeError, match='REENTRANT'):
                coordinator.poll_once()
            return original(*args, **kwargs)
        monkeypatch.setattr(runtime, 'call_bound', callback)
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert calls == ['account.facts.read_after.v2']
        assert progress(ledger) is None


def test_replaced_internal_client_call_is_not_a_source(v2, tmp_path, monkeypatch):
    client, _, calls, _, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, _):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        monkeypatch.setattr(client, '_call', lambda *_args, **_kwargs: object())
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert calls == []


def test_next_page_journal_drift_cannot_advance_or_ack(v2, tmp_path):
    client, _, calls, wire, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, _):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        assert coordinator.poll_once().status == 'APPLIED'
        wire['journal_id'] = 'other-journal'
        wire['page']['events'] = [dict(wire['page']['events'][0], sequence=2, phase='LIVE')]
        wire['page'].update(offered_sequence=2, acknowledged_sequence=1)
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert calls.count('account.facts.ack') == 1
        assert progress(ledger)['cursor'] == 1


def test_remote_ack_regression_during_recovery_is_rejected(v2, tmp_path, monkeypatch):
    client, runtime, _, wire, _ = v2
    original = runtime.call_bound
    calls = []
    def fail_second_ack(mount, operation, arguments, **kwargs):
        calls.append(operation)
        if operation == 'account.facts.ack' and calls.count('account.facts.ack') == 2:
            raise TimeoutError('synthetic')
        result = original(mount, operation, arguments, **kwargs)
        if operation == 'account.facts.ack':
            response = dict(result.result)
            response['progress'] = dict(response['progress'], offered_sequence=2)
            return replace(result, result=response)
        return result
    monkeypatch.setattr(runtime, 'call_bound', fail_second_ack)
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, _):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        assert coordinator.poll_once().status == 'APPLIED'
        wire['page']['events'] = [dict(wire['page']['events'][0], sequence=2, phase='LIVE')]
        wire['page'].update(offered_sequence=2, acknowledged_sequence=1)
        assert coordinator.poll_once().status == 'UNKNOWN'
        wire['page']['acknowledged_sequence'] = 0
        assert coordinator.recover_pending_once().status == 'UNKNOWN'
        assert calls.count('account.facts.ack') == 2
        assert progress(ledger)['pending'] == 2


def test_ack_record_commit_uncertainty_never_repeats_ack(v2, tmp_path, monkeypatch):
    client, _, calls, _, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, journal):
        coordinator = RiskAccountFactsCoordinator(client, ledger)
        original = RiskOrderJournalStorage._transaction
        attempts = []
        @contextmanager
        def uncertain_ack_commit(owner, **kwargs):
            with original(owner, **kwargs) as db:
                yield db
            attempts.append(1)
            if len(attempts) == 5:
                raise OSError('synthetic ack observation commit uncertainty')
        monkeypatch.setattr(RiskOrderJournalStorage, '_transaction', uncertain_ack_commit)
        assert coordinator.poll_once().status == 'UNKNOWN'
        monkeypatch.setattr(RiskOrderJournalStorage, '_transaction', original)
        assert calls == ['account.facts.read_after.v2', 'account.facts.ack']
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert coordinator.recover_pending_once().status == 'UNKNOWN'
        assert calls == ['account.facts.read_after.v2', 'account.facts.ack']
        with pytest.raises(RiskOrderJournalError, match='UNCERTAIN'):
            PretradeLedger(journal, ledger._fence, schema_version=4)


def test_same_source_rebuilt_coordinator_continues_with_persisted_binding(v2, tmp_path):
    client, runtime, calls, wire, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, journal):
        assert RiskAccountFactsCoordinator(client, ledger).poll_once().status == 'APPLIED'
        rebuilt_ledger = PretradeLedger(journal, ledger._fence, schema_version=4)
        rebuilt_ledger.enable_r05_observations_v2()
        rebuilt_client = GatewayAccountFactsClient(runtime, route=client._route,
            peer=client._bound_route.peer, fingerprint=client._expected_fingerprint)
        wire['page'].update(events=[], offered_sequence=1, acknowledged_sequence=1, has_more=False)
        result = RiskAccountFactsCoordinator(rebuilt_client, rebuilt_ledger).poll_once()
        assert (result.status, result.cursor) == ('EMPTY', 1)
        assert calls == ['account.facts.read_after.v2', 'account.facts.ack', 'account.facts.read_after.v2']
        with rebuilt_ledger.transaction() as txn:
            binding = txn.r05_source_binding()
            assert binding['peer_principal'] == client._bound_route.peer.principal_ref
            assert binding['fingerprint'] == client._expected_fingerprint


def test_same_source_rebuilt_coordinator_explicitly_recovers_pending(v2, tmp_path, monkeypatch):
    client, runtime, calls, wire, _ = v2
    original = runtime.call_bound
    def lost_ack(mount, operation, arguments, **kwargs):
        if operation == 'account.facts.ack':
            raise TimeoutError('synthetic lost ACK')
        return original(mount, operation, arguments, **kwargs)
    monkeypatch.setattr(runtime, 'call_bound', lost_ack)
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, journal):
        assert RiskAccountFactsCoordinator(client, ledger).poll_once().status == 'UNKNOWN'
        rebuilt_ledger = PretradeLedger(journal, ledger._fence, schema_version=4)
        rebuilt_ledger.enable_r05_observations_v2()
        rebuilt_client = GatewayAccountFactsClient(runtime, route=client._route,
            peer=client._bound_route.peer, fingerprint=client._expected_fingerprint)
        coordinator = RiskAccountFactsCoordinator(rebuilt_client, rebuilt_ledger)
        assert coordinator.poll_once().status == 'RECOVERY_REQUIRED'
        wire['page']['acknowledged_sequence'] = 1
        assert coordinator.recover_pending_once().status == 'APPLIED'
        assert calls == ['account.facts.read_after.v2', 'account.facts.read_after.v2']


@pytest.mark.parametrize('field', ['fingerprint', 'peer', 'service', 'instance'])
def test_rebuilt_different_source_is_rejected_before_any_rpc(v2, tmp_path, field):
    client, runtime, calls, _, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, journal):
        assert RiskAccountFactsCoordinator(client, ledger).poll_once().status == 'APPLIED'
        rebuilt = PretradeLedger(journal, ledger._fence, schema_version=4)
        rebuilt.enable_r05_observations_v2()
        route, peer, fingerprint = client._route, client._bound_route.peer, client._expected_fingerprint
        if field == 'fingerprint':
            fingerprint = '0' * 64
        elif field == 'peer':
            peer = WireIdentity(WireRole.SERVICE, 'other-gateway', 'other-actor')
        elif field == 'service':
            route = replace(route, service_id='other-service')
        else:
            route = replace(route, instance_id='other-instance')
        replacement = GatewayAccountFactsClient(runtime, route=route, peer=peer, fingerprint=fingerprint)
        coordinator = RiskAccountFactsCoordinator(replacement, rebuilt)
        count = len(calls)
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert coordinator.recover_pending_once().status == 'UNKNOWN'
        assert len(calls) == count


@pytest.mark.parametrize('field', ['fingerprint', 'peer'])
def test_different_source_cannot_recover_pending_or_issue_rpc(v2, tmp_path, monkeypatch, field):
    client, runtime, calls, _, _ = v2
    original = runtime.call_bound
    def lost_ack(mount, operation, arguments, **kwargs):
        if operation == 'account.facts.ack':
            raise TimeoutError('synthetic timeout')
        return original(mount, operation, arguments, **kwargs)
    monkeypatch.setattr(runtime, 'call_bound', lost_ack)
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, journal):
        assert RiskAccountFactsCoordinator(client, ledger).poll_once().status == 'UNKNOWN'
        rebuilt = PretradeLedger(journal, ledger._fence, schema_version=4)
        rebuilt.enable_r05_observations_v2()
        peer, fingerprint = client._bound_route.peer, client._expected_fingerprint
        if field == 'fingerprint':
            fingerprint = '0' * 64
        else:
            peer = WireIdentity(WireRole.SERVICE, 'other-principal', 'other-actor')
        replacement = GatewayAccountFactsClient(runtime, route=client._route, peer=peer, fingerprint=fingerprint)
        coordinator = RiskAccountFactsCoordinator(replacement, rebuilt)
        count = len(calls)
        assert coordinator.recover_pending_once().status == 'UNKNOWN'
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert len(calls) == count
        assert progress(rebuilt)['pending'] == 1


def test_coordinator_requires_explicit_v2_before_any_rpc(v2, tmp_path):
    client, _, calls, _, _ = v2
    journal = RiskOrderJournal(tmp_path / 'legacy.sqlite', writer_id='synthetic')
    with journal.writer_fence(AccountScope('ctp', 'synthetic', 'test')) as fence:
        ledger = PretradeLedger(journal, fence, schema_version=4)
        ledger.enable_r05_observations()
        with pytest.raises(ValueError, match='COORDINATOR_INVALID'):
            RiskAccountFactsCoordinator(client, ledger)
    assert calls == []
