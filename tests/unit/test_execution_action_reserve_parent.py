"""Synthetic action grants; no Risk or account calls."""
# ruff: noqa: F811
from dataclasses import replace

import pytest
from bt_api_py.execution_action_grant import ExecutionActionRequest
from bt_api_py.execution_parent_service_host import ParentServiceError, parse_config
from tests.unit.test_iter65_execution_parent_service_host import setup_config
from test_iter65_atomic_submission_intake import atomic  # noqa: F401


def test_reserve_parent_requires_its_own_explicit_action_grant(atomic):
    rig, _, _ = atomic
    old = rig.intake.action_grant
    scope = rig.scope
    request = ExecutionActionRequest(old.instance_id, old.identity, scope.account_ref, scope.strategy_ref,
        scope.execution_domain_ref, old.revision, next(iter(old.instruments)), 'v1', 'reserve_parent')
    with pytest.raises(Exception): rig.registry.bind_execution_action(request)
    updated = replace(old, actions=old.actions | {'reserve_parent'})
    assert rig.registry.replace_execution_action_grant(old, updated)
    binding = rig.registry.bind_execution_action(request)
    with binding.scope() as recheck:
        recheck()


def test_parent_formal_service_does_not_enable_reserve_parent(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    assert 'reserve_parent' not in parse_config(raw).action_grant.actions
    raw['action_grant']['actions'].append('reserve_parent')
    with pytest.raises(ParentServiceError, match='INVALID_CONFIGURATION'):
        parse_config(raw)
