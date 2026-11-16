"""Synthetic journals and keys through the real local CURVE Monitor host."""

from dataclasses import replace

import pytest
import yaml
from bt_api_base.service_protocol import WireIdentity, WireRole

from bt_api_py.gateway_recovery_monitor_relay import GatewayRecoveryMonitorRelay
from bt_api_py.service_monitor_authenticated import (
    AuthenticatedMonitorSinkError,
    AuthenticatedMonitorV2Sink,
)
from tests.integration.test_monitor_service_process import client, start, stop
from tests.unit.test_gateway_recovery_monitor_relay import journal, populate
from tests.unit.test_monitor_service_v2_config import v2_document


def test_live_runtime_maintain_twice_preserves_same_relay_and_rejects_forgery(tmp_path):
    path, doc = v2_document(tmp_path)
    doc["storage"]["event_types"] = ["gateway.recovery.journal.v1"]
    doc["producer_bindings"][0]["event_types"] = ["gateway.recovery.journal.v1"]
    path.write_text(yaml.safe_dump(doc))
    keys = {
        name: (
            (tmp_path / f"{name}.public").read_bytes(),
            (tmp_path / f"{name}.secret").read_bytes(),
        )
        for name in ("events", "producer")
    }
    process = start(path)
    runtime = relay = None
    try:
        runtime = client(
            doc["ingress"], keys["events"][0], keys["producer"], "producer", "events.append"
        )
        runtime.start()
        peer = WireIdentity(
            WireRole.SERVICE, doc["ingress"]["principal_ref"], doc["ingress"]["service_id"]
        )
        sink = AuthenticatedMonitorV2Sink(
            runtime,
            scope_ref=doc["ingress"]["scope_ref"],
            outbox_scope="events",
            binding_ref="binding-1",
            source_ref="source-1",
            expected_peer=peer,
        )
        source = journal(tmp_path / "source.sqlite")
        populate(source, source_ref="source-1")
        relay = GatewayRecoveryMonitorRelay(
            source,
            journal_path=tmp_path / "source.sqlite",
            scope_ref="source-scope",
            source_ref="source-1",
            checkpoint_path=tmp_path / "checkpoint.sqlite",
            sink=sink,
        )
        original = runtime.bindings[0]
        assert runtime._has_current_registration_observation(original)
        assert relay.pump(max_records=1).cursor == 1
        for expected_cursor in (2, 3):
            runtime._maintain(force=True)  # real correlated CURVE service.renew, no fake receipt
            current = runtime.bindings[0]
            assert current is not original
            assert (current.registration_id, current.service_epoch) == (
                original.registration_id,
                original.service_epoch,
            )
            assert runtime._has_current_registration_observation(current)
            assert relay.pump(max_records=1).cursor == expected_cursor
            original = current
        forged = replace(original)
        runtime._bindings[0] = forged
        assert not runtime._has_current_registration_observation(forged)
        with pytest.raises(AuthenticatedMonitorSinkError, match="INGRESS_BINDING_CHANGED"):
            sink.assert_current()
        runtime._bindings[0] = original
        assert relay.pump(max_records=1).cursor == 4
    finally:
        if relay is not None:
            relay.close()
        if runtime is not None:
            runtime.close()
        stop(process)
