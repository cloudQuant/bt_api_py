"""Explicit local MOCK interruption publication; never a trading authorization.

The installed CTP snapshot implementation is a trusted process dependency, not
an identity or source authenticator. Python monkeypatching and non-cooperating
filesystem writers are outside the boundary. Only TRUSTED_LOCAL_POSIX is supported.
No unknown operation is automatically retried or assigned another ID.
"""

from __future__ import annotations

import argparse
import contextlib
import json
from dataclasses import asdict
from pathlib import Path

from bt_api_py import trading_calendar_interruption_prepare_simulation as prep
from bt_api_py import trading_calendar_interruption_review_simulation as review
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX
from bt_api_py.trading_calendar_store import (
    CurrentPrecondition,
    PolicyPublication,
    TradingCalendarStore,
    _canonical,
    _decode,
    _digest,
    _identifier,
    _parse_utc,
)

_LABELS = {
    "mode": "SIMULATION_ONLY",
    "source_authentication": "NOT_PROVEN",
    "identity_authentication": "NOT_PROVEN",
    "clock_authentication": "NOT_PROVEN",
    "durability": "NOT_PROVEN",
    "trading_authorization": "NOT_PROVEN",
}


def _factory():
    # Explicit trusted runtime dependency; no CTP import when module is loaded.
    from bt_api_ctp.calendar_snapshot import CalendarSnapshot

    return CalendarSnapshot


def _receipt_identity_locked(store, current):
    """Bounded receipt-chain fingerprint, called with the Store writer lock held."""
    budget = prep._BUDGET
    total = 0

    def read(path):
        nonlocal total
        remaining = budget.max_total_bytes - total
        if remaining <= 0:
            raise ValueError("budget")
        value = store._read_bounded(path, min(budget.max_file_bytes, remaining))
        total += len(value)
        return value

    pointer_bytes = read(store.root / "CURRENT.json")
    if (
        store._load(pointer_bytes, _reader=read, _max_publications=budget.max_publications)
        != current
    ):
        raise ValueError("current")
    store._validate_chain_receipts(
        pointer_bytes, _reader=read, _max_publications=budget.max_publications
    )
    pointer, receipts = _decode(pointer_bytes), []
    while pointer is not None:
        if len(receipts) >= budget.max_publications:
            raise ValueError("budget")
        publication_id = pointer["publication_id"]
        event_bytes = read(store.root / "publications" / f"{publication_id}.json")
        if prep._sha(event_bytes) != pointer["publication_sha256"]:
            raise ValueError("event")
        event = _decode(event_bytes)
        receipt = read(store.root / "receipts" / f"{publication_id}.json")
        record = _decode(receipt)
        if (record["publication_id"], record["version_id"], record["pointer"]) != (
            publication_id,
            event["version_id"],
            pointer,
        ):
            raise ValueError("receipt")
        receipts.append([publication_id, prep._sha(receipt)])
        previous = event["previous"]
        pointer = (
            None if previous is None else {"schema": "bt_api.calendar-store.current/1", **previous}
        )
    if read(store.root / "CURRENT.json") != pointer_bytes:
        raise ValueError("current")
    return prep._sha(_canonical(receipts))


def _arguments(
    *,
    root,
    candidate_id,
    review_schema,
    expected_candidate_payload_sha256,
    expected_current_publication_id,
    expected_current_publication_sha256,
    filesystem,
    mode,
):
    return {
        "root": root,
        "candidate_id": candidate_id,
        "review_schema": review_schema,
        "expected_candidate_payload_sha256": expected_candidate_payload_sha256,
        "expected_current_publication_id": expected_current_publication_id,
        "expected_current_publication_sha256": expected_current_publication_sha256,
        "filesystem": filesystem,
        "mode": mode,
    }


def observe(**kwargs):
    """Read the existing proposal using the installed CTP simulation validator."""
    try:
        return prep.prepare(**kwargs, snapshot_factory=_factory())
    except Exception:
        return {"status": "UNKNOWN", "reason": "OBSERVATION_UNAVAILABLE", **_LABELS}


def publish(
    *,
    root,
    candidate_id,
    review_schema,
    expected_candidate_payload_sha256,
    expected_current_publication_id,
    expected_current_publication_sha256,
    expected_proposal_sha256,
    publication_id,
    version_id,
    filesystem,
    mode,
    reviewer=None,
    confirmed_at=None,
):
    """Revalidate and attempt one explicit simulated publication by original IDs."""
    unknown = {
        "status": "UNKNOWN",
        "reason": "PUBLICATION_UNAVAILABLE",
        "publication_id": publication_id if _identifier(publication_id) else None,
        "version_id": version_id if _identifier(version_id) else None,
        **_LABELS,
    }
    try:
        if (
            type(root) is not str
            or not Path(root).is_absolute()
            or mode != "SIMULATION_ONLY"
            or filesystem != TRUSTED_LOCAL_POSIX
            or not _identifier(publication_id)
            or not _identifier(version_id)
            or not _digest(expected_proposal_sha256)
        ):
            return unknown
        store = TradingCalendarStore(root)
        root_identity = prep._root_lock_identity(root)
        # Existing/uncertain operation IDs are never silently resubmitted here.
        if any(
            store._entry_exists(path)
            for path in (
                store.root / "publications" / f"{publication_id}.json",
                store.root / "receipts" / f"{publication_id}.json",
                store.root / "versions" / f"{version_id}.json",
                store.root / "PENDING.json",
                store.root / "POLICY_CHANGE_PENDING.json",
            )
        ):
            return {
                "status": "UNKNOWN",
                "reason": "ORIGINAL_ID_RECONCILE_FIRST",
                "publication_id": publication_id,
                "version_id": version_id,
                **_LABELS,
            }
        factory = _factory()
        args = _arguments(
            root=root,
            candidate_id=candidate_id,
            review_schema=review_schema,
            expected_candidate_payload_sha256=expected_candidate_payload_sha256,
            expected_current_publication_id=expected_current_publication_id,
            expected_current_publication_sha256=expected_current_publication_sha256,
            filesystem=filesystem,
            mode=mode,
        )
        proposal = prep.prepare(**args, snapshot_factory=factory)
        if (
            proposal.get("status")
            not in {"AUTO_DECLARED", "NEEDS_CONFIRMATION", "SAME_DECLARATION"}
            or proposal.get("proposal_sha256") != expected_proposal_sha256
            or prep._root_lock_identity(root) != root_identity
        ):
            return unknown
        if proposal["status"] == "NEEDS_CONFIRMATION" and reviewer is None and confirmed_at is None:
            return {
                "status": "NEEDS_CONFIRMATION",
                "proposal_sha256": expected_proposal_sha256,
                "publication_id": publication_id,
                "version_id": version_id,
                **_LABELS,
            }
        if proposal["status"] == "NEEDS_CONFIRMATION":
            if not _identifier(reviewer):
                return unknown
            _parse_utc(confirmed_at)
        elif reviewer is not None or confirmed_at is not None:
            return unknown
        material_args = {
            "root": root,
            "candidate_id": candidate_id,
            "filesystem": filesystem,
            "snapshot_factory": factory,
            "mode": mode,
            "schema": review_schema,
        }
        body, payload = review._load_verified_material(**material_args)
        candidate = body["input"]["candidate"]
        if (
            payload != expected_candidate_payload_sha256
            or prep._target(candidate) != proposal["target"]
        ):
            return unknown
        content = candidate["content"]
        content_sha = prep._sha(_canonical(content))
        manifest = _canonical(
            {
                "schema": "bt_api.calendar-simulation-coverage/1",
                "snapshot_sha256": content_sha,
                "coverage": candidate["coverage"],
            }
        )
        manifest_sha = prep._sha(manifest)
        binding = _canonical(
            {
                "schema": "bt_api.calendar-simulation-binding/1",
                "snapshot_sha256": content_sha,
                "manifest_sha256": manifest_sha,
            }
        )
        expected = CurrentPrecondition.matching(
            expected_current_publication_id, expected_current_publication_sha256
        )
        policy = proposal["policy"]
        diff = proposal["publication_difference"]
        diff_sha = proposal["publication_difference_sha256"]
        confirmation = (
            (
                reviewer,
                proposal["target"]["version_sha256"],
                diff_sha,
                policy["revision"],
                expected,
                confirmed_at,
            )
            if proposal["status"] == "NEEDS_CONFIRMATION"
            else (None,) * 6
        )
        claim = PolicyPublication(
            policy["directory_id"],
            policy["revision"],
            policy["mode"],
            proposal["target"]["version_sha256"],
            diff,
            diff_sha,
            *confirmation,
        )

        def guard(actual_policy):
            if prep._root_lock_identity(root) != root_identity or asdict(actual_policy) != policy:
                raise ValueError("identity")
            current = store.read_current()  # caller already holds the Store writer lock
            prep._baseline_matches(
                current,
                body,
                review_schema,
                expected_current_publication_id,
                expected_current_publication_sha256,
            )
            if _receipt_identity_locked(store, current) != proposal["receipt_chain_sha256"]:
                raise ValueError("receipts")
            again, again_payload = review._load_verified_material(**material_args)
            if (
                again != body
                or again_payload != payload
                or again["difference"] != diff["declaration_difference"]
                or prep._target(again["input"]["candidate"]) != proposal["target"]
                or prep._root_lock_identity(root) != root_identity
            ):
                raise ValueError("review")

        outcome = store._publish_simulation_interruption(
            content,
            mode=mode,
            publication_id=publication_id,
            version_id=version_id,
            coverage_manifest_bytes=manifest,
            coverage_manifest_sha256=manifest_sha,
            binding_bytes=binding,
            binding_sha256=prep._sha(binding),
            expected_current=expected,
            policy_publication=claim,
            locked_policy_guard=guard,
        )
        completion = (
            "COMPLETE"
            if outcome.fence == "CLEAR"
            and (
                outcome.visibility == "UNCHANGED"
                or (
                    outcome.visibility in {"COMMITTED", "HISTORICAL_COMMITTED"}
                    and outcome.post_commit == "COMPLETE"
                )
            )
            else "INCOMPLETE"
        )
        return {
            "status": outcome.visibility,
            "completion": completion,
            "fence": outcome.fence,
            "post_commit": outcome.post_commit,
            "publication_id": publication_id,
            "version_id": outcome.version_id,
            **_LABELS,
        }
    except (KeyboardInterrupt, SystemExit) as exc:
        with contextlib.suppress(Exception):
            exc.calendar_outcome = {
                "status": "COMMIT_UNKNOWN",
                "reason": "INTERRUPTED_RECONCILE_ORIGINAL_ID",
                "completion": "INCOMPLETE",
                "fence": "UNKNOWN",
                "publication_id": publication_id if _identifier(publication_id) else None,
                "version_id": version_id if _identifier(version_id) else None,
                **_LABELS,
            }
        raise
    except Exception:
        return unknown


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def main(argv=None):
    parser = _Parser(
        description="Explicit local synthetic MOCK calendar publication; use public synthetic inputs only"
    )
    parser.add_argument("action", choices=("observe", "publish"))
    for name in (
        "root",
        "candidate-id",
        "review-schema",
        "expected-candidate-payload-sha256",
        "expected-current-publication-id",
        "expected-current-publication-sha256",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--filesystem", required=True, choices=(TRUSTED_LOCAL_POSIX,))
    parser.add_argument("--mode", required=True, choices=("SIMULATION_ONLY",))
    for name in (
        "expected-proposal-sha256",
        "publication-id",
        "version-id",
        "reviewer",
        "confirmed-at",
    ):
        parser.add_argument("--" + name)
    try:
        values = vars(parser.parse_args(argv))
    except ValueError:
        print(
            json.dumps(
                {"status": "UNKNOWN", "reason": "INVALID_ARGUMENTS", **_LABELS}, sort_keys=True
            )
        )
        return 2
    action = values.pop("action")
    optional = {
        key: values.pop(key)
        for key in (
            "expected_proposal_sha256",
            "publication_id",
            "version_id",
            "reviewer",
            "confirmed_at",
        )
    }
    try:
        result = observe(**values) if action == "observe" else publish(**values, **optional)
    except (KeyboardInterrupt, SystemExit):
        result = {
            "status": "COMMIT_UNKNOWN" if action == "publish" else "UNKNOWN",
            "reason": "INTERRUPTED_RECONCILE_ORIGINAL_ID" if action == "publish" else "INTERRUPTED",
            "completion": "INCOMPLETE",
            "fence": "UNKNOWN",
            "publication_id": optional["publication_id"]
            if _identifier(optional["publication_id"])
            else None,
            "version_id": optional["version_id"] if _identifier(optional["version_id"]) else None,
            **_LABELS,
        }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return (
        0
        if (
            result["status"]
            in {"COMMITTED", "UNCHANGED", "NEEDS_CONFIRMATION", "AUTO_DECLARED", "SAME_DECLARATION"}
            and result.get("completion") != "INCOMPLETE"
        )
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
