"""Bounded, factual LOAD01 recovery observations used by the runner overlay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import time
from typing import Any


_HEAD_FIELDS = frozenset(
    {"stream_id", "epoch", "head", "oldest_sequence", "latest_observed_at_utc"}
)
RECOVERY_CATCHUP_DEADLINE_NS = 10_000_000_000


def validate_market_head_reply(reply: Any, *, expected_stream_id: str) -> dict[str, Any]:
    """Validate the exact public ``market.head`` response shape."""

    if type(expected_stream_id) is not str or not expected_stream_id:
        raise ValueError("invalid_expected_stream_id")
    if not isinstance(reply, Mapping) or any(type(key) is not str for key in reply):
        raise ValueError("invalid_head_reply")
    if set(reply) != _HEAD_FIELDS:
        raise ValueError("invalid_head_reply")
    epoch = reply.get("epoch")
    head = reply.get("head")
    oldest = reply.get("oldest_sequence")
    latest = reply.get("latest_observed_at_utc")
    if (
        type(reply.get("stream_id")) is not str
        or reply["stream_id"] != expected_stream_id
        or type(epoch) is not str
        or not epoch
        or type(head) is not int
        or head < 0
        or type(oldest) is not int
        or oldest < 1
        or oldest > head + 1
        or (latest is not None and type(latest) is not str)
    ):
        raise ValueError("invalid_head_reply")
    return {
        "stream_id": expected_stream_id,
        "epoch": epoch,
        "sequence": head,
        "oldest_sequence": oldest,
        "latest_observed_at_utc": latest,
    }


def new_recovery_incident(
    *,
    kind: str,
    incident_id: str,
    fault_started_monotonic_ns: int,
    prior_cursors: Mapping[str, tuple[str, int]],
    snapshot_cursors: Mapping[str, tuple[str, int] | None] | None = None,
) -> dict[str, Any]:
    """Create one bounded incident with a stream row for each affected key."""

    if kind not in {"slow_consumer", "reconnect"}:
        raise ValueError("invalid_recovery_kind")
    if type(incident_id) is not str or not incident_id:
        raise ValueError("invalid_incident_id")
    if type(fault_started_monotonic_ns) is not int or fault_started_monotonic_ns <= 0:
        raise ValueError("invalid_fault_start")
    if not isinstance(prior_cursors, Mapping) or not prior_cursors:
        raise ValueError("missing_prior_cursors")
    if snapshot_cursors is not None and set(snapshot_cursors) != set(prior_cursors):
        raise ValueError("snapshot_cursor_set_mismatch")

    streams: dict[str, dict[str, Any]] = {}
    for stream_id, prior in prior_cursors.items():
        if (
            type(stream_id) is not str
            or not stream_id
            or type(prior) is not tuple
            or len(prior) != 2
            or type(prior[0]) is not str
            or not prior[0]
            or type(prior[1]) is not int
            or prior[1] < 0
        ):
            raise ValueError("invalid_prior_cursor")
        prior_epoch, prior_sequence = prior
        snapshot = None if snapshot_cursors is None else snapshot_cursors[stream_id]
        snapshot_epoch: str | None = None
        snapshot_sequence: int | None = None
        skipped: int | None = 0
        gap_status = "PENDING"
        gap_reason: str | None = None
        if snapshot_cursors is not None and snapshot is None:
            skipped = None
            gap_status = "INCOMPLETE"
            gap_reason = "missing_snapshot_cursor"
        elif snapshot is not None:
            if (
                type(snapshot) is not tuple
                or len(snapshot) != 2
                or type(snapshot[0]) is not str
                or not snapshot[0]
                or type(snapshot[1]) is not int
                or snapshot[1] < 0
            ):
                raise ValueError("invalid_snapshot_cursor")
            snapshot_epoch, snapshot_sequence = snapshot
            if snapshot_epoch != prior_epoch:
                skipped = None
                gap_status = "INCOMPLETE"
                gap_reason = "snapshot_epoch_changed"
            elif snapshot_sequence < prior_sequence:
                skipped = None
                gap_status = "INCOMPLETE"
                gap_reason = "snapshot_behind_prior_cursor"
            else:
                skipped = snapshot_sequence - prior_sequence
                if skipped:
                    gap_status = "INCOMPLETE"
                    gap_reason = "snapshot_skipped_sequences"
                else:
                    gap_status = "PENDING"

        streams[stream_id] = {
            "stream_id": stream_id,
            "prior_epoch": prior_epoch,
            "prior_sequence": prior_sequence,
            "snapshot_epoch": snapshot_epoch,
            "snapshot_sequence": snapshot_sequence,
            "snapshot_skipped_sequence_count": skipped,
            "head_target_epoch": None,
            "head_target_sequence": None,
            "head_oldest_sequence": None,
            "head_target_in_retained_range": None,
            "head_rpc_started_monotonic_ns": None,
            "head_rpc_completed_monotonic_ns": None,
            "head_captured_monotonic_ns": None,
            "head_rpc_elapsed_ns": None,
            "head_error_code": None,
            "head_maintenance_error_code": None,
            "first_qualifying_receipt": None,
            "target_receipt_observed": False,
            "target_reached": False,
            "target_timing_status": "INCOMPLETE",
            "target_timing_elapsed_ms": None,
            "target_timing_issue": None,
            "coverage_cursor_epoch": snapshot_epoch or prior_epoch,
            "coverage_cursor_sequence": (
                snapshot_sequence if snapshot_sequence is not None else prior_sequence
            ),
            "coverage_gap_event_count": 0,
            "gap_coverage_status": gap_status,
            "gap_coverage_reason": gap_reason,
        }

    return {
        "kind": kind,
        "incident_id": incident_id,
        "fault_started_monotonic_ns": fault_started_monotonic_ns,
        "fault_ended_monotonic_ns": None,
        "head_capture_started_monotonic_ns": None,
        "head_capture_completed_monotonic_ns": None,
        "head_rpc_count": 0,
        "head_rpc_elapsed_ns_total": 0,
        "head_rpc_elapsed_ns_max": 0,
        "head_rpc_error_count": 0,
        "head_maintenance_check_count": 0,
        "head_maintenance_error_count": 0,
        "head_maintenance_elapsed_ns_total": 0,
        "head_maintenance_elapsed_ns_max": 0,
        "head_capture_budget_deadline_monotonic_ns": None,
        "head_capture_error_code": None,
        "streams": streams,
    }


def attach_market_head_target(
    incident: dict[str, Any],
    *,
    stream_id: str,
    reply: Any,
    request_started_monotonic_ns: int,
    reply_received_monotonic_ns: int,
) -> dict[str, Any]:
    if (
        type(incident) is not dict
        or type(request_started_monotonic_ns) is not int
        or type(reply_received_monotonic_ns) is not int
        or request_started_monotonic_ns <= 0
        or reply_received_monotonic_ns < request_started_monotonic_ns
    ):
        raise ValueError("invalid_head_timing")
    row = incident.get("streams", {}).get(stream_id)
    if type(row) is not dict or row["head_target_sequence"] is not None:
        raise ValueError("unexpected_head_target")
    target = validate_market_head_reply(reply, expected_stream_id=stream_id)
    duration = reply_received_monotonic_ns - request_started_monotonic_ns
    row.update(
        {
            "head_target_epoch": target["epoch"],
            "head_target_sequence": target["sequence"],
            "head_oldest_sequence": target["oldest_sequence"],
            "head_target_in_retained_range": (
                target["sequence"] == 0
                or target["sequence"] >= target["oldest_sequence"]
            ),
            "head_rpc_started_monotonic_ns": request_started_monotonic_ns,
            "head_rpc_completed_monotonic_ns": reply_received_monotonic_ns,
            "head_captured_monotonic_ns": reply_received_monotonic_ns,
            "head_rpc_elapsed_ns": duration,
        }
    )
    incident["head_rpc_count"] += 1
    incident["head_rpc_elapsed_ns_total"] += duration
    incident["head_rpc_elapsed_ns_max"] = max(
        incident["head_rpc_elapsed_ns_max"], duration
    )
    if row["gap_coverage_status"] == "PENDING":
        if row["coverage_cursor_epoch"] != target["epoch"]:
            row["gap_coverage_status"] = "INCOMPLETE"
            row["gap_coverage_reason"] = "head_epoch_changed_from_cursor"
        elif row["coverage_cursor_sequence"] >= target["sequence"]:
            row["gap_coverage_status"] = "COMPLETE_TO_TARGET"
    return target


def mark_market_head_failure(
    incident: dict[str, Any],
    *,
    stream_id: str,
    safe_error_code: str,
    elapsed_ns: int | None = None,
    request_started_monotonic_ns: int | None = None,
    reply_completed_monotonic_ns: int | None = None,
    attempted: bool = True,
) -> None:
    row = incident.get("streams", {}).get(stream_id)
    if type(row) is not dict:
        raise ValueError("unexpected_head_failure_stream")
    row["head_error_code"] = safe_error_code
    if attempted:
        incident["head_rpc_error_count"] += 1
        incident["head_rpc_count"] += 1
        if type(request_started_monotonic_ns) is int:
            row["head_rpc_started_monotonic_ns"] = request_started_monotonic_ns
        if type(reply_completed_monotonic_ns) is int:
            row["head_rpc_completed_monotonic_ns"] = reply_completed_monotonic_ns
        if type(elapsed_ns) is int and elapsed_ns >= 0:
            row["head_rpc_elapsed_ns"] = elapsed_ns
            incident["head_rpc_elapsed_ns_total"] += elapsed_ns
            incident["head_rpc_elapsed_ns_max"] = max(
                incident["head_rpc_elapsed_ns_max"], elapsed_ns
            )


def capture_market_recovery_heads(
    runtime: Any,
    market_keys: Sequence[Any],
    incident: dict[str, Any],
    *,
    maintenance_if_due: Any,
    monotonic_ns: Any = time.monotonic_ns,
) -> None:
    """Capture fixed per-stream heads through the authorized market.head API."""

    keys = tuple(market_keys)
    streams = incident.get("streams")
    if (
        not keys
        or not isinstance(streams, Mapping)
        or {getattr(key, "stream_id", None) for key in keys} != set(streams)
    ):
        raise ValueError("recovery_stream_set_mismatch")
    if not callable(maintenance_if_due):
        raise ValueError("missing_recovery_maintenance_boundary")

    incident["head_capture_started_monotonic_ns"] = monotonic_ns()
    fault_ended_ns = incident.get("fault_ended_monotonic_ns")
    if type(fault_ended_ns) is not int or fault_ended_ns <= 0:
        incident["head_capture_error_code"] = "FAULT_END_ANCHOR_MISSING"
        for key in keys:
            mark_market_head_failure(
                incident,
                stream_id=key.stream_id,
                safe_error_code="FAULT_END_ANCHOR_MISSING",
                attempted=False,
            )
        incident["head_capture_completed_monotonic_ns"] = monotonic_ns()
        return
    deadline_ns = fault_ended_ns + RECOVERY_CATCHUP_DEADLINE_NS
    incident["head_capture_budget_deadline_monotonic_ns"] = deadline_ns

    def mark_remaining(start_index: int, safe_error_code: str) -> None:
        incident["head_capture_error_code"] = safe_error_code
        for pending_key in keys[start_index:]:
            mark_market_head_failure(
                incident,
                stream_id=pending_key.stream_id,
                safe_error_code=safe_error_code,
                attempted=False,
            )

    try:
        binding = runtime.market_binding_view(
            "store.market", required_operations=frozenset({"market.head"})
        )
    except Exception:
        incident["head_capture_error_code"] = "BINDING_UNAVAILABLE"
        for key in keys:
            mark_market_head_failure(
                incident,
                stream_id=key.stream_id,
                safe_error_code="BINDING_UNAVAILABLE",
                attempted=False,
            )
        incident["head_capture_completed_monotonic_ns"] = monotonic_ns()
        return

    if monotonic_ns() >= deadline_ns:
        mark_remaining(0, "CAPTURE_BUDGET_EXHAUSTED")
        incident["head_capture_completed_monotonic_ns"] = monotonic_ns()
        return

    for key_index, key in enumerate(keys):
        if monotonic_ns() >= deadline_ns:
            mark_remaining(key_index, "CAPTURE_BUDGET_EXHAUSTED")
            break

        maintenance_started_ns = monotonic_ns()
        incident["head_maintenance_check_count"] += 1
        try:
            maintenance_if_due("market-recovery-head")
        except Exception:
            maintenance_failed_ns = monotonic_ns()
            maintenance_elapsed_ns = max(0, maintenance_failed_ns - maintenance_started_ns)
            incident["head_maintenance_error_count"] += 1
            incident["head_maintenance_elapsed_ns_total"] += maintenance_elapsed_ns
            incident["head_maintenance_elapsed_ns_max"] = max(
                incident["head_maintenance_elapsed_ns_max"], maintenance_elapsed_ns
            )
            row = incident["streams"][key.stream_id]
            row["head_maintenance_error_code"] = "MAINTENANCE_FAILED"
            incident["head_capture_error_code"] = "MAINTENANCE_FAILED"
            mark_market_head_failure(
                incident,
                stream_id=key.stream_id,
                safe_error_code="MAINTENANCE_FAILED",
                attempted=False,
            )
            for remaining in keys[key_index + 1 :]:
                mark_market_head_failure(
                    incident,
                    stream_id=remaining.stream_id,
                    safe_error_code="CAPTURE_ABORTED",
                    attempted=False,
                )
            break
        maintenance_completed_ns = monotonic_ns()
        maintenance_elapsed_ns = max(0, maintenance_completed_ns - maintenance_started_ns)
        incident["head_maintenance_elapsed_ns_total"] += maintenance_elapsed_ns
        incident["head_maintenance_elapsed_ns_max"] = max(
            incident["head_maintenance_elapsed_ns_max"], maintenance_elapsed_ns
        )
        if maintenance_completed_ns >= deadline_ns:
            mark_remaining(key_index, "CAPTURE_BUDGET_EXHAUSTED")
            break

        request_started_ns = monotonic_ns()
        if request_started_ns >= deadline_ns:
            mark_remaining(key_index, "CAPTURE_BUDGET_EXHAUSTED")
            break
        reply_received_ns: int | None = None
        try:
            reply = runtime.call(
                "store.market",
                "market.head",
                {"key": key.to_dict()},
                scope_ref=binding.scope_ref,
            )
            reply_received_ns = monotonic_ns()
            attach_market_head_target(
                incident,
                stream_id=key.stream_id,
                reply=reply,
                request_started_monotonic_ns=request_started_ns,
                reply_received_monotonic_ns=reply_received_ns,
            )
        except Exception:
            failed_at_ns = monotonic_ns()
            elapsed_ns = (reply_received_ns or failed_at_ns) - request_started_ns
            incident["head_capture_error_code"] = "HEAD_CAPTURE_FAILED"
            mark_market_head_failure(
                incident,
                stream_id=key.stream_id,
                safe_error_code="HEAD_CAPTURE_FAILED",
                elapsed_ns=elapsed_ns,
                request_started_monotonic_ns=request_started_ns,
                reply_completed_monotonic_ns=(reply_received_ns or failed_at_ns),
                attempted=True,
            )
            for remaining in keys[key_index + 1 :]:
                mark_market_head_failure(
                    incident,
                    stream_id=remaining.stream_id,
                    safe_error_code="CAPTURE_ABORTED",
                    attempted=False,
                )
            break
        if monotonic_ns() >= deadline_ns and key_index + 1 < len(keys):
            mark_remaining(key_index + 1, "CAPTURE_BUDGET_EXHAUSTED")
            break
    incident["head_capture_completed_monotonic_ns"] = monotonic_ns()


def capture_strategy_market_recovery_heads(
    runtime_probe: Any,
    runtime: Any,
    market_keys: Sequence[Any],
    incident: dict[str, Any],
    *,
    monotonic_ns: Any = time.monotonic_ns,
) -> None:
    """Use the strategy's live runtime-probe maintenance boundary per head RPC."""

    maintenance_if_due = getattr(runtime_probe, "_maintain_if_due", None)
    capture_market_recovery_heads(
        runtime,
        market_keys,
        incident,
        maintenance_if_due=maintenance_if_due,
        monotonic_ns=monotonic_ns,
    )


def record_market_delivery_receipt(
    incidents: Sequence[dict[str, Any]],
    delivery_receipts: dict[str, dict[str, Any]],
    *,
    stream_id: str,
    epoch: str,
    sequence: int,
    received_monotonic_ns: int,
) -> dict[str, Any]:
    """Record the actual client poll receipt and feed the recovery observers."""

    receipt = {
        "epoch": epoch,
        "sequence": sequence,
        "received_monotonic_ns": received_monotonic_ns,
    }
    delivery_receipts[stream_id] = receipt
    for incident in incidents:
        observe_market_receipt(
            incident,
            stream_id=stream_id,
            epoch=epoch,
            sequence=sequence,
            received_monotonic_ns=received_monotonic_ns,
        )
    return receipt


def _target_timing_anchor_issue(
    incident: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    receipt_monotonic_ns: int | None = None,
) -> str | None:
    anchors = (
        incident.get("fault_started_monotonic_ns"),
        incident.get("fault_ended_monotonic_ns"),
        incident.get("head_capture_started_monotonic_ns"),
        row.get("head_rpc_started_monotonic_ns"),
        row.get("head_rpc_completed_monotonic_ns"),
        incident.get("head_capture_completed_monotonic_ns"),
    )
    if any(type(value) is not int or value <= 0 for value in anchors):
        return "MISSING_OR_INVALID_MONOTONIC_ANCHOR"
    fault_start, fault_end, capture_start, rpc_start, rpc_end, capture_end = anchors
    if not (fault_start <= fault_end <= capture_start <= rpc_start <= rpc_end <= capture_end):
        return "MONOTONIC_ANCHOR_ORDER_INVALID"
    if receipt_monotonic_ns is not None:
        if type(receipt_monotonic_ns) is not int or receipt_monotonic_ns <= 0:
            return "MISSING_OR_INVALID_RECEIPT_TIME"
        if receipt_monotonic_ns < capture_end:
            return "RECEIPT_PRECEDES_CAPTURE_COMPLETION"
    return None


def observe_market_receipt(
    incident: dict[str, Any],
    *,
    stream_id: str,
    epoch: str,
    sequence: int,
    received_monotonic_ns: int,
) -> None:
    """Observe only an actual client-delivered envelope receipt."""

    row = incident.get("streams", {}).get(stream_id)
    if type(row) is not dict:
        raise ValueError("unexpected_receipt_stream")
    if (
        type(epoch) is not str
        or not epoch
        or type(sequence) is not int
        or sequence < 0
        or type(received_monotonic_ns) is not int
        or received_monotonic_ns <= 0
    ):
        raise ValueError("invalid_market_receipt")

    target_epoch = row["head_target_epoch"]
    target_sequence = row["head_target_sequence"]
    captured_ns = row["head_captured_monotonic_ns"]
    if target_epoch is None or target_sequence is None or captured_ns is None:
        return

    if row["coverage_cursor_sequence"] < target_sequence:
        cursor_epoch = row["coverage_cursor_epoch"]
        cursor_sequence = row["coverage_cursor_sequence"]
        if epoch != cursor_epoch:
            if row["gap_coverage_status"] != "INCOMPLETE":
                row["gap_coverage_status"] = "INCOMPLETE"
                row["gap_coverage_reason"] = "delivered_epoch_changed_before_target"
        elif sequence > cursor_sequence:
            first_missing = cursor_sequence + 1
            last_needed = min(sequence - 1, target_sequence)
            if first_missing <= last_needed:
                row["coverage_gap_event_count"] += last_needed - first_missing + 1
                if row["gap_coverage_status"] != "INCOMPLETE":
                    row["gap_coverage_status"] = "INCOMPLETE"
                    row["gap_coverage_reason"] = "delivered_sequence_gap_before_target"
            row["coverage_cursor_sequence"] = sequence
            if (
                row["gap_coverage_status"] == "PENDING"
                and row["coverage_cursor_sequence"] >= target_sequence
            ):
                row["gap_coverage_status"] = "COMPLETE_TO_TARGET"

    if (
        not row["target_receipt_observed"]
        and received_monotonic_ns >= captured_ns
        and epoch == target_epoch
        and sequence >= target_sequence
    ):
        row["target_receipt_observed"] = True
        row["first_qualifying_receipt"] = {
            "epoch": epoch,
            "sequence": sequence,
            "received_monotonic_ns": received_monotonic_ns,
            "elapsed_from_fault_start_ms": None,
            "elapsed_from_fault_end_ms": None,
            "elapsed_from_head_capture_complete_ms": None,
        }
        issue = _target_timing_anchor_issue(
            incident, row, receipt_monotonic_ns=received_monotonic_ns
        )
        if issue is not None:
            row["target_timing_issue"] = issue
            row["target_timing_status"] = "INCOMPLETE"
            return

        fault_started_ns = incident["fault_started_monotonic_ns"]
        fault_ended_ns = incident["fault_ended_monotonic_ns"]
        capture_completed_ns = incident["head_capture_completed_monotonic_ns"]
        elapsed_from_fault_end_ns = received_monotonic_ns - fault_ended_ns
        elapsed_ms = elapsed_from_fault_end_ns / 1_000_000
        row["first_qualifying_receipt"].update(
            {
                "elapsed_from_fault_start_ms": (
                    received_monotonic_ns - fault_started_ns
                ) / 1_000_000,
                "elapsed_from_fault_end_ms": elapsed_ms,
                "elapsed_from_head_capture_complete_ms": (
                    received_monotonic_ns - capture_completed_ns
                ) / 1_000_000,
            }
        )
        row["target_reached"] = True
        row["target_timing_elapsed_ms"] = elapsed_ms
        row["target_timing_status"] = (
            "WITHIN_10S"
            if elapsed_from_fault_end_ns <= RECOVERY_CATCHUP_DEADLINE_NS
            else "EXCEEDED_10S"
        )


def recovery_incident_summary(
    incident: Mapping[str, Any], *, observed_through_monotonic_ns: int | None = None
) -> dict[str, Any]:
    streams = incident.get("streams")
    if not isinstance(streams, Mapping):
        raise ValueError("invalid_recovery_incident")
    target_count = sum(
        1 for row in streams.values() if row.get("head_target_sequence") is not None
    )
    observed_receipt_count = sum(
        1 for row in streams.values() if row.get("target_receipt_observed") is True
    )
    reached_count = sum(1 for row in streams.values() if row.get("target_reached") is True)
    timing_rows: dict[str, dict[str, Any]] = {}
    for stream_id, row in streams.items():
        timing_status = row.get("target_timing_status")
        timing_issue = row.get("target_timing_issue")
        timing_elapsed_ms = row.get("target_timing_elapsed_ms")
        if row.get("head_target_sequence") is None:
            timing_status = "INCOMPLETE"
            timing_issue = timing_issue or "HEAD_TARGET_MISSING"
        elif row.get("target_receipt_observed") is True:
            if row.get("target_reached") is not True or timing_status not in {
                "WITHIN_10S",
                "EXCEEDED_10S",
            }:
                timing_status = "INCOMPLETE"
                timing_issue = timing_issue or _target_timing_anchor_issue(incident, row)
        else:
            anchor_issue = _target_timing_anchor_issue(incident, row)
            if anchor_issue is not None:
                timing_status = "INCOMPLETE"
                timing_issue = timing_issue or anchor_issue
            else:
                deadline_ns = (
                    incident["fault_ended_monotonic_ns"]
                    + RECOVERY_CATCHUP_DEADLINE_NS
                )
                if (
                    type(observed_through_monotonic_ns) is int
                    and observed_through_monotonic_ns >= deadline_ns
                ):
                    timing_status = "EXCEEDED_10S"
                    timing_issue = "TARGET_RECEIPT_MISSING_AT_DEADLINE"
                    timing_elapsed_ms = (
                        observed_through_monotonic_ns
                        - incident["fault_ended_monotonic_ns"]
                    ) / 1_000_000
                else:
                    timing_status = "INCOMPLETE"
                    timing_issue = timing_issue or "AWAITING_TARGET_RECEIPT"
        timing_rows[stream_id] = {
            "target_timing_status": timing_status,
            "target_timing_elapsed_ms": timing_elapsed_ms,
            "target_timing_issue": timing_issue,
            "target_timing_deadline_monotonic_ns": (
                incident["fault_ended_monotonic_ns"] + RECOVERY_CATCHUP_DEADLINE_NS
                if type(incident.get("fault_ended_monotonic_ns")) is int
                else None
            ),
        }
    incomplete_coverage = sum(
        1 for row in streams.values() if row.get("gap_coverage_status") == "INCOMPLETE"
    )
    pending_coverage = sum(
        1 for row in streams.values() if row.get("gap_coverage_status") == "PENDING"
    )
    complete_coverage = sum(
        1 for row in streams.values() if row.get("gap_coverage_status") == "COMPLETE_TO_TARGET"
    )
    skipped_values = [
        row.get("snapshot_skipped_sequence_count") for row in streams.values()
    ]
    known_skipped_values = [value for value in skipped_values if type(value) is int]
    unknown_skipped_count = len(skipped_values) - len(known_skipped_values)
    return {
        "kind": incident.get("kind"),
        "incident_id": incident.get("incident_id"),
        "window_number": incident.get("window_number"),
        "target_timing_deadline_ms": RECOVERY_CATCHUP_DEADLINE_NS / 1_000_000,
        "target_stream_count": target_count,
        "target_receipt_count": observed_receipt_count,
        "target_reached_count": reached_count,
        "target_timing_status": (
            "EXCEEDED_10S"
            if any(row["target_timing_status"] == "EXCEEDED_10S" for row in timing_rows.values())
            else "WITHIN_10S_FOR_ALL_TARGETS"
            if target_count > 0
            and target_count == len(streams)
            and all(row["target_timing_status"] == "WITHIN_10S" for row in timing_rows.values())
            else "INCOMPLETE"
        ),
        "target_timing_max_ms": max(
            (row["target_timing_elapsed_ms"] for row in timing_rows.values()
             if type(row["target_timing_elapsed_ms"]) in (int, float)),
            default=None,
        ),
        "head_rpc_count": incident.get("head_rpc_count", 0),
        "head_rpc_error_count": incident.get("head_rpc_error_count", 0),
        "head_maintenance_check_count": incident.get("head_maintenance_check_count", 0),
        "head_maintenance_error_count": incident.get("head_maintenance_error_count", 0),
        "head_maintenance_elapsed_ms_total": incident.get(
            "head_maintenance_elapsed_ns_total", 0
        ) / 1_000_000,
        "head_maintenance_elapsed_ms_max": incident.get(
            "head_maintenance_elapsed_ns_max", 0
        ) / 1_000_000,
        "head_capture_budget_deadline_monotonic_ns": incident.get(
            "head_capture_budget_deadline_monotonic_ns"
        ),
        "head_capture_budget_elapsed_ms": (
            None
            if _head_capture_budget_status(incident) == "INCOMPLETE"
            else (
                incident["head_capture_completed_monotonic_ns"]
                - incident["fault_ended_monotonic_ns"]
            )
            / 1_000_000
        ),
        "head_capture_budget_status": _head_capture_budget_status(incident),
        "head_capture_timeout_status": _head_capture_timeout_status(incident),
        "head_capture_error_code": incident.get("head_capture_error_code"),
        "head_rpc_elapsed_ms_total": incident.get("head_rpc_elapsed_ns_total", 0)
        / 1_000_000,
        "head_rpc_elapsed_ms_max": incident.get("head_rpc_elapsed_ns_max", 0)
        / 1_000_000,
        "head_capture_started_monotonic_ns": incident.get(
            "head_capture_started_monotonic_ns"
        ),
        "head_capture_completed_monotonic_ns": incident.get(
            "head_capture_completed_monotonic_ns"
        ),
        "head_capture_elapsed_ms": (
            None
            if type(incident.get("head_capture_started_monotonic_ns")) is not int
            or type(incident.get("head_capture_completed_monotonic_ns")) is not int
            or not (
                0
                < incident["head_capture_started_monotonic_ns"]
                <= incident["head_capture_completed_monotonic_ns"]
            )
            else (
                incident["head_capture_completed_monotonic_ns"]
                - incident["head_capture_started_monotonic_ns"]
            )
            / 1_000_000
        ),
        "fault_started_monotonic_ns": incident.get("fault_started_monotonic_ns"),
        "fault_ended_monotonic_ns": incident.get("fault_ended_monotonic_ns"),
        "snapshot_skipped_sequence_count": (
            sum(known_skipped_values) if unknown_skipped_count == 0 else None
        ),
        "snapshot_skipped_sequence_known_stream_count": len(known_skipped_values),
        "snapshot_skipped_sequence_unknown_stream_count": unknown_skipped_count,
        "gap_coverage_incomplete_stream_count": incomplete_coverage,
        "gap_coverage_status": (
            "INCOMPLETE"
            if incomplete_coverage
            else "PENDING"
            if pending_coverage
            else "COMPLETE_TO_TARGET"
            if complete_coverage == len(streams) and streams
            else "INCOMPLETE_NO_STREAMS"
        ),
        "streams": {
            key: {**dict(row), **timing_rows[key]} for key, row in streams.items()
        },
    }


def _head_capture_budget_status(incident: Mapping[str, Any]) -> str:
    fault_started = incident.get("fault_started_monotonic_ns")
    fault_ended = incident.get("fault_ended_monotonic_ns")
    capture_started = incident.get("head_capture_started_monotonic_ns")
    capture_completed = incident.get("head_capture_completed_monotonic_ns")
    expected_deadline = (
        fault_ended + RECOVERY_CATCHUP_DEADLINE_NS
        if type(fault_ended) is int
        else None
    )
    if (
        type(fault_started) is not int
        or type(fault_ended) is not int
        or type(capture_started) is not int
        or type(capture_completed) is not int
        or expected_deadline is None
        or incident.get("head_capture_budget_deadline_monotonic_ns") != expected_deadline
        or not (0 < fault_started <= fault_ended <= capture_started <= capture_completed)
    ):
        return "INCOMPLETE"
    return (
        "EXCEEDED_10S"
        if capture_completed > expected_deadline
        else "WITHIN_10S"
    )


def _head_capture_timeout_status(incident: Mapping[str, Any]) -> str:
    budget_status = _head_capture_budget_status(incident)
    if budget_status == "EXCEEDED_10S":
        return "CAPTURE_BUDGET_EXCEEDED"
    if budget_status == "INCOMPLETE":
        return "INCOMPLETE"
    if incident.get("head_capture_error_code") == "CAPTURE_BUDGET_EXHAUSTED":
        return "CAPTURE_BUDGET_EXCEEDED"
    if incident.get("head_rpc_error_count", 0):
        return "HEAD_RPC_ERROR"
    return "NO_TIMEOUT"


def aggregate_recovery_observations(
    strategy_results: Mapping[int, Mapping[str, Any]],
    *,
    observed_through_monotonic_ns: int | None = None,
) -> dict[str, Any]:
    incidents = [
        (strategy_index, strategy.get("profile_id"), incident)
        for strategy_index, strategy in strategy_results.items()
        for incident in strategy.get("market_recovery_observations", ())
    ]
    summaries = [
        {
            **recovery_incident_summary(
                incident, observed_through_monotonic_ns=observed_through_monotonic_ns
            ),
            "strategy_index": strategy_index,
            "profile_id": profile_id,
        }
        for strategy_index, profile_id, incident in incidents
    ]
    target_streams = sum(row["target_stream_count"] for row in summaries)
    target_receipts = sum(row["target_receipt_count"] for row in summaries)
    capture_ms = [row["head_capture_elapsed_ms"] for row in summaries]
    expected_incident_count = 0
    expected_head_rpc_count = 0
    expected_head_rpc_coverage_known = True
    expected_by_profile_kind: dict[str, dict[str, int]] = {}
    expected_targets_by_profile_kind: dict[str, dict[str, int | None]] = {}
    for strategy_index, strategy in strategy_results.items():
        profile_id = strategy.get("profile_id")
        profile_key = (
            profile_id
            if type(profile_id) is str and profile_id
            else "UNKNOWN_PROFILE"
        )
        stream_ids = strategy.get("market_stream_ids")
        streams_valid = type(stream_ids) in (tuple, list) and not any(
            type(stream_id) is not str or not stream_id for stream_id in stream_ids
        )
        reconnect_records = strategy.get("reconnect_records", ())
        reconnect_count = (
            len(reconnect_records)
            if type(reconnect_records) in (tuple, list)
            else 0
        )
        slow_count = int(strategy_index == 0 and bool(strategy.get("slow_pause")))
        expected_for_strategy = reconnect_count + slow_count
        expected_incident_count += expected_for_strategy
        profile_expected = expected_by_profile_kind.setdefault(
            profile_key, {"slow_consumer": 0, "reconnect": 0}
        )
        profile_expected["reconnect"] += reconnect_count
        profile_expected["slow_consumer"] += slow_count
        profile_targets = expected_targets_by_profile_kind.setdefault(
            profile_key, {"slow_consumer": 0, "reconnect": 0}
        )
        if streams_valid:
            target_count = len(stream_ids)
            expected_head_rpc_count += expected_for_strategy * target_count
            profile_targets["reconnect"] = (
                int(profile_targets["reconnect"] or 0)
                + reconnect_count * target_count
            )
            profile_targets["slow_consumer"] = (
                int(profile_targets["slow_consumer"] or 0)
                + slow_count * target_count
            )
        elif expected_for_strategy:
            expected_head_rpc_coverage_known = False
            profile_targets["reconnect"] = None if reconnect_count else profile_targets["reconnect"]
            profile_targets["slow_consumer"] = None if slow_count else profile_targets["slow_consumer"]

    observed_by_profile_kind: dict[str, dict[str, int]] = {}
    targets_by_profile_kind: dict[str, dict[str, int]] = {}
    receipts_by_profile_kind: dict[str, dict[str, int]] = {}
    within_by_profile_kind: dict[str, dict[str, int]] = {}
    exceeded_by_profile_kind: dict[str, dict[str, int]] = {}
    reconnect_window_counts_by_profile: dict[str, dict[str, int]] = {}
    reconnect_window_invalid_incident_count = 0
    profile_identity_missing_incident_count = 0
    for row in summaries:
        profile_id = row.get("profile_id")
        if type(profile_id) is not str or not profile_id:
            profile_identity_missing_incident_count += 1
        profile_key = (
            profile_id
            if type(profile_id) is str and profile_id
            else "UNKNOWN_PROFILE"
        )
        kind = row.get("kind")
        if kind not in {"slow_consumer", "reconnect"}:
            continue
        observed_by_profile_kind.setdefault(
            profile_key, {"slow_consumer": 0, "reconnect": 0}
        )[kind] += 1
        targets_by_profile_kind.setdefault(
            profile_key, {"slow_consumer": 0, "reconnect": 0}
        )[kind] += row["target_stream_count"]
        receipts_by_profile_kind.setdefault(
            profile_key, {"slow_consumer": 0, "reconnect": 0}
        )[kind] += row["target_receipt_count"]
        within_by_profile_kind.setdefault(
            profile_key, {"slow_consumer": 0, "reconnect": 0}
        )[kind] += sum(
            stream.get("target_timing_status") == "WITHIN_10S"
            for stream in row["streams"].values()
        )
        exceeded_by_profile_kind.setdefault(
            profile_key, {"slow_consumer": 0, "reconnect": 0}
        )[kind] += sum(
            stream.get("target_timing_status") == "EXCEEDED_10S"
            for stream in row["streams"].values()
        )
        if kind == "reconnect":
            window_number = row.get("window_number")
            if type(window_number) is int and 1 <= window_number <= 5:
                windows = reconnect_window_counts_by_profile.setdefault(profile_key, {})
                window_key = str(window_number)
                windows[window_key] = windows.get(window_key, 0) + 1
            else:
                reconnect_window_invalid_incident_count += 1

    profile_counts: dict[str, dict[str, Any]] = {}
    all_profile_keys = (
        set(expected_by_profile_kind)
        | set(observed_by_profile_kind)
        | set(targets_by_profile_kind)
    )
    for profile_key in sorted(all_profile_keys):
        by_kind: dict[str, dict[str, Any]] = {}
        for kind in ("slow_consumer", "reconnect"):
            expected_incidents = expected_by_profile_kind.get(profile_key, {}).get(kind, 0)
            observed_incidents = observed_by_profile_kind.get(profile_key, {}).get(kind, 0)
            expected_targets = expected_targets_by_profile_kind.get(profile_key, {}).get(kind, 0)
            observed_targets = targets_by_profile_kind.get(profile_key, {}).get(kind, 0)
            within = within_by_profile_kind.get(profile_key, {}).get(kind, 0)
            exceeded = exceeded_by_profile_kind.get(profile_key, {}).get(kind, 0)
            kind_status = (
                "EXCEEDED_10S"
                if exceeded
                else "WITHIN_10S_FOR_ALL_TARGETS"
                if expected_incidents > 0
                and expected_targets is not None
                and observed_incidents == expected_incidents
                and observed_targets == expected_targets
                and within == expected_targets
                else "INCOMPLETE"
            )
            by_kind[kind] = {
                "incident_count": observed_incidents,
                "incident_count_expected_from_observed_faults": expected_incidents,
                "target_stream_count": observed_targets,
                "target_stream_count_expected": expected_targets,
                "target_receipt_count": receipts_by_profile_kind.get(profile_key, {}).get(kind, 0),
                "target_timing_within_10s_count": within,
                "target_timing_exceeded_10s_count": exceeded,
                "target_timing_status": kind_status,
            }
        profile_counts[profile_key] = {
            "by_kind": by_kind,
            "reconnect_window_counts": reconnect_window_counts_by_profile.get(
                profile_key, {}
            ),
        }

    unrecovered_gap_values = [
        strategy.get("unrecovered_gap_count") for strategy in strategy_results.values()
    ]
    unrecovered_gap_events = [
        strategy.get("unrecovered_gap_events") for strategy in strategy_results.values()
    ]
    unrecovered_gap_count = (
        sum(unrecovered_gap_values)
        if unrecovered_gap_values
        and all(type(value) is int and value >= 0 for value in unrecovered_gap_values)
        else None
    )
    unrecovered_gap_event_count = (
        sum(unrecovered_gap_events)
        if unrecovered_gap_events
        and all(type(value) is int and value >= 0 for value in unrecovered_gap_events)
        else None
    )
    complete_rpc_coverage = all(
        incident.get("head_rpc_count") == len(incident.get("streams", {}))
        and incident.get("head_rpc_error_count") == 0
        for _, _, incident in incidents
    )
    incomplete_incidents = sum(row["gap_coverage_status"] == "INCOMPLETE" for row in summaries)
    pending_incidents = sum(row["gap_coverage_status"] == "PENDING" for row in summaries)
    complete_incidents = sum(
        row["gap_coverage_status"] == "COMPLETE_TO_TARGET" for row in summaries
    )
    unknown_snapshot_streams = sum(
        row["snapshot_skipped_sequence_unknown_stream_count"] for row in summaries
    )
    known_snapshot_streams = sum(
        row["snapshot_skipped_sequence_known_stream_count"] for row in summaries
    )
    known_snapshot_skips = sum(
        row["snapshot_skipped_sequence_count"] or 0
        for row in summaries
        if type(row["snapshot_skipped_sequence_count"]) is int
    )
    stream_timing_rows = [
        stream
        for incident in summaries
        for stream in incident["streams"].values()
        if stream.get("head_target_sequence") is not None
    ]
    exceeded_timing_count = sum(
        row.get("target_timing_status") == "EXCEEDED_10S" for row in stream_timing_rows
    )
    within_timing_count = sum(
        row.get("target_timing_status") == "WITHIN_10S" for row in stream_timing_rows
    )
    timing_status = (
        "EXCEEDED_10S"
        if exceeded_timing_count
        else "WITHIN_10S_FOR_ALL_TARGETS"
        if (
            expected_incident_count > 0
            and expected_head_rpc_coverage_known
            and profile_identity_missing_incident_count == 0
            and reconnect_window_invalid_incident_count == 0
            and len(summaries) == expected_incident_count
            and target_streams == expected_head_rpc_count
            and within_timing_count == expected_head_rpc_count
            and sum(row["head_rpc_count"] for row in summaries)
            == expected_head_rpc_count
            and complete_rpc_coverage
        )
        else "INCOMPLETE"
    )
    return {
        "evidence_scope": "same-process synthetic LOAD01 mid-run recovery observation",
        "incidents": summaries,
        "profile_counts": profile_counts,
        "profile_identity_missing_incident_count": profile_identity_missing_incident_count,
        "reconnect_window_invalid_incident_count": reconnect_window_invalid_incident_count,
        "incident_count": len(summaries),
        "incident_count_expected_from_observed_faults": expected_incident_count,
        "head_rpc_count": sum(row["head_rpc_count"] for row in summaries),
        "head_rpc_count_expected_from_observed_faults": expected_head_rpc_count,
        "head_rpc_error_count": sum(row["head_rpc_error_count"] for row in summaries),
        "head_capture_elapsed_ms_total": sum(value or 0.0 for value in capture_ms),
        "head_capture_elapsed_ms_max": max((value for value in capture_ms if value is not None), default=0.0),
        "observed_through_monotonic_ns": observed_through_monotonic_ns,
        "target_timing_deadline_ms": RECOVERY_CATCHUP_DEADLINE_NS / 1_000_000,
        "target_stream_count": target_streams,
        "target_receipt_count": target_receipts,
        "target_reached_count": sum(row["target_reached_count"] for row in summaries),
        "target_timing_within_10s_count": within_timing_count,
        "target_timing_exceeded_10s_count": exceeded_timing_count,
        "target_timing_status": timing_status,
        "snapshot_skipped_sequence_count": (
            known_snapshot_skips if unknown_snapshot_streams == 0 else None
        ),
        "snapshot_skipped_sequence_known_stream_count": known_snapshot_streams,
        "snapshot_skipped_sequence_unknown_stream_count": unknown_snapshot_streams,
        "gap_coverage_incomplete_incident_count": incomplete_incidents,
        "unrecovered_gap_count": unrecovered_gap_count,
        "unrecovered_gap_event_count": unrecovered_gap_event_count,
        "unrecovered_gap_events_count_unknown_strategy_count": sum(
            type(value) is not int or value < 0 for value in unrecovered_gap_events
        ),
        "unrecovered_gap_count_unknown_strategy_count": sum(
            type(value) is not int or value < 0 for value in unrecovered_gap_values
        ),
        "gap_coverage_status": (
            "INCOMPLETE"
            if incomplete_incidents or unrecovered_gap_count is None or unrecovered_gap_count > 0
            else "PENDING"
            if pending_incidents
            else "COMPLETE_TO_TARGET"
            if complete_incidents == len(summaries) and summaries
            else "INCOMPLETE_NO_OBSERVATIONS"
        ),
        "interpretation": (
            "A post-capture receipt at or beyond a fixed same-epoch head proves arrival at that target only. "
            "Snapshot cursor skips remain separately counted and make interval coverage incomplete."
        ),
    }
