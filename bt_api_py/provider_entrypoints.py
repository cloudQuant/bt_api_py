"""Common exact-route discovery for provider-owned SDK projections."""

from __future__ import annotations

from importlib import metadata
from typing import Any


class ProviderEntryPointError(RuntimeError):
    """A provider declaration is unavailable, ambiguous, or malformed."""


def load_route_entrypoint(group: str, exchange_name: str, *, optional: bool = False) -> Any:
    """Load exactly one callable for a route without selecting an implicit provider."""
    try:
        discovered = metadata.entry_points()
        if hasattr(discovered, "select"):
            entries = tuple(discovered.select(group=group, name=exchange_name))
        elif isinstance(discovered, dict):
            entries = tuple(e for e in discovered.get(group, ()) if e.name == exchange_name)
        else:
            entries = tuple(e for e in discovered if e.group == group and e.name == exchange_name)
        if not entries and optional:
            return None
        if len(entries) != 1:
            raise ValueError("provider declaration is absent or ambiguous")
        provider = entries[0].load()
        if not callable(provider):
            raise TypeError("provider declaration is not callable")
        return provider
    except Exception:
        raise ProviderEntryPointError("provider_entry_point_unavailable") from None


__all__ = ("ProviderEntryPointError", "load_route_entrypoint")
