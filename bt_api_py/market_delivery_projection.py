"""Neutral consumer projection of SDK market-event continuity.

The caller owns event delivery, local state and proof of source identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass(frozen=True)
class SdkMarketEventProjection:
    """A pure consumer projection plus state deltas for one SDK event."""

    event: Dict[str, Any]
    counter_deltas: Tuple[Tuple[str, int], ...] = ()
    state_updates: Tuple[Tuple[str, Any], ...] = ()
    drop_reason: Optional[str] = None
    drop_safety_impact: Optional[str] = None
    drop_mark_stale: bool = True


def normalize_sdk_market_event(raw_event: Mapping) -> Tuple[Dict[str, Any], str, str]:
    """Copy neutral SDK event fields and apply Backtrader's field aliases."""
    event = dict(raw_event)
    event.setdefault("received_monotonic_ns", event.get("recv_monotonic_ns"))
    event.setdefault("sequence", event.get("ingest_seq", 0))
    event.setdefault("volume", event.get("delta_volume"))
    event.setdefault("price", event.get("last_price"))
    event.setdefault("bid_volume", event.get("bid_size"))
    event.setdefault("ask_volume", event.get("ask_size"))
    symbol = str(event.get("symbol") or "")
    kind = str(event.get("kind") or "market").lower()
    return event, symbol, kind


def project_sdk_market_event(
    event: Dict[str, Any],
    venue: str,
    symbol: str,
    current_state: Mapping,
    previous_seen: Optional[int],
    coalesced_count: int,
    *,
    event_id_factory: Callable[[], str],
    wall_time_factory: Callable[[], float],
) -> SdkMarketEventProjection:
    """Project continuity and freshness facts without mutating Store state.

    The caller applies returned counter/state deltas and records any projected
    drop through its existing Store lifecycle. The event mapping is a private
    copy prepared by :func:`normalize_sdk_market_event`.
    """
    event = dict(event)
    event["coalesced_count"] = coalesced_count
    # Keep setdefault's eager default evaluation semantics from the Store path.
    event.setdefault("event_id", event_id_factory())
    received_monotonic_ns = event.get("received_monotonic_ns")
    clock_domain_id = event.get("clock_domain_id")
    if (
        isinstance(received_monotonic_ns, bool)
        or not isinstance(received_monotonic_ns, int)
        or received_monotonic_ns <= 0
        or not isinstance(clock_domain_id, str)
        or not clock_domain_id.strip()
    ):
        return SdkMarketEventProjection(
            event=event,
            drop_reason="causal_provenance_missing_or_invalid",
        )

    event["clock_domain_id"] = clock_domain_id.strip()
    event.setdefault("received_wall_time", event.get("local_time") or wall_time_factory())
    event.setdefault("exchange_time", event.get("timestamp"))
    event.setdefault("source", "bt_api_py")
    from .market_stream_contracts import decode_market_event_continuity

    snapshot_kind, continuity, sequence, previous_sequence = decode_market_event_continuity(
        event.get("snapshot_or_delta"),
        event.get("continuity_status") or event.get("continuity"),
        event.get("sequence"),
        event.get("previous_sequence"),
    )
    event["snapshot_or_delta"] = snapshot_kind
    event["continuity_status"] = continuity
    explicit_stale = bool(event.get("stale", False))
    stale_reason = str(event.get("stale_reason") or "")
    sequence_key = (venue, symbol)
    event["sequence"] = sequence
    event["previous_sequence"] = previous_sequence

    is_snapshot = event["snapshot_or_delta"] == "snapshot"
    if sequence and previous_seen is not None and not is_snapshot:
        if sequence < previous_seen:
            return SdkMarketEventProjection(
                event=event,
                counter_deltas=(("out_of_order", 1),),
                drop_reason="sequence_out_of_order",
            )
        if sequence == previous_seen:
            return SdkMarketEventProjection(
                event=event,
                counter_deltas=(("duplicate", 1),),
                drop_reason="duplicate_sequence",
                drop_safety_impact="duplicate_removed_without_state_change",
                drop_mark_stale=False,
            )
        if previous_sequence is not None and previous_sequence != previous_seen:
            continuity = "gap"
            stale_reason = "sequence_gap"

    event["_sequence_key"] = sequence_key
    event["_sequence_value"] = sequence
    unhealthy = continuity in {
        "gap",
        "stale",
        "disconnected",
        "checksum_failed",
        "out_of_order",
    }
    counter_deltas = []
    if unhealthy:
        counter_deltas.append(("sequence_gap" if continuity == "gap" else continuity, 1))
        explicit_stale = True
        stale_reason = stale_reason or continuity
    if explicit_stale:
        counter_deltas.append(("stale", 1))
        state_updates = {
            "stale": True,
            "stale_reason": stale_reason or "stale_event",
        }
        event.update(stale=True, stale_reason=state_updates["stale_reason"])
    elif is_snapshot and continuity in {"ok", "continuous", "recovered", "snapshot"}:
        # Snapshot recovery is provisional until the native object validates.
        # Otherwise a crossed/empty book can falsely clear a prior gap.
        event["_recovery_candidate"] = True
        if current_state.get("stale"):
            event.update(
                stale=True,
                stale_reason=current_state.get("stale_reason") or "recovery_pending_validation",
            )
        else:
            event.update(stale=False, stale_reason="")
        state_updates = {}
    elif current_state.get("stale"):
        # A continuous delta cannot recover a previously broken book. Keep
        # every delivered event unsafe until the SDK emits a valid snapshot.
        event.update(stale=True, stale_reason=current_state.get("stale_reason") or "stale_stream")
        state_updates = {}
    else:
        state_updates = {}
    state_updates.update(
        continuity_status=(
            "recovery_pending" if event.get("_recovery_candidate") else continuity
        ),
        last_event_id=event["event_id"],
        clock_domain_id=event["clock_domain_id"],
        last_received_monotonic_ns=event["received_monotonic_ns"],
    )
    event["continuity_status"] = continuity
    return SdkMarketEventProjection(
        event=event,
        counter_deltas=tuple(counter_deltas),
        state_updates=tuple(state_updates.items()),
    )



__all__ = ("SdkMarketEventProjection", "normalize_sdk_market_event", "project_sdk_market_event")
