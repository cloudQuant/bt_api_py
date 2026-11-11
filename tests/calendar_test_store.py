"""Synthetic test caller of the managed store; never used by application code.

Existing storage regression scenarios concern pointer, lock, and crash behavior.
This fixture explicitly initializes an auto policy and supplies a synthetic
candidate, diff, and CURRENT precondition for each of those scenarios. None of
the declarations represents verified source data or permission to publish.
"""

import json

from bt_api_py import trading_calendar_store as module
from bt_api_py.trading_calendar_store import (
    CalendarStoreError,
    CurrentPrecondition,
    PolicyPublication,
    PublicationResult,
    TradingCalendarStore,
    calendar_snapshot_digest,
)


class FixtureCalendarStore(TradingCalendarStore):
    def publish(self, content, **kwargs):
        publication_id, version_id = kwargs.get("publication_id", ""), kwargs.get("version_id", "")
        try:
            try:
                policy = self.read_review_policy()
            except CalendarStoreError:
                policy = self.initialize_review_policy(config_read_succeeded=True)
        except (CalendarStoreError, OSError):
            return PublicationResult("COMMIT_UNKNOWN", "UNCONFIRMED", publication_id, version_id)

        expected = kwargs.get("expected_current")
        if expected is None:
            try:
                event_path = self.root / "publications" / f"{publication_id}.json"
                receipt_path = self.root / "receipts" / f"{publication_id}.json"
                if receipt_path.exists() and event_path.exists():
                    event = json.loads(event_path.read_bytes())
                    baseline = event.get("review_policy", {}).get("baseline")
                    expected = CurrentPrecondition(**baseline) if baseline is not None else None
                if expected is None:
                    pointer = json.loads((self.root / "CURRENT.json").read_bytes())
                    expected = CurrentPrecondition.matching(
                        pointer["publication_id"], pointer["publication_sha256"]
                    )
            except (OSError, ValueError, KeyError, TypeError):
                expected = CurrentPrecondition.absent()
        artifacts = {
            key: kwargs[key]
            for key in (
                "coverage_manifest_bytes",
                "coverage_manifest_sha256",
                "binding_bytes",
                "binding_sha256",
            )
            if key in kwargs
        }
        try:
            digest = calendar_snapshot_digest(content, **artifacts)
        except (CalendarStoreError, ValueError, TypeError, RecursionError):
            digest = "0" * 64
        difference = {}
        claim = PolicyPublication(
            policy.directory_id,
            policy.revision,
            policy.mode,
            digest,
            difference,
            module._sha(module._canonical(difference)),
        )
        kwargs["expected_current"] = expected
        kwargs["policy_publication"] = claim
        return super().publish(content, **kwargs)
