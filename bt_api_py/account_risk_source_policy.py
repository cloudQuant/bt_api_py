"""Resolve the selected provider's account-risk source requirement."""

from functools import lru_cache

from bt_api_base.account_risk_source_policy import AccountRiskSourcePolicy

from .provider_entrypoints import load_route_entrypoint


@lru_cache(maxsize=128)
def requires_native_account_binding(provider: str) -> bool:
    """Read one explicit provider declaration without asserting source proof."""
    if type(provider) is not str or not provider or provider != provider.strip():
        raise ValueError("account_risk_source_policy_unavailable")
    factory = load_route_entrypoint("bt_api.account_risk_source_policies", provider.upper())
    policy = factory()
    if type(policy) is not AccountRiskSourcePolicy:
        raise ValueError("account_risk_source_policy_unavailable")
    return policy.requires_native_account_binding


__all__ = ("requires_native_account_binding",)
