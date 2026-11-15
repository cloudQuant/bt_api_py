"""Construct synthetic pre-guard Store bytes in a test-owned temporary root.

This is a historical test fixture, not a product writer or evidence of a real
publication. It deliberately does not call the current Store publish method.
"""

from bt_api_py import trading_calendar_store as store_module
from bt_api_py.trading_calendar_store import CalendarStoreError, PublicationResult


def write_historical_interruption(
    store,
    content,
    *,
    publication_id,
    version_id,
    coverage_manifest_bytes=None,
    coverage_manifest_sha256=None,
    binding_bytes=None,
    binding_sha256=None,
    republish=False,
):
    """Write one old-format synthetic event and receipt, without the new guard."""
    try:
        policy = store.read_review_policy()
    except CalendarStoreError:
        policy = store.initialize_review_policy(config_read_succeeded=True)
    if policy.mode != "auto":
        raise ValueError("fixture supports automatic policy only")
    current_path = store.root / "CURRENT.json"
    current = store.read_current() if current_path.exists() else None
    previous = (
        None
        if current is None
        else {
            "publication_id": current.publication_id,
            "publication_sha256": current.publication_sha256,
        }
    )
    baseline = (
        {"mode": "ABSENT", "publication_id": None, "publication_sha256": None}
        if previous is None
        else {"mode": "MATCH", **previous}
    )
    content_bytes = store_module._content(content)
    artifacts = (coverage_manifest_bytes, coverage_manifest_sha256, binding_bytes, binding_sha256)
    snapshot_bytes = store_module._snapshot_bytes(content_bytes, *artifacts)
    snapshot_sha = store_module._sha(snapshot_bytes)
    difference = {}
    evidence = {
        "directory_id": policy.directory_id,
        "policy_revision": policy.revision,
        "mode": policy.mode,
        "mode_source": policy.mode_source,
        "snapshot_sha256": snapshot_sha,
        "difference": difference,
        "difference_sha256": store_module._sha(store_module._canonical(difference)),
        "reviewer": None,
        "confirmation_snapshot_sha256": None,
        "confirmation_difference_sha256": None,
        "confirmation_policy_revision": None,
        "baseline": baseline,
        "confirmation_current": None,
        "confirmation_utc": None,
        "identity_status": "UNAUTHENTICATED",
        "source_status": "UNVERIFIED",
    }
    lower = store._clock_sample()
    covered = any(item is not None for item in artifacts)
    event = {
        "schema": store_module._EVENT_POLICY_COVERED if covered else store_module._EVENT_POLICY,
        "publication_id": publication_id,
        "sequence": 1 if current is None else current.sequence + 1,
        "previous": previous,
        "version_id": version_id,
        "content_sha256": store_module._sha(content_bytes),
        "lower_utc": lower[0],
        "monotonic_ns": lower[1],
        "boot_id": lower[2],
        "utc_bound_us": lower[3],
        "review_policy": evidence,
    }
    if covered:
        event["version_sha256"] = snapshot_sha
    event_bytes = store_module._canonical(event)
    pointer = {
        "schema": store_module._POINTER,
        "publication_id": publication_id,
        "publication_sha256": store_module._sha(event_bytes),
    }
    version_path = store.root / "versions" / f"{version_id}.json"
    if republish:
        if not version_path.is_file() or version_path.read_bytes() != snapshot_bytes:
            raise ValueError("fixture republish mismatch")
    else:
        with version_path.open("xb") as output:
            output.write(snapshot_bytes)
    with (store.root / "publications" / f"{publication_id}.json").open("xb") as output:
        output.write(event_bytes)
    # The entire directory is an isolated test fixture, never a live store.
    current_path.write_bytes(store_module._canonical(pointer))
    upper = store._clock_sample()
    receipt = {
        "schema": store_module._RECEIPT_TIMED,
        "publication_id": publication_id,
        "version_id": version_id,
        "pointer": pointer,
        "observation": "POST_REPLACE_READBACK",
        "observed_utc": upper[0],
        "monotonic_ns": upper[1],
        "boot_id": upper[2],
        "utc_bound_us": upper[3],
        "clock_valid": store_module._clock_pair_valid(lower, upper),
    }
    with (store.root / "receipts" / f"{publication_id}.json").open("xb") as output:
        output.write(store_module._canonical(receipt))
    assert store.read_current_with_consistent_receipts().snapshot.publication_id == publication_id
    return PublicationResult("COMMITTED", "UNCONFIRMED", publication_id, version_id, fence="CLEAR")
