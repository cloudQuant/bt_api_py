"""Select an exact provider declaration for risk dispatch evidence."""

from bt_api_base.risk_dispatch_evidence import RiskDispatchEvidenceProfile

from .provider_entrypoints import ProviderEntryPointError, load_route_entrypoint


def risk_dispatch_evidence_profile(provider):
    """Return one exact provider declaration, failing closed if unavailable."""
    if type(provider) is not str or not provider or provider != provider.strip():
        raise ProviderEntryPointError("provider_entry_point_unavailable")
    factory = load_route_entrypoint("bt_api.risk_dispatch_evidence", provider)
    try:
        profile = factory()
        if type(profile) is not RiskDispatchEvidenceProfile or profile.provider != provider:
            raise ValueError("provider declaration does not match account scope")
        return profile
    except Exception:
        raise ProviderEntryPointError("provider_entry_point_unavailable") from None


__all__ = ("risk_dispatch_evidence_profile",)
