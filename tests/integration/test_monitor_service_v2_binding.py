"""Synthetic CURVE clients against the real standalone Monitor Host and SQLite."""

import pytest
import yaml
from bt_api_base.service_protocol import WireScope
from bt_api_monitor.durable import DurableOutbox

from bt_api_py.service_runtime import ServiceRuntimeError
from tests.integration.test_monitor_service_process import client, start, stop
from tests.unit.test_monitor_service_v2_config import v2_document


def test_v2_producer_binding_is_enforced_before_durable_ack(tmp_path):
    path, doc = v2_document(tmp_path)
    doc["storage"]["event_types"] = ["gateway.recovery.journal.v1"]
    doc["producer_bindings"][0]["event_types"] = ["gateway.recovery.journal.v1"]
    # The second producer is authenticated for the same account but has no v2 binding.
    doc["ingress"]["principals"].append(doc["heartbeat"]["principals"][0])
    path.write_text(yaml.safe_dump(doc))
    keys = {
        name: (
            (tmp_path / f"{name}.public").read_bytes(),
            (tmp_path / f"{name}.secret").read_bytes(),
        )
        for name in ("events", "producer", "gateway")
    }
    event = {
        "schema_version": 2,
        "event_id": "synthetic-1",
        "event_type": "gateway.recovery.journal.v1",
        "occurred_at": 1800000000.0,
        "data": {"value": 1},
        "binding_ref": "binding-1",
        "source_ref": "source-1",
    }
    process = start(path)
    try:
        producer = client(
            doc["ingress"], keys["events"][0], keys["producer"], "producer", "events.append"
        )
        producer.start()
        first = dict(producer.call("upstream.telemetry", "events.append", event))
        assert first["event_id"].startswith("service-event-v2:")
        assert first["source_event_id"] == "synthetic-1"
        assert first["sequence"] > 0
        assert dict(producer.call("upstream.telemetry", "events.append", event)) == first
        downgraded = {k: v for k, v in event.items() if k not in {"binding_ref", "source_ref"}}
        downgraded["schema_version"] = 1
        with pytest.raises(ServiceRuntimeError):
            producer.call("upstream.telemetry", "events.append", downgraded)
        producer.close()

        other = client(
            doc["ingress"], keys["events"][0], keys["gateway"], "gateway", "events.append"
        )
        other.start()
        with pytest.raises(ServiceRuntimeError):
            other.call("upstream.telemetry", "events.append", event)
        other.close()

        altered = client(
            doc["ingress"], keys["events"][0], keys["producer"], "producer", "events.append"
        )
        altered._scope_resolver = lambda _: WireScope(
            "synthetic-domain",
            account_ref="synthetic-account",
            strategy_ref="strategy",
            run_id="run-1",
        )
        with pytest.raises(ServiceRuntimeError):
            altered.start()
        altered.close()
    finally:
        stop(process)

    outbox = DurableOutbox(tmp_path / "data/events.sqlite", read_only=True)
    rows, more = outbox.read_page_types("events", ("gateway.recovery.journal.v1",), 0, 10)
    assert not more and len(rows) == 1
    assert rows[0].event.event_id == first["event_id"]
    data = rows[0].event.data
    assert data["binding_ref"] == "binding-1" and data["source_ref"] == "source-1"
    assert data["schema_version"] == 2 and data["source_event_id"] == "synthetic-1"
    assert dict(data["scope"]) == {**doc["producer_bindings"][0]["scope"], "account_refs": ()}
    assert data["producer"]["principal_ref"] == "producer"
    assert data["payload"]["value"] == 1
