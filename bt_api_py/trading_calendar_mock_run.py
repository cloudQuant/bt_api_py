"""One isolated, explicitly fictional run through the normal observation entry."""

import json
import os
import tempfile

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mock as mock
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review


def read_audit(directory, chain, run_id, root):
    """Read the original declaration only; visibility does not settle a write."""
    unknown = {"state": "UNKNOWN", "environment": "SIMULATION_ONLY", "data_origin": "MOCK"}
    try:
        raw = runs._read_entry(directory, chain, f"mock-{run_id}.json")
        if raw is None:
            return None
        body, digest = storage._unwrap(raw)
        expected = {
            "schema",
            "data_origin",
            "environment",
            "publication",
            "source_authentication",
            "run_id",
            "capture_id",
            "parent_id",
            "derived_id",
            "state",
            "simulation_root",
            "year",
        }
        ids = [body[key] for key in ("run_id", "capture_id", "parent_id", "derived_id")]
        if (
            set(body) != expected
            or body["schema"] != "bt_api.calendar-mock-run/1"
            or body["run_id"] != run_id
            or body["simulation_root"] != root
            or any(not runs._id(value) for value in ids)
            or len(set(ids)) != 4
            or body["data_origin"] != "MOCK"
            or body["environment"] != "SIMULATION_ONLY"
            or body["publication"] != "NOT_AUTHORIZED"
            or body["source_authentication"] != "NOT_PROVEN"
            or body["state"] != "NOT_ATTEMPTED"
            or type(body["year"]) is not int
            or not 1901 <= body["year"] <= 9998
        ):
            return unknown
        return {
            "state": "VISIBLE_UNAUTHENTICATED",
            "data_origin": "MOCK",
            "environment": "SIMULATION_ONLY",
            "capture_id": body["capture_id"],
            "parent_id": body["parent_id"],
            "derived_id": body["derived_id"],
            "year": body["year"],
            "payload_sha256": digest,
        }
    except Exception:
        return unknown


def run_mock(
    *,
    root,
    config,
    supplement,
    budget_seconds,
    enable_shfe,
    filesystem,
    run_id,
    candidate_id,
    fetch_product_navigation,
    ine_evidence_ids,
    ine_retry_config,
    capture_id,
    parent_id,
    parent_sha256,
    derived_id,
    source_texts,
    decisions,
    year,
    emit,
):
    """Create a private temporary root; never write or publish to a caller's root.

    Callers supply all four stable IDs and receive them before the first write.
    The resulting root is retained for read-only inspection and caller cleanup.
    This routine never writes CURRENT. It does not establish a security boundary
    against another process under the same OS identity or hostile file replacement.
    """
    result = {
        "data_origin": "MOCK",
        "environment": "SIMULATION_ONLY",
        "publication": "NOT_AUTHORIZED",
        "source_authentication": "NOT_PROVEN",
        "run_id": run_id,
        "capture_id": capture_id,
        "parent_id": parent_id,
        "derived_id": derived_id,
        "state": "NOT_ATTEMPTED",
        "overall": "INCOMPLETE",
        "simulation_publication": "NOT_ATTEMPTED",
    }
    ids = (run_id, capture_id, parent_id, derived_id)
    if (
        any(not runs._id(value) for value in ids)
        or len(set(ids)) != 4
        or type(year) is not int
        or not 1901 <= year <= 9998
        or filesystem != runs.TRUSTED_LOCAL_POSIX
        or not runs._supported()
        or root is not None
        or config is not None
        or supplement is not None
        or enable_shfe is not False
        or candidate_id
        or fetch_product_navigation
        or ine_evidence_ids is not None
        or ine_retry_config is not None
        or parent_sha256
        or source_texts is not None
        or decisions is not None
        or not callable(emit)
        or not runs._valid_number(budget_seconds, 0.001, 86400.0)
    ):
        result["reason"] = "INVALID_ARGUMENTS"
        return result
    try:
        value, addition = mock.build(year)
        temporary_root = os.path.join(
            os.path.realpath(tempfile.gettempdir()), f"bt-calendar-MOCK-SIMULATION_ONLY-{run_id}"
        )
        result["simulation_root"] = temporary_root
        emit({"event": "MOCK_IDS_ALLOCATED", **result})
    except (Exception, KeyboardInterrupt):
        result["reason"] = "ALLOCATION_NOT_CONFIRMED"
        return result
    try:
        try:
            os.mkdir(temporary_root, 0o700)
        except FileExistsError:
            result.update(state="NOT_COMMITTED", reason="RUN_ID_EXISTS_RECONCILE_FIRST")
            return result
        emit({"event": "MOCK_ROOT_CREATED", "simulation_root": temporary_root, **result})
        fds, chain = runs._open_runs(temporary_root, create=True)
        try:
            audit = {
                "schema": "bt_api.calendar-mock-run/1",
                **{
                    key: value
                    for key, value in result.items()
                    if key not in {"overall", "simulation_publication"}
                },
                "year": year,
            }
            state, reason = runs._write_once(
                fds[-1], chain, f"mock-{run_id}.json", storage._envelope(audit)
            )
        finally:
            runs._close(fds)
        if state != "COMMITTED":
            result.update(state=state, reason=reason)
            return result
        parent = review.build_once(
            root=temporary_root,
            candidate_id=parent_id,
            input_data=value,
            filesystem=filesystem,
            emit=lambda _: None,
        )
        result["parent_state"] = parent["status"]
        if parent["status"] != "COMMITTED":
            result.update(state=parent["status"], reason="PARENT_" + parent["status"])
            return result
        shown = review.show(root=temporary_root, candidate_id=parent_id, filesystem=filesystem)
        if shown.get("status") != "VISIBLE_UNAUTHENTICATED":
            result.update(state="COMMIT_UNKNOWN", reason="PARENT_RECHECK_UNKNOWN")
            return result
        config_path = os.path.join(temporary_root, "MOCK-config.json")
        input_path = os.path.join(temporary_root, "MOCK-supplement.json")
        for path, document in (
            (config_path, {"calendars": {"root": temporary_root}}),
            (input_path, addition),
        ):
            with open(path, "x", encoding="utf-8") as file:
                json.dump(document, file)
        observed = runs.run_once(
            root=temporary_root,
            config=config_path,
            supplement=input_path,
            budget_seconds=budget_seconds,
            enable_shfe=False,
            filesystem=filesystem,
            run_id=run_id,
            capture_id=capture_id,
            parent_id=parent_id,
            parent_sha256=shown["payload_sha256"],
            derived_id=derived_id,
            source_texts={},
            decisions=[],
        )
        result.update(observed)
        result.update(
            data_origin="MOCK",
            environment="SIMULATION_ONLY",
            publication="NOT_AUTHORIZED",
            source_authentication="NOT_PROVEN",
        )
        result["parent_sha256"] = shown["payload_sha256"]
        result["candidate_state"] = observed.get("derived_state", "NOT_ATTEMPTED")
        result["state"] = (
            "INCOMPLETE" if result["candidate_state"] == "COMMITTED" else result["candidate_state"]
        )
        return result
    except (Exception, KeyboardInterrupt):
        result.update(state="COMMIT_UNKNOWN", reason="INTERRUPTED_OR_IO_UNKNOWN")
        return result
