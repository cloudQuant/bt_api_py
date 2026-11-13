"""MOCK/SIMULATION_ONLY publication using the existing local calendar store.

Only an existing successful mock run in its private temporary root is accepted.
This is a simulation facility, not source authentication or account authority.
A caller deliberately pointing a normal store reader at the root can still read
its CURRENT; operating-system ownership and this marker are not a security gate.
Never point a production configuration or account at this directory.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mock as mock
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review
from bt_api_py.trading_calendar_binding import GateDecision, SimulationCalendarBinding
from bt_api_py.trading_calendar_store import (
    CalendarStoreError,
    CurrentPrecondition,
    PolicyPublication,
    TradingCalendarStore,
    calendar_snapshot_digest,
)

_LABEL = {
    "data_origin": "MOCK",
    "environment": "SIMULATION_ONLY",
    "source_authentication": "NOT_PROVEN",
}
_FS = "TRUSTED_LOCAL_POSIX"


def _json(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _labeled(**value):
    return {**_LABEL, **value}


def _id(value):
    return type(value) is str and re.fullmatch(r"MOCK_[A-Za-z0-9_-]{1,123}", value) is not None


def _attach_interrupt(exc, *, operation, status, run_id, **ids):
    """Expose original identities without settling an interrupted write."""
    exc.mock_outcome = _labeled(operation=operation, status=status, run_id=run_id, **ids)


class _MockGate:
    def verify(self, event, context):
        if type(event) is not GateDecision:
            raise ValueError("mock decision required")
        return event


class MockBinding:
    """Labeled wrapper around the existing simulated, gated day binding."""

    def __init__(self, binding, guard):
        self._binding = binding
        self._guard = guard

    def status(self):
        self._guard()
        return _labeled(binding=self._binding.status())

    def query(self, **scope):
        self._guard()
        return _labeled(query=self._binding.query(**scope))

    def activate(self, *, trading_day, sequence, event_id):
        self._guard()
        if not _id(event_id):
            raise ValueError("mock event ID required")
        event = GateDecision(True, "MOCK.route", "MOCK.generation", trading_day, sequence, event_id)
        return _labeled(activation=self._binding.activate(event))


class MockCalendarPublication:
    """Operator for one mock run root; no production identity is established.

    All IDs used for version store writes must begin with MOCK_. A write outcome
    of UNKNOWN is never retried; use the original ID with ``verify_publication``.
    Candidate construction and review policy initialization are separate writes.
    """

    def __init__(self, *, root, run_id):
        expected = os.path.join(
            os.path.realpath(tempfile.gettempdir()), f"bt-calendar-MOCK-SIMULATION_ONLY-{run_id}"
        )
        if not runs._id(run_id) or type(root) is not str or root != expected:
            raise ValueError("not an isolated mock run root")
        self.root, self.run_id = root, run_id
        self._identity = self._stat_root()
        shown = runs.show_once(root=root, run_id=run_id, filesystem=_FS)
        audit = shown.get("mock_audit", {})
        association = shown.get("derived_association", {})
        if (
            audit.get("state") != "VISIBLE_UNAUTHENTICATED"
            or association.get("state") != "VISIBLE_UNAUTHENTICATED"
            or association.get("derived_id") != audit.get("derived_id")
            or association.get("capture_id") != audit.get("capture_id")
        ):
            raise ValueError("mock capture association unavailable")
        self._audit = audit
        self._store = TradingCalendarStore(root)
        self._guard()

    def _stat_root(self):
        info = os.lstat(self.root)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or os.path.realpath(self.root) != self.root
        ):
            raise ValueError("mock root unavailable")
        return info.st_dev, info.st_ino

    def _guard(self):
        from bt_api_py.trading_calendar_mock_run import read_audit

        if self._stat_root() != self._identity:
            raise ValueError("mock root changed")
        fds, chain = runs._open_runs(self.root, create=False)
        try:
            audit = read_audit(fds[-1], chain, self.run_id, self.root)
            if audit != self._audit:
                raise ValueError("mock audit changed")
        finally:
            runs._close(fds)

    def initialize(self, *, mode):
        """Initialize explicit policy once; do not retry an uncertain result."""
        if type(mode) is not str or mode not in {"auto", "manual"}:
            return _labeled(status="NOT_ATTEMPTED", reason="INVALID_MODE")
        attempted = False
        try:
            self._guard()
            if any(
                os.path.lexists(Path(self.root) / name)
                for name in (
                    "CURRENT.json",
                    "versions",
                    "publications",
                    "receipts",
                    ".lock",
                    "REVIEW_POLICY.json",
                    "REVIEW_POLICY_INITIALIZED.json",
                    "policy_changes",
                )
            ):
                return _labeled(status="NOT_ATTEMPTED", reason="STORE_NOT_PRISTINE_RECONCILE_FIRST")
            attempted = True
            policy = self._store.initialize_review_policy(config_read_succeeded=True, mode=mode)
            return _labeled(status="INITIALIZED", policy=asdict(policy))
        except (KeyboardInterrupt, SystemExit) as exc:
            _attach_interrupt(
                exc,
                operation="POLICY_INITIALIZE",
                run_id=self.run_id,
                status="COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
            )
            raise
        except Exception:
            return _labeled(status="UNKNOWN", reason="POLICY_INITIALIZATION_UNKNOWN")

    def change_policy(self, *, expected_revision, mode, operation_id, actor, at_utc, emit):
        """Attempt one declared simulation policy change with an explicit CAS."""
        if (
            not _id(operation_id)
            or not _id(actor)
            or type(mode) is not str
            or mode not in {"auto", "manual"}
            or not callable(emit)
        ):
            return _labeled(status="NOT_COMMITTED", reason="INVALID_ARGUMENTS")
        try:
            self._guard()
            emit(_labeled(event="MOCK_POLICY_ID_ALLOCATED", operation_id=operation_id))
        except (KeyboardInterrupt, SystemExit) as exc:
            _attach_interrupt(
                exc,
                operation="POLICY_CHANGE",
                run_id=self.run_id,
                operation_id=operation_id,
                status="NOT_COMMITTED",
            )
            raise
        except Exception:
            return _labeled(status="NOT_COMMITTED", reason="ALLOCATION_NOT_CONFIRMED")
        attempted = False
        try:
            descriptors, chain = runs._open_runs(self.root, create=False)
            try:
                claim = _labeled(
                    schema="bt_api.calendar-mock-policy-claim/1",
                    operation_id=operation_id,
                    expected_revision=expected_revision,
                    mode=mode,
                    actor=actor,
                    at_utc=at_utc,
                    state="INTENT_ONLY",
                )
                attempted = True
                state, reason = runs._write_once(
                    descriptors[-1],
                    chain,
                    f"mock-policy-{operation_id}.json",
                    storage._envelope(claim),
                )
            finally:
                runs._close(descriptors)
            if state != "COMMITTED":
                return _labeled(
                    status=state,
                    reason=reason,
                    operation_id=operation_id,
                    claim="ORIGINAL_ID_RECONCILE_FIRST",
                )
            result = self._store.change_review_policy(
                expected_revision=expected_revision,
                mode=mode,
                operation_id=operation_id,
                actor=actor,
                at_utc=at_utc,
            )
            return _labeled(status=result.status, operation_id=operation_id, result=asdict(result))
        except (KeyboardInterrupt, SystemExit) as exc:
            _attach_interrupt(
                exc,
                operation="POLICY_CHANGE",
                run_id=self.run_id,
                operation_id=operation_id,
                status="COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
            )
            raise
        except Exception:
            return _labeled(status="COMMIT_UNKNOWN", operation_id=operation_id)

    def inspect_policy_change(self, *, operation_id):
        """Read one original policy change without attempting recovery."""
        if not _id(operation_id):
            return _labeled(status="UNKNOWN")
        try:
            self._guard()
            result = self._store.read_policy_change(operation_id)
            return _labeled(
                status="VISIBLE_UNAUTHENTICATED", operation_id=operation_id, record=result
            )
        except Exception:
            return _labeled(status="UNKNOWN", operation_id=operation_id)

    def inspect_policy_claim(self, *, operation_id):
        """Read the immutable policy intent without replaying the change."""
        if not _id(operation_id):
            return _labeled(status="UNKNOWN")
        try:
            self._guard()
            fds, chain = runs._open_runs(self.root, create=False)
            try:
                raw = runs._read_entry(fds[-1], chain, f"mock-policy-{operation_id}.json")
                if raw is None:
                    return _labeled(status="NOT_OBSERVED", operation_id=operation_id)
                body, digest = storage._unwrap(raw)
                if (
                    type(body) is not dict
                    or set(body)
                    != {
                        "schema",
                        "operation_id",
                        "expected_revision",
                        "mode",
                        "actor",
                        "at_utc",
                        "state",
                        *_LABEL,
                    }
                    or body["schema"] != "bt_api.calendar-mock-policy-claim/1"
                    or body["operation_id"] != operation_id
                    or not _id(body["actor"])
                    or body["mode"] not in {"auto", "manual"}
                    or body["state"] != "INTENT_ONLY"
                    or any(body.get(key) != value for key, value in _LABEL.items())
                ):
                    raise ValueError("policy claim changed")
                return _labeled(
                    status="VISIBLE_UNAUTHENTICATED",
                    operation_id=operation_id,
                    claim=body,
                    payload_sha256=digest,
                )
            finally:
                runs._close(fds)
        except Exception:
            return _labeled(status="UNKNOWN", operation_id=operation_id)

    def build_candidate(self, *, candidate_id, year, variant, emit):
        """Retain a deterministic mock candidate with either original or altered hours."""
        if (
            not runs._id(candidate_id)
            or year != self._audit["year"]
            or type(variant) is not str
            or variant not in {"BASE", "AFTERNOON_CHANGE"}
            or not callable(emit)
        ):
            return _labeled(status="NOT_COMMITTED", reason="INVALID_ARGUMENTS")
        attempted = False
        try:
            self._guard()
            data, _ = mock.build(year)
            if variant == "AFTERNOON_CHANGE":
                for shard in data["shards"]:
                    row = next(
                        row for row in shard["layers"]["trading_hours"] if row["kind"] == "future"
                    )
                    next(w for w in row["day_windows"] if w["id"] == "afternoon")["end"] = "14:30"
            emit(_labeled(event="MOCK_CANDIDATE_ID_ALLOCATED", candidate_id=candidate_id))
            attempted = True
            built = review.build_once(
                root=self.root,
                candidate_id=candidate_id,
                input_data=data,
                filesystem=_FS,
                emit=lambda _: None,
                propagate_interrupt=True,
            )
            return _labeled(**built)
        except (KeyboardInterrupt, SystemExit) as exc:
            status = getattr(exc, "calendar_outcome", {}).get("status")
            _attach_interrupt(
                exc,
                operation="CANDIDATE_BUILD",
                run_id=self.run_id,
                candidate_id=candidate_id,
                status=status or ("COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED"),
            )
            raise
        except Exception:
            return _labeled(
                status="COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
                candidate_id=candidate_id,
                reason="CANDIDATE_OUTCOME_UNKNOWN",
            )

    def _candidate(self, candidate_id):
        shown = review.show(root=self.root, candidate_id=candidate_id, filesystem=_FS)
        if shown.get("status") != "VISIBLE_UNAUTHENTICATED":
            raise ValueError("candidate unavailable")
        body = shown["bundle"]
        if (
            body["input"].get("metadata") != mock.LABEL
            or body["report"]["status"] != "STRUCTURALLY_READY_UNVERIFIED"
            or body["report"]["source_authentication"] != "NOT_PROVEN"
            or any(shard["layers"]["sources"] != [mock.SOURCE] for shard in body["input"]["shards"])
        ):
            raise ValueError("not a complete mock declaration")
        composite = body["report"]["composite"]
        if type(composite) is not dict:
            raise ValueError("composite unavailable")
        return shown, composite

    def prepare(self, *, candidate_id):
        """Read a candidate and baseline without publication or authorization."""
        try:
            self._guard()
            shown, composite = self._candidate(candidate_id)
            policy = self._store.read_review_policy()
            manifest, binding = _json(composite["manifest"]), _json(composite["binding"])
            snapshot_hash = calendar_snapshot_digest(
                composite["snapshot"],
                coverage_manifest_bytes=manifest,
                coverage_manifest_sha256=composite["manifest_sha256"],
                binding_bytes=binding,
                binding_sha256=composite["binding_sha256"],
            )
            pointer = Path(self.root) / "CURRENT.json"
            if not os.path.lexists(pointer):
                for name in ("versions", "publications", "receipts"):
                    if any((Path(self.root) / name).iterdir()):
                        raise ValueError("first publication cannot be established")
                baseline = asdict(CurrentPrecondition.absent())
                from bt_api_py.trading_calendar_full_diff import _changes

                layers = ("sources", "trading_days", "trading_hours", "exceptions", "coverage")
                before = {layer: [] for layer in layers}
                after = {**composite["snapshot"], "coverage": composite["manifest"]["coverage"]}
                difference = _labeled(
                    schema="bt_api.calendar-mock-first-difference/1",
                    baseline="NO_PRIOR_SIMULATION_PUBLICATION",
                    candidate_id=candidate_id,
                    candidate_payload_sha256=shown["payload_sha256"],
                    store_snapshot_sha256=snapshot_hash,
                    changes=_changes(before, after),
                )
                if len(_json(difference)) > 16 * 1024 * 1024:
                    raise ValueError("first difference exceeds budget")
                kind = "FIRST_SIMULATION_PUBLICATION"
            else:
                current = self._store.read_current()
                baseline = asdict(
                    CurrentPrecondition.matching(current.publication_id, current.publication_sha256)
                )
                compared = review.compare_full(
                    root=self.root,
                    candidate_id=candidate_id,
                    filesystem=_FS,
                    candidate_payload_sha256=shown["payload_sha256"],
                    current_publication_id=current.publication_id,
                    current_publication_sha256=current.publication_sha256,
                )
                if compared.get("status") not in {"SAME", "CHANGED"}:
                    raise ValueError("comparison unavailable")
                kind, difference = compared["status"], compared["difference"]
            proposal = _labeled(
                status=kind,
                candidate_id=candidate_id,
                candidate_payload_sha256=shown["payload_sha256"],
                snapshot_sha256=snapshot_hash,
                difference=difference,
                difference_sha256=_hash(difference),
                baseline=baseline,
                policy=asdict(policy),
                publication="NOT_AUTHORIZED",
            )
            self._guard()
            return proposal
        except (KeyboardInterrupt, SystemExit) as exc:
            _attach_interrupt(
                exc,
                operation="PREPARE",
                run_id=self.run_id,
                candidate_id=candidate_id,
                status="UNKNOWN",
            )
            raise
        except Exception:
            return _labeled(
                status="UNKNOWN",
                reason="INPUT_OR_BASELINE_UNAVAILABLE",
                publication="NOT_AUTHORIZED",
            )

    def _reserve(self, fresh, publication_id, version_id, *, state):
        claim = _labeled(
            schema="bt_api.calendar-mock-publication-claim/1",
            publication_id=publication_id,
            version_id=version_id,
            proposal_sha256=_hash(fresh),
            candidate_id=fresh["candidate_id"],
            candidate_payload_sha256=fresh["candidate_payload_sha256"],
            snapshot_sha256=fresh["snapshot_sha256"],
            difference_sha256=fresh["difference_sha256"],
            baseline=fresh["baseline"],
            state=state,
        )
        version = _labeled(
            schema="bt_api.calendar-mock-version-claim/1",
            publication_id=publication_id,
            version_id=version_id,
            proposal_sha256=claim["proposal_sha256"],
            state="INTENT_ONLY",
        )
        descriptors, chain = runs._open_runs(self.root, create=False)
        lock = None
        try:
            lock, identity = storage._lock(descriptors[-1], chain)
            with os.scandir(descriptors[-1]) as entries:
                names = []
                for index, entry in enumerate(entries):
                    if index >= 4096:
                        raise ValueError("reservation index budget")
                    if entry.name.startswith(("mock-publication-", "mock-version-")):
                        names.append(entry.name)
            by_publication, by_version = {}, {}
            for name in names:
                raw = runs._read_entry(descriptors[-1], chain, name)
                if raw is None:
                    raise ValueError("reservation changed")
                existing, _ = storage._unwrap(raw)
                if (
                    type(existing) is not dict
                    or existing.get("schema")
                    not in {
                        "bt_api.calendar-mock-publication-claim/1",
                        "bt_api.calendar-mock-version-claim/1",
                    }
                    or not _id(existing.get("publication_id"))
                    or not _id(existing.get("version_id"))
                    or any(existing.get(key) != value for key, value in _LABEL.items())
                    or type(existing.get("proposal_sha256")) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", existing["proposal_sha256"]) is None
                    or name
                    != (
                        "mock-publication-" + existing["publication_id"] + ".json"
                        if existing["schema"] == "bt_api.calendar-mock-publication-claim/1"
                        else "mock-version-" + existing["version_id"] + ".json"
                    )
                ):
                    raise ValueError("reservation index invalid")
                pub, version_id_seen, digest = (
                    existing["publication_id"],
                    existing["version_id"],
                    existing["proposal_sha256"],
                )
                if (pub in by_publication and by_publication[pub] != (version_id_seen, digest)) or (
                    version_id_seen in by_version and by_version[version_id_seen] != (pub, digest)
                ):
                    raise ValueError("conflicting reservation index")
                by_publication[pub] = (version_id_seen, digest)
                by_version[version_id_seen] = (pub, digest)
            if publication_id in by_publication or version_id in by_version:
                return "NOT_COMMITTED", "ID_EXISTS"
            for index, (name, body) in enumerate(
                (
                    (f"mock-version-{version_id}.json", version),
                    (f"mock-publication-{publication_id}.json", claim),
                )
            ):
                storage._check_lock(descriptors[-1], chain, lock, identity)
                outcome, reason = runs._write_once(
                    descriptors[-1], chain, name, storage._envelope(body)
                )
                if outcome != "COMMITTED":
                    if index:
                        return "UNKNOWN", "PARTIAL_CLAIM_RESERVATION_" + outcome
                    return outcome, reason
            storage._check_lock(descriptors[-1], chain, lock, identity)
            return "COMMITTED", "NONE"
        except Exception:
            return "UNKNOWN", "RESERVATION_INDEX_UNAVAILABLE"
        finally:
            if lock is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(lock, fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    os.close(lock)
            runs._close(descriptors)

    def _retain_first_difference(self, fresh, publication_id):
        """Keep full first-publication additions in a separately bound immutable file."""
        body = _labeled(
            schema="bt_api.calendar-mock-retained-first-difference/1",
            publication_id=publication_id,
            difference=fresh["difference"],
            difference_sha256=fresh["difference_sha256"],
        )
        encoded = storage._envelope(body)
        if len(encoded) > review._OUTPUT_LIMIT:
            raise ValueError("first difference exceeds retention budget")
        fds, chain = review._open(self.root, False)
        lock = None
        try:
            lock, identity = storage._lock(fds[-1], chain)
            storage._check_lock(fds[-1], chain, lock, identity)
            return storage._write(
                fds[-1],
                chain,
                f"mock-first-difference-{publication_id}.json",
                encoded,
                review._OUTPUT_LIMIT,
            )
        finally:
            if lock is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(lock, fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    os.close(lock)
            runs._close(fds)

    def publish(
        self, *, proposal, publication_id, version_id, emit, reviewer=None, confirmed_at=None
    ):
        """Recompute the exact proposal and attempt one simulated publication."""
        if (
            type(proposal) is not dict
            or not _id(publication_id)
            or not _id(version_id)
            or publication_id == version_id
            or not callable(emit)
        ):
            return _labeled(status="NOT_COMMITTED", reason="INVALID_ARGUMENTS")
        try:
            self._guard()
            emit(
                _labeled(
                    event="MOCK_PUBLICATION_IDS_ALLOCATED",
                    publication_id=publication_id,
                    version_id=version_id,
                )
            )
        except (KeyboardInterrupt, SystemExit) as exc:
            _attach_interrupt(
                exc,
                operation="PUBLICATION",
                run_id=self.run_id,
                publication_id=publication_id,
                version_id=version_id,
                candidate_id=proposal.get("candidate_id"),
                status="NOT_COMMITTED",
            )
            raise
        except Exception:
            return _labeled(status="NOT_COMMITTED", reason="ALLOCATION_NOT_CONFIRMED")
        attempted = False
        try:
            fresh = self.prepare(candidate_id=proposal.get("candidate_id"))
            if fresh != proposal or fresh.get("status") not in {
                "FIRST_SIMULATION_PUBLICATION",
                "SAME",
                "CHANGED",
            }:
                return _labeled(
                    status="NOT_COMMITTED",
                    reason="STALE_OR_UNAVAILABLE_PROPOSAL",
                    publication_id=publication_id,
                )
            if fresh["status"] == "SAME":
                try:
                    self._guard()
                    with self._store._writer_lock():
                        if self._store._pending_exists() or self._store._policy_change_pending():
                            raise ValueError("pending store operation")
                        if any(
                            os.path.lexists(Path(self.root) / folder / f"{name}.json")
                            for folder, name in (
                                ("publications", publication_id),
                                ("receipts", publication_id),
                                ("versions", version_id),
                            )
                        ):
                            return _labeled(
                                status="NOT_COMMITTED",
                                reason="EXISTING_STORE_ID",
                                publication_id=publication_id,
                                version_id=version_id,
                            )
                        current = self._store.read_current()
                        pointer = self._store._read(Path(self.root) / "CURRENT.json")
                        self._store._validate_chain_receipts(pointer, _max_publications=256)
                        policy = self._store._review_policy_locked()
                        candidate, _ = self._candidate(fresh["candidate_id"])
                        difference = fresh["difference"]
                        if (
                            policy is None
                            or asdict(policy) != fresh["policy"]
                            or asdict(
                                CurrentPrecondition.matching(
                                    current.publication_id, current.publication_sha256
                                )
                            )
                            != fresh["baseline"]
                            or difference.get("schema")
                            != "bt_api.calendar-full-declaration-difference/1"
                            or difference.get("candidate_id") != fresh["candidate_id"]
                            or difference.get("candidate_payload_sha256")
                            != fresh["candidate_payload_sha256"]
                            or difference.get("before", {}).get("version_sha256")
                            != current.version_sha256
                            or difference.get("after", {}).get("store_snapshot_sha256")
                            != fresh["snapshot_sha256"]
                            or type(difference.get("changes")) is not dict
                            or set(difference["changes"])
                            != {
                                "sources",
                                "trading_days",
                                "trading_hours",
                                "exceptions",
                                "coverage",
                            }
                            or any(
                                type(rows) is not list or rows
                                for rows in difference["changes"].values()
                            )
                            or candidate["payload_sha256"] != fresh["candidate_payload_sha256"]
                        ):
                            raise ValueError("same observation changed")
                        self._guard()
                        attempted = True
                        state, reason = self._reserve(
                            fresh, publication_id, version_id, state="NO_CHANGE"
                        )
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    return _labeled(
                        status="COMMIT_UNKNOWN" if attempted else "UNKNOWN",
                        reason="SAME_REVALIDATION_OR_RECORD_UNKNOWN",
                        publication_id=publication_id,
                    )
                return _labeled(
                    status="SAME" if state == "COMMITTED" else state,
                    reason=reason,
                    publication_id=publication_id,
                    version_id=version_id,
                    reservation_state="RESERVED" if state == "COMMITTED" else "PARTIAL_OR_UNKNOWN",
                    claim="NO_CHANGE_RECORDED"
                    if state == "COMMITTED"
                    else "ORIGINAL_IDS_RECONCILE_FIRST",
                    publication="NOT_ATTEMPTED",
                )
            policy = fresh["policy"]
            expected = CurrentPrecondition(**fresh["baseline"])
            store_difference = fresh["difference"]
            if fresh["status"] == "FIRST_SIMULATION_PUBLICATION":
                store_difference = _labeled(
                    schema="bt_api.calendar-mock-first-difference-reference/1",
                    publication_id=publication_id,
                    first_difference_sha256=fresh["difference_sha256"],
                    candidate_id=fresh["candidate_id"],
                    candidate_payload_sha256=fresh["candidate_payload_sha256"],
                )
            store_difference_sha256 = _hash(store_difference)
            if policy["mode"] == "manual":
                if not _id(reviewer) or type(confirmed_at) is not str:
                    return _labeled(
                        status="PENDING_REVIEW", publication_id=publication_id, proposal=proposal
                    )
                stamp = datetime.fromisoformat(confirmed_at.replace("Z", "+00:00"))
                if stamp.utcoffset() != timedelta(0):
                    raise ValueError("confirmation time must be UTC")
                confirmation = (
                    reviewer,
                    fresh["snapshot_sha256"],
                    store_difference_sha256,
                    policy["revision"],
                    expected,
                    confirmed_at,
                )
            elif reviewer is not None or confirmed_at is not None:
                return _labeled(
                    status="NOT_COMMITTED",
                    reason="UNEXPECTED_CONFIRMATION",
                    publication_id=publication_id,
                )
            else:
                confirmation = (None,) * 6
            # Never resubmit a known ID, even if store-level replay would succeed.
            if any(
                os.path.lexists(Path(self.root) / folder / f"{name}.json")
                for folder, name in (
                    ("publications", publication_id),
                    ("receipts", publication_id),
                    ("versions", version_id),
                )
            ):
                return _labeled(
                    status="NOT_COMMITTED",
                    reason="ORIGINAL_ID_RECONCILE_FIRST",
                    publication_id=publication_id,
                )
            shown, composite = self._candidate(fresh["candidate_id"])
            if shown["payload_sha256"] != fresh["candidate_payload_sha256"]:
                raise ValueError("candidate changed")
            evidence = PolicyPublication(
                policy["directory_id"],
                policy["revision"],
                policy["mode"],
                fresh["snapshot_sha256"],
                store_difference,
                store_difference_sha256,
                *confirmation,
            )
            self._guard()
            attempted = True
            claim_state, reason = self._reserve(
                fresh, publication_id, version_id, state="INTENT_ONLY"
            )
            if claim_state != "COMMITTED":
                return _labeled(
                    status=claim_state,
                    reason=reason,
                    publication_id=publication_id,
                    version_id=version_id,
                    reservation_state=(
                        "EXISTING"
                        if reason == "ID_EXISTS"
                        else "NOT_COMMITTED"
                        if claim_state == "NOT_COMMITTED"
                        else "PARTIAL_OR_UNKNOWN"
                    ),
                    claim="ORIGINAL_IDS_RECONCILE_FIRST",
                )
            if fresh["status"] == "FIRST_SIMULATION_PUBLICATION":
                state = self._retain_first_difference(fresh, publication_id)
                if state != "COMMITTED":
                    return _labeled(
                        status="UNKNOWN",
                        reason="PARTIAL_RESERVATION_FIRST_DIFFERENCE_" + state,
                        publication_id=publication_id,
                        version_id=version_id,
                        reservation_state="RESERVED",
                        publication_visibility="NOT_ATTEMPTED",
                        claim="ORIGINAL_IDS_RECONCILE_FIRST",
                    )
                retained = self.inspect_first_difference(publication_id=publication_id)
                if (
                    retained.get("status") != "VISIBLE_UNAUTHENTICATED"
                    or retained.get("difference_sha256") != fresh["difference_sha256"]
                    or retained.get("difference") != fresh["difference"]
                ):
                    return _labeled(
                        status="COMMIT_UNKNOWN",
                        reason="FIRST_DIFFERENCE_RECHECK_UNKNOWN",
                        publication_id=publication_id,
                        version_id=version_id,
                        reservation_state="RESERVED",
                        publication_visibility="NOT_ATTEMPTED",
                        claim="ORIGINAL_IDS_RECONCILE_FIRST",
                    )
            attempted = True
            outcome = self._store.publish(
                composite["snapshot"],
                publication_id=publication_id,
                version_id=version_id,
                coverage_manifest_bytes=_json(composite["manifest"]),
                coverage_manifest_sha256=composite["manifest_sha256"],
                binding_bytes=_json(composite["binding"]),
                binding_sha256=composite["binding_sha256"],
                expected_current=expected,
                policy_publication=evidence,
            )
            return _labeled(
                status="UNKNOWN" if outcome.visibility == "NOT_COMMITTED" else outcome.visibility,
                publication_id=publication_id,
                version_id=version_id,
                result=asdict(outcome),
                publication_visibility=outcome.visibility,
                reservation_state="RESERVED",
                claim="ORIGINAL_IDS_RECONCILE_FIRST",
                publication="SIMULATION_ONLY",
            )
        except (KeyboardInterrupt, SystemExit) as exc:
            _attach_interrupt(
                exc,
                operation="PUBLICATION",
                run_id=self.run_id,
                publication_id=publication_id,
                version_id=version_id,
                candidate_id=proposal.get("candidate_id"),
                status="COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
            )
            raise
        except Exception:
            return _labeled(
                status="COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
                reason="PUBLICATION_OUTCOME_UNKNOWN",
                publication_id=publication_id,
                version_id=version_id,
                reservation_state="PARTIAL_OR_UNKNOWN" if attempted else "NOT_ATTEMPTED",
                claim="ORIGINAL_IDS_RECONCILE_FIRST" if attempted else "NOT_ATTEMPTED",
            )

    def verify_publication(self, *, publication_id):
        """Read-only verification of one original simulated publication ID."""
        if not _id(publication_id):
            return _labeled(status="UNKNOWN")
        try:
            self._guard()
            return _labeled(
                status=self._store.verify_publication(publication_id), publication_id=publication_id
            )
        except (CalendarStoreError, OSError, ValueError):
            return _labeled(status="UNKNOWN", publication_id=publication_id)

    def inspect_publication_claim(self, *, publication_id):
        """Observe the original declaration; visibility is not commit proof."""
        if not _id(publication_id):
            return _labeled(status="UNKNOWN")
        try:
            self._guard()
            descriptors, chain = runs._open_runs(self.root, create=False)
            try:
                raw = runs._read_entry(
                    descriptors[-1], chain, f"mock-publication-{publication_id}.json"
                )
                if raw is None:
                    return _labeled(status="NOT_OBSERVED", publication_id=publication_id)
                body, digest = storage._unwrap(raw)
                if (
                    type(body) is not dict
                    or set(body)
                    != {
                        "schema",
                        "publication_id",
                        "version_id",
                        "proposal_sha256",
                        "candidate_id",
                        "candidate_payload_sha256",
                        "snapshot_sha256",
                        "difference_sha256",
                        "baseline",
                        "state",
                        *_LABEL,
                    }
                    or body.get("schema") != "bt_api.calendar-mock-publication-claim/1"
                    or body.get("publication_id") != publication_id
                    or not _id(body.get("version_id"))
                    or not runs._id(body.get("candidate_id"))
                    or any(
                        type(body.get(key)) is not str
                        or re.fullmatch(r"[0-9a-f]{64}", body[key]) is None
                        for key in (
                            "candidate_payload_sha256",
                            "snapshot_sha256",
                            "difference_sha256",
                        )
                    )
                    or type(body.get("baseline")) is not dict
                    or type(body.get("proposal_sha256")) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", body["proposal_sha256"]) is None
                    or body.get("data_origin") != "MOCK"
                    or body.get("environment") != "SIMULATION_ONLY"
                    or body.get("source_authentication") != "NOT_PROVEN"
                    or body.get("state") not in {"INTENT_ONLY", "NO_CHANGE"}
                ):
                    raise ValueError("invalid claim")
                return _labeled(
                    status="VISIBLE_UNAUTHENTICATED",
                    publication_id=publication_id,
                    claim=body,
                    payload_sha256=digest,
                )
            finally:
                runs._close(descriptors)
        except Exception:
            return _labeled(status="UNKNOWN", publication_id=publication_id)

    def inspect_first_difference(self, *, publication_id):
        """Read the complete retained first-simulation additions by original ID."""
        if not _id(publication_id):
            return _labeled(status="UNKNOWN")
        try:
            self._guard()
            fds, chain = review._open(self.root, False)
            try:
                raw = storage._read(
                    fds[-1],
                    chain,
                    f"mock-first-difference-{publication_id}.json",
                    review._OUTPUT_LIMIT,
                )
                if raw is None:
                    return _labeled(status="NOT_OBSERVED", publication_id=publication_id)
                body, digest = storage._unwrap(raw)
                if (
                    type(body) is not dict
                    or set(body)
                    != {"schema", "publication_id", "difference", "difference_sha256", *_LABEL}
                    or body["schema"] != "bt_api.calendar-mock-retained-first-difference/1"
                    or body["publication_id"] != publication_id
                    or any(body.get(key) != value for key, value in _LABEL.items())
                    or _hash(body["difference"]) != body["difference_sha256"]
                ):
                    raise ValueError("first difference changed")
                return _labeled(
                    status="VISIBLE_UNAUTHENTICATED",
                    publication_id=publication_id,
                    difference=body["difference"],
                    difference_sha256=body["difference_sha256"],
                    payload_sha256=digest,
                )
            finally:
                runs._close(fds)
        except Exception:
            return _labeled(status="UNKNOWN", publication_id=publication_id)

    def inspect_version_claim(self, *, version_id):
        """Inspect the originally reserved version identity without retrying."""
        if not _id(version_id):
            return _labeled(status="UNKNOWN")
        try:
            self._guard()
            fds, chain = runs._open_runs(self.root, create=False)
            try:
                raw = runs._read_entry(fds[-1], chain, f"mock-version-{version_id}.json")
                if raw is None:
                    return _labeled(status="NOT_OBSERVED", version_id=version_id)
                body, digest = storage._unwrap(raw)
                if (
                    type(body) is not dict
                    or set(body)
                    != {
                        "schema",
                        "publication_id",
                        "version_id",
                        "proposal_sha256",
                        "state",
                        *_LABEL,
                    }
                    or body["schema"] != "bt_api.calendar-mock-version-claim/1"
                    or body["version_id"] != version_id
                    or not _id(body["publication_id"])
                    or any(body.get(key) != value for key, value in _LABEL.items())
                    or body["state"] != "INTENT_ONLY"
                    or type(body["proposal_sha256"]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", body["proposal_sha256"]) is None
                ):
                    raise ValueError("version claim changed")
                return _labeled(
                    status="VISIBLE_UNAUTHENTICATED",
                    version_id=version_id,
                    claim=body,
                    payload_sha256=digest,
                )
            finally:
                runs._close(fds)
        except Exception:
            return _labeled(status="UNKNOWN", version_id=version_id)

    def binding(self, *, trading_day):
        from bt_api_ctp.calendar_snapshot import CalendarSnapshot

        if type(trading_day) is not date:
            raise ValueError("date required")
        self._guard()
        return MockBinding(
            SimulationCalendarBinding(
                mode="SIMULATION_ONLY",
                store=self._store,
                snapshot_factory=CalendarSnapshot,
                gate=_MockGate(),
                route="MOCK.route",
                source_generation="MOCK.generation",
                trading_day=trading_day,
                last_sequence=0,
            ),
            self._guard,
        )
