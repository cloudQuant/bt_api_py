"""Legacy neutral broker-event envelope projection for SDK consumers.

Provider adapters remain responsible for interpreting native payloads. This
module preserves the existing generic envelope flattening only; it does not
authenticate, normalize, or establish the provenance of any event.
"""


def iter_broker_event_rows(update):
    """Yield the existing flat row representation of a broker event."""
    if not isinstance(update, dict):
        return

    data = update.get("data")
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, (list, tuple)):
        rows = [item for item in data if isinstance(item, dict)]
    else:
        yield update
        return

    if not rows:
        yield update
        return

    envelope = {key: value for key, value in update.items() if key not in {"data", "id"}}
    if update.get("id") not in (None, ""):
        envelope["message_id"] = update.get("id")
    for row in rows:
        flat = dict(envelope)
        flat.update(row)
        if "kind" not in flat and update.get("kind") not in (None, ""):
            flat["kind"] = update.get("kind")
        yield flat


__all__ = ["iter_broker_event_rows"]
