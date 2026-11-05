"""Common exact-route discovery for provider-owned SDK projections."""

from __future__ import annotations

import threading
from importlib import metadata
from typing import Any


class ProviderEntryPointError(RuntimeError):
    """A provider declaration is unavailable, ambiguous, or malformed."""


# ``metadata.entry_points()`` scans every installed distribution (hundreds of
# milliseconds in a large environment) and was called on hot paths such as
# query-lease revalidation, making short SDK leases expire before use.  The
# discovered declarations are cached per (group, route) and remembered with
# the exact ``entry_points`` callable they came from, so a replaced discovery
# source (for example a test double) is never answered from a stale entry.
_ENTRY_CACHE: dict[tuple[str, str], tuple[Any, tuple[Any, ...]]] = {}
_ENTRY_CACHE_LOCK = threading.Lock()


def _route_entries(group: str, exchange_name: str) -> tuple[Any, ...]:
    source = metadata.entry_points
    key = (group, exchange_name)
    with _ENTRY_CACHE_LOCK:
        cached = _ENTRY_CACHE.get(key)
    if cached is not None and cached[0] is source:
        return cached[1]
    discovered = source()
    if hasattr(discovered, "select"):
        entries = tuple(discovered.select(group=group, name=exchange_name))
    elif isinstance(discovered, dict):
        entries = tuple(e for e in discovered.get(group, ()) if e.name == exchange_name)
    else:
        entries = tuple(e for e in discovered if e.group == group and e.name == exchange_name)
    with _ENTRY_CACHE_LOCK:
        _ENTRY_CACHE[key] = (source, entries)
    return entries


def clear_route_entrypoint_cache() -> None:
    """Forget cached declarations (after installing or removing a provider)."""
    with _ENTRY_CACHE_LOCK:
        _ENTRY_CACHE.clear()


def load_route_entrypoint(group: str, exchange_name: str, *, optional: bool = False) -> Any:
    """Load exactly one callable for a route without selecting an implicit provider."""
    try:
        entries = _route_entries(group, exchange_name)
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


__all__ = ("ProviderEntryPointError", "clear_route_entrypoint_cache", "load_route_entrypoint")
