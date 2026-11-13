"""One explicitly configured offline-testable SIMULATION_ONLY daily batch.

No scheduler or account integration. AKShare requests are made only for scopes
explicitly supplied by the caller; a request date is not an effective date.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from dataclasses import asdict

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mixed_publication as mixed_publication
from bt_api_py import trading_calendar_mixed_simulation_version as mixed
from bt_api_py import trading_calendar_mock_akshare as akshare
from bt_api_py import trading_calendar_mock_cli as cli
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from bt_api_py.trading_calendar_store import TradingCalendarStore, _scope_key

FS = runs.TRUSTED_LOCAL_POSIX
LABEL = {"environment": "SIMULATION_ONLY", "source_authentication": "NOT_PROVEN"}


def _write(root, name, body):
    fds, chain = runs._open_runs(root, create=False)
    try:
        return runs._write_once(fds[-1], chain, name, storage._envelope(body))
    finally:
        runs._close(fds)


def _read(root, name):
    fds, chain = runs._open_runs(root, create=False)
    try:
        raw = runs._read_entry(fds[-1], chain, name)
    finally:
        runs._close(fds)
    return None if raw is None else storage._unwrap(raw)


def _baseline(operator):
    operator._guard()
    with operator._store._readonly_lock():
        if operator._store._pending_exists() or operator._store._policy_change_pending():
            raise ValueError("store operation pending")
        policy = operator._store._review_policy_locked()
        if policy is None:
            raise ValueError("policy unavailable")
        operator._check_policy_locked(policy)
        current = operator._store.read_current()
        operator._check_simulated_current(current)
        return (
            {
                "publication_id": current.publication_id,
                "publication_sha256": current.publication_sha256,
                "version_sha256": current.version_sha256,
            },
            asdict(policy),
        )


def run(*, root, run_id, scopes, supplement=None, budget_seconds=600):
    """Capture all scopes before a single candidate/review/publication attempt."""
    try:
        if (
            type(scopes) is not list
            or not 2 <= len(scopes) <= 8
            or type(budget_seconds) not in (int, float)
            or not math.isfinite(budget_seconds)
            or not 0 < budget_seconds <= 600
        ):
            raise ValueError
        for item in scopes:
            if (
                type(item) is not dict
                or set(item) != {"contract", "target", "assumptions", "alignment", "timeout"}
                or item["alignment"] != "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING"
            ):
                raise ValueError
            mixed._template_input(item["target"], item["assumptions"])
        keys = [_scope_key(item["target"]) for item in scopes]
        if len(keys) != len(set(keys)):
            return {**LABEL, "status": "NOT_COMMITTED", "reason": "BATCH_SCOPE_OVERLAP"}
        for i, left in enumerate(keys):
            for right in keys[i + 1 :]:
                if (
                    left[:2] == right[:2]
                    and left[3] == right[3]
                    and left[4] <= right[5]
                    and right[4] <= left[5]
                ):
                    return {**LABEL, "status": "NOT_COMMITTED", "reason": "BATCH_SCOPE_OVERLAP"}
        requested_date = cli._beijing_today()
        deadline = time.monotonic() + budget_seconds
        operator = MockCalendarPublication(root=root, run_id=run_id)
        if operator._declaration is None:
            raise ValueError
        from bt_api_py.trading_calendar_check import _absolute_path

        path = str(
            _absolute_path(
                supplement if supplement is not None else os.path.join(root, "supplement.yaml")
            )
        )
        os.lstat(path)
        for item in scopes:
            akshare.validate_request(
                {
                    "venue": item["target"]["venue"],
                    "contract": item["contract"],
                    "requested_date": requested_date,
                    "kind": "future",
                    "timeout": item["timeout"],
                },
                year=operator._audit["year"],
            )
        baseline, policy = _baseline(operator)
        from bt_api_py import trading_calendar_mock as mock

        parent_data, _ = mock.build(operator._audit["year"])
        for item in scopes:
            try:
                shard, row = mixed_publication._scope_rows(parent_data, item["target"])
                if (
                    not (
                        shard["coverage_start"]
                        <= item["target"]["valid_from"]
                        <= item["target"]["valid_to"]
                        <= shard["coverage_end"]
                    )
                    or not row["windows_complete"]
                    or not row["exceptions_complete"]
                    or any(
                        item["assumptions"][scope]["phase"] not in {"continuous", "auction"}
                        or item["assumptions"][scope]["timezone"] != "Asia/Shanghai"
                        for scope in ("DAY", "NIGHT")
                    )
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                return {
                    **LABEL,
                    "status": "NOT_COMMITTED",
                    "reason": "BATCH_TARGET_NOT_MATERIALIZABLE",
                }
    except FileNotFoundError:
        return {**LABEL, "status": "NOT_COMMITTED", "reason": "SUPPLEMENT_MISSING"}
    except Exception:
        return {**LABEL, "status": "UNKNOWN", "reason": "BATCH_INPUT_OR_BASELINE_UNAVAILABLE"}
    new_run, capture_id, parent_id, batch_id, candidate_id = (uuid.uuid4().hex for _ in range(5))
    ids = cli.allocate()
    observations = [uuid.uuid4().hex for _ in scopes]
    all_ids = {new_run, capture_id, parent_id, batch_id, candidate_id, *ids.values(), *observations}
    if len(all_ids) != 9 + len(observations) or run_id in all_ids:
        return {**LABEL, "status": "NOT_COMMITTED", "reason": "INVALID_ALLOCATED_IDS"}
    cli._report(
        "MOCK_BATCH_IDS_ALLOCATED",
        simulation_root=root,
        run_id=new_run,
        original_run_id=run_id,
        batch_id=batch_id,
        capture_id=capture_id,
        parent_id=parent_id,
        derived_id=ids["candidate_id"],
        batch_candidate_id=candidate_id,
        observation_ids=observations,
        **ids,
    )
    intent = {
        "schema": "bt_api.calendar-daily-batch-intent/1",
        **LABEL,
        "batch_id": batch_id,
        "run_id": new_run,
        "original_run_id": run_id,
        "candidate_id": candidate_id,
        "derived_id": ids["candidate_id"],
        "capture_id": capture_id,
        "parent_id": parent_id,
        "review_id": ids["review_id"],
        "publication_id": ids["publication_id"],
        "version_id": ids["version_id"],
        "observations": [
            {"observation_id": oid, **scope}
            for oid, scope in zip(observations, scopes, strict=True)
        ],
        "requested_date": requested_date.isoformat(),
        "date_role": "REQUEST_DATE_NOT_EFFECTIVE_DATE",
        "budget_seconds": budget_seconds,
        "baseline": baseline,
        "policy": policy,
    }
    state, reason = _write(root, f"mock-daily-batch-intent-{batch_id}.json", intent)
    if state != "COMMITTED":
        return {
            **LABEL,
            "status": "UNKNOWN",
            "reason": "BATCH_INTENT_RECONCILE_FIRST",
            "batch_id": batch_id,
            "intent_state": state,
            "detail": reason,
        }
    try:
        stored_intent, intent_sha = mixed_publication._batch_intent(root, batch_id)
        if stored_intent != intent:
            raise ValueError
    except Exception:
        return {
            **LABEL,
            "status": "UNKNOWN",
            "reason": "BATCH_INTENT_RECONCILE_FIRST",
            "batch_id": batch_id,
        }

    def work(*, operator, derived_id, binding, ids, result):
        records, templates, captures = [], [], []
        try:
            if _baseline(operator) != (baseline, policy):
                return {
                    **result,
                    "status": "NOT_COMMITTED",
                    "reason": "BATCH_BASELINE_CHANGED",
                    "batch_id": batch_id,
                }
            for scope, oid in zip(scopes, observations, strict=True):
                request = {
                    "venue": scope["target"]["venue"],
                    "contract": scope["contract"],
                    "requested_date": requested_date,
                    "kind": "future",
                    "timeout": scope["timeout"],
                }
                try:
                    mixed_publication._batch_intent(root, batch_id, intent_sha)
                except Exception:
                    fetched = {
                        "observation_state": "NOT_ATTEMPTED",
                        "observation_status": "NOT_ATTEMPTED",
                        "projection_state": "NOT_ATTEMPTED",
                        "reason": "BATCH_INTENT_UNAVAILABLE",
                    }
                else:
                    fetched = akshare.observe_once(
                        root=root,
                        run_id=new_run,
                        observation_id=oid,
                        year=operator._audit["year"],
                        request=request,
                        filesystem=FS,
                        deadline_monotonic=deadline,
                    )
                row = {
                    "observation_id": oid,
                    "source_type": "AKSHARE",
                    "source_authentication": "NOT_PROVEN",
                    "target": scope["target"],
                    "assumptions": scope["assumptions"],
                    "alignment": scope["alignment"],
                    "timeout": scope["timeout"],
                    "contract": scope["contract"],
                    "requested_date": requested_date.isoformat(),
                    "date_role": "REQUEST_DATE_NOT_EFFECTIVE_DATE",
                    "observation_state": fetched.get("observation_state", "UNKNOWN"),
                    "observation_status": fetched.get("observation_status", "UNKNOWN"),
                    "projection_state": fetched.get("projection_state", "UNKNOWN"),
                    "reason": fetched.get("reason", "NONE"),
                }
                records.append(row)
                captures.append(fetched.get("observation_result", {}))
                if (
                    row["observation_state"] != "COMMITTED"
                    or row["observation_status"] != "SUCCESS"
                    or row["projection_state"] != "VISIBLE_UNAUTHENTICATED"
                ):
                    continue
                try:
                    template = mixed.build_template(
                        root=root,
                        candidate_root=root,
                        observation_id=oid,
                        run_id=new_run,
                        candidate_id=derived_id,
                        filesystem=FS,
                        target=scope["target"],
                        assumptions=scope["assumptions"],
                    )
                    digest = mixed.template_digest(
                        template, root=root, candidate_root=root, filesystem=FS, include_trace=True
                    )
                    templates.append({"template": template, "evidence_sha256": digest})
                except Exception:
                    row["reason"] = "TEMPLATE_UNAVAILABLE"
            partial = len(templates) != len(scopes)
            if partial:
                if _baseline(operator) != (baseline, policy):
                    return {
                        **result,
                        "status": "NOT_COMMITTED",
                        "reason": "BATCH_BASELINE_CHANGED",
                        "batch_id": batch_id,
                        "observations": records,
                    }
                current = TradingCalendarStore(root).read_current()
                prior = current.simulation_provenance
                prior_scopes = (
                    prior["scopes"] if prior and "scopes" in prior else [prior] if prior else []
                )
                successes = {entry["template"]["trace"]["observation_id"] for entry in templates}
                from bt_api_py import trading_calendar_akshare_observation as observation

                for row, captured in zip(records, captures, strict=True):
                    shown = observation.show(
                        root=root,
                        run_id=new_run,
                        observation_id=row["observation_id"],
                        filesystem=FS,
                    )
                    row["observation_diagnostics"] = {
                        key: shown.get(key, captured.get(key, "UNKNOWN"))
                        for key in (
                            "data_state",
                            "transport_status",
                            "direct_child_cleanup",
                            "timeout_retry",
                            "automatic_retry",
                            "capture_state",
                            "reported_observation_status",
                        )
                    }
                    row["failure_proof"] = None
                    if row["observation_id"] in successes:
                        row.update(check_status="SUCCESS", disposition="UPDATED_AKSHARE")
                    elif (
                        row["observation_state"] == "COMMITTED"
                        and row["observation_status"] in mixed_publication._DEFINITE_FAILURES
                    ):
                        try:
                            row["failure_proof"] = mixed_publication._failure_proof(
                                root, new_run, row
                            )
                        except Exception:
                            row.update(
                                check_status="UNKNOWN",
                                disposition="UNAVAILABLE",
                                reason="FAILURE_OBSERVATION_RECONCILE_FIRST",
                            )
                        else:
                            retained = any(
                                scope["target"] == row["target"] for scope in prior_scopes
                            )
                            row.update(
                                check_status="FAILED",
                                disposition="RETAINED_AKSHARE" if retained else "MOCK_FALLBACK",
                            )
                    else:
                        row.update(check_status="UNKNOWN", disposition="UNAVAILABLE")
                if not templates:
                    for row in records:
                        if row["check_status"] == "FAILED":
                            row["disposition"] = "CURRENT_UNCHANGED"
            evidence = {
                "schema": "bt_api.calendar-daily-batch-evidence/2"
                if partial
                else "bt_api.calendar-daily-batch-evidence/1",
                **LABEL,
                "batch_id": batch_id,
                "run_id": new_run,
                "candidate_id": candidate_id,
                "derived_id": derived_id,
                "baseline": baseline,
                "policy": policy,
                "intent_sha256": intent_sha,
                "supplement_sha256": binding["normalized_content_sha256"],
                "observations": records,
            }
            state, reason = _write(root, f"mock-daily-batch-{batch_id}.json", evidence)
            if state != "COMMITTED":
                return {
                    **result,
                    "status": "UNKNOWN",
                    "reason": "BATCH_EVIDENCE_RECONCILE_FIRST",
                    "batch_id": batch_id,
                    "evidence_state": state,
                    "observations": records,
                }
            reread = _read(root, f"mock-daily-batch-{batch_id}.json")
            if reread is None or reread[0] != evidence:
                return {
                    **result,
                    "status": "UNKNOWN",
                    "reason": "BATCH_EVIDENCE_RECONCILE_FIRST",
                    "batch_id": batch_id,
                    "observations": records,
                }
            try:
                mixed_publication._batch_record(root, batch_id, reread[1])
            except Exception:
                return {
                    **result,
                    "status": "UNKNOWN",
                    "reason": "BATCH_INTENT_RECONCILE_FIRST",
                    "batch_id": batch_id,
                    "observations": records,
                    "publication": "NOT_ATTEMPTED",
                }
            from bt_api_py import trading_calendar_scope_checks as scope_checks

            checks = scope_checks.record(
                operator=operator, batch_id=batch_id, evidence_sha256=reread[1]
            )
            if checks.get("status") != "VISIBLE_UNAUTHENTICATED":
                return {
                    **result,
                    "status": "UNKNOWN",
                    "reason": "SCOPE_CHECK_RECONCILE_FIRST",
                    "batch_id": batch_id,
                    "batch_evidence_sha256": reread[1],
                    "observations": records,
                    "scope_checks": checks,
                    "publication": "NOT_ATTEMPTED",
                }
            if partial and (
                not templates or any(row["check_status"] == "UNKNOWN" for row in records)
            ):
                return {
                    **result,
                    "status": "UNKNOWN"
                    if any(row["check_status"] == "UNKNOWN" for row in records)
                    else "NOT_COMMITTED",
                    "reason": "BATCH_ALL_FAILED"
                    if not templates and all(row["check_status"] == "FAILED" for row in records)
                    else "BATCH_INCOMPLETE",
                    "batch_id": batch_id,
                    "observations": records,
                    "publication": "NOT_ATTEMPTED",
                    "fallback": "EXISTING_CURRENT_NOT_REPLACED_BY_THIS_RUN",
                    "scope_checks": checks,
                }
            if _baseline(operator) != (baseline, policy):
                return {
                    **result,
                    "status": "NOT_COMMITTED",
                    "reason": "BATCH_BASELINE_CHANGED",
                    "batch_id": batch_id,
                    "observations": records,
                    "scope_checks": checks,
                }
            built = mixed_publication.build_batch_once(
                root=root,
                filesystem=FS,
                candidate_id=candidate_id,
                templates=templates,
                batch_id=batch_id,
                batch_record_sha256=reread[1],
                baseline=baseline,
            )
            if built.get("status") != "COMMITTED":
                return {
                    **result,
                    "status": built.get("status", "UNKNOWN"),
                    "reason": built.get("reason", "BATCH_CANDIDATE_RECONCILE_FIRST"),
                    "batch_id": batch_id,
                    "observations": records,
                    "scope_checks": checks,
                }
            final_ids = {**ids, "candidate_id": candidate_id}
            proposal = cli._call(
                operator.prepare, operator=operator, ids=final_ids, candidate_id=candidate_id
            )
            action = cli._act(operator, final_ids, proposal, input_binding=binding)
            return {
                **result,
                **action,
                "data_origin": "AKSHARE_MOCK",
                "batch_id": batch_id,
                "batch_evidence_sha256": reread[1],
                "observations": records,
                "batch_status": "PARTIAL" if partial else "COMPLETE",
                "scope_checks": checks,
                "date_role": "REQUEST_DATE_NOT_EFFECTIVE_DATE",
            }
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return {
                **result,
                "status": "UNKNOWN",
                "reason": "BATCH_RECONCILE_ORIGINAL_IDS",
                "batch_id": batch_id,
                "observations": records,
            }

    return cli._update_with_supplement(
        operator,
        path=path,
        variant="BASE",
        new_run_id=new_run,
        capture_id=capture_id,
        parent_id=parent_id,
        ids=ids,
        batch_work=work,
        parent_data=parent_data,
    )


def show(*, root, run_id, batch_id, reconcile_scope_checks=False):
    if type(reconcile_scope_checks) is not bool:
        return {**LABEL, "status": "UNKNOWN", "reason": "INVALID_RECONCILE_OPTION"}
    if not runs._id(batch_id) or not runs._id(run_id):
        return {**LABEL, "status": "UNKNOWN", "reason": "INVALID_BATCH_ID"}
    try:
        operator = MockCalendarPublication(root=root, run_id=run_id)
        operator._guard()
        intent = _read(root, f"mock-daily-batch-intent-{batch_id}.json")
        evidence = _read(root, f"mock-daily-batch-{batch_id}.json")
        if intent is None:
            return (
                {**LABEL, "status": "UNKNOWN", "reason": "BATCH_ORPHAN_EVIDENCE"}
                if evidence is not None
                else {**LABEL, "status": "NOT_OBSERVED"}
            )
        if (
            intent[0].get("batch_id") != batch_id
            or run_id not in {intent[0].get("run_id"), intent[0].get("original_run_id")}
            or (evidence is not None and evidence[0].get("batch_id") != batch_id)
        ):
            raise ValueError
        mixed_publication._batch_intent(root, batch_id, intent[1])
        if evidence is not None:
            mixed_publication._batch_record(root, batch_id, evidence[1])
        from bt_api_py import trading_calendar_scope_checks as scope_checks

        reconciliation = (
            scope_checks.reconcile(operator=operator, batch_id=batch_id)
            if reconcile_scope_checks
            else None
        )
        scope_report = scope_checks.show(
            operator=operator,
            targets=[
                {"target": item["target"], "requested_contract": item["contract"]}
                for item in intent[0]["observations"]
            ],
            batch_id=batch_id,
        )
        return {
            **LABEL,
            "status": "VISIBLE_UNAUTHENTICATED"
            if scope_report.get("status") == "VISIBLE_UNAUTHENTICATED"
            and (
                reconciliation is None or reconciliation.get("status") == "VISIBLE_UNAUTHENTICATED"
            )
            else "UNKNOWN",
            "intent": intent[0],
            "intent_sha256": intent[1],
            "evidence": None if evidence is None else evidence[0],
            "evidence_sha256": None if evidence is None else evidence[1],
            "scope_checks": scope_report,
            "scope_check_reconciliation": reconciliation,
        }
    except Exception:
        return {**LABEL, "status": "UNKNOWN", "reason": "BATCH_EVIDENCE_UNAVAILABLE"}
