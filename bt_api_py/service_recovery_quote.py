"""Neutral recovery quote cursor validation and per-consumer revision state.

These helpers do not establish source trust or bind an observation to a
framework bar. Callers retain responsibility for both decisions.
"""

from collections.abc import Mapping
from typing import Any


def normalize_recovery_quote(*, source_ref, generation, cursor, instrument, as_of_ns):
    """Return a copy of a structurally usable quote revision, or ``None``."""
    if (
        type(source_ref) is not str or not source_ref
        or type(generation) is not str or not generation
        or type(instrument) is not str or not instrument
        or type(cursor) is not int or cursor < 0
        or type(as_of_ns) is not int or as_of_ns <= 0
    ):
        return None
    return {
        "source_ref": source_ref,
        "generation": generation,
        "cursor": cursor,
        "instrument": instrument,
        "as_of_ns": as_of_ns,
    }


def recovery_quote_fill_matches(binding, fill):
    """Compare a saved quote revision with a fill without attesting its source."""
    return not (
        binding.get("source_ref") != fill.get("quote_source_ref")
        or binding.get("generation") != fill.get("quote_generation")
        or binding.get("cursor") != fill.get("quote_cursor")
        or binding.get("instrument") != fill.get("quote_instrument")
    )


def validate_recovery_quote_binding(binding, *, instrument):
    """Validate a supplied neutral binding without attesting its origin."""
    fields = {"source_ref", "generation", "cursor", "instrument", "as_of_ns"}
    if not isinstance(binding, Mapping) or frozenset(binding) != fields:
        raise ValueError("recovery feed quote binding has an invalid shape")
    source_ref = binding.get("source_ref")
    generation = binding.get("generation")
    quote_instrument = binding.get("instrument")
    cursor = binding.get("cursor")
    as_of_ns = binding.get("as_of_ns")
    if (
        type(source_ref) is not str or not source_ref
        or type(generation) is not str or not generation
        or type(quote_instrument) is not str or quote_instrument != instrument
        or type(cursor) is not int or cursor < 0
        or type(as_of_ns) is not int or as_of_ns <= 0
    ):
        raise ValueError("recovery feed quote binding is invalid")
    return {
        "source_ref": source_ref,
        "generation": generation,
        "cursor": cursor,
        "instrument": quote_instrument,
        "as_of_ns": as_of_ns,
    }


class ServiceRecoveryQuoteCursor:
    """Retain strictly advancing observations for one consuming feed."""

    def __init__(self):
        self._streams: dict[tuple[str, str, str], tuple[int, int]] = {}
        self._latest: dict[str, Any] | None = None

    def note(self, *, source_ref, generation, cursor, instrument, as_of_ns):
        normalized = normalize_recovery_quote(
            source_ref=source_ref, generation=generation, cursor=cursor,
            instrument=instrument, as_of_ns=as_of_ns,
        )
        if normalized is None:
            return "invalid", None
        stream_key = (source_ref, generation, instrument)
        previous = self._streams.get(stream_key)
        if previous is not None and (cursor <= previous[0] or as_of_ns < previous[1]):
            return "not_newer", None
        self._streams[stream_key] = (cursor, as_of_ns)
        self._latest = normalized
        return None, dict(normalized)

    def latest(self):
        return None if self._latest is None else dict(self._latest)


__all__ = (
    "ServiceRecoveryQuoteCursor",
    "normalize_recovery_quote",
    "recovery_quote_fill_matches",
    "validate_recovery_quote_binding",
)
