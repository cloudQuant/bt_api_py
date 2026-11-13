"""Declared association between one observation capture and a derived review.

The separate immutable records are not a transaction or proof of identity,
source, time ordering or durable storage. Unknown writes are never retried.
"""

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review
from bt_api_py import trading_calendar_supplement_capture as capture

_INTENT_V1 = "bt_api.calendar-observation-derived-intent/1"
_INTENT = "bt_api.calendar-observation-derived-intent/2"
_CLAIM = "bt_api.calendar-observation-derived-claim/1"
_RESULT = "bt_api.calendar-observation-derived-result/1"


def _intent(run_id, capture_id, parent_id, parent_sha256, derived_id):
    return {
        "schema": _INTENT,
        "run_id": run_id,
        "capture_id": capture_id,
        "parent_id": parent_id,
        "parent_sha256": parent_sha256,
        "derived_id": derived_id,
        "association": "DECLARED_ONLY",
        "authentication": "NOT_PROVEN",
    }


def _check_intent(value, run_id):
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema",
            "run_id",
            "capture_id",
            "parent_id",
            "parent_sha256",
            "derived_id",
            "association",
            "authentication",
        }
        or value["schema"] not in {_INTENT_V1, _INTENT}
        or value["run_id"] != run_id
        or any(not runs._id(value[k]) for k in ("run_id", "capture_id", "parent_id", "derived_id"))
        or len({value[k] for k in ("run_id", "capture_id", "parent_id", "derived_id")}) != 4
        or type(value["parent_sha256"]) is not str
        or runs._SHA.fullmatch(value["parent_sha256"]) is None
        or value["association"] != "DECLARED_ONLY"
        or value["authentication"] != "NOT_PROVEN"
    ):
        raise ValueError("invalid association intent")
    return value


def _claim(intent, intent_sha):
    return {
        "schema": _CLAIM,
        "derived_id": intent["derived_id"],
        "run_id": intent["run_id"],
        "intent_sha256": intent_sha,
        "association": "DECLARED_ONLY",
        "authentication": "NOT_PROVEN",
    }


def _read_claim(directory, chain, intent):
    if intent["schema"] == _INTENT_V1:
        return
    raw = runs._read_entry(directory, chain, f"derived-claim-{intent['derived_id']}.json")
    if raw is None:
        raise ValueError("claim unavailable")
    body, _ = storage._unwrap(raw)
    expected = _claim(intent, storage._unwrap(storage._envelope(intent))[1])
    if body != expected:
        raise ValueError("claim changed")


def show_claim(*, root, filesystem, derived_id):
    """Inspect the declared claim at its original ID; never infer commit success."""
    summary = {
        "derived_id": derived_id if runs._id(derived_id) else "",
        "state": "UNKNOWN",
        "association": "DECLARED_ONLY",
        "authentication": "NOT_PROVEN",
    }
    if not runs._id(derived_id) or filesystem != runs.TRUSTED_LOCAL_POSIX:
        return summary
    try:
        fds, chain = runs._open_runs(root, create=False)
        try:
            raw = runs._read_entry(fds[-1], chain, f"derived-claim-{derived_id}.json")
            if raw is None:
                summary["state"] = "NOT_OBSERVED"
                return summary
            body, _ = storage._unwrap(raw)
            if (
                set(body)
                != {
                    "schema",
                    "derived_id",
                    "run_id",
                    "intent_sha256",
                    "association",
                    "authentication",
                }
                or body["schema"] != _CLAIM
                or body["derived_id"] != derived_id
                or not runs._id(body["run_id"])
                or body["run_id"] == derived_id
                or type(body["intent_sha256"]) is not str
                or runs._SHA.fullmatch(body["intent_sha256"]) is None
                or body["association"] != "DECLARED_ONLY"
                or body["authentication"] != "NOT_PROVEN"
            ):
                return summary
            summary.update(
                state="VISIBLE_UNAUTHENTICATED",
                run_id=body["run_id"],
                intent_sha256=body["intent_sha256"],
            )
            return summary
        finally:
            runs._close(fds)
    except Exception:
        return summary


def _observation(directory, chain, intent):
    rid = intent["run_id"]
    raw_intent = runs._read_entry(directory, chain, f"run-{rid}.intent")
    raw_result = runs._read_entry(directory, chain, f"run-{rid}.json")
    if raw_intent is None or raw_result is None:
        raise ValueError("run incomplete")
    run_intent = runs._valid_intent(raw_intent, rid)
    result = runs._valid_result(raw_result, rid, run_intent)
    if (
        run_intent["schema"] != runs._INTENT_SCHEMA_V6
        or run_intent["capture_id"] != intent["capture_id"]
        or result["capture_state"] != "COMMITTED"
    ):
        raise ValueError("capture not committed")
    return raw_intent, raw_result, result["capture_payload_sha256"]


def _inputs(root, filesystem, directory, chain, intent):
    _read_claim(directory, chain, intent)
    run_intent, run_result, digest = _observation(directory, chain, intent)
    observed = capture.read_capture(
        root=root, capture_id=intent["capture_id"], filesystem=filesystem, include_content=True
    )
    if (
        observed.get("status") != "VISIBLE_UNAUTHENTICATED"
        or observed.get("capture_id") != intent["capture_id"]
        or observed.get("run_id") != intent["run_id"]
        or observed.get("payload_sha256") != digest
        or type(observed.get("content")) is not dict
    ):
        raise ValueError("capture changed")
    parent = review.show(root=root, candidate_id=intent["parent_id"], filesystem=filesystem)
    if (
        parent.get("status") != "VISIBLE_UNAUTHENTICATED"
        or parent.get("payload_sha256") != intent["parent_sha256"]
        or parent["bundle"]["schema"] not in {review._SCHEMA, review._SCHEMA_REFERENCES}
    ):
        raise ValueError("parent changed")
    if _observation(directory, chain, intent) != (run_intent, run_result, digest):
        raise ValueError("run changed")
    _read_claim(directory, chain, intent)
    return observed


def run_associated(*, base_kwargs, parent_id, parent_sha256, derived_id, source_texts, decisions):
    root, filesystem, run_id, capture_id = (
        base_kwargs[k] for k in ("root", "filesystem", "run_id", "capture_id")
    )
    intent = _intent(run_id, capture_id, parent_id, parent_sha256, derived_id)
    result = {
        "run_id": run_id if runs._id(run_id) else "",
        "capture_id": capture_id if runs._id(capture_id) else "",
        "derived_id": derived_id if runs._id(derived_id) else "",
        "derived_state": "NOT_ATTEMPTED",
        "association": "DECLARED_ONLY",
        "authentication": "NOT_PROVEN",
    }
    try:
        if not runs._valid_run_inputs(**base_kwargs):
            result["reason"] = "INVALID_ARGUMENTS"
            return result
        from bt_api_py.trading_calendar_check import _absolute_path

        try:
            for path in (root, base_kwargs["config"], base_kwargs["supplement"]):
                _absolute_path(path)
        except ValueError:
            result["reason"] = "INVALID_ABSOLUTE_PATH"
            return result
        from bt_api_py.trading_calendar_preview import _supervisor_supported

        if not _supervisor_supported():
            result["reason"] = "SUPERVISOR_UNSUPPORTED"
            return result
        _check_intent(intent, run_id)
        if filesystem != runs.TRUSTED_LOCAL_POSIX or not runs._supported():
            raise ValueError("filesystem")
        if derived_id == base_kwargs.get("candidate_id") or parent_id == base_kwargs.get(
            "candidate_id"
        ):
            raise ValueError("id collision")
        other_ids = list((base_kwargs.get("ine_evidence_ids") or {}).values())
        retry = base_kwargs.get("ine_retry_config") or {}
        other_ids.extend(eid for ids in (retry.get("evidence_ids") or {}).values() for eid in ids)
        if parent_id in other_ids or derived_id in other_ids:
            raise ValueError("id collision")
        review._bounded(source_texts)
        review._bounded(decisions)
        if type(source_texts) is not dict or type(decisions) is not list:
            raise ValueError("invalid inputs")
        # Freeze caller inputs and detect later mutations before building.
        frozen = storage._json([source_texts, decisions])
        if any(
            storage.detail._sensitive([source_texts, decisions], path)
            for path in (root, base_kwargs["config"], base_kwargs["supplement"])
        ):
            raise ValueError("sensitive input")
        import json

        detached = json.loads(frozen, object_pairs_hook=runs._unique_pairs)
        parent = review.show(root=root, candidate_id=parent_id, filesystem=filesystem)
        if (
            parent.get("status") != "VISIBLE_UNAUTHENTICATED"
            or parent.get("payload_sha256") != parent_sha256
            or parent["bundle"]["schema"] not in {review._SCHEMA, review._SCHEMA_REFERENCES}
        ):
            result["reason"] = "PARENT_UNAVAILABLE_OR_CHANGED"
            return result
        review_fds, review_chain = review._open(root, False)
        try:
            if (
                storage._read(
                    review_fds[-1],
                    review_chain,
                    f"candidate-{derived_id}.json",
                    review._OUTPUT_LIMIT,
                )
                is not None
            ):
                result["reason"] = "DERIVED_ID_EXISTS"
                return result
        finally:
            runs._close(review_fds)
        fds, chain = runs._open_runs(root, create=True)
    except (Exception, KeyboardInterrupt):
        result["reason"] = "INVALID_OR_UNAVAILABLE_INPUT"
        return result
    directory = fds[-1]
    try:
        intent_raw = storage._envelope(intent)
        claim_raw = storage._envelope(_claim(intent, storage._unwrap(intent_raw)[1]))
        result["claim_installation"] = "COMMIT_UNKNOWN"
        claim_state, claim_reason = runs._write_once(
            directory, chain, f"derived-claim-{derived_id}.json", claim_raw
        )
        result["claim_installation"] = claim_state
        if claim_state != "COMMITTED":
            result["reason"] = (
                "DERIVED_ID_CLAIM_" + claim_state if claim_reason == "ID_EXISTS" else claim_reason
            )
            return result
        _read_claim(directory, chain, intent)
        state, reason = runs._write_once(directory, chain, f"derived-{run_id}.intent", intent_raw)
        if state != "COMMITTED":
            result.update(association_intent=state, reason=reason)
            return result
        result["association_intent"] = "COMMITTED"
        _read_claim(directory, chain, intent)
        event = runs.run_once(**base_kwargs)
        result.update(event)
        result.update(
            derived_id=derived_id,
            derived_state="NOT_ATTEMPTED",
            association="DECLARED_ONLY",
            authentication="NOT_PROVEN",
        )
        if (
            event.get("run_id") != run_id
            or event.get("capture_id") != capture_id
            or event.get("intent_installation") != "COMMITTED"
            or event.get("record_installation") != "COMMITTED"
            or event.get("capture_state") != "COMMITTED"
            or event.get("capture_payload_sha256") is None
            or event.get("cleanup_status") == "UNCERTAIN"
        ):
            result["reason"] = "RUN_OR_CAPTURE_UNCERTAIN"
            return result
        observed = _inputs(root, filesystem, directory, chain, intent)
        if (
            observed["payload_sha256"] != event["capture_payload_sha256"]
            or storage._json([source_texts, decisions]) != frozen
        ):
            raise ValueError("inputs changed")
        built = review.build_derived_once(
            root=root,
            candidate_id=derived_id,
            parent_id=parent_id,
            parent_sha256=parent_sha256,
            supplement=observed["content"],
            source_texts=detached[0],
            decisions=detached[1],
            filesystem=filesystem,
            emit=lambda _: None,
        )
        result["derived_state"] = built["status"]
        if built["status"] != "COMMITTED":
            result["reason"] = "DERIVED_" + built["status"]
            return result
        derived = review.show(root=root, candidate_id=derived_id, filesystem=filesystem)
        reobserved = _inputs(root, filesystem, directory, chain, intent)
        if (
            derived.get("status") != "VISIBLE_UNAUTHENTICATED"
            or reobserved != observed
            or derived["bundle"]["assembly"]["parent"]
            != {"candidate_id": parent_id, "payload_sha256": parent_sha256}
            or derived["bundle"]["assembly"]["supplement"] != observed["content"]
        ):
            raise ValueError("derived verification failed")
        body = {
            "schema": _RESULT,
            "intent_sha256": storage._unwrap(intent_raw)[1],
            "capture_sha256": observed["payload_sha256"],
            "derived_sha256": derived["payload_sha256"],
            "state": "COMMITTED",
        }
        state, reason = runs._write_once(
            directory, chain, f"derived-{run_id}.json", storage._envelope(body)
        )
        if state == "COMMITTED":
            checked = show_association(root=root, filesystem=filesystem, run_id=run_id)
            if (
                checked is None
                or checked.get("state") != "VISIBLE_UNAUTHENTICATED"
                or checked.get("capture_sha256") != observed["payload_sha256"]
                or checked.get("derived_sha256") != derived["payload_sha256"]
            ):
                state, reason = "COMMIT_UNKNOWN", "ASSOCIATION_RECHECK_UNKNOWN"
        result.update(
            association_record=state,
            derived_payload_sha256=derived["payload_sha256"] if state == "COMMITTED" else None,
            reason="NONE" if state == "COMMITTED" else reason,
        )
        if state != "COMMITTED":
            result["derived_state"] = "COMMIT_UNKNOWN"
        return result
    except (Exception, KeyboardInterrupt):
        result.update(
            derived_state="COMMIT_UNKNOWN",
            derived_payload_sha256=None,
            reason="ASSOCIATION_OUTCOME_UNKNOWN",
        )
        return result
    finally:
        runs._close(fds)


def show_association(*, root, filesystem, run_id):
    summary = {"state": "UNKNOWN", "association": "DECLARED_ONLY", "authentication": "NOT_PROVEN"}
    try:
        fds, chain = runs._open_runs(root, create=False)
    except Exception:
        return summary
    try:
        directory = fds[-1]
        raw = runs._read_entry(directory, chain, f"derived-{run_id}.intent")
        if raw is None:
            return None
        intent, intent_sha = storage._unwrap(raw)
        _check_intent(intent, run_id)
        summary.update(
            capture_id=intent["capture_id"],
            parent_id=intent["parent_id"],
            parent_sha256=intent["parent_sha256"],
            derived_id=intent["derived_id"],
        )
        _read_claim(directory, chain, intent)
        result_raw = runs._read_entry(directory, chain, f"derived-{run_id}.json")
        if result_raw is None:
            return summary
        body, _ = storage._unwrap(result_raw)
        if (
            type(body) is not dict
            or set(body) != {"schema", "intent_sha256", "capture_sha256", "derived_sha256", "state"}
            or body["schema"] != _RESULT
            or body["intent_sha256"] != intent_sha
            or body["state"] != "COMMITTED"
            or any(
                type(body[k]) is not str or runs._SHA.fullmatch(body[k]) is None
                for k in ("capture_sha256", "derived_sha256")
            )
        ):
            return summary
        observed = _inputs(root, filesystem, directory, chain, intent)
        derived = review.show(root=root, candidate_id=intent["derived_id"], filesystem=filesystem)
        if (
            observed["payload_sha256"] == body["capture_sha256"]
            and derived.get("status") == "VISIBLE_UNAUTHENTICATED"
            and derived["payload_sha256"] == body["derived_sha256"]
            and derived["bundle"]["assembly"]["parent"]
            == {"candidate_id": intent["parent_id"], "payload_sha256": intent["parent_sha256"]}
            and derived["bundle"]["assembly"]["supplement"] == observed["content"]
            and _inputs(root, filesystem, directory, chain, intent) == observed
        ):
            summary.update(
                state="VISIBLE_UNAUTHENTICATED",
                capture_sha256=body["capture_sha256"],
                derived_sha256=body["derived_sha256"],
            )
        return summary
    except Exception:
        return summary
    finally:
        runs._close(fds)
