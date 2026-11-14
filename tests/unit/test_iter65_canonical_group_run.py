"""Mapped group delegates share durable run and freeze checks across aliases."""
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_execution.contracts import ExecutionScope
from bt_api_py.service_registry import ServiceRegistryStrategyRunConflictError
from tests.unit.test_service_group_registration_lifecycle import (
    _dispatch_group, _new_registry, _register_group,
)


def mapping():
    first = WireScope('live:venue-a', account_ref='account-a', strategy_ref='strategy-a')
    second = replace(first, execution_domain_ref='other-domain')
    target = ExecutionScope('FAKE', 'test', 'canonical-account', 'canonical-strategy')
    return first, second, {first: target, second: target}


def add_synthetic_history(registry, wire, run):
    # Isolated fixture of a previously persisted strategy claim. It grants no
    # provider authority and deliberately carries no active registration.
    registry._db.execute(
        'INSERT INTO strategy_write_claims '
        '(execution_domain_ref,strategy_ref,account_ref,run_id,created_at) VALUES(?,?,?,?,?)',
        (wire.execution_domain_ref, wire.strategy_ref, wire.account_ref, run, 100.0))


def test_group_cannot_seed_conflicting_run_in_mapped_alias(tmp_path):
    registry, identity, group_scope, _ = _new_registry(tmp_path / 'registry.sqlite')
    first, second, targets = mapping()
    try:
        registry.configure_strategy_canonical_targets(targets=targets)
        add_synthetic_history(registry, second, 'other-run')
        registration = _register_group(registry, identity, group_scope)
        with pytest.raises(ServiceRegistryStrategyRunConflictError):
            registry.bind_trusted_group_run(first.execution_domain_ref, first.strategy_ref,
                first.account_ref, registration_id=registration['registration_id'])
        assert registry.strategy_write_run_id(first.execution_domain_ref, first.strategy_ref, first.account_ref) is None
    finally:
        registry.close()


def test_group_same_run_works_until_other_alias_freezes(tmp_path):
    registry, identity, group_scope, _ = _new_registry(tmp_path / 'registry.sqlite')
    first, second, targets = mapping()
    registry.register_handler('account-owner', 'orders.submit_group_leg', lambda *_: {'submitted': True})
    try:
        registry.configure_strategy_canonical_targets(targets=targets)
        add_synthetic_history(registry, second, 'run-a')
        registration = _register_group(registry, identity, group_scope)
        assert registry.bind_trusted_group_run(first.execution_domain_ref, first.strategy_ref,
            first.account_ref, registration_id=registration['registration_id']) == 'run-a'
        assert _dispatch_group(registry, identity, group_scope, registration)['ok']
        registry.freeze_strategy(replace(second, run_id='run-a'), 'freeze')
        assert not _dispatch_group(registry, identity, group_scope, registration)['ok']
    finally:
        registry.close()


def test_unmapped_legacy_group_history_quarantines_mapped_aliases(tmp_path):
    registry, identity, group_scope, _ = _new_registry(tmp_path / 'registry.sqlite')
    first, second, targets = mapping()
    try:
        add_synthetic_history(registry, second, 'run-a')
        registry.configure_strategy_canonical_targets(targets=targets)
        registration = _register_group(registry, identity, group_scope)
        with pytest.raises(ServiceRegistryStrategyRunConflictError):
            registry.bind_trusted_group_run(first.execution_domain_ref, first.strategy_ref,
                first.account_ref, registration_id=registration['registration_id'])
    finally:
        registry.close()
