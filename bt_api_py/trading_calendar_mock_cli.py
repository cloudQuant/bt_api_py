"""Explicit MOCK/SIMULATION_ONLY calendar update orchestration.

This uses only an existing mock run's private temporary or declared root. Its CURRENT is
readable by a normal low-level store if deliberately configured there: this is
not a production isolation or authorization boundary. Reviewers and times are
simulation declarations, and missing or uncertain writes must be inspected by
original ID rather than retried.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
import uuid
from dataclasses import asdict
from pathlib import Path

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication

_LABEL = {
    "data_origin": "MOCK",
    "environment": "SIMULATION_ONLY",
    "source_authentication": "NOT_PROVEN",
}
_SCHEMA = "bt_api.calendar-mock-pending-review/1"
_SUPPLEMENT_SCHEMA = "bt_api.calendar-mock-pending-review/2"


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)


def allocate(*, candidate_id=None):
    ids = {
        "candidate_id": candidate_id or uuid.uuid4().hex,
        "review_id": uuid.uuid4().hex,
        "publication_id": "MOCK_" + uuid.uuid4().hex,
        "version_id": "MOCK_" + uuid.uuid4().hex,
    }
    if not runs._id(ids["candidate_id"]) or not runs._id(ids["review_id"]):
        raise ValueError("invalid allocated identity")
    if len({ids["candidate_id"], ids["review_id"], ids["publication_id"], ids["version_id"]}) != 4:
        raise ValueError("duplicate allocated identity")
    return ids


def _report(event, **fields):
    _emit({"event": event, **_LABEL, **fields})


def _retain_pending(operator, ids, proposal, *, input_binding=None):
    body = {
        "schema": _SCHEMA,
        **_LABEL,
        "run_id": operator.run_id,
        **ids,
        "proposal": proposal,
        "proposal_sha256": hashlib.sha256(_canonical(proposal)).hexdigest(),
    }
    if input_binding is not None:
        body["schema"] = _SUPPLEMENT_SCHEMA
        body["input_binding"] = input_binding
    encoded = storage._envelope(body)
    if len(encoded) > review._OUTPUT_LIMIT:
        return {"status": "NOT_COMMITTED", "reason": "PENDING_REVIEW_TOO_LARGE"}
    try:
        operator._guard()
        fds, chain = review._open(operator.root, False)
    except Exception:
        return {"status": "NOT_COMMITTED", "reason": "PENDING_REVIEW_UNAVAILABLE"}
    lock = None
    attempted = False
    try:
        lock, identity = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, identity)
        operator._guard()
        attempted = True
        state = storage._write(
            fds[-1], chain, f"mock-review-{ids['review_id']}.json", encoded, review._OUTPUT_LIMIT
        )
        if state == "COMMITTED":
            observed = _read_pending(operator, ids["review_id"])
            if observed.get("status") != "VISIBLE_UNAUTHENTICATED" or observed.get("body") != body:
                state = "COMMIT_UNKNOWN"
        return {
            "status": state,
            "review_payload_sha256": storage._unwrap(encoded)[1] if state == "COMMITTED" else None,
        }
    except (KeyboardInterrupt, SystemExit) as exc:
        exc.mock_outcome = {
            **_LABEL,
            "operation": "PENDING_REVIEW",
            "run_id": operator.run_id,
            **ids,
            "status": "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
        }
        raise
    except Exception:
        return {
            "status": "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
            "reason": "PENDING_REVIEW_IO_UNKNOWN",
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def _read_pending(operator, review_id):
    if not runs._id(review_id):
        return {"status": "UNKNOWN"}
    try:
        operator._guard()
        fds, chain = review._open(operator.root, False)
        try:
            raw = storage._read(
                fds[-1], chain, f"mock-review-{review_id}.json", review._OUTPUT_LIMIT
            )
            if raw is None:
                return {"status": "NOT_OBSERVED"}
            body, digest = storage._unwrap(raw)
            if (
                type(body) is not dict
                or set(body)
                != (
                    {
                        "schema",
                        *_LABEL,
                        "run_id",
                        "candidate_id",
                        "review_id",
                        "publication_id",
                        "version_id",
                        "proposal",
                        "proposal_sha256",
                    }
                    | ({"input_binding"} if body.get("schema") == _SUPPLEMENT_SCHEMA else set())
                )
                or body["schema"] not in {_SCHEMA, _SUPPLEMENT_SCHEMA}
                or body["run_id"] != operator.run_id
                or body["review_id"] != review_id
                or any(body[key] != value for key, value in _LABEL.items())
                or not runs._id(body["candidate_id"])
                or not runs._id(body["review_id"])
                or type(body["publication_id"]) is not str
                or not body["publication_id"].startswith("MOCK_")
                or type(body["version_id"]) is not str
                or not body["version_id"].startswith("MOCK_")
                or type(body["proposal"]) is not dict
                or body["proposal"].get("candidate_id") != body["candidate_id"]
                or body["proposal"].get("policy", {}).get("mode") != "manual"
                or body["proposal"].get("status") not in {"FIRST_SIMULATION_PUBLICATION", "CHANGED"}
                or hashlib.sha256(_canonical(body["proposal"])).hexdigest()
                != body["proposal_sha256"]
            ):
                raise ValueError("pending review changed")
            if body["schema"] == _SUPPLEMENT_SCHEMA:
                binding = body["input_binding"]
                if (
                    type(binding) is not dict
                    or set(binding) != {"path", "capture_id", "normalized_content_sha256"}
                    or type(binding["path"]) is not str
                    or not os.path.isabs(binding["path"])
                    or not (
                        (
                            binding["capture_id"] is None
                            and binding["normalized_content_sha256"] is None
                        )
                        or (
                            runs._id(binding["capture_id"])
                            and type(binding["normalized_content_sha256"]) is str
                            and runs._SHA.fullmatch(binding["normalized_content_sha256"])
                            is not None
                        )
                    )
                ):
                    raise ValueError("pending input binding invalid")
            operator._guard()
            return {"status": "VISIBLE_UNAUTHENTICATED", "body": body, "payload_sha256": digest}
        finally:
            runs._close(fds)
    except Exception:
        return {"status": "UNKNOWN"}


def _call(function, *, operator, ids, **kwargs):
    try:
        return function(**kwargs)
    except (KeyboardInterrupt, SystemExit) as exc:
        _report(
            "MOCK_INTERRUPTED",
            simulation_root=operator.root,
            run_id=operator.run_id,
            **ids,
            outcome=getattr(exc, "mock_outcome", {"status": "UNKNOWN"}),
        )
        raise


def _verify_supplement(operator, binding):
    """Re-read a pending input through the existing protected reader."""
    try:
        from bt_api_ctp.calendar_supplement_reader import read_supplement

        from bt_api_py import trading_calendar_supplement_capture as capture
        from bt_api_py.trading_calendar_check import _read_checked_supplement
        from bt_api_py.trading_calendar_observation_derived import show_association

        operator._guard()
        if binding["capture_id"] is None:
            try:
                os.lstat(binding["path"])
            except FileNotFoundError:
                operator._guard()
                return "MATCH"
            return "CHANGED"
        association = show_association(
            root=operator.root, filesystem=runs.TRUSTED_LOCAL_POSIX, run_id=operator.run_id
        )
        if (
            association.get("state") != "VISIBLE_UNAUTHENTICATED"
            or association.get("capture_id") != binding["capture_id"]
        ):
            return "UNKNOWN"
        captured = capture.read_capture(
            root=operator.root,
            capture_id=binding["capture_id"],
            filesystem=runs.TRUSTED_LOCAL_POSIX,
            include_content=True,
        )
        status, read = _read_checked_supplement(Path(binding["path"]), read_supplement)
        if status == "MISSING":
            return "CHANGED"
        if status != "CANDIDATE" or read is None or read.candidate is None:
            return "UNKNOWN"
        content, digest = capture._normalize(read.candidate)
        operator._guard()
        if (
            captured.get("status") != "VISIBLE_UNAUTHENTICATED"
            or captured.get("run_id") != operator.run_id
            or captured.get("normalized_content_sha256") != binding["normalized_content_sha256"]
        ):
            return "UNKNOWN"
        return (
            "MATCH"
            if digest == binding["normalized_content_sha256"] and content == captured.get("content")
            else "CHANGED"
        )
    except Exception:
        return "UNKNOWN"


def _result_code(result):
    status = result.get("status")
    if status == "COMMITTED" and result.get("current_visibility") == "VISIBLE":
        return 0
    if status in {"PENDING_REVIEW", "SAME"}:
        return 10
    if status in {"UNKNOWN", "COMMIT_UNKNOWN"}:
        return 25
    return 24


def _finish(operator, ids, published):
    output = {
        **_LABEL,
        "simulation_root": operator.root,
        "run_id": operator.run_id,
        **ids,
        **published,
    }
    if published.get("status") != "COMMITTED":
        return output
    proof = published.get("result")
    if (
        type(proof) is not dict
        or proof.get("visibility") != "COMMITTED"
        or proof.get("fence") != "CLEAR"
        or proof.get("post_commit") != "COMPLETE"
        or published.get("publication") != "SIMULATION_ONLY"
    ):
        output.update(status="COMMIT_UNKNOWN", reason="PUBLICATION_PROOF_INCOMPLETE")
        return output
    try:
        observation = _call(
            operator.verify_publication,
            operator=operator,
            ids=ids,
            publication_id=ids["publication_id"],
        )
        output["current_visibility"] = observation.get("status")
    except Exception:
        output["current_visibility"] = "UNKNOWN"
    if output["current_visibility"] != "VISIBLE":
        output.update(status="COMMIT_UNKNOWN", reason="CURRENT_RECHECK_UNKNOWN")
    return output


def _act(operator, ids, proposal, *, input_binding=None):
    context = {**_LABEL, "simulation_root": operator.root, "run_id": operator.run_id, **ids}
    if proposal.get("status") not in {"FIRST_SIMULATION_PUBLICATION", "SAME", "CHANGED"}:
        return {**context, "status": "UNKNOWN", "reason": "PROPOSAL_UNAVAILABLE"}
    context["data_origin"] = proposal["data_origin"]
    mode = proposal.get("policy", {}).get("mode")
    if mode == "manual" and proposal["status"] != "SAME":
        try:
            saved = (
                _retain_pending(operator, ids, proposal)
                if input_binding is None
                else _retain_pending(operator, ids, proposal, input_binding=input_binding)
            )
        except (KeyboardInterrupt, SystemExit) as exc:
            _report(
                "MOCK_INTERRUPTED",
                simulation_root=operator.root,
                run_id=operator.run_id,
                **ids,
                outcome=getattr(exc, "mock_outcome", {"status": "UNKNOWN"}),
            )
            raise
        if saved.get("status") != "COMMITTED":
            return {
                **context,
                **saved,
                "reason": saved.get("reason", "PENDING_REVIEW_OUTCOME_UNKNOWN"),
            }
        return {
            **context,
            "status": "PENDING_REVIEW",
            "review_payload_sha256": saved["review_payload_sha256"],
            "proposal": proposal,
            "publication": "NOT_ATTEMPTED",
        }
    if mode not in {"auto", "manual"}:
        return {**context, "status": "UNKNOWN", "reason": "POLICY_UNAVAILABLE"}
    if input_binding is not None:
        verdict = _verify_supplement(operator, input_binding)
        if verdict != "MATCH":
            return {
                **context,
                "status": "NOT_COMMITTED" if verdict == "CHANGED" else "UNKNOWN",
                "reason": "INPUT_CHANGED" if verdict == "CHANGED" else "INPUT_UNAVAILABLE",
                "publication": "NOT_ATTEMPTED",
            }
    published = _call(
        operator.publish,
        operator=operator,
        ids=ids,
        proposal=proposal,
        publication_id=ids["publication_id"],
        version_id=ids["version_id"],
        emit=_emit,
    )
    return _finish(operator, ids, published)


def initial(*, root, run_id, candidate_id, mode, ids, input_binding=None):
    operator = MockCalendarPublication(root=root, run_id=run_id)
    if input_binding is not None:
        verdict = _verify_supplement(operator, input_binding)
        if verdict != "MATCH":
            return {
                **_LABEL,
                "simulation_root": root,
                "run_id": run_id,
                **ids,
                "status": "NOT_COMMITTED" if verdict == "CHANGED" else "UNKNOWN",
                "reason": "INPUT_CHANGED" if verdict == "CHANGED" else "INPUT_UNAVAILABLE",
                "publication": "NOT_ATTEMPTED",
            }
    initialized = _call(operator.initialize, operator=operator, ids=ids, mode=mode)
    if initialized.get("status") != "INITIALIZED":
        return {
            **_LABEL,
            "simulation_root": root,
            "run_id": run_id,
            **ids,
            "status": "UNKNOWN",
            "reason": "POLICY_INITIALIZATION_UNKNOWN",
            "policy_result": initialized,
        }
    proposal = _call(operator.prepare, operator=operator, ids=ids, candidate_id=candidate_id)
    return _act(operator, ids, proposal, input_binding=input_binding)


def _update_with_supplement(
    operator,
    *,
    path,
    variant,
    new_run_id,
    capture_id,
    parent_id,
    ids,
    mixed_request=None,
    mixed_candidate_id=None,
):
    from bt_api_py import trading_calendar_mock as mock
    from bt_api_py import trading_calendar_supplement_capture as capture

    root, original_run = operator.root, operator.run_id
    run_id, derived_id = new_run_id, ids["candidate_id"]
    result = {
        **_LABEL,
        "simulation_root": root,
        "run_id": run_id,
        "original_run_id": original_run,
        "capture_id": capture_id,
        "parent_id": parent_id,
        "derived_id": derived_id,
        **ids,
        "supplement_status": "REQUESTED",
        "status": "NOT_COMMITTED",
    }
    try:
        operator._guard()
        audit = {
            "schema": "bt_api.calendar-mock-run/1",
            **_LABEL,
            "publication": "NOT_AUTHORIZED",
            "run_id": run_id,
            "capture_id": capture_id,
            "parent_id": parent_id,
            "derived_id": derived_id,
            "state": "NOT_ATTEMPTED",
            "simulation_root": root,
            "year": operator._audit["year"],
        }
        fds, chain = runs._open_runs(root, create=False)
        try:
            state, _ = runs._write_once(
                fds[-1], chain, f"mock-{run_id}.json", storage._envelope(audit)
            )
        finally:
            runs._close(fds)
        if state != "COMMITTED":
            return {**result, "status": state, "reason": "AUDIT_RECONCILE_FIRST"}
        result.update(reservation_state="RESERVED", claim="ORIGINAL_IDS_RECONCILE_FIRST")
        data, _ = mock.build(operator._audit["year"])
        if variant == "AFTERNOON_CHANGE":
            for shard in data["shards"]:
                row = next(
                    row for row in shard["layers"]["trading_hours"] if row["kind"] == "future"
                )
                next(w for w in row["day_windows"] if w["id"] == "afternoon")["end"] = "14:30"
        parent = review.build_once(
            root=root,
            candidate_id=parent_id,
            input_data=data,
            filesystem=runs.TRUSTED_LOCAL_POSIX,
            emit=lambda _: None,
            propagate_interrupt=True,
        )
        if parent.get("status") != "COMMITTED":
            return {
                **result,
                "status": parent.get("status", "UNKNOWN"),
                "reason": "PARENT_RECONCILE_FIRST",
            }
        shown = review.show(root=root, candidate_id=parent_id, filesystem=runs.TRUSTED_LOCAL_POSIX)
        if shown.get("status") != "VISIBLE_UNAUTHENTICATED":
            return {**result, "status": "UNKNOWN", "reason": "PARENT_UNAVAILABLE"}
        observed = runs.run_once(
            root=root,
            config=operator._declaration["config"],
            supplement=path,
            budget_seconds=30,
            enable_shfe=False,
            filesystem=runs.TRUSTED_LOCAL_POSIX,
            run_id=run_id,
            capture_id=capture_id,
            parent_id=parent_id,
            parent_sha256=shown["payload_sha256"],
            derived_id=derived_id,
            source_texts={},
            decisions=[],
        )
        if observed.get("derived_state") != "COMMITTED":
            if observed.get("derived_reason") == "SUPPLEMENT_CONFLICT" and observed.get(
                "conflict_kind"
            ) in {"SOURCE", "DAY", "TEMPLATE", "NIGHT_MAPPING"}:
                return {
                    **result,
                    "status": "NOT_COMMITTED",
                    "reason": "SUPPLEMENT_CONFLICT",
                    "conflict_kind": observed["conflict_kind"],
                    "supplement_status": "CAPTURED_UNVERIFIED",
                }
            reread = runs.show_once(root=root, run_id=run_id, filesystem=runs.TRUSTED_LOCAL_POSIX)
            preview = (reread.get("observation") or {}).get("preview") or {}
            return {
                **result,
                "status": "UNKNOWN",
                "reason": "CAPTURE_OR_DERIVATION_UNAVAILABLE",
                "capture_state": observed.get("capture_state", "UNKNOWN"),
                "derived_state": observed.get("derived_state", "UNKNOWN"),
                "supplement_status": preview.get("supplement_status", "UNKNOWN"),
                "input_code": preview.get("input_code", "UNKNOWN"),
            }
        result.update(
            capture_state=observed.get("capture_state", "UNKNOWN"),
            derived_state="COMMITTED",
            candidate_state="COMMITTED",
        )
        captured = capture.read_capture(
            root=root,
            capture_id=capture_id,
            filesystem=runs.TRUSTED_LOCAL_POSIX,
            include_content=True,
        )
        if (
            captured.get("status") != "VISIBLE_UNAUTHENTICATED"
            or captured.get("run_id") != run_id
            or type(captured.get("content")) is not dict
            or type(captured["content"].get("sources")) is not list
        ):
            return {
                **result,
                "status": "UNKNOWN",
                "reason": "CAPTURE_RECONCILE_FIRST",
                "supplement_status": "UNKNOWN",
            }
        if captured["content"]["sources"] != [mock.SOURCE]:
            return {**result, "status": "NOT_COMMITTED", "reason": "SUPPLEMENT_NOT_MOCK"}
        binding = {
            "path": path,
            "capture_id": capture_id,
            "normalized_content_sha256": captured["normalized_content_sha256"],
        }
        child = MockCalendarPublication(root=root, run_id=run_id)
        candidate_id = derived_id
        if mixed_request is not None:
            from bt_api_py import trading_calendar_mixed_publication as mixed_publication
            from bt_api_py import trading_calendar_mixed_simulation_version as mixed_version

            try:
                template = mixed_version.build_template(
                    root=root,
                    candidate_root=root,
                    filesystem=runs.TRUSTED_LOCAL_POSIX,
                    observation_id=mixed_request["observation_id"],
                    run_id=mixed_request["observation_run_id"],
                    candidate_id=derived_id,
                    target=mixed_request["target"],
                    assumptions=mixed_request["assumptions"],
                )
                evidence_hash = mixed_version.template_digest(
                    template,
                    root=root,
                    candidate_root=root,
                    filesystem=runs.TRUSTED_LOCAL_POSIX,
                    include_trace=True,
                )
            except Exception:
                return {
                    **result,
                    "status": "UNKNOWN",
                    "reason": "MIXED_TEMPLATE_UNAVAILABLE",
                    "publication": "NOT_ATTEMPTED",
                }
            built = mixed_publication.build_once(
                root=root,
                filesystem=runs.TRUSTED_LOCAL_POSIX,
                candidate_id=mixed_candidate_id,
                template=template,
                evidence_sha256=evidence_hash,
            )
            if built.get("status") != "COMMITTED":
                return {
                    **result,
                    "status": built.get("status", "UNKNOWN"),
                    "reason": built.get("reason", "MIXED_CANDIDATE_RECONCILE_FIRST"),
                    "mixed_candidate_id": mixed_candidate_id,
                    "publication": "NOT_ATTEMPTED",
                }
            candidate_id = mixed_candidate_id
            ids = {**ids, "candidate_id": candidate_id}
            result["mixed_candidate_id"] = candidate_id
            result["observation_status"] = "RETAINED_OBSERVATION_REUSED_NO_NEW_FETCH"
        proposal = _call(child.prepare, operator=child, ids=ids, candidate_id=candidate_id)
        action = _act(child, ids, proposal, input_binding=binding)
        return {
            **result,
            **action,
            **({"data_origin": "AKSHARE_MOCK"} if mixed_request is not None else {}),
            "supplement_status": "CAPTURED_UNVERIFIED",
            "normalized_content_sha256": binding["normalized_content_sha256"],
        }
    except (KeyboardInterrupt, SystemExit):
        _report("MOCK_INTERRUPTED", **result, reason="ORIGINAL_IDS_RECONCILE_FIRST")
        raise
    except Exception:
        return {**result, "status": "UNKNOWN", "reason": "UPDATE_OUTCOME_UNKNOWN_RECONCILE_FIRST"}


def update(*, root, run_id, variant, supplement=None, mixed_request=None):
    if mixed_request is not None:
        from bt_api_py import trading_calendar_mixed_simulation_version as mixed_version

        try:
            if (
                type(mixed_request) is not dict
                or set(mixed_request)
                != {"observation_id", "observation_run_id", "target", "assumptions", "alignment"}
                or not runs._id(mixed_request["observation_id"])
                or not runs._id(mixed_request["observation_run_id"])
                or mixed_request["alignment"] != "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING"
            ):
                raise ValueError
            mixed_version._template_input(mixed_request["target"], mixed_request["assumptions"])
        except Exception:
            return {**_LABEL, "status": "NOT_COMMITTED", "reason": "INVALID_MIXED_REQUEST"}
    new_run_id, capture_id, parent_id = (uuid.uuid4().hex for _ in range(3))
    ids = allocate()
    mixed_candidate_id = uuid.uuid4().hex if mixed_request is not None else None
    allocated = {new_run_id, capture_id, parent_id, *ids.values()}
    if mixed_candidate_id is not None:
        allocated.add(mixed_candidate_id)
    if len(allocated) != (8 if mixed_request is not None else 7) or run_id in allocated:
        raise ValueError("invalid allocated identities")
    _report(
        "MOCK_UPDATE_IDS_ALLOCATED",
        simulation_root=root,
        run_id=run_id,
        new_run_id=new_run_id,
        capture_id=capture_id,
        parent_id=parent_id,
        derived_id=ids["candidate_id"],
        **({"mixed_candidate_id": mixed_candidate_id} if mixed_candidate_id is not None else {}),
        **ids,
    )
    operator = MockCalendarPublication(root=root, run_id=run_id)
    if mixed_request is None and os.path.lexists(Path(root) / "CURRENT.json"):
        try:
            current = operator._store.read_current()
            operator._check_simulated_current(current)
        except Exception:
            return {**_LABEL, "status": "UNKNOWN", "reason": "CURRENT_UNAVAILABLE"}
        if current.simulation_provenance is not None:
            return {
                **_LABEL,
                "status": "NOT_COMMITTED",
                "reason": "MIXED_SCOPE_REQUIRED_FOR_EXISTING_VERSION",
                "publication": "NOT_ATTEMPTED",
            }
    if allocated.intersection(
        {operator._audit["capture_id"], operator._audit["parent_id"], operator._audit["derived_id"]}
    ):
        raise ValueError("allocated ID collides with run")
    missing_binding = None
    if getattr(operator, "_declaration", None) is not None:
        from bt_api_py.trading_calendar_check import _absolute_path

        path = str(
            _absolute_path(
                supplement if supplement is not None else os.path.join(root, "supplement.yaml")
            )
        )
        try:
            os.lstat(path)
        except FileNotFoundError:
            return {
                **_LABEL,
                "simulation_root": root,
                "run_id": run_id,
                "status": "NOT_COMMITTED",
                "reason": "SUPPLEMENT_MISSING",
                "supplement_status": "MISSING",
                "publication": "NOT_ATTEMPTED",
            }
        except OSError:
            return {
                **_LABEL,
                "simulation_root": root,
                "run_id": run_id,
                "status": "UNKNOWN",
                "reason": "SUPPLEMENT_UNAVAILABLE",
                "supplement_status": "UNKNOWN",
            }
        else:
            return _update_with_supplement(
                operator,
                path=path,
                variant=variant,
                new_run_id=new_run_id,
                capture_id=capture_id,
                parent_id=parent_id,
                ids=ids,
                mixed_request=mixed_request,
                mixed_candidate_id=mixed_candidate_id,
            )
    elif supplement is not None:
        return {
            **_LABEL,
            "simulation_root": root,
            "run_id": run_id,
            "status": "NOT_COMMITTED",
            "reason": "SUPPLEMENT_REQUIRES_CONFIGURED_ROOT",
        }
    if mixed_request is not None:
        return {**_LABEL, "status": "NOT_COMMITTED", "reason": "MIXED_REQUIRES_CONFIGURED_ROOT"}
    built = _call(
        operator.build_candidate,
        operator=operator,
        ids=ids,
        candidate_id=ids["candidate_id"],
        year=operator._audit["year"],
        variant=variant,
        emit=_emit,
    )
    if built.get("status") != "COMMITTED":
        return {
            **_LABEL,
            "simulation_root": root,
            "run_id": run_id,
            **ids,
            "status": built.get("status", "UNKNOWN"),
            "candidate_result": built,
        }
    proposal = _call(operator.prepare, operator=operator, ids=ids, candidate_id=ids["candidate_id"])
    return {
        **_act(operator, ids, proposal, input_binding=missing_binding),
        "supplement_status": "MISSING",
    }


def _confirm(
    operator,
    *,
    review_id,
    review_sha256,
    reviewer,
    confirmed_at,
    stale_directory=None,
    stale_chain=None,
):
    root, run_id = operator.root, operator.run_id
    pending = _read_pending(operator, review_id)
    if (
        pending.get("status") != "VISIBLE_UNAUTHENTICATED"
        or pending.get("payload_sha256") != review_sha256
    ):
        return {
            **_LABEL,
            "simulation_root": root,
            "run_id": run_id,
            "status": "UNKNOWN",
            "reason": "PENDING_REVIEW_UNAVAILABLE_OR_CHANGED",
            "review_id": review_id,
        }
    body = pending["body"]
    ids = {key: body[key] for key in ("candidate_id", "review_id", "publication_id", "version_id")}
    _report("MOCK_CONFIRM_IDS", simulation_root=root, run_id=run_id, **ids)
    if body.get("schema") == _SUPPLEMENT_SCHEMA:
        from bt_api_py.trading_calendar_mock_publication import _id as mock_id
        from bt_api_py.trading_calendar_store import _parse_utc

        try:
            if not mock_id(reviewer):
                raise ValueError("invalid simulation reviewer")
            _parse_utc(confirmed_at)
        except Exception:
            return {
                **_LABEL,
                "simulation_root": root,
                "run_id": run_id,
                **ids,
                "status": "NOT_COMMITTED",
                "reason": "INVALID_CONFIRMATION",
            }
        stale_body = {
            "schema": "bt_api.calendar-mock-review-stale/1",
            **_LABEL,
            "run_id": run_id,
            "review_id": review_id,
            "review_sha256": review_sha256,
            "reason": "SUPPLEMENT_CHANGED",
        }
        stale_name = f"mock-review-stale-{review_id}.json"
        try:
            raw = storage._read(stale_directory, stale_chain, stale_name, review._OUTPUT_LIMIT)
            if raw is not None:
                record = storage._unwrap(raw)[0]
                if record.get("reason") not in {
                    "SUPPLEMENT_CHANGED",
                    "BASELINE_CHANGED",
                    "POLICY_CHANGED",
                } or record != {**stale_body, "reason": record["reason"]}:
                    raise ValueError("stale record changed")
                return {
                    **_LABEL,
                    "simulation_root": root,
                    "run_id": run_id,
                    **ids,
                    "status": "NOT_COMMITTED",
                    "reason": "REVIEW_STALE",
                }
            attempt_body = {
                "schema": "bt_api.calendar-mock-confirm-intent/1",
                **_LABEL,
                "run_id": run_id,
                "review_id": review_id,
                "review_sha256": review_sha256,
                "state": "INTENT_ONLY",
            }
            attempt_name = f"mock-confirm-intent-{review_id}.json"
            raw = storage._read(stale_directory, stale_chain, attempt_name, review._OUTPUT_LIMIT)
            if raw is not None:
                if storage._unwrap(raw)[0] != attempt_body:
                    raise ValueError("confirmation intent changed")
                return {
                    **_LABEL,
                    "simulation_root": root,
                    "run_id": run_id,
                    **ids,
                    "status": "UNKNOWN",
                    "reason": "CONFIRMATION_RECONCILE_FIRST",
                    "confirmation_intent": "VISIBLE_UNAUTHENTICATED",
                }
            state = storage._write(
                stale_directory,
                stale_chain,
                attempt_name,
                storage._envelope(attempt_body),
                review._OUTPUT_LIMIT,
            )
            if state != "COMMITTED":
                return {
                    **_LABEL,
                    "simulation_root": root,
                    "run_id": run_id,
                    **ids,
                    "status": "UNKNOWN",
                    "reason": "CONFIRMATION_RECONCILE_FIRST",
                    "confirmation_intent": "UNKNOWN",
                }
            ids["confirmation_intent"] = "COMMITTED"
            verdict = _verify_supplement(operator, body["input_binding"])
            if verdict == "UNKNOWN":
                return {
                    **_LABEL,
                    "simulation_root": root,
                    "run_id": run_id,
                    **ids,
                    "status": "UNKNOWN",
                    "reason": "SUPPLEMENT_UNAVAILABLE",
                }
            if verdict == "CHANGED":
                state = storage._write(
                    stale_directory,
                    stale_chain,
                    stale_name,
                    storage._envelope(stale_body),
                    review._OUTPUT_LIMIT,
                )
                if state != "COMMITTED":
                    return {
                        **_LABEL,
                        "simulation_root": root,
                        "run_id": run_id,
                        **ids,
                        "status": "UNKNOWN",
                        "reason": "STALE_RECORD_RECONCILE_FIRST",
                    }
                return {
                    **_LABEL,
                    "simulation_root": root,
                    "run_id": run_id,
                    **ids,
                    "status": "NOT_COMMITTED",
                    "reason": "REVIEW_STALE",
                }
        except Exception:
            return {
                **_LABEL,
                "simulation_root": root,
                "run_id": run_id,
                **ids,
                "status": "UNKNOWN",
                "reason": "STALE_RECORD_UNAVAILABLE",
            }
    for result in (
        operator.inspect_publication_claim(publication_id=ids["publication_id"]),
        operator.inspect_version_claim(version_id=ids["version_id"]),
    ):
        if result.get("status") != "NOT_OBSERVED":
            return {
                **_LABEL,
                "simulation_root": root,
                "run_id": run_id,
                **ids,
                "status": "UNKNOWN",
                "reason": "ORIGINAL_IDS_RECONCILE_FIRST",
                "claim_observation": result,
            }
    fresh = _call(operator.prepare, operator=operator, ids=ids, candidate_id=ids["candidate_id"])
    if fresh != body["proposal"]:
        if body.get("schema") == _SUPPLEMENT_SCHEMA:
            if fresh.get("status") not in {"FIRST_SIMULATION_PUBLICATION", "SAME", "CHANGED"}:
                return {
                    **_LABEL,
                    "simulation_root": root,
                    "run_id": run_id,
                    **ids,
                    "status": "UNKNOWN",
                    "reason": "PROPOSAL_UNAVAILABLE",
                }
            if fresh.get("policy") != body["proposal"].get("policy"):
                stale_reason = "POLICY_CHANGED"
            elif fresh.get("baseline") != body["proposal"].get("baseline"):
                stale_reason = "BASELINE_CHANGED"
            else:
                return {
                    **_LABEL,
                    "simulation_root": root,
                    "run_id": run_id,
                    **ids,
                    "status": "UNKNOWN",
                    "reason": "PROPOSAL_CHANGED_UNPROVEN",
                }
            state = storage._write(
                stale_directory,
                stale_chain,
                stale_name,
                storage._envelope({**stale_body, "reason": stale_reason}),
                review._OUTPUT_LIMIT,
            )
            if state != "COMMITTED":
                return {
                    **_LABEL,
                    "simulation_root": root,
                    "run_id": run_id,
                    **ids,
                    "status": "UNKNOWN",
                    "reason": "STALE_RECORD_RECONCILE_FIRST",
                }
            return {
                **_LABEL,
                "simulation_root": root,
                "run_id": run_id,
                **ids,
                "status": "NOT_COMMITTED",
                "reason": "REVIEW_STALE",
                "stale_reason": stale_reason,
            }
        return {
            **_LABEL,
            "simulation_root": root,
            "run_id": run_id,
            **ids,
            "status": "NOT_COMMITTED",
            "reason": "REVIEW_STALE_OR_CHANGED",
        }
    if body.get("schema") == _SUPPLEMENT_SCHEMA:
        verdict = _verify_supplement(operator, body["input_binding"])
        if verdict == "UNKNOWN":
            return {
                **_LABEL,
                "simulation_root": root,
                "run_id": run_id,
                **ids,
                "status": "UNKNOWN",
                "reason": "SUPPLEMENT_UNAVAILABLE",
            }
        if verdict == "CHANGED":
            state = storage._write(
                stale_directory,
                stale_chain,
                stale_name,
                storage._envelope(stale_body),
                review._OUTPUT_LIMIT,
            )
            if state != "COMMITTED":
                return {
                    **_LABEL,
                    "simulation_root": root,
                    "run_id": run_id,
                    **ids,
                    "status": "UNKNOWN",
                    "reason": "STALE_RECORD_RECONCILE_FIRST",
                }
            return {
                **_LABEL,
                "simulation_root": root,
                "run_id": run_id,
                **ids,
                "status": "NOT_COMMITTED",
                "reason": "REVIEW_STALE",
            }
    published = _call(
        operator.publish,
        operator=operator,
        ids=ids,
        proposal=body["proposal"],
        publication_id=ids["publication_id"],
        version_id=ids["version_id"],
        reviewer=reviewer,
        confirmed_at=confirmed_at,
        emit=_emit,
    )
    return _finish(operator, ids, published)


def confirm(*, root, run_id, review_id, review_sha256, reviewer, confirmed_at):
    operator = MockCalendarPublication(root=root, run_id=run_id)
    pending = _read_pending(operator, review_id)
    kwargs = {
        "review_id": review_id,
        "review_sha256": review_sha256,
        "reviewer": reviewer,
        "confirmed_at": confirmed_at,
    }
    if pending.get("body", {}).get("schema") != _SUPPLEMENT_SCHEMA:
        return _confirm(operator, **kwargs)
    fds, chain = review._open(root, False)
    lock = None
    try:
        lock_name = f".mock-confirm-{review_id}.lock"
        lock = os.open(lock_name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fds[-1])
        info = os.fstat(lock)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise ValueError("invalid review confirmation lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        entry = os.stat(lock_name, dir_fd=fds[-1], follow_symlinks=False)
        if (entry.st_dev, entry.st_ino) != (
            info.st_dev,
            info.st_ino,
        ) or not runs._directory_matches(chain):
            raise ValueError("review confirmation lock changed")
        return _confirm(operator, **kwargs, stale_directory=fds[-1], stale_chain=chain)
    except Exception:
        return {
            **_LABEL,
            "simulation_root": root,
            "run_id": run_id,
            "review_id": review_id,
            "status": "UNKNOWN",
            "reason": "REVIEW_LOCK_UNAVAILABLE",
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def show(
    *,
    root,
    run_id,
    review_id=None,
    publication_id=None,
    version_id=None,
    candidate_id=None,
    akshare_observation_id=None,
):
    operator = MockCalendarPublication(root=root, run_id=run_id)
    result = {
        **_LABEL,
        "status": "OBSERVED_UNAUTHENTICATED",
        "run_id": run_id,
        "simulation_root": root,
        "publication": "NOT_AUTHORIZED",
    }
    try:
        operator._guard()
        current = operator._store.read_current()
        operator._check_simulated_current(current)
        origin = "AKSHARE_MOCK" if current.simulation_provenance is not None else "MOCK"
        result["data_origin"] = origin
        result["current"] = {
            "status": "VISIBLE_UNAUTHENTICATED",
            "data_origin": origin,
            "publication_id": current.publication_id,
            "publication_sha256": current.publication_sha256,
            "version_id": current.version_id,
            "version_sha256": current.version_sha256,
            "coverage_status": current.coverage_status,
            "receipt_consistency": "NOT_CHECKED",
        }
    except Exception:
        result["current"] = {"status": "UNKNOWN", "reason": "CURRENT_UNAVAILABLE"}
    if review_id:
        pending = _read_pending(operator, review_id)
        if pending.get("status") == "VISIBLE_UNAUTHENTICATED":
            try:
                shown, _ = operator._candidate(pending["body"]["candidate_id"])
                provenance = operator._candidate_provenance(pending["body"]["candidate_id"], shown)
                pending["data_origin"] = "AKSHARE_MOCK" if provenance is not None else "MOCK"
                if result["current"]["status"] != "VISIBLE_UNAUTHENTICATED":
                    result["data_origin"] = pending["data_origin"]
            except Exception:
                pending["data_origin"] = "UNKNOWN"
        if (
            pending.get("status") == "VISIBLE_UNAUTHENTICATED"
            and getattr(operator, "_declaration", None) is not None
        ):
            try:
                with operator._store._readonly_lock():
                    if operator._store._policy_change_pending():
                        raise ValueError("policy transition pending")
                    policy = operator._store._review_policy_locked()
                    if policy is None:
                        raise ValueError("policy unavailable")
                    operator._check_policy_locked(policy)
                    result["review_policy"] = {
                        "status": (
                            "CURRENT"
                            if asdict(policy) == pending["body"]["proposal"]["policy"]
                            else "STALE"
                        )
                    }
            except Exception:
                result["review_policy"] = {"status": "UNKNOWN"}
        if pending.get("body", {}).get("schema") == _SUPPLEMENT_SCHEMA:
            try:
                fds, chain = review._open(root, False)
                try:
                    raw = storage._read(
                        fds[-1], chain, f"mock-review-stale-{review_id}.json", review._OUTPUT_LIMIT
                    )
                    if raw is None:
                        result["review_stale"] = {"status": "NOT_OBSERVED"}
                    else:
                        body, digest = storage._unwrap(raw)
                        expected = {
                            "schema": "bt_api.calendar-mock-review-stale/1",
                            **_LABEL,
                            "run_id": run_id,
                            "review_id": review_id,
                            "review_sha256": pending["payload_sha256"],
                            "reason": "SUPPLEMENT_CHANGED",
                        }
                        if body.get("reason") not in {
                            "SUPPLEMENT_CHANGED",
                            "BASELINE_CHANGED",
                            "POLICY_CHANGED",
                        } or body != {**expected, "reason": body["reason"]}:
                            raise ValueError("stale record changed")
                        result["review_stale"] = {
                            "status": "VISIBLE_UNAUTHENTICATED",
                            "payload_sha256": digest,
                            "reason": body["reason"],
                        }
                    raw = storage._read(
                        fds[-1],
                        chain,
                        f"mock-confirm-intent-{review_id}.json",
                        review._OUTPUT_LIMIT,
                    )
                    if raw is None:
                        result["confirmation_intent"] = {"status": "NOT_OBSERVED"}
                    else:
                        intent, digest = storage._unwrap(raw)
                        expected = {
                            "schema": "bt_api.calendar-mock-confirm-intent/1",
                            **_LABEL,
                            "run_id": run_id,
                            "review_id": review_id,
                            "review_sha256": pending["payload_sha256"],
                            "state": "INTENT_ONLY",
                        }
                        if intent != expected:
                            raise ValueError("confirmation intent changed")
                        result["confirmation_intent"] = {
                            "status": "VISIBLE_UNAUTHENTICATED",
                            "payload_sha256": digest,
                        }
                finally:
                    runs._close(fds)
            except Exception:
                result["review_stale"] = {"status": "UNKNOWN"}
                result["confirmation_intent"] = {"status": "UNKNOWN"}
            pending["body"]["input_binding"] = {
                key: value
                for key, value in pending["body"]["input_binding"].items()
                if key != "path"
            }
        result["pending_review"] = pending
    if publication_id:
        result["publication_claim"] = operator.inspect_publication_claim(
            publication_id=publication_id
        )
        result["publication_visibility"] = operator.verify_publication(
            publication_id=publication_id
        )
        result["first_difference"] = operator.inspect_first_difference(
            publication_id=publication_id
        )
    if version_id:
        result["version_claim"] = operator.inspect_version_claim(version_id=version_id)
    if candidate_id:
        result["proposal"] = operator.prepare(candidate_id=candidate_id)
    if akshare_observation_id:
        from bt_api_py.trading_calendar_mock_akshare import show as show_akshare

        result["akshare_candidate"] = show_akshare(
            root=root,
            run_id=run_id,
            observation_id=akshare_observation_id,
            filesystem=runs.TRUSTED_LOCAL_POSIX,
        )
    return result
