"""Discover the exact route's provider-owned execution configuration policy."""

from __future__ import annotations

from bt_api_base.execution_configuration_policy import ExecutionConfigurationPolicy

from .provider_entrypoints import load_route_entrypoint


def execution_configuration_policy(exchange_name: str) -> ExecutionConfigurationPolicy:
    """Resolve an explicit declaration; missing, conflicting or invalid is an error."""
    factory = load_route_entrypoint("bt_api.execution_configuration_policies", exchange_name)
    policy = factory(exchange_name)
    if type(policy) is not ExecutionConfigurationPolicy:
        raise ValueError("execution configuration policy is invalid")
    return policy


__all__ = ("execution_configuration_policy",)
