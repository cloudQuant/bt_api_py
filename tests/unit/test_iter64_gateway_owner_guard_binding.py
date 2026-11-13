"""Local factory composition only; no CTP account or dispatch authority."""

import os
from dataclasses import replace

import pytest
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

import bt_api_py.ctp_gateway_trading as trading
from bt_api_py._gateway_owner_guard_binding import _GatewayOwnerGuardBinding
from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.service_components import ServiceResourceConstructionError
from tests.test_iter63_ctp_gateway_trading import FakeSession, _resources, make


def test_real_factory_private_binding_lifecycle(tmp_path, monkeypatch):
    captured = {}
    other_guard_root = tmp_path / "other-guard"
    other_guard_root.mkdir()

    def capture(owner, journal, guard, dispatcher, **kwargs):
        binding = _GatewayOwnerGuardBinding(owner, journal, guard, dispatcher, **kwargs)
        captured.update(owner=owner, journal=journal, binding=binding, closing=kwargs['closing'])
        # The factory's object is not usable until the owner publishes it.
        with pytest.raises(RuntimeError):
            binding.assert_bound(owner, journal)
        with pytest.raises(RuntimeError):
            _GatewayOwnerGuardBinding(owner, journal, guard, dispatcher,
                                      **dict(kwargs, generation='wrong'))
        other = GatewayDirectCommandJournal.create(tmp_path / 'other.sqlite', journal.account_scope,
                                                    generation=journal.generation)
        with pytest.raises(RuntimeError):
            _GatewayOwnerGuardBinding(owner, other, guard, dispatcher,
                                      **dict(kwargs, journal_id=other.journal_id))
        wrong_guard = LocalAccountSessionGuard(other_guard_root, 'different-account', 'owner')
        wrong_guard.acquire()
        try:
            with pytest.raises(DirectCommandJournalError, match='GUARD_BINDING_MISMATCH'):
                _GatewayOwnerGuardBinding(owner, journal, wrong_guard, dispatcher, **kwargs)
        finally:
            wrong_guard.release()
        return binding

    monkeypatch.setattr(trading, '_GatewayOwnerGuardBinding', capture)
    profile, _, _, authority, _, _ = make(tmp_path, monkeypatch)
    resources = _resources(profile, authority)
    binding, owner, journal = (captured[key] for key in ('binding', 'owner', 'journal'))
    # The same private object is now passed only to trusted Host composition;
    # the previous no-export assertion is preserved in the Git history.
    assert resources.values['owner_guard_binding'] is binding
    assert not hasattr(binding, 'guard') and not hasattr(binding, 'issue_permit')

    def check_owner_thread():
        assert binding.assert_bound(owner, journal) is None
        assert binding.assert_instance('gateway-a') is None
        with pytest.raises(RuntimeError):
            binding.assert_instance('other-instance')
        with pytest.raises(RuntimeError):
            binding.assert_bound(owner, object())
        other_owner = GatewayDirectOwner(lambda: None, queue_size=1, max_queue_wait_seconds=1,
                                         busy_timeout_seconds=1)
        with pytest.raises(RuntimeError):
            binding.assert_bound(other_owner, journal)
        original = owner._binding
        try:
            for field in ('account_scope', 'journal_id', 'generation'):
                owner._binding = replace(original, **{field: 'wrong'})
                with pytest.raises(RuntimeError):
                    binding.assert_bound(owner, journal)
        finally:
            owner._binding = original
        original_dispatcher = owner._dispatcher
        try:
            owner._dispatcher = object()
            with pytest.raises(RuntimeError):
                binding.assert_bound(owner, journal)
        finally:
            owner._dispatcher = original_dispatcher
        original_generation = journal._generation
        try:
            journal._generation = 'wrong'
            with pytest.raises(RuntimeError):
                binding.assert_bound(owner, journal)
        finally:
            journal._generation = original_generation
        captured['closing'].set()
        try:
            with pytest.raises(RuntimeError):
                binding.assert_bound(owner, journal)
        finally:
            captured['closing'].clear()
        return True

    try:
        assert owner.submit_service_call(check_owner_thread).result(timeout_seconds=3)
        with pytest.raises(RuntimeError):
            binding.assert_bound(owner, journal)
        # The PID check must reject even when fork preserves the object references.
        if hasattr(os, 'fork'):
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                try:
                    binding.assert_bound(owner, journal)
                except RuntimeError:
                    os.write(write_fd, b'rejected')
                finally:
                    os.close(write_fd)
                    os._exit(0)
            os.close(write_fd)
            assert os.read(read_fd, 16) == b'rejected'
            os.close(read_fd)
            assert os.waitpid(pid, 0)[1] == 0
    finally:
        resources.close()
    with pytest.raises(RuntimeError):
        binding.assert_bound(owner, journal)


def test_factory_failure_after_binding_runs_existing_cleanup(tmp_path, monkeypatch):
    captured = {}

    def fail_after_binding(owner, journal, guard, dispatcher, **kwargs):
        captured['binding'] = _GatewayOwnerGuardBinding(owner, journal, guard, dispatcher, **kwargs)
        captured['owner'], captured['journal'] = owner, journal
        raise ValueError('synthetic factory failure')

    monkeypatch.setattr(trading, '_GatewayOwnerGuardBinding', fail_after_binding)
    profile, settings, _, authority, _, _ = make(tmp_path, monkeypatch)
    with pytest.raises(ServiceResourceConstructionError):
        _resources(profile, authority)
    assert FakeSession.instances[0].events[-1][0] == 'close'
    with pytest.raises(RuntimeError):
        captured['binding'].assert_bound(captured['owner'], captured['journal'])
    guard = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, 'after-failure')
    guard.acquire()
    guard.release()


def test_failed_factory_retains_guard_until_session_close_confirmed(tmp_path, monkeypatch):
    captured = {}

    def fail_after_binding(owner, journal, guard, dispatcher, **kwargs):
        captured['binding'] = _GatewayOwnerGuardBinding(owner, journal, guard, dispatcher, **kwargs)
        captured['owner'], captured['journal'] = owner, journal
        FakeSession.instances[0].close_allowed = False
        raise ValueError('synthetic factory failure')

    monkeypatch.setattr(trading, '_GatewayOwnerGuardBinding', fail_after_binding)
    profile, settings, _, authority, _, _ = make(tmp_path, monkeypatch)
    with pytest.raises(ServiceResourceConstructionError) as failure:
        _resources(profile, authority, cleanup_on_error=False)
    contender = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, 'contender')
    try:
        with pytest.raises(trading.CtpGatewayTradingError, match='TRADING_OWNER_CLOSE_UNCONFIRMED'):
            failure.value.resources.close()
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
        with pytest.raises(RuntimeError):
            captured['binding'].assert_bound(captured['owner'], captured['journal'])
    finally:
        FakeSession.instances[0].close_allowed = True
        failure.value.resources.close()
    contender.acquire()
    contender.release()
