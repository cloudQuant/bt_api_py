"""Resolve a provider-declared execution-ledger identity family."""

from functools import lru_cache

from bt_api_base.execution_ledger_identity_policy import ExecutionLedgerIdentityPolicy

from ._contracts.errors import NormalizedApiError
from .provider_entrypoints import load_route_entrypoint


@lru_cache(maxsize=128)
def uses_credential_bound_ledger(provider: str) -> bool:
    """Select one immutable provider format; empty historical rows remain unclassified."""
    if provider == "":
        return False
    if type(provider) is not str or not provider or provider != provider.strip():
        raise NormalizedApiError(
            "execution_identity", "execution_ledger_policy_unavailable", definite_reject=True
        )
    try:
        factory = load_route_entrypoint("bt_api.execution_ledger_identity_policies", provider)
        policy = factory()
        if type(policy) is not ExecutionLedgerIdentityPolicy:
            raise TypeError("invalid execution ledger policy")
        return policy.credential_bound
    except Exception:
        raise NormalizedApiError(
            "execution_identity", "execution_ledger_policy_unavailable", definite_reject=True
        ) from None


__all__ = ("uses_credential_bound_ledger",)
